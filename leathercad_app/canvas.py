"""The drawing canvas: a millimetre-accurate, Y-up QGraphicsView.

Coordinates in the scene are millimetres. The view is Y-flipped so positive Y is
up (CAD convention) and matches the exporters. Tools create model shapes; the
selection/move tool drags items (which is how you overlay pieces to check fit).
"""

from __future__ import annotations

import math
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QPainter, QPen, QPainterPath, QPolygonF,
                           QTransform)
from PySide6.QtWidgets import (QGraphicsScene, QGraphicsView, QGraphicsPathItem,
                               QGraphicsItem, QGraphicsLineItem, QMenu)

from leathercad.geometry import Vec2
from leathercad.document import Document
from leathercad.shapes import (Rectangle, Ellipse, Circle, Polygon, PathShape,
                               EditablePath, Edge, Transform)
from leathercad.stitchsettings import StitchSettings
from leathercad.stitchline import StitchLine
from leathercad.holes import LooseHole
from leathercad.stitching import stitch_polyline, Hole, StitchResult, flip_symmetry
from .items import (ShapeItem, StitchLineItem, VertexHandle, HoleItem,
                    ResizeHandle, DimensionItem)

try:
    import shiboken6

    def _alive(obj) -> bool:
        """True if the C++ object behind a PySide wrapper still exists."""
        return shiboken6.isValid(obj)
except Exception:  # pragma: no cover
    def _alive(obj) -> bool:
        return obj is not None


# tool modes
SELECT = "select"
RECT = "rect"
ROUNDED = "rounded"
ELLIPSE = "ellipse"
CIRCLE = "circle"
POLYGON = "polygon"
STITCHLINE = "stitchline"
HOLE = "hole"
SLOT = "slot"
SCORE = "score"
TRIM = "trim"
LINE = "line"
CONSTRUCTION = "construction"
MEASURE = "measure"
DIMENSION = "dimension"

_DRAG_TOOLS = (RECT, ROUNDED, ELLIPSE, CIRCLE, SLOT, LINE, CONSTRUCTION)
_POLY_TOOLS = (POLYGON, STITCHLINE, SCORE)

_SNAP_LABEL = {"center": "centre", "end": "endpoint", "mid": "midpoint",
               "quad": "quadrant", "hole": "hole centre", "cross": "intersection",
               "align": "aligned", "grid": "", "edge": "on line"}


def _rev(seg):
    a, b, kind, mid = seg
    return (b, a, kind, mid)


def _chain_segments(segments, tol):
    """Greedily chain segments sharing endpoints (within tol). Returns
    (ordered chain, leftover segments)."""
    remaining = list(segments)
    chain = [remaining.pop(0)]
    grew = True
    while grew:
        grew = False
        end = chain[-1][1]
        for i, s in enumerate(remaining):
            if (s[0] - end).length() < tol:
                chain.append(remaining.pop(i)); grew = True; break
            if (s[1] - end).length() < tol:
                chain.append(_rev(remaining.pop(i))); grew = True; break
    grew = True
    while grew:
        grew = False
        start = chain[0][0]
        for i, s in enumerate(remaining):
            if (s[1] - start).length() < tol:
                chain.insert(0, remaining.pop(i)); grew = True; break
            if (s[0] - start).length() < tol:
                chain.insert(0, _rev(remaining.pop(i))); grew = True; break
    return chain, remaining


def _path_from_chain(chain, tol, layer):
    """Build a shape from an ordered chain of world-space segments."""
    nodes = [chain[0][0]]
    edges = []
    for a, b, kind, mid in chain:
        nodes.append(b)
        edges.append((kind, mid))
    closed = len(nodes) > 2 and (nodes[-1] - nodes[0]).length() < tol
    if closed:
        nodes = nodes[:-1]
    allpts = list(nodes) + [m for _, m in edges if m is not None]
    cx = sum(p.x for p in allpts) / len(allpts)
    cy = sum(p.y for p in allpts) / len(allpts)
    def loc(p):
        return Vec2(p.x - cx, p.y - cy)
    local_nodes = [loc(p) for p in nodes]

    if any(k == "arc" for k, _ in edges):
        e_objs = [Edge(k, loc(m) if m is not None else None) for k, m in edges]
        sh = EditablePath(nodes=local_nodes, edges=e_objs, closed=closed)
    elif closed:
        sh = Polygon(points=local_nodes, close_path=True, sharp_corners=True)
    else:
        sh = PathShape(points=local_nodes, close_path=False)
    sh.transform = Transform(x=cx, y=cy)
    sh.layer = layer
    return sh


def _point_polyline_dist(p: Vec2, poly) -> float:
    """Shortest distance from point ``p`` to a polyline (list of Vec2)."""
    best = float("inf")
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        ab = b - a
        d2 = ab.length_sq()
        t = 0.0 if d2 <= 1e-12 else max(0.0, min(1.0, (p - a).dot(ab) / d2))
        best = min(best, (a.lerp(b, t) - p).length())
    return best


def _point_in_poly(p: Vec2, poly) -> bool:
    """Ray-cast point-in-polygon test (poly is a list of Vec2)."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i].x, poly[i].y
        xj, yj = poly[j].x, poly[j].y
        if (yi > p.y) != (yj > p.y):
            x_cross = xi + (p.y - yi) * (xj - xi) / (yj - yi)
            if p.x < x_cross:
                inside = not inside
        j = i
    return inside


def _segment_shape(kind, wpts, layer):
    """Build a standalone open path for one segment, recentred on its own
    centroid so it has an independent transform for moving."""
    cx = sum(p.x for p in wpts) / len(wpts)
    cy = sum(p.y for p in wpts) / len(wpts)
    local = [Vec2(p.x - cx, p.y - cy) for p in wpts]
    if kind == "arc" and len(local) == 3:
        sh = EditablePath(nodes=[local[0], local[2]],
                          edges=[Edge("arc", local[1])], closed=False)
    else:
        sh = PathShape(points=[local[0], local[-1]], close_path=False)
    sh.transform = Transform(x=cx, y=cy)
    sh.layer = layer
    return sh


class Canvas(QGraphicsView):
    selectionChangedSig = Signal()
    documentChangedSig = Signal()
    toolFinished = Signal()
    requestSelectTool = Signal()  # Esc with nothing in progress -> pointer
    cursorMoved = Signal(float, float)
    commitRequested = Signal()   # a discrete edit finished -> push undo snapshot
    statusMessage = Signal(str)  # transient hint (live dimensions while drawing)

    def __init__(self, document: Document):
        super().__init__()
        self.doc = document
        self.scene_obj = QGraphicsScene(self)
        self.scene_obj.setSceneRect(-500, -500, 1000, 1000)
        self.setScene(self.scene_obj)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        # Rubber-band selects by item SHAPE (outline for shapes), so a box drawn
        # inside a shape selects only the holes there, not the shape itself.
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)

        self._zoom = 3.0  # px per mm
        self._apply_zoom()

        self.tool = SELECT
        self._default_stitch = lambda: StitchSettings(pitch_mm=3.85, inset=3.5)
        self._current_layer = "Cut"

        # in-progress construction state
        self._start: Optional[QPointF] = None
        self._preview: Optional[QGraphicsPathItem] = None
        self._poly_pts: List[QPointF] = []
        self._moved_during_press = False
        self._suppress_commit = False
        self._suppress_next_release = False
        self.hole_tool_diameter = 4.0
        # drawing style: False = click first point, click second point (default);
        # True = press-drag-release. Tunable from the View menu.
        self.drag_to_draw = False
        self._handles: List[VertexHandle] = []
        self._edit_owner = None
        self._resize_handles = []
        self._active_resize = None   # handle currently being dragged
        # Hold strong Python refs to every scene item we create. PySide6 can
        # otherwise garbage-collect a live item's wrapper and free the C++
        # object while it is still selected -> crash in clearSelection().
        self._live = set()
        self._snap_cache = None   # static snap nodes captured at drag start
        self._group_drag = None   # active move-group drag state
        self._nonmovable_members = []   # items we temporarily froze for a group drag

        # snapping -- grid and node snapping toggle independently
        self.snap_to_nodes = True    # ends / midpoints / centres / intersections
        self.snap_to_grid = True
        self.snap_grid = 1.0         # mm
        self._snap_marker: Optional[QGraphicsPathItem] = None
        self._node_hl: Optional[QGraphicsPathItem] = None
        self._trim_hover: Optional[QGraphicsPathItem] = None
        self._align_guides: List[QGraphicsLineItem] = []

        self.scene_obj.selectionChanged.connect(self.selectionChangedSig)

    # -- zoom / view ----------------------------------------------------
    def _apply_zoom(self) -> None:
        self.setTransform(QTransform().scale(self._zoom, -self._zoom))

    def wheelEvent(self, event):
        old = self.mapToScene(event.position().toPoint())
        factor = 1.0015 ** event.angleDelta().y()
        self._zoom = max(0.3, min(40.0, self._zoom * factor))
        self._apply_zoom()
        new = self.mapToScene(event.position().toPoint())
        delta = new - old
        self.translate(delta.x(), delta.y())

    def fit_to_content(self) -> None:
        rect = self.scene_obj.itemsBoundingRect()
        if rect.isNull():
            rect = QRectF(-50, -50, 100, 100)
        rect = rect.adjusted(-15, -15, 15, 15)
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        self._zoom = max(0.3, min(40.0, min(vw / rect.width(),
                                            vh / rect.height())))
        self._apply_zoom()
        self.centerOn(rect.center())

    # -- snapping -------------------------------------------------------
    def _snap_candidates(self, exclude=None):
        excl = exclude if isinstance(exclude, (set, list, tuple)) else {exclude}
        pts = []
        for it in self.scene_obj.items():
            if it in excl:
                continue
            if isinstance(it, ShapeItem):
                pts.extend(it.world_snap_nodes())
            elif isinstance(it, StitchLineItem):
                pts.extend(it.line.points)
            elif isinstance(it, HoleItem):
                pts.append(it.hole.point)
        return pts

    def snap(self, pos: QPointF):
        """Return (snapped QPointF, is_node_snap). Pure -- no visuals."""
        p, vtx, _guides, _kind = self._smart_snap(pos)
        return p, vtx

    def _smart_snap(self, pos: QPointF):
        """Snap the cursor. Node snapping and grid snapping toggle
        independently: node snap catches ends / midpoints / centres /
        intersections (and alignment with them); grid snap rounds to the grid.
        With node snap on and grid snap off, points that aren't on a node stay
        free. Returns (snapped QPointF, is_node_snap, guides, kind) where kind is
        'center'|'end'|'mid'|'cross'|'align'|'grid'|None."""
        guides = []
        gridok = self.snap_to_grid and self.snap_grid > 0
        thr = 10.0 / self._zoom          # ~10 px in mm

        if self.snap_to_nodes:
            near = Vec2(pos.x(), pos.y())
            cands = self._typed_candidates(near, thr * 1.5)

            # 1. direct point snap wins (end / midpoint / centre / intersection).
            # High-value osnaps (intersection, endpoint, centre, hole centre) are
            # made "stickier" with a priority weight so you don't have to hover
            # pixel-perfect on a centre to catch it over a closer quadrant/edge.
            prio = {"cross": 0.5, "end": 0.55, "center": 0.55, "hole": 0.65,
                    "mid": 0.9, "quad": 1.1}
            best, best_score, best_d, best_kind = None, thr, thr, None
            for c, kind in cands:
                d = ((pos.x() - c.x) ** 2 + (pos.y() - c.y) ** 2) ** 0.5
                if d >= thr:
                    continue
                score = d * prio.get(kind, 1.0)
                if score < best_score:
                    best_score, best_d, best, best_kind = score, d, c, kind
            if best is not None:
                return QPointF(best.x, best.y), True, guides, best_kind

            # 1b. snap onto the body of a nearby construction/guide line -- the
            # "nearest point on line" osnap, so a diagonal guide is snappable
            # along its whole length, not just at its end/mid nodes.
            foot = self._nearest_on_guide(near, thr)
            if foot is not None:
                return QPointF(foot.x, foot.y), True, guides, "edge"

            # 2. alignment snap: lock x and/or y to an aligned KEY point (ends /
            # centres / midpoints) that's reasonably close -- not every stitch
            # hole or intersection, which would put guides everywhere.
            ax = ay = None
            dx = dy = thr
            lim = 300.0 / self._zoom
            for c, kind in cands:
                if kind in ("hole", "cross") or (c - near).length() > lim:
                    continue
                if abs(c.x - pos.x()) < dx:
                    dx, ax = abs(c.x - pos.x()), c
                if abs(c.y - pos.y()) < dy:
                    dy, ay = abs(c.y - pos.y()), c
            if ax is not None or ay is not None:
                g = self.snap_grid
                nx = ax.x if ax else (round(pos.x() / g) * g if gridok else pos.x())
                ny = ay.y if ay else (round(pos.y() / g) * g if gridok else pos.y())
                if ax is not None:
                    guides.append((ax, "v"))
                if ay is not None:
                    guides.append((ay, "h"))
                return QPointF(nx, ny), True, guides, "align"

        # 3. grid snap (only if enabled)
        if gridok:
            g = self.snap_grid
            return QPointF(round(pos.x() / g) * g, round(pos.y() / g) * g), \
                False, guides, "grid"

        # 4. free
        return pos, False, guides, None

    def _typed_candidates(self, near: Vec2, radius: float):
        """All snap targets as (Vec2, kind): shape nodes (typed), seam points,
        hole centres, nearby outline intersections ('cross') and the midpoints
        of the sub-segments those intersections carve out ('mid')."""
        out = []
        for it in self.scene_obj.items():
            if isinstance(it, ShapeItem):
                out.extend(it.world_snap_nodes_typed())
            elif isinstance(it, StitchLineItem):
                out.extend((p, "end") for p in it.line.points)
            elif isinstance(it, HoleItem):
                out.append((it.hole.point, "center"))
        edges = self._edges_near(near, radius)
        all_edges = self._all_edges()
        out.extend((x, "cross")
                   for x in self._intersection_candidates(edges, near, radius))
        out.extend((p, "mid")
                   for p in self._split_midpoints(edges, all_edges, near, radius))
        return out

    def _all_edges(self):
        """Every outline segment (a, b, owner) in the scene -- used as cutters so
        an edge is split at ALL its junctions even when the crossing line is far
        from the cursor. Bounded so a very busy scene stays responsive."""
        edges = []
        for it in self.scene_obj.items():
            poly = None
            if isinstance(it, ShapeItem):
                poly = [Vec2(p.x, p.y) for p in it.model.world_polyline()[0]]
            elif isinstance(it, StitchLineItem):
                poly = [Vec2(p.x, p.y) for p in it.line.points]
            if not poly or len(poly) < 2:
                continue
            owner = id(it)
            for k in range(len(poly) - 1):
                edges.append((poly[k], poly[k + 1], owner))
            if len(edges) > 4000:
                break
        return edges

    def _nearest_on_guide(self, near: Vec2, radius: float):
        """Nearest point on a construction line or open guide line within
        ``radius`` (the 'nearest' osnap). Skips closed shape outlines so it
        doesn't fire all over solid pieces -- only guides/lines."""
        best, best_d = None, radius
        for it in self.scene_obj.items():
            if isinstance(it, ShapeItem):
                m = it.model
                is_guide = getattr(m, "construction", False) or (
                    isinstance(m, PathShape) and not getattr(m, "close_path", False))
                if not is_guide:
                    continue
                poly = [Vec2(p.x, p.y) for p in m.world_polyline()[0]]
            elif isinstance(it, StitchLineItem):
                poly = [Vec2(p.x, p.y) for p in it.line.points]
            else:
                continue
            for k in range(len(poly) - 1):
                a, b = poly[k], poly[k + 1]
                ab = b - a
                d2 = ab.length_sq()
                if d2 <= 1e-12:
                    continue
                t = max(0.0, min(1.0, (near - a).dot(ab) / d2))
                foot = a.lerp(b, t)
                d = (foot - near).length()
                if d < best_d:
                    best_d, best = d, foot
        return best

    def _edges_near(self, near: Vec2, radius: float):
        """Outline edges (a, b, owner) within ``radius`` of ``near``."""
        edges = []
        for it in self.scene_obj.items():
            poly = None
            if isinstance(it, ShapeItem):
                poly = [Vec2(p.x, p.y) for p in it.model.world_polyline()[0]]
            elif isinstance(it, StitchLineItem):
                poly = [Vec2(p.x, p.y) for p in it.line.points]
            if not poly or len(poly) < 2:
                continue
            owner = id(it)
            for k in range(len(poly) - 1):
                a, b = poly[k], poly[k + 1]
                if _point_polyline_dist(near, [a, b]) <= radius:
                    edges.append((a, b, owner))
        return edges

    def _intersection_candidates(self, edges, near: Vec2, radius: float):
        """Points where two different outlines cross among ``edges``."""
        from leathercad.trim import _seg_intersect
        out = []
        for i in range(len(edges)):
            for j in range(i + 1, len(edges)):
                if edges[i][2] == edges[j][2]:
                    continue                       # same object
                x = _seg_intersect(edges[i][0], edges[i][1],
                                   edges[j][0], edges[j][1])
                if x is not None and (x - near).length() <= radius:
                    out.append(x)
        return out

    def _split_midpoints(self, edges, cutters, near: Vec2, radius: float):
        """Midpoints of the pieces an edge (near the cursor) is cut into by every
        other outline in the scene -- e.g. a line bisected by a construction line
        gives you the 1/4 and 3/4 points, snappable even when you hover on the
        sub-segment far from the crossing (the junctions are real 'nodes')."""
        from leathercad.trim import _seg_intersect
        out = []
        for a, b, owner in edges:
            ab = b - a
            length2 = ab.length_sq()
            if length2 <= 1e-12:
                continue
            ts = [0.0, 1.0]
            for c, d, o2 in cutters:
                if o2 == owner:
                    continue
                x = _seg_intersect(a, b, c, d)
                if x is None:
                    continue
                t = (x - a).dot(ab) / length2
                if 1e-6 < t < 1 - 1e-6:
                    ts.append(t)
            if len(ts) <= 2:                       # nothing cut this edge
                continue
            ts = sorted(set(round(t, 6) for t in ts))
            for i in range(len(ts) - 1):
                p = a.lerp(b, 0.5 * (ts[i] + ts[i + 1]))
                if (p - near).length() <= radius:
                    out.append(p)
        return out

    def _all_intersections(self, exclude=None):
        """Every point where two different outlines cross (for the move / node
        drag snap cache). Bounded work, skipped on very busy scenes."""
        from leathercad.trim import _seg_intersect
        excl = exclude if isinstance(exclude, (set, list, tuple)) else {exclude}
        entries = []
        for it in self.scene_obj.items():
            if it in excl:
                continue
            poly = None
            if isinstance(it, ShapeItem):
                poly = [Vec2(p.x, p.y) for p in it.model.world_polyline()[0]]
            elif isinstance(it, StitchLineItem):
                poly = [Vec2(p.x, p.y) for p in it.line.points]
            if not poly or len(poly) < 2:
                continue
            xs = [p.x for p in poly]
            ys = [p.y for p in poly]
            entries.append((poly, (min(xs), min(ys), max(xs), max(ys))))
        if sum(len(p) for p, _ in entries) > 3000:
            return []                              # too busy -- skip
        out = []
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                A, ba = entries[i]
                B, bb = entries[j]
                if ba[2] < bb[0] or bb[2] < ba[0] or ba[3] < bb[1] or bb[3] < ba[1]:
                    continue                       # bounding boxes disjoint
                for k in range(len(A) - 1):
                    for l in range(len(B) - 1):
                        x = _seg_intersect(A[k], A[k + 1], B[l], B[l + 1])
                        if x is not None:
                            out.append(x)
        return out

    def _show_align_guides(self, guides):
        # ensure two reusable dashed line items
        while len(self._align_guides) < len(guides):
            ln = QGraphicsLineItem()
            ln.setZValue(998)
            pen = QPen(QColor(255, 120, 0, 200), 0, Qt.DashLine)
            pen.setCosmetic(True)
            ln.setPen(pen)
            self.scene_obj.addItem(ln)
            self._align_guides.append(ln)
        big = 100000.0
        for i, ln in enumerate(self._align_guides):
            if i < len(guides):
                target, axis = guides[i]
                if axis == "v":
                    ln.setLine(target.x, target.y - big, target.x, target.y + big)
                else:
                    ln.setLine(target.x - big, target.y, target.x + big, target.y)
                ln.setVisible(True)
            else:
                ln.setVisible(False)

    def _clear_align_guides(self):
        for ln in self._align_guides:
            ln.setVisible(False)

    # marker glyph per snap kind: square=end, circle=centre, diamond=midpoint,
    # X=intersection, small dot=grid, plus-with-ring for alignment.
    def _glyph_path(self, pt, kind, r):
        path = QPainterPath()
        xa, ya = getattr(pt, "x"), getattr(pt, "y")
        x = xa() if callable(xa) else xa          # QPointF (method) or Vec2 (attr)
        y = ya() if callable(ya) else ya
        if kind == "center":
            path.addEllipse(QPointF(x, y), r, r)
        elif kind == "hole":                      # small ring (stitch hole)
            path.addEllipse(QPointF(x, y), r * 0.7, r * 0.7)
        elif kind in ("mid", "quad"):             # diamond
            path.moveTo(x, y - r); path.lineTo(x + r, y)
            path.lineTo(x, y + r); path.lineTo(x - r, y); path.closeSubpath()
        elif kind == "cross":                     # X
            path.moveTo(x - r, y - r); path.lineTo(x + r, y + r)
            path.moveTo(x - r, y + r); path.lineTo(x + r, y - r)
        elif kind == "grid":
            path.addEllipse(QPointF(x, y), r * 0.35, r * 0.35)
        else:                                     # 'end' / default: square
            path.addRect(x - r, y - r, 2 * r, 2 * r)
        return path

    def _show_snap_marker(self, pt: QPointF, kind):
        if not kind:
            if self._snap_marker is not None:
                self._snap_marker.setVisible(False)
            return
        if self._snap_marker is None:
            self._snap_marker = QGraphicsPathItem()
            self._snap_marker.setZValue(1000)
            self.scene_obj.addItem(self._snap_marker)
        r = 6.0 / self._zoom
        path = QPainterPath() if kind == "align" else self._glyph_path(pt, kind, r)
        if kind in ("align", "grid"):             # crosshair through the point
            path.moveTo(pt.x() - r * 1.6, pt.y()); path.lineTo(pt.x() + r * 1.6, pt.y())
            path.moveTo(pt.x(), pt.y() - r * 1.6); path.lineTo(pt.x(), pt.y() + r * 1.6)
        self._snap_marker.setPath(path)
        color = QColor(150, 150, 150) if kind == "grid" else QColor(255, 120, 0)
        pen = QPen(color, 0)
        pen.setCosmetic(True)
        self._snap_marker.setPen(pen)
        self._snap_marker.setBrush(Qt.NoBrush)
        self._snap_marker.setVisible(True)

    def _show_snap_nodes(self, cursor: QPointF):
        """Faintly mark the snap targets near the cursor so you can see where a
        click will land (circle centres, midpoints, ends, intersections)."""
        if not self.snap_to_nodes:
            self._hide_snap_nodes()
            return
        near = Vec2(cursor.x(), cursor.y())
        hl = 60.0 / self._zoom
        r = 3.0 / self._zoom
        path = QPainterPath()
        seen = set()
        for pt, kind in self._typed_candidates(near, hl):
            if (pt - near).length() > hl:
                continue
            key = (round(pt.x, 2), round(pt.y, 2), kind)
            if key in seen:
                continue
            seen.add(key)
            path.addPath(self._glyph_path(pt, kind, r))
        if self._node_hl is None:
            self._node_hl = QGraphicsPathItem()
            self._node_hl.setZValue(996)
            self.scene_obj.addItem(self._node_hl)
        self._node_hl.setPath(path)
        pen = QPen(QColor(255, 150, 40, 190), 0)
        pen.setCosmetic(True)
        self._node_hl.setPen(pen)
        self._node_hl.setBrush(Qt.NoBrush)
        self._node_hl.setVisible(True)

    def _hide_snap_nodes(self):
        if self._node_hl is not None:
            self._node_hl.setVisible(False)

    def _hide_snap_marker(self):
        self._hide_snap_nodes()
        if self._snap_marker is not None:
            self._snap_marker.setVisible(False)
        self._clear_align_guides()

    # -- magnetic node snapping while dragging shapes -------------------
    def _restore_group_movability(self) -> None:
        """Re-enable ItemIsMovable on any items a group drag temporarily froze.
        Self-healing: called at the start of every drag so a leftover frozen
        member (e.g. if a release was missed) can always be moved again."""
        for m in self._nonmovable_members:
            if _alive(m):
                m.setFlag(QGraphicsItem.ItemIsMovable, True)
        self._nonmovable_members = []

    def begin_move_snap(self, item) -> None:
        # capture other shapes' nodes (+ their intersections) once, at drag start
        self._restore_group_movability()
        self._group_drag = None
        if _alive(item):
            item.setFlag(QGraphicsItem.ItemIsMovable, True)   # leader always moves
        if not self.snap_to_nodes:
            self._snap_cache = None
            return
        sel = self.selected_items()
        if len(sel) <= 1:
            self._snap_cache = (self._snap_candidates(exclude=item)
                                + self._all_intersections(exclude=item))
            return
        # A move-group is being dragged: snap the whole group by its leader (the
        # pressed item) and move the other members to match. Exclude every group
        # member from the targets so the group can't snap to itself, and drive
        # the members by hand (Qt would otherwise move each by the raw delta,
        # ignoring the snap and distorting the group).
        self._snap_cache = (self._snap_candidates(exclude=set(sel))
                            + self._all_intersections(exclude=set(sel)))
        leader = item
        members = [it for it in sel if it is not leader]
        lp = leader.pos()
        offsets = []
        for m in sel:
            mp = m.pos()
            bx, by = mp.x() - lp.x(), mp.y() - lp.y()
            for o in getattr(m, "_snap_offsets", None) or [Vec2(0.0, 0.0)]:
                offsets.append(Vec2(bx + o.x, by + o.y))
        self._group_drag = {
            "leader": leader,
            "members": members,
            "leader_start": QPointF(lp),
            "starts": {id(m): QPointF(m.pos()) for m in members},
            "offsets": offsets,
        }
        for m in members:
            m.setFlag(QGraphicsItem.ItemIsMovable, False)
        self._nonmovable_members = list(members)

    def end_move_snap(self) -> None:
        self._restore_group_movability()
        self._group_drag = None
        self._snap_cache = None
        self._hide_snap_marker()

    def snap_move(self, item, value: QPointF) -> QPointF:
        """Snap a dragged shape so one of its nodes lands on a nearby node."""
        if not self.snap_to_nodes or self._snap_cache is None:
            return value
        gd = self._group_drag
        if gd is not None and item is not gd["leader"]:
            return value          # members are driven from the leader (below)
        thr = 12.0 / self._zoom
        vx, vy = value.x(), value.y()

        def _best(cand_offsets):
            bd, bp, bt = thr, None, None
            for off in cand_offsets:
                nx, ny = vx + off.x, vy + off.y
                for s in self._snap_cache:
                    d = ((nx - s.x) ** 2 + (ny - s.y) ** 2) ** 0.5
                    if d < bd:
                        bd, bp, bt = d, QPointF(s.x - off.x, s.y - off.y), s
            return bp, bt

        if gd is not None:
            offsets = gd["offsets"]           # group nodes, relative to leader
        else:
            offsets = getattr(item, "_snap_offsets", None)
        if not offsets:
            return value

        best = best_target = None
        # Circles / ellipses lock by their centre first: try centre-only, and
        # only fall back to quadrants if the centre finds no target in range.
        if gd is None and getattr(item, "_center_snap_priority", False):
            kinds = getattr(item, "_snap_offset_kinds", [])
            centre = [o for o, k in zip(offsets, kinds) if k == "center"]
            if centre:
                best, best_target = _best(centre)
        if best is None:
            best, best_target = _best(offsets)

        if best is None:
            self._hide_snap_marker()
            best = value
        else:
            self._show_snap_marker(QPointF(best_target.x, best_target.y), "end")
        if gd is not None:                    # drag the rest of the group along
            dx = best.x() - gd["leader_start"].x()
            dy = best.y() - gd["leader_start"].y()
            for m in gd["members"]:
                st = gd["starts"][id(m)]
                m.setPos(st.x() + dx, st.y() + dy)
        return best

    # -- node-to-node snapping while editing nodes ----------------------
    def begin_node_snap(self, handle) -> None:
        if not self.snap_to_nodes:
            self._snap_cache = None
            return
        start = handle.pos()
        # snap to OTHER objects (and their intersections), not the shape being
        # edited -- otherwise the node just sticks to its own neighbours.
        owner = getattr(handle, "owner", None)
        cands = (self._snap_candidates(exclude=owner)
                 + self._all_intersections(exclude=owner))
        self._snap_cache = [c for c in cands
                            if (c.x - start.x()) ** 2 + (c.y - start.y()) ** 2 > 0.25]

    def snap_node(self, value: QPointF) -> QPointF:
        if not self.snap_to_nodes or self._snap_cache is None:
            return value
        thr = 10.0 / self._zoom
        best = None
        best_d = thr
        for s in self._snap_cache:
            d = ((value.x() - s.x) ** 2 + (value.y() - s.y) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best = QPointF(s.x, s.y)
        if best is not None:
            self._show_snap_marker(best, "end")
            return best
        self._hide_snap_marker()
        return value

    def end_node_snap(self) -> None:
        self._snap_cache = None
        self._hide_snap_marker()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan_last = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        self._suppress_next_release = False   # clear any stale flag
        raw = self.mapToScene(event.position().toPoint())
        if self.tool == TRIM:
            if event.button() == Qt.LeftButton:
                self._do_trim(Vec2(raw.x(), raw.y()))
            event.accept()
            return
        pos = raw
        if self.tool != SELECT:
            pos, _v = self.snap(pos)
            pos, _o = self._maybe_ortho(raw, pos, event)
        if self.tool == SELECT:
            return super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            if self.tool == HOLE:
                self._place_hole(pos)
            elif self.tool in (MEASURE, DIMENSION):
                # two clicks: first sets the start, second finishes.
                if self._start is None:
                    self._start = pos
                    self._preview = QGraphicsPathItem()
                    pen = QPen(QColor(120, 120, 130), 0, Qt.DashLine)
                    pen.setCosmetic(True)
                    self._preview.setPen(pen)
                    self.scene_obj.addItem(self._preview)
                else:
                    start = self._start
                    self._start = None
                    self._clear_preview()
                    self._suppress_next_release = True
                    if self.tool == MEASURE:
                        self._show_measure(start, pos)
                    else:
                        self._add_dimension(start, pos)
                    self._hide_snap_marker()
            elif self.tool in _DRAG_TOOLS:
                if not self.drag_to_draw and self._start is not None:
                    # click-to-place: this is the second click -> finish. Swallow
                    # the release that follows so the view (now on the Select
                    # tool) doesn't get an unpaired release that leaves the new
                    # shape un-grabbable until you reselect it.
                    start = self._start
                    self._start = None
                    self._clear_preview()
                    self._suppress_next_release = True
                    self._finalize_drag(start, pos)
                    self._hide_snap_marker()
                    self.statusMessage.emit("")
                else:
                    # begin (drag-mode press, or first click of click-to-place)
                    self._start = pos
                    self._preview = QGraphicsPathItem()
                    pen = QPen(QColor(120, 120, 120), 0, Qt.DashLine)
                    pen.setCosmetic(True)
                    self._preview.setPen(pen)
                    self.scene_obj.addItem(self._preview)
            elif self.tool in _POLY_TOOLS:
                self._poly_pts.append(pos)
                self._update_poly_preview(pos)
        event.accept()

    def _measure_text(self, a: QPointF, b: QPointF) -> str:
        import math
        dx, dy = b.x() - a.x(), b.y() - a.y()
        dist = (dx * dx + dy * dy) ** 0.5
        ang = math.degrees(math.atan2(dy, dx))
        return (f"length {dist:.2f} mm   ∠ {ang:.1f}°   "
                f"(dx {dx:.2f}, dy {dy:.2f})")

    def _show_measure(self, a: QPointF, b: QPointF) -> None:
        self.statusMessage.emit(self._measure_text(a, b))
        self.toolFinished.emit()

    def _add_dimension(self, a: QPointF, b: QPointF) -> None:
        from leathercad.dimension import Dimension
        dim = Dimension(p1=Vec2(a.x(), a.y()), p2=Vec2(b.x(), b.y()),
                        offset=8.0, layer="Dimension")
        self.doc.dimensions.append(dim)
        self._add_item(DimensionItem(dim, self))
        self.statusMessage.emit(self._measure_text(a, b))
        self.documentChangedSig.emit()
        self._emit_commit()
        self.toolFinished.emit()

    def _apply_ortho(self, start: QPointF, pos: QPointF) -> QPointF:
        """Constrain ``pos`` to a 0 / 45 / 90 degree ray from ``start`` (Shift)."""
        dx, dy = pos.x() - start.x(), pos.y() - start.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-9:
            return pos
        step = math.pi / 4.0
        ang = round(math.atan2(dy, dx) / step) * step
        return QPointF(start.x() + length * math.cos(ang),
                       start.y() + length * math.sin(ang))

    def _ortho_anchor(self) -> QPointF | None:
        """The point ortho locks are measured from for the current tool."""
        if self._start is not None and self.tool in (LINE, CONSTRUCTION):
            return self._start
        if self._poly_pts and self.tool in _POLY_TOOLS:
            return self._poly_pts[-1]
        return None

    def _maybe_ortho(self, raw: QPointF, pos: QPointF, event) -> tuple[QPointF, bool]:
        """Return (pos, applied): lock to 0/45/90 from the anchor when Shift held."""
        if not (event.modifiers() & Qt.ShiftModifier):
            return pos, False
        anchor = self._ortho_anchor()
        if anchor is None:
            return pos, False
        return self._apply_ortho(anchor, raw), True

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MiddleButton and hasattr(self, "_pan_last"):
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.translate(delta.x() / self._zoom, -delta.y() / self._zoom)
            return
        raw = self.mapToScene(event.position().toPoint())
        self.cursorMoved.emit(raw.x(), raw.y())
        if self.tool == TRIM:
            self._hide_snap_marker()
            self._update_trim_hover(Vec2(raw.x(), raw.y()))
            super().mouseMoveEvent(event)
            return
        pos = raw
        if self.tool != SELECT:
            pos, vtx, guides, kind = self._smart_snap(raw)
            pos, applied = self._maybe_ortho(raw, pos, event)   # 0/45/90 lock
            if applied:
                guides, kind = [], None
            self._show_align_guides(guides)
            self._show_snap_nodes(raw)
            self._show_snap_marker(pos, kind)
            self.statusMessage.emit(_SNAP_LABEL.get(kind, ""))
        else:
            self._hide_snap_marker()
        if (self._preview is not None and self._start is not None
                and self.tool in (MEASURE, DIMENSION)):
            path = QPainterPath()
            path.moveTo(self._start)
            path.lineTo(pos)
            self._preview.setPath(path)
            self.statusMessage.emit(self._measure_text(self._start, pos))
        elif self._preview is not None and self._start is not None:
            self._preview.setPath(self._preview_path(self._start, pos))
            w = abs(pos.x() - self._start.x())
            h = abs(pos.y() - self._start.y())
            if self.tool in (CIRCLE,):
                r = max(w, h)
                self.statusMessage.emit(f"r {r:.1f} mm   ø {2*r:.1f} mm")
            else:
                self.statusMessage.emit(f"{w:.1f} × {h:.1f} mm")
        elif self._poly_pts and self.tool in _POLY_TOOLS:
            self._update_poly_preview(pos)
            last = self._poly_pts[-1]
            seg = ((pos.x() - last.x()) ** 2 + (pos.y() - last.y()) ** 2) ** 0.5
            self.statusMessage.emit(f"segment {seg:.1f} mm   ({len(self._poly_pts)} pts)")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.setCursor(Qt.ArrowCursor)
            return
        if self._suppress_next_release:      # release of a click-to-place finish
            self._suppress_next_release = False
            event.accept()
            return
        pos = self.mapToScene(event.position().toPoint())
        if self.tool != SELECT:
            pos, _v = self.snap(pos)
        # In drag mode, releasing finishes the shape. In click-to-place mode the
        # release after the first click does nothing (the second click finishes).
        if (self.drag_to_draw and self._preview is not None
                and self._start is not None):
            self.scene_obj.removeItem(self._preview)
            self._preview = None
            self._finalize_drag(self._start, pos)
            self._start = None
            self._hide_snap_marker()
            self.statusMessage.emit("")
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self._moved_during_press:
            self._moved_during_press = False
            self._emit_commit()

    def mouseDoubleClickEvent(self, event):
        if self.tool in _POLY_TOOLS and self._poly_pts:
            self._finalize_poly()
            event.accept()
            return
        if self.tool == SELECT:
            pos = self.mapToScene(event.position().toPoint())
            it = self.itemAt(event.position().toPoint())
            owner = it
            while owner is not None and not isinstance(owner, (ShapeItem, StitchLineItem)):
                owner = owner.parentItem()
            if isinstance(owner, StitchLineItem) or (
                    isinstance(owner, ShapeItem)
                    and isinstance(owner.model, (Polygon, PathShape))):
                self.enter_vertex_edit(owner)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        # select the item under the cursor if it isn't already selected
        it = self.itemAt(event.pos())
        owner = it
        while owner is not None and not isinstance(
                owner, (ShapeItem, StitchLineItem, HoleItem)):
            owner = owner.parentItem()
        if owner is not None and not owner.isSelected():
            self.scene_obj.clearSelection()
            owner.setSelected(True)

        sel = self.selected_items()
        shapes = [i for i in sel if isinstance(i, ShapeItem)]
        holes = [i for i in sel if isinstance(i, HoleItem)]
        can_group = len(shapes) == 1 and bool(holes)
        can_ungroup = any(
            isinstance(i, StitchLineItem)
            or (isinstance(i, ShapeItem)
                and (i.model.baked_holes
                     or (i.model.stitch and i.model.stitch.enabled)))
            for i in sel)

        can_nodes = len(shapes) == 1
        parametric = can_nodes and not isinstance(
            shapes[0].model, (Polygon, PathShape, EditablePath))

        menu = QMenu(self)
        a_mgroup = menu.addAction("Group (move together)")
        a_mgroup.setEnabled(len(sel) >= 2)
        a_mungroup = menu.addAction("Ungroup (move group)")
        a_mungroup.setEnabled(self.selection_has_group())
        menu.addSeparator()
        a_group = menu.addAction("Group holes into shape")
        a_group.setEnabled(can_group)
        a_ungroup = menu.addAction("Ungroup stitching → individual holes")
        a_ungroup.setEnabled(can_ungroup)
        a_nodes = menu.addAction("Convert to editable nodes"
                                 if parametric else "Edit nodes")
        a_nodes.setEnabled(can_nodes)
        a_break = menu.addAction("Break apart into segments")
        a_break.setEnabled(bool(shapes))
        a_join = menu.addAction("Join / weld segments")
        a_join.setEnabled(len(shapes) >= 2)
        a_offset = menu.addAction("Offset / seam allowance…")
        a_offset.setEnabled(bool(shapes))
        menu.addSeparator()
        a_dup = menu.addAction("Duplicate")
        a_dup.setEnabled(bool(shapes))
        a_back = menu.addAction("Make back piece (mirror)")
        a_back.setEnabled(bool(shapes))
        a_del = menu.addAction("Delete")
        a_del.setEnabled(bool(sel))
        chosen = menu.exec(event.globalPos())
        if chosen is a_mgroup:
            self.make_group()
        elif chosen is a_mungroup:
            self.ungroup_group()
        elif chosen is a_group:
            self.group_selected()
        elif chosen is a_ungroup:
            self.ungroup_selected()
        elif chosen is a_nodes:
            self.convert_to_nodes()
        elif chosen is a_break:
            self.break_apart_selected()
        elif chosen is a_join:
            self.join_selected()
        elif chosen is a_offset:
            dist, ok = self._ask_offset_distance()
            if ok:
                self.offset_selected(dist)
        elif chosen is a_dup:
            self.duplicate_selected()
        elif chosen is a_back:
            self.make_back_piece_selected()
        elif chosen is a_del:
            self.delete_selected()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            # Esc cancels whatever is in progress; a second Esc (nothing in
            # progress) drops back to the pointer/Select tool.
            busy = (bool(self._poly_pts) or self._start is not None
                    or bool(self._handles))
            self._cancel_poly()
            self.clear_vertex_handles()
            if self._start is not None:      # cancel an in-progress click-draw
                self._start = None
                self._clear_preview()
                self._hide_snap_marker()
                self.statusMessage.emit("")
            if not busy and self.tool != SELECT:
                self.requestSelectTool.emit()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._poly_pts:
                self._finalize_poly()
        else:
            super().keyPressEvent(event)

    # -- preview helpers ------------------------------------------------
    def _preview_path(self, a: QPointF, b: QPointF) -> QPainterPath:
        path = QPainterPath()
        x0, y0 = min(a.x(), b.x()), min(a.y(), b.y())
        w, h = abs(b.x() - a.x()), abs(b.y() - a.y())
        if self.tool in (ELLIPSE, CIRCLE):
            if self.tool == CIRCLE:
                s = max(w, h)
                path.addEllipse(a.x() - s, a.y() - s, 2 * s, 2 * s)
            else:
                path.addEllipse(x0, y0, w, h)
        elif self.tool == ROUNDED:
            r = min(w, h) * 0.18
            path.addRoundedRect(x0, y0, w, h, r, r)
        elif self.tool == SLOT:
            r = min(w, h) / 2.0
            path.addRoundedRect(x0, y0, w, h, r, r)
        elif self.tool in (LINE, CONSTRUCTION):
            path.moveTo(a)
            path.lineTo(b)
        else:
            path.addRect(x0, y0, w, h)
        return path

    def _update_poly_preview(self, cur: QPointF) -> None:
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(QColor(120, 120, 120), 0, Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self.scene_obj.addItem(self._preview)
        path = QPainterPath()
        pts = self._poly_pts + [cur]
        path.moveTo(pts[0])
        for p in pts[1:]:
            path.lineTo(p)
        self._preview.setPath(path)

    def _clear_preview(self) -> None:
        if self._preview is not None:
            self.scene_obj.removeItem(self._preview)
            self._preview = None

    # -- finalize creation ---------------------------------------------
    def _finalize_drag(self, a: QPointF, b: QPointF) -> None:
        w, h = abs(b.x() - a.x()), abs(b.y() - a.y())
        if w < 1 and h < 1:
            return
        cx, cy = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
        if self.tool == RECT:
            sh = Rectangle(width=w, height=h,
                           transform=Transform(x=cx, y=cy),
                           stitch=self._default_stitch(), layer=self._current_layer)
        elif self.tool == ROUNDED:
            sh = Rectangle(width=w, height=h, corner_radius=min(w, h) * 0.18,
                           transform=Transform(x=cx, y=cy),
                           stitch=self._default_stitch(), layer=self._current_layer)
        elif self.tool == CIRCLE:
            s = max(w, h)
            sh = Circle(rx=s, ry=s, transform=Transform(x=a.x(), y=a.y()),
                        stitch=self._default_stitch(), layer=self._current_layer)
        elif self.tool == ELLIPSE:
            sh = Ellipse(rx=w / 2, ry=h / 2, transform=Transform(x=cx, y=cy),
                         stitch=self._default_stitch(), layer=self._current_layer)
        elif self.tool == SLOT:
            sh = Rectangle(width=w, height=h, corner_radius=min(w, h) / 2.0,
                           transform=Transform(x=cx, y=cy),
                           layer=self._current_layer)  # slot: cut only, no stitch
        elif self.tool == LINE:
            sh = PathShape(points=[Vec2(a.x() - cx, a.y() - cy),
                                   Vec2(b.x() - cx, b.y() - cy)],
                           close_path=False, transform=Transform(x=cx, y=cy),
                           layer=self._current_layer)
        elif self.tool == CONSTRUCTION:
            # a guide that ends exactly where you snapped -- no overshoot.
            sh = PathShape(points=[Vec2(a.x() - cx, a.y() - cy),
                                   Vec2(b.x() - cx, b.y() - cy)],
                           close_path=False, transform=Transform(x=cx, y=cy),
                           layer=self._current_layer)
            sh.construction = True
        else:
            return
        self.add_shape(sh)
        self.toolFinished.emit()

    def _place_hole(self, pos: QPointF) -> None:
        d = self.hole_tool_diameter
        sh = Circle(rx=d / 2, ry=d / 2, transform=Transform(x=pos.x(), y=pos.y()),
                    layer=self._current_layer)  # hardware hole: cut only
        self.add_shape(sh)
        self.toolFinished.emit()

    def _finalize_poly(self) -> None:
        pts = list(self._poly_pts)
        self._poly_pts = []
        self._clear_preview()
        if len(pts) < 2:
            return
        if self.tool == POLYGON:
            cx = sum(p.x() for p in pts) / len(pts)
            cy = sum(p.y() for p in pts) / len(pts)
            local = [Vec2(p.x() - cx, p.y() - cy) for p in pts]
            sh = Polygon(points=local, close_path=True, sharp_corners=True,
                         transform=Transform(x=cx, y=cy),
                         stitch=self._default_stitch(), layer=self._current_layer)
            self.add_shape(sh)
        elif self.tool == SCORE:
            score_layer = "Score" if self.doc.layer("Score") else self._current_layer
            sh = PathShape(points=[Vec2(p.x(), p.y()) for p in pts],
                           close_path=False, transform=Transform(),
                           layer=score_layer)  # fold / skive / decoration line
            self.add_shape(sh)
        else:  # STITCHLINE
            sl = StitchLine(points=[Vec2(p.x(), p.y()) for p in pts],
                            closed=False,
                            settings=StitchSettings(pitch_mm=3.85, inset=0.0,
                                                    fit="endpoints"))
            self.add_stitch_line(sl)
        self.toolFinished.emit()

    def _cancel_poly(self) -> None:
        self._poly_pts = []
        self._clear_preview()

    # -- vertex editing -------------------------------------------------
    def enter_vertex_edit(self, owner) -> None:
        self.clear_vertex_handles()
        self.clear_resize_handles()
        self._edit_owner = owner
        # Lock the shape while editing so clicks hit the node handles, not the
        # shape (which would drag the whole outline and leave nodes behind).
        owner.setFlag(QGraphicsItem.ItemIsMovable, False)
        for node in owner.editable_nodes():
            h = VertexHandle(node, owner, self)
            self.scene_obj.addItem(h)
            self._handles.append(h)

    def clear_vertex_handles(self) -> None:
        if self._edit_owner is not None and _alive(self._edit_owner):
            self._edit_owner.setFlag(QGraphicsItem.ItemIsMovable, True)
        for h in self._handles:
            self.scene_obj.removeItem(h)
        self._handles = []
        self._edit_owner = None

    # -- box resize handles --------------------------------------------
    def show_resize_handles(self, owner) -> None:
        """Put 8 box-resize grips (4 corners + 4 edge midpoints) on ``owner``."""
        self.clear_resize_handles()
        if owner is None or owner.resize_extents() is None:
            return
        grips = [(-1, -1), (0, -1), (1, -1), (1, 0),
                 (1, 1), (0, 1), (-1, 1), (-1, 0)]
        for g in grips:
            h = ResizeHandle(g, owner, self)
            self.scene_obj.addItem(h)
            self._resize_handles.append(h)

    def clear_resize_handles(self) -> None:
        for h in self._resize_handles:
            self.scene_obj.removeItem(h)
        self._resize_handles = []
        self._active_resize = None

    def resize_handle_moved(self, dragged) -> None:
        # the shape geometry changed: move the sibling grips to the new box and
        # keep the Properties fields in step (also refreshes any snap caches).
        for h in self._resize_handles:
            if h is not dragged:
                h.reposition()
        self.documentChangedSig.emit()

    # -- tracked scene item lifetime -----------------------------------
    def _add_item(self, item):
        self.scene_obj.addItem(item)
        self._live.add(item)
        return item

    def _remove_item(self, item) -> None:
        # Deselect first so the item is never freed while still in the scene's
        # selection set (the cause of the clearSelection use-after-free crash).
        if item.isSelected():
            item.setSelected(False)
        self._live.discard(item)
        self.scene_obj.removeItem(item)

    # -- document <-> scene --------------------------------------------
    def rebuild(self) -> None:
        """Rebuild all items from the document (after open/load)."""
        self.scene_obj.clearSelection()   # drop selection before freeing items
        self.selectionChangedSig.emit()   # let panels release their item refs
        self.scene_obj.clear()
        self._live.clear()
        self._preview = None
        self._snap_marker = None
        self._node_hl = None
        self._trim_hover = None
        self._align_guides = []
        self._handles = []
        self._resize_handles = []
        self._edit_owner = None
        self._group_drag = None
        self._nonmovable_members = []
        for sh in self.doc.shapes:
            self._add_item(ShapeItem(sh, self))
        for sl in self.doc.stitch_lines:
            self._add_item(StitchLineItem(sl, self))
        for h in self.doc.holes:
            self._add_item(HoleItem(h, self))
        for dm in getattr(self.doc, "dimensions", []):
            self._add_item(DimensionItem(dm, self))
        self.apply_layer_visibility()
        self.documentChangedSig.emit()

    def add_shape(self, shape) -> ShapeItem:
        self.doc.add_shape(shape)
        item = self._add_item(ShapeItem(shape, self))
        self.scene_obj.clearSelection()
        item.setSelected(True)
        self.documentChangedSig.emit()
        self._emit_commit()
        return item

    def add_stitch_line(self, line) -> StitchLineItem:
        self.doc.add_stitch_line(line)
        item = self._add_item(StitchLineItem(line, self))
        self.documentChangedSig.emit()
        self._emit_commit()
        return item

    def selected_items(self):
        return [it for it in self.scene_obj.selectedItems()
                if isinstance(it, (ShapeItem, StitchLineItem, HoleItem,
                                   DimensionItem))]

    def delete_selected(self) -> None:
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                self.doc.remove_shape(it.model)
            elif isinstance(it, StitchLineItem):
                self.doc.remove_stitch_line(it.line)
            elif isinstance(it, DimensionItem):
                if it.dim in self.doc.dimensions:
                    self.doc.dimensions.remove(it.dim)
            else:  # HoleItem
                self.doc.remove_hole(it.hole)
            self._remove_item(it)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    def duplicate_selected(self) -> None:
        import copy
        from leathercad.geometry import Vec2
        new_items = []
        self._suppress_commit = True
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                sh = copy.deepcopy(it.model)
                sh.transform.x += 8
                sh.transform.y -= 8
                from leathercad.shapes import _next_id
                sh.shape_id = _next_id("shape")
                sh.group_id = None          # a copy is not in the original group
                new_items.append(self.add_shape(sh))
            elif isinstance(it, HoleItem):
                from leathercad.holes import _next_id as _next_hole_id
                lh = copy.deepcopy(it.hole)
                lh.point = Vec2(it.hole.point.x + 8, it.hole.point.y - 8)
                lh.hole_id = _next_hole_id()
                lh.group_id = None
                self.doc.holes.append(lh)
                new_items.append(self._add_item(HoleItem(lh, self)))
            elif isinstance(it, StitchLineItem):
                from leathercad.stitchline import _next_id as _next_line_id
                sl = copy.deepcopy(it.line)
                sl.points = [Vec2(p.x + 8, p.y - 8) for p in it.line.points]
                sl.line_id = _next_line_id()
                sl.group_id = None
                self.doc.add_stitch_line(sl)
                new_items.append(self._add_item(StitchLineItem(sl, self)))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for it in new_items:
            it.setSelected(True)
        if new_items:
            self.documentChangedSig.emit()
            self._emit_commit()

    # -- array (grid / circular duplication) ---------------------------
    def _clone_item(self, it, translate=(0.0, 0.0), pivot=None,
                    pos_angle=0.0, orient_angle=0.0):
        """Clone one selectable item, rotating its POSITION around ``pivot`` by
        ``pos_angle`` degrees (if given), optionally rotating its ORIENTATION by
        ``orient_angle``, then translating. Fresh ids, ungrouped."""
        import copy
        import math
        from leathercad.geometry import Vec2
        from leathercad.shapes import _next_id as _sid
        from leathercad.holes import _next_id as _hid
        from leathercad.stitchline import _next_id as _lid

        def place(p):
            x, y = p.x, p.y
            if pivot is not None and abs(pos_angle) > 1e-12:
                a = math.radians(pos_angle)
                dx, dy = x - pivot[0], y - pivot[1]
                x = pivot[0] + dx * math.cos(a) - dy * math.sin(a)
                y = pivot[1] + dx * math.sin(a) + dy * math.cos(a)
            return Vec2(x + translate[0], y + translate[1])

        def spin(v):
            if abs(orient_angle) < 1e-12:
                return v
            a = math.radians(orient_angle)
            return Vec2(v.x * math.cos(a) - v.y * math.sin(a),
                        v.x * math.sin(a) + v.y * math.cos(a))

        if isinstance(it, ShapeItem):
            sh = copy.deepcopy(it.model)
            c = place(Vec2(sh.transform.x, sh.transform.y))
            sh.transform.x, sh.transform.y = c.x, c.y
            sh.transform.rotation += orient_angle
            sh.shape_id = _sid("shape")
            sh.group_id = None
            self.doc.add_shape(sh)
            return self._add_item(ShapeItem(sh, self))
        if isinstance(it, HoleItem):
            lh = copy.deepcopy(it.hole)
            lh.point = place(lh.point)
            lh.tangent = spin(lh.tangent)
            lh.hole_id = _hid()
            lh.group_id = None
            self.doc.holes.append(lh)
            return self._add_item(HoleItem(lh, self))
        if isinstance(it, StitchLineItem):
            sl = copy.deepcopy(it.line)
            sl.points = [place(p) for p in sl.points]
            sl.line_id = _lid()
            sl.group_id = None
            self.doc.add_stitch_line(sl)
            return self._add_item(StitchLineItem(sl, self))
        return None

    def array_grid(self, rows: int, cols: int, dx: float, dy: float) -> None:
        """Duplicate the selection into a rows x cols grid (the original stays
        at cell 0,0); dx / dy are the world spacings in mm."""
        sel = list(self.selected_items())
        if not sel or rows < 1 or cols < 1:
            return
        made = []
        self._suppress_commit = True
        for r in range(rows):
            for cc in range(cols):
                if r == 0 and cc == 0:
                    continue
                for it in sel:
                    m = self._clone_item(it, translate=(cc * dx, r * dy))
                    if m:
                        made.append(m)
        self._suppress_commit = False
        self._finish_array(made)

    def array_circular(self, count: int, cx: float, cy: float,
                       total_deg: float = 360.0, rotate_items: bool = True) -> None:
        """Duplicate the selection ``count`` times around (cx, cy). A full 360°
        spreads ``count`` copies evenly; a partial arc spans the copies across
        ``total_deg``. ``rotate_items`` also spins each copy to face out."""
        sel = list(self.selected_items())
        if not sel or count < 2:
            return
        full = abs(total_deg % 360.0) < 1e-6 and abs(total_deg) > 1e-6
        step = total_deg / count if full else total_deg / (count - 1)
        made = []
        self._suppress_commit = True
        for k in range(1, count):
            ang = step * k
            for it in sel:
                m = self._clone_item(it, pivot=(cx, cy), pos_angle=ang,
                                     orient_angle=ang if rotate_items else 0.0)
                if m:
                    made.append(m)
        self._suppress_commit = False
        self._finish_array(made)

    def _finish_array(self, made) -> None:
        self.scene_obj.clearSelection()
        for m in made:
            m.setSelected(True)
        if made:
            self.documentChangedSig.emit()
            self._emit_commit()

    def selection_center(self):
        """World centroid of the current selection's bounding box (array pivot
        default)."""
        items = self.selected_items()
        xs, ys = [], []
        for it in items:
            if isinstance(it, ShapeItem):
                x0, y0, x1, y1 = it.model.bounds()
                xs += [x0, x1]
                ys += [y0, y1]
            elif isinstance(it, HoleItem):
                xs.append(it.hole.point.x)
                ys.append(it.hole.point.y)
            elif isinstance(it, StitchLineItem):
                xs += [p.x for p in it.line.points]
                ys += [p.y for p in it.line.points]
        if not xs:
            return 0.0, 0.0
        return 0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys))

    def _ask_offset_distance(self):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getDouble(
            self, "Offset / seam allowance",
            "Distance (mm)   —   positive = outward, negative = inward:",
            3.0, -100.0, 100.0, 2)

    def offset_selected(self, dist: float) -> None:
        """Offset each selected shape's outline by ``dist`` mm (>0 outward / seam
        allowance, <0 inward) as a NEW shape on the same layer. Arcs are
        flattened -- the result is a polygon following the offset outline."""
        from leathercad.offset import offset_closed, offset_open
        from leathercad.shapes import Polygon, PathShape, _next_id
        if abs(dist) < 1e-9:
            return
        made = []
        self._suppress_commit = True
        for it in self._shape_items():
            wpts, _corners, closed = it.model.world_polyline()
            if len(wpts) < 2:
                continue
            if closed:
                ring = offset_closed(wpts, dist)
                if len(ring) >= 2 and (ring[0] - ring[-1]).length() < 1e-6:
                    ring = ring[:-1]
                if len(ring) < 3:
                    continue
                cx = sum(p.x for p in ring) / len(ring)
                cy = sum(p.y for p in ring) / len(ring)
                sh = Polygon(points=[Vec2(p.x - cx, p.y - cy) for p in ring],
                             close_path=True, transform=Transform(x=cx, y=cy),
                             layer=it.model.layer)
            else:
                line = offset_open(wpts, dist)
                if len(line) < 2:
                    continue
                cx = sum(p.x for p in line) / len(line)
                cy = sum(p.y for p in line) / len(line)
                sh = PathShape(points=[Vec2(p.x - cx, p.y - cy) for p in line],
                               close_path=False, transform=Transform(x=cx, y=cy),
                               layer=it.model.layer)
            sh.shape_id = _next_id("shape")
            self.doc.add_shape(sh)
            made.append(self._add_item(ShapeItem(sh, self)))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for m in made:
            m.setSelected(True)
        if made:
            self.documentChangedSig.emit()
            self._emit_commit()

    def make_back_piece_selected(self) -> None:
        """Duplicate each selected shape as its mirror image -- the matching
        back piece you laser from the reverse side. The mirror keeps every hole
        registered with the front hole-for-hole, so the two pieces stitch
        together back-to-back. The copy is placed just to the right."""
        import copy
        from leathercad.shapes import _next_id
        from leathercad.holes import _next_id as _next_hole_id
        new_items = []
        self._suppress_commit = True
        for it in self._shape_items():
            sh = copy.deepcopy(it.model)
            sh.transform.mirror_x = not sh.transform.mirror_x
            # place the mirrored copy flush to the right of the original
            o_minx, o_miny, o_maxx, o_maxy = it.model.bounds()
            m_minx, _, _, _ = sh.bounds()
            sh.transform.x += (o_maxx + 20.0) - m_minx
            sh.shape_id = _next_id("shape")
            sh.group_id = None
            if sh.name:
                sh.name = sh.name + " (back)"
            new_items.append(self.add_shape(sh))
            # Mirror any LOOSE holes that live inside this shape too, registered
            # to the mirrored copy (baked holes already ride with the shape).
            ot, mt = it.model.transform, sh.transform
            poly = [Vec2(p.x, p.y) for p in it.model.world_polyline()[0]]
            for h in list(self.doc.holes):
                if len(poly) >= 3 and not _point_in_poly(h.point, poly):
                    continue
                if len(poly) < 3:
                    continue
                local = ot.inverse_apply(h.point)
                ltan = ot.inverse_apply_dir(h.tangent)
                nh = copy.deepcopy(h)
                nh.hole_id = _next_hole_id()
                nh.group_id = None
                nh.point = mt.apply(local)
                nh.tangent = mt.apply_dir(ltan)
                self.doc.holes.append(nh)
                new_items.append(self._add_item(HoleItem(nh, self)))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for it in new_items:
            it.setSelected(True)
        if new_items:
            self._emit_commit()

    def _emit_commit(self) -> None:
        if not self._suppress_commit:
            self.commitRequested.emit()

    # -- align / distribute --------------------------------------------
    def _shape_items(self):
        return [it for it in self.selected_items() if isinstance(it, ShapeItem)]

    def align_selected(self, mode: str) -> None:
        items = self._shape_items()
        if len(items) < 2:
            return
        boxes = [(it, it.model.bounds()) for it in items]
        if mode in ("left", "hcenter", "right"):
            if mode == "left":
                target = min(b[0] for _, b in boxes)
                for it, b in boxes:
                    it.model.transform.x += target - b[0]
            elif mode == "right":
                target = max(b[2] for _, b in boxes)
                for it, b in boxes:
                    it.model.transform.x += target - b[2]
            else:
                target = sum((b[0] + b[2]) / 2 for _, b in boxes) / len(boxes)
                for it, b in boxes:
                    it.model.transform.x += target - (b[0] + b[2]) / 2
        else:  # top/vcenter/bottom
            if mode == "bottom":
                target = min(b[1] for _, b in boxes)
                for it, b in boxes:
                    it.model.transform.y += target - b[1]
            elif mode == "top":
                target = max(b[3] for _, b in boxes)
                for it, b in boxes:
                    it.model.transform.y += target - b[3]
            else:
                target = sum((b[1] + b[3]) / 2 for _, b in boxes) / len(boxes)
                for it, b in boxes:
                    it.model.transform.y += target - (b[1] + b[3]) / 2
        for it, _ in boxes:
            it.sync_from_model()
        self.documentChangedSig.emit()
        self._emit_commit()

    def distribute_selected(self, horizontal: bool) -> None:
        items = self._shape_items()
        if len(items) < 3:
            return
        def center(it):
            b = it.model.bounds()
            return ((b[0] + b[2]) / 2) if horizontal else ((b[1] + b[3]) / 2)
        items.sort(key=center)
        lo, hi = center(items[0]), center(items[-1])
        step = (hi - lo) / (len(items) - 1)
        for i, it in enumerate(items[1:-1], start=1):
            c = center(it)
            target = lo + step * i
            if horizontal:
                it.model.transform.x += target - c
            else:
                it.model.transform.y += target - c
            it.sync_from_model()
        self.documentChangedSig.emit()
        self._emit_commit()

    def item_moved(self, item) -> None:
        self._moved_during_press = True
        self._reposition_resize_handles()
        self.documentChangedSig.emit()

    def selection_changed(self) -> None:
        if self._edit_owner is not None and not self._edit_owner.isSelected():
            self.clear_vertex_handles()
        self._refresh_resize_handles()
        self.selectionChangedSig.emit()

    def _refresh_resize_handles(self) -> None:
        """Show box-resize grips when exactly one resizable shape is selected
        and we're not in vertex-edit mode; otherwise hide them."""
        if self._edit_owner is not None:
            self.clear_resize_handles()
            return
        sel = [it for it in self.selected_items() if isinstance(it, ShapeItem)]
        if len(sel) == 1 and sel[0].resize_extents() is not None:
            self.show_resize_handles(sel[0])
        else:
            self.clear_resize_handles()

    def _reposition_resize_handles(self) -> None:
        # never reposition the handle the user is actively dragging -- that
        # would snap it back to the pre-resize box and fight the drag.
        for h in self._resize_handles:
            if h is self._active_resize:
                continue
            if _alive(h) and _alive(h.owner):
                h.reposition()

    def refresh_item(self, item) -> None:
        if item is None or not _alive(item):
            return
        item.sync_from_model()
        self._reposition_resize_handles()
        self.documentChangedSig.emit()

    def refresh_all(self) -> None:
        for it in self.scene_obj.items():
            if isinstance(it, (ShapeItem, StitchLineItem)):
                it.sync_from_model()
        self.apply_layer_visibility()

    def _layer_visible(self, name: str) -> bool:
        lyr = self.doc.layer(name)
        return lyr.visible if lyr else True

    def stitch_layer_visible(self) -> bool:
        """Visibility of the stitch layer -- the blue stitch holes a shape draws
        belong to it, not to the shape's own (cut) layer."""
        for lyr in self.doc.layers:
            if getattr(lyr, "role", None) == "stitch" or lyr.name == "Stitch":
                return lyr.visible
        return True

    def apply_layer_visibility(self) -> None:
        """Show/hide each scene item according to its layer's ``visible`` flag.
        A shape stays visible if EITHER its outline layer is on, OR it carries
        stitch holes and the stitch layer is on -- so hiding Cut still lets its
        blue stitching show (and vice-versa)."""
        stitch_vis = self.stitch_layer_visible()
        for it in self.scene_obj.items():
            if isinstance(it, ShapeItem):
                outline_vis = self._layer_visible(it.model.layer)
                has_holes = bool(it._holes and it._holes.count)
                vis = outline_vis or (has_holes and stitch_vis)
                it.update()            # re-evaluate which parts to draw
            elif isinstance(it, StitchLineItem):
                vis = self._layer_visible(it.line.layer)
            elif isinstance(it, HoleItem):
                vis = self._layer_visible(it.hole.layer)
            else:
                continue
            if not vis and it.isSelected():
                it.setSelected(False)      # don't leave hidden items selected
            it.setVisible(vis)

    def layer_color(self, name: str) -> str:
        lyr = self.doc.layer(name)
        return lyr.color if lyr else "#0066ff"

    def total_holes(self) -> int:
        n = 0
        for it in self.scene_obj.items():
            if isinstance(it, (ShapeItem, StitchLineItem, HoleItem)):
                n += it.hole_count
        return n

    # -- ungroup: shape/seam stitching -> individual holes -------------
    def symmetry_report_selected(self) -> str:
        """Report whether the selected shape's holes are flip-symmetric (so a
        flipped piece lines up back-to-back). Checked in the piece's own frame."""
        shapes = [it for it in self.selected_items() if isinstance(it, ShapeItem)]
        if len(shapes) != 1:
            return "Select a single shape with stitching to check."
        sh = shapes[0].model
        if sh.baked_holes:
            local = [h.point for h in sh.baked_holes]
        elif sh.stitch and sh.stitch.enabled:
            path = sh.local_path()
            res = stitch_polyline([Vec2(p.x, p.y) for p in path.flatten()],
                                  list(path.corner_points), path.closed, sh.stitch)
            local = [h.point for h in res.holes]
        else:
            return "This shape has no stitch holes to check."
        if not local:
            return "This shape has no stitch holes to check."

        def line(axis, label):
            ok, off, un = flip_symmetry(local, axis)
            if ok:
                return f"• {label}: SYMMETRIC ✓"
            return (f"• {label}: not symmetric — {un} of {len(local)} holes off "
                    f"by up to {off:.2f} mm")

        return (f"Back-to-back / flip symmetry ({len(local)} holes):\n\n"
                + line("vertical", "Flip left ↔ right") + "\n"
                + line("horizontal", "Flip top ↔ bottom") + "\n\n"
                "Tip: set Stitching → Symmetry to force it, if the shape itself "
                "is symmetric about that axis.")

    def _shape_world_holes(self, sh):
        """(StitchResult in world coords, style) for a shape's current holes,
        or (None, None) if it has none."""
        if sh.baked_holes:
            res = StitchResult(holes=[
                Hole(sh.transform.apply(h.point), sh.transform.apply_dir(h.tangent))
                for h in sh.baked_holes])
            return res, (sh.stitch or StitchSettings())
        if sh.stitch and sh.stitch.enabled:
            return stitch_polyline(*sh.world_polyline(), sh.stitch), sh.stitch
        return None, None

    def ungroup_selected(self) -> None:
        """Explode selected shapes'/seams' holes into individual, directly
        selectable/deletable holes (auto-spacing is turned off)."""
        made = []
        self._suppress_commit = True
        for it in list(self.selected_items()):
            if isinstance(it, ShapeItem):
                sh = it.model
                res, style = self._shape_world_holes(sh)
                if res and res.count:
                    made += self._explode(res, style)
                    sh.baked_holes = None
                    if sh.stitch:
                        sh.stitch.enabled = False
                    it.sync_from_model()
            elif isinstance(it, StitchLineItem):
                res = it.line.result()
                if res.count:
                    made += self._explode(res, it.line.settings)
                    self.doc.remove_stitch_line(it.line)
                    self._remove_item(it)
        self._suppress_commit = False
        if made:
            self.scene_obj.clearSelection()
            for m in made:
                m.setSelected(True)
            self.documentChangedSig.emit()
            self._emit_commit()

    def _explode(self, res, style):
        items = []
        for h in res.holes:
            lh = LooseHole(
                point=h.point, tangent=h.tangent,
                hole_style=style.hole_style, hole_diameter=style.hole_diameter,
                slit_length=style.slit_length, slit_angle=style.slit_angle,
                layer="Stitch")
            self.doc.add_hole(lh)
            items.append(self._add_item(HoleItem(lh, self)))
        return items

    # -- convert a parametric shape into editable nodes ----------------
    def convert_to_nodes(self) -> None:
        """Turn the selected parametric shape (rect/rounded/ellipse/circle) into
        a Polygon/PathShape whose nodes you can drag, then show its nodes."""
        shapes = [it for it in self.selected_items() if isinstance(it, ShapeItem)]
        if len(shapes) != 1:
            self.statusMessage.emit("Select one shape to convert to nodes")
            return
        it = shapes[0]
        sh = it.model
        if isinstance(sh, (Polygon, PathShape, EditablePath)):
            self.enter_vertex_edit(it)   # already has nodes
            return

        if isinstance(sh, Rectangle) and sh.corner_radius <= 0:
            hw, hh = sh.width / 2.0, sh.height / 2.0
            new = Polygon(points=[Vec2(-hw, -hh), Vec2(hw, -hh),
                                  Vec2(hw, hh), Vec2(-hw, hh)],
                          close_path=True, sharp_corners=True)
        elif isinstance(sh, Rectangle):                 # rounded rect: keep arcs
            new = EditablePath.from_rounded_rect(sh.width, sh.height, sh.corner_radius)
        elif isinstance(sh, Ellipse) and abs(sh.rx - sh.ry) < 1e-9:  # circle: exact arcs
            new = EditablePath.from_ellipse(sh.rx, sh.ry)
        else:                                           # true ellipse etc: flatten
            path = sh.local_path()
            pts = path.flatten()
            closed = path.closed
            if closed and len(pts) > 1 and (pts[0] - pts[-1]).length() < 1e-9:
                pts = pts[:-1]
            new = PathShape(points=[Vec2(p.x, p.y) for p in pts], close_path=closed)
        # preserve everything else
        new.transform = sh.transform
        new.layer = sh.layer
        new.opacity = sh.opacity
        new.stitch = sh.stitch
        new.baked_holes = sh.baked_holes

        idx = self.doc.shapes.index(sh)
        self.doc.shapes[idx] = new
        self._remove_item(it)
        new_item = self._add_item(ShapeItem(new, self))
        self.scene_obj.clearSelection()
        new_item.setSelected(True)
        self.enter_vertex_edit(new_item)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    # -- break a shape into individually movable segments --------------
    # -- trim: cut the segment under the cursor back to its intersections ---
    def _trim_targets(self):
        return [it for it in self._live
                if isinstance(it, ShapeItem) and _alive(it)]

    def _entity_for_trim(self, click: Vec2):
        """The ShapeItem whose outline passes nearest ``click`` (within a few
        px), or None."""
        best, best_d = None, float("inf")
        for it in self._trim_targets():
            wpts, _c, _cl = it.model.world_polyline()
            d = _point_polyline_dist(click, [Vec2(p.x, p.y) for p in wpts])
            if d < best_d:
                best_d, best = d, it
        tol = 12.0 / max(self._zoom, 1e-3)     # ~12 px in mm
        return best if (best is not None and best_d <= tol) else None

    def _trim_cutters(self, exclude):
        """World polylines of every other outline/seam, used as cut lines."""
        cutters = []
        for it in self._live:
            if it is exclude or not _alive(it):
                continue
            if isinstance(it, ShapeItem):
                wpts, _c, closed = it.model.world_polyline()
                poly = [Vec2(p.x, p.y) for p in wpts]
                if closed and poly and (poly[0] - poly[-1]).length() > 1e-9:
                    poly.append(poly[0])
                if len(poly) >= 2:
                    cutters.append(poly)
            elif isinstance(it, StitchLineItem):
                poly = [Vec2(p.x, p.y) for p in it.line.points]
                if len(poly) >= 2:
                    cutters.append(poly)
        return cutters

    def _update_trim_hover(self, click: Vec2) -> None:
        """Highlight, in red, the span the Trim tool would remove for this pick."""
        from leathercad.trim import removed_cell_polyline
        it = self._entity_for_trim(click)
        poly = None
        if it is not None:
            segs = self._segments_world(it.model)
            if segs:
                closed = it.model.world_polyline()[2]
                poly = removed_cell_polyline(segs, self._trim_cutters(it),
                                             click, closed)
        if not poly or len(poly) < 2:
            self._clear_trim_hover()
            return
        if self._trim_hover is None:
            self._trim_hover = QGraphicsPathItem()
            self._trim_hover.setZValue(999)
            self.scene_obj.addItem(self._trim_hover)
        path = QPainterPath()
        path.moveTo(poly[0].x, poly[0].y)
        for p in poly[1:]:
            path.lineTo(p.x, p.y)
        pen = QPen(QColor(230, 40, 40), 3.2)
        pen.setCosmetic(True)
        self._trim_hover.setPath(path)
        self._trim_hover.setPen(pen)
        self._trim_hover.setVisible(True)

    def _clear_trim_hover(self) -> None:
        if self._trim_hover is not None:
            self._trim_hover.setVisible(False)

    def _do_trim(self, click: Vec2) -> None:
        import copy
        from leathercad.trim import trim as _trim
        it = self._entity_for_trim(click)
        if it is None:
            self.statusMessage.emit("Trim: click on part of an outline to cut")
            return
        model = it.model
        segs = self._segments_world(model)
        if not segs:
            return
        closed = model.world_polyline()[2]
        result = _trim(segs, self._trim_cutters(it), click, closed)

        self._suppress_commit = True
        self.doc.remove_shape(model)
        self._remove_item(it)
        made = []
        if result:
            for chain in result:
                conv = [(pts[0], pts[-1], kind,
                         (pts[1] if kind == "arc" and len(pts) >= 3 else None))
                        for kind, pts in chain]
                sh = _path_from_chain(conv, 0.6, model.layer)
                if model.stitch is not None:
                    sh.stitch = copy.deepcopy(model.stitch)
                sh.opacity = model.opacity
                self.doc.add_shape(sh)
                made.append(self._add_item(ShapeItem(sh, self)))
        self._suppress_commit = False

        self.scene_obj.clearSelection()
        for m in made:
            m.setSelected(True)
        self._clear_trim_hover()
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()
        self.statusMessage.emit("Trimmed" if result else "Removed (nothing crossed it)")

    def break_apart_selected(self) -> None:
        """Explode selected shapes into one open path per edge (lines and arcs),
        each recentred with its own transform so you can move them separately."""
        shapes = [it for it in self.selected_items() if isinstance(it, ShapeItem)]
        if not shapes:
            return
        made = []
        self._suppress_commit = True
        for it in shapes:
            segs = self._segments_world(it.model)
            if len(segs) <= 1:
                continue
            # keep the stitching: bake the shape's holes into individual holes
            # (they stay exactly where they are) before splitting the outline.
            res, style = self._shape_world_holes(it.model)
            if res and res.count:
                made += self._explode(res, style)
            for kind, wpts in segs:
                new = _segment_shape(kind, wpts, it.model.layer)
                self.doc.add_shape(new)
                made.append(self._add_item(ShapeItem(new, self)))
            self.doc.remove_shape(it.model)
            self._remove_item(it)
        self._suppress_commit = False
        if made:
            self.scene_obj.clearSelection()
            for m in made:
                m.setSelected(True)
            self.documentChangedSig.emit()
            self.selectionChangedSig.emit()
            self._emit_commit()

    # -- join / weld segments back into a continuous path --------------
    def join_selected(self, tol: float = 0.6) -> None:
        """Chain selected pieces whose endpoints coincide into continuous
        paths (arcs preserved). Disconnected pieces form separate paths."""
        shapes = [it for it in self.selected_items() if isinstance(it, ShapeItem)]
        if len(shapes) < 2:
            self.statusMessage.emit("Select 2+ pieces to join")
            return
        layer = shapes[0].model.layer
        # collect every edge as a world-space segment (a, b, kind, mid)
        segs = []
        for it in shapes:
            for kind, wpts in self._segments_world(it.model):
                if kind == "arc":
                    segs.append((wpts[0], wpts[2], "arc", wpts[1]))
                else:
                    segs.append((wpts[0], wpts[1], "line", None))
        made = []
        remaining = segs
        while remaining:
            chain, remaining = _chain_segments(remaining, tol)
            made.append(_path_from_chain(chain, tol, layer))

        self._suppress_commit = True
        for it in shapes:
            self.doc.remove_shape(it.model)
            self._remove_item(it)
        items = [self._add_item(ShapeItem(self.doc.add_shape(m), self))
                 for m in made]
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for m in items:
            m.setSelected(True)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    def _segments_world(self, sh):
        """Return [(kind, [world points]), ...] -- 'line' has [a,b], 'arc'
        has [a, mid, b]."""
        t = sh.transform
        segs = []
        if isinstance(sh, EditablePath):
            n = len(sh.nodes)
            for i in range(len(sh.edges)):
                a = sh.nodes[i]
                b = sh.nodes[(i + 1) % n]
                e = sh.edges[i]
                if e.kind == "arc" and e.mid is not None:
                    segs.append(("arc", [t.apply(a), t.apply(e.mid), t.apply(b)]))
                else:
                    segs.append(("line", [t.apply(a), t.apply(b)]))
        elif isinstance(sh, (Polygon, PathShape)):
            pts = sh.points
            n = len(pts)
            rng = n if getattr(sh, "close_path", False) else n - 1
            for i in range(rng):
                segs.append(("line", [t.apply(pts[i]), t.apply(pts[(i + 1) % n])]))
        elif isinstance(sh, Rectangle) and sh.corner_radius <= 0:
            hw, hh = sh.width / 2.0, sh.height / 2.0
            c = [Vec2(-hw, -hh), Vec2(hw, -hh), Vec2(hw, hh), Vec2(-hw, hh)]
            for i in range(4):
                segs.append(("line", [t.apply(c[i]), t.apply(c[(i + 1) % 4])]))
        elif isinstance(sh, Rectangle):
            ep = EditablePath.from_rounded_rect(sh.width, sh.height, sh.corner_radius)
            ep.transform = sh.transform
            return self._segments_world(ep)
        elif isinstance(sh, Ellipse) and abs(sh.rx - sh.ry) < 1e-9:
            ep = EditablePath.from_ellipse(sh.rx, sh.ry)
            ep.transform = sh.transform
            return self._segments_world(ep)
        else:  # true ellipse / other: flatten into straight segments
            pts = sh.local_path().flatten()
            for i in range(len(pts) - 1):
                segs.append(("line", [t.apply(pts[i]), t.apply(pts[i + 1])]))
        return segs

    # -- group: individual holes -> baked into a shape -----------------
    def group_selected(self) -> None:
        """Bake selected loose holes into the single selected shape so they
        move with it (no redistribution). Needs exactly one shape selected."""
        sel = self.selected_items()
        shapes = [it for it in sel if isinstance(it, ShapeItem)]
        holes = [it for it in sel if isinstance(it, HoleItem)]
        if len(shapes) != 1 or not holes:
            self.statusMessage.emit(
                "Group: select one shape and the holes to attach to it")
            return
        shape_item = shapes[0]
        sh = shape_item.model
        baked = list(sh.baked_holes) if sh.baked_holes else []
        for h in holes:
            local = sh.transform.inverse_apply(h.hole.point)
            tan = sh.transform.inverse_apply_dir(h.hole.tangent)
            baked.append(Hole(local, tan))
            self.doc.remove_hole(h.hole)
            self._remove_item(h)
        sh.baked_holes = baked
        # keep a style on the shape for baked-hole rendering
        if sh.stitch is None:
            sh.stitch = StitchSettings(enabled=False,
                                       hole_style=holes[0].hole.hole_style,
                                       hole_diameter=holes[0].hole.hole_diameter,
                                       slit_length=holes[0].hole.slit_length,
                                       slit_angle=holes[0].hole.slit_angle)
        shape_item.sync_from_model()
        self.scene_obj.clearSelection()
        shape_item.setSelected(True)
        self.documentChangedSig.emit()
        self._emit_commit()

    # -- move-groups (select & move several items as one) --------------
    @staticmethod
    def _item_model(item):
        """The model object backing a scene item (shape / hole / seam)."""
        if isinstance(item, ShapeItem):
            return item.model
        if isinstance(item, HoleItem):
            return item.hole
        if isinstance(item, StitchLineItem):
            return item.line
        return None

    def make_group(self) -> None:
        """Tag every selected item with a shared group_id so they select and
        move together (a real group, distinct from welding/baking)."""
        sel = self.selected_items()
        if len(sel) < 2:
            self.statusMessage.emit("Group: select two or more items")
            return
        from leathercad.shapes import _next_id
        gid = _next_id("group")
        for it in sel:
            m = self._item_model(it)
            if m is not None:
                m.group_id = gid
        self.documentChangedSig.emit()
        self._emit_commit()

    def ungroup_group(self) -> None:
        """Clear move-group membership from the selected items' groups."""
        gids = {self._item_model(it).group_id for it in self.selected_items()
                if self._item_model(it) is not None}
        gids.discard(None)
        if not gids:
            return
        for it in self.scene_obj.items():
            m = self._item_model(it)
            if m is not None and getattr(m, "group_id", None) in gids:
                m.group_id = None
        self.documentChangedSig.emit()
        self._emit_commit()

    def selection_has_group(self) -> bool:
        return any(getattr(self._item_model(it), "group_id", None)
                   for it in self.selected_items())

    def select_group_of(self, item) -> None:
        """On pressing a grouped item, select the whole group (itself included)
        so Qt's multi-item drag moves every member together. Driven by the mouse
        press -- NOT by selection_changed, whose cascade would re-select members
        while Qt is trying to deselect them (leaving the group 'stuck')."""
        # heal any member a previous group drag left frozen, and make sure the
        # item now being pressed can move (runs on every item's press)
        self._restore_group_movability()
        if _alive(item):
            item.setFlag(QGraphicsItem.ItemIsMovable, True)
        m = self._item_model(item)
        gid = getattr(m, "group_id", None) if m is not None else None
        if not gid:
            return
        for it in self.scene_obj.items():
            mm = self._item_model(it)
            if mm is not None and getattr(mm, "group_id", None) == gid:
                it.setSelected(True)

    # -- grid -----------------------------------------------------------
    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor(250, 250, 248))
        left = math.floor(rect.left() / 10) * 10
        top = math.floor(rect.bottom() / 10) * 10
        minor = QPen(QColor(230, 230, 226), 0)
        minor.setCosmetic(True)
        major = QPen(QColor(210, 210, 205), 0)
        major.setCosmetic(True)
        x = left
        while x < rect.right():
            painter.setPen(major if int(round(x)) % 50 == 0 else minor)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += 10
        y = math.floor(rect.top() / 10) * 10
        while y < rect.bottom():
            painter.setPen(major if int(round(y)) % 50 == 0 else minor)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += 10
        axis = QPen(QColor(200, 160, 160), 0)
        axis.setCosmetic(True)
        painter.setPen(axis)
        painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0))
        painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))

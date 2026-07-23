"""The drawing canvas: a millimetre-accurate, Y-up QGraphicsView.

Coordinates in the scene are millimetres. The view is Y-flipped so positive Y is
up (CAD convention) and matches the exporters. Tools create model shapes; the
selection/move tool drags items (which is how you overlay pieces to check fit).
"""

from __future__ import annotations

import math
from typing import List, Optional

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QPainter, QPen, QPainterPath,
                           QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import (QGraphicsScene, QGraphicsView, QGraphicsPathItem,
                               QGraphicsItem, QGraphicsLineItem, QMenu)

from leathercad.geometry import Vec2
from leathercad.document import Document
from leathercad.shapes import (Rectangle, Ellipse, Circle, Polygon, PathShape,
                               EditablePath, Edge, Transform, arc_through)
from leathercad.stitchsettings import StitchSettings
from leathercad.stitchline import StitchLine
from leathercad.holes import LooseHole
from leathercad.stitching import stitch_polyline, Hole, StitchResult, flip_symmetry
from .items import (ShapeItem, StitchLineItem, VertexHandle, HoleItem,
                    ResizeHandle, RotateHandle, DimensionItem, TextItem,
                    bake_text_contours)

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
TEXT = "text"
PEN = "pen"
FILLET = "fillet"        # click a corner to round it (Shift = chamfer)
EXTEND = "extend"        # click a line's end to grow it to the next outline
OFFSET = "offset"        # click a shape, move inside/outside, click to place
UNDERLAYCAL = "underlaycal"   # two clicks on the tracing image -> real distance
ARC3 = "arc3"            # 3-point arc: start, end, then a point on the arc
ARCCENTER = "arccenter"  # centre arc: centre, start, end (sweeps CCW)
CIRCLE2 = "circle2"      # 2-point circle: two ends of a diameter
CIRCLE3 = "circle3"      # 3-point circle: through three points

_DRAG_TOOLS = (RECT, ROUNDED, ELLIPSE, CIRCLE, SLOT, LINE, CONSTRUCTION)
_POLY_TOOLS = (POLYGON, STITCHLINE, SCORE)
# multi-click primitives (each click drops a point; the shape finalises once it
# has enough points)
_MULTI_TOOLS = (ARC3, ARCCENTER, CIRCLE2, CIRCLE3)
_MULTI_NEED = {ARC3: 3, ARCCENTER: 3, CIRCLE2: 2, CIRCLE3: 3}
# (tool, points already placed) -> prompt for the next click
_MULTI_HINT = {
    (ARC3, 1): "arc: click the end point",
    (ARC3, 2): "arc: click a point on the arc (its bulge)",
    (ARCCENTER, 1): "centre arc: click the start point (sets the radius)",
    (ARCCENTER, 2): "centre arc: click the end point (sweeps counter-clockwise)",
    (CIRCLE2, 1): "circle: click the opposite end of the diameter",
    (CIRCLE3, 1): "circle: click the second point",
    (CIRCLE3, 2): "circle: click the third point",
}

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
    # emitted synchronously on EVERY canvas move (documentChangedSig is
    # rate-limited during drags) -- for cheap must-not-go-stale listeners
    # like the Properties geometry fields
    geometryMovedSig = Signal()
    toolFinished = Signal()
    requestSelectTool = Signal()  # Esc with nothing in progress -> pointer
    cursorMoved = Signal(float, float)
    commitRequested = Signal()   # a discrete edit finished -> push undo snapshot
    statusMessage = Signal(str)  # transient hint (live dimensions while drawing)

    def __init__(self, document: Document):
        super().__init__()
        self.doc = document
        self.scene_obj = QGraphicsScene(self)
        # An effectively infinite canvas (Fusion-style). A small scene rect
        # CLAMPS view translation, which silently broke cursor-anchored zoom
        # (the correction pan hit the wall, so points near the view edge slid)
        # and made panning rubber-band at the boundary.
        self.scene_obj.setSceneRect(-100000, -100000, 200000, 200000)
        self.setScene(self.scene_obj)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.fillet_radius: Optional[float] = None   # set by the toolbar box
        self.fillet_chamfer: bool = False            # Round vs Chamfer mode
        self.dark = False        # canvas swatches (paper/grid/axis), see theme.py
        self._underlay_item = None   # tracing photo behind the drawing
        self._cal_pts: List[QPointF] = []    # underlay calibration clicks

        # in-progress construction state
        self._start: Optional[QPointF] = None
        self._preview: Optional[QGraphicsPathItem] = None
        self._poly_pts: List[QPointF] = []
        # pen / bezier tool: anchor points + per-anchor out-handle offsets
        self._pen_pts: List[QPointF] = []
        self._pen_handles: List[Optional[QPointF]] = []
        self._pen_drag = False
        self._pen_hud: Optional[QGraphicsPathItem] = None
        self._pen_rmb = False    # a right-click just finished a pen curve
        # multi-click arc / circle tools: collected click points (scene coords)
        self._multi_pts: List[QPointF] = []
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
        self._snap_grid = None    # same nodes bucketed for O(1) lookup
        self._snap_cell = 1.0     # grid cell size == snap threshold (mm)
        self._group_drag = None   # active move-group drag state
        self._group_driving = False   # snap_move is repositioning members
        self._last_move_refresh = 0.0     # rate-limit for item_moved refresh
        self._move_refresh_pending = False
        # interactive Offset tool state (armed shape + live preview)
        self._offset_item = None          # the ShapeItem being offset
        self._offset_pts = []             # its world outline (flattened)
        self._offset_closed = True
        self._offset_dist = 0.0           # live signed distance (mm)
        self._offset_preview = None       # dashed QGraphicsPathItem
        self._nonmovable_members = []   # items we temporarily froze for a group drag
        self.line_width = 1.0     # on-screen outline stroke width (cosmetic px)
        # colour of the line/outline WHILE drawing (the live preview), for
        # visibility over a tracing photo. The finished shape reverts to its
        # layer colour -- this only tints the in-progress preview. None -> the
        # default bright blue.
        self.draw_color: Optional[QColor] = None
        self._default_preview_color = QColor(30, 140, 255)
        # when True, dragging a resize grip keeps the shape's aspect ratio;
        # holding Shift inverts it (so Shift always toggles aspect-lock).
        self.aspect_lock = False

        # snapping -- grid and node snapping toggle independently
        self.snap_to_nodes = True    # ends / midpoints / centres / intersections
        self.snap_to_grid = True
        self.snap_grid = 1.0         # mm
        self._snap_marker: Optional[QGraphicsPathItem] = None
        self._node_hl: Optional[QGraphicsPathItem] = None
        self._trim_hover: Optional[QGraphicsPathItem] = None
        self._align_guides: List[QGraphicsLineItem] = []

        self._selchg_emit_pending = False
        self._selected_shapes = set()     # live tally for O(1) resize-grip test
        self._suspend_move_refresh = False   # set during bulk creates (duplicate)
        self.scene_obj.selectionChanged.connect(self._emit_selection_changed)

    # -- zoom / view ----------------------------------------------------
    def _apply_zoom(self) -> None:
        self.setTransform(QTransform().scale(self._zoom, -self._zoom))

    ZOOM_MIN = 0.05     # px per mm -- far enough out for a whole belt
    ZOOM_MAX = 120.0

    def wheelEvent(self, event):
        # Fusion-style zoom: the scene point under the cursor stays under the
        # cursor (zoom about the mouse, not the view centre).
        self.zoom_at(event.position().toPoint(),
                     1.0015 ** event.angleDelta().y())

    def zoom_at(self, view_pos, factor: float) -> None:
        old = self.mapToScene(view_pos)
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
        self._apply_zoom()
        new = self.mapToScene(view_pos)
        delta = new - old
        self.translate(delta.x(), delta.y())

    def fit_to_content(self) -> None:
        rect = QRectF()
        for it in self.scene_obj.items():
            if it is self._underlay_item:
                continue                 # the tracing photo isn't content
            rect = rect.united(it.sceneBoundingRect())
        if rect.isNull():
            rect = QRectF(-50, -50, 100, 100)
        rect = rect.adjusted(-15, -15, 15, 15)
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX,
                                            min(vw / rect.width(),
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
        # very busy scenes (dense imports): thin the cache so every mouse-move
        # scan stays fast; snapping degrades gracefully instead of lagging
        if len(pts) > 6000:
            step = len(pts) // 6000 + 1
            pts = pts[::step]
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
                poly = it.world_outline()
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
                poly = it.world_outline()
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
                poly = it.world_outline()
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

    def crosshair_cursor(self) -> QCursor:
        """A precision '+' cursor with a white halo so it stays visible over a
        busy tracing photo (the plain OS crosshair is a thin black line that
        vanishes on dark or noisy images). Built once, cached."""
        cur = getattr(self, "_crosshair_cur", None)
        if cur is not None:
            return cur
        S = 29
        pm = QPixmap(S, S)
        pm.fill(Qt.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.Antialiasing, False)
        c = S // 2
        gap = 3                       # small hole at the centre so the exact
        arm = c - 2                   # point is never covered
        segs = [((c, 1), (c, c - gap)), ((c, c + gap), (c, S - 2)),
                ((1, c), (c - gap, c)), ((c + gap, c), (S - 2, c))]
        # white halo underneath, dark line on top
        for width, col in ((3.0, QColor(255, 255, 255, 230)),
                           (1.0, QColor(20, 20, 20, 255))):
            pen = QPen(col, width)
            pr.setPen(pen)
            for (x0, y0), (x1, y1) in segs:
                pr.drawLine(x0, y0, x1, y1)
        pr.end()
        cur = QCursor(pm, c, c)       # hotspot dead centre
        self._crosshair_cur = cur
        return cur

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
        pen = QPen(color, 2.0)          # thicker so it reads over a tracing photo
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
        # Don't fight the node-edit lock: the item being edited stays frozen so a
        # stray press on its body can't drag it out from under its handles.
        if self._edit_owner is not None and item is self._edit_owner:
            return
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
            self._build_snap_grid()
            return
        # A move-group is being dragged: snap the whole group by its leader (the
        # pressed item) and move the other members to match. Exclude every group
        # member from the targets so the group can't snap to itself, and drive
        # the members by hand (Qt would otherwise move each by the raw delta,
        # ignoring the snap and distorting the group).
        self._snap_cache = (self._snap_candidates(exclude=set(sel))
                            + self._all_intersections(exclude=set(sel)))
        self._build_snap_grid()
        leader = item
        members = [it for it in sel if it is not leader]
        lp = leader.pos()
        offsets = []
        for m in sel:
            mp = m.pos()
            bx, by = mp.x() - lp.x(), mp.y() - lp.y()
            for o in getattr(m, "_snap_offsets", None) or [Vec2(0.0, 0.0)]:
                offsets.append(Vec2(bx + o.x, by + o.y))
        # a huge multi-select would mean scanning thousands of group nodes on
        # every mouse move -- thin them; group snapping degrades gracefully
        if len(offsets) > 600:
            offsets = offsets[::len(offsets) // 600 + 1]
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

    def _build_snap_grid(self) -> None:
        """Bucket the drag snap cache into threshold-sized cells. Each mouse
        move then checks the 3x3 neighbourhood of every dragged node instead
        of the whole cache -- the difference between a beachball and a live
        drag when both the selection and the scene are big."""
        cell = 12.0 / self._zoom
        grid: dict = {}
        for s in (self._snap_cache or []):
            grid.setdefault((int(s.x // cell), int(s.y // cell)), []).append(s)
        self._snap_cell = cell
        self._snap_grid = grid

    def end_move_snap(self) -> None:
        self._restore_group_movability()
        self._group_drag = None
        self._snap_cache = None
        self._snap_grid = None
        self._hide_snap_marker()

    def snap_move(self, item, value: QPointF) -> QPointF:
        """Snap a dragged shape so one of its nodes lands on a nearby node."""
        if not self.snap_to_nodes or self._snap_cache is None:
            return value
        gd = self._group_drag
        if gd is not None and item is not gd["leader"]:
            return value          # members are driven from the leader (below)
        # threshold frozen at drag start (== the grid cell), so the 3x3-cell
        # lookup below is guaranteed to cover it
        thr = self._snap_cell if self._snap_grid is not None else 12.0 / self._zoom
        vx, vy = value.x(), value.y()
        grid = self._snap_grid

        def _best(cand_offsets):
            bd, bp, bt = thr, None, None
            for off in cand_offsets:
                nx, ny = vx + off.x, vy + off.y
                if grid is not None:
                    ix, iy = int(nx // thr), int(ny // thr)
                    near = []
                    for gx in (ix - 1, ix, ix + 1):
                        for gy in (iy - 1, iy, iy + 1):
                            near.extend(grid.get((gx, gy), ()))
                else:
                    near = self._snap_cache
                for s in near:
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
            # members' item_moved is suppressed while we drive them: the
            # leader reports the move once, instead of N status/panel
            # refreshes per mouse move
            self._group_driving = True
            try:
                for m in gd["members"]:
                    st = gd["starts"][id(m)]
                    m.setPos(st.x() + dx, st.y() + dy)
            finally:
                self._group_driving = False
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
        if self.tool == PEN and event.button() == Qt.RightButton:
            # right-click finishes an open curve (a standard pen-tool finish, and
            # more reliable than a double-click, which needs two clicks to land on
            # the same pixel within the double-click time to register).
            self._pen_rmb = True          # swallow the context menu that follows
            self._finish_pen(closed=False)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._pen_rmb = False
        self._suppress_next_release = False   # clear any stale flag
        raw = self.mapToScene(event.position().toPoint())
        if self.tool == TRIM:
            if event.button() == Qt.LeftButton:
                self._do_trim(Vec2(raw.x(), raw.y()))
            event.accept()
            return
        if self.tool == FILLET:
            if event.button() == Qt.LeftButton:
                self._do_fillet(Vec2(raw.x(), raw.y()))
            event.accept()
            return
        if self.tool == EXTEND:
            if event.button() == Qt.LeftButton:
                self._do_extend(Vec2(raw.x(), raw.y()))
            event.accept()
            return
        if self.tool == OFFSET:
            if event.button() == Qt.LeftButton:
                if self._offset_item is None:
                    self._arm_offset(Vec2(raw.x(), raw.y()))
                else:
                    self._commit_offset()
            elif event.button() == Qt.RightButton:
                self._cancel_offset()
            event.accept()
            return
        if self.tool == UNDERLAYCAL:
            if event.button() == Qt.LeftButton:
                self._cal_pts.append(QPointF(raw))
                if len(self._cal_pts) >= 2:
                    p1, p2 = self._cal_pts[0], self._cal_pts[1]
                    self._cal_pts = []
                    self._clear_preview()
                    self._hide_snap_marker()
                    from PySide6.QtWidgets import QInputDialog
                    real, ok = QInputDialog.getDouble(
                        self, "Calibrate tracing image",
                        "Real distance between your two clicks (mm):",
                        100.0, 0.1, 5000.0, 2)
                    if ok and self.calibrate_underlay(p1, p2, real):
                        self.statusMessage.emit(
                            "Tracing image scaled — 1 mm on screen is now "
                            "1 mm in real life")
                    self.toolFinished.emit()
                else:
                    # anchor a live rubber line at the first click
                    self._preview = QGraphicsPathItem()
                    pen = QPen(self.preview_color(), 2, Qt.DashLine)
                    pen.setCosmetic(True)
                    self._preview.setPen(pen)
                    self.scene_obj.addItem(self._preview)
                    self._show_snap_marker(QPointF(raw.x(), raw.y()), "end")
                    self.statusMessage.emit(
                        "Calibrate: now click the SECOND point of the known "
                        "distance")
            event.accept()
            return
        pos = raw
        if self.tool != SELECT:
            pos, _v = self.snap(pos)
            pos, _o, _k = self._maybe_ortho(raw, pos, event)
        if self.tool == SELECT:
            return super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            if self.tool in _MULTI_TOOLS:
                self._multi_pts.append(pos)
                if len(self._multi_pts) >= _MULTI_NEED[self.tool]:
                    self._finalize_multi()
                else:
                    self._update_multi_preview(pos)
                event.accept()
                return
            if self.tool == PEN:
                # click near the first anchor closes the path; otherwise drop a
                # new anchor and start a (possible) handle drag off it.
                if (len(self._pen_pts) >= 2
                        and self._near(pos, self._pen_pts[0])):
                    self._finish_pen(closed=True)
                else:
                    self._pen_pts.append(pos)
                    self._pen_handles.append(None)
                    self._pen_drag = True
                    self._update_pen_preview(raw, dragging=True)
                event.accept()
                return
            if self.tool == HOLE:
                self._place_hole(pos)
            elif self.tool == TEXT:
                self._place_text(pos)
            elif self.tool in (MEASURE, DIMENSION):
                # two clicks: first sets the start, second finishes.
                if self._start is None:
                    self._start = pos
                    self._preview = QGraphicsPathItem()
                    pen = QPen(self.preview_color(), 2, Qt.DashLine)
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
                    pen = QPen(self.preview_color(), 2, Qt.DashLine)
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
                        offset=8.0, layer="Dimension",
                        a_ref=self._dim_attach(a), b_ref=self._dim_attach(b))
        self.doc.dimensions.append(dim)
        self._add_item(DimensionItem(dim, self))
        self.statusMessage.emit(self._measure_text(a, b))
        self.documentChangedSig.emit()
        self._emit_commit()
        self.toolFinished.emit()

    def _dim_attach(self, world: QPointF):
        """If ``world`` sits on a shape's snap node, return (shape_id, u, v) --
        fractional position in that shape's bounding box -- so the dimension
        endpoint tracks the shape through moves and resizes."""
        tol = 1.5
        best, best_d = None, tol
        for it in self.scene_obj.items():
            if not isinstance(it, ShapeItem):
                continue
            for n in it.world_snap_nodes():
                d = ((n.x - world.x()) ** 2 + (n.y - world.y()) ** 2) ** 0.5
                if d < best_d:
                    best_d, best = d, it
        if best is None:
            return None
        minx, miny, maxx, maxy = best.model.bounds()
        w = (maxx - minx) or 1e-9
        h = (maxy - miny) or 1e-9
        return (best.model.shape_id, (world.x() - minx) / w,
                (world.y() - miny) / h)

    def _dim_world(self, ref, fallback: Vec2) -> Vec2:
        if not ref:
            return fallback
        sid, u, v = ref
        sh = next((s for s in self.doc.shapes if s.shape_id == sid), None)
        if sh is None:
            return fallback
        minx, miny, maxx, maxy = sh.bounds()
        return Vec2(minx + u * (maxx - minx), miny + v * (maxy - miny))

    def update_dimensions(self) -> None:
        """Recompute associative dimension endpoints from their shapes (called
        after a move/resize) and refresh the dimension items' geometry + label."""
        dims = {id(dm): dm for dm in getattr(self.doc, "dimensions", [])}
        for dm in dims.values():
            if dm.a_ref or dm.b_ref:
                dm.p1 = self._dim_world(dm.a_ref, dm.p1)
                dm.p2 = self._dim_world(dm.b_ref, dm.p2)
        for it in self.scene_obj.items():
            if isinstance(it, DimensionItem):
                it.sync_from_model()

    def _place_text(self, pos: QPointF) -> None:
        from PySide6.QtWidgets import QInputDialog
        from leathercad.text import TextShape
        text, ok = QInputDialog.getText(self, "Text", "Lettering:")
        if not ok or not text.strip():
            self.toolFinished.emit()
            return
        size = getattr(self, "text_size", 8.0)
        contours = bake_text_contours(text, "Sans", size)
        tx = TextShape(text=text,
                       contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                       size=size, font_family="Sans",
                       transform=Transform(x=pos.x(), y=pos.y()),
                       layer="Engrave" if self.doc.layer("Engrave") else self._current_layer)
        self.doc.texts.append(tx)
        item = self._add_item(TextItem(tx, self))
        self.scene_obj.clearSelection()
        item.setSelected(True)
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

    def _ortho_edge_snap(self, anchor: QPointF, op: QPointF):
        """Ortho + object snap: when locked to an ortho ray, snap the point to
        where that ray CROSSES a nearby outline / guide edge (e.g. a vertical
        cut line while you draw horizontally). Returns a QPointF or None."""
        if not self.snap_to_nodes:
            return None
        dx, dy = op.x() - anchor.x(), op.y() - anchor.y()
        L = math.hypot(dx, dy)
        if L < 1e-9:
            return None
        ux, uy = dx / L, dy / L                     # unit along the ortho ray
        ox, oy = anchor.x(), anchor.y()
        thr = 10.0 / self._zoom
        best, best_d = None, thr
        for a, b, _owner in self._all_edges():
            ex, ey = b.x - a.x, b.y - a.y
            det = ex * uy - ux * ey                 # ray dir x edge dir
            if abs(det) < 1e-12:                    # parallel: no crossing
                continue
            rx, ry = a.x - ox, a.y - oy
            s = (ux * ry - uy * rx) / det           # param along the edge
            if s < -1e-9 or s > 1 + 1e-9:
                continue                            # crossing is off the edge
            px, py = a.x + s * ex, a.y + s * ey
            if (px - ox) * ux + (py - oy) * uy <= 1e-9:
                continue                            # behind the anchor
            d = math.hypot(px - op.x(), py - op.y())
            if d < best_d:
                best_d, best = d, QPointF(px, py)
        return best

    def _maybe_ortho(self, raw: QPointF, pos: QPointF, event):
        """Return (pos, applied, kind): lock to 0/45/90 from the anchor when
        Shift held. While locked, still snap to where the ortho ray crosses a
        nearby edge, so you can e.g. draw horizontally onto a vertical cut
        line; ``kind`` is 'cross' when such a snap took, else None."""
        if not (event.modifiers() & Qt.ShiftModifier):
            return pos, False, None
        anchor = self._ortho_anchor()
        if anchor is None:
            return pos, False, None
        op = self._apply_ortho(anchor, raw)
        crossed = self._ortho_edge_snap(anchor, op)
        if crossed is not None:
            return crossed, True, "cross"
        return op, True, None

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
        if self.tool == OFFSET:
            self._hide_snap_marker()
            if self._offset_item is not None:
                self._update_offset_preview(Vec2(raw.x(), raw.y()))
            super().mouseMoveEvent(event)
            return
        if self.tool == UNDERLAYCAL and self._cal_pts:
            # live rubber line + on-screen distance from the first calibration
            # click, so it's obvious where the pointer and the span are
            a = self._cal_pts[0]
            if self._preview is not None and _alive(self._preview):
                path = QPainterPath()
                path.moveTo(a)
                path.lineTo(raw)
                self._preview.setPath(path)
            self._show_snap_marker(QPointF(raw.x(), raw.y()), "end")
            d = ((raw.x() - a.x()) ** 2 + (raw.y() - a.y()) ** 2) ** 0.5
            self.statusMessage.emit(
                f"Calibrate: {d:.1f} mm on screen · click the SECOND point")
            super().mouseMoveEvent(event)
            return
        pos = raw
        if self.tool != SELECT:
            pos, vtx, guides, kind = self._smart_snap(raw)
            pos, applied, okind = self._maybe_ortho(raw, pos, event)  # 0/45/90
            if applied:
                guides, kind = [], okind
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
        elif self.tool in _MULTI_TOOLS and self._multi_pts:
            self._update_multi_preview(pos)
            self.statusMessage.emit(_MULTI_HINT.get(
                (self.tool, len(self._multi_pts)), ""))
        elif self.tool == PEN and self._pen_pts:
            if self._pen_drag and (event.buttons() & Qt.LeftButton):
                a = self._pen_pts[-1]                 # pull a free tangent handle
                self._pen_handles[-1] = QPointF(raw.x() - a.x(), raw.y() - a.y())
                self._update_pen_preview(None, dragging=True)
                self.statusMessage.emit("drag to shape the curve · release for a corner")
            else:
                self._update_pen_preview(pos, dragging=False)
                self.statusMessage.emit(
                    f"{len(self._pen_pts)} pts · click to add, drag to curve · "
                    "right-click / Enter to finish, click start to close")
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
        if self.tool == PEN:
            if self._pen_drag:
                self._pen_drag = False
                h = self._pen_handles[-1] if self._pen_handles else None
                if h is not None and (h.x() ** 2 + h.y() ** 2) ** 0.5 < 2.0 / self._zoom:
                    self._pen_handles[-1] = None     # negligible pull -> corner
                self._update_pen_preview(None, dragging=False)
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
        if self.tool == PEN and self._pen_pts:
            self._finish_pen(closed=False)   # double-click ends an open curve
            event.accept()
            return
        if self.tool == SELECT:
            pos = self.mapToScene(event.position().toPoint())
            # already node-editing: a double-click on an edge inserts a node
            if self._edit_owner is not None and self.insert_node_at(
                    Vec2(pos.x(), pos.y())):
                event.accept()
                return
            it = self.itemAt(event.position().toPoint())
            owner = it
            while owner is not None and not isinstance(owner, (ShapeItem, StitchLineItem)):
                owner = owner.parentItem()
            if isinstance(owner, StitchLineItem) or (
                    isinstance(owner, ShapeItem)
                    and isinstance(owner.model,
                                   (Polygon, PathShape, EditablePath))):
                self.enter_vertex_edit(owner)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        # Right-click finishes a pen curve instead of opening the menu. Handle it
        # here too (not just in mousePressEvent) because some platforms deliver
        # the context-menu event on the press -- and swallow the menu that the
        # just-finished right-click would otherwise pop after we switch to Select.
        if self.tool == PEN or self._pen_rmb:
            self._pen_rmb = False
            if self._pen_pts:
                self._finish_pen(closed=False)
            event.accept()
            return
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
        a_union = menu.addAction("Union (merge shapes)")
        a_union.setEnabled(len(shapes) >= 2)
        a_subtract = menu.addAction("Subtract (bottom − top)")
        a_subtract.setEnabled(len(shapes) >= 2)
        a_intersect = menu.addAction("Intersect")
        a_intersect.setEnabled(len(shapes) >= 2)
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
        elif chosen is a_union:
            self.boolean_selected("union")
        elif chosen is a_subtract:
            self.boolean_selected("difference")
        elif chosen is a_intersect:
            self.boolean_selected("intersection")
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
            busy = (bool(self._poly_pts) or bool(self._pen_pts)
                    or bool(self._multi_pts) or bool(self._cal_pts)
                    or self._offset_item is not None
                    or self._start is not None or bool(self._handles))
            self._cancel_poly()
            self._cancel_pen()
            self._cancel_multi()
            self._cancel_offset()
            if self._cal_pts:                # drop the calibration rubber line
                self._cal_pts = []
                self._clear_preview()
                self._hide_snap_marker()
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
            elif self._pen_pts:
                self._finish_pen(closed=False)
            elif self.tool == OFFSET and self._offset_item is not None:
                self._ask_offset_exact()
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
            pen = QPen(self.preview_color(), 2, Qt.DashLine)
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

    # -- pen / bezier tool ---------------------------------------------
    def _near(self, a: QPointF, b: QPointF) -> bool:
        tol = 6.0 / max(self._zoom, 1e-6)      # ~6 px in mm
        return ((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2) ** 0.5 < tol

    def _bez_seg(self, path: QPainterPath, a: QPointF, ha, b: QPointF, hb) -> None:
        """Append the a->b segment: a cubic bezier when either endpoint has a
        tangent handle, else a straight line. ``ha``/``hb`` are out-handle
        offsets; b's *incoming* control point is the mirror of its out handle."""
        if ha is None and hb is None:
            path.lineTo(b)
            return
        c1x = a.x() + (ha.x() if ha is not None else 0.0)
        c1y = a.y() + (ha.y() if ha is not None else 0.0)
        c2x = b.x() - (hb.x() if hb is not None else 0.0)
        c2y = b.y() - (hb.y() if hb is not None else 0.0)
        path.cubicTo(c1x, c1y, c2x, c2y, b.x(), b.y())

    def _update_pen_preview(self, cursor: Optional[QPointF], dragging: bool) -> None:
        pts, handles = self._pen_pts, self._pen_handles
        if not pts:
            return
        curve = QPainterPath()
        curve.moveTo(pts[0])
        for i in range(1, len(pts)):
            self._bez_seg(curve, pts[i - 1], handles[i - 1], pts[i], handles[i])
        # tentative segment to the cursor while hovering (not dragging a handle)
        if not dragging and cursor is not None and pts:
            a, ha = pts[-1], handles[-1]
            self._bez_seg(curve, a, ha, cursor, None)
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(self.preview_color(), 2, Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self.scene_obj.addItem(self._preview)
        self._preview.setPath(curve)

        # HUD: tangent handle line + control dots for the anchor being dragged
        hud = QPainterPath()
        if dragging and handles and handles[-1] is not None:
            a, ho = pts[-1], handles[-1]
            p_in = QPointF(a.x() - ho.x(), a.y() - ho.y())
            p_out = QPointF(a.x() + ho.x(), a.y() + ho.y())
            hud.moveTo(p_in)
            hud.lineTo(p_out)
            r = 1.2 / self._zoom
            for p in (p_in, p_out):
                hud.addEllipse(p, r, r)
        if self._pen_hud is None:
            self._pen_hud = QGraphicsPathItem()
            hpen = QPen(QColor(30, 140, 255), 0)
            hpen.setCosmetic(True)
            self._pen_hud.setPen(hpen)
            self.scene_obj.addItem(self._pen_hud)
        self._pen_hud.setPath(hud)

    def _clear_pen_hud(self) -> None:
        if self._pen_hud is not None:
            self.scene_obj.removeItem(self._pen_hud)
            self._pen_hud = None

    def _finish_pen(self, closed: bool) -> None:
        pts = list(self._pen_pts)
        handles = list(self._pen_handles)
        self._pen_pts = []
        self._pen_handles = []
        self._pen_drag = False
        self._clear_pen_hud()
        self._clear_preview()
        self._hide_snap_marker()
        self.statusMessage.emit("")
        # drop a trailing near-duplicate anchor (e.g. from a finishing dbl-click)
        while len(pts) >= 2 and self._near(pts[-1], pts[-2]):
            pts.pop()
            handles.pop()
        if len(pts) < 2:
            self.toolFinished.emit()
            return
        cx = sum(p.x() for p in pts) / len(pts)
        cy = sum(p.y() for p in pts) / len(pts)
        anchors = [Vec2(p.x() - cx, p.y() - cy) for p in pts]
        outs = [Vec2(h.x(), h.y()) if h is not None else None for h in handles]
        ep = EditablePath.from_bezier(anchors, outs, closed=closed)
        ep.transform = Transform(x=cx, y=cy)
        ep.layer = self._current_layer
        ep.stitch = self._default_stitch()
        self.add_shape(ep)
        self.toolFinished.emit()

    def _cancel_pen(self) -> None:
        self._pen_pts = []
        self._pen_handles = []
        self._pen_drag = False
        self._clear_pen_hud()
        self._clear_preview()

    # -- arc / circle (multi-click) tools -------------------------------
    def _arc_flatten(self, start: QPointF, through: QPointF, end: QPointF):
        """World-space polyline of the arc through three points (empty if the
        three points are collinear)."""
        ep = EditablePath(nodes=[Vec2(start.x(), start.y()), Vec2(end.x(), end.y())],
                          edges=[Edge("arc", Vec2(through.x(), through.y()))],
                          closed=False)                 # identity transform -> world
        return ep.local_path().flatten()

    def _centre_arc_through(self, centre: QPointF, start: QPointF, end: QPointF):
        """For a centre arc: the start/end points snapped to the radius circle and
        a through-point at the mid of the CCW sweep. Returns (s, thru, e) as
        QPointF, or None if degenerate."""
        r = math.hypot(start.x() - centre.x(), start.y() - centre.y())
        if r < 1e-6:
            return None
        a0 = math.atan2(start.y() - centre.y(), start.x() - centre.x())
        a1 = math.atan2(end.y() - centre.y(), end.x() - centre.x())
        sweep = (a1 - a0) % (2 * math.pi)               # CCW from start to end
        if sweep < 1e-6:
            sweep = 2 * math.pi
        am = a0 + sweep / 2.0
        s = QPointF(centre.x() + r * math.cos(a0), centre.y() + r * math.sin(a0))
        e = QPointF(centre.x() + r * math.cos(a1), centre.y() + r * math.sin(a1))
        thru = QPointF(centre.x() + r * math.cos(am), centre.y() + r * math.sin(am))
        return s, thru, e

    def _update_multi_preview(self, cursor: QPointF) -> None:
        pts = self._multi_pts
        path = QPainterPath()
        tool = self.tool
        if tool == CIRCLE2 and len(pts) == 1:
            a, b = pts[0], cursor
            cx, cy = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
            r = math.hypot(b.x() - a.x(), b.y() - a.y()) / 2.0
            path.addEllipse(QPointF(cx, cy), r, r)
        elif tool == CIRCLE3:
            if len(pts) == 1:
                path.moveTo(pts[0]); path.lineTo(cursor)
            elif len(pts) == 2:
                cr = arc_through(Vec2(pts[0].x(), pts[0].y()),
                                 Vec2(pts[1].x(), pts[1].y()),
                                 Vec2(cursor.x(), cursor.y()))
                if cr:
                    c, r = cr[0], cr[1]
                    path.addEllipse(QPointF(c.x, c.y), r, r)
                else:
                    path.moveTo(pts[0]); path.lineTo(pts[1]); path.lineTo(cursor)
        elif tool == ARC3:
            if len(pts) == 1:
                path.moveTo(pts[0]); path.lineTo(cursor)
            elif len(pts) == 2:                          # start, end placed
                poly = self._arc_flatten(pts[0], cursor, pts[1])   # through=cursor
                if len(poly) >= 2:
                    path.moveTo(poly[0].x, poly[0].y)
                    for p in poly[1:]:
                        path.lineTo(p.x, p.y)
                else:
                    path.moveTo(pts[0]); path.lineTo(pts[1])
        elif tool == ARCCENTER:
            if len(pts) == 1:                            # centre placed -> radius
                r = math.hypot(cursor.x() - pts[0].x(), cursor.y() - pts[0].y())
                path.addEllipse(pts[0], r, r)
                path.moveTo(pts[0]); path.lineTo(cursor)
            elif len(pts) == 2:                          # centre, start -> sweep
                tri = self._centre_arc_through(pts[0], pts[1], cursor)
                if tri:
                    s, thru, e = tri
                    poly = self._arc_flatten(s, thru, e)
                    if len(poly) >= 2:
                        path.moveTo(poly[0].x, poly[0].y)
                        for p in poly[1:]:
                            path.lineTo(p.x, p.y)
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(self.preview_color(), 2, Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            self.scene_obj.addItem(self._preview)
        self._preview.setPath(path)

    def _finalize_multi(self) -> None:
        pts = list(self._multi_pts)
        tool = self.tool
        self._multi_pts = []
        self._clear_preview()
        self._hide_snap_marker()
        self.statusMessage.emit("")
        sh = None
        if tool == CIRCLE2 and len(pts) == 2:
            a, b = pts
            cx, cy = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
            r = math.hypot(b.x() - a.x(), b.y() - a.y()) / 2.0
            if r > 1e-3:
                sh = Circle(rx=r, ry=r, transform=Transform(x=cx, y=cy),
                            stitch=self._default_stitch(), layer=self._current_layer)
        elif tool == CIRCLE3 and len(pts) == 3:
            cr = arc_through(Vec2(pts[0].x(), pts[0].y()),
                             Vec2(pts[1].x(), pts[1].y()),
                             Vec2(pts[2].x(), pts[2].y()))
            if cr:
                c, r = cr[0], cr[1]
                sh = Circle(rx=r, ry=r, transform=Transform(x=c.x, y=c.y),
                            stitch=self._default_stitch(), layer=self._current_layer)
        elif tool == ARC3 and len(pts) == 3:
            # points placed as start, end, through
            sh = self._make_arc_shape(pts[0], pts[2], pts[1])
        elif tool == ARCCENTER and len(pts) == 3:
            tri = self._centre_arc_through(pts[0], pts[1], pts[2])
            if tri:
                s, thru, e = tri
                sh = self._make_arc_shape(s, e, thru)
        if sh is not None:
            self.add_shape(sh)
        self.toolFinished.emit()

    def _make_arc_shape(self, start: QPointF, end: QPointF, through: QPointF):
        """A standalone arc as an EditablePath (kept editable, not flattened).
        Returns None if the three points are collinear."""
        if arc_through(Vec2(start.x(), start.y()), Vec2(through.x(), through.y()),
                       Vec2(end.x(), end.y())) is None:
            return None
        allp = [start, end, through]
        cx = sum(p.x() for p in allp) / 3.0
        cy = sum(p.y() for p in allp) / 3.0
        nodes = [Vec2(start.x() - cx, start.y() - cy),
                 Vec2(end.x() - cx, end.y() - cy)]
        edges = [Edge("arc", Vec2(through.x() - cx, through.y() - cy))]
        return EditablePath(nodes=nodes, edges=edges, closed=False,
                            transform=Transform(x=cx, y=cy),
                            stitch=self._default_stitch(), layer=self._current_layer)

    def _cancel_multi(self) -> None:
        self._multi_pts = []
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
        rot = RotateHandle(owner, self)          # spin grip above the box
        self.scene_obj.addItem(rot)
        self._resize_handles.append(rot)

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
        self._underlay_item = None        # freed by scene.clear()
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
        # any in-flight offset preview was freed by scene.clear(); drop the
        # stale wrappers so a later mouse-move rebuilds them instead of crashing
        self._offset_preview = None
        self._offset_item = None
        self._offset_pts = []
        for sh in self.doc.shapes:
            self._add_item(ShapeItem(sh, self))
        for sl in self.doc.stitch_lines:
            self._add_item(StitchLineItem(sl, self))
        for h in self.doc.holes:
            self._add_item(HoleItem(h, self))
        for dm in getattr(self.doc, "dimensions", []):
            self._add_item(DimensionItem(dm, self))
        for tx in getattr(self.doc, "texts", []):
            self._add_item(TextItem(tx, self))
        self._rebuild_underlay()
        self.apply_layer_visibility()
        self.documentChangedSig.emit()

    def convert_circles_to_holes(self) -> None:
        """Reclassify the selected circle shapes as loose stitch holes (for
        imported drawings where holes arrive as plain circles)."""
        from leathercad.holes import LooseHole
        circles = [it for it in self.selected_items()
                   if isinstance(it, ShapeItem) and isinstance(it.model, Circle)]
        if not circles:
            self.statusMessage.emit(
                "Select the circle(s) you want to become stitch holes")
            return
        self._suppress_commit = True
        for it in circles:
            m = it.model
            c = m.transform.apply(Vec2(0.0, 0.0))
            lh = LooseHole(point=Vec2(c.x, c.y), hole_diameter=2.0 * m.rx)
            self.doc.holes.append(lh)
            self.doc.remove_shape(m)
            self._remove_item(it)
            self._add_item(HoleItem(lh, self))
        self._suppress_commit = False
        self.statusMessage.emit(
            f"{len(circles)} circle(s) are now stitch holes — group them to a "
            "shape with Ctrl+Shift+A if they belong to a piece")
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    # -- node add / delete (in node-edit mode) ---------------------------
    @staticmethod
    def _nearest_segment(pts, closed, target: Vec2):
        """(index, projection, dist) of the segment of ``pts`` nearest target.
        Segment i joins pts[i] -> pts[i+1] (wrapping when closed)."""
        best = (None, None, float("inf"))
        n = len(pts)
        m = n if closed else n - 1
        for i in range(m):
            a, b = pts[i], pts[(i + 1) % n]
            ab = b - a
            denom = ab.length_sq()
            t = 0.0 if denom < 1e-12 else max(
                0.0, min(1.0, (target - a).dot(ab) / denom))
            proj = a.lerp(b, t)
            d = (proj - target).length()
            if d < best[2]:
                best = (i, proj, d)
        return best

    def insert_node_at(self, world: Vec2) -> bool:
        """Double-click an edge while node-editing: add a vertex there."""
        it = self._edit_owner
        if it is None or not _alive(it):
            return False
        thr = 10.0 / max(self._zoom, 1e-6)
        if isinstance(it, StitchLineItem):
            pts = it.line.points
            i, proj, d = self._nearest_segment(pts, it.line.closed, world)
            if i is None or d > thr:
                return False
            pts.insert(i + 1, proj)
        elif isinstance(it, ShapeItem):
            sh = it.model
            local = sh.transform.inverse_apply(world)
            if isinstance(sh, (Polygon, PathShape)):
                closed = bool(getattr(sh, "close_path", False))
                i, proj, d = self._nearest_segment(sh.points, closed, local)
                if i is None or d > thr:
                    return False
                sh.points.insert(i + 1, proj)
            elif isinstance(sh, EditablePath):
                n = len(sh.nodes)
                best = (None, None, float("inf"))
                for i, e in enumerate(sh.edges):
                    if e.kind != "line":
                        continue          # arcs/beziers have their own handles
                    a, b = sh.nodes[i], sh.nodes[(i + 1) % n]
                    _idx, proj, d = self._nearest_segment([a, b], False, local)
                    if proj is not None and d < best[2]:
                        best = (i, proj, d)
                i, proj, d = best
                if i is None or d > thr:
                    self.statusMessage.emit(
                        "Add nodes on straight edges (arcs already have a "
                        "midpoint handle)")
                    return False
                sh.nodes.insert(i + 1, proj)
                sh.edges.insert(i, Edge("line"))
            else:
                return False
        else:
            return False
        it.sync_from_model()
        self.enter_vertex_edit(it)        # rebuild handles incl. the new one
        self.statusMessage.emit(
            "Node added — drag it, Alt-click a node to delete it")
        self.documentChangedSig.emit()
        self._emit_commit()
        return True

    def delete_node(self, handle) -> bool:
        """Alt-click a node handle: remove that vertex (an arc's midpoint
        handle straightens the arc into a line)."""
        it = handle.owner
        if not _alive(it):
            return False
        world = Vec2(handle.pos().x(), handle.pos().y())
        if isinstance(it, StitchLineItem):
            pts = it.line.points
            if len(pts) <= 2:
                self.statusMessage.emit("A seam needs at least two points")
                return False
            idx = min(range(len(pts)),
                      key=lambda i: (pts[i] - world).length())
            pts.pop(idx)
        elif isinstance(it, ShapeItem):
            sh = it.model
            local = sh.transform.inverse_apply(world)
            if isinstance(sh, (Polygon, PathShape)):
                closed = bool(getattr(sh, "close_path", False))
                if len(sh.points) <= (3 if closed else 2):
                    self.statusMessage.emit("Can't remove any more nodes")
                    return False
                idx = min(range(len(sh.points)),
                          key=lambda i: (sh.points[i] - local).length())
                sh.points.pop(idx)
            elif isinstance(sh, EditablePath):
                if getattr(handle.node, "is_ctrl", False):
                    self.statusMessage.emit(
                        "Drag the green handles to reshape — delete the "
                        "anchor node instead")
                    return False
                if handle.node.is_mid:    # straighten the arc into a line
                    edge = min((e for e in sh.edges
                                if e.kind == "arc" and e.mid is not None),
                               key=lambda e: (e.mid - local).length(),
                               default=None)
                    if edge is None:
                        return False
                    edge.kind = "line"
                    edge.mid = None
                else:
                    n = len(sh.nodes)
                    if n <= (3 if sh.closed else 2):
                        self.statusMessage.emit("Can't remove any more nodes")
                        return False
                    idx = min(range(n),
                              key=lambda i: (sh.nodes[i] - local).length())
                    m = len(sh.edges)
                    sh.nodes.pop(idx)
                    if sh.closed:
                        sh.edges.pop(idx % m)
                        sh.edges[(idx - 1) % len(sh.edges)] = Edge("line")
                    elif idx == 0:
                        sh.edges.pop(0)
                    elif idx == n - 1:
                        sh.edges.pop(-1)
                    else:
                        sh.edges.pop(idx)
                        sh.edges[idx - 1] = Edge("line")
            else:
                return False
        else:
            return False
        it.sync_from_model()
        self.enter_vertex_edit(it)
        self.statusMessage.emit("Node removed")
        self.documentChangedSig.emit()
        self._emit_commit()
        return True

    # -- area / leather usage --------------------------------------------
    def area_report(self, usable_pct: float = 75.0) -> str:
        """Material usage: area of every closed cut piece (leather is sold by
        the square foot), selection-scoped like the other reports."""
        from leathercad.offset import signed_area
        sel = self.selected_items()
        pool = sel if sel else list(self.scene_obj.items())
        rows = []
        total_mm2 = 0.0
        for it in pool:
            if not isinstance(it, ShapeItem):
                continue
            sh = it.model
            if getattr(sh, "construction", False):
                continue
            lyr = self.doc.layer(sh.layer)
            if lyr is not None and getattr(lyr, "role", "cut") != "cut":
                continue
            pts, _c, closed = sh.world_polyline()
            if not closed or len(pts) < 4:
                continue
            ring = pts[:-1] if (pts[0] - pts[-1]).length() < 1e-9 else pts
            a = abs(signed_area(ring))
            total_mm2 += a
            b = sh.bounds()
            rows.append(f"{sh.name or type(sh).__name__}:  "
                        f"{b[2] - b[0]:.0f}×{b[3] - b[1]:.0f} mm · "
                        f"{a / 100.0:.1f} cm²")
        if not rows:
            return "No closed cut pieces to measure."
        cm2 = total_mm2 / 100.0
        sqft = total_mm2 / 92903.04
        usable = max(usable_pct, 1.0) / 100.0
        buy = sqft / usable
        scope = "selection" if sel else "whole pattern"
        rows += ["", f"Total ({scope}): {cm2:.1f} cm²  =  {sqft:.2f} sq ft",
                 f"Buy ≈ {buy:.2f} sq ft of leather "
                 f"(assuming {usable_pct:g}% of the hide is usable)"]
        return "\n".join(rows)

    # -- extend a line to the next outline --------------------------------
    def _do_extend(self, world: Vec2) -> None:
        """Click near an open path's end: grow it until it meets an outline."""
        from leathercad.trim import _seg_intersect
        pick = 15.0 / max(self._zoom, 1e-6)
        best = None
        best_d = pick
        for it in self.scene_obj.items():
            if not isinstance(it, ShapeItem):
                continue
            sh = it.model
            if isinstance(sh, (Polygon, PathShape)):
                if getattr(sh, "close_path", False) or len(sh.points) < 2:
                    continue
                pts = sh.points
            elif isinstance(sh, EditablePath):
                if sh.closed or len(sh.nodes) < 2:
                    continue
                pts = sh.nodes
            else:
                continue
            t = sh.transform
            for idx in (0, len(pts) - 1):
                w = t.apply(pts[idx])
                d = ((w.x - world.x) ** 2 + (w.y - world.y) ** 2) ** 0.5
                if d < best_d:
                    best_d, best = d, (it, idx)
        if best is None:
            self.statusMessage.emit(
                "Extend: click the END of a line/path to grow it to the next "
                "outline")
            return
        it, idx = best
        sh = it.model
        pts = sh.points if isinstance(sh, (Polygon, PathShape)) else sh.nodes
        t = sh.transform
        end = t.apply(pts[idx])
        nb = t.apply(pts[1] if idx == 0 else pts[-2])
        d = Vec2(end.x - nb.x, end.y - nb.y)
        if d.length() < 1e-9:
            return
        d = d.normalized()
        far = Vec2(end.x + d.x * 10000.0, end.y + d.y * 10000.0)
        hit = None
        hit_d = float("inf")
        for other in self.scene_obj.items():
            if other is it or not isinstance(other, ShapeItem):
                continue
            opts, _c, _cl = other.model.world_polyline()
            for k in range(len(opts) - 1):
                p = _seg_intersect(end, far, opts[k], opts[k + 1])
                if p is None:
                    continue
                dd = ((p.x - end.x) ** 2 + (p.y - end.y) ** 2) ** 0.5
                if 1e-6 < dd < hit_d:
                    hit_d, hit = dd, p
        if hit is None:
            self.statusMessage.emit(
                "Nothing to extend to in that direction")
            return
        pts[idx] = t.inverse_apply(hit)
        it.sync_from_model()
        self.statusMessage.emit(f"Extended {hit_d:.1f} mm to the outline")
        self.documentChangedSig.emit()
        self._emit_commit()

    # -- tracing underlay ------------------------------------------------
    def set_underlay(self, path: str) -> bool:
        """Place a reference photo behind the drawing (traceable, never
        exported). Starts at ~200 mm wide, half opacity, position locked."""
        from PySide6.QtGui import QPixmap
        pm = QPixmap(path)
        if pm.isNull():
            self.statusMessage.emit("Couldn't load that image")
            return False
        scale = 200.0 / max(pm.width(), 1)
        self.doc.underlay = {"path": path, "x": -100.0,
                             "y": pm.height() * scale / 2.0,
                             "scale": scale, "opacity": 0.5, "visible": True}
        self._rebuild_underlay()
        self._emit_commit()
        return True

    def _rebuild_underlay(self) -> None:
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QGraphicsPixmapItem
        if self._underlay_item is not None:
            self.scene_obj.removeItem(self._underlay_item)
            self._underlay_item = None
        u = getattr(self.doc, "underlay", None)
        if not u:
            return
        pm = QPixmap(u["path"])
        if pm.isNull():
            return                        # image moved/deleted: skip quietly
        item = QGraphicsPixmapItem(pm)
        item.setZValue(-1000)             # always behind the drawing
        item.setOpacity(float(u.get("opacity", 0.5)))
        item.setVisible(bool(u.get("visible", True)))
        s = float(u.get("scale", 1.0))
        # flip Y so the photo reads upright in our Y-up world
        item.setTransform(QTransform().scale(s, -s))
        item.setPos(float(u.get("x", 0.0)), float(u.get("y", 0.0)))
        item.setTransformationMode(Qt.SmoothTransformation)
        self.scene_obj.addItem(item)
        self._underlay_item = item

    def underlay_config(self, **kw) -> None:
        """Update underlay settings (opacity / visible / locked / remove)."""
        u = getattr(self.doc, "underlay", None)
        if u is None:
            return
        if kw.pop("remove", False):
            self.doc.underlay = None
            self._rebuild_underlay()
            self._emit_commit()
            return
        u.update({k: v for k, v in kw.items() if k in
                  ("opacity", "visible", "x", "y", "scale")})
        it = self._underlay_item
        if it is not None:
            it.setOpacity(float(u.get("opacity", 0.5)))
            it.setVisible(bool(u.get("visible", True)))
        self._emit_commit()

    def calibrate_underlay(self, p1: QPointF, p2: QPointF,
                           real_mm: float) -> bool:
        """Two clicked scene points a known real distance apart -> rescale the
        photo so that distance measures true, keeping p1 pinned."""
        u = getattr(self.doc, "underlay", None)
        it = self._underlay_item
        if u is None or it is None or real_mm <= 0:
            return False
        measured = ((p2.x() - p1.x()) ** 2 + (p2.y() - p1.y()) ** 2) ** 0.5
        if measured < 1e-6:
            return False
        f = real_mm / measured
        u["scale"] = float(u.get("scale", 1.0)) * f
        # keep the first clicked point fixed while scaling about the item origin
        u["x"] = p1.x() - f * (p1.x() - float(u.get("x", 0.0)))
        u["y"] = p1.y() - f * (p1.y() - float(u.get("y", 0.0)))
        self._rebuild_underlay()
        self._emit_commit()
        return True

    def place_shapes(self, shapes) -> list:
        """Add shapes (e.g. a library part) centred in the current view, as one
        undoable step, and select them."""
        if not shapes:
            return []
        bs = [sh.bounds() for sh in shapes]
        cx = (min(b[0] for b in bs) + max(b[2] for b in bs)) / 2.0
        cy = (min(b[1] for b in bs) + max(b[3] for b in bs)) / 2.0
        target = self.mapToScene(self.viewport().rect().center())
        dx, dy = target.x() - cx, target.y() - cy
        self._suppress_commit = True
        items = []
        for sh in shapes:
            sh.transform.x += dx
            sh.transform.y += dy
            self.doc.add_shape(sh)
            items.append(self._add_item(ShapeItem(sh, self)))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for it in items:
            it.setSelected(True)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()
        return items

    def thread_report(self, thickness_mm: float = 3.0,
                      tail_mm: float = 150.0) -> str:
        """Estimated saddle-stitch thread for the selected stitched items (or
        everything stitched, when nothing is selected)."""
        from leathercad.thread import estimate_thread, format_length
        from leathercad.stitching import holes_for_shape
        from leathercad.stitchsettings import StitchSettings

        sel = self.selected_items()
        pool = sel if sel else list(self.scene_obj.items())
        rows = []
        total = 0.0
        for it in pool:
            if isinstance(it, ShapeItem):
                res = holes_for_shape(it.model)
                if not res.count:
                    continue
                st = it.model.stitch or StitchSettings()
                name = it.model.name or type(it.model).__name__
            elif isinstance(it, StitchLineItem):
                res = it.line.result()
                if not res.count:
                    continue
                st = it.line.settings
                name = it.line.name or "Seam"
            else:
                continue
            est = estimate_thread(res, st, thickness_mm, tail_mm)
            total += est["thread_mm"]
            extra = "  (double row)" if est["rows"] == 2 else ""
            rows.append(f"{name}:  {est['holes']} holes · seam "
                        f"{format_length(est['seam_mm'])} → thread ≈ "
                        f"{format_length(est['thread_mm'])}{extra}")
        if not rows:
            return ("Nothing stitched here yet — enable Stitching on a piece "
                    "or draw a seam first.")
        scope = "selection" if sel else "whole pattern"
        rows += ["", f"Total ({scope}): ≈ {format_length(total)} of thread",
                 f"(assumes {thickness_mm:g} mm total leather stack and "
                 f"{tail_mm:g} mm needle tails per run — cut generously)"]
        return "\n".join(rows)

    def seam_mate_report(self) -> str:
        """Compare the two selected stitched items (shapes or seams): pieces
        sewn together MUST have the same hole count, or assembly fails."""
        from leathercad.stitching import holes_for_shape
        picks = []
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                res = holes_for_shape(it.model)
                if res.count:
                    nm = it.model.name or type(it.model).__name__
                    picks.append((nm, res))
            elif isinstance(it, StitchLineItem):
                res = it.line.result()
                if res.count:
                    picks.append((it.line.name or "Seam", res))
        if len(picks) != 2:
            return ("Select exactly TWO stitched items (pieces or seams) "
                    "to compare — e.g. a body and its gusset.")
        out = []
        counts = []
        for name, res in picks:
            gaps = res.chord_spacings()
            length = sum(gaps)
            counts.append(res.count)
            if gaps:
                out.append(f"{name}:  {res.count} holes ·"
                           f" seam ≈ {length:.1f} mm ·"
                           f" spacing {min(gaps):.2f}–{max(gaps):.2f} mm")
            else:
                out.append(f"{name}:  {res.count} hole")
        diff = abs(counts[0] - counts[1])
        if diff == 0:
            out += ["", "✓ Hole counts MATCH — these seams will sew together."]
        else:
            out += ["", f"✗ Hole counts differ by {diff} — the pieces will NOT "
                        "line up stitch-for-stitch. Match the seam lengths, or "
                        "use one shared Stitch line (seam) so both pieces get "
                        "identical holes."]
        return "\n".join(out)

    def add_guide(self, orientation: str, coord: float) -> ShapeItem:
        """Drop a ruler guide: a long construction line at x (``'v'``) or y
        (``'h'``) = ``coord``. Guides snap along their whole body, never export
        and delete like any shape."""
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        span = max(rect.width(), rect.height()) * 2 + 200
        if orientation == "h":
            a = Vec2(rect.center().x() - span / 2 - 0, 0.0)
            b = Vec2(rect.center().x() + span / 2, 0.0)
            t = Transform(x=0.0, y=coord)
            pts = [Vec2(a.x, 0.0), Vec2(b.x, 0.0)]
        else:
            t = Transform(x=coord, y=0.0)
            pts = [Vec2(0.0, rect.center().y() - span / 2),
                   Vec2(0.0, rect.center().y() + span / 2)]
        sh = PathShape(points=pts, close_path=False, transform=t,
                       layer=self._current_layer)
        sh.construction = True
        return self.add_shape(sh)

    def _do_fillet(self, world: Vec2) -> None:
        """Round (or, in Chamfer mode, bevel) the corner nearest the click, at
        the radius set in the toolbar. Works on a vertex inside one shape AND
        across two separate lines/paths whose ends meet at the click -- those
        are welded into one path first (the draw-two-lines-then-round-the-corner
        workflow)."""
        from leathercad.modify import fillet_vertex, chamfer_vertex, to_editable
        chamfer = bool(getattr(self, "fillet_chamfer", False))

        pick = 15.0 / max(self._zoom, 1e-6)       # generous ~15 px pick radius
        cands = []                                # (dist, item, idx, world pos)
        for it in self.scene_obj.items():
            if not isinstance(it, ShapeItem):
                continue
            sh = it.model
            if getattr(sh, "construction", False):
                continue                          # guides aren't fillet targets
            if isinstance(sh, (Polygon, PathShape)):
                pts = sh.points
            elif isinstance(sh, EditablePath):
                pts = sh.nodes
            else:
                continue
            t = sh.transform
            for i, p in enumerate(pts):
                w = t.apply(p)
                d = ((w.x - world.x) ** 2 + (w.y - world.y) ** 2) ** 0.5
                if d < pick:
                    cands.append((d, it, i, w))
        if not cands:
            self.statusMessage.emit(
                "Fillet: click a corner of a polygon / path, or the point "
                "where two line ends meet. (For a whole rectangle, set its "
                "Corner radius in Properties.)")
            return
        cands.sort(key=lambda c: c[0])
        _d, it, idx, wpos = cands[0]
        if not self.fillet_radius:
            self.fillet_radius = 6.0              # sane default, never a popup

        sh = it.model
        ep_probe = to_editable(sh)
        endpoint = (ep_probe is not None and not ep_probe.closed
                    and idx in (0, len(ep_probe.nodes) - 1))
        if endpoint:
            # an open end: look for another open end meeting it -> weld + round
            partner = self._fillet_partner(it, idx, wpos, cands)
            if partner is not None:
                self._fillet_across(it, idx, partner, chamfer)
                return
            self.statusMessage.emit(
                "Fillet: this is a loose end — bring another line's end to "
                "this point (they snap), then click their corner")
            return

        ep = ep_probe
        if ep is None:
            return
        if ep is not sh:                          # Polygon/PathShape -> editable
            self.doc.shapes[self.doc.shapes.index(sh)] = ep
            self._remove_item(it)
            it = self._add_item(ShapeItem(ep, self))
        done = (chamfer_vertex if chamfer else fillet_vertex)(
            ep, idx, self.fillet_radius)
        if not done:
            self.statusMessage.emit(
                "That corner can't be rounded — it needs a straight edge on "
                "both sides (weld paths first with Ctrl+J if they're separate)")
            return
        it.sync_from_model()
        self._fillet_done_msg(chamfer)
        self.documentChangedSig.emit()
        self._emit_commit()

    def _fillet_done_msg(self, chamfer: bool) -> None:
        self.statusMessage.emit(
            f"{'Chamfered' if chamfer else 'Filleted'} at "
            f"{self.fillet_radius:g} mm — keep clicking corners "
            "(radius / mode in the toolbar)")

    def _fillet_polyline(self, sh):
        """World points of an open, all-straight shape (weldable for fillet),
        or None."""
        if getattr(sh, "construction", False):
            return None
        if isinstance(sh, (Polygon, PathShape)):
            if getattr(sh, "close_path", False):
                return None
            pts = sh.points
        elif isinstance(sh, EditablePath):
            if sh.closed or any(e.kind != "line" for e in sh.edges):
                return None
            pts = sh.nodes
        else:
            return None
        if len(pts) < 2:
            return None
        return [sh.transform.apply(p) for p in pts]

    def _fillet_partner(self, it, idx, wpos, cands):
        """Another shape's open END within a hair of ``wpos`` -> (item, idx)."""
        tol = max(0.75, 8.0 / max(self._zoom, 1e-6))     # snap slop, in mm
        for _d, jt, j, w in cands:
            if jt is it:
                continue
            pts = self._fillet_polyline(jt.model)
            if pts is None or j not in (0, len(pts) - 1):
                continue
            if ((w.x - wpos.x) ** 2 + (w.y - wpos.y) ** 2) ** 0.5 <= tol:
                return jt, j
        return None

    def _fillet_across(self, it_a, idx_a, partner, chamfer: bool) -> None:
        """Weld two open paths at their meeting ends and round that corner."""
        from leathercad.modify import fillet_vertex, chamfer_vertex
        it_b, idx_b = partner
        a = self._fillet_polyline(it_a.model)
        b = self._fillet_polyline(it_b.model)
        if a is None or b is None:
            self.statusMessage.emit(
                "Those paths can't be welded automatically — join them with "
                "Ctrl+J first, then fillet")
            return
        if idx_a == 0:
            a = a[::-1]                            # junction goes at A's end
        if idx_b != 0:
            b = b[::-1]                            # ...and at B's start
        junction = Vec2((a[-1].x + b[0].x) / 2.0, (a[-1].y + b[0].y) / 2.0)
        merged = a[:-1] + [junction] + b[1:]
        closed = len(merged) > 3 and (merged[0] - merged[-1]).length() < 1e-6
        if closed:
            merged = merged[:-1]                   # ends met too: a loop
        cx = sum(p.x for p in merged) / len(merged)
        cy = sum(p.y for p in merged) / len(merged)
        base = it_a.model
        ep = EditablePath(nodes=[Vec2(p.x - cx, p.y - cy) for p in merged],
                          edges=[Edge("line") for _ in
                                 range(len(merged) if closed else len(merged) - 1)],
                          closed=closed,
                          transform=Transform(x=cx, y=cy),
                          layer=base.layer, stitch=base.stitch)
        ep.opacity = base.opacity
        for old in (it_a, it_b):
            self.doc.remove_shape(old.model)
            self._remove_item(old)
        self.doc.add_shape(ep)
        item = self._add_item(ShapeItem(ep, self))
        done = (chamfer_vertex if chamfer else fillet_vertex)(
            ep, len(a) - 1, self.fillet_radius)
        item.sync_from_model()
        self.scene_obj.clearSelection()
        item.setSelected(True)
        if done:
            self._fillet_done_msg(chamfer)
        else:
            self.statusMessage.emit(
                "Lines welded into one path, but that corner couldn't be "
                "rounded (are they parallel?)")
        self.documentChangedSig.emit()
        self._emit_commit()

    def boolean_selected(self, op: str) -> None:
        """Union / difference / intersection of the selected closed shapes.
        The BOTTOM shape (lowest z) is the subject: Subtract removes the upper
        shapes from it, Illustrator "Minus Front" style. The result keeps the
        subject's layer and stitch settings; extra rings (disjoint parts or
        cutouts left by a swallowed cutter) become their own shapes."""
        import copy as _copy
        from leathercad.boolean import combine
        from leathercad.offset import signed_area

        ordered = []
        for it in self.selected_items():
            if not isinstance(it, ShapeItem):
                continue
            pts, _c, closed = it.model.world_polyline()
            if not closed or len(pts) < 4:
                continue
            ring = pts[:-1] if (pts[0] - pts[-1]).length() < 1e-9 else pts
            ordered.append((self.doc.shapes.index(it.model), it, ring))
        if len(ordered) < 2:
            self.statusMessage.emit(
                "Select two or more CLOSED shapes for a boolean operation")
            return
        ordered.sort(key=lambda x: x[0])          # bottom-most first
        base = ordered[0][1].model
        result = combine(op, [ring for _i, _it, ring in ordered])
        keep_stitch = _copy.deepcopy(base.stitch)
        layer, opacity = base.layer, base.opacity
        for _i, it, _r in ordered:
            self.doc.remove_shape(it.model)
            self._remove_item(it)
        if not result:
            self.statusMessage.emit("Nothing left after the operation")
            self.documentChangedSig.emit()
            self._emit_commit()
            return
        result.sort(key=lambda r: -abs(signed_area(r)))
        first_item = None
        for k, ring in enumerate(result):
            cx = sum(p.x for p in ring) / len(ring)
            cy = sum(p.y for p in ring) / len(ring)
            local = [Vec2(p.x - cx, p.y - cy) for p in ring]
            sh = Polygon(points=local, close_path=True, sharp_corners=False,
                         transform=Transform(x=cx, y=cy),
                         stitch=keep_stitch if k == 0 else None, layer=layer)
            sh.opacity = opacity
            self.doc.add_shape(sh)
            item = self._add_item(ShapeItem(sh, self))
            if k == 0:
                first_item = item
        self.scene_obj.clearSelection()
        if first_item is not None:
            first_item.setSelected(True)
        self.documentChangedSig.emit()
        self._emit_commit()

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
                                   DimensionItem, TextItem))]

    def delete_selected(self) -> None:
        items = self.selected_items()
        if not items:
            return
        # Bucket the models to drop by identity, then rebuild each document
        # list in a single pass. Per-item doc.remove_*() does an ``in`` scan +
        # list.remove() -- O(N) each, so deleting M of N holes was O(M*N) (the
        # 20 s stall on a 20k-hole document).
        del_shapes, del_lines, del_holes = set(), set(), set()
        del_dims, del_texts = set(), set()
        for it in items:
            if isinstance(it, ShapeItem):
                del_shapes.add(id(it.model))
            elif isinstance(it, StitchLineItem):
                del_lines.add(id(it.line))
            elif isinstance(it, DimensionItem):
                del_dims.add(id(it.dim))
            elif isinstance(it, TextItem):
                del_texts.add(id(it.model))
            else:  # HoleItem
                del_holes.add(id(it.hole))
        if del_shapes:
            self.doc.shapes = [s for s in self.doc.shapes
                               if id(s) not in del_shapes]
        if del_lines:
            self.doc.stitch_lines = [l for l in self.doc.stitch_lines
                                     if id(l) not in del_lines]
        if del_holes:
            self.doc.holes = [h for h in self.doc.holes
                              if id(h) not in del_holes]
        if del_dims:
            self.doc.dimensions = [d for d in self.doc.dimensions
                                   if id(d) not in del_dims]
        if del_texts:
            self.doc.texts = [t for t in self.doc.texts
                              if id(t) not in del_texts]
        for it in items:
            self._remove_item(it)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    def duplicate_selected(self) -> None:
        import copy
        from leathercad.geometry import Vec2
        new_items = []
        self._suppress_commit = True
        # Positioning each freshly-created item fires item_moved -> a full
        # document refresh (dimension re-anchor + total_holes scene scan); doing
        # that per item is O(N^2). Suspend it and refresh once at the end.
        self._suspend_move_refresh = True
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                sh = copy.deepcopy(it.model)
                sh.transform.x += 8
                sh.transform.y -= 8
                from leathercad.shapes import _next_id
                sh.shape_id = _next_id("shape")
                sh.group_id = None          # a copy is not in the original group
                # Lightweight add (like the hole/seam branches): the per-shape
                # add_shape() does clearSelection + documentChangedSig + commit
                # each, which is O(N^2) selection churn for a big duplicate.
                self.doc.add_shape(sh)
                new_items.append(self._add_item(ShapeItem(sh, self)))
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
        self._suspend_move_refresh = False
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
        allowance, <0 inward) as a NEW shape on the same layer. Circles and
        (rounded) rectangles stay parametric; other outlines become polygons."""
        from leathercad.offset import offset_shape
        if abs(dist) < 1e-9:
            return
        made = []
        self._suppress_commit = True
        for it in self._shape_items():
            sh = offset_shape(it.model, dist)
            if sh is None:
                continue
            self.doc.add_shape(sh)
            made.append(self._add_item(ShapeItem(sh, self)))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for m in made:
            m.setSelected(True)
        if made:
            self.documentChangedSig.emit()
            self._emit_commit()

    # -- interactive Offset tool ----------------------------------------
    _OFFSET_HINT = ("Offset: move the cursor inside / outside · click to "
                    "place · Enter = type an exact distance · Esc cancels")

    def _arm_offset(self, p: Vec2) -> None:
        """First click of the Offset tool: pick the shape under the cursor and
        start the live inside/outside preview."""
        vp = self.mapFromScene(QPointF(p.x, p.y))
        target = None
        for it in self.items(vp):
            if isinstance(it, ShapeItem):
                target = it
                break
        if target is None:
            self.statusMessage.emit("Offset: click a shape's outline")
            return
        wpts, _corners, closed = target.model.world_polyline()
        if len(wpts) < 2:
            return
        self._offset_item = target
        self._offset_pts = [Vec2(q.x, q.y) for q in wpts]
        self._offset_closed = closed
        self._update_offset_preview(p)

    def _signed_offset_dist(self, p: Vec2) -> float:
        """Distance from ``p`` to the armed outline; sign picks the side
        (>0 outward / right of travel, <0 inward / left)."""
        from leathercad.geometry import point_in_polygon
        pts = self._offset_pts
        best_d2, best_i, best_t = float("inf"), 0, 0.0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            dx, dy = b.x - a.x, b.y - a.y
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((p.x - a.x) * dx
                                                          + (p.y - a.y) * dy) / L2))
            qx, qy = a.x + t * dx, a.y + t * dy
            d2 = (p.x - qx) ** 2 + (p.y - qy) ** 2
            if d2 < best_d2:
                best_d2, best_i, best_t = d2, i, t
        dist = best_d2 ** 0.5
        if self._offset_closed:
            return -dist if point_in_polygon(p, pts) else dist
        a, b = pts[best_i], pts[best_i + 1]
        cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        return -dist if cross > 0 else dist    # right of travel = positive

    def _update_offset_preview(self, p: Vec2) -> None:
        from leathercad.offset import offset_closed, offset_open
        dist = self._signed_offset_dist(p)
        if self.snap_to_grid and self.snap_grid > 0:      # honour grid steps
            dist = round(dist / self.snap_grid) * self.snap_grid
        self._offset_dist = dist
        out = (offset_closed(self._offset_pts, dist) if self._offset_closed
               else offset_open(self._offset_pts, dist))
        path = QPainterPath()
        if len(out) >= 2:
            path.moveTo(out[0].x, out[0].y)
            for q in out[1:]:
                path.lineTo(q.x, q.y)
        # Recreate if the C++ item was freed underneath us (e.g. a rebuild /
        # scene.clear() while an offset was mid-flight) -- a dead wrapper is not
        # None, so the plain ``is None`` check would sail past into a crash.
        if self._offset_preview is None or not _alive(self._offset_preview):
            self._offset_preview = QGraphicsPathItem()
            pen = QPen(QColor(30, 140, 255), 0, Qt.DashLine)
            pen.setCosmetic(True)
            self._offset_preview.setPen(pen)
            self._offset_preview.setZValue(997)
            self.scene_obj.addItem(self._offset_preview)
        self._offset_preview.setPath(path)
        side = "outward" if dist > 0 else "inward"
        self.statusMessage.emit(
            f"Offset {abs(dist):.2f} mm {side} · click to place · "
            f"Enter = type an exact distance · Esc cancels")

    def _commit_offset(self, dist: Optional[float] = None) -> None:
        from leathercad.offset import offset_shape
        it = self._offset_item
        d = self._offset_dist if dist is None else dist
        self._cancel_offset()
        if it is None or not _alive(it) or abs(d) < 1e-9:
            return
        sh = offset_shape(it.model, d)
        if sh is None:
            self.statusMessage.emit(
                "Offset: too far inward — the shape would collapse")
            return
        self.doc.add_shape(sh)
        new_item = self._add_item(ShapeItem(sh, self))
        self.scene_obj.clearSelection()
        new_item.setSelected(True)
        self.documentChangedSig.emit()
        self._emit_commit()
        self.statusMessage.emit(self._OFFSET_HINT)

    def _ask_offset_exact(self) -> None:
        """Enter while the Offset preview is live: type the exact distance."""
        from PySide6.QtWidgets import QInputDialog
        d, ok = QInputDialog.getDouble(
            self, "Offset outline",
            "Distance (mm)   —   positive = outward, negative = inward:",
            round(self._offset_dist, 2), -1000.0, 1000.0, 2)
        if ok:
            self._commit_offset(d)

    def _cancel_offset(self) -> None:
        if self._offset_preview is not None:
            self.scene_obj.removeItem(self._offset_preview)
            self._offset_preview = None
        self._offset_item = None
        self._offset_pts = []
        self._offset_dist = 0.0

    # -- generated parts: insert centred in the view ---------------------
    def insert_generated(self, shapes=(), stitch_lines=(),
                         group: bool = False) -> None:
        """Add generated model objects, centred on the current view, selected
        and committed as one undo step. ``group=True`` welds them into a
        move-group (e.g. a zip window + its stitch ring travel together)."""
        from leathercad.shapes import _next_id as _nid
        xs, ys = [], []
        for sh in shapes:
            b = sh.bounds()
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
        for sl in stitch_lines:
            xs += [p.x for p in sl.points]
            ys += [p.y for p in sl.points]
        if not xs:
            return
        target = self.mapToScene(self.viewport().rect().center())
        dx = target.x() - 0.5 * (min(xs) + max(xs))
        dy = target.y() - 0.5 * (min(ys) + max(ys))
        gid = (_nid("group")
               if group and len(shapes) + len(stitch_lines) > 1 else None)
        made = []
        self._suppress_commit = True
        for sh in shapes:
            sh.transform.x += dx
            sh.transform.y += dy
            if gid:
                sh.group_id = gid
            self.doc.add_shape(sh)
            made.append(self._add_item(ShapeItem(sh, self)))
        for sl in stitch_lines:
            sl.points = [Vec2(p.x + dx, p.y + dy) for p in sl.points]
            sl.corner_points = [Vec2(p.x + dx, p.y + dy)
                                for p in sl.corner_points]
            if gid:
                sl.group_id = gid
            self.doc.add_stitch_line(sl)
            made.append(self._add_item(StitchLineItem(sl, self)))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for m in made:
            m.setSelected(True)
        self.documentChangedSig.emit()
        self._emit_commit()

    # -- user parameters: re-drive bound fields --------------------------
    def apply_param_bindings(self) -> list:
        """Re-evaluate every parameter-bound shape field (Document.bindings)
        against the current parameter values and push the results into the
        models. Returns a list of human-readable problems (bad expressions);
        an empty list means everything applied cleanly."""
        from leathercad.expr import evaluate
        doc = self.doc
        if not doc.bindings:
            return []
        try:
            vals = doc.param_values()
        except ValueError as e:
            return [str(e)]
        by_id = {sh.shape_id: sh for sh in doc.shapes}
        errors, changed = [], False
        for key, expr in list(doc.bindings.items()):
            sid, _, field = key.rpartition(":")
            sh = by_id.get(sid)
            if sh is None:                       # shape was deleted
                doc.bindings.pop(key, None)
                continue
            try:
                v = float(evaluate(expr, vals))
            except Exception as e:
                errors.append(f"{sh.name or sid} · {field} = {expr}: {e}")
                continue
            if self._set_bound_field(sh, field, v):
                changed = True
        if changed:
            self.rebuild()
            self.documentChangedSig.emit()
        return errors

    @staticmethod
    def _set_bound_field(sh, field: str, v: float) -> bool:
        t = sh.transform
        if field == "x":
            t.x = v
        elif field == "y":
            t.y = v
        elif field == "rot":
            t.rotation = v
        elif field == "w" and hasattr(sh, "width"):
            sh.width = v
        elif field == "h" and hasattr(sh, "height"):
            sh.height = v
        elif field == "corner" and hasattr(sh, "corner_radius"):
            sh.corner_radius = v
        elif field == "rx" and hasattr(sh, "rx"):
            sh.rx = v
        elif field == "ry" and hasattr(sh, "ry"):
            sh.ry = v
        elif field == "pitch" and sh.stitch is not None:
            sh.stitch.pitch_mm = v
        elif field == "inset" and sh.stitch is not None:
            sh.stitch.inset = v
        else:
            return False
        return True

    # -- nesting: pack pieces onto a leather sheet ----------------------
    def nest_selected(self, sheet_w: float, sheet_h: float, *,
                      margin: float = 5.0, spacing: float = 3.0,
                      allow_rotate: bool = True) -> str:
        """Pack the selected shapes (or ALL shapes if nothing is selected)
        onto a sheet_w x sheet_h mm sheet, using their real outlines. Grouped
        shapes travel as one piece; anything sitting INSIDE a piece (slots,
        hardware holes, loose stitch holes, seams, lettering) rides along.
        Draws the sheet as a construction rectangle and returns a report."""
        from leathercad.nesting import NestPiece, nest, rotate90
        from leathercad.geometry import point_in_polygon
        from leathercad.shapes import Rectangle as _Rect

        cand = [it for it in self._shape_items()
                if not getattr(it.model, "construction", False)]
        if not cand:
            cand = [it for it in self.scene_obj.items()
                    if isinstance(it, ShapeItem)
                    and not getattr(it.model, "construction", False)]
        if not cand:
            return "Nothing to nest — draw or select some pieces first."

        # 1. move-groups nest as one piece
        groups: dict = {}
        for it in cand:
            gid = getattr(it.model, "group_id", None) or id(it)
            groups.setdefault(gid, []).append(it)

        def outline_of(it):
            pts, _c, closed = it.model.world_polyline()
            return [Vec2(p.x, p.y) for p in pts], closed

        pieces = []          # [ {shapes, outlines, bbox, ring} ]
        for gid, members in groups.items():
            outlines = [outline_of(m) for m in members]
            allpts = [p for pts, _c in outlines for p in pts]
            if len(allpts) < 2:
                continue
            xs = [p.x for p in allpts]
            ys = [p.y for p in allpts]
            ring = max((pts for pts, c in outlines if c and len(pts) >= 3),
                       key=lambda r: len(r), default=None)
            pieces.append({"shapes": list(members), "outlines": outlines,
                           "bbox": (min(xs), min(ys), max(xs), max(ys)),
                           "ring": ring, "holes": [], "seams": [], "texts": []})

        def containing_piece(x, y, exclude=None):
            best = None
            for pc in pieces:
                if pc is exclude or pc["ring"] is None:
                    continue
                bx = pc["bbox"]
                if not (bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]):
                    continue
                if point_in_polygon(Vec2(x, y), pc["ring"]):
                    area = (bx[2] - bx[0]) * (bx[3] - bx[1])
                    if best is None or area < best[0]:
                        best = (area, pc)
            return best[1] if best else None

        # 2. a piece fully inside another (a slot in a panel) merges into it
        for pc in sorted(pieces, key=lambda p: (p["bbox"][2] - p["bbox"][0])
                         * (p["bbox"][3] - p["bbox"][1])):
            b = pc["bbox"]
            host = containing_piece(0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]),
                                    exclude=pc)
            if host is not None and host in pieces and pc in pieces \
                    and host["bbox"][0] <= b[0] and host["bbox"][1] <= b[1] \
                    and host["bbox"][2] >= b[2] and host["bbox"][3] >= b[3]:
                host["shapes"] += pc["shapes"]
                host["outlines"] += pc["outlines"]
                pieces.remove(pc)

        # 3. loose holes / seams / lettering inside a piece ride along
        for h in self.doc.holes:
            pc = containing_piece(h.point.x, h.point.y)
            if pc is not None:
                pc["holes"].append(h)
        for sl in self.doc.stitch_lines:
            if not sl.points:
                continue
            mx = sum(p.x for p in sl.points) / len(sl.points)
            my = sum(p.y for p in sl.points) / len(sl.points)
            pc = containing_piece(mx, my)
            if pc is not None:
                pc["seams"].append(sl)
        for tx in getattr(self.doc, "texts", []):
            pc = containing_piece(tx.transform.x, tx.transform.y)
            if pc is not None:
                pc["texts"].append(tx)

        # 4. sheet sits at the min corner of everything being nested
        sx = min(pc["bbox"][0] for pc in pieces)
        sy = min(pc["bbox"][1] for pc in pieces)
        nest_pieces = [NestPiece(key=i, outlines=pc["outlines"],
                                 allow_rotate=allow_rotate)
                       for i, pc in enumerate(pieces)]
        placements, unplaced, used = nest(
            nest_pieces, sheet_w, sheet_h,
            margin=margin, spacing=spacing, sheet_x=sx, sheet_y=sy)
        if not placements:
            return ("No piece fits the sheet — try a bigger sheet, a smaller "
                    "margin, or allow rotation.")

        # 5. move every member of every placed piece as a rigid unit
        def move_xy(x, y, pl):
            if pl.rotated:
                p = rotate90(Vec2(x, y), pl.pivot)
                x, y = p.x, p.y
            return x + pl.dx, y + pl.dy

        for pl in placements:
            pc = pieces[pl.key]
            for it in pc["shapes"]:
                t = it.model.transform
                t.x, t.y = move_xy(t.x, t.y, pl)
                if pl.rotated:
                    t.rotation = ((t.rotation + 90 + 180) % 360) - 180
            for tx in pc["texts"]:
                t = tx.transform
                t.x, t.y = move_xy(t.x, t.y, pl)
                if pl.rotated:
                    t.rotation = ((t.rotation + 90 + 180) % 360) - 180
            for h in pc["holes"]:
                h.point = Vec2(*move_xy(h.point.x, h.point.y, pl))
                if pl.rotated:
                    h.tangent = Vec2(-h.tangent.y, h.tangent.x)
            for sl in pc["seams"]:
                sl.points = [Vec2(*move_xy(p.x, p.y, pl)) for p in sl.points]
                sl.corner_points = [Vec2(*move_xy(p.x, p.y, pl))
                                    for p in sl.corner_points]

        # 6. show the sheet itself as a construction rectangle
        sheet = _Rect(name="Sheet", width=sheet_w, height=sheet_h,
                      transform=Transform(x=sx + sheet_w / 2.0,
                                          y=sy + sheet_h / 2.0),
                      layer="Cut")
        sheet.construction = True
        self.doc.add_shape(sheet)

        self.rebuild()
        self.documentChangedSig.emit()
        self._emit_commit()

        lines = [f"Placed {len(placements)} of {len(pieces)} piece"
                 f"{'s' if len(pieces) != 1 else ''} on the "
                 f"{sheet_w:g} × {sheet_h:g} mm sheet.",
                 f"Sheet blocked out (incl. {spacing:g} mm gaps): {used:.0%}."]
        if unplaced:
            names = [pieces[k]['shapes'][0].model.name
                     or f"piece {k + 1}" for k in unplaced]
            lines.append("Didn't fit: " + ", ".join(names) + ".")
        if allow_rotate:
            lines.append("Rotated pieces are turned 90° — check grain "
                         "direction before cutting.")
        return "\n".join(lines)

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
        if self._suspend_move_refresh:
            return      # bulk op (duplicate/array): one refresh happens at end
        self._moved_during_press = True
        if self._group_driving:
            return      # a driven group member; the drag leader reports once
        # Cheap must-stay-fresh work runs on every move: the resize grips
        # track the shape, and the Properties geometry fields re-sync so a
        # later _apply can never write a stale position back.
        self._reposition_resize_handles()
        self.geometryMovedSig.emit()
        # The full refresh (dimension re-anchoring, status bar, layer combo,
        # window title) is heavier than a frame. First move of a burst runs
        # it synchronously; the rest coalesce into one trailing refresh, so
        # dragging 100 selected shapes costs one refresh per frame, not 100.
        now = time.monotonic()
        if now - self._last_move_refresh >= 0.03:
            self._last_move_refresh = now
            self._move_refresh()
        elif not self._move_refresh_pending:
            self._move_refresh_pending = True
            QTimer.singleShot(40, self._flush_move_refresh)

    def _move_refresh(self) -> None:
        self.update_dimensions()
        self.documentChangedSig.emit()

    def _flush_move_refresh(self) -> None:
        self._move_refresh_pending = False
        self._last_move_refresh = time.monotonic()
        self._move_refresh()

    def selection_changed(self, item=None, selected=None) -> None:
        # Maintain an O(1) tally of selected ShapeItems so a big multi-select
        # doesn't rebuild a filtered Python list per member (the O(N^2) lock).
        if item is not None and selected is not None and isinstance(item, ShapeItem):
            if selected:
                self._selected_shapes.add(item)
            else:
                self._selected_shapes.discard(item)
        if self._edit_owner is not None and not self._edit_owner.isSelected():
            self.clear_vertex_handles()
        self._refresh_resize_handles()
        self._emit_selection_changed()

    def _emit_selection_changed(self) -> None:
        """Coalesce properties-panel rebuilds.

        Every selected item fires an ``ItemSelectedHasChanged`` event, and the
        scene emits ``selectionChanged`` once per item too, so a large
        multi-select (rubber-band, Select All, duplicate) would otherwise
        rebuild the panel O(N) times -- O(N^2) work that locked the UI.
        Collapse a burst into a single emit on the next event-loop turn."""
        if self._selchg_emit_pending:
            return
        self._selchg_emit_pending = True
        QTimer.singleShot(0, self._flush_selection_emit)

    def _flush_selection_emit(self) -> None:
        self._selchg_emit_pending = False
        self.selectionChangedSig.emit()

    def _refresh_resize_handles(self) -> None:
        """Show box-resize grips when exactly one resizable shape is selected
        and we're not in vertex-edit mode; otherwise hide them."""
        if self._edit_owner is not None:
            self.clear_resize_handles()
            return
        # O(1): consult the maintained set instead of building a filtered list
        # per member (the old comprehension was the O(N^2) multi-select lock).
        # Only prune/validate near a single selection -- when many are selected
        # we clear the grips regardless, so a stale entry there is harmless and
        # scanning the whole set would resurrect the O(N) cost.
        if len(self._selected_shapes) <= 2:
            self._selected_shapes = {it for it in self._selected_shapes
                                     if _alive(it) and it.isSelected()}
        if len(self._selected_shapes) == 1:
            it = next(iter(self._selected_shapes))
            if it.resize_extents() is not None:
                self.show_resize_handles(it)
                return
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
        self.update_dimensions()
        self.documentChangedSig.emit()

    def refresh_all(self) -> None:
        for it in self.scene_obj.items():
            if isinstance(it, (ShapeItem, StitchLineItem)):
                it.sync_from_model()
        self.apply_layer_visibility()

    def outline_width(self, selected: bool = False) -> float:
        """Cosmetic stroke width for outlines / lines (user-tunable)."""
        return self.line_width + (0.8 if selected else 0.0)

    def set_line_width(self, w: float) -> None:
        self.line_width = max(0.2, w)

    def preview_color(self) -> QColor:
        """Colour for the in-progress drawing preview (user's draw colour, or a
        bright default)."""
        return self.draw_color or self._default_preview_color

    def set_draw_color(self, color) -> None:
        """Set the WHILE-DRAWING preview colour (None -> bright default). Only
        tints the live preview; finished shapes keep their layer colour."""
        self.draw_color = QColor(color) if color is not None else None
        # recolour a preview that's currently on screen
        if self._preview is not None and _alive(self._preview):
            pen = self._preview.pen()
            pen.setColor(self.preview_color())
            self._preview.setPen(pen)
        for it in self.scene_obj.items():
            if hasattr(it, "update"):
                it.update()

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
            elif isinstance(it, TextItem):
                vis = self._layer_visible(it.model.layer)
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
        paths (arcs preserved). Disconnected pieces form separate paths.

        Only OPEN, CUT-layer segments are joined. Score / engrave / stitch
        pieces and already-closed shapes (circles, finished outlines) are left
        untouched, so selecting the whole document welds only the loose cut
        edges -- never a fold line, and never a standalone relief hole."""
        sel = [it for it in self.selected_items() if isinstance(it, ShapeItem)]
        shapes = [it for it in sel if self._is_open_cut(it.model)]
        if len(shapes) < 2:
            if len(sel) >= 2:
                self.statusMessage.emit(
                    "Join welds open Cut segments — select 2+ of them")
            else:
                self.statusMessage.emit("Select 2+ open cut segments to join")
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

    def _layer_role(self, name: str) -> str:
        lyr = self.doc.layer(name)
        return lyr.role if lyr is not None else "cut"

    def _is_open_cut(self, m) -> bool:
        """True for an OPEN cut-layer segment -- the only thing join welds.
        Excludes score/engrave/stitch layers and already-closed shapes
        (circles, ellipses, rectangles, closed polygons / paths)."""
        if self._layer_role(m.layer) != "cut":
            return False
        if isinstance(m, (Circle, Ellipse, Rectangle)):
            return False
        return not (getattr(m, "closed", False)
                    or getattr(m, "close_path", False))

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
        # baked holes render in the shape's stitch STYLE, so adopt the grouped
        # holes' style (round / slit + sizes) -- otherwise slit holes baked
        # into a round-stitched shape would come out round, and vice versa.
        h0 = holes[0].hole
        if sh.stitch is None:
            sh.stitch = StitchSettings(enabled=False)
        sh.stitch.hole_style = h0.hole_style
        sh.stitch.hole_diameter = h0.hole_diameter
        sh.stitch.slit_length = h0.slit_length
        sh.stitch.slit_angle = h0.slit_angle
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

    def press_select(self, item, event=None) -> None:
        """Left-press selection. Make ``item`` (plus its move-group) the current
        selection, clearing any previous selection FIRST -- unless Ctrl/Shift is
        held for multi-select, or the item is already selected (so an existing
        multi-selection can still be dragged as one).

        Doing this on the press, before the drag begins, is what stops a
        previously-selected item from being dragged along with a freshly-pressed
        one: Qt commits its own press-selection only after our handlers run, so
        without this the stale selection is still live when the group-drag setup
        (begin_move_snap) samples it.
        """
        mods = event.modifiers() if event is not None else Qt.NoModifier
        multi = bool(mods & (Qt.ControlModifier | Qt.ShiftModifier))
        if not multi and not item.isSelected():
            self.scene_obj.clearSelection()
            if _alive(item):
                item.setSelected(True)
        self.select_group_of(item)

    def select_group_of(self, item) -> None:
        """On pressing a grouped item, select the whole group (itself included)
        so Qt's multi-item drag moves every member together. Driven by the mouse
        press -- NOT by selection_changed, whose cascade would re-select members
        while Qt is trying to deselect them (leaving the group 'stuck')."""
        # While node-editing an item, pressing it (a click on the line/outline
        # body, or a near-miss on a node handle) must NOT re-enable its
        # movability -- that would let the whole item drag away and leave its
        # node handles stranded behind it.
        if self._edit_owner is not None and item is self._edit_owner:
            return
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
    def set_dark_theme(self, dark: bool) -> None:
        self.dark = bool(dark)
        self.viewport().update()

    def drawBackground(self, painter, rect):
        from .theme import CANVAS
        sw = CANVAS[getattr(self, "dark", False)]
        painter.fillRect(rect, sw["bg"])
        # Adaptive grid: pick the finest 10^k step that stays >= ~8 px apart,
        # so zooming far out over a huge canvas never draws thousands of lines.
        step = 10.0
        while step * self._zoom < 8.0:
            step *= 10.0
        big = step * 5.0
        minor = QPen(sw["minor"], 0)
        minor.setCosmetic(True)
        major = QPen(sw["major"], 0)
        major.setCosmetic(True)
        x = math.floor(rect.left() / step) * step
        while x < rect.right():
            painter.setPen(major if abs(x % big) < 1e-6 else minor)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = math.floor(rect.top() / step) * step
        while y < rect.bottom():
            painter.setPen(major if abs(y % big) < 1e-6 else minor)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step
        axis = QPen(sw["axis"], 0)
        axis.setCosmetic(True)
        painter.setPen(axis)
        painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0))
        painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))

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
                               QGraphicsItem, QMenu)

from leathercad.geometry import Vec2
from leathercad.document import Document
from leathercad.shapes import (Rectangle, Ellipse, Circle, Polygon, PathShape,
                               EditablePath, Edge, Transform)
from leathercad.stitchsettings import StitchSettings
from leathercad.stitchline import StitchLine
from leathercad.holes import LooseHole
from leathercad.stitching import stitch_polyline, Hole, StitchResult, flip_symmetry
from .items import ShapeItem, StitchLineItem, VertexHandle, HoleItem

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

_DRAG_TOOLS = (RECT, ROUNDED, ELLIPSE, CIRCLE, SLOT)
_POLY_TOOLS = (POLYGON, STITCHLINE, SCORE)


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
        self.hole_tool_diameter = 4.0
        self._handles: List[VertexHandle] = []
        self._edit_owner = None
        # Hold strong Python refs to every scene item we create. PySide6 can
        # otherwise garbage-collect a live item's wrapper and free the C++
        # object while it is still selected -> crash in clearSelection().
        self._live = set()
        self._snap_cache = None   # static snap nodes captured at drag start

        # snapping
        self.snap_enabled = True
        self.snap_grid = 1.0      # mm
        self.snap_vertices = True
        self._snap_marker: Optional[QGraphicsPathItem] = None

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
        pts = []
        for it in self.scene_obj.items():
            if it is exclude:
                continue
            if isinstance(it, ShapeItem):
                pts.extend(it.world_snap_nodes())
            elif isinstance(it, StitchLineItem):
                pts.extend(it.line.points)
            elif isinstance(it, HoleItem):
                pts.append(it.hole.point)
        return pts

    def snap(self, pos: QPointF):
        """Return (snapped QPointF, is_vertex_snap)."""
        if not self.snap_enabled:
            return pos, False
        thr = 10.0 / self._zoom  # ~10 px in mm
        if self.snap_vertices:
            best = None
            best_d = thr
            for c in self._snap_candidates():
                d = ((pos.x() - c.x) ** 2 + (pos.y() - c.y) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = c
            if best is not None:
                return QPointF(best.x, best.y), True
        g = self.snap_grid
        if g > 0:
            return QPointF(round(pos.x() / g) * g, round(pos.y() / g) * g), False
        return pos, False

    def _show_snap_marker(self, pt: QPointF, vertex: bool):
        if self._snap_marker is None:
            self._snap_marker = QGraphicsPathItem()
            self._snap_marker.setZValue(1000)
            self.scene_obj.addItem(self._snap_marker)
        path = QPainterPath()
        r = 6.0 / self._zoom
        path.addEllipse(pt, r, r)
        path.moveTo(pt.x() - r * 1.6, pt.y()); path.lineTo(pt.x() + r * 1.6, pt.y())
        path.moveTo(pt.x(), pt.y() - r * 1.6); path.lineTo(pt.x(), pt.y() + r * 1.6)
        self._snap_marker.setPath(path)
        pen = QPen(QColor(255, 120, 0) if vertex else QColor(150, 150, 150), 0)
        pen.setCosmetic(True)
        self._snap_marker.setPen(pen)
        self._snap_marker.setVisible(True)

    def _hide_snap_marker(self):
        if self._snap_marker is not None:
            self._snap_marker.setVisible(False)

    # -- magnetic node snapping while dragging shapes -------------------
    def begin_move_snap(self, item) -> None:
        # capture other shapes'/holes' nodes once, at the start of the drag
        if self.snap_enabled and len(self.selected_items()) <= 1:
            self._snap_cache = self._snap_candidates(exclude=item)
        else:
            self._snap_cache = None

    def end_move_snap(self) -> None:
        self._snap_cache = None
        self._hide_snap_marker()

    def snap_move(self, item, value: QPointF) -> QPointF:
        """Snap a dragged shape so one of its nodes lands on a nearby node."""
        if not self.snap_enabled or self._snap_cache is None:
            return value
        offsets = getattr(item, "_snap_offsets", None)
        if not offsets:
            return value
        thr = 12.0 / self._zoom
        best = None
        best_target = None
        best_d = thr
        vx, vy = value.x(), value.y()
        for off in offsets:
            nx, ny = vx + off.x, vy + off.y
            for s in self._snap_cache:
                d = ((nx - s.x) ** 2 + (ny - s.y) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = QPointF(s.x - off.x, s.y - off.y)
                    best_target = s
        if best is not None:
            self._show_snap_marker(QPointF(best_target.x, best_target.y), True)
            return best
        self._hide_snap_marker()
        return value

    # -- node-to-node snapping while editing nodes ----------------------
    def begin_node_snap(self, handle) -> None:
        if not self.snap_enabled:
            self._snap_cache = None
            return
        start = handle.pos()
        cands = self._snap_candidates()
        # exclude the dragged node's own current position
        self._snap_cache = [c for c in cands
                            if (c.x - start.x()) ** 2 + (c.y - start.y()) ** 2 > 0.25]

    def snap_node(self, value: QPointF) -> QPointF:
        if not self.snap_enabled or self._snap_cache is None:
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
            self._show_snap_marker(best, True)
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
        raw = self.mapToScene(event.position().toPoint())
        if self.tool == TRIM:
            if event.button() == Qt.LeftButton:
                self._do_trim(Vec2(raw.x(), raw.y()))
            event.accept()
            return
        pos = raw
        if self.tool != SELECT:
            pos, _v = self.snap(pos)
        if self.tool == SELECT:
            return super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            if self.tool == HOLE:
                self._place_hole(pos)
            elif self.tool in _DRAG_TOOLS:
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

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MiddleButton and hasattr(self, "_pan_last"):
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.translate(delta.x() / self._zoom, -delta.y() / self._zoom)
            return
        raw = self.mapToScene(event.position().toPoint())
        self.cursorMoved.emit(raw.x(), raw.y())
        pos = raw
        if self.tool != SELECT:
            pos, vtx = self.snap(raw)
            self._show_snap_marker(pos, vtx)
        else:
            self._hide_snap_marker()
        if self._preview is not None and self._start is not None:
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
        pos = self.mapToScene(event.position().toPoint())
        if self.tool != SELECT:
            pos, _v = self.snap(pos)
        if self._preview is not None and self._start is not None:
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
        menu.addSeparator()
        a_dup = menu.addAction("Duplicate")
        a_dup.setEnabled(bool(shapes))
        a_back = menu.addAction("Make back piece (mirror)")
        a_back.setEnabled(bool(shapes))
        a_del = menu.addAction("Delete")
        a_del.setEnabled(bool(sel))
        chosen = menu.exec(event.globalPos())
        if chosen is a_group:
            self.group_selected()
        elif chosen is a_ungroup:
            self.ungroup_selected()
        elif chosen is a_nodes:
            self.convert_to_nodes()
        elif chosen is a_break:
            self.break_apart_selected()
        elif chosen is a_join:
            self.join_selected()
        elif chosen is a_dup:
            self.duplicate_selected()
        elif chosen is a_back:
            self.make_back_piece_selected()
        elif chosen is a_del:
            self.delete_selected()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancel_poly()
            self.clear_vertex_handles()
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
        self._handles = []
        self._edit_owner = None
        for sh in self.doc.shapes:
            self._add_item(ShapeItem(sh, self))
        for sl in self.doc.stitch_lines:
            self._add_item(StitchLineItem(sl, self))
        for h in self.doc.holes:
            self._add_item(HoleItem(h, self))
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
                if isinstance(it, (ShapeItem, StitchLineItem, HoleItem))]

    def delete_selected(self) -> None:
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                self.doc.remove_shape(it.model)
            elif isinstance(it, StitchLineItem):
                self.doc.remove_stitch_line(it.line)
            else:  # HoleItem
                self.doc.remove_hole(it.hole)
            self._remove_item(it)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    def duplicate_selected(self) -> None:
        import copy
        new_items = []
        self._suppress_commit = True
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                sh = copy.deepcopy(it.model)
                sh.transform.x += 8
                sh.transform.y -= 8
                from leathercad.shapes import _next_id
                sh.shape_id = _next_id("shape")
                new_items.append(self.add_shape(sh))
        self._suppress_commit = False
        self.scene_obj.clearSelection()
        for it in new_items:
            it.setSelected(True)
        if new_items:
            self._emit_commit()

    def make_back_piece_selected(self) -> None:
        """Duplicate each selected shape as its mirror image -- the matching
        back piece you laser from the reverse side. The mirror keeps every hole
        registered with the front hole-for-hole, so the two pieces stitch
        together back-to-back. The copy is placed just to the right."""
        import copy
        from leathercad.shapes import _next_id
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
            if sh.name:
                sh.name = sh.name + " (back)"
            new_items.append(self.add_shape(sh))
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
        self.documentChangedSig.emit()

    def selection_changed(self) -> None:
        if self._edit_owner is not None and not self._edit_owner.isSelected():
            self.clear_vertex_handles()
        self.selectionChangedSig.emit()

    def refresh_item(self, item) -> None:
        if item is None or not _alive(item):
            return
        item.sync_from_model()
        self.documentChangedSig.emit()

    def refresh_all(self) -> None:
        for it in self.scene_obj.items():
            if isinstance(it, (ShapeItem, StitchLineItem)):
                it.sync_from_model()

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

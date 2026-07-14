"""QGraphicsItems that render leathercad model objects with live stitch holes.

Geometry is drawn in a *pre-translation* frame (rotation + mirror baked into the
path via ``Transform.apply_dir``) and positioned with ``setPos``. That composes
to exactly ``Transform.apply`` -- the same transform the exporters use -- so the
canvas is WYSIWYG with the laser output. Holes are computed on the local outline
(the chord-spacing engine is transform-invariant) and cached until geometry or
stitch settings change.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QPainterPath, QPainterPathStroker, QPen,
                           QPolygonF, QBrush, QFont)
from PySide6.QtWidgets import (QGraphicsItem, QApplication,
                               QGraphicsSimpleTextItem)

# Click tolerance (mm) around an outline for selection/hit-testing.
OUTLINE_HIT_MM = 3.5


def _outline_hit_shape(poly: QPolygonF, closed: bool = False) -> QPainterPath:
    """A thin band around a polyline, so only the OUTLINE is clickable (not the
    filled interior). Lets you click inside a shape to reach what's behind it."""
    path = QPainterPath()
    if poly.size() >= 2:
        path.addPolygon(poly)
        if closed and not poly.isClosed():
            path.closeSubpath()
    stroker = QPainterPathStroker()
    stroker.setWidth(OUTLINE_HIT_MM)
    return stroker.createStroke(path)

from leathercad.geometry import Vec2
from leathercad.shapes import Shape
from leathercad.stitchline import StitchLine
from leathercad.stitching import stitch_polyline, StitchResult, Hole
from leathercad.holes import LooseHole


def _holes_to_path(holes, style, diameter, slit_len, slit_angle) -> QPainterPath:
    """All of an item's stitch holes as ONE QPainterPath. Painting thousands
    of holes is then a single C++ draw call instead of a Python loop of
    drawEllipse -- the difference between 4 fps and realtime on big patterns."""
    import math
    path = QPainterPath()
    if style == "diamond":
        from leathercad.holes import diamond_points
        from leathercad.irons import DIAMOND_WIDTH_RATIO
        for h in holes:
            a, b, c, d = diamond_points(h.point, h.tangent, slit_len,
                                        slit_angle, DIAMOND_WIDTH_RATIO)
            path.moveTo(a.x, a.y)
            path.lineTo(b.x, b.y)
            path.lineTo(c.x, c.y)
            path.lineTo(d.x, d.y)
            path.closeSubpath()
    elif style == "slit":
        half = slit_len / 2.0
        ca = math.cos(math.radians(slit_angle))
        sa = math.sin(math.radians(slit_angle))
        for h in holes:
            dx = h.tangent.x * ca - h.tangent.y * sa
            dy = h.tangent.x * sa + h.tangent.y * ca
            path.moveTo(h.point.x - dx * half, h.point.y - dy * half)
            path.lineTo(h.point.x + dx * half, h.point.y + dy * half)
    else:
        r = diameter / 2.0
        for h in holes:
            path.addEllipse(QPointF(h.point.x, h.point.y), r, r)
    return path


def _draw_holes(painter, holes, style, diameter, slit_len, slit_angle):
    import math
    if style == "diamond":
        from leathercad.holes import diamond_points
        from leathercad.irons import DIAMOND_WIDTH_RATIO
        for h in holes:
            pts = diamond_points(h.point, h.tangent, slit_len, slit_angle,
                                 DIAMOND_WIDTH_RATIO)
            painter.drawPolygon(QPolygonF([QPointF(p.x, p.y) for p in pts]))
    elif style == "slit":
        half = slit_len / 2.0
        ca = math.cos(math.radians(slit_angle))
        sa = math.sin(math.radians(slit_angle))
        for h in holes:
            dx = h.tangent.x * ca - h.tangent.y * sa
            dy = h.tangent.x * sa + h.tangent.y * ca
            painter.drawLine(QPointF(h.point.x - dx * half, h.point.y - dy * half),
                             QPointF(h.point.x + dx * half, h.point.y + dy * half))
    else:
        r = diameter / 2.0
        for h in holes:
            painter.drawEllipse(QPointF(h.point.x, h.point.y), r, r)


def _qpoly(points: List[Vec2]) -> QPolygonF:
    return QPolygonF([QPointF(p.x, p.y) for p in points])


def _paint_backstitch(painter, result, settings) -> None:
    """Ring the reinforcement holes at each end of an OPEN seam."""
    if settings is None or result is None or result.closed:
        return
    n = getattr(settings, "backstitch", 0)
    if n <= 0 or not result.holes:
        return
    mult = 2 if getattr(settings, "rows", 1) == 2 else 1
    k = min(n * mult, len(result.holes))
    ring = QPen(QColor(220, 40, 40), 0)
    ring.setCosmetic(True)
    painter.setPen(ring)
    painter.setBrush(Qt.NoBrush)
    marked = list(result.holes[:k]) + list(result.holes[-k:])
    for h in marked:
        painter.drawEllipse(QPointF(h.point.x, h.point.y), 1.4, 1.4)


class VertexHandle(QGraphicsItem):
    """A constant-size draggable handle for one editable node. Square = an
    on-path node; round/orange = an arc midpoint. Uses a NodeRef callback so
    lines, arcs, polygons and seams all edit through one mechanism."""

    SIZE = 4.0  # pixels (item ignores view transform)

    def __init__(self, node, owner, canvas):
        super().__init__()
        self.node = node
        self.owner = owner
        self.canvas = canvas
        self._dragged = False
        self._shift = False
        self._drag_start = None      # node world pos when the drag began
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
            | QGraphicsItem.ItemIgnoresTransformations
        )
        self.setZValue(2000)
        self.setPos(node.world.x, node.world.y)

    def boundingRect(self) -> QRectF:
        s = self.SIZE + 4      # generous hit area so nodes are easy to grab
        return QRectF(-s, -s, 2 * s, 2 * s)

    def shape(self):
        p = QPainterPath()
        s = self.SIZE + 3
        p.addEllipse(QPointF(0, 0), s, s)
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        s = self.SIZE
        if getattr(self.node, "is_ctrl", False):   # bezier tangent handle
            painter.setPen(QPen(QColor(40, 170, 90), 1))
            painter.setBrush(QBrush(QColor(90, 210, 130)))
            r = s - 0.5
            painter.drawEllipse(QPointF(0, 0), r, r)
        elif self.node.is_mid:   # arc midpoint
            painter.setPen(QPen(QColor(210, 120, 0), 1))
            painter.setBrush(QBrush(QColor(255, 235, 200)))
            painter.drawEllipse(QPointF(0, 0), s, s)
        else:                  # on-path node
            painter.setPen(QPen(QColor(30, 110, 220), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.drawRect(QRectF(-s, -s, 2 * s, 2 * s))

    def mousePressEvent(self, event):
        if (event.modifiers() & Qt.AltModifier) and self.canvas is not None:
            self.canvas.delete_node(self)           # Alt-click removes the node
            event.accept()
            return
        self._shift = bool(event.modifiers() & Qt.ShiftModifier)
        self._drag_start = QPointF(self.pos())      # where the node started
        if self.canvas is not None:
            self.canvas.begin_node_snap(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # read Shift from the live event -- QApplication.keyboardModifiers() is
        # unreliable mid-drag (notably on macOS).
        self._shift = bool(event.modifiers() & Qt.ShiftModifier)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._shift = False
        self._drag_start = None
        if self.canvas is not None:
            self.canvas.end_node_snap()
        if self._dragged and self.canvas is not None:
            self._dragged = False
            self.canvas.commitRequested.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.canvas is not None:
            shift = self._shift or bool(
                QApplication.keyboardModifiers() & Qt.ShiftModifier)
            if shift and self._drag_start is not None:
                # Constrain the node's MOVEMENT to 0 / 45 / 90 degrees from where
                # the drag began -- the same ortho lock as the drawing tools, so
                # an endpoint can be dragged straight up into a vertical position
                # (the old lock was relative to the far neighbour, so a node that
                # started well off-axis could never reach vertical).
                return self.canvas._apply_ortho(self._drag_start, value)
            if getattr(self.node, "is_ctrl", False):
                return value      # tangent handles move freely (no osnap)
            return self.canvas.snap_node(value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._dragged = True
            self.node.setter(Vec2(self.pos().x(), self.pos().y()))
            self.owner.sync_from_model()
        return super().itemChange(change, value)


class ResizeHandle(QGraphicsItem):
    """A constant-size box-resize grip on a shape's bounding box. ``grip`` is a
    (gx, gy) pair in {-1, 0, 1}: corners scale both axes, edge midpoints one.
    Dragging keeps the opposite corner/edge pinned in world space (Illustrator /
    Fusion style) and resizes the shape's width/height (or rx/ry)."""

    SIZE = 3.5  # pixels (ignores view transform)

    def __init__(self, grip, owner, canvas):
        super().__init__()
        self.grip = grip                 # (gx, gy)
        self.owner = owner
        self.canvas = canvas
        self._dragged = False
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
            | QGraphicsItem.ItemIgnoresTransformations
        )
        self.setZValue(2100)
        gx, gy = grip
        if gx and gy:
            self.setCursor(Qt.SizeFDiagCursor if gx == gy else Qt.SizeBDiagCursor)
        elif gx:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.SizeVerCursor)
        self.reposition()

    # world position of this grip on the current shape box
    def _grip_world(self):
        t = self.owner.model.transform
        ext = self.owner.resize_extents()
        if ext is None:
            return None
        hx, hy = ext
        cx, cy = self.owner.resize_center()
        gx, gy = self.grip
        return t.apply(Vec2(cx + gx * hx, cy + gy * hy))

    def reposition(self):
        w = self._grip_world()
        if w is not None:
            self._syncing = True
            self.setPos(w.x, w.y)
            self._syncing = False

    def boundingRect(self) -> QRectF:
        s = self.SIZE + 4
        return QRectF(-s, -s, 2 * s, 2 * s)

    def shape(self):
        p = QPainterPath()
        s = self.SIZE + 3
        p.addRect(QRectF(-s, -s, 2 * s, 2 * s))
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        s = self.SIZE
        painter.setPen(QPen(QColor(30, 110, 220), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawRect(QRectF(-s, -s, 2 * s, 2 * s))

    def mousePressEvent(self, event):
        # Drive the resize by hand (below) instead of Qt's ItemIsMovable move --
        # the default mouseMoveEvent also drags every *selected* item, which
        # would translate the shape we're resizing. Just grab the mouse here.
        self._dragged = False
        if self.canvas is not None:
            self.canvas._active_resize = self
            self.canvas.begin_node_snap(self)
        event.accept()

    def mouseMoveEvent(self, event):
        if self.canvas is None:
            return
        world = self.canvas.snap_node(event.scenePos())   # snap to other nodes
        self._dragged = True
        self._apply_resize(Vec2(world.x(), world.y()))
        self._syncing = True
        self.setPos(world)                                 # glyph follows cursor
        self._syncing = False
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.canvas is not None:
            self.canvas.end_node_snap()
            self.canvas._active_resize = None
        if self._dragged and self.canvas is not None:
            self._dragged = False
            self.canvas.commitRequested.emit()
        event.accept()

    def _apply_resize(self, grip_world: Vec2) -> None:
        owner = self.owner
        ext = owner.resize_extents()
        if ext is None:
            return
        hx, hy = ext
        cx, cy = owner.resize_center()
        t = owner.model.transform
        gx, gy = self.grip
        anchor_local = Vec2(cx - gx * hx, cy - gy * hy)   # opposite corner/edge
        anchor_world = t.apply(anchor_local)              # stays fixed
        g_local = t.inverse_apply(grip_world)
        hx_new = abs(g_local.x - anchor_local.x) / 2.0 if gx else hx
        hy_new = abs(g_local.y - anchor_local.y) / 2.0 if gy else hy
        hx_new = max(0.5, hx_new)
        hy_new = max(0.5, hy_new)
        owner.set_resize_extents(hx_new, hy_new)
        # keep the anchor pinned. Scaling preserves the local box centre, so the
        # opposite corner is (cx,cy) +/- the new half-extents; place the shape so
        # it still lands on anchor_world.
        anchor_new = Vec2(cx - gx * hx_new, cy - gy * hy_new)
        o = anchor_world - t.apply_dir(anchor_new)
        t.x, t.y = o.x, o.y
        owner.sync_from_model()
        if self.canvas is not None:
            self.canvas.resize_handle_moved(self)


def bake_text_contours(text, family, size_mm):
    """Convert a string into glyph outline contours (local mm, y-up), scaled so
    the cap height is ``size_mm``. Done here (Qt layer) so the engine stays
    Qt-free; the result is stored as plain polylines on the text model."""
    from PySide6.QtGui import QFont, QPainterPath, QFontMetricsF
    font = QFont()
    if family and family != "Sans":
        font.setFamily(family)          # avoid the missing-"Sans" alias lookup
    font.setStyleHint(QFont.SansSerif)
    font.setPointSizeF(100.0)
    fm = QFontMetricsF(font)
    cap = fm.capHeight() or fm.ascent() or 100.0
    scale = size_mm / cap
    path = QPainterPath()
    path.addText(0.0, 0.0, font, text or "")
    contours = []
    for poly in path.toSubpathPolygons():
        c = [Vec2(pt.x() * scale, -pt.y() * scale) for pt in poly]  # Qt y-down -> y-up
        if len(c) >= 2:
            contours.append(c)
    return contours


class TextItem(QGraphicsItem):
    """Engrave lettering: renders the baked glyph contours; movable/selectable."""

    def __init__(self, model, canvas=None):
        super().__init__()
        self.model = model
        self.canvas = canvas
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(30)
        self._snap_offsets = [Vec2(0.0, 0.0)]     # snap by the text origin
        self.sync_from_model()

    def sync_from_model(self):
        self.prepareGeometryChange()
        self._path = QPainterPath()
        for c in self.model.contours:
            if len(c) < 2:
                continue
            self._path.moveTo(c[0].x, c[0].y)
            for p in c[1:]:
                self._path.lineTo(p.x, p.y)
            self._path.closeSubpath()
        self._color = QColor(self.canvas.layer_color(self.model.layer)
                             if self.canvas else "#888888")
        self.setPos(self.model.transform.x, self.model.transform.y)
        self.setOpacity(max(0.05, min(1.0, self.model.opacity)))
        r = self._path.boundingRect()
        self._brect = r.adjusted(-2, -2, 2, 2)
        self.update()

    def boundingRect(self):
        return self._brect

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(OUTLINE_HIT_MM)
        return st.createStroke(self._path)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        sel = self.isSelected()
        pen = QPen(QColor(30, 140, 255) if sel else self._color)
        pen.setCosmetic(True)
        pen.setWidthF(self.canvas.outline_width(sel) if self.canvas else 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._path)

    @property
    def hole_count(self):
        return 0

    def mousePressEvent(self, event):
        if self.canvas is not None:
            self.canvas.press_select(self, event)   # select this (+group) only
            self.canvas.begin_move_snap(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.canvas is not None:
            self.canvas.end_move_snap()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.canvas is not None:
            return self.canvas.snap_move(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.model.transform.x = self.pos().x()
            self.model.transform.y = self.pos().y()
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed(self, bool(value))
        return super().itemChange(change, value)


class DimensionItem(QGraphicsItem):
    """A linear dimension annotation: extension lines, an offset dimension line
    with arrowheads, and the measured length as constant-size text. Selectable
    and deletable; never exported (it's an annotation)."""

    def __init__(self, dim, canvas=None):
        super().__init__()
        self.dim = dim
        self.canvas = canvas
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(40)
        self._label = QGraphicsSimpleTextItem(self)
        self._label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        f = QFont()
        f.setPointSizeF(9.0)
        self._label.setFont(f)
        self.sync_from_model()

    def sync_from_model(self):
        self.prepareGeometryChange()
        a, b = self.dim.line_points()
        self._a, self._b = QPointF(a.x, a.y), QPointF(b.x, b.y)
        self._p1 = QPointF(self.dim.p1.x, self.dim.p1.y)
        self._p2 = QPointF(self.dim.p2.x, self.dim.p2.y)
        self._label.setText(self.dim.label())
        self._label.setBrush(QColor(70, 70, 70))
        # anchor the label at the dim-line midpoint (constant screen size)
        br = self._label.boundingRect()
        self._label.setPos((a.x + b.x) / 2.0 - br.width() / 2.0 * 0,
                           (a.y + b.y) / 2.0)
        xs = [a.x, b.x, self.dim.p1.x, self.dim.p2.x]
        ys = [a.y, b.y, self.dim.p1.y, self.dim.p2.y]
        self._brect = QRectF(min(xs) - 6, min(ys) - 6,
                             max(xs) - min(xs) + 12, max(ys) - min(ys) + 12)
        self.update()

    def boundingRect(self):
        return self._brect

    def shape(self):
        return _outline_hit_shape(QPolygonF([self._a, self._b]), closed=False)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(30, 140, 255) if self.isSelected() else QColor(120, 120, 130))
        pen.setCosmetic(True)
        base = self.canvas.line_width if self.canvas else 1.0
        pen.setWidthF(base + (0.5 if self.isSelected() else 0.0))
        painter.setPen(pen)
        painter.drawLine(self._p1, self._a)      # extension lines
        painter.drawLine(self._p2, self._b)
        painter.drawLine(self._a, self._b)       # dimension line
        self._arrow(painter, self._a, self._b)
        self._arrow(painter, self._b, self._a)

    def _arrow(self, painter, tip, tail):
        import math
        ang = math.atan2(tail.y() - tip.y(), tail.x() - tip.x())
        s = 2.4
        for da in (0.42, -0.42):
            painter.drawLine(tip, QPointF(tip.x() + s * math.cos(ang + da),
                                          tip.y() + s * math.sin(ang + da)))

    @property
    def hole_count(self):
        return 0

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged and self.canvas is not None:
            self.canvas.selection_changed(self, bool(value))
        return super().itemChange(change, value)


class HoleItem(QGraphicsItem):
    """One individual (ungrouped) stitch hole: selectable, movable, deletable."""

    def __init__(self, hole: LooseHole, canvas=None):
        super().__init__()
        self.hole = hole
        self.canvas = canvas
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setZValue(50)
        # A hole snaps by its single centre, exactly like a circle's centre.
        self._snap_offsets = [Vec2(0.0, 0.0)]
        self._snap_offset_kinds = ["center"]
        self._center_snap_priority = True
        self.sync_from_model()

    def sync_from_model(self):
        self.prepareGeometryChange()
        self.setPos(self.hole.point.x, self.hole.point.y)
        self.update()

    def boundingRect(self):
        # generous, zoom-independent hit area so small holes are easy to click
        r = max(1.6, self.hole.hole_diameter) + 1.0
        return QRectF(-r, -r, 2 * r, 2 * r)

    def shape(self):
        p = QPainterPath()
        r = max(1.6, self.hole.hole_diameter)
        p.addEllipse(QPointF(0, 0), r, r)
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        color = (self.canvas.layer_color(self.hole.layer)
                 if self.canvas else "#0066ff")
        pen = QPen(QColor(30, 140, 255) if self.isSelected() else QColor(color))
        pen.setCosmetic(True)
        pen.setWidthF(1.6 if self.isSelected() else 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        h = self.hole
        # draw the actual hole geometry, centred at the item origin
        centred = Hole(Vec2(0.0, 0.0), h.tangent)
        _draw_holes(painter, [centred], h.hole_style, h.hole_diameter,
                    h.slit_length, h.slit_angle)
        if self.isSelected():
            painter.setPen(QPen(QColor(30, 140, 255), 0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            r = max(1.6, h.hole_diameter) + 0.6
            painter.drawEllipse(QPointF(0, 0), r, r)

    def mousePressEvent(self, event):
        if self.canvas is not None:
            self.canvas.press_select(self, event)   # select this (+group) only
            self.canvas.begin_move_snap(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.canvas is not None:
            self.canvas.end_move_snap()

    def itemChange(self, change, value):
        # Magnetic centre snapping while dragging; free otherwise.
        if change == QGraphicsItem.ItemPositionChange and self.canvas is not None:
            return self.canvas.snap_move(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.hole.point = Vec2(self.pos().x(), self.pos().y())
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed(self, bool(value))
        return super().itemChange(change, value)

    @property
    def hole_count(self):
        return 1


class ShapeItem(QGraphicsItem):
    """Renders a model ``Shape`` (outline + stitch holes)."""

    def __init__(self, shape: Shape, canvas=None):
        super().__init__()
        self.model = shape
        self.canvas = canvas
        self._outline: QPolygonF = QPolygonF()
        self._holes: Optional[StitchResult] = None
        self._holes_path: Optional[QPainterPath] = None
        self._holes_pts: Optional[QPolygonF] = None
        self._holes_size_mm = 1.0
        self._brect = QRectF()
        self._color = QColor("#ff0000")
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.sync_from_model()

    # -- model sync -----------------------------------------------------
    def sync_from_model(self) -> None:
        """Rebuild geometry + holes from the model and reposition."""
        self.prepareGeometryChange()
        t = self.model.transform
        path = self.model.local_path()
        # oriented (rotation+mirror), pre-translation -- matches Transform.apply
        local = path.flatten()
        oriented = [t.apply_dir(p) for p in local]
        self._outline = _qpoly(oriented)
        self._closed = path.closed

        # snap targets from REAL geometry (Fusion / LightBurn style): endpoints,
        # edge/arc midpoints, arc & shape centres and circle quadrants -- not
        # bounding-box corners, which don't sit on a rounded / round outline.
        self._snap_typed_local = self._geometry_snap_nodes(path, local)
        node_locals = [p for p, _k in self._snap_typed_local]
        self._snap_local = node_locals
        self._snap_offsets = [t.apply_dir(p) for p in node_locals]
        self._snap_offset_kinds = [k for _p, k in self._snap_typed_local]
        # For a circle / ellipse the meaningful anchor is the centre (Fusion /
        # LightBurn lock a circle by its centre); its quadrants otherwise steal
        # the snap.  Flag it so the canvas prefers the centre while dragging.
        from leathercad.shapes import Circle, Ellipse
        self._center_snap_priority = isinstance(self.model, (Circle, Ellipse))
        self._hole_locals = []          # local stitch-hole centres (set below)

        self._holes = None
        st = self.model.stitch
        if self.model.baked_holes:
            # grouped/baked holes: local coords, oriented into pre-translation
            self._hole_locals = [Vec2(h.point.x, h.point.y)
                                 for h in self.model.baked_holes]
            oriented = [Hole(t.apply_dir(h.point), t.apply_dir(h.tangent))
                        for h in self.model.baked_holes]
            self._holes = StitchResult(holes=oriented)
        elif st is not None and st.enabled:
            _, corner_pts, closed = self._local_geometry()
            res = stitch_polyline([Vec2(p.x, p.y) for p in local],
                                  corner_pts, closed, st)
            self._hole_locals = [Vec2(h.point.x, h.point.y) for h in res.holes]
            # orient holes into the pre-translation frame
            oriented_holes = [Hole(t.apply_dir(h.point), t.apply_dir(h.tangent))
                              for h in res.holes]
            res.holes = oriented_holes
            self._holes = res

        # pre-batch the holes into one path (and their centres into one polygon
        # for the zoomed-out dot LOD) so paint() is a single draw call
        if self._holes and self._holes.count:
            style = st.hole_style if st else "round"
            if style in ("slit", "diamond"):
                self._holes_path = _holes_to_path(
                    self._holes.holes, style, 0.0,
                    st.slit_length, st.slit_angle)
                self._holes_size_mm = st.slit_length
            else:
                self._holes_path = _holes_to_path(
                    self._holes.holes, "round",
                    st.hole_diameter if st else 1.0, 0.0, 0.0)
                self._holes_size_mm = st.hole_diameter if st else 1.0
            self._holes_pts = QPolygonF(
                [QPointF(h.point.x, h.point.y) for h in self._holes.holes])
        else:
            self._holes_path = None
            self._holes_pts = None

        if self.canvas is not None:
            self._color = QColor(self.canvas.layer_color(self.model.layer))
        self.setPos(t.x, t.y)
        self.setOpacity(max(0.05, min(1.0, self.model.opacity)))
        self._recompute_bounds()
        self.update()

    def _local_geometry(self):
        path = self.model.local_path()
        pts = path.flatten()
        return pts, list(path.corner_points), path.closed

    def _recompute_bounds(self) -> None:
        r = self._outline.boundingRect()
        pad = 2.0
        if self._holes:
            pad += max(1.0, (self.model.stitch.hole_diameter
                             if self.model.stitch else 1.0))
        self._brect = r.adjusted(-pad, -pad, pad, pad)

    # -- QGraphicsItem interface ---------------------------------------
    def boundingRect(self) -> QRectF:
        return self._brect

    def shape(self):
        # Only the outline is clickable (not the filled interior). Open paths
        # (lines, construction lines) get a straight band, not a closed sliver.
        return _outline_hit_shape(self._outline, closed=getattr(self, "_closed", True))

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        selected = self.isSelected()
        # the outline follows the shape's own layer; the stitch holes follow the
        # stitch layer -- so hiding Cut hides the outline but keeps the blue holes
        outline_vis = (self.canvas._layer_visible(self.model.layer)
                       if self.canvas else True)
        holes_vis = self.canvas.stitch_layer_visible() if self.canvas else True
        # outline -- when selected, the outline itself recolours to the selection
        # blue (so a highlighted arc/line reads clearly, not just a bounding box)
        if getattr(self.model, "construction", False):
            pen = QPen(QColor(30, 140, 255) if selected else QColor(150, 150, 160))
            pen.setStyle(Qt.DashLine)
        elif selected:
            pen = QPen(QColor(30, 140, 255))
        else:
            pen = QPen(self._color)          # finished shape -> its layer colour
        pen.setCosmetic(True)
        w = self.canvas.outline_width(selected) if self.canvas else 1.0
        pen.setWidthF(w)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if outline_vis and self._outline.size() >= 2:
            painter.drawPolyline(self._outline)

        # holes
        if holes_vis and self._holes and self._holes.count:
            hp = QPen(QColor(self.canvas.layer_color("Stitch"))
                      if self.canvas else QColor("#0066ff"))
            hp.setCosmetic(True)
            hp.setWidthF(self.canvas.line_width if self.canvas else 1.0)
            painter.setBrush(Qt.NoBrush)
            st = self.model.stitch
            if self._holes_path is not None:
                # one pre-batched draw call for ALL holes. Zoomed out far
                # enough that a hole covers < ~2px, stroking antialiased
                # ellipses is wasted work -- a plain dot reads identically
                # and paints an order of magnitude faster.
                lod = option.levelOfDetailFromTransform(painter.worldTransform())
                if (self._holes_pts is not None
                        and self._holes_size_mm * lod < 2.0):
                    hp.setWidthF(max(1.6, hp.widthF()))
                    painter.setPen(hp)
                    painter.drawPoints(self._holes_pts)
                else:
                    painter.setPen(hp)
                    painter.drawPath(self._holes_path)
            painter.setPen(hp)
            _paint_backstitch(painter, self._holes, st)

        # a dashed bounding box only for CLOSED shapes -- open segments (lines,
        # arcs from a broken-apart piece) are shown selected by their recoloured
        # outline instead, so you don't get a box floating around every corner.
        if selected and getattr(self, "_closed", True):
            sel = QPen(QColor(30, 140, 255), 0, Qt.DashLine)
            sel.setCosmetic(True)
            painter.setPen(sel)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._outline.boundingRect())

    def mousePressEvent(self, event):
        if self.canvas is not None:
            self.canvas.press_select(self, event)   # select this (+group) only
            self.canvas.begin_move_snap(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.canvas is not None:
            self.canvas.end_move_snap()

    def itemChange(self, change, value):
        # Magnetic node snapping while dragging; free otherwise.
        if change == QGraphicsItem.ItemPositionChange and self.canvas is not None:
            return self.canvas.snap_move(self, value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.model.transform.x = self.pos().x()
            self.model.transform.y = self.pos().y()
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed(self, bool(value))
        return super().itemChange(change, value)

    @property
    def hole_count(self) -> int:
        return self._holes.count if self._holes else 0

    # -- editable nodes (for vertex editing) ---------------------------
    def _geometry_snap_nodes(self, path, local):
        """Meaningful snap points in local coords, as (Vec2, kind): endpoints,
        edge/arc midpoints, arc & shape centres, circle quadrants."""
        from leathercad.shapes import Circle, Ellipse
        from leathercad.path import Arc
        m = self.model
        typed = []
        if isinstance(m, (Circle, Ellipse)):
            typed.append((Vec2(0.0, 0.0), "center"))
            typed += [(Vec2(m.rx, 0.0), "quad"), (Vec2(-m.rx, 0.0), "quad"),
                      (Vec2(0.0, m.ry), "quad"), (Vec2(0.0, -m.ry), "quad")]
            return typed
        for seg in path.segments:
            if isinstance(seg, Arc):
                mid = seg._point(seg.a0 + seg._sweep() / 2.0)
                typed += [(seg.start(), "end"), (seg.end(), "end"),
                          (mid, "mid"), (seg.center, "center")]
            else:
                a, b = seg.start(), seg.end()
                typed += [(a, "end"), (b, "end"),
                          (Vec2(0.5 * (a.x + b.x), 0.5 * (a.y + b.y)), "mid")]
        if getattr(path, "closed", False) and local:      # geometric centre
            xs = [p.x for p in local]
            ys = [p.y for p in local]
            typed.append((Vec2(0.5 * (min(xs) + max(xs)),
                               0.5 * (min(ys) + max(ys))), "center"))
        out = []                                           # drop near-duplicates
        for p, k in typed:
            if not any((p - q).length() < 1e-6 for q, _ in out):
                out.append((p, k))
        return out

    # -- box resize (drag-handle) support ------------------------------
    def _resize_points(self):
        """The local points a path-like shape box-resizes by (nodes + any arc /
        bezier control points), or None for non-path shapes. Polygons and open
        multi-point paths resize by their points; a 2-point line keeps its
        length/angle fields instead."""
        from leathercad.shapes import Polygon, PathShape, EditablePath
        sh = self.model
        if isinstance(sh, EditablePath):
            pts = list(sh.nodes)
            for e in sh.edges:
                for q in (e.mid, e.c1, e.c2):
                    if q is not None:
                        pts.append(q)
            return pts if len(pts) >= 2 else None
        if isinstance(sh, Polygon):
            return sh.points if len(sh.points) >= 3 else None
        if isinstance(sh, PathShape) and len(sh.points) >= 3:
            return sh.points          # a 2-point line is excluded (it's a line)
        return None

    def _resize_box(self):
        """(cx, cy, hx, hy) of the local point hull, or None."""
        pts = self._resize_points()
        if not pts:
            return None
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0,
                (max(xs) - min(xs)) / 2.0, (max(ys) - min(ys)) / 2.0)

    def resize_extents(self):
        """Local half-extents (hx, hy) of a box-resizable shape, or None.
        Rectangles resize by width/height, circles/ellipses by rx/ry, polygons
        and multi-point paths by their bounding box."""
        from leathercad.shapes import Rectangle, Circle, Ellipse
        sh = self.model
        if isinstance(sh, Rectangle):
            return sh.width / 2.0, sh.height / 2.0
        if isinstance(sh, (Circle, Ellipse)):
            return sh.rx, sh.ry
        box = self._resize_box()
        return (box[2], box[3]) if box is not None else None

    def resize_center(self):
        """The local point the box grips are measured from. Rect/circle/ellipse
        are centred on the origin; a path-like shape uses its bbox centre (its
        points aren't origin-centred, so the grips must follow it)."""
        from leathercad.shapes import Rectangle, Circle, Ellipse
        if isinstance(self.model, (Rectangle, Circle, Ellipse)):
            return 0.0, 0.0
        box = self._resize_box()
        return (box[0], box[1]) if box is not None else (0.0, 0.0)

    def set_resize_extents(self, hx: float, hy: float) -> None:
        from leathercad.shapes import (Rectangle, Circle, Ellipse, Polygon,
                                        PathShape, EditablePath)
        sh = self.model
        hx = max(0.5, hx)
        hy = max(0.5, hy)
        if isinstance(sh, Rectangle):
            sh.width, sh.height = 2.0 * hx, 2.0 * hy
            return
        if isinstance(sh, Circle):
            sh.rx = sh.ry = max(hx, hy)     # a circle stays round
            return
        if isinstance(sh, Ellipse):
            sh.rx, sh.ry = hx, hy
            return
        box = self._resize_box()
        if box is None:
            return
        cx, cy, ohx, ohy = box
        sx = hx / ohx if ohx > 1e-9 else 1.0
        sy = hy / ohy if ohy > 1e-9 else 1.0

        def scaled(p):
            return Vec2(cx + sx * (p.x - cx), cy + sy * (p.y - cy))

        if isinstance(sh, EditablePath):
            sh.nodes = [scaled(p) for p in sh.nodes]
            for e in sh.edges:
                if e.mid is not None:
                    e.mid = scaled(e.mid)
                if e.c1 is not None:
                    e.c1 = scaled(e.c1)
                if e.c2 is not None:
                    e.c2 = scaled(e.c2)
        else:                              # Polygon or open PathShape
            sh.points = [scaled(p) for p in sh.points]
            cr = getattr(sh, "corner_radius", 0.0)
            if cr:
                sh.corner_radius = cr * min(sx, sy)

    def world_snap_nodes(self):
        t = self.model.transform
        return [t.apply(p) for p in getattr(self, "_snap_local", [])]

    def world_snap_nodes_typed(self):
        t = self.model.transform
        out = [(t.apply(p), kind)
               for p, kind in getattr(self, "_snap_typed_local", [])]
        out += [(t.apply(p), "hole") for p in getattr(self, "_hole_locals", [])]
        return out

    def editable_nodes(self):
        from leathercad.shapes import Polygon, PathShape, EditablePath
        sh = self.model
        t = sh.transform
        out = []

        def neighbour(pts, i, closed):
            n = len(pts)
            if n < 2:
                return None
            j = i - 1 if i > 0 else (n - 1 if closed else 1)
            return t.apply(pts[j])          # world pos of the adjacent node

        if isinstance(sh, (Polygon, PathShape)):
            closed = getattr(sh, "close_path", False)
            for i in range(len(sh.points)):
                out.append(NodeRef(t.apply(sh.points[i]), self._set_point(i),
                                   ref=neighbour(sh.points, i, closed)))
        elif isinstance(sh, EditablePath):
            for i in range(len(sh.nodes)):
                out.append(NodeRef(t.apply(sh.nodes[i]), self._set_epnode(i),
                                   ref=neighbour(sh.nodes, i, sh.closed)))
            for e in sh.edges:
                if e.kind == "arc" and e.mid is not None:
                    out.append(NodeRef(t.apply(e.mid), self._set_epmid(e),
                                       is_mid=True))
                elif e.kind == "bezier":
                    if e.c1 is not None:
                        out.append(NodeRef(t.apply(e.c1), self._set_edge_c1(e),
                                           is_ctrl=True))
                    if e.c2 is not None:
                        out.append(NodeRef(t.apply(e.c2), self._set_edge_c2(e),
                                           is_ctrl=True))
        return out

    def _set_point(self, i):
        def s(world):
            self.model.points[i] = self.model.transform.inverse_apply(world)
        return s

    def _set_epnode(self, i):
        def s(world):
            sh = self.model
            new_local = sh.transform.inverse_apply(world)
            old = sh.nodes[i]
            dx, dy = new_local.x - old.x, new_local.y - old.y
            sh.nodes[i] = new_local
            # Move the bezier tangent handles attached to this anchor so the
            # curve translates with it (like every vector editor does).
            n, m = len(sh.nodes), len(sh.edges)
            e_out = sh.edges[i] if i < m else None            # node i -> i+1
            if (e_out is not None and e_out.kind == "bezier"
                    and e_out.c1 is not None):
                e_out.c1 = Vec2(e_out.c1.x + dx, e_out.c1.y + dy)
            j = i - 1 if i >= 1 else (m - 1 if sh.closed else None)   # edge -> i
            e_in = sh.edges[j] if (j is not None and 0 <= j < m) else None
            if (e_in is not None and e_in.kind == "bezier"
                    and e_in.c2 is not None):
                e_in.c2 = Vec2(e_in.c2.x + dx, e_in.c2.y + dy)
        return s

    def _set_epmid(self, edge):
        def s(world):
            edge.mid = self.model.transform.inverse_apply(world)
        return s

    def _set_edge_c1(self, edge):
        def s(world):
            edge.c1 = self.model.transform.inverse_apply(world)
        return s

    def _set_edge_c2(self, edge):
        def s(world):
            edge.c2 = self.model.transform.inverse_apply(world)
        return s


class RotateHandle(QGraphicsItem):
    """A rotation grip floating above the selected shape's box. Dragging it
    spins the shape's transform.rotation about its own origin, snapped to 1°
    (hold Shift for 15° detents). Drag is driven manually, like ResizeHandle,
    so Qt's item-move machinery never fights it."""

    SIZE = 4.5  # px (ignores view transform)
    OFFSET_PX = 22.0    # gap above the top-centre resize grip

    def __init__(self, owner, canvas):
        super().__init__()
        self.owner = owner
        self.canvas = canvas
        self.grip = ("rotate",)          # never equals a resize grip
        self._dragged = False
        self._start_rot = 0.0
        self._start_ang = 0.0
        self.setFlags(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(2100)
        self.setCursor(Qt.OpenHandCursor)
        self.reposition()

    def _grip_world(self):
        owner = self.owner
        ext = owner.resize_extents()
        if ext is None:
            return None
        _hx, hy = ext
        cx, cy = owner.resize_center()
        t = owner.model.transform
        pad = self.OFFSET_PX / max(getattr(self.canvas, "_zoom", 3.0), 1e-6)
        return t.apply(Vec2(cx, cy + hy + pad))

    def reposition(self):
        w = self._grip_world()
        if w is not None:
            self.setPos(w.x, w.y)

    def boundingRect(self) -> QRectF:
        s = self.SIZE + 5
        return QRectF(-s, -s, 2 * s, 2 * s)

    def shape(self):
        p = QPainterPath()
        s = self.SIZE + 4
        p.addEllipse(QPointF(0, 0), s, s)
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        s = self.SIZE
        painter.setPen(QPen(QColor(30, 110, 220), 1.2))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(QPointF(0, 0), s, s)
        painter.setBrush(QBrush(QColor(30, 110, 220)))
        painter.drawEllipse(QPointF(0, 0), 1.2, 1.2)

    # -- manual drag ------------------------------------------------------
    def _origin(self) -> Vec2:
        t = self.owner.model.transform
        return t.apply(Vec2(0.0, 0.0))

    def mousePressEvent(self, event):
        import math
        o = self._origin()
        sp = event.scenePos()
        self._start_ang = math.degrees(math.atan2(sp.y() - o.y, sp.x() - o.x))
        self._start_rot = self.owner.model.transform.rotation
        self._dragged = False
        if self.canvas is not None:
            self.canvas._active_resize = self       # don't reposition me mid-drag
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        import math
        o = self._origin()
        sp = event.scenePos()
        ang = math.degrees(math.atan2(sp.y() - o.y, sp.x() - o.x))
        snap = 15.0 if (event.modifiers() & Qt.ShiftModifier) else 1.0
        rot = self._start_rot + (ang - self._start_ang)
        rot = round(rot / snap) * snap
        self.apply_rotation(rot)
        self._dragged = True
        event.accept()

    def apply_rotation(self, degrees_ccw: float) -> None:
        t = self.owner.model.transform
        t.rotation = ((degrees_ccw + 180.0) % 360.0) - 180.0
        self.owner.sync_from_model()
        self.reposition()
        if self.canvas is not None:
            self.canvas.resize_handle_moved(self)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        if self.canvas is not None:
            self.canvas._active_resize = None
        if self._dragged and self.canvas is not None:
            self._dragged = False
            self.canvas.commitRequested.emit()
        event.accept()


class NodeRef:
    """One editable node: its world position + a setter that takes a new world
    point and writes it back to the model (converting to local)."""

    __slots__ = ("world", "setter", "is_mid", "ref", "is_ctrl")

    def __init__(self, world: Vec2, setter, is_mid: bool = False, ref=None,
                 is_ctrl: bool = False):
        self.world = world
        self.setter = setter
        self.is_mid = is_mid
        self.ref = ref     # world pos of the adjacent node (for Shift-ortho)
        self.is_ctrl = is_ctrl   # a bezier tangent-control handle


class StitchLineItem(QGraphicsItem):
    """Renders a shared seam (StitchLine): the path plus its holes."""

    def __init__(self, line: StitchLine, canvas=None):
        super().__init__()
        self.line = line
        self.canvas = canvas
        self._poly = QPolygonF()
        self._holes: Optional[StitchResult] = None
        self._holes_path: Optional[QPainterPath] = None
        self._holes_pts: Optional[QPolygonF] = None
        self._holes_size_mm = 1.0
        self._brect = QRectF()
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.sync_from_model()

    def sync_from_model(self) -> None:
        # Paint relative to a base point and position the item with setPos, so
        # dragging follows the cursor smoothly (no mid-drag point mutation).
        self.prepareGeometryChange()
        pts = self.line.points
        self._base = Vec2(pts[0].x, pts[0].y) if pts else Vec2(0.0, 0.0)
        rel = [Vec2(p.x - self._base.x, p.y - self._base.y) for p in pts]
        self._poly = _qpoly(rel)
        self._holes = self.line.result()
        self._rel_holes = [Hole(Vec2(h.point.x - self._base.x,
                                     h.point.y - self._base.y), h.tangent)
                           for h in self._holes.holes]
        # pre-batch the holes into one path (+ centres for the dot LOD) so
        # paint() is a single draw call
        st = self.line.settings
        if self._rel_holes:
            style = st.hole_style if st else "round"
            if style in ("slit", "diamond"):
                self._holes_path = _holes_to_path(
                    self._rel_holes, style, 0.0, st.slit_length, st.slit_angle)
                self._holes_size_mm = st.slit_length
            else:
                self._holes_path = _holes_to_path(
                    self._rel_holes, "round",
                    st.hole_diameter if st else 1.0, 0.0, 0.0)
                self._holes_size_mm = st.hole_diameter if st else 1.0
            self._holes_pts = QPolygonF(
                [QPointF(h.point.x, h.point.y) for h in self._rel_holes])
        else:
            self._holes_path = None
            self._holes_pts = None
        r = self._poly.boundingRect()
        self._brect = r.adjusted(-3, -3, 3, 3)
        self.setPos(self._base.x, self._base.y)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._brect

    def shape(self):
        return _outline_hit_shape(self._poly, closed=self.line.closed)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        guide = QPen(QColor(120, 120, 120), 0, Qt.DotLine)
        guide.setCosmetic(True)
        painter.setPen(guide)
        if self._poly.size() >= 2:
            painter.drawPolyline(self._poly)
        hp = QPen(QColor(self.canvas.layer_color("Stitch"))
                  if self.canvas else QColor("#0066ff"))
        hp.setCosmetic(True)
        hp.setWidthF(self.canvas.line_width if self.canvas else 1.0)
        painter.setBrush(Qt.NoBrush)
        if self._holes_path is not None:
            lod = option.levelOfDetailFromTransform(painter.worldTransform())
            if (self._holes_pts is not None
                    and self._holes_size_mm * lod < 2.0):
                hp.setWidthF(max(1.6, hp.widthF()))
                painter.setPen(hp)
                painter.drawPoints(self._holes_pts)
            else:
                painter.setPen(hp)
                painter.drawPath(self._holes_path)
        painter.setPen(hp)
        _paint_backstitch(painter, StitchResult(holes=self._rel_holes,
                                                closed=self._holes.closed),
                          self.line.settings)
        if self.isSelected() and self._poly.size() >= 2:
            # Highlight the seam itself, not a bounding box -- it is just a line.
            sel = QPen(QColor(30, 140, 255))
            sel.setCosmetic(True)
            sel.setWidthF((self.canvas.outline_width(True)
                           if self.canvas else 2.0) + 0.6)
            painter.setPen(sel)
            painter.drawPolyline(self._poly)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed(self, bool(value))
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if self.canvas is not None:
            self.canvas.press_select(self, event)   # select this (+group) only
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._bake_move()

    def _bake_move(self):
        """Bake the drag offset (setPos) into the seam points, once, on release."""
        dx = self.pos().x() - self._base.x
        dy = self.pos().y() - self._base.y
        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            self.line.points = [Vec2(p.x + dx, p.y + dy) for p in self.line.points]
            self.line.corner_points = [Vec2(p.x + dx, p.y + dy)
                                       for p in self.line.corner_points]
            self.sync_from_model()
            if self.canvas is not None:
                self.canvas.commitRequested.emit()

    @property
    def hole_count(self) -> int:
        return self._holes.count if self._holes else 0

    def editable_nodes(self):
        out = []
        for i in range(len(self.line.points)):
            p = self.line.points[i]
            out.append(NodeRef(Vec2(p.x, p.y), self._set_pt(i)))
        return out

    def _set_pt(self, i):
        def s(world):
            self.line.points[i] = Vec2(world.x, world.y)
        return s

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
                           QPolygonF, QBrush)
from PySide6.QtWidgets import QGraphicsItem, QApplication

# Click tolerance (mm) around an outline for selection/hit-testing.
OUTLINE_HIT_MM = 2.0


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


def _draw_holes(painter, holes, style, diameter, slit_len, slit_angle):
    import math
    if style == "slit":
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
        if self.node.is_mid:   # arc midpoint
            painter.setPen(QPen(QColor(210, 120, 0), 1))
            painter.setBrush(QBrush(QColor(255, 235, 200)))
            painter.drawEllipse(QPointF(0, 0), s, s)
        else:                  # on-path node
            painter.setPen(QPen(QColor(30, 110, 220), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.drawRect(QRectF(-s, -s, 2 * s, 2 * s))

    def mousePressEvent(self, event):
        if self.canvas is not None:
            self.canvas.begin_node_snap(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.canvas is not None:
            self.canvas.end_node_snap()
        if self._dragged and self.canvas is not None:
            self._dragged = False
            self.canvas.commitRequested.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.canvas is not None:
            ref = getattr(self.node, "ref", None)
            shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
            if shift and ref is not None:
                # constrain the segment to this node's neighbour to 0 / 90 deg
                dx, dy = value.x() - ref.x, value.y() - ref.y
                if abs(dx) >= abs(dy):
                    return QPointF(value.x(), ref.y)     # horizontal
                return QPointF(ref.x, value.y())         # vertical
            return self.canvas.snap_node(value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._dragged = True
            self.node.setter(Vec2(self.pos().x(), self.pos().y()))
            self.owner.sync_from_model()
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

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.hole.point = Vec2(self.pos().x(), self.pos().y())
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed()
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
        # outline
        if getattr(self.model, "construction", False):
            pen = QPen(QColor(150, 150, 160))
            pen.setStyle(Qt.DashLine)
        else:
            pen = QPen(self._color)
        pen.setCosmetic(True)
        pen.setWidthF(1.6 if self.isSelected() else 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if self._outline.size() >= 2:
            painter.drawPolyline(self._outline)

        # holes
        if self._holes and self._holes.count:
            hp = QPen(QColor(self.canvas.layer_color("Stitch"))
                      if self.canvas else QColor("#0066ff"))
            hp.setCosmetic(True)
            hp.setWidthF(1.0)
            painter.setPen(hp)
            st = self.model.stitch
            if st and st.hole_style == "slit":
                half = st.slit_length / 2.0
                import math
                ca = math.cos(math.radians(st.slit_angle))
                sa = math.sin(math.radians(st.slit_angle))
                for h in self._holes.holes:
                    # rotate tangent by slit angle
                    dx = h.tangent.x * ca - h.tangent.y * sa
                    dy = h.tangent.x * sa + h.tangent.y * ca
                    painter.drawLine(
                        QPointF(h.point.x - dx * half, h.point.y - dy * half),
                        QPointF(h.point.x + dx * half, h.point.y + dy * half))
            else:
                d = (st.hole_diameter if st else 1.0)
                r = d / 2.0
                for h in self._holes.holes:
                    painter.drawEllipse(QPointF(h.point.x, h.point.y), r, r)
            _paint_backstitch(painter, self._holes, st)

        if self.isSelected():
            sel = QPen(QColor(30, 140, 255), 0, Qt.DashLine)
            sel.setCosmetic(True)
            painter.setPen(sel)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._outline.boundingRect())

    def mousePressEvent(self, event):
        if self.canvas is not None:
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
                self.canvas.selection_changed()
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
        return out

    def _set_point(self, i):
        def s(world):
            self.model.points[i] = self.model.transform.inverse_apply(world)
        return s

    def _set_epnode(self, i):
        def s(world):
            self.model.nodes[i] = self.model.transform.inverse_apply(world)
        return s

    def _set_epmid(self, edge):
        def s(world):
            edge.mid = self.model.transform.inverse_apply(world)
        return s


class NodeRef:
    """One editable node: its world position + a setter that takes a new world
    point and writes it back to the model (converting to local)."""

    __slots__ = ("world", "setter", "is_mid", "ref")

    def __init__(self, world: Vec2, setter, is_mid: bool = False, ref=None):
        self.world = world
        self.setter = setter
        self.is_mid = is_mid
        self.ref = ref     # world pos of the adjacent node (for Shift-ortho)


class StitchLineItem(QGraphicsItem):
    """Renders a shared seam (StitchLine): the path plus its holes."""

    def __init__(self, line: StitchLine, canvas=None):
        super().__init__()
        self.line = line
        self.canvas = canvas
        self._poly = QPolygonF()
        self._holes: Optional[StitchResult] = None
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
        painter.setPen(hp)
        for h in self._rel_holes:
            painter.drawEllipse(QPointF(h.point.x, h.point.y), 0.5, 0.5)
        _paint_backstitch(painter, StitchResult(holes=self._rel_holes,
                                                closed=self._holes.closed),
                          self.line.settings)
        if self.isSelected():
            sel = QPen(QColor(30, 140, 255), 0, Qt.DashLine)
            sel.setCosmetic(True)
            painter.setPen(sel)
            painter.drawRect(self._poly.boundingRect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed()
        return super().itemChange(change, value)

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

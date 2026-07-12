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
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF, QBrush
from PySide6.QtWidgets import QGraphicsItem

from leathercad.geometry import Vec2
from leathercad.shapes import Shape
from leathercad.stitchline import StitchLine
from leathercad.stitching import stitch_polyline, StitchResult


def _qpoly(points: List[Vec2]) -> QPolygonF:
    return QPolygonF([QPointF(p.x, p.y) for p in points])


class ShapeItem(QGraphicsItem):
    """Renders a model ``Shape`` (outline + stitch holes)."""

    def __init__(self, shape: Shape, canvas=None):
        super().__init__()
        self.shape = shape
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
        t = self.shape.transform
        # oriented (rotation+mirror), pre-translation -- matches Transform.apply
        local = self.shape.local_path().flatten()
        oriented = [t.apply_dir(p) for p in local]
        self._outline = _qpoly(oriented)

        self._holes = None
        st = self.shape.stitch
        if st is not None and st.enabled:
            _, corner_pts, closed = self._local_geometry()
            res = stitch_polyline([Vec2(p.x, p.y) for p in local],
                                  corner_pts, closed, st)
            # orient holes into the pre-translation frame
            oriented_holes = []
            from leathercad.stitching import Hole
            for h in res.holes:
                oriented_holes.append(
                    Hole(t.apply_dir(h.point), t.apply_dir(h.tangent)))
            res.holes = oriented_holes
            self._holes = res

        if self.canvas is not None:
            self._color = QColor(self.canvas.layer_color(self.shape.layer))
        self.setPos(t.x, t.y)
        self.setOpacity(max(0.05, min(1.0, self.shape.opacity)))
        self._recompute_bounds()
        self.update()

    def _local_geometry(self):
        path = self.shape.local_path()
        pts = path.flatten()
        return pts, list(path.corner_points), path.closed

    def _recompute_bounds(self) -> None:
        r = self._outline.boundingRect()
        pad = 2.0
        if self._holes:
            pad += max(1.0, (self.shape.stitch.hole_diameter
                             if self.shape.stitch else 1.0))
        self._brect = r.adjusted(-pad, -pad, pad, pad)

    # -- QGraphicsItem interface ---------------------------------------
    def boundingRect(self) -> QRectF:
        return self._brect

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        # outline
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
            st = self.shape.stitch
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

        if self.isSelected():
            sel = QPen(QColor(30, 140, 255), 0, Qt.DashLine)
            sel.setCosmetic(True)
            painter.setPen(sel)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._outline.boundingRect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if (self.canvas is not None and self.canvas.snap_enabled
                    and self.canvas.snap_grid > 0):
                g = self.canvas.snap_grid
                from PySide6.QtCore import QPointF
                value = QPointF(round(value.x() / g) * g,
                                round(value.y() / g) * g)
            return value
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.shape.transform.x = self.pos().x()
            self.shape.transform.y = self.pos().y()
            if self.canvas is not None:
                self.canvas.item_moved(self)
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed()
        return super().itemChange(change, value)

    @property
    def hole_count(self) -> int:
        return self._holes.count if self._holes else 0


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
        self.prepareGeometryChange()
        self._poly = _qpoly(self.line.points)
        self._holes = self.line.result()
        r = self._poly.boundingRect()
        self._brect = r.adjusted(-3, -3, 3, 3)
        self.setPos(0, 0)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._brect

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
        if self._holes:
            for h in self._holes.holes:
                painter.drawEllipse(QPointF(h.point.x, h.point.y), 0.5, 0.5)
        if self.isSelected():
            sel = QPen(QColor(30, 140, 255), 0, Qt.DashLine)
            sel.setCosmetic(True)
            painter.setPen(sel)
            painter.drawRect(self._poly.boundingRect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            # translate the underlying points by the moved delta
            dx = self.pos().x()
            dy = self.pos().y()
            if dx or dy:
                self.line.points = [Vec2(p.x + dx, p.y + dy)
                                    for p in self.line.points]
                self.line.corner_points = [Vec2(p.x + dx, p.y + dy)
                                           for p in self.line.corner_points]
                self.setPos(0, 0)
                self.sync_from_model()
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            if self.canvas is not None:
                self.canvas.selection_changed()
        return super().itemChange(change, value)

    @property
    def hole_count(self) -> int:
        return self._holes.count if self._holes else 0

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
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QGraphicsPathItem

from leathercad.geometry import Vec2
from leathercad.document import Document
from leathercad.shapes import (Rectangle, Ellipse, Circle, Polygon, PathShape,
                               Transform)
from leathercad.stitchsettings import StitchSettings
from leathercad.stitchline import StitchLine
from .items import ShapeItem, StitchLineItem


# tool modes
SELECT = "select"
RECT = "rect"
ROUNDED = "rounded"
ELLIPSE = "ellipse"
CIRCLE = "circle"
POLYGON = "polygon"
STITCHLINE = "stitchline"


class Canvas(QGraphicsView):
    selectionChangedSig = Signal()
    documentChangedSig = Signal()
    toolFinished = Signal()
    cursorMoved = Signal(float, float)
    commitRequested = Signal()   # a discrete edit finished -> push undo snapshot

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

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._pan_last = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        pos = self.mapToScene(event.position().toPoint())
        if self.tool == SELECT:
            return super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            if self.tool in (RECT, ROUNDED, ELLIPSE, CIRCLE):
                self._start = pos
                self._preview = QGraphicsPathItem()
                pen = QPen(QColor(120, 120, 120), 0, Qt.DashLine)
                pen.setCosmetic(True)
                self._preview.setPen(pen)
                self.scene_obj.addItem(self._preview)
            elif self.tool in (POLYGON, STITCHLINE):
                self._poly_pts.append(pos)
                self._update_poly_preview(pos)
        event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MiddleButton and hasattr(self, "_pan_last"):
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.translate(delta.x() / self._zoom, -delta.y() / self._zoom)
            return
        pos = self.mapToScene(event.position().toPoint())
        self.cursorMoved.emit(pos.x(), pos.y())
        if self._preview is not None and self._start is not None:
            self._preview.setPath(self._preview_path(self._start, pos))
        elif self._poly_pts and self.tool in (POLYGON, STITCHLINE):
            self._update_poly_preview(pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.setCursor(Qt.ArrowCursor)
            return
        pos = self.mapToScene(event.position().toPoint())
        if self._preview is not None and self._start is not None:
            self.scene_obj.removeItem(self._preview)
            self._preview = None
            self._finalize_drag(self._start, pos)
            self._start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self._moved_during_press:
            self._moved_during_press = False
            self._emit_commit()

    def mouseDoubleClickEvent(self, event):
        if self.tool in (POLYGON, STITCHLINE) and self._poly_pts:
            self._finalize_poly()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancel_poly()
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
        else:
            return
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

    # -- document <-> scene --------------------------------------------
    def rebuild(self) -> None:
        """Rebuild all items from the document (after open/load)."""
        self.scene_obj.clear()
        self._preview = None
        for sh in self.doc.shapes:
            self.scene_obj.addItem(ShapeItem(sh, self))
        for sl in self.doc.stitch_lines:
            self.scene_obj.addItem(StitchLineItem(sl, self))
        self.documentChangedSig.emit()

    def add_shape(self, shape) -> ShapeItem:
        self.doc.add_shape(shape)
        item = ShapeItem(shape, self)
        self.scene_obj.addItem(item)
        self.scene_obj.clearSelection()
        item.setSelected(True)
        self.documentChangedSig.emit()
        self._emit_commit()
        return item

    def add_stitch_line(self, line) -> StitchLineItem:
        self.doc.add_stitch_line(line)
        item = StitchLineItem(line, self)
        self.scene_obj.addItem(item)
        self.documentChangedSig.emit()
        self._emit_commit()
        return item

    def selected_items(self):
        return [it for it in self.scene_obj.selectedItems()
                if isinstance(it, (ShapeItem, StitchLineItem))]

    def delete_selected(self) -> None:
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                self.doc.remove_shape(it.shape)
            else:
                self.doc.remove_stitch_line(it.line)
            self.scene_obj.removeItem(it)
        self.documentChangedSig.emit()
        self.selectionChangedSig.emit()
        self._emit_commit()

    def duplicate_selected(self) -> None:
        import copy
        new_items = []
        self._suppress_commit = True
        for it in self.selected_items():
            if isinstance(it, ShapeItem):
                sh = copy.deepcopy(it.shape)
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
        boxes = [(it, it.shape.bounds()) for it in items]
        if mode in ("left", "hcenter", "right"):
            if mode == "left":
                target = min(b[0] for _, b in boxes)
                for it, b in boxes:
                    it.shape.transform.x += target - b[0]
            elif mode == "right":
                target = max(b[2] for _, b in boxes)
                for it, b in boxes:
                    it.shape.transform.x += target - b[2]
            else:
                target = sum((b[0] + b[2]) / 2 for _, b in boxes) / len(boxes)
                for it, b in boxes:
                    it.shape.transform.x += target - (b[0] + b[2]) / 2
        else:  # top/vcenter/bottom
            if mode == "bottom":
                target = min(b[1] for _, b in boxes)
                for it, b in boxes:
                    it.shape.transform.y += target - b[1]
            elif mode == "top":
                target = max(b[3] for _, b in boxes)
                for it, b in boxes:
                    it.shape.transform.y += target - b[3]
            else:
                target = sum((b[1] + b[3]) / 2 for _, b in boxes) / len(boxes)
                for it, b in boxes:
                    it.shape.transform.y += target - (b[1] + b[3]) / 2
        for it, _ in boxes:
            it.sync_from_model()
        self.documentChangedSig.emit()
        self._emit_commit()

    def distribute_selected(self, horizontal: bool) -> None:
        items = self._shape_items()
        if len(items) < 3:
            return
        def center(it):
            b = it.shape.bounds()
            return ((b[0] + b[2]) / 2) if horizontal else ((b[1] + b[3]) / 2)
        items.sort(key=center)
        lo, hi = center(items[0]), center(items[-1])
        step = (hi - lo) / (len(items) - 1)
        for i, it in enumerate(items[1:-1], start=1):
            c = center(it)
            target = lo + step * i
            if horizontal:
                it.shape.transform.x += target - c
            else:
                it.shape.transform.y += target - c
            it.sync_from_model()
        self.documentChangedSig.emit()
        self._emit_commit()

    def item_moved(self, item) -> None:
        self._moved_during_press = True
        self.documentChangedSig.emit()

    def selection_changed(self) -> None:
        self.selectionChangedSig.emit()

    def refresh_item(self, item) -> None:
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
            if isinstance(it, (ShapeItem, StitchLineItem)):
                n += it.hole_count
        return n

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

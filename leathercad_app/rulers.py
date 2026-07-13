"""Millimetre rulers for the canvas + drag-out guides.

Two thin widgets sit above and left of the canvas, painting adaptive mm ticks
that track the canvas zoom/pan (they repaint whenever the canvas viewport
paints). A marker follows the cursor. Press-and-drag *out of a ruler* onto
the canvas to drop a guide: the top ruler drops a horizontal guide, the left
ruler a vertical one -- guides are ordinary construction lines (snappable
along their whole body, never exported, deletable like any shape).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

THICKNESS = 22          # px

_BG = QColor(246, 246, 244)
_FG = QColor(110, 110, 112)
_TICK = QColor(150, 150, 152)
_MARK = QColor(30, 140, 255)


def _nice_step(px_per_mm: float, min_px: float = 46.0) -> float:
    """Smallest 1/2/5*10^k mm step whose labels are at least min_px apart."""
    step = 1.0
    while step * px_per_mm < min_px:
        for k in (2.0, 2.5, 2.0):
            step *= k
            if step * px_per_mm >= min_px:
                break
    return step


class Ruler(QWidget):
    def __init__(self, canvas, horizontal: bool, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.horizontal = horizontal
        self._cursor_mm: float | None = None
        self._dragging = False
        if horizontal:
            self.setFixedHeight(THICKNESS)
        else:
            self.setFixedWidth(THICKNESS)
        self.setMouseTracking(True)
        canvas.viewport().installEventFilter(self)
        canvas.cursorMoved.connect(self._cursor_moved)

    # repaint in step with the canvas
    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Paint, QEvent.Resize, QEvent.Wheel):
            self.update()
        return False

    def _cursor_moved(self, x: float, y: float):
        self._cursor_mm = x if self.horizontal else y
        self.update()

    # -- painting ---------------------------------------------------------
    def paintEvent(self, ev):
        from .theme import RULER
        sw = RULER[getattr(self.canvas, "dark", False)]
        bg, fg, tick = sw["bg"], sw["fg"], sw["tick"]
        p = QPainter(self)
        p.fillRect(self.rect(), bg)
        p.setPen(QPen(tick, 1))
        c = self.canvas
        vp = c.viewport()
        # scene mm across the ruler
        if self.horizontal:
            a = c.mapToScene(0, 0).x()
            b = c.mapToScene(vp.width(), 0).x()
            length = self.width()
        else:
            a = c.mapToScene(0, 0).y()
            b = c.mapToScene(0, vp.height()).y()
            length = self.height()
        lo, hi = min(a, b), max(a, b)
        span = max(hi - lo, 1e-6)
        px_per_mm = length / span
        step = _nice_step(px_per_mm)
        font = QFont()
        font.setPixelSize(9)
        p.setFont(font)

        def to_px(mm: float) -> float:
            if self.horizontal:
                return c.mapFromScene(QPointF(mm, 0)).x()
            return c.mapFromScene(QPointF(0, mm)).y()

        import math
        t = math.floor(lo / step) * step
        while t <= hi + step:
            px = to_px(t)
            minor = step / 5.0
            for k in range(1, 5):
                mpx = to_px(t + k * minor)
                if self.horizontal:
                    p.drawLine(QPointF(mpx, THICKNESS - 5), QPointF(mpx, THICKNESS))
                else:
                    p.drawLine(QPointF(THICKNESS - 5, mpx), QPointF(THICKNESS, mpx))
            label = f"{t:g}"
            if self.horizontal:
                p.drawLine(QPointF(px, THICKNESS - 10), QPointF(px, THICKNESS))
                p.setPen(QPen(fg, 1))
                p.drawText(QRectF(px + 2, 0, 60, THICKNESS - 8),
                           Qt.AlignLeft | Qt.AlignVCenter, label)
                p.setPen(QPen(tick, 1))
            else:
                p.drawLine(QPointF(THICKNESS - 10, px), QPointF(THICKNESS, px))
                p.setPen(QPen(fg, 1))
                p.save()
                p.translate(4, px - 2)
                p.rotate(-90)
                p.drawText(QRectF(0, 0, 60, THICKNESS - 8),
                           Qt.AlignLeft | Qt.AlignVCenter, label)
                p.restore()
                p.setPen(QPen(tick, 1))
            t += step

        if self._cursor_mm is not None:
            p.setPen(QPen(_MARK, 1))
            px = to_px(self._cursor_mm)
            if self.horizontal:
                p.drawLine(QPointF(px, 0), QPointF(px, THICKNESS))
            else:
                p.drawLine(QPointF(0, px), QPointF(THICKNESS, px))
        p.end()

    # -- drag a guide out of the ruler --------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.SplitVCursor if self.horizontal else Qt.SplitHCursor)

    def mouseReleaseEvent(self, ev):
        if not self._dragging:
            return
        self._dragging = False
        self.unsetCursor()
        vp = self.canvas.viewport()
        pos = vp.mapFromGlobal(ev.globalPosition().toPoint())
        if not vp.rect().contains(pos):
            return                    # released back on the ruler: no guide
        scene = self.canvas.mapToScene(pos)
        if self.horizontal:
            self.canvas.add_guide("h", scene.y())     # top ruler -> horizontal
        else:
            self.canvas.add_guide("v", scene.x())     # left ruler -> vertical

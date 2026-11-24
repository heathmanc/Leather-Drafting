"""The "resize from" picker beside the toolbar's W and H boxes.

A width or height typed in W or H resizes the selection about one point of
its box, which stays put: a corner, the middle of a side, or the centre.
The picker is that box drawn small with its nine points (like a shape's
resize grips); click one to choose it. The chosen point is filled in."""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QWidget

# Each point is (gx, gy): gx -1 the left side .. 1 the right, gy 1 the top
# .. -1 the bottom (the drawing's y runs up the page).
NAMES = {(-1, 1): "top left", (0, 1): "top", (1, 1): "top right",
         (-1, 0): "left", (0, 0): "centre", (1, 0): "right",
         (-1, -1): "bottom left", (0, -1): "bottom", (1, -1): "bottom right"}
# how a point is saved in the settings
KEYS = {(-1, 1): "tl", (0, 1): "t", (1, 1): "tr",
        (-1, 0): "l", (0, 0): "c", (1, 0): "r",
        (-1, -1): "bl", (0, -1): "b", (1, -1): "br"}
_FROM_KEY = {v: k for k, v in KEYS.items()}


def origin_from_key(key) -> tuple:
    """The point a saved key names; the centre for anything else."""
    return _FROM_KEY.get(str(key), (0, 0))


class OriginPicker(QWidget):
    """Nine points on a small box; one is chosen. ``originChanged`` gives
    the new ``(gx, gy)`` when a click chooses another."""

    originChanged = Signal(tuple)

    SIDE = 24            # px: the box of points is SIDE tall
    WIDE = 30            # px: and the widget a little wider, to stand clear
    STEP = 8             # px between neighbouring points

    def __init__(self, parent=None):
        super().__init__(parent)
        self._origin = (0, 0)
        self._hover = None
        self.setFixedSize(self.WIDE, self.SIDE)
        self.setMouseTracking(True)                # the point under it lights
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keys stay on the canvas
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Resize from")
        self._retip()

    # -- the point -------------------------------------------------------
    def origin(self) -> tuple:
        return self._origin

    def setOrigin(self, origin) -> None:
        origin = (int(origin[0]), int(origin[1]))
        if origin not in NAMES or origin == self._origin:
            return
        self._origin = origin
        self._retip()
        self.update()
        self.originChanged.emit(origin)

    def _retip(self) -> None:
        self.setToolTip(
            f"Resize from: the {NAMES[self._origin]}.\n"
            "A width or height typed in W or H resizes the selection\n"
            "about this point of its box, which stays put. Click one\n"
            "of the nine points to choose: a corner, the middle of a\n"
            "side, or the centre.")

    def _centre(self, g) -> QPoint:
        """Where point ``g`` is drawn (whole pixels, so it's crisp)."""
        return QPoint(self.width() // 2 + g[0] * self.STEP,
                      self.height() // 2 - g[1] * self.STEP)

    def point_at(self, pos):
        """The point a click at ``pos`` (widget px) chooses: the nearest of
        the nine -- each owns a third of the widget each way."""
        col = min(2, max(0, int(pos.x() * 3 // self.width())))
        row = min(2, max(0, int(pos.y() * 3 // self.height())))
        return (col - 1, 1 - row)

    # -- the mouse -------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setOrigin(self.point_at(event.position()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        g = self.point_at(event.position())
        if g != self._hover:
            self._hover = g
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self.update()
        super().leaveEvent(event)

    # -- drawing ---------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)                     # square pixels: no smoothing
        pal = self.palette()
        ink = pal.color(QPalette.ColorRole.WindowText)
        accent = pal.color(QPalette.ColorRole.Highlight)
        paper = pal.color(QPalette.ColorRole.Base)
        # the box through the outer points
        edge = QColor(ink)
        edge.setAlphaF(0.5)
        p.setPen(QPen(edge, 0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        a = self._centre((-1, 1))              # its top left point
        p.drawRect(QRect(a.x(), a.y(), 2 * self.STEP, 2 * self.STEP))
        # the nine points: little grips (5 px), the chosen one filled (7 px)
        for g in NAMES:
            c = self._centre(g)
            if g == self._origin:
                p.fillRect(QRect(c.x() - 3, c.y() - 3, 7, 7), accent)
            else:
                p.fillRect(QRect(c.x() - 2, c.y() - 2, 5, 5), paper)
                p.setPen(QPen(accent if g == self._hover else ink, 0))
                p.drawRect(QRect(c.x() - 2, c.y() - 2, 4, 4))
        p.end()

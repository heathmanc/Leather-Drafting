"""Simple vector tool icons drawn at runtime (no asset files needed)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF


_FG_LIGHT = QColor(60, 60, 66)
_FG_DARK = QColor(210, 212, 218)
_ACCENT_LIGHT = QColor(30, 110, 220)
_ACCENT_DARK = QColor(100, 165, 255)

# current stroke colours -- swapped by tool_icon(dark=...) before drawing
_FG = _FG_LIGHT
_ACCENT = _ACCENT_LIGHT


def _canvas(size: int):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(_FG)
    pen.setWidthF(1.6)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    return pm, p


def _dots_along(p, pts, color=None, r=1.3):
    p.setBrush(color if color is not None else _ACCENT)
    p.setPen(Qt.NoPen)
    for pt in pts:
        p.drawEllipse(pt, r, r)


def tool_icon(kind: str, size: int = 22, dark: bool = False) -> QIcon:
    # swap the stroke colours for the theme (GUI is single-threaded)
    global _FG, _ACCENT
    _FG = _FG_DARK if dark else _FG_LIGHT
    _ACCENT = _ACCENT_DARK if dark else _ACCENT_LIGHT
    pm, p = _canvas(size)
    m = 4
    rect = QRectF(m, m, size - 2 * m, size - 2 * m)

    if kind == "select":
        poly = QPolygonF([QPointF(6, 4), QPointF(6, 17), QPointF(9.5, 13.5),
                          QPointF(12, 18), QPointF(14, 17), QPointF(11.5, 12.5),
                          QPointF(16, 12)])
        p.setBrush(_FG)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly)
    elif kind == "rect":
        p.drawRect(rect)
    elif kind == "rounded":
        p.drawRoundedRect(rect, 4, 4)
    elif kind == "ellipse":
        p.drawEllipse(rect.adjusted(0, 2, 0, -2))
    elif kind == "circle":
        d = min(rect.width(), rect.height())
        p.drawEllipse(QRectF(rect.center().x() - d / 2, rect.center().y() - d / 2, d, d))
    elif kind == "polygon":
        cx, cy, rr = size / 2, size / 2, size / 2 - m
        poly = QPolygonF([QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
                          for a in [math.radians(-90 + 72 * i) for i in range(5)]])
        p.drawPolygon(poly)
    elif kind == "hole":
        c = rect.center()
        p.drawEllipse(c, 5, 5)
        p.setBrush(_FG); p.setPen(Qt.NoPen)
        p.drawEllipse(c, 1.6, 1.6)
    elif kind == "slot":
        r2 = rect.height() / 2
        p.drawRoundedRect(rect, r2, r2)
    elif kind == "score":
        pen = p.pen(); pen.setStyle(Qt.DashLine); p.setPen(pen)
        p.drawLine(QPointF(m, size / 2), QPointF(size - m, size / 2))
    elif kind == "stitchline":
        pen = p.pen(); pen.setStyle(Qt.DotLine); p.setPen(pen)
        y = size / 2
        p.drawLine(QPointF(m, y), QPointF(size - m, y))
        _dots_along(p, [QPointF(x, y) for x in range(m + 1, size - m, 4)])
    elif kind == "pin":
        # a push-pin
        p.setBrush(_ACCENT); p.setPen(QPen(_FG, 1.2))
        p.drawEllipse(QPointF(size / 2, size / 2 - 3), 4, 4)
        p.setPen(QPen(_FG, 1.6))
        p.drawLine(QPointF(size / 2, size / 2 + 1), QPointF(size / 2, size - m))
    elif kind == "ungroup":
        p.drawRect(QRectF(m, m, 8, 8))
        p.drawRect(QRectF(size - m - 8, size - m - 8, 8, 8))
        _dots_along(p, [QPointF(size / 2, size / 2)], _ACCENT, 1.4)
    elif kind == "line":
        p.drawLine(QPointF(m + 1, size - m - 1), QPointF(size - m - 1, m + 1))
        _dots_along(p, [QPointF(m + 1, size - m - 1),
                        QPointF(size - m - 1, m + 1)], _ACCENT, 1.5)
    elif kind == "construction":
        pen = p.pen(); pen.setStyle(Qt.DashLine); p.setPen(pen)
        p.drawLine(QPointF(m, size - m - 1), QPointF(size - m, m + 1))
    elif kind == "trim":
        # scissors: two blades crossing, rings at the bottom
        b1 = QPointF(size * 0.28, size - m - 1)
        b2 = QPointF(size * 0.72, size - m - 1)
        p.drawLine(QPointF(size * 0.78, m), b1)
        p.drawLine(QPointF(size * 0.22, m), b2)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(b1, 2.3, 2.3)
        p.drawEllipse(b2, 2.3, 2.3)
    elif kind == "measure":
        # a ruler: a line with tick marks
        y = size * 0.5
        p.drawLine(QPointF(m, y), QPointF(size - m, y))
        n = 5
        for i in range(n + 1):
            x = m + (size - 2 * m) * i / n
            hgt = 4.0 if i % n == 0 else 2.5
            p.drawLine(QPointF(x, y), QPointF(x, y - hgt))
    elif kind == "text":
        # a capital "A"
        f = p.font()
        f.setPointSizeF(size * 0.6)
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, "A")
    elif kind == "dimension":
        # dimension line with arrow tips + extension ticks
        y = size * 0.55
        a = QPointF(m + 1, y)
        b = QPointF(size - m - 1, y)
        p.drawLine(a, b)
        p.drawLine(QPointF(a.x(), y - 4), QPointF(a.x(), y + 2))
        p.drawLine(QPointF(b.x(), y - 4), QPointF(b.x(), y + 2))
        for tip, d in ((a, 1), (b, -1)):
            p.drawLine(tip, QPointF(tip.x() + d * 3, y - 2))
            p.drawLine(tip, QPointF(tip.x() + d * 3, y + 2))
    elif kind == "arc":
        # a quarter/half arc with its two endpoint dots
        cx, cy = size * 0.32, size * 0.7
        rr = size * 0.5
        rect_a = QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr)
        p.drawArc(rect_a, 0 * 16, 90 * 16)
        import math as _m
        a = QPointF(cx + rr, cy)
        b = QPointF(cx, cy - rr)
        _dots_along(p, [a, b], color=_ACCENT, r=1.4)
    elif kind == "circle2":
        d = min(rect.width(), rect.height())
        cc = rect.center()
        p.drawEllipse(QRectF(cc.x() - d / 2, cc.y() - d / 2, d, d))
        _dots_along(p, [QPointF(cc.x() - d / 2, cc.y()),
                        QPointF(cc.x() + d / 2, cc.y())], color=_ACCENT, r=1.5)
    elif kind == "circle3":
        d = min(rect.width(), rect.height())
        cc = rect.center()
        p.drawEllipse(QRectF(cc.x() - d / 2, cc.y() - d / 2, d, d))
        r = d / 2
        pts3 = [QPointF(cc.x() + r * math.cos(a), cc.y() + r * math.sin(a))
                for a in (math.radians(-90), math.radians(30), math.radians(150))]
        _dots_along(p, pts3, color=_ACCENT, r=1.5)
    elif kind == "fillet":
        # a square corner being rounded: two edges + the fillet arc
        from PySide6.QtGui import QPainterPath
        p.drawLine(QPointF(m, m), QPointF(m, size - m - 6))
        p.drawLine(QPointF(m + 6, size - m), QPointF(size - m, size - m))
        fp = QPainterPath(QPointF(m, size - m - 6))
        fp.quadTo(QPointF(m, size - m), QPointF(m + 6, size - m))
        ap = QPen(_ACCENT)
        ap.setWidthF(1.8)
        p.setPen(ap)
        p.drawPath(fp)
    elif kind == "pen":
        # an S-curve with its two bezier control handles + anchor dots
        a = QPointF(m, size - m)
        b = QPointF(size - m, m)
        c1 = QPointF(size - m, size - m)
        c2 = QPointF(m, m)
        from PySide6.QtGui import QPainterPath
        path = QPainterPath(a)
        path.cubicTo(c1, c2, b)
        p.drawPath(path)
        hp = QPen(_ACCENT)
        hp.setWidthF(0.9)
        p.setPen(hp)
        p.drawLine(a, c1)
        p.drawLine(b, c2)
        _dots_along(p, [a, b], color=_FG, r=1.4)
        _dots_along(p, [c1, c2], color=_ACCENT, r=1.2)
    else:
        p.drawRect(rect)
    p.end()
    return QIcon(pm)

"""The 3D view of a folded piece.

``foldsolid`` makes the model -- the leather as solid cells, at any moment
of the folding -- and ``bsp`` puts the cells in back-to-front order; this
module paints them with a plain ``QPainter`` (no OpenGL, so it works
anywhere the rest of the app does, and off screen for pictures), and wraps
that in a widget to turn, zoom and click on.

What is drawn: the leather with its true thickness and rounded bends, its
front (the side the pattern is drawn on) and back in different colours,
stitch holes and cutouts, every fold line coloured by the way it folds and
lettered, and the panels numbered.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPen,
                           QPolygonF)
from PySide6.QtWidgets import QToolTip, QWidget

from leathercad import bsp
from leathercad.bsp import BACK, DOTS, FRONT, INNER, LINE, POLY

_LEVELS = 24                      # shades of each colour


class Look:
    """The colours of the view, for a light or a dark window."""

    def __init__(self, dark: bool = False):
        self.dark = dark
        self.ground = QColor(30, 32, 36) if dark else QColor(245, 245, 247)
        self.text = QColor(232, 232, 236) if dark else QColor(40, 40, 46)
        self.faint = QColor(150, 152, 160) if dark else QColor(110, 112, 120)
        k = 0.9 if dark else 1.0
        self.front = (205 * k, 146 * k, 92 * k)      # grain side: tan
        self.back = (232 * k, 214 * k, 190 * k)      # flesh side: pale
        self.wall = (170 * k, 116 * k, 70 * k)       # the cut edge
        self.edge = QColor(70, 44, 24)
        self.hole = QColor(58, 36, 20)
        self.bad = QColor(226, 38, 38)
        self.cut = QColor(52, 54, 60) if dark else QColor(120, 122, 130)
        self.score = QColor(92, 60, 36)              # scored or engraved
        self.to_front = QColor(24, 104, 232)         # folds front to front
        self.to_back = QColor(206, 30, 122)          # folds back to back
        self.idle = QColor(128, 128, 136)            # a fold that isn't made
        self.pick = QColor(255, 196, 0)
        self._brushes = {}
        self._pens = {}

    def shade(self, kind: int, level: int) -> Tuple[QBrush, QPen]:
        """The fill for a face of this kind at this brightness, and a pen of
        the same colour (stroking with it hides the seams between the
        pieces a face is painted in)."""
        key = (kind, level)
        got = self._brushes.get(key)
        if got is None:
            base = self.front if kind == FRONT else self.back \
                if kind == BACK else self.wall
            s = 0.58 + 0.42 * level / (_LEVELS - 1)
            c = QColor(int(base[0] * s), int(base[1] * s), int(base[2] * s))
            pen = QPen(c, 1.0)
            pen.setCosmetic(True)
            got = self._brushes[key] = (QBrush(c), pen)
        return got

    def fold_colour(self, fold, live: bool) -> QColor:
        if not live:
            return self.idle
        return self.to_front if fold.direction >= 0 else self.to_back


class Camera:
    """Where the model is looked at from: turned ``yaw`` about the upright
    of the pattern and tipped back ``pitch`` from looking straight down on
    its front (both radians), about ``centre``; ``radius`` mm fill the
    view at ``zoom`` 1."""

    def __init__(self):
        self.yaw = -0.45
        self.pitch = 0.95
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.centre = (0.0, 0.0, 0.0)
        self.radius = 50.0

    def axes(self):
        """Unit vectors in the model: to the right of the screen, up it,
        and out of it towards the eye."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        return ((cy, sy, 0.0), (-cp * sy, cp * cy, sp),
                (sp * sy, -sp * cy, cp))

    def scale(self, w: float, h: float) -> float:
        return self.zoom * 0.46 * max(40.0, min(w, h)) / max(self.radius, 1e-6)

    def mapping(self, w: float, h: float):
        """``(sx, sy)`` of a model point is ``ox + ax*x + ay*y + az*z`` and
        ``oy + bx*x + by*y + bz*z``: returns ``(ox, ax, ay, az, oy, bx, by,
        bz)``."""
        r, u, _e = self.axes()
        s = self.scale(w, h)
        c = self.centre
        ax, ay, az = s * r[0], s * r[1], s * r[2]
        bx, by, bz = -s * u[0], -s * u[1], -s * u[2]
        ox = 0.5 * w + self.pan_x - (ax * c[0] + ay * c[1] + az * c[2])
        oy = 0.5 * h + self.pan_y - (bx * c[0] + by * c[1] + bz * c[2])
        return ox, ax, ay, az, oy, bx, by, bz


class Scene:
    """The model at one moment of the folding, ready to paint from any
    side: the partition tree of its cells, and where its fold lines and
    panel numbers go."""

    def __init__(self, layout, t: float, bad=()):
        self.layout = layout
        self.t = t
        self.fractions = layout.fractions(t)
        self.tree = bsp.build(layout.solid(self.fractions, bad))
        self.folds = layout.fold_marks(self.fractions)
        self.panels = layout.panel_marks(self.fractions)


def _discs(order, m):
    """Each cell's circle on the screen: ``(x, y, radius squared, cell)``."""
    ox, ax, ay, az, oy, bx, by, bz = m
    s2 = ax * ax + ay * ay + az * az              # (pixels per mm) squared
    return [(ox + ax * c.cx + ay * c.cy + az * c.cz,
             oy + bx * c.cx + by * c.cy + bz * c.cz, c.rad * c.rad * s2, c)
            for c in order]


def _hidden(pt, discs, eye, m, reach=1e-3) -> bool:
    """Whether something of the model lies between ``pt`` and the eye."""
    ox, ax, ay, az, oy, bx, by, bz = m
    ex, ey, ez = eye
    px, py, pz = pt
    sx = ox + ax * px + ay * py + az * pz
    sy = oy + bx * px + by * py + bz * pz
    for cx, cy, r2, cell in discs:
        if (cx - sx) ** 2 + (cy - sy) ** 2 > r2:
            continue
        for f in cell.faces:
            if f.kind == INNER:
                continue
            nx, ny, nz, d = f.plane
            facing = nx * ex + ny * ey + nz * ez
            if facing <= 1e-6:
                continue
            # how far towards the eye the face's plane is from the point
            if (d - nx * px - ny * py - nz * pz) / facing <= reach:
                continue
            x0, y0, z0 = f.pts[-1]
            u0 = ox + ax * x0 + ay * y0 + az * z0
            v0 = oy + bx * x0 + by * y0 + bz * z0
            inside = True
            sign = 0.0
            for x1, y1, z1 in f.pts:
                u1 = ox + ax * x1 + ay * y1 + az * z1
                v1 = oy + bx * x1 + by * y1 + bz * z1
                c = (u1 - u0) * (sy - v0) - (v1 - v0) * (sx - u0)
                if c != 0.0:
                    if sign == 0.0:
                        sign = c
                    elif (c > 0.0) != (sign > 0.0):
                        inside = False
                        break
                u0, v0 = u1, v1
            if inside:
                return True
    return False


def paint_scene(p: QPainter, w: int, h: int, scene: Scene, cam: Camera,
                look: Look, *, labels: bool = True, selected: int = -1,
                hover: int = -1):
    """Paint the model into a ``w`` x ``h`` area. Returns the fold lines as
    drawn -- ``[(fold, x0, y0, x1, y1)]`` -- for finding the one under the
    mouse."""
    p.fillRect(0, 0, w, h, look.ground)
    p.setRenderHint(QPainter.Antialiasing, True)
    layout = scene.layout
    m = cam.mapping(w, h)
    ox, ax, ay, az, oy, bx, by, bz = m
    scale = cam.scale(w, h)
    right, up, eye = cam.axes()
    ex, ey, ez = eye
    # light from up and to the left of the eye, so the faces towards you
    # are all lit and still differ
    lx = 0.80 * ex - 0.30 * right[0] + 0.45 * up[0]
    ly = 0.80 * ey - 0.30 * right[1] + 0.45 * up[1]
    lz = 0.80 * ez - 0.30 * right[2] + 0.45 * up[2]
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln

    edge = QPen(look.edge, 1.0)
    edge.setCosmetic(True)
    cut = QBrush(look.cut)
    marks = {"cut": QPen(look.cut, 1.8), "line": QPen(look.score, 1.1)}
    for pen in marks.values():
        pen.setCosmetic(True)
    top = _LEVELS - 1
    shade = look.shade
    order = bsp.back_to_front(scene.tree, ex, ey, ez)
    for cell in order:
        for f in cell.faces:
            kind = f.kind
            if kind == INNER:
                continue
            nx, ny, nz, _d = f.plane
            if nx * ex + ny * ey + nz * ez <= 1e-6:
                continue                       # turned away from the eye
            lum = nx * lx + ny * ly + nz * lz
            brush, pen = shade(kind, 0 if lum <= 0.0 else top if lum >= 1.0
                               else int(lum * top + 0.5))
            pts = [QPointF(ox + ax * x + ay * y + az * z,
                           oy + bx * x + by * y + bz * z) for x, y, z in f.pts]
            poly = QPolygonF(pts)
            p.setBrush(brush)
            edges = f.edges
            if edges is None or True not in edges:
                p.setPen(pen)
                p.drawPolygon(poly)
            elif False not in edges:
                p.setPen(edge)
                p.drawPolygon(poly)
            else:
                p.setPen(pen)
                p.drawPolygon(poly)
                p.setPen(edge)
                n = len(pts)
                for k in range(n):
                    if edges[k]:
                        p.drawLine(pts[k], pts[k + 1 if k + 1 < n else 0])
            if f.decals:
                for dk, geo, style in f.decals:
                    if dk == DOTS:
                        dia, wrong = style
                        dot = QPen(look.bad if wrong else look.hole,
                                   max(1.6, dia * scale) * (1.5 if wrong
                                                            else 1.0))
                        dot.setCapStyle(Qt.RoundCap)
                        p.setPen(dot)
                        p.drawPoints(QPolygonF([
                            QPointF(ox + ax * x + ay * y + az * z,
                                    oy + bx * x + by * y + bz * z)
                            for x, y, z in geo]))
                    elif dk == LINE:
                        (x, y, z), (x2, y2, z2) = geo
                        p.setPen(marks.get(style, edge))
                        p.drawLine(QPointF(ox + ax * x + ay * y + az * z,
                                           oy + bx * x + by * y + bz * z),
                                   QPointF(ox + ax * x2 + ay * y2 + az * z2,
                                           oy + bx * x2 + by * y2 + bz * z2))
                    elif dk == POLY:
                        p.setPen(Qt.NoPen)
                        p.setBrush(cut)
                        p.drawPolygon(QPolygonF([
                            QPointF(ox + ax * x + ay * y + az * z,
                                    oy + bx * x + by * y + bz * z)
                            for x, y, z in geo]))

    def spot(q):
        return (ox + ax * q[0] + ay * q[1] + az * q[2],
                oy + bx * q[0] + by * q[1] + bz * q[2])

    # The fold lines go on last, over the leather they lie on: drawn with
    # the faces, the next strip of the bend painted half of each away. A
    # line shows on a face turned towards the eye, wherever nothing stands
    # in front of it; the chosen fold is lit through whatever does.
    discs = _discs(order, m)
    drawn = []
    pieces = 8
    for fi, marks in scene.folds.items():
        fold = layout.folds[fi]
        pen = fold_pen(look, fold, bool(layout.steps[fi]))
        for a, b, a2, b2, nn in marks:
            facing = nn[0] * ex + nn[1] * ey + nn[2] * ez
            for q, r, side in ((a, b, facing), (a2, b2, -facing)):
                (x0, y0), (x1, y1) = spot(q), spot(r)
                drawn.append((fi, x0, y0, x1, y1))
                if fi == selected or fi == hover:
                    glow = QColor(look.pick)
                    glow.setAlpha(150 if fi == selected else 90)
                    halo = QPen(glow, 7.0 if fi == selected else 5.0)
                    halo.setCapStyle(Qt.RoundCap)
                    p.setPen(halo)
                    p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
                if side <= 0.02:
                    continue                   # that face is turned away
                start = None
                p.setPen(pen)
                for k in range(pieces + 1):
                    clear = False
                    if k < pieces:
                        t = (k + 0.5) / pieces
                        clear = not _hidden(
                            (q[0] + (r[0] - q[0]) * t,
                             q[1] + (r[1] - q[1]) * t,
                             q[2] + (r[2] - q[2]) * t), discs, eye, m)
                    if clear and start is None:
                        start = k
                    elif not clear and start is not None:
                        t0, t1 = start / pieces, k / pieces
                        p.drawLine(QPointF(x0 + (x1 - x0) * t0,
                                           y0 + (y1 - y0) * t0),
                                   QPointF(x0 + (x1 - x0) * t1,
                                           y0 + (y1 - y0) * t1))
                        start = None
    if labels:
        _paint_labels(p, scene, look, discs, eye, m, selected)
    return drawn


def fold_pen(look: Look, fold, live: bool) -> QPen:
    """The line a fold is drawn with: solid to the front, dashed to the
    back, dotted grey for one that isn't folded."""
    pen = QPen(look.fold_colour(fold, live), 2.2)
    pen.setCosmetic(True)
    if not live:
        pen.setStyle(Qt.DotLine)
    elif fold.direction < 0:
        pen.setDashPattern([4.0, 2.5])
    else:
        pen.setCapStyle(Qt.RoundCap)
    return pen


def _paint_labels(p, scene, look, discs, eye, m, selected):
    layout = scene.layout
    ox, ax, ay, az, oy, bx, by, bz = m
    ex, ey, ez = eye

    def spot(q):
        return QPointF(ox + ax * q[0] + ay * q[1] + az * q[2],
                       oy + bx * q[0] + by * q[1] + bz * q[2])

    font = QFont(p.font())
    font.setBold(True)
    font.setPointSizeF(10.0)
    p.setFont(font)
    # panel numbers, on whichever face is towards the eye and in clear view
    for panel, (front, back, nn) in zip(layout.panels, scene.panels):
        facing = nn[0] * ex + nn[1] * ey + nn[2] * ez
        if abs(facing) < 0.08:
            continue
        at = front if facing > 0.0 else back
        if _hidden(at, discs, eye, m):
            continue
        c = spot(at)
        box = QRectF(c.x() - 14, c.y() - 10, 28, 20)
        r, g, b = look.back if facing < 0.0 else look.front
        halo = QColor(int(r), int(g), int(b))
        p.setPen(QPen(halo.lighter(125), 3.0))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(box.translated(dx, dy), Qt.AlignCenter,
                       str(panel.number))
        p.setPen(look.edge)
        p.drawText(box, Qt.AlignCenter, str(panel.number))
    # fold letters: a disc in the fold's colour at the middle of the line
    for fi, marks in scene.folds.items():
        fold = layout.folds[fi]
        if not fold.label:
            continue
        a, b, a2, b2, nn = max(marks, key=lambda mk: (mk[0][0] - mk[1][0]) ** 2
                               + (mk[0][1] - mk[1][1]) ** 2
                               + (mk[0][2] - mk[1][2]) ** 2)
        facing = nn[0] * ex + nn[1] * ey + nn[2] * ez
        mids = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2),
                ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2,
                 (a2[2] + b2[2]) / 2)]
        if facing < 0.0:
            mids.reverse()
        at, ghost = None, False
        if abs(facing) >= 0.05 and not _hidden(mids[0], discs, eye, m):
            at = mids[0]
        elif fi == selected:
            at, ghost = mids[0], True          # out of sight: shown faintly
        if at is None:
            continue
        c = spot(at)
        colour = QColor(look.fold_colour(fold, bool(layout.steps[fi])))
        if ghost:
            colour.setAlpha(120)
        r = 9.5
        p.setPen(QPen(QColor(255, 255, 255, 230 if not ghost else 120), 1.4))
        p.setBrush(QBrush(colour))
        p.drawEllipse(c, r, r)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter,
                   fold.label)


def render(layout, t: float, w: int = 900, h: int = 620, *, dark: bool = False,
           yaw: Optional[float] = None, pitch: Optional[float] = None,
           zoom: float = 1.0, labels: bool = True, selected: int = -1,
           bad=()) -> QImage:
    """A picture of ``layout`` at time ``t`` of its folding (see
    ``Layout.fractions``)."""
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    cam = Camera()
    cam.centre, cam.radius = layout.extent()
    if yaw is not None:
        cam.yaw = yaw
    if pitch is not None:
        cam.pitch = pitch
    cam.zoom = zoom
    p = QPainter(img)
    paint_scene(p, w, h, Scene(layout, t, bad), cam, Look(dark),
                labels=labels, selected=selected)
    p.end()
    return img


class _Way:
    """A fold direction on its own, for the legend."""

    def __init__(self, direction):
        self.direction = direction


class FoldView(QWidget):
    """The model on screen. Drag to turn it, drag with the right or middle
    button (or Shift) to slide it, roll the wheel to zoom, double-click to
    fit it back in. Clicking a fold line picks that fold. The view keeps
    the model centred and filling it as it folds, until it is zoomed or
    slid by hand."""

    foldPicked = Signal(int)          # the fold clicked on, -1 for none

    def __init__(self, parent=None, dark: bool = False):
        super().__init__(parent)
        self.look = Look(dark)
        self.cam = Camera()
        self.layout_ = None
        self.scene: Optional[Scene] = None
        self.t = 0.0
        self.bad: frozenset = frozenset()
        self.selected = -1
        self.hover = -1
        self.labels = True
        self.follow = True            # keep the model filling the view
        self.caption = ""
        self._drawn: List[tuple] = []
        self._press = None
        self._moved = False
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- what is shown --------------------------------------------------------

    def set_layout(self, layout, t: Optional[float] = None, bad=(),
                   refit: bool = False):
        """Show ``layout`` (None for nothing). The view stays as it was
        turned unless ``refit``."""
        first = self.layout_ is None
        self.layout_ = layout
        self.bad = frozenset(bad)
        if t is not None:
            self.t = t
        if layout is None:
            self.scene = None
        else:
            self.t = min(max(self.t, 0.0), float(layout.n_steps))
            if first or refit:
                self.follow = True
            self.scene = Scene(layout, self.t, self.bad)
            self._frame()
        self.update()

    def set_time(self, t: float):
        if self.layout_ is None:
            return
        t = min(max(t, 0.0), float(self.layout_.n_steps))
        if self.scene is not None and abs(t - self.t) < 1e-9:
            return
        self.t = t
        self.scene = Scene(self.layout_, t, self.bad)
        self._frame()
        self.update()

    def _frame(self):
        """While the view follows the model, keep it centred and filling
        the view as it folds (it stops once you zoom or slide it yourself;
        Fit starts it again)."""
        if not self.follow or self.layout_ is None or self.scene is None:
            return
        lo, hi = self.layout_.bounds3(self.scene.fractions)
        self.cam.centre = tuple(0.5 * (a + b) for a, b in zip(lo, hi))
        self.cam.radius = max(5.0, 0.5 * math.dist(lo, hi))
        self.cam.pan_x = self.cam.pan_y = 0.0
        self.cam.zoom = 1.0

    def set_selected(self, fold: int):
        if fold != self.selected:
            self.selected = fold
            self.update()

    def look_from(self, where: str):
        """Turn to a set view: ``"front"``, ``"back"``, ``"side"`` or the
        three-quarter ``"iso"``."""
        cam = self.cam
        if where == "front":
            cam.yaw, cam.pitch = 0.0, 0.0
        elif where == "back":
            cam.yaw, cam.pitch = 0.0, math.pi
        elif where == "side":
            cam.yaw, cam.pitch = 0.0, 0.5 * math.pi
        else:
            cam.yaw, cam.pitch = -0.45, 0.95
        self.update()

    def fit(self):
        """Bring the model, as it stands, back to fill the view."""
        self.follow = True
        self._frame()
        self.update()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, _e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self.scene is None:
            p.fillRect(0, 0, w, h, self.look.ground)
            p.setPen(self.look.faint)
            p.drawText(self.rect().adjusted(40, 40, -40, -40),
                       Qt.AlignCenter | Qt.TextWordWrap,
                       self.caption or "Nothing to fold")
            p.end()
            return
        self._drawn = paint_scene(p, w, h, self.scene, self.cam, self.look,
                                  labels=self.labels, selected=self.selected,
                                  hover=self.hover)
        self._paint_legend(p, w, h)
        p.end()

    def _paint_legend(self, p: QPainter, w: int, h: int):
        look = self.look
        font = QFont(p.font())
        font.setBold(False)
        font.setPointSizeF(9.0)
        p.setFont(font)
        if self.caption:
            p.setPen(look.text)
            p.drawText(QRectF(12, 8, w - 24, 22),
                       Qt.AlignLeft | Qt.AlignVCenter, self.caption)
        x, y = 12.0, h - 16.0
        fm = p.fontMetrics()
        for kind, text in ((FRONT, "front of the leather"),
                           (BACK, "back")):
            brush, _pen = look.shade(kind, _LEVELS - 4)
            p.setPen(QPen(look.edge, 1.0))
            p.setBrush(brush)
            p.drawRect(QRectF(x, y - 9, 13, 10))
            p.setPen(look.faint)
            p.drawText(QPointF(x + 18, y), text)
            x += 18 + fm.horizontalAdvance(text) + 16
        for way, text in ((1, "folds to the front"),
                          (-1, "folds to the back")):
            p.setPen(fold_pen(look, _Way(way), True))
            p.drawLine(QPointF(x, y - 4), QPointF(x + 22, y - 4))
            p.setPen(look.faint)
            p.drawText(QPointF(x + 27, y), text)
            x += 27 + fm.horizontalAdvance(text) + 16

    # -- the mouse ------------------------------------------------------------

    def fold_at(self, x: float, y: float, reach: float = 8.0) -> int:
        """The fold whose line is nearest ``(x, y)``, within ``reach``
        pixels, or -1."""
        best, bd = -1, reach
        for fi, x0, y0, x1, y1 in self._drawn:
            dx, dy = x1 - x0, y1 - y0
            ll = dx * dx + dy * dy
            t = 0.0 if ll < 1e-9 else max(0.0, min(1.0, (
                (x - x0) * dx + (y - y0) * dy) / ll))
            d = math.hypot(x - x0 - t * dx, y - y0 - t * dy)
            if d < bd:
                best, bd = fi, d
        return best

    def mousePressEvent(self, e):
        self._press = (e.position().x(), e.position().y(), e.button(),
                       bool(e.modifiers() & Qt.ShiftModifier))
        self._moved = False

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._press is None:
            fold = self.fold_at(x, y)
            if fold != self.hover:
                self.hover = fold
                self.setCursor(Qt.PointingHandCursor if fold >= 0
                               else Qt.ArrowCursor)
                self.update()
            if fold >= 0 and self.layout_ is not None:
                f = self.layout_.folds[fold]
                step = self.layout_.steps[fold]
                tip = f"Fold {f.label}" + (f", {f.name}" if f.name else "") \
                    + (f": to the {'front' if f.direction >= 0 else 'back'}, "
                       f"{f.angle:g}°, step {step}" if step else ": left flat")
                QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            return
        px, py, button, shift = self._press
        dx, dy = x - px, y - py
        if not self._moved and abs(dx) + abs(dy) < 4:
            return
        self._moved = True
        self._press = (x, y, button, shift)
        if button == Qt.LeftButton and not shift:
            self.cam.yaw -= dx * 0.01
            self.cam.pitch = min(math.pi, max(0.0, self.cam.pitch - dy * 0.01))
        else:
            self.follow = False
            self.cam.pan_x += dx
            self.cam.pan_y += dy
        self.update()

    def mouseReleaseEvent(self, e):
        press, self._press = self._press, None
        if press is not None and not self._moved \
                and press[2] == Qt.LeftButton:
            self.foldPicked.emit(self.fold_at(e.position().x(),
                                              e.position().y()))

    def mouseDoubleClickEvent(self, _e):
        self.fit()

    def leaveEvent(self, _e):
        if self.hover != -1:
            self.hover = -1
            self.update()

    def wheelEvent(self, e):
        step = e.angleDelta().y() / 120.0
        if not step:
            return
        k = 1.15 ** step
        new = min(40.0, max(0.1, self.cam.zoom * k))
        k = new / self.cam.zoom
        # keep what is under the mouse where it is
        mx = e.position().x() - 0.5 * self.width()
        my = e.position().y() - 0.5 * self.height()
        self.cam.pan_x = mx - (mx - self.cam.pan_x) * k
        self.cam.pan_y = my - (my - self.cam.pan_y) * k
        self.cam.zoom = new
        self.follow = False
        self.update()

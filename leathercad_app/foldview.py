"""The 3D view of a folded piece.

``foldsolid`` makes the model -- the leather as solid cells, at any moment
of the folding -- and ``bsp`` puts the cells in back-to-front order; this
module paints them with a plain ``QPainter`` (no OpenGL, so it works
anywhere the rest of the app does, and off screen for pictures), and wraps
that in a widget to turn, zoom and click on.

What is drawn: the leather with its true thickness and rounded bends --
the grain of its front (the side the pattern is drawn on), the fibres of
its back, its cut edge, a sheen that moves as it turns, and a soft shadow
where one layer lies on another -- with its stitch holes and cutouts,
every fold line coloured by the way it folds and lettered, and the panels
numbered.

A model may be a stack of pieces (``leathercad.sheets``): then each piece
is painted in its own leather and can be clicked on, the chosen ones are
lit, and the layers can be lifted apart to show what lies on what.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter,
                           QPainterPath, QPen, QPolygonF, QRadialGradient,
                           QTransform)
from PySide6.QtWidgets import QToolTip, QWidget

from leathercad import bsp
from leathercad.bsp import BACK, DOTS, FRONT, INNER, LINE, POLY
from leathercad.foldsolid import LANDS, LONE, MEETS, OFF
from leathercad.sheets import BLANK, sheet_of

_LEVELS = 24                      # shades of each colour, dark to fully lit
_SHEEN = 8                        # ... and steps of sheen on top of that
_TILE = 256                       # a leather texture is this many pixels
_TILE_MM = 80.0                   # ... square, and covers this much leather
_CREASE = 0.94                    # cosine of the turn that shows as a crease
# How strongly (of 255) a mark is laid over a piece: so strongly that the
# colour seen is the mark's on any leather. Laid thinly, red over black
# leather is burgundy leather, and amber over dark brown is chestnut.
_WASH = 200
_THIN = 3.0                       # a layer under this many pixels is thin
_GRAIN = (23.0, 41.0)             # mm the grain shifts from piece to piece
_HINT = "Drag to turn · right-drag to slide · wheel to zoom"

#: Leathers to show the piece in: the colour of the grain side, of the
#: flesh side and of the cut edge.
LEATHERS = {
    "Natural": ((216, 172, 124), (232, 208, 174), (160, 114, 72)),
    "Tan": ((196, 132, 78), (224, 198, 162), (134, 86, 48)),
    "Chestnut": ((150, 82, 44), (208, 174, 136), (98, 54, 30)),
    "Dark brown": ((96, 62, 44), (184, 154, 124), (62, 40, 28)),
    "Black": ((54, 52, 52), (130, 122, 114), (34, 32, 32)),
    "Burgundy": ((122, 44, 52), (198, 160, 140), (80, 30, 36)),
    "Navy": ((48, 62, 98), (158, 154, 148), (32, 40, 64)),
    "Olive": ((104, 104, 62), (192, 182, 148), (68, 68, 42)),
}
DEFAULT_LEATHER = "Tan"

_maps = {}                        # the grey texture maps, made once


def _wrapped(x, y, r):
    """Where to draw something at ``(x, y)``, ``r`` across, so that the
    tile it is on joins up with itself at the edges."""
    for dx in (-_TILE, 0, _TILE):
        for dy in (-_TILE, 0, _TILE):
            if -r <= x + dx <= _TILE + r and -r <= y + dy <= _TILE + r:
                yield x + dx, y + dy


def _grain_map(cells: int = 64) -> QImage:
    """The grain of the leather as heights: pale pebbles with darker
    creases between them. Each pebble is a soft-edged disc and the picture
    keeps the lightest of them at every point, which leaves a crease
    wherever two pebbles meet."""
    rng = random.Random(7)
    img = QImage(_TILE, _TILE, QImage.Format_RGB32)
    img.fill(QColor(110, 110, 110))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setCompositionMode(QPainter.CompositionMode_Lighten)
    p.setPen(Qt.NoPen)
    s = _TILE / cells
    r = 1.5 * s
    for i in range(cells):
        for j in range(cells):
            x = (i + 0.5 + rng.uniform(-0.5, 0.5)) * s
            y = (j + 0.5 + rng.uniform(-0.5, 0.5)) * s
            top = rng.randint(228, 255)
            g = QRadialGradient(QPointF(0.0, 0.0), r)
            g.setColorAt(0.00, QColor(top, top, top))
            g.setColorAt(0.22, QColor(top - 6, top - 6, top - 6))
            g.setColorAt(0.36, QColor(200, 200, 200))
            g.setColorAt(0.50, QColor(150, 150, 150))
            g.setColorAt(1.00, QColor(110, 110, 110))
            brush = QBrush(g)
            for wx, wy in _wrapped(x, y, r):
                p.save()
                p.translate(wx, wy)
                p.setBrush(brush)
                p.drawEllipse(QPointF(0.0, 0.0), r, r)
                p.restore()
    p.end()
    return img


def _relief_map(heights: QImage) -> QImage:
    """Heights as light and shade, lit from up and to the left (mid grey
    is flat): the heights, with their own negative laid over them a pixel
    down and across."""
    negative = QImage(heights)
    negative.invertPixels()
    out = QImage(heights)
    p = QPainter(out)
    p.setOpacity(0.5)
    for dx in (-_TILE, 0, _TILE):
        for dy in (-_TILE, 0, _TILE):
            p.drawImage(dx + 1, dy + 1, negative)
    p.end()
    return out


def _mottle_map(seed: int) -> QImage:
    """Faint lighter and darker patches on a clear ground: no hide is one
    even colour. Kept faint, and many, so the tile doesn't show as a
    pattern when it repeats."""
    rng = random.Random(seed)
    img = QImage(_TILE, _TILE, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    for k in range(220):
        x, y = rng.uniform(0, _TILE), rng.uniform(0, _TILE)
        r = rng.uniform(6, 26)
        v = 0 if k % 2 else 255
        a = rng.randint(3, 8)
        for wx, wy in _wrapped(x, y, r):
            g = QRadialGradient(QPointF(wx, wy), r)
            g.setColorAt(0.0, QColor(v, v, v, a))
            g.setColorAt(1.0, QColor(v, v, v, 0))
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(wx, wy), r, r)
    p.end()
    return img


def _fibre_map() -> QImage:
    """The flesh side: short fibres lying every way, on a clear ground."""
    rng = random.Random(5)
    img = QImage(_TILE, _TILE, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    for _k in range(6500):
        x, y = rng.uniform(0, _TILE), rng.uniform(0, _TILE)
        a, ln = rng.uniform(0.0, math.pi), rng.uniform(1.5, 6.0)
        v = 255 if rng.random() < 0.5 else 30
        pen = QPen(QColor(v, v, v, rng.randint(10, 26)), 1.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        dx, dy = math.cos(a) * ln, math.sin(a) * ln
        for wx, wy in _wrapped(x, y, ln):
            p.drawLine(QPointF(wx, wy), QPointF(wx + dx, wy + dy))
    p.end()
    return img


def texture_maps() -> dict:
    """The grey maps every leather's textures are made from: the grain's
    creases and its relief, the flesh side's fibres, and faint patches for
    each side. Made the first time they're wanted (a fifth of a second)."""
    if not _maps:
        grain = _grain_map()
        _maps.update(grain=grain, relief=_relief_map(grain),
                     fibres=_fibre_map(), front=_mottle_map(3),
                     back=_mottle_map(11))
    return _maps


def _lum(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


class Look:
    """How the view looks: the leather it shows (see ``LEATHERS``), and
    the colours round it for a light or a dark window."""

    def __init__(self, dark: bool = False, leather: str = DEFAULT_LEATHER):
        self.dark = dark
        self.leather = leather if leather in LEATHERS else DEFAULT_LEATHER
        self.ground = QColor(30, 32, 36) if dark else QColor(245, 245, 247)
        self.text = QColor(232, 232, 236) if dark else QColor(40, 40, 46)
        self.faint = QColor(150, 152, 160) if dark else QColor(110, 112, 120)
        self.front, self.back, self.wall = LEATHERS[self.leather]
        # the line along an edge of the leather: darker than the cut edge
        # -- but lighter for a dark leather in a dark window, where a darker
        # line would lose the piece against the ground
        w = self.wall
        self.pale_edge = dark and _lum(w) <= 70
        self.edge = QColor(*(min(255, int(c + 58)) for c in w)) \
            if self.pale_edge else QColor(*(int(c * 0.5) for c in w))
        self.bad = QColor(214, 40, 40)
        # a stitch hole by what the check of the folded piece found for it
        self.hole = {MEETS: QColor(40, 190, 96), OFF: self.bad,
                     LONE: QColor(255, 176, 0)}
        self.cut = QColor(52, 54, 60) if dark else QColor(120, 122, 130)
        self.idle = QColor(128, 128, 136)            # a fold that isn't made
        self.pick = QColor(255, 196, 0)
        # washed over leather that is in the way of a fold: beside
        # ``bad``, for the leather that has run into other leather
        self.warn = QColor(240, 120, 0)
        self._brushes = {}

    def flat(self, kind: int) -> Tuple[int, int, int]:
        """The plain colour of one side of the leather, or of its edge."""
        return self.front if kind == FRONT else self.back if kind == BACK \
            else self.wall

    def is_dark(self, kind: int) -> bool:
        """Whether that side is dark enough to need light marks on it."""
        return _lum(self.flat(kind)) < 105

    def ink(self, kind: int) -> QColor:
        """The colour for marks on one side: stitch holes, scored lines,
        the panel's number."""
        if self.is_dark(kind):
            return QColor(226, 216, 200)
        r, g, b = self.flat(kind)
        return QColor(int(r * 0.28), int(g * 0.26), int(b * 0.24))

    def texture(self, kind: int, level: int) -> QImage:
        """One side of the leather at one brightness, as a tile."""
        r, g, b = self.flat(kind)
        s = 0.58 + 0.42 * min(level, _LEVELS - 1) / (_LEVELS - 1)
        maps = texture_maps()
        img = QImage(_TILE, _TILE, QImage.Format_RGB32)
        img.fill(QColor(int(r * s), int(g * s), int(b * s)))
        p = QPainter(img)
        if kind == FRONT:
            p.drawImage(0, 0, maps["front"])
            p.setCompositionMode(QPainter.CompositionMode_Multiply)
            p.setOpacity(0.30)
            p.drawImage(0, 0, maps["grain"])
            p.setCompositionMode(QPainter.CompositionMode_Overlay)
            p.setOpacity(0.55)
            p.drawImage(0, 0, maps["relief"])
            if level >= _LEVELS:               # the light catching it
                sheen = 0.20 * (level - _LEVELS + 1) / _SHEEN
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                p.setOpacity(1.0)
                p.fillRect(0, 0, _TILE, _TILE,
                           QColor(255, 244, 228, int(255 * sheen)))
        else:
            p.drawImage(0, 0, maps["back"])
            p.drawImage(0, 0, maps["fibres"])
        p.end()
        return img

    def shade(self, kind: int, level: int) -> Tuple[QBrush, QPen]:
        """What a face of this kind is painted with at this brightness:
        the leather's texture for its front and back (the caller lays it
        on the face), a plain colour for the cut edge -- and a hairline pen
        of the colour that comes to, for the rim of the face and the seams
        between the pieces it is painted in."""
        key = (kind, level)
        got = self._brushes.get(key)
        if got is None:
            if kind == FRONT or kind == BACK:
                tile = self.texture(kind, level)
                c = tile.scaled(1, 1, Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation).pixelColor(0, 0)
                brush = QBrush(tile)
            else:
                r, g, b = self.wall
                s = 0.58 + 0.42 * min(level, _LEVELS - 1) / (_LEVELS - 1)
                c = QColor(int(r * s), int(g * s), int(b * s))
                brush = QBrush(c)
            pen = QPen(c, 1.0)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.FlatCap)
            got = self._brushes[key] = (brush, pen)
        return got

    def fold_colour(self, fold, live: bool, kind: int = FRONT) -> QColor:
        """The colour of a fold line on one side of the leather: blue for
        a fold to the front, magenta for one to the back, lighter on a
        dark leather; grey for a fold that isn't made."""
        if not live:
            return self.idle
        if self.is_dark(kind):
            return QColor(110, 176, 255) if fold.direction >= 0 \
                else QColor(255, 122, 190)
        return QColor(24, 104, 232) if fold.direction >= 0 \
            else QColor(206, 30, 122)


_PLAIN = Look()                   # the fold colours for a light leather


def fold_colour(fold, live: bool = True) -> QColor:
    """The colour that stands for a fold in lists and keys: blue to the
    front, magenta to the back, grey for one that isn't made."""
    return QColor(_PLAIN.fold_colour(fold, live))


def hole_colour(state: int) -> QColor:
    """The colour of a stitch hole that ``MEETS`` the holes it is stacked
    with, is ``OFF`` them, or is ``LONE``: green, red, amber."""
    return QColor(_PLAIN.hole[state])


class Camera:
    """Where the model is looked at from: turned ``yaw`` about the upright
    of the pattern and tipped back ``pitch`` from looking straight down on
    its front (both radians), about ``centre``; ``radius`` mm fill the
    view at ``zoom`` 1.

    ``turned`` is for a stack whose main piece lies grain down on the
    bench: the model is turned half round the upright of the pattern
    before it is looked at -- turned over like a page -- so that looking
    straight down shows its back, with left and right changed over.
    Every view, the mouse and the light then work as they did, on the
    stack as it lies on the bench."""

    def __init__(self):
        self.yaw = -0.45
        self.pitch = 0.95
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.centre = (0.0, 0.0, 0.0)
        self.radius = 50.0
        self.turned = False

    def axes(self):
        """Unit vectors in the model: to the right of the screen, up it,
        and out of it towards the eye."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        if self.turned:                # the same, seen from the model
            return ((-cy, sy, 0.0), (cp * sy, cp * cy, -sp),
                    (-sp * sy, -sp * cy, -cp))
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
    panel numbers go.

    A layout with ``sheets`` is a stack of pieces. Its ``solid``,
    ``fold_marks``, ``panel_marks`` and ``bounds3`` take ``apart`` as
    well: how far (mm) to lift each layer off the one under it.

    A layout may say with ``grain`` which side of its leather its own
    piece -- the main piece of a stack -- is drawn from: -1 for the flesh
    side. The front of the model is then that piece's flesh side, and its
    faces are painted so."""

    def __init__(self, layout, t: float, check=None, apart: float = 0.0):
        self.layout = layout
        self.t = t
        self.fractions = layout.fractions(t)
        self.sheets = getattr(layout, "sheets", None) or ()
        self.apart = max(0.0, float(apart)) if self.sheets else 0.0
        self.grain = 1 if getattr(layout, "grain", 1) >= 0 else -1
        if self.sheets:
            cells = layout.solid(self.fractions, check, self.apart)
            self.folds = layout.fold_marks(self.fractions, self.apart)
            self.panels = layout.panel_marks(self.fractions, self.apart)
        else:
            cells = layout.solid(self.fractions, check)
            self.folds = layout.fold_marks(self.fractions)
            self.panels = layout.panel_marks(self.fractions)
        if self.grain < 0:
            # the model is made with the grain on the front of the main
            # piece: here it is on its back
            for cell in cells:
                if sheet_of(cell.tag):
                    continue
                for f in cell.faces:
                    if f.kind == FRONT:
                        f.kind = BACK
                    elif f.kind == BACK:
                        f.kind = FRONT
        self.tree = bsp.build(cells)

    def bounds(self):
        """The box round the model as it stands: ``(low corner, high)``."""
        if self.sheets:
            return self.layout.bounds3(self.fractions, self.apart)
        return self.layout.bounds3(self.fractions)

    def face_of(self, piece: int, front: bool) -> int:
        """Which side of its leather a piece shows on the face it turns
        the way the main piece's front is turned (or, not ``front``, its
        back): ``FRONT`` for the grain side, ``BACK`` for the flesh."""
        grain = self.sheets[piece - 1].grain \
            if 0 < piece <= len(self.sheets) \
            else self.grain if piece == 0 else 1
        return FRONT if (grain > 0) == front else BACK

    def thickness_of(self, piece: int) -> float:
        """How thick a piece of the model is."""
        if 0 < piece <= len(self.sheets):
            return self.sheets[piece - 1].thickness
        return self.layout.thickness


def _hidden(pt, discs, eye, m, reach=1e-3) -> bool:
    """Whether something of the model lies between ``pt`` and the eye.
    ``discs`` is what could be in the way, nearest the eye first: for each
    cell its circle on the screen, how near the eye it comes, and its faces
    turned towards the eye -- ``(x, y, radius squared, nearness, [(plane,
    facing, corners on the screen)])``."""
    ox, ax, ay, az, oy, bx, by, bz = m
    px, py, pz = pt
    sx = ox + ax * px + ay * py + az * pz
    sy = oy + bx * px + by * py + bz * pz
    near = eye[0] * px + eye[1] * py + eye[2] * pz + reach
    for cx, cy, r2, front, faces in discs:
        if front <= near or (cx - sx) ** 2 + (cy - sy) ** 2 > r2:
            continue                           # behind the point, or beside it
        for (nx, ny, nz, d), facing, flat in faces:
            # how far towards the eye the face's plane is from the point
            if (d - nx * px - ny * py - nz * pz) / facing <= reach:
                continue
            u0, v0 = flat[-1]
            inside = True
            sign = 0.0
            for u1, v1 in flat:
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


def _in_sight(q, r, pieces: int, fine: bool, discs, eye, m):
    """The stretches of the line from ``q`` to ``r`` that nothing of the
    model stands in front of, as ``(from, to)`` along it from 0 to 1. The
    line is tried at the middle of each of ``pieces`` equal lengths of
    it, and a length goes with its middle. If ``fine``, the place where
    it passes out of sight is looked for between the two middles it lies
    between, to an eighth of the way from one to the next."""
    def clear(t):
        return not _hidden((q[0] + (r[0] - q[0]) * t,
                            q[1] + (r[1] - q[1]) * t,
                            q[2] + (r[2] - q[2]) * t), discs, eye, m)

    out = []
    start = 0.0
    was, before = False, 0.0
    for i in range(pieces + 1):
        t = (i + 0.5) / pieces
        now = i < pieces and clear(t)
        if now != was:
            edge = i / pieces
            if fine:
                if i == 0 or i == pieces:      # an end of the line itself
                    edge = float(i == pieces)
                else:
                    lo, hi = before, t
                    for _round in range(3):
                        mid = 0.5 * (lo + hi)
                        if clear(mid) == was:
                            lo = mid
                        else:
                            hi = mid
                    edge = 0.5 * (lo + hi)
            if now:
                start = edge
            else:
                out.append((start, edge))
        was, before = now, t
    return out


def paint_scene(p: QPainter, w: int, h: int, scene: Scene, cam: Camera,
                look: Look, *, labels: bool = True, selected: int = -1,
                hover: int = -1, looks=None, chosen=(), wash=None,
                seen: Optional[list] = None):
    """Paint the model into a ``w`` x ``h`` area. Returns the fold lines as
    drawn -- ``[(fold, x0, y0, x1, y1)]`` -- for finding the one under the
    mouse.

    The faces are filled without smoothing: that is many times quicker for
    the narrow strips a bend is made of, and it leaves no gaps between the
    pieces a face is painted in. What smoothing is for -- the rim of the
    model against what lies behind it -- is done with a smooth hairline
    along each edge that can be one: dark along an edge of the leather, the
    face's own colour where it turns out of sight round a bend.

    The pieces of a stack are told apart by the tags of the cells (see
    ``sheets.sheet_of``). ``looks`` has the ``Look`` of each piece that is
    in a leather of its own, ``{piece: Look}``: only its leather is taken
    from it, the colours of the window are all ``look``'s.

    The pieces in ``chosen`` are lit: a line of ``look.pick`` along every
    edge of the piece, strong where the edge is in sight and faint
    through whatever lies over it. The leather itself is left as it is,
    so a chosen piece still shows what it is made of. ``wash`` marks
    pieces with a colour of the caller's: ``{(piece, None): colour}`` for
    a whole piece, ``{(piece, fold): colour}`` for what it has in the
    bend of one fold. The colour is laid over the leather ``_WASH`` of
    255 strong -- so strongly that it is the mark that is seen, whatever
    the leather -- or, if the colour has an alpha of its own below 255,
    as strongly as that says. A piece that is both chosen and marked has
    the mark's colour over it and the pick colour round it.

    ``seen``, if given, is filled with the stretches of the fold lines
    that are in sight, each as a line returned is."""
    looks = looks or {}
    p.fillRect(0, 0, w, h, look.ground)
    smooth = QPainter.Antialiasing
    p.setRenderHint(smooth, False)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    layout = scene.layout
    m = cam.mapping(w, h)
    ox, ax, ay, az, oy, bx, by, bz = m
    scale = cam.scale(w, h)
    right, up, eye = cam.axes()
    ex, ey, ez = eye
    # light from up and to the left of the eye, so the faces towards you
    # are all lit and still differ
    lx = 0.72 * ex - 0.36 * right[0] + 0.58 * up[0]
    ly = 0.72 * ey - 0.36 * right[1] + 0.58 * up[1]
    lz = 0.72 * ez - 0.36 * right[2] + 0.58 * up[2]
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln
    # ... and half way between it and the eye, where the grain catches it
    hx, hy, hz = lx + ex, ly + ey, lz + ez
    ln = math.sqrt(hx * hx + hy * hy + hz * hz)
    hx, hy, hz = hx / ln, hy / ln, hz / ln

    top = _LEVELS - 1
    order = bsp.back_to_front(scene.tree, ex, ey, ez)

    # What there is to paint, cell by cell from the back: the edges of the
    # leather that throw a shadow, and the faces turned towards the eye --
    # each with its brightness, its corners on the screen, the edges of the
    # leather round it (``rims``) and the edges to soften (``soft``). The
    # seams, where the same surface carries on in the next face, are kept
    # apart by colour.
    cells = []
    seams = {}
    discs = []                    # what can hide a point: see ``_hidden``
    s2 = ax * ax + ay * ay + az * az              # (pixels per mm) squared
    pile = bool(scene.sheets)
    for cell in order:
        cast, faces, solid, faint = [], [], [], []
        piece = sheet_of(cell.tag)
        leather = looks.get(piece, look)       # the piece's own, or the usual
        # In a pile of thin layers of a dark leather, in a dark window,
        # the pale line along each edge of each layer would be most of
        # what shows of the pile's cut edge. So the lines along the cut
        # edge of a layer only a few pixels thick are drawn faint: its
        # own, and the line where a face of the layer meets it
        thin = pile and leather.pale_edge \
            and scene.thickness_of(piece) * scale < _THIN
        for f in cell.faces:
            kind = f.kind
            if kind == INNER:
                continue
            nx, ny, nz, _d = f.plane
            edges = f.edges
            facing = nx * ex + ny * ey + nz * ez
            if kind <= BACK:
                # the cut edge beyond an edge of this face is turned towards
                # the eye where this, along that edge, comes out more than 0
                c0 = ny * ez - nz * ey
                c1 = nz * ex - nx * ez
                c2 = nx * ey - ny * ex
            if facing <= 1e-6:
                # turned away from the eye, so not painted: but where the
                # cut edge beside it shows, its rim is the foot of that
                # edge, and there the shadow of the leather falls from
                if kind <= BACK and edges is not None and True in edges:
                    at = f.pts
                    n = len(at)
                    for i in range(n):
                        if edges[i] is not True:
                            continue
                        x, y, z = at[i]
                        x2, y2, z2 = at[i + 1 if i + 1 < n else 0]
                        if (x2 - x) * c0 + (y2 - y) * c1 + (z2 - z) * c2 > 0.0:
                            cast.append(QPointF(ox + ax * x + ay * y + az * z,
                                                oy + bx * x + by * y + bz * z))
                            cast.append(QPointF(
                                ox + ax * x2 + ay * y2 + az * z2,
                                oy + bx * x2 + by * y2 + bz * z2))
                continue
            lum = nx * lx + ny * ly + nz * lz
            level = 0 if lum <= 0.0 else top if lum >= 1.0 \
                else int(lum * top + 0.5)
            if kind == FRONT:
                gloss = nx * hx + ny * hy + nz * hz
                if gloss > 0.9:                # the grain side has a sheen
                    step = int((gloss ** 24) * _SHEEN + 0.5)
                    if step > 0:
                        level = top + min(step, _SHEEN)
            flat = [(ox + ax * x + ay * y + az * z,
                     oy + bx * x + by * y + bz * z) for x, y, z in f.pts]
            solid.append((f.plane, facing, flat))
            pts = [QPointF(x, y) for x, y in flat]
            n = len(pts)
            rims, soft = [], []
            if kind <= BACK:
                seam = None
                at = f.pts
                for i in range(n):
                    e = edges[i] if edges is not None else False
                    if e is True:
                        j = i + 1 if i + 1 < n else 0
                        # with the cut edge out of sight, this is the rim
                        # of the layer: its shadow falls from here
                        x, y, z = at[i]
                        x2, y2, z2 = at[j]
                        if (x2 - x) * c0 + (y2 - y) * c1 \
                                + (z2 - z) * c2 <= 0.0:
                            cast.append(pts[i])
                            cast.append(pts[j])
                        elif thin:
                            faint.append(pts[i])
                            faint.append(pts[j])
                            continue
                        rims.append(pts[i])
                        rims.append(pts[j])
                    elif e is not False and (
                            e[0] * ex + e[1] * ey + e[2] * ez <= 1e-6
                            or e[0] * nx + e[1] * ny + e[2] * nz < _CREASE):
                        # the leather turns out of sight here, or sharply
                        soft.append(pts[i])
                        soft.append(pts[i + 1 if i + 1 < n else 0])
                    else:
                        if seam is None:
                            key = (leather, kind, level)
                            seam = seams.get(key)
                            if seam is None:
                                seam = seams[key] = QPainterPath()
                        seam.moveTo(pts[i])
                        seam.lineTo(pts[i + 1 if i + 1 < n else 0])
            else:
                along = faint if thin else rims
                for i in range(n):
                    (along if edges is not None and edges[i] is True
                     else soft).extend((pts[i],
                                        pts[i + 1 if i + 1 < n else 0]))
            faces.append((f, level, pts, rims, soft))
        if faces or cast:
            cells.append((cast, faces, piece, leather, cell.tag, faint))
        if solid:
            x, y, z = cell.cx, cell.cy, cell.cz
            discs.append((ox + ax * x + ay * y + az * z,
                          oy + bx * x + by * y + bz * z,
                          cell.rad * cell.rad * s2,
                          ex * x + ey * y + ez * z + cell.rad, solid))
    discs.reverse()                            # nearest the eye first

    # Under everything, a line of the leather's own colour along every
    # seam: where a face was cut in two and the one beside it was not, the
    # odd pixel between them can go unfilled, and would show the background
    p.setBrush(Qt.NoBrush)
    for (leather, kind, level), seam in seams.items():
        p.setPen(leather.shade(kind, level)[1])
        p.drawPath(seam)

    cut = QBrush(look.cut)
    rims_of = {}                  # the line along an edge, in each leather
    inks_of = {}                  # ... and the colour of the marks on it
    bands = {}                    # the shadow the edge of each piece throws
    marks = {}                    # how the cells of a piece are marked
    lit = frozenset(chosen)
    wash = wash or {}
    ghost = []                    # the edges of the chosen pieces

    def shadows(piece):
        # the soft shadow an edge of the leather throws on what lies under
        # it: three bands, each narrower and so darker towards the edge
        got = bands.get(piece)
        if got is None:
            reach = min(11.0, max(3.0, 1.6 * scene.thickness_of(piece)
                                  * scale))
            got = bands[piece] = []
            for part in (1.0, 0.62, 0.3):
                band = QPen(QColor(0, 0, 0, 9), reach * part)
                band.setCapStyle(Qt.FlatCap)
                got.append(band)
        return got

    def marked(piece, tag):
        # How the cells of a piece are marked: the colours laid over its
        # leather (the one for all of it, then the one for its part in
        # this bend), and for a piece that is chosen the line along its
        # edges
        fold = tag[1] if tag is not None and tag[0] == "bend" else None
        got = marks.get((piece, fold))
        if got is None:
            over = []
            for colour in (wash.get((piece, None)),
                           wash.get((piece, fold)) if fold is not None
                           else None):
                if colour is not None:
                    colour = QColor(colour)
                    if colour.alpha() == 255:
                        colour.setAlpha(_WASH)
                    over.append(colour)
            line = None
            if piece in lit:
                line = QPen(look.pick, 2.6)
                line.setCosmetic(True)
                line.setCapStyle(Qt.RoundCap)
            got = marks[(piece, fold)] = (over, [QBrush(c) for c in over],
                                          line)
        return got

    def under(pen, over):
        # a hairline of the leather's own colour, as it shows through the
        # colours laid over that leather
        c = pen.color()
        r, g, b = c.red(), c.green(), c.blue()
        for top in over:
            a = top.alpha() / 255.0
            r += (top.red() - r) * a
            g += (top.green() - g) * a
            b += (top.blue() - b) * a
        pen = QPen(pen)
        pen.setColor(QColor(int(r + 0.5), int(g + 0.5), int(b + 0.5)))
        return pen

    k = _TILE_MM / _TILE                       # mm to a pixel of texture
    none = QPen(Qt.NoPen)
    plain = ((), (), None)
    for cast, faces, piece, leather, tag, faint in cells:
        shade = leather.shade
        over, fills, line = marked(piece, tag) if lit or wash else plain
        if cast:
            for band in shadows(piece):
                p.setPen(band)
                p.drawLines(cast)
        # the faces of one cell never hide each other, so each kind of
        # thing is done for all of them together. First the soft rim of a
        # face of the leather, under the face: only the half outside shows
        on = False
        for f, level, pts, rims, soft in faces:
            if soft and f.kind <= BACK:
                if not on:
                    p.setRenderHint(smooth, True)
                    on = True
                pen = shade(f.kind, level)[1]
                p.setPen(under(pen, over) if over else pen)
                p.drawLines(soft)
        if on:
            p.setRenderHint(smooth, False)
        # ... then the faces themselves
        p.setPen(none)
        lines = []
        more = False
        for f, level, pts, rims, soft in faces:
            brush = shade(f.kind, level)[0]
            if f.kind <= BACK:
                uv = f.uv
                if uv is not None:
                    # lay the leather's texture on the face where the
                    # pattern puts it, so the grain runs on from panel to
                    # bend to panel
                    o, u, v = uv
                    if piece:
                        # ... but not from one piece of a stack to the
                        # next: each is cut from its own bit of the hide
                        gx, gy = _GRAIN[0] * piece, _GRAIN[1] * piece
                        o = (o[0] + gx * u[0] + gy * v[0],
                             o[1] + gx * u[1] + gy * v[1],
                             o[2] + gx * u[2] + gy * v[2])
                    brush.setTransform(QTransform(
                        k * (ax * u[0] + ay * u[1] + az * u[2]),
                        k * (bx * u[0] + by * u[1] + bz * u[2]),
                        k * (ax * v[0] + ay * v[1] + az * v[2]),
                        k * (bx * v[0] + by * v[1] + bz * v[2]),
                        ox + ax * o[0] + ay * o[1] + az * o[2],
                        oy + bx * o[0] + by * o[1] + bz * o[2]))
                if f.decals:
                    more = True
            elif soft:
                more = True
            p.setBrush(brush)
            poly = QPolygonF(pts)
            p.drawPolygon(poly)
            for fill in fills:
                p.setBrush(fill)
                p.drawPolygon(poly)
            if rims:
                lines += rims
        if line is not None and faint:
            lines += faint                     # (a lit piece: drawn in full)
            faint = ()
        if not (lines or more or faint):
            continue
        # ... and over them what is drawn smooth: the marks on the leather,
        # the corners of its cut edge, and the line along each of its edges
        p.setRenderHint(smooth, True)
        if more:
            inks = inks_of.get(leather)
            if inks is None:
                inks = inks_of[leather] = {FRONT: leather.ink(FRONT),
                                           BACK: leather.ink(BACK)}
            for f, level, pts, rims, soft in faces:
                kind = f.kind
                if kind > BACK:
                    if soft:
                        pen = shade(kind, level)[1]
                        p.setPen(under(pen, over) if over else pen)
                        p.drawLines(soft)
                    continue
                if not f.decals:
                    continue
                for dk, geo, style in f.decals:
                    if dk == DOTS:
                        dia, state = style
                        _paint_holes(p, [
                            QPointF(ox + ax * x + ay * y + az * z,
                                    oy + bx * x + by * y + bz * z)
                            for x, y, z in geo], state,
                            max(1.6, dia * scale), inks[kind], look)
                    elif dk == LINE:
                        (x, y, z), (x2, y2, z2) = geo
                        mark = QPen(inks[kind], 1.8 if style == "cut"
                                    else 1.1)
                        mark.setCosmetic(True)
                        p.setPen(mark)
                        p.drawLine(QPointF(ox + ax * x + ay * y + az * z,
                                           oy + bx * x + by * y + bz * z),
                                   QPointF(ox + ax * x2 + ay * y2 + az * z2,
                                           oy + bx * x2 + by * y2 + bz * z2))
                    elif dk == POLY:
                        p.setPen(none)
                        p.setBrush(cut)
                        p.drawPolygon(QPolygonF([
                            QPointF(ox + ax * x + ay * y + az * z,
                                    oy + bx * x + by * y + bz * z)
                            for x, y, z in geo]))
        if lines or faint:
            edge = rims_of.get(leather)
            if edge is None:
                pen = QPen(leather.edge, 1.0)
                pen.setCosmetic(True)
                dim = QColor(leather.edge)
                dim.setAlpha(70)
                edge = rims_of[leather] = (pen, QPen(pen))
                edge[1].setColor(dim)
            if faint:
                p.setPen(edge[1])
                p.drawLines(faint)
            if lines:
                # a piece that is chosen has its edges in the pick
                # colour, wider than the leather's own line
                p.setPen(edge[0] if line is None else line)
                p.drawLines(lines)
                if piece in lit:
                    ghost += lines
        p.setRenderHint(smooth, False)
    p.setRenderHint(smooth, True)
    if ghost:
        # ... and the edges of a chosen piece once more, faintly, through
        # whatever lies over it: a piece that is covered still shows
        # where it is
        through = QColor(look.pick)
        through.setAlpha(110)
        pen = QPen(through, 1.4)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLines(ghost)

    def spot(q):
        return (ox + ax * q[0] + ay * q[1] + az * q[2],
                oy + bx * q[0] + by * q[1] + bz * q[2])

    # The fold lines go on last, over the leather they lie on: drawn with
    # the faces, the next strip of the bend painted half of each away. A
    # line shows on a face turned towards the eye, wherever nothing stands
    # in front of it; the chosen fold is lit through whatever does. (On a
    # stack a mark says, last, which piece each of its two lines is on --
    # and there a line is cut off behind a piece to the pixel, not to the
    # nearest eighth of its length: what is under the mouse beside it is
    # either the line or the piece, and a click has to know which.)
    drawn = []
    for fi, marks in scene.folds.items():
        fold = layout.folds[fi]
        live = bool(layout.steps[fi])
        for mark in marks:
            a, b, a2, b2, nn = mark[:5]
            on = mark[5] if len(mark) > 5 else (0, 0)
            facing = nn[0] * ex + nn[1] * ey + nn[2] * ez
            for q, r, side, front in ((a, b, facing, True),
                                      (a2, b2, -facing, False)):
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
                piece = on[0] if front else on[1]
                p.setPen(fold_pen(looks.get(piece, look), fold, live,
                                  scene.face_of(piece, front)))
                pieces = 8
                if pile:                       # one for every few pixels
                    pieces = max(8, min(96, 1 + int(math.hypot(
                        x1 - x0, y1 - y0) / 6.0)))
                for t0, t1 in _in_sight(q, r, pieces, pile, discs, eye, m):
                    run = (x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0,
                           x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)
                    p.drawLine(QPointF(run[0], run[1]),
                               QPointF(run[2], run[3]))
                    if seen is not None:
                        seen.append((fi,) + run)
    if labels:
        _paint_labels(p, scene, look, discs, eye, m, selected, looks)
    return drawn


def _paint_holes(p: QPainter, spots, state: int, size: float, ink: QColor,
                 look: Look):
    """Paint stitch holes ``size`` pixels across at ``spots``, by what the
    check of the folded piece found for them. A hole it has nothing about
    is a plain dot of ``ink``. One whose stack lines up is a green bead,
    one whose stack is out of line a bigger red one, and one with nothing
    to meet an amber bead with the hole still showing in it -- each ringed
    in ``ink`` to stand off any leather, and each a different shape as
    well as colour. ``LANDS`` spots get a red ring: where a hole that a
    red one is out of line with comes down. ``BLANK`` spots get a thin
    ring of ``ink`` alone, with no colour of a fault in it: where a hole
    of another piece of a stack comes through this one, which has no hole
    there."""
    if not state:
        dot = QPen(ink, size)
        dot.setCapStyle(Qt.RoundCap)
        p.setPen(dot)
        p.drawPoints(QPolygonF(spots))
        return
    bead = max(4.4, 1.35 * size)
    if state == BLANK:
        quiet = QColor(ink)
        quiet.setAlpha(170)
        r = 0.5 * bead + 1.2
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(quiet, 1.1))
        for q in spots:                # (quicker than all as one path)
            p.drawEllipse(q, r, r)
        return
    if state == LANDS:
        r = 0.5 * bead + 1.2
        p.setBrush(Qt.NoBrush)
        for colour, width in ((ink, 2.8), (look.bad, 1.4)):
            p.setPen(QPen(colour, width))
            for q in spots:
                p.drawEllipse(q, r, r)
        return
    if state == OFF:
        bead *= 1.25
    layers = [(ink, bead + 1.8), (look.hole[state], bead)]
    if state == LONE:
        layers.append((ink, 0.5 * bead))
    poly = QPolygonF(spots)
    for colour, width in layers:
        dot = QPen(colour, width)
        dot.setCapStyle(Qt.RoundCap)
        p.setPen(dot)
        p.drawPoints(poly)


def fold_pen(look: Look, fold, live: bool, kind: int = FRONT) -> QPen:
    """The line a fold is drawn with on one side of the leather: solid to
    the front, dashed to the back, dotted grey for one that isn't folded."""
    pen = QPen(look.fold_colour(fold, live, kind), 2.2)
    pen.setCosmetic(True)
    if not live:
        pen.setStyle(Qt.DotLine)
    elif fold.direction < 0:
        pen.setDashPattern([4.0, 2.5])
    else:
        pen.setCapStyle(Qt.RoundCap)
    return pen


def _paint_labels(p, scene, look, discs, eye, m, selected, looks=None):
    layout = scene.layout
    looks = looks or {}
    ox, ax, ay, az, oy, bx, by, bz = m
    ex, ey, ez = eye

    def spot(q):
        return QPointF(ox + ax * q[0] + ay * q[1] + az * q[2],
                       oy + bx * q[0] + by * q[1] + bz * q[2])

    font = QFont(p.font())
    font.setBold(True)
    font.setPointSizeF(10.0)
    p.setFont(font)
    # panel numbers, on whichever face is towards the eye and in clear
    # view (on a stack a mark says, last, which piece each spot is on)
    for panel, mark in zip(layout.panels, scene.panels):
        front, back, nn = mark[:3]
        on = mark[3] if len(mark) > 3 else (0, 0)
        facing = nn[0] * ex + nn[1] * ey + nn[2] * ez
        if abs(facing) < 0.08:
            continue
        up = facing > 0.0
        at, piece = (front, on[0]) if up else (back, on[1])
        if _hidden(at, discs, eye, m):
            continue
        kind = scene.face_of(piece, up)
        leather = looks.get(piece, look)
        c = spot(at)
        box = QRectF(c.x() - 14, c.y() - 10, 28, 20)
        r, g, b = leather.flat(kind)
        # ringed in the leather's own colour, so the grain doesn't break
        # up the figure
        halo = QColor(int(r), int(g), int(b))
        p.setPen(QPen(halo.darker(118) if leather.is_dark(kind)
                      else halo.lighter(122), 3.0))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(box.translated(dx, dy), Qt.AlignCenter,
                       str(panel.number))
        p.setPen(leather.ink(kind))
        p.drawText(box, Qt.AlignCenter, str(panel.number))
    # fold letters: a disc in the fold's colour at the middle of the line
    for fi, marks in scene.folds.items():
        fold = layout.folds[fi]
        if not fold.label:
            continue
        a, b, a2, b2, nn = max(marks, key=lambda mk: (mk[0][0] - mk[1][0]) ** 2
                               + (mk[0][1] - mk[1][1]) ** 2
                               + (mk[0][2] - mk[1][2]) ** 2)[:5]
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
        # the letter's disc is always the strong colour: it has a white
        # ring to stand off any leather
        colour = QColor(_PLAIN.fold_colour(fold, bool(layout.steps[fi])))
        if ghost:
            colour.setAlpha(120)
        r = 9.5
        p.setPen(QPen(QColor(255, 255, 255, 230 if not ghost else 120), 1.4))
        p.setBrush(QBrush(colour))
        p.drawEllipse(c, r, r)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter,
                   fold.label)


def sheet_looks(layout, look: Look, leathers=None, made=None) -> dict:
    """The ``Look`` of each piece of a stack that is shown in a leather of
    its own: ``{piece: Look}``, for ``paint_scene``. A piece in the usual
    leather -- that of ``look`` -- is left out.

    A piece's leather is the one ``leathers`` names for it, by its number
    (0 is the main piece) or else by its ``key``; failing that, its own
    ``leather``; and failing that, or if no such leather is known, the
    usual one. The key and the leather of a sheet are the sheet's, those
    of the main piece the stack's own (a piece shown alone has neither:
    it goes by the number 0). ``made`` keeps the Looks from one call to
    the next, by leather: one is made the first time its leather is
    wanted and dropped once no piece is in it."""
    leathers = leathers or {}
    made = {} if made is None else made
    out = {}
    sheets = getattr(layout, "sheets", None) or ()
    for piece in range(len(sheets) + 1):
        name = leathers.get(piece)
        if name is None:
            of = sheets[piece - 1] if piece else layout
            key = getattr(of, "key", "")
            name = leathers.get(key) if key else None
            if name is None:
                name = getattr(of, "leather", "")
        if name in LEATHERS and name != look.leather:
            if name not in made:
                made[name] = Look(look.dark, name)
            out[piece] = made[name]
    in_use = {lk.leather for lk in out.values()}
    for name in [name for name in made if name not in in_use]:
        del made[name]
    return out


def pieces_at(scene: Scene, cam: Camera, w: int, h: int, x: float,
              y: float, first: bool = False) -> List[int]:
    """The pieces of the model under a point of a ``w`` x ``h`` picture
    of it, the one in view first and then each that lies behind it, in
    turn: 0 for the main piece (or a piece shown alone), else a sheet's
    number. The cells are gone through as they are painted, but from the
    front: the first face turned to the eye that the point is on is the
    one in view there. With ``first`` the looking stops at that one."""
    ox, ax, ay, az, oy, bx, by, bz = cam.mapping(w, h)
    ex, ey, ez = cam.axes()[2]
    s2 = ax * ax + ay * ay + az * az
    out: List[int] = []
    for cell in reversed(bsp.back_to_front(scene.tree, ex, ey, ez)):
        piece = sheet_of(cell.tag)
        if piece in out:
            continue
        cx, cy, cz = cell.cx, cell.cy, cell.cz
        if (ox + ax * cx + ay * cy + az * cz - x) ** 2 \
                + (oy + bx * cx + by * cy + bz * cz - y) ** 2 \
                > cell.rad * cell.rad * s2:
            continue
        for f in cell.faces:
            nx, ny, nz, _d = f.plane
            if f.kind == INNER or nx * ex + ny * ey + nz * ez <= 1e-6:
                continue
            px, py, pz = f.pts[-1]
            u0 = ox + ax * px + ay * py + az * pz
            v0 = oy + bx * px + by * py + bz * pz
            sign = 0.0
            for px, py, pz in f.pts:
                u1 = ox + ax * px + ay * py + az * pz
                v1 = oy + bx * px + by * py + bz * pz
                c = (u1 - u0) * (y - v0) - (v1 - v0) * (x - u0)
                if c != 0.0:
                    if sign == 0.0:
                        sign = c
                    elif (c > 0.0) != (sign > 0.0):
                        break
                u0, v0 = u1, v1
            else:
                out.append(piece)
                if first:
                    return out
                break
    return out


def piece_at(scene: Scene, cam: Camera, w: int, h: int, x: float,
             y: float) -> int:
    """The piece of the model in view at a point of a ``w`` x ``h``
    picture of it: 0 for the main piece (or a piece shown alone), a
    sheet's number, or -1 where there is nothing (see ``pieces_at``)."""
    under = pieces_at(scene, cam, w, h, x, y, True)
    return under[0] if under else -1


def render(layout, t: float, w: int = 900, h: int = 620, *, dark: bool = False,
           yaw: Optional[float] = None, pitch: Optional[float] = None,
           zoom: float = 1.0, labels: bool = True, selected: int = -1,
           check=None, leather: str = DEFAULT_LEATHER, leathers=None,
           chosen=(), wash=None, apart: float = 0.0,
           turned: bool = False) -> QImage:
    """A picture of ``layout`` at time ``t`` of its folding (see
    ``Layout.fractions``), in one of the ``LEATHERS``; with the ``check``
    from ``layout.lineup()``, its stitch holes show what that found.

    For a stack: ``leathers`` names the leather of any piece that is not
    in the usual one (see ``sheet_looks``), ``chosen`` and ``wash`` light
    and mark pieces (see ``paint_scene``), ``apart`` lifts the layers off
    each other, and ``turned`` shows it as it lies on the bench with its
    main piece grain down (see ``Camera``)."""
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    cam = Camera()
    scene = Scene(layout, t, check, apart)
    if scene.apart:
        lo, hi = scene.bounds()
        cam.centre = tuple(0.5 * (a + b) for a, b in zip(lo, hi))
        cam.radius = max(5.0, 0.5 * math.dist(lo, hi))
    else:
        cam.centre, cam.radius = layout.extent()
    if yaw is not None:
        cam.yaw = yaw
    if pitch is not None:
        cam.pitch = pitch
    cam.zoom = zoom
    cam.turned = bool(turned)
    look = Look(dark, leather)
    p = QPainter(img)
    paint_scene(p, w, h, scene, cam, look, labels=labels, selected=selected,
                looks=sheet_looks(layout, look, leathers), chosen=chosen,
                wash=wash)
    p.end()
    return img


class _Way:
    """A fold direction on its own, for the legend."""

    def __init__(self, direction):
        self.direction = direction


class FoldView(QWidget):
    """The model on screen. Drag to turn it, drag with the right or middle
    button (or Shift) to slide it, roll the wheel to zoom, double-click to
    fit it back in. Clicking a fold line picks that fold; clicking
    anywhere else picks the piece that is there, or none, and Alt+click
    the next piece down under the pointer, as on the drawing. The view
    keeps the model centred and filling it as it folds, until it is
    zoomed or slid by hand.

    What the view is told about the pieces of a stack -- the leather of
    each (``set_leathers``), the chosen ones (``set_chosen``), the marks
    (``set_wash``), how far apart the layers are lifted (``set_apart``)
    and whether the model is turned like a page (``set_turned``) -- it
    keeps until it is told otherwise. ``set_layout`` changes none of it,
    and a piece is only a number: shown a piece alone after a stack, the
    view lights, marks and colours that piece as the stack's piece 0."""

    foldPicked = Signal(int)          # the fold clicked on, -1 for none
    # the piece clicked on (0 the main piece, -1 for none), and whether it
    # is to be added to those chosen (Ctrl or Shift was held)
    piecePicked = Signal(int, bool)

    def __init__(self, parent=None, dark: bool = False):
        super().__init__(parent)
        self.look = Look(dark)
        self.cam = Camera()
        self.layout_ = None
        self.scene: Optional[Scene] = None
        self.t = 0.0
        self.apart = 0.0              # how far a stack's layers are lifted
        self.check = None             # what ``layout.lineup()`` found
        self.check_shown = True       # ... and whether the holes show it
        self.selected = -1
        self.hover = -1
        self.chosen: Tuple[int, ...] = ()    # the pieces that are lit
        self.wash: dict = {}          # colours over pieces: ``paint_scene``
        self.labels = True
        self.follow = True            # keep the model filling the view
        self.caption = ""
        self._drawn: List[tuple] = []
        self._seen: List[tuple] = []  # ... and the stretches of them in sight
        self._leathers: dict = {}     # the leather given for a piece
        self._looks: dict = {}        # a Look for each leather in use
        self._press = None
        self._moved = False
        self._under = None            # the last pick: where, of what, which
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- what is shown --------------------------------------------------------

    def set_layout(self, layout, t: Optional[float] = None, check=None,
                   refit: bool = False):
        """Show ``layout`` (None for nothing). The view stays as it was
        turned unless ``refit``; and what it was told of the pieces of a
        stack stays as it was (see the class)."""
        first = self.layout_ is None
        self.layout_ = layout
        self.check = check
        if t is not None:
            self.t = t
        if layout is None:
            self.scene = None
        else:
            self.t = min(max(self.t, 0.0), float(layout.n_steps))
            if first or refit:
                self.follow = True
            self.scene = Scene(layout, self.t, self._marks(), self.apart)
            self._frame()
        self.update()

    def set_time(self, t: float):
        if self.layout_ is None:
            return
        t = min(max(t, 0.0), float(self.layout_.n_steps))
        if self.scene is not None and abs(t - self.t) < 1e-9:
            return
        self.t = t
        self.scene = Scene(self.layout_, t, self._marks(), self.apart)
        self._frame()
        self.update()

    def set_apart(self, apart: float):
        """Lift the layers of a stack ``apart`` mm off each other, to show
        what lies on what (0 lays them down again). A piece shown alone
        has nothing to lift."""
        apart = max(0.0, float(apart))
        if abs(apart - self.apart) < 1e-9:
            return
        self.apart = apart
        if self.layout_ is not None and self.scene is not None \
                and self.scene.sheets:
            self.scene = Scene(self.layout_, self.t, self._marks(), apart)
            self._frame()
            self.update()

    def _marks(self):
        """The check of the stitch holes, if the model is to show it."""
        return self.check if self.check_shown else None

    def show_check(self, on: bool):
        """Mark the stitch holes by whether they line up, or leave them
        all plain."""
        on = bool(on)
        if on != self.check_shown:
            self.check_shown = on
            if self.layout_ is not None:
                self.scene = Scene(self.layout_, self.t, self._marks(),
                                   self.apart)
            self.update()

    def _frame(self):
        """While the view follows the model, keep it centred and filling
        the view as it folds (it stops once you zoom or slide it yourself;
        Fit starts it again)."""
        if not self.follow or self.layout_ is None or self.scene is None:
            return
        lo, hi = self.scene.bounds()
        self.cam.centre = tuple(0.5 * (a + b) for a, b in zip(lo, hi))
        self.cam.radius = max(5.0, 0.5 * math.dist(lo, hi))
        self.cam.pan_x = self.cam.pan_y = 0.0
        self.cam.zoom = 1.0

    def set_leather(self, name: str):
        """Show the piece in another of the ``LEATHERS``: the usual one,
        for every piece of a stack that has none of its own."""
        if name != self.look.leather:
            self.look = Look(self.look.dark, name)
            self.update()

    def set_leathers(self, leathers):
        """Say which leather each piece of a stack is shown in:
        ``{piece: name}``, a piece being its number in the model (0 is the
        main piece) or its ``key`` -- a sheet's own, the stack's for its
        main piece. A piece left out is in its own ``leather``, or the
        usual one (``set_leather``). See ``sheet_looks``."""
        leathers = dict(leathers or {})
        if leathers != self._leathers:
            self._leathers = leathers
            self.update()

    def leather_of(self, piece: int) -> str:
        """The leather a piece of the model is shown in."""
        looks = sheet_looks(self.layout_, self.look, self._leathers,
                            self._looks)
        return looks.get(piece, self.look).leather

    def set_selected(self, fold: int):
        if fold != self.selected:
            self.selected = fold
            self.update()

    def set_chosen(self, pieces):
        """Light the chosen pieces of a stack (their numbers in the model,
        0 for the main piece): a line of the pick colour round each, in
        sight or not. Their leather is left as it is."""
        pieces = tuple(pieces)
        if pieces != self.chosen:
            self.chosen = pieces
            self.update()

    def set_wash(self, wash):
        """Mark pieces of the model with a colour laid over them, as
        ``paint_scene`` takes it: ``{(piece, None): colour}`` for a whole
        piece, ``{(piece, fold): colour}`` for what it has in one bend.
        ``look.warn`` and ``look.bad`` are the colours for leather that is
        in the way of a fold and leather that has run into leather. A
        colour is laid on strongly enough to be seen for itself on any
        leather; one that carries an alpha of its own, below 255, is laid
        on as strongly as that says."""
        wash = dict(wash or {})
        if wash != self.wash:
            self.wash = wash
            self.update()

    def set_turned(self, on: bool):
        """Show a stack as it lies on the bench with its main piece grain
        down: turned over like a page (see ``Camera``)."""
        if bool(on) != self.cam.turned:
            self.cam.turned = bool(on)
            self.update()

    def look_from(self, where: str):
        """Turn to a set view: ``"front"``, ``"back"``, ``"side"`` or the
        three-quarter ``"iso"``. For a stack on the bench ``"top"`` looks
        straight down on it, as ``"front"`` does, and ``"under"`` shows
        what is underneath as if the stack were turned over like a page:
        its top edge still at the top, left and right changed over."""
        cam = self.cam
        if where == "front" or where == "top":
            cam.yaw, cam.pitch = 0.0, 0.0
        elif where == "back":
            cam.yaw, cam.pitch = 0.0, math.pi
        elif where == "under":
            cam.yaw, cam.pitch = math.pi, math.pi
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
        looks = sheet_looks(self.layout_, self.look, self._leathers,
                            self._looks)
        self._seen = []
        self._drawn = paint_scene(p, w, h, self.scene, self.cam, self.look,
                                  labels=self.labels, selected=self.selected,
                                  hover=self.hover, looks=looks,
                                  chosen=self.chosen, wash=self.wash,
                                  seen=self._seen)
        self._paint_legend(p, w, h, looks)
        p.end()

    def _paint_legend(self, p: QPainter, w: int, h: int, looks=None):
        look = self.look
        stack = bool(self.scene is not None and self.scene.sheets)
        font = QFont(p.font())
        font.setBold(False)
        font.setPointSizeF(9.0)
        p.setFont(font)
        fm = p.fontMetrics()
        used = 0
        if self.caption:
            p.setPen(look.text)
            p.drawText(QRectF(12, 8, w - 24, 22),
                       Qt.AlignLeft | Qt.AlignVCenter, self.caption)
            used = fm.horizontalAdvance(self.caption) + 24
        if fm.horizontalAdvance(_HINT) <= w - 24 - used:
            p.setPen(look.faint)               # how to move it, if it fits
            p.drawText(QRectF(12, 8, w - 24, 22),
                       Qt.AlignRight | Qt.AlignVCenter, _HINT)
        x, y = 12.0, h - 16.0
        # the two sides of the leather: where the pieces of a stack are in
        # leathers of their own, of the piece that is chosen, or of the
        # main piece if none is
        leather = look
        if stack and looks:
            leather = looks.get(self.chosen[0] if self.chosen else 0, look)
        for kind, text in ((FRONT, "grain side" if stack
                            else "front of the leather"),
                           (BACK, "flesh side" if stack else "back")):
            p.setPen(QPen(leather.edge, 1.0))
            p.setBrush(QBrush(QColor(*leather.flat(kind))))
            p.drawRect(QRectF(x, y - 9, 13, 10))
            p.setPen(look.faint)
            p.drawText(QPointF(x + 18, y), text)
            x += 18 + fm.horizontalAdvance(text) + 16
        # the fold lines: both ways for a piece shown alone; for a stack
        # only the lines that are in the picture, and so the grey line of
        # a fold that is left flat too
        ways = [(1, True, "folds to the front"),
                (-1, True, "folds to the back")]
        if stack:
            layout = self.layout_
            made = {layout.folds[fi].direction >= 0
                    for fi in self.scene.folds if layout.steps[fi]}
            ways = [way for way in ways if (way[0] > 0) in made]
            if any(not layout.steps[fi] for fi in self.scene.folds):
                ways.append((1, False, "fold left flat"))
        for way, live, text in ways:
            p.setPen(fold_pen(_PLAIN, _Way(way), live))
            p.drawLine(QPointF(x, y - 4), QPointF(x + 22, y - 4))
            p.setPen(look.faint)
            p.drawText(QPointF(x + 27, y), text)
            x += 27 + fm.horizontalAdvance(text) + 16
        check = self._marks()
        blanks = getattr(check, "blanks", None)
        if check is None or not (check.state or blanks):
            return
        # ... and above it, what the stitch holes are saying
        head = "Stitch holes, folded:"
        marks = [(MEETS, "line up"), (OFF, "out of line"),
                 (LANDS, "where the other hole is"),
                 (LONE, "no hole to meet")]
        if stack:
            # of a stack, lying flat or not: and only the marks it has
            head = "Stitch holes:"
            found = set(check.state.values())
            if check.rings:
                found.add(LANDS)
            marks = [mark for mark in marks if mark[0] in found]
            if blanks:
                marks.append((BLANK, "another piece's hole comes through"))
        x, y = 12.0, h - 38.0
        p.setPen(look.faint)
        p.drawText(QPointF(x, y), head)
        x += fm.horizontalAdvance(head) + 12
        for state, text in marks:
            _paint_holes(p, [QPointF(x + 6, y - 4)], state, 4.0, look.text,
                         look)
            p.setPen(look.faint)
            p.drawText(QPointF(x + 17, y), text)
            x += 17 + fm.horizontalAdvance(text) + 14

    # -- the mouse ------------------------------------------------------------

    def fold_at(self, x: float, y: float, reach: float = 8.0) -> int:
        """The fold whose line is nearest ``(x, y)``, within ``reach``
        pixels, or -1. On a stack a line counts only where it is in
        sight: under a piece that lies over it, that piece is what a
        click is for."""
        stack = self.scene is not None and self.scene.sheets
        best, bd = -1, reach
        for fi, x0, y0, x1, y1 in self._seen if stack else self._drawn:
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

    def piece_at(self, x: float, y: float) -> int:
        """The piece of the model in view at a point of the view: 0 for
        the main piece (or a piece shown alone), a sheet's number, -1 for
        none."""
        if self.scene is None:
            return -1
        return piece_at(self.scene, self.cam, self.width(), self.height(),
                        x, y)

    def pieces_at(self, x: float, y: float) -> List[int]:
        """Every piece of the model under a point of the view, the one in
        view first and then those behind it, in turn."""
        if self.scene is None:
            return []
        return pieces_at(self.scene, self.cam, self.width(), self.height(),
                         x, y)

    def _next_under(self, x: float, y: float, under: List[int]) -> int:
        """Alt+click: the piece after the one picked last time at this
        spot (round to the first again after the last), or the second
        from the front on a first Alt+click -- as on the drawing."""
        i = 0
        if self._under is not None:
            lx, ly, pieces, k = self._under
            if abs(lx - x) <= 4 and abs(ly - y) <= 4 \
                    and set(pieces) == set(under) and pieces[k] in under:
                i = under.index(pieces[k])
        return (i + 1) % len(under)

    def mouseReleaseEvent(self, e):
        press, self._press = self._press, None
        if press is not None and not self._moved \
                and press[2] == Qt.LeftButton:
            x, y = e.position().x(), e.position().y()
            fold = self.fold_at(x, y)
            self.foldPicked.emit(fold)
            if fold < 0:                       # not a fold line: a piece
                mods = e.modifiers()
                under = self.pieces_at(x, y)
                k = 0
                if mods & Qt.AltModifier and len(under) > 1:
                    k = self._next_under(x, y, under)
                self._under = (x, y, under, k) if under else None
                self.piecePicked.emit(
                    under[k] if under else -1,
                    bool(mods & (Qt.ControlModifier | Qt.ShiftModifier)))

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

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

_LEVELS = 24                      # shades of each colour, dark to fully lit
_SHEEN = 8                        # ... and steps of sheen on top of that
_TILE = 256                       # a leather texture is this many pixels
_TILE_MM = 80.0                   # ... square, and covers this much leather
_CREASE = 0.94                    # cosine of the turn that shows as a crease

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
        self.edge = QColor(*(min(255, int(c + 58)) for c in w)) \
            if dark and _lum(w) <= 70 else QColor(*(int(c * 0.5) for c in w))
        self.bad = QColor(226, 38, 38)
        self.cut = QColor(52, 54, 60) if dark else QColor(120, 122, 130)
        self.idle = QColor(128, 128, 136)            # a fold that isn't made
        self.pick = QColor(255, 196, 0)
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


def paint_scene(p: QPainter, w: int, h: int, scene: Scene, cam: Camera,
                look: Look, *, labels: bool = True, selected: int = -1,
                hover: int = -1):
    """Paint the model into a ``w`` x ``h`` area. Returns the fold lines as
    drawn -- ``[(fold, x0, y0, x1, y1)]`` -- for finding the one under the
    mouse.

    The faces are filled without smoothing: that is many times quicker for
    the narrow strips a bend is made of, and it leaves no gaps between the
    pieces a face is painted in. What smoothing is for -- the rim of the
    model against what lies behind it -- is done with a smooth hairline
    along each edge that can be one: dark along an edge of the leather, the
    face's own colour where it turns out of sight round a bend."""
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
    shade = look.shade
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
    for cell in order:
        cast, faces, solid = [], [], []
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
                        rims.append(pts[i])
                        rims.append(pts[j])
                        # with the cut edge out of sight, this is the rim
                        # of the layer: its shadow falls from here
                        x, y, z = at[i]
                        x2, y2, z2 = at[j]
                        if (x2 - x) * c0 + (y2 - y) * c1 \
                                + (z2 - z) * c2 <= 0.0:
                            cast.append(pts[i])
                            cast.append(pts[j])
                    elif e is not False and (
                            e[0] * ex + e[1] * ey + e[2] * ez <= 1e-6
                            or e[0] * nx + e[1] * ny + e[2] * nz < _CREASE):
                        # the leather turns out of sight here, or sharply
                        soft.append(pts[i])
                        soft.append(pts[i + 1 if i + 1 < n else 0])
                    else:
                        if seam is None:
                            seam = seams.get((kind, level))
                            if seam is None:
                                seam = seams[(kind, level)] = QPainterPath()
                        seam.moveTo(pts[i])
                        seam.lineTo(pts[i + 1 if i + 1 < n else 0])
            else:
                for i in range(n):
                    (rims if edges is not None and edges[i] is True
                     else soft).extend((pts[i],
                                        pts[i + 1 if i + 1 < n else 0]))
            faces.append((f, level, pts, rims, soft))
        if faces or cast:
            cells.append((cast, faces))
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
    for (kind, level), seam in seams.items():
        p.setPen(shade(kind, level)[1])
        p.drawPath(seam)

    edge = QPen(look.edge, 1.0)
    edge.setCosmetic(True)
    cut = QBrush(look.cut)
    # the soft shadow an edge of the leather throws on what lies under it:
    # three bands, each narrower and so darker towards the edge
    reach = min(11.0, max(3.0, 1.6 * layout.thickness * scale))
    shadows = []
    for part in (1.0, 0.62, 0.3):
        band = QPen(QColor(0, 0, 0, 9), reach * part)
        band.setCapStyle(Qt.FlatCap)
        shadows.append(band)
    k = _TILE_MM / _TILE                       # mm to a pixel of texture
    inks = {FRONT: look.ink(FRONT), BACK: look.ink(BACK)}
    none = QPen(Qt.NoPen)
    for cast, faces in cells:
        if cast:
            for band in shadows:
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
                p.setPen(shade(f.kind, level)[1])
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
            p.drawPolygon(QPolygonF(pts))
            if rims:
                lines += rims
        if not lines and not more:
            continue
        # ... and over them what is drawn smooth: the marks on the leather,
        # the corners of its cut edge, and the line along each of its edges
        p.setRenderHint(smooth, True)
        if more:
            for f, level, pts, rims, soft in faces:
                kind = f.kind
                if kind > BACK:
                    if soft:
                        p.setPen(shade(kind, level)[1])
                        p.drawLines(soft)
                    continue
                if not f.decals:
                    continue
                for dk, geo, style in f.decals:
                    if dk == DOTS:
                        dia, wrong = style
                        dot = QPen(look.bad if wrong else inks[kind],
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
        if lines:
            p.setPen(edge)
            p.drawLines(lines)
        p.setRenderHint(smooth, False)
    p.setRenderHint(smooth, True)

    def spot(q):
        return (ox + ax * q[0] + ay * q[1] + az * q[2],
                oy + bx * q[0] + by * q[1] + bz * q[2])

    # The fold lines go on last, over the leather they lie on: drawn with
    # the faces, the next strip of the bend painted half of each away. A
    # line shows on a face turned towards the eye, wherever nothing stands
    # in front of it; the chosen fold is lit through whatever does.
    drawn = []
    pieces = 8
    for fi, marks in scene.folds.items():
        fold = layout.folds[fi]
        live = bool(layout.steps[fi])
        for a, b, a2, b2, nn in marks:
            facing = nn[0] * ex + nn[1] * ey + nn[2] * ez
            for q, r, side, kind in ((a, b, facing, FRONT),
                                     (a2, b2, -facing, BACK)):
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
                p.setPen(fold_pen(look, fold, live, kind))
                for i in range(pieces + 1):
                    clear = False
                    if i < pieces:
                        t = (i + 0.5) / pieces
                        clear = not _hidden(
                            (q[0] + (r[0] - q[0]) * t,
                             q[1] + (r[1] - q[1]) * t,
                             q[2] + (r[2] - q[2]) * t), discs, eye, m)
                    if clear and start is None:
                        start = i
                    elif not clear and start is not None:
                        t0, t1 = start / pieces, i / pieces
                        p.drawLine(QPointF(x0 + (x1 - x0) * t0,
                                           y0 + (y1 - y0) * t0),
                                   QPointF(x0 + (x1 - x0) * t1,
                                           y0 + (y1 - y0) * t1))
                        start = None
    if labels:
        _paint_labels(p, scene, look, discs, eye, m, selected)
    return drawn


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
        at, kind = (front, FRONT) if facing > 0.0 else (back, BACK)
        if _hidden(at, discs, eye, m):
            continue
        c = spot(at)
        box = QRectF(c.x() - 14, c.y() - 10, 28, 20)
        r, g, b = look.flat(kind)
        # ringed in the leather's own colour, so the grain doesn't break
        # up the figure
        halo = QColor(int(r), int(g), int(b))
        p.setPen(QPen(halo.darker(118) if look.is_dark(kind)
                      else halo.lighter(122), 3.0))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(box.translated(dx, dy), Qt.AlignCenter,
                       str(panel.number))
        p.setPen(look.ink(kind))
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


def render(layout, t: float, w: int = 900, h: int = 620, *, dark: bool = False,
           yaw: Optional[float] = None, pitch: Optional[float] = None,
           zoom: float = 1.0, labels: bool = True, selected: int = -1,
           bad=(), leather: str = DEFAULT_LEATHER) -> QImage:
    """A picture of ``layout`` at time ``t`` of its folding (see
    ``Layout.fractions``), in one of the ``LEATHERS``."""
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    cam = Camera()
    cam.centre, cam.radius = layout.extent()
    if yaw is not None:
        cam.yaw = yaw
    if pitch is not None:
        cam.pitch = pitch
    cam.zoom = zoom
    p = QPainter(img)
    paint_scene(p, w, h, Scene(layout, t, bad), cam, Look(dark, leather),
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

    def set_leather(self, name: str):
        """Show the piece in another of the ``LEATHERS``."""
        if name != self.look.leather:
            self.look = Look(self.look.dark, name)
            self.update()

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
            p.setPen(QPen(look.edge, 1.0))
            p.setBrush(QBrush(QColor(*look.flat(kind))))
            p.drawRect(QRectF(x, y - 9, 13, 10))
            p.setPen(look.faint)
            p.drawText(QPointF(x + 18, y), text)
            x += 18 + fm.horizontalAdvance(text) + 16
        for way, text in ((1, "folds to the front"),
                          (-1, "folds to the back")):
            p.setPen(fold_pen(_PLAIN, _Way(way), True))
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

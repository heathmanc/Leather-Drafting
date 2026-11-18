"""A stack of pieces in the 3D view: each piece in its own leather, the
piece under the mouse, the chosen piece lit, the layers pulled apart and
the bench seen from above."""

import math
import os
import random

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad import bsp  # noqa: E402
from leathercad import foldsolid as fs  # noqa: E402
from leathercad import polycut as pc  # noqa: E402
from leathercad import sheets as sh  # noqa: E402
from leathercad.generators import bifold_wallet  # noqa: E402
from leathercad.stitching import holes_for_shape  # noqa: E402

MAIN = [(0, 0), (200, 0), (200, 100), (0, 100)]
# the shell, the liner, then each pocket stack from its backing up
WORN = ["Chestnut", "Natural", "Black", "Navy", "Burgundy", "Olive",
        "Dark brown", "Tan", "Navy", "Black"]
# a point of the pattern where each sheet of the bifold is the one on top
# (see ``_bifold``): the liner above the backings, then up each stack
SPOTS = [(50.0, 38.5), (50.0, 21.5), (30.0, 4.0), (50.0, -6.0),
         (50.0, -28.0), (-50.0, 21.5), (-30.0, 4.0), (-50.0, -6.0),
         (-50.0, -28.0)]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _ring(shape):
    return [(p.x, p.y) for p in shape.world_polyline()[0]]


def _bifold(holes=True):
    """The generated bifold put together (see tests/test_sheets.py): the
    shell grain down, sheets 1 to 9 the liner, the left backing and
    pockets 3, 2, 1, the right backing and pockets 3, 2, 1. The left
    stack, as it lies on the bench, is on the right of the shell as it is
    drawn."""
    parts = bifold_wallet()
    shell, spine, liner = parts[0], parts[1], parts[2]
    outline = _ring(shell)
    x0, y0, x1, _y1 = pc.bounds(outline)
    sheets = []

    def lay(shape, where):
        r = _ring(shape)
        a0, b0, a1, _b1 = pc.bounds(r)
        dx = {"left": x1 - a1, "right": x0 - a0,
              "middle": 0.5 * (x0 + x1) - 0.5 * (a0 + a1)}[where]
        sheets.append(sh.Sheet([(x + dx, y + y0 - b0) for x, y in r], 0.8,
                               side=-1, grain=-1, name=shape.name,
                               key=shape.shape_id))

    lay(liner, "middle")
    for stack, where in ((parts[3:7], "left"), (parts[7:11], "right")):
        for p in reversed(stack):
            lay(p, where)
    ends = spine.world_polyline()[0]
    fold = fs.FoldLine((ends[0].x, ends[0].y), (ends[-1].x, ends[-1].y),
                       180.0, 1, label="A")
    dots = [(h.point.x, h.point.y, 1.0)
            for h in holes_for_shape(shell, None).holes] if holes else []
    layout = fs.fold_piece(outline, [fold], thickness=1.6, holes=dots)
    return sh.FlatStack(layout, sheets, outline, name=shell.name)


def _camera(fv, stack, yaw, pitch, turned=True, apart=0.0, zoom=1.0):
    """The camera ``foldview.render`` looks through."""
    cam = fv.Camera()
    if apart:
        lo, hi = stack.bounds3([], apart)
        cam.centre = tuple(0.5 * (a + b) for a, b in zip(lo, hi))
        cam.radius = max(5.0, 0.5 * math.dist(lo, hi))
    else:
        cam.centre, cam.radius = stack.extent()
    cam.yaw, cam.pitch, cam.zoom, cam.turned = yaw, pitch, zoom, turned
    return cam


def _pixel(cam, w, h, p):
    """Where a point of the model shows in a ``w`` x ``h`` picture."""
    ox, ax, ay, az, oy, bx, by, bz = cam.mapping(w, h)
    return (ox + ax * p[0] + ay * p[1] + az * p[2],
            oy + bx * p[0] + by * p[1] + bz * p[2])


def _on_top(stack, k, apart=0.0):
    """A point of the model on the outer face of sheet ``k`` of the
    bifold, where nothing lies over it."""
    s = stack.sheets[k - 1]
    x, y = SPOTS[k - 1]
    return (x, y, -(0.8 + s.hi + apart * s.rank))


def _patch(img, cx, cy, half=4):
    """The colour of a bit of a picture, on average."""
    r = g = b = n = 0
    for x in range(int(cx) - half, int(cx) + half + 1):
        for y in range(int(cy) - half, int(cy) + half + 1):
            c = img.pixelColor(x, y)
            r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
    return (r / n, g / n, b / n)


def _rgb(c):
    return (c.red(), c.green(), c.blue())


def _leathers():
    return dict(enumerate(WORN))


# -- a leather for each piece -------------------------------------------------

def test_a_pixel_on_each_pocket_of_the_bifold_has_that_pockets_leather(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold()
    w, h = 900, 600
    kw = dict(yaw=0.0, pitch=0.0, turned=True, labels=False,
              leather="Chestnut")
    img = fv.render(stack, 0.0, w, h, leathers=_leathers(), **kw)
    cam = _camera(fv, stack, 0.0, 0.0)
    scene = fv.Scene(stack, 0.0)
    for k in range(1, 10):
        x, y = _pixel(cam, w, h, _on_top(stack, k))
        # the left stack is on the left of the bench, the right on the right
        assert (x < w / 2) == (k <= 5) or k == 1
        seen = _patch(img, x, y)
        grain = {name: math.dist(seen, rgb[0])
                 for name, rgb in fv.LEATHERS.items()}
        assert grain[WORN[k]] < 30, (k, seen)
        assert min(grain, key=grain.get) == WORN[k], (k, seen)
        assert fv.piece_at(scene, cam, w, h, x, y) == k
    # in one leather they are all the same, and the picture is another
    plain = fv.render(stack, 0.0, w, h, **kw)
    assert plain != img
    for k in range(1, 10):
        seen = _patch(plain, *_pixel(cam, w, h, _on_top(stack, k)))
        assert math.dist(seen, fv.LEATHERS["Chestnut"][0]) < 30, k
    # a sheet's own leather is used when nothing else is said of it, and
    # one that is not known is the usual one
    stack.sheets[4].leather = "Navy"
    stack.sheets[3].leather = "Snakeskin"
    own = fv.render(stack, 0.0, w, h, **kw)
    assert math.dist(_patch(own, *_pixel(cam, w, h, _on_top(stack, 5))),
                     fv.LEATHERS["Navy"][0]) < 30
    assert math.dist(_patch(own, *_pixel(cam, w, h, _on_top(stack, 4))),
                     fv.LEATHERS["Chestnut"][0]) < 30


def test_from_under_the_bench_the_shells_grain_side_shows(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold()
    w, h = 900, 600
    img = fv.render(stack, 0.0, w, h, yaw=math.pi, pitch=math.pi,
                    turned=True, labels=False, leather="Chestnut",
                    leathers=_leathers())
    cam = _camera(fv, stack, math.pi, math.pi)
    scene = fv.Scene(stack, 0.0)
    for x, y in ((50.0, 0.0), (-50.0, 20.0), (60.0, -30.0)):
        px, py = _pixel(cam, w, h, (x, y, 0.8))
        # as it is drawn: what is on the right of the pattern is on the right
        assert (px > w / 2) == (x > 0) and (py < h / 2) == (y > 0)
        seen = _patch(img, px, py)
        assert math.dist(seen, fv.LEATHERS["Chestnut"][0]) < 30, seen
        assert fv.piece_at(scene, cam, w, h, px, py) == 0
    # past the shell's round corner a square corner of the pocket backing
    # shows, flesh side: the black one on the pattern's right
    px, py = _pixel(cam, w, h, (99.0, -44.4, -1.6))
    assert fv.piece_at(scene, cam, w, h, px, py) == 2
    assert math.dist(_rgb(img.pixelColor(int(px), int(py))),
                     fv.LEATHERS["Black"][1]) < 50
    px, py = _pixel(cam, w, h, (-99.0, -44.4, -1.6))
    assert fv.piece_at(scene, cam, w, h, px, py) == 6


def test_the_usual_look_keeps_the_windows_colours_and_others_are_made_once(
        qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    look = fv.Look(False, "Chestnut")
    made = {}
    looks = fv.sheet_looks(stack, look, _leathers(), made)
    # the shell is in the usual leather: only the sheets have a look
    assert sorted(looks) == list(range(1, 10))
    assert [looks[k].leather for k in range(1, 10)] == WORN[1:]
    assert sorted(made) == sorted(set(WORN[1:]))
    assert looks[3] is looks[8] is made["Navy"]          # one a leather
    again = fv.sheet_looks(stack, look, _leathers(), made)
    assert all(again[k] is looks[k] for k in looks)
    # by its key instead of its number; its number wins if both are given
    by_key = {s.key: "Olive" for s in stack.sheets}
    by_key[2] = "Black"
    looks = fv.sheet_looks(stack, look, by_key, made)
    assert [looks[k].leather for k in range(1, 10)] \
        == ["Olive", "Black"] + ["Olive"] * 7
    assert sorted(made) == ["Black", "Olive"]            # the rest dropped
    assert fv.sheet_looks(stack, look, None, made) == {} and made == {}
    # a piece shown alone is piece 0, and is told its leather as any other
    # is; a number that no piece of the model has says nothing
    lay = fs.fold_piece(MAIN, [])
    alone = fv.sheet_looks(lay, look, {0: "Black"})
    assert list(alone) == [0] and alone[0].leather == "Black"
    assert fv.sheet_looks(lay, look, {0: "Chestnut", 1: "Black"}) == {}
    # the colours of the window are the one look's
    look.ground = QColor(255, 0, 255)
    cam = _camera(fv, stack, 0.0, 0.0)
    img = QImage(300, 200, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    fv.paint_scene(p, 300, 200, fv.Scene(stack, 0.0), cam, look,
                   looks=fv.sheet_looks(stack, look, _leathers()))
    p.end()
    assert _rgb(img.pixelColor(3, 3)) == (255, 0, 255)


def test_a_panels_number_is_written_in_the_ink_of_the_piece_it_is_on(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    w, h = 900, 600
    cam = _camera(fv, stack, 0.0, 0.0)
    # panel 1's number is on pocket 3 of the stack that lies on that half
    (_f, back, _n, on), _two = stack.panel_marks([0.0])
    assert on == (0, 7)
    px, py = _pixel(cam, w, h, back)
    kw = dict(yaw=0.0, pitch=0.0, turned=True, leather="Chestnut")

    def inks(leather):
        img = fv.render(stack, 0.0, w, h, leathers={7: leather}, **kw)
        bare = fv.render(stack, 0.0, w, h, leathers={7: leather},
                         labels=False, **kw)
        return [_rgb(img.pixelColor(x, y))
                for x in range(int(px) - 9, int(px) + 10)
                for y in range(int(py) - 9, int(py) + 10)
                if img.pixel(x, y) != bare.pixel(x, y)]

    # dark figures on a pale leather, pale ones on a dark leather: each in
    # that leather's own ink, and not in the other's
    own = {name: _rgb(fv.Look(False, name).ink(fv.FRONT))
           for name in ("Natural", "Black")}
    assert sum(own["Natural"]) < 150 and sum(own["Black"]) > 600
    for name, other in (("Natural", "Black"), ("Black", "Natural")):
        figure = inks(name)
        assert len(figure) > 20
        assert min(math.dist(c, own[name]) for c in figure) < 12
        assert min(math.dist(c, own[other]) for c in figure) > 12


def test_the_grain_of_one_piece_does_not_run_on_into_the_next(qapp):
    from leathercad_app import foldview as fv
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    one = sh.FlatStack(lay, [sh.Sheet(_rect(20, 20, 180, 80), 1.0)], MAIN)
    two = sh.FlatStack(lay, [sh.Sheet(_rect(20, 20, 100, 80), 1.0),
                             sh.Sheet(_rect(100, 20, 180, 80), 1.0)], MAIN)
    assert [s.lo for s in two.sheets] == [0.0, 0.0]
    kw = dict(yaw=0.0, pitch=0.0, labels=False)
    a, b = fv.render(one, 0.0, 600, 400, **kw), \
        fv.render(two, 0.0, 600, 400, **kw)
    cam = _camera(fv, one, 0.0, 0.0, turned=False)

    def bit(img, x, y):
        px, py = _pixel(cam, 600, 400, (x, y, 2.0))
        return [_rgb(img.pixelColor(int(px) + i, int(py) + j))
                for i in range(-8, 9) for j in range(-8, 9)]

    # the first piece is the same leather in both, grain for grain; the
    # second is cut from another bit of the hide
    assert bit(a, 60, 50) == bit(b, 60, 50)
    assert bit(a, 140, 50) != bit(b, 140, 50)
    assert math.dist(_patch(a, *_pixel(cam, 600, 400, (140, 50, 2.0))),
                     _patch(b, *_pixel(cam, 600, 400, (140, 50, 2.0)))) < 12


def test_a_sheet_slid_along_the_bench_is_painted_with_the_same_grain(qapp):
    from leathercad_app import foldview as fv
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    w, h = 600, 400
    cam = fv.Camera()
    cam.yaw = cam.pitch = 0.0
    cam.centre, cam.radius = (100.0, 50.0, 0.0), 110.0
    slide = 24.0 / cam.scale(w, h)             # 24 pixels along the bench

    def picture(dx):
        stack = sh.FlatStack(lay, [sh.Sheet(_rect(30 + dx, 30, 90 + dx, 70),
                                            1.0)], MAIN)
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        fv.paint_scene(p, w, h, fv.Scene(stack, 0.0), cam, fv.Look(),
                       labels=False)
        p.end()
        return img

    here, there = picture(0.0), picture(slide)
    x0, y0 = _pixel(cam, w, h, (36.0, 64.0, 2.0))
    x1, y1 = _pixel(cam, w, h, (84.0, 36.0, 2.0))
    spots = [(x, y) for x in range(int(x0), int(x1))
             for y in range(int(y0), int(y1))]
    assert len(spots) > 3000

    def alike(dx):
        return sum(math.dist(_rgb(here.pixelColor(x, y)),
                             _rgb(there.pixelColor(x + dx, y))) <= 2
                   for x, y in spots) / len(spots)

    # the same grain at the same place on the piece, 24 pixels along; and
    # not the same at one place on the bench: the piece does not slide
    # over its own grain
    assert alike(24) > 0.99
    assert alike(0) < 0.2


def test_the_shadow_of_a_piece_reaches_as_far_as_that_piece_is_thick(qapp):
    from leathercad_app import foldview as fv
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    w, h = 600, 400
    cam = _camera(fv, lay, 0.0, 0.0, turned=False)
    scale = cam.scale(w, h)
    shots = []
    for t in (0.4, 4.0):
        stack = sh.FlatStack(lay, [sh.Sheet(_rect(60, 20, 140, 80), t)],
                             MAIN)
        scene = fv.Scene(stack, 0.0)
        assert (scene.thickness_of(0), scene.thickness_of(1)) == (2.0, t)
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        fv.paint_scene(p, w, h, scene, cam, fv.Look(), labels=False)
        p.end()
        shots.append(img)
    thin, thick = shots
    # the shadow is a band along the edge, 1.6 times the thickness wide on
    # the screen, between 3 and 11 pixels: half of it falls outside
    assert 1.6 * 0.4 * scale < 3.0 and 10.0 < 1.6 * 4.0 * scale < 11.0
    px, py = _pixel(cam, w, h, (140.0, 50.0, 1.0))

    def lit(img, off):
        return sum(sum(_rgb(img.pixelColor(int(px) + off, int(py) + j)))
                   for j in (-6, 0, 6))

    # 3 and 4 pixels off the edge only the thick piece darkens the main
    # piece under it; 7 and more off, neither does
    for off in (3, 4):
        assert lit(thick, off) < lit(thin, off) - 6, off
    for off in (7, 9, 12):
        assert lit(thick, off) == lit(thin, off), off
    # ... and on the piece itself, away from its edge, they are the same
    assert lit(thick, -30) == lit(thin, -30)


# -- the piece under the mouse ------------------------------------------------

def _hit(face, o, v):
    """How far along ``v`` from ``o`` the line meets the face, or None."""
    nx, ny, nz, d = face.plane
    den = nx * v[0] + ny * v[1] + nz * v[2]
    if abs(den) < 1e-9:
        return None
    s = (d - (nx * o[0] + ny * o[1] + nz * o[2])) / den
    p = (o[0] + s * v[0], o[1] + s * v[1], o[2] + s * v[2])
    pts = face.pts
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        e = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        q = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
        c = (e[1] * q[2] - e[2] * q[1], e[2] * q[0] - e[0] * q[2],
             e[0] * q[1] - e[1] * q[0])
        if c[0] * nx + c[1] * ny + c[2] * nz < -1e-9:
            return None
    return s


def _in_view(cells, cam, w, h, x, y):
    """The piece whose face is nearest the eye on the line of sight
    through a pixel, by trying every face of every cell: ``(piece, how
    far it is in front of the next face)``, the piece -1 for none."""
    right, up, eye = cam.axes()
    s = cam.scale(w, h)
    dx, dy = (x - 0.5 * w - cam.pan_x) / s, -(y - 0.5 * h - cam.pan_y) / s
    o = tuple(c + dx * r + dy * u for c, r, u in zip(cam.centre, right, up))
    hits = []
    for cell in cells:
        for f in cell.faces:
            n = f.plane
            if f.kind == bsp.INNER \
                    or n[0] * eye[0] + n[1] * eye[1] + n[2] * eye[2] <= 1e-6:
                continue
            at = _hit(f, o, eye)
            if at is not None:
                hits.append((at, sh.sheet_of(cell.tag)))
    if not hits:
        return -1, 1.0
    hits.sort(reverse=True)
    return hits[0][1], (hits[0][0] - hits[1][0]) if len(hits) > 1 else 1.0


def test_the_piece_under_a_pixel_is_the_one_whose_face_is_nearest(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold()
    rng = random.Random(5)
    w, h = 700, 660
    tried = met = 0
    seen = set()
    views = ((0.0, 0.0, True, 0.0), (-0.45, 0.95, True, 0.0),
             (-0.5, 1.3, True, 6.0), (2.4, 2.2, True, 6.0),
             (0.8, 1.45, False, 3.0))
    for yaw, pitch, turned, apart in views:
        cells = stack.solid([], None, apart)
        scene = fv.Scene(stack, 0.0, None, apart)
        assert bsp.count(scene.tree)[1] == len(cells) == 27
        cam = _camera(fv, stack, yaw, pitch, turned, apart)
        # pixels in and just round the model's place in the picture
        lo, hi = stack.bounds3([], apart)
        box = [_pixel(cam, w, h, (x, y, z)) for x in (lo[0], hi[0])
               for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        x0, x1 = min(q[0] for q in box) - 15, max(q[0] for q in box) + 15
        y0, y1 = min(q[1] for q in box) - 15, max(q[1] for q in box) + 15
        for _ in range(100):
            x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
            want, clear = _in_view(cells, cam, w, h, x, y)
            if clear < 1e-6:
                continue                       # two faces in one place
            tried += 1
            met += want >= 0
            seen.add(want)
            assert fv.piece_at(scene, cam, w, h, x, y) == want, (x, y)
    assert tried == 500 and met > 250
    assert seen == set(range(-1, 10))          # every piece, and none
    # a piece shown alone is piece 0, and off it there is none
    lay = fs.fold_piece(MAIN, [fs.FoldLine((80, 0), (80, 100), 90, 1)])
    cam = _camera(fv, lay, -0.45, 0.95, turned=False)
    scene = fv.Scene(lay, 1.0)
    assert fv.piece_at(scene, cam, w, h,
                       *_pixel(cam, w, h, (140, 50, 1))) == 0
    assert fv.piece_at(scene, cam, w, h, 3, 3) == -1


def test_odd_pieces_on_both_faces_are_painted_and_picked_right(qapp):
    from leathercad_app import foldview as fv
    c, sn = math.cos(0.5), math.sin(0.5)
    lay = fs.fold_piece(MAIN, [fs.FoldLine((120, 0), (120, 100), 90, 1)],
                        thickness=2.0, holes=[(30, 30, 1.2), (60, 30, 1.2)])
    ell = [(20, 15), (110, 15), (110, 40), (60, 40), (60, 85), (20, 85)]
    sheets = [
        sh.Sheet(ell, 1.2, leather="Navy", holes=[(30, 30.4, 1.0)],
                 cutouts=[_rect(70, 20, 90, 30)],
                 marks=[("cut", [(25, 60), (55, 80)])]),
        sh.Sheet([(100 + x * c - y * sn, 50 + x * sn + y * c)
                  for x, y in _rect(-45, -12, 45, 12)], 0.8, grain=-1,
                 leather="Olive"),
        sh.Sheet(_rect(150, -10, 215, 60), 1.5, side=-1, leather="Black"),
        sh.Sheet(_rect(90, 20, 170, 80), 0.6, side=-1, grain=-1,
                 leather="Burgundy")]
    stack = sh.FlatStack(lay, sheets, MAIN)
    assert [s.rests for s in sheets] == [[0], [1], [0], [3]]
    assert [round(s.lo, 6) for s in sheets] == [0.0, 1.2, 0.0, 1.5]
    check = stack.lineup()
    assert check.holes(fs.MEETS) == {0, 2}
    cells = stack.solid([], check)
    assert len(cells) == len(lay.cells) + 5        # the L is two cells
    rng = random.Random(9)
    w, h = 600, 500
    seen = set()
    for yaw, pitch, turned in ((0.0, 0.0, False), (0.0, 0.0, True),
                               (-0.45, 0.95, False), (2.0, 2.4, False)):
        img = fv.render(stack, 0.0, w, h, yaw=yaw, pitch=pitch,
                        turned=turned, check=check)
        cam = _camera(fv, stack, yaw, pitch, turned)
        scene = fv.Scene(stack, 0.0, check)
        assert img.pixelColor(2, 2) == fv.Look().ground
        for _ in range(60):
            x, y = rng.uniform(60, w - 60), rng.uniform(60, h - 60)
            want, clear = _in_view(cells, cam, w, h, x, y)
            if clear > 1e-6:
                assert fv.piece_at(scene, cam, w, h, x, y) == want
                seen.add(want)
    assert seen == {-1, 0, 1, 2, 3, 4}
    # straight down on the front the L shows its grain and the strap that
    # lies on it, grain down, its flesh side; on the back it is the others
    cam = _camera(fv, stack, 0.0, 0.0, turned=False)
    top = fv.render(stack, 0.0, w, h, yaw=0.0, pitch=0.0, labels=False)
    assert math.dist(_patch(top, *_pixel(cam, w, h, (35, 70, 2.2))),
                     fv.LEATHERS["Navy"][0]) < 30
    assert math.dist(_patch(top, *_pixel(cam, w, h, (120, 60, 3.0))),
                     fv.LEATHERS["Olive"][1]) < 50
    cam = _camera(fv, stack, 0.0, 0.0, turned=True)
    under = fv.render(stack, 0.0, w, h, yaw=0.0, pitch=0.0, turned=True,
                      labels=False)
    assert math.dist(_patch(under, *_pixel(cam, w, h, (200, 20, -2.5))),
                     fv.LEATHERS["Black"][1]) < 50
    assert math.dist(_patch(under, *_pixel(cam, w, h, (120, 50, -3.1))),
                     fv.LEATHERS["Burgundy"][0]) < 30


# -- lit, and marked ----------------------------------------------------------

def _mixed(under, over, alpha):
    return tuple(u + (o - u) * alpha / 255.0 for u, o in zip(under, over))


def _off_ring(x, y, ring):
    """How far a pixel is from an outline drawn on the screen."""
    best = math.inf
    x0, y0 = ring[-1]
    for x1, y1 in ring:
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy)
                         / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - x0 - t * dx, y - y0 - t * dy))
        x0, y0 = x1, y1
    return best


def test_a_chosen_piece_has_the_pick_colour_round_it_and_keeps_its_leather(
        qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    w, h = 450, 300
    kw = dict(yaw=0.0, pitch=0.0, turned=True, labels=False,
              leather="Chestnut", leathers=_leathers())
    plain = fv.render(stack, 0.0, w, h, **kw)
    lit = fv.render(stack, 0.0, w, h, chosen=(4,), **kw)
    cam = _camera(fv, stack, 0.0, 0.0)
    pick = _rgb(fv.Look().pick)
    changed = [(x, y) for x in range(w) for y in range(h)
               if lit.pixel(x, y) != plain.pixel(x, y)]
    assert len(changed) > 500
    # every pixel that changed is along an edge of the chosen piece --
    # all the way round it, though only the strip above pocket 1 shows
    s = stack.sheets[3]
    ring = [_pixel(cam, w, h, (x, y, -(0.8 + s.hi))) for x, y in s.ring]
    assert all(_off_ring(x + 0.5, y + 0.5, ring) < 2.6 for x, y in changed)
    xs, ys = [q[0] for q in ring], [q[1] for q in ring]
    assert min(x for x, _y in changed) <= min(xs) < max(xs) \
        <= max(x for x, _y in changed) + 1
    assert min(y for _x, y in changed) <= min(ys) < max(ys) \
        <= max(y for _x, y in changed) + 1
    # the line is the pick colour itself, not that colour over the leather
    assert sum(math.dist(_rgb(lit.pixelColor(x, y)), pick) < 12
               for x, y in changed) > 250
    # ... and the piece keeps its leather: what shows of it is, away from
    # its edges, as it was, pixel for pixel
    px, py = _pixel(cam, w, h, _on_top(stack, 4))
    assert all(lit.pixel(int(px) + i, int(py) + j)
               == plain.pixel(int(px) + i, int(py) + j)
               for i in range(-40, 41) for j in range(-3, 4))
    assert math.dist(_patch(lit, px, py, 3), fv.LEATHERS[WORN[4]][0]) < 30
    # several at once, the main piece among them (seen from under)
    under = dict(kw, yaw=math.pi, pitch=math.pi)
    both = fv.render(stack, 0.0, w, h, chosen=(0, 4), **under)
    bare = fv.render(stack, 0.0, w, h, **under)
    cam = _camera(fv, stack, math.pi, math.pi)
    px, py = _pixel(cam, w, h, (50.0, 20.0, 0.8))
    assert _patch(both, px, py, 2) == _patch(bare, px, py, 2)
    # (the pocket, which lies behind the shell from here, shows through
    # it: faintly, along its top edge 1 mm below the middle)
    px, py = _pixel(cam, w, h, (50.0, -1.0, 0.8))
    assert _patch(both, px, py, 2) != _patch(bare, px, py, 2)
    edge = _pixel(cam, w, h, (50.0, 45.0, 0.8))    # the shell's top edge
    assert any(math.dist(_rgb(both.pixelColor(int(edge[0]),
                                              int(edge[1]) + j)), pick) < 12
               for j in (-2, -1, 0, 1, 2))


def test_a_chosen_piece_shows_through_what_lies_over_it(qapp):
    from leathercad_app import foldview as fv
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    under = sh.Sheet(_rect(20, 20, 120, 80), 1.0, leather="Navy")
    over = sh.Sheet(_rect(60, 40, 180, 90), 1.0, leather="Olive")
    stack = sh.FlatStack(lay, [under, over], MAIN)
    assert over.rests == [1]
    w, h = 600, 400
    kw = dict(yaw=0.0, pitch=0.0, labels=False)
    plain = fv.render(stack, 0.0, w, h, **kw)
    lit = fv.render(stack, 0.0, w, h, chosen=(1,), **kw)
    cam = _camera(fv, stack, 0.0, 0.0, turned=False)
    pick = _rgb(fv.Look().pick)

    def most_like_pick(img, x, y):
        px, py = _pixel(cam, w, h, (x, y, 3.0))
        return min(math.dist(_rgb(img.pixelColor(int(px) + i, int(py))), pick)
                   for i in (-2, -1, 0, 1, 2))

    # its right edge, 120 mm across: in sight below the piece that lies
    # over it, and under that piece further up
    assert most_like_pick(lit, 120, 30) < 12 < most_like_pick(plain, 120, 30)
    seen = most_like_pick(lit, 120, 60)
    was = most_like_pick(plain, 120, 60)
    # there the line is drawn too, but faintly: the olive leather over it
    # shows through the pick colour
    assert 40 < seen < was - 40
    # ... and where it is not chosen nothing of it shows through
    px, py = _pixel(cam, w, h, (120, 60, 3.0))
    assert math.dist(_patch(plain, px, py, 2), fv.LEATHERS["Olive"][0]) < 30
    assert fv.render(stack, 0.0, w, h, chosen=(2,), **kw).pixelColor(
        int(px), int(py)) == plain.pixelColor(int(px), int(py))


def test_a_mark_is_seen_for_what_it_is_on_every_leather(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    w, h = 450, 300
    look = fv.Look()
    cam = _camera(fv, stack, 0.0, 0.0)
    grains = {name: rgb[0] for name, rgb in fv.LEATHERS.items()}
    kw = dict(yaw=0.0, pitch=0.0, turned=True, labels=False)
    at = _pixel(cam, w, h, _on_top(stack, 4))      # on left pocket 2
    other = _pixel(cam, w, h, _on_top(stack, 8))   # on right pocket 2
    for name in fv.LEATHERS:
        for colour in (look.warn, look.bad):
            img = fv.render(stack, 0.0, w, h, leather="Chestnut",
                            leathers={4: name}, wash={(4, None): colour},
                            **kw)
            seen = _patch(img, *at, 3)
            # nearer the mark's own colour than any leather there is, and
            # not to be taken for one: laid thinly, red over black leather
            # is burgundy leather and amber over dark brown is chestnut
            nearest = min(math.dist(seen, g) for g in grains.values())
            assert nearest > 30, (name, _rgb(colour), seen)
            assert math.dist(seen, _rgb(colour)) < nearest, (name, seen)
        # a piece that is chosen as well as marked red is still red, with
        # the pick colour round it: not the amber of another mark
        img = fv.render(stack, 0.0, w, h, leather=name, chosen=(4,),
                        wash={(4, None): look.bad, (8, None): look.warn},
                        **kw)
        red, amber = _patch(img, *at, 3), _patch(img, *other, 3)
        assert math.dist(red, amber) > 50, name
        assert math.dist(red, _rgb(look.bad)) < math.dist(red,
                                                          _rgb(look.warn))
        assert math.dist(amber, _rgb(look.warn)) < math.dist(
            amber, _rgb(look.bad))
        # ... and a piece that is only chosen is still its own leather
        img = fv.render(stack, 0.0, w, h, leather="Chestnut",
                        leathers={4: name}, chosen=(4,), **kw)
        seen = _patch(img, *at, 3)
        assert min(grains, key=lambda n: math.dist(seen, grains[n])) == name
        assert math.dist(seen, grains[name]) < 30, (name, seen)


def test_a_mark_covers_the_lines_between_the_facets_of_a_cut_edge(qapp):
    from leathercad_app import foldview as fv
    # a round of thick leather seen from the side: its cut edge is 36
    # facets, with a fine line of the edge's colour where two of them meet
    disc = [(60 + 30 * math.cos(math.radians(a)),
             50 + 30 * math.sin(math.radians(a))) for a in range(0, 360, 10)]
    lay = fs.fold_piece(disc, [], thickness=20.0)
    w, h = 500, 300
    kw = dict(yaw=0.0, pitch=0.5 * math.pi, labels=False)
    blue = QColor(0, 0, 255, 254)
    plain = fv.render(lay, 0.0, w, h, **kw)
    marked = fv.render(lay, 0.0, w, h, wash={(0, None): blue}, **kw)
    cam = _camera(fv, lay, 0.0, 0.5 * math.pi, turned=False)
    x0, y = _pixel(cam, w, h, (32.0, 20.0, 0.0))
    x1, _y = _pixel(cam, w, h, (88.0, 20.0, 0.0))
    row = [_rgb(marked.pixelColor(x, int(y)))
           for x in range(int(x0), int(x1))]
    assert len(row) > 100
    # along the middle of the edge every pixel is the mark's blue: none
    # is left the brown of the leather's edge
    assert all(r < 12 and g < 12 and b > 240 for r, g, b in row)
    assert all(_rgb(plain.pixelColor(x, int(y)))[0] > 60
               for x in range(int(x0), int(x1)))


def test_a_wash_marks_a_whole_piece_or_what_it_has_in_one_bend(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    w, h = 900, 600
    look = fv.Look()
    kw = dict(yaw=0.0, pitch=0.0, turned=True, labels=False,
              leather="Chestnut", leathers=_leathers())
    plain = fv.render(stack, 0.0, w, h, **kw)
    red = fv.render(stack, 0.0, w, h, wash={(4, None): look.bad}, **kw)
    cam = _camera(fv, stack, 0.0, 0.0)
    px, py = _pixel(cam, w, h, _on_top(stack, 4))
    want = _mixed(_patch(plain, px, py), _rgb(look.bad), fv._WASH)
    assert math.dist(_patch(red, px, py), want) < 4
    for k in (3, 5, 8):                        # and nothing on the others
        spot = _pixel(cam, w, h, _on_top(stack, k))
        assert _patch(red, *spot) == _patch(plain, *spot)
    # a colour that says how strong it is to be is taken at its word
    faint = QColor(look.bad)
    faint.setAlpha(40)
    pale = fv.render(stack, 0.0, w, h, wash={(4, None): faint}, **kw)
    assert math.dist(_patch(pale, px, py),
                     _mixed(_patch(plain, px, py), _rgb(look.bad), 40)) < 4
    # what the shell has in the bend of fold A (its fold is cut ready, 2.5
    # mm wide, though it is not made): amber there, and nowhere else
    assert look.warn != look.pick and look.warn != look.bad
    under = dict(kw, yaw=math.pi, pitch=math.pi, zoom=3.0)
    plain = fv.render(stack, 0.0, w, h, **under)
    bend = fv.render(stack, 0.0, w, h, wash={(0, 0): look.warn}, **under)
    cam = _camera(fv, stack, math.pi, math.pi, zoom=3.0)
    px, py = _pixel(cam, w, h, (0.75, 20.0, 0.8))
    assert math.dist(_patch(bend, px, py, 1),
                     _mixed(_patch(plain, px, py, 1), _rgb(look.warn),
                            fv._WASH)) < 5
    px, py = _pixel(cam, w, h, (8.0, 20.0, 0.8))
    assert _patch(bend, px, py, 1) == _patch(plain, px, py, 1)
    # the whole shell, and its bend over that
    whole = fv.render(stack, 0.0, w, h, wash={(0, None): look.bad}, **under)
    assert _patch(whole, px, py, 1) != _patch(plain, px, py, 1)


def _shows(img, x, y, colour, reach=3, tol=45) -> bool:
    """Whether a picture has ``colour``, or near it, within ``reach``
    pixels of a spot."""
    want = _rgb(colour)
    return any(math.dist(_rgb(img.pixelColor(int(x) + dx, int(y) + dy)),
                         want) < tol
               for dx in range(-reach, reach + 1)
               for dy in range(-reach, reach + 1))


def test_a_hole_that_comes_through_is_ringed_quietly_on_the_piece(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold()
    check = stack.lineup()
    w, h = 900, 600
    kw = dict(yaw=0.0, pitch=0.0, turned=True, labels=False, zoom=2.0,
              leather="Chestnut", leathers=_leathers())
    plain = fv.render(stack, 0.0, w, h, **kw)
    marked = fv.render(stack, 0.0, w, h, check=check, **kw)
    cam = _camera(fv, stack, 0.0, 0.0, zoom=2.0)
    scale = cam.scale(w, h)
    look = fv.Look(False, "Natural")
    ink = _rgb(look.ink(fv.FRONT))
    # the holes of the shell's top row come through the liner, above the
    # pockets: each has a ring on the liner, of the liner's own ink
    top = [s for s in check.passed[1] if s[1] > 40 and 10 < s[0] < 80]
    assert len(top) >= 15
    r = 0.5 * max(4.4, 1.35 * max(1.6, 1.0 * scale)) + 1.2
    for x, y, _dia in top:
        cx, cy = _pixel(cam, w, h, (x, y, -1.6))
        ring = [(cx + r * math.cos(a), cy + r * math.sin(a))
                for a in (0.0, 1.57, 3.14, 4.71)]
        for qx, qy in ring:
            was = _rgb(plain.pixelColor(int(qx), int(qy)))
            now = _rgb(marked.pixelColor(int(qx), int(qy)))
            # darker there, towards the ink
            assert sum(now) < sum(was) - 60, (x, y)
            assert math.dist(now, ink) < math.dist(was, ink)
        # the liner has no hole there: inside the ring it is as it was
        assert marked.pixelColor(int(cx), int(cy)) \
            == plain.pixelColor(int(cx), int(cy))
        # ... and nothing about it has the colour of a fault
        for state in (fs.MEETS, fs.OFF, fs.LONE):
            assert not _shows(marked, cx, cy, fv.hole_colour(state),
                              reach=int(r) + 2), (x, y, state)
    # from under, on the shell, the holes are plain holes: nothing is
    # marked on the piece they are cut in
    under = dict(kw, yaw=math.pi, pitch=math.pi)
    assert fv.render(stack, 0.0, w, h, check=check, **under) \
        == fv.render(stack, 0.0, w, h, **under)


# -- pulled apart, and the bench from above -----------------------------------

def test_pulling_apart_lifts_the_layers_and_cuts_nothing(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold()
    view = fv.FoldView()
    view.resize(700, 660)
    view.set_layout(stack)
    assert view.t == 0.0 and view.apart == 0.0 and view.scene.apart == 0.0
    flat = view.cam.radius
    assert abs(flat - 0.5 * math.dist(*stack.bounds3([]))) < 1e-9
    view.set_apart(5.0)
    lo, hi = view.scene.bounds()
    # the top pocket is five layers up: 25 mm above where it lay
    assert abs(lo[2] + (0.8 + 4.0 + 25.0)) < 1e-9 and hi[2] == 0.8
    assert bsp.count(view.scene.tree)[1] == 27
    assert abs(view.cam.centre[2] + 14.5) < 1e-9 and view.cam.radius > flat
    assert view.follow
    # the marks go up with the leather they are on
    assert view.scene.folds[0][1][2][2] == -(0.8 + 0.8 + 5.0)
    view.set_apart(0.0)
    assert view.cam.radius == flat and view.scene.apart == 0.0
    # there is no folding of a stack to step through yet
    view.set_time(1.0)
    assert view.t == 0.0
    # a piece alone has no layers: nothing changes
    lay = fs.fold_piece(MAIN, [fs.FoldLine((80, 0), (80, 100), 90, 1)])
    view.set_layout(lay, refit=True)
    radius, tree = view.cam.radius, view.scene.tree
    view.set_apart(9.0)
    assert view.scene.apart == 0.0 and view.scene.tree is tree
    assert view.cam.radius == radius
    view.set_time(1.0)
    assert view.t == 1.0 and view.scene.apart == 0.0
    # ... and a stack shown after it is shown as far apart as was asked
    view.set_layout(stack, refit=True)
    assert view.scene.apart == 9.0
    # pictures: pulled apart is another picture, framed to hold it all
    kw = dict(yaw=-0.5, pitch=1.3, turned=True, labels=False)
    apart = fv.render(stack, 0.0, 500, 400, apart=6.0, **kw)
    assert apart != fv.render(stack, 0.0, 500, 400, **kw)
    ground = fv.Look().ground
    assert all(apart.pixelColor(x, y) == ground
               for x in range(0, 500, 7) for y in (2, 397))


def test_a_stack_turned_like_a_page_shows_the_bench_from_above(qapp):
    from leathercad_app import foldview as fv
    cam = fv.Camera()
    assert cam.turned is False
    cam.yaw = cam.pitch = 0.0
    assert cam.axes() == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    cam.turned = True
    # the eye is over the back of the pattern, and its right is on the left
    assert cam.axes() == ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                          (0.0, 0.0, -1.0))
    cam.radius = 100.0
    ox, ax, ay, az, oy, bx, by, bz = cam.mapping(600, 400)
    assert (ox, oy) == (300.0, 200.0) and ax < 0 and by < 0
    assert (ay, az, bx, bz) == (0.0, 0.0, 0.0, 0.0)
    # at any turn of the view it is the same view of the model turned half
    # round its upright: right, up and the eye all turned with it
    rng = random.Random(2)
    for _ in range(20):
        cam.yaw, cam.pitch = rng.uniform(-4, 4), rng.uniform(0, math.pi)
        cam.turned = False
        plain = cam.axes()
        cam.turned = True
        assert cam.axes() == tuple((-x, y, -z) for x, y, z in plain)
        r, u, e = cam.axes()
        cross = (r[1] * u[2] - r[2] * u[1], r[2] * u[0] - r[0] * u[2],
                 r[0] * u[1] - r[1] * u[0])
        assert math.dist(cross, e) < 1e-12           # still right-handed
    # the set views: top is front; under is the stack turned like a page,
    # so that for a stack that lies turned it is the pattern as drawn
    view = fv.FoldView()
    for where, yaw, pitch in (("top", 0.0, 0.0), ("front", 0.0, 0.0),
                              ("under", math.pi, math.pi),
                              ("back", 0.0, math.pi),
                              ("side", 0.0, 0.5 * math.pi),
                              ("iso", -0.45, 0.95)):
        view.look_from(where)
        assert (view.cam.yaw, view.cam.pitch) == (yaw, pitch), where
    view.look_from("under")
    assert math.dist(view.cam.axes()[0], (-1, 0, 0)) < 1e-12
    assert math.dist(view.cam.axes()[1], (0, 1, 0)) < 1e-12
    view.set_turned(True)
    assert view.cam.turned
    assert math.dist(view.cam.axes()[0], (1, 0, 0)) < 1e-12
    assert math.dist(view.cam.axes()[2], (0, 0, 1)) < 1e-12
    # the light is the eye's: the flesh side of a piece is lit the same
    # seen turned from above as it is seen from the back
    lay = fs.fold_piece(MAIN, [])
    turned = fv.render(lay, 0.0, 400, 300, yaw=0.0, pitch=0.0, turned=True,
                       labels=False)
    back = fv.render(lay, 0.0, 400, 300, yaw=0.0, pitch=math.pi,
                     labels=False)
    assert math.dist(_patch(turned, 200, 150, 30),
                     _patch(back, 200, 150, 30)) < 3
    assert math.dist(_patch(turned, 200, 150, 30), fv.Look().back) < 50


# -- the view on screen -------------------------------------------------------

def _view(stack, w=700, h=660):
    from leathercad_app import foldview as fv
    view = fv.FoldView()
    view.resize(w, h)
    view.show()
    QApplication.processEvents()
    view.set_turned(True)
    view.set_layout(stack)
    view.look_from("top")
    view.repaint()
    return view


def test_clicking_a_piece_picks_it_and_a_fold_line_only_where_it_shows(qapp):
    stack = _bifold(holes=False)
    view = _view(stack)
    try:
        w, h = view.width(), view.height()
        picked, folds = [], []
        view.piecePicked.connect(lambda k, add: picked.append((k, add)))
        view.foldPicked.connect(folds.append)

        def click(p, mods=Qt.NoModifier):
            x, y = _pixel(view.cam, w, h, p)
            QTest.mouseClick(view, Qt.LeftButton, mods, QPoint(int(x), int(y)))
            return int(x), int(y)

        x, y = click(_on_top(stack, 4))
        assert view.piece_at(x, y) == 4
        assert picked == [(4, False)] and folds == [-1]
        click(_on_top(stack, 7), Qt.ControlModifier)      # one more
        click(_on_top(stack, 1), Qt.ShiftModifier)
        assert picked[1:] == [(7, True), (1, True)]
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))
        assert picked[-1] == (-1, False) and view.piece_at(4, 4) == -1
        assert folds == [-1] * 4
        # on the fold line, which lies in sight on the liner between the
        # pocket stacks, it is the fold that is picked and no piece
        del picked[:], folds[:]
        x, y = click((0.0, 10.0, -1.6))
        assert view.fold_at(x, y) == 0 and view.piece_at(x, y) == 1
        assert folds == [0] and picked == []
        # a drag turns the view and picks nothing
        mid = QPoint(w // 2, h // 2)
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, mid)
        QTest.mouseMove(view, mid + QPoint(40, 20))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier,
                           mid + QPoint(40, 20))
        assert folds == [0] and picked == [] and view.cam.yaw != 0.0
        # Pulled apart and seen from the side, the line on the shell's
        # underside is behind everything, and the line on the liner is
        # behind the pockets for part of its way. Near a stretch that is
        # out of sight a click is for the piece that is there.
        view.set_apart(6.0)
        view.cam.yaw, view.cam.pitch = -0.5, 1.3
        view.repaint()
        assert len(view._drawn) == 4 and view._seen
        assert all(s[0] == 0 for s in view._seen)

        def off(x, y, x0, y0, x1, y1):
            dx, dy = x1 - x0, y1 - y0
            t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy)
                             / (dx * dx + dy * dy)))
            return math.hypot(x - x0 - t * dx, y - y0 - t * dy)

        hidden = []
        for _f, x0, y0, x1, y1 in view._drawn:
            for i in range(41):
                x, y = x0 + (x1 - x0) * i / 40, y0 + (y1 - y0) * i / 40
                if all(off(x, y, *s[1:]) > 9.0 for s in view._seen) \
                        and view.piece_at(x, y) > 0:
                    hidden.append((x, y))
        assert len(hidden) > 10
        del picked[:], folds[:]
        for x, y in hidden[::5]:
            assert view.fold_at(x, y) == -1
            QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier,
                             QPoint(int(x), int(y)))
            assert folds[-1] == -1
            assert picked[-1] == (view.piece_at(int(x), int(y)), False)
            assert picked[-1][0] > 0
        # where it is in sight the line is still to be had
        _f, x0, y0, x1, y1 = view._seen[0]
        assert view.fold_at(0.5 * (x0 + x1), 0.5 * (y0 + y1)) == 0
    finally:
        view.close()


def test_a_fold_line_out_of_sight_still_picks_on_a_piece_shown_alone(qapp):
    # as it always did: the fold is lit through whatever hides its line
    from leathercad_app import foldview as fv
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1, label="A"),
             fs.FoldLine((160, 0), (160, 100), 180, 1, order=2, label="B")]
    lay = fs.fold_piece([(0, 0), (240, 0), (240, 100), (0, 100)], folds,
                        base=1)
    view = fv.FoldView()
    view.resize(700, 660)
    view.show()
    QApplication.processEvents()
    try:
        view.set_layout(lay, 2.0)
        view.look_from("front")
        view.repaint()
        assert len(view._drawn) == 4
        picked, got = [], []
        view.piecePicked.connect(lambda k, add: picked.append((k, add)))
        view.foldPicked.connect(got.append)
        for fold, x0, y0, x1, y1 in view._drawn:
            x, y = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
            assert view.fold_at(x, y) in (0, 1)
        # folded in three and seen from the front both bends are edge on:
        # no stretch of either line is in sight
        assert view._seen == []
        _f, x0, y0, x1, y1 = next(d for d in view._drawn if d[0] == 0)
        at = QPoint(int(0.5 * (x0 + x1)), int(0.5 * (y0 + y1)))
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, at)
        assert got == [view.fold_at(at.x(), at.y())] and got[0] >= 0
        assert picked == []
        # off the lines, in the middle of it, the piece itself
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier,
                         QPoint(350, 330))
        assert got[-1] == -1 and picked == [(0, False)]
    finally:
        view.close()


def test_the_view_is_told_each_pieces_leather_by_its_number_or_its_key(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    view = _view(stack)
    try:
        w, h = view.width(), view.height()
        assert view.look.leather == fv.DEFAULT_LEATHER == "Tan"
        assert [view.leather_of(k) for k in range(10)] == ["Tan"] * 10
        liner, pocket = stack.sheets[0], stack.sheets[4]
        view.set_leathers({0: "Chestnut", liner.key: "Natural", 5: "Olive",
                           pocket.key: "Black", 7: "Snakeskin"})
        assert view.leather_of(0) == "Chestnut"
        assert view.leather_of(1) == "Natural"
        assert view.leather_of(5) == "Olive"      # its number, not its key
        assert view.leather_of(7) == view.leather_of(3) == "Tan"
        img = view.grab().toImage()
        ratio = img.width() / w

        def seen(k):
            x, y = _pixel(view.cam, w, h, _on_top(stack, k))
            return _patch(img, x * ratio, y * ratio)

        assert math.dist(seen(1), fv.LEATHERS["Natural"][0]) < 30
        assert math.dist(seen(5), fv.LEATHERS["Olive"][0]) < 30
        assert math.dist(seen(3), fv.LEATHERS["Tan"][0]) < 30
        # a Look is made for each leather in use, once, and no other
        assert sorted(view._looks) == ["Chestnut", "Natural", "Olive"]
        olive = view._looks["Olive"]
        view.repaint()
        assert view._looks["Olive"] is olive
        # the usual leather is the view's own look, as it was: a piece in
        # that leather needs no look of its own
        view.set_leather("Natural")
        view.repaint()
        assert view.look.leather == "Natural"
        assert sorted(view._looks) == ["Chestnut", "Olive"]
        assert view.leather_of(1) == view.leather_of(3) == "Natural"
        view.set_leathers(None)
        view.repaint()
        assert view._looks == {} and view.leather_of(0) == "Natural"
        # the key under the model shows the leather of the chosen piece
        view.set_leathers({5: "Olive", 2: "Black"})
        view.set_chosen([5, 2])
        assert view.chosen == (5, 2)
        img = view.grab().toImage()

        def swatch():                 # the first of the two, bottom left
            return _rgb(img.pixelColor(int(18 * ratio), int((h - 20) * ratio)))

        assert swatch() == fv.LEATHERS["Olive"][0]
        view.set_chosen(())
        img = view.grab().toImage()
        assert swatch() == fv.LEATHERS["Natural"][0]
        view.set_wash({(2, None): view.look.bad})
        assert view.wash == {(2, None): view.look.bad}
        assert view.grab().toImage() != img
    finally:
        view.close()


def test_a_stack_with_no_sheets_is_painted_as_its_piece_is(qapp):
    from leathercad_app import foldview as fv
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1, label="A"),
             fs.FoldLine((160, 0), (160, 100), 90, -1, order=2, label="B")]
    holes = [(20 + 5 * k, 10, 1.0) for k in range(10)] \
        + [(140 - 5 * k, 10.6, 1.0) for k in range(10)]
    lay = fs.fold_piece([(0, 0), (240, 0), (240, 100), (0, 100)], folds,
                        base=1, holes=holes)
    stack = sh.FlatStack(lay)
    for t in (0.0, 1.3, 2.0):
        assert fv.render(stack, t, 500, 400, check=stack.lineup()) \
            == fv.render(lay, t, 500, 400, check=lay.lineup())
    scene = fv.Scene(stack, 1.0, None, 7.0)
    assert scene.sheets == () and scene.apart == 0.0
    assert scene.bounds() == lay.bounds3(lay.fractions(1.0))
    assert scene.face_of(0, True) == fv.FRONT
    assert scene.face_of(0, False) == fv.BACK


# -- fold lines, in sight or not ----------------------------------------------

def _lines_judged(fv, stack, yaw, pitch, apart, w=700, h=660):
    """The fold lines of a picture, pixel by pixel of their length, each
    point tried against every face of the model: ``(pixels in sight,
    pixels drawn though a face is in front, pixels in sight and not
    drawn)``."""
    scene = fv.Scene(stack, 0.0, None, apart)
    cells = stack.solid([], None, apart)
    cam = _camera(fv, stack, yaw, pitch, True, apart)
    eye = cam.axes()[2]
    faces = [f for c in cells for f in c.faces if f.kind != bsp.INNER
             and sum(n * e for n, e in zip(f.plane[:3], eye)) > 1e-6]
    seen = []
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    fv.paint_scene(p, w, h, scene, cam, fv.Look(), labels=False, seen=seen)
    p.end()
    sight = through = missing = 0
    for fold, marks in scene.folds.items():
        for mark in marks:
            a, b, a2, b2, nn = mark[:5]
            facing = sum(n * e for n, e in zip(nn, eye))
            for q, r, side in ((a, b, facing), (a2, b2, -facing)):
                if side <= 0.02:
                    continue
                (x0, y0), (x1, y1) = _pixel(cam, w, h, q), _pixel(cam, w, h, r)
                ln = math.hypot(x1 - x0, y1 - y0)
                if ln < 1.0:
                    continue
                runs = []
                for _f, u0, v0, u1, v1 in seen:
                    if abs((u0 - x0) * (y1 - y0) - (v0 - y0) * (x1 - x0)) \
                            < 0.05 * ln and abs((u1 - x0) * (y1 - y0)
                                                - (v1 - y0) * (x1 - x0)) \
                            < 0.05 * ln:
                        t0 = ((u0 - x0) * (x1 - x0) + (v0 - y0) * (y1 - y0)) \
                            / (ln * ln)
                        t1 = ((u1 - x0) * (x1 - x0) + (v1 - y0) * (y1 - y0)) \
                            / (ln * ln)
                        runs.append((min(t0, t1), max(t0, t1)))
                for i in range(int(ln) + 1):
                    t = (i + 0.5) / (int(ln) + 1)
                    at = tuple(q[k] + (r[k] - q[k]) * t for k in range(3))
                    hidden = any((_hit(f, at, eye) or 0.0) > 2e-3
                                 for f in faces)
                    drawn = any(t0 <= t <= t1 for t0, t1 in runs)
                    sight += not hidden
                    through += drawn and hidden
                    missing += not hidden and not drawn
    return sight, through, missing


def test_a_fold_line_of_a_stack_is_cut_off_behind_a_piece_to_the_pixel(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    total = 0
    # the stack seen from one side and from the other, flat and pulled
    # apart: the line on the liner runs behind the pockets for part of
    # its way, and pulled apart 3 mm only 16 px of it are in sight
    for yaw, pitch, apart in ((-0.45, 0.95, 0.0), (0.6, 0.9, 0.0),
                              (-0.45, 0.95, 1.0), (-0.45, 0.95, 3.0),
                              (-0.5, 1.3, 6.0)):
        sight, through, missing = _lines_judged(fv, stack, yaw, pitch, apart)
        assert sight >= 15, (yaw, apart)
        # (by eighths of its length it was up to 9 px drawn through a
        # pocket here, and 16 px in sight left out)
        assert through <= 1 and missing <= 1, (yaw, apart, through, missing)
        total += sight
    assert total > 350
    # By itself: a line 80 mm long, and a square standing 5 mm in front
    # of it from 30.3 to 51 mm along. Tried at the middle of each eighth
    # of its length, an eighth goes with its middle ...
    m = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)   # the screen is x, y
    eye = (0.0, 0.0, 1.0)
    square = [(30.3, -5.0), (51.0, -5.0), (51.0, 5.0), (30.3, 5.0)]
    discs = [(40.0, 0.0, 400.0, 6.0, [((0.0, 0.0, 1.0, 5.0), 1.0, square)])]
    q, r = (0.0, 0.0, 0.0), (80.0, 0.0, 0.0)
    assert fv._in_sight(q, r, 8, False, discs, eye, m) == [(0.0, 0.375),
                                                           (0.625, 1.0)]
    # ... and looked for finely, where it passes out of sight is found
    (a0, a1), (b0, b1) = fv._in_sight(q, r, 8, True, discs, eye, m)
    assert (a0, b1) == (0.0, 1.0)
    assert abs(80 * a1 - 30.3) < 0.7 and abs(80 * b0 - 51.0) < 0.7
    assert fv._in_sight(q, r, 8, True, [], eye, m) == [(0.0, 1.0)]
    # a piece shown alone keeps its lines as they were, by eighths; it is
    # a stack's that are tried every few pixels, and finely
    asked = []
    real = fv._in_sight

    def spy(q, r, pieces, fine, *rest):
        asked.append((pieces, fine))
        return real(q, r, pieces, fine, *rest)

    try:
        fv._in_sight = spy
        fv.render(fs.fold_piece(MAIN, [fs.FoldLine((80, 0), (80, 100), 90,
                                                   1)]), 0.5, 600, 400)
        assert asked and set(asked) == {(8, False)}
        del asked[:]
        fv.render(stack, 0.0, 700, 660, turned=True)
        assert asked and all(fine and pieces >= 8 for pieces, fine in asked)
        assert max(pieces for pieces, _fine in asked) > 20
    finally:
        fv._in_sight = real


# -- the key under the model --------------------------------------------------

class _Told:
    """A painter that does as a ``QPainter`` does, and keeps the words it
    is given to write and the colour of each path it is given to draw."""

    def __init__(self, painter):
        self._painter = painter
        self.words = []
        self.paths = []

    def __getattr__(self, name):
        return getattr(self._painter, name)

    def drawText(self, *args):
        self.words.append(args[-1])
        return self._painter.drawText(*args)

    def drawPath(self, path):
        self.paths.append(_rgb(self._painter.pen().color()))
        return self._painter.drawPath(path)


def _legend(view):
    """The words of the key under a view's model, and its first swatch."""
    from leathercad_app import foldview as fv
    w, h = view.width(), view.height()
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(view.look.ground)
    p = QPainter(img)
    told = _Told(p)
    view._paint_legend(told, w, h, fv.sheet_looks(
        view.layout_, view.look, view._leathers))
    p.end()
    return told.words, _rgb(img.pixelColor(18, h - 20))


def test_the_key_of_a_stack_shows_what_is_in_the_picture(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold()
    view = _view(stack)
    try:
        view.caption = ""
        hint = "Drag to turn · right-drag to slide · wheel to zoom"
        # a flat stack: its fold is left flat, and drawn grey -- no fold
        # line to the front or to the back is in the picture
        words, swatch = _legend(view)
        assert words == [hint, "grain side", "flesh side", "fold left flat"]
        assert swatch == fv.LEATHERS["Tan"][0]
        # the two swatches are the main piece's leather until a piece is
        # chosen, then that piece's
        view.set_leathers({0: "Navy", 4: "Olive"})
        assert _legend(view)[1] == fv.LEATHERS["Navy"][0]
        view.set_chosen([4, 0])
        assert _legend(view)[1] == fv.LEATHERS["Olive"][0]
        view.set_chosen(())
        view.set_leathers(None)
        # the holes: all that the check of a flat bifold finds is holes of
        # the shell coming through the other pieces
        check = stack.lineup()
        assert not check.state and check.blanks
        view.set_layout(stack, check=check)
        words, _swatch = _legend(view)
        assert words[-2:] == ["Stitch holes:",
                              "another piece's hole comes through"]
        view.show_check(False)
        assert _legend(view)[0][-1] == "fold left flat"
        # A stack whose folds are made has the two kinds of line there
        # are. (No stack folds yet: here one is told that it does.)
        stack.steps = [1]
        view.set_layout(stack)
        assert _legend(view)[0] == [hint, "grain side", "flesh side",
                                    "folds to the front"]
        stack.folds[0].direction = -1
        assert _legend(view)[0][-1] == "folds to the back"
    finally:
        view.close()


def test_the_key_of_a_piece_shown_alone_is_as_it_was(qapp):
    from leathercad_app import foldview as fv
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1, label="A"),
             fs.FoldLine((160, 0), (160, 100), 180, 1, order=2, label="B")]
    holes = [(20 + 5 * k, 10, 1.0) for k in range(10)] \
        + [(140 - 5 * k, 10.6, 1.0) for k in range(10)]
    lay = fs.fold_piece([(0, 0), (240, 0), (240, 100), (0, 100)], folds,
                        base=1, holes=holes)
    view = fv.FoldView()
    view.resize(900, 600)
    try:
        view.set_layout(lay)
        view.caption = "Flat"
        words, swatch = _legend(view)
        assert words == [
            "Flat", "Drag to turn · right-drag to slide · wheel to zoom",
            "front of the leather", "back", "folds to the front",
            "folds to the back"]
        assert swatch == fv.LEATHERS["Tan"][0]
        view.set_layout(lay, check=lay.lineup())
        assert _legend(view)[0][6:] == [
            "Stitch holes, folded:", "line up", "out of line",
            "where the other hole is", "no hole to meet"]
        # its leather is laid where the pattern puts it, not shifted as a
        # sheet's is: straight down on it, a pixel to each pixel of the
        # tile, the picture is the tile itself
        flat = fs.fold_piece(MAIN, [])
        cam = fv.Camera()
        cam.yaw = cam.pitch = 0.0
        cam.centre = (100.0, 50.0, 0.0)
        cam.radius = 0.46 * 400 * (80.0 / 256)     # 3.2 pixels to a mm
        look = fv.Look()
        img = QImage(640, 400, QImage.Format_RGB32)
        p = QPainter(img)
        fv.paint_scene(p, 640, 400, fv.Scene(flat, 0.0), cam, look,
                       labels=False)
        p.end()
        assert cam.scale(640, 400) == 3.2
        tile = look.texture(fv.FRONT, 23)          # lit from straight above
        # the pattern's (0, 0) is at pixel (0, 360): its y runs up the
        # picture, and the tile with it
        assert _pixel(cam, 640, 400, (0.0, 0.0, 1.0)) == (0.0, 360.0)
        for x, y in ((40, 300), (200, 200), (301, 77), (500, 150),
                     (620, 340)):
            want = _rgb(tile.pixelColor(x % 256, (360 - y) % 256))
            assert math.dist(_rgb(img.pixelColor(x, y)), want) < 24, (x, y)
    finally:
        view.close()


# -- each piece in its own leather, down to its lines -------------------------

def test_the_lines_of_a_piece_are_in_its_own_leather(qapp):
    from leathercad_app import foldview as fv
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    ell = [(20, 20), (120, 20), (120, 40), (60, 40), (60, 80), (20, 80)]
    stack = sh.FlatStack(lay, [sh.Sheet(ell, 1.0, leather="Navy")], MAIN)
    assert len(stack.solid([])) == 3               # the L is in two parts
    w, h = 600, 400
    cam = _camera(fv, stack, 0.0, 0.0, turned=False)
    look = fv.Look(False, "Natural")
    looks = fv.sheet_looks(stack, look)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    told = _Told(p)
    fv.paint_scene(told, w, h, fv.Scene(stack, 0.0), cam, look, labels=False,
                   looks=looks)
    p.end()
    # the seam between the two parts of the L is laid under them in the
    # colour of its own leather, blue, not that of the piece it lies on
    assert len(told.paths) == 1
    r, g, b = told.paths[0]
    assert b > r + 20 and math.dist((r, g, b), fv.LEATHERS["Navy"][0]) < 40
    # ... and the line along its edge is its own leather's too: dark on
    # the pale main piece, where the main piece's own line is brown
    px, py = _pixel(cam, w, h, (70.0, 20.0, 2.0))  # on its bottom edge
    edge = min((_rgb(img.pixelColor(int(px), int(py) + j))
                for j in (-2, -1, 0, 1, 2)), key=sum)
    assert math.dist(edge, _rgb(looks[1].edge)) < 35
    assert math.dist(edge, _rgb(look.edge)) > 40
    # in a dark window a dark piece has the pale line a dark leather is
    # given there, whatever the usual leather is
    dark = fv.sheet_looks(stack, fv.Look(True, "Natural"))[1]
    assert dark.dark and dark.pale_edge and sum(_rgb(dark.edge)) > 250
    assert not looks[1].dark and sum(_rgb(looks[1].edge)) < 80
    # A fold that is made is drawn in the colour for the leather it lies
    # on: light blue on the bifold's black liner, where the strong blue
    # of a pale leather would be lost. (No stack folds yet: this one is
    # told that it does.)
    stack = _bifold(holes=False)
    stack.steps = [1]
    cam = _camera(fv, stack, 0.0, 0.0)
    img = fv.render(stack, 0.0, 900, 600, yaw=0.0, pitch=0.0, turned=True,
                    labels=False, leathers={1: "Black"})
    px, py = _pixel(cam, 900, 600, (0.0, 20.0, -1.6))      # in the trench
    line = [_rgb(img.pixelColor(int(px) + i, int(py))) for i in (-1, 0, 1)]
    assert min(math.dist(c, (110, 176, 255)) for c in line) < 30
    assert min(math.dist(c, (24, 104, 232)) for c in line) > 60


def test_the_cut_edge_of_thin_dark_layers_is_not_all_pale_lines(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    leathers = {0: "Black", 1: "Navy", **{k: "Burgundy" for k in range(2, 10)}}
    w, h = 700, 660
    burgundy = fv.Look(True, "Burgundy")
    assert burgundy.pale_edge and not fv.Look(False, "Burgundy").pale_edge
    pale, wall = _rgb(burgundy.edge), burgundy.wall

    def down_the_edge(zoom, dark=True):
        """The pixels down the cut edge of the left pile, at its front."""
        img = fv.render(stack, 0.0, w, h, dark=dark, leather="Black",
                        leathers=leathers, yaw=0.0, pitch=1.25, turned=True,
                        labels=False, zoom=zoom)
        cam = _camera(fv, stack, 0.0, 1.25, zoom=zoom)
        x, y0 = _pixel(cam, w, h, (50.0, -45.0, -4.8))
        _x, y1 = _pixel(cam, w, h, (50.0, -45.0, -1.6))
        return cam.scale(w, h), [_rgb(img.pixelColor(int(x), y))
                                 for y in range(int(min(y0, y1)) + 1,
                                                int(max(y0, y1)))]

    def is_wall(c):
        return min(math.dist(c, tuple(v * k for v in wall))
                   for k in (0.6, 0.7, 0.8, 0.9, 1.0)) < 25

    # four layers of 0.8 mm, 2.2 pixels each: the line along each edge of
    # each would be half of what shows. They are drawn faint: most of the
    # edge is the burgundy of the leather, and none of it the pale line
    scale, edge = down_the_edge(1.0)
    assert 0.8 * scale < fv._THIN and len(edge) == 7
    assert not any(math.dist(c, pale) < 30 for c in edge)
    assert sum(is_wall(c) for c in edge) >= 5
    # closer to, with 4.4 pixels to a layer, each has its pale line again
    scale, edge = down_the_edge(2.0)
    assert 0.8 * scale > fv._THIN
    assert sum(math.dist(c, pale) < 30 for c in edge) >= 3
    # and in a light window the lines are the dark ones they always were
    _scale, edge = down_the_edge(1.0, dark=False)
    dark_line = _rgb(fv.Look(False, "Burgundy").edge)
    assert sum(math.dist(c, dark_line) < 30 for c in edge) >= 2


# -- told once, kept until told otherwise -------------------------------------

def test_alt_click_picks_the_next_piece_down_under_the_pointer(qapp):
    stack = _bifold(holes=False)
    view = _view(stack)
    try:
        w, h = view.width(), view.height()
        picked = []
        view.piecePicked.connect(lambda k, add: picked.append((k, add)))
        x, y = _pixel(view.cam, w, h, _on_top(stack, 5))   # on left pocket 1
        at = QPoint(int(x), int(y))
        # under the pointer: pocket 1, pocket 2, pocket 3, their backing,
        # the liner and the shell, in that order from the eye
        assert view.pieces_at(at.x(), at.y()) == [5, 4, 3, 2, 1, 0]
        assert view.piece_at(at.x(), at.y()) == 5
        assert view.pieces_at(3, 3) == [] and view.piece_at(3, 3) == -1

        def click(mods, where=at):
            QTest.mouseClick(view, Qt.LeftButton, mods, where)
            return picked[-1]

        assert click(Qt.NoModifier) == (5, False)
        # Alt+click: the one under it, then the next, and round again
        assert [click(Qt.AltModifier)[0] for _ in range(7)] \
            == [4, 3, 2, 1, 0, 5, 4]
        assert click(Qt.AltModifier | Qt.ControlModifier) == (3, True)
        # a plain click is the one in view again, and Alt starts from it
        assert click(Qt.NoModifier) == (5, False)
        assert click(Qt.AltModifier) == (4, False)
        # somewhere else it starts afresh: above the pockets, from the
        # liner down to the shell under it, and back
        x, y = _pixel(view.cam, w, h, _on_top(stack, 1))
        gap = QPoint(int(x), int(y))
        assert view.pieces_at(gap.x(), gap.y()) == [1, 0]
        assert [click(Qt.AltModifier, gap)[0] for _ in range(3)] == [0, 1, 0]
        # with one piece under the pointer, or none, Alt changes nothing
        x, y = _pixel(view.cam, w, h, (50.0, 44.0, -0.8))
        off = QPoint(int(x), int(y))
        assert view.pieces_at(off.x(), off.y()) == [0]
        assert click(Qt.AltModifier, off) == (0, False)
        assert click(Qt.AltModifier, QPoint(3, 3)) == (-1, False)
    finally:
        view.close()


def test_a_piece_is_under_the_pointer_once_however_many_cells_it_is_in(qapp):
    from leathercad_app import foldview as fv
    # a piece folded shut on itself: from above, both its panels are
    # under a point of it, and it is one piece there all the same
    lay = fs.fold_piece(MAIN, [fs.FoldLine((100, 0), (100, 100), 180, 1)],
                        thickness=2.0)
    w, h = 400, 300
    cam = fv.Camera()
    cam.centre, cam.radius = lay.extent()
    cam.yaw = cam.pitch = 0.0
    scene = fv.Scene(lay, 1.0)
    frames = lay.frames(lay.fractions(1.0))
    spots = [fs.place(frames[i], (50.0 + 100 * i, 50.0), 0.0)
             for i in (0, 1)]
    (x0, y0), (x1, y1) = (_pixel(cam, w, h, p) for p in spots)
    # (the two panels lie one over the other: a point of each, 50 mm from
    # the fold, shows within a few pixels of the other)
    assert math.hypot(x1 - x0, y1 - y0) < 12
    assert abs(spots[0][2] - spots[1][2]) > 2.0
    assert fv.pieces_at(scene, cam, w, h, x0, y0) == [0]
    assert fv.piece_at(scene, cam, w, h, x0, y0) == 0
    # and so is a sheet whose outline is kept in several parts, seen from
    # the side through two arms of it
    arms = [(40, 20), (160, 20), (160, 80), (130, 80), (130, 40), (70, 40),
            (70, 80), (40, 80)]
    stack = sh.FlatStack(fs.fold_piece(MAIN, [], thickness=2.0),
                         [sh.Sheet(arms, 6.0)], MAIN)
    assert len(stack.sheets[0].parts) > 1
    scene = fv.Scene(stack, 0.0)
    cam = fv.Camera()
    cam.centre, cam.radius = stack.extent()
    most = 0
    for yaw in (0.0, 0.8, 1.6, 2.4):
        cam.yaw, cam.pitch = yaw, 1.45
        for x in range(0, w, 4):
            for y in range(0, h, 4):
                under = fv.pieces_at(scene, cam, w, h, x, y)
                assert len(set(under)) == len(under), (yaw, x, y, under)
                assert under in ([], [0], [1], [0, 1], [1, 0])
                most = max(most, len(under))
    assert most == 2


def test_what_the_view_is_told_of_a_stack_it_keeps_for_the_next_model(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    lone = fs.fold_piece(MAIN, [fs.FoldLine((80, 0), (80, 100), 90, 1)])
    fresh = fv.FoldView()
    fresh.resize(500, 400)
    fresh.set_layout(lone, 1.0)
    view = fv.FoldView()
    view.resize(500, 400)
    try:
        red = view.look.bad
        view.set_turned(True)
        view.set_layout(stack)
        view.set_leathers({0: "Black", stack.sheets[0].key: "Navy"})
        view.set_chosen([0, 1])
        view.set_wash({(0, None): red})
        view.set_apart(4.0)
        # a piece shown alone after it is piece 0 of all that
        view.set_layout(lone, 1.0, refit=True)
        assert view.cam.turned and view.chosen == (0, 1)
        assert view.wash == {(0, None): red} and view.apart == 4.0
        assert view.leather_of(0) == "Black"
        assert view.scene.apart == 0.0             # (it has no layers)
        assert view.grab().toImage() != fresh.grab().toImage()
        # ... until the view is told otherwise, of each
        view.set_turned(False)
        view.set_leathers(None)
        view.set_chosen(())
        view.set_wash(None)
        assert view.grab().toImage() == fresh.grab().toImage()
        assert view.leather_of(0) == "Tan"
        # the main piece of a stack can be told its leather by its key, as
        # a sheet can; its number wins if both are given
        named = sh.FlatStack(stack.layout, stack.sheets, stack.outline,
                             key="shell-1", leather="Olive")
        look = fv.Look()
        assert fv.sheet_looks(named, look)[0].leather == "Olive"
        by_key = {s.key: "Navy" for s in named.sheets}
        by_key["shell-1"] = "Navy"
        assert [lk.leather for lk in fv.sheet_looks(named, look,
                                                    by_key).values()] \
            == ["Navy"] * 10
        by_key[0] = "Black"
        assert fv.sheet_looks(named, look, by_key)[0].leather == "Black"
        assert 0 not in fv.sheet_looks(stack, look, {"shell-1": "Navy"})
    finally:
        view.close()
        fresh.close()


def test_a_picture_of_a_stack_pulled_apart_is_framed_round_all_of_it(qapp):
    from leathercad_app import foldview as fv
    stack = _bifold(holes=False)
    w, h = 500, 400
    got = fv.render(stack, 0.0, w, h, yaw=-0.5, pitch=1.3, turned=True,
                    labels=False, apart=6.0)

    def painted(cam):
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        fv.paint_scene(p, w, h, fv.Scene(stack, 0.0, None, 6.0), cam,
                       fv.Look(), labels=False)
        p.end()
        return img

    # centred on the box round the layers as they stand, and big enough
    # for it: not on the stack as it lies flat
    assert got == painted(_camera(fv, stack, -0.5, 1.3, apart=6.0))
    assert got != painted(_camera(fv, stack, -0.5, 1.3))
    lo, hi = stack.bounds3([], 6.0)
    assert _near_centre(got, fv.Look().ground)
    assert lo[2] == -(0.8 + 4.0 + 30.0) and hi[2] == 0.8


def _near_centre(img, ground) -> bool:
    """Whether what is painted in a picture has its middle within a few
    pixels of the picture's."""
    xs, ys = [], []
    for x in range(0, img.width(), 2):
        for y in range(0, img.height(), 2):
            if img.pixelColor(x, y) != ground:
                xs.append(x)
                ys.append(y)
    return abs(0.5 * (min(xs) + max(xs)) - 0.5 * img.width()) < 12 \
        and abs(0.5 * (min(ys) + max(ys)) - 0.5 * img.height()) < 12

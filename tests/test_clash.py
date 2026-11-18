"""The judge of a model: how far two cells go into each other, measured
against cases worked by hand, and a flat stack tried by it -- nothing in a
stack may be drawn through anything else."""

import math
import random

from leathercad import bsp
from leathercad import clash
from leathercad import foldsolid as fs
from leathercad import polycut as pc
from leathercad import sheets as sh
from leathercad.generators import bifold_wallet
from leathercad.stitching import holes_for_shape

X, Y, Z = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
HALF = [(0, 0), (160, 0), (160, 100), (0, 100)]


def _box(o, ex=X, ey=Y, ez=Z, sx=1.0, sy=1.0, sz=1.0, tag=None):
    """A box with a corner at ``o``, unit axes and sizes, as a cell."""
    def at(a, b, c):
        return (o[0] + ex[0] * a * sx + ey[0] * b * sy + ez[0] * c * sz,
                o[1] + ex[1] * a * sx + ey[1] * b * sy + ez[1] * c * sz,
                o[2] + ex[2] * a * sx + ey[2] * b * sy + ez[2] * c * sz)
    quads = [((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
             ((0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 0)),
             ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
             ((1, 1, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1)),
             ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
             ((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1))]
    faces = []
    for k, q in enumerate(quads):
        pts = tuple(at(*c) for c in q)
        kind = bsp.FRONT if k == 0 else bsp.BACK if k == 1 else bsp.WALL
        faces.append(bsp.Face(pts, bsp.plane_of(pts), kind, (True,) * 4, None,
                              tag, sx * sy if k < 2 else sz))
    return bsp.Cell(faces, tag)


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _ring(shape):
    return [(p.x, p.y) for p in shape.world_polyline()[0]]


def _bifold():
    """The generated bifold put together (see tests/test_sheets.py)."""
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
                               side=-1, grain=-1, name=shape.name))

    lay(liner, "middle")
    for stack, where in ((parts[3:7], "left"), (parts[7:11], "right")):
        for p in reversed(stack):
            lay(p, where)
    ends = spine.world_polyline()[0]
    fold = fs.FoldLine((ends[0].x, ends[0].y), (ends[-1].x, ends[-1].y),
                       180.0, 1, label="A")
    holes = [(h.point.x, h.point.y, 1.0)
             for h in holes_for_shape(shell, None).holes]
    layout = fs.fold_piece(outline, [fold], thickness=1.6, holes=holes)
    return sh.FlatStack(layout, sheets, outline)


def _near(a, b, tol=1e-9):
    return abs(a - b) <= tol


# -- how deep two cells go into each other ------------------------------------

def test_boxes_that_touch_do_not_clash_and_0_2_mm_in_is_0_2_mm():
    a = _box((0, 0, 0), sx=10, sy=6, sz=2)
    assert clash.depth(a, _box((10, 0, 0), sx=10, sy=6, sz=2)) == 0.0
    assert clash.depth(a, _box((0, 0, 2), sx=10, sy=6, sz=2)) == 0.0
    assert clash.depth(a, _box((30, 0, 0), sx=10, sy=6, sz=2)) == 0.0
    assert clash.depth(a, _box((10.5, 6.5, 2.5))) == 0.0   # corner to corner
    # 0.2 mm into it along x, though well into it along y and z
    b = _box((9.8, 1, 0.5), sx=10, sy=6, sz=2)
    assert _near(clash.depth(a, b), 0.2) and _near(clash.depth(b, a), 0.2)
    # a sheet lying 0.3 mm down into the one under it
    assert _near(clash.depth(a, _box((2, 1, 1.7), sx=5, sy=3, sz=1)), 0.3)
    # one inside the other: as far as it would have to go to get out
    assert _near(clash.depth(a, _box((2, 1, 0.5), sx=5, sy=3, sz=1)), 1.5)
    assert _near(clash.depth(a, a), 2.0)


def _twisted(gap):
    """A cube of side 2 about the origin, and another turned so that one
    of its edges lies ``gap`` mm from an edge of the first, across it."""
    r = math.sqrt(0.5)
    e1, e2, e3 = (r, -r, 0.0), (0.5, 0.5, r), (-0.5, -0.5, r)
    d = 2.0 * math.sqrt(2.0) + gap
    corner = tuple(d * n - a - b - c
                   for n, a, b, c in zip((r, r, 0.0), e1, e2, e3))
    return _box((-1, -1, -1), sx=2, sy=2, sz=2), \
        _box(corner, e1, e2, e3, 2, 2, 2)


def _along(n, a, b):
    """How much two cells overlap, looked at along one direction."""
    pa = [n[0] * x + n[1] * y + n[2] * z for x, y, z in a.verts]
    pb = [n[0] * x + n[1] * y + n[2] * z for x, y, z in b.verts]
    return min(max(pa), max(pb)) - max(min(pa), min(pb))


def test_two_edges_that_cross_are_parted_only_by_the_plane_between_them():
    a, b = _twisted(0.05)
    # no face of either parts them: looked at along any of their normals
    # they overlap by more than half a millimetre
    normals = [f.plane[:3] for f in a.faces + b.faces]
    assert min(_along(n, a, b) for n in normals) > 0.5
    # ... but they are 0.05 mm apart, edge to edge
    assert clash.depth(a, b) == 0.0
    assert _near(_along((math.sqrt(0.5), math.sqrt(0.5), 0.0), a, b), -0.05)
    # pushed 0.2 mm into each other, that is how deep they go
    a, b = _twisted(-0.2)
    assert _near(clash.depth(a, b), 0.2) and _near(clash.depth(b, a), 0.2)
    assert min(_along(n, a, b) for n in normals) > 0.5
    assert clash.depth(*_twisted(0.0)) < 1e-9


def test_a_cell_with_a_corner_pushed_out_is_not_convex():
    good = _box((0, 0, 0))
    assert clash.convex(good) == 0.0
    # the same box with its corner at (1, 1, 1) pulled 0.3 mm out along
    # x: the face x = 1 keeps its plane, and the corner stands in front
    faces = []
    for f in good.faces:
        pts = tuple((1.3, 1.0, 1.0) if p == (1.0, 1.0, 1.0) else p
                    for p in f.pts)
        faces.append(bsp.Face(pts, f.plane, f.kind))
    assert _near(clash.convex(bsp.Cell(faces)), 0.3)
    # every cell the fold engine makes is convex, at every moment
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1,
                                           radius=1.5)])
    for t in (0.0, 0.3, 0.7, 1.0):
        assert max(clash.convex(c) for c in lay.solid([t])) < 1e-9


# -- every pair of a model ----------------------------------------------------

def test_the_cells_of_one_folded_piece_touch_and_do_not_clash():
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 90, 1,
                                           radius=2.0)])
    for t in (0.0, 0.3, 0.6, 1.0):
        assert clash.clashes(lay.solid([t])) == []
    # folded flat with no radius the flap lies on the panel: faces touch
    flat = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                         thickness=2.0)
    cells = flat.solid([1.0])
    assert clash.clashes(cells) == []
    # a 3 mm patch drawn where the two lie: its middle is the panel's, so
    # it is 2.5 mm from clear of the panel (1 + 1.5), and it stands 0.5 mm
    # up into the flap, which lies from 1 to 3 mm
    patch, = fs.fold_piece(_rect(20, 20, 60, 80), [], thickness=3.0).solid([])
    patch.tag = ("patch",)
    found = clash.clashes(cells + [patch])
    assert [round(d, 9) for d, _a, _b in found] == [2.5, 0.5]
    assert [{a, b} for _d, a, b in found] == [{("patch",), ("panel", 0)},
                                              {("patch",), ("panel", 1)}]
    # what is let pass is told to it
    assert len(clash.clashes(cells + [patch], 0.6)) == 1
    assert clash.clashes(cells + [patch], 2.5) == []
    assert clash.clashes([]) == [] and clash.clashes([patch]) == []


def test_the_flat_bifold_goes_into_the_tree_uncut_and_nothing_clashes():
    stack = _bifold()
    for apart in (0.0, 0.5, 6.0):
        cells = stack.solid([0.0], None, apart)
        assert len(cells) == 27
        assert bsp.count(bsp.build(cells))[1] == len(cells)
        assert clash.clashes(cells) == []
        assert max(clash.convex(c) for c in cells) < 1e-9


def test_a_level_forced_wrong_is_found():
    # the control: the judge must say so when a sheet is not on its level
    stack = _bifold()
    backing = stack.sheets[1]
    assert (backing.name, backing.lo) == ("Left pocket backing", 0.8)
    backing.lo, backing.hi = 0.0, 0.8          # down into the liner
    cells = stack.solid([0.0])
    (d, a, b), = clash.clashes(cells)
    assert _near(d, 0.8) and {a, b} == {("panel", 1, 1), ("panel", 1, 2)}
    assert bsp.count(bsp.build(cells))[1] > len(cells)     # and it is cut
    backing.lo, backing.hi = 0.4, 1.2          # half way down into it
    (d, _a, _b), = clash.clashes(stack.solid([0.0]))
    assert _near(d, 0.4)
    # two sheets that overlap by 0.04 mm are on two levels; left on one
    # level they share that 0.04 mm, which is more than is let pass
    lay = fs.fold_piece(HALF, [], thickness=2.0)
    pair = [sh.Sheet(_rect(10, 10, 80, 90), 1.0),
            sh.Sheet(_rect(79.96, 10, 150, 90), 1.0)]
    stack = sh.FlatStack(lay, pair, HALF)
    assert pair[1].lo == 1.0 and clash.clashes(stack.solid([])) == []
    pair[1].lo, pair[1].hi = 0.0, 1.0
    (d, a, b), = clash.clashes(stack.solid([]))
    assert _near(d, 0.04) and {a, b} == {("panel", 0, 1), ("panel", 0, 2)}
    assert clash.JUDGE < 0.04
    # ... while sheets that butt, or share less than counts as lying on
    # each other, are left on one level and pass
    for over in (0.0, 0.5 * sh.LAP):
        pair = [sh.Sheet(_rect(10, 10, 80, 90), 1.0),
                sh.Sheet(_rect(80 - over, 10, 150, 90), 1.0)]
        stack = sh.FlatStack(lay, pair, HALF)
        assert pair[1].lo == 0.0 and clash.clashes(stack.solid([])) == []


def _rounded(x0, y0, x1, y1, r, n=8):
    pts = []
    for cx, cy, a0 in ((x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90),
                       (x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270)):
        for k in range(n + 1):
            a = math.radians(a0 + 90.0 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _turned(pts, deg, cx, cy):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(cx + (x - cx) * c - (y - cy) * s,
             cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def _any_sheet(rng, W, H, k):
    w, h = rng.uniform(15, W * 1.05), rng.uniform(10, H * 1.05)
    x, y = rng.uniform(-8, W - w + 8), rng.uniform(-8, H - h + 8)
    shape = rng.choice(["rect", "rect", "round", "turn", "ell", "notch"])
    if shape == "rect":
        ring = _rect(x, y, x + w, y + h)
    elif shape == "round":
        ring = _turned(_rounded(x, y, x + w, y + h, min(3.0, w / 3, h / 3)),
                       rng.uniform(0, 180), x + w / 2, y + h / 2)
    elif shape == "turn":
        ring = _turned(_rect(x, y, x + w, y + h), rng.uniform(-40, 40),
                       x + w / 2, y + h / 2)
    elif shape == "ell":
        nw, nh = w * rng.uniform(0.3, 0.7), h * rng.uniform(0.3, 0.7)
        ring = [(x, y), (x + w, y), (x + w, y + nh), (x + nw, y + nh),
                (x + nw, y + h), (x, y + h)]
    else:
        r = min(w, h) * rng.uniform(0.15, 0.4)
        ring = [(x, y), (x + w, y), (x + w, y + h)]
        ring += [(x + w / 2 + r * math.cos(math.pi * i / 8),
                  y + h - r * math.sin(math.pi * i / 8)) for i in range(9)]
        ring.append((x, y + h))
    return sh.Sheet(ring, rng.choice([0.4, 0.6, 0.8, 1.0, 1.4, 2.0, 2.5]),
                    side=rng.choice([1, 1, -1]), grain=rng.choice([1, -1]),
                    name=f"s{k}")


def _plan(cell):
    """A flat cell as it lies in plan, and the heights it lies between."""
    top = max((f for f in cell.faces if abs(f.plane[2]) > 0.5),
              key=lambda f: f.plane[2])
    zs = [v[2] for v in cell.verts]
    return [p[:2] for p in top.pts], min(zs), max(zs)


def test_random_flat_stacks_are_drawn_through_nothing():
    rng = random.Random(11)
    most = 0
    for _trial in range(60):
        W, H = rng.uniform(120, 240), rng.uniform(60, 110)
        r = rng.choice([0, 0, 4, 10])
        main = _rounded(0, 0, W, H, r) if r else _rect(0, 0, W, H)
        t = rng.choice([1.0, 1.6, 2.0, 3.0])
        lay = fs.fold_piece(main, [], thickness=t)
        sheets = [_any_sheet(rng, W, H, k)
                  for k in range(rng.randint(1, 7))]
        stack = sh.FlatStack(lay, sheets, main)
        for apart in (0.0, 3.0):
            cells = stack.solid([], None, apart)
            # no two cells share space, cells of one piece included, and
            # every cell is a convex solid
            assert clash.clashes(cells) == []
            assert max(clash.convex(c) for c in cells) < 1e-9
        cells = stack.solid([])
        by_piece = {}
        for c in cells:
            by_piece.setdefault(sh.sheet_of(c.tag), []).append(_plan(c))
        for k, s in enumerate(sheets, 1):
            # a sheet's cells cover it once
            area = sum(pc.area(ring) for ring, _z0, _z1 in by_piece[k])
            assert abs(area - pc.area(s.ring)) < 2e-3 * pc.area(s.ring)
            # ... and it touches each thing it rests on: its underside is
            # on the other's top, somewhere they are over each other
            for on in s.rests:
                met = False
                for ring, z0, z1 in by_piece[k]:
                    for other, o0, o1 in by_piece[on]:
                        gap = z0 - o1 if s.side > 0 else o0 - z1
                        if abs(gap) < 1e-9 \
                                and pc.convex_overlap(ring, other, sh.LAP):
                            met = True
                assert met, (k, on)
            assert s.rests or s.loose
        most = max(most, max(s.rank for s in sheets))
    assert most >= 4                           # some of them piled high

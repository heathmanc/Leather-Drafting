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


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def test_a_tilted_slab_is_parted_from_a_corner_only_by_its_own_face():
    # A cube of 10 mm, and a slab tilted two ways -- its face is turned
    # the way (2, 3, 6) points -- that lies beyond the cube's corner at
    # (10, 10, 10). The cells of a stack that is folded are tilted like
    # this, and nothing parts the two but the slab's own face
    box = _box((0, 0, 0), sx=10, sy=10, sz=10)
    n = (2 / 7, 3 / 7, 6 / 7)
    ex = (3 / math.sqrt(13), -2 / math.sqrt(13), 0.0)
    ey = _cross(n, ex)

    def slab(gap):
        o = tuple(10 + gap * a - 20 * b - 20 * c
                  for a, b, c in zip(n, ex, ey))
        return _box(o, ex, ey, n, 40, 40, 3)

    far = slab(0.5)
    assert _near(_along(n, box, far), -0.5)
    # looked at along a face of the cube, or square to an edge of each,
    # they overlap: by 3 mm and more
    others = [X, Y, Z] + [_cross(u, v) for u in (X, Y, Z)
                          for v in (ex, ey, n)]
    assert min(_along(tuple(c / math.hypot(*axis) for c in axis), box, far)
               for axis in others) > 3.0
    # 0.5 mm apart, and corner to face, they do not clash: whichever of
    # the two is asked about first
    for gap in (0.5, 0.0):
        assert clash.depth(box, slab(gap)) < 1e-9
        assert clash.depth(slab(gap), box) < 1e-9
    assert clash.clashes([box, far]) == [] == clash.clashes([far, box])
    # the corner 0.2 mm into the slab is 0.2 mm deep
    assert _near(clash.depth(box, slab(-0.2)), 0.2)
    assert _near(clash.depth(slab(-0.2), box), 0.2)


def _wedge():
    """A wedge 10 mm long along y: its back at x = 0, 6 mm high, and two
    faces that slope to meet at its edge, x = 4 -- one turned up and
    along, (0.6, 0, 0.8), one down and along, (0.6, 0, -0.8)."""
    def at(x, z):
        return [(x, 0.0, z), (x, 10.0, z)]
    (b0, b1), (t0, t1), (e0, e1) = at(0, -3), at(0, 3), at(4, 0)
    rings = [(b0, b1, t1, t0),                 # the back
             (t0, t1, e1, e0),                 # sloping up
             (b1, b0, e0, e1),                 # sloping down
             (b0, e0, t0), (b1, t1, e1)]       # the two ends
    faces = []
    for pts in rings:
        plane = bsp.plane_of(pts)
        # (counter-clockwise from outside: turn round any that is not)
        if plane[0] * 1.5 + plane[1] * 5.0 - plane[3] > 0.0:
            pts = pts[::-1]
            plane = bsp.plane_of(pts)
        faces.append(bsp.Face(tuple(pts), plane, bsp.WALL))
    return bsp.Cell(faces)


def test_two_faces_that_differ_only_up_and_down_are_two_ways_to_look():
    wedge = _wedge()
    assert clash.convex(wedge) < 1e-12
    normals = sorted(tuple(round(c, 9) for c in f.plane[:3])
                     for f in wedge.faces)
    assert (0.6, 0.0, 0.8) in normals and (0.6, 0.0, -0.8) in normals
    # a cube with its corner 0.5 mm clear of the face that slopes down:
    # the plane of that face parts them, and no other does
    box = _box((2.3, 2.0, -7.9), sx=6, sy=6, sz=6)
    assert _near(_along((0.6, 0.0, -0.8), wedge, box), -0.5)
    assert _along((0.6, 0.0, 0.8), wedge, box) > 1.0
    assert min(_along(axis, wedge, box) for axis in (X, Y, Z)) > 1.0
    assert clash.depth(wedge, box) == 0.0 == clash.depth(box, wedge)
    # 0.2 mm into that face it is 0.2 mm deep
    near = _box((2.3 - 0.7 * 0.6, 2.0, -7.9 + 0.7 * 0.8), sx=6, sy=6, sz=6)
    assert _near(clash.depth(wedge, near), 0.2)


def _rod(tilt=0.0, up=0.0):
    """A rod 300 mm long and 2 mm square along x, standing on one of its
    long edges, tilted ``tilt`` radians about the upright and lifted
    ``up`` mm."""
    r = math.sqrt(0.5)
    c, s = math.cos(tilt), math.sin(tilt)
    ex, ey, ez = (c, 0.0, -s), (r * s, r, r * c), (-r * s, r, -r * c)
    o = tuple(-150 * a - b - d + lift
              for a, b, d, lift in zip(ex, ey, ez, (0.0, up, 0.0)))
    return _box(o, ex, ey, ez, 300, 2, 2)


def test_two_long_cells_a_hair_apart_edge_along_edge_do_not_clash():
    # One rod lying on the other, edge on edge, 0.000 001 mm between
    # them, the upper turned a millionth of a radian about the upright:
    # the two edges all but run alike, and nothing parts the rods but the
    # plane between those two edges
    low, high = _rod(), _rod(9.9e-7, 2.0 * math.sqrt(2.0) + 1e-6)
    top = max(v[1] for v in low.verts)
    assert _near(top, math.sqrt(2.0), 1e-12)
    assert _near(min(v[1] for v in high.verts) - top, 1e-6, 1e-9)
    normals = [f.plane[:3] for f in low.faces + high.faces]
    assert min(_along(n, low, high) for n in normals) > clash.JUDGE
    assert clash.depth(low, high) == 0.0 == clash.depth(high, low)
    assert clash.clashes([low, high]) == []
    # pushed 0.2 mm into each other they are 0.2 x sin 45 deep, as far as
    # either has to move along a face of its own to come clear
    deep = clash.depth(low, _rod(9.9e-7, 2.0 * math.sqrt(2.0) - 0.2))
    assert abs(deep - 0.2 * math.sqrt(0.5)) < 2e-4
    # and exactly along each other, a hair apart, they do not clash either
    assert clash.depth(low, _rod(0.0, 2.0 * math.sqrt(2.0) + 1e-6)) == 0.0


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
    # Pushed 0.3 mm IN instead, each face still carrying the plane it
    # had: no corner is in front of any of those. But the face at x = 1
    # is no longer flat, and two of its own corners stand in front of the
    # plane through its four
    faces = []
    for f in good.faces:
        pts = tuple((0.7, 1.0, 1.0) if p == (1.0, 1.0, 1.0) else p
                    for p in f.pts)
        faces.append(bsp.Face(pts, f.plane, f.kind))
    dented = bsp.Cell(faces)
    assert max(n[0] * x + n[1] * y + n[2] * z - n[3]
               for n in (f.plane for f in dented.faces)
               for x, y, z in dented.verts) == 0.0
    assert 0.05 < clash.convex(dented) < 0.3
    # it is a length, to hold against a tolerance: 0.0 is the convex one
    assert not clash.convex(good) < 0.0 and clash.convex(dented) > 1e-7
    # a box with a face left out is no solid: its five faces add up to
    # the one that is missing, 1 mm2, over the root of their 5 mm2
    assert _near(clash.convex(bsp.Cell(good.faces[1:])), math.sqrt(0.2))
    # ... nor is one with a face turned inside out
    top = good.faces[0]
    flipped = bsp.Face(top.pts[::-1], (0.0, 0.0, -1.0, -1.0), top.kind)
    assert clash.convex(bsp.Cell([flipped] + good.faces[1:])) >= 1.0
    # every cell the fold engine makes is convex, at every moment
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1,
                                           radius=1.5)])
    for t in (0.0, 0.3, 0.7, 1.0):
        assert max(clash.convex(c) for c in lay.solid([t])) < 1e-9


def test_a_face_that_carries_a_wrong_plane_is_told_and_does_not_mislead():
    good = _box((0, 0, 0))
    # the face at x = 1, and the same carrying a plane of half length
    side = good.faces[4]
    assert side.plane == (1.0, 0.0, 0.0, 1.0)

    def with_plane(plane):
        return bsp.Cell([bsp.Face(f.pts, plane if f is side else f.plane,
                                  f.kind) for f in good.faces])

    half = with_plane((0.5, 0.0, 0.0, 0.5))
    # no corner stands in front of it; but it is half a mm out, at the
    # far corners of the cell, from the plane through the face's corners
    assert _near(clash.convex(half), 0.5)
    # the judge looks along the normal a face carries, made a unit
    # length: 0.2 mm into that face is 0.2 mm, not 0.1
    other = _box((0.8, 0.2, 0.2), sx=3, sy=3, sz=3)
    assert _near(clash.depth(half, other), 0.2)
    assert _near(clash.depth(other, half), 0.2)
    # a face that carries no plane at all, or one turned otherwise, is
    # told the same way; and none of them raises
    nought = with_plane((0.0, 0.0, 0.0, 0.0))
    assert _near(clash.convex(nought), 1.0)
    assert clash.depth(nought, _box((1.5, 0, 0))) == 0.0
    tilted = with_plane((0.8, 0.6, 0.0, 0.8))
    assert clash.convex(tilted) > 0.5
    assert clash.convex(good) == 0.0
    # a sheet of a stack has a wall for each edge of its outline, each
    # with its own plane, of unit length
    lay = fs.fold_piece(HALF, [], thickness=2.0)
    stack = sh.FlatStack(lay, [sh.Sheet(_rounded(20, 20, 120, 80, 8), 1.0)],
                         HALF)
    cell = stack.solid([])[-1]
    walls = [f for f in cell.faces if f.kind == bsp.WALL]
    assert len(walls) == len(stack.sheets[0].ring)
    assert all(_near(math.hypot(*f.plane[:3]), 1.0, 1e-12) for f in walls)


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


def test_a_level_left_wrong_is_found(monkeypatch):
    # The control: the judge must say so when sheets are left on a level
    # they should have climbed off. Here the rule that lifts them is
    # switched off: nothing lies on anything until it is a metre on to it
    assert clash.clashes(_bifold().solid([0.0])) == []
    monkeypatch.setattr(sh, "LAP", 1000.0)
    stack = _bifold()
    assert [s.lo for s in stack.sheets] == [0.0] * 9
    cells = stack.solid([0.0])
    found = clash.clashes(cells)
    # the liner in each of the eight pocket pieces, and the four of each
    # pile in each other: every one 0.8 mm, all its thickness, in the next
    pairs = {tuple(sorted((a[2], b[2]))) for _d, a, b in found}
    assert pairs == {(1, k) for k in range(2, 10)} \
        | {(j, k) for lo in (2, 6) for j in range(lo, lo + 4)
           for k in range(j + 1, lo + 4)}
    assert len(found) == 20 and all(_near(d, 0.8) for d, _a, _b in found)
    assert bsp.count(bsp.build(cells))[1] > len(cells)     # and they are cut
    # Two sheets that overlap by 0.04 mm are on two levels. A rule that
    # let 0.05 mm pass would leave them on one, sharing that 0.04 mm --
    # which is more than the judge lets through
    lay = fs.fold_piece(HALF, [], thickness=2.0)

    def pair(over):
        two = [sh.Sheet(_rect(10, 10, 80, 90), 1.0),
               sh.Sheet(_rect(80 - over, 10, 150, 90), 1.0)]
        return two, sh.FlatStack(lay, two, HALF)

    monkeypatch.setattr(sh, "LAP", 0.05)
    two, stack = pair(0.04)
    assert two[1].lo == 0.0
    (d, a, b), = clash.clashes(stack.solid([]))
    assert _near(d, 0.04) and {a, b} == {("panel", 0, 1), ("panel", 0, 2)}
    assert clash.JUDGE < 0.04
    monkeypatch.undo()
    assert sh.LAP == 1e-6 <= clash.JUDGE
    two, stack = pair(0.04)
    assert two[1].lo == 1.0 and clash.clashes(stack.solid([])) == []
    # ... while sheets that butt, or share less than counts as lying on
    # each other, are left on one level and pass
    for over in (0.0, 0.5 * sh.LAP):
        two, stack = pair(over)
        assert two[1].lo == 0.0 and clash.clashes(stack.solid([])) == []


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

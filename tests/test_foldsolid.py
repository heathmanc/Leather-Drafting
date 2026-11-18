"""Folding a piece as solid leather: where the panels end up, how much
leather each bend takes, what the order of folding does, and the model the
view paints."""

import math
import random

from leathercad import bsp
from leathercad import foldsolid as fs
from leathercad import polycut as pc
from leathercad.folds import fold_allowance

RECT = [(0, 0), (240, 0), (240, 100), (0, 100)]
HALF = [(0, 0), (160, 0), (160, 100), (0, 100)]
# a 60 x 40 base with a 30 mm arm on each side
CROSS = [(0, 0), (0, -30), (60, -30), (60, 0), (90, 0), (90, 40), (60, 40),
         (60, 70), (0, 70), (0, 40), (-30, 40), (-30, 0)]


def _trifold(order=(1, 2), ways=(1, 1), **kw):
    folds = [fs.FoldLine((80, 0), (80, 100), 180, ways[0], order=order[0],
                         label="A"),
             fs.FoldLine((160, 0), (160, 100), 180, ways[1], order=order[1],
                         label="B")]
    return fs.fold_piece(RECT, folds, **kw)


def _one(angle=180.0, way=1, radius=-1.0, **kw):
    return fs.fold_piece(
        HALF, [fs.FoldLine((80, 0), (80, 100), angle, way, radius=radius,
                           label="A")], **kw)


def _folded(lay):
    return lay.frames([1.0] * len(lay.folds))


def _heights(lay):
    fr = _folded(lay)
    return [round(fs.place(fr[p.index], p.spot)[2], 6) for p in lay.panels]


def _near(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _area3(pts):
    nx = ny = nz = 0.0
    x0, y0, z0 = pts[-1]
    for x1, y1, z1 in pts:
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
        x0, y0, z0 = x1, y1, z1
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


# -- cutting into panels ------------------------------------------------------

def test_fold_lines_cut_the_piece_into_numbered_panels():
    # (folded as a zigzag, nothing lies inside either fold: both form on
    # their lines)
    lay = _trifold(ways=(1, -1))
    assert [p.number for p in lay.panels] == [1, 2, 3]
    assert [round(p.area) for p in lay.panels] == [8000, 8000, 8000]
    assert [round(p.spot[0]) for p in lay.panels] == [40, 120, 200]
    assert len(lay.hinges) == len(lay.tree) == 2
    assert lay.crosses == [True, True] and lay.steps == [1, 2]


def test_a_fold_line_need_not_be_drawn_edge_to_edge():
    for a, b in (((80, 10), (80, 90)),       # stops short at both ends
                 ((80, -20), (80, 130)),     # runs on past the piece
                 ((80.2, 0.1), (79.9, 99.8))):   # ends just off the outline
        lay = fs.fold_piece(HALF, [fs.FoldLine(a, b, 90, 1)])
        assert len(lay.panels) == 2 and len(lay.tree) == 1
        assert abs(sum(p.area for p in lay.panels) - 16000) < 1e-6
        h = lay.tree[0]
        assert abs((h.k1 - h.k0) - 100.0) < 0.01     # right across


def test_a_fold_line_off_the_piece_does_nothing():
    lay = fs.fold_piece(HALF, [fs.FoldLine((300, 0), (300, 100), 90, 1),
                               fs.FoldLine((80, 0), (80, 100), 90, 1)])
    assert lay.crosses == [False, True]
    assert lay.steps == [0, 1] and lay.used[0] == 0.0
    assert len(lay.panels) == 2


def test_folds_at_the_foot_of_each_arm_of_a_cross():
    """Each fold line runs on along two edges of the outline: it must cut
    only across the foot of its arm."""
    folds = [fs.FoldLine((0, 0), (0, 40), 90, 1),
             fs.FoldLine((60, 0), (60, 40), 90, 1),
             fs.FoldLine((0, 0), (60, 0), 90, 1),
             fs.FoldLine((0, 40), (60, 40), 90, 1)]
    lay = fs.fold_piece(CROSS, folds)
    assert sorted(round(p.area) for p in lay.panels) == [1200, 1200, 1800,
                                                         1800, 2400]
    assert len(lay.tree) == 4
    assert lay.panels[lay.base].area == 2400          # the biggest stays
    fr = _folded(lay)
    for p in lay.panels:
        n = fr[p.index][3]
        if p.index == lay.base:
            assert _near(n, (0, 0, 1))
        else:                          # a wall: upright, facing inwards
            assert abs(n[2]) < 1e-9
            c = fs.place(fr[p.index], p.spot)
            assert (30 - c[0]) * n[0] + (20 - c[1]) * n[1] > 0


# -- where a fold puts a panel ------------------------------------------------

def test_a_180_fold_lays_the_flap_one_thickness_up_and_mirrored():
    lay = _one(thickness=2.0)
    assert lay.base == 0
    fr = _folded(lay)
    assert _near(fs.place(fr[1], (120, 50)), (40, 50, 2.0))
    assert _near(fs.place(fr[1], (150, 10)), (10, 10, 2.0))
    assert _near(fr[1][3], (0, 0, -1))               # face down
    # with an inside radius the flap rides that much higher, still mirrored
    assert _near(fs.place(_folded(_one(radius=1.5))[1], (120, 50)),
                 (40, 50, 2.0 + 3.0))
    # folded to the back it goes underneath
    assert _near(fs.place(_folded(_one(way=-1))[1], (120, 50)),
                 (40, 50, -2.0))


def test_a_90_fold_stands_the_panel_up_past_the_bend():
    lay = _one(angle=90.0, thickness=2.0)
    rho = 1.0                                   # no inside radius: t / 2
    h = 0.5 * math.radians(90) * rho
    fr = _folded(lay)
    assert _near(fr[1][3], (-1, 0, 0))          # its front faces the base
    assert _near(fs.place(fr[1], (120, 50)),
                 (80 - h + rho, 50, rho + 40 - h))
    # half way there it is half way round, the bend the same length
    mid = lay.frames([0.5])
    assert _near(mid[1][3], (-math.sin(math.radians(45)), 0,
                             math.cos(math.radians(45))))


def test_a_bend_takes_angle_times_radius_plus_half_the_thickness():
    for angle, t, r in ((180, 2.0, 0.0), (90, 3.0, 1.5), (45, 1.2, 4.0)):
        lay = _one(angle=angle, radius=r, thickness=t)
        assert abs(lay.used[0] - fold_allowance(angle, t, r)) < 1e-9
        assert lay.radius[0] == r and lay.wrap[0] == 0.0
    # a fold with no radius of its own takes the piece's
    lay = _one(angle=90, thickness=2.0, radius=-1.0)
    assert lay.radius[0] == 0.0
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 90, 1)],
                        thickness=2.0, radius=2.5)
    assert lay.radius[0] == 2.5
    assert abs(lay.used[0] - math.radians(90) * 3.5) < 1e-9


# -- the order of folding -----------------------------------------------------

def test_the_fold_made_second_goes_round_the_first():
    lay = _trifold(order=(1, 2), base=1)
    assert _heights(lay) == [2.0, 0.0, 4.0]
    assert _near(lay.radius, [0.0, 1.0]) and _near(lay.wrap, [0.0, 1.0])
    assert abs(lay.used[1] - math.pi * 2.0) < 1e-9    # twice the leather
    lay = _trifold(order=(2, 1), base=1)
    assert _heights(lay) == [4.0, 0.0, 2.0]
    assert _near(lay.radius, [1.0, 0.0])


def test_which_panel_stays_still_does_not_change_the_stack():
    for base, want in ((0, [0.0, 2.0, -2.0]), (1, [2.0, 0.0, 4.0]),
                       (2, [2.0, 4.0, 0.0])):
        lay = _trifold(base=base)
        assert lay.base == base and _heights(lay) == want
        assert _near(lay.radius, [0.0, 1.0])    # B goes round panel 1


def test_folds_that_do_not_land_on_each_other_need_no_room():
    lay = _trifold(ways=(1, -1), base=0)              # a zigzag
    assert _heights(lay) == [0.0, 2.0, 4.0]
    assert _near(lay.radius, [0.0, 0.0])
    lay = _trifold(ways=(1, -1), base=1)
    assert _heights(lay) == [2.0, 0.0, -2.0]


def test_thickness_and_radius_change_the_model():
    assert _heights(_trifold(base=1, thickness=4.0)) == [4.0, 0.0, 8.0]
    lay = _trifold(base=1, thickness=2.0, radius=3.0)
    # A bends at 3: its flap lies 2 x (3 + 1) up; B must clear it
    assert _heights(lay) == [8.0, 0.0, 10.0]
    assert _near(lay.radius, [3.0, 4.0]) and _near(lay.wrap, [0.0, 1.0])
    lo, hi = lay.bounds3([1.0, 1.0])
    assert abs(lo[2] + 1.0) < 1e-9 and abs(hi[2] - 11.0) < 1e-9


def test_steps_and_how_far_each_fold_has_gone():
    lay = _trifold()
    assert lay.n_steps == 2
    assert lay.fractions(0.0) == [0.0, 0.0]
    assert lay.fractions(0.5) == [0.5, 0.0]
    assert lay.fractions(1.25) == [1.0, 0.25]
    assert lay.fractions(2.0) == [1.0, 1.0]
    together = _trifold(order=(1, 1))
    assert together.steps == [1, 1] and together.n_steps == 1
    f = [fs.FoldLine((0, 0), (1, 0), order=o) for o in (0, 5, 0, 2, 5)]
    assert fs.fold_steps(f, [True] * 5) == [3, 2, 4, 1, 2]
    assert fs.fold_steps(f, [True, False, True, True, True]) == [3, 0, 4, 1,
                                                                 2]


def test_an_unticked_fold_stays_flat():
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, active=False),
             fs.FoldLine((160, 0), (160, 100), 90, 1)]
    lay = fs.fold_piece(RECT, folds, base=0)
    assert lay.steps == [0, 1] and lay.used[0] == 0.0
    fr = _folded(lay)
    assert fr[1] == fr[0]                              # carried, not turned
    assert _near(fr[2][3], (-1, 0, 0))
    assert not any(c.hinge is not None and lay.tree[c.hinge].fold == 0
                   for c in lay.cells)


def test_folds_too_close_for_their_bends_are_reported():
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, label="A"),
             fs.FoldLine((82, 0), (82, 100), 180, -1, label="B")]
    lay = fs.fold_piece(RECT, folds, thickness=2.0, radius=1.0)
    assert len(lay.warnings) == 1
    assert "A and B are 2.0 mm apart" in lay.warnings[0]
    assert "6.3 mm" in lay.warnings[0]
    assert not _trifold().warnings


def test_folds_that_cross_fold_one_after_the_other():
    square = [(0, 0), (120, 0), (120, 120), (0, 120)]
    folds = [fs.FoldLine((60, 0), (60, 120), 180, 1, order=1),
             fs.FoldLine((0, 60), (120, 60), 180, 1, order=2)]
    lay = fs.fold_piece(square, folds)
    assert len(lay.panels) == 4 and len(lay.hinges) == 4
    assert len(lay.tree) == 3                 # the ring is cut at one hinge
    marks = lay.fold_marks([1.0, 1.0])        # every stretch still marked
    assert len(marks[0]) == 2 and len(marks[1]) == 2
    assert sorted(_heights(lay)) == [0.0, 2.0, 4.0, 6.0]
    assert _near(lay.radius, [0.0, 2.0])        # B goes round 2 layers
    # the two quarters inside the second fold are shown cut along it: it
    # does not move for them (nowhere would clear them), and says so
    assert lay.shift == [0.0, 0.0] and lay.stuck == [False, True]
    assert lay.warnings == [
        "Fold 2 runs across panels 3 and 4, which are folded inside it: "
        "each is shown cut along the fold and 4.71 mm into its bend. "
        "Cutting each back by that much at the fold clears it."]


# -- the cells and the solid --------------------------------------------------

def test_the_cells_cover_the_piece_once_and_are_convex():
    lay = _trifold(radius=1.0)
    assert abs(sum(c.area for c in lay.cells) - 24000) < 1e-6
    assert all(pc.is_convex(c.pts, 1e-9) for c in lay.cells)
    for i in range(len(lay.cells)):
        for j in range(i + 1, len(lay.cells)):
            assert not pc.convex_overlap(lay.cells[i].pts, lay.cells[j].pts,
                                         1e-6)
    # a 180 degree bend at 12 degrees a strip: 8 strips each side of the line
    strips = [c for c in lay.cells if c.hinge == 0]
    assert sorted(c.strip for c in strips) == list(range(16))
    assert abs(sum(c.area for c in strips) - lay.used[0] * 100) < 1e-6
    coarse = _trifold(radius=1.0, detail=45.0)
    assert len([c for c in coarse.cells if c.hinge == 0]) == 4


def test_the_solid_is_closed_convex_cells_at_every_stage():
    lay = _trifold(base=1, radius=1.5)
    for t in (0.0, 0.4, 1.0, 1.7, 2.0):
        cells = lay.solid(lay.fractions(t))
        assert len(cells) == len(lay.cells)
        front = 0.0
        for c in cells:
            for f in c.faces:
                nx, ny, nz, d = f.plane
                assert abs(nx * nx + ny * ny + nz * nz - 1.0) < 1e-9
                # flat, and the whole cell behind it
                assert all(abs(nx * x + ny * y + nz * z - d) < 1e-7
                           for x, y, z in f.pts)
                assert all(nx * x + ny * y + nz * z - d < 1e-6
                           for x, y, z in c.verts)
                if f.kind == bsp.FRONT:
                    front += _area3(f.pts)
        if t == 0.0:
            assert abs(front - 24000) < 1e-6
        tree = bsp.build(cells)
        assert bsp.count(tree)[1] >= len(cells)


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _shared(cells, kind):
    """The faces of one side of the leather, and for every edge the faces
    that have it: ``(faces, {edge: [face]})``."""
    faces = [f for c in cells for f in c.faces if f.kind == kind]
    by_edge = {}
    for f in faces:
        for i in range(len(f.pts)):
            by_edge.setdefault(_edge(f, i), []).append(f)
    return faces, by_edge


def _edge(f, i):
    a, b = f.pts[i], f.pts[(i + 1) % len(f.pts)]
    return frozenset((tuple(round(v, 5) for v in a),
                      tuple(round(v, 5) for v in b)))


def test_each_edge_of_a_face_says_what_is_across_it():
    # one 90 degree bend in eight strips: every strip and both panels run
    # the full height, so each edge is shared with just one other face
    lay = _one(angle=90.0, radius=2.0)
    for t in (0.0, 0.5, 1.0):
        cells = lay.solid(lay.fractions(t))
        for kind in (bsp.FRONT, bsp.BACK):
            faces, by_edge = _shared(cells, kind)
            assert len(faces) == 10
            turns = 0
            for f in faces:
                n = f.plane[:3]
                for i, e in enumerate(f.edges):
                    others = [g for g in by_edge[_edge(f, i)] if g is not f]
                    if e is True:
                        assert not others          # an edge of the leather
                        continue
                    # where the leather goes on round the bend: the way
                    # the next face along is turned
                    assert e is not False
                    turns += 1
                    assert len(others) == 1
                    assert _near(e, others[0].plane[:3], 1e-9)
                    step = math.degrees(math.acos(min(1.0, _dot(e, n))))
                    assert step <= 90.0 * t / 8 + 1e-6
            assert turns == 18          # one a panel, two a strip
    # ... and the cut edge of the leather has only lines to draw or not
    for c in lay.solid([1.0]):
        for f in c.faces:
            if f.kind == bsp.WALL:
                assert all(e is True or e is False for e in f.edges)


def test_the_inside_of_a_bend_with_no_radius_is_a_crease():
    lay = _one(angle=90.0, radius=0.0, thickness=2.0)   # front to front

    def onward(cells, kind):
        """For each flat panel: the way its face is turned, and the way
        the face its bend edge leads on to is."""
        out = []
        for c in cells:
            if c.tag[0] != "panel":
                continue
            (f,) = [f for f in c.faces if f.kind == kind]
            (e,) = [e for e in f.edges if e is not True and e is not False]
            out.append((f.plane[:3], e))
        return out

    full = lay.solid([1.0])
    # folded right up, the fronts of the two panels meet in a crease: each
    # leads straight on to the other, a right angle away
    (n0, e0), (n1, e1) = onward(full, bsp.FRONT)
    assert _near(e0, n1, 1e-9) and _near(e1, n0, 1e-9)
    assert abs(_dot(n0, n1)) < 1e-9
    # the back is the outside of the bend: the leather goes round in strips
    for n, e in onward(full, bsp.BACK):
        assert 0.99 < _dot(n, e) < 1.0
    # part folded there is still room inside the bend, so strips there too
    for n, e in onward(lay.solid([0.5]), bsp.FRONT):
        assert 0.99 < _dot(n, e) < 1.0


def _pattern(face, p):
    """Where on the pattern the texture of a face puts its point ``p``."""
    o, u, v = face.uv
    d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    uu, uv, vv = _dot(u, u), _dot(u, v), _dot(v, v)
    du, dv = _dot(d, u), _dot(d, v)
    det = uu * vv - uv * uv
    return ((du * vv - dv * uv) / det, (dv * uu - du * uv) / det)


def test_the_texture_lies_as_the_pattern_does_and_runs_on_round_a_bend():
    lay = _trifold(base=1, radius=1.5)
    # flat, the model is the pattern: every corner of the leather has the
    # texture of its own place on it, at full size
    for kind in (bsp.FRONT, bsp.BACK):
        faces, _by_edge = _shared(lay.solid(lay.fractions(0.0)), kind)
        for f in faces:
            _o, u, v = f.uv
            assert abs(_dot(u, u) - 1.0) < 1e-9
            assert abs(_dot(v, v) - 1.0) < 1e-9
            assert abs(_dot(u, v)) < 1e-9
            for p in f.pts:
                assert _near(_pattern(f, p), p[:2], 1e-9)
    # folded, the texture of one face carries on in the next: both put the
    # edge they share at the same place on the pattern
    for t in (0.6, 2.0):
        for kind in (bsp.FRONT, bsp.BACK):
            faces, by_edge = _shared(lay.solid(lay.fractions(t)), kind)
            pairs = 0
            for f in faces:
                for i in range(len(f.pts)):
                    for g in by_edge[_edge(f, i)]:
                        if g is f:
                            continue
                        pairs += 1
                        for p in (f.pts[i], f.pts[(i + 1) % len(f.pts)]):
                            assert _near(_pattern(f, p), _pattern(g, p), 1e-6)
            assert pairs >= 2 * (len(faces) - 1)
    # the cut edge and the faces inside the leather have none
    assert all(f.uv is None for c in lay.solid([1.0, 1.0]) for f in c.faces
               if f.kind in (bsp.WALL, bsp.INNER))


def test_a_bend_is_an_arc_of_the_radius_asked_for():
    r, t = 3.0, 2.0
    lay = _one(radius=r, thickness=t)
    rho = r + 0.5 * t
    h = 0.5 * math.pi * rho
    cells = lay.solid([1.0])
    seen = {bsp.FRONT: set(), bsp.BACK: set()}
    for c in cells:
        if c.tag[0] != "bend":
            continue
        for f in c.faces:
            if f.kind in seen:
                for x, _y, z in f.pts:
                    # round the line through (80 - h, *, rho)
                    seen[f.kind].add(round(math.hypot(x - (80 - h), z - rho),
                                           6))
    assert seen[bsp.FRONT] == {r}             # the inside of the bend
    assert seen[bsp.BACK] == {r + t}          # the outside
    # folded to the back, the front of the leather is the outside
    lay = _one(radius=r, thickness=t, way=-1)
    inside = {round(math.hypot(x - (80 - h), z + rho), 6)
              for c in lay.solid([1.0]) if c.tag[0] == "bend"
              for f in c.faces if f.kind == bsp.BACK for x, _y, z in f.pts}
    assert inside == {r}


def test_holes_and_cutouts_ride_on_the_faces():
    # the third hole is in the bend
    holes = [(40, 50, 1.0), (120, 50, 1.2), (80.2, 30, 1.0)]
    slot = [(20, 20), (60, 20), (60, 24), (20, 24)]
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                        holes=holes, cutouts=[slot])
    assert lay.hole_panel == [0, 1, 1] and lay.hole_flat == [True, True,
                                                             False]
    # (folded with no inside radius, the inside of the bend closes to a
    # line: the hole in the bend shows on the outside only)
    check = fs.Check()                 # as if the second hole were off
    check.state[1] = fs.OFF
    for t, on_faces in ((0.0, 6), (1.0, 5)):
        dots = polys = marked = 0
        for c in lay.solid(lay.fractions(t), check):
            for f in c.faces:
                for kind, geo, style in f.decals or ():
                    assert f.kind in (bsp.FRONT, bsp.BACK)
                    if kind == bsp.DOTS:
                        dots += len(geo)
                        assert style in ((1.0, 0), (1.2, fs.OFF))
                        marked += len(geo) if style[1] == fs.OFF else 0
                    else:
                        assert kind == bsp.POLY
                        polys += 1
        assert dots == on_faces and marked == 2    # on front and back
        assert polys == 2
    # flat, a hole sits right over its place in the pattern
    spots = {tuple(round(v, 6) for v in p)
             for c in lay.solid([0.0]) for f in c.faces
             for kind, geo, _s in f.decals or () if kind == bsp.DOTS
             for p in geo}
    assert (40.0, 50.0, 1.0) in spots and (40.0, 50.0, -1.0) in spots


def test_lines_drawn_on_the_piece_ride_on_the_faces():
    slit = ("cut", [(20, 30), (140, 30)])                 # across the fold
    vee = ("line", [(100, 60), (120, 80), (140, 60)])
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                        marks=[slit, vee], radius=1.0)
    want = {"cut": 120.0, "line": 2 * math.hypot(20, 20)}
    for t in (0.0, 1.0):
        for face in (bsp.FRONT, bsp.BACK):
            got = {"cut": 0.0, "line": 0.0}
            for c in lay.solid([t]):
                for f in c.faces:
                    for kind, geo, style in f.decals or ():
                        if kind == bsp.LINE and f.kind == face:
                            got[style] += math.dist(*geo)
            # flat, every bit of each line is there; folded, the slit is a
            # shade shorter where it goes round the inside or the outside
            # of the bend
            assert abs(got["line"] - want["line"]) < 1e-6
            if t == 0.0:
                assert abs(got["cut"] - want["cut"]) < 1e-6
            else:
                assert 115.0 < got["cut"] < 126.0
    # a drawing full of lettering shows the first few thousand strokes
    many = [("line", [(10 + 0.01 * k, 10), (10 + 0.01 * k, 12)])
            for k in range(7000)]
    busy = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                         marks=many)
    assert sum(len(c.lines) for c in busy.cells) == 6000


def test_fold_marks_lie_on_the_bend_and_panel_marks_on_the_panels():
    lay = _one(thickness=2.0, radius=1.0)
    a, b, a2, b2, nn = lay.fold_marks([0.0])[0][0]
    assert sorted((a, b)) == [(80.0, 0.0, 1.0), (80.0, 100.0, 1.0)]
    assert sorted((a2, b2)) == [(80.0, 0.0, -1.0), (80.0, 100.0, -1.0)]
    assert _near(nn, (0, 0, 1))
    rho = 2.0
    h = 0.5 * math.pi * rho
    a, b, a2, b2, nn = lay.fold_marks([1.0])[0][0]
    # half way round: the inside of the bend, then the outside
    x_in, x_out = 80 - h + rho - 1.0, 80 - h + rho + 1.0
    assert all(_near(p, (x_in, y, rho)) for p, y in zip(sorted((a, b)),
                                                        (0, 100)))
    assert all(_near(p, (x_out, y, rho)) for p, y in zip(sorted((a2, b2)),
                                                         (0, 100)))
    assert _near(nn, (-1, 0, 0))
    front, back, n = lay.panel_marks([1.0])[1]
    assert _near(n, (0, 0, -1)) and _near(front, (40, 50, 3.0))
    assert _near(back, (40, 50, 5.0))
    centre, radius = lay.extent()
    assert _near(centre, (80, 50, 0)) and radius > 94


# -- do the stitch holes meet? ------------------------------------------------

def _stitched(shift=0.0, extra=()):
    holes = [(20 + 5 * k, 10, 1.0) for k in range(10)]
    holes += [(160 - (20 + 5 * k) + shift, 10, 1.0) for k in range(10)]
    return fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                         holes=holes + list(extra))


def test_holes_mirrored_about_the_fold_meet():
    check = _stitched().lineup()
    assert len(check.findings) == 1
    f = check.findings[0]
    assert (f.a, f.b, f.holes, f.off, f.lone, f.met) == (1, 2, 20, 0, 0, 20)
    assert f.gap < 1e-9
    assert check.holes(fs.MEETS) == set(range(20))
    assert not check.off and not check.lands and not check.rings


def test_holes_a_little_out_are_marked_and_measured():
    lay = _stitched(shift=1.5)
    check = lay.lineup()
    f = check.findings[0]
    assert (f.off, f.lone, f.met) == (20, 0, 0) and abs(f.worst - 1.5) < 1e-9
    assert check.off == set(range(20)) == set(check.state)
    # each has a mark on its own panel where the hole it should have met
    # comes down: 1.5 mm along the row from it
    for k in range(20):
        (x, y, dia), = check.lands[k]
        hx, hy, _dia = lay.holes[k]
        assert abs(abs(x - hx) - 1.5) < 1e-9 and abs(y - hy) < 1e-9
        assert dia == 1.0
    assert sum(len(v) for v in check.rings.values()) == 20
    # ... and the model carries those marks, on both faces of the leather
    styles = {}
    for c in lay.solid([1.0], check):
        for face in c.faces:
            for kind, geo, style in face.decals or ():
                if kind == bsp.DOTS:
                    styles[style] = styles.get(style, 0) + len(geo)
    assert styles == {(1.0, fs.OFF): 40, (1.0, fs.LANDS): 40}
    near = _stitched(shift=0.5).lineup()               # near enough
    assert not near.off and len(near.holes(fs.MEETS)) == 20
    assert abs(near.findings[0].gap - 0.5) < 1e-9


def test_a_hole_with_nothing_near_it_is_told_apart():
    check = _stitched(extra=[(40, 60, 1.0)]).lineup()
    f = check.findings[0]
    assert (f.holes, f.off, f.lone, f.met) == (21, 0, 1, 20)
    assert check.holes(fs.LONE) == {20} and not check.off
    # a flap over a panel with no holes of its own: nothing to report
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                        holes=[(20 + 5 * k, 10, 1.0) for k in range(10)])
    check = lay.lineup()
    assert not check.findings and not check.state
    # and nothing lies on anything until it is folded flat
    check = _one(angle=90.0).lineup()
    assert not check.findings and not check.state


def _stack_of_three(out=0.0, third=True):
    """A strip folded in three, with one stitch hole through each layer
    where they lie together: the hole in the middle panel at (120, 50),
    the other two coming down ``out`` mm to either side of it."""
    fr = _folded(_trifold(base=1))

    def over(panel, spot):
        """The point of a panel that comes down on ``spot`` of panel 2."""
        o, ex, ey, _n = fr[panel]
        p3 = fs.place(fr[1], spot)
        v = (p3[0] - o[0], p3[1] - o[1], p3[2] - o[2])
        return (v[0] * ex[0] + v[1] * ex[1] + v[2] * ex[2],
                v[0] * ey[0] + v[1] * ey[1] + v[2] * ey[2], 1.0)

    holes = [over(0, (120 + out, 50)), (120, 50, 1.0)]
    if third:
        holes.append(over(2, (120 - out, 50)))
    return _trifold(base=1, holes=holes)


def test_a_hole_is_judged_by_the_whole_stack_it_is_in():
    # three layers with the holes right over each other
    check = _stack_of_three().lineup()
    assert [(f.a, f.b, f.met, f.off) for f in check.findings] \
        == [(1, 2, 2, 0), (1, 3, 2, 0), (2, 3, 2, 0)]
    assert check.holes(fs.MEETS) == {0, 1, 2} and not check.lands
    # the outer two each 0.6 mm from the middle one, on opposite sides:
    # near enough to it, but 1.2 mm from each other
    lay = _stack_of_three(out=0.6)
    check = lay.lineup()
    found = {(f.a, f.b): f for f in check.findings}
    assert (found[1, 2].met, found[1, 2].off) == (2, 0)
    assert (found[2, 3].met, found[2, 3].off) == (2, 0)
    assert (found[1, 3].met, found[1, 3].off) == (0, 2)
    assert abs(found[1, 3].worst - 1.2) < 1e-9
    assert abs(found[1, 2].gap - 0.6) < 1e-9
    # a needle has to go through all three: so all three are out of line,
    # the middle one too -- it is the one you see from on top
    assert check.off == {0, 1, 2}
    # the middle hole has both of the others marked, either side of it
    assert sorted(round(x, 6) for x, _y, _d in check.lands[1]) \
        == [119.4, 120.6]
    # ... and each outer hole has the other, 1.2 mm from itself
    for k in (0, 2):
        (x, y, _dia), = check.lands[k]
        assert abs(math.hypot(x - lay.holes[k][0], y - lay.holes[k][1])
                   - 1.2) < 1e-9
    assert sum(len(v) for v in check.rings.values()) == 4
    # with only two of them, 0.6 mm apart, there is nothing wrong
    check = _stack_of_three(out=0.6, third=False).lineup()
    assert check.holes(fs.MEETS) == {0, 1} and not check.off


# -- a fold that closes over what is folded inside it -------------------------

def _parting(cell):
    """The ways a convex cell can be parted from another: the normals of
    its faces, and the directions its edges run in."""
    normals, edges = [], []
    for f in cell.faces:
        ways = [(normals, f.plane[:3])]
        ways += [(edges, (q[0] - p[0], q[1] - p[1], q[2] - p[2]))
                 for p, q in zip(f.pts, f.pts[1:] + f.pts[:1])]
        for kept, v in ways:
            ln = math.sqrt(_dot(v, v))
            if ln > 1e-9 and not any(abs(_dot(v, w)) > (1 - 1e-9) * ln
                                     for w in kept):
                kept.append((v[0] / ln, v[1] / ln, v[2] / ln))
    return normals, edges


def _overlap(a, b, ways, tol):
    """The least that two cells overlap along any of ``ways`` (it stops at
    the first along which they are ``tol`` or less into each other)."""
    least = math.inf
    for n in ways:
        pa = [_dot(n, v) for v in a.verts]
        pb = [_dot(n, v) for v in b.verts]
        least = min(least, max(pa) - min(pb), max(pb) - min(pa))
        if least <= tol:
            break
    return least


def _inside_each_other(cells, tol=0.05):
    """The pairs of cells of a model with the leather of one inside the
    other, more than ``tol`` mm deep: ``[(tag, tag, depth)]``.

    Two convex cells are apart if some plane parts them, and the planes to
    try are those of their own faces and those along an edge of each (the
    separating-axis test). ``depth`` is the least they overlap along any
    of those ways: how far one would have to move to come clear. Cells
    that only touch -- neighbours along the leather, layers lying on each
    other, the two faces of a crease -- are not inside each other."""
    found = []
    ways = {}
    for i, a in enumerate(cells):
        for j in range(i + 1, len(cells)):
            b = cells[j]
            if math.dist((a.cx, a.cy, a.cz), (b.cx, b.cy, b.cz)) \
                    >= a.rad + b.rad - tol:
                continue                   # too far apart to touch
            for k in (i, j):
                if k not in ways:
                    ways[k] = _parting(cells[k])
            (na, ea), (nb, eb) = ways[i], ways[j]
            depth = _overlap(a, b, na + nb, tol)
            if depth > tol:
                across = []
                for u in ea:
                    for v in eb:
                        c = (u[1] * v[2] - u[2] * v[1],
                             u[2] * v[0] - u[0] * v[2],
                             u[0] * v[1] - u[1] * v[0])
                        ln = math.sqrt(_dot(c, c))
                        if ln > 1e-6:
                            across.append((c[0] / ln, c[1] / ln, c[2] / ln))
                depth = min(depth, _overlap(a, b, across, tol))
            if depth > tol:
                found.append((a.tag, b.tag, depth))
    return found


def _rounded(w, h, r):
    """A ``w`` x ``h`` rectangle about the origin with its corners rounded
    at ``r``, as a drawing gives it: a point every 7.5 degrees."""
    out = []
    for sx, sy, turn in ((1, -1, -90), (1, 1, 0), (-1, 1, 90), (-1, -1, 180)):
        out += [(sx * (w / 2 - r) + r * math.cos(math.radians(turn + 7.5 * k)),
                 sy * (h / 2 - r) + r * math.sin(math.radians(turn + 7.5 * k)))
                for k in range(13)]
    return out


def _four_folds(at=(-18.5, 0.0, 18.5), own=-1.0):
    """Three folds down a piece 60 mm high, each right over: A to the
    front first, then B and then C to the back. ``own`` is a radius for
    fold A alone."""
    return [fs.FoldLine((x, 30), (x, -30), 180, way, radius=r, order=k + 1,
                        label=fs.letter(k))
            for k, (x, way, r) in enumerate(zip(at, (1, -1, -1),
                                                (own, -1.0, -1.0)))]


def _four_panels(base=3, at=(-18.5, 0.0, 18.5), own=-1.0, **kw):
    """A 90 x 60 piece with rounded corners folded in four: panels 1 and 2
    end up between panels 4 and 3, fold A inside fold C. Panels 2 and 3
    are drawn the same width (18.5 mm), which leaves fold A too far along
    for fold C to close on its line. Panel 4 stays still unless told."""
    return fs.fold_piece(_rounded(90, 60, 8), _four_folds(at, own),
                         base=base, **kw)


def _beyond(lay):
    """How far the free edge of the four panels' panel 1 finishes beyond
    that of panel 4, along panel 4."""
    fr = _folded(lay)
    p1, p4 = fs.place(fr[0], (-45, 0)), fs.place(fr[3], (45, 0))
    return _dot((p1[0] - p4[0], p1[1] - p4[1], p1[2] - p4[2]), fr[3][1])


def test_leather_drawn_inside_other_leather_is_found():
    """The check the tests below lean on, tried on the fault they are
    about: the four panels with every fold kept on its line."""
    folds = _four_folds()
    regions, lines = fs.split_piece(fs.model_outline(_rounded(90, 60, 8)),
                                    folds)
    drawn = fs.Layout(regions, folds, lines, base=3)

    def found(when):
        return _inside_each_other(drawn.solid(drawn.fractions(when)))

    # with two folds made all is well: layers lying on each other, the
    # strips of a bend side by side, a crease closed on itself -- none of
    # that is leather inside leather
    assert found(1) == [] and found(2) == []
    # the third closes through panels 1 and 2 and the fold between them
    assert {frozenset(pair[:2]) for pair in found(3)} == {
        frozenset({("bend", 2), ("panel", 0)}),
        frozenset({("bend", 2), ("panel", 1)}),
        frozenset({("bend", 2), ("bend", 0)})}
    assert max(depth for _a, _b, depth in found(3)) > 1.2
    # ... more and more of it as it comes round
    closing = [len(found(when)) for when in (2.25, 2.5, 2.75)]
    assert closing[0] > 0 and closing == sorted(closing)
    # a layout that can't move its folds says what is in the way of one
    assert drawn.shift == [0.0, 0.0, 0.0] and drawn.stuck == [0, 0, 1]
    assert drawn.warnings == [
        "What is folded inside fold C reaches 3.14 mm into its bend. "
        "Shorten panel 2 by 3.14 mm where it reaches into the fold."]


def test_a_fold_forms_off_its_line_to_go_round_a_fold_inside_it():
    lay = _four_panels()
    t = lay.thickness
    # fold C closes over panels 1 and 2: it bends at the radius that has
    # room for the two of them, and takes the leather that needs ...
    assert _near(lay.radius, [0.0, 0.0, t]) and _near(lay.wrap, [0, 0, t])
    assert _near(lay.used, [math.pi, math.pi, 3 * math.pi], 1e-9)
    # ... but it forms pi t / 2 further on, towards panel 4, where the
    # round of fold A fits inside its own
    assert _near(lay.shift, [0.0, 0.0, math.pi * t / 2], 1e-6)
    assert lay.shift_toward == [None, None, 4]
    assert lay.shift_for == [None, None, 2]          # the panel too long
    assert not lay.warnings
    assert [p.number for p in lay.panels] == [1, 2, 3, 4] and lay.base == 3
    assert [round(p.box[2], 2) for p in lay.panels] \
        == [-18.5, 0.0, 21.64, 45.0]
    (x, _y), _e, _ln = lay.lines[2]                  # the middle of its bend
    half = 0.5 * lay.used[2]
    assert (round(x - half, 2), round(x + half, 2)) == (16.93, 26.35)
    # the two bends are about the same line -- where fold C's begins on
    # panel 4, three half thicknesses down: fold A is creased along it,
    # and its outside lies on the inside of fold C
    cells = lay.solid([1.0] * 3)

    def radii(fold, kind=None):
        return {round(math.hypot(px - (x + half), pz + 1.5 * t), 6)
                for c in cells if c.tag == ("bend", fold) for f in c.faces
                if kind in (None, f.kind) for px, _py, pz in f.pts}

    assert radii(0) == {0.0, t} and radii(0, bsp.BACK) == {t}
    assert radii(2, bsp.BACK) == {t} and radii(2, bsp.FRONT) == {2 * t}
    # so the outer panels no longer finish level: panel 1 lies under panel
    # 4 and runs 2 pi past its edge
    assert _near(fs.place(_folded(lay)[0], (-45, 0)),
                 (45 + 2 * math.pi, 0, -t), 1e-6)
    assert abs(_beyond(lay) - 2 * math.pi) < 1e-6
    # ... and nothing is drawn through anything, at any stage
    for when in (1, 2, 2.25, 2.5, 2.75, 3):
        assert _inside_each_other(lay.solid(lay.fractions(when))) == []
    # whichever panel stays still, it is the same piece folded the same
    for base in (0, 1, 2):
        other = _four_panels(base=base)
        assert other.base == base
        assert _near(other.shift, lay.shift, 1e-9)
        assert other.shift_toward == lay.shift_toward
        assert other.shift_for == lay.shift_for
        assert _near(other.radius, lay.radius)
        assert _near(other.used, lay.used, 1e-9)
        assert all(_near(p.box, q.box, 1e-9)
                   for p, q in zip(other.panels, lay.panels))
        assert abs(_beyond(other) - 2 * math.pi) < 1e-6
        for when in (1, 2, 2.25, 2.5, 2.75, 3):
            assert _inside_each_other(other.solid(other.fractions(when))) \
                == []


def test_shortening_the_panel_it_names_puts_the_fold_back_on_its_line():
    by = _four_panels().shift[2]
    # panel 2 that much shorter: fold line A moved towards fold line B
    fixed = _four_panels(at=(-18.5 + by, 0.0, 18.5))
    assert fixed.shift == [0.0, 0.0, 0.0] and not fixed.warnings
    assert fixed.shift_toward == fixed.shift_for == [None, None, None]
    assert abs(fixed.lines[2][0][0] - 18.5) < 1e-9
    assert _near(fixed.radius, [0.0, 0.0, 2.0])
    for when in (2.5, 3):
        assert _inside_each_other(fixed.solid(fixed.fractions(when))) == []
    # (panel 1 has the leather panel 2 lost: it runs as far past panel 4
    # as it did with the fold moved)
    assert abs(_beyond(fixed) - 2 * math.pi) < 1e-6
    # shortened by less, the fold still has the rest to make up
    part = _four_panels(at=(-18.5 + 2.0, 0.0, 18.5))
    assert _near(part.shift, [0.0, 0.0, by - 2.0], 1e-6)
    assert part.shift_for == [None, None, 2]


def test_a_letter_fold_in_equal_thirds_folds_the_cover_off_its_line():
    # the flap is as long as the panel it lies on, so it reaches the line
    # the cover folds on -- half the leather of that bend past the place
    # where the bend starts
    for base in (0, 1, 2):
        lay = _trifold(base=base)
        assert _near(lay.shift, [0.0, math.pi], 1e-6)          # pi t / 2
        assert lay.shift_toward == [None, 3]                   # the cover
        assert lay.shift_for == [None, 1]                      # the flap
        assert not lay.warnings
        assert _near(lay.radius, [0.0, 1.0])
        assert abs(lay.used[1] - 2 * math.pi) < 1e-9
        assert [round(p.area) for p in lay.panels] == [8000, 8314, 7686]
        for when in (1, 1.5, 2):
            assert _inside_each_other(lay.solid(lay.fractions(when))) == []
    # the flap a little shorter, the fold that much nearer its line; once
    # it is short by all of pi t / 2 the fold is on it
    for short, shift in ((1.0, math.pi - 1.0), (3.0, math.pi - 3.0),
                         (3.2, 0.0), (6.0, 0.0)):
        folds = [fs.FoldLine((80 - short, 0), (80 - short, 100), 180, 1,
                             order=1, label="A"),
                 fs.FoldLine((160 - short, 0), (160 - short, 100), 180, 1,
                             order=2, label="B")]
        lay = fs.fold_piece([(0, 0), (240 - short, 0), (240 - short, 100),
                             (0, 100)], folds)
        assert _near(lay.shift, [0.0, shift], 1e-6)
        assert _inside_each_other(lay.solid([1.0, 1.0])) == []


def test_how_far_a_fold_moves_goes_with_the_thickness():
    for t in (1.0, 2.0, 3.0):
        lay = _four_panels(thickness=t)
        assert _near(lay.shift, [0.0, 0.0, math.pi * t / 2], 1e-6)
        assert lay.shift_toward[2] == 4 and lay.shift_for[2] == 2
        assert _near(lay.radius, [0.0, 0.0, t])
        assert _inside_each_other(lay.solid([1.0] * 3)) == []
    # a radius of its own on the fold inside holds panels 1 and 2 apart,
    # and fold C bends at that much more to go round them -- but it is no
    # further in the way: the two bends grow together
    for own in (0.5, 1.5, 3.0):
        for base in (3, 0):
            lay = _four_panels(base=base, own=own)
            assert _near(lay.radius, [own, 0.0, 2.0 + own])
            assert _near(lay.shift, [0.0, 0.0, math.pi], 1e-6)
            assert lay.shift_toward[2] == 4 and lay.shift_for[2] == 2
            assert _inside_each_other(lay.solid([1.0] * 3)) == []


def test_a_fold_goes_round_the_gaps_in_what_it_closes_over_too():
    # with a bend radius of 1 a fold holds its two panels a thickness
    # apart. Fold C of the four panels closes over panels 1 and 2 with
    # two such gaps: 8 mm in all, whichever panel stays still. (Panel 1
    # was 2 mm the wrong side of panel 4 once, the gaps taken for room.)
    for base, heights in ((3, [-2.0, -6.0, -10.0, 0.0]),
                          (0, [0.0, 4.0, 8.0, -2.0])):
        lay = _four_panels(base=base, radius=1.0)
        assert _near(lay.radius, [1.0, 1.0, 4.0])
        assert _near(lay.wrap, [0.0, 0.0, 3.0])
        assert _heights(lay) == heights
        assert _inside_each_other(lay.solid([1.0] * 3)) == []
    # A flap whose own fold bends at 3 lies 8 up, with room under it. A
    # cover as long as the panel reaches the fold that holds the flap
    # there, so it has to go over the two of them ...
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, radius=3.0, order=1),
             fs.FoldLine((160, 0), (160, 100), 180, 1, order=2)]
    lay = fs.fold_piece(RECT, folds, base=1)
    assert _near(lay.radius, [3.0, 4.0]) and _heights(lay) == [8.0, 0.0, 10.0]
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []
    # ... but one 10 mm shorter stops short of that fold, and lies flat
    # on the panel under the flap, as it always did
    lay = fs.fold_piece([(0, 0), (230, 0), (230, 100), (0, 100)], folds,
                        base=1)
    assert _near(lay.radius, [3.0, 0.0]) and _heights(lay) == [8.0, 0.0, 2.0]
    assert lay.shift == [0.0, 0.0]
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []


def test_a_fold_with_nothing_in_its_way_stays_on_its_line():
    """... and the model is then, to the last corner, that of the piece
    cut along the lines it was given."""
    letter = [(0, 0), (234, 0), (234, 100), (0, 100)]
    designs = [
        (HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)]),
        # a tray: a side to stand up on each edge of the base
        (CROSS, [fs.FoldLine((0, 0), (0, 40), 90, 1),
                 fs.FoldLine((60, 0), (60, 40), 90, 1),
                 fs.FoldLine((0, 0), (60, 0), 90, 1),
                 fs.FoldLine((0, 40), (60, 40), 90, 1)]),
        # a letter fold whose flap stops 6 mm short of the second fold
        (letter, [fs.FoldLine((74, 0), (74, 100), 180, 1, order=1),
                  fs.FoldLine((154, 0), (154, 100), 180, 1, order=2)]),
        # a zigzag
        (RECT, [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1),
                fs.FoldLine((160, 0), (160, 100), 180, -1, order=2)]),
        # the four panels, with panel 2 as short as it has to be
        (_rounded(90, 60, 8), _four_folds(at=(-18.5 + 3.2, 0.0, 18.5)))]
    for outline, folds in designs:
        lay = fs.fold_piece(outline, folds)
        assert lay.shift == [0.0] * len(folds)
        assert lay.shift_toward == lay.shift_for == [None] * len(folds)
        assert not lay.warnings
        regions, lines = fs.split_piece(fs.model_outline(outline), folds)
        drawn = fs.Layout(regions, folds, lines)
        assert lay.lines == drawn.lines and not drawn.warnings
        for half_steps in range(2 * lay.n_steps + 1):
            at = lay.fractions(0.5 * half_steps)
            assert [[f.pts for f in c.faces] for c in lay.solid(at)] \
                == [[f.pts for f in c.faces] for c in drawn.solid(at)]
    # a net has nothing to say either: five squares, four of them stood up
    net = fs.fold_net([[(x, y), (x + 40, y), (x + 40, y + 40), (x, y + 40)]
                       for x, y in ((0, 0), (-40, 0), (40, 0), (0, 40),
                                    (0, -40))])
    assert net.shift == [0.0] * 4 and not net.warnings
    assert net.shift_toward == net.shift_for == [None] * 4


def _strip(*widths):
    """A strip 60 mm high, and the places to fold it into panels as wide
    as ``widths``."""
    at = [sum(widths[:k]) for k in range(1, len(widths))]
    return [(0, 0), (sum(widths), 0), (sum(widths), 60), (0, 60)], at


def _two_flaps(flap, base=1, other=40.0):
    """A strip of four panels: flaps 1 and 4 fold in on to panels 2 and 3
    (40 mm each), and then fold B closes the two over each other. Flap 4
    is 40 mm, as long as its panel, unless ``other`` says; flap 1 is
    ``flap``."""
    outline, (a, b, c) = _strip(flap, 40, 40, other)
    folds = [fs.FoldLine((a, 0), (a, 60), 180, 1, order=1, label="A"),
             fs.FoldLine((b, 0), (b, 60), 180, 1, order=3, label="B"),
             fs.FoldLine((c, 0), (c, 60), 180, 1, order=2, label="C")]
    return fs.fold_piece(outline, folds, base=base)


def test_leather_folded_in_from_both_sides_leaves_a_fold_nowhere_to_go():
    # both flaps reach fold line B -- half the leather of its bend, 4.7
    # mm, past where the bend starts on either side. Moving it clears
    # neither without running into the other
    lay = _two_flaps(40)
    assert _near(lay.radius, [0.0, 2.0, 0.0])        # round both flaps
    assert lay.shift == [0.0, 0.0, 0.0] and lay.stuck == [0, 1, 0]
    assert lay.warnings == [
        "What is folded inside fold B reaches into its bend from both "
        "sides: panel 1 by 4.71 mm and panel 4 by 4.71 mm. Shorten each "
        "by that much where it reaches into the fold."]
    # with flap 1 2 mm shorter the fold goes half way: 1 mm towards the
    # panel that flap lies on
    lay = _two_flaps(38)
    assert _near(lay.shift, [0.0, 1.0, 0.0], 1e-6)
    assert lay.shift_toward == [None, 2, None]
    assert lay.shift_for == [None, None, None]       # no one panel to trim
    assert lay.shift_trim == [[], [], []] and lay.remedy(1) == ""
    assert lay.stuck == [False, True, False]
    assert lay.warnings == [
        "What is folded inside fold B reaches into its bend from both "
        "sides: panel 1 by 2.71 mm and panel 4 by 4.71 mm. Shorten each "
        "by that much where it reaches into the fold."]
    # ... the same whichever of the two middle panels stays still
    other = _two_flaps(38, base=2)
    assert _near(other.shift, lay.shift, 1e-6)
    assert other.shift_toward == lay.shift_toward
    assert other.warnings == lay.warnings
    # 8 mm shorter, flap 1 would be clear of the fold on its line; but the
    # fold wants to go 4.7 mm that way for flap 4, and meets it after 4
    lay = _two_flaps(32)
    assert _near(lay.shift, [0.0, 4.0, 0.0], 1e-6)
    assert lay.shift_toward == [None, 2, None] and lay.shift_for[1] is None
    assert lay.warnings == [
        "What is folded inside fold B reaches 4.71 mm into its bend, and "
        "the fold cannot move further out of its way: panel 1 is folded "
        "in from the other side. Shorten panel 4 by 4.71 mm where it "
        "reaches into the fold."]
    # and 10 mm shorter there is room: the fold moves for flap 4 alone
    lay = _two_flaps(30)
    assert _near(lay.shift, [0.0, 1.5 * math.pi, 0.0], 1e-6)
    assert lay.shift_for == [None, 4, None] and not lay.warnings
    assert lay.stuck == [False, False, False]
    assert _inside_each_other(lay.solid([1.0] * 3)) == []
    # A flap 4 mm longer than its panel lies across the line as well, and
    # lifts the other flap's fold. With it cut back, that fold lies flat
    # and fold B goes round less: flap 4 then has more to lose than it is
    # in the way by now -- as has been tried
    lay = _two_flaps(44)
    assert _near(lay.radius, [0.0, 3.0, 1.0])
    assert lay.warnings == [
        "What is folded inside fold B reaches into its bend from both "
        "sides: panel 1 by 10.28 mm and panel 4 by 3.45 mm. Shorten panel "
        "1 by 10.28 mm and panel 4 by 4.71 mm where they reach into the "
        "fold."]
    lay = _two_flaps(44 - 10.29, other=40 - 4.72)
    assert _near(lay.radius, [0.0, 2.0, 0.0])
    assert lay.shift == [0.0, 0.0, 0.0] and not lay.warnings
    assert _inside_each_other(lay.solid([1.0] * 3)) == []


def _short_end(last, *more):
    """The four panels' folds on a plain strip whose fourth panel is only
    ``last`` mm (and then ``more`` panels, the first of them stood up)."""
    outline, at = _strip(26.5, 18.5, 18.5, last, *more)
    folds = [fs.FoldLine((x, 0), (x, 60), angle, way, order=k + 1,
                         label=fs.letter(k))
             for k, (x, way, angle) in enumerate(zip(
                 at, (1, -1, -1, 1), (180, 180, 180, 90)))]
    return fs.fold_piece(outline, folds, base=3)


def test_a_fold_stops_where_its_bend_would_no_longer_fit():
    # fold C wants to go 3.1 mm towards panel 4, which is 6 mm wide; half
    # its own bend, 4.7 mm, has to fit there too
    lay = _short_end(6.0)
    room = 6.0 - 1.5 * math.pi
    assert _near(lay.shift, [0.0, 0.0, room], 1e-6)
    assert lay.shift_toward == [None, None, 4]
    # (what to shorten is in the warning: the fold is not where it would
    # have to be)
    assert lay.shift_for == [None, None, None] and lay.remedy(2) == ""
    assert lay.stuck == [False, False, True]
    assert lay.warnings == [
        "What is folded inside fold C reaches 3.14 mm into its bend, and "
        "the fold cannot move further out of its way: its bend would not "
        "fit before the edge of panel 4. Shorten panel 2 by 3.14 mm where "
        "it reaches into the fold."]
    assert abs(sum(c.area for c in lay.cells) - 69.5 * 60) < 1e-6
    # with no room at all it stays where it is
    lay = _short_end(4.0)
    assert lay.shift == [0.0, 0.0, 0.0]
    assert lay.warnings == [
        "What is folded inside fold C reaches 3.14 mm into its bend, and "
        "the fold cannot move out of its way: its bend would not fit "
        "before the edge of panel 4. Shorten panel 2 by 3.14 mm where it "
        "reaches into the fold."]
    # another fold line there has a bend of its own to fit (0.8 mm of it)
    lay = _short_end(8.0, 30.0)
    assert _near(lay.shift, [0.0, 0.0, 8.0 - 1.75 * math.pi, 0.0], 1e-6)
    assert lay.warnings == [
        "What is folded inside fold C reaches 3.14 mm into its bend, and "
        "the fold cannot move further out of its way: its bend would not "
        "fit before fold D. Shorten panel 2 by 3.14 mm where it reaches "
        "into the fold."]
    # ... and with room enough beside it nothing is said
    lay = _short_end(12.0, 30.0)
    assert _near(lay.shift, [0.0, 0.0, math.pi, 0.0], 1e-6)
    assert lay.shift_for == [None, None, 2, None] and not lay.warnings


def test_a_fold_at_the_foot_of_a_flap_cannot_move_on_to_a_wider_panel():
    # a 100 mm body; on it a 60 mm flap with a flap of its own, as long as
    # itself, that folds in first. The fold at the foot would have to form
    # 3.1 mm into the body -- and there it would run right across it
    piece = [(0, 0), (100, 0), (100, 80), (80, 80), (80, 160), (20, 160),
             (20, 80), (0, 80)]
    folds = [fs.FoldLine((20, 120), (80, 120), 180, 1, order=1, label="A"),
             fs.FoldLine((20, 80), (80, 80), 180, 1, order=2, label="B")]
    for base in (None, 0, 1):
        lay = fs.fold_piece(piece, folds, base=base)
        assert lay.shift == [0.0, 0.0]
        assert [round(p.area) for p in lay.panels] == [2400, 2400, 8000]
        assert lay.warnings == [
            "What is folded inside fold B reaches 3.14 mm into its bend, "
            "and the fold cannot move out of its way. Shorten panel 1 by "
            "3.14 mm where it reaches into the fold."]
    # the other way -- up into the flap, for something folded in on the
    # body -- it is free to go: a pocket as tall as the body, say
    piece = [(0, -80), (100, -80), (100, 80), (80, 80), (80, 120),
             (20, 120), (20, 80), (0, 80)]
    folds = [fs.FoldLine((0, 0), (100, 0), 180, 1, order=1, label="A"),
             fs.FoldLine((20, 80), (80, 80), 180, 1, order=2, label="B")]
    lay = fs.fold_piece(piece, folds)
    assert _near(lay.shift, [0.0, math.pi], 1e-6) and not lay.warnings
    assert lay.shift_toward == [None, 1] and lay.shift_for == [None, 3]
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []


def test_the_panels_keep_their_numbers_when_a_fold_moves():
    # a body, a pocket as tall as it that folds up from below, and on top
    # a flap that leans a little to the right. Numbered by where they lie
    # they are flap, body, pocket; with the flap's fold moved 3.1 mm up
    # into it they would come out body, pocket, flap
    piece = [(0.4, -80), (100, -80), (100, 80), (80, 80), (80.5, 120),
             (20.5, 120), (20, 80), (0.4, 80)]
    folds = [fs.FoldLine((0.4, 0), (100, 0), 180, 1, order=1, label="A"),
             fs.FoldLine((20, 80), (80, 80), 180, 1, order=2, label="B")]
    lay = fs.fold_piece(piece, folds)
    assert _near(lay.shift, [0.0, math.pi], 1e-6)
    assert [(p.number, round(p.box[1], 2), round(p.box[3], 2))
            for p in lay.panels] == [(1, 83.14, 120.0), (2, 0.0, 83.14),
                                     (3, -80.0, 0.0)]
    assert lay.base == 1                              # the body, still
    assert lay.shift_toward == [None, 1] and lay.shift_for == [None, 3]
    # (cut along the moved line and numbered afresh)
    moved = [folds[0], fs.FoldLine((20, 80 + math.pi), (80, 80 + math.pi),
                                   180, 1, order=2, label="B")]
    regions, lines = fs.split_piece(fs.model_outline(piece), moved)
    afresh = fs.Layout(regions, moved, lines)
    assert [round(p.box[1], 2) for p in afresh.panels] == [0.0, -80.0, 83.14]
    # ... and the folds are the ones given, each still on its own row
    assert all(f is g for f, g in zip(lay.folds, folds))
    assert folds[1].a == (20, 80) and folds[1].b == (80, 80)


def test_leather_that_is_only_near_a_fold_is_not_inside_it():
    # a flap folds over on to a panel that reaches on round it, to a tab
    # stood up 40 mm beyond the fold: at the height of the flap, and past
    # where its bend starts, but nothing to do with it
    piece = [(0, 0), (100, 0), (100, 40), (60, 40), (60, 50), (140, 50),
             (140, 30), (110, 30), (110, 10), (140, 10), (140, 0), (160, 0),
             (160, 80), (0, 80)]
    folds = [fs.FoldLine((60, 0), (60, 40), 180, 1, order=2, label="A"),
             fs.FoldLine((140, 10), (140, 30), 90, 1, order=1, label="B")]
    # (with a bend radius the fold holds the flap 2 mm off the panel: the
    # tab is then level with the gap between them, 50 mm beyond the fold)
    for radius in (0.0, 1.0):
        lay = fs.fold_piece(piece, folds, radius=radius)
        assert [round(p.area) for p in lay.panels] == [8800, 1600, 600]
        assert lay.shift == [0.0, 0.0] and not lay.warnings
    # a flap folded forward over a narrow panel and the one beyond it;
    # then the narrow one is folded back, all but shut, and the flap
    # stands out past that fold like a flag -- on the outside of it
    strip = [(0, 0), (85, 0), (85, 50), (0, 50)]
    for angle in (180, 176):
        folds = [fs.FoldLine((40, 0), (40, 50), angle, -1, order=2),
                 fs.FoldLine((45, 0), (45, 50), 180, 1, order=1)]
        for base in (0, 2):
            lay = fs.fold_piece(strip, folds, base=base)
            assert lay.shift == [0.0, 0.0] and not lay.warnings


def test_leather_lying_across_a_fold_is_in_its_way_too():
    # the far end of a strip is folded back under it and then up round its
    # near end, on to the front (fold C forms 2 mm and half its bend past
    # its line to clear that end). Last, panel 2 is to fold over on to
    # panel 1 -- with panel 4 lying across the fold. Nowhere on panel 1
    # clears it: there is all of panel 1 in the way, the 3 mm that fold C
    # stands out beyond it, and half of fold A's own bend
    for last in (21.5, 24.0):
        outline, at = _strip(12, 30, 40, last)
        folds = [fs.FoldLine((x, 0), (x, 60), 180, way, order=step,
                             label=fs.letter(k))
                 for k, (x, way, step) in enumerate(zip(at, (1, -1, -1),
                                                        (3, 1, 2)))]
        for base in (0, 3):
            lay = fs.fold_piece(outline, folds, base=base)
            assert _near(lay.shift, [12 - math.pi, 0.0, 2 + math.pi], 1e-6)
            assert lay.shift_toward == [1, None, 4]
            assert lay.shift_for[0] is None
            # (fold C, off its line itself, is between the fold and the
            # panel that lies in it: nothing sure can be said of how
            # much to take off)
            assert lay.warnings == [
                "What is folded inside fold A reaches 18.08 mm into its "
                "bend, and the fold cannot move further out of its way: "
                "its bend would not fit before the edge of panel 1. Put "
                "fold C right first."]
    # (at 21.5 mm panel 4 stops short of the flat of panel 2, and short of
    # the round of fold A's bend: it lies in the leather of the bend)


def _roll(*widths, ways=None, base=None, **kw):
    """A strip folded right over at each place in turn, from the left:
    every fold to the front unless ``ways`` says, the last panel still
    unless ``base`` does."""
    outline, at = _strip(*widths)
    folds = [fs.FoldLine((x, 0), (x, 60), 180, way, order=k + 1,
                         label=fs.letter(k))
             for k, (x, way) in enumerate(zip(at, ways or [1] * len(at)))]
    return fs.fold_piece(outline, folds,
                         base=len(at) if base is None else base, **kw)


def test_what_it_says_to_shorten_puts_a_rolled_strip_right():
    # rolled up from the left: panel 1 on to panel 2, the two on to panel
    # 3, the three on to panel 4. Panel 1 just fits inside fold B; fold A,
    # at the end of panel 2, is half the leather of its bend too far
    # along for fold C to close on its line
    for base in (3, 0):
        lay = _roll(18.5 - math.pi, 18.5, 18.5, 60, base=base)
        assert _near(lay.shift, [0.0, 0.0, math.pi], 1e-6)
        assert lay.shift_toward == [None, None, 4] and not lay.warnings
        # panel 2 that much shorter would leave panel 1 as much too long
        # for fold B: it is named too, with the fold it would then be in
        assert [(panel, round(mm, 4), fold)
                for panel, mm, fold in lay.shift_trim[2]] \
            == [(2, 3.1416, None), (1, 3.1416, 1)]
        assert lay.shift_for == [None, None, 2]
        assert lay.remedy(2) == (
            "To have it fold on its line, shorten panel 2 by 3.14 mm where "
            "it reaches into the fold, and then panel 1 by 3.14 mm where "
            "it reaches into fold B.")
        assert lay.remedy(0) == lay.remedy(1) == ""
    # take it at its word, each of the two that much narrower: every fold
    # is on its line, and no more is said
    fixed = _roll(18.5 - math.pi - 3.14, 18.5 - 3.14, 18.5, 60)
    assert fixed.shift == [0.0, 0.0, 0.0] and not fixed.warnings
    assert fixed.shift_trim == [[], [], []] and fixed.remedy(2) == ""
    assert _inside_each_other(fixed.solid([1.0] * 3)) == []
    # (panel 2 alone would not have done: fold B then has panel 1 in its
    # way, and fold C closes over a fold that is off its line itself)
    part = _roll(18.5 - math.pi, 18.5 - 3.14, 18.5, 60)
    assert [round(mm, 2) for mm in part.shift] == [0.0, 3.14, 6.28]


def test_a_fold_that_closes_over_one_off_its_line_waits_for_it():
    # three panels of 18.5 mm rolled up on a longer one: fold B is off its
    # line for panel 1, and fold C, which closes over fold B, by three
    # times as much -- which is no measure of anything in the drawing
    lay = _roll(18.5, 18.5, 18.5, 60)
    assert _near(lay.shift, [0.0, math.pi, 3 * math.pi], 1e-6)
    assert lay.shift_toward == [None, 3, 4] and not lay.warnings
    assert lay.shift_trim[2] == [] and lay.shift_for == [None, 1, None]
    assert lay.shift_after == [None, None, 1]
    assert lay.remedy(1) == (
        "To have it fold on its line, shorten panel 1 by 3.14 mm where it "
        "reaches into the fold.")
    assert lay.remedy(2) == ("It closes over fold B, which is off its line "
                             "as well: put that right first.")
    # with fold B put right, fold C says what is left to do ...
    lay = _roll(18.5 - 3.14, 18.5, 18.5, 60)
    assert [round(mm, 2) for mm in lay.shift] == [0.0, 0.0, 3.14]
    assert lay.shift_after == [None, None, None]
    assert lay.remedy(2) == (
        "To have it fold on its line, shorten panel 2 by 3.14 mm where it "
        "reaches into the fold, and then panel 1 by 3.14 mm where it "
        "reaches into fold B.")
    # ... and with that done there is nothing off its line
    lay = _roll(18.5 - 2 * 3.14, 18.5 - 3.14, 18.5, 60)
    assert lay.shift == [0.0, 0.0, 0.0] and not lay.warnings


def test_no_panel_is_told_to_lose_more_than_it_has():
    # the four panels' strip with fold A made to the back like the other
    # two: panel 1 (26.5 mm) lies 8 mm and half a bend across the line
    # that fold B is to form on, and there is not that much room before
    # fold C (18.5 mm less half the leather of each bend)
    back = [-1, -1, -1]
    lay = _roll(26.5, 18.5, 18.5, 26.5, ways=back)
    assert _near(lay.shift, [0.0, 18.5 - 2.5 * math.pi, 0.0], 1e-6)
    assert lay.stuck == [False, True, True]
    # fold C is not moved round a fold that has leather in its bend --
    # nor told to take 25 mm off panel 2, which is 18.5 mm wide: it waits
    assert lay.warnings == [
        "What is folded inside fold B reaches 11.14 mm into its bend, and "
        "the fold cannot move further out of its way: its bend would not "
        "fit before fold C. Shorten panel 1 by 11.14 mm where it reaches "
        "into the fold.",
        "Fold C closes over what is left in the way of fold B: put fold B "
        "right first."]
    assert lay.shift_trim == [[], [], []] and lay.remedy(2) == ""
    # do as the warning says, and then as fold C does
    lay = _roll(26.5 - 11.14, 18.5, 18.5, 26.5, ways=back)
    assert [round(mm, 2) for mm in lay.shift] == [0.0, 0.0, 3.14]
    assert not lay.warnings and lay.stuck == [False, False, False]
    assert [(panel, round(mm, 2), fold)
            for panel, mm, fold in lay.shift_trim[2]] \
        == [(2, 3.14, None), (1, 3.14, 1)]
    lay = _roll(26.5 - 11.14 - 3.14, 18.5 - 3.14, 18.5, 26.5, ways=back)
    assert lay.shift == [0.0, 0.0, 0.0] and not lay.warnings
    assert _inside_each_other(lay.solid([1.0] * 3)) == []


def test_what_it_says_to_shorten_holds_on_random_strips():
    rng = random.Random(1)
    taken = chains = 0
    for _trial in range(40):
        n = rng.randint(3, 5)
        widths = [rng.choice([12.0, 18.5, 20.0, 26.5, 30.0, 40.0])
                  + rng.choice([0.0, rng.uniform(-3, 3)]) for _ in range(n)]
        ways = [rng.choice([1, -1]) for _ in range(n - 1)]
        order = rng.sample(range(1, n), n - 1)
        thick = rng.choice([1.0, 2.0, 3.0])

        def folded():
            outline, at = _strip(*widths)
            folds = [fs.FoldLine((x, 0), (x, 60), 180, way, order=step,
                                 label=fs.letter(k))
                     for k, (x, way, step) in enumerate(zip(at, ways, order))]
            return fs.fold_piece(outline, folds, thickness=thick)

        lay = folded()
        said = [k for k in sorted(range(n - 1), key=lambda k: lay.steps[k])
                if lay.shift_trim[k]]
        if lay.warnings or not said:
            continue
        # the first fold made that says what to shorten: each panel it
        # names that much narrower, as the report writes it
        k = said[0]
        taken += 1
        chains += len(lay.shift_trim[k]) > 1
        for panel, mm, _fold in lay.shift_trim[k]:
            assert round(mm, 2) < widths[panel - 1]
            widths[panel - 1] -= round(mm, 2)
        lay = folded()
        assert lay.shift[k] == 0.0 and not lay.warnings
    assert taken == 15 and chains == 1


def test_a_panel_that_reaches_a_fold_sideways_is_trimmed_on_that_side():
    # a 70 mm column with a wing on either side and on top a flap as wide
    # as itself. The flap comes down first, then the wings fold in over
    # it: it is the flap's two sides that reach into their folds
    def piece(left=0.0, right=0.0):
        outline = [(0, 0), (194, 0), (194, 90), (132, 90), (132 - right, 90),
                   (132 - right, 160), (62 + left, 160), (62 + left, 90),
                   (62, 90), (0, 90)]
        folds = [fs.FoldLine((62, 0), (62, 90), 180, 1, order=2, label="A"),
                 fs.FoldLine((132, 0), (132, 90), 180, 1, order=3, label="B"),
                 fs.FoldLine((62, 90), (132, 90), 180, 1, order=1, label="C")]
        return fs.fold_piece(outline, folds)

    lay = piece()
    assert _near(lay.shift, [math.pi, 1.5 * math.pi, 0.0], 1e-6)
    assert lay.shift_toward == [1, 4, None] and not lay.warnings
    assert lay.shift_for == [2, 2, None]              # the flap, both times
    assert lay.remedy(0) == (
        "To have it fold on its line, shorten panel 2 by 3.14 mm where it "
        "reaches into the fold.")
    assert lay.remedy(1) == (
        "To have it fold on its line, shorten panel 2 by 4.71 mm where it "
        "reaches into the fold.")
    # (not at its far end: 5 mm shorter, it is as much in the way)
    lay = piece(left=3.14, right=4.72)
    assert lay.shift == [0.0, 0.0, 0.0] and not lay.warnings
    assert _inside_each_other(lay.solid([1.0] * 3)) == []


def test_a_fold_across_leather_folded_inside_it_shows_it_cut():
    # a 15 mm hem folded down along the top of a piece, and then the piece
    # folded in half across it: the two halves of the hem lie inside that
    # fold, which runs right across them. The model can only show them
    # cut along it -- each ending in the bend, half its leather past the
    # line it starts on
    piece = [(0, 0), (100, 0), (100, 75), (0, 75)]
    folds = [fs.FoldLine((0, 60), (100, 60), 180, 1, order=1, label="A"),
             fs.FoldLine((50, 0), (50, 75), 180, 1, order=2, label="B")]
    said = ("Fold B runs across panels 1 and 3, which are folded inside it: "
            "each is shown cut along the fold and 4.71 mm into its bend. "
            "Cutting each back by that much at the fold clears it.")
    for base in (None, 0, 3):
        lay = fs.fold_piece(piece, folds, base=base)
        assert _near(lay.radius, [0.0, 2.0])         # round both halves
        assert lay.shift == [0.0, 0.0]               # no move clears them
        assert lay.warnings == [said] and lay.stuck == [False, True]
    found = {frozenset(pair[:2])
             for pair in _inside_each_other(lay.solid([1.0, 1.0]))}
    assert frozenset({("bend", 1), ("panel", 0)}) in found
    assert frozenset({("bend", 1), ("panel", 2)}) in found
    # cut back by that much -- a notch in the hem where the fold comes --
    # the hem is clear of the bend and there is nothing to say
    notch = [(0, 0), (100, 0), (100, 75), (54.72, 75), (54.72, 60),
             (45.28, 60), (45.28, 75), (0, 75)]
    lay = fs.fold_piece(notch, folds)
    assert _near(lay.radius, [0.0, 2.0]) and len(lay.panels) == 4
    assert lay.stuck == [False, False] and not lay.warnings
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []
    # with the hem on the outside nothing is inside the fold at all
    out = [folds[0], fs.FoldLine((50, 0), (50, 75), 180, -1, order=2)]
    lay = fs.fold_piece(piece, out)
    assert _near(lay.radius, [0.0, 0.0]) and not lay.warnings
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []
    # a letter fold across the hem: the cover's fold goes round the flap
    # as it would without a hem, but it is not said to have gone round
    # what is inside it, nor how to put it back: the hem is still in it
    piece = [(0, 0), (120, 0), (120, 75), (0, 75)]
    folds = [fs.FoldLine((0, 60), (120, 60), 180, 1, order=1, label="A"),
             fs.FoldLine((40, 0), (40, 75), 180, 1, order=2, label="B"),
             fs.FoldLine((80, 0), (80, 75), 180, 1, order=3, label="C")]
    lay = fs.fold_piece(piece, folds)
    assert lay.shift[:2] == [0.0, 0.0] and lay.shift[2] > 4.0
    assert lay.stuck == [False, True, True]
    assert lay.shift_trim == [[], [], []] and lay.remedy(2) == ""
    assert [w[:38] for w in lay.warnings] == [
        "Fold B runs across panels 1 and 3, whi",
        "Fold C runs across panels 3 and 5, whi"]


def test_a_fold_that_moves_says_what_it_then_brings_down_in_other_leather():
    # panel 4 is stood up first, a wall. Flap 1 folds under panel 2, and
    # the two under panel 3, which is 2 mm longer: they stop short of the
    # wall. But the flap is as long as panel 2, so the second fold forms
    # 3.14 mm further on -- and what it carries then ends in the wall
    def stack(flap):
        outline = [(0, 0), (flap + 73, 0), (flap + 73, 30), (0, 30)]
        folds = [fs.FoldLine((x, 0), (x, 30), angle, -1, order=step,
                             label=fs.letter(k))
                 for k, (x, angle, step) in enumerate((
                     (flap, 180, 2), (flap + 28, 180, 3),
                     (flap + 58, 90, 1)))]
        return [fs.fold_piece(outline, folds, base=base) for base in (2, 0)]

    for lay in stack(28):
        assert _near(lay.shift, [0.0, math.pi, 0.0], 1e-6)
        assert lay.shift_toward == [None, 3, None]
        assert lay.warnings == [
            "With fold B off its line, panel 2 and panel 4 are drawn 3.50 mm "
            "into each other (and they are not the only ones)."]
        assert lay.stuck == [False, False, False]    # its own bend is clear
        found = {frozenset(pair[:2])
                 for pair in _inside_each_other(lay.solid([1.0] * 3))}
        assert found == {frozenset({("panel", 0), ("panel", 3)}),
                         frozenset({("panel", 1), ("panel", 3)})}
        # what it says to shorten puts that right as well
        assert lay.remedy(1) == (
            "To have it fold on its line, shorten panel 1 by 3.14 mm where "
            "it reaches into the fold.")
    for lay in stack(28 - 3.14):
        assert lay.shift == [0.0, 0.0, 0.0] and not lay.warnings
        assert _inside_each_other(lay.solid([1.0] * 3)) == []
    # a letter fold whose cover ends in a lip turned down past the folded
    # edge: with the cover's fold 3.14 mm further on, the lip comes down
    # in the fold of the flap
    outline, at = _strip(40, 40, 46, 12)
    folds = [fs.FoldLine((x, 0), (x, 60), angle, 1, order=k + 1,
                         label=fs.letter(k))
             for k, (x, angle) in enumerate(zip(at, (180, 180, 90)))]
    lay = fs.fold_piece(outline, folds, base=1)
    assert _near(lay.shift, [0.0, math.pi, 0.0], 1e-6)
    assert lay.warnings == [
        "With fold B off its line, panel 4 and fold A are drawn 1.50 mm into "
        "each other."]
    # (a quarter of a millimetre is leather inside leather as well: a
    # stack 3.2 mm short of its wall, in 1.5 mm leather)
    outline, at = _strip(48.9, 50, 53.2, 22.3)
    folds = [fs.FoldLine((x, 0), (x, 60), angle, -1, order=step,
                         label=fs.letter(k))
             for k, (x, angle, step) in enumerate(zip(at, (180, 180, 90),
                                                      (2, 3, 1)))]
    lay = fs.fold_piece(outline, folds, thickness=1.5, base=1)
    assert lay.warnings == [
        "With fold B off its line, panel 4 and fold A are drawn 0.22 mm into "
        "each other."]



def test_a_moved_fold_says_when_its_own_bend_comes_down_in_a_wall():
    # a flap on panel 3's long edge folds down first, its side edge on
    # fold line B; panel 1 folds over it, panel 4 under panel 3, and
    # panel 5 is stood up beside the folded edge. Fold B forms 3.14 mm
    # further on to go round the flap -- and its own bend then comes
    # down in the wall, whether the wall is stood up last or first
    outline = [(0, 0), (127, 0), (127, 30), (60, 30), (60, 50), (30, 50),
               (30, 30), (0, 30)]
    for orders in ((1, 2, 3, 4), (2, 3, 4, 1)):
        folds = [fs.FoldLine(a, b, angle, way, order=step, label=name)
                 for (a, b, angle, way, name), step in zip((
                     ((30, 30), (60, 30), 180, 1, "F"),
                     ((30, 0), (30, 30), 180, 1, "B"),
                     ((70, 0), (70, 30), 180, -1, "C"),
                     ((112, 0), (112, 30), 90, -1, "D")), orders)]
        lay = fs.fold_piece(outline, folds, thickness=2.0)
        assert _near(lay.shift, [0.0, math.pi, 0.0, 0.0], 1e-6)
        assert lay.warnings == [
            "With fold B off its line, panel 5 and fold B are drawn 1.79 mm "
            "into each other."]
        found = _inside_each_other(lay.solid([1.0] * 4))
        assert {frozenset(pair[:2]) for pair in found} == {
            frozenset({("panel", 4), ("bend", 1)})}
        assert abs(max(d for _a, _b, d in found) - 1.7854) < 1e-3
    # the flap 3.14 mm narrower: the fold stays on its line, and nothing
    # is in anything
    outline[5:7] = [(33.14, 50), (33.14, 30)]
    folds = [fs.FoldLine((33.14, 30), (60, 30), 180, 1, order=1, label="F"),
             fs.FoldLine((30, 0), (30, 30), 180, 1, order=2, label="B"),
             fs.FoldLine((70, 0), (70, 30), 180, -1, order=3, label="C"),
             fs.FoldLine((112, 0), (112, 30), 90, -1, order=4, label="D")]
    lay = fs.fold_piece(outline, folds, thickness=2.0)
    assert lay.shift == [0.0, 0.0, 0.0, 0.0] and not lay.warnings
    assert _inside_each_other(lay.solid([1.0] * 4)) == []


def test_a_moved_fold_says_when_the_panel_it_moved_on_to_meets_a_wall():
    # the same, with the flap 6 mm past fold line B: the fold goes 9.14 mm
    # further on, and the panel it now ends on -- the still side of the
    # moved hinge -- comes down 1.91 mm into the wall
    outline = [(0, 0), (116.7, 0), (116.7, 30), (60, 30), (60, 50),
               (24, 50), (24, 32), (30, 32), (30, 30), (0, 30)]
    folds = [fs.FoldLine((30, 30), (60, 30), 180, 1, order=1, label="F"),
             fs.FoldLine((30, 0), (30, 30), 180, 1, order=2, label="B"),
             fs.FoldLine((70, 0), (70, 30), 180, -1, order=3, label="C"),
             fs.FoldLine((114, 0), (114, 30), 90, -1, order=4, label="D")]
    lay = fs.fold_piece(outline, folds, thickness=2.0)
    assert round(lay.shift[1], 3) == 9.142
    assert lay.warnings == [
        "With fold B off its line, panel 3 and panel 5 are drawn 1.91 mm "
        "into each other."]


def test_what_a_moved_fold_brings_down_is_looked_for_at_every_whole_step():
    # the wall is stood up first and tipped away again by the last fold:
    # folded right up nothing is in anything, but at the end of step 3
    # what fold B carries is 3.50 mm into the wall
    folds = [fs.FoldLine((x, 0), (x, 30), angle, way, order=step,
                         label=name)
             for x, angle, way, step, name in ((28, 180, -1, 2, "A"),
                                               (56, 180, -1, 3, "B"),
                                               (86, 90, -1, 1, "C"),
                                               (82, 90, 1, 4, "E"))]
    lay = fs.fold_piece([(0, 0), (101, 0), (101, 30), (0, 30)], folds,
                        thickness=2.0, base=2)
    assert _near(lay.shift, [0.0, math.pi, 0.0, 0.0], 1e-3)
    assert _inside_each_other(lay.solid(lay.fractions(4.0))) == []
    deep = {frozenset(pair[:2]): d for *pair, d
            in _inside_each_other(lay.solid(lay.fractions(3.0)))}
    assert abs(deep[frozenset({("panel", 1), ("panel", 4)})] - 3.4978) \
        < 1e-3
    assert lay.warnings == [
        "With fold B off its line, panel 2 and panel 5 are drawn 3.50 mm "
        "into each other (and they are not the only ones)."]

def test_nothing_is_said_to_shorten_that_would_leave_folds_too_close():
    # panel 3 is 8 mm wide, and the folds either side of it bend at 2 and
    # at 3 mm: their bends need 11 mm between them. They have it only
    # because fold C stands 3.28 mm off its line, for panel 1
    def narrow(first):
        outline, at = _strip(first, 25, 8, 25)
        folds = [fs.FoldLine((x, 0), (x, 60), 180, way, order=step,
                             label=fs.letter(k))
                 for k, (x, way, step) in enumerate(zip(at, (1, -1, 1),
                                                        (1, 3, 2)))]
        return fs.fold_piece(outline, folds, radius=2.0, base=1)

    lay = narrow(30)
    assert _near(lay.radius, [2.0, 2.0, 3.0])
    assert [round(mm, 2) for mm in lay.shift] == [0.0, 0.0, 3.28]
    assert lay.shift_toward == [None, None, 4] and not lay.warnings
    # with panel 1 that much shorter the fold would be back on its line,
    # and no better off: so that is not said
    assert lay.shift_trim == [[], [], []] and lay.remedy(2) == ""
    assert lay.shift_for == [None, None, None]
    lay = narrow(30 - 3.29)
    assert lay.shift == [0.0, 0.0, 0.0]
    assert lay.warnings == ["Folds B and C are 8.0 mm apart, but their bends "
                            "need 11.0 mm between them."]


def test_a_fold_is_not_told_to_mend_what_is_wrong_before_it():
    # five panels, the first fold made second and unable to be: panel 3
    # lies across it, and it goes as far as it can for that, 13.79 mm.
    # Fold C, made after it, is on its line only because of that; and
    # fold D, 3.21 mm off its own for what is inside it, could not be
    # tried on its line without fold C being in trouble first. That is
    # not fold D's to mend: nothing is said to shorten for it
    outline, at = _strip(18.5, 30, 18.5, 20, 18.5)
    folds = [fs.FoldLine((x, 0), (x, 60), 180, way, radius=r, order=step,
                         label=fs.letter(k))
             for k, (x, way, r, step) in enumerate(zip(
                 at, (-1, -1, 1, 1), (0.0, 0.0, 0.0, -1.0), (2, 1, 3, 4)))]
    lay = fs.fold_piece(outline, folds, thickness=3.0, radius=1.0, base=3)
    assert [round(mm, 2) for mm in lay.shift] == [13.79, 0.0, 0.0, 3.21]
    assert lay.stuck == [True, False, False, False]
    assert lay.shift_trim == [[], [], [], []] and lay.remedy(3) == ""
    assert lay.shift_after == [None, None, None, None]
    assert lay.warnings == [
        "What is folded inside fold A reaches 31.71 mm into its bend, and "
        "the fold cannot move further out of its way: its bend would not "
        "fit before the edge of panel 1."]


def test_a_fold_not_quite_shut_is_measured_as_its_panels_lie():
    # a letter fold in equal thirds whose cover closes to within 2
    # degrees: the flap still reaches the line the cover folds on, half
    # the leather of that bend past the place where it starts -- measured
    # along the panel it came in on, whichever panel stays still
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1, label="A"),
             fs.FoldLine((160, 0), (160, 100), 178, 1, order=2, label="B")]
    for base in (0, 1, 2):
        lay = fs.fold_piece(RECT, folds, base=base)
        assert abs(lay.shift[1] - 0.5 * lay.used[1]) < 1e-6
        assert lay.shift_toward == [None, 3] and lay.shift_for == [None, 1]
        assert not lay.warnings
    # (with the cover still, the flap ends up 2 degrees off it and a
    # little way in it. So it would with the fold on its line: that is
    # not the move's doing, and is not said to be)
    assert _inside_each_other(lay.solid([1.0, 1.0])) != []


def test_a_fold_with_leather_left_in_its_bend_is_not_pushed_along():
    # panel 2 (50 mm) lies across the lines of both folds C and D. Fold C
    # goes as far as fold D lets it and still has panel 2 in its bend;
    # fold D, which closes over the same leather, stays on its line (it
    # was once moved 21 mm on, to make room for fold C's bend)
    outline, at = _strip(20, 50, 18.5, 25, 30)
    folds = [fs.FoldLine((x, 0), (x, 60), 180, way, order=k + 1,
                         label=fs.letter(k))
             for k, (x, way) in enumerate(zip(at, (-1, 1, 1, -1)))]
    lay = fs.fold_piece(outline, folds, thickness=1.0, radius=0.5)
    assert lay.shift[:2] == [0.0, 0.0] and lay.shift[3] == 0.0
    assert abs(lay.lines[3][0][0] - 113.5) < 1e-9
    assert lay.stuck == [False, False, True, True]
    assert lay.shift_toward == [None, None, 4, None]
    assert lay.warnings[1] == ("Fold D closes over what is left in the way "
                               "of fold C: put fold C right first.")


def test_a_fold_slides_along_a_slanting_edge_as_it_moves():
    # a letter fold in equal thirds on a parallelogram, the folds running
    # with its slanting ends: the cover's fold meets the long edges at 27
    # degrees, and its ends slide 7 mm along them as it moves 3.14 mm
    piece = [(0, 0), (240, 0), (440, 100), (200, 100)]
    folds = [fs.FoldLine((80, 0), (280, 100), 180, 1, order=1, label="A"),
             fs.FoldLine((160, 0), (360, 100), 180, 1, order=2, label="B")]
    lay = fs.fold_piece(piece, folds)
    assert _near(lay.shift, [0.0, math.pi], 1e-6) and not lay.warnings
    assert lay.shift_toward == [None, 3] and lay.shift_for == [None, 1]
    assert [round(p.area, 1) for p in lay.panels] == [8000.0, 8702.5, 7297.5]
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []


def test_a_fold_is_put_back_where_its_bend_still_fits():
    # folds C and D each go as far as they can for panel 2, which lies
    # across both; then fold D turns out to bend wider than it did where
    # it started (it has more to go round). It is put back until its bend
    # ends where the leather does
    outline, at = _strip(8, 51.74, 26.5, 10, 20)
    folds = [fs.FoldLine((x, 0), (x, 60), 180, way, order=step,
                         label=fs.letter(k))
             for k, (x, way, step) in enumerate(zip(at, (-1, 1, 1, 1),
                                                    (1, 2, 4, 3)))]
    lay = fs.fold_piece(outline, folds, thickness=1.0, radius=1.0, base=0)
    assert _near(lay.radius, [1.0, 1.0, 5.0, 3.0])
    ends = [(ln[0][0] - 0.5 * used, ln[0][0] + 0.5 * used)
            for ln, used in zip(lay.lines, lay.used)]
    assert abs(ends[3][1] - 116.24) < 1e-6          # the end of the strip
    assert abs(ends[2][1] - ends[3][0]) < 1e-6      # and fold C up to it
    assert lay.stuck == [False, False, True, True] and len(lay.warnings) == 2


def test_a_move_that_brings_nothing_clear_is_taken_back():
    # four folds that cross on an L-shaped piece, two of them 1.2 mm
    # apart. What is in the way of fold C moves with it: a move leaves it
    # wanting to go as far again, so it is put back on its line (it once
    # crept on, 0.07 mm a round, until the rounds ran out)
    piece = [(0, 0), (64.56, 0), (64.56, 19.94), (44.57, 19.94),
             (44.57, 52.92), (0, 52.92)]
    folds = [fs.FoldLine(a, b, angle, way, radius=r, order=step,
                         label=fs.letter(k))
             for k, (a, b, angle, way, r, step) in enumerate((
                 ((69.56, 24.97), (-5, 32.66), 178, -1, 0.0, 2),
                 ((8.33, -5), (8.33, 57.92), 180, 1, -1.0, 2),
                 ((69.56, 11.26), (-5, 11.26), 178, 1, -1.0, 4),
                 ((7.17, -5), (7.17, 57.92), 178, -1, 1.0, 4)))]
    lay = fs.fold_piece(piece, folds, thickness=1.0, radius=1.0, base=1)
    assert lay.shift == [0.0] * 4 and lay.to_move is None
    assert lay.stuck == [False, False, True, True]
    assert lay.warnings[0] == ("Folds B and D are 1.2 mm apart, but their "
                               "bends need 5.7 mm between them.")
    assert lay.warnings[3] == ("Fold D closes over what is left in the way "
                               "of fold C: put fold C right first.")


def test_the_strips_of_a_bend_that_has_moved_are_not_inside_each_other():
    # a flap on the left whose foot stops half a millimetre short of the
    # corner of the body. Its fold moves 0.7 mm on to the body for the
    # flap on the right, and the body's strips of the bend are then
    # L-shaped: parted along a slant, the bend twisted the two parts into
    # each other
    piece = [(0, 0), (58, 0), (58, 10), (112, 10), (112, 50), (58, 50),
             (58, 60), (0, 60), (0, 50), (-30, 50), (-30, 0.5), (0, 0.5)]
    folds = [fs.FoldLine((58, 10), (58, 50), 180, 1, order=1, label="R"),
             fs.FoldLine((0, 0.5), (0, 50), 180, 1, order=2, label="L")]
    lay = fs.fold_piece(piece, folds, thickness=3.0)
    assert _near(lay.shift, [0.0, 1.5 * math.pi - 4.0], 1e-6)
    assert not lay.warnings
    assert abs(sum(c.area for c in lay.cells) - 7125.0) < 1e-6
    assert all(pc.is_convex(c.pts, 1e-9) for c in lay.cells)
    for when in (1.0, 1.5, 2.0):
        assert _inside_each_other(lay.solid(lay.fractions(when))) == []


def test_room_for_a_bend_is_taken_where_the_bend_is():
    # A flap is tucked in under a keeper whose own fold bends at 2 mm:
    # while it ends within the round of that bend it lies under the
    # keeper, as it always did -- it was once thrown over the keeper as
    # soon as it passed the line the bend starts on
    def tuck(flap):
        outline, at = _strip(flap, 18.85, 8)
        folds = [fs.FoldLine((at[0], 0), (at[0], 60), 180, 1, order=2),
                 fs.FoldLine((at[1], 0), (at[1], 60), 180, 1, radius=2.0,
                             order=1)]
        return fs.fold_piece(outline, folds, base=1, radius=0.5)

    for flap in (14.0, 14.6, 15.5):
        lay = tuck(flap)
        assert _near(lay.radius, [0.5, 2.0]) and lay.shift == [0.0, 0.0]
        assert _heights(lay) == [3.0, 0.0, 6.0] and not lay.warnings
        assert _inside_each_other(lay.solid([1.0, 1.0])) == []
    lay = tuck(17.0)                                 # too long: over it
    assert _near(lay.radius, [3.0, 2.0])
    assert _heights(lay) == [8.0, 0.0, 6.0]
    # Four folds with radii of their own, the last closing 0.4 mm clear
    # of the round of the first: it keeps the radius it had, 2.5 mm (the
    # round was taken for solid from the line it starts on, and the fold
    # given 6.5 mm to go over it)
    outline = [(0, 0), (256.4, 0), (256.4, 60), (0, 60)]
    folds = [fs.FoldLine((x, 0), (x, 60), 180, way, radius=r, order=step)
             for x, way, r, step in ((83.2, -1, 3.0, 2), (108.2, 1, 1.5, 3),
                                     (173.2, 1, -1.0, 4),
                                     (216.4, -1, 4.0, 1))]
    lay = fs.fold_piece(outline, folds, radius=1.0, base=2)
    assert _near(lay.radius, [3.0, 1.5, 2.5, 4.0])
    assert _heights(lay) == [13.0, 5.0, 0.0, 7.0, 17.0]
    assert lay.shift == [0.0] * 4 and not lay.warnings
    for when in (3.5, 3.9, 4.0):
        assert _inside_each_other(lay.solid(lay.fractions(when))) == []


def test_folds_that_do_not_close_flat_are_left_where_they_are_drawn():
    # a letter fold in equal thirds whose cover stops short of flat: the
    # flap is as far along as ever, but there is no closed bend for it to
    # be inside (175 degrees and over is taken for flat)
    for angle in (150, 170, 174):
        folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1),
                 fs.FoldLine((160, 0), (160, 100), angle, 1, radius=1.0,
                             order=2)]
        for base in (0, 2):
            lay = fs.fold_piece(RECT, folds, base=base)
            assert lay.shift == [0.0, 0.0] and not lay.warnings
            assert lay.layers == [0, 0] and lay.stuck == [False, False]
    folds[1].angle = 176
    lay = fs.fold_piece(RECT, folds)
    assert lay.shift[1] > 3.0 and lay.shift_toward == [None, 3]


def test_orders_that_cannot_be_folded_are_drawn_as_they_always_were():
    # panel 3 is folded back first, under panel 2 and on past the line
    # panel 1 is then to fold under on: it ends up in the gap fold A
    # leaves, with panel 1 gone through it on the way. Nothing moves for
    # it, and nothing is made of it
    folds = [fs.FoldLine((41.2, 0), (41.2, 30), 180, -1, radius=2.6, order=1),
             fs.FoldLine((60, 0), (60, 30), 180, -1, order=2)]
    lay = fs.fold_piece([(0, 0), (72, 0), (72, 30), (0, 30)], folds,
                        thickness=4.0, base=1)
    assert _near(lay.radius, [2.6, 0.0]) and lay.shift == [0.0, 0.0]
    assert not lay.warnings
    assert _inside_each_other(lay.solid([1.0, 1.0])) == []
    # three walls turn the far end of a strip back under panel 2, and the
    # last of them stands up to it; then panel 1 folds under as well,
    # across the top of that wall. Its edge is level with the fold, 9.5
    # mm beyond it: beside the fold, not inside it
    outline, at = _strip(25.7, 29.2, 40.9, 38.7, 40.2)
    folds = [fs.FoldLine((x, 0), (x, 60), angle, -1, radius=r, order=step)
             for x, angle, r, step in zip(at, (180, 90, 90, 90),
                                          (0.39, -1.0, -1.0, 0.24),
                                          (4, 3, 1, 2))]
    lay = fs.fold_piece(outline, folds, thickness=0.5, base=1)
    assert lay.shift == [0.0] * 4 and not lay.warnings
    # the four corners of a square folded to the middle: each flap comes
    # to a point at either end of its fold, where there is no bend to
    # speak of for the next flap to be inside. All four lie on the square
    square = [(0, 0), (80, 0), (80, 80), (0, 80)]
    folds = [fs.FoldLine(a, b, 180, 1, order=k + 1)
             for k, (a, b) in enumerate((((40, 0), (0, 40)),
                                         ((80, 40), (40, 0)),
                                         ((40, 80), (80, 40)),
                                         ((0, 40), (40, 80))))]
    lay = fs.fold_piece(square, folds, thickness=1.0)
    assert _near(lay.radius, [0.0] * 4) and lay.shift == [0.0] * 4
    assert _heights(lay) == [1.0, 1.0, 0.0, 1.0, 1.0] and not lay.warnings


def test_a_net_cannot_move_its_folds_and_says_what_is_in_the_way():
    def rect(x, y, w, h):
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    # three panels drawn edge to edge, folded as a letter in equal thirds
    lay = fs.fold_net([rect(0, 0, 80, 100), rect(80, 0, 80, 100),
                       rect(160, 0, 80, 100)], angle=180.0)
    assert lay.steps == [1, 2] and _near(lay.radius, [0.0, 1.0])
    assert lay.shift == [0.0, 0.0]
    assert lay.shift_toward == lay.shift_for == [None, None]
    assert lay.lines[1][0] == (160, 0)               # where it was drawn
    assert lay.warnings == [
        "What is folded inside fold B reaches 3.14 mm into its bend. "
        "Shorten panel 1 by 3.14 mm where it reaches into the fold."]
    # with the first panel that much narrower there is nothing to say
    lay = fs.fold_net([rect(3.2, 0, 76.8, 100), rect(80, 0, 80, 100),
                       rect(160, 0, 80, 100)], angle=180.0)
    assert lay.shift == [0.0, 0.0] and not lay.warnings


# -- panels drawn edge to edge ------------------------------------------------

def test_a_net_folds_along_the_edges_its_panels_share():
    def rect(x, y, w, h):
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    lay = fs.fold_net([rect(0, 0, 60, 40), rect(-30, 0, 30, 40),
                       rect(60, 0, 30, 40), rect(0, 40, 60, 30),
                       rect(0, -30, 60, 30)], thickness=2.0)
    assert len(lay.panels) == 5 and len(lay.tree) == 4
    assert [f.label for f in lay.folds] == ["A", "B", "C", "D"]
    assert lay.panels[lay.base].area == 2400
    fr = _folded(lay)
    walls = [p for p in lay.panels if p.index != lay.base]
    assert all(abs(fr[p.index][3][2]) < 1e-9 for p in walls)
    assert abs(sum(c.area for c in lay.cells) - 8400) < 1e-6
    # panels that only touch at a corner, or not at all, are not joined
    apart = fs.net_regions([rect(0, 0, 10, 10), rect(10, 10, 10, 10),
                            rect(40, 0, 10, 10)])
    assert apart[1] == []


# -- robustness ---------------------------------------------------------------

def test_random_pieces_and_fold_lines_fold_without_trouble():
    rng = random.Random(11)
    for _trial in range(60):
        n = rng.randint(4, 12)
        poly = []
        for i in range(n):
            a = 2 * math.pi * (i + rng.uniform(0.25, 0.75)) / n
            r = rng.uniform(15, 60)
            poly.append((math.cos(a) * r, math.sin(a) * r))
        folds = []
        for k in range(rng.randint(1, 4)):
            if rng.random() < 0.3:
                a, b = rng.sample(poly, 2)       # corner to corner
            else:
                a = (rng.uniform(-40, 40), rng.uniform(-40, 40))
                th, ln = rng.uniform(0, math.pi), rng.uniform(5, 150)
                b = (a[0] + math.cos(th) * ln, a[1] + math.sin(th) * ln)
            folds.append(fs.FoldLine(
                a, b, rng.choice([30, 90, 135, 180, 180]),
                rng.choice([1, -1]), radius=rng.choice([-1, -1, 0, 1.5, 4]),
                order=rng.choice([0, 1, 2, 3]), label=fs.letter(k)))
        holes = [(rng.uniform(-50, 50), rng.uniform(-50, 50), 1.0)
                 for _ in range(20)]
        lay = fs.fold_piece(poly, folds, thickness=rng.choice([0.5, 2.0, 4.0]),
                            radius=rng.choice([0.0, 1.0]), holes=holes)
        area = abs(pc.area(poly))
        assert abs(sum(c.area for c in lay.cells) - area) < 2e-3 * area
        assert all(pc.is_convex(c.pts, 1e-6) for c in lay.cells)
        for t in (0.0, 0.37 * lay.n_steps, float(lay.n_steps)):
            fr = lay.fractions(t)
            tree = bsp.build(lay.solid(fr))
            assert bsp.back_to_front(tree, 0.3, -0.5, 0.8)
            lay.fold_marks(fr)
            lay.panel_marks(fr)
        lay.lineup()


def test_letters():
    assert [fs.letter(k) for k in (0, 1, 25, 26, 27, 51, 52)] == \
        ["A", "B", "Z", "AA", "AB", "AZ", "BA"]


def test_a_busy_outline_is_thinned_for_the_model():
    n = 1000
    top = [(240 * k / n, 100 + 3 * math.sin(2 * math.pi * 40 * k / n))
           for k in range(n + 1)]
    bot = [(240 * k / n, -3 * math.sin(2 * math.pi * 40 * k / n))
           for k in range(n + 1)]
    scalloped = bot + top[::-1]
    ring = fs.model_outline(scalloped)
    assert len(ring) <= 260
    area = pc.area(pc.ccw(scalloped))
    assert abs(pc.area(ring) - area) < 0.01 * area
    lay = fs.fold_piece(scalloped, [fs.FoldLine((80, -10), (80, 110), 180, 1)])
    assert len(lay.panels) == 2 and len(lay.cells) < 200
    # an ordinary outline is followed as it is; a doubled point is dropped
    assert fs.model_outline(RECT) == RECT
    ring = fs.model_outline(RECT[::-1] + [RECT[-1]])   # the wrong way round
    assert len(ring) == 4 and set(ring) == set(RECT) and pc.area(ring) > 0


def test_the_hole_check_agrees_with_measuring_every_pair():
    rng = random.Random(1)
    holes = [(rng.uniform(2, 158), rng.uniform(2, 98), 1.0)
             for _ in range(1500)]
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                        holes=holes)
    check = lay.lineup(tol=0.75, near=4.0)
    findings, bad = check.findings, check.off
    assert len(findings) == 1
    left = [(k, h) for k, h in enumerate(holes) if lay.hole_panel[k] == 0
            and lay.hole_flat[k]]
    right = [(k, (160 - h[0], h[1])) for k, h in enumerate(holes)
             if lay.hole_panel[k] == 1 and lay.hole_flat[k]]
    want = set()
    off = lone = 0
    for mine, theirs in ((left, right), (right, left)):
        for k, p in mine:
            d = min(math.hypot(p[0] - q[1][0], p[1] - q[1][1])
                    for q in theirs)
            if 0.75 < d <= 4.0:
                want.add(k)
                off += 1
            elif d > 4.0:
                lone += 1
    assert bad == want and len(bad) > 100
    assert (findings[0].off, findings[0].lone) == (off, lone)
    assert findings[0].holes == len(left) + len(right)
    # every hole where the two panels lie together is one of the three
    assert len(check.state) == findings[0].holes
    assert len(check.holes(fs.LONE)) == lone
    assert len(check.holes(fs.MEETS)) == findings[0].met

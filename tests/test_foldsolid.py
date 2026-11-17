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
    lay = _trifold()
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
    for t, on_faces in ((0.0, 6), (1.0, 5)):
        dots = polys = marked = 0
        for c in lay.solid(lay.fractions(t), bad=[1]):
            for f in c.faces:
                for kind, geo, style in f.decals or ():
                    assert f.kind in (bsp.FRONT, bsp.BACK)
                    if kind == bsp.DOTS:
                        dots += len(geo)
                        marked += len(geo) if style[1] else 0
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
    findings, bad = _stitched().lineup()
    assert len(findings) == 1 and not bad
    f = findings[0]
    assert (f.a, f.b, f.holes, f.off, f.lone) == (1, 2, 20, 0, 0)


def test_holes_a_little_out_are_marked_and_measured():
    findings, bad = _stitched(shift=1.5).lineup()
    f = findings[0]
    assert f.off == 20 and abs(f.worst - 1.5) < 1e-9 and f.lone == 0
    assert bad == set(range(20))
    assert not _stitched(shift=0.5).lineup()[1]        # near enough


def test_a_hole_with_nothing_near_it_is_counted_but_not_marked():
    findings, bad = _stitched(extra=[(40, 60, 1.0)]).lineup()
    f = findings[0]
    assert (f.holes, f.off, f.lone) == (21, 0, 1) and not bad
    # a flap over a panel with no holes of its own: nothing to report
    lay = fs.fold_piece(HALF, [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                        holes=[(20 + 5 * k, 10, 1.0) for k in range(10)])
    assert lay.lineup() == ([], set())
    # and nothing lies on anything until it is folded flat
    assert _one(angle=90.0).lineup() == ([], set())


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
    findings, bad = lay.lineup(tol=0.75, near=4.0)
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

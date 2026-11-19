"""The polygon tools the fold engine is built on: cutting an outline along a
line, splitting it into convex parts, thinning out a flattened curve."""

import math
import random

from leathercad import polycut as pc

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
# a cross: a 10 mm square with a 10 mm arm on each side
CROSS = [(0, 0), (0, -10), (10, -10), (10, 0), (20, 0), (20, 10), (10, 10),
         (10, 20), (0, 20), (0, 10), (-10, 10), (-10, 0)]


def _outline(n):
    return [pc.OUTLINE] * n


def _flag_length(pts, flags, flag):
    n = len(pts)
    return sum(math.dist(pts[i], pts[(i + 1) % n])
               for i in range(n) if flags[i] == flag)


def _star(rng):
    """A random simple polygon: one point in each slice round the origin."""
    n = rng.randint(4, 14)
    out = []
    for i in range(n):
        a = 2 * math.pi * (i + rng.uniform(0.25, 0.75)) / n
        r = rng.uniform(3, 20)
        out.append((math.cos(a) * r, math.sin(a) * r))
    return out


def test_cut_square_in_two_keeps_flags_and_sides():
    pieces = pc.cut(SQUARE, _outline(4), (5, -3), (0, 1), None, 7)
    assert len(pieces) == 2
    assert abs(sum(pc.area(p) for p, _f in pieces) - 100.0) < 1e-9
    for pts, flags in pieces:
        assert pc.area(pts) > 0                      # counter-clockwise
        assert flags.count(7) == 1 and flags.count(pc.OUTLINE) == 3
        i = flags.index(7)
        a, b = pts[i], pts[(i + 1) % len(pts)]
        along = b[1] - a[1]                          # the cut runs along +y
        xs = [p[0] for p in pts]
        # the piece whose new edge runs along the line is on its left
        assert (max(xs) <= 5 + 1e-9) == (along > 0)


def test_line_along_an_outline_edge_is_not_a_cut():
    """The line x = 0 runs along two edges of the cross and through the
    foot of its left arm: only the stretch through leather is cut."""
    assert pc.spans(CROSS, (0, 0), (0, 1)) == [(0, 10)]
    for a, e in (((0, 0), (0, 1)), ((0, 10), (0, -1))):
        pieces = pc.cut(CROSS, _outline(12), a, e, None, 3)
        assert sorted(len(p) for p, _f in pieces) == [4, 10]
        arm = min(pieces, key=lambda pf: len(pf[0]))
        assert abs(pc.area(arm[0]) - 100.0) < 1e-9
        for pts, flags in pieces:
            assert flags.count(3) == 1
            # no outline was turned into a cut, none lost
        assert abs(sum(_flag_length(p, f, pc.OUTLINE) for p, f in pieces)
                   - 120.0) < 1e-9


def test_cut_only_the_stretches_asked_for():
    u = [(0, 0), (30, 0), (30, 20), (20, 20), (20, 5), (10, 5), (10, 20),
         (0, 20)]
    assert pc.spans(u, (-5, 12), (1, 0)) == [(5, 15), (25, 35)]
    both = pc.cut(u, _outline(8), (-5, 12), (1, 0), None, 1)
    assert len(both) == 3
    left_leg = pc.cut(u, _outline(8), (-5, 12), (1, 0),
                      lambda lo, hi: hi < 20, 1)
    assert len(left_leg) == 2
    assert abs(min(pc.area(p) for p, _f in left_leg) - 80.0) < 1e-9


def test_a_line_that_misses_leaves_the_polygon_alone():
    pieces = pc.cut(SQUARE, _outline(4), (20, 0), (0, 1), None, 1)
    assert pieces == [(SQUARE, _outline(4))]


def test_random_cuts_keep_area_outline_and_sides():
    rng = random.Random(4)
    for _trial in range(1500):
        poly = _star(rng)
        if rng.random() < 0.5:                 # through two corners: the
            v, w = rng.sample(poly, 2)         # awkward cases, on purpose
            a, d = v, (w[0] - v[0], w[1] - v[1])
        else:
            a = (rng.uniform(-10, 10), rng.uniform(-10, 10))
            th = rng.uniform(0, math.pi)
            d = (math.cos(th), math.sin(th))
        ln = math.hypot(*d)
        e = (d[0] / ln, d[1] / ln)
        pieces = pc.cut(poly, _outline(len(poly)), a, e, None, 5)
        assert abs(pc.area(poly) - sum(pc.area(p) for p, _f in pieces)) < 1e-6
        assert abs(_flag_length(poly, _outline(len(poly)), pc.OUTLINE)
                   - sum(_flag_length(p, f, pc.OUTLINE) for p, f in pieces)) \
            < 1e-6
        for pts, flags in pieces:
            assert pc.area(pts) > 0 and len(pts) == len(flags)
            side = [(q[1] - a[1]) * e[0] - (q[0] - a[0]) * e[1] for q in pts]
            assert not (min(side) < -1e-5 and max(side) > 1e-5)
            parts = pc.convex_parts(pts, flags)
            assert abs(sum(pc.area(q) for q, _g in parts) - pc.area(pts)) \
                < 1e-6
            for q, g in parts:
                assert pc.is_convex(q, 1e-7) and len(q) == len(g)
            for flag in (pc.OUTLINE, 5):
                assert abs(_flag_length(pts, flags, flag) - sum(
                    _flag_length(q, g, flag) for q, g in parts)) < 1e-6


def test_convex_parts_of_a_t_shape():
    t = [(0, 0), (30, 0), (30, 40), (60, 40), (60, 70), (-30, 70), (-30, 40),
         (0, 40)]
    parts = pc.convex_parts(t, _outline(8))
    assert len(parts) == 2
    assert sum(f.count(pc.INNER) for _p, f in parts) == 2   # one shared line
    assert pc.convex_parts(SQUARE, _outline(4)) == [(SQUARE, _outline(4))]


def test_simplify_thins_a_rounded_corner_within_tolerance():
    ring = []
    for cx, cy, a0 in ((72, 8, -90), (72, 42, 0), (8, 42, 90), (8, 8, 180)):
        for i in range(17):
            a = math.radians(a0 + 90 * i / 16)
            ring.append((cx + 8 * math.cos(a), cy + 8 * math.sin(a)))
    thin = pc.simplify(ring, 0.1)
    assert 12 <= len(thin) < len(ring)
    assert abs(pc.area(thin) - pc.area(ring)) < 0.01 * pc.area(ring)
    assert pc.simplify(SQUARE, 0.1) == SQUARE


def test_tidy_drops_empty_edges_and_spikes():
    pts = [(0, 0), (10, 0), (10, 0), (10, 10), (15, 10), (10, 10), (0, 10)]
    flags = [1, 2, 3, 4, 5, 6, 7]
    p, f = pc.tidy(pts, flags)
    assert pc.area(p) == 100.0 and len(p) == len(f) == 4
    # each edge that is left still has its own flag
    edge = {(p[i], p[(i + 1) % 4]): f[i] for i in range(4)}
    assert edge[((0, 0), (10, 0))] == 1
    assert edge[((10, 0), (10, 10))] == 3
    assert edge[((10, 10), (0, 10))] == 6
    assert edge[((0, 10), (0, 0))] == 7


def test_convex_tests():
    other = [(10, 0), (20, 0), (20, 10), (10, 10)]
    assert not pc.convex_overlap(SQUARE, other, 0.1)       # only touching
    assert pc.convex_overlap(SQUARE, [(9, 0), (20, 0), (20, 10), (9, 10)],
                             0.1)
    assert pc.inside_convex((5, 5), SQUARE)
    assert pc.inside_convex((10, 5), SQUARE)               # on the edge
    assert not pc.inside_convex((10.1, 5), SQUARE)
    assert pc.clip_to_convex([(-5, -5), (5, -5), (5, 5), (-5, 5)], SQUARE) \
        == [(0.0, 0.0), (5.0, 0.0), (5, 5), (0.0, 5.0)]
    assert pc.clip_segment_to_convex((-5, 5), (15, 5), SQUARE) \
        == ((0.0, 5.0), (10.0, 5.0))
    assert pc.clip_segment_to_convex((-5, 15), (15, 15), SQUARE) is None


# -- a corner given twice -----------------------------------------------------

def _square_with_a_corner_twice(k, x0=10000.0, y0=10000.0):
    # a 50 mm square 10 m from the origin, its corner k given twice: the
    # second a hair (1e-12 mm each way) from the first, square to the way
    # the middle lies from it, so that the edge of no length between them
    # points along the square's other edges' lines, not into it
    sq = [(x0, y0), (x0 + 50, y0), (x0 + 50, y0 + 50), (x0, y0 + 50)]
    x, y = sq[k]
    dx, dy = x0 + 25 - x, y0 + 25 - y
    twin = (x - math.copysign(1e-12, dy), y + math.copysign(1e-12, dx))
    assert twin != (x, y)
    return sq[:k + 1] + [twin] + sq[k + 1:]


def test_an_edge_of_no_length_decides_nothing_inside_a_convex_polygon():
    # once the edge between the two decided: its direction is noise, and
    # the middle of the square, 25 mm inside, was outside it
    for k in range(4):
        pts = _square_with_a_corner_twice(k)
        assert pc.inside_convex((10025, 10025), pts)
        assert pc.inside_convex((10001, 10049), pts)
        assert pc.inside_convex((10050, 10025), pts)       # on an edge
        assert not pc.inside_convex((10060, 10025), pts)
        assert not pc.inside_convex((10025, 9990), pts)
        assert not pc.inside_convex((9990, 9990), pts)    # past a corner
        assert pc.inside_convex((10025, 10025), pts, 0.0)
        assert not pc.inside_convex((10060, 10025), pts, 0.0)


def test_a_sliver_thinner_than_the_tolerance_still_ends_at_its_ends():
    # its ends are edges of no length, but a point past them is outside
    sliver = [(0, 0), (100, 0), (100, 1e-10), (0, 1e-10)]
    assert pc.inside_convex((50, 0), sliver)
    assert not pc.inside_convex((150, 0), sliver)
    assert not pc.inside_convex((-50, 0), sliver)


def test_convex_parts_give_a_corner_given_twice_once():
    for k in range(4):
        pts = _square_with_a_corner_twice(k)
        (part, flags), = pc.convex_parts(pts, [1, 2, 3, 4, 5])
        assert len(part) == len(flags) == 4
        assert abs(pc.area(part) - 2500.0) < 1e-6
        assert pc.inside_convex((10025, 10025), part)


def test_a_corner_given_twice_thirty_metres_off_is_judged_by_its_size():
    # 30 m from the origin the rounding of a corner is about 4e-12 mm, and
    # two copies of one 1e-10 mm apart are still one corner: what counts
    # as no length grows with the distance, not a fixed 1e-11 mm. Its edge
    # points so that the middle lies past its line
    sq = [(30000, 30000), (30050, 30000), (30050, 30050), (30000, 30050)]
    for k in range(4):
        x, y = sq[k]
        dx, dy = 30025 - x, 30025 - y
        twin = (x - math.copysign(1e-10, dy), y + math.copysign(1e-10, dx))
        pts = sq[:k + 1] + [twin] + sq[k + 1:]
        assert 1e-10 < math.dist(pts[k], pts[k + 1]) < 2e-10
        assert pc.inside_convex((30025, 30025), pts, 0.0)
        assert not pc.inside_convex((30060, 30025), pts, 0.0)
        (part, flags), = pc.convex_parts(pts, [1, 2, 3, 4, 5])
        assert len(part) == len(flags) == 4


def test_past_an_edge_of_no_length_an_edge_still_counts_to_the_tolerance():
    # a point a hair past the right edge, on the wrong side of the edge of
    # no length at its foot, is on that edge to within the tolerance
    pts = _square_with_a_corner_twice(1)
    assert pc.inside_convex((10050 + 5e-7, 10025), pts, 1e-6)
    assert pc.inside_convex((10050 + 5e-7, 10000 - 5e-7), pts, 1e-6)
    assert not pc.inside_convex((10050 + 2e-6, 10025), pts, 1e-6)

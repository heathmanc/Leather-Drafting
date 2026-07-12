"""Tests for the trim engine (remove the cell under a pick, keep curve types)."""

import math

from leathercad.geometry import Vec2
from leathercad.trim import trim, intersection_arclengths


def V(x, y):
    return Vec2(float(x), float(y))


def _endpoints(chain):
    return [(round(pts[0].x, 3), round(pts[0].y, 3),
             round(pts[-1].x, 3), round(pts[-1].y, 3)) for _k, pts in chain]


def test_trim_open_line_keeps_far_side():
    seg = [("line", [V(0, 0), V(100, 0)])]
    cut = [[V(40, -10), V(40, 10)]]
    # click left of the crossing -> the left stub is removed
    res = trim(seg, cut, V(20, 0), closed=False)
    assert len(res) == 1
    (ax, ay, bx, by), = _endpoints(res[0])
    assert (ax, bx) == (40.0, 100.0)
    # click right -> the right stub is removed
    res2 = trim(seg, cut, V(70, 0), closed=False)
    (ax, ay, bx, by), = _endpoints(res2[0])
    assert (ax, bx) == (0.0, 40.0)


def test_trim_open_line_middle_splits_in_two():
    seg = [("line", [V(0, 0), V(100, 0)])]
    cut = [[V(30, -5), V(30, 5)], [V(70, -5), V(70, 5)]]
    res = trim(seg, cut, V(50, 0), closed=False)
    assert len(res) == 2
    spans = sorted((e[0], e[2]) for c in res for e in _endpoints(c))
    assert spans == [(0.0, 30.0), (70.0, 100.0)]


def test_trim_closed_square_becomes_open_loop():
    sq = [("line", [V(0, 0), V(100, 0)]), ("line", [V(100, 0), V(100, 100)]),
          ("line", [V(100, 100), V(0, 100)]), ("line", [V(0, 100), V(0, 0)])]
    cut = [[V(30, -5), V(30, 5)], [V(70, -5), V(70, 5)]]
    res = trim(sq, cut, V(50, 0), closed=True)
    assert len(res) == 1
    chain = res[0]
    # survivor runs the long way round from (70,0) to (30,0)
    assert (round(chain[0][1][0].x, 1), round(chain[0][1][0].y, 1)) == (70.0, 0.0)
    assert (round(chain[-1][1][-1].x, 1), round(chain[-1][1][-1].y, 1)) == (30.0, 0.0)


def test_trim_closed_no_crossing_deletes():
    sq = [("line", [V(0, 0), V(10, 0)]), ("line", [V(10, 0), V(10, 10)]),
          ("line", [V(10, 10), V(0, 10)]), ("line", [V(0, 10), V(0, 0)])]
    assert trim(sq, [], V(5, 0), closed=True) is None


def test_trim_preserves_arc_and_refits_it():
    # a semicircle centred at the origin, radius 10; cut it at the top
    seg = [("arc", [V(10, 0), V(0, 10), V(-10, 0)])]
    cut = [[V(0, -2), V(0, 20)]]
    res = trim(seg, cut, V(7, 7), closed=False)   # remove the right half
    assert len(res) == 1 and res[0][0][0] == "arc"
    for _k, pts in res[0]:
        for p in pts:
            assert abs(p.length() - 10.0) < 1e-6   # still on the circle
    # survivor is the left half: (0,10) -> ... -> (-10,0)
    a = res[0][0][1][0]
    b = res[0][-1][1][-1]
    assert abs(a.x) < 1e-6 and abs(a.y - 10) < 1e-6
    assert abs(b.x + 10) < 1e-6 and abs(b.y) < 1e-6


def test_trim_keeps_arc_when_cutting_an_adjacent_line():
    seg = [("line", [V(0, 0), V(40, 0)]),
           ("arc", [V(40, 0), V(50, 10), V(40, 20)]),
           ("line", [V(40, 20), V(0, 20)])]
    cut = [[V(20, -5), V(20, 5)]]
    res = trim(seg, cut, V(10, 0), closed=False)
    assert len(res) == 1
    kinds = [k for k, _ in res[0]]
    assert "arc" in kinds            # the corner arc survives intact


def test_intersection_arclengths_reports_crossings():
    seg = [("line", [V(0, 0), V(100, 0)])]
    cut = [[V(25, -5), V(25, 5)], [V(80, -5), V(80, 5)]]
    s, total = intersection_arclengths(seg, cut)
    assert abs(total - 100) < 1e-6
    assert [round(x, 1) for x in s] == [25.0, 80.0]

"""Trim cuts on the true outline, ends shared with the cut line, a preview on
the drawn line -- and the fillet that closes an outline at its last corner.

The report: two circles joined by two waist arcs (a bookmark). Trimming the
waist arcs inside the circles left a 0.076 mm nub at each quadrant; trimming
the circles' inner arcs stopped 0.06 mm past the waist arcs' ends; the red
preview didn't lie on the drawn circle; and filleting the four corners at
35 mm refused the fourth as "a loose end" whatever the order.
"""

import json
import math
import os

import pytest

from leathercad.geometry import Vec2
from leathercad.shapes import Circle, Transform
from leathercad.trim import (Cutter, first_hit, intersection_arclengths,
                             removed_cell_polyline, trim)


def V(x, y):
    return Vec2(float(x), float(y))


C4 = V(218.5, 29.5)
R = 17.5


def _circle(c, r):
    """A circle as the app hands it to Trim: four quarter arcs."""
    pts = [c + V(r * math.cos(k * math.pi / 2), r * math.sin(k * math.pi / 2))
           for k in range(5)]
    mids = [c + V(r * math.cos((k + 0.5) * math.pi / 2),
                  r * math.sin((k + 0.5) * math.pi / 2)) for k in range(4)]
    return [("arc", [pts[k], mids[k], pts[k + 1]]) for k in range(4)]


# the waist arcs as left by a 0.4.0 trim: their ends 0.017 mm inside circle 4
TOP_END, BOT_END = V(226.2394, 13.8236), V(226.2394, 45.1764)
TOP = [("arc", [V(275.7606, 13.8236), V(251.0, 16.3025), TOP_END])]
BOT = [("arc", [BOT_END, V(251.0, 42.6975), V(275.7606, 45.1764)])]


def _poly_dist(p, poly):
    best = float("inf")
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        ab = b - a
        d2 = ab.length_sq()
        t = 0.0 if d2 < 1e-18 else max(0.0, min(1.0, (p - a).dot(ab) / d2))
        best = min(best, (a + ab * t - p).length())
    return best


def _bez(c, t):
    a, c1, c2, b = c
    u = 1 - t
    return a * u ** 3 + c1 * (3 * u * u * t) + c2 * (3 * u * t * t) + b * t ** 3


# -- the engine ---------------------------------------------------------------
def test_curve_trimmed_to_a_circle_ends_on_it_and_leaves_no_nub():
    waist = [("arc", [V(218.5, 12.0), V(251.0, 16.3025), V(283.5, 12.0)])]
    res = trim(waist, [Cutter(_circle(C4, R), closed=True)], V(222.0, 12.8),
               closed=False)
    assert len(res) == 1                 # no 0.076 mm piece left at the quadrant
    start = res[0][0][1][0]
    assert abs((start - C4).length() - R) < 1e-9
    assert start.x > 226.0               # the far crossing, not the quadrant


def test_cut_curve_stopping_a_hair_inside_cuts_at_its_end_and_shares_it():
    res = trim(_circle(C4, R), [Cutter(TOP, closed=False),
                                Cutter(BOT, closed=False)],
               V(236.0, 29.5), closed=True)
    assert len(res) == 1
    chain = res[0]
    ends = {chain[0][1][0], chain[-1][1][-1]}
    assert ends == {TOP_END, BOT_END}    # one shared point each, exactly
    for kind, pts in chain:              # still the circle (to a hair)
        assert kind == "arc"
        for p in pts:
            assert abs((p - C4).length() - R) < 0.02
    assert max(p.x for _k, pts in chain for p in pts) <= TOP_END.x + 1e-9


def test_crossing_just_past_a_cut_lines_end_is_that_end():
    # the waist arc crosses the true circle 0.06 mm past its own end: one cut
    hits, total = intersection_arclengths(_circle(C4, R),
                                          [Cutter(TOP, closed=False)])
    assert len(hits) == 1


def test_cut_line_ending_short_or_past_the_outline_shares_its_end():
    line = [("line", [V(0, 0), V(100, 0)])]
    for tip in (V(40, 0.03), V(40, -0.03)):          # stops short / runs past
        cut = Cutter([("line", [V(40, 20), tip])], closed=False)
        res = trim(line, [cut], V(20, 0), closed=False)
        assert len(res) == 1
        assert res[0][0][1][0] == tip
        assert res[0][-1][1][-1] == V(100, 0)


def test_hover_preview_lies_on_the_drawn_outline():
    circle = Circle(rx=R, ry=R, transform=Transform(x=C4.x, y=C4.y))
    drawn = circle.world_polyline()[0]   # what the screen draws (flattened)
    poly = removed_cell_polyline(_circle(C4, R), [Cutter(TOP, closed=False),
                                                  Cutter(BOT, closed=False)],
                                 V(236.0, 29.5), True, display=drawn)
    assert poly and len(poly) > 4
    assert max(_poly_dist(p, drawn) for p in poly) < 1e-9
    firsts = sorted([poly[0], poly[-1]], key=lambda p: p.y)
    assert (firsts[0] - TOP_END).length() < 0.03
    assert (firsts[1] - BOT_END).length() < 0.03
    assert abs(max(p.x for p in poly) - (C4.x + R)) < 1e-9   # the inner side


def test_hover_preview_without_display_still_works():
    poly = removed_cell_polyline(_circle(C4, R), [Cutter(TOP, closed=False),
                                                  Cutter(BOT, closed=False)],
                                 V(236.0, 29.5), True)
    assert poly and abs(max(p.x for p in poly) - (C4.x + R)) < 0.06


def test_sliver_at_an_open_end_goes_with_the_span_beside_it():
    line = [("line", [V(0, 0), V(100, 0)])]
    cuts = [[V(0.1, -5), V(0.1, 5)], [V(50, -5), V(50, 5)]]
    res = trim(line, cuts, V(25, 0), closed=False)
    assert len(res) == 1
    assert (res[0][0][1][0], res[0][-1][1][-1]) == (V(50, 0), V(100, 0))
    # ...unless the sliver is what was clicked
    res = trim(line, cuts, V(0.05, 0), closed=False)
    assert len(res) == 1
    assert abs(res[0][0][1][0].x - 0.1) < 1e-9


def test_bezier_cut_lands_on_the_true_curve():
    # a line cut by a curve: the kept end is on the curve, not a chord of it
    ctrl = (V(20, 0), V(40, 30), V(60, -10), V(80, 20))
    line = [("line", [V(0, 10), V(100, 10)])]
    res = trim(line, [Cutter([("bezier", list(ctrl))], closed=False)],
               V(5, 10), closed=False)
    x = res[0][0][1][0]
    lo, hi = 0.0, 0.3                    # the first crossing of y = 10
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if _bez(ctrl, mid).y < 10:
            lo = mid
        else:
            hi = mid
    assert (x - _bez(ctrl, lo)).length() < 1e-6
    # a curve cut by a line: split exactly where the line crosses it
    curve = [("bezier", [V(0, 0), V(30, 40), V(70, 40), V(100, 0)])]
    res = trim(curve, [[V(50, -10), V(50, 60)]], _bez(
        (V(0, 0), V(30, 40), V(70, 40), V(100, 0)), 0.2), closed=False)
    kind, pts = res[0][0]
    assert kind == "bezier"
    assert (pts[0] - V(50, 30)).length() < 1e-6 and pts[-1] == V(100, 0)


def test_closed_polyline_cutter_cuts_along_its_closing_edge():
    line = [("line", [V(-10, 5), V(30, 5)])]
    box = [V(0, 0), V(20, 0), V(20, 10), V(0, 10)]
    hits, _ = intersection_arclengths(line, [Cutter.from_polyline(box, closed=True)])
    assert [round(h, 6) for h in hits] == [10.0, 30.0]
    hits, _ = intersection_arclengths(line, [Cutter.from_polyline(box)])
    assert [round(h, 6) for h in hits] == [30.0]           # open: no x=0 edge


def test_first_hit_is_exact_on_a_circle():
    got = first_hit(V(0, 0), V(1, 0.3), [Cutter(_circle(V(50, 10), 7.0),
                                                 closed=True)])
    assert got is not None
    d, p = got
    assert abs((p - V(50, 10)).length() - 7.0) < 1e-9
    assert abs(d - p.length()) < 1e-9


# -- the canvas ---------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def _bookmark_window():
    from leathercad.document import Document
    from leathercad.shapes import Edge, EditablePath
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    for cx in (218.5, 283.5):
        doc.add_shape(Circle(rx=R, ry=R, layer="Cut",
                             transform=Transform(x=cx, y=29.5)))
    for y0, ym in ((12.0, 16.3025), (47.0, 42.6975)):
        doc.add_shape(EditablePath(nodes=[V(218.5, y0), V(283.5, y0)],
                                   edges=[Edge("arc", V(251.0, ym))],
                                   closed=False, layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()
    win.canvas.resize(800, 600)
    return win


def _open_ends(doc):
    out = []
    for sh in doc.shapes:
        pts, _c, closed = sh.world_polyline()
        if not closed:
            out += [pts[0], pts[-1]]
    return out


def test_bookmark_trims_to_shared_ends_and_fillets_all_four_corners(qapp):
    from leathercad_app import canvas as cm
    win = _bookmark_window()
    c = win.canvas
    msgs = []
    c.statusMessage.connect(msgs.append)
    c.tool = cm.TRIM
    c._zoom = 20.0
    for click in (V(222.0, 12.8), V(280.0, 12.8), V(222.0, 46.2), V(280.0, 46.2)):
        c._do_trim(click)                    # the waist arcs inside the circles
    assert len(win.doc.shapes) == 4          # no nubs at the quadrants
    c._zoom = 6.0
    for click in (V(236.0, 29.5), V(266.0, 29.5)):
        c._update_trim_hover(click)          # red preview on the drawn circle
        path = c._trim_hover.path()
        poly = [V(path.elementAt(i).x, path.elementAt(i).y)
                for i in range(path.elementCount())]
        drawn = c._entity_for_trim(click).world_outline()
        assert max(_poly_dist(p, drawn) for p in poly) < 1e-9
        c._do_trim(click)                    # the circles' inner arcs
    ends = _open_ends(win.doc)
    assert len(ends) == 8
    for p in ends:                           # every end meets one other, exactly
        assert sorted((p - q).length() for q in ends)[1] < 1e-9
    state = json.loads(json.dumps(win.doc.to_dict()))
    corners = {"a": V(226.3, 13.84), "b": V(275.7, 13.84),
               "c": V(275.7, 45.16), "d": V(226.3, 45.16)}
    c.tool = cm.FILLET
    c.fillet_radius = 35.0
    for order in ("abcd", "dabc", "acbd"):
        win._load_state(json.loads(json.dumps(state)))
        c._zoom = 6.0
        for k in order:
            c._do_fillet(corners[k])
            assert msgs[-1].startswith("Filleted at 35"), (order, k, msgs[-1])
        paths = [s for s in win.doc.shapes if s.kind == "editpath"]
        assert len(paths) == 1 and paths[0].closed
        assert sum(e.kind == "arc" for e in paths[0].edges) >= 8


def test_fillet_closes_an_outline_whose_last_ends_sit_a_hair_apart(qapp):
    # four separate lines round a square; the last corner meets 0.06 mm off
    from leathercad.document import Document
    from leathercad.shapes import EditablePath, PathShape
    from leathercad_app import canvas as cm
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    for a, b in ((V(0, 0), V(100, 0)), (V(100, 0), V(100, 100)),
                 (V(100, 100), V(0, 100)), (V(0, 100), V(0.06, 0.0))):
        doc.add_shape(PathShape(points=[a, b], close_path=False, layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(600, 450)
    msgs = []
    c.statusMessage.connect(msgs.append)
    c.tool = cm.FILLET
    c.fillet_radius = 10.0
    for p in (V(100, 0), V(100, 100), V(0, 100), V(0, 0)):
        c._do_fillet(p)
        assert msgs[-1].startswith("Filleted at 10"), msgs[-1]
    assert len(win.doc.shapes) == 1
    sh = win.doc.shapes[0]
    assert isinstance(sh, EditablePath) and sh.closed
    assert sum(e.kind == "arc" for e in sh.edges) == 4


def test_lone_open_path_with_far_apart_ends_is_still_a_loose_end(qapp):
    from leathercad.document import Document
    from leathercad.shapes import PathShape
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(PathShape(points=[V(0, 0), V(50, 0), V(50, 50)],
                            close_path=False, layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(600, 450)
    msgs = []
    c.statusMessage.connect(msgs.append)
    c.fillet_radius = 5.0
    c._do_fillet(V(0, 0))
    assert "loose end" in msgs[-1]
    assert not getattr(win.doc.shapes[0], "closed", False)


def test_merge_closes_pieces_whose_far_ends_nearly_meet():
    from leathercad.shapes import Edge
    from leathercad_app.canvas import _merge_open
    a = ([V(0.03, 0), V(50, 0), V(50, 50)], [Edge("line"), Edge("line")])
    b = ([V(50, 50), V(0, 50), V(0, 0)], [Edge("line"), Edge("line")])
    nodes, edges, closed, corner = _merge_open(a, 2, b, 0)
    assert closed and len(nodes) == len(edges) == 4
    assert nodes[0] == V(0.015, 0)            # met halfway
    assert corner == 2


def test_extend_stops_exactly_on_a_circle(qapp):
    from leathercad.document import Document
    from leathercad.shapes import PathShape
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(Circle(rx=7.0, ry=7.0, layer="Cut",
                         transform=Transform(x=50, y=10)))
    line = PathShape(points=[V(0, 0), V(10, 3)], close_path=False, layer="Cut")
    doc.add_shape(line)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(600, 450)
    c._do_extend(V(10, 3))
    end = line.transform.apply(line.points[-1])
    assert abs((end - V(50, 10)).length() - 7.0) < 1e-9


def test_trimming_a_small_span_off_a_loop_leaves_it_open(qapp):
    # two cut lines 0.4 mm apart across a square's top edge: trimming between
    # them used to close the loop straight back up (Join's 0.6 mm)
    from leathercad.document import Document
    from leathercad.shapes import PathShape, Rectangle
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    sq = Rectangle(width=100, height=100, layer="Cut")
    doc.add_shape(sq)
    for x in (10.0, 10.4):
        doc.add_shape(PathShape(points=[V(x, 40), V(x, 60)], layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(600, 450)
    c._zoom = 400.0
    c._do_trim(V(10.2, 50))
    outline = [s for s in win.doc.shapes
               if s.world_polyline()[0] and max(p.y for p in s.world_polyline()[0]) > 49
               and min(p.y for p in s.world_polyline()[0]) < -49]
    assert len(outline) == 1
    pts, _c, closed = outline[0].world_polyline()
    assert not closed
    assert {(round(pts[0].x, 6), round(pts[0].y, 6)),
            (round(pts[-1].x, 6), round(pts[-1].y, 6))} == {(10.0, 50.0), (10.4, 50.0)}

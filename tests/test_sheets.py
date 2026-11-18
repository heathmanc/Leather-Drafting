"""Pieces laid on a piece that folds, lying flat: the height each comes to
rest at, what it rests on and stands clear of, the stitch holes through
the layers, and the stack as a model for the view."""

import math

from leathercad import bsp
from leathercad import clash
from leathercad import foldsolid as fs
from leathercad import polycut as pc
from leathercad import sheets as sh
from leathercad import templates
from leathercad.generators import bifold_wallet
from leathercad.stitching import holes_for_shape

MAIN = [(0, 0), (200, 0), (200, 100), (0, 100)]


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _ring(shape):
    return [(p.x, p.y) for p in shape.world_polyline()[0]]


def _laid(*pieces, t=2.0, main=MAIN):
    """Sheets ``(ring, thickness)`` or ``(ring, thickness, side)`` laid on
    ``main`` in that order: ``(the sheets, the notes)``."""
    sheets = [sh.Sheet(p[0], p[1], *p[2:], name=f"s{k}")
              for k, p in enumerate(pieces, 1)]
    return sheets, sh.settle(main, t, sheets)


def _bifold(shell_t=1.6, inner_t=0.8):
    """The generated bifold put together: the shell grain down on the
    bench, the liner on its flesh side flush with its bottom edge and
    centred, and a pocket stack flush with the bottom and with each outer
    side, backing first. Sheets 1 to 9 are the liner, the left backing and
    pockets 3, 2, 1, then the right backing and pockets 3, 2, 1."""
    parts = bifold_wallet()
    shell, spine, liner = parts[0], parts[1], parts[2]
    outline = _ring(shell)
    x0, y0, x1, _y1 = pc.bounds(outline)
    sheets = []

    def lay(shape, where):
        r = _ring(shape)
        a0, b0, a1, _b1 = pc.bounds(r)
        # on the bench the shell is turned over like a page: its left
        # there is its right as drawn
        dx = {"left": x1 - a1, "right": x0 - a0,
              "middle": 0.5 * (x0 + x1) - 0.5 * (a0 + a1)}[where]
        sheets.append(sh.Sheet([(x + dx, y + y0 - b0) for x, y in r],
                               inner_t, side=-1, grain=-1, name=shape.name,
                               key=shape.shape_id))

    lay(liner, "middle")
    for stack, where in ((parts[3:7], "left"), (parts[7:11], "right")):
        for p in reversed(stack):
            lay(p, where)
    ends = spine.world_polyline()[0]
    fold = fs.FoldLine((ends[0].x, ends[0].y), (ends[-1].x, ends[-1].y),
                       180.0, 1, label="A", name=spine.name)
    holes = [(h.point.x, h.point.y, 1.0)
             for h in holes_for_shape(shell, None).holes]
    layout = fs.fold_piece(outline, [fold], thickness=shell_t, holes=holes)
    return sh.FlatStack(layout, sheets, outline, name=shell.name)


def _as_drawn(doc, t=1.2, cutouts=()):
    """A template's pieces as a stack, where they are drawn: the first is
    the main piece and the rest lie on its front in the order drawn. Each
    has its own stitching and the holes of the seams that lie on it."""
    closed = [s for s in doc.shapes if s.world_polyline()[2]]
    seams = [(h.point.x, h.point.y, 1.0) for sl in doc.stitch_lines
             for h in sl.result().holes]

    def holes(shape):
        own = [(h.point.x, h.point.y, 1.0)
               for h in holes_for_shape(shape, doc).holes]
        return own + [h for h in seams if pc.inside(h[:2], _ring(shape))]

    main = closed[0]
    layout = fs.fold_piece(_ring(main), [], thickness=t, holes=holes(main))
    sheets = [sh.Sheet(_ring(s), t, name=s.name, key=s.shape_id,
                       holes=holes(s), cutouts=cutouts)
              for s in closed[1:]]
    return sh.FlatStack(layout, sheets, _ring(main), name=main.name)


def _near(a, b, tol=1e-9):
    return abs(a - b) <= tol


# -- the levels ---------------------------------------------------------------

def test_a_sheet_comes_to_rest_on_the_highest_leather_under_it():
    (a, b, c), notes = _laid((_rect(10, 10, 90, 90), 1.0),
                             (_rect(20, 20, 60, 60), 0.5),
                             (_rect(120, 10, 190, 90), 0.7))
    assert (a.lo, a.hi, a.rests, a.rank) == (0.0, 1.0, [0], 1)
    assert (b.lo, b.hi, b.rests, b.rank) == (1.0, 1.5, [1], 2)
    assert (c.lo, c.hi, c.rests, c.rank) == (0.0, 0.7, [0], 1)
    # how far each lies over what it rests on: what it would have to
    # slide to come off it
    assert _near(a.lap, 90.0) and _near(b.lap, 50.0)
    assert not (a.clear or b.clear or c.clear or a.loose or a.past)
    # the thickest place is the main piece and the two on each other
    thick, = notes
    assert thick.kind == "thick" and _near(thick.a, 2.0 + 1.5)
    assert thick.other == (0, 1, 2) and _near(thick.b, 3.5)
    assert 20 < thick.at[0] < 60 and 20 < thick.at[1] < 60


def test_the_generated_bifold_is_5_6_mm_at_its_thickest():
    stack = _bifold()
    assert [s.name for s in stack.sheets] == [
        "Bill pocket liner", "Left pocket backing", "Left pocket 3",
        "Left pocket 2", "Left pocket 1 (front)", "Right pocket backing",
        "Right pocket 3", "Right pocket 2", "Right pocket 1 (front)"]
    levels = [0.0, 0.8, 1.6, 2.4, 3.2, 0.8, 1.6, 2.4, 3.2]
    assert all(_near(s.lo, lo) and _near(s.hi, lo + 0.8)
               for s, lo in zip(stack.sheets, levels))
    assert [s.rests for s in stack.sheets] == [[0], [1], [2], [3], [4], [1],
                                               [6], [7], [8]]
    assert [s.rank for s in stack.sheets] == [1, 2, 3, 4, 5, 2, 3, 4, 5]
    # each lies right across what is under it: by its own height
    assert [round(s.lap, 6) for s in stack.sheets] == [
        88.0, 79.0, 54.0, 44.0, 34.0, 79.0, 54.0, 44.0, 34.0]
    assert not any(s.clear or s.loose for s in stack.sheets)
    thick = stack.notes[0]
    assert thick.kind == "thick" and _near(thick.a, 5.6)
    assert _near(thick.b, 5.6)                 # leather all the way through
    # the shell, the liner, the left backing and its three pockets
    assert thick.other == (0, 1, 2, 3, 4, 5)
    assert all(pc.inside(thick.at, stack.sheets[k - 1].ring)
               for k in thick.other[1:])


def test_any_real_overlap_climbs_and_butting_edge_to_edge_does_not():
    assert sh.LAP <= clash.JUDGE               # what is left level passes
    for over, climbs in ((0.2, True), (0.04, True), (1e-5, True),
                         (1e-7, False), (0.0, False), (-3.0, False)):
        (a, b), notes = _laid((_rect(10, 10, 100, 90), 1.0),
                              (_rect(100 - over, 10, 190, 90), 1.0))
        assert (b.lo == 1.0) == climbs, over
        assert b.rests == ([1] if climbs else [0])
        slivers = [n for n in notes if n.kind == "sliver"]
        if climbs:
            # ... and is told: it rides up on that little overlap
            assert _near(b.lap, over)
            assert slivers == [sh.Note("sliver", 2, -1, b.lap, other=(1,))]
        else:
            assert b.lo == 0.0 and not slivers
    # a proper overlap is not a sliver
    (a, b), notes = _laid((_rect(10, 10, 100, 90), 1.0),
                          (_rect(98.5, 10, 190, 90), 1.0))
    assert b.lo == 1.0 and _near(b.lap, 1.5)
    assert [n.kind for n in notes] == ["thick", "clear"]


def test_a_piece_across_two_others_rests_on_the_higher_and_bridges_the_rest():
    (a, b, c), notes = _laid((_rect(20, 20, 100, 80), 2.0),
                             (_rect(100, 20, 180, 80), 0.5),
                             (_rect(60, 40, 140, 60), 1.0))
    assert (c.lo, c.hi, c.rests) == (2.0, 3.0, [1])
    assert c.clear == [(2, 1.5)]               # 1.5 mm clear of the thinner
    assert sh.Note("clear", 3, -1, 1.5, other=(2,)) in notes
    assert not a.clear and not b.clear
    # with a gap between the two it stands clear of the main piece as
    # well, by more: the widest gap comes first
    (a, b, c), notes = _laid((_rect(20, 20, 90, 80), 2.0),
                             (_rect(110, 20, 180, 80), 0.5),
                             (_rect(60, 40, 140, 60), 1.0))
    assert c.clear == [(0, 2.0), (2, 1.5)]
    assert sh.Note("clear", 3, -1, 2.0, other=(0, 2)) in notes
    # a pocket lying flat on a liner that is shorter than it stands clear
    # of the main piece beyond the liner's edge
    (liner, pocket), notes = _laid((_rect(0, 0, 200, 60), 0.8),
                                   (_rect(10, 0, 90, 80), 0.8))
    assert pocket.rests == [1] and pocket.clear == [(0, 0.8)]


def test_what_is_laid_last_goes_on_top_whatever_its_size():
    # a strap across a pile of three that stands lower than the piece it
    # rests on: one more than the highest rank of anything under it, so
    # that pulled apart it stays above them all
    pile = [(_rect(110, 20, 180, 80), 0.5)] * 3
    sheets, _notes = _laid((_rect(20, 20, 90, 80), 2.0), *pile,
                           (_rect(60, 40, 140, 60), 1.0))
    strap = sheets[-1]
    assert [s.rank for s in sheets] == [1, 1, 2, 3, 4]
    assert strap.rests == [1] and strap.lo == 2.0
    assert strap.clear == [(0, 2.0), (4, 0.5)]


def test_the_generators_pockets_stand_2_49_mm_past_the_shells_round_corner():
    stack = _bifold()
    out = 6.0 * (math.sqrt(2.0) - 1.0)        # a square corner on a 6 mm one
    assert round(out, 2) == 2.49
    liner, pockets = stack.sheets[0], stack.sheets[1:]
    assert liner.past == 0.0 and liner.past_at is None
    for k, s in enumerate(pockets, 2):
        assert _near(s.past, out, 1e-9)
        side = 1 if k <= 5 else -1             # as drawn: left is right
        assert _near(s.past_at[0], side * 99.6, 1e-9)
        assert s.past_at[1] == -45.0
    past = [n for n in stack.notes if n.kind == "past"]
    assert [n.sheet for n in past] == [2, 3, 4, 5, 6, 7, 8, 9]
    assert all(_near(n.a, out) and n.at == s.past_at
               for n, s in zip(past, pockets))
    # a strap right across, 60 mm out at each end
    (strap,), notes = _laid((_rect(-60, 40, 260, 60), 1.0))
    assert _near(strap.past, 60.0) and strap.rests == [0]
    assert notes[1] == sh.Note("past", 1, -1, 60.0, at=strap.past_at)


def test_a_piece_over_nothing_lies_at_the_level_of_the_face_and_is_told():
    (a, b), notes = _laid((_rect(20, 20, 80, 80), 1.0),
                          (_rect(230, 20, 280, 80), 1.0))
    assert (b.lo, b.hi, b.rests, b.loose, b.rank) == (0.0, 1.0, [], True, 1)
    assert _near(b.past, 80.0)
    assert notes[-1] == sh.Note("loose", 2)
    assert not a.loose
    # resting on a strap that overhangs is not lying on nothing
    (strap, patch), notes = _laid((_rect(150, 40, 300, 60), 1.0),
                                  (_rect(240, 30, 290, 70), 0.5))
    assert patch.rests == [1] and patch.lo == 1.0 and not patch.loose
    assert "loose" not in [n.kind for n in notes]


def test_an_outline_with_no_area_is_no_leather_and_lifts_nothing():
    for ring in ([], [(10, 10), (150, 10)], [(10, 10), (80, 10), (150, 10)],
                 [(10, 10), (150, 10), (150, 10 + 1e-7), (10, 10 + 1e-7)]):
        (odd, real), notes = _laid((ring, 0.8), (_rect(5, 5, 150, 90), 1.0))
        assert odd.parts == [] and odd.loose and odd.past == 0.0
        # what is laid over it lies on the main piece, as if it were not
        # there, and it is not part of the thickest place
        assert (real.lo, real.rests, real.rank) == (0.0, [0], 1)
        assert not real.clear
        assert [(n.kind, n.sheet) for n in notes] == [("thick", 0),
                                                      ("loose", 1)]
        assert _near(notes[0].a, 3.0) and notes[0].other == (0, 2)
    # in the model it is nothing: no cell, and no room made for it
    lay = fs.fold_piece(MAIN, [], thickness=2.0, holes=[(20, 20, 1.0)])
    odd = sh.Sheet([(900, 900), (950, 900)], 0.8, holes=[(30, 30, 1.0)])
    real = sh.Sheet(_rect(5, 5, 150, 90), 1.0, holes=[(20, 20.2, 1.0)])
    stack = sh.FlatStack(lay, [odd, real], [])
    assert stack.outline == sh.outline_of(lay)       # no outline: its own
    cells = stack.solid([], None, 4.0)
    assert len(cells) == len(lay.cells) + 1
    assert cells[-1].tag == ("panel", 0, 2)
    assert stack.bounds3([], 4.0) == ((0.0, 0.0, -1.0), (200.0, 100.0, 6.0))
    # its holes are numbered with the rest, and are in no leather
    assert stack.hole_sheet == [0, 1, 2]
    check = stack.lineup()
    assert check.holes(fs.MEETS) == {0, 2} and 1 not in check.state
    assert [(f.a, f.b, f.holes) for f in check.findings] == [(0, 2, 2)]


def test_each_face_of_the_main_piece_has_a_pile_of_its_own():
    (f1, b1, f2, b2), notes = _laid((_rect(20, 20, 120, 80), 1.0, 1),
                                    (_rect(20, 20, 120, 80), 0.6, -1),
                                    (_rect(60, 30, 180, 70), 0.5, 1),
                                    (_rect(100, 30, 180, 70), 0.4, -1))
    assert (f1.lo, f2.lo, f2.rests) == (0.0, 1.0, [1])
    assert (b1.lo, b2.lo, b2.rests) == (0.0, 0.6, [2])
    # thickest where the two piles are over each other: 100 to 120 across
    thick = notes[0]
    assert _near(thick.a, 2.0 + 1.5 + 1.0) and _near(thick.b, 4.5)
    assert thick.other == (0, 1, 3, 2, 4)      # front pile, then back pile
    assert 100 < thick.at[0] < 120 and 30 < thick.at[1] < 70


def test_a_sheet_is_made_ready_as_the_main_pieces_outline_is():
    s = sh.Sheet([(0, 0), (0, 50), (80, 50), (80, 0), (0, 0)], 0.01,
                 side=-3, grain=0, holes=[[5, 5, 1.2]],
                 cutouts=[[(10, 10), (10, 20), (20, 20), (20, 10)]],
                 marks=[("cut", [[1, 1], [9, 9]])])
    # counter-clockwise, and the doubled point at its end dropped
    assert s.ring == [(80.0, 0.0), (80.0, 50.0), (0.0, 50.0), (0.0, 0.0)]
    assert (s.thickness, s.side, s.grain) == (0.05, -1, 1)
    assert s.holes == [(5, 5, 1.2)] and pc.area(s.cutouts[0]) == 100.0
    assert s.marks == [("cut", [(1, 1), (9, 9)])]
    assert (s.name, s.key, s.leather) == ("", "", "")
    assert s.parts == [s.ring]
    # an outline with a notch in it is kept in convex parts
    ell = sh.Sheet([(0, 0), (60, 0), (60, 20), (20, 20), (20, 50), (0, 50)],
                   1.0)
    assert len(ell.parts) == 2
    assert _near(sum(pc.area(p) for p in ell.parts), 1800.0)
    assert sh.sheet_of(("panel", 1)) == 0 and sh.sheet_of(("bend", 0)) == 0
    assert sh.sheet_of(("panel", 1, 4)) == 4 and sh.sheet_of(None) == 0


# -- what there is to tell ----------------------------------------------------

def test_notes_are_numbers_and_come_in_the_order_they_are_told():
    stack = _bifold()
    assert [n.kind for n in stack.notes] == ["thick"] + ["past"] * 8 + ["flat"]
    assert stack.notes[-1] == sh.Note("flat", 0, -1, 1.0)   # one fold unmade
    sheets, notes = _laid((_rect(10, 10, 100, 90), 1.0),
                          (_rect(99.8, 10, 190, 90), 1.0),      # a sliver
                          (_rect(-30, 40, 30, 60), 0.5),        # past the edge
                          (_rect(60, 30, 140, 70), 0.5),        # a bridge
                          (_rect(300, 0, 340, 40), 0.5))        # on nothing
    assert [(n.kind, n.sheet) for n in notes] == [
        ("thick", 0), ("past", 3), ("past", 5), ("clear", 2), ("clear", 3),
        ("clear", 4), ("sliver", 2), ("loose", 5)]
    for n in notes:
        assert isinstance(n.kind, str) and n.fold == -1
        assert all(isinstance(v, (int, float)) for v in (n.sheet, n.a, n.b))
        assert all(isinstance(k, int) for k in n.other)
        assert n.at is None or len(n.at) == 2
    # less than a curve's flattening is not told as standing past the edge
    (s,), notes = _laid((_rect(-0.04, 10, 50, 90), 1.0))
    assert _near(s.past, 0.04) and [n.kind for n in notes] == ["thick"]


# -- stitch holes through the layers ------------------------------------------

def test_the_shells_holes_come_through_the_bifolds_liner_and_pockets():
    stack = _bifold()
    assert len(stack.holes) == 142 and stack.hole_sheet == [0] * 142
    check = stack.lineup()
    found = {(f.a, f.b): f for f in check.findings}
    assert sorted(found) == [(0, k) for k in range(1, 10)]
    # all 142 through the liner; 44, 37, 35 and 32 through the backing and
    # pockets 3, 2 and 1 on each side
    assert [found[0, k].holes for k in range(1, 10)] == [142, 44, 37, 35, 32,
                                                         44, 37, 35, 32]
    for f in check.findings:
        assert (f.off, f.met, f.lone, f.through) == (0, 0, f.holes,
                                                     (f.holes, 0))
        assert f.laid and f.names[0] == "Outer shell"
    assert found[0, 5].names == ("Outer shell", "Left pocket 1 (front)")
    # none of it is a fault: no hole is given a colour, and each piece is
    # marked where a hole comes through it
    assert not check.state and not check.lands and not check.rings
    assert {k: len(v) for k, v in check.passed.items()} == {
        1: 142, 2: 44, 3: 37, 4: 35, 5: 32, 6: 44, 7: 37, 8: 35, 9: 32}
    assert sum(len(v) for v in check.passed.values()) == 438
    assert sum(len(v) for v in check.blanks.values()) == 438
    assert set(check.blanks) == set(range(18, 27))     # the sheets' cells


def test_the_card_holders_holes_as_they_are_drawn():
    # Card holder: the back panel is stitched, the pocket has no holes
    stack = _as_drawn(templates.card_holder())
    assert [s.name for s in stack.sheets] == ["Pocket"]
    assert _near(stack.notes[0].a, 2.4)
    assert stack.hole_sheet == [0] * 82
    check = stack.lineup()
    f, = check.findings
    assert (f.a, f.b, f.holes, f.lone, f.through) == (0, 1, 46, 46, (46, 0))
    assert f.names == ("Back panel", "Pocket") and not check.state
    assert len(check.passed[1]) == 46
    # a window cut in the pocket is no leather: holes there come through
    # nothing (here the bottom row, between the corners)
    window = _rect(-40, -34, 40, -29)
    inside = [h for h in stack.holes if pc.inside(h[:2], window)]
    assert len(inside) == 20
    cut = _as_drawn(templates.card_holder(), cutouts=[window])
    assert cut.lineup().findings[0].holes == 46 - 20
    # Slim card holder: one seam through four layers, which each have the
    # holes of it that lie on them
    stack = _as_drawn(templates.slim_card_holder())
    assert _near(stack.notes[0].a, 4.8)
    assert [stack.hole_sheet.count(k) for k in range(4)] == [67, 65, 61, 55]
    check = stack.lineup()
    found = {(f.a, f.b): f for f in check.findings}
    assert {k: (f.holes, f.met, f.off, f.lone) for k, f in found.items()} \
        == {(0, 1): (130, 130, 0, 0), (0, 2): (122, 122, 0, 0),
            (0, 3): (110, 110, 0, 0), (1, 2): (122, 122, 0, 0),
            (1, 3): (110, 110, 0, 0), (2, 3): (110, 110, 0, 0)}
    assert all(f.gap < 1e-9 for f in check.findings)
    # every hole that lies on another layer meets: only the back panel's
    # two at the top of the seam, above every pocket, lie on none
    assert len(check.holes(fs.MEETS)) == 67 + 65 + 61 + 55 - 2
    assert not check.off and not check.passed and not check.blanks


def test_laid_holes_meet_miss_or_come_through():
    def holes(dx):
        return [(20 + 10 * k + dx, 50, 1.2) for k in range(4)]
    main = fs.fold_piece(MAIN, [], holes=holes(0.0) + [(150, 50, 1.2)])
    near = sh.Sheet(_rect(10, 30, 60, 70), 1.0, name="near",
                    holes=holes(0.5)[:3])               # 0.5 mm along
    out = sh.Sheet(_rect(10, 30, 60, 70), 1.0, name="out",
                   holes=[(21.5, 50, 1.0)])             # 1.5 from the first
    stack = sh.FlatStack(main, [near], MAIN, name="main")
    check = stack.lineup()
    f, = check.findings
    # four of the main piece's holes lie under it, and its own three
    assert (f.holes, f.met, f.off, f.lone, f.through) == (7, 6, 0, 1, (1, 0))
    assert _near(f.gap, 0.5)
    assert check.holes(fs.MEETS) == {0, 1, 2, 5, 6, 7} and not check.off
    assert check.passed == {1: [(50.0, 50, 1.2)]}
    # one that is 1.5 mm out is out of line, and both pieces are marked
    # where the other's hole is; the main piece's next hole is too far off
    # to have been meant for it
    stack = sh.FlatStack(main, [out], MAIN)
    check = stack.lineup()
    f, = check.findings
    assert (f.holes, f.off, f.lone, f.met) == (5, 2, 3, 0)
    assert _near(f.worst, 1.5) and check.off == {0, 5}
    assert check.lands == {0: [(21.5, 50, 1.0)], 5: [(20.0, 50, 1.2)]}
    assert check.landed == {0: [(21.5, 50, 1.0)], 1: [(20.0, 50, 1.2)]}
    assert len(check.passed[1]) == 3
    # the marks are sorted by the cell they are on, for the model: the
    # main piece is cell 0 and the sheet cell 1
    assert check.rings == {0: [(21.5, 50, 1.0)], 1: [(20.0, 50, 1.2)]}
    assert check.blanks == {1: [(30.0, 50, 1.2), (40.0, 50, 1.2),
                                (50.0, 50, 1.2)]}
    styles = {}
    for cell in stack.solid([], check):
        for face in cell.faces:
            for kind, geo, style in face.decals or ():
                if kind == bsp.DOTS:
                    key = (sh.sheet_of(cell.tag), style)
                    styles[key] = styles.get(key, 0) + len(geo)
    assert styles == {
        (0, (1.2, fs.OFF)): 2, (0, (1.2, 0)): 8, (0, (1.0, fs.LANDS)): 2,
        (1, (1.0, fs.OFF)): 2, (1, (1.2, fs.LANDS)): 2,
        (1, (1.2, sh.BLANK)): 6}
    # pieces on the two faces of the main piece are sewn through it too
    under = sh.Sheet(_rect(10, 30, 60, 70), 1.0, side=-1, name="under",
                     holes=holes(0.0)[:2])
    check = sh.FlatStack(main, [near, under], MAIN).lineup()
    assert sorted((f.a, f.b) for f in check.findings) == [(0, 1), (0, 2),
                                                          (1, 2)]
    assert len(check.holes(fs.MEETS)) == 3 + 3 + 2


# -- the stack as a model -----------------------------------------------------

def test_a_stack_with_no_sheets_is_its_layout():
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1, label="A"),
             fs.FoldLine((160, 0), (160, 100), 180, 1, order=2, label="B")]
    ring = [(0, 0), (240, 0), (240, 100), (0, 100)]
    holes = [(20, 10, 1.0), (140, 10, 1.0)]
    lay = fs.fold_piece(ring, folds, base=1, holes=holes)
    stack = sh.FlatStack(lay)
    assert stack.sheets == [] and stack.layout is lay
    for name in ("thickness", "folds", "steps", "n_steps", "panels", "base",
                 "crosses", "used", "radius", "wrap", "warnings"):
        assert getattr(stack, name) == getattr(lay, name), name
    assert stack.n_steps == 2 and stack.radius == [0.0, 1.0]
    assert stack.holes == holes and stack.hole_sheet == [0, 0]
    assert [n.kind for n in stack.notes] == ["thick"]
    for t in (0.0, 0.7, 2.0):
        fr = stack.fractions(t)
        assert fr == lay.fractions(t)
        mine, its = stack.solid(fr), lay.solid(fr)
        assert [c.verts for c in mine] == [c.verts for c in its]
        assert [c.tag for c in mine] == [c.tag for c in its]
        assert stack.fold_marks(fr) == lay.fold_marks(fr)
        assert stack.panel_marks(fr) == lay.panel_marks(fr)
        assert stack.bounds3(fr) == lay.bounds3(fr)
    assert stack.extent() == lay.extent()
    assert stack.lineup().state == lay.lineup().state == {0: fs.MEETS,
                                                          1: fs.MEETS}
    # the outline is put together again from the panels
    assert _near(pc.area(stack.outline), 24000.0) and len(stack.outline) == 8
    assert set(ring) <= set(stack.outline)


def test_the_outline_of_a_layout_is_put_together_from_its_panels():
    cross = [(0, 0), (0, -30), (60, -30), (60, 0), (90, 0), (90, 40),
             (60, 40), (60, 70), (0, 70), (0, 40), (-30, 40), (-30, 0)]
    folds = [fs.FoldLine((0, 0), (0, 40), 90, 1),
             fs.FoldLine((60, 0), (60, 40), 90, 1),
             fs.FoldLine((0, 0), (60, 0), 90, 1),
             fs.FoldLine((0, 40), (60, 40), 90, 1)]
    ring = sh.outline_of(fs.fold_piece(cross, folds))
    assert _near(pc.area(ring), pc.area(cross)) and set(ring) == set(cross)
    # folds that cross cut the piece in four, one cut going right through
    square = [(0, 0), (120, 0), (120, 120), (0, 120)]
    folds = [fs.FoldLine((60, 0), (60, 120), 180, 1, order=1),
             fs.FoldLine((0, 60), (120, 60), 180, 1, order=2)]
    ring = sh.outline_of(fs.fold_piece(square, folds))
    assert _near(pc.area(ring), 14400.0)
    # the generated shell, round corners and all
    stack = _bifold()
    again = sh.outline_of(stack.layout)
    assert _near(pc.area(again), pc.area(stack.outline), 1e-6)


def test_the_flat_stack_is_one_cell_for_each_convex_part_of_a_sheet():
    stack = _bifold()
    lay = stack.layout
    assert lay.n_steps == 1 and len(lay.cells) == 18
    # shown flat: its fold is not made, and there is nothing to play
    assert (stack.steps, stack.n_steps, stack.used, stack.wrap) \
        == ([0], 0, [0.0], [0.0])
    assert stack.radius == [0.0] and stack.warnings == []
    assert stack.fractions(1.0) == [0.0]
    assert stack.thickness == 1.6 and stack.folds is lay.folds
    assert stack.panels is lay.panels and stack.crosses == [True]
    cells = stack.solid(stack.fractions(0.0))
    assert len(cells) == 18 + 9
    # the main piece's cells are its layout's own, flat
    own = lay.solid([0.0])
    assert [c.verts for c in cells[:18]] == [c.verts for c in own]
    assert [c.tag for c in cells[:18]] == [c.tag for c in own]
    # each sheet's cell says which panel of the main piece it lies on: the
    # left stack (as it lies on the bench) on panel 2, the right on panel 1
    assert [c.tag for c in cells[18:]] == [
        ("panel", 1, 1)] + [("panel", 1, k) for k in (2, 3, 4, 5)] \
        + [("panel", 0, k) for k in (6, 7, 8, 9)]
    for cell, s in zip(cells[18:], stack.sheets):
        zs = sorted({v[2] for v in cell.verts})
        assert _near(zs[0], -(0.8 + s.hi)) and _near(zs[1], -(0.8 + s.lo))
        kinds = {round(f.plane[2]): f.kind for f in cell.faces
                 if abs(f.plane[2]) > 0.5}
        # grain away from the shell: its flesh side is the face turned the
        # way the shell's front is
        assert kinds == {1: bsp.BACK, -1: bsp.FRONT}
        front = [f for f in cell.faces if f.kind == bsp.FRONT]
        assert _near(sum(_area3(f.pts) for f in front), pc.area(s.ring))
        # its cut edge, with a line down each sharp corner
        walls = [f for f in cell.faces if f.kind == bsp.WALL]
        assert len(walls) == len(s.ring)
        assert all(f.edges[0] is True and f.edges[2] is True for f in walls)
        assert clash.convex(cell) < 1e-9
    assert all(f.kind != bsp.INNER for c in cells[18:] for f in c.faces)
    assert stack.extent()[0] == (0.0, 0.0, -2.0)
    lo, hi = stack.bounds3([0.0])
    assert _near(lo[2], -4.8) and _near(hi[2], 0.8)
    assert _near(hi[0], 99.6, 1e-9) and _near(lo[1], -45.0)


def _area3(pts):
    nx = ny = nz = 0.0
    x0, y0, z0 = pts[-1]
    for x1, y1, z1 in pts:
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
        x0, y0, z0 = x1, y1, z1
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def test_a_sheet_shows_its_grain_where_its_grain_is():
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    cases = ((1, 1, 1.0, 1.5, bsp.FRONT), (1, -1, 1.0, 1.5, bsp.BACK),
             (-1, 1, -1.5, -1.0, bsp.FRONT), (-1, -1, -1.5, -1.0, bsp.BACK))
    for side, grain, z0, z1, up in cases:
        stack = sh.FlatStack(lay, [sh.Sheet(_rect(20, 20, 80, 80), 0.5,
                                            side=side, grain=grain)], MAIN)
        cell = stack.solid([])[1]
        top = [f for f in cell.faces if f.plane[2] > 0.5][0]
        bot = [f for f in cell.faces if f.plane[2] < -0.5][0]
        assert {p[2] for p in top.pts} == {z1}
        assert {p[2] for p in bot.pts} == {z0}
        assert top.kind == up and bot.kind == bsp.FRONT + bsp.BACK - up
        # the texture lies where the pattern does
        assert top.uv == ((0.0, 0.0, z1), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def test_pulling_apart_lifts_each_sheet_by_its_rank():
    stack = _bifold()
    for apart in (0.0, 2.5, 8.0):
        cells = stack.solid([0.0], None, apart)
        for cell, s in zip(cells[18:], stack.sheets):
            top = -(0.8 + s.lo + apart * s.rank)
            zs = sorted({v[2] for v in cell.verts})
            assert _near(zs[1], top) and _near(zs[0], top - 0.8)
        # the shell stays where it is
        assert {v[2] for c in cells[:18] for v in c.verts} == {-0.8, 0.8}
        lo, hi = stack.bounds3([0.0], apart)
        assert _near(lo[2], -(0.8 + 4.0 + 5 * apart)) and _near(hi[2], 0.8)
    # the marks go with the leather they are on
    (fl, pl), = (m[5] for m in stack.fold_marks([0.0], 6.0)[0][1:])
    assert (fl, pl) == (0, 1)
    assert stack.fold_marks([0.0], 6.0)[0][1][2][2] == -(0.8 + 0.8 + 6.0)


def test_fold_lines_and_panel_numbers_sit_on_the_outermost_leather():
    stack = _bifold()
    top, rest = stack.fold_marks([0.0])[0]
    # the liner stops 2 mm short of the shell's top edge: there the line
    # is on the shell itself, on both of its faces
    assert top == ((0.0, 45.0, 0.8), (0.0, 43.0, 0.8), (0.0, 45.0, -0.8),
                   (0.0, 43.0, -0.8), (0.0, 0.0, 1.0), (0, 0))
    # ... and from there down it is on the liner, on the side that is on
    assert rest == ((0.0, 43.0, 0.8), (0.0, -45.0, 0.8), (0.0, 43.0, -1.6),
                    (0.0, -45.0, -1.6), (0.0, 0.0, 1.0), (0, 1))
    # each panel's number is on the pocket that covers its spot: pocket 3
    # of the stack that lies on it
    one, two = stack.panel_marks([0.0])
    assert one[3] == (0, 7) and two[3] == (0, 3)
    assert _near(one[0][2], 0.8) and _near(one[1][2], -(0.8 + 2.4))
    assert one[0][:2] == one[1][:2] == stack.panels[0].spot
    assert one[2] == (0.0, 0.0, 1.0)


def test_holes_cutouts_and_lines_of_a_sheet_ride_on_its_faces():
    lay = fs.fold_piece(MAIN, [], holes=[(5, 5, 1.0)])
    ell = [(20, 20), (120, 20), (120, 40), (60, 40), (60, 80), (20, 80)]
    slot = _rect(30, 25, 50, 30)
    sheet = sh.Sheet(ell, 1.0, holes=[(100, 30, 1.2), (40, 60, 1.2)],
                     cutouts=[slot], marks=[("cut", [(30, 50), (50, 70)]),
                                            ("line", [(25, 22), (115, 22)])])
    stack = sh.FlatStack(lay, [sheet], MAIN)
    assert stack.holes == [(5, 5, 1.0), (100, 30, 1.2), (40, 60, 1.2)]
    assert stack.hole_sheet == [0, 1, 1]
    cells = stack.solid([])[1:]
    assert len(cells) == 2 and all(c.tag == ("panel", 0, 1) for c in cells)
    for z in (2.0, 1.0):                   # its top face, then its underside
        dots, cut, score, patch = [], 0.0, 0.0, 0.0
        for cell in cells:
            face, = [f for f in cell.faces
                     if {p[2] for p in f.pts} == {z}]
            for kind, geo, style in face.decals or ():
                assert all(p[2] == z for p in geo)
                if kind == bsp.DOTS:
                    dots += [(p[:2], style) for p in geo]
                elif kind == bsp.LINE and style == "cut":
                    cut += math.dist(*geo)
                elif kind == bsp.LINE:
                    score += math.dist(*geo)
                else:
                    patch += _area3(geo)
        assert sorted(dots) == [((40, 60), (1.2, 0)), ((100, 30), (1.2, 0))]
        assert _near(cut, math.hypot(20, 20)) and _near(score, 90.0)
        assert _near(patch, 100.0)
    # the seams between its parts are inside the leather: not painted
    inner = [f for c in cells for f in c.faces if f.kind == bsp.INNER]
    assert len(inner) == 2 and all(f.edges is None for f in inner)
    assert _near(sum(c.big for c in cells), pc.area(ell))

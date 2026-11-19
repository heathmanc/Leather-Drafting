"""Pieces laid on a piece that folds, lying flat: the height each comes to
rest at, what it rests on and stands clear of, the stitch holes through
the layers, and the stack as a model for the view."""

import dataclasses
import math
import random

import pytest

from leathercad import bsp
from leathercad import clash
from leathercad import foldsolid as fs
from leathercad import polycut as pc
from leathercad import sheets as sh
from leathercad import templates
from leathercad.generators import bifold_wallet
from leathercad.geometry import Vec2
from leathercad.path import CubicBezier
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


def _lie(ring, turn=0.0, dx=0.0, dy=0.0):
    """An outline turned ``turn`` degrees about the origin and moved."""
    c, sn = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    return [(dx + x * c - y * sn, dy + x * sn + y * c) for x, y in ring]


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


def test_a_piece_in_many_parts_is_told_clear_of_each_thing_it_bridges():
    # a comb across three piles and the gaps between them: its back along
    # y = 40 to 50, a tooth up over each pile
    comb = [(20, 40), (190, 40), (190, 50), (160, 50), (160, 70), (140, 70),
            (140, 50), (100, 50), (100, 70), (80, 70), (80, 50), (40, 50),
            (40, 70), (20, 70)]
    (a, d, b, c, top), notes = _laid((_rect(10, 10, 50, 90), 2.0),
                                     (_rect(75, 15, 105, 85), 0.3),
                                     (_rect(70, 10, 110, 90), 1.5),
                                     (_rect(130, 10, 170, 90), 0.5),
                                     (comb, 1.0))
    assert len(top.parts) >= 4
    assert (b.lo, b.hi, b.clear) == (0.3, 1.8, [(0, 0.3)])
    assert (top.lo, top.rests, top.rank) == (2.0, [1], 3)
    # the widest gap first: the main piece between the piles, then the
    # thin pile, then the pile 0.2 mm lower than the one it rests on.
    # Piece 2 is wholly under piece 3: nothing stands clear of it
    assert [k for k, _gap in top.clear] == [0, 4, 3]
    assert [gap for _k, gap in top.clear] == pytest.approx([2.0, 1.5, 0.2])
    assert notes[-1] == sh.Note("clear", 5, -1, 2.0, other=(0, 4, 3))
    # laid the other way round along the bench it is the same
    flip = [(210 - x, y) for x, y in comb]
    (_a, _d, _b, _c, top), _notes = _laid((_rect(160, 10, 200, 90), 2.0),
                                          (_rect(105, 15, 135, 85), 0.3),
                                          (_rect(100, 10, 140, 90), 1.5),
                                          (_rect(40, 10, 80, 90), 0.5),
                                          (flip, 1.0))
    assert [k for k, _gap in top.clear] == [0, 4, 3]
    # with its back on the tall pile alone it stands clear of nothing
    (_a, top), _notes = _laid((_rect(10, 10, 195, 90), 2.0), (comb, 1.0))
    assert top.rests == [1] and top.clear == []


def test_a_piece_wholly_under_another_of_its_own_outline_is_not_bridged():
    # The plate cut in two along a curve; on the left half a second piece
    # of the very same outline; a strap across the cut. Whatever is over
    # the first left half is over the second first: the strap rests on
    # that, and stands clear of the right half only -- not of the left
    # half under it, nor of the main piece, which shows under the strap
    # only in the hair's width between the two halves as they are laid
    main = [(0, 0), (300, 0), (300, 200), (0, 200)]
    rng = random.Random(8)
    for trial in range(12):
        left, right, _cut = _cut_in_two((
            (140, 50), (rng.uniform(100, 190), rng.uniform(60, 90)),
            (rng.uniform(100, 190), rng.uniform(110, 140)), (140, 150)))
        (low, beside, top, strap), notes = _laid(
            (left, 0.8), (right, 0.8), (left, 0.6),
            (_rect(110, 80, 170, 120), 1.2), main=main)
        assert low.ring == top.ring and top.rests == [1]
        assert (strap.lo, strap.rests) == (1.4, [3]), trial
        assert strap.clear == [(2, pytest.approx(0.6))], trial
        assert notes[-1] == sh.Note("clear", 4, -1, strap.clear[0][1],
                                    other=(2,)), trial
    # Two pockets of one outline with a thumb notch, one on the other,
    # and a strap across them that stands out over the notch and past
    # their edge: clear of the main piece, by both their thicknesses,
    # and of nothing else -- wherever on the bench it lies
    arc = [(50 + 12 * math.cos(math.pi * i / 14),
            60 - 12 * math.sin(math.pi * i / 14)) for i in range(15)]
    pocket = [(0, 0), (100, 0), (100, 60)] + arc + [(0, 60)]
    plate = _rect(-900, -900, 900, 900)
    for turn, dx, dy in ((0, 0, 0), (10, 0, 0), (0, 300, 300),
                         (45, 37.5, 12.25), (65, 150, -300), (70, -300, 150)):
        (low, top, strap), notes = _laid(
            (_lie(pocket, turn, dx, dy), 0.8),
            (_lie(pocket, turn, dx, dy), 0.6),
            (_lie(_rect(20, 20, 130, 75), turn, dx, dy), 1.2), main=plate)
        assert (top.rests, strap.rests) == ([1], [2])
        assert strap.clear == [(0, pytest.approx(1.4))], (turn, dx, dy)


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
    # counter-clockwise from its first corner, and the doubled point at
    # its end dropped
    assert s.ring == [(0.0, 0.0), (80.0, 0.0), (80.0, 50.0), (0.0, 50.0)]
    assert (s.thickness, s.side, s.grain) == (0.05, -1, 1)
    assert sh.Sheet(_rect(0, 0, 9, 9), 1.0, side=0).side == 1
    assert s.holes == [(5, 5, 1.2)] and pc.area(s.cutouts[0]) == 100.0
    assert s.marks == [("cut", [(1, 1), (9, 9)])]
    assert (s.name, s.key, s.leather) == ("", "", "")
    assert s.parts == [s.ring]
    # its grain is laid from its first corner, along its first edge
    assert s.frame == ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    # an outline with a notch in it is kept in convex parts
    ell = sh.Sheet([(0, 0), (60, 0), (60, 20), (20, 20), (20, 50), (0, 50)],
                   1.0)
    assert len(ell.parts) == 2
    assert _near(sum(pc.area(p) for p in ell.parts), 1800.0)
    assert sh.sheet_of(("panel", 1)) == 0 and sh.sheet_of(("bend", 0)) == 0
    assert sh.sheet_of(("panel", 1, 4)) == 4 and sh.sheet_of(None) == 0


def test_the_thickest_place_counts_the_main_piece_only_where_it_is():
    # a strap that stands 80 mm past the edge, with a keeper on it out
    # there: over the main piece the stack is 2 + 1, at the keeper 1 + 3
    (strap, keeper), notes = _laid((_rect(120, 40, 280, 60), 1.0),
                                   (_rect(230, 35, 270, 65), 3.0))
    assert (keeper.lo, keeper.rests) == (1.0, [1])
    thick = notes[0]
    assert (thick.a, thick.b, thick.other) == (4.0, 4.0, (1, 2))
    assert thick.at == (250.0, 50.0)
    # a 3 mm piece lying beside the main piece is 3 mm; a pocket on each
    # face of the main piece, one over the other, is 2 + 0.8 + 0.6
    (_loose, _front, _back), notes = _laid((_rect(230, 20, 280, 80), 3.0),
                                           (_rect(20, 20, 80, 80), 0.8),
                                           (_rect(30, 30, 90, 90), 0.6, -1))
    thick = notes[0]
    assert _near(thick.a, 3.4) and _near(thick.b, 3.4)
    assert thick.other == (0, 2, 3)
    assert 30 < thick.at[0] < 80 and 30 < thick.at[1] < 80
    # piles on the two faces count together only where one is over the
    # other
    _two, notes = _laid((_rect(20, 20, 80, 80), 1.0),
                        (_rect(120, 20, 180, 80), 0.6, -1))
    assert (notes[0].a, notes[0].other) == (3.0, (0, 1))
    assert 20 < notes[0].at[0] < 80
    # Of the thickest place, the point told is one with most leather in
    # it. Here sheet 2 (front) and sheet 4 (back) are over each other from
    # 150 to 230 mm across, the main piece between them as far as 200:
    # 6.6 mm from the underside of the one to the top of the other, and
    # 2 + 0.6 + 2 of leather where the main piece is
    _four, notes = _laid((_rect(20, 20, 100, 80), 1.0),
                         (_rect(60, 30, 230, 70), 0.6),
                         (_rect(210, 20, 280, 80), 1.0, -1),
                         (_rect(150, 10, 260, 90), 2.0, -1))
    thick = notes[0]
    assert _near(thick.a, 6.6) and _near(thick.b, 4.6)
    assert thick.other == (0, 2, 4) and 150 < thick.at[0] < 200


def test_of_places_as_thick_as_each_other_the_one_with_most_leather_is_told():
    # A step lying beside the main piece, a strap from it across on to
    # the main piece, and a patch on the main piece elsewhere. The strap
    # is a bridge over the main piece: 2 + 0.5 of gap + 1 = 3.5 mm from
    # the main piece's underside to the strap's top, 3 mm of it leather.
    # The patch is 2 + 1.5 = 3.5 mm too, and leather all through
    (step, strap, patch), notes = _laid((_rect(200, 20, 250, 80), 0.5),
                                        (_rect(150, 40, 230, 60), 1.0),
                                        (_rect(20, 20, 80, 80), 1.5))
    assert (step.rests, strap.lo, strap.rests) == ([], 0.5, [1])
    assert strap.clear == [(0, 0.5)]
    thick = notes[0]
    assert (thick.a, thick.b, thick.other) == (3.5, 3.5, (0, 3))
    assert 20 < thick.at[0] < 80 and 20 < thick.at[1] < 80
    # with the patch laid first it is the same; and a patch a hair
    # thinner is not as thick: then it is the bridge that is told
    (patch, step, strap), notes = _laid((_rect(20, 20, 80, 80), 1.5),
                                        (_rect(200, 20, 250, 80), 0.5),
                                        (_rect(150, 40, 230, 60), 1.0))
    assert (notes[0].a, notes[0].b, notes[0].other) == (3.5, 3.5, (0, 1))
    _three, notes = _laid((_rect(200, 20, 250, 80), 0.5),
                          (_rect(150, 40, 230, 60), 1.0),
                          (_rect(20, 20, 80, 80), 1.49))
    assert (notes[0].a, notes[0].b, notes[0].other) == (3.5, 3.0, (0, 2))
    assert 150 < notes[0].at[0] < 200


def test_the_thickest_place_is_told_alike_wherever_the_stack_is_drawn():
    # the plate cut in two along a curve, the thicker half laid first:
    # 2 + 1 mm where that half is, and the other half only touches it
    main = [(0, 0), (300, 0), (300, 200), (0, 200)]
    left, right, _cut = _cut_in_two(((140, 50), (180, 80), (100, 120),
                                     (140, 150)))
    for turn, dx, dy in ((0, 0, 0), (0, 0, 100), (0, 0, 200),
                         (0, 700, 1000), (90, 0, 0), (37, -300, 450)):
        (a, b), notes = _laid((_lie(left, turn, dx, dy), 1.0),
                              (_lie(right, turn, dx, dy), 0.8),
                              main=_lie(main, turn, dx, dy))
        assert (a.lo, b.lo) == (0.0, 0.0)
        thick, = notes
        where = (turn, dx, dy)
        assert (thick.a, thick.b, thick.other) == (3.0, 3.0, (0, 1)), where
        # ... at a point well inside that half, not on the cut
        assert pc.inside(thick.at, a.ring), where
        assert sh._to_ring(thick.at, a.ring) > 1.0, where
        assert not pc.inside(thick.at, b.ring), where
    # What two pieces that only touch have in common is a speck a
    # millionth of a millionth of a mm across: it is no place, wherever
    # it is and however its area comes out
    speck = [(142.20199, 129.7745), (142.20199 + 5e-13, 129.7745 + 4e-12),
             (142.20199 + 3e-13, 129.7745 + 1e-12)]
    assert not sh._wider(speck, sh.LAP) and not sh._wider(speck, sh.HAIR)
    assert abs(sh._area(speck)) < 1e-20 < 1e-13 < abs(pc.area(speck)) + 1e-13
    # the centre of a bit is a point of it, wherever the bit is drawn
    for dx, dy in ((0, 0), (800, 600), (-5000, 9000)):
        bit = [(dx + 1.0, dy + 2.0), (dx + 1.0004, dy + 2.0),
               (dx + 1.0002, dy + 2.0003)]
        cx, cy = sh._centre(bit)
        assert abs(cx - dx - 1.0002) < 1e-9 and abs(cy - dy - 2.0001) < 1e-9
    assert sh._centre([(3.0, 4.0), (3.0, 4.0), (5.0, 4.0)]) == (
        11.0 / 3.0, 4.0)                       # no area: its corners' middle


def _cut_in_two(ctrl, x0=80.0, x1=200.0):
    """A plate cut in two along a bezier curve, flattened as the app
    flattens one: ``(the piece on the left, the piece on the right, the
    corners along the cut)``, which both pieces have."""
    bez = CubicBezier(*[Vec2(*q) for q in ctrl])
    cut = [tuple(map(float, ctrl[0]))] + [(q.x, q.y) for q in bez.flatten()]
    y0, y1 = cut[0][1], cut[-1][1]
    return [(x0, y0)] + cut + [(x0, y1)], \
        [(x1, y1)] + cut[::-1] + [(x1, y0)], cut


def _within(ring, given):
    """Whether no corner of an outline is outside the one it was made
    from."""
    return all(pc.inside(q, given) or sh._to_ring(q, given) < 1e-9
               for q in ring)


def _outside(ring, given) -> float:
    """How far an outline is outside the one it was made from, at the
    most: at its corners and at seven places along each edge."""
    worst = 0.0
    a = ring[-1]
    for b in ring:
        for k in range(8):
            q = (a[0] + (b[0] - a[0]) * k / 8, a[1] + (b[1] - a[1]) * k / 8)
            if not pc.inside(q, given):
                worst = max(worst, sh._to_ring(q, given))
        a = b
    return worst


def test_two_pieces_cut_along_one_curve_lie_on_one_level():
    main = [(0, 0), (300, 0), (300, 200), (0, 200)]
    left, right, cut = _cut_in_two(((140, 50), (180, 80), (100, 120),
                                    (140, 150)))
    assert len(cut) == 77 and set(cut) <= set(left) & set(right)
    (a, b), notes = _laid((left, 1.0), (right, 0.8), main=main)
    # neither is on the other: both lie on the main piece, and there is
    # nothing to tell but the 3 mm of the thicker with the main piece
    assert (a.lo, b.lo, a.rests, b.rests) == (0.0, 0.0, [0], [0])
    assert [n.kind for n in notes] == ["thick"] and _near(notes[0].a, 3.0)
    assert sh._reach(a.parts, b.parts) <= sh.LAP
    # Each outline is thinned by itself, and so they no longer have every
    # corner of the cut in common -- but each is thinned inwards only, to
    # within 0.03 mm: neither has leather it was not drawn with
    assert len(a.ring) < len(left) and len(b.ring) < len(right)
    assert len(set(a.ring) & set(b.ring)) < len(cut) - 10
    for s, given in ((a, pc.ccw(left)), (b, pc.ccw(right))):
        assert _within(s.ring, given)
        assert pc.area(given) - 3.0 < pc.area(s.ring) <= pc.area(given)
        assert max(sh._to_ring(q, s.ring) for q in given) <= 0.03 + 1e-9
    # in the model they share nothing
    lay = fs.fold_piece(main, [], thickness=2.0)
    stack = sh.FlatStack(lay, [sh.Sheet(left, 1.0), sh.Sheet(right, 0.8)],
                         main)
    assert clash.clashes(stack.solid([])) == []
    # any curve, and whichever of the two is laid first
    rng = random.Random(8)
    for trial in range(40):
        left, right, _cut = _cut_in_two((
            (140, 50), (rng.uniform(100, 190), rng.uniform(60, 90)),
            (rng.uniform(100, 190), rng.uniform(110, 140)), (140, 150)))
        pair = [(left, 1.0), (right, 0.8)][::1 if trial % 2 else -1]
        (a, b), notes = _laid(*pair, main=main)
        assert b.lo == 0.0 and [n.kind for n in notes] == ["thick"], trial
        # ... and the thickest place is the thicker of the two on the main
        # piece, 3 mm of leather, at a point of that piece: the other only
        # touches it
        thick, mm1 = notes[0], (a if trial % 2 else b)
        assert (thick.a, thick.b) == (3.0, 3.0), trial
        assert thick.other == (0, 1 if trial % 2 else 2), trial
        assert pc.inside(thick.at, mm1.ring), trial
    # ... and a hair's gap along the cut is a gap, not an overlap
    left, right, _cut = _cut_in_two(((140, 50), (180, 80), (100, 120),
                                     (140, 150)))
    apart = [(x + 0.002, y) for x, y in right]
    (a, b), _notes = _laid((left, 1.0), (apart, 0.8), main=main)
    assert b.lo == 0.0
    # while a piece really pushed 0.2 mm on to the other does climb
    nudged = [(x - 0.2, y) for x, y in right]
    (a, b), _notes = _laid((left, 1.0), (nudged, 0.8), main=main)
    assert (b.lo, b.rests) == (1.0, [1])


def _scalloped(cx, cy, r, lobes=24, n=600, deep=2.0):
    """A round with a wavy edge, drawn in ``n`` corners."""
    return [(cx + (r + deep * math.cos(lobes * 2 * math.pi * k / n))
             * math.cos(2 * math.pi * k / n),
             cy + (r + deep * math.cos(lobes * 2 * math.pi * k / n))
             * math.sin(2 * math.pi * k / n)) for k in range(n)]


def test_a_busy_outline_is_thinned_in_its_hollows_too_and_inwards_only():
    given = _scalloped(120, 100, 60)
    s = sh.Sheet(given, 1.0)
    # 600 corners given, half of them in the 24 hollows of its edge:
    # about as few are kept as of a lone piece's outline, which is thinned
    # both ways
    plain = fs.model_outline(given)
    assert len(given) == 600 and len(plain) == 152
    assert len(s.ring) < 1.5 * len(plain) and len(s.parts) < 120
    assert _within(s.ring, given)
    # (so busy an outline is followed to 0.03 x 1.6 x 1.6 x 1.6 = 0.123 mm,
    # the lone piece's too: closer, it would keep more than 260 corners)
    assert 0.1 < max(sh._to_ring(q, s.ring) for q in given) <= 0.123
    assert pc.area(given) - 30.0 < pc.area(s.ring) < pc.area(given)
    # round a hollow the corners kept are not corners that were given:
    # they stand a little way into the leather
    assert len(set(s.ring) - set(given)) > 40
    # made ready a second time, it stays as it is
    again = sh.Sheet(s.ring, 1.0)
    assert again.ring == s.ring and again.frame == s.frame
    # Round a bulge a corner is dropped only where the line between its
    # neighbours cuts it off. One that stands ever so little to the left
    # of that line, where the leather is, stays however near it is: to
    # drop it would add leather
    assert sh._chords([(0, 0), (5, -0.01), (10, 0)], 0.03) == [
        (0, 0), (10, 0)]
    assert sh._chords([(0, 0), (5, 0.01), (10, 0)], 0.03) == [
        (0, 0), (5, 0.01), (10, 0)]
    assert len(sh._chords([(0, 0), (5, -0.04), (10, 0)], 0.03)) == 3
    # an outline with few corners is not touched
    box = sh.Sheet([(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)],
                   1.0)
    assert sorted(box.ring) == sorted([(0.0, 0.0), (30.0, 0.0), (30.0, 10.0),
                                       (10.0, 10.0), (10.0, 30.0),
                                       (0.0, 30.0)])


def _wavy_strap(n=1000, waves=40):
    """The strap with two wavy edges of tests/test_foldsolid.py: 240 mm
    long, its long edges waving 3 mm either way, in ``2 n + 2`` corners."""
    top = [(240 * k / n, 100 + 3 * math.sin(2 * math.pi * waves * k / n))
           for k in range(n + 1)]
    bot = [(240 * k / n, -3 * math.sin(2 * math.pi * waves * k / n))
           for k in range(n + 1)]
    return bot + top[::-1]


def test_a_lining_cut_to_a_busy_main_pieces_own_outline_is_within_it():
    strap = _wavy_strap()
    plain = fs.model_outline(strap)
    assert len(strap) == 2002 and len(plain) == 164
    # The main piece's outline is thinned both ways, so busy a one to
    # half a mm: as it is laid it stops up to 0.445 mm short of the
    # outline given, and reaches as far outside it
    given = pc.ccw([(float(x), float(y)) for x, y in strap])
    reach, short = sh._strays(given, plain)
    assert _near(reach, 0.445, 1e-3) and _near(short, 0.445, 1e-3)
    assert sh._strays(MAIN, MAIN) == (0.0, 0.0)
    # On it: a lining cut to that very outline; on that a pocket that
    # shares the strap's lower edge and no more; and a patch that stands
    # 2 mm past the strap's straight end
    half = [q for q in strap[:1001] if q[0] <= 120.0]
    liner = sh.Sheet(strap, 0.8)
    pocket = sh.Sheet(half + [(120.0, 60.0), (0.0, 60.0)], 0.8)
    patch = sh.Sheet(_rect(-2, 40, 30, 60), 0.5)
    notes = sh.settle(strap, 2.0, [liner, pocket, patch])
    assert [s.rests for s in (liner, pocket, patch)] == [[0], [1], [2]]
    # The lining lies within the main piece, and is told as standing
    # nowhere past it. It is laid in as few corners as the main piece is
    # -- the same corners, drawn in -- never outside the outline it was
    # given, which it follows to 0.85 mm
    assert liner.past == 0.0
    assert len(liner.ring) == 164 and len(liner.parts) < 100
    assert _within(liner.ring, given)
    assert 0.8 < liner.within < 0.86
    far = max(sh._to_ring(q, liner.ring) for q in given[::7])
    assert liner.within - 0.02 < far <= liner.within + 1e-9
    # The pocket has fewer corners, so it is followed more closely,
    # inwards: as it is laid it stands 0.43 mm past the main piece's
    # thinned outline in places, and past the lining's by more. That is
    # the thinning -- less than the main piece is laid short by, and
    # less than twice what the lining was followed to -- and neither is
    # told: the pocket stands past no edge and clear of nothing
    assert _near(pocket.within, 0.03 * 1.6 ** 3) and len(pocket.ring) < 260
    assert 0.4 < pocket.past < short and pocket.clear == []
    # what does stand past the edge is told, to the hair
    assert _near(patch.past, 2.0) and patch.clear == []
    assert [(n.kind, n.sheet) for n in notes] == [("thick", 0), ("past", 3)]
    assert _near(notes[0].a, 2.0 + 0.8 + 0.8 + 0.5)
    # ... nor is the 0.1 mm of a scalloped lining on a scalloped piece
    round_ = _scalloped(120, 100, 60)
    (same,), notes = _laid((round_, 0.8), main=round_)
    assert 0.09 < same.past < 0.1 < sh._strays(
        pc.ccw(round_), fs.model_outline(round_))[1]
    assert [n.kind for n in notes] == ["thick"]
    # A busy sheet on a plain main piece is told to the hair, as any is:
    # the main piece's own outline is followed exactly there
    (on_plain,), notes = _laid(([(x + 0.3, y) for x, y in strap], 0.8),
                               main=_rect(0, -3.3, 240, 103.2))
    assert [n.kind for n in notes] == ["thick", "past"]
    assert _near(notes[1].a, 0.3, 0.02)
    # the stack is told the same: it settles from the outline as given,
    # and so knows how far short of it the main piece is laid
    stack = sh.FlatStack(fs.fold_piece(plain, [], thickness=2.0), [
        sh.Sheet(strap, 0.8),
        sh.Sheet(half + [(120.0, 60.0), (0.0, 60.0)], 0.8)], strap)
    assert [n.kind for n in stack.notes] == ["thick"]
    assert 0.4 < stack.sheets[1].past < short
    assert stack.outline == plain
    # ... while between two plain pieces a place a third of a mm wide is
    # one to stand clear of
    (_a, _b, c), _notes = _laid((_rect(20, 20, 100, 80), 2.0),
                                (_rect(100.3, 20, 180, 80), 0.5),
                                (_rect(60, 40, 140, 60), 1.0))
    assert c.clear == [(0, 2.0), (2, 1.5)]


def test_a_lining_of_a_scalloped_round_is_not_told_as_past_it(monkeypatch):
    # A round with 25 shallow scallops and a lining cut the same. Taking
    # the main piece out of the lining, part by part, cuts one bit of it
    # through a corner: that corner came out twice, a millionth of a
    # millionth of a mm apart, and the "edge" between the two, which
    # runs any way at all, parted the bit from the very parts it lay in.
    # Left over, it was measured from the main piece's outline as if it
    # were outside it: 0.09 mm "past the edge".
    ring = [(120 + (r * math.cos(a)), 100 + r * math.sin(a))
            for a, r in ((2 * math.pi * k / 1200, 34.85357557868675
                          + 0.5149222909899973
                          * math.cos(25 * 2 * math.pi * k / 1200))
                         for k in range(1200))]
    (lining,), notes = _laid((ring, 0.8), main=ring)
    plain = fs.model_outline(ring)
    assert len(plain) == 241 and len(lining.ring) == 225
    assert _near(lining.within, 0.048)
    short = sh._strays(pc.ccw(ring), plain)[1]
    assert 0.029 < short < 0.03
    # it stands nowhere further past the main piece as laid than that
    # is laid short of its outline, and nothing is told
    assert lining.past < short and [n.kind for n in notes] == ["thick"]
    assert not sh._in(lining.past_at, sh._convex(plain))
    # a polygon cut through a corner has that corner once on each side
    left, right = sh._split(_rect(0, 0, 10, 10), (0.0, 0.0),
                            (10.0, 10.0 + 1e-13))
    assert len(left) == 3 and len(right) == 3
    halves = ([(0, 0), (5.0, 0.0), (5.0, 10.0), (0, 10)],
              [(5.0, 0.0), (10, 0), (10, 10), (5.0, 10.0)])
    assert sh._split(_rect(0, 0, 10, 10), (5.0, 0.0), (5.0, 9.0)) == halves
    # (nor is the last corner of a side its first over again)
    assert sh._split(_rect(0, 0, 10, 10) + [(0, 1e-13)], (5.0, 0.0),
                     (5.0, 9.0)) == halves
    # ... and should a bit of a piece be left where the other was taken
    # out of it all the same, it is not past that other
    ell = [(0, 0), (60, 0), (60, 20), (20, 20), (20, 60), (0, 60)]
    ground = sh._convex(ell)
    assert len(ground) == 2
    inner = [(5.0, 5.0), (15.0, 5.0), (10.0, 12.0)]
    assert sh._past([inner], ground, ell) == (0.0, None)
    monkeypatch.setattr(sh, "_minus", lambda *_a, **_k: [inner])
    assert sh._past([inner], ground, ell) == (0.0, None)


def test_past_the_edge_is_told_by_more_than_the_main_piece_is_laid_short():
    # A main piece whose bottom edge bulges 0.4 mm, laid as the plain
    # rectangle without the bulge: 0.4 short of its outline, nowhere
    # outside it. A piece that goes 0.3 mm into the bulge is within the
    # main piece as it is drawn: there is nothing to tell.
    plain = _rect(0, 0, 200, 100)
    piece = [(80, -0.3), (120, -0.3), (120, 50), (80, 50)]
    bulged = [(0, 0), (100, -0.4), (200, 0), (200, 100), (0, 100)]
    assert sh._strays(bulged, plain) == (0.0, 0.4)
    s = sh.Sheet(piece, 1.0)
    notes = sh._lay(bulged, plain, 2.0, [s])
    assert _near(s.past, 0.3) and [n.kind for n in notes] == ["thick"]
    # One whose edge is drawn 0.4 mm in, laid as the same rectangle,
    # reaches 0.4 outside its outline and is nowhere short of it: the
    # same piece stands past the edge as it is laid, and so further past
    # the edge as it is drawn. That is told.
    dented = [(0, 0), (100, 0.4), (200, 0), (200, 100), (0, 100)]
    assert sh._strays(dented, plain) == (0.4, 0.0)
    s = sh.Sheet(piece, 1.0)
    notes = sh._lay(dented, plain, 2.0, [s])
    assert [(n.kind, n.sheet) for n in notes] == [("thick", 0), ("past", 1)]
    assert _near(notes[1].a, 0.3)


def test_a_piece_laid_short_along_a_busy_edge_is_no_bridge():
    # A lining with wavy edges on a plain main piece; on it the lower
    # half of the same strap; on that, the left part of that half. All
    # three share the wavy lower edge as they are drawn, and each is
    # thinned by itself: the lining to 0.85 mm, the half to 0.5, the part
    # to 0.2. So each piece as it is laid reaches a little past the one
    # under it, here and there along that edge, over the piece below --
    main = _rect(-10, -10, 250, 110)
    strap = _wavy_strap()
    lining = sh.Sheet(strap, 0.8)
    half = sh.Sheet(strap[:1001] + [(240.0, 50.0), (0.0, 50.0)], 1.0)
    part = sh.Sheet([q for q in strap[:1001] if q[0] <= 130.0]
                    + [(130.0, 40.0), (0.0, 40.0)], 0.6)
    notes = sh.settle(main, 2.0, [lining, half, part])
    assert [round(s.within, 3) for s in (lining, half, part)] \
        == [0.849, 0.5, 0.197]
    assert [s.rests for s in (lining, half, part)] == [[0], [1], [2]]
    assert sh._reach(half.parts, lining.parts) > 40.0
    # -- by a strip no wider than the one under it is laid short by. That
    # is no place where a piece bridges a gap: nothing stands clear of
    # anything, and there is nothing to tell but the 4.4 mm of all four
    assert [s.clear for s in (lining, half, part)] == [[], [], []]
    assert [(n.kind, n.sheet) for n in notes] == [("thick", 0)]
    assert _near(notes[0].a, 4.4) and notes[0].other == (0, 1, 2, 3)
    # (the strips are there: counted from a hair, as between plain
    # pieces, the half would be told to stand clear of the main piece)
    under = [(lining.hi, 0)]
    assert sh._gaps(half, under, sh._convex(main), [lining, half, part]) \
        == []
    lining.within = 0.0
    assert sh._gaps(half, under, sh._convex(main), [lining, half, part]) \
        == [(0, 0.8)]


def test_the_thick_point_is_a_point_of_the_place_however_small_or_far_off():
    # One piece on another by two thousandths of a micron, drawn ten
    # metres from the origin of the pattern: the thickest place is that
    # hair of overlap, 40 mm long, and the point told is in it
    dx, dy = 10000.0, 5000.0

    def far(ring):
        return [(x + dx, y + dy) for x, y in ring]

    a = sh.Sheet(far(_rect(20, 20, 100, 80)), 1.0)
    b = sh.Sheet(far(_rect(100 - 2e-6, 30, 180, 70)), 1.5)
    notes = sh.settle(far(MAIN), 2.0, [a, b])
    assert (b.lo, b.rests) == (1.0, [1])
    assert (notes[0].a, notes[0].b, notes[0].other) == (4.5, 4.5, (0, 1, 2))
    x, y = notes[0].at
    assert 100 - 2.1e-6 < x - dx < 100.0 + 1e-7 and y - dy == 50.0
    # Two pieces that overlap by a hair which the main piece's edge runs
    # down the middle of: 0.8 thousandths of a micron of it on the main
    # piece, which is none. The thickest place is the two of them, 4 mm,
    # and the main piece, whose edge the point told is on, is not in it:
    # there is no more leather in a place than the place is high
    e = 0.8e-6
    a = sh.Sheet(_rect(120, 20, 200 + e, 80), 1.0)
    b = sh.Sheet(_rect(200 - e, 20, 260, 80), 3.0)
    notes = sh.settle(MAIN, 2.0, [a, b])
    assert (b.lo, b.rests) == (1.0, [1])
    assert (notes[0].a, notes[0].b, notes[0].other) == (4.0, 4.0, (1, 2))
    assert notes[0].at == (200.0, 50.0)
    assert sh._to_ring(notes[0].at, MAIN) == 0.0


def test_an_edge_of_no_length_gets_no_wall(monkeypatch):
    # should a part of a sheet come with a corner given twice, its cell
    # has a wall for each edge there is, and none -- it would have no way
    # to face -- for the one that is not
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    s = sh.Sheet(_rect(10, 10, 60, 50), 1.0)
    real = pc.convex_parts
    twice = [(10.0, 10.0), (60.0, 10.0), (60.0, 10.0), (60.0, 50.0),
             (10.0, 50.0)]

    def parts(ring, flags):
        if list(ring) == s.ring:
            return [(twice, [pc.OUTLINE] * 5)]
        return real(ring, flags)

    monkeypatch.setattr(pc, "convex_parts", parts)
    cell = sh.FlatStack(lay, [s], MAIN).solid([])[-1]
    assert cell.tag == ("panel", 0, 1) and len(cell.faces[0].pts) == 5
    assert len(cell.faces) == 6
    assert all(_near(math.hypot(*f.plane[:3]), 1.0, 1e-12)
               for f in cell.faces)
    assert clash.convex(cell) < 1e-9


def test_a_rough_outline_is_laid_in_as_few_corners_as_a_lone_pieces():
    # An oval drawn in 1500 corners and read from a file that keeps two
    # decimals: every corner is a hundredth of a mm out, one way or the
    # other, so that the outline turns in and out at every other corner.
    # Thinned inwards alone it could lose hardly any
    exact = [(120 + 70 * math.cos(2 * math.pi * k / 1500),
              80 + 45 * math.sin(2 * math.pi * k / 1500))
             for k in range(1500)]
    rough = [(round(x, 2), round(y, 2)) for x, y in exact]
    assert len(sh._inward(pc.ccw(rough), 0.03)) > 1000
    plain = fs.model_outline(rough)
    s = sh.Sheet(rough, 1.0)
    assert len(plain) == 128 and len(s.ring) == 128 and len(s.parts) == 1
    # it is thinned both ways and then drawn in: nowhere outside the
    # outline given, and within 0.035 mm of it
    given = pc.ccw(rough)
    assert _within(s.ring, given)
    assert 0.03 < s.within < 0.035
    assert max(sh._to_ring(q, s.ring) for q in given) <= s.within + 1e-9
    assert pc.area(given) - 6.0 < pc.area(s.ring) < pc.area(given)
    # the same oval drawn exactly is thinned as it always was
    smooth = sh.Sheet(exact, 1.0)
    assert smooth.within == 0.03 and len(smooth.ring) == len(
        fs.model_outline(exact)) == 128
    # ... and two rough pieces cut along one line still share nothing: a
    # plate cut in two along a wavy line of 801 corners, each kept to
    # two decimals. Both halves are laid in 60 corners, on one level
    cut = [(round(100 + 8 * math.sin(0.012 * t), 2), round(10 + 0.1 * t, 2))
           for t in range(801)]
    left = [(20.0, 10.0)] + cut + [(20.0, 90.0)]
    right = [(180.0, 90.0)] + cut[::-1] + [(180.0, 10.0)]
    assert len(sh._inward(pc.ccw(left), 0.5)) > 500
    (a, b), notes = _laid((left, 1.0), (right, 0.8))
    assert len(a.ring) <= 61 and len(b.ring) <= 61
    assert _within(a.ring, pc.ccw(left)) and _within(b.ring, pc.ccw(right))
    assert (a.lo, b.lo) == (0.0, 0.0) and [n.kind for n in notes] == ["thick"]
    assert sh._reach(a.parts, b.parts) <= sh.LAP
    assert 0.03 < a.within < 0.06 and 0.03 < b.within < 0.06
    # while one pushed 0.2 mm on to the other does climb
    (a, b), _notes = _laid((left, 1.0), ([(x - 0.2, y) for x, y in right],
                                         0.8))
    assert (b.lo, b.rests) == (1.0, [1])
    # How closely an outline is followed is kept with the sheet: not at
    # all loosely for one with no corner dropped, to 0.03 mm for a curve
    assert sh.Sheet(_rect(0, 0, 50, 30), 1.0).within == 0.0
    arc = [(50 + 12 * math.cos(math.pi * i / 14),
            60 - 12 * math.sin(math.pi * i / 14)) for i in range(15)]
    assert sh.Sheet([(0, 0), (100, 0), (100, 60)] + arc + [(0, 60)],
                    1.0).within == 0.0         # (14 chords: none dropped)
    disc = [(60 + 5 * math.cos(2 * math.pi * k / 256),
             50 + 5 * math.sin(2 * math.pi * k / 256)) for k in range(256)]
    round_ = sh.Sheet(disc, 1.0)
    assert round_.within == 0.03 and len(round_.ring) == 32
    # An overlap smaller than that, along a curve, may not be seen: the
    # round is laid in 32 chords, each 0.024 mm inside the circle at its
    # middle. A straight piece 0.02 mm on to the circle there shares
    # nothing with the round as it is laid, and is left on its level;
    # 0.05 mm on, it climbs
    (ax, ay), (bx, by) = round_.ring[:2]
    mid = (0.5 * (ax + bx) - 60, 0.5 * (ay + by) - 50)
    assert _near(5 - math.hypot(*mid), 5 * (1 - math.cos(math.pi / 32)))
    assert 0.02 < 5 - math.hypot(*mid) < 0.03
    turn = math.degrees(math.atan2(mid[1], mid[0]))
    for over, lo in ((0.02, 0.0), (0.05, 1.0)):
        (_r, flat), _notes = _laid(
            (disc, 1.0), (_lie(_rect(5 - over, -20, 30, 20), turn, 60, 50),
                          0.5))
        assert flat.lo == lo, over


def test_a_coarse_outline_is_drawn_in_where_it_would_have_added_leather():
    # A zigzag edge thinned to its points has the leather of every notch
    # between two of them. Here the corners that stay are every other
    # one: the lines between them are moved in until the notches are
    # outside them again
    saw = [(0.0, 0.0), (60.0, 0.0), (60.0, 20.0)] + [
        (60.0 - 3 * k, 20.0 - (0.3 if k % 2 else 0.0)) for k in range(1, 20)
    ] + [(0.0, 20.0)]
    levels = sh._levels(saw)
    assert [q for q, lv in zip(saw, levels) if lv > 0.4] == pc.simplify(
        saw, 0.4) == [(0.0, 0.0), (60.0, 0.0), (60.0, 20.0), (0.0, 20.0)]
    assert pc.simplify(saw, 0.2) == [
        q for q, lv in zip(saw, levels) if lv > 0.2]
    ring, off = sh._coarse(saw, 0.4, levels)
    # the top edge has come down to the bottoms of the notches; the
    # others are where they were, and their corners were corners given
    assert ring == [(0.0, 0.0), (60.0, 0.0), (60.0, 19.7), (0.0, 19.7)]
    assert _near(off, 0.3) and _within(ring, saw)
    # an outline with nothing to its left of those lines is left alone
    ring, off = sh._coarse(saw, 0.0, levels)
    assert ring == saw and off == 0.0
    # A line that would have to turn back on itself to be moved in has
    # no place to meet the next: a spike 6 mm high and 0.2 mm across at
    # its foot, one side of it a hair hollow
    spike = [(0.0, 0.0), (60.0, 0.0), (60.0, 20.0), (40.1, 20.0),
             (40.0, 23.0), (40.0, 26.0), (39.9, 20.0), (0.0, 20.0)]
    assert sh._coarse(spike, 0.25, sh._levels(spike)) is None
    assert sh._simple(saw) and not sh._simple(
        [(0, 0), (10, 10), (10, 0), (0, 10)])
    # Round a hollow corner the outline is the leather to the left of
    # either line, not of both: there a line is moved in past the corners
    # its neighbour stands for as well. A plate with a barb in its top
    # edge, a quarter of a mm back on itself: without that the thinned
    # outline would have 0.025 mm of leather that the plate has not
    barb = [(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (36.0, 19.4),
            (30.0, 20.1), (30.06, 20.0), (30.09, 19.7), (30.24, 20.0),
            (0.0, 20.0)]
    ring, off = sh._coarse(barb, 0.35, sh._levels(barb))
    assert len(ring) == 7 and 0.3 < off < 0.31
    assert _outside(ring, barb) < 1e-9
    assert all(_near(got, want, 1e-3) for q, r in zip(ring[4:6], [
        (30.2234, 20.0739), (30.308, 19.6978)]) for got, want in zip(q, r))
    # Two lines that are moved in until they have passed each other make
    # an outline that crosses itself: none is made. A plate with a tab
    # 0.6 mm wide, its two sides waving by half of that
    tab = pc.ccw(
        [(0, 0), (100, 0), (100, 50), (50.3, 50)]
        + [(50.3 + 0.3 * math.sin(1.7 * k), 50 + 0.75 * k)
           for k in range(1, 40)] + [(50.3, 80), (49.7, 80)]
        + [(49.7 + 0.3 * math.sin(1.3 * k), 50 + 0.75 * k)
           for k in range(39, 0, -1)] + [(49.7, 50), (0, 50)])
    assert len(tab) == 86 and sh._simple(tab)
    levels = sh._levels(tab)
    assert sh._coarse(tab, 0.3, levels) is None
    assert sh._coarse(tab, 0.5, levels) is None
    assert len(sh._coarse(tab, 0.1, levels)[0]) == 65
    # ... and as a sheet it is thinned inwards, as any is, and is whole
    thin = sh.Sheet(tab, 1.0)
    assert thin.within == 0.03 and sh._simple(thin.ring)
    assert len(thin.ring) == 85 and _outside(thin.ring, tab) < 1e-9


def test_a_speck_of_a_notch_in_an_edge_is_no_notch():
    # a notch a hundred-thousandth of a mm deep in the bottom edge of a
    # piece: dirt in a drawing. Kept, it would be taken for a straight
    # corner and its two walls for edges of one convex part
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    for d in (1.5e-6, 1e-5, 2e-5):
        notched = [(10, 10), (60 - d, 10), (60, 10 + d), (60 + d, 10),
                   (110, 10), (110, 70), (10, 70)]
        assert not pc.is_convex(notched[::1]) or d < 2.3e-5
        low = sh.Sheet(notched, 1.0, holes=[(30, 30, 1.0), (60, 40, 1.0),
                                            (90, 30, 1.0)])
        assert low.ring == [(10.0, 10.0), (110.0, 10.0), (110.0, 70.0),
                            (10.0, 70.0)] and len(low.parts) == 1
        top = sh.Sheet(_rect(70, 20, 90, 36), 0.8)
        stack = sh.FlatStack(lay, [low, top], MAIN)
        # a piece wholly on it lies on it
        assert (top.lo, top.rests) == (1.0, [1]), d
        assert _near(stack.notes[0].a, 3.8) and stack.notes[0].other == (
            0, 1, 2)
        cells = stack.solid([])
        assert clash.clashes(cells) == []
        # ... and every one of its holes is on its faces
        dots = [p for f in cells[1].faces if f.plane[2] > 0.5
                for kind, geo, _style in f.decals or ()
                if kind == bsp.DOTS for p in geo]
        assert sorted(p[:2] for p in dots) == [(30, 30), (60, 40), (90, 30)]
    # a notch that is one, however small, stays
    real = sh.Sheet([(10, 10), (59.9, 10), (60, 10.1), (60.1, 10), (110, 10),
                     (110, 70), (10, 70)], 1.0)
    assert len(real.ring) == 7 and len(real.parts) == 2
    # so does a corner that is all but straight: there is nothing to it
    flat = [(0.0, 0.0), (50.0, 0.0), (100.0, 1e-12), (100.0, 50.0),
            (0.0, 50.0)]
    assert sh._specks(flat) == flat


def test_piled_busy_outlines_settle_without_clipping_everything(monkeypatch):
    calls, tried = [], []
    clip, overlap = pc.clip_to_convex, pc.convex_overlap
    monkeypatch.setattr(pc, "clip_to_convex",
                        lambda *a: calls.append(1) or clip(*a))
    monkeypatch.setattr(pc, "convex_overlap",
                        lambda *a: tried.append(1) or overlap(*a))
    sheets = [sh.Sheet(_scalloped(120 + 8 * i, 100 + 5 * i, 60 - 6 * i), 1.0)
              for i in range(3)]
    notes = sh.settle([(0, 0), (300, 0), (300, 200), (0, 200)], 2.0, sheets)
    assert [s.lo for s in sheets] == [0.0, 1.0, 2.0]
    assert [s.clear for s in sheets] == [[], [(0, 1.0)], [(0, 2.0), (1, 1.0)]]
    assert [(n.kind, n.sheet) for n in notes] == [("thick", 0), ("clear", 2),
                                                  ("clear", 3)]
    assert _near(notes[0].a, 5.0) and notes[0].other == (0, 1, 2, 3)
    # (125 751 clips before each bit was tried against the box round each
    # part first, and the thickest place followed by its biggest bits;
    # 40 688 parts tried against each other before what a sheet stands
    # clear of was looked for part by part, until found)
    assert len(calls) < 20000
    assert len(tried) < 10000


def test_a_piece_moved_after_it_was_made_is_laid_where_it_now_is():
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    a = sh.Sheet(_rect(10, 10, 90, 90), 1.0)
    b = sh.Sheet(_rect(120, 10, 190, 90), 0.5)
    sh.settle(MAIN, 2.0, [a, b])
    assert (b.lo, b.rests) == (0.0, [0])
    assert b.frame == ((120.0, 10.0), (1.0, 0.0), (0.0, 1.0))
    b.ring = _rect(50, 10, 120, 90)            # dragged half on to the first
    notes = sh.settle(MAIN, 2.0, [a, b])
    assert (b.lo, b.hi, b.rests, b.rank) == (1.0, 1.5, [1], 2)
    assert b.parts == [b.ring] == [[(50.0, 10.0), (120.0, 10.0),
                                    (120.0, 90.0), (50.0, 90.0)]]
    assert b.frame == ((50.0, 10.0), (1.0, 0.0), (0.0, 1.0))
    assert sh.Note("clear", 2, -1, 1.0, other=(0,)) in notes
    # the model made of them has it on top of the first, through nothing
    stack = sh.FlatStack(lay, [a, b], MAIN)
    cells = stack.solid([])
    assert clash.clashes(cells) == []
    assert sorted({v[2] for v in cells[2].verts}) == [2.0, 2.5]
    assert sorted({v[0] for v in cells[2].verts}) == [50.0, 120.0]
    # the same for a sheet made anew from it, and for an outline that is
    # changed where it is
    c = dataclasses.replace(b, ring=_rect(120, 10, 190, 90))
    sh.settle(MAIN, 2.0, [a, c])
    assert (c.lo, c.rests) == (0.0, [0])
    c.ring[:] = [(x - 70, y) for x, y in c.ring]
    sh.settle(MAIN, 2.0, [a, c])
    assert (c.lo, c.rests) == (1.0, [1])
    # ... and its thickness is the one it has now
    b.thickness = 0.8
    sh.settle(MAIN, 2.0, [a, b])
    assert (b.lo, b.hi) == (1.0, 1.8)


def test_standing_past_the_edge_is_told_wherever_a_piece_is_furthest_out():
    # A thumb notch of 15 mm in the main piece's top edge, drawn in 40
    # chords, and a pocket flush with that edge across it. Its corners
    # are all on the main piece; the middle of its top edge is over the
    # notch, as far from the leather as the notch's chords are from its
    # centre
    arc = [(100 + 15 * math.cos(math.pi * i / 40),
            100 - 15 * math.sin(math.pi * i / 40)) for i in range(41)]
    notch = [(0, 0), (200, 0), (200, 100)] + arc + [(0, 100)]
    (pocket,), notes = _laid((_rect(40, 40, 160, 100), 1.0), main=notch)
    assert abs(pocket.past - 15.0 * math.cos(math.pi / 80)) < 0.01
    assert math.dist(pocket.past_at, (100.0, 100.0)) < 0.5
    assert notes[1] == sh.Note("past", 1, -1, pocket.past, at=pocket.past_at)
    # 4 mm higher its corners stand 4 mm past the edge; the middle of its
    # top edge is further out: from the two ends of the notch
    (pocket,), _notes = _laid((_rect(40, 40, 160, 104), 1.0), main=notch)
    assert abs(pocket.past - math.hypot(15.0, 4.0)) < 0.01
    assert math.dist(pocket.past_at, (100.0, 104.0)) < 0.05
    # a strap across the empty corner of an L-shaped piece, every corner
    # of it on the leather: it stands 5 mm out, where its far edge is as
    # far from the one arm as from the other
    ell = [(0, 0), (200, 0), (200, 50), (100, 50), (100, 100), (0, 100)]
    (strap,), notes = _laid(([(60, 70), (130, 20), (140, 30), (70, 80)],
                             1.0), main=ell)
    assert all(pc.inside(q, ell) for q in strap.ring)
    assert abs(strap.past - 5.0) < 0.01
    assert math.dist(strap.past_at, (105.0, 55.0)) < 0.02
    assert [n.kind for n in notes] == ["thick", "past"]
    # where the main piece's edge is straight, or bulges, it is a corner
    # that is furthest out, and that to the last place
    (tab,), _notes = _laid((_rect(150, 30, 215.25, 70), 1.0))
    assert tab.past == 15.25 and tab.past_at in ((215.25, 30.0),
                                                 (215.25, 70.0))
    # 0.3 mm is told, a hair is not
    (edge,), notes = _laid((_rect(-0.3, 10, 50, 90), 1.0))
    assert _near(edge.past, 0.3) and [n.kind for n in notes] == ["thick",
                                                                 "past"]


def test_standing_past_a_notched_main_piece_is_not_measured_part_by_part(
        monkeypatch):
    # A shell with a 15 mm thumb notch in its top edge, kept in 40 convex
    # parts, and the bifold's nine pieces on it: the lining and one
    # backing lie across the notch
    arc = [(100 + 15 * math.cos(math.pi * i / 40),
            90 - 15 * math.sin(math.pi * i / 40)) for i in range(41)]
    shell = [(0, 0), (200, 0), (200, 90)] + arc + [(0, 90)]
    assert len(sh._convex(fs.model_outline(shell))) == 40

    def pile():
        out = [sh.Sheet(_rect(4, 0, 196, 88), 0.8)]
        for x0, x1 in ((0, 96), (104, 200)):
            out += [sh.Sheet(_rect(x0, 0, x1, top), 0.8)
                    for top in (90, 54, 44, 34)]
        return out

    tried = []
    inside = pc.inside_convex
    monkeypatch.setattr(pc, "inside_convex",
                        lambda *a: tried.append(1) or inside(*a))
    sheets = pile()
    sh.settle(shell, 1.6, sheets)
    # the lining stands 13 mm out over the notch at the middle of its top
    # edge, the left backing 11 mm at its corner; the rest stand nowhere
    assert [round(s.past, 2) for s in sheets] == [12.99, 10.99, 0.0, 0.0,
                                                  0.0, 10.99, 0.0, 0.0, 0.0]
    assert math.dist(sheets[0].past_at, (100.0, 88.0)) < 0.5
    # (18 690 times a corner was tried against a part of the shell when
    # every bit was measured from every part: now each is measured from
    # the shell's outline first, and from its parts only where that
    # leaves the answer open)
    assert len(tried) < 8000
    # How far a sheet stands past the main piece goes by the two outlines
    # alone. Laid again with nothing changed, none is measured again; one
    # sheet moved, that one is; another main piece, all are
    calls = []
    past = sh._past
    monkeypatch.setattr(sh, "_past", lambda *a: calls.append(1) or past(*a))
    sh.settle(shell, 1.6, sheets)
    assert calls == [] and round(sheets[0].past, 2) == 12.99
    sheets[5].ring = _rect(116, 0, 200, 90)
    sh.settle(shell, 1.6, sheets)
    assert calls == [1] and sheets[5].past == 0.0
    sheets[5].thickness = 1.0                  # (nothing to do with it)
    sh.settle(shell, 1.6, sheets)
    assert calls == [1]
    sh.settle(_rect(0, 0, 200, 80), 1.6, sheets)
    assert len(calls) == 10 and [s.past for s in sheets] == [
        8.0, 10.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0]


def test_a_rest_on_under_a_millimetre_is_told_as_a_sliver_and_no_other():
    assert sh.SLIVER == 1.0
    for over, told in ((0.6, True), (0.99, True), (1.2, False)):
        (a, b), notes = _laid((_rect(10, 10, 100, 90), 1.0),
                              (_rect(100 - over, 10, 190, 90), 1.0))
        assert _near(b.lap, over) and b.rests == [1]
        slivers = [n for n in notes if n.kind == "sliver"]
        assert slivers == ([sh.Note("sliver", 2, -1, b.lap, other=(1,))]
                           if told else []), over
    # how far a piece is on another is the most that any part of the one
    # reaches into any part of the other, whichever part is tried first:
    # a T whose bar is 20 mm in from the edge of the piece under it and
    # whose stem, narrow as it is, would have to slide 28 mm to come off
    big, bar, stem = (_rect(0, 0, 100, 100), _rect(60, 80, 90, 90),
                      _rect(72, 30, 78, 80))
    assert _near(sh._reach([bar], [big]), 20.0)
    assert _near(sh._reach([stem], [big]), 28.0)
    for parts in ([bar, stem], [stem, bar]):
        assert _near(sh._reach(parts, [big]), 28.0)
        assert _near(sh._reach([big], parts), 28.0)
    tee = [(72, 30), (78, 30), (78, 80), (90, 80), (90, 90), (60, 90),
           (60, 80), (72, 80)]
    (_low, top), notes = _laid((big, 1.0), (tee, 1.0))
    assert _near(top.lap, 28.0) and [n.kind for n in notes] == ["thick"]
    # a piece that is on the main piece by as little is not told so: it
    # is told as standing past its edge
    (s,), notes = _laid((_rect(199.4, 10, 260, 90), 1.0))
    assert s.rests == [0] and _near(s.lap, 0.6)
    assert [n.kind for n in notes] == ["thick", "past"]
    # An L-shaped piece is kept in two parts, along the line from its
    # inside corner to its outside one. A narrow strap lying along that
    # line, wholly on the L, is not half on each part: it is no sliver
    ell = [(20, 20), (80, 20), (80, 40), (40, 40), (40, 70), (20, 70)]
    low = sh.Sheet(ell, 1.0)
    assert len(low.parts) == 2
    assert all((40.0, 40.0) in part and (20.0, 20.0) in part
               for part in low.parts)
    c = math.sqrt(0.5)
    for wide in (0.8, 1.6):
        strap = [(30 + x * c - y * c, 30 + x * c + y * c)
                 for x, y in _rect(-6, -wide / 2, 6, wide / 2)]
        (low, top), notes = _laid((ell, 1.0), (strap, 0.5))
        # as far as either part goes it is on by half its width ...
        assert _near(sh._reach(top.parts, low.parts), wide / 2, 1e-9)
        # ... but it would have to slide a long way to come off the L
        assert top.rests == [1] and top.lap > 5.0
        assert [n.kind for n in notes] == ["thick"], wide
    # What really is on the L by a sliver is told, by the least slide
    # that takes it off: over its outer edge, and into its inside corner
    (low, top), notes = _laid((ell, 1.0), (_rect(5, 30, 20.5, 60), 0.5))
    assert _near(top.lap, 0.5)
    assert notes[-1] == sh.Note("sliver", 2, -1, top.lap, other=(1,))
    (low, top), notes = _laid((ell, 1.0), (_rect(39.5, 39.5, 60, 60), 0.5))
    assert _near(top.lap, 0.5 * math.sqrt(2.0)) and notes[-1].kind == "sliver"
    # a place under a bridge is told when it is 0.3 mm wide, not a hair
    for gap, clear in ((0.3, [(0, 2.0), (2, 1.5)]), (0.04, [(2, 1.5)])):
        (_a, _b, c), _notes = _laid((_rect(20, 20, 100, 80), 2.0),
                                    (_rect(100 + gap, 20, 180, 80), 0.5),
                                    (_rect(60, 40, 140, 60), 1.0))
        assert c.clear == clear, gap


def test_how_far_a_piece_is_on_another_is_not_told_as_more_than_it_is():
    # A plate with a slot cut in from its top edge, and a tongue as wide
    # as the slot lying in it, 0.1 mm on to the plate at the slot's end:
    # slid 0.1 mm back out it is off. Its long sides lie along the slot's
    # sides -- touching them, on to nothing -- and what a part of the one
    # and a part of the other have in common there is a line with no
    # width, of which no point but its ends is worked out safely
    plate = [(0, 0), (100, 0), (100, 70), (53, 70), (53, 35), (47, 35),
             (47, 70), (0, 70)]
    tongue = _rect(47, 34.9, 53, 75)
    bench = _rect(-900, -900, 900, 900)
    for turn, dx, dy in ((0, 0, 0), (5, 300, 0), (5, -300, -150),
                         (15, -300, 150), (45, -150, -150), (35, 150, 150)):
        (low, top), notes = _laid((_lie(plate, turn, dx, dy), 1.0),
                                  (_lie(tongue, turn, dx, dy), 0.8),
                                  main=bench)
        assert len(low.parts) > 1 and top.rests == [1]
        assert _near(top.lap, 0.1, 1e-9), (turn, dx, dy, top.lap)
        assert notes[-1] == sh.Note("sliver", 2, -1, top.lap, other=(1,))


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
    assert {k: len(v) for k, v in check.through.items()} == {
        1: 142, 2: 44, 3: 37, 4: 35, 5: 32, 6: 44, 7: 37, 8: 35, 9: 32}
    assert sum(len(v) for v in check.through.values()) == 438
    assert check.passed is check.through       # (its older name)
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
    assert len(check.through[1]) == 46
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
    assert not check.off and not check.through and not check.blanks


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
    assert check.through == {1: [(50.0, 50, 1.2)]}
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
    assert len(check.through[1]) == 3
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


def test_each_hole_of_a_stack_has_its_own_number_whatever_cells_it_is_on():
    # The main piece has one hole. The first sheet is an L, in two cells,
    # with three holes; the second lies on it with two: one on a hole of
    # the L, one over leather with no hole
    main = fs.fold_piece(MAIN, [], holes=[(190.0, 90.0, 1.0)])
    ell = [(20, 20), (120, 20), (120, 40), (60, 40), (60, 80), (20, 80)]
    low = sh.Sheet(ell, 1.0, holes=[(100.0, 30.0, 1.0), (40.0, 60.0, 1.0),
                                    (30.0, 30.0, 1.0)])
    top = sh.Sheet(_rect(25, 25, 110, 35), 0.5,
                   holes=[(30.0, 30.0, 1.0), (70.0, 30.0, 1.0)])
    stack = sh.FlatStack(main, [low, top], MAIN)
    assert len(stack.solid([])) == 4               # 1 + 2 + 1 cells
    assert stack.hole_sheet == [0, 1, 1, 1, 2, 2]
    assert stack.holes[3:5] == [(30.0, 30.0, 1.0)] * 2
    check = stack.lineup()
    # holes 3 and 4 meet; hole 5 comes through the L, hole 1 of the L
    # through the sheet on it; and all of them through the main piece,
    # at four spots
    assert check.state == {3: fs.MEETS, 4: fs.MEETS}
    assert check.through[1] == [(70.0, 30.0, 1.0)]
    assert check.through[2] == [(100.0, 30.0, 1.0)]
    assert sorted(check.through[0]) == [
        (30.0, 30.0, 1.0), (40.0, 60.0, 1.0), (70.0, 30.0, 1.0),
        (100.0, 30.0, 1.0)]
    found = {}
    for cell in stack.solid([], check):
        for face in cell.faces:
            if face.plane[2] > 0.5:
                for kind, geo, (_dia, state) in face.decals or ():
                    if kind == bsp.DOTS:
                        for q in geo:
                            found.setdefault(sh.sheet_of(cell.tag), []) \
                                .append((q[0], q[1], state))
    # on the model each is the colour of its own number: the second
    # sheet's first hole green, its second plain
    assert sorted(found[2]) == [(30.0, 30.0, fs.MEETS), (70.0, 30.0, 0),
                                (100.0, 30.0, sh.BLANK)]
    assert sorted(found[1]) == [(30.0, 30.0, fs.MEETS), (40.0, 60.0, 0),
                                (70.0, 30.0, sh.BLANK), (100.0, 30.0, 0)]
    assert sorted(found[0]) == [
        (30.0, 30.0, sh.BLANK), (40.0, 60.0, sh.BLANK),
        (70.0, 30.0, sh.BLANK), (100.0, 30.0, sh.BLANK), (190.0, 90.0, 0)]
    # The mark for a hole that comes through is this module's own: no
    # mark the fold engine has for a hole is the same number
    theirs = {name: v for name, v in vars(fs).items()
              if name.isupper() and not name.startswith("_")
              and type(v) is int and not hasattr(bsp, name)
              and not hasattr(pc, name)}
    assert {"MEETS", "OFF", "LONE", "LANDS"} <= set(theirs)
    assert sh.BLANK not in theirs.values()


def test_a_hole_that_is_missed_is_ringed_on_the_other_pieces_not_its_own():
    # Three layers. The main piece's hole and the second sheet's are 0.7
    # mm apart: they meet. The first sheet's is 0.7 mm further on: it
    # meets the second sheet's, and is 1.4 mm out of line with the main
    # piece's. One stack of three holes, out of line
    main = fs.fold_piece(MAIN, [], holes=[(50.0, 50.0, 1.0)])
    one = sh.Sheet(_rect(20, 20, 120, 80), 1.0, holes=[(51.4, 50.0, 1.2)])
    two = sh.Sheet(_rect(30, 30, 110, 70), 1.0, holes=[(50.7, 50.0, 0.8)])
    check = sh.FlatStack(main, [one, two], MAIN).lineup()
    assert check.state == {0: fs.OFF, 1: fs.OFF, 2: fs.OFF}
    assert {(f.a, f.b): (f.met, f.off) for f in check.findings} == {
        (0, 1): (0, 2), (0, 2): (2, 0), (1, 2): (2, 0)}
    # Each piece is ringed where the hole that was missed comes down on
    # it: the first sheet where the main piece's is, the main piece where
    # the first sheet's is, the second sheet at both -- and no piece at
    # its own hole
    assert check.landed == {0: [(51.4, 50.0, 1.2)], 1: [(50.0, 50.0, 1.0)],
                            2: [(50.0, 50.0, 1.0), (51.4, 50.0, 1.2)]} \
        or check.landed == {0: [(51.4, 50.0, 1.2)], 1: [(50.0, 50.0, 1.0)],
                            2: [(51.4, 50.0, 1.2), (50.0, 50.0, 1.0)]}
    assert check.lands[0] == [(51.4, 50.0, 1.2)]
    assert check.lands[1] == [(50.0, 50.0, 1.0)]
    assert sorted(check.lands[2]) == [(50.0, 50.0, 1.0), (51.4, 50.0, 1.2)]
    assert not check.through


def test_a_hole_within_4_mm_of_another_is_out_of_line_and_past_that_not():
    main = fs.fold_piece(MAIN, [], holes=[(50.0, 50.0, 1.0)])
    for off, state in ((0.7, fs.MEETS), (0.8, fs.OFF), (3.0, fs.OFF),
                       (3.9, fs.OFF), (4.1, None)):
        sheet = sh.Sheet(_rect(20, 20, 120, 80), 1.0,
                         holes=[(50.0 + off, 50.0, 1.0)])
        check = sh.FlatStack(main, [sheet], MAIN).lineup()
        assert check.state.get(0) == check.state.get(1) == state, off
        f, = check.findings
        assert (f.holes, f.off, f.lone) == (
            (2, 2, 0) if state == fs.OFF else (2, 0, 0) if state
            else (2, 0, 2)), off
        # each comes through the other piece only when it is too far from
        # that piece's hole to have been meant for it
        assert check.through == ({} if state else {
            0: [(54.1, 50.0, 1.0)], 1: [(50.0, 50.0, 1.0)]})


def test_a_hole_on_the_very_edge_of_a_piece_is_not_in_it():
    # two pockets that butt at 100 mm across; the main piece has a row of
    # holes down that line and one down the middle of each pocket
    rows = [(x, 20.0 + 5 * k, 1.0) for x in (60.0, 100.0, 140.0)
            for k in range(11)]
    main = fs.fold_piece(MAIN, [], holes=rows)

    def through(*pieces):
        stack = sh.FlatStack(main, [sh.Sheet(ring, 1.0, cutouts=cut)
                                    for ring, cut in pieces], MAIN)
        return [f.holes for f in stack.lineup().findings]

    left, right = _rect(20, 10, 100, 90), _rect(100, 10, 180, 90)
    # the row on the line is in neither: each has the row down its middle
    assert through((left, ()), (right, ())) == [11, 11]
    # ... whichever of the two it is, and whichever way round it is drawn

    def mirror(ring):
        return [(200 - x, y) for x, y in ring]

    assert through((mirror(left), ()), (mirror(right), ())) == [11, 11]
    assert through((right[::-1], ()), (left[::-1], ())) == [11, 11]
    # within a hair of the edge is on it; further in is in the leather
    assert through((_rect(20, 10, 100.04, 90), ())) == [11]
    assert through((_rect(20, 10, 100.06, 90), ())) == [22]
    # The edge of a window is an edge like any other. This one has nine
    # holes of the pocket's own row inside it and one on each end of it
    assert through((left, [_rect(55, 20, 65, 70)])) == []
    assert through((left, [_rect(55, 20.06, 65, 69.94)])) == [2]
    assert through((left, [_rect(55, 19.94, 65, 70.06)])) == []


def test_a_spot_is_marked_once_and_a_window_in_the_main_piece_is_no_leather():
    # the main piece's hole and the first sheet's are one on the other;
    # both come through the second sheet, which has no hole: one spot
    main = fs.fold_piece(MAIN, [], holes=[(50.0, 50.0, 1.0)])
    one = sh.Sheet(_rect(20, 20, 120, 80), 1.0, holes=[(50.0, 50.0, 1.0)])
    two = sh.Sheet(_rect(30, 30, 110, 70), 1.0)
    check = sh.FlatStack(main, [one, two], MAIN).lineup()
    assert check.holes(fs.MEETS) == {0, 1}
    assert check.through == {2: [(50.0, 50.0, 1.0)]}
    assert sum(len(v) for v in check.blanks.values()) == 1
    assert sorted((f.a, f.b, f.through) for f in check.findings) == [
        (0, 1, (0, 0)), (0, 2, (1, 0)), (1, 2, (1, 0))]
    # A window cut in the main piece: a sheet's hole over it goes through
    # no leather there, and is not looked at. Its other hole comes
    # through the main piece, which is ringed there on both its faces
    main = fs.fold_piece(MAIN, [], cutouts=[_rect(40, 40, 60, 60)])
    sheet = sh.Sheet(_rect(20, 20, 120, 80), 1.0,
                     holes=[(50.0, 50.0, 1.0), (100.0, 50.0, 1.0)])
    stack = sh.FlatStack(main, [sheet], MAIN)
    check = stack.lineup()
    f, = check.findings
    assert (f.a, f.b, f.holes, f.lone, f.through) == (0, 1, 1, 1, (0, 1))
    assert check.through == {0: [(100.0, 50.0, 1.0)]} and not check.state
    assert check.blanks == {0: [(100.0, 50.0, 1.0)]}
    cells = stack.solid([], check)
    rings = sorted((geo, style) for face in cells[0].faces
                   for kind, geo, style in face.decals or ()
                   if kind == bsp.DOTS)
    assert rings == [([(100.0, 50.0, -1.0)], (1.0, sh.BLANK)),
                     ([(100.0, 50.0, 1.0)], (1.0, sh.BLANK))]
    # ... and the sheet's own holes are plain holes
    dots = sorted((geo, style) for face in cells[1].faces
                  for kind, geo, style in face.decals or ()
                  if kind == bsp.DOTS and face.plane[2] > 0.5)
    assert dots == [([(50.0, 50.0, 2.0), (100.0, 50.0, 2.0)], (1.0, 0))]
    # without the check nothing is ringed
    assert not any(style[1] for cell in stack.solid([])
                   for face in cell.faces
                   for kind, _geo, style in face.decals or ()
                   if kind == bsp.DOTS)


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
    # ... with the two ends of its fold line among the corners, where the
    # panels meet. A stack that is given its main piece's outline has
    # that one, made ready, and not this
    assert len(again) == 46 and len(stack.outline) == 44
    assert set(again) - set(stack.outline) == {(0.0, -45.0), (0.0, 45.0)}
    assert stack.outline == fs.model_outline(_ring(bifold_wallet()[0]))

    class Panel:
        def __init__(self, pts):
            self.pts, self.flags = pts, [pc.OUTLINE] * len(pts)
            self.area = abs(pc.area(pts))

    class InBits:
        """Panels whose edges make three ways round, not one."""
        panels = [Panel(_rect(0, 0, 10, 10)), Panel(_rect(20, 0, 60, 30)),
                  Panel(_rect(70, 0, 75, 5))]

    # of more ways round than one, the outline is the longest: the one
    # with most leather in it
    assert sh.outline_of(InBits) == _rect(20, 0, 60, 30)


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
        # its leather is laid from its own first corner, along its edge
        assert top.uv == ((20.0, 20.0, z1), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        assert bot.uv == ((20.0, 20.0, z0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def _grain_at(face, p):
    """Where on its leather a point of a face is: what the texture is
    laid by."""
    o, u, v = face.uv
    d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    return (d[0] * u[0] + d[1] * u[1] + d[2] * u[2],
            d[0] * v[0] + d[1] * v[1] + d[2] * v[2])


def test_a_sheet_moved_or_turned_takes_its_grain_with_it():
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    ell = [(20, 20), (120, 20), (120, 40), (60, 40), (60, 80), (20, 80)]
    spot = (100.0, 30.0)                   # a point of the leather

    def lie(turn, dx, dy):
        c, sn = math.cos(turn), math.sin(turn)

        def to(q):
            return (dx + q[0] * c - q[1] * sn, dy + q[0] * sn + q[1] * c)
        stack = sh.FlatStack(lay, [sh.Sheet([to(q) for q in ell], 1.0)],
                             MAIN)
        top = [f for cell in stack.solid([])[1:] for f in cell.faces
               if f.plane[2] > 0.5]
        assert len(top) == 2               # the L is in two parts
        x, y = to(spot)
        return [_grain_at(f, (x, y, 2.0)) for f in top]

    home = lie(0.0, 0.0, 0.0)
    # the same on both parts of it, and 80 mm along its first edge, 10 in
    assert all(_near(g[0], 80.0) and _near(g[1], 10.0) for g in home)
    # slid 13 mm along the bench, and turned a third of the way round and
    # carried off: that point of the leather has the same grain on it
    for turn, dx, dy in ((0.0, 13.0, 0.0), (0.0, -7.5, 21.0),
                         (2.1, 150.0, 60.0), (-0.4, 30.0, 40.0)):
        there = lie(turn, dx, dy)
        assert all(math.dist(g, (80.0, 10.0)) < 1e-9 for g in there), turn


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
    # panel 1's number is on pocket 3 of its stack, three layers up
    one, _two = stack.panel_marks([0.0], 6.0)
    assert one[3] == (0, 7) and stack.sheets[6].rank == 3
    assert _near(one[0][2], 0.8) and _near(one[1][2], -(0.8 + 2.4 + 18.0))


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
    assert one[2] == (0.0, 0.0, 1.0)
    # On the shell's own face, which nothing lies on, it is where the
    # layout puts it. On the other face that spot is 1 mm from the edge
    # of pocket 2: there the number is moved 3 mm up, on to pocket 3
    # alone, 4 mm from the nearest edge of any piece
    x, y = stack.panels[0].spot
    assert y == 0.0 and one[0][:2] == (x, y) and one[1][:2] == (x, 3.0)
    assert two[0][:2] == stack.panels[1].spot and two[1][1] == 3.0

    def room(q):
        return min(sh._to_ring(q, s.ring) for s in stack.sheets)

    assert _near(room((x, y)), 1.0)
    assert _near(room(one[1][:2]), 4.0) and _near(room(two[1][:2]), 4.0)
    # a panel too small for that keeps the clearest place there is
    lay = fs.fold_piece(MAIN, [fs.FoldLine((6, 0), (6, 100), 90, 1)])
    stack = sh.FlatStack(lay, [sh.Sheet(_rect(1, 10, 190, 90), 1.0)], MAIN)
    strip, body = stack.panel_marks([0.0])
    assert strip[0][:2] == lay.panels[0].spot == (3.0, 50.0)
    assert body[0][:2] == lay.panels[1].spot
    assert body[0][0] > 90 and body[3] == (1, 0)


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


def test_a_stack_is_a_picture_of_its_sheets_as_they_lay_when_it_was_made():
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    a = sh.Sheet(_rect(10, 10, 90, 90), 1.0)
    b = sh.Sheet(_rect(50, 10, 120, 90), 0.5)

    def spans(stack):
        return [sorted({v[2] for v in cell.verts})
                for cell in stack.solid([])[1:]]

    one = sh.FlatStack(lay, [a, b], MAIN)
    assert spans(one) == [[1.0, 2.0], [2.0, 2.5]]
    # the same two sheets laid the other way round, for another stack
    two = sh.FlatStack(lay, [b, a], MAIN)
    assert (b.lo, a.lo) == (0.0, 0.5)
    assert spans(two) == [[1.0, 1.5], [1.5, 2.5]]
    # ... leave the first as it was: its picture still agrees with its
    # notes
    assert spans(one) == [[1.0, 2.0], [2.0, 2.5]]
    assert _near(one.notes[0].a, 3.5) and one.notes[0].other == (0, 1, 2)
    assert one.bounds3([]) == ((0.0, 0.0, -1.0), (200.0, 100.0, 2.5))
    assert one.extent()[0] == (100.0, 50.0, 0.75)
    # a sheet moved afterwards does not move in it either
    b.ring = _rect(130, 10, 190, 90)
    sh.settle(MAIN, 2.0, [a, b])
    assert sorted({v[0] for v in one.solid([])[2].verts}) == [50.0, 120.0]
    assert spans(one) == [[1.0, 2.0], [2.0, 2.5]]
    # ... nor one that is turned over, put on the other face, made
    # thicker or given holes: all of that is the stack's own record too
    b.side, b.grain, b.thickness = -1, -1, 3.0
    b.holes.append((160.0, 50.0, 1.0))             # (to its own list)
    a.holes = [(60.0, 50.0, 1.0)]
    sh.settle(MAIN, 2.0, [a, b])
    assert (b.lo, b.hi) == (0.0, 3.0)
    assert spans(one) == [[1.0, 2.0], [2.0, 2.5]]
    top = [f for f in one.solid([])[2].faces if f.plane[2] > 0.5][0]
    assert top.kind == bsp.FRONT and {p[2] for p in top.pts} == {2.5}
    assert (one.thickness_of(2), one.grain_of(2)) == (0.5, 1)
    assert (one.thickness_of(0), one.grain_of(0)) == (2.0, 1)
    assert (one.thickness_of(1), one.thickness_of(7)) == (1.0, 2.0)
    assert one.fold_marks([]) == {} and one.holes == []
    assert one.lineup().findings == []         # it had no holes then
    assert _near(one.notes[0].a, 3.5) and one.notes[0].other == (0, 1, 2)
    # a stack made of them now has them as they now are
    now = sh.FlatStack(lay, [a, b], MAIN)
    assert spans(now) == [[1.0, 2.0], [-4.0, -1.0]]
    assert (now.thickness_of(2), now.grain_of(2)) == (3.0, -1)
    assert [(f.a, f.b, f.through) for f in now.lineup().findings] == [
        (0, 1, (0, 1)), (0, 2, (0, 1))]


def test_a_stack_names_its_main_piece_and_is_its_layout_for_the_rest():
    fold = fs.FoldLine((80, 0), (80, 100), 90, 1)
    lay = fs.fold_piece(MAIN, [fold], holes=[(20, 20, 1.0)])
    patch = sh.Sheet(_rect(90, 10, 150, 90), 0.5, name="Patch", key="s7")
    stack = sh.FlatStack(lay, [patch], MAIN, name="Body", key="s1",
                         leather="Navy")
    assert (stack.name, stack.key, stack.leather) == ("Body", "s1", "Navy")
    # a piece is a number: 0 the main piece, k the sheet sheets[k - 1]
    assert stack.sheet(1) is patch
    assert [stack.sheet(k) for k in (0, 2, -1)] == [None, None, None]
    assert all(stack.sheet(n.sheet) in (None, patch) for n in stack.notes)
    # what a stack has no word of its own for is its layout's
    assert stack.cells is lay.cells and stack.hinges is lay.hinges
    assert stack.frames([0.0]) == lay.frames([0.0])
    with pytest.raises(AttributeError):
        stack.no_such_thing
    bare = sh.FlatStack(lay)
    assert (bare.name, bare.key, bare.leather) == ("", "", "")
    assert bare.cells is lay.cells and bare.sheet(0) is None
    assert bare.frames([1.0]) == lay.frames([1.0]) != lay.frames([0.0])


def test_a_flat_stacks_folds_keep_the_radius_they_were_asked_for():
    folds = [fs.FoldLine((80, 0), (80, 100), 180, 1, order=1),
             fs.FoldLine((160, 0), (160, 100), 180, 1, order=2)]
    lay = fs.fold_piece([(0, 0), (240, 0), (240, 100), (0, 100)], folds,
                        base=1)
    # the second fold closes over the first flap: 1 mm of room is given
    assert (lay.radius, lay.wrap) == ([0.0, 1.0], [0.0, 1.0])
    stack = sh.FlatStack(lay, [sh.Sheet(_rect(90, 10, 150, 90), 0.5)])
    # no fold of a stack is made yet, so none needs room: what is left is
    # the radius that was asked for
    assert (stack.radius, stack.wrap, stack.used) == ([0.0, 0.0],
                                                      [0.0, 0.0], [0.0, 0.0])
    assert stack.notes[-1] == sh.Note("flat", 0, -1, 2.0)
    assert lay.radius == [0.0, 1.0]
    # a main piece with no folds has none left unmade: nothing to say
    bare = sh.FlatStack(fs.fold_piece(MAIN, []),
                        [sh.Sheet(_rect(90, 10, 150, 90), 0.5)], MAIN)
    assert bare.n_steps == 0 and [n.kind for n in bare.notes] == ["thick"]


def test_the_cut_edge_of_a_sheet_has_a_line_down_each_sharp_corner_only():
    lay = fs.fold_piece(MAIN, [], thickness=2.0)
    # three square corners, and one rounded in six chords of 15 degrees
    arc = [(90 + 10 * math.cos(math.radians(a)),
            70 + 10 * math.sin(math.radians(a))) for a in range(0, 91, 15)]
    ring = [(20.0, 20.0), (100.0, 20.0)] + arc + [(20.0, 80.0)]
    stack = sh.FlatStack(lay, [sh.Sheet(ring, 1.0)], MAIN)
    cell, = stack.solid([])[1:]
    walls = [f for f in cell.faces if f.kind == bsp.WALL]
    assert len(walls) == len(stack.sheets[0].ring) == 10
    sharp = {(20.0, 20.0), (100.0, 20.0), (20.0, 80.0)}
    for f in walls:
        foot, far = f.pts[0][:2], f.pts[1][:2]
        # along the top and the bottom always; up the side only where the
        # edge turns sharply
        assert f.edges == (True, far in sharp, True, foot in sharp)
    assert sum(f.edges[1] for f in walls) == 3


def test_a_sheet_over_nothing_is_drawn_on_the_nearest_panel():
    ring = [(0, 0), (200, 0), (200, 100), (0, 100)]
    lay = fs.fold_piece(ring, [fs.FoldLine((80, 0), (80, 100), 90, 1)])
    assert [p.index for p in lay.panels] == [0, 1]
    assert lay.panels[0].spot[0] < 80 < lay.panels[1].spot[0]
    for where, panel in ((_rect(230, 20, 280, 80), 1),
                         (_rect(-90, 20, -30, 80), 0),
                         (_rect(60, 120, 75, 150), 0),
                         (_rect(85, 120, 150, 150), 1)):
        stack = sh.FlatStack(lay, [sh.Sheet(where, 1.0)])
        assert stack.sheets[0].loose
        assert stack.solid([])[-1].tag == ("panel", panel, 1), where
    # ... and one that is over the main piece, on the panel it has most
    # leather over
    for where, panel in ((_rect(60, 20, 120, 80), 1),
                         (_rect(30, 20, 90, 80), 0)):
        stack = sh.FlatStack(lay, [sh.Sheet(where, 1.0)])
        assert stack.solid([])[-1].tag == ("panel", panel, 1), where


# -- marks only where a piece has leather ------------------------------------

def _windowed(d, side=1):
    """A pocket 100 x 80 mm with a window (40, 30)-(100, 70) cut in it,
    stitched every 4 mm round the window 2 mm outside it, on a main piece
    140 x 100 mm whose own ring of holes is the pocket's moved ``d`` mm in
    towards the window: ``(the layout, its check)``."""
    def round_window(off):
        x0, y0, x1, y1 = 40 - off, 30 - off, 100 + off, 70 + off
        out = []
        x = x0
        while x <= x1 + 1e-9:
            out += [(x, y0, 1.0), (x, y1, 1.0)]
            x += 4.0
        y = y0 + 4.0
        while y < y1 - 1e-9:
            out += [(x0, y, 1.0), (x1, y, 1.0)]
            y += 4.0
        return out
    pocket = sh.Sheet(_rect(20, 10, 120, 90), 1.0, side, name="Pocket",
                      holes=round_window(2.0),
                      cutouts=[_rect(40, 30, 100, 70)])
    lay = fs.fold_piece(_rect(0, 0, 140, 100), [], thickness=2.0,
                        holes=round_window(2.0 - d), sheets=[pocket])
    return lay, lay.lineup()


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("d, off", [(2.0, 54), (3.5, 49), (4.0, 50)])
def test_a_missed_hole_in_a_pockets_window_is_not_ringed_on_the_window(
        d, off, side):
    # the main piece's holes lie on the window's edge (d = 2) or 1.5 and
    # 2 mm inside it: the pocket's holes miss them, and are still off ...
    lay, check = _windowed(d, side)
    assert len(check.holes(fs.OFF)) == off
    # ... but where the missed holes are, the pocket has no leather: no
    # spot of them is told of it, and no ring is drawn on its window
    assert check.landed == {} and check.lands == {}
    in_window = [q for cell in lay.solid([], check)
                 if sh.sheet_of(cell.tag) == 1 for f in cell.faces
                 for kind, geo, style in f.decals or ()
                 if kind == bsp.DOTS and style[1] == fs.LANDS for q in geo
                 if 40 <= q[0] <= 100 and 30 <= q[1] <= 70]
    assert in_window == []


def test_a_missed_hole_is_still_ringed_where_the_other_piece_has_leather():
    # the main piece's ring 2 mm further out than the pocket's: each piece
    # has leather where the other's holes are, and both are ringed there
    lay, check = _windowed(-2.0)
    assert len(check.holes(fs.OFF)) == 112
    assert {k: len(v) for k, v in check.landed.items()} == {0: 52, 1: 54}
    for x, y, _dia in check.landed[1]:
        assert not (40 <= x <= 100 and 30 <= y <= 70)
        assert 20 < x < 120 and 10 < y < 90
    rings = [q for cell in lay.solid([], check)
             if sh.sheet_of(cell.tag) == 1 for f in cell.faces
             for kind, geo, style in f.decals or ()
             if kind == bsp.DOTS and style[1] == fs.LANDS for q in geo]
    assert rings and not any(40 <= q[0] <= 100 and 30 <= q[1] <= 70
                             for q in rings)


@pytest.mark.parametrize("dx, dy, turn", [
    (0.0, 0.0, 0.0), (10000.0, 10000.0, 0.0), (0.0, 0.0, 37.0),
    (0.0, 0.0, 90.0), (0.0, 0.0, 180.0), (500.0, -80.0, 211.0)])
def test_the_two_pieces_that_clear_each_other_at_a_corner_are_named_in_order(
        dx, dy, turn):
    # two piles the same each side of a fold standing at a right angle:
    # the two top pieces ask for the room, and are named by their numbers
    # (which half stays still goes by where the halves lie on the page)
    a = math.radians(turn)
    c, s = math.cos(a), math.sin(a)

    def f(p):
        return (dx + p[0] * c - p[1] * s, dy + p[0] * s + p[1] * c)
    sheets = [sh.Sheet([f(p) for p in r], t, 1) for r, t in (
        (_rect(60, 10, 99, 90), 1.0), (_rect(101, 10, 140, 90), 1.0),
        (_rect(70, 20, 99, 80), 0.8), (_rect(101, 20, 130, 80), 0.8))]
    lay = fs.fold_piece([f(p) for p in _rect(0, 0, 200, 100)],
                        [fs.FoldLine(f((100, -5)), f((100, 105)), 90.0, 1)],
                        thickness=1.5, sheets=sheets)
    assert lay.why == [("corner", 3, 4)]
    assert lay.radius[0] == pytest.approx(1.8, abs=1e-6)

"""Pieces that arrive put together: the bifold and the card-pocket stack
from Edit > Generate, the Card holder and Slim card holder templates, the
leather thickness their dialogs ask for, and Leather thickness on the
right-click menu."""

import json
import math
import os
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QSettings  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog,  # noqa: E402
                               QFormLayout, QLabel, QMenu)

from leathercad import assembly as asm  # noqa: E402
from leathercad import polycut as pc  # noqa: E402
from leathercad import templates  # noqa: E402
from leathercad.document import Document  # noqa: E402
from leathercad.generators import (bifold_wallet, box_gusset,  # noqa: E402
                                   card_pocket_stack, hemmed_pocket,
                                   key_tab, strap)
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (PathShape, Placement, Rectangle,  # noqa: E402
                               Transform)
from leathercad.stitching import holes_for_shape  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """Main windows on a drawing, their settings kept in a file of the
    test's own, shut without a question when the test is over."""
    from leathercad_app.mainwindow import MainWindow
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        MainWindow, "_settings",
        lambda self: QSettings(ini, QSettings.Format.IniFormat))
    made = []

    def make(doc):
        win = MainWindow(doc)
        win.canvas.rebuild()
        made.append(win)
        return win
    yield make
    for win in made:
        win._unsaved_changes = False
        win.hide()


@pytest.fixture
def mm(qapp):
    from leathercad_app import units
    was = units.current()
    units.set_current(units.MM)
    yield units
    units.set_current(was)


def _drawing(shapes):
    doc = Document("generated")
    for sh in shapes:
        doc.add_shape(sh)
    return doc


def _near(values, abs=1e-9):
    return pytest.approx(values, abs=abs)


def _flat(points):
    """Points laid end to end, to compare a whole row of them at once."""
    return [v for p in points for v in p]


def _boxes(doc):
    """Where the box of each placed piece lies on the bench, by name."""
    return {p.name: p.box for st in asm.stacks(doc, detail=False)
            for p in st.pieces}


def _held(shape):
    """The point of its box a piece is held by, and how far that point
    is from the same point of the main piece's box."""
    p = shape.placed
    return (p.ax, p.x, p.ay, p.y)


def _lap(a, b):
    """Whether two boxes share leather, not only an edge."""
    return (min(a[2], b[2]) - max(a[0], b[0]) > 1e-6
            and min(a[3], b[3]) - max(a[1], b[1]) > 1e-6)


def _levels(stack):
    """How high each piece's underside lies over the main piece's face,
    by name, and how thick the stack is at its thickest. Worked out as a
    stack is laid: in the order of the pile a piece rests on the highest
    top of anything laid before it that it lies over, else on the main
    piece."""
    tops, levels = [], {}
    for p in stack.over():
        lo = max((top for box, top in tops if _lap(box, p.box)),
                 default=0.0)
        levels[p.name] = lo
        tops.append((p.box, lo + p.thickness))
    return levels, stack.main.thickness + max(top for _box, top in tops)


_PILE = ["Outer shell", "Bill pocket liner",
         "Left pocket backing", "Left pocket 3", "Left pocket 2",
         "Left pocket 1 (front)",
         "Right pocket backing", "Right pocket 3", "Right pocket 2",
         "Right pocket 1 (front)"]


# -- the bifold ---------------------------------------------------------------

def test_the_generated_bifold_is_one_stack_of_ten_pieces_shell_at_the_bottom():
    parts = bifold_wallet(shell_t=1.6, inner_t=0.8)
    doc = _drawing(parts)
    st, = asm.stacks(doc)
    assert [p.name for p in st.pieces] == _PILE
    assert [p.order for p in st.pieces] == list(range(1, 11))
    # the shell is the main piece, turned over: its grain is the outside
    shell = st.main
    assert shell is st.pieces[0] and not st.lost
    assert (shell.name, shell.grain_up, shell.mirrored) == \
        ("Outer shell", False, True)
    # everything else lies on it, grain up, as it is cut
    assert [p.name for p in st.over()] == _PILE[1:] and st.under() == []
    assert all(p.grain_up and not p.mirrored for p in st.pieces[1:])
    assert all(p.placed.turn == 0.0 for p in st.pieces)
    # each with the thickness of its leather
    assert [p.thickness for p in st.pieces] == [1.6] + [0.8] * 9
    # the stack folds along the shell's spine, over the pockets: on the
    # bench the faces turned to the eye come together
    fold, = shell.folds
    assert (fold.name, fold.angle, fold.direction) == ("Spine fold", 180.0, 1)
    assert fold.a + fold.b == _near((0.0, -45.0, 0.0, 45.0))
    # the laser cuts the shell's 142 stitch holes, and none in the rest
    assert [len(p.holes) for p in st.pieces] == [142] + [0] * 9
    # nothing is left to put on, and the fold line is no piece
    assert [s.name for s in asm.offered(doc)] == \
        [s.name for s in parts if not s.is_fold_line]
    assert parts[1].placed is None and parts[1].thickness == 0.0


def test_the_bifold_called_as_today_has_no_thickness_and_the_same_stack():
    parts = bifold_wallet()
    doc = _drawing(parts)
    st, = asm.stacks(doc)
    assert [p.name for p in st.pieces] == _PILE
    assert [p.thickness for p in st.pieces] == [0.0] * 10
    assert not st.main.grain_up
    saved = doc.to_dict()["shapes"]
    assert all("thickness" not in s for s in saved)
    assert [("placed" in s) for s in saved] == [True, False] + [True] * 9
    # one thickness given: the pieces of that leather have it
    by = {s.name: s.thickness for s in bifold_wallet(shell_t=2.0)}
    assert by.pop("Outer shell") == 2.0 and set(by.values()) == {0.0}
    by = {s.name: s.thickness for s in bifold_wallet(inner_t=1.0)}
    assert (by.pop("Outer shell"), by.pop("Spine fold")) == (0.0, 0.0)
    assert set(by.values()) == {1.0}


def test_the_liner_lies_centred_and_each_pocket_pile_level_with_its_end():
    parts = bifold_wallet(shell_t=1.6, inner_t=0.8)
    doc = _drawing(parts)
    by = {s.name: s for s in parts}
    assert _boxes(doc) == {
        "Outer shell": _near((-99.6, -45.0, 99.6, 45.0)),
        "Bill pocket liner": _near((-99.6, -45.0, 99.6, 43.0)),
        "Left pocket backing": _near((-99.6, -45.0, -2.0, 34.0)),
        "Left pocket 3": _near((-99.6, -45.0, -2.0, 9.0)),
        "Left pocket 2": _near((-99.6, -45.0, -2.0, -1.0)),
        "Left pocket 1 (front)": _near((-99.6, -45.0, -2.0, -11.0)),
        "Right pocket backing": _near((2.0, -45.0, 99.6, 34.0)),
        "Right pocket 3": _near((2.0, -45.0, 99.6, 9.0)),
        "Right pocket 2": _near((2.0, -45.0, 99.6, -1.0)),
        "Right pocket 1 (front)": _near((2.0, -45.0, 99.6, -11.0))}
    # held by what is level: the liner by its middle and its bottom, a
    # pocket pile by its bottom and the end of the shell it lies at
    assert _held(by["Bill pocket liner"]) == (0.5, 0.0, 0.0, 0.0)
    for name in _PILE[2:6]:
        assert _held(by[name]) == (0.0, 0.0, 0.0, 0.0)
    for name in _PILE[6:]:
        assert _held(by[name]) == (1.0, 0.0, 0.0, 0.0)
    # what the stack is written as: the shell, and one piece of each kind
    key = by["Outer shell"].placed.of
    assert key.startswith("stack")
    assert by["Outer shell"].placed == Placement(of=key, order=1, main=True,
                                                 over=True)
    assert by["Bill pocket liner"].placed == Placement(
        of=key, order=2, ax=0.5, ay=0.0, cy=-1.0)
    assert by["Left pocket 2"].placed == Placement(
        of=key, order=5, ax=0.0, ay=0.0, cx=-50.8, cy=-23.0)
    assert by["Right pocket backing"].placed == Placement(
        of=key, order=7, ax=1.0, ay=0.0, cx=50.8, cy=-5.5)
    # so the pockets made 5.54 mm narrower, to stop at the bend, stay
    # level with the ends of the shell, and a shorter liner stays centred
    for name in _PILE[2:]:
        by[name].width -= 5.54
    by["Bill pocket liner"].width -= 3.77
    now = _boxes(doc)
    assert now["Left pocket 3"] == _near((-99.6, -45.0, -7.54, 9.0))
    assert now["Right pocket backing"] == _near((7.54, -45.0, 99.6, 34.0))
    assert now["Bill pocket liner"] == _near((-97.715, -45.0, 97.715, 43.0))


def test_the_bifold_s_levels_are_0_to_3_2_and_it_is_5_6_mm_at_the_thickest():
    st, = asm.stacks(_drawing(bifold_wallet(shell_t=1.6, inner_t=0.8)))
    levels, thickest = _levels(st)
    assert levels == {
        "Bill pocket liner": 0.0,
        "Left pocket backing": _near(0.8), "Left pocket 3": _near(1.6),
        "Left pocket 2": _near(2.4), "Left pocket 1 (front)": _near(3.2),
        "Right pocket backing": _near(0.8), "Right pocket 3": _near(1.6),
        "Right pocket 2": _near(2.4), "Right pocket 1 (front)": _near(3.2)}
    # the shell, the liner, a backing and three pockets
    assert thickest == _near(1.6 + 5 * 0.8) == _near(5.6)
    # the two piles lie apart: 4 mm between them, across the spine
    left, right = st.pieces[5], st.pieces[9]     # the two front pockets
    assert not _lap(left.box, right.box)
    assert right.box[0] - left.box[2] == _near(4.0)
    # in other leather the same stack stands as high as its leather
    st, = asm.stacks(_drawing(bifold_wallet(pockets_per_side=2, shell_t=2.0,
                                            inner_t=1.0)))
    levels, thickest = _levels(st)
    assert sorted(set(levels.values())) == _near([0.0, 1.0, 2.0, 3.0])
    assert thickest == _near(6.0)


def test_the_bifold_called_as_today_has_the_names_and_sizes_it_had():
    parts = bifold_wallet()
    assert [p.name for p in parts] == [
        "Outer shell", "Spine fold", "Bill pocket liner",
        "Left pocket 1 (front)", "Left pocket 2", "Left pocket 3",
        "Left pocket backing",
        "Right pocket 1 (front)", "Right pocket 2", "Right pocket 3",
        "Right pocket backing"]
    shell, spine, liner = parts[:3]
    assert (shell.width, shell.height, shell.corner_radius) == \
        (199.2, 90.0, 6.0) == (2 * (85.6 + 12) + 4, 90.0, 6.0)
    assert (liner.width, liner.height, liner.corner_radius) == \
        (199.2, 88.0, 6.0)
    assert [(p.width, p.height, p.corner_radius) for p in parts[3:]] == \
        [(97.6, 34.0, 0.0), (97.6, 44.0, 0.0), (97.6, 54.0, 0.0),
         (97.6, 79.0, 0.0)] * 2
    # laid out in a row on the drawing, where they were
    assert _flat((p.transform.x, p.transform.y) for p in parts
                 if p is not spine) == _near(_flat([
                     (0.0, 0.0), (0.0, -110.0),
                     (178.4, -28.0), (286.0, -23.0), (393.6, -18.0),
                     (501.2, -5.5),
                     (628.8, -28.0), (736.4, -23.0), (844.0, -18.0),
                     (951.6, -5.5)]))
    assert all(p.group_id is None and p.layer == "Cut" for p in parts
               if p is not spine)
    # the shell stitched all round, the rest unstitched
    assert (shell.stitch.pitch_mm, shell.stitch.inset) == (3.85, 3.5)
    assert [holes_for_shape(p).count for p in parts] == [142] + [0] * 10
    # the spine: the same line, folding flat
    assert [(p.x, p.y) for p in spine.points] == [(0.0, -45.0), (0.0, 45.0)]
    assert (spine.layer, spine.fold_angle) == ("Score", 180.0)
    # the tick off changes nothing, whatever the leather
    plain = bifold_wallet(shell_t=1.6, inner_t=0.8, fit=False)
    assert [(p.width, p.height) for p in plain if not p.is_fold_line] == \
        [(p.width, p.height) for p in parts if not p.is_fold_line]


def test_with_room_for_the_fold_the_shell_is_210_28_and_the_liner_206_51():
    parts = bifold_wallet(shell_t=1.6, inner_t=0.8, fit=True)
    doc = _drawing(parts)
    shell, _spine, liner = parts[:3]
    # each half carries the liner, a backing and three pockets: 4 mm
    pile = 0.8 * (3 + 2)
    spine = math.pi * (pile + 1.6 / 2)
    assert spine == pytest.approx(15.08, abs=0.005)
    assert shell.width == _near(2 * 97.6 + spine)
    assert shell.width == pytest.approx(210.28, abs=0.005)
    # the liner goes round the inside of the bend: 3.77 mm shorter
    assert liner.width == _near(shell.width - math.pi * (1.6 + 0.8) / 2)
    assert liner.width == pytest.approx(206.51, abs=0.005)
    assert shell.width - liner.width == pytest.approx(3.77, abs=0.005)
    # nothing else changes size
    assert (shell.height, liner.height) == (90.0, 88.0)
    assert [(p.width, p.height) for p in parts[3:]] == \
        [(97.6, 34.0), (97.6, 44.0), (97.6, 54.0), (97.6, 79.0)] * 2
    # the pockets end on the edge of the bend, 7.54 mm from the fold line
    st, = asm.stacks(doc)
    fold, = st.main.folds
    assert fold.a[0] == fold.b[0] == 0.0
    half = shell.width / 2
    for p in st.pieces[2:6]:
        assert (p.box[0], p.box[2]) == _near((-half, -spine / 2))
        assert fold.a[0] - p.box[2] == pytest.approx(7.54, abs=0.005)
    for p in st.pieces[6:]:
        assert (p.box[0], p.box[2]) == _near((spine / 2, half))
        assert p.box[0] - fold.a[0] == pytest.approx(7.54, abs=0.005)
    # the liner lies centred, 1.88 mm short of the shell at each end
    box = st.pieces[1].box
    assert (box[0], box[2]) == _near((-liner.width / 2, liner.width / 2))
    assert box[0] + half == pytest.approx(1.88, abs=0.005)
    assert [p.name for p in st.pieces] == _PILE
    assert [p.thickness for p in st.pieces] == [1.6] + [0.8] * 9
    # and on the drawing the row of pockets starts clear of the wider shell
    assert parts[3].transform.x - 97.6 / 2 - half == _near(30.0)


def test_room_for_the_fold_follows_the_leather_and_takes_both_thicknesses():
    def widths(**kw):
        parts = bifold_wallet(fit=True, **kw)
        return parts[0].width, parts[2].width
    # a thinner shell bends tighter: 14.45 mm of spine
    shell, liner = widths(shell_t=1.2, inner_t=0.8)
    assert shell == _near(195.2 + math.pi * 4.6)
    assert (shell, liner) == pytest.approx((209.65, 206.51), abs=0.005)
    # two pockets a side of 1 mm leather under a 2 mm shell
    shell, liner = widths(pockets_per_side=2, shell_t=2.0, inner_t=1.0)
    assert shell == _near(195.2 + math.pi * (1.0 * 4 + 1.0))
    assert liner == _near(shell - math.pi * 1.5)
    assert (shell, liner) == pytest.approx((210.91, 206.20), abs=0.005)
    # one pocket a side: a pile of three layers
    shell, liner = widths(pockets_per_side=1, shell_t=1.6, inner_t=0.8)
    assert shell == _near(195.2 + math.pi * (2.4 + 0.8))
    # a wider card: the panels grow, the spine does not
    shell, liner = widths(card_w=90.0, shell_t=1.6, inner_t=0.8)
    assert shell == _near(2 * 102.0 + math.pi * 4.8)
    # without both thicknesses there is no leather to make room for
    assert widths() == (199.2, 199.2)
    assert widths(shell_t=1.6) == (199.2, 199.2)
    assert widths(inner_t=0.8) == (199.2, 199.2)


# -- folds that close grain out -----------------------------------------------

def test_the_spine_the_ring_fold_and_the_buckle_fold_fold_to_the_back():
    def fold(parts):
        ln, = [p for p in parts if p.is_fold_line]
        return ln.name, ln.fold_dir, ln.fold_angle
    assert fold(bifold_wallet()) == ("Spine fold", "back", 180.0)
    assert fold(key_tab(20, 80)) == ("Ring fold", "back", 180.0)
    assert fold(strap(1050, 38, fold_from_end=90)) == \
        ("Buckle fold", "back", 180.0)
    # a hem always did; a gusset's corners stand open and fold as they did
    assert fold(hemmed_pocket()) == ("Hem fold", "back", 180.0)
    assert [(p.fold_dir, p.fold_angle) for p in box_gusset()
            if p.is_fold_line] == [("front", 90.0)] * 2
    # a piece drawn grain up folds its flesh faces together: for the key
    # tab alone on the bench, the faces turned away from the eye
    doc = _drawing(key_tab(20, 80))
    asm.put_on(doc, doc.shapes[0])
    tab, = asm.stacks(doc)[0].pieces
    assert (tab.grain_up, tab.folds[0].direction) == (True, -1)
    # the bifold's shell lies grain down under its pockets: the same fold
    # closes over them there
    st, = asm.stacks(_drawing(bifold_wallet()))
    assert (st.main.grain_up, st.main.folds[0].direction) == (False, 1)
    assert st.main.fold_dir(1) == "back"


# -- the card-pocket stack ----------------------------------------------------

def test_the_card_pocket_stack_arrives_stacked_on_its_backing():
    pieces = card_pocket_stack(thickness=0.8)
    doc = _drawing(pieces)
    st, = asm.stacks(doc)
    # the backing at the bottom, then the tallest pocket; the front one on
    # top
    assert [p.name for p in st.pieces] == [
        "Pocket backing", "Pocket 4", "Pocket 3", "Pocket 2",
        "Pocket 1 (front)"]
    assert st.main.name == "Pocket backing" and not st.lost
    assert all(p.grain_up and not p.mirrored for p in st.pieces)
    assert [p.thickness for p in st.pieces] == [0.8] * 5
    # every pocket centred on the backing, bottoms level
    assert _boxes(doc) == {
        "Pocket backing": _near((-51.8, -48.5, 51.8, 48.5)),
        "Pocket 4": _near((-51.8, -48.5, 51.8, 25.5)),
        "Pocket 3": _near((-51.8, -48.5, 51.8, 13.5)),
        "Pocket 2": _near((-51.8, -48.5, 51.8, 1.5)),
        "Pocket 1 (front)": _near((-51.8, -48.5, 51.8, -10.5))}
    assert [_held(p) for p in pieces[:4]] == [(0.5, 0.0, 0.0, 0.0)] * 4
    key = pieces[4].placed.of
    assert pieces[4].placed == Placement(of=key, order=1, main=True)
    assert pieces[0].placed == Placement(of=key, order=5, ax=0.5, ay=0.0,
                                         cy=-29.5)
    # each pocket rests on the one behind it: five layers, 4 mm
    levels, thickest = _levels(st)
    assert list(levels.values()) == _near([0.0, 0.8, 1.6, 2.4])
    assert thickest == _near(4.0)
    # the pieces are the ones it always made, in a row on the drawing
    assert _flat((p.width, p.height) for p in pieces) == _near(_flat(
        [(103.6, 38.0), (103.6, 50.0), (103.6, 62.0), (103.6, 74.0),
         (103.6, 97.0)]))
    assert [p.transform.x for p in pieces] == _near(
        [51.8, 170.4, 289.0, 407.6, 526.2])
    assert all(p.bounds()[1] == _near(0.0) for p in pieces)
    # with no thickness given the pieces have none, and are stacked still
    plain = card_pocket_stack(count=1)
    assert [p.thickness for p in plain] == [0.0, 0.0]
    st, = asm.stacks(_drawing(plain))
    assert [p.name for p in st.pieces] == ["Pocket backing",
                                           "Pocket 1 (front)"]
    assert st.pieces[1].box == _near((-51.8, -30.5, 51.8, 7.5))
    # two stacks generated are two stacks
    assert plain[0].placed.of != pieces[0].placed.of


# -- templates ----------------------------------------------------------------

def _as_drawn(stack):
    """Whether every piece lies on the bench where it is drawn against
    the main piece."""
    b = stack.main.shape.bounds()
    cx, cy = 0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])
    for p in stack.pieces:
        x0, y0, x1, y1 = p.shape.bounds()
        assert p.box == _near((x0 - cx, y0 - cy, x1 - cx, y1 - cy))
    return True


def test_the_card_holder_arrives_put_together_as_drawn():
    doc = templates.card_holder()
    panel, pocket = doc.shapes
    st, = asm.stacks(doc)
    # the back panel at the bottom, grain down; the pocket on it
    assert [(p.name, p.order, p.main, p.grain_up) for p in st.pieces] == \
        [("Back panel", 1, True, False), ("Pocket", 2, False, True)]
    assert [p.thickness for p in st.pieces] == [1.2, 1.2]
    assert (panel.thickness, pocket.thickness) == (1.2, 1.2)
    # as drawn: as wide as the panel, their bottoms level
    assert _as_drawn(st)
    assert _boxes(doc) == {"Back panel": _near((-52.5, -35.0, 52.5, 35.0)),
                           "Pocket": _near((-52.5, -35.0, 52.5, 10.0))}
    assert _held(pocket) == (0.5, 0.0, 0.0, 0.0)
    key = panel.placed.of
    assert panel.placed == Placement(of=key, order=1, main=True, over=True)
    assert pocket.placed == Placement(of=key, order=2, ax=0.5, ay=0.0,
                                      cy=-12.5)
    # 2.4 mm thick where the pocket lies
    levels, thickest = _levels(st)
    assert levels == {"Pocket": 0.0} and thickest == _near(2.4)
    # the panel's 82 stitch holes are its own; 46 pass under the pocket,
    # which has none
    under, on = st.pieces
    assert (len(under.holes), len(on.holes)) == (82, 0)
    assert sum(pc.inside(h[:2], on.outline) for h in under.holes) == 46
    # the drawing itself is the one it was
    assert [(s.width, s.height, s.transform.x, s.transform.y)
            for s in doc.shapes] == [(105, 70, 0, 0), (105, 45, 0, -12.5)]


def test_the_slim_card_holder_arrives_put_together_four_layers():
    doc = templates.slim_card_holder()
    st, = asm.stacks(doc)
    assert [(p.name, p.grain_up) for p in st.pieces] == [
        ("Back panel", False), ("Card pocket (back)", True),
        ("Card pocket (middle)", True), ("Card pocket (front)", True)]
    assert st.main.name == "Back panel"
    assert [p.thickness for p in st.pieces] == [1.2] * 4
    assert _as_drawn(st)
    assert _boxes(doc) == {
        "Back panel": _near((-35.0, -46.0, 35.0, 46.0)),
        "Card pocket (back)": _near((-35.0, -46.0, 35.0, 38.0)),
        "Card pocket (middle)": _near((-35.0, -46.0, 35.0, 30.0)),
        "Card pocket (front)": _near((-35.0, -46.0, 35.0, 22.0))}
    assert [_held(s) for s in doc.shapes[1:]] == [(0.5, 0.0, 0.0, 0.0)] * 3
    # four layers at the bottom, 4.8 mm
    levels, thickest = _levels(st)
    assert list(levels.values()) == _near([0.0, 1.2, 2.4])
    assert thickest == _near(4.8)
    # the one seam goes through every layer it is drawn over
    assert [len(p.holes) for p in st.pieces] == [67, 65, 61, 55]


def test_templates_that_are_one_piece_arrive_as_they_did():
    for build in (templates.belt, templates.key_fob,
                  templates.fold_over_wallet):
        doc = build()
        assert asm.stacks(doc) == []
        assert all(s.placed is None and s.thickness == 0.0
                   for s in doc.shapes)
        saved = doc.to_dict()["shapes"]
        assert all("placed" not in s and "thickness" not in s
                   for s in saved)


# -- kept with the drawing ----------------------------------------------------

def test_a_generated_stack_reads_back_exactly_as_it_was_written():
    for doc in (_drawing(bifold_wallet(shell_t=1.6, inner_t=0.8)),
                _drawing(bifold_wallet(shell_t=1.6, inner_t=0.8, fit=True)),
                _drawing(card_pocket_stack(thickness=0.8)),
                templates.card_holder(), templates.slim_card_holder()):
        st, = asm.stacks(doc)
        # the place on each piece is the one a window on the stack would
        # write for it at its next change: no number of the generator's own
        for p in st.pieces:
            assert p.shape.placed == p.placed
        # ... so a change that moves nothing changes nothing
        was = {s.shape_id: replace(s.placed) for s in doc.shapes
               if s.placed is not None}
        assert asm.move_by(doc, st.pieces[-1].shape, 0.0, 0.0)
        assert {s.shape_id: s.placed for s in doc.shapes
                if s.placed is not None} == was
        # through a file and back the stack is the same
        boxes = _boxes(doc)
        back = Document.from_dict(json.loads(json.dumps(doc.to_dict())))
        again, = asm.stacks(back)
        assert again.key == st.key
        assert [(p.name, p.thickness, p.grain_up) for p in again.pieces] == \
            [(p.name, p.thickness, p.grain_up) for p in st.pieces]
        assert _boxes(back) == {n: _near(b) for n, b in boxes.items()}


def test_places_survive_the_insert_and_one_undo_takes_the_whole_wallet_back(
        window):
    win = window(Document())
    c = win.canvas
    parts = bifold_wallet(shell_t=1.6, inner_t=0.8)
    places = [replace(p.placed) if p.placed else None for p in parts]
    boxes = _boxes(_drawing(parts))
    drawn = [(p.transform.x, p.transform.y) for p in parts]
    c.insert_generated(shapes=parts)
    assert win.doc.shapes == parts
    # the insert moves the whole row to the middle of the view, every
    # piece by the same distance: nothing else of a piece is touched
    (dx, dy), = {(round(p.transform.x - x, 6), round(p.transform.y - y, 6))
                 for p, (x, y) in zip(parts, drawn)}
    assert abs(dx) > 100.0                  # the row was drawn off-centre
    centre = c.mapToScene(c.viewport().rect().center())
    b = [p.bounds() for p in parts]
    assert (0.5 * (min(x[0] for x in b) + max(x[2] for x in b)),
            0.5 * (min(x[1] for x in b) + max(x[3] for x in b))) == \
        _near((centre.x(), centre.y()), abs=1e-6)
    # ... so every place is the one generated, and the stack lies as it lay
    assert [p.placed for p in parts] == places
    assert [p.thickness for p in parts] == [1.6, 0.0] + [0.8] * 9
    assert _boxes(win.doc) == {n: _near(b) for n, b in boxes.items()}
    st, = asm.stacks(win.doc)
    assert [p.name for p in st.pieces] == _PILE
    assert st.main.folds[0].a + st.main.folds[0].b == \
        _near((0.0, -45.0, 0.0, 45.0))
    # one undo step takes the whole wallet back, and redo brings it again
    assert win.history.can_undo()
    win.undo()
    assert win.doc.shapes == [] and asm.stacks(win.doc) == []
    assert not win.history.can_undo()
    win.redo()
    st, = asm.stacks(win.doc)
    assert [(p.name, p.thickness) for p in st.pieces] == \
        list(zip(_PILE, [1.6] + [0.8] * 9))
    assert _boxes(win.doc) == {n: _near(b) for n, b in boxes.items()}
    # a second wallet is a second stack; the first is as it was
    win.canvas.insert_generated(shapes=bifold_wallet())
    first, second = asm.stacks(win.doc)
    assert first.key == st.key != second.key
    assert [len(s.pieces) for s in (first, second)] == [10, 10]
    assert [p.thickness for p in second.pieces] == [0.0] * 10
    win.undo()
    assert [s.key for s in asm.stacks(win.doc)] == [st.key]


# -- the dialogs --------------------------------------------------------------

def _rows(dlg):
    """The fields of a dialog's form, by their labels."""
    form = dlg.findChild(QFormLayout)
    return {form.itemAt(r, QFormLayout.LabelRole).widget().text():
            form.itemAt(r, QFormLayout.FieldRole).widget()
            for r in range(form.rowCount())}


def test_the_bifold_dialog_asks_for_the_leather_and_remembers_it(
        window, tmp_path, monkeypatch, mm):
    win = window(Document())
    seen = {}

    def answer(shell_t=None, inner_t=None, fit=None):
        def run(dlg):
            f = _rows(dlg)
            seen["labels"] = list(f)
            seen["text"] = dlg.findChild(QLabel).text()
            seen["was"] = (f["Shell leather"].value(),
                           f["Lining and pocket leather"].value(),
                           f["Make room for the fold"].isChecked())
            seen["shown"] = (f["Shell leather"].text(),
                             f["Lining and pocket leather"].text())
            if shell_t is not None:
                f["Shell leather"].setValue(shell_t)
            if inner_t is not None:
                f["Lining and pocket leather"].setValue(inner_t)
            if fit is not None:
                f["Make room for the fold"].setChecked(fit)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QDialog, "exec", run)

    def made():
        parts = win.doc.shapes[-11:]
        return ([round(parts[0].width, 2), round(parts[2].width, 2)],
                [p.thickness for p in parts])
    # as it opens: 1.6 and 0.8 mm, no room made for the fold
    answer()
    win._gen_bifold()
    assert seen["labels"][-3:] == ["Shell leather",
                                   "Lining and pocket leather",
                                   "Make room for the fold"]
    assert "put together" in seen["text"]
    assert seen["was"] == (1.6, 0.8, False)
    assert seen["shown"] == ("1.6 mm", "0.8 mm")
    assert made() == ([199.2, 199.2], [1.6, 0.0] + [0.8] * 9)
    st, = asm.stacks(win.doc)
    assert [p.name for p in st.pieces] == _PILE
    # 2 mm and 1 mm, with room for the fold: 6 mm round a 5 mm pile
    answer(2.0, 1.0, True)
    win._gen_bifold()
    assert made() == ([214.05, 209.34], [2.0, 0.0] + [1.0] * 9)
    assert win.doc.shapes[-11].width == _near(195.2 + math.pi * 6.0)
    # kept with the dialog's other values, under their names
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert s.value("gen_bf_shell_t", type=float) == 2.0
    assert s.value("gen_bf_inner_t", type=float) == 1.0
    assert s.value("gen_bf_fit", type=bool) is True
    assert s.value("gen_bf_cw", type=float) == 85.6
    # ... and offered again the next time, in inches too
    mm.set_current(mm.IN)
    answer(fit=False)
    win._gen_bifold()
    assert seen["was"] == (2.0, 1.0, True)
    assert seen["shown"] == ("0.0787 in", "0.0394 in")
    assert made() == ([199.2, 199.2], [2.0, 0.0] + [1.0] * 9)
    assert s.value("gen_bf_fit", type=bool) is False
    assert len(asm.stacks(win.doc)) == 3
    # one wallet, one undo step
    win.undo()
    assert len(asm.stacks(win.doc)) == 2 and len(win.doc.shapes) == 22


def test_the_card_pocket_dialog_asks_for_a_thickness_and_remembers_it(
        window, tmp_path, monkeypatch, mm):
    win = window(Document())
    seen = {}

    def answer(thickness=None):
        def run(dlg):
            f = _rows(dlg)
            seen["labels"] = list(f)
            seen["text"] = dlg.findChild(QLabel).text()
            seen["was"] = f["Leather thickness"].value()
            seen["shown"] = f["Leather thickness"].text()
            if thickness is not None:
                f["Leather thickness"].setValue(thickness)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QDialog, "exec", run)
    answer()
    win._gen_card_pockets()
    assert seen["labels"] == ["Card width", "Card height", "Pockets",
                              "Reveal step", "Pocket depth",
                              "Side allowance", "Leather thickness"]
    assert "put together" in seen["text"]
    assert (seen["was"], seen["shown"]) == (0.8, "0.8 mm")
    assert [(s.name, s.thickness) for s in win.doc.shapes] == [
        ("Pocket 1 (front)", 0.8), ("Pocket 2", 0.8), ("Pocket 3", 0.8),
        ("Pocket 4", 0.8), ("Pocket backing", 0.8)]
    st, = asm.stacks(win.doc)
    assert [p.name for p in st.pieces] == [
        "Pocket backing", "Pocket 4", "Pocket 3", "Pocket 2",
        "Pocket 1 (front)"]
    assert st.pieces[1].box == _near((-51.8, -48.5, 51.8, 25.5))
    # another leather, remembered
    answer(1.4)
    win._gen_card_pockets()
    assert [s.thickness for s in win.doc.shapes[5:]] == [1.4] * 5
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert s.value("genCardThickness", type=float) == 1.4
    assert s.value("genCardCount", type=int) == 4
    answer()
    win._gen_card_pockets()
    assert (seen["was"], seen["shown"]) == (1.4, "1.4 mm")
    assert len(asm.stacks(win.doc)) == 3


# -- Leather thickness on the right-click menu --------------------------------

def _line(a, b, name, way="", layer="Score"):
    ln = PathShape(points=[Vec2(*a), Vec2(*b)], close_path=False, name=name,
                   layer=layer)
    ln.fold_dir, ln.fold_angle = way, 180.0
    return ln


def _select(win, *shapes):
    """Select these shapes on the canvas; their items come back."""
    from leathercad_app.items import ShapeItem
    c = win.canvas
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem) and any(it.model is s for s in shapes):
            it.setSelected(True)
    QApplication.processEvents()
    return [it for it in c.selected_items() if isinstance(it, ShapeItem)]


@pytest.fixture
def right_click(monkeypatch):
    """A right-click on the canvas answered without a menu or a prompt
    being shown: the menu's Leather thickness… is taken (if it can be) and
    ``seen["type"]`` typed into the prompt. What was offered is kept in
    ``seen``."""
    from leathercad_app import canvas as cm
    from leathercad_app.mathspin import MathSpinBox
    seen = {}

    class Menu(QMenu):
        def exec(self, *args, **kwargs):
            seen["menu"] = [a.text() for a in self.actions() if a.text()]
            entry = next(a for a in self.actions()
                         if a.text() == "Leather thickness…")
            seen["offered"] = entry.isEnabled()
            return entry if entry.isEnabled() else None

    def prompt(dlg):
        box = dlg.findChild(MathSpinBox)
        seen["title"] = dlg.windowTitle()
        seen["hint"] = dlg.findChild(QLabel).text()
        seen["shown"] = box.text()
        box.selectAll()
        QTest.keyClicks(box, seen["type"])
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(cm, "QMenu", Menu)
    monkeypatch.setattr(QDialog, "exec", prompt)

    def click(win, x, y, type=""):
        c = win.canvas
        seen.clear()
        seen["type"] = type
        pos = c.mapFromScene(QPointF(x, y))
        c.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Mouse, pos, c.viewport().mapToGlobal(pos)))
        QApplication.processEvents()
        return seen
    return click


def test_right_click_leather_thickness_sets_every_chosen_closed_piece(
        window, right_click, mm):
    doc = _drawing([
        Rectangle(name="Shell", width=200, height=90, layer="Cut"),
        Rectangle(name="Pocket", width=80, height=40, layer="Cut",
                  transform=Transform(x=300)),
        _line((0, -45), (0, 45), "Spine", way="back"),
        _line((250, -40), (250, 40), "Slit", layer="Cut")])
    win = window(doc)

    def thicknesses():
        return [s.thickness for s in win.doc.shapes]
    # everything chosen, a right-click on the pocket's edge: the two
    # closed pieces get it, the fold line and the slit are passed over
    _select(win, *win.doc.shapes)
    seen = right_click(win, 260.0, 0.0, type="1.6")
    assert seen["offered"]
    i = seen["menu"].index("Leather thickness…")
    assert seen["menu"][i - 1] == "Mark as fold line"
    assert seen["title"] == "Leather thickness"
    assert seen["hint"].startswith(
        "How thick the leather of these 2 pieces is")
    assert seen["shown"] == "0 mm"
    assert thicknesses() == [1.6, 1.6, 0.0, 0.0]
    # ... and Properties shows it at once
    assert win.properties.thickness.text() == "1.6 mm"
    # as one undo step
    assert win.history.can_undo()
    win.undo()
    assert thicknesses() == [0.0, 0.0, 0.0, 0.0]
    assert not win.history.can_undo()
    win.redo()
    assert thicknesses() == [1.6, 1.6, 0.0, 0.0]
    # on the edge of a piece that is not chosen: that piece alone. The
    # prompt opens on what it has, and takes inches
    mm.set_current(mm.IN)
    _select(win)
    seen = right_click(win, -100.0, 20.0, type="1/8")
    assert seen["hint"].startswith("How thick this piece's leather is")
    assert seen["shown"] == "0.063 in"
    assert thicknesses() == _near([3.175, 1.6, 0.0, 0.0])
    mm.set_current(mm.MM)
    # 0 takes it off again
    seen = right_click(win, -100.0, 20.0, type="0")
    assert seen["shown"] == "3.175 mm"
    assert thicknesses() == [0.0, 1.6, 0.0, 0.0]
    assert "thickness" not in win.doc.to_dict()["shapes"][0]
    # a fold line or a slit has no leather: nothing to set
    spine, slit = win.doc.shapes[2:]
    _select(win, spine, slit)
    seen = right_click(win, 250.0, 0.0, type="2")
    assert not seen["offered"]
    assert thicknesses() == [0.0, 1.6, 0.0, 0.0]


def test_set_thickness_takes_what_properties_takes(window, mm):
    doc = _drawing([
        Rectangle(name="Shell", width=200, height=90, layer="Cut"),
        Rectangle(name="Guide", width=50, height=50, layer="Cut",
                  transform=Transform(x=300), construction=True),
        _line((0, -45), (0, 45), "Spine", way="back")])
    win = window(doc)
    c = win.canvas
    items = _select(win, *win.doc.shapes)
    assert len(items) == 3
    c.set_thickness(items, 2.4)
    assert [s.thickness for s in win.doc.shapes] == [2.4, 0.0, 0.0]
    # thinner than the preview's thinnest leather is that; 12 mm at most
    c.set_thickness(items, 0.05)
    assert win.doc.shapes[0].thickness == 0.2
    c.set_thickness(items, 40.0)
    assert win.doc.shapes[0].thickness == 12.0
    c.set_thickness(items, 0.0)
    assert win.doc.shapes[0].thickness == 0.0
    # each was an undo step of its own; the same value again is none
    for want in (12.0, 0.2, 2.4, 0.0):
        win.undo()
        assert win.doc.shapes[0].thickness == want
    assert not win.history.can_undo()
    items = _select(win, *win.doc.shapes)
    c.set_thickness(items, 0.0)
    assert not win.history.can_undo()
    # nothing with leather among them: nothing happens
    c.set_thickness(_select(win, win.doc.shapes[2]), 1.0)
    assert [s.thickness for s in win.doc.shapes] == [0.0, 0.0, 0.0]
    assert not win.history.can_undo()

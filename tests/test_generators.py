"""Parametric generators: card-pocket stack + zipper opening."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.generators import (CARD_W, ZIP_WINDOW, card_pocket_stack,  # noqa: E402
                                   zipper_opening)
from leathercad.shapes import Transform  # noqa: E402


def test_card_stack_sizing_math():
    pieces = card_pocket_stack(card_w=85.6, card_h=54.0, count=3,
                               reveal=12.0, depth=38.0, ease=2.0,
                               allowance=7.0)
    assert len(pieces) == 4                        # 3 pockets + backing
    w = 85.6 + 2 * 2.0 + 2 * 7.0                   # card + ease + seams
    assert all(abs(p.width - w) < 1e-9 for p in pieces)
    assert [p.height for p in pieces[:3]] == [38.0, 50.0, 62.0]
    # backing: last pocket depth + exposed card head + allowance
    assert pieces[3].height == 62.0 + (54.0 - 38.0) + 7.0
    assert pieces[3].name == "Pocket backing"
    assert pieces[0].name.startswith("Pocket 1")
    # a card physically fits between the seams
    assert w - 2 * 7.0 >= CARD_W

    # bottom-aligned row, no overlaps
    lefts = [p.transform.x - p.width / 2 for p in pieces]
    rights = [p.transform.x + p.width / 2 for p in pieces]
    for i in range(len(pieces) - 1):
        assert rights[i] < lefts[i + 1]
    assert all(abs((p.transform.y - p.height / 2) - 0.0) < 1e-9
               for p in pieces)


def test_zipper_opening_geometry():
    slot, ring = zipper_opening(size="#5", length=150.0, stitch_offset=3.0)
    assert slot.width == 150.0
    assert slot.height == ZIP_WINDOW["#5"] == 8.0
    assert slot.corner_radius == 4.0               # stadium ends
    assert ring.closed
    # ring points sit exactly window/2 + offset from the slot's spine
    L = 150.0 / 2 - 4.0
    r = 4.0 + 3.0
    for p in ring.points:
        if -L <= p.x <= L:                         # straight sections
            d = abs(p.y)
        else:                                      # end caps
            cx = L if p.x > 0 else -L
            d = ((p.x - cx) ** 2 + p.y ** 2) ** 0.5
        assert abs(d - r) < 1e-6
    # the seam actually produces holes
    res = ring.result()
    assert res.count > 20
    assert res.closed


def test_zipper_sizes_differ():
    s3, _ = zipper_opening(size="#3", length=100)
    s8, _ = zipper_opening(size="#8", length=100)
    assert s3.height == 6.0 and s8.height == 10.0


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_insert_generated_centres_groups_and_commits(qapp):
    from leathercad.document import Document
    from leathercad_app import canvas as cm

    doc = Document()
    c = cm.Canvas(doc)
    c.rebuild()
    c.resize(800, 600)
    slot, ring = zipper_opening(size="#5", length=120.0)
    c.insert_generated(shapes=[slot], stitch_lines=[ring], group=True)
    assert doc.shapes == [slot]
    assert doc.stitch_lines == [ring]
    # grouped: window and ring move together on a click-drag
    assert slot.group_id is not None
    assert slot.group_id == ring.group_id
    # centred where the user is looking (view centre), ring still concentric
    centre = c.mapToScene(c.viewport().rect().center())
    assert abs(slot.transform.x - centre.x()) < 1.0
    assert abs(slot.transform.y - centre.y()) < 1.0
    xs = [p.x for p in ring.points]
    assert abs(0.5 * (min(xs) + max(xs)) - slot.transform.x) < 1e-6

    pieces = card_pocket_stack(count=2)
    c.insert_generated(shapes=pieces)
    assert len(doc.shapes) == 1 + 3               # slot + 2 pockets + backing
    assert all(p.group_id is None for p in pieces)  # separate pieces


# ---------------------------------------------------------------------------
# straps, gussets, wallets, tabs, hems -- and the fold allowance
# ---------------------------------------------------------------------------
def _extent(parts):
    b = [p.bounds() for p in parts]
    return (min(x[0] for x in b), min(x[1] for x in b),
            max(x[2] for x in b), max(x[3] for x in b))


def test_strap_tips_and_fittings():
    from leathercad.generators import strap
    from leathercad.offset import signed_area
    for tip in ("round", "point", "square"):
        parts = strap(1050, 38, tip=tip, slot_len=40, fold_from_end=90,
                      holes=5)
        x0, y0, x1, y1 = _extent(parts)
        assert abs((x1 - x0) - 1050.0) < 0.05 and abs((y1 - y0) - 38.0) < 1e-6
        body = parts[0]
        area = abs(signed_area(body.world_polyline()[0][:-1]))
        assert 0.98 * 1050 * 38 < area <= 1050 * 38 + 1e-6
        kinds = [p.kind for p in parts]
        assert kinds.count("circle") == 5 and "rectangle" in kinds
        fold = [p for p in parts if p.is_fold_line]
        assert len(fold) == 1 and fold[0].fold_angle == 180.0
        assert len({p.group_id for p in parts}) <= 2    # grouped on insert
    holes = [p for p in strap(holes=3, hole_spacing=20, hole_from_tip=50)
             if p.kind == "circle"]
    xs = sorted(p.transform.x for p in holes)
    assert [round(b - a, 6) for a, b in zip(xs, xs[1:])] == [20.0, 20.0]
    assert round(525 - xs[-1], 6) == 50.0                # from the tip


def test_gussets_have_the_right_length():
    import math
    from leathercad.generators import box_gusset, curved_gusset
    parts = box_gusset(200, 150, 60)
    assert parts[0].width == 500 and parts[0].height == 60
    folds = [p for p in parts if p.is_fold_line]
    assert sorted(round(f.transform.apply(f.points[0]).x, 6) for f in folds) \
        == [-100.0, 100.0]                               # 150 in from each end
    parts = curved_gusset(200, 150, 40, 60)
    want = 2 * (150 - 40) + (200 - 80) + 2 * math.pi * 40 / 2
    assert abs(parts[0].width - want) < 1e-9
    marks = [p for p in parts if p.kind == "path"]
    assert len(marks) == 4


def test_bifold_key_tab_and_hem():
    from leathercad.generators import bifold_wallet, key_tab, hemmed_pocket
    from leathercad.folds import fold_allowance
    parts = bifold_wallet(pockets_per_side=3)
    names = [p.name for p in parts]
    assert "Outer shell" in names and "Spine fold" in names
    assert sum("left" in n.lower() for n in names) == 4     # 3 pockets + backing
    assert sum("right" in n.lower() for n in names) == 4
    shell = parts[0]
    assert shell.width == 2 * (85.6 + 12) + 4
    tab = key_tab(20, 80, ring_slot=12)
    assert [p.kind for p in tab] == ["editpath", "path", "circle", "circle",
                                     "rectangle"]
    hem = hemmed_pocket(100, 60, hem=12, thickness=1.5)
    body, fold = hem
    assert abs(body.height - (72 + fold_allowance(180, 1.5))) < 1e-9
    assert body.stitch_edges == [True, True, True, False, False, False, True,
                                 True]                   # mouth unsewn
    assert fold.is_fold_line and fold.fold_dir == "back"


def test_fold_allowance_lengthens_the_flap_side():
    from leathercad.document import Document
    from leathercad.folds import apply_fold_allowance, fold_allowance, \
        folds_crossing
    from leathercad.generators import _fold_line
    from leathercad.geometry import Vec2
    from leathercad.holes import LooseHole
    from leathercad.shapes import Rectangle
    assert abs(fold_allowance(180, 2.0) - 3.14159265) < 1e-6
    assert abs(fold_allowance(90, 3.0, 1.0) - 3.92699) < 1e-5
    d = Document()
    r = Rectangle(width=100, height=60, layer="Cut")
    d.add_shape(r)
    f = _fold_line("f", 20, -30, 20, 30, 180)
    d.add_shape(f)
    d.holes.append(LooseHole(point=Vec2(40, 0)))       # on the flap
    d.holes.append(LooseHole(point=Vec2(-40, 0)))      # on the body
    assert folds_crossing(r, [f]) == [f]
    assert folds_crossing(Rectangle(width=10, height=10,
                                    transform=Transform(x=200)), [f]) == []
    res = apply_fold_allowance(d, r, [f], 2.0)
    (fold, allow, piece), = res
    b = piece.bounds()
    assert abs((b[2] - b[0]) - (100 + allow)) < 1e-9
    assert abs(d.holes[0].point.x - (40 + allow)) < 1e-9     # flap moved
    assert d.holes[1].point.x == -40                        # body didn't
    assert abs(f.transform.x - allow / 2) < 1e-9             # crease centred


def test_fold_allowance_command(qapp):
    from leathercad.document import Document
    from leathercad.generators import _fold_line
    from leathercad.shapes import Rectangle
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(Rectangle(width=100, height=60, layer="Cut"))
    doc.add_shape(_fold_line("f", 20, -30, 20, 30, 180))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for i in c.scene_obj.items():
        if c._item_model(i) is not None and not getattr(
                c._item_model(i), "is_fold_line", False):
            i.setSelected(True)
    win._gen_dialog = lambda *a, **k: {"fa_t": 2.0, "fa_r": 0.0}
    win._fold_allowance()
    piece = [s for s in win.doc.shapes if not s.is_fold_line][0]
    b = piece.bounds()
    assert abs((b[2] - b[0]) - 103.14159265) < 1e-6
    win.undo()
    piece = [s for s in win.doc.shapes if not s.is_fold_line][0]
    assert piece.width == 100

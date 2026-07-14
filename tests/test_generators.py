"""Parametric generators: card-pocket stack + zipper opening."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.generators import (CARD_W, ZIP_WINDOW, card_pocket_stack,  # noqa: E402
                                   zipper_opening)


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

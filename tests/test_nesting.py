"""Nesting: shape-aware sheet packing (engine + canvas command)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.nesting import NestPiece, Placement, nest, rotate90  # noqa: E402


def _rect(x, y, w, h):
    return ([Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h), Vec2(x, y + h),
             Vec2(x, y)], True)


def _placed_bbox(piece, pl):
    pts = [p for ring, _c in piece.outlines for p in ring]
    if pl.rotated:
        pts = [rotate90(p, pl.pivot) for p in pts]
    xs = [p.x + pl.dx for p in pts]
    ys = [p.y + pl.dy for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# -- engine -------------------------------------------------------------------
def test_nest_places_all_inside_margins_without_overlap():
    pieces = [NestPiece(key=i, outlines=[_rect(i * 200.0, 0, 40, 30)])
              for i in range(4)]
    pl, unplaced, used = nest(pieces, 100, 80, margin=3, spacing=2, cell=1.0)
    assert len(pl) == 4 and not unplaced and used > 0.5
    boxes = [_placed_bbox(pieces[p.key], p) for p in pl]
    for b in boxes:                                  # inside the margins
        assert b[0] >= 2.99 and b[1] >= 2.99
        assert b[2] <= 97.01 and b[3] <= 77.01
    for i in range(len(boxes)):                      # pairwise disjoint
        for j in range(i + 1, len(boxes)):
            A, B = boxes[i], boxes[j]
            assert (A[2] <= B[0] + 1e-6 or B[2] <= A[0] + 1e-6
                    or A[3] <= B[1] + 1e-6 or B[3] <= A[1] + 1e-6)


def test_nest_rotates_when_needed_and_respects_no_rotate():
    strap = [NestPiece(key="s", outlines=[_rect(0, 0, 60, 20)])]
    pl, unplaced, _ = nest(strap, 30, 70, margin=2, spacing=2, cell=1.0)
    assert pl and pl[0].rotated and not unplaced
    fixed = [NestPiece(key="s", outlines=[_rect(0, 0, 60, 20)],
                       allow_rotate=False)]
    pl, unplaced, _ = nest(fixed, 30, 70, margin=2, spacing=2, cell=1.0)
    assert not pl and unplaced == ["s"]


def test_nest_is_shape_aware_not_bbox():
    """A 30x30 square fits into an L-piece's notch: their bounding boxes
    could never share a 70x70 sheet, their real outlines can."""
    L = ([Vec2(0, 0), Vec2(60, 0), Vec2(60, 20), Vec2(20, 20), Vec2(20, 60),
          Vec2(0, 60), Vec2(0, 0)], True)
    pieces = [NestPiece("L", [L]),
              NestPiece("sq", [_rect(100, 100, 30, 30)], allow_rotate=False)]
    pl, unplaced, _ = nest(pieces, 70, 70, margin=2, spacing=2, cell=1.0)
    assert len(pl) == 2 and not unplaced


def test_nest_reports_oversize_pieces():
    pieces = [NestPiece("big", [_rect(0, 0, 500, 500)]),
              NestPiece("ok", [_rect(0, 0, 20, 20)])]
    pl, unplaced, _ = nest(pieces, 100, 100)
    assert unplaced == ["big"]
    assert [p.key for p in pl] == ["ok"]


# -- canvas command -----------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_nest_selected_moves_pieces_and_riders(qapp):
    from leathercad.document import Document, LooseHole
    from leathercad.shapes import Rectangle, Transform
    from leathercad_app import canvas as cm

    doc = Document()
    panels = []
    for i in range(3):
        r = Rectangle(name=f"Panel {i}", width=80, height=60,
                      transform=Transform(x=i * 500.0, y=0), layer="Cut")
        doc.add_shape(r)
        panels.append(r)
    # a slot INSIDE panel 0 and a loose hole inside it: both must ride along
    slot = Rectangle(name="Slot", width=20, height=6,
                     transform=Transform(x=0, y=10), layer="Cut")
    doc.add_shape(slot)
    hole = LooseHole(point=Vec2(10, -10))
    doc.holes.append(hole)

    c = cm.Canvas(doc)
    c.rebuild()
    rel_slot = (slot.transform.x - panels[0].transform.x,
                slot.transform.y - panels[0].transform.y)
    rel_hole = (hole.point.x - panels[0].transform.x,
                hole.point.y - panels[0].transform.y)

    report = c.nest_selected(300, 200, margin=5, spacing=3,
                             allow_rotate=False)
    assert "Placed 3 of 3" in report

    # everything landed on the sheet (which sits at the old bbox min corner)
    for p in panels:
        b = p.bounds()
        assert b[0] >= -45.0 and b[2] <= -40.0 + 300.0 + 5

    # the slot and hole kept their position relative to their panel
    assert abs((slot.transform.x - panels[0].transform.x) - rel_slot[0]) < 1e-6
    assert abs((slot.transform.y - panels[0].transform.y) - rel_slot[1]) < 1e-6
    assert abs((hole.point.x - panels[0].transform.x) - rel_hole[0]) < 1e-6
    assert abs((hole.point.y - panels[0].transform.y) - rel_hole[1]) < 1e-6

    # the sheet is drawn as a construction rectangle and never nests itself
    sheets = [s for s in doc.shapes if getattr(s, "construction", False)]
    assert len(sheets) == 1 and sheets[0].name == "Sheet"
    report2 = c.nest_selected(300, 200, margin=5, spacing=3,
                              allow_rotate=False)
    # slot still merges into its panel; the construction sheet never nests
    assert "Placed 3 of 3" in report2


def test_nest_selected_rotation_keeps_rider_registration(qapp):
    from leathercad.document import Document, LooseHole
    from leathercad.shapes import Rectangle, Transform
    from leathercad_app import canvas as cm

    doc = Document()
    strap = Rectangle(name="Strap", width=180, height=20,
                      transform=Transform(x=0, y=0), layer="Cut")
    doc.add_shape(strap)
    hole = LooseHole(point=Vec2(80, 0))        # near the strap's right tip
    doc.holes.append(hole)
    c = cm.Canvas(doc)
    c.rebuild()

    report = c.nest_selected(60, 250, margin=5, spacing=3, allow_rotate=True)
    assert "Placed 1 of 1" in report
    assert strap.transform.rotation in (90.0, -90.0)
    # the hole must still sit 80 mm from the strap centre ALONG the strap,
    # which now points in Y
    dx = hole.point.x - strap.transform.x
    dy = hole.point.y - strap.transform.y
    assert abs(dx) < 1e-6 and abs(abs(dy) - 80.0) < 1e-6


def test_nest_report_names_what_does_not_fit(qapp):
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    from leathercad_app import canvas as cm

    doc = Document()
    doc.add_shape(Rectangle(name="Body", width=400, height=400,
                            transform=Transform(x=0, y=0), layer="Cut"))
    doc.add_shape(Rectangle(name="Tab", width=20, height=20,
                            transform=Transform(x=500, y=0), layer="Cut"))
    c = cm.Canvas(doc)
    c.rebuild()
    report = c.nest_selected(100, 100, margin=5, spacing=3,
                             allow_rotate=False)
    assert "Placed 1 of 2" in report
    assert "Body" in report                    # named, not silently dropped

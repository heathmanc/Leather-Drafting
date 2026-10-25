"""Boolean union / difference / intersection: engine and canvas op."""

import os

import pytest

from leathercad.boolean import boolean_op, combine
from leathercad.geometry import Vec2
from leathercad.offset import signed_area


def rect(x0, y0, x1, y1):
    return [Vec2(x0, y0), Vec2(x1, y0), Vec2(x1, y1), Vec2(x0, y1)]


def area(rings):
    return sum(abs(signed_area(r)) for r in rings)


A = rect(0, 0, 60, 40)          # 2400
B = rect(40, 20, 100, 60)       # 2400, overlapping A by 20x20 = 400


def test_union_difference_intersection_areas():
    assert round(area(boolean_op("union", A, B)), 3) == 4400.0
    assert round(area(boolean_op("difference", A, B)), 3) == 2000.0
    assert round(area(boolean_op("intersection", A, B)), 3) == 400.0
    # each is a single connected ring here
    for op in ("union", "difference", "intersection"):
        assert len(boolean_op(op, A, B)) == 1


def test_containment_and_disjoint_fallbacks():
    inner = rect(10, 10, 20, 20)
    far = rect(200, 200, 220, 220)
    assert len(boolean_op("union", A, inner)) == 1          # inner vanishes
    diff = boolean_op("difference", A, inner)
    assert len(diff) == 2                                    # outer + cutout
    assert round(area([diff[1]]), 3) == 100.0
    assert boolean_op("intersection", A, far) == []
    assert len(boolean_op("union", A, far)) == 2
    assert len(boolean_op("difference", A, far)) == 1
    # subject swallowed entirely
    assert boolean_op("difference", inner, A) == []


def test_combine_folds_multiple():
    C = rect(90, 20, 140, 60)                                # overlaps B only
    res = combine("union", [A, B, C])
    # A∪B = 4400, ∪C adds 2000 - overlap(10x40=400) = 6000
    assert round(area(res), 3) == 6000.0


def test_degenerate_shared_edge_does_not_crash():
    E = rect(60, 0, 120, 40)                                 # shares A's edge
    res = boolean_op("union", A, E)
    assert res, "shared-edge union must return usable rings"
    assert abs(area(res) - 4800.0) < 1.0


def test_canvas_boolean_replaces_shapes():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitchsettings import StitchSettings
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))                    # bottom (subject)
    doc.add_shape(Rectangle(width=40, height=40, transform=Transform(x=40, y=20),
                            layer="Cut"))                    # top
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.boolean_selected("union")
    assert len(win.doc.shapes) == 1                          # merged
    sh = win.doc.shapes[0]
    pts, _corners, closed = sh.world_polyline()
    assert closed
    ring = pts[:-1]
    # A spans -30..30 x -20..20; B spans 20..60 x 0..40 -> overlap 10x20 = 200
    assert round(abs(signed_area(ring)), 1) == 3800.0        # 2400+1600-200
    assert sh.stitch is not None                             # kept from subject
    items = [it for it in c.scene_obj.items() if isinstance(it, ShapeItem)]
    assert len(items) == 1 and items[0].hole_count > 0       # holes recomputed


def test_canvas_subtract_bottom_minus_top():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))                    # bottom: kept
    doc.add_shape(Rectangle(width=20, height=20, transform=Transform(x=30, y=20),
                            layer="Cut"))                    # top: bites corner
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.boolean_selected("difference")
    assert len(win.doc.shapes) == 1
    pts, _c2, closed = win.doc.shapes[0].world_polyline()
    assert closed
    # top rect spans x 20..40, y 10..30 -> overlap with A's corner = 10x10=100
    assert round(abs(signed_area(pts[:-1])), 1) == 2300.0


def _areas(rings):
    return sorted(round(abs(signed_area(r)), 6) for r in rings)


@pytest.mark.parametrize("a,b", [
    (rect(0, 0, 10, 10), rect(10, 0, 20, 10)),      # B to the right
    (rect(10, 0, 20, 10), rect(0, 0, 10, 10)),      # B to the left
    (rect(0, 0, 10, 10), rect(0, 10, 10, 20)),      # B above
    (rect(0, 10, 10, 20), rect(0, 0, 10, 10)),      # B below
])
def test_union_of_pieces_sharing_an_edge_fuses_from_any_side(a, b):
    # the old fixed (+x, +y) nudge pushed a piece on that side AWAY, so the
    # union came back as two separate pieces
    assert _areas(boolean_op("union", a, b)) == [200.0]


def test_shared_edge_cases_come_out_exact():
    notch = boolean_op("difference", rect(0, 0, 30, 10), rect(10, 0, 20, 5))
    assert _areas(notch) == [250.0]
    pts = {(p.x, p.y) for p in notch[0]}
    assert {(10, 0), (10, 5), (20, 5), (20, 0)} <= pts    # exact corners
    assert _areas(boolean_op("union", rect(0, 0, 30, 10),
                             rect(10, 10, 20, 20))) == [400.0]
    assert _areas(boolean_op("intersection", rect(0, 0, 30, 10),
                             rect(10, 0, 20, 5))) == [50.0]
    assert boolean_op("intersection", rect(0, 0, 10, 10),
                      rect(10, 0, 20, 10)) == []


def test_combine_applies_every_operand_to_every_piece():
    # the bar splits the strap; the later hole must still be cut from the
    # right-hand piece (it was only applied to the largest piece)
    out = combine("difference", [rect(0, 0, 100, 50), rect(60, -1, 65, 51),
                                 rect(80, 20, 90, 30)])
    assert _areas(out) == [100.0, 1750.0, 3000.0]
    # overlapping cutouts merge into one hole, not two overlapping rings
    out = combine("difference", [rect(0, 0, 100, 100), rect(20, 20, 40, 40),
                                 rect(30, 30, 50, 50)])
    assert _areas(out) == [700.0, 10000.0]
    # a later shape can join two pieces
    out = combine("union", [rect(0, 0, 50, 50), rect(100, 0, 120, 20),
                            rect(110, 10, 130, 30)])
    assert _areas(out) == [700.0, 2500.0]


def test_union_keeps_the_stitch_hole_on_real_corners():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Circle, Transform
    from leathercad.stitchsettings import StitchSettings
    from leathercad.stitching import holes_for_shape
    win = MainWindow(Document())
    c = win.canvas
    r = c.add_shape(Rectangle(width=60, height=40, layer="Cut",
                              stitch=StitchSettings(pitch_mm=4.0, inset=3.0)))
    ci = c.add_shape(Circle(rx=10, ry=10, layer="Cut",
                            transform=Transform(x=30, y=20)))
    c.scene_obj.clearSelection()
    r.setSelected(True)
    ci.setSelected(True)
    c.boolean_selected("union")
    sh = win.doc.shapes[0]
    holes = holes_for_shape(sh).holes
    for corner in (Vec2(-27, -17), Vec2(27, -17), Vec2(-27, 17)):
        assert min((h.point - corner).length() for h in holes) < 1e-6
    assert sum(sh.corner_mask) == 5      # 3 corners + where the circle meets

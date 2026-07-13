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

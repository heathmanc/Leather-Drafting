"""Math in numeric fields + weld-lines-into-stitched-shape."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import PathShape, Transform  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# -- math fields ---------------------------------------------------------
def test_expression_evaluator(qapp):
    from leathercad_app.mathspin import evaluate
    assert evaluate("105/2 + 3") == 55.5
    assert evaluate("4*25.4") == 101.6
    assert abs(evaluate("1in") - 25.4) < 1e-9          # unit suffixes
    assert abs(evaluate("3cm+5mm") - 35.0) < 1e-9
    for bad in ("__import__('os')", "x+1", "abs(4)", "(1).real"):
        with pytest.raises(Exception):
            evaluate(bad)


def test_math_in_properties_width_field(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.shapes import Rectangle
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()
    it = [i for i in win.canvas.scene_obj.items() if isinstance(i, ShapeItem)][0]
    it.setSelected(True)
    win.properties.show_selection([it])
    w = win.properties.w
    w.lineEdit().setText("105/2 + 3 mm")
    w.interpretText()
    w.editingFinished.emit()
    assert abs(it.model.width - 55.5) < 1e-6           # expression applied


# -- weld lines -> stitched shape ------------------------------------------
def _line(a, b):
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return PathShape(points=[Vec2(a[0] - cx, a[1] - cy),
                             Vec2(b[0] - cx, b[1] - cy)],
                     close_path=False, transform=Transform(x=cx, y=cy),
                     layer="Cut")


def test_weld_four_lines_becomes_stitched_shape(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    doc = Document()
    for a, b in (((0, 0), (60, 0)), ((60, 0), (60, 40)),
                 ((60, 40), (0, 40)), ((0, 40), (0, 0))):
        doc.add_shape(_line(a, b))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.join_selected()
    assert len(win.doc.shapes) == 1
    sh = win.doc.shapes[0]
    assert getattr(sh, "closed", False) or getattr(sh, "close_path", False)
    assert sh.stitch is not None and sh.stitch.enabled   # holes added
    items = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)]
    assert items[0].hole_count > 20                      # perimeter stitched


def test_weld_open_chain_stays_unstitched(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    doc = Document()
    doc.add_shape(_line((0, 0), (60, 0)))
    doc.add_shape(_line((60, 0), (60, 40)))              # an L, not a loop
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.join_selected()
    assert len(win.doc.shapes) == 1
    assert win.doc.shapes[0].stitch is None              # open: no auto holes

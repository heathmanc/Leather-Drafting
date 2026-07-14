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


# -- weld lines into a path ------------------------------------------------
def _line(a, b, layer="Cut"):
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return PathShape(points=[Vec2(a[0] - cx, a[1] - cy),
                             Vec2(b[0] - cx, b[1] - cy)],
                     close_path=False, transform=Transform(x=cx, y=cy),
                     layer=layer)


def test_weld_four_lines_becomes_closed_shape(qapp):
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
    assert sh.stitch is None                             # NOT auto-stitched


def test_join_ignores_score_and_engrave(qapp):
    """Selecting the whole document must not fold score/engrave lines into the
    cut outline: only Cut-layer pieces are joined, the rest are untouched."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    doc = Document()
    for a, b in (((0, 0), (60, 0)), ((60, 0), (60, 40)),
                 ((60, 40), (0, 40)), ((0, 40), (0, 0))):
        doc.add_shape(_line(a, b))
    fold = _line((10, 20), (50, 20), layer="Score")      # a fold line
    doc.add_shape(fold)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.join_selected()
    # the four cut lines welded into one; the score line survives on its own
    cut = [s for s in win.doc.shapes if s.layer == "Cut"]
    score = [s for s in win.doc.shapes if s.layer == "Score"]
    assert len(cut) == 1 and len(score) == 1
    assert score[0] is fold                              # untouched
    assert cut[0].layer == "Cut"                         # result is a cut piece


def test_join_leaves_closed_cut_shapes_alone(qapp):
    """A standalone closed cut shape (e.g. a relief circle) has nothing to
    weld, so join must leave it untouched, not convert it to a path."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.shapes import Circle
    doc = Document()
    doc.add_shape(_line((0, 0), (60, 0)))
    doc.add_shape(_line((60, 0), (60, 40)))
    circ = Circle(rx=2, ry=2, transform=Transform(x=100, y=100), layer="Cut")
    doc.add_shape(circ)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.join_selected()
    assert circ in win.doc.shapes                        # circle untouched
    assert circ.kind == "circle"                         # not converted
    assert sum(1 for s in win.doc.shapes if s.kind == "circle") == 1

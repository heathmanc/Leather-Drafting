"""Offscreen smoke test for the Qt app (skipped if PySide6 is unavailable)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _shape_items(canvas):
    from leathercad_app.items import ShapeItem
    return [it for it in canvas.scene_obj.items() if isinstance(it, ShapeItem)]


def test_mainwindow_builds_and_edits(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    doc = Document()
    doc.add_shape(Rectangle(width=80, height=50, corner_radius=6,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()

    items = _shape_items(win.canvas)
    assert items
    item = items[0]
    n0 = item.hole_count
    assert n0 > 0

    # drive a property change through the panel and confirm holes recompute
    win.canvas.scene_obj.clearSelection()
    item.setSelected(True)
    win.properties.show_selection(win.canvas.selected_items())
    assert win.properties.isEnabled()
    win.properties.pitch.setValue(2.5)  # finer iron -> more holes
    assert item.hole_count > n0


def test_add_and_delete_shape(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    item = win.canvas.add_shape(
        Rectangle(width=40, height=30,
                  stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    assert len(win.doc.shapes) == 1
    win.canvas.scene_obj.clearSelection()
    item.setSelected(True)
    win.canvas.delete_selected()
    assert len(win.doc.shapes) == 0


def test_undo_redo(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    for i in range(3):
        win.canvas.add_shape(Rectangle(
            width=40, height=30, transform=Transform(x=i * 60, y=0),
            stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    assert len(win.doc.shapes) == 3
    win.undo()
    win.undo()
    assert len(win.doc.shapes) == 1
    win.redo()
    assert len(win.doc.shapes) == 2

    # property-edit undo
    item = win.canvas.add_shape(Rectangle(
        width=40, height=30, stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
        layer="Cut"))
    win.properties.show_selection([item])
    win.properties.w.setValue(88.0)
    win.properties.w.editingFinished.emit()
    assert item.model.width == 88.0
    win.undo()
    assert all(s.width != 88.0 for s in win.doc.shapes)


def test_align_left(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    for x in (0, 40, 90):
        win.canvas.add_shape(Rectangle(
            width=20, height=20, transform=Transform(x=x, y=0),
            stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    for it in _shape_items(win.canvas):
        it.setSelected(True)
    win.canvas.align_selected("left")
    lefts = {round(it.model.bounds()[0], 3) for it in win.canvas._shape_items()}
    assert len(lefts) == 1


def test_item_does_not_shadow_qt_shape_method(qapp):
    """Regression: ShapeItem must not shadow QGraphicsItem.shape() (a method Qt
    calls during mouse hit-testing) with the model object, or moving the mouse
    over a shape raises 'Rectangle object is not callable'."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QTransform
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    item = win.canvas.add_shape(Rectangle(
        width=80, height=50, transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    # shape() is Qt's method -> QPainterPath; the model lives on .model
    assert isinstance(item.shape(), QPainterPath)
    assert isinstance(item.model, Rectangle)
    # the hit-testing call that crashed must succeed
    hit = win.canvas.scene_obj.itemAt(QPointF(0, 0), QTransform())
    assert hit is item


def test_snapping(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_enabled = True
    c.snap_grid = 1.0

    p, vtx = c.snap(QPointF(12.3, 7.8))
    assert (round(p.x()), round(p.y())) == (12, 8) and not vtx

    c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                          stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                          layer="Cut"))
    p, vtx = c.snap(QPointF(19.4, 14.6))
    assert (round(p.x()), round(p.y())) == (20, 15) and vtx

    item = _shape_items(c)[0]
    item.setPos(10.4, -3.7)
    assert (item.pos().x(), item.pos().y()) == (10.0, -4.0)


def test_hole_slot_score_tools(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import Circle, Rectangle, PathShape

    win = MainWindow(Document())
    c = win.canvas

    c.tool = cm.HOLE
    c.hole_tool_diameter = 5.0
    c._place_hole(QPointF(20, 10))
    assert isinstance(win.doc.shapes[-1], Circle)
    assert win.doc.shapes[-1].stitch is None  # hardware hole, cut only

    c.tool = cm.SLOT
    c._finalize_drag(QPointF(0, 0), QPointF(40, 12))
    slot = win.doc.shapes[-1]
    assert isinstance(slot, Rectangle) and abs(slot.corner_radius - 6.0) < 1e-6

    c.tool = cm.SCORE
    c._poly_pts = [QPointF(0, 0), QPointF(30, 0), QPointF(30, 20)]
    c._finalize_poly()
    score = win.doc.shapes[-1]
    assert isinstance(score, PathShape) and score.layer == "Score"
    assert not score.close_path


def test_vertex_editing(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.canvas import Transform
    from leathercad.document import Document
    from leathercad.shapes import Polygon
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    poly = Polygon(points=[Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20),
                           Vec2(-20, 20)],
                   close_path=True, transform=Transform(x=100, y=0))
    item = c.add_shape(poly)
    c.enter_vertex_edit(item)
    assert len(c._handles) == 4
    c._handles[0].setPos(70, -30)   # world -> local (-30,-30) via inverse xform
    assert abs(poly.points[0].x - (-30)) < 1e-6
    assert abs(poly.points[0].y - (-30)) < 1e-6
    c.clear_vertex_handles()
    assert c._handles == []


def test_export_from_document(qapp, tmp_path):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad import export

    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, corner_radius=5,
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    win = MainWindow(doc)
    svg = tmp_path / "a.svg"
    export.export_svg(win.doc, str(svg))
    assert svg.read_text().count("<circle") > 5

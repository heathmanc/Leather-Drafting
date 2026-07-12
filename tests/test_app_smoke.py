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

    items = [it for it in win.canvas.scene_obj.items() if hasattr(it, "shape")]
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
    assert item.shape.width == 88.0
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
    for it in win.canvas.scene_obj.items():
        if hasattr(it, "shape"):
            it.setSelected(True)
    win.canvas.align_selected("left")
    lefts = {round(it.shape.bounds()[0], 3) for it in win.canvas._shape_items()}
    assert len(lefts) == 1


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

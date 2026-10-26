"""Inches mode reaches every length field, readout and prompt; the Array
dialog remembers itself and defaults its spacing to the piece; a saved part
keeps its seams and loose holes."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.holes import LooseHole  # noqa: E402
from leathercad.shapes import PathShape, Rectangle  # noqa: E402
from leathercad.stitchline import StitchLine  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402
from leathercad_app import canvas as canvas_mod  # noqa: E402
from leathercad_app import units as U  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def inches():
    """Inches for one test -- and back to millimetres afterwards, in the
    module state AND the persisted setting a new MainWindow restores from,
    so no later test inherits inches."""
    U.set_current("in")
    yield
    U.set_current("mm")
    QSettings("Stitch Hero", "Stitch Hero").setValue("units", "mm")


def _win(doc=None):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc or Document())
    win.resize(1400, 900)
    win.show()
    c = win.canvas
    c.rebuild()
    c.snap_to_grid = c.snap_to_nodes = False
    c.drag_to_draw = False
    QApplication.processEvents()
    return win, c


def _ev(c, kind, x, y, buttons=Qt.LeftButton):
    vp = c.viewport()
    pt = QPointF(c.mapFromScene(QPointF(x, y)))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    QApplication.sendEvent(vp, QMouseEvent(kind, pt, vp.mapToGlobal(pt), btn,
                                           buttons, Qt.NoModifier))
    QApplication.processEvents()


def test_toolbar_fields_follow_the_unit(qapp, inches):
    win, c = _win()
    win._set_units("in")                 # the window restores its saved unit
    assert win.fillet_spin.suffix() == " in" and win.kerf_spin.suffix() == " in"
    assert win.fillet_spin.valueFromText("0.25") == pytest.approx(6.35)
    win.fillet_spin.setValue(6.35)
    assert win.fillet_spin.text().startswith("0.25")
    assert c.fillet_radius == pytest.approx(6.35)
    assert win.kerf_spin.valueFromText("0.01") == pytest.approx(0.254)
    assert win.kerf_spin.valueFromText("0.2mm") == pytest.approx(0.2)
    win.hide()


def test_live_readouts_and_statuses_follow_the_unit(qapp, inches):
    win, c = _win()
    win._set_units("in")
    msgs = []
    c.statusMessage.connect(msgs.append)
    win._select_mode(canvas_mod.RECT)
    _ev(c, QEvent.MouseButtonPress, 0, 0)
    _ev(c, QEvent.MouseButtonRelease, 0, 0, Qt.NoButton)
    _ev(c, QEvent.MouseMove, 25.4, 12.7, Qt.NoButton)
    assert msgs[-1].endswith(" in") and "×" in msgs[-1] and "mm" not in msgs[-1]
    _ev(c, QEvent.MouseButtonPress, 25.4, 12.7)
    _ev(c, QEvent.MouseButtonRelease, 25.4, 12.7, Qt.NoButton)
    win._select_mode(canvas_mod.CIRCLE)
    _ev(c, QEvent.MouseButtonPress, 100, 0)
    _ev(c, QEvent.MouseButtonRelease, 100, 0, Qt.NoButton)
    _ev(c, QEvent.MouseMove, 125.4, 0, Qt.NoButton)
    assert msgs[-1].startswith("r ") and msgs[-1].endswith(" in")
    c.cancel_in_progress()
    # the fillet status
    c.fillet_radius = 6.35
    c._fillet_done_msg(False, True)
    assert "0.25 in" in msgs[-1] and "mm" not in msgs[-1]
    win.hide()


def test_prompts_and_two_number_dialogs_take_the_display_unit(qapp, inches,
                                                              monkeypatch):
    from leathercad_app import prompts
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    sp = prompts.length_spin(0, 1000, 2, 25.4)
    assert sp.suffix() == " in" and sp.text().startswith("1")
    assert sp.valueFromText("2") == pytest.approx(50.8)
    assert sp.valueFromText("0.5in") == pytest.approx(12.7)
    assert sp.valueFromText("10mm") == pytest.approx(10.0)
    v, ok = prompts.ask_length(None, "t", "l", 3.0, -100, 100, 2)
    assert ok and v == pytest.approx(3.0)
    win, c = _win()
    win._set_units("in")
    got = win._two_numbers("t", ("a", "b"), (25.4, 0.0))
    assert got == pytest.approx((25.4, 0.0))      # millimetres come back
    win.hide()


def test_array_dialog_defaults_to_the_piece_and_remembers(qapp, inches, tmp_path):
    from leathercad_app.arraydialog import ArrayDialog
    U.set_current("in")
    s = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    dlg = ArrayDialog(None, center=(0.0, 0.0), size=(40.0, 20.0), settings=s)
    assert dlg.dx.value() == pytest.approx(43.0)   # width + gap, in mm
    assert dlg.dx.suffix() == " in"
    assert "centre" in dlg.dx.toolTip().lower()
    dlg.rows.setValue(3)
    assert dlg.dy.value() == pytest.approx(23.0)   # a second row needs a step
    dlg.cols.setValue(7)
    dlg.accept()
    again = ArrayDialog(None, center=(0.0, 0.0), size=(40.0, 20.0), settings=s)
    assert again.rows.value() == 3 and again.cols.value() == 7
    mode, p = again.result_params()
    assert mode == "grid" and p["dx"] == pytest.approx(43.0)


def test_saved_parts_keep_seams_and_loose_holes(qapp, tmp_path):
    from leathercad_app import partslib
    from leathercad_app.items import HoleItem, StitchLineItem
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, layer="Cut"))
    doc.add_stitch_line(StitchLine(points=[Vec2(0, 30), Vec2(40, 30)],
                                   corner_points=[], closed=False,
                                   settings=StitchSettings(pitch_mm=4, inset=0)))
    doc.add_hole(LooseHole(point=Vec2(5, 5)))
    win, c = _win(doc)
    for it in list(c.scene_obj.items()):
        if c._item_model(it) is not None:
            it.setSelected(True)
    win.parts.base_dir = str(tmp_path)
    win.parts.save_selection("Pocket")
    path = partslib.list_parts(str(tmp_path))[0][1]
    shapes, texts, seams, holes = partslib.load_part_all(path)
    assert len(shapes) == 1 and len(seams) == 1 and len(holes) == 1
    fresh = Document()
    win2, c2 = _win(fresh)
    c2.place_shapes(shapes, texts, seams, holes)
    assert len(fresh.shapes) == 1 and len(fresh.stitch_lines) == 1
    assert len(fresh.holes) == 1
    assert any(isinstance(i, StitchLineItem) for i in c2.scene_obj.items())
    assert any(isinstance(i, HoleItem) for i in c2.scene_obj.items())
    # the seam kept its place relative to the piece
    r = fresh.shapes[0]
    sl = fresh.stitch_lines[0]
    assert sl.points[0].y - r.transform.y == pytest.approx(30.0)
    win.hide()
    win2.hide()


def test_offset_of_a_guide_from_the_tool_stays_a_guide(qapp):
    win, c = _win()
    guide = c.add_shape(PathShape(points=[Vec2(-50, 0), Vec2(50, 0)],
                                  close_path=False, layer="Cut",
                                  construction=True))
    c.scene_obj.clearSelection()
    guide.setSelected(True)
    c.offset_selected(5.0)
    assert len(win.doc.shapes) == 2 and all(s.construction for s in win.doc.shapes)
    win.hide()

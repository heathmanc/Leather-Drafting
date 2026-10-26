"""Inches as a display unit: shown and typed in inches, stored in mm."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.dimension import Dimension  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Rectangle  # noqa: E402
from leathercad_app import units  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _back_to_mm():
    yield
    units.set_current("mm")


def test_formatting_and_conversion():
    units.set_current("mm")
    assert units.fmt(12.7) == "12.70 mm"
    assert units.fmt_area(2500.0) == "25.0 cm²"
    units.set_current("in")
    assert units.fmt(12.7) == "0.5 in"
    assert units.fmt_num(25.4) == "1"
    assert units.fmt_area(645.16) == "1.00 sq in"
    assert units.has_explicit_unit("10mm") and units.has_explicit_unit("1/2 in")
    assert not units.has_explicit_unit("3/8") and not units.has_explicit_unit("w*2")
    assert Dimension(Vec2(0, 0), Vec2(25.4, 0)).label("in") == "1 in"
    assert Dimension(Vec2(0, 0), Vec2(12.7, 0)).label("in") == "0.5 in"


def test_length_fields_show_and_take_inches(qapp):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    item = win.canvas.add_shape(Rectangle(width=25.4, height=50.8, layer="Cut"))
    props = win.properties
    props.show_selection([item])
    assert props.w.text() == "25.4 mm"
    win._set_units("in")
    assert props.w.text() == "1 in" and props.h.text() == "2 in"
    # typed values are inches; an explicit unit wins
    assert props.w.valueFromText("3/8") == pytest.approx(9.525)
    assert props.w.valueFromText("10mm") == pytest.approx(10.0)
    props.w.lineEdit().setText("1.5 in")
    props.w.interpretText()
    props.w.editingFinished.emit()
    assert item.model.width == pytest.approx(38.1)
    # status bar, measure and grid follow
    win._cursor_moved(25.4, -12.7)
    assert win.sb_pos.text().startswith("X 1   Y -0.5")
    from PySide6.QtCore import QPointF
    assert win.canvas._measure_text(QPointF(0, 0), QPointF(50.8, 0)).startswith(
        "length 2 in")
    assert win.grid_combo.currentText() == "1/16 in"
    assert win.canvas.snap_grid == pytest.approx(25.4 / 16)
    win._set_units("mm")
    assert props.w.text() == "38.1 mm" and win.grid_combo.currentText() == "1 mm"


def test_inch_ruler_steps():
    from leathercad_app.rulers import _nice_step_in
    assert _nice_step_in(400.0) == 1 / 8          # 50 px per eighth
    assert _nice_step_in(20.0) == 5.0

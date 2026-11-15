"""Number boxes with a unit: the unit is part of the box's text, so typing a
unit never doubles it.

The report: "The entry block for line length is strange, sometimes 'mm'
ends up there twice." Qt held the unit as a suffix fixed at the end of the
box and put whatever was typed in front of it: typing ``25mm`` -- which the
box accepts -- showed ``25mm mm``, and typing at the very end of the box
gave ``27 mmmm mm``."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def mm(qapp):
    from leathercad_app import units
    was = units.current() if hasattr(units, "current") else None
    units.set_current(units.MM)
    yield units
    if was is not None:
        units.set_current(was)


def _box(suffix=" mm", physical=True):
    from leathercad_app import units
    from leathercad_app.mathspin import MathSpinBox
    sp = MathSpinBox()
    sp.setRange(-10000, 10000)
    sp.setDecimals(4)
    sp.trim_zeros = True
    if physical:
        sp.physical = True
        units.register_spin(sp)
    else:
        sp.setSuffix(suffix)
    sp.setValue(30.0)
    sp.show()
    return sp


def _type(qapp, sp, keys, select=True):
    sp.setFocus()
    if select:
        sp.selectAll()
    QTest.keyClicks(sp, keys)
    typing = sp.lineEdit().text()
    QTest.keyClick(sp, Qt.Key_Return)
    qapp.processEvents()
    return typing, sp.lineEdit().text()


def test_typing_a_unit_never_doubles_it(qapp, mm):
    sp = _box()
    assert sp.lineEdit().text() == "30 mm" and sp.suffix() == " mm"
    for keys, want in (("25mm", 25.0), ("26 mm", 26.0), ("27", 27.0),
                       ("1in", 25.4), ("10 cm", 100.0)):
        typing, after = _type(qapp, sp, keys)
        assert typing == keys                     # just what was typed
        assert sp.value() == pytest.approx(want)
        assert after.endswith(" mm") and after.count("mm") == 1


def test_editing_just_the_number_keeps_the_unit(qapp, mm):
    sp = _box()
    sp.setFocus()
    le = sp.lineEdit()
    le.setCursorPosition(2)                       # after "30"
    QTest.keyClick(sp, Qt.Key_Backspace)
    QTest.keyClick(sp, Qt.Key_Backspace)
    QTest.keyClicks(sp, "42")
    assert le.text() == "42 mm"
    QTest.keyClick(sp, Qt.Key_Return)
    assert sp.value() == pytest.approx(42.0)


def test_inches_show_and_read_inches(qapp, mm):
    sp = _box()
    mm.set_current(mm.IN)
    try:
        assert sp.suffix() == " in" and sp.lineEdit().text().endswith(" in")
        _type(qapp, sp, "2")
        assert sp.value() == pytest.approx(50.8)
        _type(qapp, sp, "25mm")
        assert sp.value() == pytest.approx(25.0)
        typing, after = _type(qapp, sp, "1 in")
        assert sp.value() == pytest.approx(25.4)
        assert after.count("in") == 1
    finally:
        mm.set_current(mm.MM)


def test_a_parameter_named_like_a_unit_is_not_cut_short(qapp, mm):
    from leathercad_app import mathspin
    mathspin.set_params_provider(lambda: {"margin": 12.0, "rim": 3.0})
    sp = _box()
    mm.set_current(mm.IN)
    try:
        _type(qapp, sp, "margin")                 # ends in "in"
        assert sp.value() == pytest.approx(12.0)
        assert sp.last_expr == "margin"
        _type(qapp, sp, "margin in")              # the box's own unit after it
        assert sp.value() == pytest.approx(12.0)
    finally:
        mm.set_current(mm.MM)
        mathspin.set_params_provider(None)


def test_other_units_in_the_text_too(qapp, mm):
    sp = _box(" °", physical=False)
    assert sp.lineEdit().text() == "30 °"
    _type(qapp, sp, "45°")
    assert sp.value() == pytest.approx(45.0)
    _type(qapp, sp, "90 °")
    assert sp.value() == pytest.approx(90.0)
    assert sp.lineEdit().text() == "90 °"

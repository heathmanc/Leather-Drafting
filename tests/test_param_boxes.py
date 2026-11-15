"""Typing a parameter into a box: however the box is entered, it takes, and
it stays linked.

The report: with a parameter ``sidelength`` = 10 mm, typing ``sidelength``
into a box's width didn't work, nor into a circle's radius or diameter. A
click into a box left the cursor where it landed, so the name was typed
into the old number ("40 mmsidelength"), which reads as nothing, and the
box went back to 40 without a word. A circle's radius was set but never
stayed linked (refilling the radius / diameter twins forgot that it came
from a parameter), and its diameter couldn't be linked at all."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolTip  # noqa: E402

from leathercad.shapes import Circle, Rectangle, Transform  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _rig(qapp, sh, params=None):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.params = dict(params or {"sidelength": "10mm"})
    doc.add_shape(sh)
    win = MainWindow(doc)
    win.resize(1300, 900)
    win.show()
    qapp.processEvents()
    it = next(i for i in win.canvas.scene_obj.items()
              if getattr(i, "model", None) is sh)
    it.setSelected(True)
    qapp.processEvents()
    return win, doc


def _enter(spin, how):
    le = spin.lineEdit()
    y = le.height() // 2
    if how == "tab":
        spin.setFocus(Qt.TabFocusReason)
    elif how == "click at the end":
        x = le.fontMetrics().horizontalAdvance(le.text()) + 4
        QTest.mouseClick(le, Qt.LeftButton, Qt.NoModifier,
                         QPoint(min(x, le.width() - 2), y))
    elif how == "click in the number":
        QTest.mouseClick(le, Qt.LeftButton, Qt.NoModifier, QPoint(6, y))
    elif how == "double-click":
        QTest.mouseDClick(le, Qt.LeftButton, Qt.NoModifier, QPoint(6, y))


def _box():
    return Rectangle(width=40, height=20, layer="Cut", transform=Transform(0, 0))


def _ring():
    return Circle(rx=15, ry=15, layer="Cut", transform=Transform(0, 0))


@pytest.mark.parametrize("how", ["tab", "click at the end",
                                 "click in the number", "double-click"])
@pytest.mark.parametrize("make,field,read,key", [
    (_box, "w", lambda s: s.width, "w"),
    (_box, "h", lambda s: s.height, "h"),
    (_ring, "circ_r", lambda s: s.rx, "rx"),
    (_ring, "circ_d", lambda s: 2 * s.rx, "d"),
])
def test_a_parameter_typed_into_a_box_takes_and_links(qapp, how, make,
                                                       field, read, key):
    sh = make()
    win, doc = _rig(qapp, sh)
    try:
        spin = getattr(win.properties, field)
        _enter(spin, how)
        QTest.keyClicks(spin, "sidelength")
        QTest.keyClick(spin, Qt.Key_Return)
        qapp.processEvents()
        assert read(sh) == pytest.approx(10.0)
        assert doc.bindings == {f"{sh.shape_id}:{key}": "sidelength"}
    finally:
        win.hide()


def _type(qapp, spin, text):
    _enter(spin, "click in the number")
    QTest.keyClicks(spin, text)
    QTest.keyClick(spin, Qt.Key_Return)
    qapp.processEvents()


def test_changing_the_parameter_resizes_what_is_linked(qapp):
    box = _box()
    win, doc = _rig(qapp, box)
    try:
        _type(qapp, win.properties.w, "sidelength*2")
        doc.params = {"sidelength": "15mm"}
        assert win.canvas.apply_param_bindings() == []
        assert win.doc.shapes[0].width == pytest.approx(30.0)
    finally:
        win.hide()
    for field, want_r in (("circ_r", 15.0), ("circ_d", 7.5)):
        ring = _ring()
        win, doc = _rig(qapp, ring)
        try:
            _type(qapp, getattr(win.properties, field), "sidelength")
            doc.params = {"sidelength": "15mm"}
            assert win.canvas.apply_param_bindings() == []
            c = win.doc.shapes[0]
            assert (c.rx, c.ry) == (pytest.approx(want_r), pytest.approx(want_r))
        finally:
            win.hide()


def test_radius_and_diameter_are_one_link(qapp):
    ring = _ring()
    win, doc = _rig(qapp, ring, {"r": "6mm", "d": "20mm"})
    try:
        _type(qapp, win.properties.circ_r, "r")
        assert doc.bindings == {f"{ring.shape_id}:rx": "r"}
        _type(qapp, win.properties.circ_d, "d")
        assert doc.bindings == {f"{ring.shape_id}:d": "d"}   # radius let go
        _type(qapp, win.properties.circ_r, "9")
        assert doc.bindings == {}                             # plain: no link
        assert ring.rx == pytest.approx(9.0)
    finally:
        win.hide()


def test_a_misspelt_parameter_says_so_and_keeps_the_value(qapp):
    box = _box()
    win, doc = _rig(qapp, box)
    try:
        _type(qapp, win.properties.w, "sidelenght")
        assert box.width == pytest.approx(40.0)
        assert "no parameter called “sidelenght”" in QToolTip.text()
        assert doc.bindings == {}
    finally:
        QToolTip.hideText()
        win.hide()


def test_a_second_click_still_places_the_cursor(qapp):
    box = _box()
    win, _doc = _rig(qapp, box)
    try:
        spin = win.properties.w
        le = spin.lineEdit()
        _enter(spin, "click in the number")
        assert le.selectedText() == le.text()          # first click: all
        QTest.mouseClick(le, Qt.LeftButton, Qt.NoModifier,
                         QPoint(6, le.height() // 2))
        assert not le.hasSelectedText()                # second: a cursor
    finally:
        win.hide()

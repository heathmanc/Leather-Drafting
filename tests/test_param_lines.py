"""A line's Length and Angle take parameters, and stay linked to them.

The report: a parameter ``sidelength`` = 10 mm "can't be used in the length
of a line box". Typing it into a drawn line's Length set the length once
but never linked it -- the line's Length and Angle weren't among the fields
a parameter re-drives -- and a straight segment left by Trim, Join or Break
apart had no Line box at all."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Edge, EditablePath, PathShape,  # noqa: E402
                               Transform, set_straight_line,
                               straight_line_ends)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _drawn():
    return PathShape(points=[Vec2(0, 0), Vec2(30, 40)], close_path=False,
                     layer="Cut", transform=Transform(5, 5))


def _segment():
    return EditablePath(nodes=[Vec2(-15, 0), Vec2(15, 0)], edges=[Edge("line")],
                        closed=False, layer="Cut", transform=Transform(0, 20))


def _rig(qapp, sh, params=None):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.params = dict(params or {"sidelength": "10mm"})
    doc.add_shape(sh)
    win = MainWindow(doc)
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    it = next(i for i in win.canvas.scene_obj.items()
              if getattr(i, "model", None) is sh)
    it.setSelected(True)
    qapp.processEvents()
    return win, doc


def _type(qapp, spin, text):
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, text)
    QTest.keyClick(spin, Qt.Key_Return)
    qapp.processEvents()


def _len_ang(sh):
    p0, p1 = straight_line_ends(sh)
    return (math.hypot(p1.x - p0.x, p1.y - p0.y),
            math.degrees(math.atan2(p1.y - p0.y, p1.x - p0.x)), p0)


@pytest.mark.parametrize("make", [_drawn, _segment])
def test_a_line_length_set_from_a_parameter_follows_it(qapp, make):
    sh = make()
    win, doc = _rig(qapp, sh)
    try:
        assert win.properties.g_line.isVisible()
        start = _len_ang(sh)[2]
        _type(qapp, win.properties.line_len, "sidelength")
        assert _len_ang(sh)[0] == pytest.approx(10.0)
        assert doc.bindings == {f"{sh.shape_id}:len": "sidelength"}
        doc.params = {"sidelength": "15mm"}
        assert win.canvas.apply_param_bindings() == []
        line = win.doc.shapes[0]
        length, _ang, p0 = _len_ang(line)
        assert length == pytest.approx(15.0)
        assert (p0.x, p0.y) == (pytest.approx(start.x), pytest.approx(start.y))
    finally:
        win.hide()


def test_an_expression_in_the_angle_box_links_too(qapp):
    sh = _drawn()
    win, doc = _rig(qapp, sh, {"tilt": "30"})
    try:
        _type(qapp, win.properties.line_angle, "tilt + 15")
        assert _len_ang(sh)[1] == pytest.approx(45.0)
        assert doc.bindings == {f"{sh.shape_id}:ang": "tilt + 15"}
        doc.params = {"tilt": "60"}
        win.canvas.apply_param_bindings()
        length, ang, _p0 = _len_ang(win.doc.shapes[0])
        assert ang == pytest.approx(75.0)
        assert length == pytest.approx(50.0)          # the length stays
    finally:
        win.hide()


def test_a_plain_number_unlinks_the_length(qapp):
    sh = _drawn()
    win, doc = _rig(qapp, sh)
    try:
        _type(qapp, win.properties.line_len, "sidelength")
        assert doc.bindings
        _type(qapp, win.properties.line_len, "12")
        assert _len_ang(sh)[0] == pytest.approx(12.0)
        assert doc.bindings == {}
    finally:
        win.hide()


def test_a_curved_two_node_path_is_not_a_line():
    arc = EditablePath(nodes=[Vec2(-10, 0), Vec2(10, 0)],
                       edges=[Edge("arc", Vec2(0, 5))], closed=False)
    assert straight_line_ends(arc) is None
    assert not set_straight_line(arc, length=5.0)
    seg = _segment()
    assert set_straight_line(seg, length=20.0, angle_deg=90.0)
    length, ang, p0 = _len_ang(seg)
    assert (length, ang) == (pytest.approx(20.0), pytest.approx(90.0))
    assert (p0.x, p0.y) == (pytest.approx(-15.0), pytest.approx(20.0))

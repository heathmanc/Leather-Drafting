"""Resizing to a typed size, from the top toolbar.

The request: "i need an aspect ratio lock button on the top toolbar. i also
want to be able to resize by inputing x/y coords on the top tool bar also.
they would obey the ratio lock if on."

The aspect lock was only in the View menu. It's now a chain button on the
top toolbar as well (the same switch), between two boxes, W and H, that
show the selection's width and height and resize it to a size typed in
them: about its centre, as one undo step, the other size keeping in
proportion while the lock is on."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Circle, PathShape, Rectangle,  # noqa: E402
                               Transform)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def rig(qapp):
    """``rig(*shapes, lock=False)``: a window on a drawing of ``shapes``,
    with the aspect lock set. The lock is a saved setting, so it's put
    back off afterwards for the tests that follow."""
    wins = []

    def make(*shapes, lock=False):
        from leathercad.document import Document
        from leathercad_app.mainwindow import MainWindow
        doc = Document()
        for sh in shapes:
            doc.add_shape(sh)
        win = MainWindow(doc)
        win.resize(1400, 900)
        win.show()
        qapp.processEvents()
        win.act_aspect_lock.setChecked(lock)
        wins.append(win)
        return win

    yield make
    for win in wins:
        win.act_aspect_lock.setChecked(False)
        win.hide()


def _select(qapp, win, *models):
    c = win.canvas
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if any(getattr(it, "model", None) is m for m in models):
            it.setSelected(True)
    qapp.processEvents()


def _type(qapp, spin, text):
    """Click into the box (which selects what's in it), type, press Enter."""
    le = spin.lineEdit()
    QTest.mouseClick(le, Qt.LeftButton, Qt.NoModifier,
                     QPoint(6, le.height() // 2))
    QTest.keyClicks(spin, text)
    QTest.keyClick(spin, Qt.Key_Return)
    qapp.processEvents()


def _box(w=80.0, h=40.0, x=0.0, y=0.0, **kw):
    return Rectangle(width=w, height=h, layer="Cut",
                     transform=Transform(x=x, y=y), **kw)


def test_the_lock_is_on_the_toolbar_and_in_the_view_menu(qapp, rig):
    win = rig(_box())
    tb = win.findChild(QToolBar, "ActionToolbar")
    assert win.act_aspect_lock in tb.actions()
    view = next(a.menu() for a in win.menuBar().actions()
                if a.text().replace("&", "") == "View")
    assert win.act_aspect_lock in view.actions()
    # it's one switch: the toolbar's button sets what the grips and the
    # boxes do, and is remembered
    button = tb.widgetForAction(win.act_aspect_lock)
    QTest.mouseClick(button, Qt.LeftButton)
    assert win.act_aspect_lock.isChecked() and win.canvas.aspect_lock
    assert win._settings().value("aspectLock", type=bool) is True
    QTest.mouseClick(button, Qt.LeftButton)
    assert not win.act_aspect_lock.isChecked()
    assert not win.canvas.aspect_lock


def test_the_boxes_show_the_size_of_what_is_selected(qapp, rig):
    r = _box(80, 40)
    win = rig(r)
    for box in (win.size_w, win.size_h):
        assert not box.isEnabled() and box.text() == "—"
    _select(qapp, win, r)
    assert win.size_w.isEnabled() and win.size_h.isEnabled()
    assert win.size_w.value() == pytest.approx(80.0)
    assert win.size_h.value() == pytest.approx(40.0)
    assert win.size_w.text() == "80 mm"
    win.canvas.scene_obj.clearSelection()
    qapp.processEvents()
    assert not win.size_w.isEnabled() and win.size_w.text() == "—"


def test_a_typed_width_resizes_about_the_centre_as_one_undo(qapp, rig):
    r = _box(80, 40, x=10, y=5)
    win = rig(r)
    _select(qapp, win, r)
    _type(qapp, win.size_w, "120")
    assert r.width == pytest.approx(120.0)
    assert r.height == pytest.approx(40.0)            # the lock is off
    assert (r.transform.x, r.transform.y) == pytest.approx((10.0, 5.0))
    assert win.size_w.value() == pytest.approx(120.0)
    assert win.size_h.value() == pytest.approx(40.0)
    win.undo()
    assert win.doc.shapes[0].width == pytest.approx(80.0)
    assert win.doc.shapes[0].height == pytest.approx(40.0)


def test_with_the_lock_on_the_other_size_keeps_in_proportion(qapp, rig):
    r = _box(80, 40)
    win = rig(r, lock=True)
    _select(qapp, win, r)
    _type(qapp, win.size_h, "60")
    assert (r.width, r.height) == pytest.approx((120.0, 60.0))
    assert win.size_w.value() == pytest.approx(120.0)
    _type(qapp, win.size_w, "40")
    assert (r.width, r.height) == pytest.approx((40.0, 20.0))
    assert win.size_h.value() == pytest.approx(20.0)


def test_a_group_resizes_as_one_piece(qapp, rig):
    a = _box(40, 40, x=-30, group_id="g1")            # x -50 .. -10
    b = _box(20, 20, x=30, group_id="g1")             # x  20 ..  40
    win = rig(a, b)
    _select(qapp, win, a)                             # one of the group
    assert win.size_w.value() == pytest.approx(90.0)
    assert win.size_h.value() == pytest.approx(40.0)
    _type(qapp, win.size_w, "180")                    # twice as wide
    assert (a.width, a.height) == pytest.approx((80.0, 40.0))
    assert (b.width, b.height) == pytest.approx((40.0, 20.0))
    # about the group's centre, x = -5: each piece's middle twice as far
    assert a.transform.x == pytest.approx(-55.0)
    assert b.transform.x == pytest.approx(65.0)
    assert win.size_w.value() == pytest.approx(180.0)


def test_sizes_are_shown_and_typed_in_inches_too(qapp, rig):
    from leathercad_app import units
    r = _box(76.2, 25.4)                              # 3 in x 1 in
    win = rig(r)
    _select(qapp, win, r)
    units.set_current("in")
    try:
        assert win.size_w.text() == "3 in"
        _type(qapp, win.size_w, "4")
        assert r.width == pytest.approx(101.6)
        _type(qapp, win.size_h, "20mm")               # a unit of its own
        assert r.height == pytest.approx(20.0)
    finally:
        units.set_current("mm")


def test_a_circle_stays_round(qapp, rig):
    ring = Circle(rx=15, ry=15, layer="Cut", transform=Transform(0, 0))
    win = rig(ring)                                   # the lock is off
    _select(qapp, win, ring)
    assert win.size_w.value() == pytest.approx(30.0)
    _type(qapp, win.size_w, "50")
    assert ring.rx == pytest.approx(25.0) and ring.ry == pytest.approx(25.0)
    assert win.size_h.value() == pytest.approx(50.0)


def test_a_straight_line_has_no_height_to_type(qapp, rig):
    line = PathShape(points=[Vec2(-50, 0), Vec2(50, 0)], close_path=False,
                     layer="Cut", transform=Transform(0, 0))
    win = rig(line, lock=True)
    _select(qapp, win, line)
    assert win.size_w.isEnabled() and win.size_w.value() == pytest.approx(100)
    assert not win.size_h.isEnabled() and win.size_h.text() == "—"
    _type(qapp, win.size_w, "150")
    xs = [p.x for p in line.points]
    assert max(xs) - min(xs) == pytest.approx(150.0)
    assert all(abs(p.y) < 1e-9 for p in line.points)  # still straight
    assert not win.canvas.resize_selection_to(height=10.0)


@pytest.mark.parametrize("turn", [30.0, 45.0, 90.0])
def test_scale_doubles_a_turned_piece(qapp, rig, turn):
    """Scale grew a rectangle or oval turned off the page axes by too much:
    a uniform x2 of one turned 30 degrees made it x2.7 (the stretch of
    each of its axes was taken as the sum of its parts, not their
    length). The typed sizes go through Scale, so they missed too."""
    from leathercad.shapes import Ellipse
    r = _box(80, 40)
    oval = Ellipse(rx=20, ry=10, layer="Cut", transform=Transform(200, 0))
    ring = Circle(rx=15, ry=15, layer="Cut", transform=Transform(-200, 0))
    for sh in (r, oval, ring):
        sh.transform.rotation = turn
    win = rig(r, oval, ring)
    _select(qapp, win, r, oval, ring)
    win.canvas.scale_selected(2.0)
    assert (r.width, r.height) == pytest.approx((160.0, 80.0))
    assert (oval.rx, oval.ry) == pytest.approx((40.0, 20.0))
    assert (ring.rx, ring.ry) == pytest.approx((30.0, 30.0))


def test_a_turned_piece_takes_the_size_typed(qapp, rig):
    r = _box(80, 40)
    r.transform.rotation = 30.0
    win = rig(r, lock=True)
    _select(qapp, win, r)
    w0, h0 = win.size_w.value(), win.size_h.value()   # its box, 89.28 x 74.64
    _type(qapp, win.size_w, f"{2 * w0:.4f}")
    assert (r.width, r.height) == pytest.approx((160.0, 80.0), abs=1e-3)
    assert r.transform.rotation == pytest.approx(30.0)
    assert win.size_h.value() == pytest.approx(2 * h0, abs=1e-3)


def test_the_boxes_follow_a_grip_drag(qapp, rig):
    from leathercad_app.items import ResizeHandle
    r = _box(80, 40)
    win = rig(r)
    _select(qapp, win, r)
    corner = next(g for g in win.canvas.scene_obj.items()
                  if isinstance(g, ResizeHandle) and g.grip == (1, 1))
    corner._apply_resize(Vec2(60, 30))                # from 40, 20
    QTest.qWait(150)
    assert win.size_w.value() == pytest.approx(r.width)
    assert win.size_w.value() == pytest.approx(100.0)
    assert win.size_h.value() == pytest.approx(50.0)


def test_a_size_being_typed_is_not_overwritten(qapp, rig):
    r = _box(80, 40)
    win = rig(r)
    _select(qapp, win, r)
    le = win.size_w.lineEdit()
    QTest.mouseClick(le, Qt.LeftButton, Qt.NoModifier,
                     QPoint(6, le.height() // 2))
    QTest.keyClicks(win.size_w, "12")
    win._refresh_size_fields()                        # the drawing changed
    assert le.text() == "12"
    QTest.keyClick(win.size_w, Qt.Key_Return)
    qapp.processEvents()
    assert r.width == pytest.approx(12.0)

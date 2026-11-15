"""Picking things drawn close together.

The report: a construction line ran between the centres of two 1 mm holes
5 mm apart, with a vertical line through its middle, and the construction
line could not be selected to delete it. Every item's pick zone was a fixed
size in millimetres -- a 3.5 mm band round a line, a 1.6 mm disk round a
hole -- and Qt gives a click to the TOPMOST item whose zone it falls in, so
the holes' disks and the crossing line's band covered the guide end to end
at any zoom. A click now goes to the item whose drawn line or hole is
nearest the pointer, pick zones shrink when zoomed in, and Alt+click steps
to the next item under the pointer."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.holes import LooseHole  # noqa: E402
from leathercad.shapes import Circle, PathShape, Transform  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _rig(qapp, holes="loose", zoom=3.0):
    """The reported layout: two 1 mm holes at x = 0 and 5, a construction
    line between their centres, a vertical line through its middle drawn
    last (so on top)."""
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    win.resize(1000, 800)
    win.show()
    qapp.processEvents()
    c = win.canvas
    for x in (0.0, 5.0):
        if holes == "loose":
            c.doc.holes.append(LooseHole(point=Vec2(x, 0.0), hole_diameter=1.0))
        else:                                   # the Hole tool's cut circle
            c.doc.add_shape(Circle(rx=0.5, ry=0.5, layer="Cut",
                                   transform=Transform(x=x, y=0)))
    guide = PathShape(points=[Vec2(-2.5, 0), Vec2(2.5, 0)], close_path=False,
                      construction=True, layer="Cut",
                      transform=Transform(x=2.5, y=0))
    vert = PathShape(points=[Vec2(0, -4), Vec2(0, 4)], close_path=False,
                     layer="Cut", transform=Transform(x=2.5, y=0))
    c.doc.add_shape(guide)
    c.doc.add_shape(vert)
    c.rebuild()
    c._zoom = zoom
    c._apply_zoom()
    c.centerOn(QPointF(2.5, 0))
    qapp.processEvents()
    return win, c, guide, vert


def _click(c, x, y, mods=Qt.NoModifier):
    vp = QPointF(c.mapFromScene(QPointF(x, y)))
    gp = QPointF(c.viewport().mapToGlobal(vp.toPoint()))
    c.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, vp, gp,
                                  Qt.LeftButton, Qt.LeftButton, mods))
    c.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, vp, gp,
                                    Qt.LeftButton, Qt.NoButton, mods))


def _selected_models(c):
    out = []
    for it in c.selected_items():
        out.append(getattr(it, "model", None) or getattr(it, "hole", None))
    return out


@pytest.mark.parametrize("holes", ["loose", "circle"])
@pytest.mark.parametrize("zoom", [3.0, 30.0])
def test_the_guide_between_two_holes_can_be_picked(qapp, holes, zoom):
    win, c, guide, vert = _rig(qapp, holes, zoom)
    try:
        for x in (1.25, 1.75, 3.25, 3.75):     # clear of the holes and line
            c.scene_obj.clearSelection()
            _click(c, x, 0.0)
            assert _selected_models(c) == [guide], (holes, zoom, x)
        # ...and deleted, which is what it was picked for
        c.delete_selected()
        assert guide not in c.doc.shapes and vert in c.doc.shapes
    finally:
        win.hide()


def test_a_tie_goes_to_the_top_and_alt_click_steps_through(qapp):
    win, c, guide, vert = _rig(qapp, zoom=30.0)
    msgs = []
    c.statusMessage.connect(msgs.append)
    try:
        _click(c, 2.5, 0.0)                    # right on the crossing
        assert _selected_models(c) == [vert]   # the one drawn on top
        assert "Alt+click" in msgs[-1]
        _click(c, 2.5, 0.0, Qt.AltModifier)
        assert _selected_models(c) == [guide]
        assert "construction line" in msgs[-1]
        _click(c, 2.5, 0.0, Qt.AltModifier)    # and round again
        assert _selected_models(c) == [vert]
    finally:
        win.hide()


def test_pick_zones_shrink_when_zoomed_in(qapp):
    from leathercad_app.items import HoleItem, ShapeItem, hit_half_width
    win, c, guide, vert = _rig(qapp, zoom=3.0)
    try:
        assert hit_half_width(c) == pytest.approx(5.0 / 3.0)   # ~ as before
        item = next(it for it in c.scene_obj.items()
                    if isinstance(it, ShapeItem) and it.model is guide)
        hole = next(it for it in c.scene_obj.items()
                    if isinstance(it, HoleItem))
        assert item.shape().contains(QPointF(0.0, 1.5))      # 1.5 mm off it
        assert hole.shape().contains(QPointF(1.5, 0.0))
        c._zoom = 30.0
        c._apply_zoom()
        assert hit_half_width(c) == pytest.approx(5.0 / 30.0)
        assert not item.shape().contains(QPointF(0.0, 1.5))
        assert item.shape().contains(QPointF(0.0, 0.1))
        assert not hole.shape().contains(QPointF(1.5, 0.0))
        assert hole.shape().contains(QPointF(0.6, 0.0))     # just past its rim
        c._zoom = 0.5                         # zoomed out: never bigger
        c._apply_zoom()
        assert hit_half_width(c) == pytest.approx(1.75)
    finally:
        win.hide()


def _ev(c, kind, x, y, held=True, mods=Qt.NoModifier):
    vp = QPointF(c.mapFromScene(QPointF(x, y)))
    gp = QPointF(c.viewport().mapToGlobal(vp.toPoint()))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    return QMouseEvent(kind, vp, gp, btn,
                       Qt.LeftButton if held else Qt.NoButton, mods)


def test_a_multi_selection_still_drags_but_a_click_picks_the_nearest(qapp):
    """With several things selected, a press in a selected item's zone stays
    with the selection, so dragging moves them all -- even nearer something
    unselected. A plain click there still picks the nearest thing alone."""
    win, c, guide, vert = _rig(qapp, zoom=3.0)
    c.snap_to_grid = c.snap_to_nodes = False
    try:
        hole = c.doc.holes[0]
        items = {id(getattr(it, "model", None) or getattr(it, "hole", None)): it
                 for it in c.scene_obj.items()}

        c.scene_obj.clearSelection()
        items[id(vert)].setSelected(True)
        items[id(hole)].setSelected(True)
        assert c.pick_candidates(c.mapFromScene(QPointF(1.0, 0.0)))[0] \
            .model is guide                     # the nearest is the guide
        x0, hx0, gx0 = vert.transform.x, hole.point.x, guide.transform.x
        c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, 1.0, 0.0))
        for k in range(1, 6):
            c.mouseMoveEvent(_ev(c, QEvent.MouseMove, 1.0 + 2.0 * k, 0.0))
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, 11.0, 0.0,
                                held=False))
        assert vert.transform.x > x0 + 5 and hole.point.x > hx0 + 5
        assert guide.transform.x == gx0          # it wasn't selected
        assert set(map(id, _selected_models(c))) == {id(vert), id(hole)}
    finally:
        win.hide()
    # a plain click in the same place means the guide
    win, c, guide, vert = _rig(qapp, zoom=3.0)
    try:
        hole = c.doc.holes[0]
        items = {id(getattr(it, "model", None) or getattr(it, "hole", None)): it
                 for it in c.scene_obj.items()}
        items[id(vert)].setSelected(True)
        items[id(hole)].setSelected(True)
        _click(c, 1.0, 0.0)
        assert _selected_models(c) == [guide]
    finally:
        win.hide()


def test_locked_items_are_looked_through(qapp):
    win, c, guide, vert = _rig(qapp, zoom=30.0)
    try:
        from leathercad.layers import Layer
        c.doc.layers.append(Layer(name="Locked", color="#ff00ff",
                                  locked=True))
        vert.layer = "Locked"
        c.rebuild()
        _click(c, 2.5, 0.0)                    # the crossing
        assert _selected_models(c) == [guide]
    finally:
        win.hide()


def test_right_click_and_double_click_use_the_same_pick(qapp):
    win, c, guide, vert = _rig(qapp, zoom=3.0)
    try:
        it = c.pick_item(c.mapFromScene(QPointF(1.25, 0.0)))
        assert it.model is guide
    finally:
        win.hide()


def test_a_double_click_keeps_the_nearest_item(qapp):
    """A double-click with nothing to edit (a rectangle) is a second press
    to Qt, which went to the topmost item -- here a hole beside the edge."""
    from leathercad.document import Document
    from leathercad.shapes import Rectangle
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    win.resize(1000, 800)
    win.show()
    qapp.processEvents()
    c = win.canvas
    try:
        rect = Rectangle(width=20, height=10, layer="Cut",
                         transform=Transform(x=0, y=0))
        c.doc.add_shape(rect)
        c.doc.holes.append(LooseHole(point=Vec2(11.2, 0.0), hole_diameter=1.0))
        c.rebuild()
        c._zoom = 3.0
        c._apply_zoom()
        c.centerOn(QPointF(10.0, 0.0))
        qapp.processEvents()
        _click(c, 10.0, 0.0)                     # on the right edge
        assert _selected_models(c) == [rect]
        c.mouseDoubleClickEvent(_ev(c, QEvent.MouseButtonDblClick, 10.0, 0.0))
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, 10.0, 0.0,
                                held=False))
        assert _selected_models(c) == [rect]
    finally:
        win.hide()

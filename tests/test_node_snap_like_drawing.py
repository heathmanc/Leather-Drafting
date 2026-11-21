"""A node dragged in node editing snaps the way a point being drawn does.

The report: adjusting a node after double-clicking a line, it didn't always
feel able to snap to nodes. It snapped only to points, nearest first, so a
stitched corner's hole took the node meant for the corner; nothing showed
what it could snap to until it had; it couldn't land on a line between its
points; a curve offered a "middle" off the curve; Shift turned snapping off
and the grid was ignored. Each drag here is made with the mouse, as in the
app, and ends with the pointer a few pixels OFF its target: a node that lands
exactly on it can only have snapped there."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Edge, EditablePath, PathShape, Rectangle,  # noqa: E402
                               Transform)
from leathercad.stitchsettings import StitchSettings  # noqa: E402

MISS = (5, -4)            # px between where the pointer ends and the target


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _line(x0, y0, x1, y1):
    return PathShape(points=[Vec2(x0, y0), Vec2(x1, y1)], close_path=False,
                     layer="Cut")


def _stitched(w, h, x, y):
    return Rectangle(width=w, height=h, transform=Transform(x=x, y=y),
                     stitch=StitchSettings(pitch_mm=3.85, inset=3.5,
                                           hole_style="slit"),
                     layer="Cut")


def _curve():
    """A cubic with both handles 40 up: its middle is at (80, 30); the
    middle of the straight line between its ends, (80, 0), is not on it."""
    return EditablePath(nodes=[Vec2(60, 0), Vec2(100, 0)],
                        edges=[Edge("bezier", c1=Vec2(60, 40),
                                    c2=Vec2(100, 40))],
                        closed=False, layer="Cut")


class _Drawing:
    """A window with line (0,0)-(40,0) and ``others``, its node at (40, 0)
    being edited after a double-click on the line, as in the app."""

    def __init__(self, qapp, others, grid=False):
        from leathercad_app.mainwindow import MainWindow
        self.qapp = qapp
        doc = Document()
        doc.add_shape(_line(0, 0, 40, 0))
        for s in others:
            doc.add_shape(s)
        self.win = MainWindow(doc)
        self.win.resize(1200, 900)
        self.win.show()
        qapp.processEvents()
        c = self.c = self.win.canvas
        c.snap_to_nodes, c.snap_to_grid = True, grid
        c._zoom = 3.0
        c._apply_zoom()
        c.centerOn(QPointF(50, 15))
        qapp.processEvents()
        self.said = []
        c.statusMessage.connect(self.said.append)
        p = self.px(20, 0)                   # the line's middle
        for kind, btns in ((QEvent.MouseButtonPress, Qt.LeftButton),
                           (QEvent.MouseButtonRelease, Qt.NoButton),
                           (QEvent.MouseButtonDblClick, Qt.LeftButton),
                           (QEvent.MouseButtonRelease, Qt.NoButton)):
            self.send(kind, p, Qt.LeftButton, btns)
        assert c._edit_owner is not None, "the double-click didn't edit it"
        self.node = [h for h in c._handles if h.node.index == 1][0]

    def px(self, x, y):
        return self.c.viewportTransform().map(QPointF(x, y))

    def send(self, kind, px, btn, btns, mods=Qt.NoModifier):
        vp = self.c.viewport()
        QApplication.sendEvent(vp, QMouseEvent(kind, px, vp.mapToGlobal(px),
                                               btn, btns, mods))
        self.qapp.processEvents()

    def drag(self, tx, ty, miss=MISS, mods=Qt.NoModifier, h=None):
        """Drag the node towards (tx, ty), the pointer ending ``miss`` px
        off it. Returns where the node landed; ``self.during`` holds what
        showed just before the release."""
        c, h = self.c, h or self.node
        s = self.px(h.pos().x(), h.pos().y())
        e = self.px(tx, ty) + QPointF(*miss)
        self.send(QEvent.MouseButtonPress, s, Qt.LeftButton, Qt.LeftButton,
                  mods)
        for k in range(1, 13):
            self.send(QEvent.MouseMove, s + (e - s) * (k / 12), Qt.NoButton,
                      Qt.LeftButton, mods)
        self.during = {
            "mark": c._snap_marker is not None and c._snap_marker.isVisible(),
            "hints": c._node_hl is not None and c._node_hl.isVisible(),
            "said": self.said[-1] if self.said else None}
        # where the node would be, following the pointer, unsnapped
        r = c.viewportTransform().inverted()[0].map(e)
        self.raw_end = (r.x(), r.y())
        self.send(QEvent.MouseButtonRelease, e, Qt.LeftButton, Qt.NoButton,
                  mods)
        return (round(h.pos().x(), 6), round(h.pos().y(), 6))

    def done(self):
        self.win.hide()


def test_snap_targets_show_while_a_node_is_dragged(qapp):
    d = _Drawing(qapp, [_line(70, 25, 110, 25)])
    assert d.drag(70, 25) == (70.0, 25.0)
    assert d.during == {"mark": True, "hints": True, "said": "endpoint"}
    c = d.c
    # and go with the release
    assert not (c._node_hl is not None and c._node_hl.isVisible())
    assert not c._snap_marker.isVisible()
    assert d.said[-1] == ""
    d.done()


def test_a_stitched_corner_wins_over_its_own_stitch_hole(qapp):
    # the pointer comes at the corner (60, 10) from inside the piece, where
    # its corner hole (63.5, 13.5) is nearer than the corner itself
    d = _Drawing(qapp, [_stitched(40, 30, 80, 25)])
    assert d.drag(60, 10, miss=(7, -7)) == (60.0, 10.0)
    assert d.during["said"] == "endpoint"
    d.done()


def test_a_hole_still_catches_a_node_brought_right_onto_it(qapp):
    d = _Drawing(qapp, [_stitched(40, 30, 80, 25)])
    assert d.drag(63.5, 13.5, miss=(1, -1)) == (63.5, 13.5)
    assert d.during["said"] == "hole centre"
    d.done()


def test_a_line_end_lands_on_another_line_anywhere_along_it(qapp):
    # (70, 10) is no node of the line (60,0)-(100,40), which is y = x - 60
    d = _Drawing(qapp, [_line(60, 0, 100, 40)])
    x, y = d.drag(70, 10)
    assert abs(y - (x - 60)) < 1e-6                # on that line...
    assert abs(x - 70) < 3 and abs(y - 10) < 3     # ...where it was taken
    # ...where the line's own edge from its other end, (0, 0), through the
    # pointer meets it: the line keeps the direction it was dragged to
    rx, ry = d.raw_end
    nx, ny = d.node.pos().x(), d.node.pos().y()    # (not rounded)
    assert (rx, ry) != (nx, ny)
    assert abs(rx * ny - ry * nx) < 1e-6
    assert d.during["said"] == "intersection"
    d.done()


def test_a_curves_middle_is_on_the_curve(qapp):
    from leathercad_app.items import ShapeItem
    ep = _curve()
    item = ShapeItem(ep)
    path = ep.local_path()
    mids = [(round(p.x, 6), round(p.y, 6))
            for p, k in item._geometry_snap_nodes(path, path.flatten())
            if k == "mid"]
    assert mids == [(80.0, 30.0)]
    # and a node dragged there snaps to it; dragged to the empty paper
    # under it, where the old "middle" was, it stays where it's put
    d = _Drawing(qapp, [_curve()])
    assert d.drag(80, 30) == (80.0, 30.0)
    assert d.during["said"] == "midpoint"
    d.done()
    d = _Drawing(qapp, [_curve()])
    x, y = d.drag(80, 0)
    assert (x, y) != (80.0, 0.0) and not d.during["mark"]
    d.done()


def test_shift_still_catches_a_line_it_meets(qapp):
    # level from (40, 0), across an upright line at x = 75
    d = _Drawing(qapp, [_line(75, -20, 75, 20)])
    assert d.drag(75, 0, miss=(5, -2), mods=Qt.ShiftModifier) == (75.0, 0.0)
    assert d.during["said"] == "intersection"
    d.done()


def test_shift_takes_the_length_from_a_node_and_keeps_the_lock(qapp):
    # dragged level towards a line's end above the way: the node stays
    # level with where it started, as far along as that end
    d = _Drawing(qapp, [_line(70, 2, 110, 2)])
    assert d.drag(70, 2, miss=(4, 0), mods=Qt.ShiftModifier) == (70.0, 0.0)
    d.done()


def test_ctrl_leaves_a_node_free(qapp):
    d = _Drawing(qapp, [_line(70, 25, 110, 25)], grid=True)
    x, y = d.drag(70, 25, mods=Qt.ControlModifier)
    assert (x, y) != (70.0, 25.0)
    assert x != round(x) or y != round(y)          # nor on the grid
    assert d.during == {"mark": False, "hints": False, "said": ""}
    d.done()


def test_the_grid_catches_a_node_when_no_point_is_near(qapp):
    d = _Drawing(qapp, [_line(70, 25, 110, 25)], grid=True)
    assert d.drag(52.3, 7.7, miss=(0, 0)) == (52.0, 8.0)
    # a point in reach still beats the grid
    assert d.drag(70.3, 24.6, miss=(0, 0)) == (70.0, 25.0)
    d.done()


def test_a_node_put_somewhere_by_code_goes_exactly_there(qapp):
    d = _Drawing(qapp, [_line(70, 25, 110, 25)], grid=True)
    d.node.setPos(70.3, 24.6)                     # no press: nothing armed
    assert (round(d.node.pos().x(), 6), round(d.node.pos().y(), 6)) == (
        70.3, 24.6)
    d.done()


def test_a_drag_neither_reindexes_the_scene_nor_changes_its_index(qapp):
    d = _Drawing(qapp, [_line(70, 25, 110, 25), _stitched(40, 30, 80, 60)])
    c = d.c
    idx = c._snap_index()
    owner = c._edit_owner
    before = len(idx["owners"][id(owner)]["pts"])
    calls = []
    real = c._snap_index

    def counting():
        calls.append(1)
        return real()
    # every mouse move of the drag: the targets were taken at the press
    c._snap_index = counting
    try:
        h = d.node
        s = d.px(h.pos().x(), h.pos().y())
        e = d.px(70, 25) + QPointF(*MISS)
        d.send(QEvent.MouseButtonPress, s, Qt.LeftButton, Qt.LeftButton)
        calls.clear()
        for k in range(1, 13):
            d.send(QEvent.MouseMove, s + (e - s) * (k / 12), Qt.NoButton,
                   Qt.LeftButton)
        assert calls == []
        d.send(QEvent.MouseButtonRelease, e, Qt.LeftButton, Qt.NoButton)
    finally:
        del c._snap_index
    # the scene's own index still has the edited line in it
    idx = c._snap_index()
    assert id(owner) in idx["owners"]
    assert len(idx["owners"][id(owner)]["pts"]) == before
    d.done()

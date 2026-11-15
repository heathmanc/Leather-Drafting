"""Ending a line or arc exactly where it goes through another outline.

The report (a 128 x 93 mm square and a 3-point arc drawn from inside it):
"as it goes through the top of the square, I would figure I'd be able to
snap at that point". Only an edge's corners, its midpoint and its crossings
with other outlines snapped (and the whole length of a construction line),
so the arc's end could not be put on the square's top edge; it ended
1.22 mm past it. While a line, polyline, measure or arc is being drawn, the
point being placed now snaps to where it crosses an outline near the
pointer, worked out on the outline's true lines and curves."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Circle, EditablePath, Rectangle,  # noqa: E402
                               Transform, arc_through)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _square():
    """The reported square: x -826..-698, y 343..436 (436 is its top)."""
    return Rectangle(width=128, height=93, layer="Cut",
                     transform=Transform(x=-762.0, y=389.5))


def _rig(qapp, *shapes, zoom=3.0, centre=(-795.0, 425.0), grid=False):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    win.resize(1200, 900)
    win.show()
    qapp.processEvents()
    c = win.canvas
    c.snap_to_grid = grid
    for sh in shapes:
        c.doc.add_shape(sh)
    c.rebuild()
    c._zoom = zoom
    c._apply_zoom()
    c.centerOn(QPointF(*centre))
    qapp.processEvents()
    return win, c


def _ev(c, kind, x, y, held=True, mods=Qt.NoModifier):
    vp = QPointF(c.mapFromScene(QPointF(x, y)))
    gp = QPointF(c.viewport().mapToGlobal(vp.toPoint()))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    return QMouseEvent(kind, vp, gp, btn,
                       Qt.LeftButton if held else Qt.NoButton, mods)


def _click(c, x, y):
    c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, x, y))
    c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, x, y, held=False))


def _at(c, x, y):
    """The scene point the pointer is really on (a whole pixel)."""
    return c.mapToScene(c.mapFromScene(QPointF(x, y)))


def _new_arc(c):
    arcs = [s for s in c.doc.shapes if isinstance(s, EditablePath)]
    assert len(arcs) == 1
    a = arcs[0]
    t = a.transform
    return (t.apply(a.nodes[0]), t.apply(a.edges[0].mid), t.apply(a.nodes[1]))


def _draw_arc(c, start, through, end):
    import leathercad_app.canvas as cm
    c.tool = cm.ARC3
    c._multi_pts = []
    _click(c, *start)
    _click(c, *through)
    c.mouseMoveEvent(_ev(c, QEvent.MouseMove, *end))
    snapped = c._smart_snap(_at(c, *end))
    _click(c, *end)
    return snapped


@pytest.mark.parametrize("grid", [False, True])
def test_the_reported_arc_ends_on_the_squares_top_edge(qapp, grid):
    win, c = _rig(qapp, _square(), grid=grid)
    try:
        s, m, e = (-788.0, 411.0), (-794.3, 426.09), (-806.27, 437.22)
        pos, _vtx, _g, kind = _draw_arc(c, s, m, e)
        assert kind == "cross"
        start, thru, end = _new_arc(c)
        assert end.y == pytest.approx(436.0, abs=1e-9)    # right on the edge
        # where the arc it showed crosses the edge: the end is on the
        # circle through the first two points and the pointer
        cur = _at(c, *e)
        cr = arc_through(start, thru, Vec2(cur.x(), cur.y()))
        assert (end - cr[0]).length() == pytest.approx(cr[1], abs=1e-9)
        assert -826.0 < end.x < -698.0
    finally:
        win.hide()


def test_an_arc_end_short_of_the_edge_snaps_onto_it_too(qapp):
    win, c = _rig(qapp, _square())
    try:
        e = (-804.6, 435.1)                      # 0.9 mm below the edge
        _p, _v, _g, kind = _draw_arc(c, (-788.0, 411.0), (-794.3, 426.09), e)
        assert kind == "cross"
        start, thru, end = _new_arc(c)
        assert end.y == pytest.approx(436.0, abs=1e-9)
        # carried on along the arc it showed (lining y up with the square's
        # corners used to put it on the edge too, but off that arc)
        cur = _at(c, *e)
        cr = arc_through(start, thru, Vec2(cur.x(), cur.y()))
        assert (end - cr[0]).length() == pytest.approx(cr[1], abs=1e-9)
    finally:
        win.hide()


def test_an_arc_ends_on_the_true_curve_of_a_round_piece(qapp):
    ring = Circle(rx=30, ry=30, layer="Cut", transform=Transform(x=0, y=0))
    win, c = _rig(qapp, ring, centre=(0.0, 20.0))
    try:
        # from inside the circle out through its rim between quadrants,
        # where the flattened outline sits up to 0.02 mm inside the curve
        pos, _v, _g, kind = _draw_arc(c, (0.0, 5.0), (8.0, 17.0),
                                      (21.6, 21.6))
        assert kind == "cross"
        _s, _t, end = _new_arc(c)
        assert end.length() == pytest.approx(30.0, abs=1e-6)
    finally:
        win.hide()


def test_a_line_ends_where_it_crosses_an_edge(qapp):
    import leathercad_app.canvas as cm
    win, c = _rig(qapp, _square())
    try:
        c.tool = cm.LINE
        _click(c, -790.0, 420.0)                 # its start, inside
        cur = _at(c, -809.7, 437.3)              # just past the top edge
        pos, _v, _g, kind = c._smart_snap(cur)
        assert kind == "cross"
        assert pos.y() == pytest.approx(436.0, abs=1e-9)
        # along the line it showed: start, crossing and pointer in a row
        ax, ay = -790.0, 420.0
        cross = ((pos.x() - ax) * (cur.y() - ay)
                 - (pos.y() - ay) * (cur.x() - ax))
        assert cross == pytest.approx(0.0, abs=1e-6)
        _click(c, cur.x(), cur.y())
        line = [s for s in c.doc.shapes if s is not None and s.kind != "rectangle"]
        assert line
    finally:
        win.hide()


def test_a_corner_still_wins_near_it(qapp):
    import leathercad_app.canvas as cm
    win, c = _rig(qapp, _square(), centre=(-820.0, 430.0))
    try:
        c.tool = cm.LINE
        _click(c, -810.0, 420.0)
        # 1.2 mm from the corner (-826, 436), a hair outside the top edge
        pos, _v, _g, kind = c._smart_snap(_at(c, -825.0, 436.6))
        assert kind == "end"
        assert (pos.x(), pos.y()) == (pytest.approx(-826.0),
                                      pytest.approx(436.0))
    finally:
        win.hide()


def _wedge():
    """A triangle whose long side runs diagonally from (0, 0) to (100, 100),
    so a point beside its middle lines up with none of its corners."""
    from leathercad.shapes import Polygon
    return Polygon(points=[Vec2(0, 0), Vec2(100, 0), Vec2(100, 100)],
                   layer="Cut", transform=Transform(x=0, y=0))


def test_only_the_point_being_drawn_to_snaps_onto_an_edge(qapp):
    import leathercad_app.canvas as cm
    win, c = _rig(qapp, _wedge(), centre=(25.0, 20.0))
    try:
        c.tool = cm.LINE
        # nothing drawn yet: a first point beside the edge stays put
        cur = _at(c, 25.0, 26.0)
        pos, _v, _g, kind = c._smart_snap(cur)
        assert kind is None
        assert (pos.x(), pos.y()) == (cur.x(), cur.y())
        # drawing from below, the line's end lands where it crosses the side
        _click(c, 25.0, -20.0)
        pos, _v, _g, kind = c._smart_snap(cur)
        assert kind == "cross"
        assert pos.x() == pytest.approx(pos.y(), abs=1e-9)        # on y = x
        assert pos.x() == pytest.approx(cur.x(), abs=0.05)        # in line
    finally:
        win.hide()


def test_a_centre_arc_ends_where_its_circle_crosses_an_edge(qapp):
    import leathercad_app.canvas as cm
    post = Rectangle(width=40, height=80, layer="Cut",
                     transform=Transform(x=30.0, y=0.0))   # left side x = 10
    win, c = _rig(qapp, post, centre=(10.0, 10.0))
    try:
        c.tool = cm.ARCCENTER
        c._multi_pts = []
        _click(c, 0.0, 0.0)                      # centre
        _click(c, 20.0, 0.0)                     # start: radius 20
        # near where the circle meets x = 10: (10, 17.3205)
        pos, _v, _g, kind = c._smart_snap(_at(c, 9.4, 17.9))
        assert kind == "cross"
        assert (pos.x(), pos.y()) == (pytest.approx(10.0, abs=1e-9),
                                      pytest.approx(math.sqrt(300.0), abs=1e-9))
    finally:
        win.hide()


def test_shift_lock_crossing_lands_on_the_true_curve(qapp):
    import leathercad_app.canvas as cm
    ring = Circle(rx=30, ry=30, layer="Cut", transform=Transform(x=0, y=0))
    win, c = _rig(qapp, ring, centre=(0.0, 20.0))
    try:
        c.tool = cm.LINE
        c._start = QPointF(0.0, 13.0)

        class _Shift:
            def modifiers(self):
                return Qt.ShiftModifier

        raw = QPointF(27.4, 13.4)                # locks horizontal at y = 13
        pos, applied, kind = c._maybe_ortho(raw, raw, _Shift())
        assert applied and kind == "cross"
        assert pos.y() == pytest.approx(13.0, abs=1e-9)
        assert pos.x() == pytest.approx(math.sqrt(900.0 - 169.0), abs=1e-6)
    finally:
        win.hide()

"""Moving and resizing with construction lines.

The report: "snapping with construction lines doesn't work well. I have a
shape and a construction line grouped, when moving, it snaps to nothing."
A moved piece snapped only to points -- corners, ends, midpoints, centres,
crossings -- so a construction line caught it only at its two ends and its
middle, though the guide says construction lines snap anywhere along their
body while moving too. And a construction line grabbed along its length
snapped by whichever point of it (or of the piece grouped with it) was
nearest the grab, which on a long line could be far from the pointer.

Now a moved piece (or group), and a resize grip, lands on a construction
line anywhere along it when no point is in reach; and a construction line
grabbed away from its ends and middle snaps by the spot grabbed."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication,  # noqa: E402
                               QGraphicsSceneMouseEvent)

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import PathShape, Rectangle, Transform  # noqa: E402

# a pointer position lands on a whole pixel: 1/3 mm at the zoom used here
PX = 0.35


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _rig(qapp, *shapes, zoom=3.0, centre=(60.0, 10.0)):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    win.resize(1200, 900)
    win.show()
    qapp.processEvents()
    c = win.canvas
    c.snap_to_grid = False
    for sh in shapes:
        c.doc.add_shape(sh)
    c.rebuild()
    c._zoom = zoom
    c._apply_zoom()
    c.centerOn(QPointF(*centre))
    qapp.processEvents()
    return win, c


def _guide(a, b, **kw):
    """A construction line from ``a`` to ``b``, placed at its middle."""
    cx, cy = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    return PathShape(points=[Vec2(a[0] - cx, a[1] - cy),
                             Vec2(b[0] - cx, b[1] - cy)],
                     close_path=False, construction=True, layer="Cut",
                     transform=Transform(x=cx, y=cy), **kw)


def _ev(c, kind, x, y, held=True):
    vp = QPointF(c.mapFromScene(QPointF(x, y)))
    gp = QPointF(c.viewport().mapToGlobal(vp.toPoint()))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    return QMouseEvent(kind, vp, gp, btn,
                       Qt.LeftButton if held else Qt.NoButton, Qt.NoModifier)


def _drag(c, grab, path):
    """Press at ``grab``, move through ``path`` (scene points), release at
    the last one. Returns the snap markers shown on the last move as
    (x, y, kind)."""
    shown = []
    orig = c._show_snap_marker

    def record(pt, kind):
        shown.append((pt.x(), pt.y(), kind))
        return orig(pt, kind)

    c._show_snap_marker = record
    try:
        c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, *grab))
        for x, y in path:
            shown.clear()
            c.mouseMoveEvent(_ev(c, QEvent.MouseMove, x, y))
        last = [s for s in shown if s[2]]
        x, y = path[-1]
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, x, y,
                                held=False))
    finally:
        del c._show_snap_marker
    return last


def _towards(grab, end, steps=5):
    """``steps`` pointer positions from ``grab`` (not included) to ``end``."""
    return [(grab[0] + (end[0] - grab[0]) * k / steps,
             grab[1] + (end[1] - grab[1]) * k / steps)
            for k in range(1, steps + 1)]


def test_a_piece_grouped_with_its_guide_lands_on_another_guide(qapp):
    """The report: a piece and a construction line grouped, moved onto a
    guide. Its grabbed corner, let go 0.9 mm short of the guide and 25 mm
    from the guide's middle, used to stay where it was."""
    a = Rectangle(width=60, height=40, layer="Cut", group_id="group1",
                  transform=Transform(0, 0))
    centre_line = _guide((0, -30), (0, 30), group_id="group1")
    rail = _guide((100, -200), (100, 200))
    win, c = _rig(qapp, a, centre_line, rail)
    try:
        grab = (-29.0, 19.0)                   # by its corner (-30, 20)
        shown = _drag(c, grab, _towards(grab, (101.9, 24.0)))
        corner = (a.transform.x - 30.0, a.transform.y + 20.0)
        assert corner[0] == pytest.approx(100.0, abs=1e-9)    # on the guide
        assert corner[1] == pytest.approx(25.0, abs=PX)       # where put
        assert shown and shown[-1][2] == "edge"
        # the group came along as one
        assert centre_line.transform.x == pytest.approx(a.transform.x)
        assert centre_line.transform.y == pytest.approx(a.transform.y)
    finally:
        win.hide()


def test_a_piece_lands_on_a_slanted_guide_anywhere_along_it(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    rail = _guide((60, -60), (160, 40))        # y = x - 120, middle (110, -10)
    win, c = _rig(qapp, a, rail)
    try:
        grab = (-29.0, 19.0)
        # the corner let go at (141, 19.5): 1.06 mm off the line
        _drag(c, grab, _towards(grab, (142.0, 18.5)))
        cx, cy = a.transform.x - 30.0, a.transform.y + 20.0
        assert cx - cy - 120.0 == pytest.approx(0.0, abs=1e-9)
        assert math.hypot(cx - 141.0, cy - 19.5) < 1.06 + PX
    finally:
        win.hide()


def test_a_guide_grabbed_along_it_snaps_by_the_spot_grabbed(qapp):
    """A piece grouped with a long construction line through it, grabbed on
    the line 30 mm from any of the group's points, taken to another piece's
    corner: the line goes through the corner. It used to snap by the
    piece's side midpoint, 30 mm from the pointer -- nothing near the
    pointer ever snapped."""
    a = Rectangle(width=60, height=40, layer="Cut", group_id="group1",
                  transform=Transform(0, 0))
    centre_line = _guide((-100, 0), (100, 0), group_id="group1")
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=150, y=30))      # corner (120, 10)
    win, c = _rig(qapp, a, centre_line, b)
    msgs = []
    c.statusMessage.connect(msgs.append)
    try:
        grab = (60.0, 0.3)
        shown = _drag(c, grab, _towards(grab, (120.4, 11.2)))
        # the spot grabbed, (60, 0) on the line, is on the corner
        assert (centre_line.transform.x, centre_line.transform.y) == (
            pytest.approx(60.0, abs=1e-9), pytest.approx(10.0, abs=1e-9))
        assert (a.transform.x, a.transform.y) == (
            pytest.approx(60.0, abs=1e-9), pytest.approx(10.0, abs=1e-9))
        assert shown and shown[-1][:2] == (pytest.approx(120.0),
                                           pytest.approx(10.0))
        assert any("spot on the construction line" in m for m in msgs)
    finally:
        win.hide()


def test_a_guide_grabbed_near_its_end_snaps_by_its_end(qapp):
    line = _guide((0, 0), (80, 0))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=150, y=30))      # corner (120, 10)
    win, c = _rig(qapp, line, b)
    msgs = []
    c.statusMessage.connect(msgs.append)
    try:
        grab = (79.0, 0.3)                      # 1 mm from its end (80, 0)
        # the end let go at (120.8, 10.9), 1.2 mm from the corner
        _drag(c, grab, _towards(grab, (119.8, 11.2)))
        assert (line.transform.x + 40.0, line.transform.y) == (
            pytest.approx(120.0, abs=1e-9), pytest.approx(10.0, abs=1e-9))
        assert any("Snaps by its end" in m for m in msgs)
    finally:
        win.hide()


def test_a_point_on_the_guide_wins_over_the_guide(qapp):
    """Near a corner that sits on the guide, the moved corner goes onto that
    corner, not onto the guide beside it."""
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    rail = _guide((100, -200), (100, 200))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=130, y=50))      # corner (100, 30)
    win, c = _rig(qapp, a, rail, b)
    try:
        grab = (-29.0, 19.0)
        # let go at (100.9, 31.5): 0.9 mm off the guide, 1.75 mm off the corner
        _drag(c, grab, _towards(grab, (101.9, 30.5)))
        assert (a.transform.x - 30.0, a.transform.y + 20.0) == (
            pytest.approx(100.0, abs=1e-9), pytest.approx(30.0, abs=1e-9))
    finally:
        win.hide()


def test_a_group_does_not_snap_to_its_own_guide(qapp):
    """The group's own construction line moves with it: where it started
    is no target."""
    a = Rectangle(width=60, height=40, layer="Cut", group_id="group1",
                  transform=Transform(0, 0))
    centre_line = _guide((0, -30), (0, 30), group_id="group1")
    win, c = _rig(qapp, a, centre_line)
    try:
        grab = (-29.0, 19.0)
        # the corner let go at (-0.8, 23), 0.8 mm beside where the line was
        _drag(c, grab, _towards(grab, (0.2, 22.0)))
        assert a.transform.x - 30.0 == pytest.approx(-0.8, abs=PX)
    finally:
        win.hide()


def _gev(kind, x, y):
    e = QGraphicsSceneMouseEvent(kind)
    e.setScenePos(QPointF(x, y))
    e.setButton(Qt.LeftButton)
    e.setButtons(Qt.LeftButton)
    e.setModifiers(Qt.NoModifier)
    return e


def _grip_drag(g, to):
    p = g.pos()
    g.mousePressEvent(_gev(QEvent.GraphicsSceneMousePress, p.x(), p.y()))
    for x, y in _towards((p.x(), p.y()), to, steps=3):
        g.mouseMoveEvent(_gev(QEvent.GraphicsSceneMouseMove, x, y))
    g.mouseReleaseEvent(_gev(QEvent.GraphicsSceneMouseRelease, *to))


def _grips(c, items):
    from leathercad_app.items import ResizeHandle
    c.scene_obj.clearSelection()
    for it in items:
        it.setSelected(True)
    c.selection_changed()
    return {g.grip: g for g in c.scene_obj.items()
            if isinstance(g, ResizeHandle)}


def _item(c, model):
    from leathercad_app.items import ShapeItem
    return next(it for it in c.scene_obj.items()
                if isinstance(it, ShapeItem) and it.model is model)


def test_a_resize_grip_lands_on_a_guide(qapp):
    r = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    rail = _guide((50, -200), (50, 200))       # middle (50, 0)
    win, c = _rig(qapp, r, rail)
    try:
        grips = _grips(c, [_item(c, r)])
        # the right side's grip (30, 0) to (49.2, 5): 0.8 mm short of the
        # guide, 5 mm from its middle
        _grip_drag(grips[(1, 0)], (49.2, 5.0))
        assert r.width == pytest.approx(80.0, abs=1e-9)   # right side at 50
    finally:
        win.hide()


def test_a_group_resize_does_not_snap_to_the_groups_own_guide(qapp):
    """A group's resize stretches its construction line with it, so where
    the line started is no target for the grip."""
    r = Rectangle(width=60, height=40, layer="Cut", group_id="group1",
                  transform=Transform(0, 0))
    centre_line = _guide((10, -30), (10, 30), group_id="group1")
    win, c = _rig(qapp, r, centre_line)
    try:
        grips = _grips(c, [_item(c, r), _item(c, centre_line)])
        assert c._resize_group                   # resizing the group
        # the right side's grip (30, 0) to (10.8, 5): 0.8 mm beside where
        # the line was
        _grip_drag(grips[(1, 0)], (10.8, 5.0))
        assert r.width == pytest.approx(40.8, abs=1e-6)
    finally:
        win.hide()

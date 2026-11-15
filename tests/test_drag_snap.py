"""Snapping while dragging a shape: the point you grabbed snaps, onto the
target nearest it.

The report: dragging a shape, "it's very random as to what gets snapped to
what". Every node of the dragged shape (each corner, edge midpoint, centre)
was tried against every target on each mouse move, and the shape jumped to
whichever pair happened to line up -- a rectangle held by its top-left
corner snapped its far corner onto something 50-70 mm from the pointer.
Now only the dragged shape's node nearest where it was grabbed snaps (round
things by their centre, unless grabbed right on a rim point)."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.shapes import Circle, Rectangle, Transform  # noqa: E402


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


def _ev(c, kind, x, y, held=True, mods=Qt.NoModifier):
    vp = QPointF(c.mapFromScene(QPointF(x, y)))
    gp = QPointF(c.viewport().mapToGlobal(vp.toPoint()))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    return QMouseEvent(kind, vp, gp, btn,
                       Qt.LeftButton if held else Qt.NoButton, mods)


def _drag(c, grab, path, spy=None):
    """Press at ``grab``, move through ``path`` (scene points), release at
    the last one. ``spy`` collects (pointer, snapped target) per move."""
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
            if spy is not None:
                hit = [s for s in shown if s[2] == "end"]
                spy.append(((x, y), hit[-1][:2] if hit else None))
        x, y = path[-1]
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, x, y,
                                held=False))
    finally:
        del c._show_snap_marker


def test_a_shape_held_by_a_corner_never_snaps_far_from_the_pointer(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=110, y=2))
    win, c = _rig(qapp, a, b)
    try:
        grab = (-29.0, 19.0)                  # by its top-left corner
        spy = []
        _drag(c, grab, [(grab[0] + 1.5 * k, grab[1] + 0.05 * k)
                        for k in range(1, 61)], spy)
        # it used to snap its far corners onto b, 47-73 mm from the pointer
        far = [(p, t) for p, t in spy
               if t is not None and math.hypot(t[0] - p[0], t[1] - p[1]) > 6]
        assert far == []
    finally:
        win.hide()


def test_the_grabbed_corner_lands_on_the_target_near_it(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=110, y=2))
    win, c = _rig(qapp, a, b)
    try:
        grab = (-29.0, 19.0)
        # bring the grabbed top-left corner (-30, 20) to ~1 mm of b's
        # top-left corner (80, 22)
        _drag(c, grab, [(grab[0] + 22.0 * k, grab[1] + 0.6 * k)
                        for k in range(1, 6)])
        assert (a.transform.x - 30.0, a.transform.y + 20.0) == (
            pytest.approx(80.0), pytest.approx(22.0))
    finally:
        win.hide()


def test_grabbing_near_a_midpoint_snaps_by_that_midpoint(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=110, y=2))
    win, c = _rig(qapp, a, b)
    msgs = []
    c.statusMessage.connect(msgs.append)
    try:
        grab = (30.0, 1.0)                    # a's right edge, by its middle
        # right midpoint (30, 0) to ~1 mm of b's left midpoint (80, 2)
        _drag(c, grab, [(grab[0] + 10.0 * k, grab[1] + 0.6 * k)
                        for k in range(1, 6)])
        assert (a.transform.x + 30.0, a.transform.y) == (
            pytest.approx(80.0), pytest.approx(2.0))
        assert any("Snaps by its midpoint" in m for m in msgs)
    finally:
        win.hide()


def test_a_circle_snaps_by_its_centre_unless_grabbed_on_a_rim_point(qapp):
    ring = Circle(rx=10, ry=10, layer="Cut", transform=Transform(0, 0))
    post = Rectangle(width=20, height=20, layer="Cut",
                     transform=Transform(x=60, y=0))   # corner at (50, 10)
    win, c = _rig(qapp, ring, post, centre=(30.0, 0.0))
    try:
        # grabbed on the rim at 45 degrees -- not near a quarter point
        g = (10 * math.cos(math.pi / 4), 10 * math.sin(math.pi / 4))
        _drag(c, g, [(g[0] + 10.0 * k, g[1] + 2.0 * k) for k in range(1, 6)])
        assert (ring.transform.x, ring.transform.y) == (
            pytest.approx(50.0), pytest.approx(10.0))    # centre on the corner
    finally:
        win.hide()
    ring = Circle(rx=10, ry=10, layer="Cut", transform=Transform(0, 0))
    post = Rectangle(width=20, height=20, layer="Cut",
                     transform=Transform(x=60, y=0))
    win, c = _rig(qapp, ring, post, centre=(30.0, 0.0))
    try:
        g = (10.0, 0.3)                       # right on its right rim point
        # right quarter point (10, 0) to ~1 mm of the corner (50, 10)
        _drag(c, g, [(g[0] + 8.0 * k, g[1] + 1.8 * k) for k in range(1, 6)])
        assert (ring.transform.x + 10.0, ring.transform.y) == (
            pytest.approx(50.0), pytest.approx(10.0))
    finally:
        win.hide()


def test_a_moved_group_snaps_by_its_point_nearest_the_grab(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", group_id="group1",
                  transform=Transform(0, 0))
    tag = Rectangle(width=10, height=6, layer="Cut", group_id="group1",
                    transform=Transform(x=0, y=-30))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=110, y=2))
    win, c = _rig(qapp, a, tag, b)
    try:
        grab = (-29.0, 19.0)
        spy = []
        _drag(c, grab, [(grab[0] + 1.5 * k, grab[1] + 0.05 * k)
                        for k in range(1, 61)], spy)
        far = [(p, t) for p, t in spy
               if t is not None and math.hypot(t[0] - p[0], t[1] - p[1]) > 6]
        assert far == []
        # the label came along
        assert tag.transform.x - a.transform.x == pytest.approx(0.0)
        assert tag.transform.y - a.transform.y == pytest.approx(-30.0)
    finally:
        win.hide()


def test_the_snapping_point_is_marked_while_dragging(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    win, c = _rig(qapp, a)
    try:
        grab = (-29.0, 19.0)
        c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, *grab))
        c.mouseMoveEvent(_ev(c, QEvent.MouseMove, grab[0] + 9, grab[1] - 7))
        m = c._src_marker
        assert m is not None and m.isVisible()
        mid = m.path().boundingRect().center()
        assert (mid.x(), mid.y()) == (pytest.approx(a.transform.x - 30, abs=0.1),
                                      pytest.approx(a.transform.y + 20, abs=0.1))
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, grab[0] + 9,
                                grab[1] - 7, held=False))
        assert not m.isVisible()
    finally:
        win.hide()


def test_with_node_snap_off_nothing_snaps(qapp):
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 0))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(x=110, y=2))
    win, c = _rig(qapp, a, b)
    c.snap_to_nodes = False
    try:
        grab = (-29.0, 19.0)
        _drag(c, grab, [(grab[0] + 22.0 * k, grab[1] + 0.6 * k)
                        for k in range(1, 6)])
        # a free drag: the corner goes exactly where the pointer took it,
        # 1 mm off b's corner (snapped, it would sit on y = 22)
        assert a.transform.y + 20.0 == pytest.approx(23.0, abs=0.35)
    finally:
        win.hide()


# The reported pieces (outline only, rounded, moved to the origin): a strip
# with a tab on its right, and a piece with a shallow notch turned 180
# degrees beside it, whose top-left corner sits 39 mm to the right of the
# strip's top-right corner and 0.9 mm higher.
_TAB_NODES = [(2.386, -50.533), (2.386, -41.236), (2.390, -40.849),
              (5.284, -36.411), (38.886, -20.816), (38.886, 20.816),
              (5.284, 36.411), (2.390, 40.849), (2.386, 41.236),
              (2.386, 50.533), (-13.864, 50.533), (-13.864, 41.236),
              (-12.542, 34.087), (-12.542, -34.087), (-13.864, -41.236),
              (-13.864, -50.533)]
_TAB_ARCS = {1: (2.387, -41.043), 2: (3.201, -38.215), 6: (3.201, 38.215),
             7: (2.387, 41.043), 11: (-13.531, 37.601), 12: (-6.243, 0.0),
             13: (-13.531, -37.601)}
_NOTCH_NODES = [(20.626, 50.533), (20.626, 41.236), (19.305, 34.087),
                (19.305, -34.087), (20.626, -41.236), (20.626, -50.533),
                (-23.124, -50.533), (-33.657, -40.0), (-33.657, 40.0),
                (-23.124, 50.533)]
_NOTCH_ARCS = {1: (20.293, 37.601), 2: (13.005, 0.0), 3: (20.293, -37.601),
               6: (-30.572, -47.448), 8: (-30.572, 47.448)}


def _editpath(nodes, arcs, **kw):
    from leathercad.geometry import Vec2
    from leathercad.shapes import Edge, EditablePath
    edges = [Edge(kind="arc", mid=Vec2(*arcs[i])) if i in arcs
             else Edge(kind="line") for i in range(len(nodes))]
    return EditablePath(nodes=[Vec2(*p) for p in nodes], edges=edges,
                        closed=True, layer="Cut", **kw)


def test_the_reported_pieces_snap_corner_to_corner(qapp):
    """Held by its top-right corner and dragged right, the strip's corner
    lands on the notched piece's top-left corner. Every point of the strip
    used to be tried: its tab latched onto the notch a few millimetres into
    the drag, and the corner never snapped at all."""
    from leathercad.geometry import Vec2
    tab = _editpath(_TAB_NODES, _TAB_ARCS, transform=Transform(0, 0))
    notch = _editpath(_NOTCH_NODES, _NOTCH_ARCS,
                      transform=Transform(x=62.018, y=0.889, rotation=180.0))
    target = notch.transform.apply(Vec2(20.626, -50.533))
    for zoom in (1.5, 3.0, 6.0):
        win, c = _rig(qapp, tab, notch, zoom=zoom, centre=(25.0, 30.0))
        try:
            start = tab.transform.apply(Vec2(2.386, 50.533))
            spy = []
            _drag(c, (start.x, start.y),
                  [(start.x + 0.5 * k, start.y + 0.012 * k)
                   for k in range(1, 81)], spy)
            snaps = [t for _p, t in spy if t is not None]
            assert snaps, zoom                       # it did snap...
            assert all(math.hypot(t[0] - target.x, t[1] - target.y) < 1e-6
                       for t in snaps), zoom         # ...only onto the corner
            # ...and it was let go there (the last move was within reach)
            corner = tab.transform.apply(Vec2(2.386, 50.533))
            assert math.hypot(corner.x - target.x, corner.y - target.y) < 1.0
        finally:
            win.hide()
        tab.transform.x, tab.transform.y = 0.0, 0.0

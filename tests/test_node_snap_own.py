"""Node editing snaps to the edited shape's own other nodes, not just to other
shapes -- but never onto the nodes a drag would collapse onto.

The report: adjusting an arc, its middle node wouldn't snap to other nodes.
Node snapping skipped the whole shape being edited (so a node wouldn't stick
to its neighbours); a bookmark welded into one outline by its fillets had
nothing left to snap to."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Edge, EditablePath, PathShape  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _editing(qapp, shape):
    from leathercad_app.items import ShapeItem
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(shape)
    win = MainWindow(doc)
    win.resize(1000, 800)
    win.show()
    qapp.processEvents()
    c = win.canvas
    c.snap_to_nodes, c.snap_to_grid = True, False
    c._zoom = 8.0
    c._apply_zoom()
    c.centerOn(QPointF(0, 0))
    it = [i for i in c.scene_obj.items()
          if isinstance(i, ShapeItem) and i.model is shape][0]
    c.scene_obj.clearSelection()
    it.setSelected(True)
    c.enter_vertex_edit(it)
    qapp.processEvents()
    return win, c


def _pos(h):
    return (round(h.pos().x(), 6), round(h.pos().y(), 6))


def _rounded_rect():
    ep = EditablePath.from_rounded_rect(80, 50, 10)
    ep.layer = "Cut"
    return ep


def test_arc_middle_node_snaps_to_its_own_outlines_other_nodes(qapp):
    win, c = _editing(qapp, _rounded_rect())
    mids = [h for h in c._handles if h.node.is_mid]
    a, b = mids[0], mids[2]                        # opposite corners
    # drag with the mouse, as in the app
    vpw = c.viewport()

    def send(kind, x, y, btn=Qt.LeftButton, btns=Qt.LeftButton):
        pt = QPointF(c.mapFromScene(QPointF(x, y)))
        QApplication.sendEvent(vpw, QMouseEvent(kind, pt, vpw.mapToGlobal(pt),
                                                btn, btns, Qt.NoModifier))
        qapp.processEvents()
    x0, y0 = _pos(a)
    x1, y1 = _pos(b)
    send(QEvent.MouseButtonPress, x0, y0)
    for t in (0.25, 0.5, 0.75, 1.0):
        send(QEvent.MouseMove, x0 + (x1 + 0.3 - x0) * t,
             y0 + (y1 - 0.3 - y0) * t, Qt.NoButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, x1 + 0.3, y1 - 0.3, Qt.LeftButton,
         Qt.NoButton)
    assert _pos(a) == _pos(b)                      # onto the other arc's middle
    win.hide()


def test_arc_middle_node_snaps_to_a_corner_node_but_not_its_own_ends(qapp):
    ep = _rounded_rect()
    win, c = _editing(qapp, ep)
    mid = [h for h in c._handles if h.node.is_mid][0]
    j = mid.node.edge
    ends = {j, (j + 1) % len(ep.nodes)}
    own_end = [h for h in c._handles if h.node.index in ends][0]
    far = [h for h in c._handles if h.node.index is not None
           and h.node.index not in ends][-1]
    c.begin_node_snap(mid)
    x, y = _pos(far)
    got = c.snap_node(QPointF(x + 0.3, y - 0.3))
    assert (round(got.x(), 6), round(got.y(), 6)) == (x, y)
    x, y = _pos(own_end)                           # would flatten the arc
    got = c.snap_node(QPointF(x + 0.3, y - 0.3))
    assert (round(got.x(), 6), round(got.y(), 6)) == (round(x + 0.3, 6),
                                                      round(y - 0.3, 6))
    c.end_node_snap()
    win.hide()


def test_open_paths_end_snaps_onto_its_own_start(qapp):
    line = PathShape(points=[Vec2(-30, -20), Vec2(30, -20), Vec2(30, 20),
                             Vec2(-29, 19)], close_path=False, layer="Cut")
    win, c = _editing(qapp, line)
    last = [h for h in c._handles if h.node.index == 3][0]
    c.begin_node_snap(last)
    got = c.snap_node(QPointF(-29.6, -19.7))
    assert (round(got.x(), 6), round(got.y(), 6)) == (-30.0, -20.0)
    c.end_node_snap()
    win.hide()


def test_a_node_still_ignores_its_neighbours_and_their_arcs(qapp):
    ep = _rounded_rect()
    win, c = _editing(qapp, ep)
    n = len(ep.nodes)
    node = [h for h in c._handles if h.node.index == 0][0]
    nxt = [h for h in c._handles if h.node.index == 1][0]
    arcs = [h for h in c._handles if h.node.is_mid
            and h.node.edge in (n - 1, 0)]          # either side of node 0
    c.begin_node_snap(node)
    for h in [nxt] + arcs:
        x, y = _pos(h)
        got = c.snap_node(QPointF(x + 0.3, y - 0.3))
        assert (round(got.x(), 6), round(got.y(), 6)) == (round(x + 0.3, 6),
                                                          round(y - 0.3, 6))
    c.end_node_snap()
    win.hide()


def test_bezier_handles_still_move_freely(qapp):
    ep = EditablePath(nodes=[Vec2(-30, 0), Vec2(30, 0)],
                      edges=[Edge("bezier", c1=Vec2(-10, 20), c2=Vec2(10, 20))],
                      closed=False, layer="Cut")
    win, c = _editing(qapp, ep)
    ctrl = [h for h in c._handles if h.node.is_ctrl][0]
    assert c._own_node_targets(ctrl) == []
    win.hide()

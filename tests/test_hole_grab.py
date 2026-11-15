"""Picking a piece up by one of its own holes.

The report: "i can't select a hole in a group and move it. i'd like its
center to be used as a snap point". Only a piece's outline was clickable --
its holes (attached, or worked out from its stitching) were no part of its
click area -- so a press on a hole started a rubber band instead, and a
piece could never be dragged, or snapped, by a hole. A click on one of the
piece's holes now picks up the piece, and while it is dragged that hole's
centre is the point that snaps. A box drawn over the holes still doesn't
take the piece."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.holes import LooseHole  # noqa: E402
from leathercad.shapes import PathShape, Rectangle, Transform  # noqa: E402
from leathercad.stitching import Hole  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402

_ROW = (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _piece(x=0.0, y=0.0, holes="baked", **kw):
    """A 40 x 30 piece with stitching; ``baked`` holes are a row along its
    edge at y = 11.5 (as after Attach holes), ``stitched`` ones are worked
    out from its stitch settings."""
    sh = Rectangle(width=40, height=30, layer="Cut",
                   transform=Transform(x=x, y=y), **kw)
    sh.stitch = StitchSettings(pitch_mm=5.0, inset=3.5, hole_diameter=1.0)
    if holes == "baked":
        sh.baked_holes = [Hole(Vec2(hx, 11.5), Vec2(1, 0)) for hx in _ROW]
    return sh


def _post(x=60.0, y=0.0, **kw):
    """A 20 x 20 square; at the default place its top-left corner is
    (50, 10)."""
    return Rectangle(width=20, height=20, layer="Cut",
                     transform=Transform(x=x, y=y), **kw)


def _rig(qapp, *shapes, holes=(), zoom=3.0, centre=(30.0, 0.0)):
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
    c.doc.holes.extend(holes)
    c.rebuild()
    c._zoom = zoom
    c._apply_zoom()
    c.centerOn(QPointF(*centre))
    qapp.processEvents()
    return win, c


def _item(c, shape):
    from leathercad_app.items import ShapeItem
    return next(it for it in c.scene_obj.items()
                if isinstance(it, ShapeItem) and it.model is shape)


def _ev(c, kind, x, y, held=True, mods=Qt.NoModifier):
    vp = QPointF(c.mapFromScene(QPointF(x, y)))
    gp = QPointF(c.viewport().mapToGlobal(vp.toPoint()))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    return QMouseEvent(kind, vp, gp, btn,
                       Qt.LeftButton if held else Qt.NoButton, mods)


def _click(c, x, y):
    c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, x, y))
    c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, x, y, held=False))


def _drag(c, grab, path):
    c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, *grab))
    for x, y in path:
        c.mouseMoveEvent(_ev(c, QEvent.MouseMove, x, y))
    c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, *path[-1],
                            held=False))


def _toward(grab, goal, steps=5, short=(0.8, 0.3)):
    """Mouse moves from ``grab`` to just short of ``goal`` (by ``short``):
    close enough to snap, not on it."""
    gx, gy = goal[0] - short[0], goal[1] - short[1]
    return [(grab[0] + (gx - grab[0]) * k / steps,
             grab[1] + (gy - grab[1]) * k / steps) for k in range(1, steps + 1)]


@pytest.mark.parametrize("holes", ["baked", "stitched"])
def test_a_click_on_one_of_a_pieces_holes_selects_the_piece(qapp, holes):
    piece = _piece(holes=holes)
    win, c = _rig(qapp, piece, centre=(0.0, 0.0))
    try:
        it = _item(c, piece)
        assert it._holes and it._holes.count
        h = it._holes.holes[len(it._holes.holes) // 2].point
        _click(c, h.x + 0.2, h.y - 0.2)          # on the hole, a hair off centre
        assert c.selected_items() == [it]
        c.scene_obj.clearSelection()
        _click(c, 0.0, 0.0)                      # the bare middle still isn't
        assert c.selected_items() == []
    finally:
        win.hide()


def test_dragging_a_piece_by_a_hole_snaps_that_holes_centre(qapp):
    piece, post = _piece(), _post()
    win, c = _rig(qapp, piece, post)
    msgs = []
    c.statusMessage.connect(msgs.append)
    try:
        grab = (15.0, 11.5)                      # the row's last hole
        c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, *grab))
        assert c.selected_items() == [_item(c, piece)]
        assert c._move_src_kind == "hole"
        assert (c._move_src.x, c._move_src.y) == (15.0, 11.5)
        path = _toward(grab, (50.0, 10.0))       # to ~1 mm of the corner
        for x, y in path:
            c.mouseMoveEvent(_ev(c, QEvent.MouseMove, x, y))
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, *path[-1],
                                held=False))
        # the hole's centre sits right on the corner
        assert (piece.transform.x + 15.0, piece.transform.y + 11.5) == (
            pytest.approx(50.0), pytest.approx(10.0))
        assert any("centre of the hole you grabbed" in m for m in msgs)
    finally:
        win.hide()


def test_a_hole_lands_on_another_pieces_hole(qapp):
    # lining up the stitch holes of two pieces, hole on hole
    a = _piece()
    b = _piece(x=70.0, y=0.8)                    # its first hole: (55, 12.3)
    win, c = _rig(qapp, a, b)
    try:
        grab = (15.0, 11.5)
        _drag(c, grab, _toward(grab, (55.0, 12.3), short=(0.7, 0.4)))
        assert (a.transform.x + 15.0, a.transform.y + 11.5) == (
            pytest.approx(55.0), pytest.approx(12.3))
    finally:
        win.hide()


def test_held_by_its_outline_a_piece_still_snaps_by_its_corner(qapp):
    piece, post = _piece(), _post(x=70.0, y=2.0)   # corner (60, 12)
    win, c = _rig(qapp, piece, post)
    try:
        grab = (19.5, 14.5)                       # by its corner (20, 15)
        _drag(c, grab, _toward(grab, (60.0, 12.0)))
        assert (piece.transform.x + 20.0, piece.transform.y + 15.0) == (
            pytest.approx(60.0), pytest.approx(12.0))
    finally:
        win.hide()


def test_a_grouped_piece_is_dragged_by_its_hole(qapp):
    piece = _piece(group_id="group1")
    tag = Rectangle(width=10, height=6, layer="Cut", group_id="group1",
                    transform=Transform(x=0, y=-30))
    post = _post()
    win, c = _rig(qapp, piece, tag, post)
    try:
        grab = (15.0, 11.5)
        c.mousePressEvent(_ev(c, QEvent.MouseButtonPress, *grab))
        assert set(c.selected_items()) == {_item(c, piece), _item(c, tag)}
        assert c._move_src_kind == "hole"
        path = _toward(grab, (50.0, 10.0))
        for x, y in path:
            c.mouseMoveEvent(_ev(c, QEvent.MouseMove, x, y))
        c.mouseReleaseEvent(_ev(c, QEvent.MouseButtonRelease, *path[-1],
                                held=False))
        assert (piece.transform.x + 15.0, piece.transform.y + 11.5) == (
            pytest.approx(50.0), pytest.approx(10.0))
        # the rest of the group came along, unchanged
        assert (tag.transform.x - piece.transform.x,
                tag.transform.y - piece.transform.y) == (
            pytest.approx(0.0), pytest.approx(-30.0))
    finally:
        win.hide()


def test_loose_holes_grouped_with_a_piece_still_drag_by_their_centre(qapp):
    piece = _piece(holes="none", group_id="group1")
    piece.stitch = None
    loose = [LooseHole(point=Vec2(hx, 11.5), hole_diameter=1.0,
                       group_id="group1") for hx in _ROW]
    win, c = _rig(qapp, piece, _post(), holes=loose)
    try:
        grab = (15.0, 11.5)
        _drag(c, grab, _toward(grab, (50.0, 10.0)))
        last = max(c.doc.holes, key=lambda h: h.point.x)
        assert (last.point.x, last.point.y) == (pytest.approx(50.0),
                                                pytest.approx(10.0))
        assert piece.transform.x == pytest.approx(35.0)
    finally:
        win.hide()


def test_a_box_over_a_pieces_holes_does_not_take_the_piece(qapp):
    piece = _piece()
    win, c = _rig(qapp, piece, centre=(0.0, 0.0))
    try:
        # a box over five of its holes, clear of the outline
        area = QPainterPath()
        area.addRect(QRectF(-11.0, 8.0, 22.0, 4.5))
        c.scene_obj.setSelectionArea(area, mode=Qt.IntersectsItemShape)
        assert c.selected_items() == []
        # the same with the mouse: press in the bare middle, drag over them
        _drag(c, (-11.0, 8.0), [(-5.0, 10.0), (0.0, 11.5), (11.0, 12.5)])
        assert c.selected_items() == []
        # a box that reaches the outline still takes it
        _drag(c, (-11.0, 8.0), [(0.0, 12.0), (11.0, 17.0)])
        assert c.selected_items() == [_item(c, piece)]
    finally:
        win.hide()


def test_the_nearest_of_a_hole_and_a_line_takes_the_click(qapp):
    piece = _piece()
    guide = PathShape(points=[Vec2(-10, 0), Vec2(10, 0)], close_path=False,
                      construction=True, layer="Cut",
                      transform=Transform(x=0, y=12.5))   # 1 mm off the row
    win, c = _rig(qapp, piece, guide, centre=(0.0, 10.0))
    try:
        _click(c, -5.0, 11.5)                    # a hole's centre
        assert c.selected_items() == [_item(c, piece)]
        c.scene_obj.clearSelection()
        _click(c, -5.0, 12.5)                    # the line, over that hole
        assert c.selected_items() == [_item(c, guide)]
    finally:
        win.hide()

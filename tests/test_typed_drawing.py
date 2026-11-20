"""Drawing as in Fusion: sizes typed in boxes beside the cursor while
drawing, tools that stay on, lines that chain into one path, a right-click
Finish / Cancel, and Ctrl to place a point without snapping."""

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.shapes import (Circle, EditablePath, PathShape,  # noqa: E402
                               Polygon, Rectangle, Transform)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Rig:
    """A shown main window with the view on the drawing, snapping off; the
    mouse and keys go through Qt as a user's do (key shortcuts included)."""

    def __init__(self, doc=None):
        from leathercad_app.mainwindow import MainWindow
        self.win = MainWindow(doc or Document())
        self.win.resize(1300, 850)
        self.win.show()
        self.win.activateWindow()
        QApplication.processEvents()
        c = self.c = self.win.canvas
        c._zoom = 1.5
        c._apply_zoom()
        c.centerOn(QPointF(150.0, 0.0))
        c.snap_to_grid = c.snap_to_nodes = False
        c.drag_to_draw = False
        QApplication.processEvents()

    @property
    def doc(self):
        return self.win.doc

    def at(self, x, y):
        return self.c.mapFromScene(QPointF(x, y))

    def scene(self, x, y):
        """Where the pointer really is for (x, y): on a whole pixel."""
        return self.c.mapToScene(self.at(x, y))

    def tool(self, mode):
        self.win._select_mode(mode)
        QApplication.processEvents()

    def move(self, x, y, mods=Qt.NoModifier):
        vp = self.c.viewport()
        pt = QPointF(self.at(x, y))
        QApplication.sendEvent(vp, QMouseEvent(
            QEvent.MouseMove, pt, vp.mapToGlobal(pt), Qt.NoButton,
            Qt.NoButton, mods))
        QApplication.processEvents()

    def click(self, x, y, mods=Qt.NoModifier):
        self.move(x, y, mods)
        QTest.mouseClick(self.c.viewport(), Qt.LeftButton, mods,
                         self.at(x, y))
        QApplication.processEvents()

    def type(self, text):
        QTest.keyClicks(self.c, text)
        QApplication.processEvents()

    def key(self, k, mods=Qt.NoModifier):
        QTest.keyClick(self.c, k, mods)
        QApplication.processEvents()

    def close(self):
        self.win.hide()


@pytest.fixture
def rig(qapp):
    r = Rig()
    yield r
    r.close()


def _ends(shape):
    return [(p.x, p.y) for p in shape.world_polyline()[0]]


def test_a_line_takes_a_typed_length_and_angle(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.LINE)
    rig.click(0, 0)
    assert rig.c._vb.keys() == ["len", "ang"] and rig.c._vb.isVisible()
    rig.type("50")
    rig.key(Qt.Key_Tab)
    rig.type("30")
    rig.key(Qt.Key_Return)                     # places the point
    rig.key(Qt.Key_Return)                     # nothing typed: ends the line
    line, = rig.doc.shapes
    assert isinstance(line, PathShape) and not line.close_path
    (x0, y0), (x1, y1) = _ends(line)
    assert (x0, y0) == (pytest.approx(0.0, abs=1e-9),
                        pytest.approx(0.0, abs=1e-9))
    assert x1 == pytest.approx(50 * math.cos(math.radians(30)), abs=1e-9)
    assert y1 == pytest.approx(25.0, abs=1e-9)
    assert rig.c.tool == cm.LINE               # stays on


def test_a_kept_length_holds_while_the_mouse_turns_the_line(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.LINE)
    rig.click(10, 10)
    start = rig.c._poly_pts[0]
    rig.type("40")
    rig.key(Qt.Key_Tab)                        # kept: the angle box is lit
    assert rig.c._vb.active == 1
    rig.click(10, 90)                          # straight up, much further
    rig.key(Qt.Key_Return)
    (x0, y0), (x1, y1) = _ends(rig.doc.shapes[-1])
    assert math.hypot(x1 - x0, y1 - y0) == pytest.approx(40.0, abs=1e-9)
    up = rig.scene(10, 90)
    ang = math.atan2(up.y() - start.y(), up.x() - start.x())
    assert math.atan2(y1 - y0, x1 - x0) == pytest.approx(ang, abs=1e-9)


def test_a_kept_angle_takes_the_mouse_length_along_it(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.LINE)
    rig.click(0, 0)
    start = rig.c._poly_pts[0]
    rig.key(Qt.Key_Tab)
    rig.type("90")
    rig.key(Qt.Key_Tab)
    rig.click(30, 40)                          # 40 up, 30 across
    rig.key(Qt.Key_Escape)                     # Esc ends the line, kept
    (x0, y0), (x1, y1) = _ends(rig.doc.shapes[-1])
    at = rig.scene(30, 40)
    assert x1 == pytest.approx(x0, abs=1e-9)   # straight up
    assert y1 - y0 == pytest.approx(at.y() - start.y(), abs=1e-9)


def test_lines_chain_into_one_path_and_close_into_a_stitched_piece(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.LINE)
    for x, y in ((0, 0), (60, 0), (60, 40)):
        rig.click(x, y)
    assert len(rig.c._poly_pts) == 3 and rig.doc.shapes == []
    rig.click(0.3, 0.2)                        # back on the start: closes
    piece, = rig.doc.shapes
    assert isinstance(piece, Polygon) and piece.close_path
    assert len(piece.points) == 3
    assert piece.stitch is not None and piece.stitch.enabled
    # an open line: Esc ends it with every point it had
    for x, y in ((100, 0), (150, 0), (150, 40), (200, 40)):
        rig.click(x, y)
    rig.key(Qt.Key_Escape)
    line = rig.doc.shapes[-1]
    assert isinstance(line, PathShape) and not line.close_path
    assert len(line.points) == 4 and line.stitch is None
    assert rig.c.tool == cm.LINE
    rig.key(Qt.Key_Escape)                     # nothing in progress: Select
    assert rig.c.tool == cm.SELECT


def test_a_line_s_two_points_still_make_the_line_properties_knows(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.LINE)
    rig.click(0, 0)
    rig.click(30, 40)
    rig.key(Qt.Key_Return)
    line = rig.doc.shapes[-1]
    assert isinstance(line, PathShape) and len(line.points) == 2
    QApplication.processEvents()
    assert rig.win.properties.g_line.isVisibleTo(rig.win.properties)


def test_numbers_and_backspace_mid_draw_go_into_the_boxes(rig):
    # "25" switched to the 2-point circle and then the centre arc and the
    # line was lost; Backspace deleted the shape drawn before
    from leathercad_app import canvas as cm
    rig.tool(cm.RECT)
    rig.click(200, 0)
    rig.click(240, 30)
    assert len(rig.doc.shapes) == 1            # selected, as a new shape is
    rig.tool(cm.LINE)
    rig.click(0, 100)
    rig.type("25")
    assert rig.c.tool == cm.LINE and rig.c._vb.fields[0].text == "25"
    assert len(rig.c._poly_pts) == 1
    for _ in range(3):
        rig.key(Qt.Key_Backspace)
    rig.key(Qt.Key_Delete)
    assert len(rig.doc.shapes) == 1            # the rectangle is still there
    assert rig.c._vb.fields[0].text is None
    rig.type("mm")                             # "M" is the seam tool's key
    assert rig.c.tool == cm.LINE
    rig.key(Qt.Key_Escape)                     # drops the typing
    rig.key(Qt.Key_Escape)                     # one point: cancelled
    assert rig.c._poly_pts == [] and rig.c.tool == cm.LINE
    rig.type("2")                              # nothing drawn: a shortcut
    assert rig.c.tool == cm.CIRCLE2


def test_a_box_takes_width_and_height_towards_the_mouse(rig):
    from leathercad_app import canvas as cm
    for mode in (cm.RECT, cm.ELLIPSE, cm.SLOT):
        rig.tool(mode)
        rig.click(200, 100)
        corner = rig.c._start
        assert rig.c._vb.keys() == ["w", "h"]
        rig.move(150, 60)                      # below and left of the corner
        rig.type("80")
        rig.key(Qt.Key_Tab)
        rig.type("40")
        rig.key(Qt.Key_Return)
        b = rig.doc.shapes[-1].bounds()
        assert b[2] == pytest.approx(corner.x(), abs=1e-9)
        assert b[3] == pytest.approx(corner.y(), abs=1e-9)
        assert b[2] - b[0] == pytest.approx(80.0, abs=1e-9)
        assert b[3] - b[1] == pytest.approx(40.0, abs=1e-9)
        assert rig.c.tool == mode
    assert [type(s).__name__ for s in rig.doc.shapes] == [
        "Rectangle", "Ellipse", "Rectangle"]


def test_a_circle_takes_its_diameter_first_and_a_radius_after_tab(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.CIRCLE)
    rig.click(300, 0)
    assert rig.c._vb.keys() == ["dia", "rad"] and rig.c._vb.active == 0
    rig.type("30")
    rig.key(Qt.Key_Return)
    first = rig.doc.shapes[-1]
    assert isinstance(first, Circle) and first.rx == pytest.approx(15.0)
    rig.click(300, 100)
    rig.key(Qt.Key_Tab)
    rig.type("10")
    assert rig.c._vb.field("dia").kept is None
    rig.key(Qt.Key_Return)
    second = rig.doc.shapes[-1]
    assert second.rx == pytest.approx(10.0)
    centre = rig.scene(300, 100)
    assert second.transform.x == pytest.approx(centre.x())
    # a 2-point circle: the diameter, from the first end towards the mouse
    rig.tool(cm.CIRCLE2)
    rig.click(0, 200)
    end = rig.c._multi_pts[0]
    rig.type("40")
    rig.key(Qt.Key_Return)
    c2 = rig.doc.shapes[-1]
    assert c2.rx == pytest.approx(20.0)
    assert c2.transform.x == pytest.approx(end.x() + 20.0)


def test_a_centre_arc_takes_a_radius_then_a_sweep(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.ARCCENTER)
    rig.click(500, 0)
    centre = rig.c._multi_pts[0]
    assert rig.c._vb.keys() == ["rad"]
    rig.type("20")
    rig.key(Qt.Key_Return)                     # the start, at 20 to the right
    assert rig.c._vb.keys() == ["sweep"]
    rig.type("90")
    rig.key(Qt.Key_Return)
    arc = rig.doc.shapes[-1]
    assert isinstance(arc, EditablePath)
    b = arc.bounds()
    assert (b[0], b[1]) == (pytest.approx(centre.x()),
                            pytest.approx(centre.y()))
    assert (b[2], b[3]) == (pytest.approx(centre.x() + 20.0),
                            pytest.approx(centre.y() + 20.0))
    # a sweep below zero turns clockwise
    rig.click(500, 100)
    centre = rig.c._multi_pts[0]
    rig.type("10")
    rig.key(Qt.Key_Return)
    rig.type("-90")
    rig.key(Qt.Key_Return)
    b = rig.doc.shapes[-1].bounds()
    assert (b[0], b[1]) == (pytest.approx(centre.x()),
                            pytest.approx(centre.y() - 10.0))
    assert (b[2], b[3]) == (pytest.approx(centre.x() + 10.0),
                            pytest.approx(centre.y()))


def test_typed_lengths_read_units_sums_and_parameters(rig):
    from leathercad_app import canvas as cm
    from leathercad_app import units as U
    rig.doc.params["strap"] = "12.5"
    rig.tool(cm.CIRCLE)
    try:
        U.set_current("in")
        rig.click(0, 0)
        rig.type("1")                          # a bare number: inches
        rig.key(Qt.Key_Return)
        assert rig.doc.shapes[-1].rx == pytest.approx(25.4 / 2)
        rig.click(0, 100)
        rig.type("10mm*2")                     # names its unit
        rig.key(Qt.Key_Return)
        assert rig.doc.shapes[-1].rx == pytest.approx(10.0)
        rig.click(0, 200)
        rig.type("strap*2")                    # a parameter is millimetres
        rig.key(Qt.Key_Return)
        assert rig.doc.shapes[-1].rx == pytest.approx(12.5)
    finally:
        U.set_current("mm")


def test_an_unreadable_value_is_refused_and_can_be_put_right(rig):
    from leathercad_app import canvas as cm
    msgs = []
    rig.c.statusMessage.connect(msgs.append)
    rig.tool(cm.LINE)
    rig.click(0, 0)
    rig.type("abc")
    rig.key(Qt.Key_Return)
    assert len(rig.c._poly_pts) == 1 and rig.doc.shapes == []
    assert "Can't use “abc” for the length" in msgs[-1]
    assert rig.c._vb.fields[0].unreadable()
    rig.key(Qt.Key_Tab)                        # Tab won't keep it either
    assert rig.c._vb.active == 0
    for _ in range(3):
        rig.key(Qt.Key_Backspace)
    rig.type("0")                              # no length
    rig.key(Qt.Key_Return)
    assert len(rig.c._poly_pts) == 1
    rig.key(Qt.Key_Backspace)
    rig.type("12")
    rig.key(Qt.Key_Return)
    assert len(rig.c._poly_pts) == 2
    p = rig.c._poly_pts
    assert math.hypot(p[1].x() - p[0].x(),
                      p[1].y() - p[0].y()) == pytest.approx(12.0)


def test_esc_drops_what_is_typed_before_it_ends_the_line(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.LINE)
    rig.click(0, 0)
    rig.click(50, 0)
    rig.type("20")
    rig.key(Qt.Key_Tab)
    rig.key(Qt.Key_Escape)                     # typing gone, line goes on
    assert rig.c._vb.any_typing() is False and len(rig.c._poly_pts) == 2
    assert rig.doc.shapes == []
    rig.key(Qt.Key_Escape)                     # ends it with its 2 points
    assert len(rig.doc.shapes) == 1 and len(rig.doc.shapes[0].points) == 2


def test_every_drawing_tool_stays_on_after_a_shape(rig):
    from leathercad_app import canvas as cm
    for mode, clicks in ((cm.RECT, [(0, 0), (20, 10)]),
                         (cm.CIRCLE, [(50, 0), (55, 0)]),
                         (cm.HOLE, [(70, 0)]),
                         (cm.CIRCLE3, [(100, 0), (110, 10), (120, 0)]),
                         (cm.CONSTRUCTION, [(0, 50), (30, 60)]),
                         (cm.MEASURE, [(0, 80), (30, 80)]),
                         (cm.DIMENSION, [(0, 100), (30, 100)])):
        rig.tool(mode)
        for x, y in clicks:
            rig.click(x, y)
        assert rig.c.tool == mode, mode
    rig.tool(cm.POLYGON)
    for x, y in ((0, 150), (30, 150), (15, 170)):
        rig.click(x, y)
    rig.key(Qt.Key_Return)
    assert rig.c.tool == cm.POLYGON and isinstance(rig.doc.shapes[-1],
                                                   Polygon)
    assert len(rig.doc.dimensions) == 1


def test_right_click_while_drawing_offers_finish_close_and_cancel(
        rig, monkeypatch):
    from leathercad_app import canvas as cm
    seen = {}

    def menu_choosing(text):
        class Menu(QMenu):
            def exec(self, *args, **kwargs):
                seen["items"] = [(a.text(), a.isEnabled())
                                 for a in self.actions() if a.text()]
                return next((a for a in self.actions() if a.text() == text),
                            None)
        return Menu

    def right_click(x, y):
        pt = rig.at(x, y)
        rig.c.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Mouse, pt, rig.c.viewport().mapToGlobal(pt)))
        QApplication.processEvents()

    rig.tool(cm.LINE)
    rig.click(0, 0)
    monkeypatch.setattr(cm, "QMenu", menu_choosing("Cancel"))
    right_click(10, 10)
    assert seen["items"] == [("Finish line", False),
                             ("Close outline", False), ("Cancel", True)]
    assert rig.c._poly_pts == [] and rig.c.tool == cm.LINE
    for x, y in ((0, 0), (40, 0), (40, 30)):
        rig.click(x, y)
    monkeypatch.setattr(cm, "QMenu", menu_choosing("Close outline"))
    right_click(10, 10)
    assert seen["items"] == [("Finish line", True),
                             ("Close outline", True), ("Cancel", True)]
    assert isinstance(rig.doc.shapes[-1], Polygon)
    rig.tool(cm.RECT)
    rig.click(100, 0)
    monkeypatch.setattr(cm, "QMenu", menu_choosing("Cancel"))
    right_click(120, 10)
    assert seen["items"] == [("Cancel", True)] and rig.c._start is None
    # nothing half-drawn: the editing menu, with a way back to Select
    monkeypatch.setattr(cm, "QMenu",
                        menu_choosing("Back to the Select tool (Esc)"))
    right_click(120, 10)
    assert seen["items"][0] == ("Back to the Select tool (Esc)", True)
    assert rig.c.tool == cm.SELECT


def test_holding_ctrl_places_a_point_without_snapping(rig):
    from leathercad_app import canvas as cm
    rig.c.snap_to_grid = True
    rig.c.snap_grid = 1.0
    rig.tool(cm.LINE)
    x, y = 10.37, 4.61
    raw = rig.scene(x, y)
    assert (raw.x() % 1.0, raw.y() % 1.0) != (0.0, 0.0)
    rig.click(x, y)
    snapped = rig.c._poly_pts[0]
    assert (snapped.x(), snapped.y()) == (round(raw.x()), round(raw.y()))
    rig.key(Qt.Key_Escape)
    rig.click(x, y, Qt.ControlModifier)
    free = rig.c._poly_pts[0]
    assert (free.x(), free.y()) == (raw.x(), raw.y())


def test_an_offset_takes_a_typed_distance_on_the_mouse_side(qapp):
    from leathercad_app import canvas as cm
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, layer="Cut",
                            transform=Transform(x=100, y=0)))
    rig = Rig(doc)
    try:
        rig.tool(cm.OFFSET)
        rig.click(130, 0)                      # on its right edge
        rig.move(145, 0)                       # outside
        assert rig.c._vb.keys() == ["dist"]
        rig.type("5")
        rig.key(Qt.Key_Return)
        b = rig.doc.shapes[-1].bounds()
        assert (b[2] - b[0], b[3] - b[1]) == (pytest.approx(70.0),
                                              pytest.approx(50.0))
        rig.click(130, 0)
        rig.move(122, 0)                       # inside
        rig.type("3")
        rig.key(Qt.Key_Return)
        b = rig.doc.shapes[-1].bounds()
        assert (b[2] - b[0], b[3] - b[1]) == (pytest.approx(54.0),
                                              pytest.approx(34.0))
        assert rig.c.tool == cm.OFFSET
    finally:
        rig.close()


def test_perfect_fit_gives_way_to_a_typed_size(rig):
    from leathercad_app import canvas as cm
    rig.c.perfect_fit = True
    rig.tool(cm.RECT)
    rig.click(0, 0)
    rig.move(80, 60)
    rig.type("73")
    rig.key(Qt.Key_Tab)
    rig.type("41")
    rig.key(Qt.Key_Return)
    r = rig.doc.shapes[-1]
    assert (r.width, r.height) == (pytest.approx(73.0), pytest.approx(41.0))


def test_a_line_s_status_gives_its_length_and_angle(rig):
    from leathercad_app import canvas as cm
    msgs = []
    rig.c.statusMessage.connect(msgs.append)
    rig.tool(cm.LINE)
    rig.click(0, 0)
    a = rig.c._poly_pts[0]
    rig.move(30, 40)
    b = rig.scene(30, 40)
    length = math.hypot(b.x() - a.x(), b.y() - a.y())
    ang = math.degrees(math.atan2(b.y() - a.y(), b.x() - a.x()))
    assert msgs[-1].startswith(f"length {length:.2f} mm   ∠ {ang:.1f}°")
    rig.key(Qt.Key_Escape)
    rig.tool(cm.CONSTRUCTION)
    rig.click(0, 0)
    rig.move(30, 40)
    assert msgs[-1].startswith("length ")


def test_drag_to_draw_draws_one_line_a_drag_and_shows_no_boxes(rig):
    from leathercad_app import canvas as cm
    rig.c.drag_to_draw = True
    rig.tool(cm.LINE)
    vp = rig.c.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, rig.at(0, 0))
    QApplication.processEvents()
    assert not rig.c._vb_active()
    rig.move(30, 0)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, rig.at(30, 0))
    QApplication.processEvents()
    line, = rig.doc.shapes
    assert len(line.points) == 2 and rig.c.tool == cm.LINE


def test_the_boxes_show_the_size_the_mouse_gives_until_one_is_typed(rig):
    from leathercad_app import canvas as cm
    rig.tool(cm.RECT)
    rig.click(0, 0)
    a = rig.c._start
    rig.move(30, 20)
    b = rig.scene(30, 20)
    w, h = rig.c._vb.field("w"), rig.c._vb.field("h")
    assert w.live == pytest.approx(abs(b.x() - a.x()))
    assert h.live == pytest.approx(abs(b.y() - a.y()))
    rig.type("7")
    assert w.text == "7" and w.value() == 7.0 and h.value() is None
    rig.move(50, 50)                           # the height still follows
    b = rig.scene(50, 50)
    assert h.live == pytest.approx(abs(b.y() - a.y()))
    assert w.live == pytest.approx(7.0)
    # the boxes sit beside the cursor, inside the view
    g = rig.c._vb.geometry()
    cur = rig.at(50, 50)
    assert g.left() > cur.x() and g.top() > cur.y()
    assert rig.c.viewport().rect().contains(g)
    rig.key(Qt.Key_Escape)
    rig.key(Qt.Key_Escape)
    assert not rig.c._vb.isVisible()

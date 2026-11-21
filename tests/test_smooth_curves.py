"""Circles and arcs are drawn as curves on the canvas, round at any zoom --
not as the polyline they are flattened to, whose sides show close up -- and
the curve drawn lies on the shape, turned and mirrored as it is."""

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Circle, Edge, EditablePath,  # noqa: E402
                               Ellipse, Polygon, Rectangle)
from leathercad.stitchsettings import StitchSettings  # noqa: E402


#: how far off the true circle Qt's curves for an arc stray: a quarter
#: circle is one cubic Bezier, at most 0.027 % of the radius out
_BEZIER = 3e-4


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _item(shape):
    from leathercad_app.items import ShapeItem
    return ShapeItem(shape)


def _samples(qp: QPainterPath, n: int = 240):
    return [qp.pointAtPercent(i / n) for i in range(n + 1)]


def _curved(qp: QPainterPath) -> bool:
    return any(qp.elementAt(i).type == QPainterPath.CurveToElement
               for i in range(qp.elementCount()))


def _dist_to_polyline(p, pts):
    best = math.inf
    for a, b in zip(pts, pts[1:]):
        ax, ay, bx, by = a.x, a.y, b.x, b.y
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p.x() - ax) * dx
                                                   + (p.y() - ay) * dy) / L2))
        best = min(best, math.hypot(p.x() - (ax + t * dx),
                                    p.y() - (ay + t * dy)))
    return best


def _oriented_fine(shape):
    """The shape's outline, finely flattened, turned and mirrored as the
    item's outline is (not moved)."""
    path = shape.local_path(0.0005)
    pts = [shape.transform.apply_dir(p) for p in path.flatten(0.0005)]
    if path.closed:
        pts.append(pts[0])
    return pts


def _on_shape(item, shape, tol=0.004):
    """Every point of the curve drawn is on the shape, and the curve goes
    all the way round it: both ways within ``tol`` mm."""
    fine = _oriented_fine(shape)
    curve = item._curve
    assert curve is not None and _curved(curve)
    far = max(_dist_to_polyline(p, fine) for p in _samples(curve))
    assert far < tol, far
    # ... and nothing of the shape is missing from it
    curve_pts = [Vec2(p.x(), p.y()) for p in _samples(curve, 720)]
    back = max(_dist_to_polyline(QPointF(v.x, v.y), curve_pts)
               for v in fine[::7])
    assert back < tol + 0.01, back


def test_a_small_circle_is_drawn_round(qapp):
    """A 5 mm circle used to be drawn as its 25-sided polyline: now a true
    circle, on the same centre and radius."""
    c = Circle()
    c.radius = 2.5
    c.transform.x, c.transform.y = 40.0, -12.0
    item = _item(c)
    assert item._outline.size() < 40                  # the sides it has
    _on_shape(item, c)
    for p in _samples(item._curve):
        assert abs(math.hypot(p.x(), p.y()) - 2.5) < _BEZIER * 2.5
    # the item's bounds hold the whole circle, not just its sides
    r = item.content_rect()
    assert r.left() <= -2.5 and r.right() >= 2.5
    assert r.top() <= -2.5 and r.bottom() >= 2.5


@pytest.mark.parametrize("rotation, mirror", [(0.0, False), (30.0, False),
                                              (-75.0, True), (200.0, True)])
def test_an_ellipse_turned_and_mirrored(qapp, rotation, mirror):
    e = Ellipse(rx=12.0, ry=5.0)
    e.transform.rotation, e.transform.mirror_x = rotation, mirror
    _on_shape(_item(e), e)


@pytest.mark.parametrize("rotation, mirror", [(0.0, False), (17.0, False),
                                              (-120.0, True)])
def test_rounded_corners_are_round(qapp, rotation, mirror):
    r = Rectangle(width=40.0, height=25.0, corner_radius=4.0)
    r.transform.rotation, r.transform.mirror_x = rotation, mirror
    _on_shape(_item(r), r)


@pytest.mark.parametrize("bulge", [6.0, -6.0])
def test_an_arc_drawn_with_the_arc_tools_is_round(qapp, bulge):
    """An arc either way round, in a path with a straight part too."""
    ep = EditablePath(nodes=[Vec2(0, 0), Vec2(20, 0), Vec2(20, 12)],
                      edges=[Edge("arc", Vec2(10, bulge)), Edge("line")],
                      closed=False)
    for rotation, mirror in ((0.0, False), (45.0, True)):
        ep.transform.rotation, ep.transform.mirror_x = rotation, mirror
        _on_shape(_item(ep), ep)


def test_straight_sides_are_still_their_polyline(qapp):
    poly = Polygon(points=[Vec2(0, 0), Vec2(30, 0), Vec2(30, 20), Vec2(0, 20)],
                   close_path=True)
    item = _item(poly)
    assert item._curve is None and item._outline.size() >= 4
    assert _item(Rectangle(width=30, height=20))._curve is None


def test_the_canvas_paints_the_curve(qapp):
    """On the canvas, close up, the circle's edge is where the circle is --
    not cut across by its sides."""
    from leathercad_app.mainwindow import MainWindow
    doc = Document("round")
    c = Circle()
    c.radius = 2.5
    doc.shapes.append(c)
    win = MainWindow(doc)
    try:
        win.resize(900, 700)
        win.show()
        win.canvas._zoom = 40.0
        win.canvas._apply_zoom()
        win.canvas.centerOn(0, 0)
        QApplication.processEvents()
        img = win.canvas.viewport().grab().toImage()
        to_px = win.canvas.viewportTransform()
        to_mm = to_px.inverted()[0]
        # half way between two corners of the 25 sides, where a side lies
        # furthest inside the circle: 0.02 mm, 0.8 px at this zoom. Across
        # the pixel row there, the middle of the line drawn -- weighed by
        # its ink, whatever its width -- is where the circle crosses it
        a = math.pi / 25.0
        row = int(to_px.map(QPointF(0.0, 2.5 * math.sin(a))).y())
        y_mm = to_mm.map(QPointF(0.0, row + 0.5)).y()
        x_true = to_px.map(QPointF(math.sqrt(2.5 ** 2 - y_mm ** 2), y_mm)).x()

        def ink(x):
            c0 = img.pixelColor(x, row)
            return 255 * 3 - c0.red() - c0.green() - c0.blue()

        ground = ink(int(x_true) - 24)
        xs = range(int(x_true) - 8, int(x_true) + 9)
        w = [max(0, ink(x) - ground) for x in xs]
        middle = sum((x + 0.5) * k for x, k in zip(xs, w)) / sum(w)
        assert abs(middle - x_true) < 0.35, (middle, x_true)
    finally:
        win._unsaved_changes = False
        win.close()


def test_unsewn_edges_follow_the_curve(qapp):
    """The dotted marker over an edge that isn't sewn follows the arc, as
    the outline does."""
    r = Rectangle(width=40.0, height=25.0, corner_radius=6.0)
    r.stitch = StitchSettings(enabled=True)
    n = len(r.local_path().segments)
    r.stitch_edges = [True] * n
    arcs = [i for i, sg in enumerate(r.local_path().segments)
            if type(sg).__name__ == "Arc"]
    r.stitch_edges[arcs[0]] = False
    item = _item(r)
    assert not item._skipped.isEmpty() and _curved(item._skipped)
    sg = r.local_path().segments[arcs[0]]
    for p in _samples(item._skipped, 60):
        assert abs(math.hypot(p.x() - sg.center.x, p.y() - sg.center.y)
                   - sg.radius) < _BEZIER * sg.radius


def test_the_arc_tools_preview_a_curve(qapp):
    from leathercad_app import canvas as cv
    from leathercad_app.canvas import Canvas
    canvas = Canvas(Document())
    path = QPainterPath()
    assert canvas._arc_preview(path, QPointF(0, 0), QPointF(5, 5),
                               QPointF(10, 0))
    assert _curved(path)
    for p in _samples(path):
        assert abs(math.hypot(p.x() - 5.0, p.y() - 0.0) - 5.0) < _BEZIER * 5.0
    # three points in a line have no arc through them
    flat = QPainterPath()
    assert not canvas._arc_preview(flat, QPointF(0, 0), QPointF(5, 0),
                                   QPointF(10, 0))
    assert flat.isEmpty()
    assert cv.ARC3 is not None


def test_an_arc_ends_exactly_where_it_should(qapp):
    """Each arc is drawn in curves of a quarter turn at most that end
    exactly on its end -- where the next part of the outline goes on from
    -- and stay on its circle between."""
    from leathercad_app.items import smooth_path
    ep = EditablePath(nodes=[Vec2(0, 0), Vec2(40, 0), Vec2(40, 12)],
                      edges=[Edge("arc", Vec2(20, 15)), Edge("line")],
                      closed=False)
    path = ep.local_path()
    arc = path.segments[0]
    qp = smooth_path(path)
    pts = [(qp.elementAt(i).x, qp.elementAt(i).y)
           for i in range(qp.elementCount())]
    end = arc.end()
    assert any(abs(x - end.x) < 1e-9 and abs(y - end.y) < 1e-9
               for x, y in pts)
    assert abs(pts[-1][0] - 40.0) < 1e-9 and abs(pts[-1][1] - 12.0) < 1e-9
    quarters = math.ceil(abs(arc._sweep()) / (math.pi / 2) - 1e-9)
    curves = sum(1 for i in range(qp.elementCount())
                 if qp.elementAt(i).type == QPainterPath.CurveToElement)
    assert curves == quarters == 2
    for t in range(0, 101):
        p = qp.pointAtPercent(t / 100.0)
        if p.x() < 40.0 - 1e-6:                # on the arc, not the line
            assert abs(math.hypot(p.x() - arc.center.x, p.y() - arc.center.y)
                       - arc.radius) < _BEZIER * arc.radius

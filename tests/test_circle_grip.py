"""Resizing a circle by its grips: one radius per cursor position.

A circle's box can only be square, so a corner grip used to pick whichever
axis had moved more from the circle's CURRENT radius -- which the previous
move had just changed, so it flipped between the two axes on alternate
moves and the circle flashed between two sizes."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsSceneMouseEvent  # noqa: E402

from leathercad import perfectfit as pf  # noqa: E402
from leathercad.shapes import Circle, Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    w = MainWindow(Document())
    s = w._settings()
    saved = s.value("perfectFit", None)
    yield w
    if saved is None:
        s.remove("perfectFit")
    else:
        s.setValue("perfectFit", saved)
    w.hide()


def _ev(kind, x, y):
    e = QGraphicsSceneMouseEvent(kind)
    e.setScenePos(QPointF(x, y))
    e.setButton(Qt.LeftButton)
    e.setButtons(Qt.LeftButton)
    e.setModifiers(Qt.NoModifier)
    return e


def _grips(c, shape):
    from leathercad_app.items import ResizeHandle
    item = c.add_shape(shape)
    c.scene_obj.clearSelection()
    item.setSelected(True)
    c.selection_changed()
    return item, {g.grip: g for g in c.scene_obj.items()
                  if isinstance(g, ResizeHandle)}


def _drag(g, steps):
    """Press on the grip, move through ``steps`` (dx, dy) offsets from it;
    the shape's radius (a rectangle's width) after each move."""
    p = g.pos()
    g.mousePressEvent(_ev(QEvent.GraphicsSceneMousePress, p.x(), p.y()))
    radii = []
    for dx, dy in steps:
        g.mouseMoveEvent(_ev(QEvent.GraphicsSceneMouseMove,
                             p.x() + dx, p.y() + dy))
        m = g.owner.model
        radii.append(m.rx if hasattr(m, "rx") else m.width)
    last = steps[-1]
    g.mouseReleaseEvent(_ev(QEvent.GraphicsSceneMouseRelease,
                            p.x() + last[0], p.y() + last[1]))
    return radii


@pytest.mark.parametrize("perfect", [False, True])
def test_corner_grip_grows_a_circle_steadily(win, perfect):
    win.act_perfect_fit.setChecked(perfect)
    ci = Circle(rx=20, ry=20, transform=Transform(x=0, y=0), layer="Cut",
                stitch=StitchSettings(pitch_mm=5.0, inset=3.5))
    _item, grips = _grips(win.canvas, ci)
    # outward, well off the diagonal: x twice as fast as y
    radii = _drag(grips[(1, 1)], [(1.0 * k, 0.4 * k) for k in range(1, 16)])
    assert all(b >= a - 1e-9 for a, b in zip(radii, radii[1:])), radii
    assert ci.rx == ci.ry and radii[-1] > 22.0
    if not perfect:
        # the box corner is the cursor's nearest point on the diagonal
        assert radii[-1] == pytest.approx(20 + (15 + 6) / 4)


def test_corner_grip_shrinks_a_circle_steadily(win):
    ci = Circle(rx=30, ry=30, transform=Transform(x=0, y=0), layer="Cut",
                stitch=StitchSettings(pitch_mm=5.0, inset=3.5))
    _item, grips = _grips(win.canvas, ci)
    radii = _drag(grips[(-1, 1)], [(0.8 * k, -0.2 * k) for k in range(1, 12)])
    assert all(b <= a + 1e-9 for a, b in zip(radii, radii[1:])), radii


def test_dragged_grip_sits_on_the_shapes_box(win):
    c = win.canvas
    ci = Circle(rx=20, ry=20, transform=Transform(x=0, y=0), layer="Cut")
    _item, grips = _grips(c, ci)
    g = grips[(1, 1)]
    _drag(g, [(3.0, 1.0), (6.0, 2.0)])
    # the opposite corner (-20, -20) stayed; the grip is on the new corner,
    # not out at the cursor
    corner = (ci.transform.x + ci.rx, ci.transform.y + ci.ry)
    assert ci.transform.x - ci.rx == pytest.approx(-20.0)
    assert (g.pos().x(), g.pos().y()) == (pytest.approx(corner[0]),
                                          pytest.approx(corner[1]))
    # an edge grip stays on its edge, whatever the cursor does across it
    r = Rectangle(width=40, height=20, transform=Transform(x=100, y=0),
                  layer="Cut")
    _item, grips = _grips(c, r)
    e = grips[(1, 0)]
    _drag(e, [(5.0, 7.0)])
    assert (e.pos().x(), e.pos().y()) == (pytest.approx(125.0),
                                          pytest.approx(0.0))


def test_perfect_circle_sizes_repeat_exactly():
    """Requests that land on the same hole count get the very same radius
    (solved once, then remembered), so a grip can't jitter between two
    numbers a hair apart."""
    a = pf.fit_circle(29.7, 5.0, 3.5)            # both nearest 33 holes
    b = pf.fit_circle(30.1, 5.0, 3.5)
    assert a.holes == b.holes and a.hx == b.hx

"""Fusion-style viewport: cursor-anchored zoom, unclamped pan, huge canvas."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _canvas(x=800, y=600):
    """Content deliberately FAR from the origin -- outside the old ±500 scene
    rect whose clamping caused the edge-of-view zoom drift."""
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=x, y=y),
                            layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.resize(600, 450)
    c.rebuild()
    c.fit_to_content()
    return win, c


def test_zoom_anchors_under_cursor_even_at_view_edge(qapp):
    """The scene point under the cursor must stay put while zooming -- also at
    the view corners and with content far from the origin (regression: the
    tiny scene rect clamped the anchor correction and points slid away)."""
    _win, c = _canvas()
    for vp in (QPoint(40, 30), QPoint(560, 420), QPoint(300, 225)):
        before = c.mapToScene(vp)
        for _ in range(5):
            c.zoom_at(vp, 1.5)
        after = c.mapToScene(vp)
        drift = ((after.x() - before.x()) ** 2
                 + (after.y() - before.y()) ** 2) ** 0.5
        assert drift < 0.5, f"cursor point slid {drift:.2f} mm at {vp}"
        for _ in range(5):
            c.zoom_at(vp, 1 / 1.5)                 # and back out again


def test_pan_is_never_clamped(qapp):
    _win, c = _canvas()
    c.centerOn(5000, 5000)                         # miles outside old bounds
    mid = c.mapToScene(c.viewport().rect().center())
    assert abs(mid.x() - 5000) < 1 and abs(mid.y() - 5000) < 1
    r = c.scene_obj.sceneRect()
    assert r.width() >= 100000 and r.height() >= 100000


def test_zoom_limits_and_far_out_grid(qapp):
    _win, c = _canvas()
    c.zoom_at(QPoint(300, 225), 1e9)
    assert c._zoom == c.ZOOM_MAX
    c.zoom_at(QPoint(300, 225), 1e-9)
    assert c._zoom == c.ZOOM_MIN
    c.viewport().repaint()                         # adaptive grid: no hang


def test_belt_template_fits_and_zooms(qapp):
    """A 1050 mm belt -- wider than the old scene rect -- fits and zooms."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad.templates import belt
    win = MainWindow(belt())
    c = win.canvas
    c.resize(800, 300)
    c.rebuild()
    c.fit_to_content()
    r = c.mapToScene(c.viewport().rect()).boundingRect()
    assert r.width() >= 1050                       # the whole strap is visible
    vp = QPoint(80, 60)
    before = c.mapToScene(vp)
    for _ in range(4):
        c.zoom_at(vp, 1.7)
    after = c.mapToScene(vp)
    assert ((after.x() - before.x()) ** 2
            + (after.y() - before.y()) ** 2) ** 0.5 < 0.5

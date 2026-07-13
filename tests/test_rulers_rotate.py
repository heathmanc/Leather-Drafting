"""Rulers, drag-out guides, and the on-canvas rotate handle."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _win():
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()
    win.canvas.resize(500, 400)
    return win


def test_rulers_exist_and_paint(qapp):
    win = _win()
    assert win.ruler_h.height() > 0 and win.ruler_v.width() > 0
    win.ruler_h.repaint()                          # paints without crashing
    win.ruler_v.repaint()
    win._set_rulers_visible(False)
    assert win.ruler_h.isHidden()
    win._set_rulers_visible(True)


def test_add_guide_creates_construction_lines(qapp):
    from leathercad.shapes import PathShape
    win = _win()
    c = win.canvas
    n0 = len(win.doc.shapes)
    c.add_guide("h", 25.0)                         # from the top ruler
    c.add_guide("v", -10.0)                        # from the left ruler
    guides = [s for s in win.doc.shapes[n0:]]
    assert len(guides) == 2
    h, v = guides
    assert isinstance(h, PathShape) and h.construction
    ha, hb = (h.transform.apply(p) for p in h.points)
    assert abs(ha.y - 25.0) < 1e-6 and abs(hb.y - 25.0) < 1e-6   # horizontal
    assert abs(hb.x - ha.x) > 300                  # long enough to feel infinite
    va, vb = (v.transform.apply(p) for p in v.points)
    assert abs(va.x + 10.0) < 1e-6 and abs(vb.x + 10.0) < 1e-6   # vertical
    # guides never export
    from leathercad.export import collect
    outlines, _ = collect(win.doc)
    assert len(outlines) == 1                      # just the rectangle


def test_rotate_handle_spins_shape(qapp):
    from leathercad_app.items import ShapeItem, RotateHandle
    win = _win()
    c = win.canvas
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]
    it.setSelected(True)
    c._refresh_resize_handles()
    rots = [h for h in c._resize_handles if isinstance(h, RotateHandle)]
    assert len(rots) == 1
    rot = rots[0]
    # grip floats above the shape's top edge
    assert rot.pos().y() > 20.0
    rot.apply_rotation(30.0)
    assert round(it.model.transform.rotation, 3) == 30.0
    # the outline actually rotated: bbox widens
    minx, miny, maxx, maxy = it.model.bounds()
    assert (maxx - minx) > 60.0
    # normalisation keeps angles in (-180, 180]
    rot.apply_rotation(370.0)
    assert round(it.model.transform.rotation, 3) == 10.0


def test_rotate_handle_repositions_with_shape(qapp):
    from leathercad_app.items import ShapeItem, RotateHandle
    win = _win()
    c = win.canvas
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]
    it.setSelected(True)
    c._refresh_resize_handles()
    rot = [h for h in c._resize_handles if isinstance(h, RotateHandle)][0]
    y0 = rot.pos().y()
    it.model.transform.rotation = 90.0
    it.sync_from_model()
    rot.reposition()
    # after a 90° spin the grip is no longer straight above the origin
    assert abs(rot.pos().x()) > 10.0 or abs(rot.pos().y() - y0) > 5.0

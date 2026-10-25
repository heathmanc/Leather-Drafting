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


def _offset_line_win():
    # a line drawn with the Score tool: its points are world points and its
    # transform origin is the page origin, far from the line itself
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.geometry import Vec2
    from leathercad.shapes import PathShape, Transform
    doc = Document()
    line = PathShape(points=[Vec2(100, 100), Vec2(140, 120), Vec2(140, 100)],
                     layer="Cut", transform=Transform())
    doc.add_shape(line)
    win = MainWindow(doc)
    win.canvas.rebuild()
    return win, line


def _centre(sh):
    b = sh.bounds()
    return round((b[0] + b[2]) / 2, 6), round((b[1] + b[3]) / 2, 6)


def test_rotate_and_mirror_turn_a_part_about_its_own_centre(qapp):
    from leathercad.geometry import Vec2
    from leathercad_app.items import ShapeItem, RotateHandle
    win, line = _offset_line_win()
    c = win.canvas
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]
    lc = Vec2(*it.pivot_center())

    def pivot():
        w = line.transform.apply(lc)
        return round(w.x, 6), round(w.y, 6)
    p0 = pivot()
    assert p0 == (120.0, 110.0)                    # the part's own middle
    it.setSelected(True)
    c._refresh_resize_handles()
    rot = [h for h in c._resize_handles if type(h) is RotateHandle][0]
    rot.apply_rotation(90.0)                       # the grip
    assert pivot() == p0
    props = win.properties
    props.show_selection([it])
    props.rot.setValue(45.0)                       # the Rotation field
    props.rot.editingFinished.emit()
    assert line.transform.rotation == 45.0 and pivot() == p0
    props.mirror.setChecked(True)                  # Mirror
    assert line.transform.mirror_x and pivot() == p0
    # the X/Y fields show the moved origin, so a later edit can't undo it
    assert abs(props.pos_x.value() - line.transform.x) < 1e-3


def test_circular_array_without_turning_moves_each_copy(qapp):
    win, line = _offset_line_win()
    c = win.canvas
    c.scene_obj.clearSelection()
    [i for i in c.scene_obj.items() if c._item_model(i) is line][0] \
        .setSelected(True)
    c.array_circular(4, 0.0, 0.0, 360.0, rotate_items=False)
    centres = sorted(_centre(s) for s in win.doc.shapes)
    assert len(set(centres)) == 4                  # not stacked
    for s in win.doc.shapes:
        assert s.transform.rotation == 0.0         # unturned
        x, y = _centre(s)
        assert abs((x * x + y * y) ** 0.5 - (120 ** 2 + 110 ** 2) ** 0.5) < 1e-6

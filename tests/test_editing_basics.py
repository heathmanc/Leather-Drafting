"""Editing basics: arrow-key nudge, scale, flip / mirror, editing several
items at once, and locked layers."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.holes import LooseHole  # noqa: E402
from leathercad.shapes import PathShape, Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _win(doc=None):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc or Document())
    win.resize(1200, 800)
    win.show()
    c = win.canvas
    c.rebuild()
    c.snap_to_grid = c.snap_to_nodes = False
    return win, c


def _items(c):
    return [i for i in c.scene_obj.items() if c._item_model(i) is not None]


def _key(c, key, mods=Qt.NoModifier):
    c.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods))


def test_arrow_keys_nudge_the_selection_and_its_group(qapp):
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, layer="Cut", group_id="g1"))
    doc.holes.append(LooseHole(point=Vec2(5, 5), group_id="g1"))
    win, c = _win(doc)
    rect = [i for i in _items(c) if hasattr(i, "model")][0]
    c.scene_obj.clearSelection()
    rect.setSelected(True)                  # just the rectangle picked...
    _key(c, Qt.Key_Right)
    _key(c, Qt.Key_Up, Qt.ShiftModifier)
    _key(c, Qt.Key_Left, Qt.ControlModifier)
    t = win.doc.shapes[0].transform
    assert (round(t.x, 6), round(t.y, 6)) == (0.9, 10.0)
    h = win.doc.holes[0].point               # ...its group-mate came along
    assert (round(h.x, 6), round(h.y, 6)) == (5.9, 15.0)
    win.undo()                               # one undo step per key press
    h = win.doc.holes[0].point
    assert (round(h.x, 6), round(h.y, 6)) == (6.0, 15.0)


def test_scale_about_the_selection_centre(qapp):
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, layer="Cut",
                            transform=Transform(x=10, y=5), group_id="g1"))
    doc.holes.append(LooseHole(point=Vec2(30, 15), group_id="g1"))
    win, c = _win(doc)
    for i in _items(c):
        i.setSelected(True)
    c.scale_selected(2.0)
    sh = win.doc.shapes[0]
    assert (sh.width, sh.height) == (80.0, 40.0)
    # the selection box was x -10..30, y -5..15: centre (10, 5)
    assert (round(sh.transform.x, 9), round(sh.transform.y, 9)) == (10.0, 5.0)
    hp = win.doc.holes[0].point
    assert (round(hp.x, 9), round(hp.y, 9)) == (50.0, 25.0)
    assert sh.stitch is None or sh.stitch.pitch_mm == 3.85   # untouched


def _world(sh):
    return sorted((round(p.x, 6), round(p.y, 6))
                  for p in sh.world_polyline()[0])


def test_flip_horizontal_mirrors_in_place(qapp):
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, layer="Cut",
                            transform=Transform(x=10, y=5, rotation=30.0)))
    doc.holes.append(LooseHole(point=Vec2(30, 15), slit_angle=30.0,
                               hole_style="slit"))
    win, c = _win(doc)
    for i in _items(c):
        i.setSelected(True)
    b = c._unit_bounds(_items(c))
    cx = (b[0] + b[2]) / 2
    before = _world(win.doc.shapes[0])
    c.mirror_selected(None, 90.0)
    sh = win.doc.shapes[0]
    want = sorted((round(2 * cx - x, 6), y) for x, y in before)
    assert _world(sh) == want
    assert sh.transform.mirror_x and abs(sh.transform.rotation + 30.0) < 1e-9
    h = win.doc.holes[0]
    assert abs(h.point.x - (2 * cx - 30)) < 1e-9 and abs(h.point.y - 15) < 1e-9
    assert h.slit_angle == -30.0            # slant sense reversed with it


def test_mirror_across_a_selected_line(qapp):
    doc = Document()
    axis = PathShape(points=[Vec2(0, 0), Vec2(100, 100)], layer="Cut",
                     construction=True)
    doc.add_shape(axis)
    doc.add_shape(Rectangle(width=20, height=10, layer="Cut",
                            transform=Transform(x=40, y=10)))
    win, c = _win(doc)
    for i in _items(c):
        i.setSelected(True)
    assert c.mirror_across_selected_line()
    sh = [s for s in win.doc.shapes if s.kind == "rectangle"][0]
    # across y = x: (x, y) -> (y, x)
    assert (round(sh.transform.x, 9), round(sh.transform.y, 9)) == (10.0, 40.0)
    assert abs(abs(sh.transform.rotation) - 90.0) < 1e-9
    assert axis.transform.x == 0.0          # the axis itself stays put


def test_editing_several_shapes_writes_only_the_changed_field(qapp):
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, layer="Cut",
                            stitch=StitchSettings(pitch_mm=3.0, inset=3.0)))
    doc.add_shape(Rectangle(width=30, height=30, layer="Cut",
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            transform=Transform(x=80)))
    win, c = _win(doc)
    items = [i for i in _items(c) if hasattr(i, "model")]
    props = win.properties
    props.show_selection(items)
    assert props.isEnabled() and "2 items" in props.readout.text()
    props.inset.setValue(5.0)
    props.inset.editingFinished.emit()
    a, b = win.doc.shapes
    assert (a.stitch.inset, b.stitch.inset) == (5.0, 5.0)
    assert (a.stitch.pitch_mm, b.stitch.pitch_mm) == (3.0, 4.0)   # kept
    idx = props.layer_combo.findData("Score")
    if idx >= 0:
        props.layer_combo.setCurrentIndex(idx)
        assert (a.layer, b.layer) == ("Score", "Score")
    win.undo()
    assert [s.stitch.inset for s in win.doc.shapes] == [5.0, 5.0]
    win.undo()
    assert [s.stitch.inset for s in win.doc.shapes] == [3.0, 3.0]


def test_locked_layer_cannot_be_selected_or_moved(qapp):
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, layer="Cut"))
    win, c = _win(doc)
    win.doc.layer("Cut").locked = True
    c.apply_layer_visibility()
    vpw = c.viewport()

    def ev(kind, x, y, btns=Qt.LeftButton):
        pt = QPointF(c.mapFromScene(QPointF(x, y)))
        btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
        QApplication.sendEvent(vpw, QMouseEvent(kind, pt, vpw.mapToGlobal(pt),
                                                btn, btns, Qt.NoModifier))
        qapp.processEvents()
    ev(QEvent.MouseButtonPress, 20, 0)          # the right edge
    ev(QEvent.MouseMove, 30, 5)
    ev(QEvent.MouseButtonRelease, 30, 5, Qt.NoButton)
    assert not c.selected_items()
    assert (win.doc.shapes[0].transform.x, win.doc.shapes[0].transform.y) == (0, 0)
    win._select_all()
    assert not c.selected_items()
    win.doc.layer("Cut").locked = False
    c.apply_layer_visibility()
    ev(QEvent.MouseButtonPress, 20, 0)
    ev(QEvent.MouseButtonRelease, 20, 0, Qt.NoButton)
    assert len(c.selected_items()) == 1

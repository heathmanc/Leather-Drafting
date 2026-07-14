"""Offscreen smoke test for the Qt app (skipped if PySide6 is unavailable)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _shape_items(canvas):
    from leathercad_app.items import ShapeItem
    return [it for it in canvas.scene_obj.items() if isinstance(it, ShapeItem)]


def test_mainwindow_builds_and_edits(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    doc = Document()
    doc.add_shape(Rectangle(width=80, height=50, corner_radius=6,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()

    items = _shape_items(win.canvas)
    assert items
    item = items[0]
    n0 = item.hole_count
    assert n0 > 0

    # drive a property change through the panel and confirm holes recompute
    win.canvas.scene_obj.clearSelection()
    item.setSelected(True)
    win.properties.show_selection(win.canvas.selected_items())
    assert win.properties.isEnabled()
    win.properties.pitch.setValue(2.5)  # finer iron -> more holes
    assert item.hole_count > n0


def test_add_and_delete_shape(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    item = win.canvas.add_shape(
        Rectangle(width=40, height=30,
                  stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    assert len(win.doc.shapes) == 1
    win.canvas.scene_obj.clearSelection()
    item.setSelected(True)
    win.canvas.delete_selected()
    assert len(win.doc.shapes) == 0


def test_undo_redo(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    for i in range(3):
        win.canvas.add_shape(Rectangle(
            width=40, height=30, transform=Transform(x=i * 60, y=0),
            stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    assert len(win.doc.shapes) == 3
    win.undo()
    win.undo()
    assert len(win.doc.shapes) == 1
    win.redo()
    assert len(win.doc.shapes) == 2

    # property-edit undo
    item = win.canvas.add_shape(Rectangle(
        width=40, height=30, stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
        layer="Cut"))
    win.properties.show_selection([item])
    win.properties.w.setValue(88.0)
    win.properties.w.editingFinished.emit()
    assert item.model.width == 88.0
    win.undo()
    assert all(s.width != 88.0 for s in win.doc.shapes)


def test_align_left(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    for x in (0, 40, 90):
        win.canvas.add_shape(Rectangle(
            width=20, height=20, transform=Transform(x=x, y=0),
            stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    for it in _shape_items(win.canvas):
        it.setSelected(True)
    win.canvas.align_selected("left")
    lefts = {round(it.model.bounds()[0], 3) for it in win.canvas._shape_items()}
    assert len(lefts) == 1


def test_item_does_not_shadow_qt_shape_method(qapp):
    """Regression: ShapeItem must not shadow QGraphicsItem.shape() (a method Qt
    calls during mouse hit-testing) with the model object, or moving the mouse
    over a shape raises 'Rectangle object is not callable'."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QTransform
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    item = win.canvas.add_shape(Rectangle(
        width=80, height=50, transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    # shape() is Qt's method -> QPainterPath; the model lives on .model
    assert isinstance(item.shape(), QPainterPath)
    assert isinstance(item.model, Rectangle)
    # clear selection so box-resize handles aren't sitting on the outline
    win.canvas.scene_obj.clearSelection()
    win.canvas.selection_changed()
    # hit-testing succeeds ON THE OUTLINE (interior is not clickable now);
    # the 80x50 rect's right edge is at x=40
    hit = win.canvas.scene_obj.itemAt(QPointF(40, 0), QTransform())
    assert hit is item
    # clicking the empty interior does NOT grab the shape
    assert win.canvas.scene_obj.itemAt(QPointF(0, 0), QTransform()) is None


def test_items_deselected_and_tracked_to_avoid_use_after_free(qapp):
    """Regression for the macOS crash in QGraphicsScene::clearSelection(): a
    selected item must be deselected before removal and kept referenced so
    PySide never frees a live, still-selected item."""
    import shiboken6
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import HoleItem
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    item = c.add_shape(Rectangle(
        width=80, height=50, corner_radius=6, transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    assert item in c._live                      # strong ref held
    c.scene_obj.clearSelection()
    item.setSelected(True)
    c.ungroup_selected()
    holes = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]
    assert all(h in c._live for h in holes)

    victims = holes[:4]
    c.scene_obj.clearSelection()
    for h in victims:
        h.setSelected(True)
    c.delete_selected()
    for h in victims:
        assert h not in c._live
        # deleted items are not left selected in the scene
        assert (not shiboken6.isValid(h)) or (not h.isSelected())

    # after a rebuild (undo) the properties panel must not hold a stale item
    win.properties.show_selection([holes[6]])
    win.undo()
    assert win.properties._item is None


def test_hole_style_visibility_toggles(qapp):
    """Switching a hole slit<->round must update which size fields show, and
    the edit must reach the model (regression: _apply nulled _item on reload)."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import HoleItem
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    item = c.add_shape(Rectangle(
        width=60, height=40, transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=4.0, inset=3.0, hole_style="slit"),
        layer="Cut"))
    c.scene_obj.clearSelection(); item.setSelected(True); c.ungroup_selected()
    hole = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)][0]
    c.scene_obj.clearSelection(); hole.setSelected(True)
    p = win.properties
    p.show_selection([hole])
    form = p._stitch_form
    assert form.isRowVisible(p.slit_len) and not form.isRowVisible(p.hole_dia_combo)
    p.punch_style.setCurrentText("Round")
    assert hole.hole.hole_style == "round"
    assert form.isRowVisible(p.hole_dia_combo) and not form.isRowVisible(p.slit_len)
    p.punch_style.setCurrentText("Diamond")
    assert hole.hole.hole_style == "diamond"
    assert form.isRowVisible(p.slit_len) and not form.isRowVisible(p.hole_dia_combo)
    p.punch_style.setCurrentText("Oblique")
    assert hole.hole.hole_style == "slit"
    assert form.isRowVisible(p.slit_len) and not form.isRowVisible(p.hole_dia_combo)


def test_baked_holes_hide_distribution_controls(qapp):
    """Baked/grouped holes have fixed positions, so the distribution controls
    (pitch, fit, symmetry, corners...) must be hidden -- only the appearance
    rows (style / ø / slit) stay. They do show for an auto-spaced shape."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem, HoleItem
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    p = win.properties
    f = p._stitch_form
    item = c.add_shape(Rectangle(
        width=60, height=40, transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=True, hole_style="slit"), layer="Cut"))

    # auto-spaced: distribution controls are visible
    c.scene_obj.clearSelection(); item.setSelected(True); c.selection_changed()
    p.show_selection([item])
    assert f.isRowVisible(p.symmetry) and f.isRowVisible(p.corner_style)
    assert f.isRowVisible(p.pitch_combo)

    # ungroup then group back -> baked holes
    c.ungroup_selected()
    holes = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]
    shp = next(it for it in c.scene_obj.items() if isinstance(it, ShapeItem))
    c.scene_obj.clearSelection(); shp.setSelected(True)
    for h in holes:
        h.setSelected(True)
    c.group_selected()
    p.show_selection([shp])
    # distribution controls hidden, appearance controls still shown
    assert not f.isRowVisible(p.symmetry)
    assert not f.isRowVisible(p.corner_style)
    assert not f.isRowVisible(p.pitch_combo) and not f.isRowVisible(p.fit)
    assert f.isRowVisible(p.punch_style) and f.isRowVisible(p.slit_len)


def test_rounded_rect_converts_to_arc_nodes_and_locks(qapp):
    from PySide6.QtWidgets import QGraphicsItem
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import EditablePath

    win = MainWindow(Document())
    c = win.canvas
    r = c.add_shape(Rectangle(width=80, height=50, corner_radius=10,
                              transform=Transform(x=0, y=0), layer="Cut"))
    c.scene_obj.clearSelection(); r.setSelected(True)
    c.convert_to_nodes()
    new = win.doc.shapes[0]
    assert isinstance(new, EditablePath)
    assert len(new.nodes) == 8
    assert sum(1 for e in new.edges if e.kind == "arc") == 4
    # 8 on-path node handles + 4 arc-midpoint handles
    mids = [h for h in c._handles if h.node.is_mid]
    assert len(c._handles) == 12 and len(mids) == 4
    # shape is locked while editing so clicks hit handles, not the outline
    assert not (c._edit_owner.flags() & QGraphicsItem.ItemIsMovable)
    c.clear_vertex_handles()
    assert c._edit_owner is None


def test_editablepath_roundtrip(tmp_path):
    from leathercad.document import Document
    from leathercad.shapes import EditablePath, Transform

    doc = Document("t")
    ep = EditablePath.from_rounded_rect(80, 50, 10)
    ep.transform = Transform(x=5, y=7)
    doc.add_shape(ep)
    p = tmp_path / "ep.json"
    doc.save(str(p))
    d2 = Document.load(str(p))
    got = d2.shapes[0]
    assert isinstance(got, EditablePath)
    assert len(got.nodes) == 8
    assert sum(1 for e in got.edges if e.kind == "arc" and e.mid is not None) == 4


def test_break_apart_into_segments(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import PathShape, EditablePath

    win = MainWindow(Document())
    c = win.canvas
    r = c.add_shape(Rectangle(width=80, height=50, corner_radius=10,
                              transform=Transform(x=0, y=0), layer="Cut"))
    c.scene_obj.clearSelection(); r.setSelected(True)
    c.break_apart_selected()
    shapes = win.doc.shapes
    lines = [s for s in shapes if isinstance(s, PathShape)]
    arcs = [s for s in shapes if isinstance(s, EditablePath)]
    assert len(shapes) == 8 and len(lines) == 4 and len(arcs) == 4

    # each piece has its own transform: moving one leaves the others put
    items = [it for it in c.scene_obj.items() if isinstance(it, ShapeItem)]
    other_before = (items[1].model.transform.x, items[1].model.transform.y)
    items[0].setPos(items[0].pos().x() + 40, items[0].pos().y() + 40)
    assert (items[1].model.transform.x, items[1].model.transform.y) == other_before

    # sharp rectangle -> 4 line segments
    s = c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=200, y=0),
                              layer="Cut"))
    c.scene_obj.clearSelection(); s.setSelected(True)
    n = len(win.doc.shapes)
    c.break_apart_selected()
    assert len(win.doc.shapes) == n - 1 + 4


def test_break_apart_preserves_stitch_holes(qapp):
    """Breaking apart a stitched shape must keep its holes (baked to
    individual holes that stay in place), not drop them."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import HoleItem
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    r = c.add_shape(Rectangle(width=100, height=64, corner_radius=14,
                              transform=Transform(x=0, y=0),
                              stitch=StitchSettings(pitch_mm=3.85, inset=3.5),
                              layer="Cut"))
    n = c.total_holes()
    assert n > 0
    c.scene_obj.clearSelection(); r.setSelected(True)
    c.break_apart_selected()
    holes = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]
    assert len(holes) == n                     # every hole preserved
    assert len(win.doc.holes) == n


def test_rubberband_inside_shape_selects_holes_not_shape(qapp):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QPainterPath
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem, HoleItem
    from leathercad.document import Document
    from leathercad.holes import LooseHole
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    c.add_shape(Rectangle(width=100, height=70, transform=Transform(x=0, y=0),
                          layer="Cut"))
    for x in (-10, 0, 10):
        win.doc.add_hole(LooseHole(point=Vec2(x, 0)))
    c.rebuild()
    area = QPainterPath(); area.addRect(QRectF(-15, -8, 30, 16))  # interior box
    c.scene_obj.setSelectionArea(area, mode=Qt.IntersectsItemShape)
    sel = c.selected_items()
    assert len([s for s in sel if isinstance(s, HoleItem)]) == 3
    assert len([s for s in sel if isinstance(s, ShapeItem)]) == 0


def test_join_welds_segments(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import EditablePath, PathShape
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    # break a rounded rect apart, then weld it back into one closed arc-path
    r = c.add_shape(Rectangle(width=80, height=50, corner_radius=10,
                              transform=Transform(x=0, y=0), layer="Cut"))
    c.scene_obj.clearSelection(); r.setSelected(True)
    c.break_apart_selected()
    pieces = [it for it in c.scene_obj.items() if isinstance(it, ShapeItem)]
    c.scene_obj.clearSelection()
    for p in pieces:
        p.setSelected(True)
    c.join_selected()
    assert len(win.doc.shapes) == 1
    jp = win.doc.shapes[0]
    assert isinstance(jp, EditablePath) and jp.closed
    assert sum(1 for e in jp.edges if e.kind == "arc") == 4

    # two separate lines sharing an endpoint weld into one 3-point path
    doc2 = Document(); c.doc = doc2; c.rebuild()
    a = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(20, 0)], close_path=False,
                              transform=Transform(x=0, y=0), layer="Cut"))
    b = c.add_shape(PathShape(points=[Vec2(20, 0), Vec2(20, 20)], close_path=False,
                              transform=Transform(x=0, y=0), layer="Cut"))
    c.scene_obj.clearSelection(); a.setSelected(True); b.setSelected(True)
    c.join_selected()
    assert len(c.doc.shapes) == 1
    assert isinstance(c.doc.shapes[0], PathShape)
    assert len(c.doc.shapes[0].points) == 3


def test_node_snap_while_editing(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_to_nodes = True
    a = c.add_shape(Rectangle(width=40, height=40, transform=Transform(x=0, y=0),
                              layer="Cut"))          # corner at (20,20)
    b = c.add_shape(Rectangle(width=30, height=30, transform=Transform(x=100, y=0),
                              layer="Cut"))
    c.scene_obj.clearSelection(); b.setSelected(True)
    c.convert_to_nodes()                             # b -> polygon, editing
    h = c._handles[0]
    c.begin_node_snap(h)
    res = c.snap_node(QPointF(20.4, 19.6))           # near A's corner (20,20)
    assert abs(res.x() - 20) < 1e-6 and abs(res.y() - 20) < 1e-6
    c.end_node_snap()


def test_outline_hit_testing(qapp):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QTransform
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    item = c.add_shape(Rectangle(width=80, height=50, transform=Transform(x=0, y=0),
                                 layer="Cut"))
    scene = c.scene_obj
    scene.clearSelection(); c.selection_changed()   # drop box-resize handles
    assert scene.itemAt(QPointF(40, 0), QTransform()) is item   # on right edge
    assert scene.itemAt(QPointF(0, 0), QTransform()) is None     # empty interior


def test_convert_shape_to_nodes(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import Polygon, PathShape

    win = MainWindow(Document())
    c = win.canvas
    r = c.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                              stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                              layer="Cut"))
    c.scene_obj.clearSelection(); r.setSelected(True)
    c.convert_to_nodes()
    new = win.doc.shapes[0]
    assert isinstance(new, Polygon) and len(new.points) == 4
    assert new.stitch is not None            # stitching preserved
    assert len(c._handles) == 4              # entered vertex-edit mode
    xs = sorted({round(p.x, 1) for p in new.points})
    ys = sorted({round(p.y, 1) for p in new.points})
    assert xs == [-30.0, 30.0] and ys == [-20.0, 20.0]


def test_ortho_snaps_onto_crossed_edge(qapp):
    """Shift-ortho must still object-snap: locked horizontal from a point on a
    fold line, the endpoint snaps to where the ray crosses a vertical cut
    edge (regression -- ortho used to discard the snap entirely)."""
    from PySide6.QtCore import QPointF, Qt
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, PathShape, Transform
    from leathercad.geometry import Vec2
    import leathercad_app.canvas as cm

    win = MainWindow(Document())
    c = win.canvas
    c.snap_to_nodes = True
    c.snap_to_grid = False
    # a vertical cut edge at x = 100 (rectangle left side) + a fold line at y=50
    c.add_shape(Rectangle(width=60, height=80, transform=Transform(x=130, y=50),
                          layer="Cut"))
    c.add_shape(PathShape(name="Fold", points=[Vec2(0, 50), Vec2(60, 50)],
                          close_path=False, transform=Transform(x=0, y=0),
                          layer="Score"))
    c.rebuild()
    c.tool = cm.LINE
    c._start = QPointF(20.0, 50.0)                 # first point on the fold line

    class _Shift:
        def modifiers(self):
            return Qt.ShiftModifier

    # cursor near the vertical edge, slightly off the horizontal: ortho locks
    # y to 50 AND snaps x to the crossed edge at 100
    raw = QPointF(98.5, 53.0)
    pos, applied, kind = c._maybe_ortho(raw, raw, _Shift())
    assert applied and kind == "cross"
    assert abs(pos.x() - 100.0) < 1e-6 and abs(pos.y() - 50.0) < 1e-6
    # also catches the edge when the cursor has passed it
    pos2, _a, k2 = c._maybe_ortho(QPointF(101.5, 53.0), QPointF(101.5, 53.0),
                                  _Shift())
    assert k2 == "cross" and abs(pos2.x() - 100.0) < 1e-6
    # far from any edge: plain ortho, y locked, x free (no phantom snap)
    pos3, _a3, k3 = c._maybe_ortho(QPointF(60.0, 53.0), QPointF(60.0, 53.0),
                                   _Shift())
    assert k3 is None and abs(pos3.y() - 50.0) < 1e-6 and abs(pos3.x() - 60.0) < 1
    # node-snap off: ortho stays a plain lock, no edge snapping
    c.snap_to_nodes = False
    pos4, _a4, k4 = c._maybe_ortho(QPointF(98.5, 53.0), QPointF(98.5, 53.0),
                                   _Shift())
    assert k4 is None and abs(pos4.y() - 50.0) < 1e-6      # y still locked
    assert abs(pos4.x() - 100.0) > 1.0                     # but NOT on the edge


def test_magnetic_node_snap_on_move(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_to_nodes = True
    c.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0), layer="Cut"))
    b = c.add_shape(Rectangle(width=20, height=20, transform=Transform(x=100, y=100), layer="Cut"))
    c.scene_obj.clearSelection(); b.setSelected(True)
    c.begin_move_snap(b)
    # dragging B so its +10,+10 corner nears A's corner (30,20) snaps exactly
    res = c.snap_move(b, QPointF(20.6, 10.5))
    assert abs(res.x() - 20) < 1e-6 and abs(res.y() - 10) < 1e-6
    # far away: unchanged (free movement)
    res2 = c.snap_move(b, QPointF(300, 300))
    assert (res2.x(), res2.y()) == (300, 300)
    c.end_move_snap()
    assert c._snap_cache is None


def test_seam_move_bakes_on_release_not_per_tick(qapp):
    """Regression: moving a seam must not reset setPos(0,0) mid-drag (which made
    items jump). Points bake to the final position on release."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import StitchLineItem
    from leathercad.document import Document
    from leathercad.stitchline import StitchLine
    from leathercad.stitchsettings import StitchSettings
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    sl = StitchLine(points=[Vec2(0, 0), Vec2(50, 0)],
                    settings=StitchSettings(pitch_mm=4.0, fit="endpoints"))
    item = c.add_stitch_line(sl)
    # simulate a drag: Qt moves the item via setPos; points bake on release
    item.setPos(20, -15)                       # base was points[0] = (0,0)
    assert sl.points[0] == Vec2(0, 0)          # not mutated mid-drag
    item._bake_move()                          # what mouseReleaseEvent calls
    assert abs(sl.points[0].x - 20) < 1e-6 and abs(sl.points[0].y + 15) < 1e-6
    assert abs(sl.points[1].x - 70) < 1e-6     # whole seam translated


def test_snapping(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_to_nodes = True
    c.snap_grid = 1.0

    p, vtx = c.snap(QPointF(12.3, 7.8))
    assert (round(p.x()), round(p.y())) == (12, 8) and not vtx

    c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                          stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                          layer="Cut"))
    p, vtx = c.snap(QPointF(19.4, 14.6))
    assert (round(p.x()), round(p.y())) == (20, 15) and vtx

    # Moving is free (follows the cursor exactly) -- snapping applies while
    # drawing, not while dragging existing shapes.
    item = _shape_items(c)[0]
    item.setPos(10.4, -3.7)
    assert (round(item.model.transform.x, 1), round(item.model.transform.y, 1)) \
        == (10.4, -3.7)


def test_hole_slot_score_tools(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import Circle, Rectangle, PathShape

    win = MainWindow(Document())
    c = win.canvas

    c.tool = cm.HOLE
    c.hole_tool_diameter = 5.0
    c._place_hole(QPointF(20, 10))
    assert isinstance(win.doc.shapes[-1], Circle)
    assert win.doc.shapes[-1].stitch is None  # hardware hole, cut only

    c.tool = cm.SLOT
    c._finalize_drag(QPointF(0, 0), QPointF(40, 12))
    slot = win.doc.shapes[-1]
    assert isinstance(slot, Rectangle) and abs(slot.corner_radius - 6.0) < 1e-6

    c.tool = cm.SCORE
    c._poly_pts = [QPointF(0, 0), QPointF(30, 0), QPointF(30, 20)]
    c._finalize_poly()
    score = win.doc.shapes[-1]
    assert isinstance(score, PathShape) and score.layer == "Score"
    assert not score.close_path


def test_vertex_editing(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.canvas import Transform
    from leathercad.document import Document
    from leathercad.shapes import Polygon
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    poly = Polygon(points=[Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20),
                           Vec2(-20, 20)],
                   close_path=True, transform=Transform(x=100, y=0))
    item = c.add_shape(poly)
    c.enter_vertex_edit(item)
    assert len(c._handles) == 4
    c._handles[0].setPos(70, -30)   # world -> local (-30,-30) via inverse xform
    assert abs(poly.points[0].x - (-30)) < 1e-6
    assert abs(poly.points[0].y - (-30)) < 1e-6
    c.clear_vertex_handles()
    assert c._handles == []


def test_tool_palette_left_and_pinnable(qapp):
    from PySide6.QtCore import Qt
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    tp = win._tool_palette
    assert win.toolBarArea(tp) == Qt.LeftToolBarArea
    assert tp.isMovable()               # draggable when unpinned
    win.act_pin.setChecked(True)
    assert not tp.isMovable()           # pinned = locked in place
    win.act_pin.setChecked(False)
    assert tp.isMovable()


def test_ungroup_makes_individual_holes_then_group_back(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import HoleItem
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    item = c.add_shape(Rectangle(
        width=80, height=50, corner_radius=6, transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=4.0, inset=3.0), layer="Cut"))
    n = item.hole_count
    assert n > 0

    # Ungroup -> individual, directly-selectable holes (no box, no edit mode)
    c.scene_obj.clearSelection()
    item.setSelected(True)
    c.ungroup_selected()
    assert item.model.stitch.enabled is False
    assert item.hole_count == 0
    holes = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]
    assert len(holes) == n
    assert len(win.doc.holes) == n

    # each hole is individually selectable and deletable
    c.scene_obj.clearSelection()
    for h in holes[:3]:
        h.setSelected(True)
    c.delete_selected()
    assert len(win.doc.holes) == n - 3

    # fiddling the (disabled) shape settings must NOT bring holes back
    item.model.stitch.pitch_mm = 2.0
    item.sync_from_model()
    assert item.hole_count == 0
    assert len(win.doc.holes) == n - 3

    # Group the remaining holes back into the shape
    remaining = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]
    c.scene_obj.clearSelection()
    item.setSelected(True)
    for h in remaining:
        h.setSelected(True)
    c.group_selected()
    assert win.doc.holes == []
    assert item.model.baked_holes is not None
    assert len(item.model.baked_holes) == n - 3
    assert item.hole_count == n - 3  # now render as baked, move with the shape


def test_group_holes_adopts_their_style(qapp):
    """Baked holes render in the shape's stitch STYLE, so grouping must adopt
    the grouped holes' style -- slit holes baked into a round-stitched shape
    must come out slit, not round (regression)."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import HoleItem
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    # a shape already carrying a ROUND stitch...
    item = c.add_shape(Rectangle(
        width=60, height=40, transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False, hole_style="round"), layer="Cut"))
    # ...and SLIT loose holes
    for x in (-20, 0, 20):
        win.doc.holes.append(LooseHole(point=Vec2(x, -18), hole_style="slit",
                                       slit_length=2.0, slit_angle=25.0))
    c.rebuild()
    item = next(it for it in c.scene_obj.items()
                if getattr(it, "model", None) is not None
                and it.model is win.doc.shapes[0])
    c.scene_obj.clearSelection()
    item.setSelected(True)
    for h in [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]:
        h.setSelected(True)
    c.group_selected()
    st = item.model.stitch
    assert st.hole_style == "slit"           # adopts the holes' style
    assert st.slit_length == 2.0 and st.slit_angle == 25.0


def test_grouped_holes_move_with_shape(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.stitching import Hole
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    r = Rectangle(width=40, height=30, transform=Transform(x=0, y=0), layer="Cut")
    r.baked_holes = [Hole(Vec2(0, 0), Vec2(1, 0))]
    item = c.add_shape(r)
    # world hole starts at shape origin (0,0); move shape, hole follows
    item.setPos(50, 20)
    win.doc  # baked hole is local (0,0) -> world (50,20) after move
    _, _, _ = r.world_polyline()
    world = r.transform.apply(r.baked_holes[0].point)
    assert abs(world.x - 50) < 1e-6 and abs(world.y - 20) < 1e-6


def test_make_back_piece_mirrors_and_registers(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.stitching import holes_for_shape

    doc = Document()
    doc.add_shape(Rectangle(width=90, height=60, corner_radius=10,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=3.85, inset=3.5),
                            layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    front = _shape_items(c)[0]
    c.scene_obj.clearSelection()
    front.setSelected(True)
    c.make_back_piece_selected()

    items = _shape_items(c)
    assert len(items) == 2
    back = [it for it in items if it.model is not front.model][0]
    # the back piece is the mirror image, placed clear of the original
    assert back.model.transform.mirror_x != front.model.transform.mirror_x
    assert back.hole_count == front.hole_count
    assert back.model.bounds()[0] >= front.model.bounds()[2]  # to the right

    # registration: every front hole has a partner on the back that is its
    # exact mirror image about the midline between the two pieces.
    fh = holes_for_shape(front.model).holes
    bh = holes_for_shape(back.model).holes
    axis = 0.5 * (front.model.transform.x + back.model.transform.x)
    for h in fh:
        mx, my = 2 * axis - h.point.x, h.point.y
        assert any(abs(b.point.x - mx) < 1e-6 and abs(b.point.y - my) < 1e-6
                   for b in bh), "front hole has no mirrored partner on the back"


def test_trim_tool_cuts_outline_at_intersections(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.canvas import TRIM
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.geometry import Vec2

    doc = Document()
    # two overlapping sharp rectangles; the right edge of A (x=30) passes
    # through B, and B's left edge (x=20) passes through A.
    a = Rectangle(width=60, height=40, transform=Transform(x=0, y=0), layer="Cut")
    b = Rectangle(width=60, height=40, transform=Transform(x=40, y=0), layer="Cut")
    doc.add_shape(a)
    doc.add_shape(b)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    n_before = len(_shape_items(c))
    assert n_before == 2

    # Trim A's right edge (x=30) at the point where B overlaps it. A spans
    # x in [-30, 30], B spans [10, 70]; A's right edge x=30 lies inside B,
    # so clicking it removes that edge back to B's top/bottom crossings.
    c.tool = TRIM
    c._do_trim(Vec2(30.0, 0.0))

    items = _shape_items(c)
    # A became an open path (still one item), B untouched -> still 2 items
    assert len(items) == 2
    # the trimmed piece is now an open outline (not a closed Rectangle)
    from leathercad.shapes import Rectangle as R
    kinds = sorted(type(it.model).__name__ for it in items)
    assert "Rectangle" in kinds            # B is still a rectangle
    assert any(not isinstance(it.model, R) for it in items)   # A was trimmed


def test_grid_and_node_snap_toggle_independently(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_grid = 1.0
    c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                          layer="Cut"))          # a corner at (20, 15)

    # node only (grid OFF): a point far from any node stays exactly free
    c.snap_to_nodes, c.snap_to_grid = True, False
    p, vtx = c.snap(QPointF(12.3, 7.8))
    assert (round(p.x(), 1), round(p.y(), 1)) == (12.3, 7.8) and not vtx
    # ...but near a node it still snaps
    p, vtx = c.snap(QPointF(19.6, 14.7))
    assert (round(p.x()), round(p.y())) == (20, 15) and vtx

    # grid only (nodes OFF): rounds to the grid, ignores nodes
    c.snap_to_nodes, c.snap_to_grid = False, True
    p, vtx = c.snap(QPointF(12.3, 7.8))
    assert (round(p.x()), round(p.y())) == (12, 8) and not vtx

    # both OFF: fully free
    c.snap_to_nodes, c.snap_to_grid = False, False
    p, vtx = c.snap(QPointF(12.3, 7.8))
    assert (round(p.x(), 1), round(p.y(), 1)) == (12.3, 7.8) and not vtx


def test_intersection_snap(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2

    win = MainWindow(Document())
    c = win.canvas
    c.snap_to_nodes, c.snap_to_grid = True, False
    # two lines crossing at (10, 10) whose own nodes/midpoints avoid that point
    c.add_shape(PathShape(points=[Vec2(-10, 10), Vec2(40, 10)], close_path=False,
                          transform=Transform(x=0, y=0), layer="Cut"))
    c.add_shape(PathShape(points=[Vec2(10, -5), Vec2(10, 20)], close_path=False,
                          transform=Transform(x=0, y=0), layer="Cut"))
    p, vtx = c.snap(QPointF(10.3, 9.7))            # near the crossing
    assert (round(p.x()), round(p.y())) == (10, 10) and vtx


def test_smart_snap_alignment_and_guides(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.canvas import LINE
    from leathercad.document import Document

    doc = Document()
    # two separate rectangles so an x-alignment to one and a y-alignment to the
    # other cross at a point that is NOT a node (otherwise a direct snap wins).
    doc.add_shape(Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                            layer="Cut"))     # centre y = 0
    doc.add_shape(Rectangle(width=40, height=30, transform=Transform(x=60, y=40),
                            layer="Cut"))     # left edge x = 40
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.tool = LINE
    # near rect-B's left edge (x=40) and rect-A's centre line (y=0)
    p, vtx, guides, kind = c._smart_snap(QPointF(39.4, 0.6))
    assert abs(p.x() - 40.0) < 1e-6      # locked to B's edge x
    assert abs(p.y() - 0.0) < 1e-6       # locked to A's centre y
    assert vtx and len(guides) == 2 and kind == "align"


def test_snap_reports_kind_including_circle_centre(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import Circle
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_to_nodes, c.snap_to_grid = True, False
    c.add_shape(Circle(rx=15, ry=15, transform=Transform(x=30, y=20), layer="Cut"))
    p, vtx, guides, kind = c._smart_snap(QPointF(30.5, 19.6))   # near the centre
    assert kind == "center" and (round(p.x()), round(p.y())) == (30, 20)


def test_snap_to_stitch_hole_centre(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document

    doc = Document()
    doc.add_shape(Rectangle(width=80, height=50, corner_radius=8,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0, inset=4.0),
                            layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.snap_to_nodes, c.snap_to_grid = True, False
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]
    holes = [p for p, k in it.world_snap_nodes_typed() if k == "hole"]
    assert holes                                    # stitch holes are snap targets
    h = holes[0]
    p, vtx, guides, kind = c._smart_snap(QPointF(h.x + 0.5, h.y - 0.4))
    assert kind == "hole" and abs(p.x() - h.x) < 1e-6 and abs(p.y() - h.y) < 1e-6


def test_snap_nodes_are_geometry_not_bbox(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.shapes import Circle
    from leathercad.document import Document
    from collections import Counter

    win = MainWindow(Document())
    c = win.canvas
    c.add_shape(Circle(rx=20, ry=20, transform=Transform(x=0, y=0), layer="Cut"))
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]
    kinds = Counter(k for _p, k in it.world_snap_nodes_typed())
    # a circle offers its centre + 4 quadrants -- no phantom bbox corners
    assert kinds == Counter({"center": 1, "quad": 4})


def test_construction_line_ends_where_drawn(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.canvas import CONSTRUCTION
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.tool = CONSTRUCTION
    c._finalize_drag(QPointF(-10, 0), QPointF(10, 0))
    g = [it.model for it in _shape_items(c)
         if getattr(it.model, "construction", False)][0]
    xs = sorted(p.x for p in g.world_polyline()[0])
    assert abs(xs[0] + 10) < 1e-6 and abs(xs[-1] - 10) < 1e-6   # no overshoot


def test_line_and_construction_tools_create_shapes(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.canvas import LINE, CONSTRUCTION
    from leathercad.shapes import PathShape
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.tool = LINE
    c._finalize_drag(QPointF(-20, 5), QPointF(20, 5))
    c.tool = CONSTRUCTION
    c._finalize_drag(QPointF(0, -20), QPointF(0, 20))

    shapes = [it.model for it in _shape_items(c)]
    assert any(isinstance(s, PathShape) and not s.construction for s in shapes)
    assert any(getattr(s, "construction", False) for s in shapes)


def test_click_to_place_vs_drag_drawing(qapp):
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.resize(400, 400)

    def press(x, y):
        vp = QPointF(c.mapFromScene(QPointF(x, y)))
        c.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, vp,
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def release(x, y):
        vp = QPointF(c.mapFromScene(QPointF(x, y)))
        c.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, vp,
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    # click-to-place (default): first press+release makes nothing; 2nd click does
    c.drag_to_draw = False
    c.tool = cm.RECT
    n = len(win.doc.shapes)
    press(-40, -25)
    release(-40, -25)
    assert len(win.doc.shapes) == n            # waiting for the second click
    press(40, 25)
    assert len(win.doc.shapes) == n + 1        # second click finishes it

    # drag mode: a single press-drag-release makes one shape
    c.drag_to_draw = True
    c.tool = cm.RECT
    n = len(win.doc.shapes)
    press(-60, -60)
    release(60, 60)
    assert len(win.doc.shapes) == n + 1


def test_every_drawing_tool_is_click_to_place(qapp):
    """No drawing tool needs click-and-hold: 2-point tools take two clicks,
    polygon/seam/score take clicks then a double-click, hole is one click."""
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.resize(500, 500)
    c.drag_to_draw = False

    def _ev(kind, x, y):
        vp = QPointF(c.mapFromScene(QPointF(x, y)))
        return QMouseEvent(kind, vp, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

    def press(x, y):
        c.mousePressEvent(_ev(QEvent.MouseButtonPress, x, y))

    def release(x, y):
        c.mouseReleaseEvent(_ev(QEvent.MouseButtonRelease, x, y))

    def click(x, y):
        press(x, y)
        release(x, y)

    def dbl(x, y):
        c.mouseDoubleClickEvent(_ev(QEvent.MouseButtonDblClick, x, y))

    # every two-point tool: click, click
    for tool in (cm.RECT, cm.ROUNDED, cm.ELLIPSE, cm.CIRCLE, cm.SLOT,
                 cm.LINE, cm.CONSTRUCTION):
        n = len(win.doc.shapes)
        c.tool = tool
        click(-40, -28)
        click(40, 28)
        assert len(win.doc.shapes) == n + 1, f"{tool} did not click-to-place"

    # hole: a single click
    n = len(win.doc.shapes)
    c.tool = cm.HOLE
    click(5, 5)
    assert len(win.doc.shapes) == n + 1

    # polygon / score: clicks then double-click to finish
    for tool in (cm.POLYGON, cm.SCORE):
        n = len(win.doc.shapes)
        c.tool = tool
        for (x, y) in [(-30, -20), (30, -20), (0, 25)]:
            click(x, y)
        dbl(0, 25)
        assert len(win.doc.shapes) == n + 1, f"{tool} did not click-to-place"

    # stitch line (seam): clicks then double-click
    n = len(win.doc.stitch_lines)
    c.tool = cm.STITCHLINE
    click(-20, 0)
    click(20, 0)
    dbl(20, 0)
    assert len(win.doc.stitch_lines) == n + 1


def test_tool_shortcuts_unique_and_unambiguous():
    from leathercad_app.mainwindow import TOOLS
    keys = [k for _n, _m, k in TOOLS]
    assert len(keys) == len(set(keys))          # no duplicated keys
    by_name = {n: k for n, _m, k in TOOLS}
    assert by_name["Line"] == "L"               # Line is L (was confusable "I")
    assert by_name["Stitch line (seam)"] == "M"


def test_delete_action_removes_selection(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    it = c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                               layer="Cut"))
    c.scene_obj.clearSelection()
    it.setSelected(True)
    n = len(win.doc.shapes)
    # the Delete menu action is what Del / Backspace trigger
    keys = {s.toString().lower() for s in win.act_del.shortcuts()}
    assert "del" in keys or "backspace" in keys
    win.act_del.trigger()
    assert len(win.doc.shapes) == n - 1


def test_node_edit_snaps_to_other_object_not_itself(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    a = c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=0, y=0),
                              layer="Cut"))          # corner at (20, 15)
    c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=100, y=0),
                          layer="Cut"))              # corner at (80, 15)
    c.snap_to_nodes, c.snap_to_grid = True, False

    class Handle:                                    # stand-in for a VertexHandle
        owner = a

        def pos(self):
            return QPointF(20, 15)

    c.begin_node_snap(Handle())
    # dragging a's node near the OTHER rect's corner snaps to it
    snapped = c.snap_node(QPointF(79.5, 14.7))
    assert (round(snapped.x()), round(snapped.y())) == (80, 15)
    # ...but it does NOT stick to a's own neighbouring corner (owner excluded)
    free = c.snap_node(QPointF(-19.5, 14.7))
    assert (round(free.x(), 1), round(free.y(), 1)) == (-19.5, 14.7)


def test_open_line_is_clickable_and_not_a_closed_sliver(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.document import Document

    win = MainWindow(Document())
    it = win.canvas.add_shape(PathShape(points=[Vec2(-20, 0), Vec2(20, 0)],
                                        close_path=False,
                                        transform=Transform(x=0, y=0), layer="Cut"))
    assert it._closed is False
    assert it.shape().contains(it.mapFromScene(QPointF(0, 0)))    # on the line


def test_split_midpoints_from_intersection(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    # a 10-long line bisected at x=5 -> quarter points at 2.5 and 7.5
    c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(10, 0)], close_path=False,
                          transform=Transform(x=0, y=0), layer="Cut"))
    c.add_shape(PathShape(points=[Vec2(5, -5), Vec2(5, 5)], close_path=False,
                          transform=Transform(x=0, y=0), layer="Cut"))
    c.snap_to_nodes, c.snap_to_grid = True, False
    p, vtx, guides, kind = c._smart_snap(QPointF(2.4, 0.2))
    assert (round(p.x(), 1), round(p.y(), 1)) == (2.5, 0.0) and kind == "mid"


def test_junction_midpoint_snaps_from_far_along_the_sub_segment(qapp):
    # A construction line crossing a regular line makes a junction; the midpoint
    # of each sub-segment must be snappable even when you hover on it far from
    # the crossing (the crossing line is out of the cursor radius there).
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(100, 0)], close_path=False,
                          transform=Transform(x=0, y=0), layer="Cut"))
    cline = PathShape(points=[Vec2(40, -30), Vec2(40, 30)], close_path=False,
                      transform=Transform(x=0, y=0), layer="Score")
    cline.construction = True
    c.add_shape(cline)
    c.snap_to_nodes, c.snap_to_grid = True, False
    # hover on the (0..40) sub-segment midpoint (20,0) -- 20 mm from the guide
    p, vtx, guides, kind = c._smart_snap(QPointF(20.4, 0.3))
    assert (round(p.x(), 1), round(p.y(), 1)) == (20.0, 0.0) and kind == "mid"


def test_construction_line_body_snaps(qapp):
    # A diagonal construction/guide line is snappable anywhere along its body
    # (the "nearest point on line" osnap), not just at its end/mid nodes.
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape, Rectangle
    from leathercad.geometry import Vec2
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    cline = PathShape(points=[Vec2(0, 0), Vec2(100, 100)], close_path=False,
                      transform=Transform(x=0, y=0), layer="Score")
    cline.construction = True
    c.add_shape(cline)
    # a solid closed shape must NOT get body-snapping on its edges
    c.add_shape(Rectangle(width=40, height=30, transform=Transform(x=200, y=200)))
    c.snap_to_nodes, c.snap_to_grid = True, False
    p, vtx, guides, kind = c._smart_snap(QPointF(31, 29))     # off the diagonal
    assert kind == "edge" and abs(p.x() - p.y()) < 1e-6       # foot lands on y=x
    # the rectangle's plain body should not snap as "edge"
    p2, _v, _g, k2 = c._smart_snap(QPointF(212, 214))
    assert k2 != "edge"


def test_node_shift_ortho_locks_movement_from_drag_start(qapp):
    # Shift while dragging a node locks its MOVEMENT to 0/45/90 from where the
    # drag began (like the drawing tools) -- so an endpoint that starts well off
    # the neighbour's axis can still be dragged straight up into a vertical line.
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.document import Document
    from PySide6.QtWidgets import QGraphicsItem
    from PySide6.QtCore import QPointF
    from leathercad_app.items import VertexHandle

    win = MainWindow(Document())
    # far-right endpoint sits up and to the right of its neighbour, like the
    # 3-point score line in the bug report
    it = win.canvas.add_shape(PathShape(points=[Vec2(0, 0), Vec2(100, 0),
                                                Vec2(140, 20)],
                                        close_path=False,
                                        transform=Transform(x=0, y=0), layer="Cut"))
    nodes = it.editable_nodes()
    end = next(n for n in nodes
               if (round(n.world.x), round(n.world.y)) == (140, 20))
    h = VertexHandle(end, it, win.canvas)
    win.canvas.scene_obj.addItem(h)
    h._shift = True
    h._drag_start = QPointF(140, 20)
    # dragging up (mostly vertical) locks the movement vertical -> x stays 140,
    # which the old neighbour-relative lock could never reach for this node
    r = h.itemChange(QGraphicsItem.ItemPositionChange, QPointF(150, 80))
    assert round(r.x()) == 140
    # dragging sideways locks horizontal -> y stays 20
    r = h.itemChange(QGraphicsItem.ItemPositionChange, QPointF(220, 25))
    assert round(r.y()) == 20


def test_shift_ortho_while_drawing(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    c = MainWindow(Document()).canvas
    # from the origin, a mostly-horizontal cursor is forced flat (0 deg)
    r = c._apply_ortho(QPointF(0, 0), QPointF(30, 8))
    assert round(r.x(), 1) == 31.0 and round(r.y(), 1) == 0.0
    # mostly-vertical -> 90 deg; diagonal -> 45 deg
    r = c._apply_ortho(QPointF(0, 0), QPointF(8, 30))
    assert round(r.x(), 1) == 0.0 and round(r.y(), 1) == 31.0
    r = c._apply_ortho(QPointF(0, 0), QPointF(20, 22))
    assert round(r.x(), 1) == round(r.y(), 1)


def test_escape_returns_to_pointer(qapp):
    from PySide6.QtCore import Qt, QEvent
    from PySide6.QtGui import QKeyEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.tool = cm.RECT
    # nothing in progress: Esc drops back to the pointer / Select tool
    c.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert c.tool == cm.SELECT


def test_ortho_anchor_for_poly_tools(qapp):
    # Shift-ortho must work for score / stitch (polyline) tools, anchored on the
    # last placed point -- not just the 2-point line / construction tools.
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document

    c = MainWindow(Document()).canvas
    for tool in (cm.SCORE, cm.STITCHLINE, cm.POLYGON):
        c.tool = tool
        c._poly_pts = [QPointF(10, 10)]
        assert c._ortho_anchor() == QPointF(10, 10)
        locked = c._apply_ortho(c._ortho_anchor(), QPointF(40, 18))
        assert round(locked.y(), 1) == 10.0     # forced horizontal
    # no anchor yet -> no ortho
    c._poly_pts = []
    assert c._ortho_anchor() is None


def test_circle_drag_locks_center(qapp):
    # Dragging a circle must lock its CENTRE to nearby object nodes, even though
    # its quadrants sit closer to the cursor.
    from PySide6.QtCore import QPointF
    from leathercad_app import canvas as cm
    from leathercad.shapes import Circle, Rectangle, Transform
    from leathercad.document import Document

    doc = Document()
    doc.add_shape(Rectangle(width=20, height=20, transform=Transform(x=40, y=40)))
    circ = Circle(rx=5, ry=5, transform=Transform(x=48, y=48))
    doc.add_shape(circ)
    c = cm.Canvas(doc)
    c.snap_to_nodes = True
    c.snap_to_grid = False
    c.rebuild()
    citem = next(it for it in c.scene_obj.items()
                 if getattr(it, "model", None) is circ)
    assert getattr(citem, "_center_snap_priority", False) is True
    c.begin_move_snap(citem)
    # rect corner is at (50, 50); nudging the centre there must lock the centre
    res = c.snap_move(citem, QPointF(49.6, 49.6))
    assert round(res.x(), 2) == 50.0 and round(res.y(), 2) == 50.0


def test_loose_hole_drag_snaps_center(qapp):
    # Moving an individual (ungrouped) stitch hole must snap its centre to
    # nearby object nodes, just like a circle.
    from PySide6.QtCore import QPointF
    from leathercad_app import canvas as cm
    from leathercad.shapes import Rectangle, Transform
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_shape(Rectangle(width=20, height=20, transform=Transform(x=40, y=40)))
    hole = LooseHole(point=Vec2(48, 48))
    doc.holes.append(hole)
    c = cm.Canvas(doc)
    c.snap_to_nodes = True
    c.snap_to_grid = False
    c.rebuild()
    hitem = next(it for it in c.scene_obj.items()
                 if getattr(it, "hole", None) is hole)
    assert getattr(hitem, "_center_snap_priority", False) is True
    c.begin_move_snap(hitem)
    res = c.snap_move(hitem, QPointF(49.6, 49.6))   # toward the (50, 50) corner
    assert round(res.x(), 2) == 50.0 and round(res.y(), 2) == 50.0


def test_duplicate_selected_loose_holes(qapp):
    # Selecting several loose holes and duplicating must add copies (with fresh
    # ids), not silently do nothing.
    from leathercad_app import canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    doc = Document()
    for x in (10, 20, 30):
        doc.holes.append(LooseHole(point=Vec2(x, 10)))
    c = cm.Canvas(doc)
    c.rebuild()
    for it in c.scene_obj.items():
        if getattr(it, "hole", None) is not None:
            it.setSelected(True)
    c.duplicate_selected()
    assert len(doc.holes) == 6
    ids = [h.hole_id for h in doc.holes]
    assert len(set(ids)) == len(ids)          # ids stay unique


def test_toggle_stitch_after_move_keeps_position(qapp):
    # Moving a shape on the canvas then toggling Stitching must not snap the
    # shape back to where it was when the panel was last populated.
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import Rectangle, Transform
    from leathercad.document import Document

    doc = Document()
    rect = Rectangle(width=40, height=30, transform=Transform(x=50, y=50))
    doc.add_shape(rect)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    item = next(it for it in c.scene_obj.items()
                if getattr(it, "model", None) is rect)
    item.setSelected(True)
    win._selection_changed()                  # panel loaded at (50, 50)
    rect.transform.x, rect.transform.y = 80, 20
    c.item_moved(item)                        # canvas drag -> panel must re-sync
    assert win.properties.pos_x.value() == 80.0
    win.properties.g_stitch.setChecked(True)  # toggling stitching runs _apply
    win.properties._apply()
    assert rect.transform.x == 80.0 and rect.transform.y == 20.0


def test_move_group_select_and_move_together(qapp):
    # Grouping several loose holes lets you select and move them as a unit:
    # clicking one member selects the whole group (Qt then moves all together).
    from leathercad_app import canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    doc = Document()
    for x in (10, 20, 30):
        doc.holes.append(LooseHole(point=Vec2(x, 10)))
    c = cm.Canvas(doc)
    c.rebuild()
    holes = [it for it in c.scene_obj.items()
             if getattr(it, "hole", None) is not None]
    for h in holes:
        h.setSelected(True)
    c.make_group()
    gids = {h.group_id for h in doc.holes}
    assert len(gids) == 1 and None not in gids            # one shared group id

    # pressing a single member grabs the whole group (press-driven, so a drag
    # moves them together and the cascade can't leave the group 'stuck')
    c.scene_obj.clearSelection()
    holes[0].setSelected(True)
    c.select_group_of(holes[0])
    assert len(c.selected_items()) == 3

    # ungroup clears membership; pressing a member then stays single
    c.ungroup_group()
    assert {h.group_id for h in doc.holes} == {None}
    c.scene_obj.clearSelection()
    holes[0].setSelected(True)
    c.select_group_of(holes[0])
    assert len(c.selected_items()) == 1


def test_group_drag_snaps_to_node_and_stays_rigid(qapp):
    # Dragging a group of loose holes must snap the group onto a nearby node
    # (e.g. a line endpoint) while keeping the members' spacing rigid.
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from leathercad_app import canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.shapes import PathShape, Transform
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(60, 50), Vec2(100, 50)],
                            close_path=False, transform=Transform(x=0, y=0),
                            layer="Cut"))
    for x in (10, 20, 30):
        doc.holes.append(LooseHole(point=Vec2(x, 10)))
    c = cm.Canvas(doc)
    c.snap_to_nodes = True
    c.snap_to_grid = False
    c.tool = cm.SELECT
    c.rebuild()
    c.resize(700, 700)
    c.show()
    holes = {round(it.hole.point.x): it for it in c.scene_obj.items()
             if getattr(it, "hole", None) is not None}
    for h in holes.values():
        h.setSelected(True)
    c.make_group()
    c.scene_obj.clearSelection()
    vp = c.viewport()

    def send(kind, world, btns=Qt.LeftButton, btn=Qt.LeftButton):
        pt = c.mapFromScene(QPointF(world))
        QApplication.sendEvent(vp, QMouseEvent(
            kind, QPointF(pt), vp.mapToGlobal(pt), btn, btns, Qt.NoModifier))

    send(QEvent.MouseButtonPress, QPointF(30, 10))       # grab the (30,10) hole
    qapp.processEvents()
    send(QEvent.MouseMove, QPointF(70, 30))
    qapp.processEvents()
    send(QEvent.MouseMove, QPointF(99.4, 49.6))          # near the line end
    qapp.processEvents()
    send(QEvent.MouseButtonRelease, QPointF(99.4, 49.6), Qt.NoButton)
    qapp.processEvents()

    pts = sorted((round(h.point.x, 2), round(h.point.y, 2)) for h in doc.holes)
    assert (100.0, 50.0) in pts                          # leader snapped to end
    assert pts == [(80.0, 50.0), (90.0, 50.0), (100.0, 50.0)]  # spacing rigid


def test_duplicate_does_not_inherit_group(qapp):
    # Duplicating grouped items must NOT leave the copies in the original group
    # (otherwise every new duplicate "latches" onto the group).
    from leathercad_app import canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    doc = Document()
    for x in (10, 20, 30):
        doc.holes.append(LooseHole(point=Vec2(x, 10)))
    c = cm.Canvas(doc)
    c.rebuild()
    holes = [it for it in c.scene_obj.items()
             if getattr(it, "hole", None) is not None]
    for h in holes:
        h.setSelected(True)
    c.make_group()
    c.duplicate_selected()
    assert {h.group_id for h in doc.holes[3:]} == {None}   # copies ungrouped


def test_make_back_piece_mirrors_loose_holes(qapp):
    # Mirroring a shape must also mirror the loose holes inside it, registered
    # to the mirrored copy for back-to-back stitching.
    from leathercad_app import canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.shapes import Rectangle, Transform
    from leathercad.geometry import Vec2

    doc = Document()
    rect = Rectangle(width=40, height=30, transform=Transform(x=50, y=50))
    doc.add_shape(rect)
    doc.holes.append(LooseHole(point=Vec2(40, 50)))   # inside the rect
    doc.holes.append(LooseHole(point=Vec2(60, 50)))
    c = cm.Canvas(doc)
    c.rebuild()
    item = next(it for it in c.scene_obj.items()
                if getattr(it, "model", None) is rect)
    item.setSelected(True)
    c.make_back_piece_selected()
    assert len(doc.holes) == 4                         # both holes mirrored
    mirrored = sorted((round(h.point.x, 1), round(h.point.y, 1))
                      for h in doc.holes[2:])
    # mirror registration: front x=40 -> back x=120, front x=60 -> back x=100
    assert mirrored == [(100.0, 50.0), (120.0, 50.0)]


def test_group_drag_self_heals_frozen_members(qapp):
    # A group drag temporarily freezes the non-leader members; if that drag's
    # release is ever missed, grabbing any member again must un-freeze the group
    # so it can always be moved (regression: "sometimes can't move a group").
    from PySide6.QtWidgets import QGraphicsItem
    from leathercad_app import canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    doc = Document()
    for x in (10, 20, 30):
        doc.holes.append(LooseHole(point=Vec2(x, 10)))
    c = cm.Canvas(doc)
    c.snap_to_nodes = True
    c.snap_to_grid = False
    c.rebuild()
    holes = {round(it.hole.point.x): it for it in c.scene_obj.items()
             if getattr(it, "hole", None) is not None}
    for h in holes.values():
        h.setSelected(True)
    c.make_group()

    def movable(it):
        return bool(it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

    # begin a group drag then never end it -> members 10 and 20 are frozen
    c.begin_move_snap(holes[30])
    assert not movable(holes[10]) and not movable(holes[20])
    # pressing any member heals the whole group (select_group_of on press)
    c.select_group_of(holes[10])
    assert movable(holes[10]) and movable(holes[20]) and movable(holes[30])


def test_layer_hide_show_toggles_item_visibility(qapp):
    # Hiding a layer must actually hide its items on the canvas (and showing it
    # brings them back).
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform

    doc = Document()
    doc.add_shape(Rectangle(width=20, height=20,
                            transform=Transform(x=10, y=10), layer="Cut"))
    doc.add_shape(Rectangle(width=20, height=20,
                            transform=Transform(x=50, y=50), layer="Score"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()

    def vis(layer):
        return next(it.isVisible() for it in c.scene_obj.items()
                    if hasattr(it, "model") and it.model.layer == layer)

    assert vis("Cut") and vis("Score")
    lp = win.layers
    for i in range(lp.list.count()):
        if "Cut" in lp.list.item(i).text():
            lp.list.setCurrentRow(i)
            break
    lp._toggle_vis()
    assert not vis("Cut") and vis("Score")     # Cut hidden, Score untouched
    lp._toggle_vis()
    assert vis("Cut") and vis("Score")         # shown again


def test_hiding_cut_keeps_stitch_holes(qapp):
    # A cut shape's blue stitch holes belong to the STITCH layer: hiding Cut
    # hides its outline but the shape stays visible so the holes still show.
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitchsettings import StitchSettings

    doc = Document()
    r = Rectangle(width=40, height=30, transform=Transform(x=30, y=30),
                  layer="Cut", stitch=StitchSettings(pitch_mm=4, inset=3))
    r.stitch.enabled = True
    doc.add_shape(r)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    item = next(it for it in c.scene_obj.items() if hasattr(it, "model"))
    assert item._holes and item._holes.count > 0

    doc.layer("Cut").visible = False
    c.apply_layer_visibility()
    assert item.isVisible()                       # holes keep the shape visible
    assert c.stitch_layer_visible()               # stitch holes still drawn
    # hiding the stitch layer too finally hides the whole shape
    doc.layer("Stitch").visible = False
    c.apply_layer_visibility()
    assert not item.isVisible()


def test_move_group_survives_save_load(qapp):
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2
    from leathercad.shapes import Rectangle, Transform

    doc = Document()
    r = Rectangle(width=10, height=10, transform=Transform(x=0, y=0))
    r.group_id = "g1"
    doc.add_shape(r)
    h = LooseHole(point=Vec2(5, 5))
    h.group_id = "g1"
    doc.holes.append(h)
    doc2 = Document.from_dict(doc.to_dict())
    assert doc2.shapes[0].group_id == "g1"
    assert doc2.holes[0].group_id == "g1"


def test_resize_handles_appear_and_resize(qapp):
    # Selecting a single box-shape shows 8 resize grips; dragging a corner grip
    # resizes the shape and keeps the OPPOSITE corner pinned in world space.
    from leathercad_app import canvas as cm
    from leathercad_app.items import ResizeHandle
    from leathercad.shapes import Rectangle, Transform
    from leathercad.document import Document

    doc = Document()
    rect = Rectangle(width=40, height=30, transform=Transform(x=50, y=50))
    doc.add_shape(rect)
    c = cm.Canvas(doc)
    c.snap_to_nodes = False
    c.snap_to_grid = False
    c.rebuild()
    item = next(it for it in c.scene_obj.items()
                if getattr(it, "model", None) is rect)
    item.setSelected(True)
    c.selection_changed()
    handles = [h for h in c.scene_obj.items() if isinstance(h, ResizeHandle)]
    assert len(handles) == 8
    tr = next(h for h in handles if h.grip == (1, 1))     # top-right grip
    assert (round(tr.pos().x()), round(tr.pos().y())) == (70, 65)
    from leathercad.geometry import Vec2
    tr._apply_resize(Vec2(80, 75))                        # drag the grip out
    assert round(rect.width) == 50 and round(rect.height) == 40
    # opposite (bottom-left) corner must stay at (30, 35)
    assert round(rect.transform.x - rect.width / 2) == 30
    assert round(rect.transform.y - rect.height / 2) == 35
    # deselecting removes the grips
    c.scene_obj.clearSelection()
    c.selection_changed()
    assert not [h for h in c.scene_obj.items() if isinstance(h, ResizeHandle)]


def test_resize_drag_resizes_not_moves_and_refits_holes(qapp):
    # Regression: dragging a resize grip must RESIZE the shape (opposite corner
    # pinned) rather than translate the selected shape, and the stitch holes
    # must be recomputed to fit the new perimeter.
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from leathercad_app import canvas as cm
    from leathercad_app.items import ResizeHandle
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitchsettings import StitchSettings
    from leathercad.document import Document

    doc = Document()
    rect = Rectangle(width=40, height=30, transform=Transform(x=50, y=50),
                     stitch=StitchSettings(pitch_mm=4.0, inset=3.0))
    rect.stitch.enabled = True
    doc.add_shape(rect)
    c = cm.Canvas(doc)
    c.snap_to_nodes = False
    c.snap_to_grid = False
    c.tool = cm.SELECT
    c.rebuild()
    c.resize(600, 600)
    c.show()
    item = next(it for it in c.scene_obj.items()
                if getattr(it, "model", None) is rect)
    item.setSelected(True)
    c.selection_changed()
    holes_before = item.hole_count
    vp = c.viewport()

    def send(kind, world, btns=Qt.LeftButton, btn=Qt.LeftButton):
        pt = c.mapFromScene(QPointF(world))
        QApplication.sendEvent(vp, QMouseEvent(
            kind, QPointF(pt), vp.mapToGlobal(pt), btn, btns, Qt.NoModifier))

    send(QEvent.MouseButtonPress, QPointF(70, 65))          # grab top-right grip
    qapp.processEvents()
    for w in [(80, 75), (90, 85)]:
        send(QEvent.MouseMove, QPointF(*w))
        qapp.processEvents()
    send(QEvent.MouseButtonRelease, QPointF(90, 85), Qt.NoButton)
    qapp.processEvents()

    assert round(rect.width) == 60 and round(rect.height) == 50   # resized
    assert round(rect.transform.x - rect.width / 2) == 30         # BL pinned
    assert round(rect.transform.y - rect.height / 2) == 35
    assert item.hole_count != holes_before                       # holes refit


def test_line_length_and_angle_field(qapp):
    import math
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    it = c.add_shape(PathShape(points=[Vec2(-20, 0), Vec2(20, 0)],
                               close_path=False, transform=Transform(x=0, y=0),
                               layer="Cut"))
    c.scene_obj.clearSelection()
    it.setSelected(True)
    p = win.properties
    p.show_selection([it])
    assert not p.g_line.isHidden()                  # Line group is shown
    assert abs(p.line_len.value() - 40.0) < 1e-6

    p.line_len.setValue(100.0)
    p.line_len.editingFinished.emit()
    a = it.model.transform.apply(it.model.points[0])
    b = it.model.transform.apply(it.model.points[1])
    assert abs(math.hypot(b.x - a.x, b.y - a.y) - 100.0) < 1e-6
    assert (round(a.x), round(a.y)) == (-20, 0)     # first point stays put


def test_export_from_document(qapp, tmp_path):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad import export

    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, corner_radius=5,
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    win = MainWindow(doc)
    svg = tmp_path / "a.svg"
    export.export_svg(win.doc, str(svg))
    assert svg.read_text().count("<circle") > 5


def test_fit_none_differs_from_fitted_closed(qapp):
    """The Fit dropdown must visibly change a closed shape: 'none' plain-marches
    (last hole wherever) instead of anchoring holes on every corner."""
    from leathercad.geometry import Vec2
    from leathercad.stitching import stitch_polyline

    pts = [Vec2(0, 0), Vec2(50, 0), Vec2(50, 30), Vec2(0, 30), Vec2(0, 0)]
    corners = [Vec2(0, 0), Vec2(50, 0), Vec2(50, 30), Vec2(0, 30)]
    fitted = stitch_polyline(list(pts), list(corners), True,
                             StitchSettings(pitch_mm=3.85, fit="auto", inset=0.0))
    none = stitch_polyline(list(pts), list(corners), True,
                           StitchSettings(pitch_mm=3.85, fit="none", inset=0.0))
    # 'none' marches at the raw pitch; fitting nudges it -> different results.
    assert none.count != fitted.count or none.points != fitted.points
    gaps = none.chord_spacings()
    assert all(abs(g - 3.85) < 1e-6 for g in gaps[:-1])   # exact raw pitch


def test_stitchline_exposes_pitch_and_fit(qapp):
    """Selecting a drawn seam must show the pitch / fit controls so the user can
    change stitch spacing -- and hide the (meaningless) inset field."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import StitchLineItem
    from leathercad.document import Document
    from leathercad.stitchline import StitchLine
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_stitch_line(StitchLine(points=[Vec2(0, 0), Vec2(40, 0)],
                                   settings=StitchSettings(pitch_mm=4.0)))
    win = MainWindow(doc)
    win.canvas.rebuild()
    items = [it for it in win.canvas.scene_obj.items()
             if isinstance(it, StitchLineItem)]
    assert items
    sl = items[0]
    n0 = sl.hole_count
    p = win.properties
    p.show_selection([sl])
    fs = p._stitch_form
    assert fs.isRowVisible(p.pitch_combo)          # pitch dropdown visible
    assert fs.isRowVisible(p.fit)
    assert not fs.isRowVisible(p.inset)            # a seam is never inset

    # changing the pitch through the panel must recompute the seam's holes
    for k in range(p.pitch_combo.count()):
        if p.pitch_combo.itemData(k) and abs(p.pitch_combo.itemData(k) - 2.0) < 1e-6:
            p.pitch_combo.setCurrentIndex(k)
            break
    assert sl.hole_count > n0


def test_layer_checkbox_toggles_visibility(qapp):
    """Ticking a layer's checkbox in the Layers panel shows/hides that layer."""
    from PySide6.QtCore import Qt
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    doc = Document()
    doc.add_shape(Rectangle(width=40, height=30, layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()
    panel = win.layers
    # find the Cut row
    row = None
    for i in range(panel.list.count()):
        if panel.list.item(i).data(Qt.UserRole) == "Cut":
            row = i
            break
    assert row is not None
    item = panel.list.item(row)
    assert item.checkState() == Qt.Checked
    item.setCheckState(Qt.Unchecked)               # user unticks -> hide layer
    assert doc.layer("Cut").visible is False


def test_layers_reload_keeps_selection(qapp):
    """Toggling a layer must not bounce the selection back to the first row."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    doc = Document()
    win = MainWindow(doc)
    panel = win.layers
    if panel.list.count() >= 2:
        panel.list.setCurrentRow(panel.list.count() - 1)
        keep = panel.list.currentRow()
        panel.reload()
        assert panel.list.currentRow() == keep


def test_stitchline_node_edit_keeps_line_locked(qapp):
    """Node-editing a seam must not let a stray press on the line body drag the
    whole seam away and strand its node handles (select_group_of used to force
    the item movable again on every press)."""
    from PySide6.QtWidgets import QGraphicsItem
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import StitchLineItem
    from leathercad.document import Document
    from leathercad.stitchline import StitchLine
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_stitch_line(StitchLine(points=[Vec2(0, 0), Vec2(40, 0)],
                                   settings=StitchSettings(pitch_mm=4.0, inset=0.0)))
    win = MainWindow(doc)
    win.canvas.rebuild()
    sl = [it for it in win.canvas.scene_obj.items()
          if isinstance(it, StitchLineItem)][0]
    sl.setSelected(True)
    win.canvas.enter_vertex_edit(sl)
    assert not (sl.flags() & QGraphicsItem.ItemIsMovable)   # frozen for editing
    # a press on the seam body (what StitchLineItem.mousePressEvent triggers)
    win.canvas.select_group_of(sl)
    assert not (sl.flags() & QGraphicsItem.ItemIsMovable)   # stays frozen
    win.canvas.begin_move_snap(sl)
    assert not (sl.flags() & QGraphicsItem.ItemIsMovable)   # still frozen
    # leaving node-edit restores normal movability
    win.canvas.clear_vertex_handles()
    assert sl.flags() & QGraphicsItem.ItemIsMovable


def test_stitchline_node_drag_moves_only_that_node(qapp):
    """Dragging a seam node moves that node's handle and endpoint together; the
    other node (and its handle) stay put."""
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import StitchLineItem
    from leathercad.document import Document
    from leathercad.stitchline import StitchLine
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_stitch_line(StitchLine(points=[Vec2(0, 0), Vec2(40, 0)],
                                   settings=StitchSettings(pitch_mm=4.0, inset=0.0)))
    win = MainWindow(doc)
    win.canvas.rebuild()
    sl = [it for it in win.canvas.scene_obj.items()
          if isinstance(it, StitchLineItem)][0]
    sl.setSelected(True)
    win.canvas.enter_vertex_edit(sl)
    h0, h1 = win.canvas._handles
    h1._drag_start = QPointF(h1.pos())
    h1.setPos(40, 20)                       # drag the endpoint up
    assert (sl.line.points[1].x, sl.line.points[1].y) == (40.0, 20.0)
    assert (sl.line.points[0].x, sl.line.points[0].y) == (0.0, 0.0)
    assert (h0.pos().x(), h0.pos().y()) == (0.0, 0.0)     # other handle unmoved
    assert (h1.pos().x(), h1.pos().y()) == (40.0, 20.0)


def test_press_unselected_item_drops_prior_selection(qapp):
    """With item A selected, plain-pressing item B must select ONLY B (drop A) so
    a drag moves B alone -- not A and B together as an accidental group."""
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform

    doc = Document()
    doc.add_shape(Rectangle(width=20, height=20, transform=Transform(x=-40, y=0),
                            layer="Cut"))
    doc.add_shape(Rectangle(width=20, height=20, transform=Transform(x=40, y=0),
                            layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    items = [it for it in c.scene_obj.items() if isinstance(it, ShapeItem)]
    A = [it for it in items if it.model.transform.x == -40][0]
    B = [it for it in items if it.model.transform.x == 40][0]

    A.setSelected(True)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(0, 0),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    c.press_select(B, ev)
    c.begin_move_snap(B)
    assert B.isSelected() and not A.isSelected()   # only B now
    assert c._group_drag is None                    # no accidental group drag
    c.end_move_snap()

    # Ctrl-press keeps the multi-selection intact (does not drop A)
    c.scene_obj.clearSelection()
    A.setSelected(True)
    ctrl = QMouseEvent(QEvent.MouseButtonPress, QPointF(0, 0),
                       Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier)
    c.press_select(B, ctrl)
    assert A.isSelected()                            # A preserved for multi-select


def test_pen_tool_draws_bezier_editpath(qapp):
    """The pen tool: click-drag places smooth anchors, a plain click a corner;
    Enter finishes an open curved EditablePath with bezier edges + a control
    handle per bezier end in node-edit."""
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent, QKeyEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import EditablePath

    win = MainWindow(Document())
    c = win.canvas
    c.resize(500, 500)
    c.snap_to_grid = False
    c.snap_to_nodes = False
    c.tool = cm.PEN

    def vp(x, y):
        return QPointF(c.mapFromScene(QPointF(x, y)))

    def press(x, y):
        c.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, vp(x, y),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    def move(x, y):
        c.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, vp(x, y),
                                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

    def release(x, y):
        c.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, vp(x, y),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

    press(0, 0); move(10, 10); release(10, 10)      # smooth anchor
    press(40, 0); move(50, -10); release(50, -10)   # smooth anchor
    press(20, -30); release(20, -30)                # corner anchor
    c.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))

    eps = [s for s in win.doc.shapes if isinstance(s, EditablePath)]
    assert len(eps) == 1
    ep = eps[0]
    assert [e.kind for e in ep.edges] == ["bezier", "bezier"]
    assert len(ep.local_path().flatten()) > 12      # a real curve, not 3 points

    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]
    it.setSelected(True)
    c.enter_vertex_edit(it)
    ctrl = [h for h in c._handles if getattr(h.node, "is_ctrl", False)]
    assert len(ctrl) == 4                            # 2 beziers x 2 control pts


def test_pen_close_path_and_ctrl_reshape(qapp):
    """Clicking the start anchor closes the curve; dragging a control handle
    reshapes it and dragging an anchor carries its handles along."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import EditablePath, Transform
    from leathercad.geometry import Vec2

    anchors = [Vec2(-20, 0), Vec2(0, 20), Vec2(20, 0), Vec2(0, -20)]
    outs = [Vec2(0, 10), Vec2(10, 0), Vec2(0, -10), Vec2(-10, 0)]
    ep = EditablePath.from_bezier(anchors, outs, closed=True)
    ep.layer = "Cut"
    ep.transform = Transform(x=0, y=0)
    doc = Document()
    doc.add_shape(ep)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]

    flat0 = ep.local_path().flatten()
    it.setSelected(True)
    c.enter_vertex_edit(it)
    ctrl = [h for h in c._handles if getattr(h.node, "is_ctrl", False)]
    h = ctrl[0]
    w = h.node.world
    h.node.setter(Vec2(w.x, w.y + 15))
    it.sync_from_model()
    assert ep.local_path().flatten() != flat0        # curve reshaped

    c.clear_vertex_handles()
    c.enter_vertex_edit(it)
    anchs = [hh for hh in c._handles
             if not getattr(hh.node, "is_ctrl", False) and not hh.node.is_mid]
    a0 = anchs[0]
    c1_before = ep.edges[0].c1
    aw = a0.node.world
    a0.node.setter(Vec2(aw.x + 5, aw.y + 5))
    assert round(ep.edges[0].c1.x - c1_before.x, 3) == 5.0   # handle followed
    assert round(ep.edges[0].c1.y - c1_before.y, 3) == 5.0


def test_single_hole_readout_no_crash(qapp):
    """A shape with exactly one hole has no chord spacing; the Properties readout
    must not crash on min([]) (regression)."""
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import PathShape, Transform
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(0, 0), Vec2(2, 0)], close_path=False,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=50.0, inset=0.0,
                                                  fit="none", enabled=True),
                            layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()
    it = [i for i in win.canvas.scene_obj.items() if isinstance(i, ShapeItem)][0]
    assert it.hole_count == 1
    it.setSelected(True)
    win.properties.show_selection(win.canvas.selected_items())   # must not raise
    assert "1 holes" in win.properties.readout.text()


def test_pen_right_click_finishes_curve(qapp):
    """Right-click finishes an open pen curve (more reliable than double-click)
    and does not leave the pen mid-draw or pop a context menu."""
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent, QContextMenuEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import EditablePath

    win = MainWindow(Document())
    c = win.canvas
    c.resize(500, 500)
    c.snap_to_grid = False
    c.snap_to_nodes = False
    c.tool = cm.PEN

    def vp(x, y):
        return QPointF(c.mapFromScene(QPointF(x, y)))

    def ev(kind, x, y, btn=Qt.LeftButton, btns=Qt.LeftButton):
        return QMouseEvent(kind, vp(x, y), btn, btns, Qt.NoModifier)

    c.mousePressEvent(ev(QEvent.MouseButtonPress, 0, 0))
    c.mouseMoveEvent(ev(QEvent.MouseMove, 10, 10, Qt.NoButton))
    c.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, 10, 10))
    c.mousePressEvent(ev(QEvent.MouseButtonPress, 40, 0))
    c.mouseMoveEvent(ev(QEvent.MouseMove, 50, -10, Qt.NoButton))
    c.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, 50, -10))
    assert len(c._pen_pts) == 2

    # right-click press then the platform context-menu event
    c.mousePressEvent(ev(QEvent.MouseButtonPress, 40, 0,
                         Qt.RightButton, Qt.RightButton))
    c.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, vp(40, 0).toPoint()))

    eps = [s for s in win.doc.shapes if isinstance(s, EditablePath)]
    assert len(eps) == 1                       # curve committed
    assert c._pen_pts == []                    # pen no longer mid-draw
    assert [e.kind for e in eps[0].edges] == ["bezier"]


def test_double_click_edits_bezier_nodes(qapp):
    """Double-clicking a bezier curve (an EditablePath) must enter node-edit --
    it used to only recognise Polygon/PathShape, so pen curves ignored it."""
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import EditablePath, Transform
    from leathercad.geometry import Vec2

    ep = EditablePath.from_bezier([Vec2(-20, 0), Vec2(20, 0)],
                                  [Vec2(5, 15), Vec2(5, -15)], closed=False)
    ep.layer = "Cut"
    ep.transform = Transform(x=0, y=0)
    doc = Document()
    doc.add_shape(ep)
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(500, 500)
    c.tool = cm.SELECT
    it = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)][0]

    apex = max(ep.local_path().flatten(), key=lambda p: p.y)   # a point on the curve
    vp = c.mapFromScene(QPointF(apex.x, apex.y))
    c.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF(vp),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert c._edit_owner is it                                 # entered node-edit
    assert any(getattr(h.node, "is_ctrl", False) for h in c._handles)


def _multi_click(c, pts):
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    for x, y in pts:
        vp = QPointF(c.mapFromScene(QPointF(x, y)))
        c.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, vp,
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_two_point_circle(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import Circle

    win = MainWindow(Document())
    c = win.canvas
    c.rebuild()
    c.resize(500, 500)
    c.snap_to_grid = False
    c.snap_to_nodes = False
    c.tool = cm.CIRCLE2
    _multi_click(c, [(0, 0), (40, 0)])                 # diameter ends
    circ = [s for s in win.doc.shapes if isinstance(s, Circle)]
    assert len(circ) == 1
    assert round(circ[0].transform.x, 1) == 20.0 and round(circ[0].rx, 1) == 20.0


def test_three_point_circle(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import Circle

    win = MainWindow(Document())
    c = win.canvas
    c.rebuild()
    c.resize(500, 500)
    c.snap_to_grid = False
    c.snap_to_nodes = False
    c.tool = cm.CIRCLE3
    _multi_click(c, [(0, 20), (20, 0), (40, 20)])      # 3 points on the rim
    circ = [s for s in win.doc.shapes if isinstance(s, Circle)]
    assert len(circ) == 1
    assert round(circ[0].transform.x, 1) == 20.0
    assert round(circ[0].transform.y, 1) == 20.0
    assert round(circ[0].rx, 1) == 20.0


def test_three_point_arc(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import EditablePath

    win = MainWindow(Document())
    c = win.canvas
    c.rebuild()
    c.resize(500, 500)
    c.snap_to_grid = False
    c.snap_to_nodes = False
    c.tool = cm.ARC3
    _multi_click(c, [(0, 0), (40, 0), (20, 15)])       # start, end, through
    eps = [s for s in win.doc.shapes if isinstance(s, EditablePath)]
    assert len(eps) == 1
    assert [e.kind for e in eps[0].edges] == ["arc"]
    wpts = [eps[0].transform.apply(p) for p in eps[0].local_path().flatten()]
    assert round(max(p.y for p in wpts), 1) == 15.0    # bulges through the point
    assert len(wpts) > 8                               # a real arc, not a chord


def test_centre_arc(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import EditablePath

    win = MainWindow(Document())
    c = win.canvas
    c.rebuild()
    c.resize(500, 500)
    c.snap_to_grid = False
    c.snap_to_nodes = False
    c.tool = cm.ARCCENTER
    _multi_click(c, [(0, 0), (20, 0), (0, 20)])        # centre, start, end (quarter)
    eps = [s for s in win.doc.shapes if isinstance(s, EditablePath)]
    assert len(eps) == 1
    assert [e.kind for e in eps[0].edges] == ["arc"]
    wpts = [eps[0].transform.apply(p) for p in eps[0].local_path().flatten()]
    # every point is ~20 mm from the centre (0,0)
    assert all(abs((p.x ** 2 + p.y ** 2) ** 0.5 - 20.0) < 0.2 for p in wpts)


def test_pdf_export_1to1_tiled(qapp, tmp_path):
    """1:1 PDF export: a small pattern is one page, a large one tiles across
    several, and the file is a valid PDF."""
    from leathercad_app.printing import export_pdf_tiled
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform

    small = Document()
    small.add_shape(Rectangle(width=80, height=60, transform=Transform(x=0, y=0),
                              stitch=StitchSettings(pitch_mm=4, inset=3),
                              layer="Cut"))
    p1 = tmp_path / "small.pdf"
    assert export_pdf_tiled(small, str(p1)) == (1, 1)
    assert p1.read_bytes()[:5] == b"%PDF-"

    big = Document()
    big.add_shape(Rectangle(width=400, height=300, transform=Transform(x=0, y=0),
                            layer="Cut"))
    p2 = tmp_path / "big.pdf"
    rows, cols = export_pdf_tiled(big, str(p2))
    assert rows >= 2 and cols >= 2                # genuinely tiled
    assert p2.read_bytes()[:5] == b"%PDF-"


def test_tool_palette_flyout_groups(qapp):
    """Related shape tools are grouped into fan-out buttons; picking a variant
    activates it and becomes the button's face, and selection stays exclusive."""
    from leathercad_app.mainwindow import MainWindow, TOOL_LAYOUT
    from leathercad_app import canvas as cm
    from leathercad.document import Document

    win = MainWindow(Document())
    # rectangles, circles/ellipse and arcs each collapse into one flyout
    groups = [e for e in TOOL_LAYOUT if not isinstance(e, str)]
    assert len(groups) == 3
    all_modes = [m for e in TOOL_LAYOUT for m in ([e] if isinstance(e, str) else e[2])]
    assert all(m in win._action_for_mode for m in all_modes)
    # the four round-shape variants all live behind one shared flyout button
    circle_btns = {id(win._group_button_for_action[win._action_for_mode[m]])
                   for m in (cm.CIRCLE, cm.ELLIPSE, cm.CIRCLE2, cm.CIRCLE3)}
    assert len(circle_btns) == 1
    a = win._action_for_mode[cm.CIRCLE3]
    a.trigger()
    assert win.canvas.tool == cm.CIRCLE3
    btn = win._group_button_for_action[a]
    assert btn.defaultAction() is a and a.isChecked()
    win._action_for_mode[cm.RECT].trigger()
    assert not a.isChecked() and win.canvas.tool == cm.RECT


def test_offset_preview_survives_rebuild_midflight(qapp):
    """Regression: an offset preview left mid-flight when the scene is cleared
    (undo / open / new) must not crash on the next mouse move -- the freed C++
    wrapper is dropped and rebuilt, not dereferenced."""
    import shiboken6
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from leathercad.document import Document
    from leathercad.shapes import Rectangle
    from leathercad.geometry import Vec2

    doc = Document()
    doc.add_shape(Rectangle(width=80, height=50, layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(500, 400)
    c.tool = cm.OFFSET
    # arm an offset on the shape, creating the live dashed preview
    c._arm_offset(Vec2(40, 0))
    assert c._offset_item is not None
    assert c._offset_preview is not None and shiboken6.isValid(c._offset_preview)

    # a rebuild (undo/open/new) clears the scene out from under the offset
    c.rebuild()
    assert c._offset_preview is None and c._offset_item is None

    # even if the offset target lingered, a mouse-move must recover, not crash
    shp = next(it for it in c.scene_obj.items()
               if it.__class__.__name__ == "ShapeItem")
    wpts, _cor, closed = shp.model.world_polyline()
    c._offset_item = shp
    c._offset_pts = [Vec2(p.x, p.y) for p in wpts]
    c._offset_closed = closed
    c._update_offset_preview(Vec2(40, 0))          # must not raise
    assert shiboken6.isValid(c._offset_preview)


def test_draw_color_override_is_display_only(qapp):
    """A bright on-screen draw colour overrides the outline pen for visibility
    while tracing, but leaves the layer colour (and export) untouched."""
    from PySide6.QtGui import QColor
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import Rectangle

    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    shp = next(it for it in c.scene_obj.items() if isinstance(it, ShapeItem))
    layer_col = QColor(c.layer_color("Cut")).name()

    win._set_draw_color(QColor(0, 230, 255))
    assert c.display_color.name() == "#00e6ff"
    # the model's layer + the layer colour are unchanged (export stays correct)
    assert shp.model.layer == "Cut"
    assert QColor(c.layer_color("Cut")).name() == layer_col

    win._set_draw_color(None)               # back to layer colours
    assert c.display_color is None


def test_pointing_tools_get_crosshair_cursor(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app import canvas as cm
    from PySide6.QtCore import Qt
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    win._set_tool(cm.LINE)
    # a custom bitmap crosshair, not the plain arrow
    assert c.viewport().cursor().shape() == Qt.BitmapCursor
    win._set_tool(cm.SELECT)
    assert c.viewport().cursor().shape() == Qt.ArrowCursor

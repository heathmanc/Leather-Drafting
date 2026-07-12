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
    assert form.isRowVisible(p.slit_len) and not form.isRowVisible(p.hole_dia)
    p.hole_style.setCurrentText("round")
    assert hole.hole.hole_style == "round"
    assert form.isRowVisible(p.hole_dia) and not form.isRowVisible(p.slit_len)
    p.hole_style.setCurrentText("slit")
    assert hole.hole.hole_style == "slit"
    assert form.isRowVisible(p.slit_len) and not form.isRowVisible(p.hole_dia)


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


def test_node_snap_while_editing(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_enabled = True
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


def test_magnetic_node_snap_on_move(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document

    win = MainWindow(Document())
    c = win.canvas
    c.snap_enabled = True
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
    c.snap_enabled = True
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

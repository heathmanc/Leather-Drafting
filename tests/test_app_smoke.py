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


def test_line_node_has_ortho_reference(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.document import Document

    win = MainWindow(Document())
    it = win.canvas.add_shape(PathShape(points=[Vec2(0, 0), Vec2(30, 8)],
                                        close_path=False,
                                        transform=Transform(x=0, y=0), layer="Cut"))
    nodes = it.editable_nodes()
    # each line endpoint knows its neighbour, so Shift can force 0/90 degrees
    assert nodes[0].ref is not None and nodes[1].ref is not None
    assert (round(nodes[0].ref.x), round(nodes[0].ref.y)) == (30, 8)

    # with Shift captured from the drag, the segment locks to 0 / 90 degrees
    from PySide6.QtWidgets import QGraphicsItem
    from PySide6.QtCore import QPointF
    from leathercad_app.items import VertexHandle
    h = VertexHandle(nodes[1], it, win.canvas)      # endpoint (30,8), ref (0,0)
    win.canvas.scene_obj.addItem(h)
    h._shift = True
    r = h.itemChange(QGraphicsItem.ItemPositionChange, QPointF(35, 20))
    assert (round(r.x()), round(r.y())) == (35, 0)  # mostly horizontal -> y locks
    r = h.itemChange(QGraphicsItem.ItemPositionChange, QPointF(5, 40))
    assert (round(r.x()), round(r.y())) == (0, 40)  # mostly vertical -> x locks


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

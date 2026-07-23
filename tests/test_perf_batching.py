"""Perf plumbing: memoised stitch fits + pre-batched hole painting.

Big documents (tens of thousands of holes) stay responsive because
(1) ``stitch_polyline`` memoises fits -- 50 copies of a shape cost one fit,
and every rebuild/undo re-uses it -- and (2) each item bakes its holes into
one QPainterPath / QPolygonF so paint() is a single C++ draw call, not a
Python loop. These tests pin both mechanisms and their safety rules.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.stitching import stitch_polyline  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


SQUARE = [Vec2(0, 0), Vec2(60, 0), Vec2(60, 40), Vec2(0, 40), Vec2(0, 0)]
CORNERS = [Vec2(0, 0), Vec2(60, 0), Vec2(60, 40), Vec2(0, 40)]


# -- stitch result cache -----------------------------------------------------
def test_cache_same_inputs_same_holes():
    st = StitchSettings(pitch_mm=3.0, inset=3.0)
    a = stitch_polyline(SQUARE, CORNERS, True, st)
    b = stitch_polyline(SQUARE, CORNERS, True, st)
    assert a.count == b.count > 0
    assert all(abs(ha.point.x - hb.point.x) < 1e-12
               and abs(ha.point.y - hb.point.y) < 1e-12
               for ha, hb in zip(a.holes, b.holes))


def test_cache_returns_independent_results():
    """Callers re-orient/replace the holes list -- a cached master handed out
    by reference would be corrupted by the first consumer."""
    st = StitchSettings(pitch_mm=3.0, inset=3.0)
    a = stitch_polyline(SQUARE, CORNERS, True, st)
    b = stitch_polyline(SQUARE, CORNERS, True, st)
    assert a is not b
    assert a.holes is not b.holes
    assert a.pitches is not b.pitches
    n = b.count
    a.holes.clear()                                   # simulate a consumer
    c = stitch_polyline(SQUARE, CORNERS, True, st)
    assert c.count == n                               # cache unharmed


def test_cache_key_covers_position_settings():
    """Any setting that moves a hole must produce a different result."""
    base = StitchSettings(pitch_mm=3.0, inset=3.0)
    n0 = stitch_polyline(SQUARE, CORNERS, True, base).count
    finer = StitchSettings(pitch_mm=2.0, inset=3.0)
    assert stitch_polyline(SQUARE, CORNERS, True, finer).count > n0
    two_rows = StitchSettings(pitch_mm=3.0, inset=3.0, rows=2)
    assert stitch_polyline(SQUARE, CORNERS, True, two_rows).count == 2 * n0


def test_cache_ignores_appearance_settings():
    """hole_style / diameter don't move holes -- same fit either way."""
    round_st = StitchSettings(pitch_mm=3.0, inset=3.0, hole_style="round")
    slit_st = StitchSettings(pitch_mm=3.0, inset=3.0, hole_style="slit")
    a = stitch_polyline(SQUARE, CORNERS, True, round_st)
    b = stitch_polyline(SQUARE, CORNERS, True, slit_st)
    assert a.count == b.count
    assert all(abs(ha.point.x - hb.point.x) < 1e-12
               for ha, hb in zip(a.holes, b.holes))


def test_cache_bounded():
    from leathercad import stitching
    st = StitchSettings(pitch_mm=3.0, inset=0.0)
    for k in range(600):                              # > _CACHE_MAX distinct keys
        pts = [Vec2(0, 0), Vec2(30 + k * 0.01, 0)]
        stitch_polyline(pts, [], False, st)
    assert len(stitching._stitch_cache) <= stitching._CACHE_MAX


# -- pre-batched hole painting ------------------------------------------------
def _shape_item(qapp, st):
    from leathercad_app.items import ShapeItem
    doc = Document()
    r = Rectangle(width=60, height=40, transform=Transform(x=10, y=5),
                  layer="Cut", stitch=st)
    doc.add_shape(r)
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    win.canvas.rebuild()
    return win, [i for i in win.canvas.scene_obj.items()
                 if isinstance(i, ShapeItem)][0]


def test_shapeitem_batches_round_holes(qapp):
    win, it = _shape_item(qapp, StitchSettings(pitch_mm=3.0, inset=3.0))
    assert it.hole_count > 0
    assert it._holes_path is not None
    # addEllipse = 1 moveTo + 4 cubics (13 elements) per hole
    assert it._holes_path.elementCount() == it.hole_count * 13
    assert it._holes_pts is not None
    assert it._holes_pts.size() == it.hole_count
    assert it._holes_size_mm == it.model.stitch.hole_diameter


def test_shapeitem_batches_slits(qapp):
    st = StitchSettings(pitch_mm=3.0, inset=3.0, hole_style="slit",
                        slit_length=1.6)
    win, it = _shape_item(qapp, st)
    # moveTo + lineTo = 2 elements per slit
    assert it._holes_path.elementCount() == it.hole_count * 2
    assert it._holes_size_mm == st.slit_length


def test_shapeitem_path_follows_settings_change(qapp):
    win, it = _shape_item(qapp, StitchSettings(pitch_mm=3.0, inset=3.0))
    before = it._holes_path.elementCount()
    it.model.stitch.pitch_mm = 2.0
    it.sync_from_model()
    assert it._holes_path.elementCount() > before
    it.model.stitch.enabled = False
    it.sync_from_model()
    assert it._holes_path is None and it._holes_pts is None


def _grid_of_rects(n_side, sel_count):
    from leathercad_app.items import ShapeItem
    doc = Document()
    for i in range(n_side):
        for j in range(n_side):
            doc.add_shape(Rectangle(width=80, height=60,
                                    transform=Transform(x=i * 100.0,
                                                        y=j * 90.0),
                                    layer="Cut"))
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    win.canvas.rebuild()
    items = [it for it in win.canvas.scene_obj.items()
             if isinstance(it, ShapeItem)]
    sel = items[:sel_count]
    for it in sel:
        it.setSelected(True)
    return win, sel


# -- big multi-select / duplicate stay O(N), not O(N^2) -----------------------
def test_multiselect_coalesces_panel_and_keeps_o1_grip_test(qapp):
    """Selecting a big batch must not rebuild the Properties panel per member
    (the O(N^2) lock). The heavy emit coalesces to one deferred fire, and the
    resize-grip decision reads an O(1) tally, not a filtered scene scan."""
    win, sel = _grid_of_rects(6, 0)          # 36 shapes, none selected yet
    c = win.canvas
    from leathercad_app.items import ShapeItem
    shapes = [it for it in c.scene_obj.items() if isinstance(it, ShapeItem)]

    rebuilds = []
    c.selectionChangedSig.connect(lambda: rebuilds.append(1))
    c.scene_obj.clearSelection()
    qapp.processEvents()
    rebuilds.clear()

    for it in shapes:                        # select all 36
        it.setSelected(True)
    # the tally tracks every selected shape without a scene scan
    assert len(c._selected_shapes) == 36
    # many selected -> no box grips, and only a single coalesced emit is pending
    assert not c._resize_handles
    assert c._selchg_emit_pending
    qapp.processEvents()
    assert len(rebuilds) == 1                 # ONE panel rebuild for the burst

    # dropping back to a single shape brings the grips back (still O(1))
    c.scene_obj.clearSelection()
    shapes[0].setSelected(True)
    qapp.processEvents()
    assert len(c._selected_shapes) == 1
    assert c._resize_handles                  # 8 grips + rotate handle


def test_duplicate_batch_refreshes_once(qapp):
    """Duplicating a big selection positions each new item, but the per-item
    move-refresh (dimension re-anchor + total_holes scene scan) is suspended so
    the whole batch costs one refresh, not one per item."""
    win, sel = _grid_of_rects(5, 25)          # select all 25
    c = win.canvas
    from leathercad_app.items import ShapeItem
    before = len([it for it in c.scene_obj.items()
                  if isinstance(it, ShapeItem)])

    hits = []
    c.documentChangedSig.connect(lambda: hits.append(1))
    c.duplicate_selected()
    # one document-changed for the whole duplicate, not 25
    assert len(hits) <= 1
    assert not c._suspend_move_refresh        # flag always cleared
    after = len([it for it in c.scene_obj.items()
                 if isinstance(it, ShapeItem)])
    assert after == before + 25               # every shape duplicated


def test_delete_batch_is_single_pass(qapp):
    """Deleting a subset of many loose holes must rebuild the document list
    once, not call list.remove() per hole (which was O(M*N) -- a 20 s stall on
    a 20k-hole document)."""
    from leathercad.holes import LooseHole
    from leathercad_app.items import HoleItem
    doc = Document()
    for i in range(400):
        doc.holes.append(LooseHole(point=Vec2((i % 20) * 3.0, (i // 20) * 3.0)))
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    holes = [it for it in c.scene_obj.items() if isinstance(it, HoleItem)]
    victims = holes[100:250]                  # 150 in the middle
    survivors = {id(h.hole) for h in holes if h not in victims}
    for it in victims:
        it.setSelected(True)
    c.delete_selected()
    assert len(doc.holes) == 250              # 400 - 150
    assert {id(h) for h in doc.holes} == survivors   # exactly the right ones
    # scene items gone too (no dangling HoleItems)
    assert len([it for it in c.scene_obj.items()
                if isinstance(it, HoleItem)]) == 250


# -- multi-select drag stays realtime -----------------------------------------
def test_drag_snap_uses_spatial_grid(qapp):
    """The move-snap cache is bucketed at drag start so each mouse move scans
    a 3x3 neighbourhood, not the whole cache; torn down with the drag."""
    win, sel = _grid_of_rects(4, 1)
    c = win.canvas
    c.begin_move_snap(sel[0])
    assert c._snap_grid is not None
    assert sum(len(v) for v in c._snap_grid.values()) == len(c._snap_cache)
    c.end_move_snap()
    assert c._snap_grid is None


def test_drag_snap_grid_still_snaps_exactly(qapp):
    from PySide6.QtCore import QPointF
    win, sel = _grid_of_rects(2, 1)          # rects at (0,0) and (100,0)...
    c = win.canvas
    it = sel[0]
    other = [s for s in c.scene_obj.items()
             if getattr(s, "model", None) is not None and s is not it]
    target = other[0].model.transform        # drag so centres coincide
    c.begin_move_snap(it)
    res = c.snap_move(it, QPointF(target.x - 0.9 / c._zoom,
                                  target.y + 0.9 / c._zoom))
    c.end_move_snap()
    assert abs(res.x() - target.x) < 1e-6    # locked onto the other centre
    assert abs(res.y() - target.y) < 1e-6


def test_group_drag_offsets_capped(qapp):
    win, sel = _grid_of_rects(12, 100)       # 100 rects x 9 nodes = 900 raw
    c = win.canvas
    c.begin_move_snap(sel[0])
    assert c._group_drag is not None
    assert len(c._group_drag["offsets"]) <= 600
    c.end_move_snap()


def test_group_drag_single_refresh_per_move(qapp):
    """Driven group members must not each fire the full document-changed
    cascade on every mouse move -- the leader reports once."""
    from PySide6.QtCore import QPointF
    win, sel = _grid_of_rects(4, 8)
    c = win.canvas
    hits = []
    c.documentChangedSig.connect(lambda: hits.append(1))
    c.begin_move_snap(sel[0])
    assert c._group_drag is not None
    hits.clear()
    p = sel[0].pos()
    c.snap_move(sel[0], QPointF(p.x() + 5.0, p.y() + 3.0))  # drives 7 members
    assert len(hits) <= 1                    # not one per member
    c.end_move_snap()


def test_geometry_fields_sync_synchronously_on_move(qapp):
    """documentChangedSig is rate-limited during drags, but the Properties
    position fields must re-sync on EVERY move (a later _apply would
    otherwise write a stale position back)."""
    win, sel = _grid_of_rects(2, 1)
    c, it = win.canvas, sel[0]
    win._selection_changed()
    it.model.transform.x = 123.0
    c.item_moved(it)                          # no event loop spin needed
    assert win.properties.pos_x.value() == 123.0
    it.model.transform.x = 77.0
    c.item_moved(it)                          # immediately again (rate window)
    assert win.properties.pos_x.value() == 77.0


def test_stitchlineitem_batches_holes(qapp):
    from leathercad.stitchline import StitchLine
    from leathercad_app.items import StitchLineItem
    doc = Document()
    line = StitchLine(points=[Vec2(0, 0), Vec2(100, 0)],
                      settings=StitchSettings(pitch_mm=4.0, inset=0.0))
    doc.stitch_lines.append(line)
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    win.canvas.rebuild()
    it = [i for i in win.canvas.scene_obj.items()
          if isinstance(i, StitchLineItem)][0]
    assert it.hole_count > 0
    assert it._holes_path is not None
    assert it._holes_path.elementCount() == it.hole_count * 13
    assert it._holes_pts.size() == it.hole_count


def test_resize_skips_stitch_fit_until_release(qapp):
    """Dragging a resize grip on a stitched shape must NOT re-fit the holes each
    move (that made big stitched shapes lag); they recompute once at the end."""
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitchsettings import StitchSettings
    from leathercad_app.items import ShapeItem, ResizeHandle
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(Rectangle(width=120, height=80, corner_radius=6,
                            transform=Transform(x=0, y=0), layer="Cut",
                            stitch=StitchSettings(enabled=True, pitch_mm=3.85,
                                                  inset=4.0)))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    shp = next(it for it in c.scene_obj.items() if isinstance(it, ShapeItem))
    assert shp._holes and shp._holes.count > 0
    c.scene_obj.clearSelection(); shp.setSelected(True); c.selection_changed()
    grip = next(g for g in c.scene_obj.items()
                if isinstance(g, ResizeHandle) and g.grip == (1, 1))

    hx0, hy0 = shp.resize_extents()
    grip._apply_resize(Vec2(200, 140))               # a move: holes skipped
    assert shp.resize_extents()[0] > hx0             # the shape did grow
    assert shp._holes is None                        # ...but no re-fit yet

    shp.sync_from_model()                            # release -> full recompute
    assert shp._holes and shp._holes.count > 0


# -- cached world outline: snap machinery never re-flattens arcs -------------
def test_world_outline_matches_and_is_cached(qapp):
    """ShapeItem.world_outline() equals model.world_polyline()[0] but reuses
    the already-flattened outline, and its cache invalidates on move/edit."""
    from leathercad_app.canvas import Canvas
    from leathercad_app.items import ShapeItem
    doc = Document()
    doc.add_shape(Rectangle(width=90, height=60, corner_radius=8,
                            transform=Transform(x=12, y=7), layer="Cut",
                            stitch=StitchSettings(enabled=True, pitch_mm=3.85,
                                                  inset=3.5)))
    c = Canvas(doc); c.rebuild()
    shp = next(it for it in c.scene_obj.items() if isinstance(it, ShapeItem))

    truth = shp.model.world_polyline()[0]
    got = shp.world_outline()
    assert len(got) == len(truth)
    assert all(abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6
               for a, b in zip(got, truth))

    assert shp.world_outline() is got            # cache hit -> same object
    shp.setPos(shp.pos().x() + 25, shp.pos().y())  # a move invalidates it
    moved = shp.world_outline()
    assert moved is not got
    assert abs(moved[0].x - got[0].x - 25) < 1e-6  # tracked the move


def test_move_snap_cache_does_not_reflatten(qapp):
    """Building the drag snap cache over many shapes must not call the
    expensive model.world_polyline() per shape (it uses the cached outline)."""
    from leathercad_app.canvas import Canvas
    from leathercad_app.items import ShapeItem
    doc = Document()
    for i in range(12):
        doc.add_shape(Rectangle(width=90, height=60, corner_radius=8,
                                transform=Transform(x=(i % 4) * 120, y=(i // 4) * 90),
                                layer="Cut",
                                stitch=StitchSettings(enabled=True, pitch_mm=3.85,
                                                      inset=3.5)))
    c = Canvas(doc); c.rebuild()
    shapes = [it for it in c.scene_obj.items() if isinstance(it, ShapeItem)]
    # prime the outline caches (as a first paint/read would)
    for s in shapes:
        s.world_outline()

    calls = {"n": 0}
    from leathercad.shapes import Shape
    orig = Shape.world_polyline
    def counting(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)
    Shape.world_polyline = counting
    try:
        one = shapes[0]
        one.setSelected(True)
        c.begin_move_snap(one)
        c.end_move_snap()
    finally:
        Shape.world_polyline = orig
    assert calls["n"] == 0        # zero re-flattens: the cache carried the drag

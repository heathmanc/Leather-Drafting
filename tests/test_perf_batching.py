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

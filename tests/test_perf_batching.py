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

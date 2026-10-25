"""Per-edge stitching: leave an edge unsewn (a pocket's open mouth)."""

import math
import os

import pytest

from leathercad.document import Document
from leathercad.geometry import Vec2
from leathercad.shapes import Polygon, Rectangle, segment_index_at
from leathercad.stitching import edge_runs, holes_for_shape, stitch_shape_path
from leathercad.stitchsettings import StitchSettings

ST = StitchSettings(pitch_mm=4.0, inset=3.0)


def _gaps(pts):
    return [(pts[i + 1] - pts[i]).length() for i in range(len(pts) - 1)]


def test_edge_runs():
    assert edge_runs(4, None, True) is None
    assert edge_runs(4, [True] * 4, True) is None
    assert edge_runs(4, [True] * 3, True) is None          # wrong length
    assert edge_runs(4, [False] * 4, True) == []
    assert edge_runs(4, [True, True, False, True], True) == [(3, 1)]  # wraps
    assert edge_runs(4, [True, True, False, True], False) == [(0, 1), (3, 3)]
    assert edge_runs(5, [True, False, True, False, True], True) == [(2, 2), (4, 0)]


def test_skipping_the_top_edge_ends_at_the_inset_corners():
    r = Rectangle(width=60, height=40, corner_radius=6)
    path = r.local_path()
    assert len(path.segments) == 8
    top = [i for i, s in enumerate(path.segments)
           if abs(s.start().y - 20) < 1e-9 and abs(s.end().y - 20) < 1e-9]
    mask = [i not in top for i in range(8)]
    res = stitch_shape_path(path, ST, mask)
    pts = res.points
    full = stitch_shape_path(path, ST).count
    assert 0 < res.count < full and not res.closed
    # the run starts and ends where the top edge's corner arcs end: on the
    # inset line (y = 17), exactly where the arcs meet the top straight
    assert (round(pts[0].x), round(pts[0].y)) == (-24, 17)
    assert (round(pts[-1].x), round(pts[-1].y)) == (24, 17)
    assert max(p.y for p in pts) <= 17 + 1e-6
    gaps = _gaps(pts)                      # every gap within the iron's 12%
    assert all(abs(g - 4.0) <= 4.0 * ST.max_dev + 1e-6 for g in gaps)


def test_mask_round_trips_and_is_ignored_when_it_no_longer_fits():
    poly = Polygon(points=[Vec2(0, 0), Vec2(50, 0), Vec2(50, 30), Vec2(0, 30)],
                   stitch=ST, stitch_edges=[True, True, False, True])
    h = holes_for_shape(poly)
    assert h.count > 0 and max(p.y for p in h.points) <= 27 + 1e-6
    d = Document()
    d.add_shape(poly)
    back = Document.from_dict(d.to_dict()).shapes[0]
    assert back.stitch_edges == [True, True, False, True]
    assert holes_for_shape(back).count == h.count
    poly.stitch_edges = [False] * 4
    assert holes_for_shape(poly).count == 0
    poly.stitch_edges = [True, False]                      # stale: ignored
    assert holes_for_shape(poly).count == holes_for_shape(
        Polygon(points=list(poly.points), stitch=ST)).count


def test_segment_index_at_picks_the_clicked_edge():
    r = Rectangle(width=60, height=40)
    assert segment_index_at(r, Vec2(0, 20.2), 1.0) == 2      # top edge
    assert segment_index_at(r, Vec2(-30.3, 0), 1.0) == 3     # left edge
    assert segment_index_at(r, Vec2(0, 0), 1.0) is None      # nowhere near


def test_canvas_toggle_and_panel_row():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    c = win.canvas
    item = c.add_shape(Rectangle(width=60, height=40, layer="Cut",
                                 stitch=StitchSettings(pitch_mm=4.0, inset=3.0)))
    n_full = item.hole_count
    idx = segment_index_at(item.model, Vec2(0, 20), 1.0)
    c.toggle_stitch_edge(item, idx)
    assert item.model.stitch_edges == [True, True, False, True]
    assert 0 < item.hole_count < n_full
    props = win.properties
    props.show_selection([item])
    assert props.edges_label.text() == "3 of 4"
    assert not props._stitch_form.isRowVisible(props.edges_row) is False
    win.undo()
    assert win.doc.shapes[0].stitch_edges is None
    win.redo()
    item = [i for i in c.scene_obj.items() if c._item_model(i) is not None][0]
    assert item.model.stitch_edges == [True, True, False, True]
    c.stitch_all_edges([item])
    assert item.model.stitch_edges is None and item.hole_count == n_full

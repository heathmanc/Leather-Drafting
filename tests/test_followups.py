"""Follow-ups from the independent verification of batches 1-2: double rows
on pieces sewn in runs and on tight arcs, baked double-row thread, seam
backstitch rings, chained unions, nudging attached dimensions, path and
import corners, and a few status lines."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Circle, PathShape, Rectangle, Transform  # noqa: E402
from leathercad.stitchline import StitchLine  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402
from leathercad.stitching import Hole, StitchResult, holes_for_shape  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _stitched(sh, **kw):
    s = StitchSettings(pitch_mm=3.85, inset=3.5)
    for k, v in kw.items():
        setattr(s, k, v)
    sh.stitch = s
    return sh


def _win(doc=None):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc or Document())
    win.show()
    c = win.canvas
    c.rebuild()
    c.snap_to_grid = c.snap_to_nodes = False
    QApplication.processEvents()
    return win, c


def _edge_dist(res, sh, idxs):
    hw, hh = sh.width / 2, sh.height / 2
    return [min(hw - abs(res.holes[i].point.x), hh - abs(res.holes[i].point.y))
            for i in idxs]


# -- double rows ----------------------------------------------------------------------
def test_double_row_goes_inward_on_a_piece_sewn_in_runs():
    pocket = _stitched(Rectangle(width=60, height=40, layer="Cut"), rows=2)
    pocket.stitch_edges = [True, True, False, True]          # open mouth
    res = holes_for_shape(pocket, Document())
    rows = res.sequences()
    assert len(rows) == 2 and res.inward != 0.0
    assert min(_edge_dist(res, pocket, rows[0])) == pytest.approx(3.5)
    inner = _edge_dist(res, pocket, rows[1])
    assert min(inner) == pytest.approx(3.5)                  # the mouth side
    assert max(inner) == pytest.approx(6.5)                  # the sewn sides
    gaps = res.chord_spacings()
    assert min(gaps) >= 0.75 * 3.85 and max(gaps) <= 1.13 * 3.85


@pytest.mark.parametrize("mk,spacing", [
    (lambda: Rectangle(width=100, height=60, corner_radius=10, layer="Cut"), 3.0),
    (lambda: Rectangle(width=100, height=60, corner_radius=10, layer="Cut"), 4.0),
    (lambda: Rectangle(width=100, height=60, corner_radius=11, layer="Cut"), 3.0),
    (lambda: Rectangle(width=100, height=60, layer="Cut"), 6.0),
    (lambda: Rectangle(width=100, height=60, layer="Cut"), 5.0),
    (lambda: Circle(rx=12, ry=12, layer="Cut"), 3.0),
    (lambda: Circle(rx=20, ry=20, layer="Cut"), 3.0),
    (lambda: Rectangle(width=60, height=40, corner_radius=8, layer="Cut"), 3.0),
])
def test_inner_row_never_pinches_on_arcs_or_wide_spacings(mk, spacing):
    res = holes_for_shape(_stitched(mk(), rows=2, row_spacing=spacing), Document())
    assert len(res.sequences()) == 2
    gaps = res.chord_spacings()
    assert min(gaps) >= 0.75 * 3.85 - 1e-9, min(gaps)
    assert max(gaps) <= 1.15 * 3.85 + 1e-9, max(gaps)


def test_tiny_pieces_keep_their_row_and_say_why():
    tiny = holes_for_shape(_stitched(Rectangle(width=8, height=8, layer="Cut"),
                                     rows=2), Document())
    assert tiny.count >= 1                                   # not lost
    small = holes_for_shape(_stitched(Rectangle(width=20, height=14, layer="Cut"),
                                      rows=2), Document())
    assert len(small.sequences(min_len=1)) == 1
    assert "second row" in small.note
    sliver = holes_for_shape(_stitched(Rectangle(width=30, height=20, layer="Cut"),
                                       rows=2, row_spacing=6.0), Document())
    assert len(sliver.sequences(min_len=1)) == 1 and "second row" in sliver.note


def test_baked_double_row_thread_is_close_to_the_live_estimate():
    from leathercad.thread import estimate_thread
    sh = _stitched(Rectangle(width=100, height=60, layer="Cut"), rows=2)
    live = holes_for_shape(sh, Document())
    baked = StitchResult(holes=[Hole(h.point, h.tangent) for h in live.holes])
    a = estimate_thread(live, sh.stitch, 3.0, 150.0)["thread_mm"]
    b = estimate_thread(baked, sh.stitch, 3.0, 150.0)["thread_mm"]
    assert abs(a - b) / a < 0.2                               # was +45 %
    # the old interleaved-pairs layout is still read as pairs
    pairs = []
    for i in range(10):
        pairs.append(Hole(Vec2(i * 4.0, 1.5), Vec2(1, 0)))
        pairs.append(Hole(Vec2(i * 4.0, -1.5), Vec2(1, 0)))
    est = estimate_thread(StitchResult(holes=pairs), StitchSettings(rows=2),
                          3.0, 150.0)
    assert est["rows"] == 2 and est["seam_mm"] == pytest.approx(72.0)


def test_seam_backstitch_rings_every_row_and_run_end():
    from leathercad_app.items import _paint_backstitch

    class Rec:
        n = 0

        def setPen(self, *_a):
            pass

        def setBrush(self, *_a):
            pass

        def drawEllipse(self, *_a):
            self.n += 1
    sl = StitchLine(points=[Vec2(0, 0), Vec2(100, 0)], corner_points=[],
                    closed=False,
                    settings=StitchSettings(pitch_mm=4, inset=0, rows=2,
                                            row_spacing=3, backstitch=3))
    res = sl.result()
    # what StitchLineItem.paint hands the painter: a relative copy that keeps
    # the layout
    rel = StitchResult(holes=list(res.holes), closed=res.closed, rows=res.rows,
                       runs=res.runs, layout=res.layout)
    rec = Rec()
    _paint_backstitch(rec, rel, sl.settings)
    assert rec.n == 2 * 2 * 3                                 # 2 rows x 2 ends x 3


# -- paths and imports ------------------------------------------------------------------
def test_repeated_vertices_and_flattened_curves_are_not_corners():
    dup = PathShape(points=[Vec2(0, 0), Vec2(50, 0), Vec2(50, 0), Vec2(97, 0)],
                    close_path=False)
    assert dup.local_path().corner_points == []
    ell = [Vec2(30 * math.cos(math.radians(a)), 20 * math.sin(math.radians(a)))
           for a in range(0, 360, 6)]
    assert PathShape(points=ell, close_path=True).local_path().corner_points == []
    kink = PathShape(points=[Vec2(0, 0), Vec2(40, 0), Vec2(80, 12)],
                     close_path=False)                        # 17 deg, isolated
    assert len(kink.local_path().corner_points) == 1


def test_imported_closed_polylines_anchor_their_real_corners():
    from leathercad.importers import _shape_from_ring
    rect = _shape_from_ring([Vec2(0, 0), Vec2(60, 0), Vec2(60, 40), Vec2(0, 40)],
                            True)
    assert rect.corner_mask == [True, True, True, True]
    res = holes_for_shape(_stitched(rect), Document())
    for c in (Vec2(3.5, 3.5), Vec2(56.5, 3.5), Vec2(56.5, 36.5), Vec2(3.5, 36.5)):
        assert min((p - c).length() for p in res.points) < 1e-6
    arc = [Vec2(50 * math.cos(math.radians(a)), 50 * math.sin(math.radians(a)))
           for a in range(0, 360, 6)]
    assert sum(_shape_from_ring(arc, True).corner_mask) == 0


def test_fit_none_with_symmetry_explains_itself():
    res = holes_for_shape(_stitched(Rectangle(width=60, height=40, layer="Cut"),
                                    fit="none", symmetry="vertical"), Document())
    assert "Fit to corners" in res.note


# -- canvas commands ------------------------------------------------------------------------
def test_union_merges_a_chain_of_overlapping_shapes(qapp):
    win, c = _win()
    items = [c.add_shape(Rectangle(width=40, height=20, layer="Cut",
                                   transform=Transform(x=x))) for x in (0, 30, 60)]
    c.scene_obj.clearSelection()
    for it in items:
        it.setSelected(True)
    c.boolean_selected("union")
    assert len(win.doc.shapes) == 1
    b = win.doc.shapes[0].bounds()
    assert b[0] == pytest.approx(-20) and b[2] == pytest.approx(80)
    win.hide()


def test_nudging_an_attached_dimension_moves_its_line_off_the_shape(qapp):
    from leathercad_app.items import DimensionItem
    win, c = _win()
    c.add_shape(Rectangle(width=40, height=20, layer="Cut"))
    c.snap_to_nodes = True
    c._add_dimension(QPointF(-20, -10), QPointF(20, -10))     # snapped corners
    dim = win.doc.dimensions[0]
    assert dim.a_ref and dim.b_ref
    item = next(i for i in c.scene_obj.items() if isinstance(i, DimensionItem))
    c.scene_obj.clearSelection()
    item.setSelected(True)
    off0 = dim.offset
    c.nudge_selected(0.0, -5.0)
    assert dim.p1 == Vec2(-20, -10) and dim.p2 == Vec2(20, -10)   # still attached
    assert dim.offset == pytest.approx(off0 - 5.0)
    win.hide()


def test_copy_along_a_slanted_rail_leaves_no_clone_on_the_original(qapp):
    win, c = _win()
    rail = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(100, 50)],
                                 close_path=False, layer="Cut", construction=True))
    piece = c.add_shape(Circle(rx=2, ry=2, layer="Cut"))
    c.scene_obj.clearSelection()
    rail.setSelected(True)
    piece.setSelected(True)
    assert c.array_along_path(5, 0.0, True)                   # follow = True
    circles = [s for s in win.doc.shapes if s.kind == "circle"]
    at_origin = [s for s in circles if math.hypot(s.transform.x, s.transform.y) < 0.05]
    assert len(at_origin) == 1 and len(circles) == 5
    win.hide()


def test_break_apart_and_join_report(qapp):
    win, c = _win()
    msgs = []
    c.statusMessage.connect(msgs.append)
    c.scene_obj.clearSelection()
    c.break_apart_selected()
    assert "Break apart" in msgs[-1]
    a = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(20, 0)], close_path=False,
                              layer="Cut"))
    b = c.add_shape(PathShape(points=[Vec2(20, 0), Vec2(20, 20)], close_path=False,
                              layer="Cut"))
    c.scene_obj.clearSelection()
    a.setSelected(True)
    b.setSelected(True)
    c.join_selected()
    assert msgs[-1].startswith("Joined 2 pieces into 1 path")
    win.hide()

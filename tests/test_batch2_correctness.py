"""Drawing and output correctness: focus after drawing, tool switching,
Measure / Dimension in drag mode, attached holes that follow a resize, corner
anchors on pen paths, honest double rows, keep-out moves by typing, offsets
of guides, symmetry that never makes a fit worse, per-run backstitch and
thread, and the small modify-command polish."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.holes import LooseHole  # noqa: E402
from leathercad.shapes import (Circle, Edge, EditablePath, PathShape,  # noqa: E402
                               Polygon, Rectangle, Transform)
from leathercad.stitchsettings import StitchSettings  # noqa: E402
from leathercad.stitching import flip_symmetry, holes_for_shape  # noqa: E402
from leathercad_app import canvas as canvas_mod  # noqa: E402
from leathercad_app.items import DimensionItem, HoleItem, ShapeItem  # noqa: E402


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
    win.resize(1400, 900)
    win.show()
    c = win.canvas
    c.rebuild()
    c.snap_to_grid = c.snap_to_nodes = False
    c.drag_to_draw = False
    QApplication.processEvents()
    return win, c


def _ev(c, kind, x, y, buttons=Qt.LeftButton, mods=Qt.NoModifier):
    vp = c.viewport()
    pt = QPointF(c.mapFromScene(QPointF(x, y)))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    QApplication.sendEvent(vp, QMouseEvent(kind, pt, vp.mapToGlobal(pt), btn,
                                           buttons, mods))
    QApplication.processEvents()


def _click(c, x, y):
    _ev(c, QEvent.MouseButtonPress, x, y)
    _ev(c, QEvent.MouseButtonRelease, x, y, Qt.NoButton)


def _drag(c, x0, y0, x1, y1):
    _ev(c, QEvent.MouseButtonPress, x0, y0)
    for k in range(1, 6):
        _ev(c, QEvent.MouseMove, x0 + (x1 - x0) * k / 5, y0 + (y1 - y0) * k / 5)
    _ev(c, QEvent.MouseButtonRelease, x1, y1, Qt.NoButton)


def _item_of(c, model):
    return next(i for i in c.scene_obj.items() if c._item_model(i) is model)


# -- drawing workflow ----------------------------------------------------------
def test_focus_lands_in_the_size_field_after_drawing(qapp):
    win, c = _win()
    win._select_mode(canvas_mod.RECT)
    _click(c, 0, 0)
    _click(c, 40, 30)                         # first shape, nothing selected before
    for _ in range(3):
        QApplication.processEvents()
    assert len(win.doc.shapes) == 1
    assert QApplication.focusWidget() is win.properties.w
    win._select_mode(canvas_mod.CIRCLE)
    _click(c, 100, 0)
    _click(c, 110, 0)                         # a later shape, one selected before
    for _ in range(3):
        QApplication.processEvents()
    assert QApplication.focusWidget() is win.properties.circ_r
    win._select_mode(canvas_mod.MEASURE)      # creates nothing: no focus jump
    _click(c, 0, 50)
    _click(c, 30, 50)
    for _ in range(3):
        QApplication.processEvents()
    assert QApplication.focusWidget() is not win.properties.w
    win.hide()


def test_switching_tools_abandons_the_half_drawn_shape(qapp):
    win, c = _win()
    win._select_mode(canvas_mod.RECT)
    _click(c, -50, -50)                       # first corner only
    assert c._start is not None
    win._select_mode(canvas_mod.CIRCLE)
    assert c._start is None and c._preview is None
    _click(c, 0, 0)                           # the circle's CENTRE, not its rim
    assert len(win.doc.shapes) == 0
    win._select_mode(canvas_mod.POLYGON)
    _click(c, 0, 0)
    _click(c, 20, 0)
    win._select_mode(canvas_mod.LINE)
    assert c._poly_pts == [] and c._preview is None
    win._select_mode(canvas_mod.POLYGON)     # re-picking the same tool is a no-op
    _click(c, 0, 20)
    win._select_mode(canvas_mod.POLYGON)
    assert len(c._poly_pts) == 1
    win.hide()


def test_measure_and_dimension_work_with_drag_to_draw(qapp):
    win, c = _win()
    c.drag_to_draw = True
    msgs = []
    c.statusMessage.connect(msgs.append)
    win._select_mode(canvas_mod.MEASURE)
    _drag(c, 0, 0, 30, 0)
    assert msgs and "length" in msgs[-1]      # the result stays up
    win._select_mode(canvas_mod.DIMENSION)
    _drag(c, 0, 20, 50, 20)
    assert len(win.doc.dimensions) == 1
    assert abs(win.doc.dimensions[0].length() - 50) < 1e-6
    win._select_mode(canvas_mod.DIMENSION)    # click-click still works too
    _click(c, 0, 40)
    _click(c, 20, 40)
    assert len(win.doc.dimensions) == 2
    dim_item = next(i for i in c.scene_obj.items()
                    if isinstance(i, DimensionItem) and i.isSelected())
    c.nudge_selected(5.0, 0.0)                # a new dimension is selected...
    assert abs(dim_item.dim.p1.x - 5.0) < 1e-9      # ...and can be nudged
    win.hide()


def test_measure_result_survives_the_finishing_click(qapp):
    win, c = _win()
    msgs = []
    c.statusMessage.connect(msgs.append)
    win._select_mode(canvas_mod.MEASURE)
    _click(c, 0, -50)
    _click(c, 30, -50)
    assert "length" in msgs[-1] and "Select" not in msgs[-1]
    assert c.tool == canvas_mod.SELECT
    win.hide()


def test_degenerate_polygon_and_seam_are_refused(qapp):
    win, c = _win()
    msgs = []
    c.statusMessage.connect(msgs.append)
    win._select_mode(canvas_mod.POLYGON)
    _click(c, 0, 0)
    _click(c, 40, 0)
    c._finalize_poly()                        # Enter
    assert len(win.doc.shapes) == 0
    assert len(c._poly_pts) == 2 and "three corners" in msgs[-1]
    _click(c, 20, 0)                          # still in a line
    c._finalize_poly()
    assert len(win.doc.shapes) == 0
    _click(c, 20, 30)
    c._finalize_poly()
    assert len(win.doc.shapes) == 1 and len(win.doc.shapes[0].points) == 4
    win._select_mode(canvas_mod.STITCHLINE)
    _click(c, 0, 50)
    _click(c, 0, 50)
    c._finalize_poly()
    assert len(win.doc.stitch_lines) == 0 and "Two different" in msgs[-1]
    _click(c, 40, 50)
    c._finalize_poly()
    assert len(win.doc.stitch_lines) == 1
    win.hide()


# -- attached holes follow a resize ------------------------------------------------
def _attached_rect(c, doc):
    doc.add_shape(_stitched(Rectangle(width=40, height=20, layer="Cut"),
                            pitch_mm=4.0))
    doc.add_hole(LooseHole(point=Vec2(0, 0)))
    c.rebuild()
    rect = _item_of(c, doc.shapes[0])
    hole = next(i for i in c.scene_obj.items() if isinstance(i, HoleItem))
    c.scene_obj.clearSelection()
    rect.setSelected(True)
    hole.setSelected(True)
    c.group_selected()
    return _item_of(c, doc.shapes[0])


def _edge_gap(sh):
    return sh.width / 2 - max(h.point.x for h in sh.baked_holes)


def test_attached_holes_follow_scale_grips_and_typed_size(qapp):
    doc = Document()
    win, c = _win(doc)
    item = _attached_rect(c, doc)
    sh = item.model
    assert len(sh.baked_holes) == 23 and abs(_edge_gap(sh) - 3.5) < 1e-6
    c.scene_obj.clearSelection()
    item.setSelected(True)
    c.scale_selected(2.0, 2.0)
    assert sh.width == 80 and abs(_edge_gap(sh) - 7.0) < 1e-6   # scaled with it
    item.set_resize_extents(60.0, 20.0)                        # a grip pull
    assert sh.width == 120 and abs(_edge_gap(sh) - 10.5) < 1e-6
    p = win.properties
    p.show_selection([item])
    p.w.setValue(40.0)                                         # typed width
    assert sh.width == 40 and abs(_edge_gap(sh) - 3.5) < 1e-6
    ys = [h.point.y for h in sh.baked_holes]
    assert abs(max(ys) - 13.0) < 1e-6          # height (40 after the scale) untouched
    win.hide()


# -- engine ---------------------------------------------------------------------------
def test_pen_join_and_free_paths_anchor_their_corners():
    corner = Vec2(40, 60)
    pen = _stitched(EditablePath(nodes=[Vec2(0, 60), Vec2(40, 60), Vec2(40, 90)],
                                 edges=[Edge("line"), Edge("line")], closed=False,
                                 layer="Cut"))
    free = _stitched(PathShape(points=[Vec2(0, 60), Vec2(40, 60), Vec2(40, 90)],
                               close_path=False, layer="Cut"))
    for sh in (pen, free):
        res = holes_for_shape(sh, Document())
        assert min((p - corner).length() for p in res.points) < 1e-6
    quad = _stitched(EditablePath(
        nodes=[Vec2(-30, -20), Vec2(30, -20), Vec2(30, 20), Vec2(-30, 20)],
        edges=[Edge("line")] * 4, closed=True, layer="Cut"))
    res = holes_for_shape(quad, Document())
    for sx in (-1, 1):
        for sy in (-1, 1):
            c = Vec2(26.5 * sx, 16.5 * sy)
            assert min((p - c).length() for p in res.points) < 1e-6
    # a smooth bezier join is not a corner
    smooth = _stitched(EditablePath(
        nodes=[Vec2(0, 0), Vec2(40, 0), Vec2(80, 0)],
        edges=[Edge("bezier", c1=Vec2(10, 0), c2=Vec2(30, 0)),
               Edge("bezier", c1=Vec2(50, 0), c2=Vec2(70, 0))],
        closed=False, layer="Cut"))
    assert len(smooth.local_path().corner_points) == 0
    # a traced curve (many small turns) gains no anchors
    arc_pts = [Vec2(50 * math.cos(math.radians(a)), 50 * math.sin(math.radians(a)))
               for a in range(0, 91, 3)]
    assert len(PathShape(points=arc_pts, close_path=False).local_path()
               .corner_points) == 0


def test_double_rows_are_inside_the_inset_and_evenly_spaced():
    sh = _stitched(Rectangle(width=60, height=40, layer="Cut"), rows=2,
                   row_spacing=3.0)
    res = holes_for_shape(sh, Document())
    outer, inner = res.sequences(row=0)[0], res.sequences(row=1)[0]
    assert abs(max(res.holes[i].point.x for i in outer) - 26.5) < 1e-6
    assert abs(max(res.holes[i].point.x for i in inner) - 23.5) < 1e-6
    assert abs(max(res.holes[i].point.y for i in inner) - 13.5) < 1e-6
    gaps = res.chord_spacings()
    assert min(gaps) >= 0.75 * 3.85 and max(gaps) <= 1.13 * 3.85
    for kw in (dict(width=30, height=20), dict(width=90, height=55, corner_radius=8),
               dict(width=200, height=30)):
        r = holes_for_shape(_stitched(Rectangle(layer="Cut", **kw), rows=2),
                            Document())
        g = r.chord_spacings()
        assert min(g) >= 0.75 * 3.85 and max(g) <= 1.13 * 3.85, kw
    # a drawn seam keeps a row either side, rung-aligned on a straight
    from leathercad.stitchline import StitchLine
    sl = StitchLine(points=[Vec2(0, 0), Vec2(100, 0)], corner_points=[],
                    closed=False,
                    settings=StitchSettings(pitch_mm=4, inset=0, rows=2,
                                            row_spacing=3))
    rr = sl.result()
    a, b = rr.sequences()
    assert len(a) == len(b) == 26
    assert all(abs(rr.holes[i].point.x - rr.holes[j].point.x) < 1e-6
               for i, j in zip(a, b))
    assert {round(rr.holes[i].point.y, 2) for i in a + b} == {-1.5, 1.5}


def test_symmetry_never_worsens_an_already_symmetric_fit():
    for mk in (lambda: Rectangle(width=60, height=40, layer="Cut"),
               lambda: Rectangle(width=80, height=50, corner_radius=5, layer="Cut"),
               lambda: Circle(rx=20, ry=20, layer="Cut")):
        plain = holes_for_shape(_stitched(mk()), Document())
        for axis in ("vertical", "horizontal"):
            sym = holes_for_shape(_stitched(mk(), symmetry=axis), Document())
            assert flip_symmetry(sym.points, axis)[0]
            assert sym.note == ""
            if flip_symmetry(plain.points, axis)[0]:
                # already symmetric: the plain fit is kept as it is
                assert sym.points == plain.points
            else:                           # (a circle's odd count, say)
                assert sym.worst_deviation(3.85) < 0.12
    pent = [Vec2(30 * math.cos(t), 30 * math.sin(t))
            for t in (i * 2 * math.pi / 5 for i in range(5))]
    plain = holes_for_shape(_stitched(Polygon(points=list(pent), layer="Cut")),
                            Document())
    asym = holes_for_shape(_stitched(Polygon(points=list(pent), layer="Cut"),
                                     symmetry="vertical"), Document())
    assert asym.points == plain.points and "isn't" in asym.note


def test_thread_and_backstitch_count_every_run():
    from leathercad.thread import estimate_thread
    strap = _stitched(Rectangle(width=100, height=60, layer="Cut"), backstitch=3)
    one = estimate_thread(holes_for_shape(strap, Document()), strap.stitch,
                          thickness_mm=3.0, tail_mm=150.0)
    strap.stitch_edges = [True, False, True, False]        # two separate runs
    res = holes_for_shape(strap, Document())
    two = estimate_thread(res, strap.stitch, thickness_mm=3.0, tail_mm=150.0)
    # two runs: two more needle tails, and backstitch at four ends not one
    assert two["thread_mm"] > one["thread_mm"] - 2 * 2 * 3.85 * 25 - 300 \
        and two["seam_mm"] < one["seam_mm"]
    assert len(res.sequences()) == 2
    # a ring: no ends, so no rings are painted; two runs ring 4 ends
    from leathercad_app.items import _paint_backstitch

    class Rec:
        def __init__(self):
            self.n = 0

        def setPen(self, *_a):
            pass

        def setBrush(self, *_a):
            pass

        def drawEllipse(self, *_a):
            self.n += 1
    rec = Rec()
    _paint_backstitch(rec, res, strap.stitch)
    assert rec.n == 4 * 3


# -- panels and commands ------------------------------------------------------------------
def test_typed_position_of_hardware_restitches_the_piece(qapp):
    from leathercad.hardware import build
    doc = Document()
    win, c = _win(doc)
    body = c.add_shape(_stitched(Rectangle(width=190, height=95, corner_radius=8,
                                           layer="Cut"), pitch_mm=4.0, inset=3.0))
    parts, _texts = build("snap_20")
    snap = None
    for sh in parts:
        sh.transform.x, sh.transform.y = -92.0, 0.0
        it = c.add_shape(sh)
        if getattr(sh, "keepout", 0):
            snap = it
    assert snap is not None
    before = [(round(h.point.x, 4), round(h.point.y, 4)) for h in body._holes.holes]
    p = win.properties
    p.show_selection([snap])
    p.pos_y.setValue(20.0)                    # typed, not dragged
    after = [(round(h.point.x, 4), round(h.point.y, 4)) for h in body._holes.holes]
    truth = holes_for_shape(body.model, doc)
    assert after != before
    assert len(after) == truth.count
    win.hide()


def test_offsetting_a_guide_gives_a_guide():
    from leathercad.offset import offset_shape
    guide = PathShape(points=[Vec2(-50, 0), Vec2(50, 0)], close_path=False,
                      layer="Cut")
    guide.construction = True
    out = offset_shape(guide, 5.0)
    assert out.construction is True
    cut = PathShape(points=[Vec2(-50, 0), Vec2(50, 0)], close_path=False,
                    layer="Cut")
    assert offset_shape(cut, 5.0).construction is False


def test_booleans_leave_shapes_that_do_not_touch_alone(qapp):
    win, c = _win()
    msgs = []
    c.statusMessage.connect(msgs.append)
    a = c.add_shape(_stitched(Rectangle(width=40, height=20, layer="Cut")))
    b = c.add_shape(_stitched(Rectangle(width=40, height=20, layer="Cut",
                                        transform=Transform(x=100))))
    c.scene_obj.clearSelection()
    a.setSelected(True)
    b.setSelected(True)
    for op in ("union", "difference", "intersection"):
        c.boolean_selected(op)
        assert len(win.doc.shapes) == 2 and all(s.stitch.enabled
                                                for s in win.doc.shapes)
        assert "Nothing to" in msgs[-1]
    c.scene_obj.clearSelection()
    d = c.add_shape(Rectangle(width=40, height=20, layer="Cut",
                              transform=Transform(x=20)))
    a.setSelected(True)
    d.setSelected(True)
    c.boolean_selected("union")
    assert "Merged" in msgs[-1] and len(win.doc.shapes) == 2
    win.hide()


def test_modify_commands_report_and_join_does_not_fake_an_undo_step(qapp):
    win, c = _win()
    msgs = []
    c.statusMessage.connect(msgs.append)
    a = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(20, 0)], close_path=False,
                              layer="Cut"))
    b = c.add_shape(PathShape(points=[Vec2(50, 0), Vec2(70, 0)], close_path=False,
                              layer="Cut"))
    ids = [s.shape_id for s in win.doc.shapes]
    c.scene_obj.clearSelection()
    a.setSelected(True)
    b.setSelected(True)
    c.join_selected()
    assert [s.shape_id for s in win.doc.shapes] == ids      # untouched
    assert "Nothing to join" in msgs[-1]
    c.make_group()
    assert "Grouped 2" in msgs[-1]
    c.ungroup_group()
    assert "Ungrouped" in msgs[-1]
    c.scene_obj.clearSelection()
    c.rotate_selected(90)
    assert "Select something" in msgs[-1]
    c.mirror_selected()
    assert "Select something" in msgs[-1]
    c.ungroup_selected()
    assert "Nothing to explode" in msgs[-1]
    win.hide()


def test_fillet_keeps_the_selection_and_opacity_steps_commit(qapp):
    win, c = _win()
    item = c.add_shape(_stitched(Polygon(points=[Vec2(-30, -20), Vec2(30, -20),
                                                 Vec2(30, 20), Vec2(-30, 20)],
                                         layer="Cut")))
    c.fillet_radius = 6.0
    c._do_fillet(Vec2(30, -20))
    sel = c.selected_items()
    assert len(sel) == 1 and sel[0].model is win.doc.shapes[0]
    p = win.properties
    p.show_selection(sel)
    p.opacity.setValue(50)                    # keyboard / groove, no drag
    assert abs(win.doc.shapes[0].opacity - 0.5) < 1e-9
    win.undo()
    assert abs(win.doc.shapes[0].opacity - 1.0) < 1e-9
    win.hide()


def test_align_moves_seams_and_loose_holes_too(qapp):
    from leathercad.stitchline import StitchLine
    doc = Document()
    win, c = _win(doc)
    msgs = []
    c.statusMessage.connect(msgs.append)
    r1 = c.add_shape(Rectangle(width=20, height=10, layer="Cut"))
    r2 = c.add_shape(Rectangle(width=20, height=10, layer="Cut",
                               transform=Transform(x=50, y=30)))
    sl = c.add_stitch_line(StitchLine(points=[Vec2(100, 40), Vec2(120, 40)],
                                      corner_points=[], closed=False,
                                      settings=StitchSettings(pitch_mm=4,
                                                              inset=0)))
    c.scene_obj.clearSelection()
    for it in (r1, r2, sl):
        it.setSelected(True)
    c.align_selected("left")
    assert abs(min(p.x for p in sl.line.points) - (-10.0)) < 1e-6
    c.scene_obj.clearSelection()
    r1.setSelected(True)
    c.align_selected("left")
    assert "Align needs" in msgs[-1]
    win.hide()


def test_copy_along_path_skips_the_clone_on_the_original(qapp):
    win, c = _win()
    msgs = []
    c.statusMessage.connect(msgs.append)
    rail = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(100, 0)],
                                 close_path=False, layer="Cut",
                                 construction=True))
    short = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(0, 10)],
                                  close_path=False, layer="Cut"))
    piece = c.add_shape(Circle(rx=2, ry=2, layer="Cut"))
    c.scene_obj.clearSelection()
    rail.setSelected(True)
    piece.setSelected(True)
    assert c.array_along_path(5, 0.0, False)
    circles = [s for s in win.doc.shapes if s.kind == "circle"]
    assert len(circles) == 5                     # original + 4 copies, no double
    xs = sorted(s.transform.x for s in circles)
    # (a flattened circle's box centre is a hundredth off its true centre)
    assert all(abs(x - want) < 0.02 for x, want in zip(xs, (0, 25, 50, 75, 100)))
    assert "skipped" in msgs[-1]
    # two plain open paths: the LONGER one is the rail
    c.scene_obj.clearSelection()
    rail.model.construction = False
    rail.setSelected(True)
    short.setSelected(True)
    assert c.array_along_path(3, 0.0, False)
    copies = [s for s in win.doc.shapes
              if s.kind == "path" and s is not rail.model and s is not short.model]
    # the short stub (centre y = 5) was copied along the 100 mm line, its
    # centre on the rail at x 0 / 50 / 100 -- not the line along the stub
    assert sorted(round(s.transform.x) for s in copies) == [0, 50, 100]
    assert all(abs(s.transform.y + 5.0) < 1e-6 for s in copies)
    win.hide()

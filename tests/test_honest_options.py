"""Every stitch option offered does what it says, and nothing else is offered:
the Fit dropdown's two real modes, the three-quarter-pitch rule for straddled
corners, a readout that measures the seam the way the needle travels, options
greyed out where the engine never reads them, a circle with one radius, and
edge masks that survive node editing."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Circle, Ellipse, Polygon, Rectangle,  # noqa: E402
                               Transform, stitched_edge_flags)
from leathercad.stitchsettings import StitchSettings  # noqa: E402
from leathercad.stitching import holes_for_shape  # noqa: E402


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
    return win, c


def _item_of(c, model):
    return next(i for i in c.scene_obj.items() if c._item_model(i) is model)


# -- engine -------------------------------------------------------------------
@pytest.mark.parametrize("pitch", [3.0, 3.38, 3.85, 4.5])
def test_straddle_never_crams_below_three_quarters_pitch(pitch):
    """A straddled corner on the awkward radii (arc between ~1.1 and ~2.6
    pitches) used to force three steps round it at ~60 % of the pitch."""
    for r10 in range(50, 160, 5):
        r = r10 / 10.0
        sh = _stitched(Rectangle(width=90, height=55, corner_radius=r,
                                 layer="Cut"),
                       corner_style="straddle", pitch_mm=pitch)
        res = holes_for_shape(sh, Document())
        gaps = res.chord_spacings()
        assert min(gaps) >= 0.75 * pitch - 1e-6, (pitch, r, min(gaps))


def test_straddle_still_straddles_where_it_fits():
    sh = _stitched(Rectangle(width=90, height=55, corner_radius=14,
                             layer="Cut"), corner_style="straddle")
    res = holes_for_shape(sh, Document())
    # no hole on any apex: the 45° point of each inset arc (radius 10.5)
    for sx in (-1, 1):
        for sy in (-1, 1):
            apex = Vec2(sx * (45 - 14 + 10.5 * math.cos(math.pi / 4)),
                        sy * (27.5 - 14 + 10.5 * math.sin(math.pi / 4)))
            assert min((p - apex).length() for p in res.points) > 0.5


def test_fit_values_other_than_none_are_one_mode():
    for mk in (lambda: Rectangle(width=60, height=40, layer="Cut"),
               lambda: Circle(rx=20, ry=20, layer="Cut")):
        sets = {}
        for fit in ("auto", "endpoints", "closed", "none"):
            res = holes_for_shape(_stitched(mk(), fit=fit), Document())
            sets[fit] = [(round(p.x, 6), round(p.y, 6)) for p in res.points]
        assert sets["auto"] == sets["endpoints"] == sets["closed"]
        assert sets["none"] != sets["auto"]


def test_chord_spacings_include_the_closing_gap():
    res = holes_for_shape(_stitched(Rectangle(width=60, height=40, layer="Cut"),
                                    fit="none"), Document())
    gaps = res.chord_spacings()
    assert len(gaps) == res.count                    # one per hole, round the loop
    assert min(gaps) < 3.0 < max(gaps)               # the short closing gap shows
    fitted = holes_for_shape(_stitched(Rectangle(width=60, height=40,
                                                 layer="Cut")), Document())
    assert max(fitted.chord_spacings()) - min(fitted.chord_spacings()) < 0.2


def test_chord_spacings_walk_rows_and_runs_separately():
    two = holes_for_shape(_stitched(Rectangle(width=60, height=40, layer="Cut"),
                                    rows=2), Document())
    assert two.rows == 2 and len(two.sequences()) == 2
    # the across-row rung (3 mm) is not a stitch; neither is a diagonal
    assert all(abs(g - 3.0) > 0.2 for g in two.chord_spacings())
    one = holes_for_shape(_stitched(Rectangle(width=60, height=40,
                                              layer="Cut")), Document())
    # one row's length, not the zig-zag through both (2.75x before)
    assert abs(two.seam_length() - one.seam_length()) < 10.0
    strap = _stitched(Rectangle(width=100, height=60, layer="Cut"))
    strap.stitch_edges = [True, False, True, False]  # both long edges only
    runs = holes_for_shape(strap, Document())
    assert runs.runs is not None and len(runs.runs) == 2
    assert max(runs.chord_spacings()) < 4.5          # no 50 mm "gap"
    assert len(runs.sequences()) == 2


def test_max_dev_is_inert_and_worst_deviation_reports():
    a = holes_for_shape(_stitched(Rectangle(width=93.7, height=61.3,
                                            corner_radius=15, layer="Cut"),
                                  pitch_mm=5.0, inset=4.0, max_dev=0.0),
                        Document())
    b = holes_for_shape(_stitched(Rectangle(width=93.7, height=61.3,
                                            corner_radius=15, layer="Cut"),
                                  pitch_mm=5.0, inset=4.0, max_dev=1.0),
                        Document())
    assert a.points == b.points
    assert a.worst_deviation(5.0) > 0.12             # a misfit the UI must flag


# -- edge masks survive node editing -------------------------------------------
def _pocket():
    sh = _stitched(Polygon(points=[Vec2(-30, -20), Vec2(30, -20), Vec2(30, 20),
                                   Vec2(-30, 20)], layer="Cut"))
    sh.stitch_edges = [True, True, False, True]      # top edge = the open mouth
    return sh


def _mouth_open(sh) -> bool:
    """The holes stop short of the top edge (y = 20 - inset)."""
    res = holes_for_shape(sh, Document())
    top = [p for p in res.points if p.y > 16.4 and abs(p.x) < 20]
    return not top


def test_inserting_a_node_keeps_the_mouth_open(qapp):
    win, c = _win()
    item = c.add_shape(_pocket())
    assert _mouth_open(item.model)
    c.enter_vertex_edit(item)
    assert c.insert_node_at(Vec2(0, -20))            # a node on the bottom edge
    flags = stitched_edge_flags(item.model)
    assert len(flags) == 5 and flags.count(False) == 1
    assert _mouth_open(item.model)
    win.hide()


def test_deleting_a_node_keeps_the_mouth_open(qapp):
    from leathercad_app.items import VertexHandle
    win, c = _win()
    sh = _pocket()
    sh.points.insert(1, Vec2(0, -20))                # five nodes
    sh.stitch_edges = [True, True, True, False, True]
    item = c.add_shape(sh)
    c.enter_vertex_edit(item)
    handle = min((h for h in c.scene_obj.items() if isinstance(h, VertexHandle)),
                 key=lambda h: (Vec2(h.pos().x(), h.pos().y()) - Vec2(0, -20))
                 .length())
    assert c.delete_node(handle)
    flags = stitched_edge_flags(item.model)
    assert len(flags) == 4 and flags == [True, True, False, True]
    assert _mouth_open(item.model)
    win.hide()


def test_filleting_a_corner_keeps_the_mouth_open(qapp):
    win, c = _win()
    item = c.add_shape(_pocket())
    c.fillet_radius = 6.0
    c._do_fillet(Vec2(30, -20))                      # round the bottom-right
    sh = win.doc.shapes[0]
    assert sh.kind != "polygon"                      # now an editable path
    flags = stitched_edge_flags(sh)
    assert len(flags) == 5 and flags.count(False) == 1
    assert _mouth_open(sh)
    win.hide()


def test_entering_node_edit_keeps_the_holes(qapp):
    win, c = _win()
    item = c.add_shape(_stitched(Polygon(
        points=[Vec2(-40, -25), Vec2(40, -25), Vec2(40, 25), Vec2(-40, 25)],
        layer="Cut")))
    before = item._holes.count
    c.enter_vertex_edit(item)
    qapp.processEvents()
    assert item._holes is not None and item._holes.count == before
    win.hide()


# -- the Properties panel --------------------------------------------------------
def test_fit_dropdown_has_two_honest_entries(qapp):
    win, c = _win()
    item = c.add_shape(_stitched(Rectangle(width=60, height=40, layer="Cut"),
                                 fit="closed"))              # an old file
    c.scene_obj.clearSelection()
    item.setSelected(True)
    qapp.processEvents()
    p = win.properties
    assert p.fit.count() == 2
    assert [p.fit.itemData(i) for i in range(2)] == ["auto", "none"]
    assert p.fit.currentIndex() == 0                 # 'closed' loads as fitted
    p.fit.setCurrentIndex(1)
    assert item.model.stitch.fit == "none"
    assert "chord spacing" in p.readout.text()
    win.hide()


def test_options_the_engine_ignores_are_not_offered(qapp):
    from leathercad.shapes import PathShape
    win, c = _win()
    p = win.properties
    form = p._stitch_form
    closed = c.add_shape(_stitched(Rectangle(width=60, height=40, layer="Cut")))
    c.scene_obj.clearSelection()
    closed.setSelected(True)
    qapp.processEvents()
    assert p.corner_style.isEnabled() and p.symmetry.isEnabled()
    assert form.isRowVisible(p.inset)
    assert not form.isRowVisible(p.backstitch)       # a loop has no ends
    open_path = c.add_shape(_stitched(PathShape(
        points=[Vec2(0, 60), Vec2(40, 60), Vec2(40, 90)], close_path=False,
        layer="Cut")))
    c.scene_obj.clearSelection()
    open_path.setSelected(True)
    qapp.processEvents()
    assert not p.corner_style.isEnabled() and not p.symmetry.isEnabled()
    assert "open path" in p.corner_style.toolTip()
    assert not form.isRowVisible(p.inset)            # never read for open paths
    assert form.isRowVisible(p.backstitch)
    pocket = c.add_shape(_pocket())                  # closed, but sewn as a run
    c.scene_obj.clearSelection()
    pocket.setSelected(True)
    qapp.processEvents()
    assert form.isRowVisible(p.inset) and form.isRowVisible(p.backstitch)
    win.hide()


def test_custom_pitch_shows_its_exact_value(qapp):
    win, c = _win()
    p = win.properties
    item = c.add_shape(_stitched(Rectangle(width=60, height=40, layer="Cut"),
                                 pitch_mm=3.39))
    c.scene_obj.clearSelection()
    item.setSelected(True)
    qapp.processEvents()
    assert "3.38" in p.pitch_combo.currentText()     # nearest iron, as before
    assert p._stitch_form.isRowVisible(p.pitch)      # ...but the real number shows
    assert abs(p.pitch.value() - 3.39) < 1e-6
    p.pitch_combo.setCurrentIndex(p.pitch_combo.currentIndex())  # no-op
    exact = c.add_shape(_stitched(Rectangle(width=60, height=40, layer="Cut"),
                                  pitch_mm=3.85))
    c.scene_obj.clearSelection()
    exact.setSelected(True)
    qapp.processEvents()
    assert not p._stitch_form.isRowVisible(p.pitch)
    win.hide()


def test_misfit_is_flagged_in_the_readout(qapp):
    win, c = _win()
    p = win.properties
    item = c.add_shape(_stitched(Rectangle(width=93.7, height=61.3,
                                           corner_radius=15, layer="Cut"),
                                 pitch_mm=5.0, inset=4.0))
    c.scene_obj.clearSelection()
    item.setSelected(True)
    qapp.processEvents()
    assert "off pitch" in p.readout.text()
    good = c.add_shape(_stitched(Rectangle(width=60, height=40, layer="Cut")))
    c.scene_obj.clearSelection()
    good.setSelected(True)
    qapp.processEvents()
    assert "off pitch" not in p.readout.text()
    win.hide()


def test_dimension_shows_no_stitch_controls(qapp):
    win, c = _win()
    p = win.properties
    pocket = c.add_shape(_pocket())
    c.scene_obj.clearSelection()
    pocket.setSelected(True)
    qapp.processEvents()
    assert p._stitch_form.isRowVisible(p.edges_row)  # "3 of 4"
    from PySide6.QtCore import QPointF
    c._add_dimension(QPointF(0, 0), QPointF(50, 0))
    from leathercad_app.items import DimensionItem
    dim = next(i for i in c.scene_obj.items() if isinstance(i, DimensionItem))
    c.scene_obj.clearSelection()
    dim.setSelected(True)
    qapp.processEvents()
    assert not p.g_stitch.isVisible()
    assert not p._stitch_form.isRowVisible(p.edges_row)
    win.hide()


def test_circle_has_one_radius(qapp):
    win, c = _win()
    p = win.properties
    item = c.add_shape(Circle(rx=20.6, ry=20.6, layer="Cut"))
    c.scene_obj.clearSelection()
    item.setSelected(True)
    qapp.processEvents()
    assert p.g_circle.isVisible() and not p.g_ellipse.isVisible()
    p.circ_r.setValue(30.0)
    m = item.model
    assert m.rx == 30.0 and m.ry == 30.0
    assert abs(p.circ_d.value() - 60.0) < 1e-9
    p.circ_d.setValue(50.0)
    assert m.rx == 25.0 and m.ry == 25.0
    ell = c.add_shape(Ellipse(rx=30, ry=20, layer="Cut"))
    c.scene_obj.clearSelection()
    ell.setSelected(True)
    qapp.processEvents()
    assert p.g_ellipse.isVisible() and not p.g_circle.isVisible()
    win.hide()


def test_a_pulled_apart_circle_is_treated_as_the_oval_it_is():
    from leathercad.offset import offset_shape
    oval = Circle(rx=30, ry=20, layer="Cut")       # what the old panel made
    out = offset_shape(oval, 3.0)
    xs = [p.x for p in out.world_polyline()[0]]
    ys = [p.y for p in out.world_polyline()[0]]
    assert abs(max(xs) - 33) < 0.1 and abs(max(ys) - 23) < 0.1
    d = Document()
    d.add_shape(oval)
    back = Document.from_dict(d.to_dict())
    assert back.shapes[0].kind == "ellipse"
    assert back.shapes[0].rx == 30 and back.shapes[0].ry == 20
    round_one = Document()
    round_one.add_shape(Circle(rx=5, ry=5, layer="Cut"))
    assert Document.from_dict(round_one.to_dict()).shapes[0].kind == "circle"

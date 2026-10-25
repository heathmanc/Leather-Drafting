"""Modify commands rebuild shapes -- they must keep curves, rounding and the
shape's identity (group, guide flag, fold, stitching, attached holes)."""

import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.offset import signed_area  # noqa: E402
from leathercad.shapes import (Circle, EditablePath, Edge, PathShape,  # noqa: E402
                               Polygon, Rectangle, Transform)
from leathercad.stitching import Hole, holes_for_shape  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _win(doc):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    win.canvas.rebuild()
    return win


def _select(c, models):
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if c._item_model(it) in models:
            it.setSelected(True)


def _curve_path():
    # a bezier hump then a straight run
    return EditablePath(
        nodes=[Vec2(0, 0), Vec2(40, 0), Vec2(80, 0)],
        edges=[Edge("bezier", c1=Vec2(10, 30), c2=Vec2(30, 30)), Edge("line")],
        closed=False, layer="Cut")


def _sample(sh, n=400):
    pts = sh.world_polyline()[0]
    return pts


def _max_dev(pts, ref):
    """Largest distance from any of ``pts`` to the polyline ``ref``."""
    def d(p):
        best = float("inf")
        for i in range(len(ref) - 1):
            a, b = ref[i], ref[i + 1]
            ab = b - a
            t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_sq(), 1e-12)))
            best = min(best, (a + ab * t - p).length())
        return best
    return max(d(p) for p in pts)


def test_break_apart_and_join_keep_a_bezier_a_curve(qapp):
    doc = Document()
    ep = _curve_path()
    doc.add_shape(ep)
    ref = _sample(ep)
    win = _win(doc)
    c = win.canvas
    _select(c, [ep])
    c.break_apart_selected()
    parts = [s for s in win.doc.shapes]
    assert sorted(s.kind for s in parts) == ["editpath", "path"]
    bez = [s for s in parts if s.kind == "editpath"][0]
    assert bez.edges[0].kind == "bezier"
    assert _max_dev(_sample(bez), ref) < 0.03          # it was moved ~25 mm
    _select(c, parts)
    c.join_selected()
    joined = win.doc.shapes[0]
    assert len(win.doc.shapes) == 1 and "bezier" in [e.kind for e in joined.edges]
    assert _max_dev(_sample(joined), ref) < 0.03


def test_trimming_a_straight_edge_leaves_the_bezier_alone(qapp):
    doc = Document()
    ep = _curve_path()
    doc.add_shape(ep)
    doc.add_shape(PathShape(points=[Vec2(60, -5), Vec2(60, 5)], layer="Cut"))
    win = _win(doc)
    win.canvas._do_trim(Vec2(70, 0))
    out = [s for s in win.doc.shapes if s.kind == "editpath"][0]
    e = out.edges[0]
    t = out.transform
    assert e.kind == "bezier"
    assert (t.apply(e.c1), t.apply(e.c2)) == (Vec2(10, 30), Vec2(30, 30))


def test_rounded_polygon_keeps_its_rounding(qapp):
    poly = Polygon(points=[Vec2(-30, -20), Vec2(30, -20), Vec2(30, 20),
                           Vec2(-30, 20)], corner_radius=8.0, layer="Cut")
    area0 = abs(signed_area(poly.world_polyline()[0][:-1]))
    # fillet one corner: the other three stay rounded
    from leathercad_app import canvas as cm
    doc = Document()
    doc.add_shape(poly)
    win = _win(doc)
    win._set_tool(cm.FILLET)
    win.fillet_spin.setValue(4.0)
    win.canvas._do_fillet(Vec2(30, 20))                 # click a corner
    out = win.doc.shapes[0]
    arcs = [e for e in out.edges if e.kind == "arc"]
    assert len(arcs) == 4                     # the others stay rounded
    # three R8 corners and one R4: area = rect - 3(4-pi)8^2/4... exactly
    want = 60 * 40 - (4 - math.pi) * (3 * 64 + 16) / 4
    # (flattened arcs shave ~0.15 mm² each off the true area)
    assert abs(abs(signed_area(out.world_polyline()[0][:-1])) - want) < 1.0
    assert abs(area0 - (60 * 40 - (4 - math.pi) * 64)) < 1.0
    # break apart: arcs, not sharp corners
    doc2 = Document()
    p2 = Polygon(points=list(poly.points), corner_radius=8.0, layer="Cut")
    doc2.add_shape(p2)
    win2 = _win(doc2)
    _select(win2.canvas, [p2])
    win2.canvas.break_apart_selected()
    assert sum(s.kind == "editpath" for s in win2.doc.shapes) == 4


def test_modify_commands_keep_group_guide_and_fold(qapp):
    doc = Document()
    guide = PathShape(points=[Vec2(-50, 0), Vec2(50, 0)], layer="Cut",
                      construction=True, group_id="group77",
                      fold_dir="valley", name="centre")
    doc.add_shape(guide)
    doc.add_shape(PathShape(points=[Vec2(0, -10), Vec2(0, 10)], layer="Cut"))
    win = _win(doc)
    win.canvas._do_trim(Vec2(30, 0))
    kept = [s for s in win.doc.shapes if s.name == "centre"]
    assert len(kept) == 1
    k = kept[0]
    assert (k.construction, k.group_id, k.fold_dir) == (True, "group77", "valley")


def test_convert_to_nodes_keeps_identity(qapp):
    doc = Document()
    r = Rectangle(width=40, height=20, corner_radius=4, layer="Cut",
                  name="flap", group_id="group5", fold_dir="mountain")
    doc.add_shape(r)
    win = _win(doc)
    _select(win.canvas, [r])
    win.canvas.convert_to_nodes()
    s = win.doc.shapes[0]
    assert s.kind == "editpath"
    assert (s.name, s.group_id, s.fold_dir) == ("flap", "group5", "mountain")


def _with_holes(sh, pts):
    sh.baked_holes = [Hole(sh.transform.inverse_apply(p), Vec2(1, 0))
                      for p in pts]
    sh.stitch = StitchSettings(enabled=False)
    return sh


def _all_hole_points(doc):
    out = []
    for s in doc.shapes:
        out += [(round(h.point.x, 6), round(h.point.y, 6))
                for h in holes_for_shape(s).holes]
    return sorted(out)


def test_attached_holes_survive_trim_union_and_join(qapp):
    pts = [Vec2(-15, 0), Vec2(0, 5), Vec2(15, -5)]
    want = sorted((round(p.x, 6), round(p.y, 6)) for p in pts)
    # union
    doc = Document()
    a = _with_holes(Rectangle(width=40, height=20, layer="Cut"), pts)
    b = Rectangle(width=20, height=20, layer="Cut", transform=Transform(x=25))
    doc.add_shape(a)
    doc.add_shape(b)
    win = _win(doc)
    _select(win.canvas, [a, b])
    win.canvas.boolean_selected("union")
    assert _all_hole_points(win.doc) == want
    # trim
    doc = Document()
    a = _with_holes(Rectangle(width=40, height=20, layer="Cut"), pts)
    doc.add_shape(a)
    doc.add_shape(PathShape(points=[Vec2(10, -20), Vec2(10, 20)], layer="Cut"))
    win = _win(doc)
    win.canvas._do_trim(Vec2(20, 0))
    assert _all_hole_points(win.doc) == want
    # join two stitched lines: stitching and attached holes kept
    doc = Document()
    l1 = PathShape(points=[Vec2(-20, 0), Vec2(0, 0)], layer="Cut",
                   stitch=StitchSettings(pitch_mm=4.0))
    l2 = PathShape(points=[Vec2(0, 0), Vec2(20, 0)], layer="Cut",
                   stitch=StitchSettings(pitch_mm=4.0))
    doc.add_shape(l1)
    doc.add_shape(l2)
    win = _win(doc)
    _select(win.canvas, [l1, l2])
    win.canvas.join_selected()
    joined = win.doc.shapes[0]
    assert joined.stitch is not None and holes_for_shape(joined).count > 5


def test_fillet_across_never_welds_different_layers(qapp):
    from leathercad_app import canvas as cm
    doc = Document()
    cut = PathShape(points=[Vec2(-30, 0), Vec2(0, 0)], layer="Cut")
    score = PathShape(points=[Vec2(0, 0), Vec2(0, 30)], layer="Score")
    doc.add_shape(cut)
    doc.add_shape(score)
    win = _win(doc)
    win._set_tool(cm.FILLET)
    win.fillet_spin.setValue(5.0)
    win.canvas._do_fillet(Vec2(0, 0))
    layers = sorted(s.layer for s in win.doc.shapes)
    assert layers == ["Cut", "Score"]


def test_circles_to_holes_keep_their_group(qapp):
    doc = Document()
    ci = Circle(rx=1, ry=1, layer="Cut", group_id="group3")
    doc.add_shape(ci)
    win = _win(doc)
    _select(win.canvas, [ci])
    win.canvas.convert_circles_to_holes()
    assert [h.group_id for h in win.doc.holes] == ["group3"]

"""Node tools, radius dimensions, copy along path, rotated-group grips and
export options / exact arcs."""

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
                               Rectangle, Transform, arc_through)
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


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


def _area(sh):
    p = sh.world_polyline()[0]
    return abs(signed_area(p[:-1] if (p[0] - p[-1]).length() < 1e-9 else p))


# -- node tools ---------------------------------------------------------------
def test_edge_conversions_round_trip():
    from leathercad.modify import convert_edge, _edge_eval
    q = EditablePath(nodes=[Vec2(10, 0), Vec2(0, 10)],
                     edges=[Edge("arc", mid=Vec2(10 / math.sqrt(2),
                                                 10 / math.sqrt(2)))],
                     closed=False)
    assert convert_edge(q, 0, "bezier")
    _k, pt = _edge_eval(q.nodes[0], q.nodes[1], q.edges[0])
    assert max(abs(pt(t / 20).length() - 10) for t in range(21)) < 0.01
    assert convert_edge(q, 0, "arc")
    _c, r, *_ = arc_through(q.nodes[0], q.edges[0].mid, q.nodes[1])
    assert abs(r - 10) < 1e-6
    assert convert_edge(q, 0, "line") and q.edges[0].kind == "line"
    assert not convert_edge(q, 0, "line")            # already one
    assert convert_edge(q, 0, "arc") and q.edges[0].mid is not None


def test_removing_a_node_keeps_the_curve():
    from leathercad.modify import remove_node, _edge_eval
    k = 7.0710678
    circle = EditablePath(
        nodes=[Vec2(10, 0), Vec2(0, 10), Vec2(-10, 0), Vec2(0, -10)],
        edges=[Edge("arc", mid=Vec2(k, k)), Edge("arc", mid=Vec2(-k, k)),
               Edge("arc", mid=Vec2(-k, -k)), Edge("arc", mid=Vec2(k, -k))],
        closed=True)
    assert remove_node(circle, 1)
    assert [e.kind for e in circle.edges] == ["arc", "arc", "arc"]
    _c, r, *_ = arc_through(circle.nodes[0], circle.edges[0].mid,
                            circle.nodes[1])
    assert abs(r - 10) < 1e-3                        # still the same circle
    mix = EditablePath(nodes=[Vec2(0, 0), Vec2(20, 0), Vec2(30, 10)],
                       edges=[Edge("line"), Edge("arc", mid=Vec2(27.0711, 2.9289))],
                       closed=False)
    _k, pl = _edge_eval(Vec2(0, 0), Vec2(20, 0), Edge("line"))
    _k, pa = _edge_eval(Vec2(20, 0), Vec2(30, 10), mix.edges[1])
    ref = [pl(i / 20) for i in range(21)] + [pa(i / 20) for i in range(21)]
    assert remove_node(mix, 1)
    assert [e.kind for e in mix.edges] == ["bezier"]   # not straightened
    _k, pb = _edge_eval(mix.nodes[0], mix.nodes[1], mix.edges[0])
    cur = [pb(i / 200) for i in range(201)]
    assert max(min((q - r).length() for q in cur) for r in ref) < 0.8


def test_break_path_at_node(qapp):
    from leathercad.modify import break_path_at
    sq = EditablePath(nodes=[Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)],
                      edges=[Edge("line")] * 4, closed=True, layer="Cut",
                      group_id="g9")
    (opened,) = break_path_at(sq, 2)
    assert not opened.closed and len(opened.nodes) == 5
    assert opened.nodes[0] == Vec2(10, 10) and opened.nodes[-1] == Vec2(10, 10)
    assert opened.group_id == "g9"
    a, b = break_path_at(opened, 2)
    assert len(a.nodes) == 3 and len(b.nodes) == 3
    assert break_path_at(opened, 0) == []
    # through the canvas: the shape is replaced
    win, c = _win()
    item = c.add_shape(sq)
    c.enter_vertex_edit(item)
    c.break_path_at_node(item, 1)
    assert len(win.doc.shapes) == 1 and not win.doc.shapes[0].closed
    win.undo()
    assert win.doc.shapes[0].closed


# -- radius dimensions -----------------------------------------------------
def test_radius_dimension_from_an_arc_or_circle(qapp):
    win, c = _win()
    circle = c.add_shape(Circle(rx=10, ry=10, layer="Cut",
                                transform=Transform(x=50)))
    hit = c._arc_under(circle.model, Vec2(60.3, 0.0))
    assert hit is not None and hit[0] == Vec2(50, 0)
    c.add_radius_dimension(*hit)
    dim = win.doc.dimensions[-1]
    assert dim.measure == "radius" and abs(dim.length() - 10) < 1e-9
    assert dim.label() == "R 10.0 mm" and dim.label("in") == "R 0.394 in"
    rect = c.add_shape(Rectangle(width=60, height=40, corner_radius=8,
                                 layer="Cut"))
    corner = Vec2(30 - 8 + 8 * math.cos(math.radians(45)),
                  20 - 8 + 8 * math.sin(math.radians(45)))
    hit = c._arc_under(rect.model, corner)
    assert hit is not None and abs((hit[1] - hit[0]).length() - 8) < 1e-9
    assert c._arc_under(rect.model, Vec2(0, 20)) is None     # a straight edge
    back = Document.from_dict(win.doc.to_dict())
    assert back.dimensions[0].measure == "radius"


# -- copy along path --------------------------------------------------------
def test_copy_along_a_path_follows_it(qapp):
    win, c = _win()
    rail = c.add_shape(PathShape(points=[Vec2(0, 0), Vec2(100, 0), Vec2(100, 100)],
                                 layer="Cut", construction=True))
    piece = c.add_shape(Rectangle(width=10, height=4, layer="Cut",
                                  transform=Transform(x=-50, y=-50)))
    c.scene_obj.clearSelection()
    rail.setSelected(True)
    piece.setSelected(True)
    assert c.array_along_path(5, 0.0, True)
    copies = [s for s in win.doc.shapes
              if s.kind == "rectangle" and s is not piece.model]
    assert len(copies) == 5
    xs = sorted((round(s.transform.x, 6), round(s.transform.y, 6)) for s in copies)
    assert xs[0] == (0.0, 0.0) and xs[-1] == (100.0, 100.0)
    assert (50.0, 0.0) in xs and (100.0, 50.0) in xs
    rots = sorted(round(s.transform.rotation, 6) for s in copies)
    assert rots[0] == 0.0 and rots[-1] == 90.0           # turned up the leg
    assert piece.model.transform.x == -50                 # original stays
    assert not c.array_along_path(3)                      # nothing selected now?


# -- rotated groups keep their grips ----------------------------------------
def test_rotated_group_has_grips_and_stretches_along_itself(qapp):
    from leathercad_app.items import ResizeHandle
    doc = Document()
    doc.add_shape(Rectangle(width=100, height=40, layer="Cut", group_id="g1",
                            transform=Transform(rotation=30.0)))
    doc.add_shape(Circle(rx=4, ry=4, layer="Cut", group_id="g1",
                         transform=Transform(x=40 * math.cos(math.radians(30)),
                                             y=40 * math.sin(math.radians(30)),
                                             rotation=30.0)))
    win, c = _win(doc)
    rect = _item_of(c, doc.shapes[0])
    c.press_select(rect)
    qapp.processEvents()
    grips = [h for h in c._resize_handles if isinstance(h, ResizeHandle)]
    assert len(grips) == 8                                # there are grips now
    right = [h for h in grips if h.grip == (1, 0)][0]
    c._begin_group_resize(rect)                       # what the press does
    gw = right._grip_world()
    # pull the right-hand grip 20 mm further along the rectangle's own axis
    u = Vec2(math.cos(math.radians(30)), math.sin(math.radians(30)))
    right._apply_resize(Vec2(gw.x + 20 * u.x, gw.y + 20 * u.y))
    c._end_group_resize()
    r = doc.shapes[0]
    assert abs(r.width - 120) < 1e-6 and abs(r.height - 40) < 1e-6
    ci = doc.shapes[1]
    # the circle sat 40 mm along the axis from the centre, in a 50 half-width:
    # scaled by 1.2 about the pinned left edge
    c_now = Vec2(ci.transform.x, ci.transform.y)
    r_centre = Vec2(r.transform.x, r.transform.y)
    along = (c_now - r_centre).dot(u)
    assert abs(along - 40 * 1.2) < 1e-6


# -- export: exact arcs and filters ---------------------------------------
def test_export_writes_true_arcs_and_honours_filters(tmp_path):
    from leathercad.export import export_dxf, export_svg
    from leathercad.importers import import_dxf, import_svg
    d = Document()
    r = Rectangle(width=60, height=40, corner_radius=8, layer="Cut",
                  transform=Transform(rotation=20, mirror_x=True),
                  stitch=StitchSettings(pitch_mm=4.0, inset=3.0))
    d.add_shape(r)
    d.add_shape(Circle(rx=5, ry=5, layer="Cut", transform=Transform(x=80)))
    want = _area(r)
    for name, exp, imp in (("a.svg", export_svg, import_svg),
                           ("a.dxf", export_dxf, import_dxf)):
        p = tmp_path / name
        exp(d, str(p))
        shapes = imp(str(p))
        assert abs(max(_area(s) for s in shapes) - want) < 1.0
    svg = (tmp_path / "a.svg").read_text()
    assert " A " in svg and "<circle" in svg          # arcs + holes
    dxf = (tmp_path / "a.dxf").read_text()
    assert dxf.count("\n42\n") >= 4                      # bulge vertices
    # filters: only the rectangle, outlines only
    p = tmp_path / "f.svg"
    export_svg(d, str(p), selection={r.shape_id}, include={"outlines"})
    svg = p.read_text()
    assert svg.count("<path") == 1 and "<circle" not in svg
    export_svg(d, str(p), include={"holes"})
    svg = p.read_text()
    assert "<path" not in svg and "<circle" in svg
    # kerf on: compensated polylines, still a closed loop
    d.kerf = 0.2
    export_dxf(d, str(tmp_path / "k.dxf"))
    assert "\n42\n" not in (tmp_path / "k.dxf").read_text()

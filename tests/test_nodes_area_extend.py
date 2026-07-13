"""Node add/delete in node-edit mode, area/leather readout, and Extend."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Rectangle, Polygon, PathShape, EditablePath,  # noqa: E402
                               Transform)
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _win(doc):
    from leathercad_app.mainwindow import MainWindow
    w = MainWindow(doc)
    w.canvas.rebuild()
    w.canvas.resize(600, 450)
    return w


def _item(c, model):
    from leathercad_app.items import ShapeItem
    return [i for i in c.scene_obj.items()
            if isinstance(i, ShapeItem) and i.model is model][0]


# -- add nodes ---------------------------------------------------------------
def test_insert_node_on_polygon_edge(qapp):
    doc = Document()
    sq = Polygon(points=[Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20),
                         Vec2(-20, 20)], close_path=True,
                 transform=Transform(x=0, y=0), layer="Cut")
    doc.add_shape(sq)
    win = _win(doc)
    c = win.canvas
    it = _item(c, sq)
    it.setSelected(True)
    c.enter_vertex_edit(it)
    assert c.insert_node_at(Vec2(0, -20))            # mid bottom edge
    assert len(sq.points) == 5
    assert any(abs(p.x) < 1e-6 and abs(p.y + 20) < 1e-6 for p in sq.points)
    assert len(c._handles) == 5                      # handles rebuilt
    assert not c.insert_node_at(Vec2(200, 200))      # nowhere near an edge


def test_insert_node_on_editablepath_line_only(qapp):
    doc = Document()
    ep = EditablePath.from_bezier([Vec2(0, 0), Vec2(40, 0)],
                                  [Vec2(10, 10), Vec2(10, -10)], closed=False)
    ep.nodes.append(Vec2(80, 0))                     # add a straight tail
    from leathercad.shapes import Edge
    ep.edges.append(Edge("line"))
    ep.transform = Transform(x=0, y=0)
    ep.layer = "Cut"
    doc.add_shape(ep)
    win = _win(doc)
    c = win.canvas
    it = _item(c, ep)
    it.setSelected(True)
    c.enter_vertex_edit(it)
    assert c.insert_node_at(Vec2(60, 0))             # on the straight edge
    assert len(ep.nodes) == 4
    assert [e.kind for e in ep.edges] == ["bezier", "line", "line"]
    assert not c.insert_node_at(Vec2(20, 8))         # on the bezier: refused


# -- delete nodes --------------------------------------------------------------
def test_delete_polygon_node_and_minimum(qapp):
    doc = Document()
    sq = Polygon(points=[Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20),
                         Vec2(-20, 20)], close_path=True,
                 transform=Transform(x=0, y=0), layer="Cut")
    doc.add_shape(sq)
    win = _win(doc)
    c = win.canvas
    it = _item(c, sq)
    it.setSelected(True)
    c.enter_vertex_edit(it)
    h = min(c._handles, key=lambda hh: (hh.pos().x() - 20) ** 2
            + (hh.pos().y() + 20) ** 2)              # the (20,-20) corner
    assert c.delete_node(h)
    assert len(sq.points) == 3
    # at 3 points, a closed polygon refuses to shrink further
    assert not c.delete_node(c._handles[0])
    assert len(sq.points) == 3


def test_delete_arc_midpoint_straightens(qapp):
    from leathercad.shapes import Edge
    doc = Document()
    ep = EditablePath(nodes=[Vec2(0, 0), Vec2(40, 0)],
                      edges=[Edge("arc", Vec2(20, 10))], closed=False,
                      transform=Transform(x=0, y=0), layer="Cut")
    doc.add_shape(ep)
    win = _win(doc)
    c = win.canvas
    it = _item(c, ep)
    it.setSelected(True)
    c.enter_vertex_edit(it)
    mid = [h for h in c._handles if h.node.is_mid][0]
    assert c.delete_node(mid)
    assert ep.edges[0].kind == "line" and ep.edges[0].mid is None


# -- area readout ----------------------------------------------------------
def test_area_report_math_and_scope(qapp):
    doc = Document()
    doc.add_shape(Rectangle(name="Body", width=100, height=100,
                            transform=Transform(x=0, y=0), layer="Cut"))
    doc.add_shape(Rectangle(name="Fold", width=50, height=30,
                            transform=Transform(x=200, y=0), layer="Score"))
    doc.add_shape(PathShape(points=[Vec2(0, 0), Vec2(30, 0)], close_path=False,
                            transform=Transform(x=0, y=100), layer="Cut"))
    win = _win(doc)
    rep = win.canvas.area_report(75.0)
    assert "Body" in rep and "100.0 cm²" in rep      # 100x100 mm = 100 cm²
    assert "Fold" not in rep                         # score layer: not leather
    assert "0.11 sq ft" in rep                       # 100 cm² = 0.1076 sq ft
    assert "0.14 sq ft" in rep                       # buy at 75% usable
    # per-shape area also shows in the Properties readout
    from leathercad_app.items import ShapeItem
    it = [i for i in win.canvas.scene_obj.items()
          if isinstance(i, ShapeItem) and i.model.name == "Body"][0]
    it.setSelected(True)
    win.properties.show_selection([it])
    assert "100.0 cm²" in win.properties.readout.text()


# -- extend ------------------------------------------------------------------
def test_extend_line_to_outline(qapp):
    doc = Document()
    wall = Rectangle(width=20, height=60, transform=Transform(x=60, y=0),
                     layer="Cut")                    # left edge at x = 50
    doc.add_shape(wall)
    line = PathShape(points=[Vec2(-15, 0), Vec2(15, 0)], close_path=False,
                     transform=Transform(x=15, y=0), layer="Cut")  # (0,0)-(30,0)
    doc.add_shape(line)
    win = _win(doc)
    c = win.canvas
    c._do_extend(Vec2(30, 0))                        # click the reachable end
    end = line.transform.apply(line.points[1])
    assert abs(end.x - 50.0) < 1e-6                  # grown to the wall
    assert abs(end.y - 0.0) < 1e-6
    start = line.transform.apply(line.points[0])
    assert abs(start.x - 0.0) < 1e-6                 # other end untouched

    # an end with nothing ahead of it: unchanged
    before = line.transform.apply(line.points[0])
    c._do_extend(Vec2(before.x, before.y))
    after = line.transform.apply(line.points[0])
    assert abs(after.x - before.x) < 1e-6

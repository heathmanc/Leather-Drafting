"""Interactive fillet / chamfer: engine math and the canvas click tool."""

import math
import os

import pytest

from leathercad.geometry import Vec2
from leathercad.modify import fillet_vertex, chamfer_vertex, to_editable
from leathercad.shapes import EditablePath, Polygon, PathShape, Transform


def _square():
    sq = Polygon(points=[Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20),
                         Vec2(-20, 20)], close_path=True)
    return to_editable(sq)


def test_fillet_replaces_corner_with_arc():
    ep = _square()
    assert fillet_vertex(ep, 1, 5.0)                     # corner (20,-20)
    assert [e.kind for e in ep.edges] == ["line", "arc", "line", "line", "line"]
    assert len(ep.nodes) == 5
    pts = ep.local_path().flatten()
    # the outline still spans the full square (only the corner was cut)
    assert round(max(p.x for p in pts) - min(p.x for p in pts), 3) == 40.0
    # closest approach to the old sharp corner == r*(sqrt(2)-1) for a 90° fillet
    d = min(math.hypot(p.x - 20, p.y + 20) for p in pts)
    assert abs(d - 5 * (math.sqrt(2) - 1)) < 0.1


def test_chamfer_cuts_straight():
    ep = _square()
    assert chamfer_vertex(ep, 2, 6.0)                    # corner (20,20)
    assert [e.kind for e in ep.edges] == ["line", "line", "line", "line", "line"]
    pts = ep.local_path().flatten()
    assert min(math.hypot(p.x - 20, p.y - 20) for p in pts) >= 4.2   # 6/sqrt2


def test_radius_clamps_to_half_edge():
    ep = _square()                                       # edges are 40 long
    assert fillet_vertex(ep, 0, 100.0)                   # absurd radius
    pts = ep.local_path().flatten()
    xs = [p.x for p in pts]
    assert min(xs) >= -20 - 1e-6                         # never explodes


def test_open_path_endpoints_refuse():
    path = PathShape(points=[Vec2(0, 0), Vec2(20, 0), Vec2(20, 20)],
                     close_path=False)
    ep = to_editable(path)
    assert not fillet_vertex(ep, 0, 3.0)                 # endpoint: no
    assert fillet_vertex(ep, 1, 3.0)                     # interior corner: yes


def test_arc_flanked_corner_refuses():
    ep = _square()
    assert fillet_vertex(ep, 1, 5.0)
    # node 2 (the fillet's own tangent point) borders the new arc -> refuse
    assert not fillet_vertex(ep, 2, 5.0)


def test_canvas_fillet_click():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(Polygon(points=[Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20),
                                  Vec2(-20, 20)], close_path=True,
                          transform=Transform(x=0, y=0), layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.fillet_radius = 5.0                                # skip the dialog
    c._do_fillet(Vec2(20, -20))                          # click the corner
    sh = win.doc.shapes[0]
    assert isinstance(sh, EditablePath)                  # auto-converted
    assert "arc" in [e.kind for e in sh.edges]
    assert win._unsaved_changes
    # chamfer another corner via the same click path
    c._do_fillet(Vec2(-20, -20), chamfer=True)
    assert len(sh.nodes) == 6

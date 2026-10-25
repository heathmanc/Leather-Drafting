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


def test_fillet_options_box_drives_radius_and_mode():
    """The persistent toolbar box sets the radius/mode (no popup, no modifier
    keys); the options show only while the fillet tool is active."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    import leathercad_app.canvas as cm
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(Polygon(points=[Vec2(-30, -20), Vec2(30, -20), Vec2(30, 20),
                                  Vec2(-30, 20)], close_path=True,
                          transform=Transform(x=0, y=0), layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    # options hidden until the fillet tool is picked
    assert not win._fillet_spin_act.isVisible()
    win._set_tool(cm.FILLET)
    assert win._fillet_spin_act.isVisible()
    assert c.fillet_radius == win.fillet_spin.value()   # box drives the canvas

    win.fillet_spin.setValue(8.0)
    assert c.fillet_radius == 8.0
    c._do_fillet(Vec2(30, 20))                           # no popup; uses 8 mm
    sh = win.doc.shapes[0]
    assert sh.kind == "editpath" and "arc" in [e.kind for e in sh.edges]

    # re-selecting the tool keeps working (regression: dialog never re-fired)
    win._set_tool(cm.SELECT)
    assert not win._fillet_spin_act.isVisible()
    win._set_tool(cm.FILLET)
    c._do_fillet(Vec2(-30, 20))
    assert sum(1 for e in win.doc.shapes[0].edges if e.kind == "arc") == 2

    # Chamfer is a selector, not Shift
    win.fillet_mode.setCurrentIndex(1)
    assert c.fillet_chamfer is True


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
    # chamfer another corner: mode is a persistent flag, not a click modifier
    c.fillet_chamfer = True
    c._do_fillet(Vec2(-20, -20))
    assert len(sh.nodes) == 6


def _two_lines_doc():
    """Two separate Line-tool lines meeting at (20,0) at 90 degrees."""
    from leathercad.document import Document
    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(-10, 0), Vec2(10, 0)],
                            close_path=False, transform=Transform(x=10, y=0),
                            layer="Cut"))        # (0,0) -> (20,0)
    doc.add_shape(PathShape(points=[Vec2(0, -10), Vec2(0, 10)],
                            close_path=False, transform=Transform(x=20, y=10),
                            layer="Cut"))        # (20,0) -> (20,20)
    return doc


def test_fillet_across_two_separate_lines():
    """Draw two lines meeting at a corner, click the corner: they weld into
    one path with a true arc between (the reported bug)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    QApplication.instance() or QApplication([])

    win = MainWindow(_two_lines_doc())
    c = win.canvas
    c.rebuild()
    c.resize(500, 400)
    c.fillet_radius = 5.0
    c._do_fillet(Vec2(20, 0))                    # click the shared corner
    assert len(win.doc.shapes) == 1              # welded into one path
    sh = win.doc.shapes[0]
    assert isinstance(sh, EditablePath)
    assert [e.kind for e in sh.edges] == ["line", "arc", "line"]
    pts = [sh.transform.apply(p) for p in sh.local_path().flatten()]
    ends = {(round(pts[0].x, 2), round(pts[0].y, 2)),
            (round(pts[-1].x, 2), round(pts[-1].y, 2))}
    assert ends == {(0.0, 0.0), (20.0, 20.0)}    # free ends untouched
    d = min(math.hypot(p.x - 20, p.y) for p in pts)
    assert abs(d - 5 * (math.sqrt(2) - 1)) < 0.1


def test_chamfer_across_two_lines_and_near_miss():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    QApplication.instance() or QApplication([])

    # ends 0.4 mm apart (imperfect snap) still weld; Shift-click chamfers
    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(-10, 0), Vec2(10, 0)],
                            close_path=False, transform=Transform(x=10, y=0),
                            layer="Cut"))
    doc.add_shape(PathShape(points=[Vec2(0, -10), Vec2(0, 10)],
                            close_path=False,
                            transform=Transform(x=20.4, y=10.3), layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(500, 400)
    c.fillet_radius = 4.0
    c.fillet_chamfer = True
    c._do_fillet(Vec2(20.2, 0.15))
    assert len(win.doc.shapes) == 1
    assert [e.kind for e in win.doc.shapes[0].edges] == ["line", "line", "line"]


def test_lone_end_gives_hint_not_weld():
    """A single line's end with nothing meeting it: no weld, no crash."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(-10, 0), Vec2(10, 0)],
                            close_path=False, transform=Transform(x=10, y=0),
                            layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    c.resize(500, 400)
    c.fillet_radius = 5.0
    c._do_fillet(Vec2(20, 0))                    # a loose end
    assert len(win.doc.shapes) == 1              # unchanged
    assert isinstance(win.doc.shapes[0], PathShape)


# ---------------------------------------------------------------------------
# Blend fillets: the side of a corner can be a curve -- a flattened ellipse
# (dozens of short lines, as Trim/Boolean leave it), an arc or a bezier. The
# fillet used to need a straight edge each side and stopped at half of the
# NEIGHBOURING EDGE, so on an ellipse notch a 10 mm request gave ~1.2 mm.
# ---------------------------------------------------------------------------
def _notch_path():
    """A slot line with a half-ellipse scoop made of short straight pieces."""
    from leathercad.shapes import Ellipse
    ell = Ellipse(rx=45, ry=26).local_path().flatten()
    scoop = sorted({(round(p.x, 9), round(p.y, 9)) for p in ell if p.y <= 1e-9})
    pts = [Vec2(-110, 0)] + [Vec2(x, y) for x, y in scoop] + [Vec2(110, 0)]
    return to_editable(PathShape(points=pts, close_path=False))


def _arc_radii(ep):
    from leathercad.shapes import arc_through
    out = []
    for k, e in enumerate(ep.edges):
        if e.kind == "arc":
            c, r, *_ = arc_through(ep.nodes[k], e.mid, ep.nodes[k + 1])
            out.append((k, c, r))
    return out


def _turn_deg(u, v):
    return math.degrees(abs(math.atan2(u.cross(v), u.dot(v))))


def test_fillet_blends_into_a_flattened_ellipse():
    ep = _notch_path()
    for x in (-45, 45):
        i = min(range(len(ep.nodes)), key=lambda k: (ep.nodes[k] - Vec2(x, 0)).length())
        assert fillet_vertex(ep, i, 10.0) == 10.0
    arcs = _arc_radii(ep)
    assert len(arcs) == 2 and all(abs(r - 10.0) < 1e-6 for _k, _c, r in arcs)
    for k, c, r in arcs:
        a, b = ep.nodes[k], ep.nodes[k + 1]
        # tangent to the straight it leaves (exactly) ...
        for p, q in ((a, ep.nodes[k - 1]), (b, ep.nodes[k + 2])):
            radial = p - c
            if abs(q.y) < 1e-9 and abs(p.y) < 1e-9:   # the straight side
                assert abs(radial.dot(q - p)) < 1e-6 * (q - p).length()
    # the free ends are untouched
    assert (ep.nodes[0].x, ep.nodes[-1].x) == (-110, 110)


def test_too_big_a_radius_uses_the_largest_that_fits():
    ep = _notch_path()
    i = min(range(len(ep.nodes)), key=lambda k: (ep.nodes[k] - Vec2(45, 0)).length())
    # up to ~175 mm fits: the straight side runs to a free end at x=110 and
    # may be used in full; the scoop side up to its midpoint
    assert fillet_vertex(_notch_path(), i, 80.0) == 80.0
    used = fillet_vertex(ep, i, 2000.0)
    assert 150.0 < used < 2000.0                   # clamped, but a real blend
    assert max(p.x for p in ep.nodes) <= 110.0 + 1e-6   # never past the end
    (_k, _c, r), = _arc_radii(ep)
    assert abs(r - used) < 1e-6


@pytest.mark.parametrize("edge", ["arc", "bezier"])
def test_fillet_is_exactly_tangent_to_a_curved_side(edge):
    from leathercad.shapes import Edge
    from leathercad.modify import _edge_eval
    e = (Edge("arc", mid=Vec2(20, -20)) if edge == "arc"
         else Edge("bezier", c1=Vec2(10, -25), c2=Vec2(30, -30)))
    end = Vec2(40, 0) if edge == "arc" else Vec2(40, -30)
    ep = EditablePath(nodes=[Vec2(-40, 0), Vec2(0, 0), end],
                      edges=[Edge("line"), e], closed=False)
    assert fillet_vertex(ep, 1, 6.0) == 6.0
    (k, c, r), = [a for a in _arc_radii(ep) if abs(a[2] - 6.0) < 1e-6]
    _kind, nxt = _edge_eval(ep.nodes[k + 1], ep.nodes[k + 2], ep.edges[k + 1])
    t_curve = nxt(1e-7) - nxt(0.0)
    radial = ep.nodes[k + 1] - c
    t_arc = Vec2(-radial.y, radial.x)
    assert min(_turn_deg(t_arc, t_curve), _turn_deg(t_arc * -1, t_curve)) < 0.01
    assert ep.edges[k + 1].kind == edge            # the curve keeps its kind


def test_duplicate_point_at_the_corner_still_fillets():
    path = PathShape(points=[Vec2(-30, 0), Vec2(0, 0), Vec2(0, 0), Vec2(0, 30)],
                     close_path=False)
    ep = to_editable(path)
    assert fillet_vertex(ep, 1, 5.0) == 5.0


def test_canvas_fillet_blends_an_ellipse_notch_and_reports_clamps():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    QApplication.instance() or QApplication([])

    ep = _notch_path()
    doc = Document()
    doc.add_shape(PathShape(points=list(ep.nodes), close_path=False,
                            transform=Transform(x=0, y=0), layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    msgs = []
    c.statusMessage.connect(msgs.append)
    c.fillet_radius = 10.0
    for x in (-45, 45):
        c._do_fillet(Vec2(x, 0))                  # click each notch corner
    sh = win.doc.shapes[0]
    radii = [r for _k, _c, r in _arc_radii(sh)]
    assert len(radii) == 2 and all(abs(r - 10.0) < 1e-6 for r in radii)
    assert msgs[-1].startswith("Filleted at 10 mm")

    win2 = MainWindow(Document())
    doc2 = win2.doc
    doc2.add_shape(PathShape(points=list(_notch_path().nodes), close_path=False,
                             transform=Transform(x=0, y=0), layer="Cut"))
    c2 = win2.canvas
    c2.rebuild()
    msgs2 = []
    c2.statusMessage.connect(msgs2.append)
    c2.fillet_radius = 2000.0
    c2._do_fillet(Vec2(45, 0))
    assert "the largest that fits" in msgs2[-1]

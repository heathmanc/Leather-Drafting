"""Bezier-curve support: the EditablePath bezier edge + from_bezier builder."""

from leathercad.geometry import Vec2
from leathercad.shapes import EditablePath, Edge, Transform
from leathercad.stitchsettings import StitchSettings
from leathercad.stitching import holes_for_shape
from leathercad.document import Document


def test_from_bezier_builds_curved_edges():
    anchors = [Vec2(0, 0), Vec2(40, 0)]
    outs = [Vec2(10, 20), Vec2(10, -20)]        # both handles pull upward
    ep = EditablePath.from_bezier(anchors, outs, closed=False)
    assert len(ep.edges) == 1
    e = ep.edges[0]
    assert e.kind == "bezier" and e.c1 is not None and e.c2 is not None
    # mirror convention: c2 = end - out_handle(end)
    assert (e.c2.x, e.c2.y) == (30.0, 20.0)
    pts = ep.local_path().flatten()
    assert len(pts) > 8                          # flattened to a smooth polyline
    assert max(p.y for p in pts) > 10            # actually bulges (not a line)


def test_from_bezier_corner_makes_straight_edge():
    anchors = [Vec2(0, 0), Vec2(10, 0), Vec2(20, 0)]
    outs = [None, None, None]                    # no handles -> plain polyline
    ep = EditablePath.from_bezier(anchors, outs, closed=False)
    assert [e.kind for e in ep.edges] == ["line", "line"]


def test_bezier_editpath_roundtrips_through_document():
    anchors = [Vec2(-20, 0), Vec2(0, 20), Vec2(20, 0), Vec2(0, -20)]
    outs = [Vec2(0, 10), Vec2(10, 0), Vec2(0, -10), Vec2(-10, 0)]
    ep = EditablePath.from_bezier(anchors, outs, closed=True)
    ep.layer = "Cut"
    doc = Document()
    doc.add_shape(ep)
    doc2 = Document.from_dict(doc.to_dict())
    ep2 = doc2.shapes[0]
    assert isinstance(ep2, EditablePath)
    assert [e.kind for e in ep2.edges] == ["bezier"] * 4
    for a, b in zip(ep.edges, ep2.edges):
        assert (round(a.c1.x, 6), round(a.c1.y, 6)) == (round(b.c1.x, 6),
                                                        round(b.c1.y, 6))
    # same geometry after a save/load cycle
    assert len(ep2.local_path().flatten()) == len(ep.local_path().flatten())


def test_closed_bezier_gets_stitched():
    anchors = [Vec2(-20, 0), Vec2(0, 20), Vec2(20, 0), Vec2(0, -20)]
    outs = [Vec2(0, 10), Vec2(10, 0), Vec2(0, -10), Vec2(-10, 0)]
    ep = EditablePath.from_bezier(anchors, outs, closed=True)
    ep.layer = "Cut"
    ep.stitch = StitchSettings(pitch_mm=4.0, inset=2.0, enabled=True)
    res = holes_for_shape(ep)
    assert res.count > 6                         # holes marched round the curve

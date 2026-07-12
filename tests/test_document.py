"""Tests for shapes, the document model, registration and export."""

import os
import math

from leathercad import (
    Document, Rectangle, Circle, Polygon, Transform, StitchSettings,
    StitchLine, Vec2, Layer,
)
from leathercad.stitching import stitch_polyline
from leathercad import export


def _holes_relative_to_origin(shape):
    res = stitch_polyline(*shape.world_polyline(), shape.stitch)
    ox, oy = shape.transform.x, shape.transform.y
    return sorted((round(h.point.x - ox, 3), round(h.point.y - oy, 3))
                  for h in res.holes)


# ---------------------------------------------------------------------------
# The registration guarantee the user asked for.
# ---------------------------------------------------------------------------
def test_identical_pieces_get_identical_holes():
    """Two pieces with the same outline + settings, differing only by position,
    must produce byte-identical hole layouts in their own frame -- so when they
    are stacked to stitch, every hole lines up with its partner."""
    a = Rectangle(width=90, height=60, corner_radius=8,
                  transform=Transform(x=0, y=0),
                  stitch=StitchSettings(pitch_mm=3.85, inset=3.5))
    b = Rectangle(width=90, height=60, corner_radius=8,
                  transform=Transform(x=250, y=40),
                  stitch=StitchSettings(pitch_mm=3.85, inset=3.5))
    assert _holes_relative_to_origin(a) == _holes_relative_to_origin(b)


def test_mirrored_piece_lines_up():
    """Lasering from the back (mirror_x) must keep holes registered: the
    mirrored layout is the mirror image of the original, hole-for-hole."""
    st = StitchSettings(pitch_mm=4.0, inset=3.0)
    a = Rectangle(width=80, height=50, corner_radius=6,
                  transform=Transform(x=0, y=0), stitch=st)
    b = Rectangle(width=80, height=50, corner_radius=6,
                  transform=Transform(x=0, y=0, mirror_x=True),
                  stitch=StitchSettings(pitch_mm=4.0, inset=3.0))
    ha = _holes_relative_to_origin(a)
    hb = _holes_relative_to_origin(b)
    mirrored = sorted((round(-x, 3), round(y, 3)) for x, y in ha)
    assert mirrored == hb


def test_start_offset_keeps_registration():
    st1 = StitchSettings(pitch_mm=4.0, inset=3.0, start_offset=0.0)
    st2 = StitchSettings(pitch_mm=4.0, inset=3.0, start_offset=0.0)
    a = Rectangle(width=70, height=45, stitch=st1)
    b = Rectangle(width=70, height=45, stitch=st2)
    assert _holes_relative_to_origin(a) == _holes_relative_to_origin(b)


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------
def test_rectangle_bounds_after_transform():
    r = Rectangle(width=40, height=20, transform=Transform(x=100, y=50))
    minx, miny, maxx, maxy = r.bounds()
    assert abs(minx - 80) < 1e-6 and abs(maxx - 120) < 1e-6
    assert abs(miny - 40) < 1e-6 and abs(maxy - 60) < 1e-6


def test_circle_is_round():
    c = Circle(rx=15, ry=15, transform=Transform(x=0, y=0))
    pts, _, closed = c.world_polyline()
    assert closed
    for p in pts:
        assert abs(p.length() - 15) < 0.1


def test_sharp_rect_has_corner_holes():
    st = StitchSettings(pitch_mm=4.0, inset=3.0, fit="closed")
    r = Rectangle(width=40, height=25, corner_radius=0, stitch=st)
    res = stitch_polyline(*r.world_polyline(), r.stitch)
    # inset corners
    inset = 3.0
    corners = [Vec2(20 - inset, 12.5 - inset), Vec2(-20 + inset, 12.5 - inset),
               Vec2(20 - inset, -12.5 + inset), Vec2(-20 + inset, -12.5 + inset)]
    for c in corners:
        assert any((h.point - c).length() < 0.6 for h in res.holes), \
            f"expected a hole near inset corner {c}"


# ---------------------------------------------------------------------------
# Document save / load round-trip
# ---------------------------------------------------------------------------
def test_document_roundtrip(tmp_path):
    doc = Document("wallet")
    doc.add_shape(Rectangle(width=90, height=60, corner_radius=8,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=3.85, inset=3.5),
                            layer="Cut"))
    doc.add_shape(Circle(rx=5, ry=5, transform=Transform(x=45, y=30),
                         layer="Cut"))
    doc.add_stitch_line(StitchLine(points=[Vec2(0, 0), Vec2(50, 0), Vec2(50, 30)],
                                   settings=StitchSettings(pitch_mm=3.0)))
    p = tmp_path / "wallet.leathercad.json"
    doc.save(str(p))
    doc2 = Document.load(str(p))
    assert len(doc2.shapes) == 2
    assert len(doc2.stitch_lines) == 1
    assert isinstance(doc2.shapes[0], Rectangle)
    assert abs(doc2.shapes[0].width - 90) < 1e-9
    assert abs(doc2.shapes[0].stitch.pitch_mm - 3.85) < 1e-9


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def test_export_svg_and_dxf(tmp_path):
    doc = Document("t")
    doc.add_shape(Rectangle(width=60, height=40, corner_radius=5,
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    svg = tmp_path / "out.svg"
    dxf = tmp_path / "out.dxf"
    export.export_svg(doc, str(svg))
    export.export_dxf(doc, str(dxf))
    svg_txt = svg.read_text()
    assert "mm" in svg_txt and "<circle" in svg_txt
    dxf_txt = dxf.read_text()
    assert "SECTION" in dxf_txt and "CIRCLE" in dxf_txt and "EOF" in dxf_txt


def test_loose_holes_roundtrip(tmp_path):
    from leathercad import LooseHole
    doc = Document("t")
    doc.add_hole(LooseHole(point=Vec2(0, 0), tangent=Vec2(1, 0)))
    doc.add_hole(LooseHole(point=Vec2(5, 0), hole_style="slit", slit_angle=25.0))
    p = tmp_path / "holes.json"
    doc.save(str(p))
    doc2 = Document.load(str(p))
    assert len(doc2.holes) == 2
    assert abs(doc2.holes[1].point.x - 5.0) < 1e-9
    assert doc2.holes[1].hole_style == "slit"


def test_baked_holes_on_shape_roundtrip(tmp_path):
    from leathercad.stitching import Hole
    doc = Document("t")
    r = Rectangle(width=40, height=30, transform=Transform(x=10, y=5))
    r.baked_holes = [Hole(Vec2(-5, -5), Vec2(1, 0)), Hole(Vec2(5, 5), Vec2(1, 0))]
    doc.add_shape(r)
    p = tmp_path / "baked.json"
    doc.save(str(p))
    doc2 = Document.load(str(p))
    assert doc2.shapes[0].baked_holes is not None
    assert len(doc2.shapes[0].baked_holes) == 2
    assert abs(doc2.shapes[0].baked_holes[0].point.x - (-5)) < 1e-9


def test_flip_symmetric_distribution():
    """A shape without corner anchors (an ellipse) isn't flip-symmetric by
    default, but the symmetry option makes a flipped piece line up."""
    from leathercad.shapes import Ellipse, Transform
    from leathercad.stitching import holes_for_shape, flip_symmetry

    plain = Ellipse(rx=50, ry=30, transform=Transform(),
                    stitch=StitchSettings(pitch_mm=3.85, inset=3.0))
    pts = [h.point for h in holes_for_shape(plain).holes]
    assert not flip_symmetry(pts, "vertical")[0]

    for axis in ("vertical", "horizontal"):
        sym = Ellipse(rx=50, ry=30, transform=Transform(),
                      stitch=StitchSettings(pitch_mm=3.85, inset=3.0,
                                            symmetry=axis))
        p = [h.point for h in holes_for_shape(sym).holes]
        ok, off, unmatched = flip_symmetry(p, axis)
        assert ok and unmatched == 0


def test_rounded_corners_are_symmetric_by_default():
    """Anchoring each corner arc as its own span makes a rounded rectangle's
    holes symmetric about both centre axes automatically -- the corner holes no
    longer land off the arc apex."""
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitching import holes_for_shape, flip_symmetry
    r = Rectangle(width=100, height=64, corner_radius=14, transform=Transform(),
                  stitch=StitchSettings(pitch_mm=3.85, inset=3.5))
    pts = [h.point for h in holes_for_shape(r).holes]
    assert flip_symmetry(pts, "vertical")[0]
    assert flip_symmetry(pts, "horizontal")[0]


def test_corner_style_midpoint_vs_straddle():
    """corner_style forces a hole ON the arc apex (midpoint) or an even pair
    straddling it (straddle); both stay symmetric about the apex."""
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitching import holes_for_shape

    def nearest_to_apex(style):
        r = Rectangle(width=100, height=64, corner_radius=14,
                      transform=Transform(),
                      stitch=StitchSettings(pitch_mm=3.85, inset=3.5,
                                            corner_style=style))
        pts = [h.point for h in holes_for_shape(r).holes]
        ri = 14 - 3.5                            # inset corner radius
        ax = (50 - 14) + ri / math.sqrt(2)       # bottom-right arc apex
        ay = -((32 - 14) + ri / math.sqrt(2))
        return min(math.hypot(p.x - ax, p.y - ay) for p in pts)

    assert nearest_to_apex("midpoint") < 0.2     # a hole sits on the apex
    assert nearest_to_apex("straddle") > 1.0     # apex bare, holes straddle it


def test_flip_symmetry_helper():
    from leathercad.stitching import flip_symmetry
    # symmetric about x=0
    pts = [Vec2(-5, 1), Vec2(5, 1), Vec2(-3, -2), Vec2(3, -2)]
    assert flip_symmetry(pts, "vertical")[0]
    # break it
    pts2 = pts + [Vec2(7, 4)]
    ok, off, un = flip_symmetry(pts2, "vertical")
    assert not ok and un >= 1


def test_two_row_saddle_stitch():
    """rows=2 doubles the holes into two parallel rows offset by row_spacing."""
    sl1 = StitchLine(points=[Vec2(0, 0), Vec2(40, 0)],
                     settings=StitchSettings(pitch_mm=4.0, fit="endpoints", rows=1))
    sl2 = StitchLine(points=[Vec2(0, 0), Vec2(40, 0)],
                     settings=StitchSettings(pitch_mm=4.0, fit="endpoints",
                                             rows=2, row_spacing=3.0))
    r1, r2 = sl1.result(), sl2.result()
    assert r2.count == 2 * r1.count
    ys = sorted(set(round(h.point.y, 3) for h in r2.holes))
    assert ys == [-1.5, 1.5]  # perpendicular offset on a horizontal seam


def test_stitch_line_registration_shared():
    """A shared stitch line yields ONE hole set; any piece against it lines
    up by construction."""
    sl = StitchLine(points=[Vec2(0, 0), Vec2(60, 0)],
                    settings=StitchSettings(pitch_mm=4.0, fit="endpoints"))
    res = sl.result()
    assert res.count >= 2
    assert (res.holes[0].point - Vec2(0, 0)).length() < 1e-6
    assert (res.holes[-1].point - Vec2(60, 0)).length() < 1e-6

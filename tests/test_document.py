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

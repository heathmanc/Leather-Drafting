"""Tests that prove the pricking-iron model behaves like a real iron."""

import math

import pytest

from leathercad import (
    Vec2, PathBuilder, Polyline, stitch_path, march_chord, march_arclength,
    polyline_from_path, spi_to_mm, mm_to_spi,
)
from leathercad.path import Path, Line


def _circle_polyline(radius: float, n: int = 4000) -> Polyline:
    pts = [Vec2(radius * math.cos(2 * math.pi * i / n),
                radius * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return Polyline(pts)


def _straight_polyline(length: float) -> Polyline:
    return Polyline([Vec2(0, 0), Vec2(length, 0)])


# ---------------------------------------------------------------------------
# The core claim: chord marching gives constant STRAIGHT-LINE spacing.
# ---------------------------------------------------------------------------
def test_chord_spacing_is_exact_on_a_circle():
    poly = _circle_polyline(20.0)
    pitch = 5.0
    positions = march_chord(poly, pitch, 0.0, poly.length)
    pts = [poly.point_at(s) for s in positions]
    gaps = [(pts[i + 1] - pts[i]).length() for i in range(len(pts) - 1)]
    for g in gaps:
        assert abs(g - pitch) < 1e-2, f"chord gap {g} should equal pitch {pitch}"


def test_arclength_spacing_is_short_on_a_circle():
    """Arc-length spacing produces chords SHORTER than the pitch on curves --
    this is exactly the flaw the project exists to fix."""
    R = 8.0
    poly = _circle_polyline(R)
    pitch = 4.0
    positions = march_arclength(poly, pitch, 0.0, poly.length)
    pts = [poly.point_at(s) for s in positions]
    gaps = [(pts[i + 1] - pts[i]).length() for i in range(len(pts) - 1)]
    expected_chord = 2 * R * math.sin(pitch / (2 * R))
    assert expected_chord < pitch  # geometric fact
    for g in gaps:
        assert abs(g - expected_chord) < 1e-2
        assert g < pitch - 1e-3  # measurably short of the nominal pitch


def test_chord_and_arclength_agree_on_straight_line():
    poly = _straight_polyline(50.0)
    c = march_chord(poly, 5.0)
    a = march_arclength(poly, 5.0)
    assert len(c) == len(a)
    for sc, sa in zip(c, a):
        assert abs(sc - sa) < 1e-6


# ---------------------------------------------------------------------------
# Fitting: holes land on corners and endpoints.
# ---------------------------------------------------------------------------
def test_open_path_fits_holes_on_both_endpoints():
    path = PathBuilder().move_to(0, 0).line_to(53, 0).build()
    res = stitch_path(path, pitch=4.0, fit="endpoints")
    pts = res.points
    assert (pts[0] - Vec2(0, 0)).length() < 1e-6
    assert (pts[-1] - Vec2(53, 0)).length() < 1e-6
    gaps = res.chord_spacings()
    # evenly spaced, near the requested pitch
    assert max(gaps) - min(gaps) < 1e-3
    assert abs(sum(gaps) / len(gaps) - 4.0) < 0.5


def test_rectangle_puts_a_hole_on_every_corner():
    b = PathBuilder().move_to(0, 0)
    b.line_to(40, 0, corner=True)
    b.line_to(40, 25, corner=True)
    b.line_to(0, 25, corner=True)
    b.close(corner=True)
    path = b.build()
    res = stitch_path(path, pitch=4.0, fit="closed")
    corners = [Vec2(0, 0), Vec2(40, 0), Vec2(40, 25), Vec2(0, 25)]
    for c in corners:
        assert any((p - c).length() < 1e-3 for p in res.points), \
            f"expected a hole at corner {c}"


def test_closed_loop_has_no_duplicate_start_hole():
    b = PathBuilder().move_to(0, 0)
    b.line_to(30, 0, corner=True).line_to(30, 30, corner=True)
    b.line_to(0, 30, corner=True).close(corner=True)
    path = b.build()
    res = stitch_path(path, pitch=5.0, fit="closed")
    pts = res.points
    # No two holes coincide (the loop closes cleanly).
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            assert (pts[i] - pts[j]).length() > 1e-4


def test_fit_stays_within_max_dev():
    path = PathBuilder().move_to(0, 0).line_to(53, 0).build()
    res = stitch_path(path, pitch=4.0, fit="endpoints", max_dev=0.12)
    for p in res.pitches:
        assert abs(p - 4.0) / 4.0 <= 0.12 + 1e-9


# ---------------------------------------------------------------------------
# SPI conversions.
# ---------------------------------------------------------------------------
def test_spi_roundtrip():
    assert abs(spi_to_mm(mm_to_spi(3.85)) - 3.85) < 1e-9
    assert abs(spi_to_mm(5) - 5.08) < 1e-9  # 25.4 / 5


# ---------------------------------------------------------------------------
# Curves: a hole count that makes sense and clean endpoints.
# ---------------------------------------------------------------------------
def test_curved_path_endpoints_and_spacing():
    b = PathBuilder().move_to(0, 0).cubic_to(20, 40, 60, 40, 80, 0)
    path = b.build()
    res = stitch_path(path, pitch=4.0, fit="endpoints")
    pts = res.points
    assert (pts[0] - Vec2(0, 0)).length() < 1e-3
    assert (pts[-1] - Vec2(80, 0)).length() < 1e-3
    gaps = res.chord_spacings()
    # chord spacing stays close to pitch everywhere
    for g in gaps:
        assert abs(g - res.pitches[0]) < 0.05


# ---------------------------------------------------------------------------
# corner_style "auto" = the least-deviation strategy of the concrete ones.
# ---------------------------------------------------------------------------
def _rounded_rect_holes(corner_style):
    import copy
    from leathercad.shapes import Rectangle, Transform
    from leathercad.stitchsettings import StitchSettings
    from leathercad.stitching import holes_for_shape
    sh = Rectangle(width=90, height=60, corner_radius=8,
                   transform=Transform(x=0, y=0),
                   stitch=StitchSettings(pitch_mm=3.85, inset=3.5,
                                         corner_style=corner_style), layer="Cut")
    return holes_for_shape(sh)


def _max_gap_dev(res, pitch):
    pts = [h.point for h in res.holes]
    n = len(pts)
    return max(abs((pts[i] - pts[(i + 1) % n]).length() - pitch) / pitch
               for i in range(n))


def test_auto_corner_style_picks_least_deviation():
    """On a generous corner (r8, 3.85 mm iron) the anchor-both-tangents strategy
    strands ~8% off pitch while an apex hole holds ~1.5%. 'auto' must land on the
    better one, never worse than every concrete strategy."""
    pitch = 3.85
    devs = {s: _max_gap_dev(_rounded_rect_holes(s), pitch)
            for s in ("tangent", "midpoint", "straddle")}
    auto = _max_gap_dev(_rounded_rect_holes("auto"), pitch)
    assert auto <= min(devs.values()) + 1e-9      # no worse than the best
    assert auto < devs["tangent"] - 0.03          # and it actually improved
    assert auto < 0.03                             # ~1.5% here, tidy


def test_auto_matches_the_winning_strategy_hole_for_hole():
    from leathercad.stitching import holes_for_shape  # noqa: F401
    pitch = 3.85
    best = min(("tangent", "midpoint", "straddle"),
               key=lambda s: _max_gap_dev(_rounded_rect_holes(s), pitch))
    a = _rounded_rect_holes("auto").holes
    b = _rounded_rect_holes(best).holes
    assert len(a) == len(b)
    for ha, hb in zip(a, b):
        assert (ha.point - hb.point).length() < 1e-6


def test_collinear_node_insert_does_not_shift_stitches():
    """Inserting a node on a straight edge (without moving it) must NOT change
    the stitch pattern: a collinear vertex is not a real corner, so it forces no
    extra hole and the whole fit stays put."""
    from leathercad.shapes import Polygon
    from leathercad.stitchsettings import StitchSettings
    from leathercad.stitching import holes_for_shape

    sq = [Vec2(0, 0), Vec2(120, 0), Vec2(120, 80), Vec2(0, 80)]
    st = StitchSettings(enabled=True, pitch_mm=4.0)
    a = Polygon(points=list(sq), sharp_corners=True, close_path=True, stitch=st)
    before = holes_for_shape(a).holes

    # add a node on the bottom edge, exactly collinear (not moved)
    sq2 = [Vec2(0, 0), Vec2(60, 0), Vec2(120, 0), Vec2(120, 80), Vec2(0, 80)]
    b = Polygon(points=list(sq2), sharp_corners=True, close_path=True, stitch=st)
    after = holes_for_shape(b).holes

    assert len(before) == len(after)
    for ha, hb in zip(before, after):
        assert (ha.point - hb.point).length() < 1e-6

    # the inserted collinear node did not become a corner -- still 4 corners
    assert len(a.local_path().corner_points) == 4
    assert len(b.local_path().corner_points) == 4


# ---------------------------------------------------------------------------
# No clusters of holes in corners (shape -> Stitch used to pack a handful of
# holes into one corner for some radius / inset / corner-style combinations).
# ---------------------------------------------------------------------------
def _shape_gaps(shape, **kw):
    from leathercad.stitching import stitch_polyline
    from leathercad.stitchsettings import StitchSettings
    st = StitchSettings()
    for k, v in kw.items():
        setattr(st, k, v)
    path = shape.local_path()
    res = stitch_polyline(path.flatten(), list(path.corner_points),
                          path.closed, st)
    pts = [h.point for h in res.holes]
    n = len(pts)
    rng = range(n) if path.closed else range(n - 1)
    return pts, [(pts[i] - pts[(i + 1) % n]).length() for i in rng], st


@pytest.mark.parametrize("w,h,r,inset", [
    (80, 50, 3.0, 3.5),           # fillet just under the default inset
    (80, 50, 3.0, 8.0),           # ...and well under a wide inset
    (20, 15, 2.7, 3.5),           # what the Rounded tool draws at this size
    (80, 50, 0.5, 5.0),
])
def test_fillet_tighter_than_inset_gets_one_corner_hole(w, h, r, inset):
    from leathercad.shapes import Rectangle
    pts, gaps, st = _shape_gaps(Rectangle(width=w, height=h, corner_radius=r),
                                inset=inset)
    assert min(gaps) >= 0.75 * st.pitch_mm
    # the stitch line has a sharp corner there, and exactly one hole on it
    cx, cy = w / 2 - inset, h / 2 - inset
    at_corner = [p for p in pts if abs(p.x - cx) < 0.5 and abs(p.y - cy) < 0.5]
    assert len(at_corner) == 1


@pytest.mark.parametrize("style", ["auto", "midpoint", "straddle"])
def test_fillets_joined_by_a_short_straight_do_not_crowd(style):
    from leathercad.shapes import Polygon
    # 45-degree corners with r=15: the fillets nearly meet, leaving a 1.5 mm
    # straight whose two tangent points each used to force a hole
    tri = Polygon(points=[Vec2(0, 0), Vec2(60, 0), Vec2(20, 40)],
                  corner_radius=15.0)
    for inset in (0.0, 3.5):
        _, gaps, st = _shape_gaps(tri, inset=inset, corner_style=style)
        assert min(gaps) >= 0.6 * st.pitch_mm


@pytest.mark.parametrize("r,inset", [(4.0, 0.0), (6.0, 2.0)])
def test_straddle_on_a_small_arc_stretches_rather_than_squeezes(r, inset):
    from leathercad.shapes import Rectangle
    _, gaps, st = _shape_gaps(Rectangle(width=80, height=50, corner_radius=r),
                              inset=inset, corner_style="straddle")
    assert min(gaps) >= 0.75 * st.pitch_mm


def test_imported_curve_with_a_corner_at_every_vertex_does_not_crowd():
    from leathercad.shapes import Polygon
    # a DXF polyline curve, ~1 mm per segment, turning >1 deg per vertex so
    # every vertex counts as a sharp corner
    pts = []
    for i in range(240):
        t = 2 * math.pi * i / 240
        rr = 40 + 4 * math.sin(5 * t)
        pts.append(Vec2(rr * math.cos(t), rr * 0.7 * math.sin(t)))
    _, gaps, st = _shape_gaps(Polygon(points=pts))
    assert min(gaps) >= 0.6 * st.pitch_mm
    assert max(gaps) <= 1.5 * st.pitch_mm


def test_open_zigzag_keeps_its_ends_and_does_not_crowd():
    from leathercad.shapes import Polygon
    zig = Polygon(points=[Vec2(i * 2.0, (i % 2) * 1.5) for i in range(30)],
                  close_path=False)
    pts, gaps, st = _shape_gaps(zig)
    assert min(gaps) >= 0.6 * st.pitch_mm
    assert (pts[0] - Vec2(0, 0)).length() < 1e-6
    assert (pts[-1] - Vec2(58, 1.5)).length() < 1e-6


def test_space_anchors_keeps_corners_and_symmetry():
    from leathercad.stitching import _space_anchors
    total = 100.0
    # a lone anchor is never touched; a corner (rank 1) beats a tangent (2)
    out = _space_anchors([10.0, 50.0, 51.0], total, True, 3.0,
                         lambda s: 1 if s == 51.0 else 2)
    assert out == [10.0, 51.0]
    # a mirror-image pair of equal rank collapses to its midpoint
    out = _space_anchors([10.0, 49.0, 51.0], total, True, 3.0, lambda s: 2)
    assert out == [10.0, 50.0]
    # rank 0 (open-path ends, symmetry crossings) always stays
    out = _space_anchors([0.0, 1.0, 30.0], 30.0, False, 3.0,
                         lambda s: 0 if s in (0.0, 30.0) else 1)
    assert out == [0.0, 30.0]
    # runs wrap across the seam of a closed ring
    out = _space_anchors([99.0, 1.0, 50.0], total, True, 3.0, lambda s: 2)
    assert out == [0.0, 50.0]


def _star(points=8, r_out=15.0, ratio=0.45, **kw):
    from leathercad.shapes import Polygon
    pts = [Vec2((r_out if i % 2 == 0 else r_out * ratio)
                * math.cos(math.pi * i / points),
                (r_out if i % 2 == 0 else r_out * ratio)
                * math.sin(math.pi * i / points)) for i in range(2 * points)]
    return Polygon(points=pts, **kw)


def test_thin_arm_corners_land_on_their_own_inset_vertices():
    # the inset of an inner corner of a thin arm moves far inward; projecting
    # the corner to the NEAREST point of the inset ring put its hole part-way
    # along the neighbouring edge and left the real inset corner bare
    pts, gaps, st = _shape_gaps(_star(), inset=2.0)
    assert len(pts) == 16
    assert max(gaps) - min(gaps) < 0.05


def test_fillet_equal_to_inset_keeps_its_tip_hole():
    # r == inset collapses each fillet to a sharp tip; keeping one of its two
    # tangent anchors (rather than their middle) put the hole just past the
    # tip and the march round the other side skipped a whole pitch
    _, gaps, st = _shape_gaps(_star(5, 30.0, corner_radius=2.0),
                              inset=2.0, pitch_mm=2.7)
    assert max(gaps) <= 1.25 * st.pitch_mm


def test_star_collapsed_to_a_tiny_star_stitches_its_tips():
    pts, gaps, st = _shape_gaps(_star(), inset=3.5)
    assert len(pts) == 8                       # one per tip, none crammed
    assert max(gaps) - min(gaps) < 0.05


def test_ring_too_small_for_two_holes_gets_one():
    from leathercad.shapes import Ellipse, Rectangle
    pts, _, _ = _shape_gaps(Ellipse(rx=4, ry=4), inset=3.5, pitch_mm=2.7)
    assert len(pts) == 1
    pts, _, _ = _shape_gaps(Rectangle(width=8, height=8), inset=5.0)
    assert pts == []                           # no stitch line fits at all


def test_march_chord_n_never_drops_the_span_start():
    from leathercad.stitching import march_chord_n
    poly = Polyline([Vec2(0, 0), Vec2(10, 0)])
    # a pitch the march can't take even once: both ends must still be holes
    assert march_chord_n(poly, 50.0, 1, 0.0, 4.0) == [0.0, 4.0]
    # stopping one step short appends the end instead of overwriting a hole
    assert march_chord_n(poly, 3.0, 2, 0.0, 9.5) == pytest.approx(
        [0.0, 3.0, 6.0, 9.5])

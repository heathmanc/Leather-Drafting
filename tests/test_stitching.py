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

"""The heart of the project: pricking-iron-accurate stitch hole spacing.

The distinction that matters for leatherwork
--------------------------------------------
Most CAD / pattern tools place stitch holes at a fixed *arc length* along the
path: walk 5 mm along the contour, drop a hole, repeat. On a curve the actual
straight-line gap between two neighbouring holes then comes out *shorter* than
5 mm -- so the holes never line up with a physical pricking iron / stitching
chisel, whose teeth are rigid and a fixed straight-line (chord) distance apart.

This module instead does **chord marching**: from each hole it finds the next
point *forward along the path* whose straight-line distance is exactly the
iron's pitch (the intersection of a circle of radius = pitch with the path).
That reproduces what an iron physically does as you rotate it to follow a curve
-- consecutive holes are always ``pitch`` apart point-to-point.

It also does the thing leatherworkers do by hand: nudge the effective pitch a
few percent so a whole number of holes lands cleanly on every corner and on
both ends of an open seam (``fit`` modes below).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .geometry import Vec2, distance
from .path import Path, DEFAULT_FLATNESS

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Polyline: a flattened path with cumulative arc-length lookups
# ---------------------------------------------------------------------------
class Polyline:
    def __init__(self, points: Sequence[Vec2]):
        self.points: List[Vec2] = list(points)
        self.cum: List[float] = [0.0]
        for i in range(1, len(self.points)):
            self.cum.append(self.cum[-1] + (self.points[i] - self.points[i - 1]).length())

    @property
    def length(self) -> float:
        return self.cum[-1] if self.cum else 0.0

    def seg_index_at(self, s: float) -> int:
        """Index i such that cum[i] <= s <= cum[i+1]."""
        s = max(0.0, min(self.length, s))
        lo, hi = 0, len(self.cum) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid + 1
            else:
                hi = mid
        return max(0, lo - 1)

    def point_at(self, s: float) -> Vec2:
        s = max(0.0, min(self.length, s))
        i = self.seg_index_at(s)
        seg_len = self.cum[i + 1] - self.cum[i]
        if seg_len <= _EPS:
            return self.points[i]
        t = (s - self.cum[i]) / seg_len
        return self.points[i].lerp(self.points[i + 1], t)

    def nearest_arclength(self, target: Vec2) -> float:
        """Arc-length of the closest point on the polyline to ``target``."""
        best_s = 0.0
        best_d = float("inf")
        for i in range(len(self.points) - 1):
            a, b = self.points[i], self.points[i + 1]
            ab = b - a
            denom = ab.length_sq()
            t = 0.0 if denom <= _EPS else max(0.0, min(1.0, (target - a).dot(ab) / denom))
            proj = a.lerp(b, t)
            d = (proj - target).length_sq()
            if d < best_d:
                best_d = d
                best_s = self.cum[i] + (b - a).length() * t
        return best_s

    def tangent_at(self, s: float) -> Vec2:
        s = max(0.0, min(self.length, s))
        i = self.seg_index_at(s)
        d = self.points[i + 1] - self.points[i]
        n = d.length()
        if n <= _EPS:
            # look at neighbours
            j = min(i + 1, len(self.points) - 2)
            d = self.points[j + 1] - self.points[j]
            n = d.length()
        return d / n if n > _EPS else Vec2(1.0, 0.0)


def polyline_from_path(path: Path, flatness: float = DEFAULT_FLATNESS) -> Polyline:
    return Polyline(path.flatten(flatness))


# ---------------------------------------------------------------------------
# Chord marching -- the pricking-iron model
# ---------------------------------------------------------------------------
def _circle_forward_t(a: Vec2, b: Vec2, c: Vec2, r: float) -> Optional[float]:
    """Parameter t in (0, 1] where segment a->b exits the circle |x-c|=r.

    Assumes ``a`` is inside or on the circle; returns the outward crossing.
    """
    d = b - a
    f = a - c
    aa = d.length_sq()
    if aa <= _EPS:
        return None
    bb = 2.0 * f.dot(d)
    cc = f.length_sq() - r * r
    disc = bb * bb - 4.0 * aa * cc
    if disc < 0.0:
        return None
    sq = math.sqrt(disc)
    t = (-bb + sq) / (2.0 * aa)  # outward (larger) root
    if t <= _EPS or t > 1.0 + _EPS:
        return None
    return min(t, 1.0)


def _next_chord(poly: Polyline, center: Vec2, cur_s: float, pitch: float,
                end_s: float):
    """Return (s, point) of the next hole exactly ``pitch`` (chord) ahead."""
    pts, cum = poly.points, poly.cum
    i = poly.seg_index_at(cur_s)
    seg_start_pt = center
    seg_start_s = cur_s
    idx = i
    n = len(pts)
    while idx < n - 1:
        a = seg_start_pt
        b = pts[idx + 1]
        b_s = cum[idx + 1]
        if b_s > end_s:
            b = poly.point_at(end_s)
            b_s = end_s
        t = _circle_forward_t(a, b, center, pitch)
        if t is not None:
            q = a.lerp(b, t)
            q_s = seg_start_s + (b_s - seg_start_s) * t
            return q_s, q
        if b_s >= end_s:
            return None
        seg_start_pt = pts[idx + 1]
        seg_start_s = cum[idx + 1]
        idx += 1
    return None


def march_chord(poly: Polyline, pitch: float, start_s: float = 0.0,
                end_s: Optional[float] = None, max_holes: int = 1_000_000) -> List[float]:
    """Arc-length positions of holes with constant *chord* spacing = pitch."""
    if end_s is None:
        end_s = poly.length
    if pitch <= 0:
        raise ValueError("pitch must be positive")
    result = [start_s]
    cur_s = start_s
    cur_pt = poly.point_at(cur_s)
    while len(result) < max_holes:
        nxt = _next_chord(poly, cur_pt, cur_s, pitch, end_s)
        if nxt is None:
            break
        cur_s, cur_pt = nxt
        result.append(cur_s)
    return result


def march_arclength(poly: Polyline, pitch: float, start_s: float = 0.0,
                    end_s: Optional[float] = None) -> List[float]:
    """Naive arc-length spacing -- provided for comparison only.

    This is what most tools do, and what a pricking iron does NOT do.
    """
    if end_s is None:
        end_s = poly.length
    n = int(math.floor((end_s - start_s) / pitch + _EPS))
    return [start_s + k * pitch for k in range(n + 1)]


# ---------------------------------------------------------------------------
# Fitting: nudge the pitch so holes land on the endpoints / corners
# ---------------------------------------------------------------------------
def _march_n_final_s(poly: Polyline, pitch: float, n_steps: int,
                     start_s: float, end_s: float) -> Optional[float]:
    """Arc-length of the point reached after exactly ``n_steps`` chord steps."""
    cur_s = start_s
    cur_pt = poly.point_at(cur_s)
    for _ in range(n_steps):
        nxt = _next_chord(poly, cur_pt, cur_s, pitch, end_s)
        if nxt is None:
            return None  # ran off the end before completing the steps
        cur_s, cur_pt = nxt
    return cur_s


def fit_pitch_for_count(poly: Polyline, n_intervals: int, start_s: float,
                        end_s: float, tol: float = 1e-4) -> Optional[float]:
    """Find the chord pitch so ``n_intervals`` steps land exactly on ``end_s``.

    Returns the pitch, or None if no pitch in a sensible band works.
    """
    if n_intervals < 1:
        return None
    span = end_s - start_s
    nominal = span / n_intervals
    lo = nominal * 0.4
    hi = nominal * 1.05  # chord pitch is always <= arc pitch
    target = poly.point_at(end_s)

    def residual(p: float) -> float:
        s_final = _march_n_final_s(poly, p, n_intervals, start_s, end_s)
        if s_final is None:
            return -1e18  # too big; couldn't complete the steps
        pt = poly.point_at(s_final)
        # Signed by how far short of the end we landed (in arc length).
        return (end_s - s_final)

    r_lo = residual(lo)
    r_hi = residual(hi)
    # residual should decrease as pitch grows (bigger steps -> reach end sooner)
    if r_lo < 0 or r_hi > 0:
        # widen the band once
        lo *= 0.7
        hi *= 1.15
        r_lo = residual(lo)
        r_hi = residual(hi)
        if not (r_lo >= 0 >= r_hi):
            return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        r = residual(mid)
        if abs(r) < tol:
            return mid
        if r > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _best_fit_span(poly: Polyline, target_pitch: float, start_s: float,
                   end_s: float, max_dev: float):
    """Choose an integer hole count for one span and return (pitch, n)."""
    span = end_s - start_s
    if span <= _EPS:
        return target_pitch, 0
    n0 = max(1, int(round(span / target_pitch)))
    best = None
    for n in (n0 - 1, n0, n0 + 1, n0 + 2):
        if n < 1:
            continue
        p = fit_pitch_for_count(poly, n, start_s, end_s)
        if p is None:
            continue
        dev = abs(p - target_pitch) / target_pitch
        if dev <= max_dev and (best is None or dev < best[2]):
            best = (p, n, dev)
    if best is None:
        return target_pitch, None  # caller falls back to plain marching
    return best[0], best[1]


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------
@dataclass
class Hole:
    point: Vec2
    tangent: Vec2  # unit direction of the path at the hole


@dataclass
class StitchResult:
    holes: List[Hole] = field(default_factory=list)
    # Effective pitch(es) actually used, per fitted span, in mm.
    pitches: List[float] = field(default_factory=list)
    closed: bool = False

    @property
    def points(self) -> List[Vec2]:
        return [h.point for h in self.holes]

    @property
    def count(self) -> int:
        return len(self.holes)

    def chord_spacings(self) -> List[float]:
        """Straight-line distance between consecutive holes (sanity check)."""
        pts = self.points
        return [distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def stitch_path(path: Path, pitch: float, *, mode: str = "chord",
                fit: str = "auto", max_dev: float = 0.12,
                flatness: float = DEFAULT_FLATNESS,
                use_corners: bool = True) -> StitchResult:
    """Place stitch holes along ``path``.

    Parameters
    ----------
    pitch : float
        Target hole spacing in mm (the pricking-iron pitch). See ``irons`` for
        SPI conversion helpers.
    mode : {"chord", "arclength"}
        ``chord`` = pricking-iron accurate (default). ``arclength`` = the naive
        contour spacing, for comparison.
    fit : {"auto", "endpoints", "closed", "none"}
        ``auto`` picks ``closed`` for closed paths and ``endpoints`` for open
        ones. Fitting nudges the pitch (within ``max_dev``) so holes land on
        every corner and on both ends. ``none`` marches at the exact pitch and
        lets the last hole fall where it may.
    max_dev : float
        Maximum fractional pitch adjustment allowed while fitting (0.12 = 12%).
    use_corners : bool
        If True, force a hole at each corner recorded on the path and fit each
        edge between corners independently.
    """
    poly = polyline_from_path(path, flatness)
    if poly.length <= _EPS:
        return StitchResult(closed=path.closed)

    if fit == "auto":
        fit = "closed" if path.closed else "endpoints"

    if mode == "arclength":
        positions = march_arclength(poly, pitch)
        return _result_from_positions(poly, positions, [pitch], path.closed)

    if mode != "chord":
        raise ValueError(f"unknown mode {mode!r}")

    # Build the list of anchor arc-length positions that split the path.
    anchors = _build_anchors(path, poly, fit, use_corners)

    if anchors is None:
        # Plain chord marching, no fitting.
        positions = march_chord(poly, pitch)
        return _result_from_positions(poly, positions, [pitch], path.closed)

    positions: List[float] = []
    pitches: List[float] = []
    for a, b in _spans(anchors, path.closed, poly.length):
        p_eff, n = _best_fit_span(poly, pitch, a, b, max_dev)
        if n is None:
            span_positions = march_chord(poly, pitch, a, b)
            pitches.append(pitch)
        else:
            span_positions = march_chord_n(poly, p_eff, n, a, b)
            pitches.append(p_eff)
        # Drop the last position of each span except the final one; the next
        # span's anchor is the same point (avoid duplicate holes at corners).
        positions.extend(span_positions[:-1])
    # Add the very last anchor (end of an open path) or close the loop.
    if not path.closed:
        positions.append(anchors[-1])
    return _result_from_positions(poly, positions, pitches, path.closed)


def march_chord_n(poly: Polyline, pitch: float, n_steps: int, start_s: float,
                  end_s: float) -> List[float]:
    """Chord-march exactly ``n_steps`` intervals from start_s to (about) end_s."""
    result = [start_s]
    cur_s = start_s
    cur_pt = poly.point_at(cur_s)
    for _ in range(n_steps):
        nxt = _next_chord(poly, cur_pt, cur_s, pitch, end_s + 1e-6)
        if nxt is None:
            break
        cur_s, cur_pt = nxt
        result.append(cur_s)
    # Snap the final hole exactly onto the span end for a clean corner.
    result[-1] = end_s
    return result


def _build_anchors(path: Path, poly: Polyline, fit: str,
                   use_corners: bool) -> Optional[List[float]]:
    corners = [c for c in path.corners if 0.0 < c < poly.length] if use_corners else []
    if fit == "none" and not corners:
        return None
    if path.closed:
        anchors = sorted(set([0.0] + corners))
        return anchors
    # open path
    anchors = sorted(set([0.0, poly.length] + corners))
    return anchors


def _spans(anchors: List[float], closed: bool, total: float):
    if closed:
        for i in range(len(anchors)):
            a = anchors[i]
            b = anchors[(i + 1) % len(anchors)]
            if i == len(anchors) - 1:
                b = total  # wrap back to start
            yield a, b
    else:
        for i in range(len(anchors) - 1):
            yield anchors[i], anchors[i + 1]


def _result_from_positions(poly: Polyline, positions: List[float],
                           pitches: List[float], closed: bool) -> StitchResult:
    holes = [Hole(poly.point_at(s), poly.tangent_at(s)) for s in positions]
    return StitchResult(holes=holes, pitches=pitches, closed=closed)


# ---------------------------------------------------------------------------
# Polyline-level API used by the shapes / document layer
# ---------------------------------------------------------------------------
def _rotate_closed_ring(points: List[Vec2], start_offset: float) -> List[Vec2]:
    """Rotate a closed ring so arc-length 0 sits at ``start_offset``.

    Keeping ``start_offset`` identical across two pieces with the same outline
    guarantees identical holes -> perfect stitch registration.
    """
    poly = Polyline(points)
    total = poly.length
    if total <= _EPS:
        return points
    s0 = start_offset % total
    if s0 <= _EPS:
        return points
    new_start = poly.point_at(s0)
    i = poly.seg_index_at(s0)
    ring = points[:-1] if (points[0] - points[-1]).length() < 1e-9 else points[:]
    n = len(ring)
    rotated = [new_start]
    for k in range(1, n + 1):
        rotated.append(ring[(i + k) % n])
    if (rotated[0] - rotated[-1]).length() > 1e-9:
        rotated.append(rotated[0])
    return rotated


def stitch_polyline(points: List[Vec2], corner_points: List[Vec2], closed: bool,
                    settings) -> StitchResult:
    """Distribute holes on a world-space polyline using ``StitchSettings``.

    ``corner_points`` are geometric points where a hole must be forced (e.g.
    sharp corners). For closed outlines the stitch line is inset from the edge
    by ``settings.inset`` first; corners are then projected onto the inset ring,
    so a sharp corner still gets a hole at the *inset* corner. This is the
    function the CAD document / export layer calls.
    """
    from .offset import offset_closed_inward

    pts = list(points)

    if closed and settings.inset and settings.inset > 0:
        pts = offset_closed_inward(pts, settings.inset)

    if closed and getattr(settings, "start_offset", 0.0):
        pts = _rotate_closed_ring(pts, settings.start_offset)

    poly = Polyline(pts)
    if poly.length <= _EPS:
        return StitchResult(closed=closed)

    # Project corner points onto the (possibly inset) stitch line.
    cor = sorted(poly.nearest_arclength(cp) for cp in (corner_points or []))

    fit = settings.fit
    if fit == "auto":
        fit = "closed" if closed else "endpoints"

    if settings.mode == "arclength":
        positions = march_arclength(poly, settings.pitch_mm)
        return _apply_rows(_result_from_positions(
            poly, positions, [settings.pitch_mm], closed), settings)

    anchors = _anchors_from(cor, poly.length, closed, fit)
    if anchors is None:
        positions = march_chord(poly, settings.pitch_mm)
        return _apply_rows(_result_from_positions(
            poly, positions, [settings.pitch_mm], closed), settings)

    positions: List[float] = []
    pitches: List[float] = []
    for a, b in _spans(anchors, closed, poly.length):
        p_eff, n = _best_fit_span(poly, settings.pitch_mm, a, b, settings.max_dev)
        if n is None:
            span_positions = march_chord(poly, settings.pitch_mm, a, b)
            pitches.append(settings.pitch_mm)
        else:
            span_positions = march_chord_n(poly, p_eff, n, a, b)
            pitches.append(p_eff)
        positions.extend(span_positions[:-1])
    if not closed:
        positions.append(anchors[-1])
    return _apply_rows(
        _result_from_positions(poly, positions, pitches, closed), settings)


def _apply_rows(result: StitchResult, settings) -> StitchResult:
    """Expand to a second parallel row of holes for saddle stitching.

    Each hole becomes two, offset by +/- row_spacing/2 along the seam normal,
    at the same tangential position (aligned rungs).
    """
    rows = getattr(settings, "rows", 1)
    if rows <= 1 or not result.holes:
        return result
    half = getattr(settings, "row_spacing", 3.0) / 2.0
    doubled: List[Hole] = []
    for h in result.holes:
        n = h.tangent.perp()
        doubled.append(Hole(h.point + n * half, h.tangent))
        doubled.append(Hole(h.point - n * half, h.tangent))
    result.holes = doubled
    return result


def _anchors_from(corners: List[float], total: float, closed: bool,
                  fit: str) -> Optional[List[float]]:
    cor = [c for c in corners if 1e-6 < c < total - 1e-6]
    if fit == "none" and not cor:
        return None
    if closed:
        return sorted(set([0.0] + cor))
    return sorted(set([0.0, total] + cor))

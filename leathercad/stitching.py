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
                   end_s: float, max_dev: float, parity: Optional[str] = None,
                   symmetric_fallback: bool = False):
    """Choose an integer hole count for one span and return (pitch, n).

    Prefers a count whose pitch stays within ``max_dev`` of the target. If none
    does and ``symmetric_fallback`` is set (every span of a closed outline), the
    closest-pitch count is used anyway rather than returning ``None`` -- a corner
    arc must place its holes *symmetrically* even when no count hits the pitch
    exactly, so we never drop back to a lopsided plain march there. ``parity``
    ('odd') forces an even pair to straddle a bare apex.
    """
    span = end_s - start_s
    if span <= _EPS:
        return target_pitch, 0
    n0 = max(1, int(round(span / target_pitch)))

    def ok_parity(n: int) -> bool:
        if parity == "even":
            return n % 2 == 0 and n >= 2   # >=2 so a hole lands on the apex
        if parity == "odd":
            return n % 2 == 1 and n >= 1
        return n >= 1

    within = []
    allc = []
    for n in range(max(1, n0 - 3), n0 + 4):
        if not ok_parity(n):
            continue
        p = fit_pitch_for_count(poly, n, start_s, end_s)
        if p is None:
            continue
        dev = abs(p - target_pitch) / target_pitch
        allc.append((dev, p, n))
        if dev <= max_dev:
            within.append((dev, p, n))
    if within:
        within.sort()
        return within[0][1], within[0][2]
    if symmetric_fallback and allc:
        allc.sort()
        return allc[0][1], allc[0][2]
    return target_pitch, None  # caller falls back to plain marching


def _span_is_arc(poly: Polyline, a: float, span_len: float, total: float,
                 tol: float = 0.1) -> bool:
    """True if the outline over the (possibly wrapping) span starting at ``a`` of
    length ``span_len`` curves (a rounded corner), as opposed to a straight edge.
    Detected by the bulge of interior samples off the chord -- a corner arc
    bulges far more than the flattening tolerance, a straight edge not at all."""
    pa = poly.point_at(a % total)
    pb = poly.point_at((a + span_len) % total)
    chord = pb - pa
    length = chord.length()
    if length <= _EPS:
        return False
    max_dev = 0.0
    for k in range(1, 8):
        p = poly.point_at((a + span_len * k / 8.0) % total)
        dev = abs((p.x - pa.x) * chord.y - (p.y - pa.y) * chord.x) / length
        max_dev = max(max_dev, dev)
    return max_dev > tol


def _plan_corners(poly: Polyline, cor: List[float], settings, style=None):
    """Decide where holes are forced around a closed outline's corners.

    Given the projected corner anchor arc-lengths ``cor`` (sharp corners plus the
    tangent points of every rounded arc), classify each span between consecutive
    anchors. A straight span is ignored here (it gets filled later). A curved
    span is a rounded corner and is handled so its holes stay **symmetric about
    the arc midpoint without ever cramming**:

      * A corner too small to hold two holes a full pitch apart collapses to a
        single hole on the arc apex (the neighbours land on the straight edges).
        This is the fix for tight radii, where forcing a hole at each tangent
        point used to pile 3+ holes into a couple of millimetres.
      * A large corner keeps its two tangent points as span anchors so the arc
        is fitted on its own -> holes at equal angular steps, symmetric about the
        apex. ``corner_style`` then biases it: ``midpoint`` also anchors the apex
        (a hole sits on it), ``straddle`` forces an odd count (the apex stays
        bare), ``auto`` takes whatever count best matches the iron.

    Returns ``(forced, straddle)``: the set of arc-lengths that must be holes,
    and the set of ``(a, b)`` arc spans whose fit must be forced odd.
    """
    total = poly.length
    p = settings.pitch_mm
    if style is None:
        style = getattr(settings, "corner_style", "auto")
    if style == "auto":
        style = "tangent"          # concrete default (see _fit_corner_style)
    # Merge near-coincident anchors (e.g. a corner whose inset collapsed the arc
    # to a point when the inset >= the radius) so it becomes one hole, not two
    # stacked on top of each other.
    merge = min(0.3, 0.1 * p)
    cor_sorted = sorted({c % total for c in cor})
    cor_set: List[float] = []
    for c in cor_sorted:
        if not cor_set or (c - cor_set[-1]) > merge:
            cor_set.append(c)
    if len(cor_set) >= 2 and (total - cor_set[-1] + cor_set[0]) <= merge:
        cor_set.pop()          # last wraps onto the first -- same point
    forced: set = set()
    straddle: set = set()
    used: set = set()
    m = len(cor_set)
    for i in range(m):
        a = cor_set[i]
        b = cor_set[(i + 1) % m]
        span_len = (b - a) % total
        if span_len <= _EPS or not _span_is_arc(poly, a, span_len, total):
            continue
        arc_len = span_len
        chord_tt = (poly.point_at(a) - poly.point_at(b)).length()
        apex = (a + arc_len / 2.0) % total
        used.add(a)
        used.add(b)
        if style == "midpoint":
            # a hole on the apex; add the tangents too only if there is room
            # for a full extra pitch on each side (else just the apex).
            if arc_len >= 2.0 * p * 0.95:
                forced |= {a, b, apex}
            else:
                forced.add(apex)
        elif style == "straddle":
            if chord_tt >= p * 0.98:
                forced |= {a, b}
                straddle.add((a, b))
            else:
                forced.add(apex)          # too tight to straddle -> apex hole
        else:  # tangent (the anchor-both-tangent-points strategy)
            if chord_tt >= p * 0.98:
                forced |= {a, b}          # arc fitted on its own (symmetric)
            else:
                forced.add(apex)          # tight corner -> single apex hole
    for c in cor_set:
        if c not in used:                 # a genuine sharp corner
            forced.add(c)
    return forced, straddle


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
    if fit == "none":
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


def _axis_crossings(poly: Polyline, axis: str) -> List[float]:
    """Arc-length positions where the outline crosses its centre axis
    (2 points for a convex shape). ``axis`` is 'vertical' or 'horizontal'."""
    pts = poly.points
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    if axis == "vertical":
        c = 0.5 * (min(xs) + max(xs))
        val = lambda p: p.x - c
    else:
        c = 0.5 * (min(ys) + max(ys))
        val = lambda p: p.y - c
    out = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        va, vb = val(a), val(b)
        if abs(va) < 1e-9:
            out.append(poly.cum[i])
        elif va * vb < 0:
            t = va / (va - vb)
            out.append(poly.cum[i] + (b - a).length() * t)
    # dedupe near-identical crossings
    out.sort()
    uniq = []
    for s in out:
        if not uniq or abs(s - uniq[-1]) > 1e-6:
            uniq.append(s)
    return uniq


def flip_symmetry(points: List[Vec2], axis: str, center: Optional[Vec2] = None,
                  tol: float = 0.05):
    """Check whether a hole set is symmetric under a flip.

    Returns (is_symmetric, max_offset_mm, unmatched_count). ``axis`` is
    'vertical' (mirror x about center) or 'horizontal' (mirror y).
    """
    if not points:
        return True, 0.0, 0
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    cx = center.x if center else 0.5 * (min(xs) + max(xs))
    cy = center.y if center else 0.5 * (min(ys) + max(ys))

    def mirror(p: Vec2) -> Vec2:
        if axis == "vertical":
            return Vec2(2 * cx - p.x, p.y)
        return Vec2(p.x, 2 * cy - p.y)

    max_off = 0.0
    unmatched = 0
    for p in points:
        m = mirror(p)
        best = min(((m - q).length() for q in points), default=float("inf"))
        max_off = max(max_off, best)
        if best > tol:
            unmatched += 1
    return unmatched == 0, max_off, unmatched


def holes_for_shape(shape) -> StitchResult:
    """World-space holes for a shape, computed on its LOCAL outline then
    transformed. Baked holes are transformed directly. Used by export and the
    symmetry check so hole placement is consistent regardless of rotation."""
    t = shape.transform
    baked = getattr(shape, "baked_holes", None)
    if baked:
        return StitchResult(holes=[Hole(t.apply(h.point), t.apply_dir(h.tangent))
                                   for h in baked])
    st = getattr(shape, "stitch", None)
    if not (st and st.enabled):
        return StitchResult()
    path = shape.local_path()
    pts = path.flatten()
    res = stitch_polyline([Vec2(p.x, p.y) for p in pts],
                          list(path.corner_points), path.closed, st)
    res.holes = [Hole(t.apply(h.point), t.apply_dir(h.tangent)) for h in res.holes]
    return res


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


# Fitting a big outline is pure but not cheap, and the SAME outline is fitted
# again on every rebuild (open, undo, redo, layer toggle...). Memoise on the
# exact inputs: items compute holes on LOCAL geometry, so moving a shape --
# or 50 copies of it -- reuses one cached fit. ~512 entries of a few hundred
# holes each is well under a megabyte.
_CACHE_MAX = 512
_stitch_cache: "OrderedDict" = None  # created lazily below


def _cache_key(points, corner_points, closed, settings):
    return (
        tuple((round(p.x, 6), round(p.y, 6)) for p in points),
        tuple((round(p.x, 6), round(p.y, 6)) for p in (corner_points or [])),
        bool(closed),
        # every settings field that moves a hole; appearance fields
        # (hole_style/diameter/slits, backstitch markers) don't invalidate
        (settings.pitch_mm, settings.mode, settings.fit, settings.max_dev,
         settings.inset, getattr(settings, "start_offset", 0.0),
         settings.rows, settings.row_spacing,
         getattr(settings, "symmetry", "none"),
         getattr(settings, "corner_style", "auto")),
    )


def _copy_result(res: StitchResult) -> StitchResult:
    # Fresh lists each time: consumers replace/re-orient the holes list
    # (items.py maps them through the shape transform), so the cached
    # master must never be handed out directly.
    return StitchResult(holes=list(res.holes), pitches=list(res.pitches),
                        closed=res.closed)


def stitch_polyline(points: List[Vec2], corner_points: List[Vec2], closed: bool,
                    settings) -> StitchResult:
    """Distribute holes on a world-space polyline using ``StitchSettings``.

    ``corner_points`` are geometric points where a hole must be forced (e.g.
    sharp corners). For closed outlines the stitch line is inset from the edge
    by ``settings.inset`` first; corners are then projected onto the inset ring,
    so a sharp corner still gets a hole at the *inset* corner. This is the
    function the CAD document / export layer calls. Results are memoised
    (see ``_cache_key``); callers get an independent ``StitchResult`` whose
    ``holes``/``pitches`` lists are safe to replace.
    """
    global _stitch_cache
    if _stitch_cache is None:
        from collections import OrderedDict
        _stitch_cache = OrderedDict()
    key = _cache_key(points, corner_points, closed, settings)
    hit = _stitch_cache.get(key)
    if hit is not None:
        _stitch_cache.move_to_end(key)
        return _copy_result(hit)
    res = _stitch_polyline_impl(points, corner_points, closed, settings)
    _stitch_cache[key] = _copy_result(res)
    while len(_stitch_cache) > _CACHE_MAX:
        _stitch_cache.popitem(last=False)
    return res


def _stitch_polyline_impl(points: List[Vec2], corner_points: List[Vec2],
                          closed: bool, settings) -> StitchResult:
    from .offset import offset_closed_inward

    pts = list(points)

    if closed and settings.inset and settings.inset > 0:
        pts = offset_closed_inward(pts, settings.inset)

    if closed and getattr(settings, "start_offset", 0.0):
        pts = _rotate_closed_ring(pts, settings.start_offset)

    poly = Polyline(pts)
    if poly.length <= _EPS:
        return StitchResult(closed=closed)
    total = poly.length

    fit = settings.fit
    if fit == "auto":
        fit = "closed" if closed else "endpoints"

    if settings.mode == "arclength":
        positions = march_arclength(poly, settings.pitch_mm)
        return _apply_rows(_result_from_positions(
            poly, positions, [settings.pitch_mm], closed), settings)

    # Symmetry: rotate so the outline starts at one axis crossing and anchor a
    # hole at the other. That splits the loop into the two mirror-image halves;
    # each is fitted deterministically, so the holes come out flip-symmetric.
    sym = getattr(settings, "symmetry", "none")
    symmetric = closed and sym in ("vertical", "horizontal")
    axis_forced: set = set()
    if symmetric:
        crossings = _axis_crossings(poly, sym)
        if len(crossings) >= 2:
            poly = Polyline(_rotate_closed_ring(list(poly.points), crossings[0]))
            total = poly.length
            other = [s for s in _axis_crossings(poly, sym) if s > 1e-6]
            if other:
                axis_forced.add(other[0])
        else:
            symmetric = False

    cor = [poly.nearest_arclength(cp) for cp in (corner_points or [])]

    # ``auto`` means "whichever corner strategy the iron marches with the least
    # pitch deviation" -- try each concrete strategy and keep the best. For open
    # paths or an explicit style there is nothing to choose.
    want = getattr(settings, "corner_style", "auto")
    if closed and want == "auto":
        best, best_bad = None, None
        for style in ("tangent", "midpoint", "straddle"):
            res = _march_corner_style(poly, cor, settings, style, closed, fit,
                                      total, symmetric, axis_forced)
            bad = _gap_badness(res, settings.pitch_mm)
            if best_bad is None or bad < best_bad:
                best, best_bad = res, bad
        return _apply_rows(best, settings)

    res = _march_corner_style(poly, cor, settings, want, closed, fit,
                              total, symmetric, axis_forced)
    return _apply_rows(res, settings)


def _gap_badness(res: "StitchResult", pitch: float):
    """How far a fit strays from the iron: (worst, mean) fractional gap error
    over the chord gaps between consecutive holes. Lower is better; ``auto``
    minimises it. Fewer than two holes can't be judged, so it's worst-ranked."""
    pts = [h.point for h in res.holes]
    if len(pts) < 2 or pitch <= 0:
        return (float("inf"), float("inf"))
    n = len(pts)
    span = range(n) if res.closed else range(n - 1)
    devs = [abs((pts[i] - pts[(i + 1) % n]).length() - pitch) / pitch
            for i in span]
    return (max(devs), sum(devs) / len(devs))


def _march_corner_style(poly: Polyline, cor: List[float], settings, style: str,
                        closed: bool, fit: str, total: float, symmetric: bool,
                        axis_forced: set) -> "StitchResult":
    """March the holes for one concrete corner ``style`` (tangent / midpoint /
    straddle). Extracted so ``auto`` can try each and pick the least-deviation
    result. Works on a private rotation of ``poly`` so candidates don't
    interfere."""
    straddle: set = set()
    if closed:
        # Plan corner holes: single apex hole on tight radii (no cramming),
        # a fitted arc span on generous ones. See _plan_corners.
        forced, straddle = _plan_corners(poly, cor, settings, style)
        forced |= axis_forced
        # The span machinery needs a hole at arc-length 0. The final hole set is
        # invariant to which corner we start at (each span is fitted from its own
        # geometry), so for a plain shape just rotate 0 onto the first corner;
        # a symmetric shape already starts on its axis crossing.
        if not symmetric and forced:
            rot = min(forced)
            if rot > _EPS:
                poly = Polyline(_rotate_closed_ring(list(poly.points), rot))
                total = poly.length
                forced = {(s - rot) % total for s in forced}
                straddle = {((a - rot) % total, (b - rot) % total)
                            for (a, b) in straddle}
        corners_for_anchors = sorted(forced)
    else:
        corners_for_anchors = sorted(set(cor))

    anchors = _anchors_from(corners_for_anchors, total, closed, fit)
    if anchors is None:
        positions = march_chord(poly, settings.pitch_mm)
        return _result_from_positions(
            poly, positions, [settings.pitch_mm], closed)

    straddle_keys = {(round(a, 4), round(b, 4)) for (a, b) in straddle}

    positions: List[float] = []
    pitches: List[float] = []
    for a, b in _spans(anchors, closed, total):
        parity = None
        # the wrap span ends at ``total``; straddle keys use it mod total (0.0)
        b_key = 0.0 if abs(b - total) < 1e-6 else b
        if (round(a, 4), round(b_key, 4)) in straddle_keys:
            parity = "odd"          # straddle: an even pair around a bare apex
        p_eff, n = _best_fit_span(poly, settings.pitch_mm, a, b,
                                  settings.max_dev, parity,
                                  symmetric_fallback=closed)
        if n is None:
            span_positions = march_chord(poly, settings.pitch_mm, a, b)
            pitches.append(settings.pitch_mm)
        else:
            span_positions = march_chord_n(poly, p_eff, n, a, b)
            pitches.append(p_eff)
        positions.extend(span_positions[:-1])
    if not closed:
        positions.append(anchors[-1])
    return _result_from_positions(poly, positions, pitches, closed)


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
    # "none": march at the exact pitch with nothing forced -- no endpoint fit,
    # no corner anchoring -- and let the last hole fall where it may. This is
    # what makes the Fit dropdown visibly change a closed shape (which otherwise
    # always anchors on its corners regardless of the mode).
    if fit == "none":
        return None
    cor = [c for c in corners if 1e-6 < c < total - 1e-6]
    if closed:
        return sorted(set([0.0] + cor))
    return sorted(set([0.0, total] + cor))

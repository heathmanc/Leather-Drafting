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
from .offset import signed_area
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
    cx, cy = center.x, center.y
    inside = pitch * pitch * (1.0 - 1e-9)
    while idx < n - 1:
        a = seg_start_pt
        b = pts[idx + 1]
        b_s = cum[idx + 1]
        if b_s > end_s:
            b = poly.point_at(end_s)
            b_s = end_s
        # A segment ending strictly inside the circle can't exit it (the exit
        # root lies past its end) -- skip the full test. On a finely
        # flattened arc most segments of a step are like this.
        if (b.x - cx) ** 2 + (b.y - cy) ** 2 < inside:
            if b_s >= end_s:
                return None
            seg_start_pt = b
            seg_start_s = b_s
            idx += 1
            continue
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
                        end_s: float, tol: float = 1e-7) -> Optional[float]:
    """Find the chord pitch so ``n_intervals`` steps land exactly on ``end_s``.

    Returns the pitch, or None if no pitch in a sensible band works.
    """
    if n_intervals < 1:
        return None
    span = end_s - start_s
    if span <= _EPS:
        return None
    # A span on one straight edge needs no search: chord == arc length.
    if poly.seg_index_at(start_s) == poly.seg_index_at(end_s - 1e-9):
        return span / n_intervals
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
    # Illinois false position: the residual is close to linear in the pitch,
    # so this converges in a handful of marches where plain bisection took
    # ~30. A trial that can't complete its steps (the sentinel) falls back to
    # a bisection step.
    side = 0
    for _ in range(80):
        if r_hi > -1e17 and r_lo != r_hi:
            mid = (lo * r_hi - hi * r_lo) / (r_hi - r_lo)
            if not lo < mid < hi:
                mid = 0.5 * (lo + hi)
        else:
            mid = 0.5 * (lo + hi)
        r = residual(mid)
        if abs(r) < tol:
            return mid
        if r > 0:
            lo, r_lo = mid, r
            if side > 0 and r_hi > -1e17:
                r_hi *= 0.5
            side = 1
        else:
            hi, r_hi = mid, r
            if side < 0:
                r_lo *= 0.5
            side = -1
        if hi - lo < 1e-15:
            break
    # Converged only if the band closed on the end. A span whose chord from
    # the start rises then falls (it runs out to a tip and back) has no
    # monotonic residual; the band then shrinks onto the tip, not the end,
    # and handing that pitch back put a stray hole on the tip.
    mid = 0.5 * (lo + hi)
    if abs(residual(mid)) < max(tol, 1e-3 * span):
        return mid
    return None


def _best_fit_span(poly: Polyline, target_pitch: float, start_s: float,
                   end_s: float, max_dev: float, parity: Optional[str] = None,
                   symmetric_fallback: bool = False, memo: Optional[dict] = None):
    """Choose an integer hole count for one span and return (pitch, n).

    Prefers a count whose pitch stays within ``max_dev`` of the target. If none
    does and ``symmetric_fallback`` is set (every span of a closed outline), the
    closest-pitch count is used anyway rather than returning ``None`` -- a corner
    arc must place its holes *symmetrically* even when no count hits the pitch
    exactly, so we never drop back to a lopsided plain march there. ``parity``
    ('odd') forces an even pair to straddle a bare apex.

    ``memo`` caches results for one stitch: ``auto`` fits the same spans for
    every corner style, each on its own rotation of the ring, so the key is
    the span's geometry (its end points and length), not its arc-lengths.
    """
    span = end_s - start_s
    if span <= _EPS:
        return target_pitch, 0
    if memo is not None:
        pa, pb = poly.point_at(start_s), poly.point_at(end_s)
        key = (round(pa.x, 9), round(pa.y, 9), round(pb.x, 9), round(pb.y, 9),
               round(span, 9), target_pitch, max_dev, parity,
               symmetric_fallback)
        if key not in memo:
            memo[key] = _best_fit_span(poly, target_pitch, start_s, end_s,
                                       max_dev, parity, symmetric_fallback)
        return memo[key]
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
        # Nothing fits the iron: take the closest count, but never one that
        # squeezes holes under the minimum spacing while a sparser count
        # exists -- holes a bit far apart just look sparse; holes much too
        # close bridge and tear, and on a small straddled arc read as a
        # cluster in the corner.
        floor = _MIN_FIT_PITCH * target_pitch
        roomy = [c for c in allc if c[1] >= floor]
        best = min(roomy or allc)
        return best[1], best[2]
    return target_pitch, None  # caller falls back to plain marching


_COS_45 = math.cos(math.radians(45.0))


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
    if max_dev <= tol:
        return False
    # A flattened fillet turns a little at every vertex; a span that turns
    # sharply at ONE vertex has a real corner inside it instead (a corner
    # anchor projected onto the wrong edge of a thin inset arm) and must not
    # be given the symmetric rounded-corner treatment.
    pts, cum = poly.points, poly.cum
    lo, hi = a % total, (a % total) + span_len
    ring = (pts[0] - pts[-1]).length() < 1e-9
    for i in range(0 if ring else 1, len(pts) - 1):
        s = cum[i]
        if not (lo < s < hi or lo < s + total < hi):
            continue
        d1 = pts[i] - pts[i - 1 if i else -2]   # the seam's predecessor
        d2 = pts[i + 1] - pts[i]
        l1, l2 = d1.length(), d2.length()
        if l1 > _EPS and l2 > _EPS and d1.dot(d2) < l1 * l2 * _COS_45:
            return False
    return True


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

    Returns ``(forced, straddle, key)``: the set of arc-lengths that must be
    holes, the set of ``(a, b)`` arc spans whose fit must be forced odd, and the
    subset of ``forced`` that marks a corner itself (sharp corners and arc
    apexes, as opposed to tangent points) -- see ``_space_anchors``.
    """
    total = poly.length
    p = settings.pitch_mm
    if style is None:
        style = getattr(settings, "corner_style", "auto")
    if style == "auto":
        style = "tangent"          # concrete default (see _fit_corner_style)
    # Merge near-coincident anchors (e.g. a corner whose inset collapsed the arc
    # to a point when the inset >= the radius) so it becomes one hole, not two
    # stacked on top of each other. The hole goes in the MIDDLE of the cluster:
    # a collapsed fillet's two tangent points straddle the sharp inset corner,
    # and keeping either one put the hole just past the corner, where the
    # chord march coming round the other side then overshot by a whole pitch.
    merge = min(0.3, 0.1 * p)
    cor_sorted = sorted({c % total for c in cor})
    clusters: List[List[float]] = []
    for c in cor_sorted:
        if not clusters or (c - clusters[-1][-1]) > merge:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    if (len(clusters) >= 2
            and (total - clusters[-1][-1] + clusters[0][0]) <= merge):
        # last cluster wraps onto the first -- unroll it past ``total``
        clusters[0] = clusters.pop() + [c + total for c in clusters[0]]
    cor_set = sorted(((cl[0] + cl[-1]) / 2.0) % total for cl in clusters)
    forced: set = set()
    straddle: set = set()
    key: set = set()
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
            key.add(apex)
        elif style == "straddle":
            if chord_tt >= p * 0.98:
                forced |= {a, b}
                # Straddling needs an odd number of steps round the arc. On
                # an arc between ~1.1 and ~2.6 pitches long the only odd
                # counts are 1 (a huge gap) or 3 (holes crammed to ~60 % of
                # the pitch): neither is sewable, so the arc is fitted
                # freely instead -- the apex gets a hole, the pitch holds.
                if _straddle_step(arc_len, chord_tt, p) >= _MIN_ANCHOR_SEP * p:
                    straddle.add((a, b))
            else:
                forced.add(apex)          # too tight to straddle -> apex hole
                key.add(apex)
        else:  # tangent (the anchor-both-tangent-points strategy)
            if chord_tt >= p * 0.98:
                forced |= {a, b}          # arc fitted on its own (symmetric)
            else:
                forced.add(apex)          # tight corner -> single apex hole
                key.add(apex)
    for c in cor_set:
        if c not in used:                 # a genuine sharp corner
            forced.add(c)
            key.add(c)
    return forced, straddle, key


# Two forced holes may never sit closer than this fraction of the pitch. Corner
# planning alone keeps its anchors >= ~0.95 pitch apart, but anchors from
# *neighbouring* features can still land on top of each other: two fillets
# joined by a 1.5 mm straight, a symmetry-axis crossing next to a corner, or an
# imported curve whose every flattened vertex counts as a sharp corner. Each
# such anchor used to force its own hole, packing a cluster into the corner.
_MIN_ANCHOR_SEP = 0.75
# A span fitted with no count inside the iron's tolerance never squeezes its
# holes closer than this fraction of the pitch while a sparser count exists --
# the same three-quarters the guide promises for corner holes.
_MIN_FIT_PITCH = 0.75


def _arc_sweep(chord: float, arc_len: float) -> float:
    """Sweep angle (radians) of a circular arc from its chord and length:
    solves sin(t/2)/(t/2) = chord/arc_len by bisection."""
    if arc_len <= _EPS or chord >= arc_len * (1 - 1e-12):
        return 0.0
    ratio = max(0.0, chord / arc_len)
    lo, hi = 1e-9, 2 * math.pi - 1e-9
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if math.sin(mid / 2) / (mid / 2) > ratio:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _straddle_step(arc_len: float, chord: float, pitch: float) -> float:
    """The chord step the fitter will use when it is made to put an ODD number
    of steps round a corner arc (so a pair straddles a bare apex): the odd
    count nearest the pitch, as ``_best_fit_span`` chooses it. Returns the
    step in mm -- below three-quarters of the pitch the holes would cram."""
    if arc_len <= _EPS or pitch <= _EPS:
        return 0.0
    sweep = _arc_sweep(chord, arc_len)
    radius = arc_len / sweep if sweep > 1e-9 else 0.0

    def step(n: int) -> float:
        if radius > 0:
            return 2.0 * radius * math.sin(sweep / (2.0 * n))
        return arc_len / n

    n0 = max(1, int(round(arc_len / pitch)))
    cands = [n for n in range(max(1, n0 - 3), n0 + 4) if n % 2 == 1]
    best = min(cands, key=lambda n: abs(step(n) - pitch))
    return step(best)


def _space_anchors(anchors, total: float, closed: bool, min_sep: float,
                   rank) -> List[float]:
    """Thin ``anchors`` (arc-lengths) so no two are closer than ``min_sep``.

    Anchors are grouped into runs whose consecutive gaps are all < ``min_sep``;
    each run keeps its most important members (lowest ``rank(s)``: 0 must stay,
    e.g. an open path's ends or a symmetry crossing; 1 a corner proper, 1.5 a
    reflex one; 2 a tangent point), nearest the run's centre first, skipping any too close to
    one already kept. A run that is just two tangent points (say the ends of
    a short straight between two fillets) is replaced by their midpoint, so a
    mirror-image shape stays symmetric. Runs never interact, so isolated
    anchors are untouched.
    """
    pts = sorted(set(anchors))
    if len(pts) < 2 or min_sep <= 0:
        return pts

    def gap(a, b):                    # forward distance a -> b
        return (b - a) % total if closed else b - a

    m = len(pts)
    start = 0
    if closed:
        # open the ring at its widest gap so no run straddles the seam (if
        # even that is < min_sep the whole ring is one run, which is fine)
        start = max(range(m), key=lambda i: gap(pts[i - 1], pts[i]))
    order = pts[start:] + pts[:start]
    runs, run = [], [order[0]]
    for s in order[1:]:
        if gap(run[-1], s) < min_sep:
            run.append(s)
        else:
            runs.append(run)
            run = [s]
    runs.append(run)

    def dist(a, b):
        d = abs(a - b)
        return min(d, total - d) if closed else d

    out: List[float] = []
    for run in runs:
        if len(run) == 1:
            out.append(run[0])
            continue
        centre = (run[0] + gap(run[0], run[-1]) / 2.0)
        if closed:
            centre %= total
        cand = sorted(run, key=lambda s: (rank(s), dist(s, centre)))
        if (len(run) == 2 and rank(run[0]) == rank(run[1]) == 2
                and len(runs) > 1):
            cand = [centre] + cand    # mirror tangent pair -> their midpoint
        kept: List[float] = []
        for s in cand:
            if rank(s) == 0 or all(dist(s, k) >= min_sep for k in kept):
                kept.append(s)
        out.extend(kept)
    return sorted(s % total if closed else s for s in out)


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
    # How ``holes`` is laid out, so readouts and estimates can walk the seam
    # the way the needle does:
    #   rows  -- 1, or 2 when every hole comes as an aligned pair (outer,
    #            inner) across the two rows of a saddle stitch;
    #   runs  -- start indices of each separately sewn run when the piece is
    #            stitched in pieces (an unsewn edge, a hardware keep-out).
    #            None = one continuous run.
    rows: int = 1
    runs: Optional[List[int]] = None

    @property
    def points(self) -> List[Vec2]:
        return [h.point for h in self.holes]

    @property
    def count(self) -> int:
        return len(self.holes)

    def sequences(self, row: Optional[int] = None) -> List[List[int]]:
        """Index lists of the holes a needle visits in order: one list per
        run and per row (``row`` 0/1 picks one row of a double row). Gaps
        between runs and across the rows are never a stitch."""
        n = len(self.holes)
        step = max(1, int(self.rows or 1))
        bounds = sorted({0, *(self.runs or [])} | {n})
        bounds = [b for b in bounds if 0 <= b <= n]
        out = []
        for a, b in zip(bounds, bounds[1:]):
            for r in range(step):
                if row is not None and r != row:
                    continue
                seq = list(range(a + r, b, step))
                if len(seq) >= 2:
                    out.append(seq)
        return out

    def chord_spacings(self) -> List[float]:
        """Straight-line distance between consecutive holes of each stitch
        run, per row -- including the closing gap of a closed loop (the one
        an unfitted march leaves short)."""
        pts = self.points
        gaps: List[float] = []
        for seq in self.sequences():
            gaps += [distance(pts[i], pts[j]) for i, j in zip(seq, seq[1:])]
            if self.closed and len(seq) > 2:
                gaps.append(distance(pts[seq[-1]], pts[seq[0]]))
        return gaps

    def seam_length(self) -> float:
        """Length of the seam along one row, summed over its runs."""
        pts = self.points
        total = 0.0
        for seq in self.sequences(row=0):
            total += sum(distance(pts[i], pts[j]) for i, j in zip(seq, seq[1:]))
            if self.closed and len(seq) > 2:
                total += distance(pts[seq[-1]], pts[seq[0]])
        return total

    def worst_deviation(self, pitch: float) -> float:
        """Largest fractional error of any chord gap against ``pitch``."""
        gaps = self.chord_spacings()
        if not gaps or pitch <= 0:
            return 0.0
        return max(abs(g - pitch) / pitch for g in gaps)


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
        p_eff, n = _best_fit_span(poly, pitch, a, b, max_dev,
                                  symmetric_fallback=True)
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
    # Snap the final hole exactly onto the span end for a clean corner -- but
    # only when it is already nearly there. If the march stopped short (the
    # fitted pitch just failed to reach), overwriting would silently delete
    # that hole (or, after zero steps, the span's START hole), leaving a gap of
    # two pitches; append the end instead.
    if len(result) > 1 and end_s - result[-1] < 0.5 * pitch:
        result[-1] = end_s
    else:
        result.append(end_s)
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


def edge_runs(n: int, edges, closed: bool):
    """Maximal runs of kept edges of an ``n``-segment path under the per-edge
    mask ``edges`` (True = stitched), as ``(first, last)`` segment indices;
    a run of a closed path may wrap (last < first). ``None`` means the mask
    keeps everything (or is unusable)."""
    if edges is None or len(edges) != n or all(edges):
        return None
    if not any(edges):
        return []
    runs = []
    i = 0
    if closed:
        # start scanning just after a skipped edge so a wrapping run is one run
        i = next(k for k in range(n) if not edges[k]) + 1
    seen = 0
    while seen < n:
        k = i % n
        if edges[k]:
            start = k
            while seen < n and edges[i % n]:
                i += 1
                seen += 1
            runs.append((start, (i - 1) % n))
        else:
            i += 1
            seen += 1
        if not closed and i >= n:
            break
    return runs


def holes_for_shape(shape, doc=None) -> StitchResult:
    """World-space holes for a shape, computed on its LOCAL outline then
    transformed. Baked holes are transformed directly. Used by export and the
    symmetry check so hole placement is consistent regardless of rotation.
    With ``doc``, the stitching keeps clear of the document's hardware
    keep-outs (see ``hardware.keepouts_for_shape``)."""
    t = shape.transform
    baked = getattr(shape, "baked_holes", None)
    if baked:
        return StitchResult(holes=[Hole(t.apply(h.point), t.apply_dir(h.tangent))
                                   for h in baked])
    st = getattr(shape, "stitch", None)
    if not (st and st.enabled):
        return StitchResult()
    keep = None
    if doc is not None:
        from .hardware import keepouts_for_shape
        keep = keepouts_for_shape(shape, doc) or None
    res = stitch_shape_path(shape.local_path(), st,
                            getattr(shape, "stitch_edges", None), keep)
    res.holes = [Hole(t.apply(h.point), t.apply_dir(h.tangent)) for h in res.holes]
    return res


def stitch_shape_path(path: Path, settings, edges=None,
                      keepouts=None) -> StitchResult:
    """Holes for a shape's own ``Path`` (local coordinates). Prefer this over
    ``stitch_polyline`` whenever the path is at hand: the stitch line is then
    inset from the true lines and arcs (exact straights, exact corner radii)
    and holes on arcs sit on the true circle -- see ``stitch_polyline``.

    ``edges`` -- one flag per ``path.segments`` entry, False for an edge that
    isn't sewn (a pocket's open mouth). The stitch line is still the inset
    of the whole outline; it is just cut where a skipped edge begins and
    ends, and each remaining run is sewn as its own open seam, ending with
    a hole at the inset corner.

    ``keepouts`` -- ``[(local centre, radius)]`` circles (hardware) the stitch
    line is cut out of: where it passes through one, the seam stops at the
    circle's edge and starts again on the far side."""
    return stitch_polyline([Vec2(p.x, p.y) for p in path.flatten()],
                           list(path.corner_points), path.closed, settings,
                           path=path, edges=edges, keepouts=keepouts)


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


# More holes than this on one stitch line (385 m at a 3.85 mm iron) means the
# geometry has gone wrong; return none rather than hang the app fitting them.
_MAX_HOLES = 100_000


# Fitting a big outline is pure but not cheap, and the SAME outline is fitted
# again on every rebuild (open, undo, redo, layer toggle...). Memoise on the
# exact inputs: items compute holes on LOCAL geometry, so moving a shape --
# or 50 copies of it -- reuses one cached fit. ~512 entries of a few hundred
# holes each is well under a megabyte.
_CACHE_MAX = 512
_stitch_cache: "OrderedDict" = None  # created lazily below


def _cache_key(points, corner_points, closed, settings, exact=False,
               edges=None, keepouts=None):
    return (
        bool(exact),
        tuple(bool(e) for e in edges) if edges is not None else None,
        tuple((round(c.x, 6), round(c.y, 6), round(r, 6))
              for c, r in keepouts) if keepouts else None,
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
                    settings, path: Optional[Path] = None,
                    edges=None, keepouts=None) -> StitchResult:
    """Distribute holes on a world-space polyline using ``StitchSettings``.

    ``corner_points`` are geometric points where a hole must be forced (e.g.
    sharp corners). For closed outlines the stitch line is inset from the edge
    by ``settings.inset`` first; corners are then projected onto the inset ring,
    so a sharp corner still gets a hole at the *inset* corner. This is the
    function the CAD document / export layer calls. Results are memoised
    (see ``_cache_key``); callers get an independent ``StitchResult`` whose
    ``holes``/``pitches`` lists are safe to replace.

    ``path`` -- the ``Path`` that ``points`` was flattened from, when known --
    makes the stitch line exact: it is inset segment by segment (a straight
    stays its true length, a corner arc keeps its true centre, radius r - inset
    and sweep), and every hole that lands on an arc of the stitch line is
    snapped onto the true circle rather than left on a flattening chord. A
    walked pricking iron then lands on the drawn line, at the drawn pitch.
    """
    global _stitch_cache
    if _stitch_cache is None:
        from collections import OrderedDict
        _stitch_cache = OrderedDict()
    key = _cache_key(points, corner_points, closed, settings, path is not None,
                     edges if path is not None else None, keepouts)
    hit = _stitch_cache.get(key)
    if hit is not None:
        _stitch_cache.move_to_end(key)
        return _copy_result(hit)
    res = _stitch_polyline_impl(points, corner_points, closed, settings, path,
                                edges, keepouts)
    _stitch_cache[key] = _copy_result(res)
    while len(_stitch_cache) > _CACHE_MAX:
        _stitch_cache.popitem(last=False)
    return res


def _stitch_polyline_impl(points: List[Vec2], corner_points: List[Vec2],
                          closed: bool, settings,
                          path: Optional[Path] = None,
                          edges=None, keepouts=None) -> StitchResult:
    from .offset import (inset_with_corners, inset_path_with_corners,
                         inset_path_images)

    pts = list(points)
    if closed and len(pts) >= 2 and (pts[0] - pts[-1]).length() > 1e-9:
        pts.append(pts[0])       # a closed ring must include its closing edge
    corner_points = list(corner_points or [])
    arcs: list = []
    inset = 0.0

    if path is not None:
        inset = settings.inset if (closed and settings.inset) else 0.0
        pts, corner_points, arcs = inset_path_with_corners(
            path, max(0.0, inset), corner_points)
        if closed and len(pts) < 3:   # piece narrower than twice the inset
            return StitchResult(closed=closed)
    elif closed and settings.inset and settings.inset > 0:
        # the corners come back as the inset ring's own corners (see
        # inset_with_corners), so they sit exactly on the stitch line
        pts, corner_points = inset_with_corners(pts, settings.inset,
                                                corner_points)
        if len(pts) < 3:          # piece narrower than twice the inset
            return StitchResult(closed=closed)

    runs = (edge_runs(len(path.segments), edges, closed)
            if path is not None else None)
    poly0 = Polyline(pts)
    excl = (_keepout_intervals(poly0, keepouts, closed) if keepouts else [])
    if runs is not None or excl:
        # Some of the stitch line isn't sewn: edges the user left unsewn, and
        # the stretches inside hardware keep-outs. Cut the line there and sew
        # every remaining run as its own open seam (ending with a hole at
        # the cut).
        if runs == []:
            return StitchResult(closed=False)
        poly = poly0
        total = poly.length
        if total <= _EPS:
            return StitchResult(closed=False)
        spans = []                              # unwrapped (s0, s1), s1 > s0
        if runs:
            segs = path.segments
            verts = []
            for a, b in runs:
                verts += [segs[a].start(), segs[b].end()]
            images = inset_path_images(path, max(0.0, inset), verts)
            for k, (a, b) in enumerate(runs):
                ia = images[2 * k] if images[2 * k] is not None else verts[2 * k]
                ib = (images[2 * k + 1] if images[2 * k + 1] is not None
                      else verts[2 * k + 1])
                s0, s1 = poly.nearest_arclength(ia), poly.nearest_arclength(ib)
                if closed and (s1 <= s0 + 1e-6):
                    s1 += total                 # wraps (or is the whole ring)
                spans.append((s0, s1))
        elif closed:
            # the whole ring, opened where the first keep-out ends
            start = excl[0][1] % total
            spans.append((start, start + total))
        else:
            spans.append((0.0, total))
        kept = _subtract_intervals(spans, excl, total if closed else None)
        if not kept and not excl and closed and runs:
            kept = spans
        cor_s = [(poly.nearest_arclength(cp), cp) for cp in corner_points]
        out = StitchResult(closed=False)
        starts: List[int] = []
        for s0, s1 in kept:
            if s1 - s0 <= 1e-6:
                continue
            whole = closed and not excl and abs((s1 - s0) - total) < 1e-6
            if whole:
                res = _distribute(poly, corner_points, True, settings, arcs,
                                  total)
                return _apply_rows(res, settings)
            run_pts = _sub_polyline(poly, s0 % total if closed else s0,
                                    s1 % total if closed else min(s1, total),
                                    closed)
            if len(run_pts) < 2:
                continue
            rpoly = Polyline(run_pts)
            run_corners = [cp for s, cp in cor_s
                           if _within_run(s, s0 % total if closed else s0,
                                          s1 % total if closed else s1,
                                          total, closed)]
            res = _distribute(rpoly, run_corners, False, settings, arcs,
                              rpoly.length)
            if res.holes:
                starts.append(len(out.holes))
            out.holes += res.holes
            out.pitches += res.pitches
        if len(starts) > 1:
            out.runs = starts
        return _apply_rows(out, settings)

    if closed and getattr(settings, "start_offset", 0.0):
        pts = _rotate_closed_ring(pts, settings.start_offset)

    poly = Polyline(pts)
    if poly.length <= _EPS:
        return StitchResult(closed=closed)
    return _apply_rows(_distribute(poly, corner_points, closed, settings,
                                   arcs, poly.length), settings)


def _keepout_intervals(poly: "Polyline", keepouts, closed: bool):
    """Arc-length intervals ``(a, b)`` of ``poly`` lying inside any keep-out
    circle, merged, as plain numbers in [0, total] (an interval touching
    both ends of a closed ring is reported as two pieces)."""
    pts = poly.points
    cum = poly.cum
    raw = []
    for c, r in keepouts:
        inside = (pts[0] - c).length() < r
        start = 0.0 if inside else None
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            d = b - a
            L2 = d.length_sq()
            if L2 <= 1e-18:
                continue
            f = a - c
            qa, qb, qc = L2, 2.0 * f.dot(d), f.dot(f) - r * r
            disc = qb * qb - 4 * qa * qc
            if disc <= 0:
                continue
            root = math.sqrt(disc)
            for t in sorted(((-qb - root) / (2 * qa), (-qb + root) / (2 * qa))):
                if 0.0 < t < 1.0:
                    s = cum[i] + math.sqrt(L2) * t
                    if inside:
                        raw.append((start, s))
                        inside, start = False, None
                    else:
                        inside, start = True, s
        if inside:
            raw.append((start, cum[-1]))
    if not raw:
        return []
    raw.sort()
    merged = [list(raw[0])]
    for a, b in raw[1:]:
        if a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a > 1e-9]


def _subtract_intervals(spans, excl, total):
    """``spans`` (unwrapped, may run past ``total`` on a closed ring) minus
    the exclusion intervals (in [0, total], repeated a turn later when the
    ring wraps). Returns the leftover pieces, in order."""
    cuts = list(excl)
    if total is not None:
        cuts += [(a + total, b + total) for a, b in excl]
        cuts += [(a - total, b - total) for a, b in excl]
    out = []
    for s0, s1 in spans:
        pieces = [(s0, s1)]
        for a, b in cuts:
            nxt = []
            for p0, p1 in pieces:
                if b <= p0 or a >= p1:
                    nxt.append((p0, p1))
                    continue
                if a > p0:
                    nxt.append((p0, a))
                if b < p1:
                    nxt.append((b, p1))
            pieces = nxt
        out += pieces
    return out


def _within_run(s: float, s0: float, s1: float, total: float,
                closed: bool) -> bool:
    """Is arc-length ``s`` strictly inside the run s0 -> s1 (forward)?"""
    eps = 1e-6
    if closed and s1 <= s0 + eps:
        return s > s0 + eps or s < s1 - eps          # the run wraps
    return s0 + eps < s < s1 - eps


def _sub_polyline(poly: "Polyline", s0: float, s1: float,
                  closed: bool) -> List[Vec2]:
    """The vertices of ``poly`` from arc-length ``s0`` forward to ``s1``
    (wrapping past the end of a closed ring), ends included."""
    total = poly.length
    if closed and s1 <= s0 + 1e-9:
        s1 += total
    out = [poly.point_at(s0 % total if closed else s0)]
    cum = poly.cum
    n = len(poly.points)
    k = 0
    while True:                           # vertices strictly inside (s0, s1)
        i = k % (n - 1) if closed else k
        if not closed and i >= n:
            break
        s = cum[i] + total * (k // (n - 1) if closed else 0)
        if s >= s1 - 1e-9:
            break
        if s > s0 + 1e-9:
            out.append(poly.points[i])
        k += 1
        if k > 4 * n:
            break
    end = poly.point_at(s1 % total if closed else min(s1, total))
    if (end - out[-1]).length() > 1e-9:
        out.append(end)
    return out


def _distribute(poly: "Polyline", corner_points: List[Vec2], closed: bool,
                settings, arcs, total: float) -> StitchResult:
    """Place holes along ``poly`` (already the stitch line): the fitting
    proper, snapped onto ``arcs``. Rows are the caller's."""

    def done(res: StitchResult) -> StitchResult:
        return _snap_to_arcs(res, arcs)

    if total > _MAX_HOLES * settings.pitch_mm:
        # never a real seam: a runaway stitch line (see offset.MITER_LIMIT)
        # would otherwise be marched hole by hole with the UI frozen
        return StitchResult(closed=closed)

    fit = settings.fit
    if fit == "auto":
        fit = "closed" if closed else "endpoints"

    if settings.mode == "arclength":
        positions = march_arclength(poly, settings.pitch_mm)
        return done(_result_from_positions(
            poly, positions, [settings.pitch_mm], closed))

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
        memo: dict = {}                     # span fits shared by the styles
        for style in ("tangent", "midpoint", "straddle"):
            res = _snap_to_arcs(_march_corner_style(
                poly, cor, settings, style, closed, fit, total, symmetric,
                axis_forced, memo), arcs)
            bad = _gap_badness(res, settings.pitch_mm)
            if best_bad is None or bad < best_bad:
                best, best_bad = res, bad
        return best                           # already snapped

    res = _march_corner_style(poly, cor, settings, want, closed, fit,
                              total, symmetric, axis_forced)
    return done(res)


def _snap_to_arcs(res: StitchResult, arcs) -> StitchResult:
    """Move holes that lie on a flattening chord of one of the stitch line's
    ``arcs`` (centre, radius, start angle, sweep) out onto the true circle,
    with the circle's tangent. A chord sits inside its arc by at most the
    flattening sagitta, so only holes that close to an arc, within its sweep,
    are touched -- straights and everything else stay put."""
    if not arcs or not res.holes:
        return res
    from .offset import ARC_FLATNESS
    tol = 2.0 * ARC_FLATNESS + 1e-9
    out = []
    for h in res.holes:
        p = h.point
        for c, r, a0, sweep in arcs:
            v = p - c
            d = v.length()
            if d <= _EPS or not (r - tol <= d <= r + 1e-7):
                continue
            ang = math.atan2(v.y, v.x)
            if sweep > 0:
                inside = (ang - a0) % (2 * math.pi) <= sweep + 1e-9
            else:
                inside = (a0 - ang) % (2 * math.pi) <= -sweep + 1e-9
            if not inside:
                continue
            u = v * (1.0 / d)
            t = Vec2(-u.y, u.x)
            if t.dot(h.tangent) < 0:
                t = t * -1.0
            h = Hole(c + u * r, t)
            break
        out.append(h)
    return StitchResult(holes=out, pitches=res.pitches, closed=res.closed)


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
                        axis_forced: set,
                        memo: Optional[dict] = None) -> "StitchResult":
    """March the holes for one concrete corner ``style`` (tangent / midpoint /
    straddle). Extracted so ``auto`` can try each and pick the least-deviation
    result. Works on a private rotation of ``poly`` so candidates don't
    interfere."""
    straddle: set = set()
    min_sep = _MIN_ANCHOR_SEP * settings.pitch_mm
    if closed:
        # Plan corner holes: single apex hole on tight radii (no cramming),
        # a fitted arc span on generous ones. See _plan_corners.
        forced, straddle, key = _plan_corners(poly, cor, settings, style)
        forced |= axis_forced
        orient = 1.0 if signed_area(poly.points) > 0 else -1.0
        step = min(0.05, 0.01 * settings.pitch_mm)

        def rank(s):
            if s in axis_forced:
                return 0
            if s not in key:
                return 2                  # a tangent point
            # of two crowded corners keep the convex one: it is what reads
            # as the shape's corner, and a span tip-to-tip fits cleanly where
            # one notch-to-notch runs out to the tip and back
            t0 = poly.tangent_at((s - step) % total)
            t1 = poly.tangent_at((s + step) % total)
            return 1 if t0.cross(t1) * orient >= 0 else 1.5

        forced = set(_space_anchors(forced, total, True, min_sep, rank))
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
        # A stitch ring too small for two holes the minimum spacing apart
        # (a tiny piece whose inset left a ring of a millimetre or two)
        # takes a single hole rather than a knot of them.
        xs = [q.x for q in poly.points]
        ys = [q.y for q in poly.points]
        if math.hypot(max(xs) - min(xs), max(ys) - min(ys)) < min_sep:
            return _result_from_positions(poly, [0.0], [settings.pitch_mm],
                                          closed)
        corners_for_anchors = sorted(forced)
    else:
        # the path's two ends always anchor, so they outrank any corner
        ends = {0.0, total}
        corners_for_anchors = [s for s in _space_anchors(
            set(cor) | ends, total, False, min_sep,
            lambda s: 0 if s in ends else 1) if s not in ends]

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
        # When no count fits the pitch, take the nearest one -- on an open
        # seam too: marching at the plain pitch there and then dropping the
        # last marched hole left an end gap of up to ~1.7 pitches.
        p_eff, n = _best_fit_span(poly, settings.pitch_mm, a, b,
                                  settings.max_dev, parity,
                                  symmetric_fallback=True, memo=memo)
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
    result.rows = 2
    if result.runs:
        result.runs = [2 * r for r in result.runs]
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

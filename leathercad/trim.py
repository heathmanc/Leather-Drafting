"""Trim: remove the span of an outline between the two intersections that
bracket a picked point -- the classic 2D-sketch trim (Fusion 360 / LightBurn).

It works on the same segment representation the app already uses for
break-apart: a list of ``(kind, [world points])`` where a line is
``("line", [a, b])``, an arc is ``("arc", [a, mid, b])`` (``mid`` is a point
the arc passes through) and a cubic bezier ``("bezier", [a, c1, c2, b])``. Curve type is preserved -- trimming an arc yields arcs.

The picked entity is intersected against a set of *cutter* polylines (every
other outline, flattened). The intersections split the entity into cells; the
cell containing the pick is removed and the remaining cells are returned as new
chains (an open path, or two of them if the entity was open and cut in the
middle). Returns ``None`` to signal "delete the whole entity" (a closed loop
with nothing crossing it, or an open one with no bracketing cut).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2
from .shapes import arc_through

_EPS = 1e-9
# How close (mm) a cut line's loose end must come to an outline to cut it.
TOUCH_TOL = 0.05

Segment = Tuple[str, List[Vec2]]


# ---------------------------------------------------------------------------
# One primitive piece of the picked outline (a line or a circular arc)
# ---------------------------------------------------------------------------
class _Piece:
    bez = None                   # a _BezPiece's control points

    def __init__(self, kind: str, a: Vec2, b: Vec2, mid: Optional[Vec2] = None):
        self.a = a
        self.b = b
        arc = arc_through(a, mid, b) if (kind == "arc" and mid is not None) else None
        if arc is None:
            self.center = None
            self.length = (b - a).length()
        else:
            self.center, self.r, self.ang0, self.ang1, self.ccw = arc
            self.sweep = _signed_sweep(self.ang0, self.ang1, self.ccw)
            self.length = abs(self.sweep) * self.r

    def point_at(self, f: float) -> Vec2:
        f = max(0.0, min(1.0, f))
        if self.center is None:
            return self.a.lerp(self.b, f)
        ang = self.ang0 + self.sweep * f
        return Vec2(self.center.x + self.r * math.cos(ang),
                    self.center.y + self.r * math.sin(ang))

    def frac_of(self, p: Vec2) -> float:
        """Approximate fraction in [0, 1] of a point lying on the piece."""
        if self.center is None:
            ab = self.b - self.a
            d = ab.length_sq()
            if d <= _EPS:
                return 0.0
            return max(0.0, min(1.0, (p - self.a).dot(ab) / d))
        ang = math.atan2(p.y - self.center.y, p.x - self.center.x)
        rel = _frac_along(ang - self.ang0, self.sweep)
        return max(0.0, min(1.0, rel))

    def flatten(self, flatness: float = 0.05) -> List[Vec2]:
        if self.center is None:
            return [self.a, self.b]
        ratio = max(-1.0, min(1.0, 1.0 - flatness / max(self.r, 1e-6)))
        step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi
        n = max(2, int(math.ceil(abs(self.sweep) / max(step, 1e-3))) + 1)
        return [self.point_at(i / (n - 1)) for i in range(n)]

    def sub(self, f0: float, f1: float) -> Segment:
        p0, p1 = self.point_at(f0), self.point_at(f1)
        if self.center is None:
            return ("line", [p0, p1])
        return ("arc", [p0, self.point_at(0.5 * (f0 + f1)), p1])


class _BezPiece(_Piece):
    """A cubic bezier piece. Fractions are by arc length, through a fine
    sample table; ``sub`` splits the curve exactly (de Casteljau), so a span
    the trim keeps whole comes back with its very own control points."""
    _N = 128

    def __init__(self, a: Vec2, c1: Vec2, c2: Vec2, b: Vec2):
        self.a, self.b = a, b
        self.center = None
        self.bez = (a, c1, c2, b)
        self._pts = [self._at(i / self._N) for i in range(self._N + 1)]
        self._cum = [0.0]
        for i in range(self._N):
            self._cum.append(self._cum[-1]
                             + (self._pts[i + 1] - self._pts[i]).length())
        self.length = self._cum[-1]

    def _at(self, t: float) -> Vec2:
        a, c1, c2, b = self.bez
        u = 1.0 - t
        return (a * (u * u * u) + c1 * (3 * u * u * t)
                + c2 * (3 * u * t * t) + b * (t * t * t))

    def _t_of(self, f: float) -> float:
        s = max(0.0, min(1.0, f)) * self.length
        lo, hi = 0, self._N
        while hi - lo > 1:
            m = (lo + hi) // 2
            if self._cum[m] <= s:
                lo = m
            else:
                hi = m
        seg = self._cum[lo + 1] - self._cum[lo]
        k = (s - self._cum[lo]) / seg if seg > _EPS else 0.0
        return (lo + k) / self._N

    def point_at(self, f: float) -> Vec2:
        return self._at(self._t_of(f))

    def frac_of(self, p: Vec2) -> float:
        best, bs = float("inf"), 0.0
        for i in range(self._N):
            a, b = self._pts[i], self._pts[i + 1]
            ab = b - a
            d2 = ab.length_sq()
            k = 0.0 if d2 <= _EPS else max(0.0, min(1.0, (p - a).dot(ab) / d2))
            d = (a + ab * k - p).length()
            if d < best:
                best, bs = d, self._cum[i] + k * ab.length()
        return bs / self.length if self.length > _EPS else 0.0

    def flatten(self, flatness: float = 0.05) -> List[Vec2]:
        return list(self._pts)

    def sub(self, f0: float, f1: float) -> Segment:
        t0, t1 = self._t_of(f0), self._t_of(f1)
        a, c1, c2, b = self.bez
        # head [0, t1] ...
        p01, p12, p23 = a.lerp(c1, t1), c1.lerp(c2, t1), c2.lerp(b, t1)
        p012, p123 = p01.lerp(p12, t1), p12.lerp(p23, t1)
        h = (a, p01, p012, p012.lerp(p123, t1))
        # ... then its tail from t0 (rescaled into the head's own range)
        u = t0 / t1 if t1 > _EPS else 0.0
        q01, q12, q23 = h[0].lerp(h[1], u), h[1].lerp(h[2], u), h[2].lerp(h[3], u)
        q012, q123 = q01.lerp(q12, u), q12.lerp(q23, u)
        return ("bezier", [q012.lerp(q123, u), q123, q23, h[3]])


def _piece_from(kind: str, pts: List[Vec2]) -> _Piece:
    if kind == "bezier" and len(pts) >= 4:
        return _BezPiece(pts[0], pts[1], pts[2], pts[-1])
    return _Piece(kind, pts[0], pts[-1],
                  pts[1] if kind == "arc" and len(pts) >= 3 else None)


def _signed_sweep(a0: float, a1: float, ccw: bool) -> float:
    two_pi = 2.0 * math.pi
    if ccw:
        d = (a1 - a0) % two_pi
    else:
        d = -((a0 - a1) % two_pi)
    if abs(d) < 1e-12:
        d = two_pi if ccw else -two_pi
    return d


def _frac_along(dang: float, sweep: float) -> float:
    two_pi = 2.0 * math.pi
    if sweep >= 0:
        dd = dang % two_pi
    else:
        dd = -((-dang) % two_pi)
    return dd / sweep if abs(sweep) > _EPS else 0.0


def _arc_hits(piece: "_Piece", c0: Vec2, c1: Vec2) -> List[float]:
    """Fractions along an arc piece where segment c0-c1 meets its circle."""
    d = c1 - c0
    f = c0 - piece.center
    a = d.dot(d)
    if a <= _EPS:
        return []
    b = 2.0 * f.dot(d)
    c = f.dot(f) - piece.r * piece.r
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return []
    root = math.sqrt(disc)
    out = []
    for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        if -1e-9 <= t <= 1.0 + 1e-9:
            x = c0 + d * t
            ang = math.atan2(x.y - piece.center.y, x.x - piece.center.x)
            frac = _frac_along(ang - piece.ang0, piece.sweep)
            if frac > 1.0 and abs(frac * abs(piece.sweep) - 2 * math.pi) < 1e-9:
                frac = 0.0                       # the start, wrapped round
            if -1e-9 <= frac <= 1.0 + 1e-9:
                out.append(min(1.0, max(0.0, frac)))
    return out


def _seg_intersect(a1: Vec2, a2: Vec2, b1: Vec2, b2: Vec2) -> Optional[Vec2]:
    """Proper intersection point of segments a1a2 and b1b2, or None."""
    r = a2 - a1
    s = b2 - b1
    rxs = r.cross(s)
    if abs(rxs) < 1e-12:
        return None
    qp = b1 - a1
    t = qp.cross(s) / rxs
    u = qp.cross(r) / rxs
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return a1 + r * t
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def intersection_arclengths(segments: Sequence[Segment],
                            cutters: Sequence[Sequence[Vec2]],
                            flatness: float = 0.05) -> Tuple[List[float], float]:
    """Arc-length positions along the entity where it crosses any cutter, plus
    the entity's total length. Exposed for previewing/highlighting."""
    pieces, cum, total = _build(segments)
    edges = _cutter_edges(cutters)
    hits: List[float] = []
    for pi, p in enumerate(pieces):
        if p.center is not None:
            # an arc: solve each cutter edge against the TRUE circle -- a
            # crossing found on the flattened arc and projected back by angle
            # landed up to ~0.9 mm short where a cutter grazes the arc
            for (c0, c1) in edges:
                for f in _arc_hits(p, c0, c1):
                    hits.append(cum[pi] + f * p.length)
            continue
        if p.bez is not None:
            pts, pc = p._pts, p._cum
            for j in range(len(pts) - 1):
                for (c0, c1) in edges:
                    x = _seg_intersect(pts[j], pts[j + 1], c0, c1)
                    if x is not None:
                        hits.append(cum[pi] + pc[j] + (x - pts[j]).length())
            continue
        for (c0, c1) in edges:
            x = _seg_intersect(p.a, p.b, c0, c1)
            if x is not None:
                hits.append(cum[pi] + p.frac_of(x) * p.length)
    # A cut line drawn to END on the outline counts as touching it even when
    # it stops a hair short: a field rounded to 4 decimals, or a move, can
    # leave it 0.0001 mm off, invisible yet enough to miss a strict crossing.
    for poly in cutters:
        if len(poly) < 2 or (poly[0] - poly[-1]).length() <= _EPS:
            continue                                  # closed: no loose ends
        for end in (poly[0], poly[-1]):
            s, d = _nearest(pieces, cum, end)
            if d <= TOUCH_TOL:
                hits.append(s)
    return _dedup(sorted(hits), max(0.02, 1e-4 * total)), total


def _cell_to_remove(pieces, cum, total, hits, click_s, closed):
    """(start_arclength, length) of the cell containing the pick, or None.
    For a closed entity with nothing crossing it, the whole loop is the cell."""
    if closed:
        if len(hits) < 2:
            return (0.0, total)                       # whole loop
        cells = [(hits[i], hits[i + 1]) for i in range(len(hits) - 1)]
        cells.append((hits[-1], hits[0] + total))     # the wrap cell
        for lo, hi in cells:
            cs = click_s if click_s >= lo - 1e-9 else click_s + total
            if lo - 1e-6 <= cs <= hi + 1e-6:
                return (lo % total, hi - lo)
        return None
    bounds = sorted(set([0.0, total] + hits))
    for i in range(len(bounds) - 1):
        if bounds[i] - 1e-6 <= click_s <= bounds[i + 1] + 1e-6:
            return (bounds[i], bounds[i + 1] - bounds[i])
    return None


def trim(segments: Sequence[Segment], cutters: Sequence[Sequence[Vec2]],
         click_point: Vec2, closed: bool,
         flatness: float = 0.05) -> Optional[List[List[Segment]]]:
    """Trim the cell under ``click_point``. Returns the surviving chains (each a
    list of ``(kind, pts)`` segments), or ``None`` to delete the whole entity."""
    pieces, cum, total = _build(segments)
    if total <= _EPS:
        return None
    hits, _ = intersection_arclengths(segments, cutters, flatness)
    click_s = _project(pieces, cum, click_point)
    cell = _cell_to_remove(pieces, cum, total, hits, click_s, closed)
    if cell is None:
        return None
    s0, clen = cell
    if closed:
        if clen >= total - 1e-6:
            return None                               # nothing brackets it
        chain = _walk(pieces, cum, total, (s0 + clen) % total, total - clen)
        return [chain] if chain else None
    chains: List[List[Segment]] = []
    if s0 > 1e-6:
        chains.append(_walk(pieces, cum, total, 0.0, s0))
    end = s0 + clen
    if total - end > 1e-6:
        chains.append(_walk(pieces, cum, total, end, total - end))
    chains = [c for c in chains if c]
    return chains or None


def removed_cell_polyline(segments: Sequence[Segment],
                          cutters: Sequence[Sequence[Vec2]],
                          click_point: Vec2, closed: bool,
                          flatness: float = 0.05) -> Optional[List[Vec2]]:
    """The portion that ``trim`` would remove for this pick, flattened to a
    polyline for highlighting. ``None`` if nothing is under the pick."""
    pieces, cum, total = _build(segments)
    if total <= _EPS:
        return None
    hits, _ = intersection_arclengths(segments, cutters, flatness)
    click_s = _project(pieces, cum, click_point)
    cell = _cell_to_remove(pieces, cum, total, hits, click_s, closed)
    if cell is None:
        return None
    removed = _walk(pieces, cum, total, cell[0], cell[1])
    out: List[Vec2] = []
    for kind, pts in removed:
        piece = _piece_from(kind, pts)
        fl = piece.flatten(flatness)
        if out and (out[-1] - fl[0]).length() < 1e-6:
            out.extend(fl[1:])
        else:
            out.extend(fl)
    return out or None


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _build(segments):
    pieces = []
    for kind, pts in segments:
        piece = _piece_from(kind, pts)
        if piece.length > _EPS:
            pieces.append(piece)
    cum = [0.0]
    for p in pieces:
        cum.append(cum[-1] + p.length)
    return pieces, cum, cum[-1]


def _cutter_edges(cutters):
    edges = []
    for poly in cutters:
        for i in range(len(poly) - 1):
            if (poly[i] - poly[i + 1]).length() > _EPS:
                edges.append((poly[i], poly[i + 1]))
    return edges


def _dedup(sorted_vals, tol):
    out = []
    for v in sorted_vals:
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _piece_at(cum, s):
    for i in range(len(cum) - 1):
        if cum[i] - 1e-9 <= s < cum[i + 1] - 1e-9:
            return i
    return len(cum) - 2


def _project(pieces, cum, target):
    """Global arc-length of the point on the entity nearest ``target``."""
    best_s, best_d = 0.0, float("inf")
    for pi, p in enumerate(pieces):
        fl = p.flatten()
        for j in range(len(fl) - 1):
            a, b = fl[j], fl[j + 1]
            ab = b - a
            d2 = ab.length_sq()
            t = 0.0 if d2 <= _EPS else max(0.0, min(1.0, (target - a).dot(ab) / d2))
            proj = a.lerp(b, t)
            d = (proj - target).length_sq()
            if d < best_d:
                best_d = d
                best_s = cum[pi] + p.frac_of(proj) * p.length
    return best_s


def _nearest(pieces, cum, target):
    """(arc-length, distance) of the point on the entity nearest ``target``,
    measured on the true lines and arcs rather than their flattening."""
    best_s, best_d = 0.0, float("inf")
    for pi, p in enumerate(pieces):
        if p.bez is not None:
            f = p.frac_of(target)
            q = p.point_at(f)
        elif p.center is None:
            f = p.frac_of(target)
            q = p.a.lerp(p.b, f)
        else:
            ang = math.atan2(target.y - p.center.y, target.x - p.center.x)
            f = _frac_along(ang - p.ang0, p.sweep)
            if f > 1.0:                       # off the sweep: nearer end
                f = 0.0 if (target - p.a).length() < (target - p.b).length() else 1.0
            q = p.point_at(f)
        d = (q - target).length()
        if d < best_d:
            best_d, best_s = d, cum[pi] + f * p.length
    return best_s, best_d


def _walk(pieces, cum, total, start, length):
    """Collect (kind, pts) sub-segments covering ``length`` forward from
    arc-length ``start``, wrapping past the seam if needed."""
    out: List[Segment] = []
    remaining = length
    s = start % total
    i = _piece_at(cum, s)
    guard = 0
    while remaining > 1e-7 and guard < 4 * len(pieces) + 4:
        guard += 1
        take = min(cum[i + 1] - s, remaining)
        if take > 1e-7:
            plen = pieces[i].length
            out.append(pieces[i].sub((s - cum[i]) / plen, (s - cum[i] + take) / plen))
            remaining -= take
        s += take
        if s >= total - 1e-7:
            s = 0.0
            i = 0
        else:
            i = _piece_at(cum, s)
    return out

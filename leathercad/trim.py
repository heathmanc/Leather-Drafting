"""Trim: remove the span of an outline between the two intersections that
bracket a picked point -- the classic 2D-sketch trim (Fusion 360 / LightBurn).

It works on the same segment representation the app already uses for
break-apart: a list of ``(kind, [world points])`` where a line is
``("line", [a, b])`` and an arc is ``("arc", [a, mid, b])`` (``mid`` is a point
the arc passes through). Curve type is preserved -- trimming an arc yields arcs.

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

Segment = Tuple[str, List[Vec2]]


# ---------------------------------------------------------------------------
# One primitive piece of the picked outline (a line or a circular arc)
# ---------------------------------------------------------------------------
class _Piece:
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
        fl = p.flatten(flatness)
        for j in range(len(fl) - 1):
            for (c0, c1) in edges:
                x = _seg_intersect(fl[j], fl[j + 1], c0, c1)
                if x is not None:
                    hits.append(cum[pi] + p.frac_of(x) * p.length)
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
        piece = _Piece(kind, pts[0], pts[-1],
                       pts[1] if kind == "arc" and len(pts) >= 3 else None)
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
        mid = pts[1] if (kind == "arc" and len(pts) >= 3) else None
        piece = _Piece(kind, pts[0], pts[-1], mid)
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

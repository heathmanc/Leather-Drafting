"""Polygon offsetting -- used to inset the stitch line from the cut edge.

Leather seams sit a few millimetres in from the edge (the "stitch line" or
"stitching margin"). Given a closed outline we offset it inward by that margin
using the standard edge-offset-and-reintersect method, which produces correct
mitered corners. Where a corner or ripple is tighter than the offset, that
method ties inside-out loops into the ring; those are trimmed away (see
``_remove_inverted_loops``) so the stitch line never doubles back on itself.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence

from .geometry import Vec2

_EPS = 1e-9


def signed_area(pts: Sequence[Vec2]) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        a += p.x * q.y - q.x * p.y
    return 0.5 * a


def _line_intersection(a0: Vec2, a1: Vec2, b0: Vec2, b1: Vec2) -> Optional[Vec2]:
    d1 = a1 - a0
    d2 = b1 - b0
    denom = d1.cross(d2)
    if abs(denom) < _EPS:
        return None
    t = (b0 - a0).cross(d2) / denom
    return a0 + d1 * t


def _unique_ring(points: Sequence[Vec2]) -> List[Vec2]:
    pts = list(points)
    if len(pts) >= 2 and (pts[0] - pts[-1]).length() < 1e-9:
        pts = pts[:-1]
    return pts


def _segment_hit(p0: Vec2, p1: Vec2, q0: Vec2, q1: Vec2):
    """``(t, u)`` where segments p0-p1 and q0-q1 cross (point = p0 + t*(p1-p0)),
    or None. Half-open on the far end so a crossing exactly on a shared vertex
    is reported once."""
    d1 = p1 - p0
    d2 = q1 - q0
    denom = d1.cross(d2)
    if abs(denom) < _EPS:
        return None
    w = q0 - p0
    t = w.cross(d2) / denom
    u = w.cross(d1) / denom
    if 0.0 <= t < 1.0 and 0.0 <= u < 1.0:
        return t, u
    return None


def _self_crossings(ring: List[Vec2]):
    """``[(i, t, j, u)]`` for every pair of non-adjacent edges i < j of the
    closed ``ring`` that cross, at parameter t along edge i and u along edge j.
    Edge k runs ring[k] -> ring[k+1]. Edges are bucketed on a grid sized to the
    typical edge length, so a ring of thousands of short flattened edges only
    tests near neighbours (the common no-crossing case stays cheap)."""
    n = len(ring)
    if n < 4:
        return []
    xs = [p.x for p in ring]
    ys = [p.y for p in ring]
    x0, y0 = min(xs), min(ys)
    span = max(max(xs) - x0, max(ys) - y0, _EPS)
    boxes = []
    for k in range(n):
        k1 = (k + 1) % n
        boxes.append((min(xs[k], xs[k1]) - x0, max(xs[k], xs[k1]) - x0,
                      min(ys[k], ys[k1]) - y0, max(ys[k], ys[k1]) - y0))
    lens = sorted(max(bx[1] - bx[0], bx[3] - bx[2]) for bx in boxes)
    cell = max(lens[n // 2] * 2.0, span / 1024.0, _EPS)

    def covered(c):
        return sum((int(bx[1] / c) - int(bx[0] / c) + 1)
                   * (int(bx[3] / c) - int(bx[2] / c) + 1) for bx in boxes)

    while covered(cell) > 16 * n:      # long diagonal edges: coarsen
        cell *= 2.0
    grid: dict = {}
    for k, (ax, bx_, ay, by) in enumerate(boxes):
        for ci in range(int(ax / cell), int(bx_ / cell) + 1):
            for cj in range(int(ay / cell), int(by / cell) + 1):
                grid.setdefault((ci, cj), []).append(k)
    seen = set()
    out = []
    for edges in grid.values():
        m = len(edges)
        for x in range(m):
            for y in range(x + 1, m):
                i, j = edges[x], edges[y]
                if i > j:
                    i, j = j, i
                if j - i <= 1 or (i == 0 and j == n - 1):
                    continue
                bi, bj = boxes[i], boxes[j]
                if (bi[1] < bj[0] or bj[1] < bi[0]
                        or bi[3] < bj[2] or bj[3] < bi[2]):
                    continue
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                hit = _segment_hit(ring[i], ring[(i + 1) % n],
                                   ring[j], ring[(j + 1) % n])
                if hit is not None:
                    out.append((i, hit[0], j, hit[1]))
    return out


class _Proximity:
    """"Is this point nearer than ``dist`` to the outline?" in ~O(1): the
    source edges are bucketed on a grid of ``dist``-sized cells, so a query
    only looks at the 3x3 cells around the point."""

    def __init__(self, src: List[Vec2], dist: float):
        self.src = src
        self.lim2 = dist * dist
        self.cell = max(dist, _EPS)
        self.grid: dict = {}
        n = len(src)
        c = self.cell
        for k in range(n):
            a, b = src[k], src[(k + 1) % n]
            for ci in range(int(math.floor(min(a.x, b.x) / c)),
                            int(math.floor(max(a.x, b.x) / c)) + 1):
                for cj in range(int(math.floor(min(a.y, b.y) / c)),
                                int(math.floor(max(a.y, b.y) / c)) + 1):
                    self.grid.setdefault((ci, cj), []).append(k)

    def too_close(self, p: Vec2) -> bool:
        src, n = self.src, len(self.src)
        ci = int(math.floor(p.x / self.cell))
        cj = int(math.floor(p.y / self.cell))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for k in self.grid.get((ci + di, cj + dj), ()):
                    a, b = src[k], src[(k + 1) % n]
                    dx, dy = b.x - a.x, b.y - a.y
                    l2 = dx * dx + dy * dy
                    t = 0.0 if l2 < _EPS else max(0.0, min(1.0, (
                        (p.x - a.x) * dx + (p.y - a.y) * dy) / l2))
                    ex = p.x - (a.x + dx * t)
                    ey = p.y - (a.y + dy * t)
                    if ex * ex + ey * ey < self.lim2:
                        return True
        return False


def _remove_inverted_loops(ring: List[Vec2], dirs: List[Vec2],
                           src: List[Vec2], dist: float) -> List[Vec2]:
    """Trim the inside-out loops ("swallowtails") out of a mitred offset ring.

    Offsetting every flattened edge by ``dist`` and re-intersecting neighbours
    is exact for gentle geometry, but where the offset exceeds the local radius
    -- a corner fillet tighter than the stitch inset, a spike thinner than twice
    the inset, a concave fillet tighter than an outward offset -- those edges
    flip direction and the ring ties a small loop that runs *backwards*.
    Walking it, the hole marcher used to pack a cluster of holes into the
    corner.

    Standard offset trimming: cut the ring at every self-crossing and at every
    cusp (where it turns back on itself at a vertex), keep the pieces that
    belong to the true offset, prune any left dangling, and re-link the
    survivors at their crossings. Ring edge k lies on the offset of source edge
    k, whose direction is ``dirs[k]``; a genuine piece runs the same way as its
    source, a swallowtail runs backwards. That test is exact, so it decides.
    Forward pieces that still overlap the wrong side (two offsets of a thin neck passing
    each other) sit well inside ``dist`` of the source ``src`` and are dropped
    too -- "well inside" (90%) because a mitred ring cuts each convex vertex
    with a chord where the true offset is an arc, so genuine pieces can dip a
    little under ``dist``; if that leaves nothing that links up, direction
    alone is used.

    Arriving at a crossing, the walk carries straight on along the same edge
    when that piece survives, so a waist pinched into two lobes stays one
    figure-8 (both lobes stitched, as before) rather than losing a lobe.
    ``ring`` and ``src`` have no repeated closing point. Returns the largest
    loop; ``[]`` if no piece survives (the offset collapsed); the untrimmed
    ``ring`` if survivors exist but can't be linked (never worse than before).
    """
    n = len(ring)
    if n < 3:
        return list(ring)
    crossings = _self_crossings(ring)
    if not crossings:
        # The usual case: nothing to trim. But a piece narrower than twice
        # the offset can fold into a ring whose backwards edges meet at cusps
        # without ever crossing; if no vertex then clears the source by a
        # margin, there is no offset at all.
        if any((ring[(k + 1) % n] - ring[k]).dot(dirs[k]) < -1e-9
               for k in range(n)):
            near = _Proximity(src, abs(dist) * 0.9)
            if all(near.too_close(p) for p in ring[::max(1, n // 64)]):
                return []
        return list(ring)

    # events along each edge: (t, crossing id, the crossing point)
    on_edge: dict = {}
    for c, (i, t, j, u) in enumerate(crossings):
        a = ring[i]
        x = a + (ring[(i + 1) % n] - a) * t
        on_edge.setdefault(i, []).append((t, c, x))
        on_edge.setdefault(j, []).append((u, c, x))
    events = []                        # (edge, crossing id, point) in ring order
    for k in sorted(on_edge):
        for t, c, x in sorted(on_edge[k], key=lambda e: e[0]):
            events.append((k, c, x))

    # Pieces between consecutive events (the last wraps past vertex 0), each
    # split further where the ring turns back on itself at a vertex (a cusp,
    # the tip of a swallowtail). Sub-segment q of a run starting on edge k
    # lies on edge k + q. A run's ends are keyed (edge, crossing) when they
    # sit on a crossing, None at a cusp. Only forward runs are kept.
    runs = []                          # (start key, end key, pts)
    m = len(events)
    for e in range(m):
        k0, c0, x0 = events[e]
        k1, c1, x1 = events[(e + 1) % m]
        pts = [x0]
        if not (k1 == k0 and e + 1 < m):    # else: just the bit of one edge
            k = (k0 + 1) % n
            while True:
                pts.append(ring[k])
                if k == k1:
                    break
                k = (k + 1) % n
        pts.append(x1)
        cur = [pts[0]]
        cur_fwd = None
        start = (k0, c0)
        for q in range(len(pts) - 1):
            fwd = (pts[q + 1] - pts[q]).dot(dirs[(k0 + q) % n]) > -1e-9
            if cur_fwd is not None and fwd != cur_fwd:
                if cur_fwd:
                    runs.append((start, None, cur))
                cur, start = [pts[q]], None
            cur_fwd = fwd
            cur.append(pts[q + 1])
        if cur_fwd:
            runs.append((start, (k1, c1), cur))

    def midpoint(pts):
        lens = [(pts[q + 1] - pts[q]).length() for q in range(len(pts) - 1)]
        half = sum(lens) / 2.0
        for q, L in enumerate(lens):
            if half <= L and L > _EPS:
                return pts[q] + (pts[q + 1] - pts[q]) * (half / L)
            half -= L
        return pts[len(pts) // 2]

    if not runs:
        return []                      # nothing runs forward: collapsed
    start_at = {r[0]: idx for idx, r in enumerate(runs) if r[0] is not None}
    other_edge = {}
    for c, (i, _t, j, _u) in enumerate(crossings):
        other_edge[(i, c)] = j
        other_edge[(j, c)] = i

    def successors(idx):
        end = runs[idx][1]
        if end is None:
            return []
        k, c = end
        return [s for s in (start_at.get((k, c)),             # straight on
                            start_at.get((other_edge[(k, c)], c)))  # turn
                if s is not None]

    def link(keep):
        keep = list(keep)
        # prune dangling runs (an overshoot ending at a cusp, a lead-in from
        # one) until every survivor can be both entered and left
        succ = [successors(i) for i in range(len(runs))]
        pred: dict = {}
        for i, ss in enumerate(succ):
            for s in ss:
                pred.setdefault(s, []).append(i)
        todo = list(range(len(runs)))
        while todo:
            i = todo.pop()
            if not keep[i]:
                continue
            if (not any(keep[s] for s in succ[i])
                    or not any(keep[p] for p in pred.get(i, ()))):
                keep[i] = False
                todo.extend(succ[i])
                todo.extend(pred.get(i, ()))
        used = [False] * len(runs)
        loops = []
        for first in range(len(runs)):
            if used[first] or not keep[first]:
                continue
            walk: List[int] = []
            at: dict = {}              # run -> its position in ``walk``
            idx = first
            while not used[idx]:
                at[idx] = len(walk)
                walk.append(idx)
                used[idx] = True
                nxt = next((s for s in succ[idx] if keep[s]), None)
                if nxt is None:
                    break
                if nxt in at:          # closed a cycle (a stray lead-in that
                    loop: List[Vec2] = []   # ran into it is left behind)
                    for q in walk[at[nxt]:]:
                        loop.extend(runs[q][2][:-1])
                    if len(loop) >= 3:
                        loops.append(loop)
                    break
                idx = nxt
        return max(loops, key=lambda lp: abs(signed_area(lp))) if loops else None

    near = _Proximity(src, abs(dist) * 0.9)
    deep = [not near.too_close(midpoint(r[2])) for r in runs]
    if not any(deep):
        return []                      # only overlaps left: collapsed
    for keep in (deep, [True] * len(runs)):
        loop = link(keep)
        if loop is not None:
            return loop
    return list(ring)


def _mitred_offset(ring: List[Vec2], move: float, sign: float):
    """Offset each edge of ``ring`` along its inward normal by ``move`` (mm;
    negative = outward) and re-intersect neighbours for mitred corners.
    Returns ``(points, dirs, starts)``: the raw ring; per ring edge, the
    direction of the source edge it lies on; and per ring vertex, the source
    vertex it is the mitre of. Zero-length source edges are skipped."""
    n = len(ring)
    offset_edges = []
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        d = b - a
        if d.length() < _EPS:
            continue
        d = d.normalized()
        inward = Vec2(-d.y, d.x) * (move * sign)       # left normal for CCW
        offset_edges.append((a + inward, b + inward, d, a))
    m = len(offset_edges)
    pts: List[Vec2] = []
    for i in range(m):
        prev = offset_edges[(i - 1) % m]
        cur = offset_edges[i]
        p = _line_intersection(prev[0], prev[1], cur[0], cur[1])
        pts.append(p if p is not None else cur[0])
    return pts, [e[2] for e in offset_edges], [e[3] for e in offset_edges]


def _runs_backwards(pts: List[Vec2], dirs: List[Vec2]) -> bool:
    """True if the raw offset ring, taken as a whole, runs against its source:
    the outline shrank past its own width and the offset lines crossed over.
    (A square inset past its half-width comes back as a small square with the
    SAME orientation -- rotated 180 degrees -- so area can't tell; direction
    can.)"""
    n = len(pts)
    along = sum((pts[(k + 1) % n] - pts[k]).dot(dirs[k]) for k in range(n))
    return along <= 0.0


def offset_closed_inward(points: Sequence[Vec2], dist: float) -> List[Vec2]:
    """Return the outline offset inward by ``dist`` (mm), as a closed ring
    (first point repeated at the end). ``dist`` <= 0 returns the input.

    Inside-out loops at corners tighter than ``dist`` are removed (see
    ``_remove_inverted_loops``). If the whole outline is narrower than twice
    ``dist`` there is no inset ring at all and ``[]`` is returned."""
    return inset_with_corners(points, dist, [])[0]


# a crossing the trim introduced counts as a corner if the ring turns by
# more than this there (a ripple's shallow envelope kinks do not)
_TRIM_CORNER_TURN = math.radians(20.0)


def inset_with_corners(points: Sequence[Vec2], dist: float,
                       corners: Sequence[Vec2]):
    """``offset_closed_inward`` plus where the outline's ``corners`` (vertices
    of ``points``) land on the inset ring. Returns ``(ring, corners)``.

    A corner lands on its own mitre point -- the ring vertex made from it.
    Projecting it to the NEAREST point of the ring instead goes wrong on a thin
    arm, whose inset corner moves far inward: the nearest point lands part-way
    along the neighbouring edge and the real inset corner gets no hole. A
    corner whose mitre vertex was trimmed away (a fillet tighter than the
    inset, an arm narrower than twice it) no longer exists on the stitch line
    and is dropped; the sharp crossings the trim left behind are the stitch
    line's real corners there, so those are returned in its place. A corner
    that isn't a vertex of ``points`` is passed through unchanged."""
    ring = _unique_ring(points)
    n = len(ring)
    if n < 3 or dist <= _EPS:
        return list(points), list(corners)
    sign = 1.0 if signed_area(ring) > 0 else -1.0  # +1 for CCW
    raw, dirs, starts = _mitred_offset(ring, dist, sign)
    return _trim_inset(raw, dirs, starts, ring, dist, corners)


def _trim_inset(raw: List[Vec2], dirs: List[Vec2], starts, src: List[Vec2],
                dist: float, corners: Sequence[Vec2]):
    """Shared tail of the insets: trim the raw ring and carry ``corners`` onto
    it. ``starts[k]`` is the source vertex raw vertex k was made from, or a
    list of them (several merge into one where an arc collapsed to a point;
    none inside an offset arc). Returns ``(closed ring, corners)``."""
    if len(raw) < 3 or _runs_backwards(raw, dirs):
        return [], []                 # too narrow for this inset
    new_pts = _remove_inverted_loops(raw, dirs, src, dist)
    if len(new_pts) < 3:
        return [], []

    def key(v):
        # matched to the micron: corner points and flattened vertices come
        # from the same segment endpoints but not always bit-identically
        return (round(v.x, 6), round(v.y, 6))

    kept = {key(p) for p in new_pts}
    raw_keys = {key(p) for p in raw}
    mitre = {key(v): p for vs, p in zip(starts, raw)
             for v in (vs if isinstance(vs, list) else [vs])}
    out: List[Vec2] = []
    for c in corners:
        m = mitre.get(key(c))
        if m is None:
            out.append(c)                 # not an outline vertex
        elif key(m) in kept:
            out.append(m)
    m = len(new_pts)
    for i, p in enumerate(new_pts):
        if key(p) in raw_keys:
            continue                      # an original vertex, not a crossing
        d1 = p - new_pts[i - 1]
        d2 = new_pts[(i + 1) % m] - p
        l1, l2 = d1.length(), d2.length()
        if (l1 > _EPS and l2 > _EPS and math.acos(max(-1.0, min(1.0,
                d1.dot(d2) / (l1 * l2)))) > _TRIM_CORNER_TURN):
            out.append(p)
    new_pts.append(new_pts[0])
    return new_pts, out


# Inset arcs are flattened this finely (sagitta, mm) so holes marched along
# them sit within a micron of the true curve before being snapped onto it.
ARC_FLATNESS = 1e-3


def _offset_path_raw(path, move: float, sign: float):
    """Offset ``path`` segment by segment along the inward normal by ``move``
    (mm), flattening only AFTER offsetting: a line shifts sideways, an arc
    stays concentric with radius r -/+ move. Joins that were smooth stay
    smooth; sharp joins are mitred. (Offsetting the flattened polyline
    instead mitres every arc chord, which pulls each straight short and
    bends the corner arcs -- a 4 mm inset of an R10.53 corner came out
    R~6.55 over 91.8 degrees with straights 0.24 mm short.)

    An arc tighter than ``move`` comes out with a negative radius -- running
    backwards, which the trim removes like any swallowtail. Bezier segments
    are flattened first and offset chord by chord.

    Returns ``(points, dirs, starts, arcs)`` as for ``_mitred_offset``, plus
    the offset arcs ``(centre, radius, start angle, sweep)`` with radius > 0.
    """
    from .path import Arc, Line, DEFAULT_FLATNESS
    pieces = []           # [pts, dirs (per edge), source vertex of pts[0]]
    arcs = []
    for seg in path.segments:
        if isinstance(seg, Line):
            d = seg.p1 - seg.p0
            L = d.length()
            if L < _EPS:
                continue
            u = d * (1.0 / L)
            nrm = Vec2(-u.y, u.x) * (move * sign)
            pieces.append([[seg.p0 + nrm, seg.p1 + nrm], [u], seg.p0])
        elif isinstance(seg, Arc):
            sweep = seg._sweep()
            if abs(sweep) < 1e-12 or seg.radius <= _EPS:
                continue
            turn = 1.0 if sweep > 0 else -1.0
            r2 = seg.radius - move * sign * turn
            rr = max(abs(r2), 1e-6)
            ratio = max(-1.0, min(1.0, 1.0 - ARC_FLATNESS / rr))
            step = max(2.0 * math.acos(ratio) if ratio < 1.0 else math.pi,
                       1e-4)
            n = max(1, int(math.ceil(abs(sweep) / step)))
            c = seg.center
            angs = [seg.a0 + sweep * i / n for i in range(n + 1)]
            pts = [Vec2(c.x + r2 * math.cos(a), c.y + r2 * math.sin(a))
                   for a in angs]
            dirs = []
            for i in range(n):
                am = 0.5 * (angs[i] + angs[i + 1])
                dirs.append(Vec2(-math.sin(am), math.cos(am)) * turn)
            if r2 > _EPS:
                arcs.append((c, r2, seg.a0, sweep))
            pieces.append([pts, dirs, seg.start()])
        else:                                   # bezier: chord by chord
            fl = [seg.start()] + seg.flatten(DEFAULT_FLATNESS)
            for a, b in zip(fl, fl[1:]):
                d = b - a
                L = d.length()
                if L < _EPS:
                    continue
                u = d * (1.0 / L)
                nrm = Vec2(-u.y, u.x) * (move * sign)
                pieces.append([[a + nrm, b + nrm], [u], a])
    m = len(pieces)
    closed = path.closed
    # join neighbours: shared point if they already meet, else a mitre
    for i in range(m if closed else m - 1):
        A, B = pieces[i], pieces[(i + 1) % m]
        a_end, b_start = A[0][-1], B[0][0]
        if (a_end - b_start).length() < 1e-9:
            B[0][0] = a_end
        else:
            x = _line_intersection(A[0][-2], A[0][-1], B[0][0], B[0][1])
            if x is not None:
                A[0][-1] = x
                B[0][0] = x
    raw: List[Vec2] = []
    dirs: List[Vec2] = []
    starts: list = []
    for i, (pts, pdirs, src) in enumerate(pieces):
        nxt = pieces[(i + 1) % m][0][0] if (closed or i + 1 < m) else None
        for j in range(len(pts) - 1):
            raw.append(pts[j])
            dirs.append(pdirs[j])
            starts.append([src] if j == 0 else [])
        if nxt is None or pts[-1] is not nxt:       # a gap (parallel join)
            raw.append(pts[-1])
            if nxt is not None:
                g = nxt - pts[-1]
                dirs.append(g * (1.0 / g.length()) if g.length() > _EPS
                            else pdirs[-1])
            else:
                dirs.append(pdirs[-1])
            starts.append([])
    # merge coincident neighbours (an arc whose radius equals the offset
    # collapses to a point): keep the first, take the later outgoing edge
    out_p: List[Vec2] = []
    out_d: List[Vec2] = []
    out_s: list = []
    for p, d, s_ in zip(raw, dirs, starts):
        if out_p and (p - out_p[-1]).length() < 1e-9:
            out_d[-1] = d
            out_s[-1] = out_s[-1] + s_
            continue
        out_p.append(p)
        out_d.append(d)
        out_s.append(s_)
    while closed and len(out_p) > 1 and (out_p[-1] - out_p[0]).length() < 1e-9:
        out_s[0] = out_s[0] + out_s[-1]
        out_p.pop()
        out_d.pop()
        out_s.pop()
    return out_p, out_d, out_s, arcs


def inset_path_with_corners(path, dist: float, corners: Sequence[Vec2]):
    """``inset_with_corners`` computed from the path's true segments rather
    than its flattened polyline (see ``_offset_path_raw``): straights keep
    their exact length and arcs their exact centre, radius and sweep.

    Returns ``(points, corners, arcs)``: the stitch line (a closed ring with
    its first point repeated when ``path`` is closed; ``[]`` if nothing fits),
    the corners carried onto it, and the stitch line's arcs as
    ``(centre, radius, start angle, sweep)`` -- holes landing on one of those
    are snapped onto the true circle by the stitcher. ``dist`` <= 0 (or an
    open path) just flattens the path, arcs finely."""
    src = path.flatten()
    ring = _unique_ring(src)
    if len(ring) < 3 and path.closed:
        return list(src), list(corners), []
    if not path.closed or dist <= _EPS:
        pts, _d, _s, arcs = _offset_path_raw(path, 0.0, 1.0)
        if path.closed and pts:
            pts = pts + [pts[0]]
        return pts, list(corners), arcs
    sign = 1.0 if signed_area(ring) > 0 else -1.0  # +1 for CCW
    raw, dirs, starts, arcs = _offset_path_raw(path, dist, sign)
    pts, out = _trim_inset(raw, dirs, starts, ring, dist, corners)
    return pts, out, (arcs if pts else [])


def offset_closed(points: Sequence[Vec2], dist: float) -> List[Vec2]:
    """Offset a closed outline by ``dist`` mm: ``dist`` > 0 grows the shape
    OUTWARD (seam/glue allowance), ``dist`` < 0 shrinks it inward. Returns a
    closed ring (first point repeated). Mitered corners via edge re-intersection.
    Inside-out loops at corners tighter than ``dist`` are trimmed away.
    """
    ring = _unique_ring(points)
    n = len(ring)
    if n < 3 or abs(dist) < _EPS:
        return list(points)
    sign = 1.0 if signed_area(ring) > 0 else -1.0     # +1 for CCW
    new_pts, dirs, _ = _mitred_offset(ring, -dist, sign)  # +dist -> outward
    # A ring that collapsed (shrunk past its own width) keeps the raw miter
    # result: callers such as export's kerf pass must never lose a cut.
    if len(new_pts) >= 3 and not _runs_backwards(new_pts, dirs):
        cleaned = _remove_inverted_loops(new_pts, dirs, ring, dist)
        if len(cleaned) >= 3:
            new_pts = cleaned
    new_pts.append(new_pts[0])
    return new_pts


def offset_shape(shape, dist: float):
    """Offset ``shape`` by ``dist`` mm as a NEW shape (``dist`` > 0 outward,
    < 0 inward). Circles and (rounded) rectangles stay parametric -- a circle
    offsets to a true circle, a rounded rectangle to a rounded rectangle with
    the corner radius grown/shrunk to the geometrically correct offset curve.
    Everything else offsets its flattened outline (mitered corners). Returns
    ``None`` when the offset would collapse the shape (inward past its size).
    """
    import copy
    from .shapes import Circle, Rectangle, Polygon, PathShape, Transform, _next_id

    if abs(dist) < _EPS:
        return None

    if isinstance(shape, Circle):
        r = shape.rx + dist
        if r <= _EPS:                                  # collapses -> refuse
            return None
        sh = Circle(rx=r, ry=r, transform=copy.deepcopy(shape.transform),
                    layer=shape.layer)
    elif type(shape) is Rectangle:
        w = shape.width + 2.0 * dist
        h = shape.height + 2.0 * dist
        if w <= _EPS or h <= _EPS:                     # collapses -> refuse
            return None
        cr = shape.corner_radius
        # the true offset of a rounded corner is an arc of radius r+dist;
        # shrinking past the radius leaves a sharp (mitered) corner
        new_cr = max(0.0, cr + dist) if cr > _EPS else 0.0
        sh = Rectangle(width=w, height=h, corner_radius=new_cr,
                       transform=copy.deepcopy(shape.transform),
                       layer=shape.layer)
    else:
        wpts, _corners, closed = shape.world_polyline()
        if len(wpts) < 2:
            return None
        if closed:
            ring = offset_closed(wpts, dist)
            if len(ring) >= 2 and (ring[0] - ring[-1]).length() < 1e-6:
                ring = ring[:-1]
            if len(ring) < 3:
                return None
            cx = sum(p.x for p in ring) / len(ring)
            cy = sum(p.y for p in ring) / len(ring)
            sh = Polygon(points=[Vec2(p.x - cx, p.y - cy) for p in ring],
                         close_path=True, transform=Transform(x=cx, y=cy),
                         layer=shape.layer)
        else:
            line = offset_open(wpts, dist)
            if len(line) < 2:
                return None
            cx = sum(p.x for p in line) / len(line)
            cy = sum(p.y for p in line) / len(line)
            sh = PathShape(points=[Vec2(p.x - cx, p.y - cy) for p in line],
                           close_path=False, transform=Transform(x=cx, y=cy),
                           layer=shape.layer)
    sh.shape_id = _next_id("shape")
    return sh


def offset_open(points: Sequence[Vec2], dist: float) -> List[Vec2]:
    """Offset an open polyline sideways by ``dist`` mm (right of travel for
    ``dist`` > 0). Corners are mitered; the two ends just shift along their
    edge normals."""
    pts = list(points)
    if len(pts) < 2 or abs(dist) < _EPS:
        return pts
    edges = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        d = b - a
        if d.length() < _EPS:
            continue
        d = d.normalized()
        normal = Vec2(d.y, -d.x) * dist               # right-hand normal
        edges.append((a + normal, b + normal))
    if not edges:
        return pts
    out = [edges[0][0]]
    for i in range(1, len(edges)):
        p = _line_intersection(edges[i - 1][0], edges[i - 1][1],
                               edges[i][0], edges[i][1])
        out.append(p if p is not None else edges[i][0])
    out.append(edges[-1][1])
    return out

"""Polygon tools for folding a piece: cut an outline along a line, split it
into convex parts, thin out a flattened curve.

A point is an ``(x, y)`` tuple in mm. A polygon is a list of points going
counter-clockwise, with no repeated closing point, and carries one flag per
edge: edge ``i`` runs from point ``i`` to point ``i + 1``. A flag says what
its edge is -- a cut edge of the leather (``OUTLINE``), a line the polygon
was only divided along (``INNER``), or whatever number the caller cut it
with -- and every tool here keeps the flags on the right edges.

Pure Python, no Qt: the fold engine (``foldsolid``) is built on these.
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

Pt = Tuple[float, float]
Poly = Tuple[List[Pt], List[int]]

OUTLINE = -1          # a real edge of the piece: the leather ends here
INNER = -2            # a dividing line only: the leather carries on across it
_HAIR = 1e-11         # two corners this close (times 1 + how far they lie
#                       from the origin, mm) are one: rounding parts them


# -- measures ----------------------------------------------------------------

def area(pts: Sequence[Pt]) -> float:
    """Signed area: positive for a counter-clockwise polygon."""
    s = 0.0
    x0, y0 = pts[-1]
    for x1, y1 in pts:
        s += x0 * y1 - x1 * y0
        x0, y0 = x1, y1
    return 0.5 * s


def centroid(pts: Sequence[Pt]) -> Pt:
    """Centre of area (the mean of the points for a polygon with none)."""
    a = cx = cy = 0.0
    x0, y0 = pts[-1]
    for x1, y1 in pts:
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
        x0, y0 = x1, y1
    if abs(a) < 1e-12:
        n = len(pts)
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    return (cx / (3.0 * a), cy / (3.0 * a))


def bounds(pts: Sequence[Pt]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def ccw(pts: Sequence[Pt]) -> List[Pt]:
    """The polygon going counter-clockwise (reversed if it wasn't)."""
    pts = list(pts)
    return pts if area(pts) >= 0.0 else pts[::-1]


def tidy(pts: Sequence[Pt], flags: Sequence[int], tol: float = 1e-7) -> Poly:
    """Drop edges of no length and spikes that run out and straight back,
    keeping each surviving edge's flag."""
    P = list(pts)
    F = list(flags)
    i = seen = 0
    while len(P) >= 3 and seen < len(P):
        n = len(P)
        i %= n
        a, b, c = P[i], P[(i + 1) % n], P[(i + 2) % n]
        if abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol:
            # edge i has no length: drop its far end, and edge i with it
            P = P[i:] + P[:i]
            F = F[i:] + F[:i]
            del P[1]
            del F[0]
            i = seen = 0
        elif n > 3 and abs(a[0] - c[0]) <= tol and abs(a[1] - c[1]) <= tol:
            # out to the next point and straight back: drop the spike
            P = P[i:] + P[:i]
            F = F[i:] + F[:i]
            del P[1:3]
            del F[0:2]
            i = seen = 0
        else:
            i += 1
            seen += 1
    return P, F


def simplify(pts: Sequence[Pt], tol: float) -> List[Pt]:
    """Thin out a closed ring of points (a flattened curve), keeping every
    point that stands more than ``tol`` off the line between its kept
    neighbours (Douglas-Peucker round the ring)."""
    n = len(pts)
    if n <= 4 or tol <= 0.0:
        return list(pts)
    # two anchors far apart: the first point and the one farthest from it
    x0, y0 = pts[0]
    far = max(range(n), key=lambda i: (pts[i][0] - x0) ** 2
              + (pts[i][1] - y0) ** 2)
    if far == 0:
        return list(pts)
    keep = [False] * n
    keep[0] = keep[far] = True
    stack = [(0, far), (far, n)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        ax, ay = pts[i]
        bx, by = pts[j % n]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            px, py = pts[k]
            if ll < 1e-18:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / ll
                t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > worst:
                worst, wi = d, k
        if worst > tol * tol:
            keep[wi] = True
            stack.append((i, wi))
            stack.append((wi, j))
    out = [p for p, k in zip(pts, keep) if k]
    return out if len(out) >= 3 else list(pts)


# -- cutting along a line ----------------------------------------------------

def _crossings(pts, a, e, eps):
    """Signed distances from the line through ``a`` along unit ``e`` (left
    positive), positions along it, and where the outline crosses it, in
    order along the line.

    A point on the line counts as just to its left, which settles every
    awkward case -- an edge lying along the line, a corner touching it --
    the same way throughout. Two crossings at one such point are put in the
    order they would have if it really stood a hair to the left."""
    ax, ay = a
    ex, ey = e
    n = len(pts)
    d = [(p[1] - ay) * ex - (p[0] - ax) * ey for p in pts]
    s = [(p[0] - ax) * ex + (p[1] - ay) * ey for p in pts]
    left = [v >= -eps for v in d]
    out = []
    for i in range(n):
        j = i + 1 if i + 1 < n else 0
        if left[i] == left[j]:
            continue
        lf, rt = (i, j) if left[i] else (j, i)
        if d[lf] <= eps:                      # the left end is on the line
            pos, vert, t = s[lf], lf, None
        else:
            t = d[i] / (d[i] - d[j])
            pos, vert = s[i] + (s[j] - s[i]) * t, None
        out.append((pos, (s[rt] - pos) / -d[rt], i, t, vert))
    out.sort()
    return d, s, out


def _stretches(pts, a, e, eps):
    """Where the line runs through the inside of the polygon, outline to
    outline: ``(from, to, end, end)`` for each stretch, an end being
    ``("v", point index)`` or ``("e", edge index, how far along it)``.

    The crossings give the stretches the line is in the polygon; a stretch
    is then divided wherever the outline comes up to the line -- a corner
    touching it, an edge lying along it -- and the part along such an edge
    is left out: the leather ends there, there is nothing to cut."""
    d, s, cr = _crossings(pts, a, e, eps)
    if len(cr) % 2:
        return d, s, []
    n = len(pts)
    online = sorted((s[i], i) for i in range(n) if abs(d[i]) <= eps)
    out = []
    for k in range(0, len(cr), 2):
        c0, c1 = cr[k], cr[k + 1]
        lo, hi = c0[0], c1[0]
        ends = [("v", c[4]) if c[4] is not None else ("e", c[2], c[3])
                for c in (c0, c1)]
        nodes = [(lo, ends[0])]
        nodes += [(pos, ("v", i)) for pos, i in online
                  if lo + 1e-9 < pos < hi - 1e-9]
        nodes.append((hi, ends[1]))
        for (p0, n0), (p1, n1) in zip(nodes, nodes[1:]):
            if p1 - p0 <= 1e-9:
                continue
            if n0[0] == "v" and n1[0] == "v" \
                    and (n0[1] - n1[1]) % n in (1, n - 1):
                continue                      # along an edge of the outline
            out.append((p0, p1, n0, n1))
    return d, s, out


def spans(pts: Sequence[Pt], a: Pt, e: Pt, eps: float = 1e-6
          ) -> List[Tuple[float, float]]:
    """The stretches of the line through ``a`` along unit ``e`` that run
    through the inside of the polygon from outline to outline, as
    ``(from, to)`` positions along the line."""
    return [(p0, p1) for p0, p1, _a, _b in _stretches(pts, a, e, eps)[2]]


def cut(pts: Sequence[Pt], flags: Sequence[int], a: Pt, e: Pt,
        want: Optional[Callable[[float, float], bool]], flag: int,
        eps: float = 1e-6) -> List[Poly]:
    """Cut a polygon along the line through ``a`` in unit direction ``e``.

    The line is cut along every stretch that runs through the inside of the
    polygon from outline to outline -- or only those ``want(from, to)``
    accepts (positions along the line). A stretch is always cut whole: a
    cut can't stop in the middle of the leather. The new edges get
    ``flag``; every piece comes back counter-clockwise, and a piece whose
    new edge runs along ``e`` lies to the left of the line there."""
    pts = list(pts)
    flags = list(flags)
    n = len(pts)
    if n < 3:
        return [(pts, flags)]
    _d, _s, stretches = _stretches(pts, a, e, eps)
    extra: List[Pt] = []                      # new points get ids n, n+1...
    ins = {}                                  # edge -> (t, id)

    def point_id(end):
        if end[0] == "v":
            return end[1]
        edge, t = end[1], end[2]
        if edge not in ins:
            j = edge + 1 if edge + 1 < n else 0
            p, q = pts[edge], pts[j]
            extra.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
            ins[edge] = (t, n + len(extra) - 1)
        return ins[edge][1]

    chords = []
    for p0, p1, n0, n1 in stretches:
        if want is not None and not want(p0, p1):
            continue
        i0, i1 = point_id(n0), point_id(n1)
        if i0 != i1:
            chords.append((i0, i1))
    if not chords:
        return [(pts, flags)]

    allpts = pts + extra
    ring = []
    for i in range(n):
        ring.append((i, flags[i]))
        if i in ins:
            ring.append((ins[i][1], flags[i]))
    rings = [ring]
    for p, q in chords:
        for ri, r in enumerate(rings):
            ids = [v for v, _f in r]
            if p not in ids or q not in ids:
                continue
            ip, iq = ids.index(p), ids.index(q)
            # p .. q, closed by the cut running q -> p (against e: this piece
            # is on the right); q .. p, closed by p -> q (on the left)
            one = (r[ip:iq] if ip < iq else r[ip:] + r[:iq]) + [(q, flag)]
            two = (r[iq:ip] if iq < ip else r[iq:] + r[:ip]) + [(p, flag)]
            rings[ri:ri + 1] = [one, two]
            break
    out = []
    for r in rings:
        P, F = tidy([allpts[v] for v, _f in r], [f for _v, f in r])
        if len(P) >= 3 and abs(area(P)) > 1e-9:
            out.append((P, F))
    return out or [(pts, flags)]


# -- convex parts ------------------------------------------------------------

def is_convex(pts: Sequence[Pt], tol: float = 1e-9) -> bool:
    n = len(pts)
    if n < 4:
        return True
    for i in range(n):
        ax, ay = pts[i - 1]
        bx, by = pts[i]
        cx, cy = pts[(i + 1) % n]
        if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) < -tol:
            return False
    return True


def _triangles(pts: Sequence[Pt]) -> List[Tuple[int, int, int]]:
    """Ear clipping of a counter-clockwise polygon into index triples."""
    n = len(pts)
    idx = list(range(n))
    tris: List[Tuple[int, int, int]] = []

    def cross(i, j, k):
        ax, ay = pts[i]
        bx, by = pts[j]
        cx, cy = pts[k]
        return (bx - ax) * (cy - by) - (by - ay) * (cx - bx)

    def blocked(i, j, k):
        ax, ay = pts[i]
        bx, by = pts[j]
        cx, cy = pts[k]
        for q in idx:
            if q == i or q == j or q == k:
                continue
            px, py = pts[q]
            if ((px == ax and py == ay) or (px == bx and py == by)
                    or (px == cx and py == cy)):
                continue                      # the same spot as a corner
            tol = -1e-12
            if ((bx - ax) * (py - ay) - (by - ay) * (px - ax) >= tol
                    and (cx - bx) * (py - by) - (cy - by) * (px - bx) >= tol
                    and (ax - cx) * (py - cy) - (ay - cy) * (px - cx) >= tol):
                return True
        return False

    while len(idx) > 3:
        m = len(idx)
        best = None
        for k in range(m):
            i, j, l = idx[k - 1], idx[k], idx[(k + 1) % m]
            c = cross(i, j, l)
            if c <= 1e-12 or blocked(i, j, l):
                continue
            best = k
            break
        if best is None:
            # nothing clips cleanly (a touch of numerical trouble): take the
            # flattest corner so the rest can still be dealt with
            best = min(range(m), key=lambda k: abs(
                cross(idx[k - 1], idx[k], idx[(k + 1) % m])))
        i, j, l = idx[best - 1], idx[best], idx[(best + 1) % m]
        if cross(i, j, l) > 1e-12:
            tris.append((i, j, l))
        del idx[best]
    if len(idx) == 3 and cross(idx[0], idx[1], idx[2]) > 1e-12:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def convex_parts(pts: Sequence[Pt], flags: Sequence[int],
                 inner: int = INNER) -> List[Poly]:
    """Split a counter-clockwise polygon into convex ones (few of them: the
    triangles of an ear clipping, merged back wherever the result stays
    convex). Edges the split adds get the ``inner`` flag. A corner given
    twice, the two a rounding apart, is given once."""
    pts = list(pts)
    flags = list(flags)
    n = len(pts)
    if n < 3:
        return []
    hair = _HAIR * (1.0 + max(max(abs(x), abs(y)) for x, y in pts))
    if any(abs(pts[i][0] - pts[i - 1][0]) <= hair
           and abs(pts[i][1] - pts[i - 1][1]) <= hair for i in range(n)):
        pts, flags = tidy(pts, flags, hair)
        n = len(pts)
        if n < 3:
            return []
    if is_convex(pts):
        return [(pts, flags)]
    polys = {k: list(t) for k, t in enumerate(_triangles(pts))}
    if not polys:
        return [(pts, flags)]
    owner = {}
    for k, poly in polys.items():
        for a in range(len(poly)):
            owner[(poly[a], poly[(a + 1) % len(poly)])] = k

    def turn(i, j, k):
        ax, ay = pts[i]
        bx, by = pts[j]
        cx, cy = pts[k]
        return (bx - ax) * (cy - by) - (by - ay) * (cx - bx)

    diagonals = [(i, j) for (i, j) in owner if i < j and (j, i) in owner]
    # longest first: the big merges, then the trimmings
    diagonals.sort(key=lambda ij: -((pts[ij[0]][0] - pts[ij[1]][0]) ** 2
                                    + (pts[ij[0]][1] - pts[ij[1]][1]) ** 2))
    for i, j in diagonals:
        ka, kb = owner.get((i, j)), owner.get((j, i))
        if ka is None or kb is None or ka == kb:
            continue
        A, B = polys[ka], polys[kb]
        ia, ib = A.index(i), B.index(j)       # A holds i -> j, B holds j -> i
        # A from j round to i, then B from i round to j, dropping the copies
        la, lb = len(A), len(B)
        merged = [A[(ia + 1 + t) % la] for t in range(la)] \
            + [B[(ib + 1 + t) % lb] for t in range(1, lb - 1)]
        m = len(merged)
        pi, pj = merged.index(i), merged.index(j)
        if (turn(merged[pi - 1], i, merged[(pi + 1) % m]) < -1e-9
                or turn(merged[pj - 1], j, merged[(pj + 1) % m]) < -1e-9):
            continue
        del polys[kb]
        polys[ka] = merged
        del owner[(i, j)], owner[(j, i)]
        for t in range(m):
            owner[(merged[t], merged[(t + 1) % m])] = ka
    out = []
    for poly in polys.values():
        m = len(poly)
        F = [flags[poly[t]] if (poly[t] + 1) % n == poly[(t + 1) % m]
             else inner for t in range(m)]
        out.append(([pts[v] for v in poly], F))
    return out


# -- convex polygon tests ----------------------------------------------------

def inside_convex(p: Pt, pts: Sequence[Pt], tol: float = 1e-9) -> bool:
    """Whether ``p`` is in a counter-clockwise convex polygon (its edge
    counts, to within ``tol`` mm).

    An edge no longer than ``tol``, or than the rounding of corners this
    far from the origin (one corner given twice), bounds nothing: its
    direction is noise, and would put a point well inside outside. A
    point past such an edge must still lie in the polygon's box, so that
    a polygon thinner than ``tol`` still ends at its ends."""
    px, py = p
    x0, y0 = pts[-1]
    nil = max(tol, _HAIR * (1.0 + abs(x0) + abs(y0)))
    nil *= nil
    skipped = False
    for x1, y1 in pts:
        dx, dy = x1 - x0, y1 - y0
        c = dx * (py - y0) - dy * (px - x0)
        if c < 0.0:
            ll = dx * dx + dy * dy
            if ll <= nil:
                skipped = True
            elif c * c > tol * tol * ll:
                return False
        x0, y0 = x1, y1
    if skipped:
        x0, y0, x1, y1 = bounds(pts)
        return (x0 - tol <= px <= x1 + tol) and (y0 - tol <= py <= y1 + tol)
    return True


def inside(p: Pt, pts: Sequence[Pt]) -> bool:
    """Whether ``p`` is in any simple polygon (ray casting)."""
    px, py = p
    res = False
    x0, y0 = pts[-1]
    for x1, y1 in pts:
        if (y1 > py) != (y0 > py):
            if px < (x0 - x1) * (py - y1) / ((y0 - y1) or 1e-30) + x1:
                res = not res
        x0, y0 = x1, y1
    return res


def convex_overlap(A: Sequence[Pt], B: Sequence[Pt], depth: float = 0.0
                   ) -> bool:
    """Whether two convex polygons overlap by more than ``depth`` mm (so
    two that only touch along an edge don't)."""
    for P, Q in ((A, B), (B, A)):
        x0, y0 = P[-1]
        for x1, y1 in P:
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy)
            if ln > 1e-12:
                # the outward normal of a counter-clockwise edge
                nx, ny = dy / ln, -dx / ln
                # how far Q reaches in behind this edge of P
                if min((q[0] - x0) * nx + (q[1] - y0) * ny for q in Q) \
                        > -depth:
                    return False
            x0, y0 = x1, y1
    return True


def clip_to_convex(subject: Sequence[Pt], clip: Sequence[Pt]) -> List[Pt]:
    """The part of ``subject`` (any polygon) inside the counter-clockwise
    convex polygon ``clip`` (Sutherland-Hodgman)."""
    out = list(subject)
    x0, y0 = clip[-1]
    for x1, y1 in clip:
        if not out:
            break
        dx, dy = x1 - x0, y1 - y0
        src, out = out, []
        px, py = src[-1]
        dp = dx * (py - y0) - dy * (px - x0)
        for qx, qy in src:
            dq = dx * (qy - y0) - dy * (qx - x0)
            if dq >= 0.0:
                if dp < 0.0:
                    t = dp / (dp - dq)
                    out.append((px + (qx - px) * t, py + (qy - py) * t))
                out.append((qx, qy))
            elif dp >= 0.0:
                t = dp / (dp - dq)
                out.append((px + (qx - px) * t, py + (qy - py) * t))
            px, py, dp = qx, qy, dq
        x0, y0 = x1, y1
    return out if len(out) >= 3 else []


def clip_segment_to_convex(p: Pt, q: Pt, clip: Sequence[Pt]
                           ) -> Optional[Tuple[Pt, Pt]]:
    """The part of the segment ``p``-``q`` inside a convex polygon."""
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    x0, y0 = clip[-1]
    for x1, y1 in clip:
        ex, ey = x1 - x0, y1 - y0
        num = ex * (p[1] - y0) - ey * (p[0] - x0)      # p's side (left +)
        den = ex * dy - ey * dx
        if abs(den) < 1e-15:
            if num < 0.0:
                return None
        else:
            t = -num / den
            if den > 0.0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return None
        x0, y0 = x1, y1
    return ((p[0] + dx * t0, p[1] + dy * t0), (p[0] + dx * t1, p[1] + dy * t1))

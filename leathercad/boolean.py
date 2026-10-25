"""Polygon boolean operations: union, difference, intersection.

Greiner–Hormann clipping on simple (non-self-intersecting) rings, with
containment/disjoint fallbacks when the outlines don't cross. Rings are lists
of Vec2 without a repeated closing point; results are lists of rings (a
difference that swallows the cutter yields the outer ring plus the cutout
ring -- exactly how a laser wants it).

Shared-edge degeneracies are dodged by growing (or, for intersection,
shrinking) the clip ring a hair (<< laser kerf) and retrying; the result is
then snapped back onto the input vertices.
"""

from __future__ import annotations

from typing import List, Optional

from .geometry import Vec2, point_in_polygon
from .offset import signed_area

_EPS = 1e-9


class _V:
    __slots__ = ("p", "next", "prev", "neighbor", "intersect", "entry",
                 "visited")

    def __init__(self, p: Vec2, intersect: bool = False):
        self.p = p
        self.next = self.prev = None
        self.neighbor = None
        self.intersect = intersect
        self.entry = False
        self.visited = False


def _seg_intersect(a0, a1, b0, b1):
    """Proper crossing of two segments -> (s, t, point) or None. Endpoint
    touches / collinear overlaps return None (handled by perturb-retry)."""
    d1 = a1 - a0
    d2 = b1 - b0
    den = d1.cross(d2)
    if abs(den) < _EPS:
        return None
    s = (b0 - a0).cross(d2) / den
    t = (b0 - a0).cross(d1) / den
    tol = 1e-9
    if tol < s < 1 - tol and tol < t < 1 - tol:
        return s, t, a0 + d1 * s
    if -tol <= s <= 1 + tol and -tol <= t <= 1 + tol:
        return "degenerate"                     # touching -> retry perturbed
    return None


def _link(points: List["_V"]):
    n = len(points)
    for i, v in enumerate(points):
        v.next = points[(i + 1) % n]
        v.prev = points[(i - 1) % n]
    return points[0]


def _build(a: List[Vec2], b: List[Vec2]):
    """Insert crossings into both rings. Returns (listA, listB, n_crossings)
    or None when a degenerate touch demands a perturbed retry."""
    ints_a = [[] for _ in a]                    # per A-edge: (s, node)
    ints_b = [[] for _ in b]
    count = 0
    na, nb = len(a), len(b)
    for i in range(na):
        a0, a1 = a[i], a[(i + 1) % na]
        lo_x, hi_x = min(a0.x, a1.x), max(a0.x, a1.x)
        lo_y, hi_y = min(a0.y, a1.y), max(a0.y, a1.y)
        for j in range(nb):
            b0, b1 = b[j], b[(j + 1) % nb]
            if (max(b0.x, b1.x) < lo_x - _EPS or min(b0.x, b1.x) > hi_x + _EPS
                    or max(b0.y, b1.y) < lo_y - _EPS
                    or min(b0.y, b1.y) > hi_y + _EPS):
                continue
            hit = _seg_intersect(a0, a1, b0, b1)
            if hit is None:
                continue
            if hit == "degenerate":
                return None
            s, t, p = hit
            va = _V(p, True)
            vb = _V(p, True)
            va.neighbor = vb
            vb.neighbor = va
            ints_a[i].append((s, va))
            ints_b[j].append((t, vb))
            count += 1

    def expand(ring, ints):
        out = []
        for i, p in enumerate(ring):
            out.append(_V(p))
            for _, node in sorted(ints[i], key=lambda x: x[0]):
                out.append(node)
        return out

    return _link(expand(a, ints_a)), _link(expand(b, ints_b)), count


def _mark(first: "_V", other: List[Vec2], invert: bool):
    """Alternate entry/exit flags along a ring, starting from containment."""
    status = not point_in_polygon(first.p, other)
    if invert:
        status = not status
    v = first
    while True:
        if v.intersect:
            v.entry = status
            status = not status
        v = v.next
        if v is first:
            break


def _trace(first: "_V") -> List[List[Vec2]]:
    rings = []
    v = first
    nodes = []
    while True:
        if v.intersect:
            nodes.append(v)
        v = v.next
        if v is first:
            break
    for start in nodes:
        if start.visited:
            continue
        ring = [start.p]
        cur = start
        while True:
            cur.visited = True
            cur.neighbor.visited = True
            if cur.entry:
                while True:
                    cur = cur.next
                    ring.append(cur.p)
                    if cur.intersect:
                        break
            else:
                while True:
                    cur = cur.prev
                    ring.append(cur.p)
                    if cur.intersect:
                        break
            cur = cur.neighbor
            if cur.visited:
                break
        # drop the duplicated closing point and any hairline slivers
        if (ring[0] - ring[-1]).length() < 1e-6:
            ring.pop()
        if len(ring) >= 3 and abs(signed_area(ring)) > 1e-6:
            rings.append(ring)
    return rings


def _clean(ring: List[Vec2]) -> List[Vec2]:
    out = []
    for p in ring:
        if not out or (p - out[-1]).length() > 1e-9:
            out.append(p)
    if len(out) >= 2 and (out[0] - out[-1]).length() < 1e-9:
        out.pop()
    return out


def _fallback(op: str, a, b) -> Optional[List[List[Vec2]]]:
    """No crossings: containment / disjoint results."""
    a_in_b = point_in_polygon(a[0], b)
    b_in_a = point_in_polygon(b[0], a)
    if op == "union":
        if b_in_a:
            return [a]
        if a_in_b:
            return [b]
        return [a, b]
    if op == "difference":
        if b_in_a:
            return [a, b]          # cutter becomes a cutout ring
        if a_in_b:
            return []              # subject swallowed entirely
        return [a]
    if op == "intersection":
        if b_in_a:
            return [b]
        if a_in_b:
            return [a]
        return []
    raise ValueError(f"unknown op {op!r}")


def _perturbed(ring: List[Vec2], d: float, twist: bool) -> List[Vec2]:
    """``ring`` grown (d > 0) or shrunk (d < 0) about its centroid by about
    |d| mm, optionally turned a hair as well."""
    import math
    cx = sum(p.x for p in ring) / len(ring)
    cy = sum(p.y for p in ring) / len(ring)
    r = max((p - Vec2(cx, cy)).length() for p in ring) or 1.0
    k = 1.0 + d / r
    a = abs(d) / r if twist else 0.0
    c, s = math.cos(a), math.sin(a)
    out = []
    for p in ring:
        x, y = (p.x - cx) * k, (p.y - cy) * k
        out.append(Vec2(cx + x * c - y * s, cy + x * s + y * c))
    return out


_SNAP = 2e-5        # mm: perturbation-sized; far below any laser kerf


def _snap_and_tidy(rings, originals) -> List[List[Vec2]]:
    """Put result vertices that are a perturbation away from an input vertex
    back on it exactly, then drop the hairline leftovers: repeated points
    and zero-width out-and-back spikes."""
    grid = {}
    for ring in originals:
        for p in ring:
            grid.setdefault((round(p.x / _SNAP), round(p.y / _SNAP)), []).append(p)

    def snap(p):
        kx, ky = round(p.x / _SNAP), round(p.y / _SNAP)
        best, bd = p, _SNAP
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in grid.get((kx + dx, ky + dy), ()):
                    d = (q - p).length()
                    if d <= bd:
                        best, bd = q, d
        return best

    out = []
    for ring in rings:
        pts = [snap(p) for p in ring]
        changed = True
        while changed and len(pts) >= 3:
            changed = False
            n = len(pts)
            for i in range(n):
                a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
                v0, v1 = b - a, c - b
                l0, l1 = v0.length(), v1.length()
                spike = (l0 > 0 and l1 > 0 and abs(v0.cross(v1)) <= 1e-9 * l0 * l1
                         + 1e-12 and v0.dot(v1) < 0)
                if l0 <= 1e-9 or spike:
                    del pts[i]
                    changed = True
                    break
        if len(pts) >= 3 and abs(signed_area(pts)) > 1e-6:
            out.append(pts)
    return out


def boolean_op(op: str, a: List[Vec2], b: List[Vec2]) -> List[List[Vec2]]:
    """union / difference (a - b) / intersection of two simple rings."""
    if op not in ("union", "difference", "intersection"):
        raise ValueError(f"unknown op {op!r}")
    a = _clean(list(a))
    b = _clean(list(b))
    if len(a) < 3 or len(b) < 3:
        return [r for r in (a, b) if len(r) >= 3]
    orig_b = b
    # Shared edges / touching vertices are degenerate for the clipper. Nudge
    # the second ring so the touch becomes a real overlap of the right kind:
    # grown for union and difference (two pieces sharing an edge then fuse;
    # a cutter flush with an edge then cuts clean through it) and shrunk for
    # intersection (a ring flush inside the other then lies inside it). A
    # fixed sideways shift pulled pieces APART when they sat to its far side.
    sign = -1.0 if op == "intersection" else 1.0
    for attempt in range(6):
        built = _build(a, b)
        if built is not None:
            break
        d = sign * 1e-6 * (10 ** (attempt // 2))
        b = _perturbed(orig_b, d, twist=bool(attempt % 2))
    else:
        return _snap_and_tidy(_fallback(op, a, b) or [], [a, orig_b])
    la, lb, count = built
    if count == 0:
        return _snap_and_tidy(_fallback(op, a, b) or [], [a, orig_b])
    if op == "union":
        _mark(la, b, invert=True)
        _mark(lb, a, invert=True)
    elif op == "difference":
        _mark(la, b, invert=True)
        _mark(lb, a, invert=False)
    else:
        _mark(la, b, invert=False)
        _mark(lb, a, invert=False)
    return _snap_and_tidy(_trace(la), [a, orig_b])


def _inside(ring: List[Vec2], other: List[Vec2]) -> bool:
    return all(point_in_polygon(p, other) for p in ring[::max(1, len(ring) // 8)])


def _overlaps(a: List[Vec2], b: List[Vec2]) -> bool:
    return len(boolean_op("intersection", a, b)) > 0


def combine(op: str, rings: List[List[Vec2]]) -> List[List[Vec2]]:
    """Fold ``op`` over 2+ rings; the first ring is the subject. Returns the
    resulting pieces followed by any cutout rings (holes inside a piece).

    Every operand acts on EVERY piece so far -- a later cutter reaches a piece
    split off by an earlier one, and a later shape can join two pieces -- and
    cutouts that overlap merge into one, so nothing is cut twice."""
    if not rings:
        return []
    pieces: List[List[Vec2]] = [_clean(list(rings[0]))]
    holes: List[List[Vec2]] = []
    for other in rings[1:]:
        other = _clean(list(other))
        if len(other) < 3:
            continue
        if op == "union":
            merged, keep = other, []
            for p in pieces:
                res = boolean_op("union", p, merged)
                outer = max(res, key=lambda r: abs(signed_area(r))) if res else None
                if outer is not None and len(res) >= 1 and not (
                        len(res) == 2 and not _inside(res[1], res[0])
                        and not _inside(res[0], res[1])):
                    merged = outer
                    holes += [r for r in res if r is not outer]
                else:
                    keep.append(p)
            pieces = keep + [merged]
            # a shape laid over a cutout fills (part of) it
            holes = [h2 for h in holes
                     for h2 in boolean_op("difference", h, other)]
        elif op == "difference":
            # fold every cutout this cutter touches into it first, so they
            # come out as one hole (or one notch) instead of overlapping rings
            cutter, left = other, []
            for h in holes:
                if _overlaps(h, cutter):
                    u = boolean_op("union", h, cutter)
                    cutter = max(u, key=lambda r: abs(signed_area(r)))
                else:
                    left.append(h)
            holes = left
            new = []
            for p in pieces:
                res = boolean_op("difference", p, cutter)
                for r in res:
                    if r is not p and _inside(r, p) and _inside(cutter, p) \
                            and abs(abs(signed_area(r)) - abs(signed_area(cutter))) < 1e-6:
                        holes.append(r)          # cutter wholly inside: a cutout
                    else:
                        new.append(r)
            pieces = new
        else:                                     # intersection
            pieces = [r for p in pieces
                      for r in boolean_op("intersection", p, other)]
            holes = [r for h in holes for r in boolean_op("intersection", h, other)]
        if not pieces:
            return []
    # a cutout only exists inside a piece
    holes = [h for h in holes if any(_inside(h, p) for p in pieces)]
    return pieces + holes

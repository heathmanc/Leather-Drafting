"""Polygon boolean operations: union, difference, intersection.

Greiner–Hormann clipping on simple (non-self-intersecting) rings, with
containment/disjoint fallbacks when the outlines don't cross. Rings are lists
of Vec2 without a repeated closing point; results are lists of rings (a
difference that swallows the cutter yields the outer ring plus the cutout
ring -- exactly how a laser wants it).

Shared-edge degeneracies are dodged by nudging the clip ring a hair
(<< laser kerf) and retrying.
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


def boolean_op(op: str, a: List[Vec2], b: List[Vec2]) -> List[List[Vec2]]:
    """union / difference (a - b) / intersection of two simple rings."""
    a = _clean(list(a))
    b = _clean(list(b))
    if len(a) < 3 or len(b) < 3:
        return [r for r in (a, b) if len(r) >= 3]
    for attempt in range(4):
        built = _build(a, b)
        if built is not None:
            break
        # touching vertices/edges: nudge the clip ring imperceptibly
        d = 1e-7 * (10 ** attempt)
        b = [Vec2(p.x + d * 1.7, p.y + d * 2.3) for p in b]
    else:
        return _fallback(op, a, b)
    la, lb, count = built
    if count == 0:
        return _fallback(op, a, b)
    if op == "union":
        _mark(la, b, invert=True)
        _mark(lb, a, invert=True)
    elif op == "difference":
        _mark(la, b, invert=True)
        _mark(lb, a, invert=False)
    elif op == "intersection":
        _mark(la, b, invert=False)
        _mark(lb, a, invert=False)
    else:
        raise ValueError(f"unknown op {op!r}")
    return _trace(la)


def combine(op: str, rings: List[List[Vec2]]) -> List[List[Vec2]]:
    """Fold ``op`` over 2+ rings. The first ring is the subject; after each
    step the largest-area ring carries on as the subject and any extra rings
    (disjoint parts, cutouts) are kept alongside."""
    if not rings:
        return []
    subject = rings[0]
    extras: List[List[Vec2]] = []
    for other in rings[1:]:
        res = boolean_op(op, subject, other)
        if not res:
            return extras          # subject vanished (difference/intersection)
        res.sort(key=lambda r: -abs(signed_area(r)))
        subject = res[0]
        extras.extend(res[1:])
    return [subject] + extras

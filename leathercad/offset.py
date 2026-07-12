"""Polygon offsetting -- used to inset the stitch line from the cut edge.

Leather seams sit a few millimetres in from the edge (the "stitch line" or
"stitching margin"). Given a closed outline we offset it inward by that margin
using the standard edge-offset-and-reintersect method, which produces correct
mitered corners. It is robust for convex and gently concave shapes (the vast
majority of leather panels); very deep concavities relative to the offset can
self-intersect, which we do not attempt to clean up here.
"""

from __future__ import annotations

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


def offset_closed_inward(points: Sequence[Vec2], dist: float) -> List[Vec2]:
    """Return the outline offset inward by ``dist`` (mm), as a closed ring
    (first point repeated at the end). ``dist`` <= 0 returns the input."""
    ring = _unique_ring(points)
    n = len(ring)
    if n < 3 or dist <= _EPS:
        return list(points)

    sign = 1.0 if signed_area(ring) > 0 else -1.0  # +1 for CCW

    # Offset every edge inward.
    offset_edges = []
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        d = (b - a)
        if d.length() < _EPS:
            continue
        d = d.normalized()
        inward = Vec2(-d.y, d.x) * (dist * sign)  # left normal for CCW
        offset_edges.append((a + inward, b + inward))

    m = len(offset_edges)
    new_pts: List[Vec2] = []
    for i in range(m):
        prev = offset_edges[(i - 1) % m]
        cur = offset_edges[i]
        p = _line_intersection(prev[0], prev[1], cur[0], cur[1])
        new_pts.append(p if p is not None else cur[0])

    new_pts.append(new_pts[0])
    return new_pts

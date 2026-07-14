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


def offset_closed(points: Sequence[Vec2], dist: float) -> List[Vec2]:
    """Offset a closed outline by ``dist`` mm: ``dist`` > 0 grows the shape
    OUTWARD (seam/glue allowance), ``dist`` < 0 shrinks it inward. Returns a
    closed ring (first point repeated). Mitered corners via edge re-intersection.
    """
    ring = _unique_ring(points)
    n = len(ring)
    if n < 3 or abs(dist) < _EPS:
        return list(points)
    sign = 1.0 if signed_area(ring) > 0 else -1.0     # +1 for CCW
    offset_edges = []
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        d = b - a
        if d.length() < _EPS:
            continue
        d = d.normalized()
        inward = Vec2(-d.y, d.x) * sign               # unit inward normal
        move = inward * (-dist)                        # +dist -> outward
        offset_edges.append((a + move, b + move))
    m = len(offset_edges)
    new_pts: List[Vec2] = []
    for i in range(m):
        prev = offset_edges[(i - 1) % m]
        cur = offset_edges[i]
        p = _line_intersection(prev[0], prev[1], cur[0], cur[1])
        new_pts.append(p if p is not None else cur[0])
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

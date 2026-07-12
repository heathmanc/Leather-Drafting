"""Drawable shapes with a rigid transform (move / rotate / mirror).

Every shape can produce its outline as a ``Path`` in *local* coordinates and as
a flattened polyline in *world* coordinates (after the transform). Because the
transform is a rigid isometry (rotation + translation + optional mirror), arc
lengths are preserved, so corner positions used for stitch registration stay
valid in world space without recomputation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2
from .path import Path, PathBuilder, DEFAULT_FLATNESS


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------
@dataclass
class Transform:
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0   # degrees, CCW
    mirror_x: bool = False  # flip across local Y axis (laser from the back)

    def apply(self, p: Vec2) -> Vec2:
        px = -p.x if self.mirror_x else p.x
        py = p.y
        r = math.radians(self.rotation)
        c, s = math.cos(r), math.sin(r)
        return Vec2(self.x + px * c - py * s,
                    self.y + px * s + py * c)

    def apply_dir(self, d: Vec2) -> Vec2:
        dx = -d.x if self.mirror_x else d.x
        dy = d.y
        r = math.radians(self.rotation)
        c, s = math.cos(r), math.sin(r)
        return Vec2(dx * c - dy * s, dx * s + dy * c)


# ---------------------------------------------------------------------------
# Shape base
# ---------------------------------------------------------------------------
_shape_counter = [0]


def _next_id(prefix: str) -> str:
    _shape_counter[0] += 1
    return f"{prefix}{_shape_counter[0]}"


@dataclass
class Shape:
    name: str = ""
    transform: Transform = field(default_factory=Transform)
    layer: str = "cut"
    # Stitching settings (None -> no stitching on this shape).
    stitch: Optional["StitchSettings"] = None
    opacity: float = 1.0
    shape_id: str = field(default_factory=lambda: _next_id("shape"))
    kind: str = "shape"

    # -- geometry (subclasses implement local_path) ---------------------
    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:  # pragma: no cover
        raise NotImplementedError

    def world_polyline(self, flatness: float = DEFAULT_FLATNESS
                       ) -> Tuple[List[Vec2], List[Vec2], bool]:
        """Return (outline points, corner points, closed) in world space."""
        path = self.local_path(flatness)
        pts_local = path.flatten(flatness)
        pts_world = [self.transform.apply(p) for p in pts_local]
        corners_world = [self.transform.apply(p) for p in path.corner_points]
        return pts_world, corners_world, path.closed

    def bounds(self) -> Tuple[float, float, float, float]:
        pts, _, _ = self.world_polyline()
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# Concrete shapes
# ---------------------------------------------------------------------------
@dataclass
class Rectangle(Shape):
    width: float = 50.0
    height: float = 30.0
    corner_radius: float = 0.0
    kind: str = "rectangle"

    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        w, h, r = self.width, self.height, self.corner_radius
        hw, hh = w / 2.0, h / 2.0
        r = max(0.0, min(r, min(hw, hh)))
        b = PathBuilder()
        if r <= 1e-9:
            b.move_to(-hw, -hh)
            b.line_to(hw, -hh, corner=True)
            b.line_to(hw, hh, corner=True)
            b.line_to(-hw, hh, corner=True)
            b.close(corner=True)
        else:
            # rounded rect, smooth (no forced corner holes)
            b.move_to(-hw + r, -hh)
            b.line_to(hw - r, -hh)
            b.arc_to(hw - r, -hh + r, -math.pi / 2, 0.0)
            b.line_to(hw, hh - r)
            b.arc_to(hw - r, hh - r, 0.0, math.pi / 2)
            b.line_to(-hw + r, hh)
            b.arc_to(-hw + r, hh - r, math.pi / 2, math.pi)
            b.line_to(-hw, -hh + r)
            b.arc_to(-hw + r, -hh + r, math.pi, 1.5 * math.pi)
            b.close(corner=False)
        return b.build(flatness)


@dataclass
class Ellipse(Shape):
    rx: float = 25.0
    ry: float = 15.0
    kind: str = "ellipse"

    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        # Adaptive step from the smaller radius.
        rmin = max(1e-3, min(self.rx, self.ry))
        ratio = max(-1.0, min(1.0, 1.0 - flatness / rmin))
        step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi
        n = max(16, int(math.ceil(2 * math.pi / max(step, 1e-3))))
        b = PathBuilder()
        for i in range(n + 1):
            a = 2 * math.pi * i / n
            x = self.rx * math.cos(a)
            y = self.ry * math.sin(a)
            if i == 0:
                b.move_to(x, y)
            else:
                b.line_to(x, y)
        b.close(corner=False)
        return b.build(flatness)


@dataclass
class Circle(Ellipse):
    kind: str = "circle"

    def __post_init__(self):
        # keep rx/ry in sync via radius property
        pass

    @property
    def radius(self) -> float:
        return self.rx

    @radius.setter
    def radius(self, r: float) -> None:
        self.rx = r
        self.ry = r


@dataclass
class Polygon(Shape):
    points: List[Vec2] = field(default_factory=list)
    corner_radius: float = 0.0
    close_path: bool = True
    sharp_corners: bool = True
    kind: str = "polygon"

    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        pts = self.points
        if len(pts) < 2:
            return Path()
        if self.corner_radius > 1e-9 and len(pts) >= 3:
            return _filleted_polygon(pts, self.corner_radius, self.close_path,
                                     flatness)
        b = PathBuilder()
        b.move_to(pts[0].x, pts[0].y)
        for p in pts[1:]:
            b.line_to(p.x, p.y, corner=self.sharp_corners)
        if self.close_path:
            b.close(corner=self.sharp_corners)
        return b.build(flatness)


@dataclass
class PathShape(Shape):
    """A freeform path (open or closed) provided as raw points."""
    points: List[Vec2] = field(default_factory=list)
    close_path: bool = False
    kind: str = "path"

    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        pts = self.points
        if len(pts) < 2:
            return Path()
        b = PathBuilder()
        b.move_to(pts[0].x, pts[0].y)
        for p in pts[1:]:
            b.line_to(p.x, p.y)
        if self.close_path:
            b.close(corner=False)
        return b.build(flatness)


# ---------------------------------------------------------------------------
# Fillet helper for polygons
# ---------------------------------------------------------------------------
def _filleted_polygon(pts: Sequence[Vec2], radius: float, closed: bool,
                      flatness: float) -> Path:
    n = len(pts)
    b = PathBuilder()
    seq = range(n) if closed else range(n)
    first = True

    def corner_arc(prev: Vec2, cur: Vec2, nxt: Vec2):
        v0 = (prev - cur)
        v1 = (nxt - cur)
        l0, l1 = v0.length(), v1.length()
        if l0 < 1e-9 or l1 < 1e-9:
            return None
        u0, u1 = v0 / l0, v1 / l1
        ang = math.acos(max(-1.0, min(1.0, u0.dot(u1))))
        if ang < 1e-6 or abs(ang - math.pi) < 1e-6:
            return None
        # tangent distance from the corner along each edge
        t = radius / math.tan(ang / 2.0)
        t = min(t, l0 * 0.5, l1 * 0.5)
        r_eff = t * math.tan(ang / 2.0)
        p_start = cur + u0 * t
        p_end = cur + u1 * t
        # arc centre: along the angle bisector
        bis = (u0 + u1)
        if bis.length() < 1e-9:
            return None
        bis = bis.normalized()
        center_dist = r_eff / math.sin(ang / 2.0)
        center = cur + bis * center_dist
        a0 = (p_start - center).angle()
        a1 = (p_end - center).angle()
        # Sweep direction matches the polygon's turn. u0/u1 point back along
        # the edges, so the turn sign is the negative of their cross product.
        ccw = (u0.cross(u1) < 0)
        return p_start, p_end, center, r_eff, a0, a1, ccw

    if not closed:
        # keep first and last vertices sharp, fillet the interior ones
        b.move_to(pts[0].x, pts[0].y)
        for i in range(1, n - 1):
            arc = corner_arc(pts[i - 1], pts[i], pts[i + 1])
            if arc is None:
                b.line_to(pts[i].x, pts[i].y)
                continue
            ps, pe, c, r_eff, a0, a1, ccw = arc
            b.line_to(ps.x, ps.y)
            b.arc_to(c.x, c.y, a0, a1, ccw)
        b.line_to(pts[-1].x, pts[-1].y)
        return b.build(flatness)

    # closed: fillet every vertex
    for i in seq:
        prev = pts[(i - 1) % n]
        cur = pts[i]
        nxt = pts[(i + 1) % n]
        arc = corner_arc(prev, cur, nxt)
        if arc is None:
            if first:
                b.move_to(cur.x, cur.y)
                first = False
            else:
                b.line_to(cur.x, cur.y)
            continue
        ps, pe, c, r_eff, a0, a1, ccw = arc
        if first:
            b.move_to(ps.x, ps.y)
            first = False
        else:
            b.line_to(ps.x, ps.y)
        b.arc_to(c.x, c.y, a0, a1, ccw)
    b.close(corner=False)
    return b.build(flatness)


# Imported here to avoid a circular import at module top.
from .stitchsettings import StitchSettings  # noqa: E402

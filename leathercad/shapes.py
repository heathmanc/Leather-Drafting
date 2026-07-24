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

    def inverse_apply(self, p: Vec2) -> Vec2:
        """World point -> local point (inverse of ``apply``)."""
        q = Vec2(p.x - self.x, p.y - self.y)
        return self.inverse_apply_dir(q)

    def inverse_apply_dir(self, d: Vec2) -> Vec2:
        """World direction -> local direction (inverse of ``apply_dir``)."""
        r = math.radians(-self.rotation)
        c, s = math.cos(r), math.sin(r)
        u = Vec2(d.x * c - d.y * s, d.x * s + d.y * c)
        return Vec2(-u.x if self.mirror_x else u.x, u.y)


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
    # When set, these baked holes (in LOCAL coords) are drawn and moved with the
    # shape instead of being computed from ``stitch`` -- the "grouped" state.
    # Each entry is a stitching.Hole. Never redistributed.
    baked_holes: Optional[list] = None
    # A construction/guide line: drawn dashed, used only as a snap/alignment
    # reference, never cut or exported.
    construction: bool = False
    # Move-group membership: items sharing a group_id move together. None -> not
    # grouped.
    group_id: Optional[str] = None
    # Fold line: when ``fold_dir`` is set ('front' or 'back'), this line is a
    # score/crease the 3D preview folds a single piece about, by ``fold_angle``
    # degrees (180 = folded flat). Empty ``fold_dir`` -> an ordinary line.
    fold_dir: str = ""
    fold_angle: float = 90.0

    @property
    def is_fold_line(self) -> bool:
        return self.fold_dir in ("front", "back")

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
        n = len(pts)

        def is_corner(i: int) -> bool:
            # Only a genuine turn forces a stitch hole. A collinear vertex
            # (e.g. a node the user inserted on a straight edge without moving
            # it) is NOT a corner, so it leaves the stitch pattern unchanged.
            if not self.sharp_corners:
                return False
            if not self.close_path and (i == 0 or i == n - 1):
                return True          # the ends of an open path always anchor
            return _is_real_corner(pts[(i - 1) % n], pts[i], pts[(i + 1) % n])

        b = PathBuilder()
        b.move_to(pts[0].x, pts[0].y)
        for i in range(1, n):
            b.line_to(pts[i].x, pts[i].y, corner=is_corner(i))
        if self.close_path:
            b.close(corner=is_corner(0))
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
def _is_real_corner(a: Vec2, b: Vec2, c: Vec2, tol_deg: float = 1.0) -> bool:
    """True if the vertex ``b`` is a genuine turn (not collinear with a→b→c).

    A node inserted on a straight edge is (nearly) collinear, so it should not
    force a stitch hole -- otherwise the stitch pattern shifts even though the
    outline did not change.
    """
    v0 = b - a
    v1 = c - b
    l0 = v0.length()
    l1 = v1.length()
    if l0 < 1e-9 or l1 < 1e-9:
        return True
    cosang = max(-1.0, min(1.0, (v0.x * v1.x + v0.y * v1.y) / (l0 * l1)))
    return math.degrees(math.acos(cosang)) > tol_deg


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


# ---------------------------------------------------------------------------
# EditablePath: keeps line/arc segments so curves stay editable by node
# ---------------------------------------------------------------------------
def arc_through(p0: Vec2, pm: Vec2, p1: Vec2):
    """Circle through 3 points -> (center, radius, a0, a1, ccw) or None if the
    points are (nearly) collinear."""
    ax, ay = p0.x, p0.y
    bx, by = pm.x, pm.y
    cx, cy = p1.x, p1.y
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    center = Vec2(ux, uy)
    r = (p0 - center).length()
    a0 = (p0 - center).angle()
    a1 = (p1 - center).angle()
    am = (pm - center).angle()
    two_pi = 2.0 * math.pi
    da1 = (a1 - a0) % two_pi
    dam = (am - a0) % two_pi
    ccw = dam < da1     # going CCW from a0 we reach the mid before the end
    return center, r, a0, a1, ccw


@dataclass
class Edge:
    kind: str = "line"            # "line", "arc" or "bezier"
    mid: Optional[Vec2] = None    # arc: a point the arc passes through (local)
    # cubic-bezier control points (local coords) for kind == "bezier":
    # c1 pulls out of the start node, c2 pulls into the end node.
    c1: Optional[Vec2] = None
    c2: Optional[Vec2] = None


@dataclass
class EditablePath(Shape):
    """A path of on-path ``nodes`` joined by line/arc ``edges``. Arcs are kept
    (not flattened) so every node -- including each arc's midpoint -- is
    individually editable. edge[i] joins nodes[i] -> nodes[i+1] (wrapping when
    ``closed``)."""
    nodes: List[Vec2] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    closed: bool = True
    kind: str = "editpath"

    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        n = len(self.nodes)
        if n < 2:
            return Path()
        b = PathBuilder()
        b.move_to(self.nodes[0].x, self.nodes[0].y)
        m = len(self.edges)
        for i in range(m):
            a = self.nodes[i]
            nb = self.nodes[(i + 1) % n]
            edge = self.edges[i]
            if edge.kind == "arc" and edge.mid is not None:
                arc = arc_through(a, edge.mid, nb)
                if arc is not None:
                    c, r, a0, a1, ccw = arc
                    b.arc_to(c.x, c.y, a0, a1, ccw)
                    continue
            if edge.kind == "bezier" and edge.c1 is not None and edge.c2 is not None:
                b.cubic_to(edge.c1.x, edge.c1.y, edge.c2.x, edge.c2.y, nb.x, nb.y)
                continue
            b.line_to(nb.x, nb.y)
        if self.closed:
            b.close(corner=False)
        return b.build(flatness)

    # -- constructors that keep arcs -----------------------------------
    @staticmethod
    def from_rounded_rect(w: float, h: float, r: float) -> "EditablePath":
        hw, hh = w / 2.0, h / 2.0
        r = max(0.0, min(r, min(hw, hh)))
        d = r * math.sin(math.pi / 4)  # arc-midpoint offset from the corner box
        nodes = [
            Vec2(-hw + r, -hh), Vec2(hw - r, -hh),          # bottom edge
            Vec2(hw, -hh + r), Vec2(hw, hh - r),            # right edge
            Vec2(hw - r, hh), Vec2(-hw + r, hh),            # top edge
            Vec2(-hw, hh - r), Vec2(-hw, -hh + r),          # left edge
        ]
        # corner centres
        br = Vec2(hw - r, -hh + r)
        tr = Vec2(hw - r, hh - r)
        tl = Vec2(-hw + r, hh - r)
        bl = Vec2(-hw + r, -hh + r)
        edges = [
            Edge("line"),
            Edge("arc", Vec2(br.x + d, br.y - d)),          # bottom-right
            Edge("line"),
            Edge("arc", Vec2(tr.x + d, tr.y + d)),          # top-right
            Edge("line"),
            Edge("arc", Vec2(tl.x - d, tl.y + d)),          # top-left
            Edge("line"),
            Edge("arc", Vec2(bl.x - d, bl.y - d)),          # bottom-left
        ]
        return EditablePath(nodes=nodes, edges=edges, closed=True)

    @staticmethod
    def from_bezier(anchors: List[Vec2], out_handles: List[Optional[Vec2]],
                    closed: bool) -> "EditablePath":
        """Build a smooth path from anchor points and their *out* handle offsets.

        ``out_handles[i]`` is the vector from ``anchors[i]`` to that anchor's
        outgoing control point (``None`` / zero = a sharp corner). Each anchor's
        incoming control point is the mirror of its out handle, so an anchor with
        a handle is smooth. Consecutive anchors are joined by a cubic bezier when
        either endpoint has a handle, otherwise by a straight line.
        """
        nodes = list(anchors)
        n = len(nodes)
        if n < 2:
            return EditablePath(nodes=nodes, edges=[], closed=False)
        m = n if closed else n - 1

        def h(i):
            v = out_handles[i] if i < len(out_handles) else None
            return v if (v is not None and v.length() > 1e-9) else None

        edges: List[Edge] = []
        for i in range(m):
            a = nodes[i]
            b = nodes[(i + 1) % n]
            ho = h(i)                          # out of the start anchor
            hi = h((i + 1) % n)                # out of the end anchor -> mirror in
            if ho is None and hi is None:
                edges.append(Edge("line"))
                continue
            c1 = a + (ho if ho is not None else Vec2(0.0, 0.0))
            c2 = b - (hi if hi is not None else Vec2(0.0, 0.0))
            edges.append(Edge("bezier", c1=c1, c2=c2))
        return EditablePath(nodes=nodes, edges=edges, closed=closed)

    @staticmethod
    def from_ellipse(rx: float, ry: float) -> "EditablePath":
        c = math.cos(math.pi / 4)
        nodes = [Vec2(rx, 0), Vec2(0, ry), Vec2(-rx, 0), Vec2(0, -ry)]
        edges = [
            Edge("arc", Vec2(rx * c, ry * c)),
            Edge("arc", Vec2(-rx * c, ry * c)),
            Edge("arc", Vec2(-rx * c, -ry * c)),
            Edge("arc", Vec2(rx * c, -ry * c)),
        ]
        return EditablePath(nodes=nodes, edges=edges, closed=True)


# Imported here to avoid a circular import at module top.
from .stitchsettings import StitchSettings  # noqa: E402

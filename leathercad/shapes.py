"""Drawable shapes with a rigid transform (move / rotate / mirror).

Every shape can produce its outline as a ``Path`` in *local* coordinates and as
a flattened polyline in *world* coordinates (after the transform). Because the
transform is a rigid isometry (rotation + translation + optional mirror), arc
lengths are preserved, so corner positions used for stitch registration stay
valid in world space without recomputation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields as dc_fields, is_dataclass
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
    # Which edges carry stitching: one flag per ``local_path().segments``
    # entry, False for an edge that isn't sewn (a pocket's open mouth). None
    # (or a list of the wrong length) stitches every edge.
    stitch_edges: Optional[List[bool]] = None
    # Hardware: the catalogue key this cut was placed for (see hardware.py)
    # and the radius (mm, about the shape's origin) stitch holes keep clear
    # of -- 0 for none. Any shape can be given a keep-out.
    hardware: str = ""
    keepout: float = 0.0
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

    # -- geometry (subclasses implement _build_local_path) --------------
    def _build_local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:  # pragma: no cover
        raise NotImplementedError

    def geometry_key(self) -> tuple:
        """Everything ``local_path`` reads, as one comparable tuple: the
        shape's own size / point fields, not its placement, layer, stitching
        or name. Cheap next to building the path, and exact -- the panel and
        the node handles assign these fields directly, so there is no setter
        to hook; the key is compared on every call instead."""
        key = []
        for f in dc_fields(self):
            if f.name in _NON_GEOMETRY_FIELDS:
                continue
            key.append(_freeze(getattr(self, f.name)))
        return tuple(key)

    def local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        """The outline in local coordinates. Cached on the shape: the same
        ``Path`` object comes back until a geometry field changes, so the
        scene item, the stitcher, the snap index and the export (which each
        used to rebuild and re-flatten it) share one build -- and can tell by
        identity that nothing moved."""
        key = (flatness, self.geometry_key())
        cache = self.__dict__.get("_lp_cache")
        if cache is not None and cache[0] == key:
            return cache[1]
        path = self._build_local_path(flatness)
        self.__dict__["_lp_cache"] = (key, path)
        self.__dict__.pop("_bounds_cache", None)
        return path

    def world_polyline(self, flatness: float = DEFAULT_FLATNESS
                       ) -> Tuple[List[Vec2], List[Vec2], bool]:
        """Return (outline points, corner points, closed) in world space."""
        path = self.local_path(flatness)
        pts_local = path.flatten(flatness)
        pts_world = [self.transform.apply(p) for p in pts_local]
        corners_world = [self.transform.apply(p) for p in path.corner_points]
        return pts_world, corners_world, path.closed

    def bounds(self) -> Tuple[float, float, float, float]:
        """World bounding box. Cached with the path: a dimension re-anchor,
        a keep-out test and a nest each asked for it, and each flattened and
        re-transformed the whole outline to answer."""
        t = self.transform
        path = self.local_path()          # refreshes the geometry cache
        key = (id(path), t.x, t.y, t.rotation, bool(t.mirror_x))
        bc = self.__dict__.get("_bounds_cache")
        if bc is not None and bc[0] == key:
            return bc[1]
        pts = [t.apply(p) for p in path.flatten()]
        if not pts:                     # a path with no points yet
            pts = [t.apply(Vec2(0.0, 0.0))]
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        box = (min(xs), min(ys), max(xs), max(ys))
        self.__dict__["_bounds_cache"] = (key, box)
        return box


# Fields of every Shape that local_path() never reads (placement, styling,
# stitching, identity). Anything else a subclass adds is geometry.
_NON_GEOMETRY_FIELDS = frozenset({
    "name", "transform", "layer", "stitch", "opacity", "shape_id", "kind",
    "baked_holes", "stitch_edges", "hardware", "keepout", "construction",
    "group_id", "fold_dir", "fold_angle",
})


def _freeze(v):
    """A hashable, comparable snapshot of a geometry field's value."""
    if isinstance(v, list):
        return tuple(_freeze(x) for x in v)
    if isinstance(v, tuple):
        return tuple(_freeze(x) for x in v)
    if isinstance(v, Vec2) or v is None or isinstance(v, (float, int, str, bool)):
        return v
    if is_dataclass(v):                    # an Edge (kind, mid, c1, c2)
        return tuple(_freeze(getattr(v, f.name)) for f in dc_fields(v))
    return repr(v)


# ---------------------------------------------------------------------------
# Concrete shapes
# ---------------------------------------------------------------------------
@dataclass
class Rectangle(Shape):
    width: float = 50.0
    height: float = 30.0
    corner_radius: float = 0.0
    kind: str = "rectangle"

    def _build_local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
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

    def _build_local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
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


def copy_shape_attrs(src, dst, *, holes: bool = False):
    """Give ``dst`` the identity ``src`` had beyond its geometry -- name,
    layer, opacity, stitching, construction (guide) flag, group, fold -- so a
    command that rebuilds a shape (trim, join, convert to nodes...) doesn't
    quietly turn a guide into a cut line or drop it out of its group.
    ``holes`` also carries attached holes (only valid in the same local
    frame). Returns ``dst``."""
    import copy
    for name in ("name", "layer", "opacity", "construction", "group_id",
                 "fold_dir", "fold_angle", "hardware", "keepout"):
        if hasattr(src, name):
            setattr(dst, name, getattr(src, name))
    dst.stitch = copy.deepcopy(src.stitch)
    if getattr(src, "stitch_edges", None) is not None:
        dst.stitch_edges = list(src.stitch_edges)   # ignored if it no longer fits
    if holes:
        dst.baked_holes = copy.deepcopy(src.baked_holes)
    return dst


def segment_index_at(sh, world: Vec2, tol: float):
    """Index of the edge of ``sh`` (a ``local_path().segments`` entry) that
    passes within ``tol`` mm of world point ``world``, or None."""
    t = sh.transform
    local = t.inverse_apply(world)
    best, best_d = None, tol
    for i, seg in enumerate(sh.local_path().segments):
        pts = [seg.start()] + seg.flatten()
        for a, b in zip(pts, pts[1:]):
            ab = b - a
            d2 = ab.length_sq()
            k = 0.0 if d2 < 1e-12 else max(0.0, min(1.0, (local - a).dot(ab) / d2))
            d = (a + ab * k - local).length()
            if d < best_d:
                best, best_d = i, d
    return best


def stitched_edge_flags(sh) -> List[bool]:
    """The per-edge stitching flags of ``sh``, sized to its current path
    (every edge on when it has no usable mask)."""
    n = len(sh.local_path().segments)
    m = getattr(sh, "stitch_edges", None)
    return list(m) if (m is not None and len(m) == n) else [True] * n


def edge_mask_snapshot(sh):
    """What an edit that may change ``sh``'s edge count must take BEFORE it
    runs so the sewn / unsewn edges survive it (see ``remap_stitch_edges``):
    ``(segments, flags)``, or None when every edge is sewn anyway."""
    m = getattr(sh, "stitch_edges", None)
    if m is None or all(m):
        return None
    segs = sh.local_path().segments
    if len(m) != len(segs):
        return None
    return segs, list(m)


def _polyline_dist(pts: List[Vec2], q: Vec2) -> float:
    best = float("inf")
    for a, b in zip(pts, pts[1:]):
        ab = b - a
        d2 = ab.length_sq()
        k = 0.0 if d2 < 1e-12 else max(0.0, min(1.0, (q - a).dot(ab) / d2))
        d = (a + ab * k - q).length()
        if d < best:
            best = d
    return best


def _samples_along(pts: List[Vec2], n: int) -> List[Vec2]:
    """``n`` points spread evenly along the inside of a polyline (never its
    very ends, which it shares with its neighbours)."""
    if len(pts) < 2:
        return list(pts)
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + (b - a).length())
    total = cum[-1]
    if total <= 1e-12:
        return [pts[0]]
    out = []
    for k in range(n):
        s = total * (k + 0.5) / n
        i = 1
        while i < len(cum) - 1 and cum[i] < s:
            i += 1
        a, b = pts[i - 1], pts[i]
        seg = cum[i] - cum[i - 1]
        f = 0.0 if seg <= 1e-12 else (s - cum[i - 1]) / seg
        out.append(a + (b - a) * f)
    return out


def remap_stitch_edges(sh, snapshot) -> bool:
    """After a node insert / delete / fillet / node conversion changed the
    edges of ``sh``, carry its sewn / unsewn flags over by WHERE each new
    edge lies on the old outline: an edge is sewn if any part of it ran along
    a sewn edge. Both halves of a split edge keep its flag, an edge merging a
    sewn and an unsewn one is sewn, and the arc a fillet puts between two
    edges follows them. Without this the mask no longer fitted the edge
    count and was silently ignored -- a pocket mouth sewn shut by adding one
    node elsewhere. Returns True when a mask was carried over."""
    if snapshot is None:
        return False
    old_segs, old_flags = snapshot
    old_pl = [[s.start()] + s.flatten() for s in old_segs]
    flags: List[bool] = []
    for seg in sh.local_path().segments:
        pts = [seg.start()] + seg.flatten()
        sewn = False
        for q in _samples_along(pts, 5):
            k = min(range(len(old_pl)), key=lambda i: _polyline_dist(old_pl[i], q))
            if old_flags[k]:
                sewn = True
                break
        flags.append(sewn)
    sh.stitch_edges = None if all(flags) else flags
    return True


@dataclass
class Polygon(Shape):
    points: List[Vec2] = field(default_factory=list)
    corner_radius: float = 0.0
    close_path: bool = True
    sharp_corners: bool = True
    # Per-vertex "this is a real corner" flags, for outlines that mix corners
    # with a flattened curve (a boolean result): there neither rule above
    # fits -- every facet would force a hole, or no corner would. Ignored
    # when it doesn't match the points (then sharp_corners decides).
    corner_mask: Optional[List[bool]] = None
    kind: str = "polygon"

    def _build_local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
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
            mask = self.corner_mask
            if mask is not None and len(mask) == n:
                return bool(mask[i])
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

    def _build_local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        pts = _dedupe_points(self.points, self.close_path)
        n = len(pts)
        if n < 2:
            return Path()
        flags = path_corner_flags(pts, self.close_path)
        b = PathBuilder()
        b.move_to(pts[0].x, pts[0].y)
        for i in range(1, n):
            b.line_to(pts[i].x, pts[i].y, corner=flags[i])
        if self.close_path:
            b.close(corner=flags[0])
        return b.build(flatness)


# A vertex of a free path or an editable path counts as a corner when the
# outline turns more than this. Polygons use 1 degree (a node dropped on a
# straight edge must not shift the holes); paths come from the pen, from
# joins and from imports whose flattened curves turn a few degrees a vertex.
_PATH_CORNER_DEG = 10.0


# Vertices closer than this (mm) are one vertex: a tenth of a micron is
# below any exporter's rounding noise and far below a laser's resolution.
_DEDUPE_MM = 1e-4


def _dedupe_points(pts: List[Vec2], closed: bool) -> List[Vec2]:
    """``pts`` without consecutive repeats (a doubled vertex, the way some
    SVG/DXF exporters write them -- exactly or to within rounding noise --
    is no corner and no edge)."""
    out: List[Vec2] = []
    for p in pts:
        if not out or (p - out[-1]).length() > _DEDUPE_MM:
            out.append(p)
    if closed and len(out) >= 2 and (out[0] - out[-1]).length() <= _DEDUPE_MM:
        out.pop()
    return out


# A turn sharper than this is a corner on its own. Flattened curves turn a
# few degrees a vertex (a coarsely traced one up to ~20), while the facets
# a leatherworker reads as corners -- an octagonal coaster's 45, a chamfer's
# two 45s, a heptagon's 51 -- all lie above it.
_SHARP_CORNER_DEG = 35.0


def path_corner_flags(pts: List[Vec2], closed: bool,
                      tol_deg: float = _PATH_CORNER_DEG,
                      sharp_deg: float = _SHARP_CORNER_DEG,
                      flat_deg: float = 3.0) -> List[bool]:
    """Which vertices of a free polyline are real corners (anchor a stitch
    hole): a sharp turn (more than ``sharp_deg``), or an isolated kink of
    more than ``tol_deg`` between two straight stretches. The gentle,
    repeated turns of a traced or flattened curve -- and the tips of a
    flattened ellipse -- are not corners. Open ends are never flagged (they
    anchor on their own)."""
    n = len(pts)
    turns = [0.0] * n
    for i in range(n):
        if not closed and (i == 0 or i == n - 1):
            continue
        d0 = pts[i] - pts[(i - 1) % n]
        d1 = pts[(i + 1) % n] - pts[i]
        l0, l1 = d0.length(), d1.length()
        if l0 < 1e-9 or l1 < 1e-9:
            continue
        cosang = max(-1.0, min(1.0, d0.dot(d1) / (l0 * l1)))
        turns[i] = math.degrees(math.acos(cosang))
    flags = []
    for i in range(n):
        t = turns[i]
        before = turns[(i - 1) % n] if (closed or i > 0) else 0.0
        after = turns[(i + 1) % n] if (closed or i < n - 1) else 0.0
        flags.append(t > sharp_deg
                     or (t > tol_deg and before < flat_deg and after < flat_deg))
    return flags


# ---------------------------------------------------------------------------
# Fillet helper for polygons
# ---------------------------------------------------------------------------
def _is_real_corner(a: Vec2, b: Vec2, c: Vec2, tol_deg: float = 1.0) -> bool:
    """True if the vertex ``b`` is a genuine turn (not collinear with a→b→c).

    A node inserted on a straight edge is (nearly) collinear, so it should not
    force a stitch hole -- otherwise the stitch pattern shifts even though the
    outline did not change.
    """
    return _is_turn(b - a, c - b, tol_deg)


def _is_turn(v0: Vec2, v1: Vec2, tol_deg: float) -> bool:
    """True if travel direction ``v0`` turns into ``v1`` by more than
    ``tol_deg`` (a zero-length direction counts as a turn)."""
    l0 = v0.length()
    l1 = v1.length()
    if l0 < 1e-9 or l1 < 1e-9:
        return True
    cosang = max(-1.0, min(1.0, (v0.x * v1.x + v0.y * v1.y) / (l0 * l1)))
    return math.degrees(math.acos(cosang)) > tol_deg


def _arc_end_tangents(a: Vec2, mid: Vec2, nb: Vec2):
    """Travel directions at the start and end of the arc a -> mid -> nb, or
    None when the three points are collinear."""
    arc = arc_through(a, mid, nb)
    if arc is None:
        return None
    c, _r, a0, a1, ccw = arc
    sgn = 1.0 if ccw else -1.0
    t0 = Vec2(-math.sin(a0), math.cos(a0)) * sgn
    t1 = Vec2(-math.sin(a1), math.cos(a1)) * sgn
    return t0, t1


def _edge_dir_out(a: Vec2, nb: Vec2, edge) -> Vec2:
    """Direction an edge leaves its start node ``a`` in."""
    if edge.kind == "bezier" and edge.c1 is not None:
        d = edge.c1 - a
        if d.length() > 1e-9:
            return d
        if edge.c2 is not None and (edge.c2 - a).length() > 1e-9:
            return edge.c2 - a
    if edge.kind == "arc" and edge.mid is not None:
        t = _arc_end_tangents(a, edge.mid, nb)
        if t is not None:
            return t[0]
    return nb - a


def _edge_dir_in(a: Vec2, nb: Vec2, edge) -> Vec2:
    """Direction an edge arrives at its end node ``nb`` in."""
    if edge.kind == "bezier" and edge.c2 is not None:
        d = nb - edge.c2
        if d.length() > 1e-9:
            return d
        if edge.c1 is not None and (nb - edge.c1).length() > 1e-9:
            return nb - edge.c1
    if edge.kind == "arc" and edge.mid is not None:
        t = _arc_end_tangents(a, edge.mid, nb)
        if t is not None:
            return t[1]
    return nb - a


def map_baked_holes(sh, fpoint, fdir) -> None:
    """Move a shape's attached holes through ``fpoint`` (and their tangents
    through ``fdir``, re-normalised) -- for an edit that changes the
    outline's size, which must carry the holes along or the seam ends up
    somewhere else than the inset says."""
    holes = getattr(sh, "baked_holes", None)
    if not holes:
        return
    from .stitching import Hole
    out = []
    for h in holes:
        t = fdir(h.tangent)
        length = t.length()
        t = t * (1.0 / length) if length > 1e-12 else h.tangent
        out.append(Hole(fpoint(h.point), t))
    sh.baked_holes = out


def scale_baked_holes(sh, fx: float, fy: float, cx: float = 0.0,
                      cy: float = 0.0) -> None:
    """Scale attached holes by (fx, fy) about the local point (cx, cy)."""
    if abs(fx - 1.0) < 1e-12 and abs(fy - 1.0) < 1e-12:
        return
    map_baked_holes(sh,
                    lambda p: Vec2(cx + (p.x - cx) * fx, cy + (p.y - cy) * fy),
                    lambda v: Vec2(v.x * fx, v.y * fy))


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

    def _build_local_path(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        n = len(self.nodes)
        if n < 2:
            return Path()
        b = PathBuilder()
        b.move_to(self.nodes[0].x, self.nodes[0].y)
        m = len(self.edges)

        def is_corner(j: int) -> bool:
            """Node ``j`` is a corner when the edge arriving and the edge
            leaving point different ways -- a pen click, a joined corner, a
            fillet's neighbours. Smooth joins (collinear handles, an arc met
            tangentially) and open ends are not."""
            if not self.closed and (j == 0 or j == n - 1):
                return False
            e_in = self.edges[(j - 1) % m]
            e_out = self.edges[j % m]
            d_in = _edge_dir_in(self.nodes[(j - 1) % n], self.nodes[j], e_in)
            d_out = _edge_dir_out(self.nodes[j], self.nodes[(j + 1) % n], e_out)
            return _is_turn(d_in, d_out, _PATH_CORNER_DEG)

        for i in range(m):
            a = self.nodes[i]
            j = (i + 1) % n
            nb = self.nodes[j]
            edge = self.edges[i]
            # the closing node is tagged by close(), not by its last edge
            tag_end = j != 0 or not self.closed
            if edge.kind == "arc" and edge.mid is not None:
                arc = arc_through(a, edge.mid, nb)
                if arc is not None:
                    c, r, a0, a1, ccw = arc
                    b.arc_to(c.x, c.y, a0, a1, ccw)   # tangent points anchor
                    continue
            if edge.kind == "bezier" and edge.c1 is not None and edge.c2 is not None:
                b.cubic_to(edge.c1.x, edge.c1.y, edge.c2.x, edge.c2.y, nb.x, nb.y)
                if tag_end and is_corner(j):
                    b.corner()
                continue
            b.line_to(nb.x, nb.y, corner=tag_end and is_corner(j))
        if self.closed:
            b.close(corner=is_corner(0))
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

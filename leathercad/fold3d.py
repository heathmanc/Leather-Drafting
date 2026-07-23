"""Assemble flat leather panels into a folded 3D model.

Pure Python, no Qt and no third-party deps -- the folding maths lives here in
the engine so the GUI only has to *project and paint* it. Everything is in
millimetres, matching the rest of ``leathercad``.

The mental model is a paper net: the flat pattern is the unfolded object, panels
are joined along shared edges (hinges), and "assembling" folds each child panel
up out of its parent's plane by the hinge's dihedral angle. A ``fraction`` in
``[0, 1]`` scales every fold at once, so a viewer can animate flat -> assembled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .geometry import Vec2


@dataclass(frozen=True)
class Vec3:
    """An immutable 3D point / vector in millimetres."""

    x: float
    y: float
    z: float

    def __add__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)

    __rmul__ = __mul__

    def dot(self, o: "Vec3") -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def cross(self, o: "Vec3") -> "Vec3":
        return Vec3(self.y * o.z - self.z * o.y,
                    self.z * o.x - self.x * o.z,
                    self.x * o.y - self.y * o.x)

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalized(self) -> "Vec3":
        n = self.length()
        return Vec3(0.0, 0.0, 0.0) if n == 0.0 else Vec3(
            self.x / n, self.y / n, self.z / n)


@dataclass
class Panel:
    """A flat panel: a closed outline (in pattern mm) plus its stitch holes."""

    id: str
    outline: List[Vec2]
    holes: List[Vec2] = field(default_factory=list)
    name: str = ""


@dataclass
class Hinge:
    """A shared edge that folds ``child`` up out of ``parent``'s plane.

    ``parent_edge`` / ``child_edge`` are the SAME physical edge expressed in each
    panel's own flat coordinates; assembly maps the child edge onto the parent
    edge and then rotates the child about it by ``angle_deg`` (0 = stays flat /
    coplanar, 90 = a right-angle wall, 180 = folded back on itself)."""

    parent: str
    child: str
    parent_edge: Tuple[Vec2, Vec2]
    child_edge: Tuple[Vec2, Vec2]
    angle_deg: float = 90.0


@dataclass
class PlacedPanel:
    """A panel after assembly: its outline and holes as 3D points."""

    id: str
    outline: List[Vec3]
    holes: List[Vec3]
    name: str = ""

    def centroid(self) -> Vec3:
        if not self.outline:
            return Vec3(0.0, 0.0, 0.0)
        n = len(self.outline)
        sx = sum(p.x for p in self.outline)
        sy = sum(p.y for p in self.outline)
        sz = sum(p.z for p in self.outline)
        return Vec3(sx / n, sy / n, sz / n)


# -- placement frame: maps a panel's flat 2D point -> 3D, plus its plane normal
_Frame = Tuple[Callable[[Vec2], Vec3], Vec3]


def _root_frame() -> _Frame:
    """The root panel lies in the z = 0 plane, drawn straight through."""
    return (lambda v: Vec3(v.x, v.y, 0.0), Vec3(0.0, 0.0, 1.0))


def _child_frame(parent: _Frame, hinge: Hinge, fraction: float,
                 body_ref: Optional[Vec2] = None) -> Optional[_Frame]:
    """Build the child's flat->3D map so its hinge edge coincides with the
    parent's (in 3D) and the panel is rotated about that edge by the dihedral
    angle. ``body_ref`` (a point inside the child, e.g. its centroid) picks the
    fold sense so the panel always lifts toward the parent's normal -- otherwise
    panels on opposite sides of the root would fold opposite ways. Returns None
    if either edge is degenerate."""
    place_p, normal_p = parent
    # the hinge axis in 3D = the parent's edge, already placed
    a = place_p(hinge.parent_edge[0])
    b = place_p(hinge.parent_edge[1])
    e1 = (b - a)
    if e1.length() < 1e-9:
        return None
    e1 = e1.normalized()
    n = normal_p.normalized()
    e2 = n.cross(e1)                       # in-parent-plane, perpendicular to edge

    # child edge, in the child's own flat frame
    c0, c1 = hinge.child_edge
    ce = c1 - c0
    if ce.length() < 1e-9:
        return None
    ce1 = ce.normalized()                  # along-edge (2D)
    ce2 = ce1.perp()                        # across-edge (2D, CCW)

    # which side of the edge is the child's body on? fold that side UP (+n).
    side = 1.0
    if body_ref is not None:
        across_body = (body_ref - c0).dot(ce2)
        if across_body < 0.0:
            side = -1.0

    theta = math.radians(hinge.angle_deg) * fraction
    ct, st = math.cos(theta), math.sin(theta)
    # folding rotates the across-edge basis out of the parent plane about e1,
    # toward +n on the child's own side (so every panel folds the same way up)
    e2f = ct * e2 + st * side * n          # child in-plane dir, tilted up
    nf = ct * n - st * side * e2           # child's new plane normal

    def place(v: Vec2) -> Vec3:
        d = v - c0
        along = d.dot(ce1)
        across = d.dot(ce2)
        return a + along * e1 + across * e2f

    return (place, nf)


def _build_tree(hinges: List[Hinge], root: str) -> List[Tuple[str, Hinge]]:
    """Breadth-first spanning order of ``(child_id, hinge)`` from ``root``.
    Each panel is placed once (extra hinges to an already-placed panel are
    ignored -- the first path from the root wins), so cycles can't loop."""
    adj: Dict[str, List[Hinge]] = {}
    for h in hinges:
        adj.setdefault(h.parent, []).append(h)
        # a hinge is undirected: allow traversal from either end
        adj.setdefault(h.child, []).append(
            Hinge(h.child, h.parent, h.child_edge, h.parent_edge, h.angle_deg))
    order: List[Tuple[str, Hinge]] = []
    seen = {root}
    queue = [root]
    while queue:
        cur = queue.pop(0)
        for h in adj.get(cur, []):
            if h.child in seen:
                continue
            seen.add(h.child)
            order.append((h.child, h))
            queue.append(h.child)
    return order


def assemble(panels: Dict[str, Panel], hinges: List[Hinge],
             root: Optional[str] = None, fraction: float = 1.0
             ) -> List[PlacedPanel]:
    """Fold ``panels`` about ``hinges`` and return them as 3D ``PlacedPanel``s.

    ``root`` (default: the first panel) stays in the z = 0 plane; every other
    panel reachable through the hinge graph is folded into place. ``fraction``
    scales all dihedral angles together (0 = flat net, 1 = fully assembled).
    Panels not reachable from the root are returned flat, laid beside it."""
    if not panels:
        return []
    if root is None or root not in panels:
        root = next(iter(panels))

    def _centroid(pts: List[Vec2]) -> Optional[Vec2]:
        if not pts:
            return None
        n = len(pts)
        return Vec2(sum(p.x for p in pts) / n, sum(p.y for p in pts) / n)

    frames: Dict[str, _Frame] = {root: _root_frame()}
    for child_id, hinge in _build_tree(hinges, root):
        if child_id not in panels or hinge.parent not in frames:
            continue
        fr = _child_frame(frames[hinge.parent], hinge, fraction,
                          body_ref=_centroid(panels[child_id].outline))
        if fr is not None:
            frames[child_id] = fr

    placed: List[PlacedPanel] = []
    for pid, panel in panels.items():
        place, _n = frames.get(pid, _root_frame())
        placed.append(PlacedPanel(
            id=pid, name=panel.name,
            outline=[place(v) for v in panel.outline],
            holes=[place(v) for v in panel.holes]))
    return placed


# -- viewing: orbit projection (orthographic) --------------------------------

def rotate_view(p: Vec3, yaw: float, pitch: float) -> Vec3:
    """Rotate a point for the camera: ``yaw`` about the vertical (z) axis then
    ``pitch`` about the horizontal (x) axis. Radians."""
    cy, sy = math.cos(yaw), math.sin(yaw)
    x1 = p.x * cy - p.y * sy
    y1 = p.x * sy + p.y * cy
    z1 = p.z
    cp, sp = math.cos(pitch), math.sin(pitch)
    y2 = y1 * cp - z1 * sp
    z2 = y1 * sp + z1 * cp
    return Vec3(x1, y2, z2)


def project(placed: List[PlacedPanel], yaw: float, pitch: float
            ) -> List[Tuple[PlacedPanel, List[Vec2], List[Vec2], float]]:
    """Orthographic-project every panel for a viewer.

    Returns ``(panel, outline2d, holes2d, depth)`` per panel, sorted back-to-
    front (painter's algorithm) so a simple filled-polygon paint reads as solid.
    The 2D coordinates keep pattern-mm scale; the caller pans / zooms / flips-Y.
    """
    out = []
    for pl in placed:
        rot = [rotate_view(v, yaw, pitch) for v in pl.outline]
        holes = [rotate_view(v, yaw, pitch) for v in pl.holes]
        depth = (sum(v.z for v in rot) / len(rot)) if rot else 0.0
        out.append((pl,
                    [Vec2(v.x, v.y) for v in rot],
                    [Vec2(v.x, v.y) for v in holes],
                    depth))
    out.sort(key=lambda t: t[3])           # far (smaller z) first
    return out

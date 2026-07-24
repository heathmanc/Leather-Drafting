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
             root: Optional[str] = None, fraction: float = 1.0,
             thickness: float = 0.0) -> List[PlacedPanel]:
    """Fold ``panels`` about ``hinges`` and return them as 3D ``PlacedPanel``s.

    ``root`` (default: the first panel) stays in the z = 0 plane; every other
    panel reachable through the hinge graph is folded into place. ``fraction``
    scales all dihedral angles together (0 = flat net, 1 = fully assembled).
    ``thickness`` lifts each panel off the root plane by ``depth * thickness`` so
    layers that fold flat onto each other stack with a visible gap instead of
    z-fighting. Panels not reachable from the root are returned flat beside it."""
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
    depth: Dict[str, int] = {root: 0}
    for child_id, hinge in _build_tree(hinges, root):
        if child_id not in panels or hinge.parent not in frames:
            continue
        fr = _child_frame(frames[hinge.parent], hinge, fraction,
                          body_ref=_centroid(panels[child_id].outline))
        if fr is not None:
            frames[child_id] = fr
            depth[child_id] = depth.get(hinge.parent, 0) + 1

    placed: List[PlacedPanel] = []
    for pid, panel in panels.items():
        place, _n = frames.get(pid, _root_frame())
        dz = depth.get(pid, 0) * thickness      # stack folded layers by thickness

        def lift(v: Vec2, _p=place, _dz=dz) -> Vec3:
            w = _p(v)
            return Vec3(w.x, w.y, w.z + _dz)

        placed.append(PlacedPanel(
            id=pid, name=panel.name,
            outline=[lift(v) for v in panel.outline],
            holes=[lift(v) for v in panel.holes]))
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


# -- single-piece scored folding --------------------------------------------
# A wallet is often ONE piece of leather scored along fold lines. These helpers
# split that single outline along its score lines into numbered panels, hinge
# adjacent panels at each score, and fold each one (front or back) -- reusing the
# same fold maths as multi-panel assembly.

@dataclass
class Fold:
    """A score line across a single piece: the segment ``a``->``b``, how far it
    folds (``angle_deg``; 180 = folded flat onto its neighbour) and which way
    (``direction`` = 'front' or 'back')."""

    a: Vec2
    b: Vec2
    angle_deg: float = 90.0
    direction: str = "front"

    def signed_angle(self) -> float:
        return -self.angle_deg if self.direction == "back" else self.angle_deg


def split_polygon_by_line(poly: List[Vec2], a: Vec2, b: Vec2
                          ) -> Tuple[List[Vec2], List[Vec2]]:
    """Split a convex-ish simple polygon by the infinite line through ``a``,``b``
    into ``(left, right)`` half-polygons (Sutherland-Hodgman clip on each side).
    Either side is ``[]`` if the polygon lies wholly on the other side."""
    nrm = (b - a).perp()                    # points to the "left" half

    def clip(keep_sign: float) -> List[Vec2]:
        out: List[Vec2] = []
        n = len(poly)
        for i in range(n):
            p, q = poly[i], poly[(i + 1) % n]
            dp = (p - a).dot(nrm) * keep_sign
            dq = (q - a).dot(nrm) * keep_sign
            if dp >= -1e-9:
                out.append(p)
            if (dp > 0) != (dq > 0):        # edge crosses the line -> add cut pt
                t = dp / (dp - dq)
                out.append(Vec2(p.x + (q.x - p.x) * t, p.y + (q.y - p.y) * t))
        return out if len(out) >= 3 else []

    return clip(1.0), clip(-1.0)


def _cut_segment(poly: List[Vec2], a: Vec2, b: Vec2
                 ) -> Optional[Tuple[Vec2, Vec2]]:
    """Where the infinite line a->b enters and exits ``poly`` -- the shared hinge
    edge between the two halves. Returns the two boundary-crossing points."""
    nrm = (b - a).perp()
    hits: List[Vec2] = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dp = (p - a).dot(nrm)
        dq = (q - a).dot(nrm)
        if (dp > 0) != (dq > 0) and abs(dp - dq) > 1e-12:
            t = dp / (dp - dq)
            hits.append(Vec2(p.x + (q.x - p.x) * t, p.y + (q.y - p.y) * t))
    if len(hits) < 2:
        return None
    return hits[0], hits[1]


def _edge_span_on_line(poly: List[Vec2], fold: Fold, tol: float = 1e-4
                       ) -> Optional[Tuple[float, float]]:
    """If ``poly`` has an edge lying along the fold's line, return that edge's
    ``(t0, t1)`` extent projected onto the line direction, else None."""
    a, b = fold.a, fold.b
    d = (b - a).normalized()
    nrm = (b - a).perp()
    best = None
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        if abs((p - a).dot(nrm)) < tol and abs((q - a).dot(nrm)) < tol:
            tp, tq = (p - a).dot(d), (q - a).dot(d)
            lo, hi = min(tp, tq), max(tp, tq)
            if best is None:
                best = (lo, hi)
            else:
                best = (min(best[0], lo), max(best[1], hi))
    return best


def panels_from_scored_piece(outline: List[Vec2], folds: List[Fold]
                             ) -> Tuple[Dict[str, Panel], List[Hinge], List[str]]:
    """Split a single ``outline`` along its ``folds`` into numbered panels and
    the hinges between them. Returns ``(panels, hinges, order)`` where ``order``
    lists the panel ids in numbering order (their ``name`` is the 1-based label).

    Two phases so cuts never lose track of each other: (1) split the outline by
    every fold line into final facets; (2) hinge any two facets that share an
    edge lying on a fold line. Handles parallel scores (a wallet strip) and
    crossing scores."""
    # -- phase 1: split by every fold line -------------------------------
    polys: List[List[Vec2]] = [list(outline)]
    for fold in folds:
        nxt: List[List[Vec2]] = []
        for poly in polys:
            left, right = split_polygon_by_line(poly, fold.a, fold.b)
            if left and right:
                nxt += [left, right]
            else:
                nxt.append(poly)
        polys = nxt

    # number panels left-to-right, then bottom-to-top, for a stable reading order
    def key(poly: List[Vec2]):
        cx = sum(p.x for p in poly) / len(poly)
        cy = sum(p.y for p in poly) / len(poly)
        return (round(cx, 3), round(cy, 3))

    polys.sort(key=key)
    panels: Dict[str, Panel] = {}
    order: List[str] = []
    for i, poly in enumerate(polys):
        pid = f"f{i}"
        panels[pid] = Panel(id=pid, outline=poly, name=str(i + 1))
        order.append(pid)

    # -- phase 2: hinge facets that share an on-fold-line edge -----------
    hinges: List[Hinge] = []
    for fold in folds:
        spans = [(pid, _edge_span_on_line(panels[pid].outline, fold))
                 for pid in order]
        spans = [(pid, s) for pid, s in spans if s is not None]
        d = (fold.b - fold.a).normalized()
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                pid_i, (lo_i, hi_i) = spans[i]
                pid_j, (lo_j, hi_j) = spans[j]
                lo, hi = max(lo_i, lo_j), min(hi_i, hi_j)
                if hi - lo <= 1e-4:
                    continue                    # edges don't overlap -> not adjacent
                # opposite sides of the line?
                nrm = (fold.b - fold.a).perp()
                ci = _poly_centroid(panels[pid_i].outline)
                cj = _poly_centroid(panels[pid_j].outline)
                if ((ci - fold.a).dot(nrm) > 0) == ((cj - fold.a).dot(nrm) > 0):
                    continue
                seg = (fold.a + d * lo, fold.a + d * hi)
                hinges.append(Hinge(pid_i, pid_j, seg, seg, fold.signed_angle()))
    return panels, hinges, order


def _poly_centroid(poly: List[Vec2]) -> Vec2:
    n = len(poly)
    return Vec2(sum(p.x for p in poly) / n, sum(p.y for p in poly) / n)


def bend_allowance(folds: List[Fold], thickness: float,
                   radius: Optional[float] = None, k: float = 0.5
                   ) -> Dict[str, float]:
    """How much longer the FLAT blank must be to survive folding around a real
    (non-zero) bend radius, versus an ideal zero-thickness crease.

    Each fold's neutral fibre travels an arc of ``angle * (radius + k*thickness)``
    while the crease line itself contributes ``0``; the difference is extra
    material you must add. Leather bends tight, so ``radius`` defaults to one
    thickness. Returns per-axis additions: a near-vertical score grows WIDTH,
    a near-horizontal score grows HEIGHT (plus the total)."""
    r = thickness if radius is None else radius
    add_w = add_h = 0.0
    per: List[float] = []
    for f in folds:
        ang = math.radians(abs(f.angle_deg))
        ba = ang * (r + k * thickness)          # arc length of the neutral fibre
        per.append(ba)
        d = f.b - f.a
        if abs(d.y) >= abs(d.x):                # vertical-ish score: folds in x
            add_w += ba
        else:
            add_h += ba
    return {"width": add_w, "height": add_h, "total": add_w + add_h,
            "per_fold": per}


def fold_bend_allowance(fold: Fold, thickness: float,
                        radius: Optional[float] = None, k: float = 0.5) -> float:
    """Bend allowance (mm of extra flat material) for a SINGLE fold."""
    r = thickness if radius is None else radius
    return math.radians(abs(fold.angle_deg)) * (r + k * thickness)


def grow_polygon_at_fold(outline: List[Vec2], fold: Fold, ba: float
                         ) -> List[Vec2]:
    """Insert ``ba`` mm of material at a fold: translate every outline vertex on
    the FAR side of the fold line out along the fold normal, leaving the near
    side fixed. The blank grows by ``ba`` across the fold (the near panel keeps
    its size; the far panel slides out to make room for the bend radius)."""
    if ba <= 0:
        return [Vec2(p.x, p.y) for p in outline]
    n = (fold.b - fold.a).perp().normalized()
    shift = n * ba
    a = fold.a
    return [p + shift if (p - a).dot(n) > 1e-9 else Vec2(p.x, p.y)
            for p in outline]


# -- registration: do stacked layers line up for stitching? ------------------
# After folding, panels that come to rest face-to-face must register: enough
# material to reach, and stitch holes that coincide so the awl passes through
# both layers. This runs on the final 3D geometry, so folds-on-folds are handled
# for free.

def _panel_plane(pl: PlacedPanel) -> Tuple[Vec3, Vec3]:
    """Centroid + unit normal of a placed panel."""
    c = pl.centroid()
    o = pl.outline
    n = Vec3(0.0, 0.0, 1.0)
    for i in range(1, len(o) - 1):
        cand = (o[i] - o[0]).cross(o[i + 1] - o[0])
        if cand.length() > 1e-9:
            n = cand.normalized()
            break
    return c, n


def _plane_basis(n: Vec3) -> Tuple[Vec3, Vec3]:
    """Two orthonormal in-plane axes for a normal ``n``."""
    ref = Vec3(1.0, 0.0, 0.0) if abs(n.x) < 0.9 else Vec3(0.0, 1.0, 0.0)
    u = (ref - n * ref.dot(n)).normalized()
    v = n.cross(u)
    return u, v


def _project2d(pts, origin: Vec3, u: Vec3, v: Vec3):
    return [Vec2((p - origin).dot(u), (p - origin).dot(v)) for p in pts]


def stacked_pairs(placed: List[PlacedPanel], thickness: float = 0.0
                  ) -> List[Tuple[PlacedPanel, PlacedPanel]]:
    """Panel pairs that come to rest face-to-face: near-parallel, within a couple
    of thicknesses of each other, and overlapping when projected together."""
    pairs = []
    gap_max = max(3.0, 2.5 * max(thickness, 0.0)) + 1e-6
    planes = {id(p): _panel_plane(p) for p in placed}
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            A, B = placed[i], placed[j]
            cA, nA = planes[id(A)]
            cB, nB = planes[id(B)]
            if abs(nA.dot(nB)) < math.cos(math.radians(25)):
                continue                              # not parallel -> not stacked
            if abs((cB - cA).dot(nA)) > gap_max:
                continue                              # too far apart in depth
            u, v = _plane_basis(nA)
            pa = _project2d(A.outline, cA, u, v)
            pb = _project2d(B.outline, cA, u, v)
            axa = [p.x for p in pa]
            aya = [p.y for p in pa]
            axb = [p.x for p in pb]
            ayb = [p.y for p in pb]
            if (min(axa) > max(axb) or min(axb) > max(axa)
                    or min(aya) > max(ayb) or min(ayb) > max(aya)):
                continue                              # projections don't overlap
            pairs.append((A, B))
    return pairs


def registration_report(placed: List[PlacedPanel], thickness: float = 0.0,
                        tol: float = 1.0):
    """For every stacked pair of layers, pair their stitch holes by stitch order
    (the k-th hole on one layer meets the k-th on the other) and check they
    coincide. Returns ``(lines, bad)``:

    * ``lines`` -- human-readable findings per layer pair (counts, worst offset,
      whether the flap reaches).
    * ``bad`` -- ``{id(panel): set(hole_index)}`` of holes that don't register
      (unmatched, or offset beyond ``tol``) so the viewer can flag them red.
    """
    lines: List[str] = []
    bad: Dict[str, set] = {}
    pairs = stacked_pairs(placed, thickness)
    if not pairs:
        return ["No layers are stacked yet — fold the piece to check registration."], bad
    for A, B in pairs:
        cA, nA = _panel_plane(A)
        u, v = _plane_basis(nA)
        # only the holes that actually overlap the other layer can stitch through
        pbA = _project2d(A.outline, cA, u, v)
        pbB = _project2d(B.outline, cA, u, v)
        ha = [(i, Vec2((h - cA).dot(u), (h - cA).dot(v)))
              for i, h in enumerate(A.holes)]
        hb = [(i, Vec2((h - cA).dot(u), (h - cA).dot(v)))
              for i, h in enumerate(B.holes)]
        # split each layer's holes into those over the other layer (can stitch
        # through both) and those that miss it entirely (not reached)
        ov_a = [(i, p) for i, p in ha if _pt_in_poly2d(p, pbB)]
        ov_b = [(i, p) for i, p in hb if _pt_in_poly2d(p, pbA)]
        na, nb = len(ov_a), len(ov_b)
        # alignment quality: nearest hole on the other layer (a fold is a
        # reflection, so mating holes are nearest, not same-indexed)
        worst = 0.0
        aligned = 0
        for ia, pa in ov_a:
            if not ov_b:
                break
            d = min(math.hypot(pa.x - pb.x, pa.y - pb.y) for _ib, pb in ov_b)
            if d <= tol:
                aligned += 1
            else:
                worst = max(worst, d)
                bad.setdefault(A.id, set()).add(ia)
        # holes that don't reach the other layer at all -> flag red
        reach = 0
        for lst, poly_other, panel in ((ha, pbB, A), (hb, pbA, B)):
            for i, p in lst:
                if not _pt_in_poly2d(p, poly_other):
                    bad.setdefault(panel.id, set()).add(i)
                    reach += 1

        msg = f"Panels {A.name}↔{B.name}: {na} vs {nb} holes stitch through both"
        if na != nb:
            msg += f" — COUNT MISMATCH ({abs(na - nb)} extra)"
        elif worst > tol:
            msg += f" — {aligned}/{na} align, worst off {worst:.1f} mm"
        elif na:
            msg += " — ✓ holes line up"
        if reach:
            msg += f"; {reach} hole(s) short of the edge (not enough material)"
        lines.append(msg)
    return lines, bad


def _pt_in_poly2d(p: Vec2, poly: List[Vec2]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        a, b = poly[i], poly[j]
        if (a.y > p.y) != (b.y > p.y):
            xint = (b.x - a.x) * (p.y - a.y) / (b.y - a.y + 1e-30) + a.x
            if p.x < xint:
                inside = not inside
        j = i
    return inside


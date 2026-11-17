"""Fold a flat piece of leather into a solid model.

The pattern is the leather's middle surface. A fold line is the middle of a
bend: an arc of ``angle x (inside radius + half the thickness)`` of leather,
half taken from each side of the line -- the same sum, about the same
line, as Edit > Fold allowance works with. Between the bends the panels
stay flat. Each fold has its own angle, direction, radius and place in the
folding order; a fold that closes flat over layers folded in before it is
given the room to go round them.

``Layout`` holds a piece cut up ready to fold: ``solid`` gives the model at
any moment of the folding as convex cells for ``bsp`` to order and the view
to paint. Pure Python, no Qt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import polycut as pc
from .bsp import BACK, DOTS, FRONT, INNER, LINE, POLY, WALL, Cell, Face, \
    plane_of

Pt = Tuple[float, float]
P3 = Tuple[float, float, float]
# where a panel lies: the 3D point of pattern (0, 0), the 3D directions of
# pattern x and y, and the normal out of the leather's front face
Frame = Tuple[P3, P3, P3, P3]

_FLAT = math.radians(175.0)     # a fold this far round lies flat on its panel
_SHARP = 0.02                   # a bend narrower than this (mm) is a crease
_FLAT_FRAME: Frame = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                      (0.0, 0.0, 1.0))


@dataclass
class FoldLine:
    """A fold across the piece, from ``a`` to ``b`` (pattern mm).

    ``direction`` is 1 to fold the front faces together, -1 the backs;
    ``radius`` the inside radius of the bend (below 0: the piece's own);
    ``order`` its step in the folding (0: not set -- after those that are,
    one at a time)."""

    a: Pt
    b: Pt
    angle: float = 90.0
    direction: int = 1
    radius: float = -1.0
    order: int = 0
    active: bool = True
    label: str = ""
    name: str = ""
    key: object = None


class Panel:
    """A flat part of the piece between folds."""

    __slots__ = ("index", "number", "pts", "flags", "area", "spot", "parts",
                 "box", "holes")

    def __init__(self, index, pts, flags):
        self.index = index
        self.number = index + 1
        self.pts = pts
        self.flags = flags
        self.area = pc.area(pts)
        self.parts = [p for p, _f in pc.convex_parts(pts, flags)] or [pts]
        self.spot = pc.centroid(max(self.parts, key=pc.area))
        self.box = pc.bounds(pts)
        self.holes: List[int] = []


class Hinge:
    """Where two panels meet along a fold: the stretch ``lo``..``hi`` of
    the fold line (mm along it from its start), with ``left`` the panel on
    the line's left. Once the folding tree is built, ``parent`` stays and
    ``child`` swings: ``a``, ``e`` and ``k0``..``k1`` are the same stretch
    on a line turned so that the child is on its left."""

    __slots__ = ("fold", "left", "right", "lo", "hi", "tree", "parent",
                 "child", "a", "e", "k0", "k1", "moves")

    def __init__(self, fold, left, right, lo, hi):
        self.fold = fold
        self.left = left
        self.right = right
        self.lo = lo
        self.hi = hi
        self.tree = False
        self.parent = self.child = -1
        self.a = self.e = None
        self.k0 = self.k1 = 0.0
        self.moves: frozenset = frozenset()


class _Cell2:
    """A convex bit of the pattern: part of a flat panel, or one strip of a
    bend (``hinge`` set, with each corner's place along and across it)."""

    __slots__ = ("pts", "flags", "panel", "hinge", "strip", "place", "area",
                 "box", "edges", "edges_rev", "sides", "tag", "dots",
                 "lines", "polys")

    def __init__(self, pts, flags, panel):
        self.pts = pts
        self.flags = flags
        self.panel = panel
        self.hinge = None
        self.strip = 0
        self.place = None
        self.area = pc.area(pts)
        self.box = pc.bounds(pts)
        self.dots = []            # (x, y, diameter, hole index)
        self.lines = []           # (fold, p, q)
        self.polys = []           # rings of points


# -- small vector helpers (tuples) --------------------------------------------

def _lin(o: P3, a: float, u: P3, b: float, v: P3) -> P3:
    return (o[0] + a * u[0] + b * v[0], o[1] + a * u[1] + b * v[1],
            o[2] + a * u[2] + b * v[2])


def _mix(a: float, u: P3, b: float, v: P3) -> P3:
    return (a * u[0] + b * v[0], a * u[1] + b * v[1], a * u[2] + b * v[2])


def _dot(u: P3, v: P3) -> float:
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def place(frame: Frame, p: Pt, lift: float = 0.0) -> P3:
    """Where pattern point ``p`` of a panel is, ``lift`` mm out of its
    front face (negative: out of the back)."""
    o, ex, ey, n = frame
    x, y = p
    return (o[0] + x * ex[0] + y * ey[0] + lift * n[0],
            o[1] + x * ex[1] + y * ey[1] + lift * n[1],
            o[2] + x * ex[2] + y * ey[2] + lift * n[2])


def child_frame(parent: Frame, a: Pt, e: Pt, direction: int, phi: float,
                width: float) -> Frame:
    """The frame of the panel on the left of the fold line (through ``a``
    along unit ``e``) once the bend -- ``width`` mm of leather centred on
    the line -- has turned through ``phi`` radians."""
    if phi < 1e-9:
        return parent
    o, ex, ey, n = parent
    c2 = (-e[1], e[0])
    e1 = _mix(e[0], ex, e[1], ey)
    cc = _mix(c2[0], ex, c2[1], ey)
    h = 0.5 * width
    rho = width / phi
    sn, cs = math.sin(phi), math.cos(phi)
    s = float(direction)
    a0 = _lin(o, a[0] - h * c2[0], ex, a[1] - h * c2[1], ey)
    end = _lin(a0, rho * sn, cc, s * rho * (1.0 - cs), n)
    t = _mix(cs, cc, s * sn, n)                # across the child panel
    n2 = _mix(cs, n, -s * sn, cc)
    ex2 = _mix(e[0], e1, c2[0], t)
    ey2 = _mix(e[1], e1, c2[1], t)
    o2 = (end[0] - h * t[0] - a[0] * ex2[0] - a[1] * ey2[0],
          end[1] - h * t[1] - a[0] * ex2[1] - a[1] * ey2[1],
          end[2] - h * t[2] - a[0] * ex2[2] - a[1] * ey2[2])
    return (o2, ex2, ey2, n2)


# -- cutting the piece into panels --------------------------------------------

def _snap(p: Pt, outline: Sequence[Pt], tol: float) -> Pt:
    """``p`` moved onto the outline if it is within ``tol`` of it: onto a
    corner for choice, else the nearest point of an edge."""
    best, bd = None, tol
    for q in outline:
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        if d <= bd:
            best, bd = q, d
    if best is not None:
        return best
    bd = tol
    x0, y0 = outline[-1]
    for x1, y1 in outline:
        dx, dy = x1 - x0, y1 - y0
        ll = dx * dx + dy * dy
        if ll > 1e-18:
            t = ((p[0] - x0) * dx + (p[1] - y0) * dy) / ll
            t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
            q = (x0 + dx * t, y0 + dy * t)
            d = math.hypot(q[0] - p[0], q[1] - p[1])
            if d < bd:
                best, bd = q, d
        x0, y0 = x1, y1
    return best if best is not None else p


def split_piece(outline: Sequence[Pt], folds: Sequence[FoldLine],
                snap: float = 0.3):
    """Cut an outline (counter-clockwise) along its fold lines.

    Returns ``(regions, lines)``: the polygons the folds leave, each with
    its edge flags (an edge along fold ``i`` is flagged ``i``), and for
    each fold its line ``(start, unit direction, length)`` -- or None for a
    fold that doesn't cross this piece.

    A fold line needn't be drawn edge to edge: it is cut along every
    stretch of leather it reaches into, from outline to outline. One that
    ends just off the outline is taken to end on it."""
    outline = list(outline)
    regions = [(outline, [pc.OUTLINE] * len(outline))]
    lines = []
    for fi, f in enumerate(folds):
        a, b = _snap(f.a, outline, snap), _snap(f.b, outline, snap)
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        if ln < 1e-6:
            lines.append(None)
            continue
        e = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)

        def want(lo, hi, ln=ln):
            over = min(hi, ln) - max(lo, 0.0)
            return over > max(1e-6, min(0.5, 0.25 * (hi - lo)))

        new, hit = [], False
        for pts, flags in regions:
            pieces = pc.cut(pts, flags, a, e, want, fi)
            hit = hit or len(pieces) > 1
            new.extend(pieces)
        regions = new
        lines.append((a, e, ln) if hit else None)
    return regions, lines


def find_hinges(regions, lines) -> List[Hinge]:
    """The stretches of each fold line with a panel on either side."""
    hinges: List[Hinge] = []
    for fi, line in enumerate(lines):
        if line is None:
            continue
        a, e, _ln = line
        runs = {1: [], -1: []}
        for ri, (pts, flags) in enumerate(regions):
            n = len(pts)
            segs = {1: [], -1: []}
            for i in range(n):
                if flags[i] != fi:
                    continue
                p, q = pts[i], pts[(i + 1) % n]
                sp = (p[0] - a[0]) * e[0] + (p[1] - a[1]) * e[1]
                sq = (q[0] - a[0]) * e[0] + (q[1] - a[1]) * e[1]
                segs[1 if sq > sp else -1].append((min(sp, sq), max(sp, sq)))
            for sign, lst in segs.items():
                lst.sort()
                cur = None
                for lo, hi in lst:
                    if cur is not None and lo <= cur[1] + 1e-7:
                        cur[1] = max(cur[1], hi)
                    else:
                        cur = [lo, hi]
                        runs[sign].append((ri, cur))
        for ra, sa in runs[1]:                # running along the line: left
            for rb, sb in runs[-1]:
                lo, hi = max(sa[0], sb[0]), min(sa[1], sb[1])
                if ra != rb and hi - lo > 1e-6:
                    hinges.append(Hinge(fi, ra, rb, lo, hi))
    return hinges


def net_regions(outlines: Sequence[Sequence[Pt]], tol: float = 0.6):
    """Panels drawn edge to edge, as a paper net: ``(regions, folds)`` with
    a fold along every edge two panels share whole."""
    polys = [pc.ccw([tuple(p) for p in o]) for o in outlines]
    regions = [(p, [pc.OUTLINE] * len(p)) for p in polys]
    folds: List[FoldLine] = []
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            A, B = polys[i], polys[j]
            found = None
            for ka in range(len(A)):
                a0, a1 = A[ka], A[(ka + 1) % len(A)]
                if math.dist(a0, a1) < 1e-6:
                    continue
                for kb in range(len(B)):
                    b0, b1 = B[kb], B[(kb + 1) % len(B)]
                    if math.dist(a0, b1) < tol and math.dist(a1, b0) < tol:
                        found = (ka, kb)
                        break
                if found:
                    break
            if found:
                ka, kb = found
                fi = len(folds)
                folds.append(FoldLine(A[ka], A[(ka + 1) % len(A)]))
                regions[i][1][ka] = fi
                regions[j][1][kb] = fi
    lines = []
    for f in folds:
        ln = math.dist(f.a, f.b)
        lines.append((f.a, ((f.b[0] - f.a[0]) / ln, (f.b[1] - f.a[1]) / ln),
                      ln))
    return regions, folds, lines


def fold_steps(folds: Sequence[FoldLine], live: Sequence[bool]) -> List[int]:
    """The step (1, 2...) each fold is made at, 0 for one that isn't made.
    Folds given the same ``order`` fold together; those given none come
    after the rest, one at a time."""
    given = sorted({f.order for f, on in zip(folds, live)
                    if on and f.order > 0})
    rank = {o: k + 1 for k, o in enumerate(given)}
    steps, nxt = [], len(given)
    for f, on in zip(folds, live):
        if not on:
            steps.append(0)
        elif f.order > 0:
            steps.append(rank[f.order])
        else:
            nxt += 1
            steps.append(nxt)
    return steps


# -- the layout ---------------------------------------------------------------

class Layout:
    """A piece cut up ready to fold.

    ``folds`` are the fold lines as given; ``panels`` the flat parts they
    leave (``base`` is the one that stays still); ``hinges`` where panels
    meet. For each fold: ``crosses`` (whether it cuts this piece),
    ``steps`` (when it folds, 0 if it doesn't), ``radius`` (the inside
    radius it bends at), ``wrap`` (how much of that is room for layers
    folded in earlier) and ``used`` (the leather the bend takes).
    """

    def __init__(self, regions, folds: Sequence[FoldLine], lines, *,
                 thickness: float = 2.0, radius: float = 0.0,
                 base: Optional[int] = None, holes=(), cutouts=(),
                 detail: float = 15.0, sharp=()):
        self.folds = list(folds)
        self.lines = list(lines)
        self.thickness = max(0.05, float(thickness))
        self.base_radius = max(0.0, float(radius))
        self.detail = max(3.0, float(detail))
        self.warnings: List[str] = []
        order = sorted(range(len(regions)), key=lambda i: (
            round(pc.centroid(regions[i][0])[0], 1),
            -round(pc.centroid(regions[i][0])[1], 1)))
        self.panels = [Panel(k, list(regions[i][0]), list(regions[i][1]))
                       for k, i in enumerate(order)]
        self.hinges = find_hinges([(p.pts, p.flags) for p in self.panels],
                                  self.lines)
        n = len(self.folds)
        self.crosses = [any(h.fold == i for h in self.hinges)
                        for i in range(n)]
        live = [self.crosses[i] and f.active and f.angle > 1e-6
                for i, f in enumerate(self.folds)]
        self.steps = fold_steps(self.folds, live)
        self.n_steps = max(self.steps, default=0)
        if base is None or not 0 <= base < len(self.panels):
            base = max(range(len(self.panels)),
                       key=lambda i: self.panels[i].area) if self.panels \
                else 0
        self.base = base
        self._sharp = set(sharp)
        self.holes = [tuple(h) for h in holes]
        self.cutouts = [pc.ccw([tuple(p) for p in c]) for c in cutouts]
        self._build_tree()
        self._settle_radii()
        self._build_cells()
        self._check_spacing()

    # -- the folding tree -----------------------------------------------------

    def _build_tree(self):
        n = len(self.panels)
        link = list(range(n))

        def find(x):
            while link[x] != x:
                link[x] = link[link[x]]
                x = link[x]
            return x

        # earlier folds first: a later fold then carries what they folded
        ranked = sorted(self.hinges, key=lambda h: (
            self.steps[h.fold] or 10 ** 6, h.fold, h.lo))
        near: Dict[int, List[Hinge]] = {i: [] for i in range(n)}
        for h in ranked:
            ra, rb = find(h.left), find(h.right)
            h.tree = ra != rb
            if h.tree:
                link[ra] = rb
                near[h.left].append(h)
                near[h.right].append(h)
        self.roots: List[int] = []
        self.tree: List[Hinge] = []            # parents before children
        kids: Dict[int, List[Hinge]] = {i: [] for i in range(n)}
        seen = set()
        starts = [self.base] + sorted(range(n),
                                      key=lambda i: -self.panels[i].area)
        for r in starts:
            if r in seen:
                continue
            self.roots.append(r)
            seen.add(r)
            queue = [r]
            while queue:
                p = queue.pop(0)
                for h in near[p]:
                    c = h.right if h.left == p else h.left
                    if c in seen:
                        continue
                    seen.add(c)
                    h.parent, h.child = p, c
                    a, e, _ln = self.lines[h.fold]
                    if c == h.left:
                        h.a, h.e, h.k0, h.k1 = a, e, h.lo, h.hi
                    else:
                        h.a, h.e = a, (-e[0], -e[1])
                        h.k0, h.k1 = -h.hi, -h.lo
                    self.tree.append(h)
                    kids[p].append(h)
                    queue.append(c)
        for h in reversed(self.tree):          # what each hinge carries
            moves = {h.child}
            for k in kids[h.child]:
                moves |= k.moves
            h.moves = frozenset(moves)
        # a hinge left out (folds that cross make a ring of them) is a cut:
        # the leather shows its edge there
        for h in self.hinges:
            if h.tree or not self.steps[h.fold]:
                continue
            a, e, _ln = self.lines[h.fold]
            for i in (h.left, h.right):
                p = self.panels[i]
                m = len(p.pts)
                for k in range(m):
                    if p.flags[k] != h.fold:
                        continue
                    q, r = p.pts[k], p.pts[(k + 1) % m]
                    mid = (0.5 * (q[0] + r[0]) - a[0]) * e[0] \
                        + (0.5 * (q[1] + r[1]) - a[1]) * e[1]
                    if h.lo - 1e-6 <= mid <= h.hi + 1e-6:
                        p.flags[k] = pc.OUTLINE

    def frames(self, fractions: Sequence[float], widths=None, angles=None
               ) -> List[Frame]:
        """Where each panel lies when every fold has gone ``fractions``
        (0..1, one per fold) of its way."""
        if widths is None:
            widths = self.used
        out: List[Optional[Frame]] = [None] * len(self.panels)
        for r in self.roots:
            out[r] = _FLAT_FRAME
        for h in self.tree:
            f = self.folds[h.fold]
            if angles is not None:
                phi = angles[h.fold]
            elif self.steps[h.fold]:
                phi = math.radians(min(f.angle, 180.0)) * fractions[h.fold]
            else:
                phi = 0.0
            out[h.child] = child_frame(out[h.parent], h.a, h.e, f.direction,
                                       phi, widths[h.fold])
        return out

    def fractions(self, t: float) -> List[float]:
        """How far each fold has gone at time ``t`` of the folding, which
        runs from 0 (flat) to ``n_steps`` (folded), one step at a time."""
        return [0.0 if not s else min(1.0, max(0.0, t - (s - 1)))
                for s in self.steps]

    # -- how tightly each fold bends ------------------------------------------

    def _settle_radii(self):
        """Give each fold its inside radius: the one asked for, plus -- for
        a fold that closes flat -- room for whatever was folded in before
        it and now lies in its way."""
        t = self.thickness
        n = len(self.folds)
        self.radius = [max(0.0, f.radius) if f.radius >= 0.0
                       else self.base_radius for f in self.folds]
        self.wrap = [0.0] * n
        theta = [math.radians(min(max(f.angle, 0.0), 180.0)) if self.steps[i]
                 else 0.0 for i, f in enumerate(self.folds)]
        done = [0.0] * n
        by_fold: Dict[int, List[Hinge]] = {}
        for h in self.tree:
            by_fold.setdefault(h.fold, []).append(h)
        for fi in sorted((i for i in range(n) if self.steps[i]),
                         key=lambda i: (self.steps[i], i)):
            if theta[fi] >= _FLAT and fi in by_fold:
                for _round in range(24):
                    angles = list(done)
                    angles[fi] = theta[fi]
                    widths = [theta[k] * (self.radius[k] + 0.5 * t)
                              for k in range(n)]
                    fr = self.frames(None, widths, angles)
                    need = max(self._clearance(h, fr) for h in by_fold[fi])
                    if need <= 1e-6:
                        break
                    self.radius[fi] += 0.5 * need
                    self.wrap[fi] += 0.5 * need
            done[fi] = theta[fi]
        self.used = [theta[k] * (self.radius[k] + 0.5 * t) for k in range(n)]
        for k in range(n):
            if self.used[k] < _SHARP:
                self.used[k] = 0.0

    def _clearance(self, h: Hinge, frames: List[Frame]) -> float:
        """How much further out the panels this hinge carries must lie to
        clear the leather they have come down on."""
        t = self.thickness
        o, ex, ey, n = frames[h.parent]
        s = float(self.folds[h.fold].direction)
        d = (s * n[0], s * n[1], s * n[2])
        e1 = _mix(h.e[0], ex, h.e[1], ey)
        cc = _mix(-h.e[1], ex, h.e[0], ey)
        foot = {}

        def footprint(i):
            if i not in foot:
                fr = frames[i]
                if abs(_dot(fr[3], n)) < 0.985:
                    foot[i] = None
                else:
                    panel = self.panels[i]
                    parts = []
                    for part in panel.parts:
                        pts3 = [place(fr, p) for p in part]
                        parts.append(pc.ccw([(_dot(q, e1), _dot(q, cc))
                                             for q in pts3]))
                    q = place(fr, panel.pts[0])
                    height = (q[0] - o[0]) * d[0] + (q[1] - o[1]) * d[1] \
                        + (q[2] - o[2]) * d[2]
                    boxes = [pc.bounds(p) for p in parts]
                    foot[i] = (parts, height, boxes)
            return foot[i]

        need = 0.0
        for m in h.moves:
            fm = footprint(m)
            if fm is None:
                continue
            for g in range(len(self.panels)):
                if g in h.moves:
                    continue
                fg = footprint(g)
                if fg is None:
                    continue
                gap = fm[1] - fg[1]
                if abs(gap) >= t - 1e-6 or t - gap <= need:
                    continue
                hit = False
                for A, ba in zip(fm[0], fm[2]):
                    for B, bb in zip(fg[0], fg[2]):
                        if (ba[0] < bb[2] and bb[0] < ba[2] and ba[1] < bb[3]
                                and bb[1] < ba[3]
                                and pc.convex_overlap(A, B, 0.3)):
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    need = t - gap
        return need

    # -- cells ----------------------------------------------------------------

    def _build_cells(self):
        flats: List[List[pc.Poly]] = [[(list(p.pts), list(p.flags))]
                                      for p in self.panels]
        strips = []                    # (panel, hinge index, strip, poly)
        self._halves = [0] * len(self.tree)
        for hi, h in enumerate(self.tree):
            w = self.used[h.fold]
            if w <= 0.0:
                continue
            f = self.folds[h.fold]
            m = max(1, int(math.ceil(min(f.angle, 180.0) * 0.5 / self.detail
                                     - 1e-9)))
            self._halves[hi] = m
            for panel, side in ((h.parent, -1.0), (h.child, 1.0)):
                flats[panel], got = _cut_zone(flats[panel], h, side,
                                              0.5 * w, m)
                strips += [(panel, hi, j, poly) for j, poly in got]
        cells: List[_Cell2] = []
        for i, polys in enumerate(flats):
            for pts, flags in polys:
                for cp, cf in pc.convex_parts(pts, flags):
                    cells.append(_Cell2(cp, cf, i))
        for panel, hi, j, (pts, flags) in strips:
            h = self.tree[hi]
            w = self.used[h.fold]
            step = w / (2 * self._halves[hi])
            for cp, cf in pc.convex_parts(pts, flags):
                c = _Cell2(cp, cf, panel)
                c.hinge, c.strip = hi, j
                c.place = [_strip_place(p, h, w, step, j) for p in cp]
                cells.append(c)
        cells = [c for c in cells if c.area > 1e-9 and len(c.pts) >= 3]
        t = self.thickness
        for c in cells:
            n = len(c.pts)
            c.edges = tuple(f == pc.OUTLINE for f in c.flags)
            c.edges_rev = tuple(c.edges[(n - 2 - k) % n] for k in range(n))
            sharp = [(round(p[0], 6), round(p[1], 6)) in self._sharp
                     for p in c.pts]
            c.sides = []
            for k in range(n):
                j = (k + 1) % n
                p, q = c.pts[k], c.pts[j]
                dx, dy = q[0] - p[0], q[1] - p[1]
                ln = math.hypot(dx, dy) or 1e-12
                wall = c.flags[k] == pc.OUTLINE
                c.sides.append((dy / ln, -dx / ln, ln * t,
                                WALL if wall else INNER,
                                (True, sharp[j], True, sharp[k]) if wall
                                else None))
            c.tag = ("panel", c.panel) if c.hinge is None \
                else ("bend", self.tree[c.hinge].fold)
        self.cells = cells
        by_panel: Dict[int, List[_Cell2]] = {}
        for c in cells:
            by_panel.setdefault(c.panel, []).append(c)
        # the fold lines themselves, drawn on both faces of the leather
        for c in cells:
            n = len(c.pts)
            for k in range(n):
                if c.flags[k] >= 0:
                    c.lines.append((c.flags[k], c.pts[k], c.pts[(k + 1) % n]))
        # stitch holes go on the cell they are in
        self.hole_panel = [-1] * len(self.holes)
        self.hole_flat = [False] * len(self.holes)
        for k, hole in enumerate(self.holes):
            p = (hole[0], hole[1])
            for panel in self.panels:
                b = panel.box
                if not (b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]
                        and pc.inside(p, panel.pts)):
                    continue
                self.hole_panel[k] = panel.index
                panel.holes.append(k)
                for c in by_panel.get(panel.index, ()):
                    b = c.box
                    if (b[0] - 1e-6 <= p[0] <= b[2] + 1e-6
                            and b[1] - 1e-6 <= p[1] <= b[3] + 1e-6
                            and pc.inside_convex(p, c.pts, 1e-6)):
                        c.dots.append((p[0], p[1], hole[2], k))
                        self.hole_flat[k] = c.hinge is None
                        break
                break
        for ring in self.cutouts:
            rb = pc.bounds(ring)
            for c in cells:
                b = c.box
                if b[0] < rb[2] and rb[0] < b[2] and b[1] < rb[3] \
                        and rb[1] < b[3]:
                    part = pc.clip_to_convex(ring, c.pts)
                    if len(part) >= 3 and abs(pc.area(part)) > 1e-6:
                        c.polys.append(part)

    def _check_spacing(self):
        """Warn of folds too close together for the bends they need."""
        seen = set()
        for i, h in enumerate(self.tree):
            for g in self.tree[i + 1:]:
                shared = {h.parent, h.child} & {g.parent, g.child}
                if not shared or h.fold == g.fold:
                    continue
                wh, wg = self.used[h.fold], self.used[g.fold]
                if wh <= 0.0 or wg <= 0.0:
                    continue
                if abs(h.e[0] * g.e[1] - h.e[1] * g.e[0]) > 0.26:
                    continue                  # not nearly side by side
                # the gap between the two lines where g runs
                mid = 0.5 * (g.k0 + g.k1)
                q = (g.a[0] + g.e[0] * mid, g.a[1] + g.e[1] * mid)
                gap = abs((q[1] - h.a[1]) * h.e[0] - (q[0] - h.a[0]) * h.e[1])
                room = 0.5 * (wh + wg)
                key = (min(h.fold, g.fold), max(h.fold, g.fold))
                if gap < room - 1e-6 and key not in seen:
                    seen.add(key)
                    a, b = self.folds[key[0]], self.folds[key[1]]
                    self.warnings.append(
                        f"Folds {a.label or key[0] + 1} and "
                        f"{b.label or key[1] + 1} are {gap:.1f} mm apart, but "
                        f"their bends need {room:.1f} mm between them.")

    # -- the solid ------------------------------------------------------------

    def _arcs(self, frames, fractions):
        """For each hinge: the direction along the fold, and the lines the
        bend's strips meet at, on the front and on the back face."""
        ht = 0.5 * self.thickness
        out = []
        for hi, h in enumerate(self.tree):
            m = self._halves[hi]
            if not m:
                out.append(None)
                continue
            f = self.folds[h.fold]
            w = self.used[h.fold]
            phi = math.radians(min(f.angle, 180.0)) * fractions[h.fold] \
                if self.steps[h.fold] else 0.0
            o, ex, ey, n = frames[h.parent]
            e, s = h.e, float(f.direction)
            c2 = (-e[1], e[0])
            e1 = _mix(e[0], ex, e[1], ey)
            cc = _mix(c2[0], ex, c2[1], ey)
            a0 = _lin(o, h.a[0] - 0.5 * w * c2[0], ex,
                      h.a[1] - 0.5 * w * c2[1], ey)
            tops, bots = [], []
            M = 2 * m
            for j in range(M + 1):
                if phi < 1e-9:
                    mid = (a0[0] + cc[0] * w * j / M, a0[1] + cc[1] * w * j / M,
                           a0[2] + cc[2] * w * j / M)
                    nn = n
                else:
                    al = phi * j / M
                    rho = w / phi
                    sn, cs = math.sin(al), math.cos(al)
                    mid = _lin(a0, rho * sn, cc, s * rho * (1.0 - cs), n)
                    nn = _mix(cs, n, -s * sn, cc)
                tops.append((mid[0] + ht * nn[0], mid[1] + ht * nn[1],
                             mid[2] + ht * nn[2]))
                bots.append((mid[0] - ht * nn[0], mid[1] - ht * nn[1],
                             mid[2] - ht * nn[2]))
            out.append((e1, tops, bots))
        return out

    def solid(self, fractions: Sequence[float], bad=()) -> List[Cell]:
        """The model with every fold ``fractions`` of its way round, as
        convex cells. ``bad`` holes (indices) are marked apart."""
        frames = self.frames(fractions)
        arcs = self._arcs(frames, fractions)
        ht = 0.5 * self.thickness
        bad = set(bad)
        out: List[Cell] = []
        for c in self.cells:
            if c.hinge is None:
                o, ex, ey, n = frames[c.panel]
                ax, ay, az = ex
                bx, by, bz = ey
                nx, ny, nz = n
                tx, ty, tz = o[0] + ht * nx, o[1] + ht * ny, o[2] + ht * nz
                ux, uy, uz = o[0] - ht * nx, o[1] - ht * ny, o[2] - ht * nz
                top = [(tx + x * ax + y * bx, ty + x * ay + y * by,
                        tz + x * az + y * bz) for x, y in c.pts]
                bot = [(ux + x * ax + y * bx, uy + x * ay + y * by,
                        uz + x * az + y * bz) for x, y in c.pts]

                def up(p, tx=tx, ty=ty, tz=tz, ax=ax, ay=ay, az=az, bx=bx,
                       by=by, bz=bz):
                    return (tx + p[0] * ax + p[1] * bx,
                            ty + p[0] * ay + p[1] * by,
                            tz + p[0] * az + p[1] * bz)

                def down(p, ux=ux, uy=uy, uz=uz, ax=ax, ay=ay, az=az, bx=bx,
                         by=by, bz=bz):
                    return (ux + p[0] * ax + p[1] * bx,
                            uy + p[0] * ay + p[1] * by,
                            uz + p[0] * az + p[1] * bz)

                pt, pb = top[0], bot[0]
                faces = [
                    Face(tuple(top), (nx, ny, nz,
                                      nx * pt[0] + ny * pt[1] + nz * pt[2]),
                         FRONT, c.edges, _decals(c, up, bad), c.tag, c.area),
                    Face(tuple(bot[::-1]),
                         (-nx, -ny, -nz,
                          -(nx * pb[0] + ny * pb[1] + nz * pb[2])),
                         BACK, c.edges_rev, _decals(c, down, bad), c.tag,
                         c.area)]
                m = len(top)
                for k in range(m):
                    j = k + 1 if k + 1 < m else 0
                    wx, wy, size, kind, edges = c.sides[k]
                    px = wx * ax + wy * bx
                    py = wx * ay + wy * by
                    pz = wx * az + wy * bz
                    b0 = bot[k]
                    faces.append(Face(
                        (b0, bot[j], top[j], top[k]),
                        (px, py, pz, px * b0[0] + py * b0[1] + pz * b0[2]),
                        kind, edges, None, c.tag, size))
                out.append(Cell(faces, c.tag))
                continue
            e1, tops, bots = arcs[c.hinge]
            j = c.strip
            t0, t1, b0, b1 = tops[j], tops[j + 1], bots[j], bots[j + 1]

            def up(q, t0=t0, t1=t1, e1=e1):
                al, tau = q
                return (t0[0] + (t1[0] - t0[0]) * tau + al * e1[0],
                        t0[1] + (t1[1] - t0[1]) * tau + al * e1[1],
                        t0[2] + (t1[2] - t0[2]) * tau + al * e1[2])

            def down(q, b0=b0, b1=b1, e1=e1):
                al, tau = q
                return (b0[0] + (b1[0] - b0[0]) * tau + al * e1[0],
                        b0[1] + (b1[1] - b0[1]) * tau + al * e1[1],
                        b0[2] + (b1[2] - b0[2]) * tau + al * e1[2])

            top = [up(q) for q in c.place]
            bot = [down(q) for q in c.place]
            h = self.tree[c.hinge]
            w = self.used[h.fold]
            step = w / (2 * self._halves[c.hinge])

            def at(p, h=h, w=w, step=step, j=j):
                return _strip_place(p, h, w, step, j)

            faces = []
            pl = plane_of(top)
            if pl is not None:
                faces.append(Face(tuple(top), pl, FRONT, c.edges,
                                  _decals(c, lambda p: up(at(p)), bad),
                                  c.tag, c.area))
            rb = bot[::-1]
            pl = plane_of(rb)
            if pl is not None:
                faces.append(Face(tuple(rb), pl, BACK, c.edges_rev,
                                  _decals(c, lambda p: down(at(p)), bad),
                                  c.tag, c.area))
            m = len(top)
            for k in range(m):
                jj = k + 1 if k + 1 < m else 0
                _wx, _wy, size, kind, edges = c.sides[k]
                faces += _side_faces(bot[k], bot[jj], top[jj], top[k], kind,
                                     edges, c.tag, size)
            if faces:
                out.append(Cell(faces, c.tag))
        return out

    # -- marks for the view ---------------------------------------------------

    def fold_marks(self, fractions: Sequence[float]):
        """Each fold line as it lies on the model: ``{fold: [(p, q, p', q')]}``
        with ``p``-``q`` on the front face and ``p'``-``q'`` on the back,
        one entry for every stretch of the fold."""
        frames = self.frames(fractions)
        ht = 0.5 * self.thickness
        out: Dict[int, list] = {}
        for h in self.tree:
            f = self.folds[h.fold]
            w = self.used[h.fold]
            phi = math.radians(min(f.angle, 180.0)) * fractions[h.fold] \
                if self.steps[h.fold] else 0.0
            o, ex, ey, n = frames[h.parent]
            e, s = h.e, float(f.direction)
            c2 = (-e[1], e[0])
            e1 = _mix(e[0], ex, e[1], ey)
            cc = _mix(c2[0], ex, c2[1], ey)
            a0 = _lin(o, h.a[0] - 0.5 * w * c2[0], ex,
                      h.a[1] - 0.5 * w * c2[1], ey)
            if phi < 1e-9 or w <= 0.0:
                mid = (a0[0] + cc[0] * 0.5 * w, a0[1] + cc[1] * 0.5 * w,
                       a0[2] + cc[2] * 0.5 * w)
                nn = n if phi < 1e-9 else _mix(
                    math.cos(0.5 * phi), n, -s * math.sin(0.5 * phi), cc)
            else:
                al, rho = 0.5 * phi, w / phi
                sn, cs = math.sin(al), math.cos(al)
                mid = _lin(a0, rho * sn, cc, s * rho * (1.0 - cs), n)
                nn = _mix(cs, n, -s * sn, cc)
            ends = []
            for lift in (ht, -ht):
                for k in (h.k0, h.k1):
                    ends.append((mid[0] + lift * nn[0] + k * e1[0],
                                 mid[1] + lift * nn[1] + k * e1[1],
                                 mid[2] + lift * nn[2] + k * e1[2]))
            out.setdefault(h.fold, []).append(tuple(ends) + (nn,))
        return out

    def panel_marks(self, fractions: Sequence[float]):
        """For each panel: a point on its front face, one on its back, and
        the normal out of the front -- where to write its number."""
        frames = self.frames(fractions)
        ht = 0.5 * self.thickness
        return [(place(frames[p.index], p.spot, ht),
                 place(frames[p.index], p.spot, -ht), frames[p.index][3])
                for p in self.panels]

    def extent(self) -> Tuple[P3, float]:
        """Centre and radius of a ball round the piece lying flat (it holds
        the piece at every stage of the folding, near enough)."""
        xs = [p[0] for panel in self.panels for p in panel.pts]
        ys = [p[1] for panel in self.panels for p in panel.pts]
        if not xs:
            return (0.0, 0.0, 0.0), 1.0
        cx, cy = 0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys))
        return (cx, cy, 0.0), 0.5 * math.hypot(max(xs) - min(xs),
                                               max(ys) - min(ys)) \
            + self.thickness

    def bounds3(self, fractions: Sequence[float]):
        """The box round the model as it stands: ``(low corner, high)``."""
        frames = self.frames(fractions)
        ht = 0.5 * self.thickness
        lo = [1e30] * 3
        hi = [-1e30] * 3
        for panel in self.panels:
            fr = frames[panel.index]
            for p in panel.pts:
                for lift in (ht, -ht):
                    q = place(fr, p, lift)
                    for k in range(3):
                        if q[k] < lo[k]:
                            lo[k] = q[k]
                        if q[k] > hi[k]:
                            hi[k] = q[k]
        return tuple(lo), tuple(hi)

    # -- do the stitch holes meet? --------------------------------------------

    def lineup(self, tol: float = 0.75):
        """Check the folded piece: where panels come to lie on each other,
        does every stitch hole of one meet a hole of the other?

        Returns ``(findings, bad)``: a line of text for each pair of panels
        lying together, and the holes (indices) with no partner within
        ``tol`` mm."""
        t = self.thickness
        frames = self.frames([1.0] * len(self.folds))
        findings: List[str] = []
        bad = set()
        reach = max(3.0 * t, 3.0) + 0.01
        n = len(self.panels)
        for i in range(n):
            for j in range(i + 1, n):
                fi, fj = frames[i], frames[j]
                if abs(_dot(fi[3], fj[3])) < 0.966:
                    continue
                pi, pj = self.panels[i], self.panels[j]
                q = place(fj, pj.pts[0])
                gap = abs((q[0] - fi[0][0]) * fi[3][0]
                          + (q[1] - fi[0][1]) * fi[3][1]
                          + (q[2] - fi[0][2]) * fi[3][2])
                if gap > reach or gap < 0.25 * t:
                    continue

                def onto(p3, fr=fi):           # into panel i's pattern plane
                    v = (p3[0] - fr[0][0], p3[1] - fr[0][1], p3[2] - fr[0][2])
                    return (_dot(v, fr[1]), _dot(v, fr[2]))

                out_j = pc.ccw([onto(place(fj, p)) for p in pj.pts])
                parts_j = [pc.ccw([onto(place(fj, p)) for p in part])
                           for part in pj.parts]
                if not any(pc.convex_overlap(A, B, 0.5)
                           for A in pi.parts for B in parts_j):
                    continue
                hi_ = [(k, (self.holes[k][0], self.holes[k][1]))
                       for k in pi.holes if self.hole_flat[k]]
                hj_ = [(k, onto(place(fj, (self.holes[k][0],
                                           self.holes[k][1]))))
                       for k in pj.holes if self.hole_flat[k]]
                over_i = [(k, p) for k, p in hi_ if pc.inside(p, out_j)]
                over_j = [(k, p) for k, p in hj_ if pc.inside(p, pi.pts)]
                if not over_i and not over_j:
                    continue
                worst, missed = 0.0, 0
                for mine, theirs in ((over_i, over_j), (over_j, over_i)):
                    for k, p in mine:
                        d = min((math.hypot(p[0] - q2[0], p[1] - q2[1])
                                 for _k2, q2 in theirs), default=1e9)
                        if d > tol:
                            bad.add(k)
                            missed += 1
                            if d < 1e8:
                                worst = max(worst, d)
                both = f"Panels {pi.number} and {pj.number}"
                total = len(over_i) + len(over_j)
                if not missed:
                    findings.append(f"{both}: all {len(over_i)} holes meet.")
                elif not over_i or not over_j:
                    findings.append(
                        f"{both}: {total} holes with nothing to meet on the "
                        f"other panel.")
                else:
                    msg = f"{both}: {missed} of {total} holes miss"
                    if worst > 0.0:
                        msg += f", by up to {worst:.1f} mm"
                    findings.append(msg + ".")
        return findings, bad


# -- helpers for the cells ----------------------------------------------------

def _strip_place(p: Pt, h: Hinge, w: float, step: float, j: int
                 ) -> Tuple[float, float]:
    """A pattern point of strip ``j`` of a bend as (how far along the fold
    line, how far across the strip from 0 to 1)."""
    dx, dy = p[0] - h.a[0], p[1] - h.a[1]
    along = dx * h.e[0] + dy * h.e[1]
    u = dy * h.e[0] - dx * h.e[1]
    tau = (u + 0.5 * w - j * step) / step
    return along, 0.0 if tau < 0.0 else 1.0 if tau > 1.0 else tau


def _cut_zone(flat: List[pc.Poly], h: Hinge, side: float, half: float,
              m: int):
    """Take the half of a bend that lies in one panel out of that panel's
    flat polygons: the band ``half`` mm deep along the hinge, in ``m``
    strips. Returns ``(what stays flat, [(strip number, polygon)])``."""
    a, e = h.a, h.e
    c2 = (-e[1], e[0])
    fold = h.fold

    def along(p):
        return (p[0] - a[0]) * e[0] + (p[1] - a[1]) * e[1]

    def depth(p):
        return side * ((p[1] - a[1]) * e[0] - (p[0] - a[0]) * e[1])

    def on_hinge(pts, flags):
        n = len(pts)
        for k in range(n):
            if flags[k] != fold:
                continue
            mid = 0.5 * (along(pts[k]) + along(pts[(k + 1) % n]))
            if h.k0 - 1e-6 <= mid <= h.k1 + 1e-6:
                return True
        return False

    def offset(dist):
        return (a[0] + side * dist * c2[0], a[1] + side * dist * c2[1])

    def spans_hinge(lo, hi):
        return min(hi, h.k1) - max(lo, h.k0) > 1e-6

    keep: List[pc.Poly] = []
    zone: List[pc.Poly] = []
    for pts, flags in flat:
        if not on_hinge(pts, flags):
            keep.append((pts, flags))
            continue
        for piece in pc.cut(pts, flags, offset(half), e, spans_hinge,
                            pc.INNER):
            (zone if on_hinge(*piece) else keep).append(piece)
    # a band that runs on well past the end of the hinge (the fold is at the
    # foot of a flap, say, and this is the wider panel it stands on) bends
    # only where the hinge is
    for k_end, sign in ((h.k0, -1.0), (h.k1, 1.0)):
        trimmed: List[pc.Poly] = []
        for pts, flags in zone:
            if max(sign * (along(p) - k_end) for p in pts) <= 2.0 * half:
                trimmed.append((pts, flags))
                continue
            origin = (a[0] + k_end * e[0], a[1] + k_end * e[1])
            for piece in pc.cut(pts, flags, origin, c2, None, pc.INNER):
                c = pc.centroid(piece[0])
                (keep if sign * (along(c) - k_end) > 0.0
                 else trimmed).append(piece)
        zone = trimmed
    for j in range(1, m):
        nxt: List[pc.Poly] = []
        for pts, flags in zone:
            nxt.extend(pc.cut(pts, flags, offset(half * j / m), e, None,
                              pc.INNER))
        zone = nxt
    strips = []
    for pts, flags in zone:
        k = int(depth(pc.centroid(pts)) / (half / m))
        k = 0 if k < 0 else m - 1 if k >= m else k
        # strips are numbered across the whole bend, from the parent's side
        strips.append((m - 1 - k if side < 0 else m + k, (pts, flags)))
    return keep, strips


def _decals(c: _Cell2, to3d, bad):
    """The marks on one face of a cell, placed by ``to3d``."""
    if not (c.dots or c.lines or c.polys):
        return None
    out = []
    for ring in c.polys:
        out.append((POLY, [to3d(p) for p in ring], "cut"))
    for fold, p, q in c.lines:
        out.append((LINE, (to3d(p), to3d(q)), fold))
    if c.dots:
        groups: Dict[tuple, list] = {}
        for x, y, dia, k in c.dots:
            groups.setdefault((dia, k in bad), []).append(to3d((x, y)))
        for (dia, wrong), pts in groups.items():
            out.append((DOTS, pts, (dia, wrong)))
    return out


def _side_faces(b0: P3, b1: P3, t1: P3, t0: P3, kind, edges, tag, size
                ) -> List[Face]:
    """The side of a bend strip between two corners: one flat face, or two
    triangles where the bend twists it out of flat."""
    ring = [b0, b1, t1, t0]
    flags = list(edges) if edges is not None else [False] * 4
    k = 0
    while k < len(ring) and len(ring) >= 3:    # corners the bend has closed
        j = (k + 1) % len(ring)
        if (abs(ring[k][0] - ring[j][0]) < 1e-9
                and abs(ring[k][1] - ring[j][1]) < 1e-9
                and abs(ring[k][2] - ring[j][2]) < 1e-9):
            del ring[j if j else k]            # edge k has no length
            del flags[k]
        else:
            k += 1
    if len(ring) < 3:
        return []
    keep = tuple(flags) if edges is not None else None
    if len(ring) == 3:
        pl = plane_of(ring)
        return [Face(tuple(ring), pl, kind, keep, None, tag, size)] \
            if pl is not None else []
    pl = plane_of(ring)
    if pl is None:
        return []
    if max(abs(pl[0] * p[0] + pl[1] * p[1] + pl[2] * p[2] - pl[3])
           for p in ring) < 1e-7:
        return [Face(tuple(ring), pl, kind, keep, None, tag, size)]
    # two triangles, folded along the diagonal that keeps the cell convex
    out = []
    one = plane_of((b0, b1, t1))
    if one is not None and one[0] * t0[0] + one[1] * t0[1] + one[2] * t0[2] \
            - one[3] <= 1e-9:
        tris = (((b0, b1, t1), (flags[0], flags[1], False)),
                ((b0, t1, t0), (False, flags[2], flags[3])))
    else:
        tris = (((b0, b1, t0), (flags[0], False, flags[3])),
                ((b1, t1, t0), (flags[1], flags[2], False)))
    for pts, fl in tris:
        pl = plane_of(pts)
        if pl is not None:
            out.append(Face(pts, pl, kind, fl if edges is not None else None,
                            None, tag, 0.5 * size))
    return out


# -- building a layout --------------------------------------------------------

def sharp_corners(outline: Sequence[Pt], degrees: float = 35.0):
    """The corners of an outline that turn by more than ``degrees``: where
    the edge of the leather shows a line down it."""
    out = set()
    n = len(outline)
    lim = math.cos(math.radians(degrees))
    for i in range(n):
        a, b, c = outline[i - 1], outline[i], outline[(i + 1) % n]
        ux, uy = b[0] - a[0], b[1] - a[1]
        vx, vy = c[0] - b[0], c[1] - b[1]
        lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
        if lu < 1e-9 or lv < 1e-9 or (ux * vx + uy * vy) / (lu * lv) < lim:
            out.add((round(b[0], 6), round(b[1], 6)))
    return out


def fold_piece(outline: Sequence[Pt], folds: Sequence[FoldLine], *,
               thickness: float = 2.0, radius: float = 0.0,
               base: Optional[int] = None, holes=(), cutouts=(),
               detail: float = 15.0, smooth: float = 0.03) -> Layout:
    """Lay a piece out for folding along its fold lines.

    ``outline`` is the piece (pattern mm, either way round); ``holes`` its
    stitch holes as ``(x, y, diameter)``; ``cutouts`` the rings cut out of
    it. ``thickness`` and ``radius`` are the leather's thickness and the
    inside radius of any fold with none of its own; ``base`` the panel
    that stays still (the biggest if not given). ``detail`` is the degrees
    of bend to a strip, ``smooth`` how closely (mm) a curved outline is
    followed."""
    ring, _f = pc.tidy([(float(p[0]), float(p[1])) for p in outline],
                       [0] * len(outline), 1e-6)
    ring = pc.simplify(pc.ccw(ring), smooth)
    regions, lines = split_piece(ring, folds)
    return Layout(regions, folds, lines, thickness=thickness, radius=radius,
                  base=base, holes=holes, cutouts=cutouts, detail=detail,
                  sharp=sharp_corners(ring))


def fold_net(outlines: Sequence[Sequence[Pt]], *, thickness: float = 2.0,
             radius: float = 0.0, base: Optional[int] = None, holes=(),
             detail: float = 15.0, smooth: float = 0.03,
             settings: Optional[Sequence[FoldLine]] = None) -> Layout:
    """Lay out panels drawn edge to edge (a paper net) for folding along
    the edges they share. ``settings`` -- fold lines from an earlier call,
    angles and all -- are kept for the edges still there."""
    rings = []
    for o in outlines:
        ring, _f = pc.tidy([(float(p[0]), float(p[1])) for p in o],
                           [0] * len(o), 1e-6)
        rings.append(pc.simplify(pc.ccw(ring), smooth))
    regions, folds, lines = net_regions(rings)
    if settings:
        for f in folds:
            for old in settings:
                if (math.dist(f.a, old.a) < 0.6 and math.dist(f.b, old.b) < 0.6
                        ) or (math.dist(f.a, old.b) < 0.6
                              and math.dist(f.b, old.a) < 0.6):
                    f.angle, f.direction = old.angle, old.direction
                    f.radius, f.order, f.active = (old.radius, old.order,
                                                   old.active)
                    break
    sharp = set()
    for r in rings:
        sharp |= sharp_corners(r)
    return Layout(regions, folds, lines, thickness=thickness, radius=radius,
                  base=base, holes=holes, detail=detail, sharp=sharp)

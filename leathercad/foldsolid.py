"""Fold a flat piece of leather into a solid model.

The pattern is the leather's middle surface. A fold line is the middle of a
bend: an arc of ``angle x (inside radius + half the thickness)`` of leather,
half taken from each side of the line -- the same sum, about the same
line, as Edit > Fold allowance works with. Between the bends the panels
stay flat. Each fold has its own angle, direction, radius and place in the
folding order; a fold that closes flat over layers folded in before it is
given the room to go round them -- a bigger radius for their thickness,
and, where they run on into its bend, a place further along the leather
to form at.

``Layout`` holds a piece cut up ready to fold: ``solid`` gives the model at
any moment of the folding as convex cells for ``bsp`` to order and the view
to paint. Pure Python, no Qt.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from . import clash
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
_REACH = 0.02                   # leather this far (mm) into a bend is let be
_TOUCH = 0.05                   # ... and this far into other leather, not said
_LAP = 0.3                      # layers this far (mm) over each other lie flat
_FURTHER = 80                   # places a stuck fold is tried at further on
# A face of a bend this near (mm) the line the bend goes round is a crease:
# it is laid on that line itself, every rung of it, so that a bend with no
# inside radius has no face there at all, not one made of rounding.
_CREASE = 1e-7
_FLAT_FRAME: Frame = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                      (0.0, 0.0, 1.0))


# What the check of the folded piece finds for a stitch hole (and LANDS, a
# mark on a panel that is not a hole of its own: where the hole that one of
# its holes should have met comes down on it)
MEETS, OFF, LONE, LANDS = 1, 2, 3, 4
# ... and, on a stack, where a hole of another piece comes through this one,
# which has no hole there (the same mark as ``sheets.BLANK``)
_BLANK = 5


@dataclass
class Lineup:
    """Two panels that lie on each other when folded, with stitch holes in
    both where they overlap: ``holes`` of them in all, ``off`` that come
    down near a hole of the other panel but not on it (``worst`` the
    furthest of those, mm), and ``lone`` with no hole of the other panel
    anywhere near. The rest meet, the furthest of them ``gap`` mm apart."""

    a: int
    b: int
    holes: int
    off: int
    worst: float
    lone: int
    gap: float = 0.0

    @property
    def met(self) -> int:
        return self.holes - self.off - self.lone


class Check:
    """What ``Layout.lineup`` found. ``findings`` has a ``Lineup`` for each
    pair of panels that are sewn together. ``state`` says of each stitch
    hole (by index) that lies against another panel with holes whether it
    ``MEETS`` the holes it is stacked with, is ``OFF`` -- something in its
    stack is out of line -- or is ``LONE``; a hole it has nothing about
    lies against no such panel. ``lands`` has, for a hole that is off, the
    spots on its own panel where the holes it is out of line with come
    down: ``[(x, y, their diameter)]`` in pattern mm. ``rings`` is the
    same spots sorted by the cell they are on, for the model."""

    __slots__ = ("findings", "state", "lands", "rings")

    def __init__(self):
        self.findings: List[Lineup] = []
        self.state: Dict[int, int] = {}
        self.lands: Dict[int, List[Tuple[float, float, float]]] = {}
        self.rings: Dict[int, List[Tuple[float, float, float]]] = {}

    def holes(self, state: int) -> set:
        """The holes found in one state."""
        return {k for k, s in self.state.items() if s == state}

    @property
    def off(self) -> set:
        """The holes that were meant to meet another and don't."""
        return self.holes(OFF)


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


class _Found:
    """What ``Layout._inside`` finds folded inside a fold that closes
    flat. ``left`` and ``right``: how far the fold would have to move
    that way for all of it to fit, as ``(mm, panel, hinge, via, way)``
    -- less than nothing is room to spare. The panel is the one that
    reaches furthest, the one to shorten instead; the hinge is the
    stretch of the fold it was measured at; ``via`` the hinge that panel
    ends in there (None for a cut edge) and ``way`` the direction it
    reaches in, on the pattern. With nothing folded in from a side the
    room is without end, and the rest None. ``cut``: ``(mm, panels)``
    for leather the fold line itself cuts across, which no move brings
    clear -- how far it is into the bend -- else None. ``layers``: how
    many panels lie inside the fold."""

    __slots__ = ("left", "right", "cut", "layers")

    def __init__(self, left, right, cut, layers):
        self.left = left
        self.right = right
        self.cut = cut
        self.layers = layers


class _Cell2:
    """A convex bit of the pattern: part of a flat panel, or one strip of a
    bend (``hinge`` set, with each corner's place along and across it).

    A cell of a piece laid on the main piece (a sheet of a stack) rides
    on the panel or the bend it is cut from: ``sheet`` is its number in
    the stack (0 for the main piece itself), ``low`` and ``high`` how far
    its two faces are out of the main piece's middle, along the normal
    out of its front (None for the main piece: half its thickness each
    way), ``kinds`` which face of its leather each of those is, and
    ``frame`` where its grain is laid from on the pattern."""

    __slots__ = ("pts", "flags", "panel", "hinge", "strip", "place", "area",
                 "box", "edges", "edges_rev", "sides", "tag", "dots",
                 "polys", "lines", "turns", "index", "sheet", "low", "high",
                 "kinds", "frame")

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
        self.polys = []           # rings of points
        self.lines = []           # (kind, p, q)
        self.turns = []           # (edge, hinge index, strip) -- see below
        self.index = -1           # its place in the layout's list of cells
        self.sheet = 0
        self.low = self.high = None
        self.kinds = (FRONT, BACK)
        self.frame = None


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
    """Panels drawn edge to edge, as a paper net: ``(regions, folds,
    lines)`` with a fold along every edge two panels share whole, lettered
    left to right (top to bottom where they line up)."""
    polys = [pc.ccw([tuple(p) for p in o]) for o in outlines]
    regions = [(p, [pc.OUTLINE] * len(p)) for p in polys]
    boxes = [pc.bounds(p) for p in polys]
    # each panel's corners by the square they are in, to find a corner of
    # one panel at a corner of another without trying every pair
    grids = []
    for poly in polys:
        grid: Dict[tuple, list] = {}
        for k, q in enumerate(poly):
            grid.setdefault((math.floor(q[0]), math.floor(q[1])), []).append(k)
        grids.append(grid)

    def corners_at(j, q):
        gx, gy = math.floor(q[0]), math.floor(q[1])
        return [k for ix in (gx - 1, gx, gx + 1) for iy in (gy - 1, gy, gy + 1)
                for k in grids[j].get((ix, iy), ())
                if math.dist(polys[j][k], q) < tol]

    shared = []
    for i in range(len(polys)):
        A, ba = polys[i], boxes[i]
        for j in range(i + 1, len(polys)):
            B, bb = polys[j], boxes[j]
            if (ba[0] > bb[2] + tol or bb[0] > ba[2] + tol
                    or ba[1] > bb[3] + tol or bb[1] > ba[3] + tol):
                continue
            found = None
            for ka in range(len(A)):
                a0, a1 = A[ka], A[(ka + 1) % len(A)]
                if math.dist(a0, a1) < 1.0:   # too short an edge to fold on
                    continue
                # the same edge of B runs the other way: it ends at a0
                for k1 in corners_at(j, a0):
                    kb = (k1 - 1) % len(B)
                    if math.dist(a1, B[kb]) < tol:
                        found = (ka, kb)
                        break
                if found:
                    break
            if found:
                shared.append((i, found[0], j, found[1]))

    def where(item):
        i, ka = item[0], item[1]
        a, b = polys[i][ka], polys[i][(ka + 1) % len(polys[i])]
        return (round(0.5 * (a[0] + b[0]), 1), -round(0.5 * (a[1] + b[1]), 1))

    shared.sort(key=where)
    folds: List[FoldLine] = []
    lines = []
    for fi, (i, ka, j, kb) in enumerate(shared):
        a, b = polys[i][ka], polys[i][(ka + 1) % len(polys[i])]
        folds.append(FoldLine(a, b, label=letter(fi)))
        regions[i][1][ka] = fi
        regions[j][1][kb] = fi
        ln = math.dist(a, b)
        lines.append((a, ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln), ln))
    return regions, folds, lines


def letter(k: int) -> str:
    """A, B ... Z, AA, AB ..."""
    out = ""
    k += 1
    while k:
        k, r = divmod(k - 1, 26)
        out = chr(65 + r) + out
    return out


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

class _Pending:
    """A cell of the model not made yet (``Layout._posed`` with ``lazy``):
    its corners and its tag, enough to put a box round it; ``full()``
    makes it -- the cell itself, or None for a strip the bend has closed
    to nothing -- and keeps it in the memo it was asked of."""

    __slots__ = ("verts", "tag", "_args", "_made")

    def __init__(self, layout, c, frames, bends, lifts, memo, key):
        if c.hinge is None:
            fr = frames[c.panel]
            ht = 0.5 * layout.thickness
            lo, hi = (-ht, ht) if lifts is None else lifts
            self.verts = [place(fr, p, z) for z in (lo, hi) for p in c.pts]
        else:
            e1, tops, bots = bends[c.hinge][:3]
            j = c.strip
            self.verts = [_strip_point(q, p0, p1, e1) for q in c.place
                          for p0, p1 in ((tops[j], tops[j + 1]),
                                         (bots[j], bots[j + 1]))]
        self.tag = c.tag
        self._args = (layout, c, frames, bends, lifts, memo, key)
        self._made = False

    def full(self):
        if self._made is False:
            layout, c, frames, bends, lifts, memo, key = self._args
            self._made = memo[key] = layout._cell(c, frames, bends, None,
                                                  lifts)
        return self._made


class Layout:
    """A piece cut up ready to fold.

    ``folds`` are the fold lines as given; ``panels`` the flat parts they
    leave (``base`` is the one that stays still); ``hinges`` where panels
    meet. For each fold: ``crosses`` (whether it cuts this piece),
    ``steps`` (when it folds, 0 if it doesn't), ``radius`` (the inside
    radius it bends at), ``wrap`` (how much of that is room for layers
    folded in earlier), ``layers`` (how many panels lie inside a fold
    that closes flat) and ``used`` (the leather the bend takes).

    A fold that closes flat needs room for the length of what is folded
    inside it as well: where that runs on into the bend, the fold can only
    form further along the leather. ``fold_piece`` moves such a fold, and
    then, for each fold again: ``shift`` is how far from its drawn line it
    folds (mm, else 0) and ``shift_toward`` the number of the panel it
    moved towards (else None). ``shift_trim`` is what to change in the
    drawing to have it fold on its line: ``[(panel, mm, fold)]``, the
    number of each panel to shorten where it reaches into a fold and by
    how much -- into this fold (``fold`` None), or into another (its
    index) that a panel made shorter pushes leather into. It is found by
    trying it on a copy of the piece (``_tried``), and is empty where
    that does not come right; ``shift_for`` is the first of those
    panels (else None). A fold that closes over an earlier one which is
    off its line as well has no such remedy but ``shift_after``, that
    fold's index, to be put right first. ``stuck`` is whether leather
    is left in the fold's bend all the same, moved or not (a warning
    says what).

    What is still in the way of a fold -- one with no room to move, or
    one of a layout that can't move its folds (a net) -- is in
    ``warnings``, with lengths in mm; ``warned`` gives the same with the
    lengths written another way.
    """

    def __init__(self, regions, folds: Sequence[FoldLine], lines, *,
                 thickness: float = 2.0, radius: float = 0.0,
                 base: Optional[int] = None, holes=(), cutouts=(),
                 marks=(), detail: float = 12.0, sharp=(),
                 numbered: bool = False,
                 moved: Optional[Sequence[float]] = None, held=(),
                 fixed=(), least: Sequence[float] = (),
                 bare: bool = False, sheets=(), outline=None,
                 settled: Optional[list] = None):
        # ``numbered``: the regions come in the order to number the panels
        # in (else they are numbered by where they lie, left to right).
        # The rest are ``fold_piece``'s, which cuts a piece again to move
        # a fold: ``moved``, how far each fold line now is to the left of
        # where it was drawn (mm); ``held``, the folds that can go no
        # further, and ``fixed``, those to be left just where they are;
        # ``least``, a radius each fold is to have at least; ``bare``,
        # what to shorten is worked out later, and the holes, cut-outs
        # and marks put on (see ``_advise``, ``_dress``). With ``moved``
        # None the folds stay on the lines given. ``settled``: the notes
        # of ``sheets.settle`` when the sheets have been laid already.
        self.folds = list(folds)
        self.sheets = list(sheets)
        self.lines = list(lines)
        self.thickness = max(0.05, float(thickness))
        self.base_radius = max(0.0, float(radius))
        self.detail = max(3.0, float(detail))
        self.warnings: List[str] = []
        self._said: List[Tuple[str, tuple, int]] = []
        self.moved = None if moved is None else [float(d) for d in moved]
        self._held = set(held)
        self._fixed = set(fixed)
        self._least = list(least)
        order = range(len(regions)) if numbered else sorted(
            range(len(regions)), key=lambda i: (
                round(pc.centroid(regions[i][0])[0], 1),
                -round(pc.centroid(regions[i][0])[1], 1)))
        self._order = list(order)
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
        self.holes: List[tuple] = []
        self.cutouts: List[List[Pt]] = []
        self.marks: List[tuple] = []
        self._build_tree()
        if self.sheets:
            self._stack(outline, settled)
        else:
            self._settle_radii()
            self._build_cells()
        self._check_spacing()
        self._check_inside()
        if not bare:
            self._advise(self)
            self._dress(holes, cutouts, marks)

    def _say(self, text: str, *lengths: float, digits: int = 2):
        """Add a warning: ``text`` with ``{}`` where each of ``lengths``
        (mm) goes."""
        self._said.append((text, lengths, digits))
        self.warnings.append(text.format(
            *(f"{mm:.{digits}f} mm" for mm in lengths)))

    def warned(self, length=None) -> List[str]:
        """The warnings, each length in them written by ``length`` (a
        function of mm) -- for a reader who works in other units. As
        ``warnings`` has them if it is not given."""
        if length is None:
            return list(self.warnings)
        return [text.format(*(length(mm) for mm in lengths))
                for text, lengths, _digits in self._said]

    def _name(self, fold: int) -> str:
        """What a fold is called in a warning: its label, else its
        number."""
        label = str(self.folds[fold].label or fold + 1)
        return label.replace("{", "{{").replace("}", "}}")

    # -- a stack of pieces ----------------------------------------------------

    def _stack(self, outline, settled):
        """Lay the sheets of a stack on the main piece and fold them with
        it.

        The sheets are laid down flat first (``sheets.settle``, unless
        ``settled`` has its notes), then cut along the main piece's fold
        lines as this layout has them and each part given the panel it
        rides on (``sheets.seat``). Then, round and round: the radius of
        each fold (``_settle_radii``, which gives room for the bodies of
        leather that come together: ``sheets.bodies``), the main piece's
        cells, and the sheets' (``sheets.cut``) -- again while what bends
        inside a fold asks for more radius than it has (``sheets.ride``),
        or leather goes over to another panel at the end of a hinge, as a
        wider bend can make it. Eight rounds at the most: the band only
        grows."""
        from . import sheets as sh
        # the main piece's own name, shape id and leather, and the side of
        # its leather it is drawn from (1 its grain side, -1 its flesh
        # side), as ``sheets.FlatStack`` has them: for the caller to set
        self.name, self.key, self.leather, self.grain = "", "", "", 1
        self.outline = model_outline(outline) \
            if outline is not None and len(outline) >= 3 \
            else sh.outline_of(self)
        self.notes = list(settled) if settled is not None \
            else sh.settle(self.outline, self.thickness, self.sheets)
        self._regions = sh.seat(self)
        self._floor: Dict[int, tuple] = {}
        for _round in range(8):
            self._bodies = sh.bodies(self)
            self._settle_radii()
            self._build_cells()
            moved = sh.cut(self)
            if not sh.ride(self) and not moved:
                break
        # (where what rides inside a bend asks for all the radius it has,
        # and is in the bend at the radius asked for, that is why it has
        # it -- whatever else asks for as much)
        t = self.thickness
        for fi, (floor, who) in self._floor.items():
            f = self.folds[fi]
            asked = f.radius if f.radius >= 0.0 else self.base_radius
            half = 0.25 * math.radians(min(f.angle, 180.0)) * (asked + t)
            if floor > 0.0 and floor >= self.radius[fi] - 1e-9 and any(
                    sh._in_band(self._regions[who[0] - 1], h, half, h.k0,
                                h.k1)
                    for h in self.tree if h.fold == fi):
                self.why[fi] = ("ride",) + who

    def sheet(self, piece: int):
        """The sheet a piece's number stands for: ``sheets[piece - 1]``,
        and None for the main piece (0) or a number no piece has."""
        if 0 < piece <= len(self.sheets):
            return self.sheets[piece - 1]
        return None

    @property
    def layout(self) -> "Layout":
        """The main piece's layout: this one (as ``sheets.FlatStack``
        has it, for a caller that takes either)."""
        return self

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
        self._kept: dict = {}          # what ``_bent`` and ``_deep`` found
        # the panels each panel is joined to, and through which hinge
        self._joins: Dict[int, List[Tuple[int, Hinge]]] = {}
        for h in self.tree:
            self._joins.setdefault(h.parent, []).append((h.child, h))
            self._joins.setdefault(h.child, []).append((h.parent, h))
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
        for i, least in enumerate(self._least):      # (see ``fold_piece``)
            if least > self.radius[i]:
                self.wrap[i] = least - self.radius[i]
                self.radius[i] = least
        theta = [math.radians(min(max(f.angle, 0.0), 180.0)) if self.steps[i]
                 else 0.0 for i, f in enumerate(self.folds)]
        done = [0.0] * n
        by_fold: Dict[int, List[Hinge]] = {}
        for h in self.tree:
            by_fold.setdefault(h.fold, []).append(h)
        # With a stack: why each fold bends as it does, as (the rule, the
        # piece, the other piece) -- see ``why``
        stack = bool(self.sheets)
        self.why: List[Optional[tuple]] = [None] * n
        for fi in sorted((i for i in range(n) if self.steps[i]),
                         key=lambda i: (self.steps[i], i)):
            if stack and fi in by_fold:
                # what rides inside the bend: no less than its pile
                floor, who = self._floor.get(fi, (0.0, ()))
                if floor > self.radius[fi]:
                    self.wrap[fi] += floor - self.radius[fi]
                    self.radius[fi] = floor
                    self.why[fi] = ("ride",) + who
            if theta[fi] >= _FLAT and fi in by_fold:
                for _round in range(24):
                    angles = list(done)
                    angles[fi] = theta[fi]
                    widths = [theta[k] * (self.radius[k] + 0.5 * t)
                              for k in range(n)]
                    fr = self.frames(None, widths, angles)
                    need, who = max(((self._clearance(h, fr, widths, angles),
                                      self._who) for h in by_fold[fi]),
                                    key=lambda got: got[0])
                    if need <= 1e-6:
                        break
                    self.radius[fi] += 0.5 * need
                    self.wrap[fi] += 0.5 * need
                    if stack:
                        self.why[fi] = ("meet",) + who
            elif stack and fi in by_fold and theta[fi] > 1e-9:
                from . import sheets as sh
                least, who = sh.corner_room(self, by_fold[fi], theta[fi],
                                            self.radius[fi])
                if least > self.radius[fi] + 1e-12:
                    self.wrap[fi] += least - self.radius[fi]
                    self.radius[fi] = least
                    # (the two by their numbers: which of them is on the
                    # panel that stays still is no matter here)
                    self.why[fi] = ("corner",) + tuple(sorted(who))
            done[fi] = theta[fi]
        self.used = [theta[k] * (self.radius[k] + 0.5 * t) for k in range(n)]
        for k in range(n):
            if self.used[k] < _SHARP:
                self.used[k] = 0.0

    def _clearance(self, h: Hinge, frames: List[Frame],
                   widths: Sequence[float], angles: Sequence[float]
                   ) -> float:
        """How much further out what this hinge carries must lie to clear
        the leather it has come down on: the main piece's own panels
        (``_clear_panels``) and, on a stack, every body of leather
        (``_clear_bodies``). ``_who`` is left with the two pieces that
        asked for the most (0 for the main piece)."""
        need = self._clear_panels(h, frames, widths, angles)
        self._who = (0, 0)
        if self.sheets:
            need = self._clear_bodies(h, frames, need)
        return need

    def _clear_bodies(self, h: Hinge, frames: List[Frame], need: float
                      ) -> float:
        """How much further out the bodies of leather this hinge carries
        must lie to clear those it has come down on, if that is more than
        ``need`` already.

        A body is a panel of the main piece, or one sheet's part on one
        panel, with its footprint and the two heights it lies between
        (``sheets.bodies``). What is carried comes to rest on top of what
        it lands on: a carried body is clear of a still one only when it
        ends above it, and where the two overlap by any real amount
        (``sheets.LAP``) it must rise by as much as they share in
        height. Two bare panels are left to ``_clear_panels``, which keeps
        the released rule and its 0.3 mm."""
        from . import sheets as sh
        o, ex, ey, n = frames[h.parent]
        s = float(self.folds[h.fold].direction)
        d = (s * n[0], s * n[1], s * n[2])
        e1 = _mix(h.e[0], ex, h.e[1], ey)
        cc = _mix(-h.e[1], ex, h.e[0], ey)
        seen: Dict[int, Optional[tuple]] = {}

        def foot(b):
            if b not in seen:
                panel, parts, low, high, _k = self._bodies[b]
                fr = frames[panel]
                if abs(_dot(fr[3], n)) < 0.985:
                    seen[b] = None
                else:
                    flat = [pc.ccw([(_dot(q, e1), _dot(q, cc))
                                    for q in (place(fr, p) for p in part)])
                            for part in parts]
                    q = place(fr, self.panels[panel].pts[0])
                    height = (q[0] - o[0]) * d[0] + (q[1] - o[1]) * d[1] \
                        + (q[2] - o[2]) * d[2]
                    way = 1.0 if _dot(fr[3], d) > 0.0 else -1.0
                    a, b2 = height + way * low, height + way * high
                    seen[b] = (flat, min(a, b2), max(a, b2),
                               [pc.bounds(f) for f in flat])
            return seen[b]

        for m, body in enumerate(self._bodies):
            if body[0] not in h.moves or foot(m) is None:
                continue
            fm = foot(m)
            for g, other in enumerate(self._bodies):
                if other[0] in h.moves or body[4] == 0 == other[4]:
                    continue
                fg = foot(g)
                if fg is None or fm[1] >= fg[2] - 1e-6:
                    continue                   # (it ends above: clear)
                lift = fg[2] - fm[1]
                if lift <= need:
                    continue
                if any(ba[0] < bb[2] and bb[0] < ba[2] and ba[1] < bb[3]
                       and bb[1] < ba[3] and pc.convex_overlap(A, B, sh.LAP)
                       for A, ba in zip(fm[0], fm[3])
                       for B, bb in zip(fg[0], fg[3])):
                    need = lift
                    self._who = (body[4], other[4])
        return need

    def _clear_panels(self, h: Hinge, frames: List[Frame],
                      widths: Sequence[float], angles: Sequence[float]
                      ) -> float:
        """How much further out the panels this hinge carries must lie to
        clear the leather they have come down on.

        A panel lies at one height, and two that overlap must be a
        thickness apart. A bend that is shut flat joins its two panels
        through every height between them: but for that, a panel with
        room to lie between two layers would be left there even where
        they are joined at its own end -- through the bend that joins
        them. Leather that lies between the two may run on as far as the
        round of the bend, and leather outside it may come as near; where
        this fold itself bends, nothing may cross such a bend at all.
        ``widths`` and ``angles`` are the leather each bend takes and how
        far round it is, as the frames have them."""
        t = self.thickness
        o, ex, ey, n = frames[h.parent]
        s = float(self.folds[h.fold].direction)
        d = (s * n[0], s * n[1], s * n[2])
        e1 = _mix(h.e[0], ex, h.e[1], ey)
        cc = _mix(-h.e[1], ex, h.e[0], ey)
        foot = {}

        def flat(points):              # as seen from over the parent panel
            return pc.ccw([(_dot(q, e1), _dot(q, cc)) for q in points])

        def footprint(i):
            if i not in foot:
                fr = frames[i]
                if abs(_dot(fr[3], n)) < 0.985:
                    foot[i] = None
                else:
                    panel = self.panels[i]
                    parts = [flat([place(fr, p) for p in part])
                             for part in panel.parts]
                    q = place(fr, panel.pts[0])
                    height = (q[0] - o[0]) * d[0] + (q[1] - o[1]) * d[1] \
                        + (q[2] - o[2]) * d[2]
                    boxes = [pc.bounds(p) for p in parts]
                    foot[i] = (parts, height, boxes)
            return foot[i]

        def across(g, panel, x0, x1, k0=None, k1=None):
            """The leather of bend ``g`` from ``x0`` to ``x1`` mm past
            the line the bend starts on, as it lies with ``panel`` (all
            along the hinge, or from ``k0`` to ``k1`` mm along it)."""
            w = widths[g.fold]
            c2 = (-g.e[1], g.e[0])
            if k0 is None:
                k0, k1 = g.k0, g.k1
            return flat([place(frames[panel],
                               (g.a[0] + k * g.e[0] + (x - 0.5 * w) * c2[0],
                                g.a[1] + k * g.e[1] + (x - 0.5 * w) * c2[1]))
                         for k, x in ((k0, x0), (k1, x0), (k1, x1),
                                      (k0, x1))])

        def over(A, B, depth):
            a, b = pc.bounds(A), pc.bounds(B)
            return (a[0] < b[2] and b[0] < a[2] and a[1] < b[3]
                    and b[1] < a[3] and pc.convex_overlap(A, B, depth))

        # Where folds cross, another stretch of this fold may carry some
        # of what stays here: more room for the fold raises that as well.
        swept = set()
        for g in self.tree:
            if g.fold == h.fold and g is not h:
                swept |= g.moves
        # The panels that lie flat, each as (panel, its polygons, their
        # boxes, its height): what the hinge carries, and what stays
        carried, still = [], []
        for i in range(len(self.panels)):
            fp = footprint(i)
            if fp is not None:
                (carried if i in h.moves else still).append(
                    (i, fp[0], fp[2], fp[1]))
        need = 0.0
        for _i, mine, boxes, high in carried:
            for _j, theirs, others, low in still:
                # (a thickness apart; no more than is asked for already)
                gap = high - low
                if abs(gap) >= t - 1e-6 or t - gap <= need:
                    continue
                if any(ba[0] < bb[2] and bb[0] < ba[2] and ba[1] < bb[3]
                       and bb[1] < ba[3] and pc.convex_overlap(A, B, _LAP)
                       for A, ba in zip(mine, boxes)
                       for B, bb in zip(theirs, others)):
                    need = t - gap
        # The bends shut flat that the hinge carries whole or leaves
        # whole, each with the heights of its two panels and its two
        # radii -- but not one that another stretch of this fold carries
        # as well, or half of: more room for the fold may then move both
        # sides of a gap (and none at all if this hinge's own parent is
        # carried so)
        bends = []
        for g in self.tree:
            if (g.fold == h.fold or angles[g.fold] < math.pi - 1e-9
                    or (g.parent in h.moves) != (g.child in h.moves)
                    or h.parent in swept or g.parent in swept
                    or g.child in swept):
                continue
            ends = footprint(g.parent), footprint(g.child)
            if ends[0] is None or ends[1] is None:
                continue
            rho = widths[g.fold] / math.pi
            bends.append((g, min(ends[0][1], ends[1][1]),
                          max(ends[0][1], ends[1][1]), rho - 0.5 * t,
                          rho + 0.5 * t))
        if not bends:
            return need
        # The leather a panel has in a bend that is made does not lie
        # flat with it -- nor does what it has in this fold's own bend,
        # which nothing may cross (where it is a whole bend: ``_bent``)
        bent: Dict[int, list] = {}
        for g in self.tree:
            w = widths[g.fold]
            if angles[g.fold] > 1e-9 and w > 0.0:
                bent.setdefault(g.parent, []).append(
                    across(g, g.parent, 0.0, 0.5 * w))
                bent.setdefault(g.child, []).append(
                    across(g, g.child, 0.5 * w, w))
        wh = widths[h.fold]
        own: Dict[int, list] = {h.parent: [], h.child: []}
        for k0, k1 in self._bent(h, wh) if wh > 0.0 else ():
            own[h.parent].append(across(h, h.parent, 0.0, 0.5 * wh, k0, k1))
            own[h.child].append(across(h, h.child, 0.5 * wh, wh, k0, k1))
        for g, low, top, r, R in bends:
            moving = g.child in h.moves
            mid = 0.5 * (low + top)
            for i, parts, _boxes, high in (still if moving else carried):
                if i in swept:             # (it rises as well, or twice)
                    continue
                # how far from the middle of the bend's gap the panel's
                # two faces are: the nearer of it, and the further
                v0, v1 = high - 0.5 * t - mid, high + 0.5 * t - mid
                near = 0.0 if v0 < 0.0 < v1 else min(abs(v0), abs(v1))
                far = max(abs(v0), abs(v1))
                want = t - (low - high) if moving else t - (high - top)
                if near >= R - 1e-6 or want <= need:
                    continue                 # (clear above or below it)
                whole = across(g, g.parent, 0.0, R)
                if any(over(whole, mine, _LAP) for mine in own.get(i, ())):
                    need = want
                    continue
                # the bend where the panel's faces meet it: its inside
                # for a panel in the gap, its outside for one beyond
                u0 = math.sqrt(max(0.0, (r + _LAP) ** 2 - far * far))
                u1 = math.sqrt(max(0.0, (R - _LAP) ** 2 - near * near))
                if u1 - u0 <= 1e-6:
                    continue
                band = across(g, g.parent, u0, u1)
                for part in parts:
                    both = pc.clip_to_convex(part, band) \
                        if over(part, band, 1e-3) else None
                    if both and not any(
                            all(pc.inside_convex(q, zone, 1e-6)
                                for q in both) for zone in bent.get(i, ())):
                        need = want
                        break
        # ... and two such bends, one carried and one that stays. Side
        # by side they are two rounds, seen end on: each a ring between
        # its two radii, and they meet where the rings do -- unless one
        # lies within the hollow of the other, or they bulge apart.
        # (Bends that cross are taken as the strips they stand over.)
        def start(g):
            """Where bend ``g`` starts, seen from over the parent panel:
            a point of that line, the way it runs, its length, and the
            way the bend bulges from it."""
            w = widths[g.fold]
            c2 = (-g.e[1], g.e[0])
            (px, py), (qx, qy), (bx, by) = (
                (_dot(q, e1), _dot(q, cc)) for q in (
                    place(frames[g.parent],
                          (g.a[0] + k * g.e[0] + (x - 0.5 * w) * c2[0],
                           g.a[1] + k * g.e[1] + (x - 0.5 * w) * c2[1]))
                    for k, x in ((g.k0, 0.0), (g.k1, 0.0), (g.k0, 1.0))))
            ln = math.hypot(qx - px, qy - py) or 1e-12
            return ((px, py), ((qx - px) / ln, (qy - py) / ln), ln,
                    (bx - px, by - py))

        for g, low, top, r, R in bends:
            if g.child not in h.moves:
                continue
            for g2, floor, roof, r2, R2 in bends:
                if (g2.child in h.moves or low - roof >= t - 1e-6
                        or floor - top >= t - 1e-6
                        or t - (low - roof) <= need):
                    continue
                (pa, ea, la, ba), (pb, eb, lb, bb) = start(g), start(g2)
                if abs(ea[0] * eb[1] - ea[1] * eb[0]) > 0.05:
                    meet = over(across(g, g.parent, 0.0, R),
                                across(g2, g2.parent, 0.0, R2), _LAP)
                else:
                    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
                    k = dx * ea[0] + dy * ea[1]        # along the first
                    k2 = k + lb * (ea[0] * eb[0] + ea[1] * eb[1])
                    ahead = dx * ba[0] + dy * ba[1]    # the way it bulges
                    apart = math.hypot(ahead, 0.5 * (floor + roof - low
                                                     - top))
                    meet = (min(la, max(k, k2)) - max(0.0, min(k, k2)) > _LAP
                            and (ahead > 0.0
                                 or ba[0] * bb[0] + ba[1] * bb[1] > 0.0)
                            and apart < R + R2 - _LAP
                            and apart + R2 > r + _LAP
                            and apart + R > r2 + _LAP)
                if meet:
                    need = t - (low - roof)
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
            for cp, cf in _strip_parts(pts, flags, h):
                c = _Cell2(cp, cf, panel)
                c.hinge, c.strip = hi, j
                c.place = [_strip_place(p, h, w, step, j) for p in cp]
                cells.append(c)
        cells = [c for c in cells if c.area > 1e-9 and len(c.pts) >= 3]
        _sides(cells, self.thickness, self._sharp)
        for c in cells:
            c.tag = ("panel", c.panel) if c.hinge is None \
                else ("bend", self.tree[c.hinge].fold)
        self.cells = cells
        by_panel: Dict[int, List[_Cell2]] = {}
        self._by_panel = by_panel
        for k, c in enumerate(cells):
            c.index = k
            by_panel.setdefault(c.panel, []).append(c)
        _turns(cells)
        self.hole_panel: List[int] = []
        self.hole_flat: List[bool] = []

    def _dress(self, holes=(), cutouts=(), marks=()):
        """Put the stitch holes, the cut-outs and the lines drawn on the
        piece on the cells they are on. (Last, and once: ``fold_piece``
        may cut a piece several times over before its folds are where
        they can form.)"""
        cells, by_panel = self.cells, self._by_panel
        self.holes = [tuple(h) for h in holes]
        self.hole_sheet = [0] * len(self.holes)
        self.cutouts = [pc.ccw([tuple(p) for p in c]) for c in cutouts]
        self.marks = [(kind, [tuple(p) for p in pts]) for kind, pts in marks]
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
        # lines drawn on the piece (slits, score lines): each bit on the
        # cell it crosses. A drawing with a great deal of them (lettering)
        # shows the first few thousand strokes
        budget = 6000
        for kind, pts in self.marks:
            for p, q in zip(pts, pts[1:]):
                budget -= 1
                if budget < 0:
                    break
                x0, x1 = (p[0], q[0]) if p[0] <= q[0] else (q[0], p[0])
                y0, y1 = (p[1], q[1]) if p[1] <= q[1] else (q[1], p[1])
                for c in cells:
                    b = c.box
                    if x0 > b[2] or x1 < b[0] or y0 > b[3] or y1 < b[1]:
                        continue
                    bit = pc.clip_segment_to_convex(p, q, c.pts)
                    if bit is not None and math.dist(*bit) > 1e-6:
                        c.lines.append((kind, bit[0], bit[1]))
        if self.sheets:
            # the sheets' own holes (numbered on from the main piece's),
            # cut-outs and marks; then what there is to tell of the stack
            # folded, and what the judge finds at each whole step
            from . import sheets as sh
            self._own_holes = len(self.holes)
            sh.dress(self)
            self.notes += sh.fold_notes(self)
            self.notes += sh.judge(self)

    def _check_spacing(self):
        """Warn of folds too close together for the bends they need."""
        seen = self._close = set()
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
                    self._say(f"Folds {self._name(key[0])} and "
                              f"{self._name(key[1])} are {{}} apart, but "
                              f"their bends need {{}} between them.",
                              gap, room, digits=1)

    # -- what is folded inside a fold -----------------------------------------

    def _check_inside(self):
        """See that what is folded inside each fold that closes flat fits
        there. Its radius gives such a fold room for the thickness of
        what it closes over; but those layers may run on past the line
        the bend starts on, and a fold of their own has a rounded end. All
        of it has to lie within the round of the bend.

        Leather that comes into the bend from the child's side is cleared
        by a fold formed nearer the parent, and the other way about; if it
        comes in from both sides nowhere clears both, and the fold goes
        half way between. A fold that should move, and can, is left in
        ``to_move`` as ``(fold, mm to its left, whether it is then to
        stay there)`` for ``fold_piece`` to cut the piece again -- the
        folds in the order they are made, as a later one is measured with
        the earlier ones where they end up. Of the rest this says what
        was found: ``shift`` and so on for a fold that has moved, a
        warning for leather still in the way. What to shorten is worked
        out last of all, for the layout that is kept (``_advise``).

        A fold left with leather in its bend is not folded as it would
        be, and nothing that closes over the same leather later can be
        measured against it: such a fold stays where it is, and is said
        to wait for the first."""
        n = len(self.folds)
        self.shift = [0.0] * n
        self.shift_toward: List[Optional[int]] = [None] * n
        self.shift_for: List[Optional[int]] = [None] * n
        self.shift_trim: List[List[tuple]] = [[] for _ in range(n)]
        self.shift_after: List[Optional[int]] = [None] * n
        self.stuck = [False] * n
        # (for a stack's notes: how far leather is left in a fold's bend,
        # where it is and with the fold on its line)
        self._left_in: Dict[int, float] = {}
        self.layers = [0] * n
        self.to_move: Optional[Tuple[int, float, bool]] = None
        self._found: Dict[int, _Found] = {}
        # (the pieces whose leather was found reaching into each fold's
        # bend: 0 the main piece, else a sheet)
        self._reaching: Dict[int, set] = {}
        # the folds to work out a remedy for: (fold, the place of its
        # warning in the list -- None for a fold that has moved clear --
        # and, for leather from both sides, what the warning names)
        self._asks: List[Tuple[int, Optional[int], Optional[list]]] = []
        self._by_fold: Dict[int, List[Hinge]] = {}
        for h in self.tree:
            self._by_fold.setdefault(h.fold, []).append(h)
        # First of all, a fold that has moved and whose bend no longer
        # fits where it stopped (it bends wider there than it did): back
        # with it, before anything is measured against it
        for fi in self._by_fold if self.moved is not None else ():
            at = self.moved[fi]
            if abs(at) > 1e-9 and fi not in self._fixed:
                room, _what = self._room(fi, at > 0.0)
                if room < -0.5 * _REACH:
                    back = max(room, -abs(at))
                    self.to_move = (fi, back if at > 0.0 else -back, True)
                    return
        # the folds that are not as they were drawn, in the order they
        # are made: True for one with leather left in its bend
        upset: Dict[int, bool] = {}
        for fi in sorted((i for i in range(n) if self.steps[i]),
                         key=lambda i: (self.steps[i], i)):
            got = self._look(fi)
            if got is None:
                continue
            self.layers[fi] = got.layers
            left, right = got.left[0], got.right[0]
            name = self._name(fi)
            at = self.moved[fi] if self.moved is not None else 0.0
            # The earlier folds that are not as drawn, between this one
            # and the leather that reaches furthest into it: what was
            # measured goes by where they are. (Where folds can move: a
            # layout whose folds all stay on their lines is measured as
            # it is drawn throughout.)
            leans = set()
            for mm, side, here in ((left, got.left, at > 1e-9),
                                   (right, got.right, at < -1e-9)):
                if mm > _REACH or here:      # (in its way, or was)
                    leans |= self._leans(side)
            before = [g for g in upset if g in leans] \
                if self.moved is not None else []
            first = next((g for g in before if upset[g]), None)
            if left + right > _REACH:          # no room for both: half way
                step = 0.5 * (left - right)
            elif left > _REACH:
                step = left
            elif right > _REACH:
                step = -right
            else:
                step = 0.0
            room, what = self._room(fi, step > 0.0) if step else (0.0, "")
            room = max(0.0, room)
            short = abs(step) > room + 1e-9
            step = min(step, room) if step > 0.0 else max(step, -room)
            free = (self.moved is not None and first is None
                    and fi not in self._held and fi not in self._fixed)
            if free and abs(step) > 0.5 * _REACH:
                self.to_move = (fi, step, short)
                return
            if abs(at) > 1e-9:
                h = (got.left[2] if at > 0.0 else got.right[2]) \
                    or self._by_fold[fi][0]
                self.shift[fi] = abs(at)
                self.shift_toward[fi] = self.panels[
                    h.left if at > 0.0 else h.right].number
                upset[fi] = False
            if got.cut is not None:
                self.stuck[fi] = True
                mm, panels = got.cut
                self._left_in[fi] = (mm, mm)
                names = _listed([self.panels[p].number for p in panels])
                self._say(f"Fold {name} runs across panels {names}, which "
                          f"are folded inside it: each is shown cut along "
                          f"the fold and {{}} into its bend. Cutting each "
                          f"back by that much at the fold clears it.", mm)
            # on each side: how far the leather is in the way of the fold
            # where it now is, how far it would be with the fold on its
            # line, and what reaches furthest
            over = [(now, drawn, side)
                    for now, drawn, side in ((left, left + at, got.left),
                                             (right, right - at, got.right))
                    if now > _TOUCH]
            if not over:
                if self.shift[fi] and before:
                    self.shift_after[fi] = before[0]
                elif self.shift[fi]:
                    self._asks.append((fi, None, None))
                continue
            upset[fi] = self.stuck[fi] = True
            self._left_in[fi] = (
                max([self._left_in.get(fi, (0.0, 0.0))[0]]
                    + [now for now, _d, _s in over]),
                max([self._left_in.get(fi, (0.0, 0.0))[1]]
                    + [drawn for _n, drawn, _s in over]))
            if first is not None:
                self._say(f"Fold {name} closes over what is left in the "
                          f"way of fold {self._name(first)}: put fold "
                          f"{self._name(first)} right first.")
                continue
            more = " further" if self.shift[fi] else ""
            # (with an earlier fold off its line in between, what to
            # shorten can't be told)
            wait = f" Put fold {self._name(before[0])} right first." \
                if before else ""
            if len(over) == 2:
                (_now, d1, s1), (_now, d2, s2) = sorted(
                    over, key=lambda side: side[2][1])
                p1, p2 = (self.panels[s[1]].number for s in (s1, s2))
                if min(d1, d2) > _TOUCH:
                    # (where folds cross, one panel may do both)
                    both = f"panel {p1} by {{}} and by {{}} again" \
                        if p1 == p2 \
                        else f"panel {p1} by {{}} and panel {p2} by {{}}"
                    self._say(f"What is folded inside fold {name} reaches "
                              f"into its bend from both sides: {both}."
                              f"{wait}", d1, d2)
                    if not wait:
                        self._asks.append((fi, len(self._said) - 1,
                                           [(p1, d1), (p2, d2)]))
                    continue
                if d2 > d1:
                    d1, p2 = d2, p1
                self._say(f"What is folded inside fold {name} reaches {{}} "
                          f"into its bend, and the fold cannot move{more} "
                          f"out of its way: panel {p2} is folded in from "
                          f"the other side.{wait}", d1)
            else:
                now, drawn, _side = over[0]
                why = ""
                if self.moved is not None:
                    why = f", and the fold cannot move{more} out of its way"
                    if short and what and room <= 0.5 * _REACH:
                        why += f": its bend would not fit before {what}"
                self._say(f"What is folded inside fold {name} reaches {{}} "
                          f"into its bend{why}.{wait}", max(now, drawn))
            if not wait:
                self._asks.append((fi, len(self._said) - 1, None))

    def _look(self, fi: int) -> Optional[_Found]:
        """What is folded inside fold ``fi`` when it has closed, the
        folds before it made (``_inside``): None for a fold that isn't
        made, has no bend, or does not close flat."""
        if fi not in self._found:
            f = self.folds[fi]
            if (not self.steps[fi] or fi not in self._by_fold
                    or self.used[fi] <= 0.0
                    or math.radians(min(f.angle, 180.0)) < _FLAT):
                return None
            at = (self.steps[fi], fi)
            made = [1.0 if step and (step, k) <= at else 0.0
                    for k, step in enumerate(self.steps)]
            self._found[fi] = self._inside(fi, self._by_fold[fi], made)
        return self._found[fi]

    def _leans(self, side: tuple) -> set:
        """The folds that a measure of ``_inside`` goes by: those between
        the hinge it was taken at and the panel that reaches furthest,
        and the one that panel ends in there."""
        _mm, panel, h, via, _way = side
        if panel is None:
            return set()
        out = {via.fold} if via is not None else set()
        away = self._away(h)
        while away[panel]:
            panel, g = next((q, g) for q, g in self._joins[panel]
                            if away[q] < away[panel])
            out.add(g.fold)
        return out

    def _advise(self, drawn: "Layout"):
        """Work out what to change in the drawing for each fold that is
        off its line, or has leather left in its bend -- by trying it
        (``_tried``) on ``drawn``, the same piece with every fold on its
        line. What comes right goes into ``shift_trim``, or on to the end
        of the fold's warning; of what does not, nothing is said.

        On a stack the pieces laid on the main piece are not shortened
        with it: what comes right for the main piece is tried again on
        the stack as it stands -- the main piece shortened so, the pieces
        on it as they are -- and given only where that has the fold form
        on its line (``_holds``)."""
        for fi, warning, named in self._asks:
            trim = self._tried(drawn, fi)
            if trim and self.sheets and not self._holds(drawn, fi):
                trim = []
            if warning is None:
                self.shift_trim[fi] = trim
                self.shift_for[fi] = trim[0][0] if trim else None
                continue
            text, lengths = self._shorten(trim)
            text = text[:1].upper() + text[1:] + "."
            # (both sides: as a rule it is just what the warning has named)
            if named is not None \
                    and sorted((p, round(mm, 2), g) for p, mm, g in trim) \
                    == sorted((p, round(mm, 2), None) for p, mm in named):
                text, lengths = ("Shorten each by that much where it "
                                 "reaches into the fold."), []
            if trim:
                said, had, digits = self._said[warning]
                said, had = f"{said} {text}", had + tuple(lengths)
                self._said[warning] = (said, had, digits)
                self.warnings[warning] = said.format(
                    *(f"{mm:.{digits}f} mm" for mm in had))
        self._asks = []

    def _tried(self, drawn: "Layout", fi: int) -> List[tuple]:
        """What to shorten in the drawing for fold ``fi`` to form on its
        line with nothing in its bend -- ``[(panel number, mm, fold)]``
        as ``shift_trim`` has it -- found by trying it.

        ``drawn`` is the piece with every fold on its line. The panel
        that reaches furthest into the fold there is made that much
        shorter on a copy of the piece (``_shortened``), and the copy is
        laid out to fold. If something is in the way then -- more of what
        is inside this fold, or leather in the bend of another that folds
        on its line as things are (a panel that is shortened at one end
        comes nearer whatever its other end is folded inside) -- that is
        shortened as well, and so on, until all of them fold where they
        are drawn. Empty if it does not come to that, or leaves two folds
        too close for their bends."""
        # this fold, and those that fold on their lines as it is (one
        # that is off its line has its own remedy), in the order made
        ours = [k for k in sorted(range(len(self.folds)),
                                  key=lambda k: (self.steps[k], k))
                if k == fi or not (self.shift[k] or self.stuck[k])]
        trim: Dict[tuple, float] = {}
        piece: Optional[Layout] = drawn
        self._steps_tried = steps = []
        for _try in range(2 * len(self.panels)):
            # the first of them with leather in its way, and what
            # reaches furthest into it
            for k in ours:
                got = piece._look(k)
                side = max(got.left, got.right, key=lambda m: m[0]) \
                    if got is not None else (0.0,)
                if side[0] > _REACH:
                    break
            else:
                if piece._close - self._close:
                    break
                return [(panel, mm, fold)
                        for (panel, fold), mm in trim.items()]
            if not trim and k != fi:
                break                  # (it is not this fold's to mend)
            mm, panel, _h, _via, way = side
            steps.append((panel, mm, way))
            piece = piece._shortened(panel, mm, way)
            if piece is None:
                break
            key = (self.panels[panel].number, None if k == fi else k)
            trim[key] = trim.get(key, 0.0) + mm
        return []

    def _shortened(self, panel: int, mm: float, way: Pt, sheets=()
                   ) -> Optional["Layout"]:
        """This piece with one panel ``mm`` shorter, laid out afresh with
        every fold on its line -- to see how it would fold then (see
        ``_tried``). The panel is cut back at the end that reaches
        ``way`` (a direction on the pattern): at the fold line it ends on
        there, all the leather beyond that line coming ``mm`` nearer with
        it -- or, if it ends in no fold, at its cut edge. The bends of
        the copy are cut in few strips: it is only there to be measured.

        None where that can't be done so simply: the panel has not that
        much lying flat, or ends on two fold lines there; folds cross on
        the piece; a fold line would have to move with some of the
        leather along it and stay with the rest; or the piece would no
        longer be hinged as it is.

        ``sheets``: pieces to lay on the copy as they are (copies of them,
        each laid afresh on it)."""
        if len(self.tree) != len(self.hinges):
            return None
        flat = [q[0] * way[0] + q[1] * way[1]
                for c in self._by_panel.get(panel, ()) if c.hinge is None
                for q in c.pts]
        if not flat or mm > max(flat) - min(flat) - 1e-6:
            return None
        # the folds the panel ends on that way, and the panels beyond
        end, n, vias, beyond = max(flat), way, [], set()
        for other, g in self._joins.get(panel, ()):
            (ax, ay), e, _ln = self.lines[g.fold]
            out = (e[1], -e[0]) if panel == g.left else (-e[1], e[0])
            at = ax * out[0] + ay * out[1]
            if out[0] * way[0] + out[1] * way[1] > 0.99 \
                    and at > max(flat) - 1e-6:
                if vias and abs(at - end) > 1e-6:
                    return None
                end, n = at, out
                vias.append(g)
                beyond.add(other)
        todo = list(beyond)
        for p in todo:
            for q, _g in self._joins.get(p, ()):
                if q != panel and q not in beyond:
                    beyond.add(q)
                    todo.append(q)

        def cut_back(q):
            over = q[0] * n[0] + q[1] * n[1] - end + mm
            if over <= 0.0 or vias and over > mm + 1e-6:
                return q
            return (q[0] - over * n[0], q[1] - over * n[1])

        regions = []
        for k, p in enumerate(self.panels):
            pts = p.pts
            if k == panel:
                pts = [cut_back(q) for q in pts]
            elif k in beyond:
                pts = [(q[0] - mm * n[0], q[1] - mm * n[1]) for q in pts]
            pts, flags = pc.tidy(pts, p.flags)
            if len(pts) < 3:
                return None
            regions.append((pts, flags))
        lines = list(self.lines)
        for g, line in enumerate(lines):
            goes = [h in vias or h.left in beyond and h.right in beyond
                    for h in self.hinges if h.fold == g]
            if not any(goes):
                continue
            (ax, ay), ge, ln = line
            if all(goes):
                lines[g] = ((ax - mm * n[0], ay - mm * n[1]), ge, ln)
            elif abs(ge[0] * n[0] + ge[1] * n[1]) < 1.0 - 1e-9:
                return None        # (one along the way slides in itself)
        new = Layout(regions, self.folds, lines, thickness=self.thickness,
                     radius=self.base_radius, base=self.base, detail=90.0,
                     numbered=True, bare=True,
                     sheets=[copy.copy(s) for s in sheets])

        def hinged(lay):
            return sorted((h.fold, h.left, h.right) for h in lay.hinges)

        return new if hinged(new) == hinged(self) else None

    def _holds(self, drawn: "Layout", fi: int) -> bool:
        """Whether what ``_tried`` last found to shorten for fold ``fi``
        has it form on its line on the stack as it stands: the main piece
        shortened so, step by step, with the pieces laid on it as they
        are, and nothing then left reaching into the fold."""
        piece: Optional[Layout] = drawn
        for panel, mm, way in self._steps_tried:
            piece = piece._shortened(panel, mm, way, self.sheets)
            if piece is None:
                return False
        got = piece._look(fi)
        if got is None:
            return True
        return max(got.left, got.right, key=lambda m: m[0])[0] <= _REACH

    def _shorten(self, trim: Sequence[tuple]) -> Tuple[str, List[float]]:
        """``trim`` (see ``shift_trim``) in words: a text with ``{}`` for
        each length, and the lengths."""
        here = [(panel, mm) for panel, mm, fold in trim if fold is None]
        if not here:
            return "", []
        text = "shorten " + " and ".join(
            f"panel {panel} by {{}}" for panel, _mm in here) + (
            " where it reaches into the fold" if len(here) == 1
            else " where they reach into the fold")
        lengths = [mm for _panel, mm in here]
        for panel, mm, fold in trim:
            if fold is not None:
                text += (f", and then panel {panel} by {{}} where it "
                         f"reaches into fold {self._name(fold)}")
                lengths.append(mm)
        return text, lengths

    def remedy(self, fold: int, length=None) -> str:
        """What to change in the drawing for a fold that has moved to
        fold on its line again, as a sentence -- empty if there is
        nothing sure to say. ``length`` writes a length in mm out (as
        ``warned`` has it)."""
        if length is None:
            def length(mm):
                return f"{mm:.2f} mm"
        if self.shift_trim[fold]:
            text, lengths = self._shorten(self.shift_trim[fold])
            return "To have it fold on its line, " + text.format(
                *(length(mm) for mm in lengths)) + "."
        after = self.shift_after[fold]
        if after is not None:
            return (f"It closes over fold "
                    f"{self.folds[after].label or after + 1}, which is off "
                    f"its line as well: put that right first.")
        return ""

    def _inside(self, fi: int, hinges: Sequence[Hinge],
                made: Sequence[float]) -> "_Found":
        """What is folded inside fold ``fi``, and how far the fold would
        have to move to its left, and to its right, for it to fit -- a
        ``_Found``.

        The fold is closed, the folds made before it are made (``made``
        has 1 for those, and for this one) and the rest lie flat. Seen
        along a hinge, the leather between the faces of its two panels
        may reach as far as the round of the bend and no further: ``v``
        mm off the middle of the gap it may run ``sqrt(r * r - v * v)``
        past the line the bend starts on (``r`` the inside radius), and
        not at all where it lies against a panel -- see ``_reach``.

        What counts is the leather of the panels that lie inside the fold
        -- some of each between its two panels where they lie over each
        other, no further on than the round of the bend, or in the
        leather of the bend itself (it lay across the line when the fold
        was made) -- and of the bends that join those. The panels the
        hinge itself joins don't: they go round the bend, not inside it.
        Nor does a panel the fold runs along elsewhere and now closes
        over (where folds cross): the fold line cuts it, wherever the
        line is, and no move brings that clear -- it is found, and kept
        apart.

        On a stack, what rides on a panel folded in earlier is leather
        folded in earlier as well, the part of it standing past the main
        piece's edge included; what rides on the fold's own two panels
        bends with it and never moves it (and what rides on a panel the
        fold runs along is said of the stack as a whole: see
        ``sheets.fold_notes``)."""
        f = self.folds[fi]
        t = self.thickness
        w = self.used[fi]
        rho = w / math.radians(min(f.angle, 180.0))
        r = rho - 0.5 * t
        if self.sheets:
            # (what rides round the inside of the bend takes that much of
            # its round)
            r -= max((self.sheets[c.sheet - 1].hi
                      for c in self.sheet_cells if c.hinge is not None
                      and self.tree[c.hinge].fold == fi
                      and self.sheets[c.sheet - 1].side == f.direction),
                     default=0.0)
        s = float(f.direction)
        frames = self.frames(made)
        arcs = self._arcs(frames, made)
        along = set()                  # the panels the fold runs along
        for h in self.hinges:
            if h.fold == fi:
                along.update((h.left, h.right))
        sides: Dict[bool, list] = {True: [], False: []}     # left, right
        cut: List[Tuple[float, int]] = []
        layers = set()
        for h in hinges:
            spans = self._bent(h, w)
            rounds = self._rounds(h, spans, rho - 0.5 * t) \
                if self.sheets else [(spans, r)]
            o, ex, ey, n = frames[h.parent]
            c2 = (-h.e[1], h.e[0])
            e1 = _mix(h.e[0], ex, h.e[1], ey)
            # a point of the line the bend goes round
            axis = place(frames[h.parent], (h.a[0] - 0.5 * w * c2[0],
                                            h.a[1] - 0.5 * w * c2[1]),
                         s * rho)
            # Leather is measured against the panel it came in on: across
            # that panel on into the bend, and out of its face. What the
            # hinge carries came in on the child (which lies a few degrees
            # off the parent if the fold is not quite shut).
            _o, cx, cy, cn = frames[h.child]
            lie = {False: (_mix(c2[0], ex, c2[1], ey), n),
                   True: (_mix(-c2[0], cx, -c2[1], cy), cn)}
            away = self._away(h)

            def past(points, carried):
                """How far past the round of the bend the furthest of
                some corners is (None: none of them is in the bend)."""
                on, out = lie[carried]
                most = None
                for q in points:
                    dq = (q[0] - axis[0], q[1] - axis[1], q[2] - axis[2])
                    k, v = _dot(dq, e1), _dot(dq, out)
                    if abs(v) < r + t - 1e-6 and any(
                            k0 <= k <= k1 for k0, k1 in spans):
                        mm = _dot(dq, on) - math.sqrt(max(0.0, r * r - v * v))
                        if most is None or mm > most:
                            most = mm
                return most

            # how far back the two panels lie over each other, flat
            depth = max(0.0, min(self._deep(h, False), self._deep(h, True))
                        - 0.5 * w)
            got = []
            clear: Dict[int, bool] = {}
            for c in self.cells + (self.sheet_cells if self.sheets else []):
                p = c.panel
                if p in (h.parent, h.child) or p not in away \
                        or c.sheet and p in along:
                    continue
                carried = p in h.moves
                if c.hinge is None and not c.sheet:
                    # (a panel that lies flat with the fold's two, in
                    # the sheet of one of them or beyond: none of it is
                    # between them, and there is no more to work out)
                    if p not in clear:
                        out = lie[carried][1]
                        q = place(frames[p], self.panels[p].pts[0])
                        v = _dot((q[0] - axis[0], q[1] - axis[1],
                                  q[2] - axis[2]), out)
                        clear[p] = abs(_dot(frames[p][3], out)) > 1 - 1e-9 \
                            and abs(v) - 0.5 * t >= r - 1e-6
                    if clear[p]:
                        continue
                front, back = self._corners(c, frames, arcs)
                reach = None
                for there, rr in rounds:
                    got1 = _reach(front, back, axis, e1, *lie[carried],
                                  there, rr, t, depth)
                    if got1 is not None:
                        reach = got1 if reach is None else (
                            max(reach[0], got1[0]), reach[1] or got1[1])
                if reach is not None:
                    got.append((c, carried, front, back) + reach)
            inside = {c.panel for c, _car, _fr, _bk, _mm, within in got
                      if within}
            layers |= inside
            for c, carried, front, back, mm, _within in got:
                if c.panel not in inside:
                    continue
                if mm > _TOUCH:
                    self._reaching.setdefault(fi, set()).add(c.sheet)
                if c.panel in along:
                    if mm > _TOUCH:
                        cut.append((mm, c.panel))
                    continue
                # The panel to shorten is the cell's own -- or, for a
                # strip of another bend that is made, the one of that
                # fold's two panels that is nearer this hinge (unless
                # that is the hinge's own). ``via``: the fold the leather
                # ends in here, if it is not a cut edge -- for a flat
                # cell, the bend that starts at its furthest corners.
                panel, via = c.panel, None
                if c.hinge is not None:
                    g = self.tree[c.hinge]
                    near = g.parent if away[g.parent] < away[g.child] \
                        else g.child
                    if away[near] and made[g.fold]:
                        panel, via = near, g
                else:
                    m = len(c.pts)
                    for k, hinge, _strip in c.turns:
                        g = self.tree[hinge]
                        far = g.child if g.parent == panel else g.parent
                        if made[g.fold] and away.get(far, 0) > away[panel]:
                            j = k + 1 if k + 1 < m else 0
                            end = past((front[k], front[j], back[k],
                                        back[j]), carried)
                            if end is not None and end > mm - 1e-6:
                                via = g
                # what came in on the child wants the fold nearer the
                # parent, and the other way about
                left = (h.child == h.left) != carried
                sides[left].append((mm, away[panel], panel, via is None, h,
                                    via, lie[carried][0]))
        out = []
        for left in (True, False):
            if not sides[left]:
                out.append((-math.inf, None, None, None, None))
                continue
            # the leather that reaches furthest -- and of all that reaches
            # as far, the nearest through the leather (one that ends in a
            # fold before one that is cut off there): shortening it draws
            # the rest back with it
            most = max(m[0] for m in sides[left])
            mm, _steps, panel, _edge, h, via, way = min(
                (m for m in sides[left] if m[0] > most - 1e-6),
                key=lambda m: m[1:4])
            _o, px, py, _n = frames[panel]
            out.append((mm, panel, h, via, (_dot(way, px), _dot(way, py))))
        across = None
        if cut:
            across = (max(mm for mm, _p in cut),
                      sorted({p for _mm, p in cut}))
        return _Found(out[0], out[1], across, len(layers))

    def _rounds(self, h: Hinge, spans, inside: float):
        """On a stack, the stretches of a hinge (``spans``, as ``_bent``
        gives them) each with the inside radius left for leather folded
        in: the round of the bend, ``inside`` mm, less the pile of what
        rides round the inside of the bend along that stretch -- and
        where that rides on round the bend past the end of the hinge,
        standing past the main piece's edge, that stretch too, with the
        round its pile leaves. ``[(stretches, radius)]``."""
        f = self.folds[h.fold]
        hi_of = []
        for c in self.sheet_cells:
            if c.hinge is None or self.tree[c.hinge] is not h:
                continue
            s = self.sheets[c.sheet - 1]
            if s.side != f.direction:
                continue
            ks = [(p[0] - h.a[0]) * h.e[0] + (p[1] - h.a[1]) * h.e[1]
                  for p in c.pts]
            hi_of.append((min(ks), max(ks), s.hi))
        if not hi_of:
            return [(spans, inside)]
        stretches = list(spans)
        for k0, k1, _hi in hi_of:
            # (past the ends of the hinge, where it rides on round)
            for lo, top in ((k0, min(k1, h.k0)), (max(k0, h.k1), k1)):
                if top - lo > 2.0 * _REACH:
                    stretches.append((lo + _REACH, top - _REACH))
        cuts = sorted({k for k0, k1, _hi in hi_of for k in (k0, k1)})
        out: Dict[float, list] = {}
        for lo, top in stretches:
            ks = [lo] + [k for k in cuts if lo < k < top] + [top]
            for a, b in zip(ks, ks[1:]):
                if b - a <= 1e-9:
                    continue
                mid = 0.5 * (a + b)
                pile = max((hi for k0, k1, hi in hi_of if k0 <= mid <= k1),
                           default=0.0)
                out.setdefault(inside - pile, []).append((a, b))
        return [(there, rr) for rr, there in out.items()]

    def _bent(self, h: Hinge, w: float) -> List[Tuple[float, float]]:
        """The stretches of a hinge the fold has a whole bend along, for
        leather to be inside of: where both its panels are deep enough
        for their half of it, ``w`` mm in all -- or half as deep as they
        get, for a panel that is not so deep anywhere. (Where a panel
        comes to a point at the end of the hinge, as the corner flap of
        an envelope does, there is no bend to speak of.) Each as ``(from,
        to)`` mm along the hinge, a hair in from its ends: leather that
        only touches the end of a hinge is beside it."""
        key = (id(h), w)
        if key not in self._kept:
            c2 = (-h.e[1], h.e[0])
            out = [(h.k0, h.k1)]
            for child, side in ((False, -1.0), (True, 1.0)):
                deep = side * min(0.5 * w, 0.5 * self._deep(h, child))
                panel = self.panels[h.child if child else h.parent]
                runs = pc.spans(panel.pts, (h.a[0] + deep * c2[0],
                                            h.a[1] + deep * c2[1]), h.e)
                out = [(max(lo, p), min(hi, q)) for lo, hi in out
                       for p, q in runs if min(hi, q) > max(lo, p)]
            self._kept[key] = [(lo + _REACH, hi - _REACH) for lo, hi in out
                               if hi - lo > 2.0 * _REACH]
        return self._kept[key]

    def _deep(self, h: Hinge, child: bool) -> float:
        """How deep the child of a hinge is (or its parent), square to
        the fold: the furthest from the line of what the panel has behind
        that stretch of it."""
        key = (id(h), child)
        if key not in self._kept:
            a, e = h.a, h.e
            side = 1.0 if child else -1.0
            panel = self.panels[h.child if child else h.parent]
            b = panel.box
            far = math.hypot(b[2] - b[0], b[3] - b[1]) + 1.0

            def at(along, deep):
                return (a[0] + along * e[0] - side * deep * e[1],
                        a[1] + along * e[1] + side * deep * e[0])

            band = pc.ccw([at(h.k0, 0.0), at(h.k1, 0.0), at(h.k1, far),
                           at(h.k0, far)])
            self._kept[key] = max(
                (side * ((q[1] - a[1]) * e[0] - (q[0] - a[0]) * e[1])
                 for part in panel.parts
                 for q in pc.clip_to_convex(part, band)), default=0.0)
        return self._kept[key]

    def _corners(self, c: _Cell2, frames: Sequence[Frame], arcs):
        """Where a cell's corners are: ``(front, back)``, the ring of them
        on either face of the leather."""
        if c.hinge is None:
            fr = frames[c.panel]
            ht = 0.5 * self.thickness
            if c.low is not None:
                return ([place(fr, p, c.high) for p in c.pts],
                        [place(fr, p, c.low) for p in c.pts])
            return ([place(fr, p, ht) for p in c.pts],
                    [place(fr, p, -ht) for p in c.pts])
        arc = arcs[c.hinge]
        if c.low is not None:
            arc = _ride(arc, c.low, c.high)
        e1, tops, bots = arc[:3]
        j = c.strip
        return tuple([_strip_point(q, p0, p1, e1) for q in c.place]
                     for p0, p1 in ((tops[j], tops[j + 1]),
                                    (bots[j], bots[j + 1])))

    def _away(self, h: Hinge) -> Dict[int, int]:
        """How many folds from a hinge each panel is, going through the
        leather: 0 for the two the hinge joins. Only the panels of its
        own piece are there."""
        away = {h.parent: 0, h.child: 0}
        todo = [h.parent, h.child]
        for p in todo:
            for q, _g in self._joins.get(p, ()):
                if q not in away:
                    away[q] = away[p] + 1
                    todo.append(q)
        return away

    def _room(self, fi: int, left: bool) -> Tuple[float, str]:
        """How far fold ``fi`` can move to its left (or to its right)
        before its bend no longer fits on the panel there, and what it
        would run into: the next fold line -- which has a bend of its own
        to fit -- or the edge of the panel. Less than nothing: the bend
        does not fit where the fold is, by that much."""
        a, e, _ln = self.lines[fi]
        side = 1.0 if left else -1.0
        half = 0.5 * self.used[fi]
        room, what = math.inf, ""
        for h in self.tree:
            if h.fold != fi:
                continue
            index = h.left if left else h.right
            here = self._deep(h, index == h.child) - half
            by = f"the edge of panel {self.panels[index].number}"
            for g in self.hinges:
                if g.fold == fi or index not in (g.left, g.right):
                    continue
                ga, ge, _gl = self.lines[g.fold]
                if abs(e[0] * ge[1] - e[1] * ge[0]) > 0.26:
                    continue                  # not nearly side by side
                mid = 0.5 * (g.lo + g.hi)
                q = (ga[0] + ge[0] * mid, ga[1] + ge[1] * mid)
                gap = side * ((q[1] - a[1]) * e[0] - (q[0] - a[0]) * e[1])
                bend = 0.5 * self.used[g.fold] if g.tree else 0.0
                if gap > 0.0 and gap - half - bend <= here:
                    here = gap - half - bend
                    by = f"fold {self._name(g.fold)}"
            if here < room:
                room, what = here, by
        return (room, what) if what else (0.0, "")

    def _landing(self, drawn: "Layout"):
        """Warn where a fold that has moved puts leather into other
        leather. A flap ended short of a wall, a lip came down just past
        a folded edge: with a fold further along the piece they may no
        longer -- nor may the moved fold's own bend, or the panel it has
        moved on to, clear a panel stood up beside the folded edge.

        At each whole step from the first at which a moved fold is made,
        every cell of the piece is set against every other (the judge of
        ``clash``, boxes first) -- those two whose places against each
        other a move can change, that is (``_unmoved``) -- and what the
        piece folded on its lines -- ``drawn`` -- does not have as deep at
        that step is the moves' doing. The bends with leather left in
        them are left out: that is said already (``_check_inside``). Each
        is laid at the door of the first moved fold whose hinge has one
        of the two on either side, else of the first fold that moved
        (``_blame``). Where the two are into each other with the fold on
        its line as well, the warning says by how much. Only the main
        piece's own cells are looked at: on a stack, what the pieces laid
        on it do is the judge's to say (``sheets.judge``)."""
        moved = sorted((k for k, mm in enumerate(self.shift) if mm),
                       key=lambda k: (self.steps[k], k))
        if not moved:
            return

        def pose(lay, step):
            made = [1.0 if s and s <= step else 0.0 for s in lay.steps]
            return [(c, cell) for c, cell in lay._posed(made, sheets=False)
                    if not (c.tag[0] == "bend" and self.stuck[c.tag[1]])]

        # the deepest of each two panels or bends, at each whole step
        # (panels before folds: the same two are named the same way
        # whichever comes first)
        found: Dict[tuple, float] = {}
        for step in range(self.steps[moved[0]], self.n_steps + 1):
            posed = pose(self, step)
            keep = self._unmoved([c for c, _x in posed])
            for deep, i, j in clash.pairs([x for _c, x in posed], _TOUCH,
                                          keep):
                ta, tb = posed[i][1].tag, posed[j][1].tag
                if ta == tb:
                    continue
                pair = (step,) + tuple(sorted(
                    (ta, tb), key=lambda tag: (tag[0] != "panel", tag[1])))
                found[pair] = max(deep, found.get(pair, 0.0))
        if not found:
            return
        # ... and the same two, folded on their lines
        posed: Dict[int, list] = {}
        new: Dict[int, list] = {}
        worst: Dict[tuple, tuple] = {}
        for (step, ta, tb), deep in found.items():
            if step not in posed:
                posed[step] = [x for _c, x in pose(drawn, step)]
            was = max((clash.depth(a, b) for a in posed[step] if a.tag == ta
                       for b in posed[step] if b.tag == tb), default=0.0)
            if was >= deep - _TOUCH:
                continue
            if deep > worst.get((ta, tb), (0.0,))[0]:
                worst[ta, tb] = (deep, was)
        for (ta, tb), (deep, was) in worst.items():
            new.setdefault(self._blame(ta, tb), []).append(
                (deep, self._part(ta), self._part(tb), was))
        for fold in sorted(new):
            deep, one, other, was = max(new[fold])
            more = "" if len(new[fold]) == 1 \
                else " (and they are not the only ones)"
            if was > _TOUCH:
                self._say(f"With fold {self._name(fold)} off its line, "
                          f"{one} and {other} are drawn {{}} into each "
                          f"other ({{}} with the fold on its line){more}.",
                          deep, was)
            else:
                self._say(f"With fold {self._name(fold)} off its line, "
                          f"{one} and {other} are drawn {{}} into each "
                          f"other{more}.", deep)

    def _part(self, tag) -> str:
        """What a part of the piece is called in a warning: a panel by its
        number, a bend by its fold."""
        return f"panel {self.panels[tag[1]].number}" \
            if tag[0] == "panel" else f"fold {self._name(tag[1])}"

    def _blame(self, ta, tb) -> int:
        """The fold that has moved that two parts of the piece (their
        tags) are laid at the door of: the first moved fold whose hinge
        has one of the two on either side, else the first that moved."""
        def panels(tag):
            if tag[0] == "panel":
                return {tag[1]}
            return {p for h in self._by_fold.get(tag[1], ())
                    for p in (h.parent, h.child)}

        moved = sorted((k for k, mm in enumerate(self.shift) if mm),
                       key=lambda k: (self.steps[k], k))
        a, b = panels(ta), panels(tb)
        for k in moved:
            for h in self._by_fold.get(k, ()):
                if (a <= h.moves and not b & h.moves
                        or b <= h.moves and not a & h.moves):
                    return k
        return moved[0] if moved else -1

    def _unmoved(self, cells):
        """For the cells of the piece (bits of the pattern), which two to
        try against each other when a fold has moved: those whose places
        against each other the move can change -- one of them on a panel
        the moved line runs along (hinged there, or cut where folds
        cross), or on its bend, or carried by a moved hinge while the
        other is not -- and never two bits of one panel or one bend. Every
        other two lie as they would with every fold on its line, and what
        is between them is no news of the move. A function of two places
        in the list (``clash``'s ``pick``)."""
        hinges = [(hi, h) for hi, h in enumerate(self.tree)
                  if self.shift[h.fold]]
        # (every panel the moved line runs along, hinged in the tree or
        # cut where folds cross: the move re-cuts it)
        beside = {p for h in self.hinges if self.shift[h.fold]
                  for p in (h.left, h.right)}
        bends = {hi for hi, _h in hinges}
        near = []
        carried = []
        tags = []
        for c in cells:
            near.append(c.panel in beside or c.hinge in bends)
            carried.append(tuple(c.panel in h.moves for _hi, h in hinges))
            tags.append(c.tag)

        def keep(i, j):
            # (two bits of one panel, or of one bend, are one part: what is
            # between them is not looked for)
            return tags[i] != tags[j] and (near[i] or near[j]
                                           or carried[i] != carried[j])

        return keep

    # -- the solid ------------------------------------------------------------

    def _arcs(self, frames, fractions):
        """For each hinge: the direction along the fold, the lines the
        bend's strips meet at on the front and on the back face, the way
        the front of the leather faces on each strip -- with the panel
        before the first strip and the one after the last at either end --
        and whether the strips are too narrow to see, on the front and on
        the back (the inside of a bend with no radius is a crease). Then,
        for what rides on the bend (``_rungs``): those lines on the middle
        of the leather and the way it faces there, a point of the line the
        bend goes round and how far that is from the middle (None and 0
        for a bend lying flat), and the way it bends. A face that lies on
        the line the bend goes round is laid on it exactly."""
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
            tops, bots, mids, nns = [], [], [], []
            M = 2 * m
            for j in range(M + 1):
                if phi < 1e-9:
                    along = w * j / M
                    mid = (a0[0] + cc[0] * along, a0[1] + cc[1] * along,
                           a0[2] + cc[2] * along)
                    nn = n
                else:
                    al = phi * j / M
                    rho = w / phi
                    sn, cs = math.sin(al), math.cos(al)
                    mid = _lin(a0, rho * sn, cc, s * rho * (1.0 - cs), n)
                    nn = _mix(cs, n, -s * sn, cc)
                mids.append(mid)
                nns.append(nn)
                tops.append((mid[0] + ht * nn[0], mid[1] + ht * nn[1],
                             mid[2] + ht * nn[2]))
                bots.append((mid[0] - ht * nn[0], mid[1] - ht * nn[1],
                             mid[2] - ht * nn[2]))
            axis, rho = None, 0.0
            if phi >= 1e-9:
                rho = w / phi
                axis = _lin(a0, 0.0, cc, s * rho, n)
                for lift, rungs in ((ht, tops), (-ht, bots)):
                    if abs(rho - s * lift) < _CREASE:
                        rungs[:] = [axis] * (M + 1)
            if phi < 1e-9:
                faces = [n] * (M + 2)
            else:
                faces = [n]
                for j in range(M):
                    al = phi * (j + 0.5) / M
                    faces.append(_mix(math.cos(al), n, -s * math.sin(al), cc))
                faces.append(_mix(math.cos(phi), n, -s * math.sin(phi), cc))
            out.append((e1, tops, bots, faces,
                        math.dist(tops[0], tops[1]) < _SHARP,
                        math.dist(bots[0], bots[1]) < _SHARP,
                        mids, nns, axis, rho, s))
        return out

    @staticmethod
    def _edges(c: _Cell2, arcs):
        """What each edge of a cell's front face is, and of its back face:
        True for an edge of the leather, False for a seam inside it, and
        where the leather goes on round a bend, the way the face it goes on
        to is turned (its normal). From a flat panel that is the first
        strip of the bend -- or, where the strips are too narrow to see,
        the panel on the far side of it."""
        if not c.turns:
            return c.edges, c.edges_rev
        n = len(c.pts)
        front, back = list(c.edges), list(c.edges_rev)
        for k, hinge, strip in c.turns:
            _e1, _tops, _bots, faces, no_top, no_bot = arcs[hinge][:6]
            fn = bn = faces[strip + 1]
            if c.hinge is None and (no_top or no_bot):
                far = faces[-1] if 2 * strip < len(faces) - 2 else faces[0]
                if no_top:
                    fn = far
                if no_bot:
                    bn = far
            front[k] = fn
            back[(n - 2 - k) % n] = (-bn[0], -bn[1], -bn[2])
        return tuple(front), tuple(back)

    def solid(self, fractions: Sequence[float],
              check: Optional[Check] = None, apart: float = 0.0
              ) -> List[Cell]:
        """The model with every fold ``fractions`` of its way round, as
        convex cells. With the ``check`` from ``lineup``, each stitch hole
        carries what the check found for it (see ``_decals``).

        A stack's sheets follow the main piece's own cells, each cut as it
        rides on a panel or round a bend (``sheet_cells``), tagged
        ``("panel", panel, sheet)`` and ``("bend", fold, sheet)``.
        ``apart`` lifts each sheet that many mm for every layer of its
        rank, away from the main piece, to show what lies on what: it is
        for the stack lying flat."""
        return [cell for _c, cell in self._posed(fractions, check, apart)]

    def _posed(self, fractions: Sequence[float],
               check: Optional[Check] = None, apart: float = 0.0,
               sheets: bool = True, memo: Optional[dict] = None,
               lazy: bool = False) -> List[Tuple[_Cell2, Cell]]:
        """``solid``, each cell of the model with the bit of the pattern it
        is made from: ``[(_Cell2, Cell)]``. With ``sheets`` False, the main
        piece's own cells only. Given a ``memo`` (a dict kept from one
        call to the next, with no ``check``), a cell lying where it lay
        at an earlier call -- on a panel lying as it lay, or round a bend
        as far round from such a panel -- is that call's cell again, not
        made anew. With ``lazy`` (and a ``memo``), a cell it does not hold
        is given as its corners only (``_Pending``), made in full when it
        is asked for -- for ``clash.pairs``, which asks only of cells
        whose boxes meet."""
        frames = self.frames(fractions)
        arcs = self._arcs(frames, fractions)
        rides: Dict[tuple, list] = {}
        if memo is not None:
            # (how each panel lies, and each bend, as a number the memo
            # keeps from call to call: a bend lies as the panel it goes
            # out of lies and as far round as its fold has gone)
            states = memo.setdefault("states", {})
            lies = [states.setdefault(fr, len(states)) for fr in frames]
            bent = [states.setdefault((lies[h.parent], fractions[h.fold]),
                                      len(states)) for h in self.tree]

        def ridden(lifts):
            if lifts is None:
                return arcs
            if lifts not in rides:
                rides[lifts] = [_ride(arc, *lifts) for arc in arcs]
            return rides[lifts]

        def make(c, lifts=None):
            if memo is None or check is not None:
                return self._cell(c, frames, ridden(lifts), check, lifts)
            # (a cell with an edge where it goes on round a bend is drawn
            # by how that bend lies too)
            key = (id(c), lies[c.panel] if c.hinge is None
                   else bent[c.hinge], lifts)
            if c.turns:
                key += tuple(bent[h] for _k, h, _s in c.turns)
            got = memo.get(key, memo)
            if got is memo:
                got = memo[key] = _Pending(self, c, frames, ridden(lifts),
                                           lifts, memo, key)
            if not lazy and isinstance(got, _Pending):
                got = got.full()
            return got

        cells = [(c, make(c)) for c in self.cells]
        if self.sheets and sheets:
            for c in self.sheet_cells:
                s = self.sheets[c.sheet - 1]
                off = s.side * apart * s.rank
                cells.append((c, make(c, (c.low + off, c.high + off))))
        return [(c, cell) for c, cell in cells if cell is not None]

    def _cell(self, c: _Cell2, frames: Sequence[Frame], arcs,
              check: Optional[Check] = None, lifts=None) -> Optional[Cell]:
        """One bit of the pattern as a cell of the model (None for a
        strip the bend has closed to nothing). A sheet's cell is given its
        ``lifts`` -- where its two faces are, out of the main piece's
        middle -- and the bends as they are at those (``_ride``)."""
        ht = 0.5 * self.thickness
        lo, hi = (-ht, ht) if lifts is None else lifts
        up_kind, down_kind = c.kinds
        blanks = getattr(check, "blanks", None) if self.sheets else None
        blank = blanks.get(c.index) if blanks else None
        if c.hinge is None:
            o, ex, ey, n = frames[c.panel]
            ax, ay, az = ex
            bx, by, bz = ey
            nx, ny, nz = n
            tx, ty, tz = o[0] + hi * nx, o[1] + hi * ny, o[2] + hi * nz
            ux, uy, uz = o[0] + lo * nx, o[1] + lo * ny, o[2] + lo * nz
            top = [(tx + x * ax + y * bx, ty + x * ay + y * by,
                    tz + x * az + y * bz) for x, y in c.pts]
            bot = [(ux + x * ax + y * bx, uy + x * ay + y * by,
                    uz + x * az + y * bz) for x, y in c.pts]

            def up(p):
                return (tx + p[0] * ax + p[1] * bx,
                        ty + p[0] * ay + p[1] * by,
                        tz + p[0] * az + p[1] * bz)

            def down(p):
                return (ux + p[0] * ax + p[1] * bx,
                        uy + p[0] * ay + p[1] * by,
                        uz + p[0] * az + p[1] * bz)

            pt, pb = top[0], bot[0]
            fe, be = self._edges(c, arcs)
            faces = [
                Face(tuple(top), (nx, ny, nz,
                                  nx * pt[0] + ny * pt[1] + nz * pt[2]),
                     up_kind, fe, _decals(c, up, check, blank), c.tag,
                     c.area,
                     _reframe(((tx, ty, tz), ex, ey), c.frame)),
                Face(tuple(bot[::-1]),
                     (-nx, -ny, -nz,
                      -(nx * pb[0] + ny * pb[1] + nz * pb[2])),
                     down_kind, be, _decals(c, down, check, blank), c.tag,
                     c.area, _reframe(((ux, uy, uz), ex, ey), c.frame))]
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
            return Cell(faces, c.tag)
        e1, tops, bots = arcs[c.hinge][:3]
        j = c.strip
        t0, t1, b0, b1 = tops[j], tops[j + 1], bots[j], bots[j + 1]

        def up(q):
            return _strip_point(q, t0, t1, e1)

        def down(q):
            return _strip_point(q, b0, b1, e1)

        top = [up(q) for q in c.place]
        bot = [down(q) for q in c.place]
        h = self.tree[c.hinge]
        w = self.used[h.fold]
        step = w / (2 * self._halves[c.hinge])

        def at(p):
            return _strip_place(p, h, w, step, j)

        faces = []
        fe, be = self._edges(c, arcs)
        # (a face between two rungs that are one line, a crease, has no
        # area: it is not asked for a plane)
        pl = None if t0 == t1 else plane_of(top)
        if pl is not None:
            faces.append(Face(tuple(top), pl, up_kind, fe,
                              _decals(c, lambda p: up(at(p)), check,
                                      blank),
                              c.tag, c.area,
                              _reframe(_strip_uv(h, w, step, j, t0, t1, e1),
                                       c.frame)))
        rb = bot[::-1]
        pl = None if b0 == b1 else plane_of(rb)
        if pl is not None:
            faces.append(Face(tuple(rb), pl, down_kind, be,
                              _decals(c, lambda p: down(at(p)), check,
                                      blank),
                              c.tag, c.area,
                              _reframe(_strip_uv(h, w, step, j, b0, b1, e1),
                                       c.frame)))
        m = len(top)
        for k in range(m):
            jj = k + 1 if k + 1 < m else 0
            _wx, _wy, size, kind, edges = c.sides[k]
            faces += _side_faces(bot[k], bot[jj], top[jj], top[k], kind,
                                 edges, c.tag, size)
        return Cell(faces, c.tag) if faces else None

    # -- marks for the view ---------------------------------------------------

    def fold_marks(self, fractions: Sequence[float], apart: float = 0.0):
        """Each fold line as it lies on the model: ``{fold: [(p, q, p', q',
        n)]}`` with ``p``-``q`` on the front face of the leather, ``p'``-
        ``q'`` on the back and ``n`` the normal out of the front there, one
        entry for every stretch of the fold (the middle of its bend).

        On a stack the line is drawn on the outermost leather over it, so
        that a lining does not bury it: a stretch comes in as many lengths
        as there are changes of what lies over it, each with one thing
        more at its end, ``(piece, piece)``, the pieces its line on the
        front and its line on the back are drawn on (``sheets``)."""
        if self.sheets:
            from . import sheets as sh
            return sh.fold_marks(self, fractions, apart)
        frames = self.frames(fractions)
        ht = 0.5 * self.thickness
        out: Dict[int, list] = {}
        for h in self.hinges:
            if h.tree:
                continue
            # a stretch cut free by folds that cross: along its panel's edge
            a, e, _ln = self.lines[h.fold]
            fr = frames[h.left]
            ends = [place(fr, (a[0] + k * e[0], a[1] + k * e[1]), lift)
                    for lift in (ht, -ht) for k in (h.lo, h.hi)]
            out.setdefault(h.fold, []).append(tuple(ends) + (fr[3],))
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

    def panel_marks(self, fractions: Sequence[float], apart: float = 0.0):
        """For each panel: a point on its front face, one on its back, and
        the normal out of the front -- where to write its number. On a
        stack, on the outermost leather there, with one thing more at the
        end, ``(piece, piece)``, as ``fold_marks`` has it."""
        if self.sheets:
            from . import sheets as sh
            return sh.panel_marks(self, fractions, apart)
        frames = self.frames(fractions)
        ht = 0.5 * self.thickness
        return [(place(frames[p.index], p.spot, ht),
                 place(frames[p.index], p.spot, -ht), frames[p.index][3])
                for p in self.panels]

    def extent(self) -> Tuple[P3, float]:
        """Centre and radius of a ball round the piece lying flat (it holds
        the piece at every stage of the folding, near enough)."""
        if self.sheets:
            lo, hi = self.bounds3([0.0] * len(self.folds))
            return (tuple(0.5 * (a + b) for a, b in zip(lo, hi)),
                    0.5 * math.dist(lo, hi))
        xs = [p[0] for panel in self.panels for p in panel.pts]
        ys = [p[1] for panel in self.panels for p in panel.pts]
        if not xs:
            return (0.0, 0.0, 0.0), 1.0
        cx, cy = 0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys))
        return (cx, cy, 0.0), 0.5 * math.hypot(max(xs) - min(xs),
                                               max(ys) - min(ys)) \
            + self.thickness

    def bounds3(self, fractions: Sequence[float], apart: float = 0.0):
        """The box round the model as it stands: ``(low corner, high)``.
        On a stack, round every cell of it, the sheets lifted ``apart``
        (see ``solid``)."""
        if self.sheets:
            pts = [v for c in self.solid(fractions, apart=apart)
                   for v in c.verts]
            return (tuple(min(p[k] for p in pts) for k in range(3)),
                    tuple(max(p[k] for p in pts) for k in range(3)))
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

    def clear_marks(self, fractions: Sequence[float]):
        """Where each fold's bend begins, either side of its line: the
        lines to keep the edges of what is laid on the piece clear of.
        ``{fold: [(p, q, p', q', n, (a, b))]}``, one entry for each side of
        every stretch, ``p``-``q`` on the front face of the main piece and
        ``p'``-``q'`` on its back, ``n`` the normal out of its front and
        ``a``-``b`` the line on the pattern. None for a fold with no
        bend."""
        frames = self.frames(fractions)
        ht = 0.5 * self.thickness
        out: Dict[int, list] = {}
        for h in self.tree:
            half = 0.5 * self.used[h.fold]
            if half <= 0.0:
                continue
            c2 = (-h.e[1], h.e[0])
            for panel, way in ((h.parent, -1.0), (h.child, 1.0)):
                a, b = ((h.a[0] + k * h.e[0] + way * half * c2[0],
                         h.a[1] + k * h.e[1] + way * half * c2[1])
                        for k in (h.k0, h.k1))
                fr = frames[panel]
                out.setdefault(h.fold, []).append(
                    (place(fr, a, ht), place(fr, b, ht), place(fr, a, -ht),
                     place(fr, b, -ht), fr[3], (a, b)))
        return out

    # -- do the stitch holes meet? --------------------------------------------

    def lineup(self, tol: float = 0.75, near: float = 4.0) -> Check:
        """Check the folded piece: where two panels come to lie on each
        other and both have stitch holes there, do the holes meet?

        A hole meets the nearest hole of the other panel if it comes down
        within ``tol`` mm of it; it misses it if it comes down within
        ``near`` mm but not on it -- those two were meant to meet and
        don't; and with nothing that near it has no hole to meet there,
        which need not be wrong (a flap lies over stitching it isn't sewn
        to). Each pair of panels gets a ``Lineup`` with the count of each.

        What a hole itself is given goes by the whole stack it is in --
        the holes that meet it, the holes that meet those, and so on
        through every layer -- since that is what a needle has to go
        through: ``OFF`` if any hole of the stack misses another, ``MEETS``
        if none does, ``LONE`` for a hole that meets nothing and misses
        nothing. Returns a ``Check``.

        A stack is checked through its layers instead
        (``sheets.stack_check``): a ``sheets.LaidCheck``."""
        if self.sheets:
            from . import sheets as sh
            return sh.stack_check(self, tol, near)
        t = self.thickness
        frames = self.frames([1.0] * len(self.folds))
        check = Check()
        seen = set()                           # holes that lie on a panel
        link: Dict[int, int] = {}              # holes that meet, in stacks
        misses: List[Tuple[int, int]] = []     # (hole, the one it misses)

        def stack(k):
            while link.setdefault(k, k) != k:
                link[k] = link[link[k]]
                k = link[k]
            return k

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
                if not over_i or not over_j:
                    continue                   # not sewn together here
                off, lone, worst, apart = 0, 0, 0.0, 0.0
                for mine, theirs in ((over_i, over_j), (over_j, over_i)):
                    grid: Dict[tuple, list] = {}       # their holes, by square
                    for k2, q2 in theirs:
                        grid.setdefault((math.floor(q2[0] / near),
                                         math.floor(q2[1] / near)),
                                        []).append((k2, q2))
                    for k, p in mine:
                        seen.add(k)
                        gx = math.floor(p[0] / near)
                        gy = math.floor(p[1] / near)
                        d, k2 = min(
                            ((math.hypot(p[0] - q3[0], p[1] - q3[1]), k3)
                             for ix in (gx - 1, gx, gx + 1)
                             for iy in (gy - 1, gy, gy + 1)
                             for k3, q3 in grid.get((ix, iy), ())),
                            default=(2.0 * near, -1))
                        if d <= tol:
                            apart = max(apart, d)
                            link[stack(k)] = stack(k2)
                        elif d <= near:
                            off += 1
                            worst = max(worst, d)
                            misses.append((k, k2))
                        else:
                            lone += 1
                check.findings.append(Lineup(
                    pi.number, pj.number, len(over_i) + len(over_j), off,
                    worst, lone, apart))
        # each hole by the stack it is in
        stacks: Dict[int, List[int]] = {}
        for k in seen:
            stacks.setdefault(stack(k), []).append(k)
        out_of_line = {stack(k) for k, _k2 in misses}
        for top, members in stacks.items():
            what = OFF if top in out_of_line \
                else MEETS if len(members) > 1 else LONE
            for k in members:
                check.state[k] = what
        # ... and for every hole of a stack with a miss in it, where the
        # hole that was missed comes down on that hole's own panel
        spots = set()
        for k, k2 in misses:
            at = self.hole_panel[k2]
            x2, y2, dia = self.holes[k2]
            p3 = place(frames[at], (x2, y2))
            for m in stacks[stack(k)]:
                panel = self.hole_panel[m]
                if panel == at:
                    continue
                o, ex, ey, _n = frames[panel]
                v = (p3[0] - o[0], p3[1] - o[1], p3[2] - o[2])
                x, y = _dot(v, ex), _dot(v, ey)
                check.lands.setdefault(m, []).append((x, y, dia))
                spot = (panel, round(x, 3), round(y, 3))
                if spot in spots:
                    continue
                spots.add(spot)
                for c in self._by_panel.get(panel, ()):
                    bx = c.box
                    if (bx[0] - 1e-6 <= x <= bx[2] + 1e-6
                            and bx[1] - 1e-6 <= y <= bx[3] + 1e-6
                            and pc.inside_convex((x, y), c.pts, 1e-6)):
                        check.rings.setdefault(c.index, []).append(
                            (x, y, dia))
                        break
        return check


# -- helpers for the cells ----------------------------------------------------

def _reach(front: Sequence[P3], back: Sequence[P3], axis: P3, e1: P3,
           cc: P3, n: P3, spans: Sequence[Tuple[float, float]], r: float,
           thick: float, depth: float
           ) -> Optional[Tuple[float, bool]]:
    """How far a cell -- ``front`` and ``back`` the rings of corners on
    its two faces -- reaches into a closed bend: mm past where the round
    of the bend lets it lie (less than nothing: short of that); and
    whether any of it is inside the fold -- between the two panels where
    they lie over each other, no further on than the round of the bend,
    or in the leather of the bend itself. None for a cell that isn't
    between the planes of the fold's two panels.

    ``axis`` is a point of the line the bend goes round, ``e1`` the way
    the hinge runs, ``cc`` across it (from the line the bend starts on,
    on into the bend) and ``n`` out of the panel there; the hinge is the
    stretches ``spans`` (each ``(from, to)`` along ``e1``), the bend has
    inside radius ``r`` in leather ``thick`` mm thick, and its two
    panels lie over each other for ``depth`` mm back from the line it
    starts on. What is measured is the part of the cell along the hinge:
    its corners there, and where its edges cross the ends of a stretch."""
    pts = []
    for x, y, z in (*front, *back):
        d = (x - axis[0], y - axis[1], z - axis[2])
        pts.append((_dot(d, e1), _dot(d, cc), _dot(d, n)))
    off = [p[2] for p in pts]
    if min(off) >= r - 1e-6 or max(off) <= 1e-6 - r:
        return None                # in the sheet of one of the two panels
    lim = r + thick - 1e-6         # between the outer faces of the panels
    skin = (r + thick - _TOUCH) ** 2        # (less far in only touches)

    def along(p, q, k0, k1):       # the part of an edge along the hinge
        dk = q[0] - p[0]
        if abs(dk) > 1e-12:
            ta, tb = (k0 - p[0]) / dk, (k1 - p[0]) / dk
            return max(0.0, min(ta, tb)), min(1.0, max(ta, tb))
        return (0.0, 1.0) if k0 <= p[0] <= k1 else (1.0, 0.0)

    most = None
    near, far = math.inf, -math.inf        # the least and the most across
    inside = False
    m = len(front)
    for (k0, k1), i in ((span, i) for span in spans for i in range(m)):
        j = i + 1 if i + 1 < m else 0
        for p, q in ((pts[i], pts[j]), (pts[m + i], pts[m + j]),
                     (pts[i], pts[m + i])):
            t0, t1 = along(p, q, k0, k1)
            if t0 > t1:
                continue
            for tt in (t0, t1):
                v = p[2] + (q[2] - p[2]) * tt
                if abs(v) >= lim:
                    continue
                u = p[1] + (q[1] - p[1]) * tt
                mm = u - math.sqrt(max(0.0, r * r - v * v))
                if most is None or mm > most:
                    most = mm
                near, far = min(near, u), max(far, u)
        # ... and the leather from face to face, where this edge of the
        # front lies along the hinge: is any of it, past the line the bend
        # starts on, within the outside of the bend? (Its two ends, and
        # the nearest of it to the line the bend goes round.)
        t0, t1 = along(pts[i], pts[j], k0, k1)
        if inside or t0 > t1:
            continue
        for tt in (t0, t1):
            (fu, fv), (bu, bv) = (
                (p[1] + (q[1] - p[1]) * tt, p[2] + (q[2] - p[2]) * tt)
                for p, q in ((pts[i], pts[j]), (pts[m + i], pts[m + j])))
            du, dv = bu - fu, bv - fv
            dd = du * du + dv * dv
            s = min(1.0, max(0.0, -(fu * du + fv * dv) / dd)) if dd else 0.0
            for u, v in ((fu, fv), (bu, bv), (fu + s * du, fv + s * dv)):
                if u > 0.0 and u * u + v * v < skin:
                    inside = True
    if most is None:
        return None
    return most, inside or (near <= r and far >= -depth)


def _listed(numbers: Sequence[int]) -> str:
    """1 and 3; 1, 3 and 5."""
    words = [str(k) for k in numbers]
    return words[0] if len(words) == 1 \
        else ", ".join(words[:-1]) + " and " + words[-1]


def _rungs(arc, lift: float) -> List[P3]:
    """The lines the strips of a bend meet at (as ``_arcs`` gives it),
    ``lift`` mm out of the middle of the main piece's leather: where a
    face of what rides on the bend lies, concentric with the main
    piece's own at every moment. A face on the line the bend goes round
    is laid on that line exactly."""
    mids, nns, axis, rho, s = arc[6:11]
    if axis is not None and abs(rho - s * lift) < _CREASE:
        return [axis] * len(mids)
    return [(m[0] + lift * v[0], m[1] + lift * v[1], m[2] + lift * v[2])
            for m, v in zip(mids, nns)]


def _ride(arc, low: float, high: float):
    """A bend as ``_arcs`` gives it, as it is for what rides on it with
    its two faces ``low`` and ``high`` mm out of the main piece's middle:
    the same strips at the same angles, its faces moved along their
    normals."""
    if arc is None:
        return None
    tops, bots = _rungs(arc, high), _rungs(arc, low)
    return (arc[0], tops, bots, arc[3],
            math.dist(tops[0], tops[1]) < _SHARP,
            math.dist(bots[0], bots[1]) < _SHARP) + tuple(arc[6:])


def _reframe(uv, frame):
    """Where the pattern lies on a face (``(o, u, v)``, as ``bsp.Face``
    has it) made to go by a sheet's own grain instead: ``frame`` is the
    point and the two ways its grain is laid from, on the pattern. As it
    is for no frame."""
    if frame is None or uv is None:
        return uv
    o, u, v = uv
    (ox, oy), (ux, uy), (vx, vy) = frame
    return ((o[0] + ox * u[0] + oy * v[0], o[1] + ox * u[1] + oy * v[1],
             o[2] + ox * u[2] + oy * v[2]),
            (ux * u[0] + uy * v[0], ux * u[1] + uy * v[1],
             ux * u[2] + uy * v[2]),
            (vx * u[0] + vy * v[0], vx * u[1] + vy * v[1],
             vx * u[2] + vy * v[2]))


def _strip_point(q: Tuple[float, float], p0: P3, p1: P3, e1: P3) -> P3:
    """Where a point of a bend's strip lies on one face of the leather:
    ``q`` is ``(how far along the fold, how far across the strip from 0
    to 1)`` as ``_strip_place`` gives it, and the face runs from the line
    through ``p0`` to the one through ``p1``, both along ``e1``."""
    al, tau = q
    return (p0[0] + (p1[0] - p0[0]) * tau + al * e1[0],
            p0[1] + (p1[1] - p0[1]) * tau + al * e1[1],
            p0[2] + (p1[2] - p0[2]) * tau + al * e1[2])


def _strip_place(p: Pt, h: Hinge, w: float, step: float, j: int
                 ) -> Tuple[float, float]:
    """A pattern point of strip ``j`` of a bend as (how far along the fold
    line, how far across the strip from 0 to 1)."""
    dx, dy = p[0] - h.a[0], p[1] - h.a[1]
    along = dx * h.e[0] + dy * h.e[1]
    u = dy * h.e[0] - dx * h.e[1]
    tau = (u + 0.5 * w - j * step) / step
    return along, 0.0 if tau < 0.0 else 1.0 if tau > 1.0 else tau


def _strip_uv(h: Hinge, w: float, step: float, j: int, p0: P3, p1: P3,
              e1: P3):
    """Where the pattern lies on one face of strip ``j`` of a bend: ``(o,
    u, v)`` with ``o + x * u + y * v`` the point of the face (which runs
    from the line through ``p0`` to the one through ``p1``, both along
    ``e1``) at ``(x, y)`` mm of the pattern."""
    ex, ey = h.e
    cx, cy = -ey, ex                           # across the fold
    ax, ay = h.a
    d = ((p1[0] - p0[0]) / step, (p1[1] - p0[1]) / step,
         (p1[2] - p0[2]) / step)               # a mm across, on this face
    k = 0.5 * w - j * step - ax * cx - ay * cy
    m = ax * ex + ay * ey
    return ((p0[0] + d[0] * k - e1[0] * m, p0[1] + d[1] * k - e1[1] * m,
             p0[2] + d[2] * k - e1[2] * m),
            (d[0] * cx + e1[0] * ex, d[1] * cx + e1[1] * ex,
             d[2] * cx + e1[2] * ex),
            (d[0] * cy + e1[0] * ey, d[1] * cy + e1[1] * ey,
             d[2] * cy + e1[2] * ey))


def _cut_zone(flat: List[pc.Poly], h: Hinge, side: float, half: float,
              m: int, ends: Optional[Tuple[bool, bool]] = None):
    """Take the half of a bend that lies in one panel out of that panel's
    flat polygons: the band ``half`` mm deep along the hinge, in ``m``
    strips. Returns ``(what stays flat, [(strip number, polygon)])``.

    The main piece's own polygons are known by their edge along the
    fold. A sheet riding on the panel (``ends`` given) is taken by where
    its leather is: whatever of it lies in the band bends, whether it
    reaches the fold line or not -- but nothing of it beyond the line,
    which is the other panel's. ``ends`` says, for each end of the hinge,
    what it does past that end: None, it stays flat there; else the bend
    goes on there but over the convex parts it gives, the main piece's
    own leather lying flat past the end (``sheets.ends``), where it lies
    flat with that."""
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
    if ends is not None:
        return _cut_rider(flat, h, side, half, m, ends)
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
    return keep, _in_strips(zone, h, side, half, m)


def _cut_rider(flat: List[pc.Poly], h: Hinge, side: float, half: float,
               m: int, ends):
    """``_cut_zone`` for a sheet riding on the panel: see there."""
    a, e = h.a, h.e
    c2 = (-e[1], e[0])

    def along(p):
        return (p[0] - a[0]) * e[0] + (p[1] - a[1]) * e[1]

    def depth(p):
        return side * ((p[1] - a[1]) * e[0] - (p[0] - a[0]) * e[1])

    keep: List[pc.Poly] = []
    zone: List[pc.Poly] = []
    line = (a[0] + side * half * c2[0], a[1] + side * half * c2[1])
    for pts, flags in flat:
        if min(depth(p) for p in pts) >= half - 1e-9:
            keep.append((pts, flags))
            continue
        for piece in pc.cut(pts, flags, line, e, None, pc.INNER):
            (zone if depth(pc.centroid(piece[0])) < half
             else keep).append(piece)
    for flat_there, (k_end, sign) in zip(ends, ((h.k0, -1.0), (h.k1, 1.0))):
        if flat_there is not None and not flat_there:
            continue                   # (it goes on, and nothing stops it)
        trimmed: List[pc.Poly] = []
        origin = (a[0] + k_end * e[0], a[1] + k_end * e[1])
        for pts, flags in zone:
            if max(sign * (along(p) - k_end) for p in pts) <= 1e-6:
                trimmed.append((pts, flags))
                continue
            for piece in pc.cut(pts, flags, origin, c2, None, pc.INNER):
                if sign * (along(pc.centroid(piece[0])) - k_end) <= 0.0:
                    trimmed.append(piece)
                elif flat_there is None:
                    keep.append(piece)
                else:
                    # past the end it bends, but where it lies on leather
                    # of the main piece that goes on flat there
                    for bit in _parted_by(piece, flat_there):
                        c = _inner_point(bit)
                        (keep if any(pc.inside_convex(c, q, 1e-9)
                                     for q in flat_there)
                         else trimmed).append(bit)
        zone = trimmed
    # nothing on the far side of the fold line bends on this side
    near: List[pc.Poly] = []
    for pts, flags in zone:
        if min(depth(p) for p in pts) >= -1e-9:
            near.append((pts, flags))
            continue
        for piece in pc.cut(pts, flags, a, e, None, pc.INNER):
            (near if depth(pc.centroid(piece[0])) > 0.0
             else keep).append(piece)
    return keep, _in_strips(near, h, side, half, m)


def _parted_by(poly: pc.Poly, parts) -> List[pc.Poly]:
    """A polygon cut along every edge of some convex polygons that runs
    through it, so that each piece lies inside one of them or outside
    them all."""
    bits = [poly]
    box = pc.bounds(poly[0])
    for q in parts:
        b = pc.bounds(q)
        if not (box[0] < b[2] and b[0] < box[2] and box[1] < b[3]
                and b[1] < box[3]):
            continue
        n = len(q)
        for i in range(n):
            p, r = q[i], q[(i + 1) % n]
            ln = math.dist(p, r)
            if ln < 1e-9:
                continue
            e = ((r[0] - p[0]) / ln, (r[1] - p[1]) / ln)

            def want(lo, hi, ln=ln):
                return min(hi, ln) - max(lo, 0.0) > 1e-6

            bits = [bit for P, F in bits
                    for bit in pc.cut(P, F, p, e, want, pc.INNER)]
    return bits


def _inner_point(poly: pc.Poly) -> Pt:
    """A point inside a polygon: the middle of its biggest convex part."""
    parts = pc.convex_parts(*poly) or [poly]
    return pc.centroid(max((p for p, _f in parts),
                           key=lambda p: abs(pc.area(p))))


def _in_strips(zone: List[pc.Poly], h: Hinge, side: float, half: float,
               m: int):
    """What lies in the half of a bend on one side of the fold, cut in
    ``m`` strips square to the way it bends: ``[(strip number, polygon)]``,
    numbered across the whole bend from the parent's side."""
    a, e = h.a, h.e
    c2 = (-e[1], e[0])

    def depth(p):
        return side * ((p[1] - a[1]) * e[0] - (p[0] - a[0]) * e[1])

    def offset(dist):
        return (a[0] + side * dist * c2[0], a[1] + side * dist * c2[1])

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
    return strips


def _strip_parts(pts: List[Pt], flags: List[int], h: Hinge,
                 rider: bool = False) -> List[pc.Poly]:
    """One strip of a bend in convex parts. Where its outline turns in
    -- at the neck of a flap on a wider panel, say -- it is first parted
    square to the fold: a seam running aslant across a strip is twisted
    by the bend, and the parts either side of it would not meet along
    it.

    A ``rider``'s strip (a piece laid on the main piece, which may lie
    across the bend at any angle) is first cut along the fold through
    every corner of it that lies between the strip's two edges, before
    anything else parts it: a part with a corner between those two
    lines has a twisted side once bent, and two such parts side by side
    do not fit together. The cuts go through the corners themselves, so
    that nothing is moved by rounding."""
    if rider:
        return _rider_parts(pts, flags, h)
    if pc.is_convex(pts):
        return [(pts, flags)]
    c2 = (-h.e[1], h.e[0])
    n = len(pts)
    turns_in = []
    for k in range(n):
        (ax, ay), (bx, by), (cx, cy) = pts[k - 1], pts[k], pts[(k + 1) % n]
        if (bx - ax) * (cy - by) - (by - ay) * (cx - bx) < -1e-9:
            turns_in.append(pts[k])
    polys = [(pts, flags)]
    for corner in turns_in:
        polys = [piece for P, F in polys
                 for piece in pc.cut(P, F, corner, c2, None, pc.INNER)]
    return [part for P, F in polys for part in pc.convex_parts(P, F)]


def _sides(cells: Sequence[_Cell2], thick: float, sharp_set) -> None:
    """Fill in what each edge of some cells is: ``edges`` and
    ``edges_rev`` (an edge of the leather, or a seam inside it, on the
    front face and on the back) and ``sides`` (see ``_Cell2``), the walls
    ``thick`` mm high. A wall shows a line down it at a corner that is in
    ``sharp_set``."""
    for c in cells:
        n = len(c.pts)
        c.edges = tuple(f == pc.OUTLINE for f in c.flags)
        c.edges_rev = tuple(c.edges[(n - 2 - k) % n] for k in range(n))
        sharp = [(round(p[0], 6), round(p[1], 6)) in sharp_set
                 for p in c.pts]
        c.sides = []
        for k in range(n):
            j = (k + 1) % n
            p, q = c.pts[k], c.pts[j]
            dx, dy = q[0] - p[0], q[1] - p[1]
            ln = math.hypot(dx, dy) or 1e-12
            wall = c.flags[k] == pc.OUTLINE
            c.sides.append((dy / ln, -dx / ln, ln * thick,
                            WALL if wall else INNER,
                            (True, sharp[j], True, sharp[k]) if wall
                            else None))


def _turns(cells: Sequence[_Cell2]) -> None:
    """Where the leather goes on round a bend from each of some cells (of
    one piece): the edges along the lines the strips of a bend meet at,
    each with the strip across it (-1 and the number of strips stand for
    the flat panel before the first and after the last). The view wants
    these: such an edge is the rim of what shows when the next strip has
    turned out of sight."""
    bends: Dict[int, List[_Cell2]] = {}
    for c in cells:
        if c.hinge is not None:
            bends.setdefault(c.panel, []).append(c)
    for c in cells:
        n = len(c.pts)
        if c.hinge is not None:
            for k in range(n):
                if c.flags[k] == pc.OUTLINE:
                    continue
                t0, t1 = c.place[k][1], c.place[(k + 1) % n][1]
                if t0 <= 1e-9 and t1 <= 1e-9:
                    c.turns.append((k, c.hinge, c.strip - 1))
                elif t0 >= 1.0 - 1e-9 and t1 >= 1.0 - 1e-9:
                    c.turns.append((k, c.hinge, c.strip + 1))
            continue
        near = bends.get(c.panel)
        if not near:
            continue
        for k in range(n):
            if c.flags[k] == pc.OUTLINE:
                continue
            p, q = c.pts[k], c.pts[(k + 1) % n]
            wx, wy = c.sides[k][0], c.sides[k][1]
            x = 0.5 * (p[0] + q[0]) + 2e-4 * wx    # just across the edge
            y = 0.5 * (p[1] + q[1]) + 2e-4 * wy
            for s in near:
                b = s.box
                if (b[0] <= x <= b[2] and b[1] <= y <= b[3]
                        and pc.inside_convex((x, y), s.pts, 0.0)):
                    c.turns.append((k, s.hinge, s.strip))
                    break


def _rider_parts(pts: List[Pt], flags: List[int], h: Hinge
                 ) -> List[pc.Poly]:
    """``_strip_parts`` for a rider's strip: see there."""
    def across(p):
        return (p[1] - h.a[1]) * h.e[0] - (p[0] - h.a[0]) * h.e[1]

    us = [across(p) for p in pts]
    lo, hi = min(us), max(us)
    cuts: List[Pt] = []
    seen: List[float] = []
    for p, u in zip(pts, us):
        if lo + 1e-6 < u < hi - 1e-6 and all(abs(u - v) > 1e-6
                                             for v in seen):
            seen.append(u)
            cuts.append(p)
    bits = [(list(pts), list(flags))]
    for p in cuts:
        bits = [piece for q, f in bits
                for piece in pc.cut(q, f, p, h.e, None, pc.INNER)]
    out = []
    for q, f in bits:
        n = len(q)
        # (convex to within a millionth of a mm at each corner: no more
        # than rounding is left after the cuts)
        if all((q[i][0] - q[i - 1][0]) * (q[(i + 1) % n][1] - q[i][1])
               - (q[i][1] - q[i - 1][1]) * (q[(i + 1) % n][0] - q[i][0])
               >= -1e-6 * (math.dist(q[i - 1], q[i]) or 1.0)
               for i in range(n)):
            out.append((q, f))
        else:
            out.extend(pc.convex_parts(q, f))
    return out


def _decals(c: _Cell2, to3d, check: Optional[Check], blank=None):
    """The marks on one face of a cell, placed by ``to3d``. Stitch holes
    are ``DOTS`` with the style ``(diameter, state)``: 0 for a hole the
    check has nothing about, else ``MEETS``, ``OFF`` or ``LONE`` -- and
    ``LANDS`` for the spots where the holes that its own missed come down.
    ``blank``: on a stack, the spots where holes of other pieces come
    through this one, which has none there (``_BLANK``)."""
    rings = check.rings.get(c.index) if check is not None else None
    if not (c.dots or c.polys or c.lines or rings or blank):
        return None
    out = []
    for ring in c.polys:
        out.append((POLY, [to3d(p) for p in ring], "cut"))
    for kind, p, q in c.lines:
        out.append((LINE, (to3d(p), to3d(q)), kind))
    groups: Dict[tuple, list] = {}
    for x, y, dia in rings or ():              # first, so under the holes
        groups.setdefault((dia, LANDS), []).append(to3d((x, y)))
    for x, y, dia in blank or ():
        groups.setdefault((dia, _BLANK), []).append(to3d((x, y)))
    if c.dots:
        state = check.state if check is not None else {}
        for x, y, dia, k in c.dots:
            groups.setdefault((dia, state.get(k, 0)), []).append(to3d((x, y)))
    for style, pts in groups.items():
        out.append((DOTS, pts, style))
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


def model_outline(outline: Sequence[Pt], smooth: float = 0.03,
                  most: int = 260) -> List[Pt]:
    """An outline made ready for the model: counter-clockwise, no doubled
    points, and a flattened curve thinned to within ``smooth`` mm -- or a
    little further for an outline so busy (a scalloped edge, a traced one)
    that it would still have more than ``most`` points, up to 0.5 mm."""
    ring, _f = pc.tidy([(float(p[0]), float(p[1])) for p in outline],
                       [0] * len(outline), 1e-6)
    ring = pc.ccw(ring)
    out = pc.simplify(ring, smooth)
    tol = max(smooth, 0.03)
    while len(out) > most and tol < 0.5:
        tol *= 1.6
        out = pc.simplify(ring, min(tol, 0.5))
    return out


def fold_piece(outline: Sequence[Pt], folds: Sequence[FoldLine], *,
               thickness: float = 2.0, radius: float = 0.0,
               base: Optional[int] = None, holes=(), cutouts=(),
               marks=(), detail: float = 12.0, smooth: float = 0.03,
               sheets=()) -> Layout:
    """Lay a piece out for folding along its fold lines.

    ``outline`` is the piece (pattern mm, either way round); ``holes`` its
    stitch holes as ``(x, y, diameter)``; ``cutouts`` the rings cut out of
    it; ``marks`` lines drawn on it, as ``(kind, points)`` with kind
    ``"cut"`` (a slit) or ``"line"`` (scored or engraved). ``thickness``
    and ``radius`` are the leather's thickness and the
    inside radius of any fold with none of its own; ``base`` the panel
    that stays still (the biggest if not given). ``detail`` is the degrees
    of bend to a strip, ``smooth`` how closely (mm) a curved outline is
    followed.

    A fold that closes flat on leather that reaches into its bend is
    moved along the piece to where it can form -- the piece is cut again
    along the moved line, so the model, the marks and the hole check all
    go by where the leather really folds -- and the layout says how far
    (``Layout.shift``), and what to shorten in the drawing to have it
    fold on its line (``Layout.shift_trim``). The panels keep the numbers
    they have with every fold on its line.

    ``sheets`` are the other pieces of a stack built on this one (see
    ``leathercad.sheets``): they are laid on it, and fold with it. Every
    layout made on the way takes them, so that they are seated again
    along a fold that has moved. They are laid in place: what
    ``sheets.settle`` works out (heights, rank, what each rests on) is
    set on the caller's own ``Sheet`` objects, and the layout keeps
    those same objects -- a sheet changed afterwards is no longer the one
    the layout was made from, and is to be laid out again. On a stack a
    fold that moves is judged as for a piece alone (``Layout._landing``)
    and as a stack (``sheets.judge``), and what to shorten is given only
    where it comes right with the pieces on it as they are."""
    ring = model_outline(outline, smooth)
    regions, lines = split_piece(ring, folds)
    kw = dict(thickness=thickness, radius=radius, detail=detail,
              sharp=sharp_corners(ring), bare=True)
    if sheets:
        from . import sheets as sh
        sheets = list(sheets)
        kw.update(sheets=sheets, outline=ring,
                  settled=sh.settle(outline, thickness, sheets))
    n = len(folds)
    first = lay = Layout(regions, folds, lines, base=base,
                         moved=[0.0] * n, **kw)
    # (from here on the regions come in the order of its panels)
    cut = ([regions[i] for i in first._order], lines)
    kw.update(base=first.base, numbered=True)
    held: set = set()
    fixed: set = set()
    goes = [0] * n
    # (the way each fold put back where it was had set out to move: 1.0 to
    # its left, -1.0 to its right)
    wants: Dict[int, float] = {}
    # One fold at a time, in the order they are made. A fold moves once,
    # as a rule: the leather inside it comes clear by just as much as the
    # fold has moved. It may have a little further to go -- it bends at
    # another radius where it now is -- but after four moves it stays;
    # so it does once it is stopped for want of room, and when it has
    # been put back because its bend grew too wide for where it stopped.
    # That is five rounds a fold at the most. A fold that has moved keeps
    # the radius it moved at, at the least: it goes round the same
    # leather wherever it forms.
    def settle(lay, cut):
        # (each fold moved until it can form, as below: the layout kept,
        # and the cut of the piece it was made from)
        for _round in range(5 * n):
            if lay.to_move is None:
                break
            fi, step, stay = lay.to_move
            back = step * lay.moved[fi] < 0.0
            goes[fi] += 1
            moved, new = lay.moved, None
            for _try in range(4):
                trial = list(lay.moved)
                trial[fi] += step
                new = _recut(ring, first, lay, fi, trial)
                if new is not None:
                    moved = trial
                    break
                stay = True            # not that far: half, and then no more
                step *= 0.5
            if back:
                fixed.add(fi)
            elif stay or goes[fi] >= 4:
                held.add(fi)
            was = lay
            if new is not None:
                lay = Layout(new[0], folds, new[1], moved=moved, held=held,
                             fixed=fixed, **kw,
                             least=[r if d else 0.0
                                    for r, d in zip(was.radius, moved)])
                if sheets:
                    # (a radius kept from where a fold was keeps its reason)
                    lay.why = [w or (o if d and r <= q + 1e-9 else None)
                               for w, o, d, r, q in zip(
                                   lay.why, was.why, moved, lay.radius,
                                   was.radius)]
                # A move that leaves the fold wanting to go as far again has
                # not brought anything clear (what is in its way moves with
                # it): back where it was, then, and no more of that
                # On a stack the pieces riding on the fold's own panels come
                # into its bend as it moves and take some of its round, so a
                # move brings less clear than it is long: a move that brings
                # anything clear is kept, and the fold goes on from there
                far = lay.to_move
                enough = abs(step) - _REACH if sheets else 0.5 * abs(step)
                if (far is None or back or far[0] != fi or far[1] * step <= 0.0
                        or abs(far[1]) <= enough):
                    cut = new
                    continue
                held.add(fi)
                wants[fi] = 1.0 if step > 0.0 else -1.0
            lay = Layout(cut[0], folds, cut[1], moved=was.moved, held=held,
                         fixed=fixed, least=was._least, **kw)
        return lay, cut

    lay, cut = settle(lay, cut)
    # On a stack a fold stopped for want of room, with leather left in its
    # bend, may yet close further on: past the pile, the pile no longer
    # rides in its bend, and it bends tighter. It is tried further on, a
    # millimetre at a time up to where the piece no longer parts so, the
    # pile measured anew at each, and kept at the first place it closes
    # clean (found to a hair between that and the last that does not).
    # So is one put back on its line because its first move brought
    # nothing clear: what moved with it may yet be left behind further on.
    # A fold that closes clean nowhere is left stuck, and the next is tried.
    tried: set = set()
    for _round in range(n if sheets else 0):
        fi = next((k for k in sorted(range(n), key=lambda k: (lay.steps[k],
                                                             k))
                   if lay.stuck[k] and k not in tried
                   and (lay.moved[k] or k in wants)), None)
        if fi is None:
            break
        got = _further(ring, first, lay, fi, held, fixed, kw, folds,
                       wants.get(fi, 0.0))
        if got is None:
            tried.add(fi)
            continue
        lay, cut = settle(*got)

    if any(lay.shift):
        lay._landing(first)
        lay._drawn = first             # (for the judge of a stack)
    lay._advise(first)
    lay._dress(holes, cutouts, marks)
    return lay


def _further(ring, first: Layout, lay: Layout, fi: int, held, fixed, kw,
             folds, way: float):
    """Where fold ``fi`` of a stack, stuck in ``lay`` for want of room or
    put back on its line, can form clean further on (see ``fold_piece``):
    ``(layout, cut)``, or None if nowhere up to the end of the panel (or
    ``_FURTHER`` places on). It goes on the way the fold has moved, or,
    for one still on its line, ``way`` (1.0 to its left, -1.0 to its
    right; None at once for one on its line with no way).

    A place is clean when the layout made there, the pile measured anew
    and the fold held where it is, finds no leather more than ``_TOUCH``
    into the fold's bend and does not send the fold back because its bend
    no longer fits there: no more than that. (A layout that first sends
    another fold back measures nothing of this one, and its place counts
    as clean; the moves that follow from it measure the fold again.) What
    else the fold formed there brings into other leather is told as for
    any fold that has moved (the ``landing`` and ``clash`` notes, and the
    warning that gives the depth with the fold on its line as well)."""
    if lay.moved[fi]:
        way = 1.0 if lay.moved[fi] > 0.0 else -1.0
    if not way:
        return None

    def clean(d):
        trial = list(lay.moved)
        trial[fi] += way * d
        new = _recut(ring, first, lay, fi, trial)
        if new is None:
            return None, None
        least = [r if m and k != fi else 0.0
                 for k, (r, m) in enumerate(zip(lay.radius, trial))]
        got = Layout(new[0], folds, new[1], moved=trial, held=held | {fi},
                     fixed=fixed, least=least, **kw)
        ok = not got.stuck[fi] and (got.to_move is None
                                    or got.to_move[0] != fi)
        return ok, (got, new)

    last = 0.0
    for k in range(1, _FURTHER + 1):
        d = float(k) if k <= 40 else 40.0 + 2.0 * (k - 40)
        ok, got = clean(d)
        if ok is None:
            return None
        if ok:
            lo, hi, best = last, d, got
            for _half in range(6):
                mid = 0.5 * (lo + hi)
                ok, got = clean(mid)
                if ok:
                    hi, best = mid, got
                else:
                    lo = mid
            return best
        last = d
    return None


def _recut(ring: Sequence[Pt], first: Layout, was: Layout, fi: int,
           moved: Sequence[float]):
    """Cut a piece again with each fold line ``moved`` mm to the left of
    where its first layout has it -- fold ``fi`` on from where the layout
    ``was`` has it: ``(regions, lines)``, the regions in the order of the
    first layout's panels, so that every panel keeps its number.

    None if the piece no longer comes apart into the same panels, hinged
    along the same folds, each stretch of the fold that moved ending
    where its ends slide to along the edges of the panels. (A fold moved
    off the foot of a flap, into the wider panel the flap stands on,
    would run right across that panel instead.)"""
    folds = []
    for f, line, d in zip(first.folds, first.lines, moved):
        if line is None:
            folds.append(replace(f, b=f.a))    # it cuts nothing, as before
            continue
        (ax, ay), (ex, ey), ln = line
        ax, ay = ax - d * ey, ay + d * ex
        folds.append(replace(f, a=(ax, ay), b=(ax + ln * ex, ay + ln * ey)))
    regions, lines = split_piece(ring, folds, 0.0)
    hinges = find_hinges(regions, lines)
    n = len(first.panels)
    if len(regions) != n or len(hinges) != len(first.hinges):
        return None

    def sides(found):
        """For each panel, the folds it lies along and on which side."""
        out = [set() for _ in range(n)]
        for h in found:
            out[h.left].add((h.fold, 1))
            out[h.right].add((h.fold, -1))
        return out

    # a region is the panel that lay along the same folds on the same
    # sides -- and of two that did, the one it shares most leather with
    before, now = sides(first.hinges), sides(hinges)
    panel = []
    for k, (pts, _flags) in enumerate(regions):
        same = [i for i in range(n) if before[i] == now[k]
                and i not in panel]
        if not same:
            return None
        panel.append(same[0] if len(same) == 1 else max(
            same, key=lambda i: _shared(pts, first.panels[i])))
    # ... and every hinge is one that was there -- one of the fold that
    # moved with its ends where the old one's have slid to
    step = moved[fi] - was.moved[fi]
    a, e, _ln = was.lines[fi]
    b = lines[fi][0]
    old = list(was.hinges)
    for h in hinges:
        for g in old:
            if ((g.fold, g.left, g.right)
                    == (h.fold, panel[h.left], panel[h.right])
                    and min(g.hi, h.hi) - max(g.lo, h.lo) > 1e-6):
                break
        else:
            return None
        old.remove(g)
        if h.fold != fi:
            continue
        into, other = (g.left, g.right) if step > 0.0 else (g.right, g.left)
        for k0, k1, ahead in ((g.hi, h.hi, step > 0.0),
                              (g.lo, h.lo, step < 0.0)):
            end = _slide(was.panels[into].pts, was.panels[other].pts,
                         (a[0] + k0 * e[0], a[1] + k0 * e[1]), step, e,
                         ahead)
            if end is None or math.dist(end, (b[0] + k1 * e[0],
                                              b[1] + k1 * e[1])) > 1e-5:
                return None
    order = sorted(range(n), key=lambda k: panel[k])
    return [regions[k] for k in order], lines


def _slide(into: Sequence[Pt], other: Sequence[Pt], p: Pt, step: float,
           e: Pt, ahead: bool) -> Optional[Pt]:
    """Where the end ``p`` of a stretch of a fold comes to when the fold
    line (along unit ``e``) moves ``step`` mm to its left -- to its right
    if that is less than 0.

    It slides along the outline of the panel the line moves into:
    ``into``, with ``ahead`` the way to follow that from ``p`` (the way
    its points run, or against). Where that panel runs on past ``p``
    along the line -- another fold meets this one there -- it follows
    the edge of the ``other`` panel that ends at ``p``, carried on. None
    if no edge leads that way from ``p``: the line would run on into the
    panel, and across it."""
    side = 1.0 if step > 0.0 else -1.0
    left = abs(step)

    def gain(q, r):                # how far that way, from q to r
        return side * ((r[1] - q[1]) * e[0] - (r[0] - q[0]) * e[1])

    n = len(into)
    k = min(range(n), key=lambda i: math.dist(into[i], p))
    if math.dist(into[k], p) <= 1e-6:
        q = into[k]
        for _edge in range(n):
            k = (k + 1) % n if ahead else (k - 1) % n
            more = gain(q, into[k])
            if more <= 1e-9:
                return None
            if more >= left:
                return (q[0] + (into[k][0] - q[0]) * left / more,
                        q[1] + (into[k][1] - q[1]) * left / more)
            left -= more
            q = into[k]
        return None
    n = len(other)
    k = min(range(n), key=lambda i: math.dist(other[i], p))
    if math.dist(other[k], p) > 1e-6:
        return None
    q = other[(k - 1) % n if ahead else (k + 1) % n]
    more = gain(q, p)
    if more <= 1e-9:
        return None
    return (p[0] + (p[0] - q[0]) * left / more,
            p[1] + (p[1] - q[1]) * left / more)


def _shared(pts: Sequence[Pt], panel: Panel) -> float:
    """The area a polygon has in common with a panel."""
    parts = (pc.clip_to_convex(pts, part) for part in panel.parts)
    return sum(abs(pc.area(p)) for p in parts if p)


def fold_net(outlines: Sequence[Sequence[Pt]], *, angle: float = 90.0,
             direction: int = 1, thickness: float = 2.0, radius: float = 0.0,
             base: Optional[int] = None, holes=(), detail: float = 12.0,
             smooth: float = 0.03) -> Layout:
    """Lay out panels drawn edge to edge (a paper net) for folding along
    the edges they share, every one by ``angle`` the same way."""
    rings = [model_outline(o, smooth) for o in outlines]
    regions, folds, lines = net_regions(rings)
    for f in folds:
        f.angle, f.direction = angle, direction
    sharp = set()
    for r in rings:
        sharp |= sharp_corners(r)
    return Layout(regions, folds, lines, thickness=thickness, radius=radius,
                  base=base, holes=holes, detail=detail, sharp=sharp)

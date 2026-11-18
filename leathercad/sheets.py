"""Pieces of leather laid on a piece that folds: the stack, lying flat.

A stack is built on one piece, the main piece: the one a
``foldsolid.Layout`` is made from, whose fold lines the whole stack is to
fold along. Every other piece of it is a ``Sheet``: its outline where it
lies, in the main piece's own coordinates, its thickness, the face of the
main piece it is on and which way up it is. ``settle`` lays the sheets
down in order and finds the height each comes to rest at; ``laid_check``
looks through the layers at every stitch hole; ``FlatStack`` is the stack
as a model for the view.

Pieces are numbered as the tags of the model number them: 0 is the main
piece and sheet ``k`` (from 1) is ``sheets[k - 1]``. Heights are mm above
the face of the main piece that a sheet is on. Pure Python, no Qt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from . import foldsolid as fs
from . import polycut as pc
from .bsp import BACK, DOTS, FRONT, INNER, WALL, Cell, Face

Pt = Tuple[float, float]

# Two pieces lie on each other when they overlap, in plan, by more than
# this (mm): any real overlap at all, so that no two pieces are ever drawn
# in the same place. Pieces that only touch share a level. It must not be
# more than ``clash.JUDGE``, which is what the check of a finished model
# lets two cells share: whatever is left on one level then passes that
# check. (It is ``bsp.EPS`` too: the painter cuts nothing that shares less.)
LAP = 1e-6
SLIVER = 1.0        # a piece resting on less overlap than this (mm) is told
# less than this (mm) is a curve drawn as short straight lines, not a
# place where one piece really stands past or clear of another
HAIR = 0.05

# A mark of the hole check, beside ``foldsolid``'s MEETS, OFF, LONE and
# LANDS: where a hole of another piece comes through this one, which has
# no hole there. It is not a fault -- pockets are sewn through holes only
# the outside piece is cut with -- so it is not a state of the hole
# itself, only a spot on the piece it comes through.
BLANK = 5


@dataclass
class Sheet:
    """A piece of leather laid on the main piece of a stack.

    ``ring`` is its outline where it lies, in the coordinates the main
    piece's layout is built in (pattern mm; it is made counter-clockwise
    and thinned as the main piece's own outline is). ``side`` is the face
    of the main piece it is on: 1 its front (the side the pattern is
    drawn on), -1 its back. ``grain`` is 1 when its grain side faces the
    way the main piece's front does, -1 when it lies the other way up.
    ``key`` is the caller's name for it (the shape's id) and ``leather``
    one of the view's leathers, "" for the usual one. ``holes``,
    ``cutouts`` and ``marks`` are its stitch holes, the rings cut out of
    it and the lines drawn on it, as ``foldsolid.fold_piece`` takes them
    and in the same coordinates as the outline.

    ``settle`` works out the rest. ``lo`` and ``hi`` are the heights of
    its underside and its top above the face of the main piece it is on.
    ``rests`` are the pieces it lies on (0 is the main piece), the one it
    overlaps most first, and ``lap`` is that overlap: how far, in mm, it
    would have to slide to come off that piece. A sheet over nothing at
    all rests on none and is ``loose``. ``clear`` are the pieces under it
    that it does not come down on, as ``[(piece, gap)]`` with the widest
    gap first. ``rank`` is one more than the highest rank of anything
    under it (the main piece has 0): how many layers it is lifted by when
    the stack is pulled apart. ``past`` is how far it stands out past the
    edge of the main piece, at its corner ``past_at``. ``parts`` is its
    outline in convex parts: none for an outline with no area, which is
    no leather -- such a sheet lifts nothing, is shown as nothing, and is
    told as lying on nothing."""

    ring: list
    thickness: float
    side: int = 1
    grain: int = 1
    name: str = ""
    key: str = ""
    leather: str = ""
    holes: list = ()
    cutouts: list = ()
    marks: list = ()
    lo: float = field(default=0.0, init=False)
    hi: float = field(default=0.0, init=False)
    rests: list = field(default_factory=list, init=False)
    clear: list = field(default_factory=list, init=False)
    rank: int = field(default=1, init=False)
    past: float = field(default=0.0, init=False)
    past_at: Optional[Pt] = field(default=None, init=False)
    lap: float = field(default=0.0, init=False)
    loose: bool = field(default=False, init=False)
    parts: list = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        self.ring = fs.model_outline(self.ring) if len(self.ring) >= 3 \
            else [(float(p[0]), float(p[1])) for p in self.ring]
        self.thickness = max(0.05, float(self.thickness))
        self.side = 1 if self.side >= 0 else -1
        self.grain = 1 if self.grain >= 0 else -1
        self.holes = [tuple(h) for h in self.holes]
        self.cutouts = [pc.ccw([tuple(p) for p in c]) for c in self.cutouts]
        self.marks = [(kind, [tuple(p) for p in pts])
                      for kind, pts in self.marks]
        self.hi = self.thickness
        self.parts = _convex(self.ring)


class Note(NamedTuple):
    """Something to tell of a stack, as numbers for the window to put into
    words (it knows the units): ``kind`` says what, ``sheet`` is the piece
    it is about and ``fold`` the fold (-1 for none), ``a`` and ``b`` are
    lengths in mm, ``other`` the other pieces it names and ``at`` a point
    of the pattern where it shows, if there is one.

    What ``settle`` tells of a flat stack:

    ``"thick"``  the stack is ``a`` mm at its thickest, ``b`` mm of that
                 leather (less than ``a`` where a piece bridges a gap);
                 ``at`` is a point of that place and ``other`` the pieces
                 with leather there: the main piece, then outwards on its
                 front, then outwards on its back
    ``"past"``   ``sheet`` stands ``a`` mm past the edge of the main
                 piece, furthest at its corner ``at``
    ``"clear"``  ``sheet`` stands ``a`` mm clear of the piece ``other[0]``
                 that lies under part of it, lower than what it rests on
                 (and of the rest of ``other`` by less)
    ``"sliver"`` ``sheet`` rests on the piece ``other[0]`` by only ``a``
                 mm of overlap
    ``"loose"``  ``sheet`` lies on nothing

    and ``FlatStack`` adds ``"flat"``: the stack is shown flat, the ``a``
    steps its main piece folds in left unmade."""

    kind: str
    sheet: int = 0
    fold: int = -1
    a: float = 0.0
    b: float = 0.0
    other: tuple = ()
    at: Optional[Pt] = None


def sheet_of(tag) -> int:
    """The piece a cell or a face of a model is of, from its tag: 0 for
    the main piece, whose tags have two parts, else the sheet's number."""
    return tag[2] if tag is not None and len(tag) > 2 else 0


# -- polygons, in plan --------------------------------------------------------

def _convex(ring) -> List[List[Pt]]:
    """An outline (counter-clockwise) as convex polygons: none, for one
    with no area."""
    if len(ring) < 3 or abs(pc.area(ring)) <= 1e-9:
        return []
    return [p for p, _f in pc.convex_parts(ring, [pc.OUTLINE] * len(ring))] \
        or [list(ring)]


def _depth(A: Sequence[Pt], B: Sequence[Pt]) -> float:
    """How far two convex polygons (counter-clockwise) reach into each
    other (mm): the least either would have to slide to come clear of the
    other, and 0 or less for two that only touch or lie apart. It is the
    most that ``polycut.convex_overlap`` will agree they overlap by."""
    best = math.inf
    for P, Q in ((A, B), (B, A)):
        x0, y0 = P[-1]
        for x1, y1 in P:
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy)
            if ln > 1e-12:
                nx, ny = dy / ln, -dx / ln     # out of the polygon
                inn = -min((q[0] - x0) * nx + (q[1] - y0) * ny for q in Q)
                if inn < best:
                    best = inn
                    if best <= 0.0:
                        return best
            x0, y0 = x1, y1
    return best


def _reach(parts_a, parts_b) -> float:
    """How far two pieces, each given in convex parts, reach into each
    other (mm): the most any part of one does into any part of the other,
    0 for pieces that lie apart."""
    best = 0.0
    boxes = [pc.bounds(B) for B in parts_b]
    for A in parts_a:
        ax0, ay0, ax1, ay1 = pc.bounds(A)
        for B, (bx0, by0, bx1, by1) in zip(parts_b, boxes):
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                d = _depth(A, B)
                if d > best:
                    best = d
    return best


def _width(pts: Sequence[Pt]) -> float:
    """How wide a convex polygon is where it is narrowest (mm)."""
    best = math.inf
    x0, y0 = pts[-1]
    for x1, y1 in pts:
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln > 1e-12:
            far = max(dx * (q[1] - y0) - dy * (q[0] - x0) for q in pts) / ln
            if far < best:
                best = far
        x0, y0 = x1, y1
    return best if best < math.inf else 0.0


def _split(pts: Sequence[Pt], p: Pt, q: Pt):
    """A convex polygon cut along the line through ``p`` and ``q``:
    ``(what lies to its left, what lies to its right)``. A point on the
    line counts as to its left."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    left: List[Pt] = []
    right: List[Pt] = []
    a = pts[-1]
    da = dx * (a[1] - p[1]) - dy * (a[0] - p[0])
    for b in pts:
        db = dx * (b[1] - p[1]) - dy * (b[0] - p[0])
        if (da < 0.0) != (db < 0.0):
            t = da / (da - db)
            m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            left.append(m)
            right.append(m)
        (right if db < 0.0 else left).append(b)
        a, da = b, db
    return left, right


def _minus(bits, parts, least: float = LAP):
    """What is left of some convex polygons once the convex ``parts`` of a
    piece are taken out of them, as convex polygons. Whatever is left
    narrower than ``least`` is dropped."""
    for B in parts:
        bx0, by0, bx1, by1 = pc.bounds(B)
        out = []
        for P in bits:
            px0, py0, px1, py1 = pc.bounds(P)
            if not (px0 < bx1 and bx0 < px1 and py0 < by1 and by0 < py1):
                out.append(P)
                continue
            rest = P
            x0, y0 = B[-1]
            for x1, y1 in B:
                if len(rest) < 3:
                    break
                # inside a counter-clockwise polygon is left of each edge
                rest, cut = _split(rest, (x0, y0), (x1, y1))
                if len(cut) >= 3 and _width(cut) > least:
                    out.append(cut)
                x0, y0 = x1, y1
        bits = out
    return bits


def _common(bits, parts, least: float = LAP):
    """The convex polygons that some convex polygons and the convex
    ``parts`` of a piece have in common, wider than ``least``."""
    out = []
    for P in bits:
        for B in parts:
            got = pc.clip_to_convex(P, B)
            if got and _width(got) > least:
                out.append(got)
    return out


def _in(p: Pt, parts) -> bool:
    """Whether a point is on a piece given in convex parts."""
    return any(pc.inside_convex(p, part, 1e-9) for part in parts)


def _to_ring(p: Pt, ring: Sequence[Pt]) -> float:
    """How far a point is from an outline."""
    best = math.inf
    x0, y0 = ring[-1]
    for x1, y1 in ring:
        dx, dy = x1 - x0, y1 - y0
        ll = dx * dx + dy * dy
        t = 0.0 if ll < 1e-18 else max(0.0, min(1.0, (
            (p[0] - x0) * dx + (p[1] - y0) * dy) / ll))
        d = math.hypot(p[0] - x0 - t * dx, p[1] - y0 - t * dy)
        if d < best:
            best = d
        x0, y0 = x1, y1
    return best


# -- the levels ---------------------------------------------------------------

def settle(main_ring: Sequence[Pt], main_thickness: float,
           sheets: Sequence[Sheet]) -> List[Note]:
    """Lay the sheets on the main piece and find where each comes to rest.

    Each face of the main piece has a pile of its own. The sheets go down
    in the order given, the nearest the main piece first, and each lies
    flat on the highest leather under any part of it: on the main piece's
    face, or on the top of the tallest sheet already on that face that it
    overlaps by more than ``LAP``. A sheet half on another therefore rests
    on the higher and stands clear of the lower, as one flat piece (it is
    not bedded down over the step).

    ``main_ring`` is the main piece's outline, as ``fold_piece`` takes it.
    Fills in what ``Sheet`` says is worked out, and returns what there is
    to tell of the stack as ``Note``s: its thickest place, then every
    sheet that stands past the main piece's edge, stands clear of
    something under it, rests on a sliver, or lies on nothing."""
    ring = fs.model_outline(main_ring) if len(main_ring) >= 3 else []
    ground = _convex(ring)
    for side in (1, -1):
        laid: List[int] = []                   # the sheets down on this face
        for k, s in enumerate(sheets):
            if s.side != side:
                continue
            under = []                 # (its top, the overlap, which sheet)
            for j in laid:
                d = _reach(s.parts, sheets[j].parts)
                if d > LAP:
                    under.append((sheets[j].hi, d, j))
            s.lo = max((u[0] for u in under), default=0.0)
            s.hi = s.lo + s.thickness
            if under:
                held = sorted(((d, j) for hi, d, j in under
                               if hi >= s.lo - 1e-9), reverse=True)
                s.rests = [j + 1 for _d, j in held]
                s.lap = held[0][0]
                s.rank = 1 + max(sheets[j].rank for _h, _d, j in under)
            else:
                s.lap = _reach(s.parts, ground)
                s.rests = [0] if s.lap > LAP else []
                s.rank = 1
            s.loose = not s.rests
            s.clear = _gaps(s, under, ground, sheets)
            s.past, s.past_at = LAP, None
            if ground and s.parts:
                for p in s.ring:
                    if not pc.inside(p, ring):
                        d = _to_ring(p, ring)
                        if d > s.past:
                            s.past, s.past_at = d, p
            if s.past_at is None:
                s.past = 0.0
            laid.append(k)
    notes = [_thickest(ring, ground, max(0.05, float(main_thickness)),
                       sheets)]
    numbered = list(enumerate(sheets, 1))
    notes += [Note("past", k, -1, s.past, at=s.past_at)
              for k, s in numbered if s.past > HAIR]
    notes += [Note("clear", k, -1, s.clear[0][1],
                   other=tuple(what for what, _gap in s.clear))
              for k, s in numbered if s.clear]
    notes += [Note("sliver", k, -1, s.lap, other=(s.rests[0],))
              for k, s in numbered if s.rests and s.rests[0]
              and s.lap < SLIVER]
    notes += [Note("loose", k) for k, s in numbered if s.loose]
    return notes


def _gaps(s: Sheet, under, ground, sheets) -> List[Tuple[int, float]]:
    """What a sheet stands clear of: every piece that is the highest
    leather under some part of it and yet lower than what it rests on, as
    ``[(piece, gap)]`` with the widest gap first."""
    out = []
    bits = list(s.parts)                       # of it, with nothing under yet
    for hi, _d, j in sorted(under, reverse=True):         # the tallest first
        if not bits:
            break
        if hi < s.lo - 1e-9 and _common(bits, sheets[j].parts, HAIR):
            out.append((j + 1, s.lo - hi))
        bits = _minus(bits, sheets[j].parts)
    if s.lo > 1e-9 and _common(bits, ground, HAIR):
        out.append((0, s.lo))
    out.reverse()
    return out


def _thickest(ring, ground, thickness: float, sheets) -> Note:
    """The thickest place of the flat stack, as a ``"thick"`` note."""
    fronts = [k for k, s in enumerate(sheets) if s.side > 0 and s.parts]
    backs = [k for k, s in enumerate(sheets) if s.side < 0 and s.parts]
    tall, pair = 0.0, (None, None)
    for side in (fronts, backs):
        for k in side:
            if sheets[k].hi > tall:
                tall = sheets[k].hi
                pair = (k, None) if side is fronts else (None, k)
    for f in fronts:                   # a pile on each face, both in one place
        for b in backs:
            both = sheets[f].hi + sheets[b].hi
            if both > tall and _reach(sheets[f].parts,
                                      sheets[b].parts) > LAP:
                tall, pair = both, (f, b)
    # a point of it: on the top sheet of each pile, and on as much as can
    # be of what that rests on, and what that rests on, down to the main
    # piece
    tops = [k for k in pair if k is not None]
    place = ground
    if tops:
        place = list(sheets[tops[0]].parts)
        for k in tops[1:]:
            place = _common(place, sheets[k].parts)
    for k in tops:
        while sheets[k].rests:
            on = sheets[k].rests[0]
            got = _common(place, sheets[on - 1].parts if on else ground)
            if not got:
                break
            place = got
            if not on:
                break
            k = on - 1
    at = pc.centroid(max(place, key=pc.area)) if place else None
    here = [0] if at is not None and _in(at, ground) else []
    leather = thickness if here else 0.0
    for side in (fronts, backs):
        for k in sorted(side, key=lambda k: sheets[k].lo):
            if at is not None and _in(at, sheets[k].parts):
                here.append(k + 1)
                leather += sheets[k].thickness
    return Note("thick", 0, -1, thickness + tall, leather, tuple(here), at)


# -- stitch holes through the layers ------------------------------------------

@dataclass
class Laid(fs.Lineup):
    """Two pieces of a stack that lie over each other, with stitch holes
    where they do: a ``foldsolid.Lineup`` with ``a`` and ``b`` the pieces
    (0 is the main piece) and ``names`` what they are called. ``holes``
    counts the holes of either that lie on the other's leather. ``lone``
    of them come through the other piece where it has no hole anywhere
    near: ``through`` says how many of those are holes of ``a`` and how
    many of ``b``. ``laid`` tells such a pair from two panels that a fold
    brings together."""

    names: Tuple[str, str] = ("", "")
    laid: bool = True
    through: Tuple[int, int] = (0, 0)


class LaidCheck(fs.Check):
    """What ``laid_check`` found: a ``foldsolid.Check`` whose ``findings``
    are ``Laid`` pairs and whose holes are numbered through the whole
    stack, the main piece's first. A hole that only comes through leather
    with no hole in it has no state: that is marked on the piece it comes
    through. ``passed`` has those spots for each piece, and ``landed`` the
    spots where holes that were missed come down on it, both as ``{piece:
    [(x, y, diameter)]}`` in pattern mm; ``rings`` and ``blanks`` are the
    same spots sorted by the cell they are on, for the model (filled in by
    whoever has the cells)."""

    __slots__ = ("blanks", "landed", "passed")

    def __init__(self):
        super().__init__()
        self.blanks: Dict[int, List[Tuple[float, float, float]]] = {}
        self.landed: Dict[int, List[Tuple[float, float, float]]] = {}
        self.passed: Dict[int, List[Tuple[float, float, float]]] = {}


class _Layer:
    """The main piece as ``laid_check`` wants a piece."""

    __slots__ = ("ring", "cutouts", "holes", "name")

    def __init__(self, ring, cutouts, holes, name):
        self.ring = ring
        self.cutouts = cutouts
        self.holes = holes
        self.name = name


def laid_check(layers, tol: float = 0.75, near: float = 4.0) -> LaidCheck:
    """Check the stitch holes of a stack lying flat: does every hole go
    through holes in the other pieces it passes through?

    ``layers`` are the pieces, the main piece first: anything with a
    ``ring``, ``cutouts``, ``holes`` as ``(x, y, diameter)`` and a
    ``name``, all in the same coordinates. The holes are numbered in that
    order, so the main piece's keep the numbers its layout gives them.

    Every two pieces are compared, whichever face of the main piece they
    are on: a needle goes through all of them. A hole is looked at only
    where the other piece has leather -- inside its outline and outside
    its cutouts. There it meets the nearest hole of that piece if it is
    within ``tol`` mm of it, misses it if it is within ``near`` mm but not
    on it, and otherwise comes through leather with no hole in it, which
    is told and marked but is no fault. As in ``Layout.lineup`` a hole is
    then given what its whole stack of holes comes to: ``OFF`` if any two
    in it miss, ``MEETS`` if it has company and none does."""
    check = LaidCheck()
    holes: List[tuple] = []
    owner: List[int] = []
    mine: List[List[int]] = []
    for i, layer in enumerate(layers):
        mine.append(list(range(len(holes), len(holes) + len(layer.holes))))
        holes += [tuple(h) for h in layer.holes]
        owner += [i] * len(layer.holes)
    boxes = [pc.bounds(layer.ring) if len(layer.ring) >= 3
             and abs(pc.area(layer.ring)) > 1e-9 else None
             for layer in layers]               # None: it has no leather
    # ... and then its holes are in nothing: numbered, but not looked at
    mine = [ks if box is not None else [] for ks, box in zip(mine, boxes)]
    grids = []                                 # each piece's holes, by square
    for ks in mine:
        grid: Dict[tuple, list] = {}
        for k in ks:
            grid.setdefault((math.floor(holes[k][0] / near),
                             math.floor(holes[k][1] / near)), []).append(k)
        grids.append(grid)

    def leather(i, p):
        box = boxes[i]
        if box is None or not (box[0] <= p[0] <= box[2]
                               and box[1] <= p[1] <= box[3]):
            return False
        return pc.inside(p, layers[i].ring) \
            and not any(pc.inside(p, c) for c in layers[i].cutouts)

    link: Dict[int, int] = {}                  # holes that meet, in stacks
    misses: List[Tuple[int, int]] = []         # (hole, the one it misses)
    seen = set()

    def stack(k):
        while link.setdefault(k, k) != k:
            link[k] = link[link[k]]
            k = link[k]
        return k

    spots = set()

    def spot(table, piece, x, y, dia):
        key = (id(table), piece, round(x, 3), round(y, 3))
        if key not in spots:
            spots.add(key)
            table.setdefault(piece, []).append((x, y, dia))

    for i in range(len(layers)):
        for j in range(i + 1, len(layers)):
            if not mine[i] and not mine[j]:
                continue
            looked = off = 0
            worst = apart = 0.0
            through = [0, 0]
            for way, (a, b) in enumerate(((i, j), (j, i))):
                for k in mine[a]:
                    x, y, dia = holes[k]
                    if not leather(b, (x, y)):
                        continue
                    looked += 1
                    gx, gy = math.floor(x / near), math.floor(y / near)
                    d, k2 = min(
                        ((math.hypot(x - holes[k3][0], y - holes[k3][1]), k3)
                         for ix in (gx - 1, gx, gx + 1)
                         for iy in (gy - 1, gy, gy + 1)
                         for k3 in grids[b].get((ix, iy), ())),
                        default=(2.0 * near, -1))
                    if d <= tol:
                        seen.update((k, k2))
                        apart = max(apart, d)
                        link[stack(k)] = stack(k2)
                    elif d <= near:
                        seen.add(k)
                        off += 1
                        worst = max(worst, d)
                        misses.append((k, k2))
                    else:
                        through[way] += 1
                        spot(check.passed, b, x, y, dia)
            if looked:
                check.findings.append(Laid(
                    i, j, looked, off, worst, sum(through), apart,
                    (layers[i].name, layers[j].name), True, tuple(through)))
    # each hole by the stack it is in, as ``Layout.lineup`` does
    stacks: Dict[int, List[int]] = {}
    for k in seen:
        stacks.setdefault(stack(k), []).append(k)
    out_of_line = {stack(k) for k, _k2 in misses}
    for top, members in stacks.items():
        what = fs.OFF if top in out_of_line else fs.MEETS
        for k in members:
            check.state[k] = what
    # ... and for every hole of a stack with a miss in it, where the hole
    # that was missed is: on each other piece of that stack
    for k, k2 in misses:
        x2, y2, dia = holes[k2]
        for m in stacks[stack(k)]:
            if owner[m] == owner[k2]:
                continue
            check.lands.setdefault(m, []).append((x2, y2, dia))
            spot(check.landed, owner[m], x2, y2, dia)
    return check


# -- the stack as a model -----------------------------------------------------

def outline_of(layout) -> List[Pt]:
    """The outline of the piece a layout was cut from, put together again
    from the edges of its panels where the leather ends (the longest way
    round, if they make more than one)."""
    def key(p):
        return (round(p[0], 6), round(p[1], 6))

    edges = {}
    for panel in layout.panels:
        pts, n = panel.pts, len(panel.pts)
        for k in range(n):
            if panel.flags[k] == pc.OUTLINE:
                edges[(key(pts[k]), key(pts[(k + 1) % n]))] = pts[k]
    # an edge that runs both ways is where folds that cross have cut two
    # panels apart: not the outline
    nxt = {a: (b, p) for (a, b), p in edges.items() if (b, a) not in edges}
    best: List[Pt] = []
    while nxt:
        start = a = next(iter(nxt))
        ring = []
        while a in nxt:
            a, p = nxt.pop(a)
            ring.append(p)
        if a == start and len(ring) >= 3 \
                and (not best or abs(pc.area(ring)) > abs(pc.area(best))):
            best = ring
    if not best and layout.panels:
        best = list(max(layout.panels, key=lambda p: p.area).pts)
    return pc.ccw(best)


class FlatStack:
    """A stack lying flat, as a model for the view: the main piece's
    ``Layout`` with sheets laid on it.

    It has every member the view and the dialog ask of a layout --
    ``fractions``, ``solid``, ``fold_marks``, ``panel_marks``,
    ``bounds3``, ``extent``, ``lineup`` and ``thickness``, ``folds``,
    ``steps``, ``n_steps``, ``panels``, ``base``, ``crosses``, ``used``,
    ``radius``, ``wrap``, ``warnings`` -- and ``sheets``, the ``notes`` of
    ``settle``, ``holes`` (every hole of the stack, the main piece's
    first) with ``hole_sheet`` (the piece each is of), the main piece's
    ``layout``, ``outline`` and ``name``.

    The sheets are settled as they are taken (``settle`` fills them in).
    With no sheets it is the layout, fold for fold. With sheets it shows
    the stack flat only: no fold is made (``steps`` are all 0 and
    ``n_steps`` is 0, so there is nothing to play), and a ``"flat"`` note
    says so. ``solid``, ``fold_marks``, ``panel_marks`` and ``bounds3``
    then take one thing more, ``apart``: every sheet is lifted that many
    mm for each layer of its ``rank``, away from the main piece, to show
    what lies on what. The marks sit on the outermost leather over them
    and say which piece that is (see ``fold_marks``).

    This class is scaffolding. Folding a stack belongs in ``foldsolid``
    -- the sheets cut along the main piece's bends and carried round them
    -- and when ``Layout`` takes sheets it does all of this itself. So it
    is kept small: it cuts nothing and borrows the layout's own helpers
    for a cell of the pattern and the marks on it."""

    def __init__(self, layout, sheets: Sequence[Sheet] = (), outline=None,
                 name: str = ""):
        self.layout = layout
        self.sheets = list(sheets)
        self.name = name
        self.outline = fs.model_outline(outline) \
            if outline is not None and len(outline) >= 3 \
            else outline_of(layout)
        self.thickness = layout.thickness
        self.folds = layout.folds
        self.panels = layout.panels
        self.base = layout.base
        self.crosses = layout.crosses
        self.notes = settle(self.outline, layout.thickness, self.sheets)
        n = len(layout.folds)
        if self.sheets:
            self.steps = [0] * n
            self.n_steps = 0
            self.used = [0.0] * n
            self.wrap = [0.0] * n
            self.radius = [r - w for r, w in zip(layout.radius, layout.wrap)]
            self.warnings: List[str] = []
            if layout.n_steps:
                self.notes.append(Note("flat", 0, -1, float(layout.n_steps)))
        else:
            self.steps, self.n_steps = layout.steps, layout.n_steps
            self.used, self.wrap = layout.used, layout.wrap
            self.radius, self.warnings = layout.radius, layout.warnings
        self.holes = list(layout.holes)
        self.hole_sheet = [0] * len(layout.holes)
        for k, s in enumerate(self.sheets, 1):
            self.holes += s.holes
            self.hole_sheet += [k] * len(s.holes)
        self._cells = self._cut()

    # -- the sheets as cells of the pattern -----------------------------------

    def _cut(self):
        """One cell for each convex part of each sheet, with the marks on
        it: made as ``Layout`` makes the cells of a panel."""
        cells = []
        index = len(self.layout.cells)
        first = len(self.layout.holes)         # the number of its first hole
        for k, s in enumerate(self.sheets, 1):
            # (a sheet with no area has no cells, but its holes are still
            # numbered with the rest)
            sharp = fs.sharp_corners(s.ring) if s.parts else ()
            parts = pc.convex_parts(s.ring, [pc.OUTLINE] * len(s.ring)) \
                if s.parts else ()
            mine = []
            for pts, flags in parts:
                c = fs._Cell2(pts, flags, self._panel_under(pts))
                n = len(pts)
                if n < 3 or c.area <= 1e-9:
                    continue
                c.edges = tuple(f == pc.OUTLINE for f in flags)
                c.edges_rev = tuple(c.edges[(n - 2 - i) % n]
                                    for i in range(n))
                corner = [(round(p[0], 6), round(p[1], 6)) in sharp
                          for p in pts]
                c.sides = []
                for i in range(n):
                    j = (i + 1) % n
                    dx, dy = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
                    ln = math.hypot(dx, dy) or 1e-12
                    wall = flags[i] == pc.OUTLINE
                    c.sides.append((dy / ln, -dx / ln, ln * s.thickness,
                                    WALL if wall else INNER,
                                    (True, corner[j], True, corner[i])
                                    if wall else None))
                c.tag = ("panel", c.panel, k)
                c.index = index
                index += 1
                mine.append(c)
            for i, (x, y, dia) in enumerate(s.holes):
                c = _cell_at(mine, x, y)
                if c is not None:
                    c.dots.append((x, y, dia, first + i))
            first += len(s.holes)
            for ring in s.cutouts:
                for c in mine:
                    part = pc.clip_to_convex(ring, c.pts)
                    if len(part) >= 3 and abs(pc.area(part)) > 1e-6:
                        c.polys.append(part)
            budget = 6000                      # strokes, as for a lone piece
            for kind, pts in s.marks:
                for p, q in zip(pts, pts[1:]):
                    budget -= 1
                    if budget < 0:
                        break
                    for c in mine:
                        bit = pc.clip_segment_to_convex(p, q, c.pts)
                        if bit is not None and math.dist(*bit) > 1e-6:
                            c.lines.append((kind, bit[0], bit[1]))
            cells += mine
        return cells

    def _panel_under(self, pts) -> int:
        """The panel of the main piece that a part of a sheet lies on: the
        one it has most leather over, or the nearest if it is over none."""
        best, most = None, 0.0
        for panel in self.panels:
            lap = 0.0
            for part in panel.parts:
                got = pc.clip_to_convex(pts, part)
                if got:
                    lap += abs(pc.area(got))
            if lap > most:
                best, most = panel.index, lap
        if best is None and self.panels:
            c = pc.centroid(pts)
            best = min(self.panels, key=lambda p: _to_ring(c, p.pts)).index
        return best or 0

    def _span(self, s: Sheet, apart: float) -> Tuple[float, float]:
        """Where a sheet's underside and its top are in the model, along
        the normal out of the main piece's front."""
        lift = 0.5 * self.thickness + apart * s.rank
        if s.side > 0:
            return lift + s.lo, lift + s.hi
        return -(lift + s.hi), -(lift + s.lo)

    def _outermost(self, side: int, p: Pt, apart: float):
        """The outermost leather at a point of the pattern, on one face of
        the main piece: ``(where its outer face is, which piece)``."""
        best = None
        for k, s in enumerate(self.sheets, 1):
            if s.side == side and (best is None or s.hi > best[0]) \
                    and _in(p, s.parts):
                best = (s.hi, k)
        if best is None:
            return side * 0.5 * self.thickness, 0
        z0, z1 = self._span(self.sheets[best[1] - 1], apart)
        return (z1 if side > 0 else z0), best[1]

    # -- what the view asks for -----------------------------------------------

    def fractions(self, t: float) -> List[float]:
        """How far each fold has gone at time ``t``: nowhere, for a stack
        with sheets."""
        if not self.sheets:
            return self.layout.fractions(t)
        return [0.0] * len(self.folds)

    def solid(self, fractions: Sequence[float], check=None,
              apart: float = 0.0) -> List[Cell]:
        """The model as convex cells: the main piece's own, flat, and one
        for each convex part of each sheet, tagged ``("panel", the panel
        of the main piece it lies on, the sheet's number)``. A sheet's
        face is ``FRONT`` where its grain is. With the ``check`` from
        ``lineup`` the stitch holes carry what it found."""
        if not self.sheets:
            return self.layout.solid(fractions, check)
        out = self.layout.solid([0.0] * len(self.folds), check)
        blanks = getattr(check, "blanks", None) or {}
        ht = 0.5 * self.thickness
        if blanks and len(out) == len(self.layout.cells):
            for i, cell in enumerate(out):
                if i not in blanks:
                    continue
                for f in cell.faces:
                    if f.kind == FRONT or f.kind == BACK:
                        z = ht if f.kind == FRONT else -ht
                        f.decals = _ringed(f.decals, blanks[i],
                                           lambda p, z=z: (p[0], p[1], z))
        for c in self._cells:
            s = self.sheets[c.tag[2] - 1]
            z0, z1 = self._span(s, apart)
            up, down = (FRONT, BACK) if s.grain > 0 else (BACK, FRONT)
            top = [(x, y, z1) for x, y in c.pts]
            bot = [(x, y, z0) for x, y in c.pts]
            marks = []
            for z in (z1, z0):
                def to3d(p, z=z):
                    return (p[0], p[1], z)
                marks.append(_ringed(fs._decals(c, to3d, check),
                                     blanks.get(c.index), to3d))
            ex, ey = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
            faces = [
                Face(tuple(top), (0.0, 0.0, 1.0, z1), up, c.edges, marks[0],
                     c.tag, c.area, ((0.0, 0.0, z1), ex, ey)),
                Face(tuple(bot[::-1]), (0.0, 0.0, -1.0, -z0), down,
                     c.edges_rev, marks[1], c.tag, c.area,
                     ((0.0, 0.0, z0), ex, ey))]
            m = len(top)
            for i in range(m):
                j = i + 1 if i + 1 < m else 0
                wx, wy, size, kind, edges = c.sides[i]
                b0 = bot[i]
                faces.append(Face((b0, bot[j], top[j], top[i]),
                                  (wx, wy, 0.0, wx * b0[0] + wy * b0[1]),
                                  kind, edges, None, c.tag, size))
            out.append(Cell(faces, c.tag))
        return out

    def fold_marks(self, fractions: Sequence[float], apart: float = 0.0):
        """Each fold line as it lies on the model, as ``Layout.fold_marks``
        gives it -- but on the outermost leather over the line, so that a
        lining does not bury it: a stretch is given in as many lengths as
        there are changes of what lies over it, and each has one thing
        more at its end, ``(piece, piece)``: the pieces its line on the
        front and its line on the back are drawn on."""
        if not self.sheets:
            return self.layout.fold_marks(fractions)
        out: Dict[int, list] = {}
        flat = self.layout.fold_marks([0.0] * len(self.folds))
        for fold, stretches in flat.items():
            for p, q, _p2, _q2, nn in stretches:
                a, b = p[:2], q[:2]
                dx, dy = b[0] - a[0], b[1] - a[1]
                ll = dx * dx + dy * dy
                if ll < 1e-18:
                    continue
                cuts = {0.0, 1.0}              # where what is on top changes
                for s in self.sheets:
                    for part in s.parts:
                        bit = pc.clip_segment_to_convex(a, b, part)
                        if bit is not None:
                            cuts.update(((r[0] - a[0]) * dx
                                         + (r[1] - a[1]) * dy) / ll
                                        for r in bit)
                runs = []              # (from, to, front, back) along a - b
                ts = sorted(cuts)
                for t0, t1 in zip(ts, ts[1:]):
                    if t1 - t0 <= 1e-9:
                        continue
                    tm = 0.5 * (t0 + t1)
                    mid = (a[0] + dx * tm, a[1] + dy * tm)
                    on = (self._outermost(1, mid, apart),
                          self._outermost(-1, mid, apart))
                    if runs and runs[-1][2:] == on:
                        runs[-1] = (runs[-1][0], t1) + on
                    else:
                        runs.append((t0, t1) + on)
                for t0, t1, (zf, kf), (zb, kb) in runs:
                    ends = [(a[0] + dx * t, a[1] + dy * t, z)
                            for z in (zf, zb) for t in (t0, t1)]
                    out.setdefault(fold, []).append(
                        tuple(ends) + (nn, (kf, kb)))
        return out

    def panel_marks(self, fractions: Sequence[float], apart: float = 0.0):
        """Where to write each panel's number, as ``Layout.panel_marks``
        gives it -- but on the outermost leather there, with one thing
        more at the end of each: ``(piece, piece)``, the pieces the spot
        on the front and the spot on the back are on."""
        if not self.sheets:
            return self.layout.panel_marks(fractions)
        out = []
        for front, _back, nn in self.layout.panel_marks(
                [0.0] * len(self.folds)):
            spot = front[:2]
            zf, kf = self._outermost(1, spot, apart)
            zb, kb = self._outermost(-1, spot, apart)
            out.append((spot + (zf,), spot + (zb,), nn, (kf, kb)))
        return out

    def bounds3(self, fractions: Sequence[float], apart: float = 0.0):
        """The box round the model as it stands: ``(low corner, high)``."""
        if not self.sheets:
            return self.layout.bounds3(fractions)
        lo, hi = self.layout.bounds3([0.0] * len(self.folds))
        lo, hi = list(lo), list(hi)
        for s in self.sheets:
            if not s.parts:
                continue                       # no leather: no room for it
            for z in self._span(s, apart):
                for x, y in s.ring:
                    for k, v in enumerate((x, y, z)):
                        if v < lo[k]:
                            lo[k] = v
                        if v > hi[k]:
                            hi[k] = v
        return tuple(lo), tuple(hi)

    def extent(self):
        """Centre and radius of a ball round the stack lying flat."""
        if not self.sheets:
            return self.layout.extent()
        lo, hi = self.bounds3([])
        return tuple(0.5 * (a + b) for a, b in zip(lo, hi)), \
            0.5 * math.dist(lo, hi)

    def lineup(self, tol: float = 0.75, near: float = 4.0):
        """Check the stitch holes: of the stack lying flat, through its
        layers (``laid_check``, with the spots it marks sorted by cell for
        ``solid``) -- or, with no sheets, of the piece folded up."""
        if not self.sheets:
            return self.layout.lineup(tol, near)
        main = _Layer(self.outline, self.layout.cutouts, self.layout.holes,
                      self.name)
        check = laid_check([main] + self.sheets, tol, near)
        for found, rings in ((check.landed, check.rings),
                             (check.passed, check.blanks)):
            for piece, spots in found.items():
                cells = [c for c in self._cells if c.tag[2] == piece] \
                    if piece else self.layout.cells
                for x, y, dia in spots:
                    c = _cell_at(cells, x, y)
                    if c is not None:
                        rings.setdefault(c.index, []).append((x, y, dia))
        return check


def _cell_at(cells, x: float, y: float):
    """The cell of the pattern that a point is in, of those given."""
    for c in cells:
        b = c.box
        if (b[0] - 1e-6 <= x <= b[2] + 1e-6
                and b[1] - 1e-6 <= y <= b[3] + 1e-6
                and pc.inside_convex((x, y), c.pts, 1e-6)):
            return c
    return None


def _ringed(decals, spots, to3d):
    """The marks on a face with the ``BLANK`` spots on it added: where
    holes of other pieces come through it."""
    if not spots:
        return decals
    groups: Dict[float, list] = {}
    for x, y, dia in spots:
        groups.setdefault(dia, []).append(to3d((x, y)))
    return (decals or []) + [(DOTS, pts, (dia, BLANK))
                             for dia, pts in groups.items()]

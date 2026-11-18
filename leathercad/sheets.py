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

import heapq
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
# place where one piece really stands past or clear of another; and a
# stitch hole this near an edge of a piece is on that edge, not in it
HAIR = 0.05
_SMOOTH = 0.03      # how closely (mm) a sheet's curved outline is followed
_BUSY = 260         # ... unless it would still have more corners than this
_GROW = 1e-9        # leather (mm) that thinning an outline may add: rounding
_ROOM = 4.0         # a panel's number wants this much (mm) clear of an edge
# where a sheet's grain is laid from when its outline has no first edge
_SQUARE = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))

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
    piece's layout is built in (pattern mm). ``side`` is the face of the
    main piece it is on: 1 its front (the side the pattern is drawn on),
    -1 its back. ``grain`` is 1 when its grain side faces the way the
    main piece's front does, -1 when it lies the other way up. ``key`` is
    the caller's name for it (the shape's id) and ``leather`` one of the
    view's leathers, "" for the usual one. ``holes``, ``cutouts`` and
    ``marks`` are its stitch holes, the rings cut out of it and the lines
    drawn on it, as ``foldsolid.fold_piece`` takes them and in the same
    coordinates as the outline.

    The outline is made ready as it is taken, and again by ``settle`` if
    another has been put in its place since (a piece that was moved):
    counter-clockwise, no doubled points, and a flattened curve thinned
    to within 0.03 mm. It is thinned inwards only, never outwards as a
    lone piece's outline may be: two pieces cut along one line share no
    leather as they are drawn, and so share none here and are not taken
    to lie on each other. ``parts`` is the outline in convex parts: none
    for an outline with no area, which is no leather -- such a sheet
    lifts nothing, is shown as nothing, and is told as lying on nothing.
    ``frame`` is where the grain of its leather is laid from, ``(a point,
    the way x runs, the way y runs)``: its first corner and its first
    edge, so that a piece moved or turned on the bench takes its grain
    with it.

    ``settle`` works out the rest. ``lo`` and ``hi`` are the heights of
    its underside and its top above the face of the main piece it is on.
    ``rests`` are the pieces it lies on (0 is the main piece), the one it
    overlaps most first, and ``lap`` is that overlap: how far, in mm, it
    would have to slide to come off that piece (for an outline that is
    not convex the slide may be longer than ``lap`` says, never shorter).
    A sheet over nothing at all rests on none and is ``loose``. ``clear``
    are the pieces under it that it does not come down on, as ``[(piece,
    gap)]`` with the widest gap first. ``rank`` is one more than the
    highest rank of anything under it (the main piece has 0): how many
    layers it is lifted by when the stack is pulled apart. ``past`` is
    how far it stands out past the edge of the main piece, and
    ``past_at`` the point of it furthest from the main piece's leather: a
    corner of it, unless it lies across a notch or an inside corner of
    the main piece (then to within a hundredth of a mm)."""

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
    frame: tuple = field(default=_SQUARE, init=False, repr=False)
    # the outline ``parts`` and ``frame`` were made from
    _seen: Optional[tuple] = field(default=None, init=False, repr=False,
                                   compare=False)

    def __post_init__(self):
        self.holes = [tuple(h) for h in self.holes]
        self.cutouts = [pc.ccw([tuple(p) for p in c]) for c in self.cutouts]
        self.marks = [(kind, [tuple(p) for p in pts])
                      for kind, pts in self.marks]
        self._ready()
        self.hi = self.thickness

    def _ready(self):
        """Make the sheet ready to be laid: its outline as the class
        says, with ``parts`` and ``frame`` from it, if the outline is not
        the one they were last made from. ``settle`` does this for every
        sheet it is given, so an outline put on a sheet after it was made
        (``sheet.ring = ...``) is the one that is laid."""
        self.thickness = max(0.05, float(self.thickness))
        self.side = 1 if self.side >= 0 else -1
        self.grain = 1 if self.grain >= 0 else -1
        given = tuple((float(p[0]), float(p[1])) for p in self.ring)
        if given == self._seen:
            return
        ring = _outline(given) if len(given) >= 3 else list(given)
        self.ring = ring
        self._seen = tuple(ring)
        self.parts = _convex(ring)
        self.frame = _frame(ring) if self.parts else _SQUARE


class Note(NamedTuple):
    """Something to tell of a stack, as numbers for the window to put into
    words (it knows the units): ``kind`` says what, ``sheet`` is the piece
    it is about and ``fold`` the fold (-1 for none), ``a``, ``b`` and
    ``c`` are lengths in mm (or a moment of the folding, where said),
    ``other`` the other pieces it names and ``at`` a point of the pattern
    where it shows, if there is one. A piece is its number in the stack,
    here and everywhere: 0 the main piece, ``k`` the sheet ``sheets[k -
    1]`` (``FlatStack.sheet`` gives the sheet of a number). A moment of
    the folding is as ``Layout.fractions`` takes it: 1.0 the end of step
    1, 0.5 half way through it.

    What ``settle`` tells of a flat stack:

    ``"thick"``  the stack is ``a`` mm at its thickest: from the lowest
                 underside to the highest top through one place, the main
                 piece counted only where it is. ``b`` mm of that is
                 leather (less than ``a`` where a piece bridges a gap);
                 ``at`` is a point of that place, where it has most
                 leather, and ``other`` the pieces with leather there:
                 the main piece, then outwards on its front, then
                 outwards on its back
    ``"past"``   ``sheet`` stands ``a`` mm past the edge of the main
                 piece, furthest at its point ``at``
    ``"clear"``  ``sheet`` stands ``a`` mm clear of the piece ``other[0]``
                 that lies under part of it, lower than what it rests on
                 (and of the rest of ``other`` by less)
    ``"sliver"`` ``sheet`` rests on the sheet ``other[0]`` by only ``a``
                 mm of overlap, less than ``SLIVER`` (a rest on the main
                 piece is never told so)
    ``"loose"``  ``sheet`` lies on nothing

    and ``FlatStack`` adds ``"flat"``: the stack is shown flat, the ``a``
    steps its main piece folds in left unmade.

    What a ``foldsolid.Layout`` with sheets adds of the stack folded
    (``fold_notes``, ``judge``), ``fold`` the fold's index:

    ``"ride"``     fold bends at ``a`` mm inside radius for ``sheet``,
                   which rides round the inside of its bend, its top ``b``
                   mm off the main piece's face
    ``"meet"``     fold bends at ``a`` mm so that ``sheet``, carried by
                   the fold, comes to rest on ``other[0]`` when shut (0,
                   0: the main piece on itself); ``b`` is the gap the bend
                   then closes round, twice the radius
    ``"corner"``   fold, standing open, bends at ``a`` mm so that
                   ``sheet`` and ``other[0]`` clear each other at its
                   corner; ``b`` as for ``"meet"``
    ``"bend"``     fold takes ``a`` mm of the main piece, ``b`` from each
                   side of where it forms
    ``"moved"``    fold forms ``a`` mm off its line, towards the panel
                   numbered ``other[0]``
    ``"stuck"``    what is folded inside fold reaches ``a`` mm into its
                   bend where it forms (``b`` with the fold on its line)
                   and the fold cannot move out of its way: it cannot
                   close as drawn. ``other`` the pieces whose leather was
                   found in the way (0 the main piece). The judge's notes
                   say what is then drawn into what
    ``"closes"``   fold closes flat over ``a`` pieces laid on its inside
                   and away from ``b`` laid on its outside (``other``)
    ``"reach"``    ``sheet`` reaches ``a`` mm into the bend of fold, from
                   one side; stopped ``b`` mm from the fold line it would
                   lie flat. ``at`` its point furthest in
    ``"cross"``    ``sheet`` goes round the bend of fold -- it has leather
                   bending on both sides of the line -- with ``a`` mm
                   more leather there than the room it has (inside the
                   bend; less than 0 outside: it needs that much more),
                   and is ``b`` mm shorter than the main piece across the
                   fold. The picture squeezes or stretches it
    ``"level"``    as ``"cross"``, but already as much shorter as the
                   bend takes inside, or as much longer as it needs
                   outside (within ``LEVEL``): shut, it comes level (the
                   picture does not slide it)
    ``"slit"``     ``sheet`` is parted along a run of ``a`` mm with no
                   bend between the two sides, as the folding draws it
                   (at the end of fold's hinge, say, or where two panels
                   that fold away from each other meet under it); ``at``
                   the middle of the run's longest stretch, ``other`` the
                   pieces lying across it there (0 the main piece, where
                   the run goes through its leather), ``b`` how deep the
                   sheet is drawn into itself, or the main piece into it,
                   there (0 for not at all). One note for each run
    ``"crossing"`` ``sheet`` lies over the place ``at`` where fold and
                   fold ``other[0]`` cross, and is cut or bent along both
                   there -- inside the main piece or past its edge: the
                   main piece alone passes through itself there, folded,
                   and the sheet is parted. ``b`` as for ``"slit"``
    ``"size"``     shut, the stack fits in a rectangle ``a`` by ``b`` mm
                   seen square to the still panel, the longer side first
    ``"shut"``     ... and is ``a`` mm thick
    ``"spine"``    the round of fold's bend, shut flat, is ``a`` mm
                   across, outside to outside
    ``"leather"``  a needle square to the still panel goes through ``a``
                   mm of leather at the most, ``b`` at the least, over the
                   still panel's flat leather
    ``"clash"``    the judge finds ``sheet`` and ``other[0]`` ``a`` mm
                   into each other at the deepest, first seen at the
                   moment ``b`` (a whole step, or a quarter, half or three
                   quarters of the way through one). A sheet with itself
                   is never so told where it is slit or over a crossing
                   (that note says it)
    ``"limit"``    ... the main piece in itself: a known limit of folding
                   one piece (folds that cross, a tray's corners), never
                   the fault of what is laid on it, and there with every
                   fold on its line
    ``"landing"``  where a fold has moved off its line: two parts of the
                   main piece (``other``, as the tags of their cells,
                   ``("panel", i)`` -- panel number ``i + 1`` -- or
                   ``("bend", fold)``) drawn ``a`` mm into each other,
                   first at the moment ``b``, that are only ``c`` mm into
                   each other with every fold on its line: the move's
                   doing, laid at the door of fold ``fold``

    Where a fold has moved, nothing drawn into anything by less than a
    moved fold is let keep (0.05 mm, ``foldsolid._TOUCH``) is told."""

    kind: str
    sheet: int = 0
    fold: int = -1
    a: float = 0.0
    b: float = 0.0
    other: tuple = ()
    at: Optional[Pt] = None
    c: float = 0.0


def sheet_of(tag) -> int:
    """The piece a cell or a face of a model is of, from its tag: 0 for
    the main piece, whose tags have two parts, else the sheet's number."""
    return tag[2] if tag is not None and len(tag) > 2 else 0


# -- polygons, in plan --------------------------------------------------------

def _outline(ring: Sequence[Pt]) -> List[Pt]:
    """A sheet's outline made ready, as ``foldsolid.model_outline`` makes
    the main piece's: counter-clockwise, no doubled points, and a
    flattened curve thinned to within ``_SMOOTH`` mm (further, up to half
    a mm, for an outline that would still have more than ``_BUSY``
    corners) -- but thinned inwards only (see ``_inward``). It starts at
    the corner it was given first, if that is one of those kept."""
    first = ring[0]
    ring, _flags = pc.tidy(list(ring), [0] * len(ring), 1e-6)
    ring = pc.ccw(ring)
    out = _inward(ring, _SMOOTH)
    tol = _SMOOTH
    while len(out) > _BUSY and tol < 0.5:
        tol *= 1.6
        out = _inward(ring, min(tol, 0.5))
    if first in out:
        k = out.index(first)
        out = out[k:] + out[:k]
    return out


def _inward(ring: Sequence[Pt], tol: float) -> List[Pt]:
    """A counter-clockwise outline thinned to within ``tol`` mm, inwards
    only: the thinned outline has no leather that the outline has not.

    Where the outline bulges, corners are dropped as ``polycut.simplify``
    drops them: the line between the corners that stay cuts them off.
    Round a hollow that line would run outside the leather, so there it
    is the edges that are dropped: the two that stay are carried on until
    they meet, a little way into the leather. Each bulge and each hollow
    is thinned by itself, and the corners where one ends and the next
    begins stay as they are: an outline thinned a second time stays as it
    is."""
    n = len(ring)
    if n <= 4 or tol <= 0.0:
        return list(ring)
    hollow = [_turn(ring[i - 1], ring[i], ring[(i + 1) % n]) < -1e-9
              for i in range(n)]
    if not any(hollow) or all(hollow):
        return _chords(ring, tol, True)
    # round from the corner where a hollow ends, run by run
    start = next(i for i in range(n) if hollow[i - 1] and not hollow[i])
    out: List[Pt] = []
    i = 0
    while i < n:
        j = i
        while j < n and hollow[(start + j) % n] == hollow[(start + i) % n]:
            j += 1
        if hollow[(start + i) % n]:
            # ... with the corner before it and the corner after it, whose
            # edges are the first and the last to carry on
            run = [ring[(start + t) % n] for t in range(i - 1, j + 1)]
            out += _tangents(run, tol)[1:-1]
        else:
            out += _chords([ring[(start + t) % n] for t in range(i, j)], tol)
        i = j
    return out if len(out) >= 3 else list(ring)


def _turn(a: Pt, b: Pt, c: Pt) -> float:
    """The sine of the turn an outline makes at ``b`` on its way from
    ``a`` to ``c``: more than 0 to the left."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    vx, vy = c[0] - b[0], c[1] - b[1]
    return (ux * vy - uy * vx) / (math.hypot(ux, uy) * math.hypot(vx, vy)
                                  or 1.0)


def _chords(pts: Sequence[Pt], tol: float, closed: bool = False) -> List[Pt]:
    """A run of corners thinned as ``polycut.simplify`` thins a ring: a
    corner stays if it stands more than ``tol`` mm off the line between
    its kept neighbours -- or at all to the left of it, where to drop it
    would add leather. The two ends of the run stay; of a ``closed`` ring
    its first corner and the one farthest from that."""
    n = len(pts)
    if n < 3:
        return list(pts)
    keep = [False] * n
    keep[0] = True
    if closed:
        x0, y0 = pts[0]
        far = max(range(n), key=lambda i: (pts[i][0] - x0) ** 2
                  + (pts[i][1] - y0) ** 2)
        if far == 0:
            return list(pts)
        keep[far] = True
        stack = [(0, far), (far, n)]
    else:
        keep[n - 1] = True
        stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        ax, ay = pts[i]
        bx, by = pts[j % n]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        most, mi = _GROW * math.sqrt(ll), -1   # the furthest to the left
        worst, wi = tol * tol, -1              # ... and the furthest off
        for k in range(i + 1, j):
            px, py = pts[k]
            if ll < 1e-18:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                c = dx * (py - ay) - dy * (px - ax)
                if c > most:
                    most, mi = c, k
                t = ((px - ax) * dx + (py - ay) * dy) / ll
                t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > worst:
                worst, wi = d, k
        k = mi if mi >= 0 else wi
        if k >= 0:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [q for q, kept in zip(pts, keep) if kept]


def _tangents(pts: Sequence[Pt], tol: float) -> List[Pt]:
    """A run of corners round a hollow of an outline (every one but the
    first and the last turns to the right), thinned by its edges: an edge
    stays if the corner its two kept neighbours would make without it --
    carried on until they meet -- would stand more than ``tol`` mm into
    the leather. The first edge and the last stay, so the run still
    leaves its first corner and reaches its last the way it did."""
    m = len(pts) - 1                   # edges: k runs pts[k] to pts[k + 1]
    if m < 3:
        return list(pts)
    way = []
    for k in range(m):
        dx, dy = pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]
        ln = math.hypot(dx, dy) or 1.0
        way.append((dx / ln, dy / ln))
    kept = {0, m - 1}
    met = {}                           # the corner two kept edges make
    stack = [(0, m - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        (ux, uy), (vx, vy) = way[a], way[b]
        px, py = pts[a + 1]
        qx, qy = pts[b]
        turn = ux * vy - uy * vx
        # Only edges that turn by less than a sixth of a turn between
        # them: the corner they make is then no further from the run than
        # it is from the lines of the edges it replaces, by a seventh.
        # Failing that, the edge between that runs most nearly the way
        # the two do together stays. (Either way the edge that stays is
        # chosen by where the lines lie, not by how many there are: that
        # is what leaves a thinned run as it is the next time.)
        between = None
        if turn < -1e-9 and ux * vx + uy * vy > 0.5:
            s = ((qx - px) * vy - (qy - py) * vx) / turn
            t = (ux * (qy - py) - uy * (qx - px)) / turn
            if s >= -1e-9 and t >= -1e-9:
                x, y = px + s * ux, py + s * uy
                far, between = max(
                    (abs(way[c][0] * (y - pts[c][1])
                         - way[c][1] * (x - pts[c][0])), c)
                    for c in range(a + 1, b))
                if far <= 0.86 * tol:
                    met[a, b] = (x, y)
                    continue
        if between is None:
            between = max(range(a + 1, b), key=lambda c: (
                way[c][0] * (ux + vx) + way[c][1] * (uy + vy)))
        kept.add(between)
        stack.append((a, between))
        stack.append((between, b))
    edges = sorted(kept)
    return [pts[0]] + [pts[b] if b == a + 1 else met[a, b]
                       for a, b in zip(edges, edges[1:])] + [pts[-1]]


def _frame(ring: Sequence[Pt]):
    """Where a sheet's grain is laid from: its first corner, the way its
    first edge runs and the way a quarter turn to the left of that."""
    x0, y0 = ring[0]
    for x1, y1 in ring[1:]:
        ln = math.hypot(x1 - x0, y1 - y0)
        if ln > 1e-9:
            ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
            return ((x0, y0), (ux, uy), (-uy, ux))
    return ((x0, y0),) + _SQUARE[1:]


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


def _reach(parts_a, parts_b, enough: float = math.inf) -> float:
    """How far two pieces, each given in convex parts, reach into each
    other (mm): the most any part of one does into any part of the other,
    0 for pieces that lie apart. The looking stops as soon as more than
    ``enough`` is found."""
    best = 0.0
    boxes = [pc.bounds(B) for B in parts_b]
    for A in parts_a:
        ax0, ay0, ax1, ay1 = pc.bounds(A)
        for B, (bx0, by0, bx1, by1) in zip(parts_b, boxes):
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                # Two parts reach into each other by no more than the
                # boxes round them do -- than one box would have to slide,
                # across or up, to come off the other: a pair that could
                # not better what is found already is not tried
                if best > 0.0 and (min(ax1 - bx0, bx1 - ax0) <= best
                                   or min(ay1 - by0, by1 - ay0) <= best):
                    continue
                d = _depth(A, B)
                if d > best:
                    best = d
                    if best > enough:
                        return best
    return best


def _lap(parts_a, ring_a, parts_b, ring_b) -> float:
    """How far one piece would have to slide to come off another (mm), at
    the least. For two convex pieces that is how far they reach into each
    other. An outline that is not convex is kept in convex parts, and the
    lines it is parted along are no edges of the leather: a narrow piece
    lying along one is not half on each side of it. So a piece that seems
    to be on by a sliver is looked at again, by the outlines themselves:
    a point that both pieces cover has to be slid out from under each,
    past the nearest edge of it."""
    best = _reach(parts_a, parts_b)
    if LAP < best < SLIVER and len(parts_a) + len(parts_b) > 2:
        for bit in _common(parts_a, parts_b, 0.0):
            for q in bit + [pc.centroid(bit)]:
                best = max(best, _to_ring(q, ring_a), _to_ring(q, ring_b))
            if best >= SLIVER:
                break
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


def _wider(pts: Sequence[Pt], least: float) -> bool:
    """Whether a convex polygon is wider than ``least`` mm where it is
    narrowest. Its area and the box round it settle most at once: it is
    at least as wide as its area over the length of that box's diagonal,
    and no wider than three times that."""
    x0, y0, x1, y1 = pc.bounds(pts)
    over = abs(pc.area(pts))
    span = least * math.hypot(x1 - x0, y1 - y0)
    if over > span:
        return True
    if 3.0 * over <= span:
        return False
    return _width(pts) > least


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


def _minus(bits, parts, least: float = LAP, boxes=None):
    """What is left of some convex polygons once the convex ``parts`` of a
    piece are taken out of them, as convex polygons. Whatever is left
    narrower than ``least`` is dropped. (``boxes`` are the boxes round the
    parts, for a caller that has them.)"""
    if boxes is None:
        boxes = [pc.bounds(B) for B in parts]
    out = []
    for P in bits:
        px0, py0, px1, py1 = pc.bounds(P)
        left = [P]
        for B, (bx0, by0, bx1, by1) in zip(parts, boxes):
            if not (px0 < bx1 and bx0 < px1 and py0 < by1 and by0 < py1):
                continue
            rests = []
            for rest in left:
                if not pc.convex_overlap(rest, B, least):
                    rests.append(rest)         # (and is not cut up for it)
                    continue
                x0, y0 = B[-1]
                for x1, y1 in B:
                    if len(rest) < 3:
                        break
                    # inside a counter-clockwise polygon is left of each
                    # edge
                    rest, cut = _split(rest, (x0, y0), (x1, y1))
                    if len(cut) >= 3 and _wider(cut, least):
                        rests.append(cut)
                    x0, y0 = x1, y1
            left = rests
            if not left:
                break
        out += left
    return out


def _common(bits, parts, least: float = LAP, any_will_do: bool = False,
            boxes=None):
    """The convex polygons that some convex polygons and the convex
    ``parts`` of a piece have in common, wider than ``least`` -- or, if
    ``any_will_do``, the first of them found. (``boxes`` as for
    ``_minus``.)"""
    out = []
    if boxes is None:
        boxes = [pc.bounds(B) for B in parts]
    for P in bits:
        px0, py0, px1, py1 = pc.bounds(P)
        for B, (bx0, by0, bx1, by1) in zip(parts, boxes):
            if px0 < bx1 and bx0 < px1 and py0 < by1 and by0 < py1:
                got = pc.clip_to_convex(P, B)
                if got and _wider(got, least):
                    out.append(got)
                    if any_will_do:
                        return out
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


def _on_edge(p: Pt, ring: Sequence[Pt], tol: float = HAIR) -> bool:
    """Whether a point is within ``tol`` mm of an outline."""
    px, py = p
    x0, y0 = ring[-1]
    for x1, y1 in ring:
        if not ((px < x0 - tol and px < x1 - tol)
                or (px > x0 + tol and px > x1 + tol)
                or (py < y0 - tol and py < y1 - tol)
                or (py > y0 + tol and py > y1 + tol)):
            dx, dy = x1 - x0, y1 - y0
            ll = dx * dx + dy * dy
            t = 0.0 if ll < 1e-18 else max(0.0, min(1.0, (
                (px - x0) * dx + (py - y0) * dy) / ll))
            ex, ey = px - x0 - t * dx, py - y0 - t * dy
            if ex * ex + ey * ey <= tol * tol:
                return True
        x0, y0 = x1, y1
    return False


def _past(parts, ground, tol: float = 0.01):
    """How far a piece stands past the edge of another, both in convex
    parts: ``(how far, the point of it furthest from the other's
    leather)``, or ``(0.0, None)`` for a piece wholly on the other.

    From a convex piece nothing is further than a corner: if the other
    piece is convex, the corners of this one give the answer. If it is
    not, what of this piece has nothing of the other under it is gone
    through in convex bits. A bit is furthest from one convex part of the
    other piece at one of its corners; so its corners give the answer
    wherever one part is the nearest to all of the bit, and it is cut
    smaller only where they do not -- over a notch or an inside corner of
    the other piece, where the furthest point may be along an edge of the
    bit or inside it -- until the answer is within ``tol`` mm."""
    best, at = LAP, None
    if len(ground) == 1:
        for part in parts:
            for q in part:
                if not pc.inside_convex(q, ground[0], 0.0):
                    d = _to_ring(q, ground[0])
                    if d > best:
                        best, at = d, q
        return (best, at) if at is not None else (0.0, None)
    todo: list = []                    # (how far it could be, ..., a bit)
    seen = 0

    def look(bit):
        nonlocal best, at, seen
        seen += 1
        off = [[0.0 if pc.inside_convex(q, G, 0.0) else _to_ring(q, G)
                for G in ground] for q in bit]
        # it is as far out as its corner furthest from every part ...
        low, k = max((min(row), k) for k, row in enumerate(off))
        if low > best:
            best, at = low, bit[k]
        # ... and no further than its corners are from any one part
        high = min(max(row[g] for row in off) for g in range(len(ground)))
        if high > best + tol:
            heapq.heappush(todo, (-high, seen, bit))

    for bit in _minus(list(parts), ground):
        look(bit)
    while todo and seen < 1500:
        high, _n, bit = heapq.heappop(todo)
        if -high <= best + tol:
            break
        # cut it in two across its longest reach
        p, q = max(((p, q) for p in bit for q in bit),
                   key=lambda pq: math.dist(*pq))
        mid = (0.5 * (p[0] + q[0]), 0.5 * (p[1] + q[1]))
        for half in _split(bit, mid, (mid[0] - (q[1] - p[1]),
                                      mid[1] + (q[0] - p[0]))):
            if len(half) >= 3 and abs(pc.area(half)) > 1e-12:
                look(half)
    return (best, at) if at is not None else (0.0, None)


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
    Each sheet is laid as it now is: its outline is made ready again if
    it has been changed since the sheet was made. Fills in what ``Sheet``
    says is worked out, and returns what there is to tell of the stack as
    ``Note``s: its thickest place, then every sheet that stands past the
    main piece's edge, stands clear of something under it, rests on a
    sliver, or lies on nothing."""
    ring = fs.model_outline(main_ring) if len(main_ring) >= 3 else []
    ground = _convex(ring)
    for s in sheets:
        s._ready()
    # for each sheet, the sheets laid before it on its face that it is over
    over: List[List[int]] = [[] for _s in sheets]
    for side in (1, -1):
        laid: List[int] = []                   # the sheets down on this face
        for k, s in enumerate(sheets):
            if s.side != side:
                continue
            # what is under it: (its top, which sheet)
            under = [(sheets[j].hi, j) for j in laid
                     if _reach(s.parts, sheets[j].parts, LAP) > LAP]
            over[k] = [j for _hi, j in under]
            s.lo = max((u[0] for u in under), default=0.0)
            s.hi = s.lo + s.thickness
            if under:
                # ... and of those, the ones it comes down on, by how far
                # it would have to slide to come off each
                held = sorted(((_lap(s.parts, s.ring, sheets[j].parts,
                                     sheets[j].ring), j)
                               for hi, j in under if hi >= s.lo - 1e-9),
                              reverse=True)
                s.rests = [j + 1 for _d, j in held]
                s.lap = held[0][0]
                s.rank = 1 + max(sheets[j].rank for _hi, j in under)
            else:
                s.lap = _lap(s.parts, s.ring, ground, ring)
                s.rests = [0] if s.lap > LAP else []
                s.rank = 1
            s.loose = not s.rests
            s.clear = _gaps(s, under, ground, sheets)
            s.past, s.past_at = _past(s.parts, ground) if ground \
                else (0.0, None)
            laid.append(k)
    notes = [_thickest(ground, max(0.05, float(main_thickness)), sheets,
                       over)]
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
    below = sorted(under, reverse=True)                   # the tallest first
    # what it could stand clear of, in that order: the main piece last
    lower = [j + 1 for hi, j in below if hi < s.lo - 1e-9]
    if s.lo > 1e-9 and ground:
        lower.append(0)
    gap = {j + 1: s.lo - hi for hi, j in below}
    gap[0] = s.lo
    boxes = {j: [pc.bounds(B) for B in sheets[j].parts] for _hi, j in below}
    clear = set()
    # Each is looked for under one convex part of the sheet after another,
    # until all are found (a sheet in many parts is seldom gone right
    # through)
    for part in s.parts:
        if len(clear) == len(lower):
            break
        bits = [part]                  # of the part, with nothing under yet
        n = 0                          # how many of ``lower`` are gone past
        for hi, j in below:
            if hi < s.lo - 1e-9:
                n += 1
                if j + 1 not in clear and _common(
                        bits, sheets[j].parts, HAIR, True, boxes[j]):
                    clear.add(j + 1)
            if all(k in clear for k in lower[n:]):
                break                  # nothing lower is still looked for
            bits = _minus(bits, sheets[j].parts, LAP, boxes[j])
            if not bits:
                break
        else:
            if 0 in lower and 0 not in clear \
                    and _common(bits, ground, HAIR, True):
                clear.add(0)
    return [(k, gap[k]) for k in reversed(lower) if k in clear]


def _thickest(ground, thickness: float, sheets, over) -> Note:
    """The thickest place of the flat stack, as a ``"thick"`` note: the
    most there is from the underside of one piece to the top of another
    that is over it. ``over`` has, for each sheet, the sheets laid before
    it on its own face that it is over."""
    def parts(piece):
        return sheets[piece - 1].parts if piece else ground

    known = {}

    def is_over(a, b):
        if a and b and sheets[a - 1].side == sheets[b - 1].side:
            return b - 1 in over[a - 1]
        if (a, b) not in known:
            known[a, b] = _reach(parts(a), parts(b), LAP) > LAP
        return known[a, b]

    live = [k for k, s in enumerate(sheets, 1) if s.parts]
    # every two pieces that could be over each other, with how far it
    # would be from the underside of the lower to the top of the upper:
    # across the main piece's own thickness for two on its two faces,
    # whether or not it is between them there
    pairs = [(thickness, 0, 0)] if ground else []
    for k in live:
        s = sheets[k - 1]
        pairs.append((s.thickness, k, k))
        if ground:
            pairs.append((thickness + s.hi, k, 0))
        pairs += [(s.hi - sheets[j].lo, k, j + 1) for j in over[k - 1]]
        pairs += [(thickness + s.hi + sheets[j - 1].hi, k, j)
                  for j in live if j < k and sheets[j - 1].side != s.side]
    pairs.sort(key=lambda pair: -pair[0])      # (the first of equals stays)
    tall, top, foot = next((pair for pair in pairs if pair[1] == pair[2]
                            or is_over(pair[1], pair[2])), (0.0, 0, 0))

    def biggest(bits):
        # (a place in very many bits is followed by its biggest only: it
        # is a point of it that is wanted, not all of it)
        return sorted(bits, key=pc.area)[-8:] if len(bits) > 8 else bits

    place = list(parts(top))
    if foot != top:
        place = _common(place, parts(foot), 0.0)
    place = biggest(place)
    # Of all that place, the part with most leather in it. Each other
    # piece is taken in or left out, the thickest first; what is left of
    # the place gets smaller as pieces are taken in.
    rest = sorted(((thickness if not k else sheets[k - 1].thickness, k)
                   for k in ([0] if ground else []) + live
                   if k != top and k != foot), key=lambda tk: -tk[0])
    best = [-1.0, place]
    tries = [200]

    def take(i, bits, leather, left):
        if leather > best[0]:
            best[:] = [leather, bits]
        if i == len(rest) or leather + left <= best[0] or tries[0] <= 0:
            return
        tries[0] -= 1
        t, k = rest[i]
        got = biggest(_common(bits, parts(k), LAP))
        if got:
            take(i + 1, got, leather + t, left - t)
            if sum(map(pc.area, got)) >= (1.0 - 1e-9) * sum(map(pc.area,
                                                                bits)):
                return                         # it is under all of the place
        take(i + 1, bits, leather, left - t)

    take(0, place, 0.0, sum(t for t, _k in rest))
    at = pc.centroid(max(best[1], key=pc.area)) if best[1] else None
    here = [0] if at is not None and _in(at, ground) else []
    leather = thickness if here else 0.0
    for side in (1, -1):
        for k in sorted((k for k in live if sheets[k - 1].side == side),
                        key=lambda k: sheets[k - 1].lo):
            if at is not None and _in(at, sheets[k - 1].parts):
                here.append(k)
                leather += sheets[k - 1].thickness
    return Note("thick", 0, -1, tall, leather, tuple(here), at)


# -- stitch holes through the layers ------------------------------------------

@dataclass
class Laid(fs.Lineup):
    """Two pieces of a stack that lie over each other, with stitch holes
    where they do: a ``foldsolid.Lineup`` with ``a`` and ``b`` the pieces
    (their numbers in the stack, 0 the main piece -- not panels, which a
    plain ``Lineup`` numbers from 1) and ``names`` what they are called.
    ``holes`` counts the holes of either that lie on the other's leather.
    ``lone`` of them come through the other piece where it has no hole
    anywhere near: ``through`` says how many of those are holes of ``a``
    and how many of ``b``. ``laid`` tells such a pair from two panels
    that a fold brings together, which have no ``laid`` at all: ask with
    ``getattr(found, "laid", False)``."""

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
    its cutouts, and more than ``HAIR`` from the edge of either: a hole
    on the very edge of a piece is not in it, whichever way that edge
    faces. There it meets the nearest hole of that piece if it is within
    ``tol`` mm of it, misses it if it is within ``near`` mm but not on
    it, and otherwise comes through leather with no hole in it, which is
    told and marked but is no fault. As in ``Layout.lineup`` a hole is
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
        ring = layers[i].ring
        if not pc.inside(p, ring) or _on_edge(p, ring):
            return False
        return not any(pc.inside(p, c) or _on_edge(p, c)
                       for c in layers[i].cutouts)

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
    ``layout``, ``outline``, ``name``, ``key`` and ``leather``, and
    ``sheet`` for the sheet of a piece's number. Anything else asked of
    it is the layout's own (``cells``, ``frames``, ``hinges`` ...).

    ``outline`` is the main piece's outline as ``fold_piece`` was given
    it; without one it is put together again from the layout's panels
    (``outline_of``). ``name``, ``key`` and ``leather`` are the main
    piece's, as a ``Sheet`` has its own.

    The sheets are settled as they are taken (``settle`` fills them in),
    and the stack is a picture of them as they then lie: the heights it
    shows are its own, so sheets that are laid again -- a piece moved,
    another order, another stack -- want a new ``FlatStack``. With no
    sheets it is the layout, fold for fold. With sheets it shows the
    stack flat only: no fold is made (``steps`` are all 0 and ``n_steps``
    is 0, so there is nothing to play; ``used`` and ``wrap`` are 0 and
    ``radius`` is the inside radius each fold was asked for, without the
    room a layout gives a fold for leather folded in before it), and a
    ``"flat"`` note says so. ``solid``, ``fold_marks``, ``panel_marks``
    and ``bounds3`` then take one thing more, ``apart``: every sheet is
    lifted that many mm for each layer of its ``rank``, away from the
    main piece, to show what lies on what. The marks sit on the outermost
    leather over them and say which piece that is (see ``fold_marks``).

    This class is scaffolding, and is superseded: a ``foldsolid.Layout``
    given the sheets (``fold_piece(..., sheets=...)``) lays them on the
    main piece and folds them with it, and offers everything this does
    under the same names. It is kept while the window still makes its
    model with it. It cuts nothing and borrows the layout's own helpers
    for a cell of the pattern and the marks on it.

    ``grain`` says which side of its leather the main piece is drawn
    from: 1 its grain side, as a piece shown alone is taken to be, or -1
    its flesh side -- the front of the model is then the flesh side of
    the main piece, and the view paints it so (``Scene.face_of``). The
    sheets say the same of themselves, each with its own ``grain``."""

    def __init__(self, layout, sheets: Sequence[Sheet] = (), outline=None,
                 name: str = "", key: str = "", leather: str = "",
                 grain: int = 1):
        self.layout = layout
        self.sheets = list(sheets)
        self.name = name
        self.key = key
        self.leather = leather
        self.grain = 1 if grain >= 0 else -1
        self.outline = fs.model_outline(outline) \
            if outline is not None and len(outline) >= 3 \
            else outline_of(layout)
        self.thickness = layout.thickness
        self.folds = layout.folds
        self.panels = layout.panels
        self.base = layout.base
        self.crosses = layout.crosses
        self.notes = settle(self.outline, layout.thickness, self.sheets)
        # each sheet as it was laid for this stack: its underside, its
        # top, its rank, its convex parts and where its grain is laid from
        self._laid = [(s.lo, s.hi, s.rank, s.parts, s.frame)
                      for s in self.sheets]
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
        # where each panel's number is written, on the front and on the
        # back: the layout's own spot, moved clear of the edges of the
        # pieces that lie on that face
        self._spots = []
        for panel in self.panels:
            both = []
            for side in (1, -1):
                rings = [s.ring for s in self.sheets
                         if s.side == side and s.parts]
                both.append(_roomy(panel, rings) if rings else panel.spot)
            self._spots.append(tuple(both))

    def __getattr__(self, name):
        # whatever else is asked of the stack is its layout's own
        if name == "layout":
            raise AttributeError(name)
        return getattr(self.layout, name)

    def sheet(self, piece: int) -> Optional[Sheet]:
        """The sheet a piece's number stands for: ``sheets[piece - 1]``,
        and None for the main piece (0) or a number no piece has."""
        if 0 < piece <= len(self.sheets):
            return self.sheets[piece - 1]
        return None

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

    def _span(self, k: int, apart: float) -> Tuple[float, float]:
        """Where the underside and the top of sheet ``k`` are in the
        model, along the normal out of the main piece's front."""
        lo, hi, rank = self._laid[k - 1][:3]
        lift = 0.5 * self.thickness + apart * rank
        if self.sheets[k - 1].side > 0:
            return lift + lo, lift + hi
        return -(lift + hi), -(lift + lo)

    def _outermost(self, side: int, p: Pt, apart: float):
        """The outermost leather at a point of the pattern, on one face of
        the main piece: ``(where its outer face is, which piece)``."""
        best = None
        for k, s in enumerate(self.sheets, 1):
            hi, parts = self._laid[k - 1][1], self._laid[k - 1][3]
            if s.side == side and (best is None or hi > best[0]) \
                    and _in(p, parts):
                best = (hi, k)
        if best is None:
            return side * 0.5 * self.thickness, 0
        z0, z1 = self._span(best[1], apart)
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
            z0, z1 = self._span(c.tag[2], apart)
            up, down = (FRONT, BACK) if s.grain > 0 else (BACK, FRONT)
            top = [(x, y, z1) for x, y in c.pts]
            bot = [(x, y, z0) for x, y in c.pts]
            marks = []
            for z in (z1, z0):
                def to3d(p, z=z):
                    return (p[0], p[1], z)
                marks.append(_ringed(fs._decals(c, to3d, check),
                                     blanks.get(c.index), to3d))
            # the grain is laid from the sheet's own corner, along its
            # own edge: it goes where the sheet goes
            (ox, oy), (ux, uy), (vx, vy) = self._laid[c.tag[2] - 1][4]
            ex, ey = (ux, uy, 0.0), (vx, vy, 0.0)
            faces = [
                Face(tuple(top), (0.0, 0.0, 1.0, z1), up, c.edges, marks[0],
                     c.tag, c.area, ((ox, oy, z1), ex, ey)),
                Face(tuple(bot[::-1]), (0.0, 0.0, -1.0, -z0), down,
                     c.edges_rev, marks[1], c.tag, c.area,
                     ((ox, oy, z0), ex, ey))]
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
                for laid in self._laid:
                    for part in laid[3]:
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
        on the front and the spot on the back are on. On a face with
        pieces on it the spot is moved, if it has to be, to where the
        number is clear of their edges: it is written on one piece, not
        across the edge of one on to the next."""
        if not self.sheets:
            return self.layout.panel_marks(fractions)
        out = []
        flat = self.layout.panel_marks([0.0] * len(self.folds))
        for (_front, _back, nn), (front, back) in zip(flat, self._spots):
            zf, kf = self._outermost(1, front, apart)
            zb, kb = self._outermost(-1, back, apart)
            out.append((front + (zf,), back + (zb,), nn, (kf, kb)))
        return out

    def bounds3(self, fractions: Sequence[float], apart: float = 0.0):
        """The box round the model as it stands: ``(low corner, high)``."""
        if not self.sheets:
            return self.layout.bounds3(fractions)
        lo, hi = self.layout.bounds3([0.0] * len(self.folds))
        lo, hi = list(lo), list(hi)
        for c in self._cells:
            for z in self._span(c.tag[2], apart):
                for x, y in c.pts:
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


def _roomy(panel, rings) -> Pt:
    """A place for a panel's number: the panel's own spot if no edge of
    the pieces whose outlines are ``rings``, nor the panel's own, is
    within ``_ROOM`` mm of it; else the nearest place on the panel, of
    those a few mm apart round the spot, that is so clear -- or failing
    any, the one that is clearest."""
    rings = [(pc.bounds(ring), ring) for ring in rings + [panel.pts]]

    def room(q):
        best = math.inf
        for (x0, y0, x1, y1), ring in rings:
            if x0 - _ROOM < q[0] < x1 + _ROOM \
                    and y0 - _ROOM < q[1] < y1 + _ROOM:
                best = min(best, _to_ring(q, ring))
        return best

    at = panel.spot
    most = room(at)
    if most >= _ROOM:
        return at
    x, y = at
    for step in range(1, 41):
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, 1),
                       (1, -1), (-1, -1)):
            q = (x + dx * step, y + dy * step)
            if not _in(q, panel.parts):
                continue
            here = room(q)
            if here >= _ROOM:
                return q
            if here > most:
                most, at = here, q
    return at


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


# -- the stack folded ---------------------------------------------------------
#
# A ``foldsolid.Layout`` given sheets folds them with the main piece. What
# follows is the part of that which is about sheets alone: where each one
# rides, where it bends, the room it asks of a fold, and what is told of
# it. The layout calls these as it is built (``Layout._stack``); each takes
# the layout and works on what the layout keeps of its sheets:
# ``_seated``, each sheet's parts as they are first laid on the panels;
# ``_regions``, for each sheet ``[(panel, outline, flags)]``, its parts
# and the panel each rides on as the bends now are; and ``sheet_cells``,
# the cells they are drawn in.
#
# What a sheet does is decided place by place, the same for every layer
# there. Leather lying over the main piece's leather goes with the
# leather under it. Leather lying over nothing of the main piece -- what
# stands past its edge -- goes with the leather it is joined to, or that
# it rests on; and past the end of a hinge it goes on round the bend if
# it is joined to leather that bends there. Wherever that parts a piece
# with no bend between the two sides, the piece is said to be slit there
# (``_tears``).

def seat(layout) -> List[list]:
    """Each sheet cut along the main piece's fold lines, as the layout has
    them -- a fold that has moved is followed -- and wherever it lies over
    two panels that part, and each part given the panel it rides on: for
    each sheet ``[(panel, outline, flags)]``.

    A sheet is cut along a fold line over every stretch of it that runs
    along a hinge, and the edge it is cut along is flagged with the fold,
    as the main piece's own are. A part that still lies over two panels
    of the main piece -- across the slit beside a flap cut into a body,
    say -- is cut along the edge of one of them, until each part lies
    over one. A part over the main piece's leather rides on the panel
    under it; a part over none rides on the panel on its side of a fold
    line it is cut along, else on what it rests on there (``_follow``),
    else on the nearest panel.

    The layout keeps what this finds in ``_seated``, for each sheet
    ``[[panel, outline, flags, how]]`` with ``how`` "on" (over the main
    piece's leather), "cut" (on its side of a fold) or "free" (going by
    what it rests on): each round of the folding starts from it
    (``cut``)."""
    layout._seated = []
    for s in layout.sheets:
        regions = [(list(s.ring), [pc.OUTLINE] * len(s.ring))] \
            if s.parts else []
        for fi, line in enumerate(layout.lines):
            if line is None:
                continue
            a, e, _ln = line
            runs = [(h.lo, h.hi) for h in layout.hinges if h.fold == fi]

            def want(lo, hi, runs=runs):
                return any(min(hi, q) - max(lo, p) > 1e-6 for p, q in runs)

            regions = [piece for pts, flags in regions
                       for piece in pc.cut(pts, flags, a, e, want, fi)]
        mine = []
        for pts, flags in regions:
            for q, f in _parted(layout, pts, flags):
                over = _over(layout, q, f)
                if over:
                    mine.append([max(over, key=over.get), q, f, "on"])
                    continue
                panel = _flagged(layout, q, f)
                mine.append([panel, q, f, "free" if panel is None
                             else "cut"])
        layout._seated.append(mine)
    for k in range(1, len(layout.sheets) + 1):
        _follow(layout, layout._seated, k)
    layout._seen = [[(p, tuple(q)) for p, q, _f, _how in mine]
                    for mine in layout._seated]
    return [[(p, q, f) for p, q, f, _how in mine] for mine in layout._seated]


def _over(layout, pts, flags=None, least: float = HAIR) -> Dict[int, float]:
    """The panels of the main piece that a part of a sheet lies over,
    each with how much of its leather (mm2) is under the part: those it
    covers more than ``least`` mm wide."""
    if flags is None:
        flags = [pc.OUTLINE] * len(pts)
    box = pc.bounds(pts)
    parts = None
    out: Dict[int, float] = {}
    for panel in layout.panels:
        b = panel.box
        if not (box[0] < b[2] and b[0] < box[2] and box[1] < b[3]
                and b[1] < box[3]):
            continue
        if parts is None:
            parts = [q for q, _f in pc.convex_parts(pts, flags)]
        got = _common(parts, panel.parts, least)
        if got:
            out[panel.index] = sum(abs(pc.area(g)) for g in got)
    return out


def _parted(layout, pts, flags, rounds: int = 24):
    """A part of a sheet cut wherever it lies over two panels of the main
    piece that are not hinged there: along an edge of one of them, each
    cut leaving every piece over fewer of them and none over none. (A
    part that cannot be parted so is left whole: it then rides on the
    panel it lies most over, and the judge says what comes of that.)"""
    todo = [(pts, flags, _over(layout, pts, flags))]
    out = []
    for _round in range(rounds):
        if not todo:
            break
        P, F, over = todo.pop()
        if len(over) <= 1:
            out.append((P, F))
            continue
        done = None
        for p in sorted(over):
            panel = layout.panels[p]
            n = len(panel.pts)
            for k in range(n):
                if panel.flags[k] != pc.OUTLINE:
                    continue
                q, r = panel.pts[k], panel.pts[(k + 1) % n]
                ln = math.dist(q, r)
                if ln < 1e-9:
                    continue
                e = ((r[0] - q[0]) / ln, (r[1] - q[1]) / ln)

                def want(lo, hi, ln=ln):
                    return min(hi, ln) - max(lo, 0.0) > 1e-6

                pieces = pc.cut(P, F, q, e, want, pc.INNER)
                if len(pieces) < 2:
                    continue
                overs = [_over(layout, a, b) for a, b in pieces]
                if all(o and set(o) <= set(over) for o in overs) and any(
                        len(o) < len(over) for o in overs):
                    done = [(a, b, o) for (a, b), o in zip(pieces, overs)]
                    break
            if done:
                break
        if done is None:
            out.append((P, F))
        else:
            todo += done
    return out + [(P, F) for P, F, _o in todo]


def _flagged(layout, pts, flags) -> Optional[int]:
    """The panel on the side of a fold line that a part of a sheet was cut
    along (None for a part cut along none): the one the hinge nearest
    along the line has there. Of the edges cut along fold lines, the one
    nearest a hinge along its line goes: a part cut along two lines, one
    of them only where it runs on past the end of its hinge, goes by the
    other."""
    n = len(pts)
    best = None
    for k in range(n):
        fi = flags[k]
        if fi < 0:
            continue
        a, e, _ln = layout.lines[fi]
        p, q = pts[k], pts[(k + 1) % n]
        sp = (p[0] - a[0]) * e[0] + (p[1] - a[1]) * e[1]
        sq = (q[0] - a[0]) * e[0] + (q[1] - a[1]) * e[1]
        lo, hi = min(sp, sq), max(sp, sq)
        for h in layout.hinges:
            if h.fold == fi:
                off = max(0.0, max(h.lo, lo) - min(h.hi, hi))
                # (it runs along the line: the part lies to its left)
                panel = h.left if sq > sp else h.right
                if best is None or off < best[0]:
                    best = (off, panel)
    return None if best is None else best[1]


def _follow(layout, regions, k: int) -> None:
    """Give each part of sheet ``k`` that lies over no leather of the main
    piece and was cut along no fold (``how`` "free") the panel of what it
    rests on there: of the parts of the sheets laid before it on its face,
    the one it lies most over. A part over none of them goes with the
    nearest panel. ``regions`` are the sheets' parts as ``_seated`` has
    them, those before ``k`` already given theirs."""
    s = layout.sheets[k - 1]
    for part in regions[k - 1]:
        if part[3] != "free" or len(part) > 4 and part[4]:
            continue
        mine = [q for q, _f in pc.convex_parts(part[1], part[2])]
        box = pc.bounds(part[1])
        best, most = None, 1e-9
        for j in range(k - 1):
            if layout.sheets[j].side != s.side:
                continue
            for other in regions[j]:
                panel, pts, flags = other[:3]
                b = pc.bounds(pts)
                if not (box[0] < b[2] and b[0] < box[2] and box[1] < b[3]
                        and b[1] < box[3]):
                    continue
                lap = sum(abs(pc.area(g)) for g in _common(
                    mine, [q for q, _f in pc.convex_parts(pts, flags)]))
                if lap > most:
                    best, most = panel, lap
        if best is None:
            c = pc.centroid(part[1])
            best = min(layout.panels, key=lambda p: _to_ring(c, p.pts)).index
        part[0] = best


def _band(h, half: float, lo: float, hi: float) -> List[Pt]:
    """The band of a bend, ``half`` mm each side of the fold line, from
    ``lo`` to ``hi`` mm along the hinge's line."""
    a, e = h.a, h.e
    c2 = (-e[1], e[0])
    return pc.ccw([(a[0] + k * e[0] + d * c2[0], a[1] + k * e[1] + d * c2[1])
                   for k, d in ((lo, -half), (hi, -half), (hi, half),
                                (lo, half))])


def _in_band(regions, h, half: float, lo: float, hi: float) -> bool:
    """Whether a sheet (its ``regions``) has leather in the band of a
    bend between ``lo`` and ``hi`` mm along the hinge, on its two
    panels."""
    rect = _band(h, half, lo, hi)
    for panel, pts, _flags in regions:
        if panel in (h.parent, h.child):
            part = pc.clip_to_convex(pts, rect)
            if len(part) >= 3 and abs(pc.area(part)) > 1e-9:
                return True
    return False


_FAR = 1e4          # (mm) as far past the end of a hinge as anything lies


def ends(layout, h, half: float):
    """What lies flat past each end of hinge ``h``, whose bend is ``half``
    mm each side of its line: for each end, the main piece's own leather
    in the band past it that does not bend with this fold, in convex
    parts -- a body wider than the flap folded on it goes on flat there.
    Whatever of a sheet lies over it lies flat with it; the rest of the
    band past the end is where the bend may go on (see ``cut``)."""
    out = []
    for k_end, sign in ((h.k0, -1.0), (h.k1, 1.0)):
        lo, hi = sorted((k_end, k_end + sign * _FAR))
        rect = _band(h, half, lo, hi)
        rb = pc.bounds(rect)
        flat = []
        for c in layout.cells:
            if c.hinge is not None and layout.tree[c.hinge].fold == h.fold:
                continue
            b = c.box
            if not (b[0] < rb[2] and rb[0] < b[2] and b[1] < rb[3]
                    and rb[1] < b[3]):
                continue
            got = pc.clip_to_convex(c.pts, rect)
            if len(got) >= 3 and abs(pc.area(got)) > 1e-9:
                flat.append(pc.ccw(got))
        out.append(flat)
    return tuple(out)


def _lifts(layout, s: Sheet) -> Tuple[float, float]:
    """Where the two faces of a sheet are, out of the main piece's middle
    along the normal out of its front."""
    ht = 0.5 * layout.thickness
    if s.side > 0:
        return ht + s.lo, ht + s.hi
    return -(ht + s.hi), -(ht + s.lo)


def _at_end(pts, h, k_end: float, sign: float, half: float, flat) -> bool:
    """Whether a part of a sheet runs on past the end of a hinge, within
    its bend, over leather that does not lie flat there (``flat``): the
    part goes on from what bends along the hinge into what lies past its
    end."""
    c2 = (-h.e[1], h.e[0])
    # (a hair past the end: an edge on the end itself is not crossed)
    k = k_end + sign * 1e-3
    o = (h.a[0] + k * h.e[0], h.a[1] + k * h.e[1])
    spans = [(max(p, -half), min(q, half)) for p, q in pc.spans(pts, o, c2)]
    spans = [(p, q) for p, q in spans if q - p > 1e-6]
    if not spans:
        return False
    for m in flat:
        cut = [(max(p, -half), min(q, half)) for p, q in pc.spans(m, o, c2)]
        for m0, m1 in cut:
            if m1 - m0 <= 1e-9:
                continue
            spans = [bit for p, q in spans
                     for bit in ((p, min(q, m0)), (max(p, m1), q))
                     if bit[1] - bit[0] > 1e-6]
    return bool(spans)


def _clipped(pts, flags, rect, flat) -> List[List[Pt]]:
    """What of a part of a sheet lies in ``rect`` (convex) and over none
    of ``flat``, in convex parts."""
    rb = pc.bounds(rect)
    box = pc.bounds(pts)
    if not (box[0] < rb[2] and rb[0] < box[2] and box[1] < rb[3]
            and rb[1] < box[3]):
        return []
    got = []
    for q, _f in pc.convex_parts(pts, flags):
        bit = pc.clip_to_convex(q, rect)
        if len(bit) >= 3 and abs(pc.area(bit)) > 1e-9:
            got.append(pc.ccw(bit))
    return _minus(got, flat) if got and flat else got


def cut(layout) -> bool:
    """Cut every sheet into the cells it is drawn in (``sheet_cells``):
    what lies flat on a panel rides on it, rigid with it; what lies in
    the band of a bend is cut into the main piece's own strips and laid
    on its rungs, moved along their normals to its own heights -- the
    same strips, at the same angles, so that every layer is concentric
    with the main piece at every moment of the folding. A sheet's strip
    is cut again along the fold at every corner inside it, so that each
    cell stays convex once bent (``foldsolid._strip_parts``).

    Each round starts from the parts as they were seated (``seat``). A
    part over no leather of the main piece goes with what it rests on
    (``_follow``). Then, past each end of each hinge, place by place and
    the same for every layer: what lies over the main piece's own flat
    leather there (``ends``) lies flat with it; a part that goes on past
    the end over anything else -- one on the panel that swings, one that
    runs on from the bend along the hinge, or one lying with either of
    those -- goes on round the bend there: it is cut along the fold line
    past the end, and what of it lies over no leather of the main piece
    goes to the panel on its side of the line. Any other part stays
    flat past the end. Returns whether any part rides on another panel
    than it did the round before: the room each fold needs is then to be
    found again."""
    regions = [[list(p) + [set()] for p in mine] for mine in layout._seated]
    for k in range(1, len(layout.sheets) + 1):
        _follow(layout, regions, k)
    flats: Dict[tuple, list] = {}
    for hi, h in enumerate(layout.tree):
        if not layout._halves[hi]:
            continue
        half = 0.5 * layout.used[h.fold]
        for end, flat in enumerate(ends(layout, h, half)):
            flats[hi, end] = flat
            _goes_on(layout, regions, hi, h, end, half, flat)
    # (what was resting on a part that has gone over goes with it)
    for k in range(1, len(layout.sheets) + 1):
        _follow(layout, regions, k)
    layout._regions = [[(p, q, f) for p, q, f, _how, _go in mine]
                       for mine in regions]
    seen = [[(p, tuple(q)) for p, q, _f, _how, _go in mine]
            for mine in regions]
    moved = seen != layout._seen
    layout._seen = seen
    cells = []
    index = len(layout.cells)
    for k, s in enumerate(layout.sheets, 1):
        by_panel: Dict[int, list] = {}
        for panel, pts, flags, _how, go in regions[k - 1]:
            by_panel.setdefault(panel, []).append((list(pts), list(flags),
                                                   go))
        strips = []
        for hi, h in enumerate(layout.tree):
            if not layout._halves[hi]:
                continue
            m, w = layout._halves[hi], layout.used[h.fold]
            for panel, side in ((h.parent, -1.0), (h.child, 1.0)):
                stay = []
                for pts, flags, go in by_panel.get(panel, ()):
                    way = tuple(flats[hi, end] if (hi, end) in go else None
                                for end in (0, 1))
                    keep, got = fs._cut_zone([(pts, flags)], h, side,
                                             0.5 * w, m, way)
                    stay += [(q, f, go) for q, f in keep]
                    strips += [(panel, hi, j, poly) for j, poly in got]
                if panel in by_panel:
                    by_panel[panel] = stay
        mine = [fs._Cell2(cp, cf, panel)
                for panel, polys in by_panel.items()
                for pts, flags, _go in polys
                for cp, cf in pc.convex_parts(pts, flags)]
        for panel, hi, j, (pts, flags) in strips:
            h = layout.tree[hi]
            w = layout.used[h.fold]
            step = w / (2 * layout._halves[hi])
            for cp, cf in fs._strip_parts(pts, flags, h, rider=True):
                c = fs._Cell2(cp, cf, panel)
                c.hinge, c.strip = hi, j
                c.place = [fs._strip_place(p, h, w, step, j) for p in cp]
                mine.append(c)
        mine = [c for c in mine if c.area > 1e-9 and len(c.pts) >= 3]
        fs._sides(mine, s.thickness, fs.sharp_corners(s.ring))
        low, high = _lifts(layout, s)
        kinds = (FRONT, BACK) if s.grain > 0 else (BACK, FRONT)
        for c in mine:
            c.sheet, c.low, c.high, c.kinds = k, low, high, kinds
            c.frame = s.frame
            c.tag = ("panel", c.panel, k) if c.hinge is None \
                else ("bend", layout.tree[c.hinge].fold, k)
            c.index = index
            index += 1
        fs._turns(mine)
        cells += mine
    layout.sheet_cells = cells
    return moved


def _goes_on(layout, regions, hi: int, h, end: int, half: float,
             flat) -> None:
    """Settle, at one end of hinge ``h``, which parts of the sheets go on
    round its bend past that end (see ``cut``): mark them in each part's
    last entry with ``(hi, end)``, and cut them along the fold line past
    the end, each bit over no leather of the main piece going to the
    panel on its side."""
    k_end, sign = ((h.k0, -1.0), (h.k1, 1.0))[end]
    lo, top = sorted((k_end, k_end + sign * _FAR))
    rect = _band(h, half, lo, top)
    # the parts on the hinge's two panels with leather in the band past
    # the end that does not lie flat there
    found = []
    for k, mine in enumerate(regions):
        for part in mine:
            if part[0] not in (h.parent, h.child):
                continue
            free = _clipped(part[1], part[2], rect, flat)
            if not free:
                continue
            goes = part[0] == h.child or _at_end(part[1], h, k_end, sign,
                                                 half, flat)
            found.append([part, free, goes])
    # ... and what lies with what goes on, goes on: one answer for every
    # layer at a place
    grew = True
    while grew:
        grew = False
        for one in found:
            if one[2]:
                continue
            if any(other[2] and any(pc.convex_overlap(A, B, LAP)
                                    for A in one[1] for B in other[1])
                   for other in found):
                one[2] = grew = True
    going = [part for part, _free, goes in found if goes]
    if not going:
        return
    ids = {id(part) for part in going}
    e = h.e

    def want(p0, p1):
        mid = 0.5 * (p0 + p1)
        if sign * (mid - k_end) <= 0.0:
            return False
        q = (h.a[0] + mid * e[0], h.a[1] + mid * e[1])
        return not any(pc.inside_convex(q, m, 1e-9) for m in flat)

    for mine in regions:
        out = []
        for part in mine:
            if id(part) not in ids:
                out.append(part)
                continue
            panel, pts, flags, how, go = part
            go = go | {(hi, end)}
            pieces = pc.cut(pts, flags, h.a, e, want, h.fold)
            if len(pieces) == 1:
                out.append([panel, pts, flags, how, go])
                continue
            for q, f in pieces:
                c = pc.centroid(max((p for p, _f in pc.convex_parts(q, f)),
                                    key=lambda p: abs(pc.area(p))))
                u = (c[1] - h.a[1]) * e[0] - (c[0] - h.a[0]) * e[1]
                al = (c[0] - h.a[0]) * e[0] + (c[1] - h.a[1]) * e[1]
                where, kind = panel, how
                if sign * (al - k_end) > 0.0 and not _over(layout, q, f):
                    where = h.child if u > 0.0 else h.parent
                    kind = "cut"
                out.append([where, q, f, kind, go])
        mine[:] = out


def ride(layout) -> bool:
    """Raise the least radius of each fold to the top of the tallest pile
    that rides inside its bend (``Layout._floor``): leather on the inside
    of a bend needs at least its own height of radius there, or it is
    turned inside out. Returns whether any fold asks for more than it had
    -- the bend is then wider, and may catch more."""
    grew = False
    for c in layout.sheet_cells:
        if c.hinge is None:
            continue
        fi = layout.tree[c.hinge].fold
        s = layout.sheets[c.sheet - 1]
        if s.side != layout.folds[fi].direction:
            continue
        if s.hi > layout._floor.get(fi, (0.0,))[0] + 1e-12:
            layout._floor[fi] = (s.hi, (c.sheet, 0))
        if s.hi > layout.radius[fi] + 1e-9:
            grew = True
    return grew


def bodies(layout) -> list:
    """The bodies of leather that may come down on each other as the
    stack folds: each panel of the main piece, and each sheet's part on
    each panel, as ``(panel, convex parts, low, high, piece)`` with
    ``low`` and ``high`` the heights it lies between, out of the main
    piece's middle along its front."""
    ht = 0.5 * layout.thickness
    out = [(p.index, p.parts, -ht, ht, 0) for p in layout.panels]
    for k, (s, regions) in enumerate(zip(layout.sheets, layout._regions),
                                     1):
        low, high = _lifts(layout, s)
        for panel, pts, flags in regions:
            parts = [q for q, _f in pc.convex_parts(pts, flags)]
            if parts:
                out.append((panel, parts, low, high, k))
    return out


def corner_need(theta: float, ha: float, hb: float, da: float,
                db: float) -> float:
    """The inside radius that keeps a pile ``ha`` mm high on one panel of
    a fold, ending ``da`` mm short of the bend, clear of one ``hb`` mm
    high ending ``db`` short of it on the other, when the fold stands
    open at ``theta`` radians.

    With both piles right up to the bend this is ``(h_min + c h_max) /
    (1 + c)``, ``c = -cos theta``: past a right angle the two piles lean
    in over each other. Up to a right angle they meet only if they are
    taller than the bend is wide. A pile that stands back from the bend
    needs that much less."""
    sn, cs = math.sin(theta), math.cos(theta)
    if sn < 1e-9:
        return 0.5 * (ha + hb) if cs < 0.0 else 0.0
    if cs >= 0.0:
        cands = [hb - (da + db * cs) / sn, ha - (db + da * cs) / sn]
        if 1.0 - cs > 1e-9:
            cands += [(ha - db * sn) / (1.0 - cs),
                      (hb - da * sn) / (1.0 - cs)]
        return max(0.0, min(cands))
    c = -cs
    return max(0.0, min(ha + c * hb - db * sn, hb + c * ha - da * sn)
               / (1.0 + c))


def corner_room(layout, hinges, theta: float, asked: float):
    """The least inside radius, not below ``asked``, at which what lies on
    the inside of a fold that stands open at ``theta`` radians does not
    meet across the corner: ``(radius, (piece, piece))``.

    Every inside sheet's part on one of the fold's two panels is set
    against every one on the other whose stretch along the fold it
    shares, and each against the bare panel across (no height, right up
    to the bend). How far a part stands back from the bend shrinks as
    the radius widens the bend, so the least radius is found by halving
    the step (a plain repeat leaves a hair of overlap)."""
    t = layout.thickness
    pairs = []
    for h in hinges:
        way = layout.folds[h.fold].direction
        a, e = h.a, h.e
        sides = {}
        for panel, sgn in ((h.parent, -1.0), (h.child, 1.0)):
            got = [(0.0, None, h.k0, h.k1, 0)]
            for k, (s, regions) in enumerate(zip(layout.sheets,
                                                 layout._regions), 1):
                if s.side != way:
                    continue
                for pan, pts, _flags in regions:
                    if pan != panel:
                        continue
                    al = [(p[0] - a[0]) * e[0] + (p[1] - a[1]) * e[1]
                          for p in pts]
                    d = min(sgn * ((p[1] - a[1]) * e[0]
                                   - (p[0] - a[0]) * e[1]) for p in pts)
                    got.append((s.hi, max(0.0, d), min(al), max(al), k))
            sides[panel] = got
        for ha, da, a0, a1, ka in sides[h.parent]:
            for hb, db, b0, b1, kb in sides[h.child]:
                if ka or kb:
                    if min(a1, b1) - max(a0, b0) > LAP:
                        pairs.append((ha, da, hb, db, ka, kb))

    def need(r):
        half = 0.5 * theta * (r + 0.5 * t)
        best, who = 0.0, ()
        for ha, da, hb, db, ka, kb in pairs:
            got = corner_need(theta, ha, hb,
                              0.0 if da is None else max(0.0, da - half),
                              0.0 if db is None else max(0.0, db - half))
            if got > best:
                best, who = got, (ka, kb)
        return best, who

    got, who = need(asked)
    if got <= asked + 1e-12:
        return asked, who
    lo, hi = asked, max(asked, need(1e9)[0])
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if need(mid)[0] > mid:
            lo = mid
        else:
            hi = mid
    return hi, need(hi)[1] or who


def dress(layout) -> None:
    """Put each sheet's stitch holes, cut-outs and marks on its cells.
    Its holes are numbered on from the main piece's in ``layout.holes``
    (``hole_sheet`` says whose each is), as ``FlatStack`` numbers
    them."""
    first = len(layout.holes)
    for k, s in enumerate(layout.sheets, 1):
        mine = [c for c in layout.sheet_cells if c.sheet == k]
        for i, (x, y, dia) in enumerate(s.holes):
            c = _cell_at(mine, x, y)
            if c is not None:
                c.dots.append((x, y, dia, first + i))
        first += len(s.holes)
        layout.holes += [tuple(h) for h in s.holes]
        layout.hole_sheet += [k] * len(s.holes)
        layout.hole_panel += [-1] * len(s.holes)
        layout.hole_flat += [False] * len(s.holes)
        for ring in s.cutouts:
            rb = pc.bounds(ring)
            for c in mine:
                b = c.box
                if b[0] < rb[2] and rb[0] < b[2] and b[1] < rb[3] \
                        and rb[1] < b[3]:
                    part = pc.clip_to_convex(ring, c.pts)
                    if len(part) >= 3 and abs(pc.area(part)) > 1e-6:
                        c.polys.append(part)
        budget = 6000                          # strokes, as for a lone piece
        for kind, pts in s.marks:
            for p, q in zip(pts, pts[1:]):
                budget -= 1
                if budget < 0:
                    break
                for c in mine:
                    bit = pc.clip_segment_to_convex(p, q, c.pts)
                    if bit is not None and math.dist(*bit) > 1e-6:
                        c.lines.append((kind, bit[0], bit[1]))


LEVEL = 0.25        # a crossing sheet this near (mm) its length comes level


def _across(ring, h) -> Tuple[float, float]:
    """How far an outline reaches each way across a hinge's fold line
    (mm, from the line, the child's way positive)."""
    us = [(p[1] - h.a[1]) * h.e[0] - (p[0] - h.a[0]) * h.e[1] for p in ring]
    return min(us), max(us)


def fold_notes(layout) -> List[Note]:
    """What there is to tell of the stack folded, as ``Note``s (numbers
    only; see ``Note`` for each kind): for each fold, why it bends as it
    does and the leather it takes; the pieces that reach into its bend,
    or go round it, or are slit at the end of it; a fold moved off its
    line; which way a fold that closes flat closes; and for the whole,
    its size shut and the leather through it."""
    out: List[Note] = []
    t = layout.thickness
    for k, f in enumerate(layout.folds):
        hinges = [h for h in layout.tree if h.fold == k]
        if not layout.steps[k] or not hinges:
            continue
        theta = math.radians(min(max(f.angle, 0.0), 180.0))
        why = layout.why[k]
        if why is not None:
            rule, one, other = why[0], why[1], why[2] if len(why) > 2 else 0
            if rule == "ride":
                out.append(Note("ride", one, k, layout.radius[k],
                                layout.sheets[one - 1].hi))
            else:
                out.append(Note(rule, one, k, layout.radius[k],
                                2.0 * layout.radius[k], (other,)))
        w = layout.used[k]
        out.append(Note("bend", 0, k, w, 0.5 * w))
        if layout.shift[k]:
            out.append(Note("moved", 0, k, layout.shift[k],
                            other=(layout.shift_toward[k],)))
        if k in layout._left_in:
            out.append(Note("stuck", 0, k, *layout._left_in[k],
                            other=tuple(sorted(layout._reaching.get(k, ())))))
        if theta >= fs._FLAT:
            inside = set()
            outside = set()
            for n, regions in enumerate(layout._regions, 1):
                if any(panel in (h.parent, h.child) for h in hinges
                       for panel, _p, _f in regions):
                    s = layout.sheets[n - 1]
                    (inside if s.side == f.direction else outside).add(n)
            if inside or outside:
                out.append(Note("closes", 0, k, float(len(inside)),
                                float(len(outside)), tuple(sorted(outside))))
        half = 0.5 * w
        for n, (s, regions) in enumerate(zip(layout.sheets,
                                             layout._regions), 1):
            bent = [c for c in layout.sheet_cells if c.sheet == n
                    and c.hinge is not None
                    and layout.tree[c.hinge].fold == k]
            if not bent:
                continue
            if _round(layout, n, k, bent):
                # it goes round the bend: theta d less room inside, more
                # outside, d from the main piece's middle to its own
                d = 0.5 * t + s.lo + 0.5 * s.thickness
                over = theta * d if s.side == f.direction else -theta * d
                lo, hi = min(_across(s.ring, h)[0] for h in hinges), \
                    max(_across(s.ring, h)[1] for h in hinges)
                main = [_across(layout.outline, h) for h in hinges]
                m0, m1 = min(a for a, _b in main), max(b for _a, b in main)
                short = (m1 - m0) - (hi - lo)
                # (already as much shorter as the bend takes inside, or as
                # much longer as it needs outside, and its ends where the
                # main piece's are, give or take that)
                level = (abs(short - over) <= LEVEL
                         and abs(m1 - hi) <= abs(over) + 0.5
                         and abs(lo - m0) <= abs(over) + 0.5)
                out.append(Note("level" if level else "cross", n, k, over,
                                short))
                continue
            # it only reaches into the bend, from one side: how far, and
            # its point furthest in
            far, at = None, None
            for c in bent:
                for p in c.pts:
                    h = layout.tree[c.hinge]
                    u = abs((p[1] - h.a[1]) * h.e[0]
                            - (p[0] - h.a[0]) * h.e[1])
                    if far is None or u < far:
                        far, at = u, p
            if far is not None and half - far > HAIR:
                out.append(Note("reach", n, k, half - far, half, at=at))
    out += _slits(layout)
    out += _crossings(layout)
    out += _shut(layout)
    return out


def _tears(layout) -> List[tuple]:
    """Where a sheet is parted with no bend between the two sides: two of
    its cells that meet along a seam in the pattern, but are carried
    apart as the stack folds -- one riding on a panel and the other on
    another, or on a bend it does not go on from there. Each is found by
    laying both cells' sides of the seam where the folding puts them, at
    two moments (every fold half way, and every fold made), and seeing
    them come apart. ``[(sheet, fold, [(p, q)])]``: the seams parted,
    gathered by sheet and fold into runs that meet end to end; ``fold``
    the fold whose folding parts them."""
    if not layout.n_steps:
        return []
    poses = []
    for share in (0.5, 1.0):
        fr = [share if s else 0.0 for s in layout.steps]
        frames = layout.frames(fr)
        poses.append((frames, layout._arcs(frames, fr)))
    by: Dict[int, list] = {}
    for c in layout.sheet_cells:
        by.setdefault(c.sheet, []).append(c)
    found: Dict[tuple, list] = {}
    for k, mine in by.items():
        for i, a in enumerate(mine):
            for b in mine[i + 1:]:
                if (a.hinge, a.panel if a.hinge is None else None) == \
                        (b.hinge, b.panel if b.hinge is None else None):
                    continue           # (they move as one)
                ba, bb = a.box, b.box
                if (ba[0] > bb[2] + 1e-6 or bb[0] > ba[2] + 1e-6
                        or ba[1] > bb[3] + 1e-6 or bb[1] > ba[3] + 1e-6):
                    continue
                for p, q, flag in _seams(a, b):
                    if any(_parts_at(layout, a, b, x, fr)
                           for x in (p, q, ((p[0] + q[0]) / 2,
                                            (p[1] + q[1]) / 2))
                           for fr in poses):
                        fold = _parting(layout, a, b, flag)
                        found.setdefault((k, fold), []).append((p, q))
    out = []
    for (k, fold), segs in sorted(found.items()):
        for run in _runs(segs):
            out.append((k, fold, run))
    return out


def _seams(a, b):
    """The stretches of edge two cells of the pattern share, inside the
    leather: ``[(p, q, flag)]``, flag that of ``a``'s edge."""
    out = []
    na, nb = len(a.pts), len(b.pts)
    for i in range(na):
        if a.flags[i] == pc.OUTLINE:
            continue
        p, q = a.pts[i], a.pts[(i + 1) % na]
        ln = math.dist(p, q)
        if ln < 1e-9:
            continue
        e = ((q[0] - p[0]) / ln, (q[1] - p[1]) / ln)
        for j in range(nb):
            r, s = b.pts[j], b.pts[(j + 1) % nb]
            # on the same line?
            if abs((r[1] - p[1]) * e[0] - (r[0] - p[0]) * e[1]) > 1e-7 \
                    or abs((s[1] - p[1]) * e[0] - (s[0] - p[0]) * e[1]) \
                    > 1e-7:
                continue
            tr = (r[0] - p[0]) * e[0] + (r[1] - p[1]) * e[1]
            ts = (s[0] - p[0]) * e[0] + (s[1] - p[1]) * e[1]
            lo, hi = max(0.0, min(tr, ts)), min(ln, max(tr, ts))
            if hi - lo > 1e-6:
                out.append(((p[0] + lo * e[0], p[1] + lo * e[1]),
                            (p[0] + hi * e[0], p[1] + hi * e[1]),
                            a.flags[i]))
    return out


def _parts_at(layout, a, b, x: Pt, pose) -> bool:
    """Whether the folding puts the point ``x`` of the pattern in two
    places as a point of cell ``a`` and as one of cell ``b`` (of one
    sheet), at a pose ``(frames, arcs)``."""
    frames, arcs = pose
    mid = 0.5 * (a.low + a.high)
    pa = _where(layout, a, x, frames, arcs, mid)
    pb = _where(layout, b, x, frames, arcs, mid)
    return math.dist(pa, pb) > 1e-6


def _where(layout, c, x: Pt, frames, arcs, lift: float):
    """Where a point of the pattern lies, ``lift`` mm out of the main
    piece's middle, as a point of cell ``c``."""
    if c.hinge is None:
        return fs.place(frames[c.panel], x, lift)
    h = layout.tree[c.hinge]
    w = layout.used[h.fold]
    step = w / (2 * layout._halves[c.hinge])
    q = fs._strip_place(x, h, w, step, c.strip)
    arc = arcs[c.hinge]
    rungs = fs._rungs(arc, lift)
    return fs._strip_point(q, rungs[c.strip], rungs[c.strip + 1], arc[0])


def _parting(layout, a, b, flag: int) -> int:
    """The fold whose folding parts two cells of a sheet: the fold of a
    bend either is on, else that of the edge between them, else the
    first made of the folds between their two panels."""
    for c in (a, b):
        if c.hinge is not None:
            return layout.tree[c.hinge].fold
    if flag >= 0:
        return flag
    up = {}
    for h in layout.tree:
        up[h.child] = h
    path = []
    for start in (a.panel, b.panel):
        seen, p = [], start
        while p in up:
            seen.append(up[p])
            p = up[p].parent
        path.append(seen)
    ids = {id(h) for h in path[0]} ^ {id(h) for h in path[1]}
    between = [h for h in path[0] + path[1] if id(h) in ids]
    if not between:
        return -1
    return min(between, key=lambda h: (layout.steps[h.fold] or 10 ** 6,
                                       h.fold)).fold


def _runs(segs):
    """Seams gathered into runs that meet end to end."""
    groups = []
    for seg in segs:
        joined = [g for g in groups
                  if any(math.dist(p, q) < 1e-6 for s in g for p in s
                         for q in seg)]
        for g in joined:
            groups.remove(g)
        groups.append(sum(joined, []) + [seg])
    return groups


def _slits(layout) -> List[Note]:
    """``"slit"`` notes, one for each run of seams a sheet is parted
    along (``_tears``): how long it is, the middle of its longest
    stretch, and the pieces that lie across it there -- the main piece
    among them where the slit runs through its leather. The layout keeps
    the runs in ``_slit_runs``, beside the notes, for the judge."""
    out = []
    layout._slit_runs = []
    for k, fold, run in _tears(layout):
        length = sum(math.dist(p, q) for p, q in run)
        p, q = max(run, key=lambda s: math.dist(*s))
        at = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        across = set()
        for a, b in run:
            x = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if pc.inside(x, layout.outline) \
                    and _to_ring(x, layout.outline) > HAIR:
                across.add(0)
            for j, s in enumerate(layout.sheets, 1):
                if j != k and s.parts and _in(x, s.parts) \
                        and _to_ring(x, s.ring) > HAIR:
                    across.add(j)
        out.append(Note("slit", k, fold, length, 0.0,
                        tuple(sorted(across)), at))
        layout._slit_runs.append((k, fold, run))
    return out


def _round(layout, n: int, fold: int, bent) -> bool:
    """Whether sheet ``n`` goes round the bend of a fold rather than only
    reaching into it, by the leather of it that really bends there
    (``bent``, its cells in the fold's bend): it has leather in the bend
    on both sides of the fold line along one hinge, or lies flat on both
    of the hinge's panels where it bends."""
    for hi, h in enumerate(layout.tree):
        if h.fold != fold:
            continue
        m = layout._halves[hi]
        strips = {c.strip >= m for c in bent if c.hinge == hi}
        if len(strips) == 2:
            return True
        if strips and all(any(c.sheet == n and c.hinge is None
                              and c.panel == p
                              for c in layout.sheet_cells)
                          for p in (h.parent, h.child)):
            return True
    return False


def _crossings(layout) -> List[Note]:
    """``"crossing"`` notes: the sheets that lie over a place where two
    fold lines of the main piece cross, and are cut or bent along both
    there -- inside the main piece, or past it where the bends go on
    through what stands past its edge. Such a sheet is parted there, as
    the main piece alone passes through itself where its own folds
    cross."""
    live = sorted({h.fold for h in layout.hinges if layout.steps[h.fold]})
    spots = []
    for i, f in enumerate(live):
        for g in live[i + 1:]:
            (ax, ay), (ex, ey), _l = layout.lines[f]
            (bx, by), (fx, fy), _m = layout.lines[g]
            den = ex * fy - ey * fx
            if abs(den) < 1e-9:
                continue
            s = ((bx - ax) * fy - (by - ay) * fx) / den
            spots.append(((ax + s * ex, ay + s * ey), f, g))
    out = []
    for n, s in enumerate(layout.sheets, 1):
        if not s.parts:
            continue
        mine = [c for c in layout.sheet_cells if c.sheet == n]
        for p, f, g in spots:
            if not (_in(p, s.parts) or _to_ring(p, s.ring) < 1.0):
                continue
            if _worked(layout, mine, f, p) and _worked(layout, mine, g, p):
                out.append(Note("crossing", n, f, other=(g,), at=p))
                break
    return out


def _worked(layout, cells, fold: int, p: Pt, near: float = 1.0) -> bool:
    """Whether some of ``cells`` are bent by a fold, or cut along its line,
    within ``near`` mm of the point ``p``."""
    for c in cells:
        b = c.box
        if not (b[0] - near <= p[0] <= b[2] + near
                and b[1] - near <= p[1] <= b[3] + near):
            continue
        if c.hinge is not None:
            if layout.tree[c.hinge].fold == fold and (
                    pc.inside_convex(p, c.pts, 1e-9)
                    or _to_ring(p, c.pts) <= near):
                return True
            continue
        n = len(c.pts)
        for k in range(n):
            if c.flags[k] == fold and _to_ring(
                    p, [c.pts[k], c.pts[(k + 1) % n]]) <= near:
                return True
    return False


def _shut(layout) -> List[Note]:
    """The stack folded right up: ``"size"`` (the length and the width of
    the smallest rectangle it fits in, seen square to the still panel,
    the longer first), ``"shut"`` (how thick it is), a ``"spine"`` for
    each fold that closes flat (how wide the round of its bend is,
    outside to outside) and ``"leather"`` (the most and the least
    leather a needle goes through, square to the still panel, over the
    still panel's flat leather: ``_through``)."""
    if not layout.n_steps:
        return []
    posed = layout._posed(layout.fractions(float(layout.n_steps)))
    cells = [cell for _c, cell in posed]
    pts = [v for c in cells for v in c.verts]
    length, width = _boxed(_hull([(p[0], p[1]) for p in pts]))
    lo, hi = min(p[2] for p in pts), max(p[2] for p in pts)
    out = [Note("size", 0, -1, length, width), Note("shut", 0, -1, hi - lo)]
    for k, f in enumerate(layout.folds):
        if layout.steps[k] and math.radians(min(f.angle, 180.0)) >= \
                fs._FLAT and any(h.fold == k for h in layout.tree):
            out.append(Note("spine", 0, k, 2.0 * (layout.radius[k]
                                                  + layout.thickness)))
    got = _through(layout, posed)
    if got is not None:
        out.append(Note("leather", 0, -1, *got))
    return out


def _hull(pts) -> List[Pt]:
    """The convex hull of some points, counter-clockwise."""
    pts = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    low: List[Pt] = []
    for p in pts:
        while len(low) >= 2 and turn(low[-2], low[-1], p) <= 1e-12:
            low.pop()
        low.append(p)
    up: List[Pt] = []
    for p in reversed(pts):
        while len(up) >= 2 and turn(up[-2], up[-1], p) <= 1e-12:
            up.pop()
        up.append(p)
    return low[:-1] + up[:-1]


def _boxed(hull) -> Tuple[float, float]:
    """The sides of the smallest rectangle that holds a convex outline,
    the longer first: it lies along one of the outline's edges."""
    if len(hull) < 2:
        return 0.0, 0.0
    best = None
    n = len(hull)
    for i in range(n):
        p, q = hull[i], hull[(i + 1) % n]
        ln = math.dist(p, q)
        if ln < 1e-9:
            continue
        ux, uy = (q[0] - p[0]) / ln, (q[1] - p[1]) / ln
        a = [x * ux + y * uy for x, y in hull]
        b = [-x * uy + y * ux for x, y in hull]
        sides = (max(a) - min(a), max(b) - min(b))
        if best is None or sides[0] * sides[1] < best[0] - 1e-9:
            best = (sides[0] * sides[1], sides)
    if best is None:
        return 0.0, 0.0
    return max(best[1]), min(best[1])


def _through(layout, posed):
    """``(most, least)``: the leather a needle square to the still panel
    goes through (mm), over the still panel's own flat leather, in the
    shut stack ``posed`` (``Layout._posed``). None for no such leather.

    The still panel's flat leather is cut by the outline, seen from
    above, of every cell over it, into pieces each under one set of
    cells: every place with a different set of layers over it is one of
    them. A cell lying level adds its thickness to a piece wholly; one
    lying aslant adds what a needle meets of it, which changes across
    the piece as a plane does -- most and least at a corner."""
    base = layout.base
    pieces = []
    for c, cell in posed:
        if c.sheet == 0 and c.panel == base and c.hinge is None:
            foot = _hull([(v[0], v[1]) for v in cell.verts])
            if len(foot) >= 3:
                pieces.append((foot, 0.0, ()))
    if not pieces:
        return None
    levels: Dict[tuple, list] = {}
    tilted = []
    for _c, cell in posed:
        foot = _hull([(v[0], v[1]) for v in cell.verts])
        if len(foot) < 3 or abs(pc.area(foot)) < 1e-9:
            continue                   # (it stands on edge: no needle in it)
        level = all(abs(f.plane[2]) < 1e-9 or abs(f.plane[2]) > 1 - 1e-9
                    for f in cell.faces)
        if level:
            zs = [v[2] for v in cell.verts]
            key = tuple(foot)
            if key not in levels:
                levels[key] = [foot, 0.0]
            levels[key][1] += max(zs) - min(zs)
        else:
            tilted.append((foot, cell))
    feet = [(foot, mm, None) for foot, mm in levels.values()] + \
        [(foot, 0.0, cell) for foot, cell in tilted]
    for foot, mm, cell in feet:
        fb = pc.bounds(foot)
        nxt = []
        for poly, got, over in pieces:
            pb = pc.bounds(poly)
            if not (pb[0] < fb[2] and fb[0] < pb[2] and pb[1] < fb[3]
                    and fb[1] < pb[3]) or not pc.convex_overlap(poly, foot,
                                                                1e-9):
                nxt.append((poly, got, over))
                continue
            inner = pc.clip_to_convex(poly, foot)
            if len(inner) >= 3 and abs(pc.area(inner)) > 1e-9:
                nxt.append((pc.ccw(inner), got + mm,
                            over + ((cell,) if cell is not None else ())))
            nxt += [(rest, got, over)
                    for rest in _minus([poly], [foot], 1e-7)]
        pieces = nxt
    most, least = 0.0, math.inf
    for poly, got, over in pieces:
        if over:
            cx, cy = pc.centroid(poly)
            spots = [(x + (cx - x) * 1e-6, y + (cy - y) * 1e-6)
                     for x, y in poly] + [(cx, cy)]
            values = [got + sum(_chord(c, x, y) for c in over)
                      for x, y in spots]
        else:
            values = [got]
        for v in values:
            if v > 1e-6:
                most, least = max(most, v), min(least, v)
    return (most, least) if most > 0.0 else None


def _chord(cell, x: float, y: float) -> float:
    """How much of the upright line through ``(x, y)`` lies in a convex
    cell (mm)."""
    lo, hi = -math.inf, math.inf
    for f in cell.faces:
        nx, ny, nz, d = f.plane
        rest = d - nx * x - ny * y
        if abs(nz) < 1e-12:
            if rest < -1e-9:
                return 0.0
            continue
        z = rest / nz
        if nz > 0.0:
            hi = min(hi, z)
        else:
            lo = max(lo, z)
    return max(0.0, hi - lo)


def judge(layout) -> List[Note]:
    """What the judge (``clash``) finds in the stack as it folds, as notes
    that name the two pieces and say how deep. It looks at each whole
    step of the folding and at three moments inside each (a quarter, half
    and three quarters of the way through it); inside a step only at two
    cells the step moves against each other (``_bodies``).

    ``"clash"``: two pieces, at their deepest, with the moment they are
    first seen so (``b``: 1.0 the end of step 1, 0.5 half way through
    it). What a sheet does to itself, or the main piece to it, where the
    sheet is noted slit or lies over a crossing of folds is that note's
    to say: its ``b`` is made the deepest of it, and no clash is given.
    ``"limit"``: the main piece in itself -- a known limit of folding one
    piece, crossing folds and a tray's corners, no fault of what is put
    on it. Where a fold has moved off its line, the main piece's own two
    parts that the piece folded with every fold on its line does not
    have as deep (the layout's ``_drawn``) are a ``"landing"`` instead:
    the move's doing. Never per frame: a layout runs it once, when it is
    made. The notes of the stack (``layout.notes``) must hold its slit
    and crossing notes already."""
    from . import clash
    n = layout.n_steps
    moments = [0.0]
    for step in range(1, n + 1):
        moments += [step - 0.75, step - 0.5, step - 0.25, float(step)]
    drawn = getattr(layout, "_drawn", None) if any(layout.shift) else None
    runs = getattr(layout, "_slit_runs", [])
    spots = [(note.sheet, note.at, note.fold, note.other[0])
             for note in layout.notes if note.kind == "crossing"]
    found: Dict[tuple, list] = {}
    moved: Dict[tuple, list] = {}
    there: Dict[tuple, float] = {}
    # (where a fold has moved, what it is left into other leather by no
    # more than a moved fold is let keep, the hair it is moved to within,
    # is no finding)
    least = fs._TOUCH if any(layout.shift) else clash.JUDGE

    def order(tag):
        return (tag[0] != "panel", tag[1])

    for t in moments:
        fr = layout.fractions(t)
        posed = layout._posed(fr)
        cells = [cell for _c, cell in posed]
        pick = None
        if t != int(t):
            body = _bodies(layout, int(math.ceil(t)), [c for c, _x in posed])

            def pick(i, j, body=body):
                return body[i] != body[j]

        lined = None
        for deep, i, j in clash.pairs(cells, least, pick):
            ca, cb = posed[i][0], posed[j][0]
            a, b = sorted((ca.sheet, cb.sheet))
            if a == b == 0 and drawn is not None:
                ta, tb = sorted((ca.tag, cb.tag), key=order)
                if lined is None:
                    lined = drawn._posed(fr, sheets=False)
                was = max((clash.depth(x, y) for c2, x in lined
                           if c2.tag == ta for d2, y in lined
                           if d2.tag == tb and y is not x), default=0.0)
                if was < deep - fs._TOUCH:
                    got = moved.setdefault((ta, tb), [deep, t, was])
                    if deep > got[0]:
                        got[0], got[2] = deep, was
                    continue
            elif b and a in (0, b):
                key = _parted_there(layout, b, [c for c in (ca, cb)
                                                if c.sheet == b],
                                    runs, spots)
                if key is not None:
                    there[key] = max(there.get(key, 0.0), deep)
                    continue
            got = found.setdefault((a, b), [deep, t])
            got[0] = max(got[0], deep)
    # the depths at slits and crossings, into their notes
    count = {"slit": 0, "crossing": 0}
    for m, note in enumerate(layout.notes):
        if note.kind in count:
            deep = there.get((note.kind, count[note.kind]))
            count[note.kind] += 1
            if deep is not None:
                layout.notes[m] = note._replace(b=deep)
    out = [Note("limit" if pair == (0, 0) else "clash", pair[0], -1,
                deep, t, (pair[1],))
           for pair, (deep, t) in sorted(found.items(),
                                         key=lambda kv: -kv[1][0])]
    for (ta, tb), (deep, t, was) in sorted(moved.items(),
                                            key=lambda kv: -kv[1][0]):
        out.append(Note("landing", 0, layout._blame(ta, tb), deep, t,
                        (ta, tb), c=was))
    return out


def _bodies(layout, step: int, cells) -> List[object]:
    """For each of some cells of the model (bits of the pattern), the body
    it moves with in step ``step`` of the folding: the panels joined by
    hinges of folds made in other steps move as one, with the bends of
    those folds and what rides on them; the bend of a fold made in this
    step moves on its own."""
    link = list(range(len(layout.panels)))

    def find(x):
        while link[x] != x:
            link[x] = link[link[x]]
            x = link[x]
        return x

    for h in layout.tree:
        if layout.steps[h.fold] != step:
            link[find(h.parent)] = find(h.child)
    out = []
    for c in cells:
        if c.hinge is not None \
                and layout.steps[layout.tree[c.hinge].fold] == step:
            out.append(("bend", c.hinge))
        else:
            out.append(find(c.panel))
    return out


def _parted_there(layout, k: int, cells, runs, spots):
    """Whether cells of sheet ``k`` that the judge finds in leather lie
    where the sheet is noted slit, or over a crossing of folds: the
    note's kind and its place among the notes of that kind, else None."""
    for r, (sheet, fold, run) in enumerate(runs):
        if sheet != k:
            continue
        reach = 0.5 * layout.used[fold] + 1.0 if fold >= 0 else 1.0
        if all(any(_near(c.pts, p, q) <= reach for p, q in run)
               for c in cells):
            return ("slit", r)
    for r, (sheet, at, f, g) in enumerate(spots):
        if sheet != k:
            continue
        reach = 0.5 * (layout.used[f] + layout.used[g]) + 1.0
        if all(_near(c.pts, at, at) <= reach for c in cells):
            return ("crossing", r)
    return None


def _near(poly, p: Pt, q: Pt) -> float:
    """How far a convex polygon is from the stretch ``p``-``q`` (mm)."""
    if pc.inside_convex(p, poly, 1e-9) or pc.inside_convex(q, poly, 1e-9):
        return 0.0
    if pc.clip_segment_to_convex(p, q, poly) is not None:
        return 0.0
    best = min(_to_ring(p, poly), _to_ring(q, poly))
    for x in poly:
        best = min(best, _to_ring(x, [p, q]))
    return best


# -- marks on the stack folded ------------------------------------------------

def _outer(layout, side: int, test, apart: float):
    """The outermost sheet on one face of the main piece that ``test``
    (a sheet's number) says lies there: ``(how far its outer face is
    from the main piece's face, which piece)`` -- ``(0, 0)`` for the main
    piece's own face."""
    best = (0.0, 0)
    for k, s in enumerate(layout.sheets, 1):
        if s.side == side and s.parts and test(k):
            out = s.hi + apart * s.rank
            if out > best[0]:
                best = (out, k)
    return best


def fold_marks(layout, fractions, apart: float = 0.0):
    """``Layout.fold_marks`` for a stack: see there. A stretch is drawn
    on the outermost leather over the fold line, the sheets that go round
    the bend there."""
    frames = layout.frames(fractions)
    arcs = layout._arcs(frames, fractions)
    ht = 0.5 * layout.thickness
    out: Dict[int, list] = {}
    bends: Dict[int, set] = {}
    for c in layout.sheet_cells:
        if c.hinge is not None:
            bends.setdefault(layout.tree[c.hinge].fold, set()).add(c.sheet)
    for hi, h in enumerate(layout.tree):
        arc = arcs[hi]
        a, e = h.a, h.e
        if arc is None:
            # (a fold with no bend: on the panels' faces, at the line)
            nn, e1, mid0 = frames[h.parent][3], None, None
        else:
            m = len(arc[6]) // 2
            mid0, nn = arc[6][m], arc[7][m]
            e1 = arc[0]
        # where what lies over the line changes, along it
        cuts = {h.k0, h.k1}
        p0 = (a[0] + h.k0 * e[0], a[1] + h.k0 * e[1])
        p1 = (a[0] + h.k1 * e[0], a[1] + h.k1 * e[1])
        for s in layout.sheets:
            for part in s.parts:
                bit = pc.clip_segment_to_convex(p0, p1, part)
                if bit is not None:
                    cuts.update((q[0] - a[0]) * e[0] + (q[1] - a[1]) * e[1]
                                for q in bit)
        ks = sorted(k for k in cuts if h.k0 - 1e-9 <= k <= h.k1 + 1e-9)
        runs = []
        for k0, k1 in zip(ks, ks[1:]):
            if k1 - k0 <= 1e-9:
                continue
            km = 0.5 * (k0 + k1)
            q = (a[0] + km * e[0], a[1] + km * e[1])
            on = tuple(_outer(layout, side, lambda k: k in bends.get(
                h.fold, ()) and _in(q, layout.sheets[k - 1].parts), apart)
                for side in (1, -1))
            if runs and runs[-1][2:] == on:
                runs[-1] = (runs[-1][0], k1) + on
            else:
                runs.append((k0, k1) + on)
        for k0, k1, (zf, kf), (zb, kb) in runs:
            ends = []
            for lift in (ht + zf, -(ht + zb)):
                for k in (k0, k1):
                    if e1 is None:
                        fr = frames[h.parent]
                        ends.append(fs.place(fr, (a[0] + k * e[0],
                                                  a[1] + k * e[1]), lift))
                    else:
                        ends.append((mid0[0] + lift * nn[0] + k * e1[0],
                                     mid0[1] + lift * nn[1] + k * e1[1],
                                     mid0[2] + lift * nn[2] + k * e1[2]))
            out.setdefault(h.fold, []).append(tuple(ends) + (nn, (kf, kb)))
    return out


def panel_marks(layout, fractions, apart: float = 0.0):
    """``Layout.panel_marks`` for a stack: see there. On a face with
    pieces on it the spot is moved, if it has to be, to where the number
    is clear of their edges (``_roomy``)."""
    frames = layout.frames(fractions)
    ht = 0.5 * layout.thickness
    out = []
    for panel in layout.panels:
        fr = frames[panel.index]
        ends, on = [], []
        for side in (1, -1):
            rings = [pts for s, regions in zip(layout.sheets,
                                               layout._regions)
                     if s.side == side
                     for pan, pts, _f in regions if pan == panel.index]
            spot = _roomy(panel, rings) if rings else panel.spot

            def there(k, spot=spot):
                return any(pan == panel.index and pc.inside(spot, pts)
                           for pan, pts, _f in layout._regions[k - 1])

            z, k = _outer(layout, side, there, apart)
            ends.append(fs.place(fr, spot, side * (ht + z)))
            on.append(k)
        out.append((ends[0], ends[1], fr[3], tuple(on)))
    return out


class StackCheck(LaidCheck):
    """What ``stack_check`` found: a ``LaidCheck`` with ``readings`` as
    well (see there)."""

    __slots__ = ("readings",)

    def __init__(self, found: LaidCheck):
        super().__init__()
        for name in ("findings", "state", "lands", "rings", "blanks",
                     "landed", "passed"):
            setattr(self, name, getattr(found, name))
        self.readings: Dict[int, Tuple[int, int, bool]] = {}


@dataclass
class Faced(fs.Lineup):
    """Two pieces of a stack that the folding brings face to face, with
    nothing between them, and stitch holes in both where they do: a
    ``foldsolid.Lineup`` with ``a`` and ``b`` the pieces (0 the main
    piece), ``names`` what they are called and ``panels`` the numbers of
    the main piece's panels each lies on. ``laid`` is False: they are not
    laid on each other, the fold brings them together."""

    names: Tuple[str, str] = ("", "")
    laid: bool = False
    panels: Tuple[int, int] = (0, 0)


def stack_check(layout, tol: float = 0.75, near: float = 4.0):
    """The stitch holes of a stack: what ``Layout.lineup`` gives for one.

    First as the stack lies flat, through its layers (``laid_check``). A
    piece with holes that goes round a bend (a ``"cross"`` or ``"level"``
    note) is read shut as well, with its holes shifted by its true
    length: at every fold it goes round, half that note's length further
    from the fold line on each side (inside the bend; nearer, outside),
    the shifts of all its folds added up. The shut reading has every such
    piece shifted together; then, piece by piece, the reading in which
    more of its holes meet is kept, the others held in the reading they
    have. ``readings`` says, for each such piece, ``(how many of its
    holes meet as it lies, how many shut, whether the shut reading is
    kept)``, over all its folds. Then the pieces the folding brings face
    to face with nothing between are compared as ``Layout.lineup``
    compares two panels, within the same reach (it is not made longer by
    the piles): ``Faced`` findings. The spots it marks are sorted by cell
    for ``solid``. A ``StackCheck``."""
    own = layout._own_holes
    main = _Layer(layout.outline, layout.cutouts, layout.holes[:own],
                  getattr(layout, "name", ""))
    layers = [main] + list(layout.sheets)
    first = own
    starts = {}
    for k, s in enumerate(layout.sheets, 1):
        starts[k] = first
        first += len(s.holes)
    # each piece that goes round a bend, with its holes read shut
    shut: Dict[int, _Layer] = {}
    for k, s in enumerate(layout.sheets, 1):
        folds = [note for note in layout.notes if note.sheet == k
                 and note.kind in ("cross", "level")]
        if not s.holes or not folds:
            continue
        moved = []
        for x, y, dia in s.holes:
            dx = dy = 0.0
            for note in folds:
                line = layout.lines[note.fold]
                if line is None:
                    continue
                (ax, ay), (ex, ey), _ln = line
                u = (y - ay) * ex - (x - ax) * ey
                way = 0.5 * note.a * (1.0 if u >= 0.0 else -1.0)
                dx, dy = dx - way * ey, dy + way * ex
            moved.append((x + dx, y + dy, dia))
        shut[k] = _Layer(s.ring, s.cutouts, moved, s.name)

    def read(chosen):
        return laid_check([shut[k] if k in chosen else layer
                           for k, layer in enumerate(layers)], tol, near)

    def met(found, k):
        mine = range(starts[k], starts[k] + len(layout.sheets[k - 1].holes))
        return sum(1 for i in mine if found.state.get(i) == fs.MEETS)

    chosen = set(shut)
    check = read(chosen)
    readings = {}
    for k in sorted(shut):
        flat = read(chosen - {k})
        now, then = met(flat, k), met(check, k)
        better = then > now
        readings[k] = (now, then, better)
        if not better:
            chosen.discard(k)
            check = flat
    check = StackCheck(check)
    check.readings = readings
    _faced(layout, check, tol, near)
    for found, rings in ((check.landed, check.rings),
                         (check.passed, check.blanks)):
        for piece, spots in found.items():
            cells = [c for c in layout.sheet_cells if c.sheet == piece] \
                if piece else layout.cells
            for x, y, dia in spots:
                c = _cell_at(cells, x, y)
                if c is not None:
                    rings.setdefault(c.index, []).append((x, y, dia))
    return check


def _faced(layout, check, tol: float, near: float) -> None:
    """Add to ``check`` the pieces that the folding brings face to face
    with nothing between (see ``stack_check``): each body of leather
    (``bodies``) against each on another panel, folded right up, where
    their planes lie together and their faces are no further apart than
    ``Layout.lineup`` lets two panels be. A hole of either that meets or
    misses one of the other is given that, as the flat reading does."""
    n = len(layout.folds)
    if not layout.n_steps:
        return
    frames = layout.frames([1.0] * n)
    t = layout.thickness
    reach = max(3.0 * t, 3.0) + 0.01 - t       # face to face
    names = [getattr(layout, "name", "")] + [s.name for s in layout.sheets]
    own = layout._own_holes
    flat_cells: Dict[int, list] = {}
    for c in layout.sheet_cells:
        if c.hinge is None:
            flat_cells.setdefault((c.sheet, c.panel), []).append(c)
    found = []
    for panel, parts, low, high, piece in bodies(layout):
        if piece:
            first = own + sum(len(s.holes) for s in layout.sheets[:piece - 1])
            cells = flat_cells.get((piece, panel), [])
            holes = [(first + i, (x, y)) for i, (x, y, _d)
                     in enumerate(layout.sheets[piece - 1].holes)
                     if _cell_at(cells, x, y) is not None]
        else:
            holes = [(i, layout.holes[i][:2]) for i in range(own)
                     if layout.hole_panel[i] == panel
                     and layout.hole_flat[i]]
        found.append((panel, parts, low, high, piece, holes))
    for i, (pa, parts_a, lo_a, hi_a, ka, holes_a) in enumerate(found):
        fa = frames[pa]
        for pb, parts_b, lo_b, hi_b, kb, holes_b in found[i + 1:]:
            if pa == pb or not holes_a or not holes_b:
                continue
            fb = frames[pb]
            if abs(fs._dot(fa[3], fb[3])) < 0.966:
                continue

            def onto(p3, fr=fa):           # into panel a's pattern plane
                v = (p3[0] - fr[0][0], p3[1] - fr[0][1], p3[2] - fr[0][2])
                return (fs._dot(v, fr[1]), fs._dot(v, fr[2]),
                        fs._dot(v, fr[3]))

            way = 1.0 if fs._dot(fa[3], fb[3]) > 0.0 else -1.0
            mid_b = onto(fs.place(fb, layout.panels[pb].pts[0]))[2]
            span_b = sorted((mid_b + way * lo_b, mid_b + way * hi_b))
            gap = max(span_b[0] - hi_a, lo_a - span_b[1])
            if gap < -1e-6 or gap > reach:
                continue
            flat_b = [pc.ccw([onto(fs.place(fb, p))[:2] for p in part])
                      for part in parts_b]
            if not any(pc.convex_overlap(A, B, 0.5) for A in parts_a
                       for B in flat_b):
                continue
            # nothing between them
            lo_gap, hi_gap = (hi_a, span_b[0]) if span_b[0] >= hi_a \
                else (span_b[1], lo_a)
            if _between(layout, found, frames, fa, onto, lo_gap, hi_gap,
                        parts_a, flat_b, (pa, ka), (pb, kb)):
                continue
            mine = [(k, p) for k, p in holes_a
                    if any(pc.inside_convex(p, B, 0.0) for B in flat_b)]
            theirs = [(k, onto(fs.place(fb, p))[:2]) for k, p in holes_b]
            theirs = [(k, p) for k, p in theirs
                      if any(pc.inside_convex(p, A, 0.0) for A in parts_a)]
            if not mine or not theirs:
                continue
            off = lone = 0
            worst = apart = 0.0
            for one, other in ((mine, theirs), (theirs, mine)):
                for k, p in one:
                    d, k2 = min(((math.dist(p, q), k3) for k3, q in other),
                                default=(2.0 * near, -1))
                    if d <= tol:
                        apart = max(apart, d)
                        if check.state.get(k) != fs.OFF:
                            check.state[k] = fs.MEETS
                    elif d <= near:
                        off += 1
                        worst = max(worst, d)
                        check.state[k] = fs.OFF
                    else:
                        lone += 1
            check.findings.append(Faced(
                ka, kb, len(mine) + len(theirs), off, worst, lone, apart,
                (names[ka], names[kb]), False,
                (layout.panels[pa].number, layout.panels[pb].number)))


def _between(layout, found, frames, fa, onto, lo, hi, parts_a, flat_b,
             one, two) -> bool:
    """Whether any body of leather lies between two that face each other
    (heights ``lo`` to ``hi`` along the first's normal) where both are."""
    for pc_, parts, low, high, piece, _holes in found:
        if (pc_, piece) in (one, two):
            continue
        fr = frames[pc_]
        if abs(fs._dot(fr[3], fa[3])) < 0.966:
            continue
        way = 1.0 if fs._dot(fr[3], fa[3]) > 0.0 else -1.0
        mid = onto(fs.place(fr, layout.panels[pc_].pts[0]))[2]
        a, b = sorted((mid + way * low, mid + way * high))
        if b <= lo + 1e-6 or a >= hi - 1e-6:
            continue
        flat = [pc.ccw([onto(fs.place(fr, p))[:2] for p in part])
                for part in parts]
        if any(pc.convex_overlap(C, A, 0.5) and pc.convex_overlap(C, B, 0.5)
               for C in flat for A in parts_a for B in flat_b):
            return True
    return False

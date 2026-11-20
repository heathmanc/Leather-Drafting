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
from dataclasses import dataclass, field, replace
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
# It is the overlap of the outlines as they are laid, which is what is
# drawn: a curve is laid as fewer, longer lines than it is drawn in, never
# outside it, so two pieces that overlap along a curve by less than the
# curve is followed to (``Sheet.within``: 0.03 mm, more for a very busy
# outline) may be laid sharing nothing, and are then left on one level.
LAP = 1e-6
SLIVER = 1.0        # a piece resting on less overlap than this (mm) is told
# less than this (mm) is a curve drawn as short straight lines, not a
# place where one piece really stands past or clear of another; and a
# stitch hole this near an edge of a piece is on that edge, not in it
HAIR = 0.05
# two pairs of stitch holes whose distances out of line are this near
# each other (mm) are as far out of line: the least the report tells apart
AS_FAR = 0.01
_SMOOTH = 0.03      # how closely (mm) a sheet's curved outline is followed
_BUSY = 260         # ... unless it would still have more corners than this
_LOOSE = 0.5        # ... and then no more loosely than this (mm)
_GROW = 1e-9        # leather (mm) that thinning an outline may add: rounding
# two edges of a coarse outline that meet at less of a turn than this (its
# sine: 20 degrees) are drawn in together, by as much as either has to be
_TURN = 0.34
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
    to lie on each other. (The other side of that: two pieces that do
    overlap along a curve, by less than the curve is followed to, may
    share nothing as they are laid, and are then left on one level.)
    ``within`` is how closely (mm) the outline as laid follows the one
    given: 0 where no corner was dropped, 0.03 for a curve, and more for
    an outline so busy -- a wavy or scalloped edge, a traced one -- that
    followed so closely it would keep more than 260 corners. What there
    is to tell of a stack is told only where it is more than the
    outlines it is about were followed to. ``parts`` is the outline in
    convex parts: none for an outline with no area, which is no leather
    -- such a sheet lifts nothing, is shown as nothing, and is told as
    lying on nothing. ``frame`` is where the grain of its leather is laid
    from, ``(a point, the way x runs, the way y runs)``: its first corner
    and its first edge, so that a piece moved or turned on the bench
    takes its grain with it.

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
    within: float = field(default=0.0, init=False, repr=False)
    # the outline ``parts`` and ``frame`` were made from
    _seen: Optional[tuple] = field(default=None, init=False, repr=False,
                                   compare=False)
    # (that outline, the main piece's, ``past``, ``past_at``) as last found
    _stood: Optional[tuple] = field(default=None, init=False, repr=False,
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
        says, with ``parts``, ``frame`` and ``within`` from it, if the
        outline is not the one they were last made from. ``settle`` does
        this for every sheet it is given, so an outline put on a sheet
        after it was made (``sheet.ring = ...``) is the one that is
        laid."""
        self.thickness = max(0.05, float(self.thickness))
        self.side = 1 if self.side >= 0 else -1
        self.grain = 1 if self.grain >= 0 else -1
        given = tuple((float(p[0]), float(p[1])) for p in self.ring)
        if given == self._seen:
            return
        ring, self.within = _outline(given) if len(given) >= 3 \
            else (list(given), 0.0)
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
    1, 0.5 half way through it. ``rest``, on a note that says leather is
    drawn into leather, is how deep at rest -- at a whole step of the
    folding, where things end up -- beside the deepest on the way; and
    ``last``, on a ``"clash"``, ``"limit"`` or ``"landing"``, the last
    whole step at which it is drawn in at rest (0 for none, or only
    lying flat: ``rest`` tells the two apart).

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
                 piece, furthest at its point ``at`` (told when it is
                 more than ``HAIR``, and more than the main piece's
                 outline was followed to)
    ``"clear"``  ``sheet`` stands ``a`` mm clear of the piece ``other[0]``
                 that lies under part of it, lower than what it rests on
                 (and of the rest of ``other`` by less), over a place
                 wider than ``HAIR`` and than the outlines allow for
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
                   there at any moment looked at (0 for not at all), and
                   ``rest`` how deep at rest. One note for each run, its
                   ``b`` and ``rest`` that run's own: two notes of one
                   sheet (a run at each end of a hinge, say) can say two
                   depths, and neither is the depth of the other run
    ``"crossing"`` ``sheet`` lies over the place ``at`` where fold and
                   fold ``other[0]`` cross, and is cut or bent along both
                   there -- inside the main piece or past its edge: the
                   main piece alone passes through itself there, folded,
                   and the sheet is parted. ``b`` and ``rest`` as for
                   ``"slit"``
    ``"size"``     shut, the stack fits in a rectangle ``a`` by ``b`` mm
                   seen square to the still panel, the longer side first
    ``"shut"``     ... and is ``a`` mm thick
    ``"spine"``    the round of fold's bend, shut flat, is ``a`` mm
                   across, outside to outside
    ``"leather"``  a needle square to the still panel goes through ``a``
                   mm of leather at the most, ``b`` at the least, over the
                   still panel's flat leather. Told only of a stack that
                   is shut -- every fold that is made closes flat -- and
                   not where finding it would take too long (``_BOXES``)
    ``"clash"``    the judge finds ``sheet`` and ``other[0]`` ``a`` mm
                   into each other at the deepest, at any moment it looks
                   at -- the whole steps, eighths of each step and a
                   sixteenth in from each end, and round the deepest of
                   those; ``b`` is the first moment they are deeper than
                   0.05 mm, to within a two-thousandth of a step (closed
                   in on from the moment looked at before the first at
                   which they are, where they are not); ``rest`` how deep
                   at rest, at the deepest whole step (0: only on the way,
                   inside a step, where leather gives), and ``last`` the
                   last whole step at which they are drawn in at rest. A
                   sheet with itself
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
                   first at the moment ``b`` (as for ``"clash"``), that
                   are only ``c`` mm into each other with every fold on
                   its line: the move's doing, laid at the door of fold
                   ``fold``; ``rest`` and ``last`` as for ``"clash"``

    Nothing drawn into anything by 0.05 mm or less (``foldsolid._TOUCH``)
    is told."""

    kind: str
    sheet: int = 0
    fold: int = -1
    a: float = 0.0
    b: float = 0.0
    other: tuple = ()
    at: Optional[Pt] = None
    c: float = 0.0
    rest: float = 0.0
    last: float = 0.0


def sheet_of(tag) -> int:
    """The piece a cell or a face of a model is of, from its tag: 0 for
    the main piece, whose tags have two parts, else the sheet's number."""
    return tag[2] if tag is not None and len(tag) > 2 else 0


# -- polygons, in plan --------------------------------------------------------

def _outline(ring: Sequence[Pt]) -> Tuple[List[Pt], float]:
    """A sheet's outline made ready, as ``foldsolid.model_outline`` makes
    the main piece's: counter-clockwise, no doubled points, and a
    flattened curve thinned to within ``_SMOOTH`` mm (further, up to half
    a mm, for an outline that would still have more than ``_BUSY``
    corners) -- but thinned inwards only (see ``_inward``). Returns
    ``(the outline, how closely it follows the one given)``. It starts at
    the corner it was given first, if that is one of those kept.

    An outline that thinning inwards cannot bring under ``_BUSY`` corners
    at any tolerance -- one edge after another waves by more than half a
    mm, or the outline is rough: its corners jitter by a hundredth of a
    mm, as a traced one's do and one read from a file that keeps two
    decimals, so that every other corner is the end of a hollow and has
    to stay -- is thinned both ways instead, as a lone piece's is, and
    then drawn in until it is nowhere outside the outline given (see
    ``_coarse``)."""
    first = ring[0]
    ring, _flags = pc.tidy(list(ring), [0] * len(ring), 1e-6)
    ring = _specks(pc.ccw(ring))
    out = _inward(ring, _SMOOTH)
    within = _SMOOTH if len(out) < len(ring) else 0.0
    if len(out) > _BUSY:
        # thinned both ways and drawn in: at the first tolerance that
        # leaves few enough corners, as a lone piece's outline has it --
        # or, where that cannot be done, the next
        levels = _levels(ring)
        ladder = [_SMOOTH]
        while ladder[-1] < _LOOSE:
            ladder.append(min(1.6 * ladder[-1], _LOOSE))
        few = [tol for tol in ladder
               if sum(level > tol for level in levels) <= _BUSY]
        coarse = None
        for tol in few + [tol for tol in ladder[::-1] if tol not in few]:
            coarse = _coarse(ring, tol, levels)
            if coarse is not None:
                break
        # thinned inwards, more and more loosely: as loosely as the
        # other way follows the outline, to begin with
        tol = _SMOOTH
        most = min(coarse[1], _LOOSE) if coarse is not None else _LOOSE
        while len(out) > _BUSY and tol < most:
            tol = min(1.6 * tol, _LOOSE)
            out = _inward(ring, tol)
        # The other way is taken where it follows the outline more
        # closely with fewer corners -- or, if this way still leaves too
        # many, where it leaves a good deal fewer. Failing that, inwards
        # as loosely as it takes.
        if coarse is not None and (
                len(coarse[0]) <= 0.6 * len(out) if len(out) > _BUSY
                else len(coarse[0]) < len(out) and coarse[1] < tol):
            out, within = coarse
        else:
            while len(out) > _BUSY and tol < _LOOSE:
                tol = min(1.6 * tol, _LOOSE)
                out = _inward(ring, tol)
            within = tol
    if first in out:
        k = out.index(first)
        out = out[k:] + out[:k]
    return out, within


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
    is, or all but (it may lose a corner that a dropped one held in
    place the first time)."""
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


def _specks(ring: Sequence[Pt]) -> List[Pt]:
    """A counter-clockwise outline without the hollows that are too small
    to be dealt with: every corner is dropped at which the outline turns
    into the leather, yet by so little leather that ``polycut`` takes the
    corner for a straight one. (It goes by the cross product of the two
    edges at a corner, in mm2: under a thousandth of a millionth that is
    a straight corner to it, whatever the angle, and two edges a
    hundred-thousandth of a mm long make no more. A notch that small in
    an edge -- dirt in a drawing, not a notch -- would be kept in one
    "convex" part with the rest, and its two walls, carried on as if
    they were edges of that part, would cut the piece in three.) What is
    filled in is less than a thousandth of a square micron a corner."""
    out = list(ring)
    i = quiet = 0
    while len(out) > 3 and quiet < len(out):
        n = len(out)
        i %= n
        a, b, c = out[i - 1], out[i], out[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if -1e-9 <= cross < 0.0 and _turn(a, b, c) < -1e-7:
            del out[i]
            i -= 1                     # (its neighbours are looked at again)
            quiet = 0
        else:
            i += 1
            quiet += 1
    return out


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


def _levels(ring: Sequence[Pt]) -> List[float]:
    """For each corner of a ring, how loosely the ring can be thinned
    before that corner goes: ``polycut.simplify(ring, tol)`` keeps just
    the corners whose level is more than ``tol``. (A corner is kept when
    it is the one furthest from the line between two kept ones, and
    further than ``tol``; so it goes with the first of the corners it
    hangs on, or when ``tol`` reaches its own distance.) One going
    through the ring serves every tolerance that is tried."""
    n = len(ring)
    level = [0.0] * n
    x0, y0 = ring[0]
    far = max(range(n), key=lambda i: (ring[i][0] - x0) ** 2
              + (ring[i][1] - y0) ** 2)
    level[0] = level[far] = math.inf
    stack = [(0, far, math.inf), (far, n, math.inf)] if far else []
    while stack:
        i, j, cap = stack.pop()
        if j - i < 2:
            continue
        ax, ay = ring[i]
        bx, by = ring[j % n]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            px, py = ring[k]
            if ll < 1e-18:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / ll
                t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > worst:
                worst, wi = d, k
        cap = min(cap, math.sqrt(worst))
        level[wi] = cap
        stack.append((i, wi, cap))
        stack.append((wi, j, cap))
    return level


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


def _coarse(ring: Sequence[Pt], tol: float, levels: Sequence[float]):
    """A counter-clockwise outline thinned both ways, as a lone piece's
    is, and then drawn in until it has no leather that the outline has
    not: ``(the outline, how far (mm) the one given is from it at most)``,
    or None where that cannot be done. ``levels`` are the ring's (see
    ``_levels``).

    The corners that stay are those ``polycut.simplify`` keeps: the line
    between two of them stands for the corners dropped between them,
    which lie within ``tol`` mm of it on either side. Where they lie to
    its left the line has leather that the outline has not, and is moved
    in, square to itself, by as far as the furthest of them lies; the
    corners of the new outline are where one such line meets the next.
    Two things more are seen to. Lines that meet at next to no turn are
    moved in by the same amount, the most either needs: moved by
    different amounts they would meet far from where they were. And
    round a hollow corner -- where the outline is the leather to the
    left of either line, not of both -- a line is moved in past the
    corners its neighbour stands for as well."""
    n = len(ring)
    idx = [i for i, level in enumerate(levels) if level > tol]
    m = len(idx)
    if m < 3:
        return None
    way, base, spans = [], [], []      # each line: its way, a point, corners
    for j in range(m):
        i0, i1 = idx[j], idx[(j + 1) % m]
        ax, ay = ring[i0]
        dx, dy = ring[i1][0] - ax, ring[i1][1] - ay
        ln = math.hypot(dx, dy)
        if ln < 1e-9:
            return None
        way.append((dx / ln, dy / ln))
        base.append((ax, ay))
        spans.append(range(i0 + 1, i1 if i1 > i0 else n))

    def left(j, k):
        # how far the corners line ``k`` stands for lie to the left of
        # line ``j``, at the most
        (ex, ey), (ax, ay) = way[j], base[j]
        return max((ex * (ring[i][1] - ay) - ey * (ring[i][0] - ax)
                    for i in spans[k]), default=0.0)

    turn = [(way[j - 1][0] * way[j][1] - way[j - 1][1] * way[j][0],
             way[j - 1][0] * way[j][0] + way[j - 1][1] * way[j][1])
            for j in range(m)]         # at the corner where line j begins
    need = [max(0.0, left(j, j)) for j in range(m)]
    for j, (s, _c) in enumerate(turn):
        if s < 0.0:                    # a hollow corner
            need[j] = max(need[j], left(j, j - 1))
            need[j - 1] = max(need[j - 1], left(j - 1, j))
    # lines that meet at next to no turn go together
    chain = list(range(m))

    def head(j):
        while chain[j] != j:
            chain[j] = chain[chain[j]]
            j = chain[j]
        return j

    for j, (s, c) in enumerate(turn):
        if c > 0.0 and abs(s) < _TURN:
            chain[head(j)] = head(j - 1)
    most: Dict[int, float] = {}
    for j in range(m):
        most[head(j)] = max(most.get(head(j), 0.0), need[j])
    move = [most[head(j)] for j in range(m)]
    out: List[Pt] = []
    for j, (s, c) in enumerate(turn):
        bx, by = base[j]
        a, b = move[j - 1], move[j]
        if a <= _GROW and b <= _GROW:
            out.append((bx, by))       # (a corner that was given)
            continue
        (ux, uy), (vx, vy) = way[j - 1], way[j]
        if c > 0.0 and abs(s) < _TURN:
            k = a / (1.0 + c)
            out.append((bx - k * (uy + vy), by + k * (ux + vx)))
        elif abs(s) < 0.5 * _TURN:
            return None                # back on itself: no place to meet
        else:
            # where the first line, moved ``a`` to its left, meets the
            # second, moved ``b`` to its own
            out.append((bx + (a * vx - b * ux) / s,
                        by + (a * vy - b * uy) / s))
    # every line still runs the way it did, and none crosses another
    for j in range(m):
        (ax, ay), (bx, by) = out[j], out[(j + 1) % m]
        if (bx - ax) * way[j][0] + (by - ay) * way[j][1] <= 1e-9:
            return None
    if not _simple(out) or _area(out) <= 0.0:
        return None
    off = 0.0
    for j in range(m):
        a, b = out[j], out[(j + 1) % m]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = dx * dx + dy * dy
        for i in (idx[j],) + tuple(spans[j]):
            px, py = ring[i]
            t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / ll))
            off = max(off, math.hypot(px - a[0] - t * dx, py - a[1] - t * dy))
    return out, off


def _simple(pts: Sequence[Pt]) -> bool:
    """Whether no two edges of a polygon cross."""
    n = len(pts)
    edges = sorted((min(pts[i][0], pts[(i + 1) % n][0]),
                    max(pts[i][0], pts[(i + 1) % n][0]), i)
                   for i in range(n))

    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    for k, (_lo, hi, i) in enumerate(edges):
        a, b = pts[i], pts[(i + 1) % n]
        for lo, _hi, j in edges[k + 1:]:
            if lo > hi:
                break                  # and so is every later one
            if (i - j) % n in (1, n - 1):
                continue               # the next edge round: they share an end
            c, d = pts[j], pts[(j + 1) % n]
            if side(a, b, c) * side(a, b, d) < 0.0 \
                    and side(c, d, a) * side(c, d, b) < 0.0:
                return False
    return True


def _strays(given: Sequence[Pt], kept: Sequence[Pt]) -> Tuple[float, float]:
    """How far an outline thinned both ways strays from the one it was
    made from, both counter-clockwise: ``(how far it reaches outside it,
    how far it stops short inside it)`` at the most, in mm. ``kept`` are
    corners of ``given``, in its order."""
    n, m = len(given), len(kept)
    idx, i = [], 0
    for q in kept:
        while i < n and given[i] != q:
            i += 1
        if i == n:
            return _LOOSE, _LOOSE      # (not made from it: the loosest)
        idx.append(i)
        i += 1
    out = short = 0.0
    for j in range(m):
        i0, i1 = idx[j], idx[(j + 1) % m]
        ax, ay = given[i0]
        dx, dy = given[i1][0] - ax, given[i1][1] - ay
        ln = math.hypot(dx, dy) or 1.0
        for i in list(range(i0 + 1, i1 if i1 > i0 else n)) \
                + (list(range(0, i1)) if i1 <= i0 else []):
            c = (dx * (given[i][1] - ay) - dy * (given[i][0] - ax)) / ln
            # a corner dropped to the left of the line is leather that
            # the thinned outline has and the given one has not
            out, short = max(out, c), max(short, -c)
    return out, short


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


def _area(pts: Sequence[Pt]) -> float:
    """The signed area of a polygon, as ``polycut.area`` gives it -- but
    worked out from the polygon's own first corner, not from the origin of
    the pattern. For a bit of a polygon a millionth of a mm across, drawn
    a hundred mm from the origin, the sums ``polycut.area`` makes come to
    their own rounding and nothing else; these do not, wherever the
    pattern is drawn."""
    x0, y0 = pts[0]
    s = 0.0
    ax, ay = pts[-1][0] - x0, pts[-1][1] - y0
    for x, y in pts:
        bx, by = x - x0, y - y0
        s += ax * by - bx * ay
        ax, ay = bx, by
    return 0.5 * s


def _centre(pts: Sequence[Pt]) -> Pt:
    """The centre of area of a convex polygon, worked out from its own
    first corner (see ``_area``): a point of the polygon, wherever it is
    drawn. For one with no area to speak of, the middle of its corners."""
    x0, y0 = pts[0]
    a = cx = cy = size = 0.0
    ax, ay = pts[-1][0] - x0, pts[-1][1] - y0
    for x, y in pts:
        bx, by = x - x0, y - y0
        c = ax * by - bx * ay
        a += c
        cx += (ax + bx) * c
        cy += (ay + by) * c
        size = max(size, abs(bx), abs(by))
        ax, ay = bx, by
    if abs(a) <= 1e-9 * size * size:
        n = len(pts)
        return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)
    return (x0 + cx / (3.0 * a), y0 + cy / (3.0 * a))


def _convex(ring) -> List[List[Pt]]:
    """An outline (counter-clockwise) as convex polygons: none, for one
    with no area."""
    if len(ring) < 3 or abs(_area(ring)) <= 1e-9:
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
    past the nearest edge of it. The points measured from are ones that
    both do cover: the corners of what two parts have in common, and its
    centre (worked out from the bit's own corner: where two parts only
    touch, what they have in common is a speck of rounding, and a centre
    summed from the origin of the pattern may be anywhere)."""
    best = _reach(parts_a, parts_b)
    if LAP < best < SLIVER and len(parts_a) + len(parts_b) > 2:
        for bit in _common(parts_a, parts_b, 0.0):
            for q in bit + [_centre(bit)]:
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
    narrowest. The box round it and its area settle most at once: it is
    no wider than that box is, at least as wide as its area over the
    length of the box's diagonal, and no wider than three times that.
    (The area is worked out from the polygon's own corner: see
    ``_area``. What two pieces that only touch have in common is a speck
    a millionth of a millionth of a mm across, and must be told as one.)"""
    x0, y0, x1, y1 = pc.bounds(pts)
    if min(x1 - x0, y1 - y0) <= least:
        return False
    over = abs(_area(pts))
    span = least * math.hypot(x1 - x0, y1 - y0)
    if over > span:
        return True
    if 3.0 * over <= span:
        return False
    return _width(pts) > least


def _split(pts: Sequence[Pt], p: Pt, q: Pt):
    """A convex polygon cut along the line through ``p`` and ``q``:
    ``(what lies to its left, what lies to its right)``. A point on the
    line counts as to its left.

    A corner of the polygon that is on the line, or all but, comes out
    twice: as itself and as the point where the line crosses. The two
    are a thousandth of a millionth of a mm apart or less, and an edge
    between them would run any way at all -- taken for a real edge, it
    can part the polygon from one it lies in. So of two corners that
    close, one is kept."""
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
            _corner(left, m)
            _corner(right, m)
        _corner(right if db < 0.0 else left, b)
        a, da = b, db
    for half in (left, right):
        if len(half) > 1 and abs(half[0][0] - half[-1][0]) < 1e-9 \
                and abs(half[0][1] - half[-1][1]) < 1e-9:
            half.pop()
    return left, right


def _corner(pts: List[Pt], q: Pt):
    """Add a corner to a polygon being made, unless it is the corner
    before it over again (to a thousandth of a millionth of a mm)."""
    if not pts or abs(q[0] - pts[-1][0]) >= 1e-9 \
            or abs(q[1] - pts[-1][1]) >= 1e-9:
        pts.append(q)


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


def _past(parts, ground, ring, tol: float = 0.01):
    """How far a piece stands past the edge of another: ``(how far, the
    point of it furthest from the other's leather)``, or ``(0.0, None)``
    for a piece wholly on the other. Both are given in convex parts, and
    the other (``ground``) by its outline too (``ring``).

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
    boxes = [pc.bounds(G) for G in ground]

    def look(bit):
        nonlocal best, at, seen
        seen += 1
        # A bit has nothing of the other piece under it, so each corner
        # is as far from that piece as from its outline. If its first
        # corner is, and all of the bit from that corner, no further out
        # than what is found already, the bit is done with
        x0, y0, x1, y1 = pc.bounds(bit)
        first = _to_ring(bit[0], ring)
        if first + math.hypot(x1 - x0, y1 - y0) <= best:
            return
        # it is as far out as the furthest of its corners ...
        low = [first] + [_to_ring(q, ring) for q in bit[1:]]
        far, k = max((d, k) for k, d in enumerate(low))
        if far > best:
            # (... unless that corner is on the other piece after all:
            # then the bit is a sliver of this one that rounding left
            # where the other was taken out of it, and is past nothing)
            if _in(bit[k], ground):
                return
            best, at = far, bit[k]
        # ... and no further than a corner is, and all of the bit from
        # that corner
        high = min(d + max(math.dist(q, r) for r in bit)
                   for d, q in zip(low, bit))
        if high <= best + tol:
            return
        # nor further than its corners are from any one part of the other
        # piece: the parts nearest it first, by the boxes round them
        for gap, g in sorted(
                (math.hypot(max(gx0 - x1, x0 - gx1, 0.0),
                            max(gy0 - y1, y0 - gy1, 0.0)), g)
                for g, (gx0, gy0, gx1, gy1) in enumerate(boxes)):
            if gap >= high:
                break                  # and so is every part after it
            G = ground[g]
            high = min(high, max(
                0.0 if pc.inside_convex(q, G, 0.0) else _to_ring(q, G)
                for q in bit))
            if high <= best + tol:
                return
        heapq.heappush(todo, (-high, seen, bit))

    # (the biggest bits first: what they are found to stand out by is
    # more than most of the rest could)
    for _size, _n, bit in sorted(
            (-abs(_area(bit)), n, bit) for n, bit in enumerate(
                _minus(list(parts), ground, LAP, boxes))):
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
            if len(half) >= 3 and abs(_area(half)) > 1e-12:
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
    sliver, or lies on nothing.

    A sheet is told as standing past the edge, or clear of a piece, only
    by more than the outlines were followed to. The main piece's is
    thinned both ways, a sheet's inwards only (``Sheet.within``), and
    very busy ones to half a mm and more: a pocket that shares the main
    piece's own wavy edge lies within it as it is drawn, and as it is
    laid may stand a third of a mm past it in places, which is the
    thinning and nothing to tell. For the rest the main piece is its
    outline as it is laid. Across a hollow of its edge that is laid a
    little outside the one drawn (0.03 mm; more for a very busy edge):
    a sheet that lies wholly beside the main piece and fits such a
    hollow is on it by that much, and is told as resting on it, not as
    lying on nothing."""
    return _lay(main_ring, fs.model_outline(main_ring)
                if len(main_ring) >= 3 else [], main_thickness, sheets)


def _lay(main_ring, ready, main_thickness: float, sheets) -> List[Note]:
    """``settle``, for a caller that has the main piece's outline made
    ready already (``ready``: ``foldsolid.model_outline`` of it)."""
    ring: List[Pt] = []
    short = 0.0                # how far it is laid short of its outline
    if len(ready) >= 3:
        given, _flags = pc.tidy([(float(p[0]), float(p[1]))
                                 for p in main_ring], [0] * len(main_ring),
                                1e-6)
        if len(ready) < len(given):
            short = _strays(pc.ccw(given), ready)[1]
        # (thinned both ways, it has no speck of a notch left in it)
        ring = list(ready)
    ground, edge = _convex(ring), tuple(ring)
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
            # (how far it stands past the main piece goes by the two
            # outlines alone: a sheet that has not moved, on a main piece
            # that has not changed, is not measured again)
            if s._stood is None or s._stood[0] is not s._seen \
                    or s._stood[1] != edge:
                s._stood = (s._seen, edge) + (
                    _past(s.parts, ground, ring) if ground else (0.0, None))
            s.past, s.past_at = s._stood[2:]
            laid.append(k)
    notes = [_thickest(ground, ring, max(0.05, float(main_thickness)),
                       sheets, over)]
    numbered = list(enumerate(sheets, 1))
    notes += [Note("past", k, -1, s.past, at=s.past_at)
              for k, s in numbered if s.past > max(HAIR, short + 1e-9)]
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
    ``[(piece, gap)]`` with the widest gap first. A place counts when it
    is wider than ``HAIR`` -- and wider than twice what the outline of
    any sheet under the sheet was followed to: the strip that a piece is
    laid short by along its edge, or two such strips side by side, is no
    place under a bridge."""
    below = sorted(under, reverse=True)                   # the tallest first
    least = max([HAIR] + [2.0 * sheets[j].within for _hi, j in under])
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
                        bits, sheets[j].parts, least, True, boxes[j]):
                    clear.add(j + 1)
            if all(k in clear for k in lower[n:]):
                break                  # nothing lower is still looked for
            bits = _minus(bits, sheets[j].parts, LAP, boxes[j])
            if not bits:
                break
        else:
            if 0 in lower and 0 not in clear \
                    and _common(bits, ground, least, True):
                clear.add(0)
    return [(k, gap[k]) for k in reversed(lower) if k in clear]


def _thickest(ground, ring, thickness: float, sheets, over) -> Note:
    """The thickest place of the flat stack, as a ``"thick"`` note: the
    most there is from the underside of one piece to the top of another
    that is over it -- and of the places that are as thick as that, the
    one with most leather in it. ``ground`` and ``ring`` are the main
    piece, in convex parts and as its outline; ``over`` has, for each
    sheet, the sheets laid before it on its own face that it is over."""
    def parts(piece):
        return sheets[piece - 1].parts if piece else ground

    def edge(piece):
        return sheets[piece - 1].ring if piece else ring

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
    pairs.sort(key=lambda pair: -pair[0])
    # the thickest of them that are over each other, and whatever else is
    # as thick (a wallet's two pocket stacks are): a few of those will do
    found: List[tuple] = []
    for pair in pairs:
        if found and (pair[0] < found[0][0] - 1e-9 or len(found) == 6):
            break
        if pair[1] == pair[2] or is_over(pair[1], pair[2]):
            found.append(pair)

    def biggest(bits):
        # (a place in very many bits is followed by its biggest only: it
        # is a point of it that is wanted, not all of it)
        return sorted(bits, key=_area)[-8:] if len(bits) > 8 else bits

    # the pieces as a note names them: the main piece, then outwards on
    # its front, then outwards on its back
    order = ([0] if ground else []) + [
        k for side in (1, -1)
        for k in sorted((k for k in live if sheets[k - 1].side == side),
                        key=lambda k: sheets[k - 1].lo)]

    def fill(top, foot):
        # Of the place where ``top`` is over ``foot``, the part with most
        # leather in it: (how much leather, the pieces there, a point of
        # it). Each other piece is taken in or left out, the thickest
        # first; what is left of the place gets smaller as pieces are
        # taken in.
        place = list(parts(top))
        if foot != top:
            place = _common(place, parts(foot), 0.0)
        place = biggest(place)
        rest = sorted(((thickness if not k else sheets[k - 1].thickness, k)
                       for k in order if k != top and k != foot),
                      key=lambda tk: -tk[0])
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
                if sum(map(_area, got)) >= (1.0 - 1e-9) * sum(map(_area,
                                                                  bits)):
                    return                     # it is under all of the place
            take(i + 1, bits, leather, left - t)

        take(0, place, 0.0, sum(t for t, _k in rest))
        # a point of it: the centre of its biggest bit, which is a point
        # of both pieces the place is between
        at = None
        for bit in sorted(best[1], key=_area, reverse=True):
            at = _centre(bit)
            if _in(at, parts(top)) and _in(at, parts(foot)):
                break
        if at is None:
            return 0.0, (), None
        # ... and what has leather there: the two, and every other piece
        # that the point is on and not merely on the edge of
        here = [k for k in order if k == top or k == foot or (
            _in(at, parts(k)) and _to_ring(at, edge(k)) > 1e-7)]
        return (sum(thickness if not k else sheets[k - 1].thickness
                    for k in here), tuple(here), at)

    if not found:
        return Note("thick", 0, -1, 0.0, 0.0, (), None)
    most = None                                # (the first of equals stays)
    for _tall, top, foot in found:
        got = fill(top, foot)
        if most is None or got[0] > most[0] + 1e-9:
            most = got
    return Note("thick", 0, -1, found[0][0], most[0], most[1], most[2])


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
    ``getattr(found, "laid", False)``. Its counts are of what the two do
    with each other; ``red`` is how many of the holes it counts as
    meeting are marked ``OFF`` all the same, because something else on
    their needle line is out of line, and ``numbers`` the holes that meet
    or miss one of the other's."""

    names: Tuple[str, str] = ("", "")
    laid: bool = True
    through: Tuple[int, int] = (0, 0)
    red: int = 0
    numbers: frozenset = field(default=frozenset(), repr=False,
                               compare=False)


class LaidCheck(fs.Check):
    """What ``laid_check`` found: a ``foldsolid.Check`` whose ``findings``
    are ``Laid`` pairs and whose holes are numbered through the whole
    stack, the main piece's first. A hole that only comes through leather
    with no hole in it has no state: that is marked on the piece it comes
    through. ``through`` has those spots for each piece, and ``landed``
    the spots where holes that were missed come down on it, both as
    ``{piece: [(x, y, diameter)]}`` in pattern mm; ``rings`` and
    ``blanks`` are the same spots sorted by the cell they are on, for the
    model (filled in by whoever has the cells). (``passed`` is an older
    name for ``through``: the spots a hole passed through, not anything
    that passed the check.) ``sewn`` is the set of holes, by number, that
    meet or miss a hole of a piece they lie on. ``apart`` has, for each
    hole marked ``OFF``, the furthest miss on its needle line (in its
    stack of holes). ``passes`` has the holes of a piece that come
    through another where it has none, by ``(the piece, the other, 0)``:
    those ``through`` has the spots of; and a stack's check
    (``stack_check``) adds those that come through a piece between two
    the folding brings face to face, the other being the piece's own
    leather on another panel where its third place is that panel's
    number."""

    __slots__ = ("apart", "blanks", "landed", "passes", "sewn", "through")

    def __init__(self):
        super().__init__()
        self.apart: Dict[int, float] = {}
        self.blanks: Dict[int, List[Tuple[float, float, float]]] = {}
        self.landed: Dict[int, List[Tuple[float, float, float]]] = {}
        self.passes: Dict[Tuple[int, int, int], set] = {}
        self.sewn: set = set()
        self.through: Dict[int, List[Tuple[float, float, float]]] = {}

    @property
    def passed(self):
        return self.through


class _Layer:
    """The main piece as ``laid_check`` wants a piece."""

    __slots__ = ("ring", "cutouts", "holes", "name")

    def __init__(self, ring, cutouts, holes, name):
        self.ring = ring
        self.cutouts = cutouts
        self.holes = holes
        self.name = name


def laid_check(layers, tol: float = 0.75, near: float = 4.0,
               memo: Optional[dict] = None) -> LaidCheck:
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
    in it miss, ``MEETS`` if it has company and none does.

    ``memo``, a dict kept from one call to the next, keeps what each two
    pieces came to, so that a stack read again with only some of its
    pieces changed (the others the same objects, unchanged, in the same
    places) compares only the pairs with a changed piece in them. Two
    pieces are taken as before only where their holes are numbered as
    before and ``tol`` and ``near`` are the same: no piece's outline,
    cutouts or holes may be changed in place."""
    check = LaidCheck()
    holes, owner, pairs = _compared(layers, tol, near, memo)
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

    sewn = []                                  # (finding, its meeting)
    for _i, _j, met, missed, lone, found in pairs:
        for k, k2 in met:
            seen.update((k, k2))
            link[stack(k)] = stack(k2)
        for k, k2 in missed:
            seen.add(k)
            misses.append((k, k2))
        for k, b, x, y, dia in lone:
            spot(check.through, b, x, y, dia)
            check.passes.setdefault((owner[k], b, 0), set()).add(k)
        if found is not None:
            sewn.append((found, met))
    check.sewn = seen
    # each hole by the stack it is in, as ``Layout.lineup`` does
    stacks: Dict[int, List[int]] = {}
    for k in seen:
        stacks.setdefault(stack(k), []).append(k)
    wide: Dict[int, float] = {}                # each stack: its worst miss
    for k, k2 in misses:
        d = math.hypot(holes[k][0] - holes[k2][0], holes[k][1] - holes[k2][1])
        wide[stack(k)] = max(wide.get(stack(k), 0.0), d)
    for top, members in stacks.items():
        what = fs.OFF if top in wide else fs.MEETS
        for k in members:
            check.state[k] = what
            if top in wide:
                check.apart[k] = wide[top]
    # (each finding as the two pieces found it, and how many of the holes
    # it says meet are marked out of line all the same)
    check.findings = [replace(found, red=sum(
        check.state[k] == fs.OFF for k, _k2 in met)) for found, met in sewn]
    # ... and for every hole of a stack with a miss in it, where the hole
    # that was missed is: on each other piece of that stack, where that
    # piece has leather (not in its cutouts, nor within a hair of an
    # edge, where nothing is to be marked)
    for k, k2 in misses:
        x2, y2, dia = holes[k2]
        for m in stacks[stack(k)]:
            if owner[m] == owner[k2] or \
                    not _has_leather(layers[owner[m]], (x2, y2)):
                continue
            check.lands.setdefault(m, []).append((x2, y2, dia))
            spot(check.landed, owner[m], x2, y2, dia)
    return check


def _has_leather(layer, p: Pt) -> bool:
    """Whether a piece has leather at a point: inside its outline and
    outside its cutouts, and more than ``HAIR`` from the edge of either
    (the rule of ``laid_check``)."""
    if not pc.inside(p, layer.ring) or _on_edge(p, layer.ring):
        return False
    return not any(pc.inside(p, c) or _on_edge(p, c)
                   for c in layer.cutouts)


def _top(link: Dict[int, int], h: int) -> int:
    """The hole that stands for the stack hole ``h`` is in, of the stacks
    ``link`` makes (each hole to one in its stack, the one that stands for
    it to itself), the way there shortened as it goes."""
    while link.setdefault(h, h) != h:
        link[h] = link[link[h]]
        h = link[h]
    return h


def _compared(layers, tol: float, near: float, memo: Optional[dict] = None,
              only: Optional[int] = None):
    """The holes of a stack's pieces compared two by two, as
    ``laid_check`` compares them: ``(holes, owner, pairs)``, every hole
    as ``(x, y, diameter)`` numbered through the stack, the piece each is
    of, and for each two pieces ``i < j`` that have holes ``(i, j, met,
    missed, lone, found)``: the holes of either that meet a hole of the
    other, with the hole each meets; those that miss one, with the hole
    each misses; those that come through the other's leather with no
    hole in it, as ``(hole, the piece it comes through, x, y,
    diameter)``; and the ``Laid`` finding, None where no hole of either
    lies on the other's leather. Only the pairs with piece ``only`` in
    them, if it is given. ``memo``: see ``laid_check``."""
    holes: List[tuple] = []
    owner: List[int] = []
    mine: List[List[int]] = []
    firsts: List[int] = []
    for i, layer in enumerate(layers):
        firsts.append(len(holes))
        mine.append(list(range(len(holes), len(holes) + len(layer.holes))))
        holes += [tuple(h) for h in layer.holes]
        owner += [i] * len(layer.holes)
    boxes = [pc.bounds(layer.ring) if len(layer.ring) >= 3
             and abs(pc.area(layer.ring)) > 1e-9 else None
             for layer in layers]               # None: it has no leather
    # ... and then its holes are in nothing: numbered, but not looked at
    mine = [ks if box is not None else [] for ks, box in zip(mine, boxes)]
    grids: List[Optional[dict]] = [None] * len(layers)

    def grid(b):                       # each piece's holes, by square
        if grids[b] is None:
            got: Dict[tuple, list] = {}
            for k in mine[b]:
                got.setdefault((math.floor(holes[k][0] / near),
                                math.floor(holes[k][1] / near)),
                               []).append(k)
            grids[b] = got
        return grids[b]

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

    # (whether a spot is on a piece's leather, kept with the memo: the
    # same piece, or one cut the same, is looked at again at many of the
    # same spots)
    skin: dict = memo.setdefault("skin", {}) if memo is not None else {}

    ids = [id(layer) for layer in layers]
    cuts = [(id(layer.ring), id(layer.cutouts)) for layer in layers]

    def look(b, x, y):
        cut = (cuts[b], x, y)
        on = skin.get(cut)
        if on is None:
            on = skin[cut] = leather(b, (x, y))
        if not on:
            return (False, 0.0, -1)
        gx, gy = math.floor(x / near), math.floor(y / near)
        squares = grids[b] if grids[b] is not None else grid(b)
        d, k2 = math.inf, -1
        for ix in (gx - 1, gx, gx + 1):
            for iy in (gy - 1, gy, gy + 1):
                for k3 in squares.get((ix, iy), ()):
                    e = math.hypot(x - holes[k3][0], y - holes[k3][1])
                    if e < d or e == d and k3 < k2:
                        d, k2 = e, k3
        return (True, d if k2 >= 0 else 2.0 * near, k2)

    def compare(i, j):
        met, missed, lone = [], [], []
        looked = off = 0
        worst = apart = 0.0
        through = [0, 0]
        for way, (a, b) in enumerate(((i, j), (j, i))):
            for k in mine[a]:
                x, y, dia = holes[k]
                inside, d, k2 = look(b, x, y)
                if not inside:
                    continue
                looked += 1
                if d <= tol:
                    apart = max(apart, d)
                    met.append((k, k2))
                elif d <= near:
                    off += 1
                    worst = max(worst, d)
                    missed.append((k, k2))
                else:
                    through[way] += 1
                    lone.append((k, b, x, y, dia))
        found = Laid(i, j, looked, off, worst, sum(through), apart,
                     (layers[i].name, layers[j].name), True,
                     tuple(through), 0, frozenset(
                         k for k, _k2 in met + missed)) if looked else None
        return (i, j, met, missed, lone, found)

    pairs = []
    for i in range(len(layers)):
        for j in range(i + 1, len(layers)):
            if not mine[i] and not mine[j] or only not in (None, i, j):
                continue
            # (the same two pieces as an earlier reading: as found then)
            # (and numbered from the same holes, and read as near)
            key = (i, j, ids[i], ids[j], firsts[i], firsts[j], tol, near)
            got = memo.get(key) if memo is not None else None
            if got is None:
                got = (compare(i, j), layers[i], layers[j])
                if memo is not None:
                    memo[key] = got
            pairs.append(got[0])
    return holes, owner, pairs


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
    it is the layout's own, and so of the main piece alone: ``cells``
    (``solid`` gives more, the sheets' after them), ``hole_panel`` and
    ``hole_flat`` (for the first of ``holes``, which are the main
    piece's), ``frames``, ``hinges`` ...

    ``outline`` is the main piece's outline as ``fold_piece`` was given
    it; without one it is put together again from the layout's panels
    (``outline_of``). ``name``, ``key`` and ``leather`` are the main
    piece's, as a ``Sheet`` has its own.

    The sheets are settled as they are taken (``settle`` fills them in),
    and the stack is a picture of them as they then lie. Everything it
    shows is its own record of them -- each one's outline, height, rank,
    thickness, the face it is on, which way up it is, its holes -- so
    sheets that are changed or laid again afterwards (a piece moved,
    turned over, another order, another stack) do not change it, and
    want a new ``FlatStack``. ``thickness_of`` and ``grain_of`` give a
    piece's thickness and way up as the stack has them. With no
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
        # (with the outline as it was given, where it was: how closely
        # the layout follows it is part of what there is to tell)
        self.notes = _lay(outline if outline is not None
                          and len(outline) >= 3 else self.outline,
                          self.outline, layout.thickness, self.sheets)
        # each sheet as it was laid for this stack: its underside, its
        # top, its rank, its convex parts, where its grain is laid from,
        # the face it is on, which way up it is and how thick
        self._laid = [(s.lo, s.hi, s.rank, s.parts, s.frame, s.side,
                       s.grain, s.thickness) for s in self.sheets]
        # ... and all of them as the hole check wants a piece
        self._layers = [_Layer(self.outline, layout.cutouts, layout.holes,
                               name)] \
            + [_Layer(list(s.ring), list(s.cutouts), list(s.holes), s.name)
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

    def thickness_of(self, piece: int) -> float:
        """How thick a piece of the stack is, as it was laid."""
        if 0 < piece <= len(self._laid):
            return self._laid[piece - 1][7]
        return self.thickness

    def grain_of(self, piece: int) -> int:
        """Which way up a piece of the stack is, as it was laid: 1 when
        its grain side faces the way the main piece's front does (as the
        main piece's own does), else -1."""
        if 0 < piece <= len(self._laid):
            return self._laid[piece - 1][6]
        return 1

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
                    ln = math.hypot(dx, dy)
                    if ln < 1e-12:
                        c.sides.append(None)   # no edge: no wall, no plane
                        continue
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
        if self._laid[k - 1][5] > 0:
            return lift + lo, lift + hi
        return -(lift + hi), -(lift + lo)

    def _outermost(self, side: int, p: Pt, apart: float):
        """The outermost leather at a point of the pattern, on one face of
        the main piece: ``(where its outer face is, which piece)``."""
        best = None
        for k, laid in enumerate(self._laid, 1):
            hi, parts = laid[1], laid[3]
            if laid[5] == side and (best is None or hi > best[0]) \
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
            z0, z1 = self._span(c.tag[2], apart)
            up, down = (FRONT, BACK) if self._laid[c.tag[2] - 1][6] > 0 \
                else (BACK, FRONT)
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
                if c.sides[i] is None:
                    continue
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
        check = laid_check(self._layers, tol, near)
        for found, rings in ((check.landed, check.rings),
                             (check.through, check.blanks)):
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
    line it, or the part it was cut from along the main piece's edge, is
    cut along, by the hinge nearest it -- so that all of a liner past a
    flap's fold line goes with the flap beside it, however far the flap
    is cut into the body -- else on
    what it rests on there (``_follow``), else on the nearest panel. Its
    own edge on a fold line decides only where it lies on the fold's
    hinge. A part as near two hinges whose panels differ (over the gap
    between two flaps, past the end of a fold that parts them) is cut
    along every fold line that runs between those hinges, and each piece
    is seated again.

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
        regions = [(bit, (pts, flags)) for pts, flags in regions
                   for bit in _edged(layout, pts, flags)]
        for (pts, flags), whole in regions:
            for q, f in _parted(layout, pts, flags):
                over = _over(layout, q, f)
                if over:
                    mine.append([max(over, key=over.get), q, f, "on"])
                    continue
                panel = _flagged(layout, q, f, hinged=True)
                if panel is not None:
                    mine.append([panel, q, f, "cut"])
                    continue
                # (a bit cut off along the main piece's edge, or cut along
                # a fold line only past the end of its hinge, keeps the
                # side of the fold line the part it was cut from lies on,
                # by the hinge nearest the bit itself. A bit as near two
                # hinges that go different ways -- over the gap between
                # two flaps, past the end of the fold that parts them --
                # is cut along the fold line between them, and each piece
                # goes with the hinge beside it, however the stack lies)
                todo, rounds = [(q, f)], 0
                while todo:
                    q, f = todo.pop()
                    near = _flagged(layout, *whole, near=q, ties=True)
                    if near is None:
                        mine.append([None, q, f, "free"])
                        continue
                    pieces = [(q, f)]
                    rounds += 1
                    if len({panel for panel, _h in near}) > 1 \
                            and rounds <= 16:
                        mids = []
                        for _panel, h in near:
                            ha, he, _hl = layout.lines[h.fold]
                            m = 0.5 * (h.lo + h.hi)
                            mids.append((ha[0] + m * he[0], ha[1] + m * he[1]))
                        # (along every such line, not the first listed:
                        # cut along all, the pieces are the same whatever
                        # the order of the fold list)
                        for fi, line in enumerate(layout.lines):
                            if line is None:
                                continue
                            a, e, _ln = line
                            ends = [(r[1] - a[1]) * e[0] - (r[0] - a[0]) * e[1]
                                    for r in mids]
                            if min(ends) < -1e-6 < 1e-6 < max(ends):
                                pieces = [b for pts, flags in pieces
                                          for b in pc.cut(pts, flags, a, e,
                                                          None, fi)]
                    if len(pieces) >= 2:
                        todo += pieces
                    else:
                        mine.append([near[0][0], q, f, "cut"])
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


def _bare(layout, pts, flags) -> bool:
    """Whether a part of a sheet lies, more than a hair wide, over none of
    the main piece's leather -- standing past its edge, or over a slit or
    a window in it."""
    box = pc.bounds(pts)
    under = [q for panel in layout.panels
             if box[0] < panel.box[2] and panel.box[0] < box[2]
             and box[1] < panel.box[3] and panel.box[1] < box[3]
             for q in panel.parts]
    mine = [q for q, _f in pc.convex_parts(pts, flags)]
    return bool(_minus(mine, under, HAIR) if under else mine)


def _edged(layout, pts, flags, rounds: int = 32):
    """A part of a sheet that lies over two panels of the main piece,
    and partly over none of its leather, cut along the main piece's edge
    until each piece lies over one panel's leather and not past its
    edge, or over none of it: so that what stands past the edge goes
    where it goes by itself, not where a hair of it lying over a body
    takes it (a liner over a flap cut into its body by slits). The edges
    of the panel it lies least over are tried first: that is the hair of
    it. (A part over one panel and past its edge to begin with stays
    whole: it goes with that panel.)"""
    over = _over(layout, pts, flags)
    if len(over) < 2 or not _bare(layout, pts, flags):
        return [(pts, flags)]
    todo = [(pts, flags, over)]
    out = []
    for _round in range(rounds):
        if not todo:
            break
        P, F, over = todo.pop()
        if not over or len(over) == 1 and not _bare(layout, P, F):
            out.append((P, F))
            continue
        done = None
        for p in sorted(over, key=over.get):
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
                if len(pieces) >= 2:
                    done = pieces
                    break
            if done:
                break
        if done is None:
            out.append((P, F))
        else:
            todo += [(a, b, _over(layout, a, b)) for a, b in done]
    return out + [(P, F) for P, F, _o in todo]


def _joined(a, b) -> float:
    """How long a stretch of edge two outlines share (mm)."""
    got = 0.0
    na, nb = len(a), len(b)
    for i in range(na):
        p, q = a[i], a[(i + 1) % na]
        ln = math.dist(p, q)
        if ln < 1e-9:
            continue
        e = ((q[0] - p[0]) / ln, (q[1] - p[1]) / ln)
        for j in range(nb):
            r, s = b[j], b[(j + 1) % nb]
            if abs((r[1] - p[1]) * e[0] - (r[0] - p[0]) * e[1]) > 1e-6 \
                    or abs((s[1] - p[1]) * e[0] - (s[0] - p[0]) * e[1]) \
                    > 1e-6:
                continue
            tr = (r[0] - p[0]) * e[0] + (r[1] - p[1]) * e[1]
            ts = (s[0] - p[0]) * e[0] + (s[1] - p[1]) * e[1]
            got += max(0.0, min(ln, max(tr, ts)) - max(0.0, min(tr, ts)))
    return got


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


def _flagged(layout, pts, flags, near=None, hinged: bool = False,
             ties: bool = False):
    """The panel on the side of a fold line that a part of a sheet was cut
    along (None for a part cut along none): the one the hinge nearest
    along the line has there. Of the edges cut along fold lines, the one
    nearest a hinge along its line goes: a part cut along two lines, one
    of them only where it runs on past the end of its hinge, goes by the
    other. Given ``near``, a bit of the part, the hinge nearest that bit
    goes instead of the one nearest the edge -- of two flaps on one fold
    line, the one beside the bit; of two lines, the one it stands by.

    An edge that runs along its line only past the end of a hinge is not
    an edge on that hinge: of two as near (a bit over the gap between two
    flaps, past the end of the fold that parts them), one on a hinge goes
    first. Given ``hinged``, only an edge on a hinge goes (None if none
    is). Given ``ties``, every ``(panel, hinge)`` as near as the one that
    goes, that one first, and of those only the ones on a hinge if it is
    on one, past one if it is past."""
    n = len(pts)
    best = []
    for k in range(n):
        fi = flags[k]
        if fi < 0:
            continue
        a, e, _ln = layout.lines[fi]
        p, q = pts[k], pts[(k + 1) % n]
        sp = (p[0] - a[0]) * e[0] + (p[1] - a[1]) * e[1]
        sq = (q[0] - a[0]) * e[0] + (q[1] - a[1]) * e[1]
        lo, hi, aside = min(sp, sq), max(sp, sq), 0.0
        edge = (lo, hi)
        if near is not None:
            along = [(r[0] - a[0]) * e[0] + (r[1] - a[1]) * e[1]
                     for r in near]
            lo, hi = min(along), max(along)
            aside = min(abs((r[1] - a[1]) * e[0] - (r[0] - a[0]) * e[1])
                        for r in near)
        for h in layout.hinges:
            if h.fold == fi:
                off = math.hypot(
                    max(0.0, max(h.lo, lo) - min(h.hi, hi)), aside)
                past = min(h.hi, edge[1]) - max(h.lo, edge[0]) <= 1e-6
                if hinged and past:
                    continue
                # (it runs along the line: the part lies to its left)
                panel = h.left if sq > sp else h.right
                best.append((off, past, panel, h))
    if not best:
        return None
    least = min(b[0] for b in best)
    best.sort(key=lambda b: (b[0] > least + 1e-6, b[1], b[0]))
    if ties:
        return [(b[2], b[3]) for b in best
                if b[0] <= least + 1e-6 and b[1] == best[0][1]]
    return best[0][2]


def _follow(layout, regions, k: int) -> None:
    """Give each part of sheet ``k`` that lies over no leather of the main
    piece and was cut along no fold (``how`` "free") the panel of what it
    rests on there: of the parts of the sheets laid before it on its face,
    the one it lies most over. A part over none of them goes with the part
    of its own sheet over the main piece's leather that it is most joined
    to; one joined to none, with the panel on its side of the nearest
    fold line (``_sided``). ``regions`` are the sheets' parts as
    ``_seated`` has them, those before ``k`` already given theirs."""
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
            # (resting on nothing there: with the leather of its own it is
            # most joined to over the main piece's, through whatever of
            # its own stands past the edge with it)
            best = _joined_to(regions[k - 1], part)
        if best is None:
            best = _sided(layout, part[1])
        part[0] = best


def _joined_to(mine, part) -> Optional[int]:
    """The panel of what a part of a sheet over nothing of the main piece
    is most joined to: of the sheet's parts over the main piece's leather
    (``mine``), the panel the run of free parts it belongs to shares the
    most edge with. None for a run joined to none."""
    free = [p for p in mine if p[3] == "free"]
    run, todo = [part], [part]
    while todo:
        one = todo.pop()
        for other in free:
            if not any(other is r for r in run) \
                    and _joined(one[1], other[1]) > 1e-6:
                run.append(other)
                todo.append(other)
    shares: Dict[int, float] = {}
    for one in run:
        for other in mine:
            if other[3] != "free":
                shared = _joined(one[1], other[1])
                if shared > 1e-6:
                    shares[other[0]] = shares.get(other[0], 0.0) + shared
    return max(shares, key=shares.get) if shares else None


def _sided(layout, pts) -> int:
    """The panel a part of a sheet joined to nothing goes with: the one
    on its side of the nearest fold line, else the nearest panel."""
    c = pc.centroid(pts)
    best = None
    for h in layout.hinges:
        a, e, _ln = layout.lines[h.fold]
        al = (c[0] - a[0]) * e[0] + (c[1] - a[1]) * e[1]
        u = (c[1] - a[1]) * e[0] - (c[0] - a[0]) * e[1]
        off = max(0.0, h.lo - al, al - h.hi)
        far = math.hypot(u, off)
        if best is None or far < best[0]:
            best = (far, h.left if u > 0.0 else h.right)
    if best is not None:
        return best[1]
    return min(layout.panels, key=lambda p: _to_ring(c, p.pts)).index


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
            # (without the edges of no length clipping leaves where a
            # corner of the cell lies on the band's edge: such an edge
            # points any way at all, and would put a point well inside
            # the part outside it)
            got = pc.clip_to_convex(c.pts, rect)
            got, _f = pc.tidy(got, [0] * len(got))
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
    # (the cells are left for the judge, which looks at the stack shut
    # too: see ``judge``)
    memo: dict = {}
    layout._poses = memo
    posed = layout._posed(layout.fractions(float(layout.n_steps)),
                          memo=memo)
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
    # (the leather a needle goes through is only told of a stack that
    # is shut: every fold that is made closes flat)
    shut = all(math.radians(min(f.angle, 180.0)) >= fs._FLAT
               for k, f in enumerate(layout.folds) if layout.steps[k])
    got = _through(layout, posed) if shut else None
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


_CROSSING = 4         # outlines crossing a box of the still panel's leather
#                       few enough to cut it by them all (``_through``)
_CUTS = 64            # ... the most cuts along an outline's side on the way
#                       down to one box, before boxes are only halved
_BOXES = 4000         # ... and the most boxes it looks at before it gives up


class _TooMany(Exception):
    """``_through`` has looked at ``_BOXES`` boxes and gives no answer."""


def _through(layout, posed):
    """``(most, least)``: the leather a needle square to the still panel
    goes through (mm), over the still panel's own flat leather, in the
    shut stack ``posed`` (``Layout._posed``). None for no such leather.

    The still panel's flat leather is cut by the outline, seen from
    above, of every cell over it, into pieces each under one set of
    cells: every place with a different set of layers over it is one of
    them. A cell lying level adds its thickness to a piece wholly, but
    for the windows and other cut-outs in it, where a needle goes
    through no leather of it; one lying aslant adds what a needle meets
    of it, which changes as a plane does across each part of it that
    lies under one of its faces and over another (``_aslant``) -- so the
    most and the least are at a corner of a piece. Only cells over the
    still panel's leather are looked at, boxes first. None as well where
    that takes more than ``_BOXES`` boxes: no figure rather than a wrong
    one."""
    base = layout.base
    frames = layout.frames(layout.fractions(float(layout.n_steps)))
    # (the cells of one sheet lying level on one panel are mostly one
    # convex piece of it, cut up only where other pieces' outlines cross
    # it: they are taken as that one piece wherever they make up all of
    # its hull, which keeps the count of pieces down)
    groups: Dict[tuple, list] = {}
    tilted = []
    for c, cell in posed:
        xs = [v[0] for v in cell.verts]
        ys = [v[1] for v in cell.verts]
        foot = _hull(list(zip(xs, ys)))
        if len(foot) < 3 or abs(pc.area(foot)) < 1e-9:
            continue                   # (it stands on edge: no needle in it)
        level = all(abs(f.plane[2]) < 1e-9 or abs(f.plane[2]) > 1 - 1e-9
                    for f in cell.faces)
        if not level:
            tilted.append(cell)
            continue
        zs = [v[2] for v in cell.verts]
        key = (c.sheet, c.panel, c.hinge, round(min(zs), 6),
               round(max(zs), 6))
        groups.setdefault(key, []).append((c, foot))
    flats = []                         # (foot, box, thickness, holes, base)
    for key, got in groups.items():
        thick = key[4] - key[3]
        holes = []
        if key[2] is None:
            fr = frames[key[1]]
            holes = [pc.ccw([fs.place(fr, q)[:2] for q in ring])
                     for c, _f in got for ring in c.polys]
        whole = None
        if len(got) > 1:
            whole = _hull([q for _c, f in got for q in f])
            if abs(abs(pc.area(whole)) - sum(abs(pc.area(f))
                                             for _c, f in got)) \
                    > 1e-6 * max(1.0, abs(pc.area(whole))):
                whole = None
        feet = [whole] if whole is not None else [f for _c, f in got]
        is_base = key[0] == 0 and key[1] == base and key[2] is None
        for foot in feet:
            flats.append((foot, pc.bounds(foot), thick, holes, is_base))
    pieces = [(foot, fb, 0.0, ()) for foot, fb, _t, _h, b in flats if b]
    if not pieces:
        return None
    region = (min(b[0] for _p, b, _g, _o in pieces),
              min(b[1] for _p, b, _g, _o in pieces),
              max(b[2] for _p, b, _g, _o in pieces),
              max(b[3] for _p, b, _g, _o in pieces))
    under = [(p, b) for p, b, _g, _o in pieces]

    def over(foot, fb):
        return (fb[0] < region[2] and region[0] < fb[2]
                and fb[1] < region[3] and region[1] < fb[3]
                and any(b[0] < fb[2] and fb[0] < b[2] and b[1] < fb[3]
                        and fb[1] < b[3]
                        and pc.convex_overlap(foot, p, 1e-9)
                        for p, b in under))

    levels: Dict[tuple, list] = {}
    for foot, fb, thick, holes, _b in flats:
        if not over(foot, fb):
            continue
        bits = [foot]
        if holes:
            # (its windows, where they lie shut)
            bits = _minus(bits, holes, 1e-7)
        for bit in bits:
            key = tuple((round(x, 9), round(y, 9)) for x, y in bit)
            if key not in levels:
                levels[key] = [bit, 0.0]
            levels[key][1] += thick
    # (an outline, the box round it, the most leather a needle meets
    # under it and, of a cell lying aslant, the plane that says how much)
    feet = [(foot, pc.bounds(foot), mm, None)
            for foot, mm in levels.values()]
    for cell in tilted:
        for foot, lin in _aslant(cell):
            fb = pc.bounds(foot)
            if over(foot, fb):
                feet.append((foot, fb, max(_at(lin, q) for q in foot), lin))
    best = [0.0, math.inf]             # (most, least) so far

    def take(v):
        if v > 1e-6:
            best[0], best[1] = max(best[0], v), min(best[1], v)

    def leaf(polys, got, part, cover):
        # (the leather in the box cut by every outline that crosses it)
        pieces = [(p, pc.bounds(p), got, cover) for p in polys]
        for foot, fb, mm, lin in part:
            nxt = []
            for poly, pb, have, on in pieces:
                if not (pb[0] < fb[2] and fb[0] < pb[2] and pb[1] < fb[3]
                        and fb[1] < pb[3]) or \
                        not pc.convex_overlap(poly, foot, 1e-9):
                    nxt.append((poly, pb, have, on))
                    continue
                inner = pc.clip_to_convex(poly, foot)
                if len(inner) >= 3 and abs(pc.area(inner)) > 1e-9:
                    inner = pc.ccw(inner)
                    if lin is None:
                        nxt.append((inner, pc.bounds(inner), have + mm, on))
                    else:
                        nxt.append((inner, pc.bounds(inner), have,
                                    on + (lin,)))
                nxt += [(rest, pc.bounds(rest), have, on)
                        for rest in _minus([poly], [foot], 1e-7)]
            pieces = nxt
        for poly, _pb, have, on in pieces:
            if not on:
                take(have)
                continue
            for q in poly:
                take(have + sum(_at(lin, q) for lin in on))

    looked = [0]

    def look(box, polys, got, part, cover, depth, cuts):
        # (a box of the still panel's leather: the outlines that cover
        # all the leather in it add to all of it -- a cell lying aslant
        # what its plane says, most and least at a corner of that
        # leather; while many cross it, and what it holds could still be
        # the most or the least, it is cut in two)
        looked[0] += 1
        if looked[0] > _BOXES:
            raise _TooMany()
        x0, y0, x1, y1 = box
        sq = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        polys = [q for q in (pc.clip_to_convex(p, sq) for p in polys)
                 if len(q) >= 3 and abs(pc.area(q)) > 1e-9]
        if not polys:
            return
        # (the box drawn in round the leather left in it)
        corners = [q for poly in polys for q in poly]
        x0 = max(x0, min(x for x, _y in corners))
        x1 = min(x1, max(x for x, _y in corners))
        y0 = max(y0, min(y for _x, y in corners))
        y1 = min(y1, max(y for _x, y in corners))
        crossing, up = [], 0.0
        cover = list(cover)
        for f in part:
            foot, fb, mm, lin = f
            if fb[2] <= x0 or x1 <= fb[0] or fb[3] <= y0 or y1 <= fb[1]:
                continue
            if all(pc.inside_convex(q, foot) for q in corners):
                if lin is None:
                    got += mm
                else:
                    cover.append(lin)
                continue
            # (one that only touches the leather here cuts nothing off
            # it: ``leaf`` asks the same of each piece)
            if not any(pc.convex_overlap(q, foot, 1e-9) for q in polys):
                continue
            crossing.append(f)
            up += mm
        low = high = got
        if cover:
            sums = [got + sum(_at(lin, q) for lin in cover)
                    for q in corners]
            low, high = min(sums), max(sums)
        if high + up <= best[0] and low >= best[1]:
            return
        if len(crossing) <= _CROSSING or depth >= 16:
            leaf(polys, got, crossing, tuple(cover))
            return
        # (cut along the side of an outline that crosses it, the one
        # nearest the middle: a box whose sides lie along the outlines'
        # own is covered whole or missed by each, and most outlines of a
        # stack lie along a few lines; halved where no side lies in it)
        wide = x1 - x0 >= y1 - y0
        m = None
        if cuts < _CUTS:
            for across in (wide, not wide):
                lo, hi = (x0, x1) if across else (y0, y1)
                k = 0 if across else 1
                sides = [v for f in crossing for v in (f[1][k], f[1][k + 2])
                         if lo + 1e-7 < v < hi - 1e-7]
                if sides:
                    mid = 0.5 * (lo + hi)
                    m, wide = min(sides, key=lambda v: abs(v - mid)), across
                    cuts, depth = cuts + 1, depth - 1
                    break
        if m is None:
            m = 0.5 * (x0 + x1) if wide else 0.5 * (y0 + y1)
        if wide:
            halves = ((x0, y0, m, y1), (m, y0, x1, y1))
        else:
            halves = ((x0, y0, x1, m), (x0, m, x1, y1))
        for half in halves:
            look(half, polys, got, crossing, cover, depth + 1, cuts)

    try:
        look(region, [p for p, _b, _g, _o in pieces], 0.0, feet, (), 0, 0)
    except _TooMany:
        return None
    most, least = best
    return (most, least) if most > 0.0 else None


def _aslant(cell):
    """A convex cell lying aslant, seen from above, as the convex pieces
    of its outline across each of which a needle meets as much of it as
    a plane says: ``(piece, (a, b, c))`` for ``a * x + b * y + c`` mm at
    ``(x, y)``. Each piece lies under one of the cell's faces turned up
    and over one turned down -- a slab of leather tipped up is one piece
    its full thickness, measured upright, and a narrow one along each
    edge where the needle comes out through its side."""
    ups, downs = [], []
    for f in cell.faces:
        nx, ny, nz, d = f.plane
        if abs(nz) < 1e-9:
            continue                   # (it stands on edge)
        ring = pc.ccw([(q[0], q[1]) for q in f.pts])
        if len(ring) < 3 or abs(pc.area(ring)) < 1e-12:
            continue
        (ups if nz > 0.0 else downs).append(
            (ring, (-nx / nz, -ny / nz, d / nz)))
    out = []
    for top, (a1, b1, c1) in ups:
        for bottom, (a0, b0, c0) in downs:
            piece = pc.clip_to_convex(top, bottom)
            if len(piece) >= 3 and abs(pc.area(piece)) > 1e-9:
                out.append((pc.ccw(piece), (a1 - a0, b1 - b0, c1 - c0)))
    return out


def _at(lin, q) -> float:
    """What the plane ``lin`` of ``_aslant`` says at the point ``q``."""
    return lin[0] * q[0] + lin[1] * q[1] + lin[2]


_EIGHTHS = 8        # moments looked at inside a step: eighths of it, and
#                     a sixteenth in from each end, where a piece parted at
#                     the end of a hinge comes into itself
_HALVINGS = 8       # ... and how closely each two's deepest is closed in on
_ONSET = 8          # ... and the moment each two are first found deeper
#                     than ``foldsolid._TOUCH``: halvings of the gap from the
#                     moment looked at before it, an eighth of a step at
#                     most, so to within a two-thousandth of a step


def judge(layout) -> List[Note]:
    """What the judge (``clash``) finds in the stack as it folds, as notes
    that name the two pieces and say how deep: more than ``foldsolid.
    _TOUCH`` (0.05 mm), always.

    At rest -- at each whole step of the folding -- every two cells are
    tried, exactly. On the way, inside each step, the two cells the step
    moves against each other (``_bodies``) are tried at eighths of the
    step and a sixteenth in from each end, and each two found into each
    other there closed in on round the deepest moment found, between the
    moments looked at either side (a golden section search,
    ``_HALVINGS`` steps of it). So each note says two depths: ``rest``,
    the deepest at any whole step (0 for what is so only on the way), and
    ``a``, the deepest at any moment looked at, with ``b`` the first
    moment it is deeper than ``_TOUCH`` (1.0 the end of step 1, 0.5 half
    way through it): the first moment looked at where it is, closed in
    on from the moment looked at before it, where it is not, by halving
    the gap between them (``_ONSET`` times), the two cells found there
    and any others of the same two pieces found with them tried at each
    -- so to within a two-thousandth of a step. ``last`` is the last
    whole step at which it is deeper than ``_TOUCH``, at rest.

    ``"clash"``: two pieces. What a sheet does to itself, or the main
    piece to it, where the sheet is noted slit or lies over a crossing of
    folds is that note's to say: its ``b`` is made the deepest of it, its
    ``rest`` the deepest at rest, and no clash is given. ``"limit"``: the
    main piece in itself -- a known limit of folding one piece, crossing
    folds and a tray's corners, no fault of what is put on it. Where a
    fold has moved off its line, the main piece's own two parts that the
    piece folded with every fold on its line does not have as deep (the
    layout's ``_drawn``) are a ``"landing"`` instead: the move's doing.
    Never per frame: a layout runs it once, when it is made. The notes
    of the stack (``layout.notes``) must hold its slit and crossing notes
    already."""
    from . import clash
    n = layout.n_steps
    least = fs._TOUCH
    drawn = getattr(layout, "_drawn", None) if any(layout.shift) else None
    runs = getattr(layout, "_slit_runs", [])
    spots = [(note.sheet, note.at, note.fold, note.other[0])
             for note in layout.notes if note.kind == "crossing"]
    # what was found, by what it is: [deepest, first moment, deepest at
    # rest, and for a landing how deep the two are folded on their lines,
    # the two cells found at the first moment, the last whole step]
    found: Dict[tuple, list] = {}
    poses: Dict[float, tuple] = {}
    lined: Dict[float, list] = {}
    # (cells on panels lying still, from moment to moment -- and the
    # stack shut, as ``_shut`` left it)
    memo: dict = layout.__dict__.pop("_poses", None) or {}

    def order(tag):
        return (tag[0] != "panel", tag[1])

    def pose(t):
        if t not in poses:
            fr = layout.fractions(t)
            frames = layout.frames(fr)
            poses[t] = (frames, layout._arcs(frames, fr), {})
        return poses[t]

    def one(c, t):
        frames, arcs, rides = pose(t)
        if c.sheet:
            lifts = (c.low, c.high)
            if lifts not in rides:
                rides[lifts] = [fs._ride(arc, *lifts) for arc in arcs]
            return layout._cell(c, frames, rides[lifts], None, lifts)
        return layout._cell(c, frames, arcs)

    def say(ca, cb, deep, t, whole):
        a, b = sorted((ca.sheet, cb.sheet))
        was = 0.0
        if a == b == 0 and drawn is not None:
            ta, tb = sorted((ca.tag, cb.tag), key=order)
            if t not in lined:
                lined[t] = drawn._posed(drawn.fractions(t), sheets=False)
            was = max((clash.depth(x, y) for c2, x in lined[t]
                       if c2.tag == ta for d2, y in lined[t]
                       if d2.tag == tb and y is not x), default=0.0)
            key = ("landing", ta, tb) if was < deep - least \
                else ("found", 0, 0)
        elif b and a in (0, b):
            key = _parted_there(layout, b, [c for c in (ca, cb)
                                            if c.sheet == b], runs, spots)
            key = ("found", a, b) if key is None else key
        else:
            key = ("found", a, b)
        got = found.setdefault(key, [0.0, t, 0.0, 0.0, [], 0.0])
        if deep > got[0]:
            got[0], got[3] = deep, was
        if t < got[1]:
            got[1], got[4] = t, []
        if t == got[1]:
            got[4].append((ca, cb))
        if whole:
            got[2] = max(got[2], deep)
            got[5] = max(got[5], t)

    # at rest: every two cells at each whole step
    for step in range(n + 1):
        posed = layout._posed(layout.fractions(float(step)), memo=memo,
                              lazy=True)
        for deep, i, j in clash.pairs([x for _c, x in posed], least):
            say(posed[i][0], posed[j][0], deep, float(step), True)
    # on the way: what each step moves against each other, at eighths of
    # the step, and each two found into each other closed in on round its
    # deepest moment
    inner = sorted({k / _EIGHTHS for k in range(1, _EIGHTHS)}
                   | {0.5 / _EIGHTHS, 1.0 - 0.5 / _EIGHTHS})
    for step in range(1, n + 1):
        follow: Dict[tuple, list] = {}
        for k in inner:
            t = step - 1 + k
            posed = layout._posed(layout.fractions(t), memo=memo, lazy=True)
            body = _bodies(layout, step, [c for c, _x in posed])

            for g, i, j in clash.pairs([x for _c, x in posed], least,
                                       bodies=body):
                ca, cb = posed[i][0], posed[j][0]
                say(ca, cb, g, t, False)
                key = (id(ca), id(cb))
                if key not in follow or g > follow[key][0]:
                    follow[key] = [g, t, ca, cb]
        for g, t, ca, cb in follow.values():

            def depth_at(x, ca=ca, cb=cb):
                one_a, one_b = one(ca, x), one(cb, x)
                if one_a is None or one_b is None:
                    return 0.0
                return clash.depth(one_a, one_b)

            # (between the moments looked at either side of it)
            at = t - (step - 1)
            lo = step - 1 + max([0.0] + [x for x in inner if x < at - 1e-12])
            hi = step - 1 + min([1.0] + [x for x in inner if x > at + 1e-12])
            r = 0.5 * (math.sqrt(5.0) - 1.0)
            x1, x2 = hi - r * (hi - lo), lo + r * (hi - lo)
            f1, f2 = depth_at(x1), depth_at(x2)
            best, when = (f1, x1) if f1 > f2 else (f2, x2)
            for _half in range(_HALVINGS):
                if f1 > f2:
                    hi, x2, f2 = x2, x1, f1
                    x1 = hi - r * (hi - lo)
                    f1 = depth_at(x1)
                    if f1 > best:
                        best, when = f1, x1
                else:
                    lo, x1, f1 = x1, x2, f2
                    x2 = lo + r * (hi - lo)
                    f2 = depth_at(x2)
                    if f2 > best:
                        best, when = f2, x2
            if best > g:
                say(ca, cb, best, when, False)
    # the moment each is first deeper than ``least``, closed in on between
    # the first moment it was found and the moment looked at before it
    looked = sorted({float(step) for step in range(n + 1)}
                    | {step - 1 + k for step in range(1, n + 1)
                       for k in inner})
    for key, got in found.items():
        if key[0] in ("slit", "crossing") or got[1] <= 0.0:
            continue
        lo = max(x for x in looked if x < got[1] - 1e-12)
        hi = got[1]

        def deepest(x, two=got[4]):
            cells = [(one(ca, x), one(cb, x)) for ca, cb in two]
            return max((clash.depth(p, q) for p, q in cells
                        if p is not None and q is not None), default=0.0)

        for _half in range(_ONSET):
            mid = 0.5 * (lo + hi)
            if deepest(mid) > least:
                hi = mid
            else:
                lo = mid
        got[1] = hi
    # the depths at slits and crossings, into their notes
    count = {"slit": 0, "crossing": 0}
    for m, note in enumerate(layout.notes):
        if note.kind in count:
            got = found.get((note.kind, count[note.kind]))
            count[note.kind] += 1
            if got is not None:
                layout.notes[m] = note._replace(b=got[0], rest=got[2])
    out = []
    for key, (deep, t, rest, was, _two, last) in sorted(
            found.items(), key=lambda kv: -kv[1][0]):
        if key[0] == "found":
            a, b = key[1:]
            out.append(Note("limit" if (a, b) == (0, 0) else "clash", a, -1,
                            deep, t, (b,), rest=rest, last=last))
        elif key[0] == "landing":
            ta, tb = key[1:]
            out.append(Note("landing", 0, layout._blame(ta, tb), deep, t,
                            (ta, tb), c=was, rest=rest, last=last))
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
    well (see there), and ``totals``: how many of the whole stack's holes
    meet read flat and read shut."""

    __slots__ = ("readings", "totals")

    def __init__(self, found: LaidCheck):
        super().__init__()
        for name in ("findings", "state", "lands", "rings", "sewn",
                     "apart", "passes", "blanks", "landed", "through"):
            setattr(self, name, getattr(found, name))
        self.readings: Dict[int, Tuple[int, int, bool]] = {}
        self.totals: Tuple[int, int] = (0, 0)


@dataclass
class Faced(fs.Lineup):
    """Two pieces of a stack that the folding brings face to face, with
    stitch holes in both where they do: a ``foldsolid.Lineup`` with ``a``
    and ``b`` the pieces (0 the main piece), ``names`` what they are
    called and ``panels`` the numbers of the main piece's panels each
    lies on. Its counts are of what the two do with each other, as two
    panels of a folded piece are counted; ``lone`` are those of its holes
    that have nothing at all to meet (no state). ``red`` is how many of
    the holes it counts as meeting are marked ``OFF`` all the same,
    because something else on their needle line is out of line, and
    ``numbers`` the holes it counts. ``laid`` is False: they are not laid
    on each other, the fold brings them together."""

    names: Tuple[str, str] = ("", "")
    laid: bool = False
    panels: Tuple[int, int] = (0, 0)
    red: int = 0
    numbers: frozenset = field(default=frozenset(), repr=False,
                               compare=False)


def stack_check(layout, tol: float = 0.75, near: float = 4.0):
    """The stitch holes of a stack: what ``Layout.lineup`` gives for one.

    The stack is read twice, whole, through its layers (``laid_check``):
    FLAT, every piece as it lies, and SHUT, every piece that goes round a
    bend (a ``"cross"`` or ``"level"`` note) with its holes where they
    land when the stack is shut (``_landed``). In each reading the pieces
    the folding brings face to face are then compared as
    ``Layout.lineup`` compares two panels, each with its holes where that
    reading has them (``_Faces.read``), and said in ``Faced`` findings
    (``_Faces._told``: two pieces next to each other on a needle line,
    and two panels of the main piece that miss each other, whatever lies
    between them; two further apart only for a miss that none of those
    says, the two furthest out of line first, then those with the least
    leather between them; a finding says all its two do with each other,
    so a miss can be said by two). Two holes that lie
    on one needle line meet, whatever leather lies between them: leather
    on it with no hole there is told (``through``, ``passes``) and never
    keeps two holes from meeting. Every hole is then given what its whole
    stack of holes comes to, through the layers and across the folds
    alike: any miss in it makes every hole of it ``OFF``. Each finding
    counts what its two pieces do with each other, and how many of those
    it says meet are marked out of line all the same (``red``).

    "The folding brings together" means what it means for one piece
    folded on itself: two faces no further apart, folded right up, than
    ``Layout.lineup`` lets two panels be (that reach is not made longer
    by the piles). Two halves that shut with more than that between them
    -- a wallet over its pockets -- are not taken to be sewn to each
    other: their holes are not compared, joined or marked by each other.
    So a needle line may carry, beyond that reach, two stacks of holes
    with different states, or holes with none.

    The marks show the reading in which more holes meet (flat, where as
    many do), and ``readings`` counts the holes as the marks show them:
    for each piece that goes round a bend and has holes, ``(how many of
    its holes meet flat, how many shut, whether the shut reading is the
    one shown)`` -- the last the same for every piece -- and ``totals``
    the whole stack's, flat and shut. The spots it marks are sorted by
    cell for ``solid``. A ``StackCheck``."""
    own = layout._own_holes
    main = _Layer(layout.outline, layout.cutouts, layout.holes[:own],
                  getattr(layout, "name", ""))
    layers = [main] + list(layout.sheets)
    first = own
    starts = {}
    for k, s in enumerate(layout.sheets, 1):
        starts[k] = first
        first += len(s.holes)

    def met(found, k):
        mine = range(starts[k], starts[k] + len(layout.sheets[k - 1].holes))
        return sum(1 for i in mine if found.state.get(i) == fs.MEETS)

    memo: dict = {}             # each two pieces, read once
    faces = _Faces(layout, tol, near)

    def read(shut):
        given = [shut.get(k, layer) for k, layer in enumerate(layers)]
        check = laid_check(given, tol, near, memo)
        faces.read(check, given, memo)
        return check

    # each piece that goes round a bend, with its holes as they land when
    # it is held each way it may be (``_landed``): read whole, as the marks
    # are made -- through the layers and across the folds -- with the
    # others held at their middles, the way most of its own holes meet is
    # the one it is read shut in; of two as good, the way more of the
    # whole stack's do; of those, the first (the middle)
    holds = {k: _landed(layout, k) for k in range(1, len(layers))}
    holds = {k: got for k, got in holds.items() if got}
    middle = {k: got[0] for k, got in holds.items()}
    # (only piece k's own pairs change from one way of holding it to the
    # next: the stacks the other pieces' holes make among themselves, and
    # which of those have a miss in them, are found once)
    given = [middle.get(k, layer) for k, layer in enumerate(layers)]
    pairs = _compared(given, tol, near, memo)[2] if holds else []
    shut: Dict[int, _Layer] = {}
    for k, ways in holds.items():
        mine = range(starts[k], starts[k] + len(layout.sheets[k - 1].holes))
        others: Dict[int, int] = {}        # the other pieces' stacks
        seen = set()
        bad = []
        met_, missed = faces.links(given, k, False)
        for i, j, met2, missed2, _lone, _found in pairs:
            if k not in (i, j):
                met_ += [(h, h2, 0.0) for h, h2 in met2]
                missed += [(h, h2, 0.0) for h, h2 in missed2]
        for h, h2, _d in met_:
            seen.update((h, h2))
            others[_top(others, h)] = _top(others, h2)
        for h, _h2, _d in missed:
            seen.add(h)
            bad.append(h)
        rest = {h: _top(others, h) for h in others}
        bad = {rest.get(h, h) for h in bad}
        best, most = ways[0], None
        for way in ways:
            held = [{**middle, k: way}.get(i, layer)
                    for i, layer in enumerate(layers)]
            link: Dict[int, int] = {}      # those stacks, joined by k's
            met_, missed = faces.links(held, k, True)
            for _i, _j, met2, missed2, _lone, _found in _compared(
                    held, tol, near, memo, k)[2]:
                met_ += [(h, h2, 0.0) for h, h2 in met2]
                missed += [(h, h2, 0.0) for h, h2 in missed2]
            got = set(seen)
            for h, h2, _d in met_:
                got.update((h, h2))
                link[_top(link, rest.get(h, h))] = \
                    _top(link, rest.get(h2, h2))
            got.update(h for h, _h2, _d in missed)
            out = {_top(link, h) for h in bad} \
                | {_top(link, rest.get(h, h)) for h, _h2, _d in missed}
            meets = {h for h in got if _top(link, rest.get(h, h)) not in out}
            score = (len(meets.intersection(mine)), len(meets))
            if most is None or score > most:
                best, most = way, score
        shut[k] = best
    flat = read({})
    if shut:
        closed = read(shut)
        shown = len(closed.holes(fs.MEETS)) > len(flat.holes(fs.MEETS))
    else:
        closed, shown = flat, False
    check = closed if shown else flat
    readings = {k: (met(flat, k), met(closed, k), shown) for k in shut}
    check = StackCheck(check)
    check.readings = readings
    check.totals = (len(flat.holes(fs.MEETS)), len(closed.holes(fs.MEETS)))
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


def _landed(layout, k: int) -> List["_Layer"]:
    """Sheet ``k`` with its holes where they land when the stack is shut,
    for each way it may be held: ``[_Layer]``, the first held at its
    middle -- its two ends coming equally far from where they lie flat --
    and then held on each panel it lies on in turn. Empty for a sheet
    without holes or that goes round no bend.

    The steps between the panels it lies on are fixed by the folds: round
    each bend it has that note's length more leather than the room it
    has (less, outside the bend), and what lies beyond the bend from
    where it is held is pushed that much further from the fold line (on
    the inside; drawn nearer, outside). Where along its length it is held
    is the one thing not fixed.

    What is not modelled: a hole that lies on the curve of a bend when
    the stack is shut, punched there or landed there. Through the layers
    it is compared as if its leather lay flat; across the folds it is
    not compared at all (``_Faces.holes`` takes only holes on flat
    leather)."""
    s = layout.sheets[k - 1]
    over = {note.fold: note.a for note in layout.notes
            if note.sheet == k and note.kind in ("cross", "level")}
    if not s.holes or not over:
        return []
    up = {h.child: h for h in layout.tree}

    def chain(p):
        out = [p]
        while out[-1] in up:
            out.append(up[out[-1]].parent)
        return out

    def push(hold, p):
        """How far a point on panel ``p`` lands from where it lies, the
        sheet held on panel ``hold``."""
        a, b = chain(hold), chain(p)
        top = next(q for q in a if q in b)
        dx = dy = 0.0
        for q in b[:b.index(top)] + a[:a.index(top)]:
            h = up[q]
            mm = over.get(h.fold)
            if not mm:
                continue
            c2 = (-h.e[1], h.e[0])          # to the child's side
            way = 1.0 if p in h.moves else -1.0
            dx += way * mm * c2[0]
            dy += way * mm * c2[1]
        return dx, dy

    panels = sorted({panel for panel, _q, _f in layout._regions[k - 1]})
    where = []
    for x, y, _d in s.holes:
        on = next((panel for panel, q, _f in layout._regions[k - 1]
                   if pc.inside((x, y), q)), None)
        if on is None:
            on = min(layout._regions[k - 1],
                     key=lambda r: _to_ring((x, y), r[1]))[0]
        where.append(on)

    def held(shifts):
        return _Layer(s.ring, s.cutouts,
                      [(x + shifts[p][0], y + shifts[p][1], d)
                       for (x, y, d), p in zip(s.holes, where)], s.name)

    # the two panels furthest apart round the bends: held at its middle,
    # it lands half way between being held on the one and on the other
    ends = max(((a, b) for a in panels for b in panels),
               key=lambda ab: (math.hypot(*push(ab[0], ab[1])), ab))
    mid = {p: tuple(0.5 * (u + v) for u, v in zip(push(ends[0], p),
                                                  push(ends[1], p)))
           for p in panels}
    out = [held(mid)]
    for hold in panels:
        out.append(held({p: push(hold, p) for p in panels}))
    return out


class _Faces:
    """The bodies of leather of a folded stack (``bodies``) that the
    folding brings face to face, found once for all its readings: each
    two on different panels, both of pieces with holes, whose planes lie
    together, folded right up, whose faces are no further apart than
    ``Layout.lineup`` lets two panels be (that reach is not made longer
    by the piles), and that overlap where they do, whatever lies between
    them. ``pairs`` has each as ``(one, other, parts of the one, parts of
    the other on its plane, the bodies between them)``, each body by its
    number in ``found``. What is worked out for a piece as one reading
    has it -- its holes on each body, those over the other of a pair, how
    near each comes to the other's -- is kept for the next reading that
    has that piece the same (the same object, unchanged)."""

    def __init__(self, layout, tol: float, near: float):
        self.layout, self.tol, self.near = layout, tol, near
        self.found = list(bodies(layout)) if layout.n_steps else []
        self.pairs: List[tuple] = []
        self.kept: Dict[tuple, object] = {}
        self.maps: Dict[tuple, tuple] = {}
        own = layout._own_holes
        self.firsts = [0]
        self.owner = [0] * own
        for k, s in enumerate(layout.sheets, 1):
            self.firsts.append(len(self.owner))
            self.owner += [k] * len(s.holes)
        self.names = [getattr(layout, "name", "") or ""] \
            + [s.name for s in layout.sheets]
        if not self.found:
            return
        self.frames = frames = layout.frames([1.0] * len(layout.folds))
        t = layout.thickness
        reach = max(3.0 * t, 3.0) + 0.01 - t       # face to face
        self.cells: Dict[tuple, list] = {}
        for c in layout.sheet_cells:
            if c.hinge is None:
                self.cells.setdefault((c.sheet, c.panel), []).append(c)
        holed = [bool(layout.sheets[k - 1].holes) if k else any(
            layout.hole_panel[i] == p and layout.hole_flat[i]
            for i in range(own)) for p, _parts, _lo, _hi, k in self.found]
        for i, (pa, parts_a, lo_a, hi_a, _ka) in enumerate(self.found):
            fa = frames[pa]
            for j in range(i + 1, len(self.found)):
                pb, parts_b, lo_b, hi_b, _kb = self.found[j]
                if pa == pb or not holed[i] or not holed[j]:
                    continue
                fb = frames[pb]
                if abs(fs._dot(fa[3], fb[3])) < 0.966:
                    continue

                def onto(p3, fr=fa):           # into panel a's pattern plane
                    v = (p3[0] - fr[0][0], p3[1] - fr[0][1],
                         p3[2] - fr[0][2])
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
                lo_gap, hi_gap = (hi_a, span_b[0]) if span_b[0] >= hi_a \
                    else (span_b[1], lo_a)
                self.pairs.append((i, j, parts_a, flat_b, _between(
                    layout, self.found, frames, fa, onto, lo_gap, hi_gap,
                    parts_a, flat_b, i, j)))

    def mapping(self, i: int, j: int) -> tuple:
        """Body ``j``'s pattern plane into body ``i``'s, folded right up:
        ``(c, u, w)``, a point ``(x, y)`` going to ``c + x u + y w``."""
        got = self.maps.get((i, j))
        if got is None:
            (o, ex, ey, _n), (oa, e1, e2, _na) = \
                self.frames[self.found[j][0]], self.frames[self.found[i][0]]
            v = (o[0] - oa[0], o[1] - oa[1], o[2] - oa[2])
            got = self.maps[i, j] = (
                (fs._dot(v, e1), fs._dot(v, e2)),
                (fs._dot(ex, e1), fs._dot(ex, e2)),
                (fs._dot(ey, e1), fs._dot(ey, e2)))
        return got

    def holes(self, layers, i: int) -> list:
        """The holes of body ``i`` as the pieces ``layers`` have them --
        the main piece's on its panel, a sheet's where they are on its
        leather there (so, for one the shut reading has moved, on the
        body they land on) -- as ``[(number, (x, y))]`` on its plane."""
        panel, _parts, _lo, _hi, piece = self.found[i]
        layer = layers[piece]
        key = ("holes", i, id(layer))
        got = self.kept.get(key)
        if got is None:
            first = self.firsts[piece]
            if piece:
                cells = self.cells.get((piece, panel), [])
                got = [(first + n, (h[0], h[1]))
                       for n, h in enumerate(layer.holes)
                       if _cell_at(cells, h[0], h[1]) is not None]
            else:
                lay = self.layout
                got = [(n, (h[0], h[1])) for n, h in enumerate(layer.holes)
                       if lay.hole_panel[n] == panel and lay.hole_flat[n]]
            self.kept[key] = got
        return got

    def over(self, layers, pair) -> Tuple[list, list]:
        """The holes of each body of ``pair`` that lie over the other, on
        the first body's plane."""
        i, j, parts_a, flat_b, _between = pair
        key = ("over", i, j, id(layers[self.found[i][4]]))
        mine = self.kept.get(key)
        if mine is None:
            mine = self.kept[key] = [
                (k, p) for k, p in self.holes(layers, i)
                if any(pc.inside_convex(p, B, 0.0) for B in flat_b)]
        key = ("over", j, i, id(layers[self.found[j][4]]))
        theirs = self.kept.get(key)
        if theirs is None:
            theirs = self.kept[key] = [
                (k, p) for k, p in _mapped(self.holes(layers, j),
                                           self.mapping(i, j))
                if any(pc.inside_convex(p, A, 0.0) for A in parts_a)]
        return mine, theirs

    def onto(self, layers, n: int, i: int) -> dict:
        """The holes of body ``n`` on body ``i``'s plane, by square
        (``_squares``)."""
        key = ("onto", n, i, id(layers[self.found[n][4]]))
        got = self.kept.get(key)
        if got is None:
            got = self.kept[key] = _squares(
                _mapped(self.holes(layers, n), self.mapping(i, n)),
                self.near)
        return got

    def compare(self, layers, pair, mine, theirs) -> list:
        """Each hole of either body of ``pair`` that lies within ``near``
        of one of the other's: ``[(hole, where, the other, how far)]``,
        ``where`` on the first body's plane."""
        i, j = pair[0], pair[1]
        ids = (id(layers[self.found[i][4]]), id(layers[self.found[j][4]]))
        key = ("compare", i, j) + ids
        here = self.kept.get(key)
        if here is None:
            here = self.kept[key] = []
            near = self.near
            for one, other, n in ((mine, theirs, 1), (theirs, mine, 0)):
                skey = ("squares", i, j, n, ids[n])
                squares = self.kept.get(skey)
                if squares is None:
                    squares = self.kept[skey] = _squares(other, near)
                for k, p in one:
                    d, k2 = _nearest(squares, p, near)
                    if d <= near:
                        here.append((k, p, k2, d))
        return here

    def links(self, layers, k: int, mine: bool) -> Tuple[list, list]:
        """The holes that meet and miss across the folds, ``(hole, the
        other, how far)``, of the pairs with piece ``k`` in them, or, not
        ``mine``, of the others."""
        met: List[tuple] = []
        missed: List[tuple] = []
        for pair in self.pairs:
            if (k in (self.found[pair[0]][4], self.found[pair[1]][4])) \
                    != mine:
                continue
            over = self.over(layers, pair)
            if not over[0] or not over[1]:
                continue
            for h, _p, h2, d in self.compare(layers, pair, *over):
                (met if d <= self.tol else missed).append((h, h2, d))
        return met, missed

    def read(self, check, layers, memo: Optional[dict] = None) -> None:
        """Join to ``check``, what ``laid_check`` found of the pieces
        ``layers``, what the folding brings face to face, each piece's
        holes where ``layers`` has them: a hole of either of two bodies
        face to face that comes down on a hole of the other meets it, and
        one that comes down near one but not on it misses it, as in
        ``laid_check``; then every hole is given what its whole stack of
        holes comes to, through the layers and across the folds alike
        (``_stacked``); and the findings (``_told``)."""
        tol = self.tol
        met: List[tuple] = []          # (hole, the hole it meets, how far)
        missed: List[tuple] = []       # (hole, the hole it misses, how far)
        got = []
        for pair in self.pairs:
            mine, theirs = self.over(layers, pair)
            if not mine or not theirs:
                continue
            here = self.compare(layers, pair, mine, theirs)
            for k, _p, k2, d in here:
                (met if d <= tol else missed).append((k, k2, d))
            got.append((pair, mine, theirs, here))
        stacks = _stacked(check, layers, met, missed, tol, self.near, memo)
        self._told(check, layers, got, *stacks)

    def crossed(self, layers, pair, here) -> list:
        """For each hole of ``here``, the bodies between the two of
        ``pair`` that it comes through, by the rule of ``laid_check``: on
        that body, and where its piece has leather (``_has_leather``, on
        its own plane: more than ``HAIR`` inside its outline and outside
        its cutouts). Each as ``(the body, whether it has a hole within
        ``near`` there, where on its plane)``. (Whether a hole comes
        through a body, and where, is the same whichever pair it is of:
        found once, as the first pair to ask has it.)"""
        i, j, between = pair[0], pair[1], pair[4]
        key = ("crossed", i, j, id(layers[self.found[i][4]]),
               id(layers[self.found[j][4]])) \
            + tuple(id(layers[self.found[n][4]]) for n in between)
        got = self.kept.get(key)
        if got is not None:
            return got
        got = self.kept[key] = [[] for _h in here]
        near, kept, owner = self.near, self.kept, self.owner
        for n in between:
            _panel, parts, _lo, _hi, piece = self.found[n]
            key2 = ("parts", n, i)
            flat = kept.get(key2)
            if flat is None:
                (c0, c1), (u0, u1), (w0, w1) = self.mapping(i, n)
                flat = kept[key2] = [pc.ccw([
                    (c0 + x * u0 + y * w0, c1 + x * u1 + y * w1)
                    for x, y in part]) for part in parts]
            squares = self.onto(layers, n, i)
            (c0, c1), (u0, u1), (w0, w1) = self.mapping(n, i)
            layer = layers[piece]
            mine = ("through", n, id(layer))
            # (a spot more than a hair inside a part of the body is in it,
            # and more than a hair inside the outline the part is of: only
            # one nearer an edge, or by a cutout, is looked at closely)
            sides = kept.get(("sides", n, i))
            if sides is None:
                sides = kept["sides", n, i] = [[
                    (x0, y0, x1 - x0, y1 - y0)
                    for (x0, y0), (x1, y1) in zip(C[-1:] + C[:-1], C)]
                    for C in flat]
            if layer.cutouts:
                sides = []
            for at, (k, p, _k2, _d) in enumerate(here):
                key3 = (mine, k, id(layers[owner[k]]))
                hit = kept.get(key3)
                if hit is None:
                    hit = kept[key3] = ()
                    q = (c0 + p[0] * u0 + p[1] * w0,
                         c1 + p[0] * u1 + p[1] * w1)
                    if _deep(p, sides) or any(
                            pc.inside_convex(p, C, 0.0) for C in flat) \
                            and _has_leather(layer, q):
                        hit = kept[key3] = (
                            n, _nearest(squares, p, near)[0] <= near, q)
                if hit:
                    got[at].append(hit)
        return got

    def bare(self, layers, pair, here) -> List[bool]:
        """For each hole of ``here``, whether it meets or misses the other
        body's with no leather between them there that has a hole near it
        (``crossed``): the two bodies are then next to each other on its
        needle line."""
        return [not bodies or not any(holed for _n, holed, _q in bodies)
                for bodies in self.crossed(layers, pair, here)]

    def _told(self, check, layers, got, link=None, laid=()) -> None:
        """The findings of a reading, and the holes that come through
        leather between two bodies face to face where it has no hole.

        Two bodies are next to each other on a needle line where nothing
        lies between them there but leather with no hole near it (which
        has no say), and two pieces laid on each other always are. Two
        bodies get a finding where nothing at all lies between them, and
        where they are next to each other on the needle line of some hole
        of theirs that meets or misses one of the other's -- unless every
        miss between them is where holed leather lies between them (with
        a hole near either of the two holes that miss). Two panels of the
        main piece, the pattern the others are cut to, get one whenever
        they miss each other, whatever lies between them. The others get
        one only for a miss that none of those findings, nor the layers,
        says: where every two next to each other on that needle line line
        up and these two do not (``link`` are the stacks of holes that
        meet, ``laid`` the misses through the layers, as ``_stacked``
        gives them; a finding says a miss of the stacks of the two holes
        it counts out of line). Stack by stack: for each stack of holes
        with such a miss and none said, the two bodies furthest out of
        line there get the finding, and of those as far as they are, to
        within ``AS_FAR``, the two with the least leather between them
        (all that lies between their two bodies, over the whole of where
        they overlap). Two bodies found for one stack are not thereby
        found for another, where two others may be further out of line.
        And none is left out if some hole of theirs marked out of line
        would then be said by no line.

        A finding says all that its two do with each other: a miss of
        theirs that a finding of two next to each other also says is
        then said twice.

        Each counts what the two do with each other: of the holes of
        either that lie over the other, those that come down near one of
        the other's, missing it or meeting it, and those that have nothing
        at all to meet (no state); and how many of those that meet are
        marked out of line all the same (``red``). A hole of either that
        meets or misses one of the other's through leather between them
        where it has no hole near is told (``passes``, ``through``)."""
        tol, state = self.tol, check.state
        layout, names = self.layout, self.names
        link = {} if link is None else link
        said, far = [], []
        found = self.found
        for item in got:
            pair, _mine, _theirs, here = item
            bare = self.bare(layers, pair, here)
            # (two holes that miss each other have holed leather between
            # them if it has a hole near either of them)
            apart = {frozenset((k, k2)) for (k, _p, k2, d), next_
                     in zip(here, bare) if d > tol and not next_}
            nexts = {next_ and frozenset((k, k2)) not in apart
                     for (k, _p, k2, d), next_ in zip(here, bare)
                     if d > tol}
            # (the main piece's own panels, the pattern the others are cut
            # to, are said whenever they miss each other)
            own = not found[pair[0]][4] and not found[pair[1]][4] \
                and bool(nexts)
            if not pair[4] or own or any(bare) and nexts != {False}:
                said.append(item)
            else:
                far.append(item)
        rest = []
        # (the misses of the pairs further apart, by the stack of each of
        # their two holes: how far, the leather between the two bodies,
        # which pair)
        misses: Dict[int, list] = {}
        for at, item in enumerate(far):
            leather = sum(found[n][3] - found[n][2] for n in item[0][4])
            for k, _p, k2, d in item[3]:
                if d > tol:
                    for top in {_top(link, k), _top(link, k2)}:
                        misses.setdefault(top, []).append((d, leather, at))
        named = set()
        if misses:
            told = set()                # the stacks a line says a miss in
            for h, h2 in list(laid) + [
                    (h, h2) for _pr, _m, _t, here in said
                    for h, _p, h2, d in here if d > tol]:
                told.update((_top(link, h), _top(link, h2)))
            # (a stack with none said: its furthest miss alone is then
            # said, not every miss of those two bodies -- in another stack
            # two others may be further out of line, and are the ones to
            # say there. Misses within AS_FAR of the furthest are as far:
            # a tolerance about the furthest, not a rounding, so that no
            # last digit chooses between two pairs whose holes coincide)
            for top, here in misses.items():
                if top not in told:
                    furthest = max(here)[0]
                    named.add(min((leather, at) for d, leather, at in here
                                  if d >= furthest - AS_FAR)[1])
        for at, item in enumerate(far):
            (said if at in named else rest).append(item)
        kept = set(check.sewn)
        for _pair, _mine, _theirs, here in said:
            kept.update(k for k, _p, _k2, _d in here)
        for item in rest:
            if any(state.get(k) == fs.OFF and k not in kept
                   for k, _p, _k2, _d in item[3]):
                said.append(item)
                kept.update(k for k, _p, _k2, _d in item[3])
        for pair, mine, theirs, here in got:
            if not any(pair is p for p, _m, _t, _h in said):
                continue
            i, j = pair[0], pair[1]
            ka, kb = self.found[i][4], self.found[j][4]
            linked = {k for k, _p, _k2, _d in here}
            lone = [k for k, _p in mine + theirs
                    if k not in linked and state.get(k) is None]
            if not linked and not lone:
                continue
            off = [d for _k, _p, _k2, d in here if d > tol]
            meets = [(k, d) for k, _p, _k2, d in here if d <= tol]
            check.findings.append(Faced(
                ka, kb, len(here) + len(lone), len(off), max(off, default=0.0),
                len(lone), max((d for _k, d in meets), default=0.0),
                (names[ka], names[kb]), False,
                (layout.panels[self.found[i][0]].number,
                 layout.panels[self.found[j][0]].number),
                sum(state.get(k) == fs.OFF for k, _d in meets),
                frozenset(linked.union(lone))))
        # the holes of either of two bodies face to face that meet or miss
        # one of the other's through leather between them where it has no
        # hole near: told, as those through the layers are, and ringed
        # where they come through it
        spots = {(piece, round(x, 3), round(y, 3))
                 for piece, where in check.through.items()
                 for x, y, _d in where}
        for pair, _mine, _theirs, here in got:
            crossed = self.crossed(layers, pair, here)
            for n in pair[4]:
                panel, piece = self.found[n][0], self.found[n][4]
                for (k, _p, _k2, _d), bodies in zip(here, crossed):
                    at = next((q for n2, holed, q in bodies
                               if n2 == n and not holed), None)
                    if at is None:
                        continue
                    x, y = at
                    whose = self.owner[k]
                    check.passes.setdefault(
                        (whose, piece, layout.panels[panel].number
                         if whose == piece else 0), set()).add(k)
                    if (piece, round(x, 3), round(y, 3)) not in spots:
                        spots.add((piece, round(x, 3), round(y, 3)))
                        dia = layers[whose].holes[k - self.firsts[whose]][2]
                        check.through.setdefault(piece, []).append(
                            (x, y, dia))


def _deep(p, sides) -> bool:
    """Whether ``p`` is more than ``HAIR`` inside one of some
    counter-clockwise convex polygons, each given by its sides as ``(x,
    y, dx, dy)``."""
    px, py = p
    for edges in sides:
        for x0, y0, dx, dy in edges:
            c = dx * (py - y0) - dy * (px - x0)
            if c <= 0.0 or c * c <= HAIR * HAIR * (dx * dx + dy * dy):
                break
        else:
            return True
    return False


def _mapped(holes, cuw) -> list:
    """Holes ``[(number, (x, y))]`` taken by ``(c, u, w)`` (see
    ``_Faces.mapping``) into another plane."""
    (c0, c1), (u0, u1), (w0, w1) = cuw
    return [(k, (c0 + p[0] * u0 + p[1] * w0, c1 + p[0] * u1 + p[1] * w1))
            for k, p in holes]


def _squares(holes, near: float) -> Dict[tuple, list]:
    """Holes, ``[(number, (x, y))]``, by square of side ``near``: one
    within ``near`` of a spot is in its square or the eight round it."""
    out: Dict[tuple, list] = {}
    for k, q in holes:
        out.setdefault((math.floor(q[0] / near), math.floor(q[1] / near)),
                       []).append((k, q))
    return out


def _nearest(squares, p, near: float) -> Tuple[float, int]:
    """The hole nearest ``p`` of those within ``near`` of it, of holes by
    square (``_squares``): ``(how far, its number)``, the lower number of
    two as near; ``(2 * near, -1)`` with none."""
    x, y = p[0], p[1]
    gx, gy = math.floor(x / near), math.floor(y / near)
    best = (2.0 * near, -1)
    for ix in (gx - 1, gx, gx + 1):
        for iy in (gy - 1, gy, gy + 1):
            for k, q in squares.get((ix, iy), ()):
                d = math.hypot(x - q[0], y - q[1])
                if d < best[0] or d == best[0] and k < best[1]:
                    best = (d, k)
    return best


def _stacked(check, layers, met, missed, tol: float, near: float,
             memo: Optional[dict] = None) -> Tuple[dict, list]:
    """Give every hole of a reading what its whole stack of holes comes
    to, as ``laid_check`` does, with the holes that meet and miss across
    the folds -- ``met`` and ``missed``, ``(hole, the other, how far)`` --
    joined to those that do through the layers: two holes that lie on one
    needle line meet, whatever lies between them, and every hole of a
    stack with a miss in it is ``OFF`` (``apart``: the furthest miss in
    it). Each ``Laid`` finding of ``check`` keeps what its two pieces do
    with each other, with how many of the holes it says meet are now
    marked out of line (``red``). Gives the stacks (each hole to one in
    its stack, as ``_top`` reads them) and the misses through the layers,
    ``[(hole, the one it misses)]``."""
    holes, _owner, pairs = _compared(layers, tol, near, memo)
    link: Dict[int, int] = {}
    stated = set()
    misses = list(missed)
    for k, k2, _d in met:
        link[_top(link, k)] = _top(link, k2)
        stated.update((k, k2))
    laid = []
    laid_misses = []
    for _i, _j, met_, missed_, _lone, found in pairs:
        for k, k2 in met_:
            link[_top(link, k)] = _top(link, k2)
            stated.update((k, k2))
        for k, k2 in missed_:
            laid_misses.append((k, k2))
            misses.append((k, k2, math.hypot(holes[k][0] - holes[k2][0],
                                             holes[k][1] - holes[k2][1])))
        if found is not None:
            laid.append((found, met_))
    wide: Dict[int, float] = {}        # each stack: its furthest miss
    for k, _k2, d in misses:
        stated.add(k)
        top = _top(link, k)
        wide[top] = max(wide.get(top, 0.0), d)
    check.state.clear()
    check.apart.clear()
    for h in stated:
        top = _top(link, h)
        if top in wide:
            check.state[h] = fs.OFF
            check.apart[h] = wide[top]
        else:
            check.state[h] = fs.MEETS
    check.findings = [replace(found, red=sum(
        check.state[k] == fs.OFF for k, _k2 in met_))
        for found, met_ in laid]
    return link, laid_misses


def _between(layout, found, frames, fa, onto, lo, hi, parts_a, flat_b,
             one, two) -> List[int]:
    """The bodies of leather (``found``, by number) that lie between two
    of them, ``one`` and ``two``, that face each other (heights ``lo`` to
    ``hi`` along the first's normal) where both are."""
    out = []
    for n, (pc_, parts, low, high, _piece) in enumerate(found):
        if n in (one, two):
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
            out.append(n)
    return out

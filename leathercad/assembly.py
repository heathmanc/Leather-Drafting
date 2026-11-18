"""Putting pieces together: which pieces of a drawing lie on which, and where.

The drawing stays the cutting layout, its pieces drawn apart. Where a piece
lies in the finished thing is kept on the piece itself (``Shape.placed``, a
``shapes.Placement``) and is never read from where the piece is drawn. The
pieces that share a ``placed.of`` are one *stack*: a pile, bottom first by
``placed.order``, of which one is the *main piece*. The others are placed
against the main piece, and the stack folds along its fold lines.

Three frames are in play.

* The *drawing*: world mm, as every shape's ``world_polyline`` gives them.
* A piece's *own frame*: its outline as drawn with its place and turn on
  the drawing taken off and its mirror kept,
  ``own = R(-rotation) (world - (x, y))``. Moving a piece, turning it and
  Nest on sheet change nothing in it. Everything that belongs to the piece
  (cutouts, slits and scored lines, its fold lines, its stitch holes) goes
  through the same map.
* The *bench*: the plane the stack lies on, seen from above. A piece's
  outline there is ``T + R(turn) M(own)``, where ``M`` mirrors x when the
  piece lies mirrored (``over != flip``); its grain is up unless ``over``.
  The middle of the main piece's box is the bench's origin.

A piece is placed by its *box*, the bounding box of ``R(turn) M(own)``: the
point ``(ax, ay)`` of its box lies ``(x, y)`` from the same point of the
main piece's box. So ``ax = 0, x = 0`` is "left edges level", ``ax = 1``
right edges, ``ax = 0.5`` centred, and what is level stays level when
either piece changes size on the drawing.

``stacks`` reads a drawing into its stacks, each piece as it lies on the
bench. ``offered`` and ``drawn_inside`` say which outlines can be put on.
The rest are the changes a window makes: put a piece on, take it off, move
it, line it up, turn it. Each changes only the shapes of the document it is
given; all that is returned is plain data. While a stack's main piece is
cut open on the drawing (``main_cut_open``), those that would change the
places or the order of its pieces do nothing. Pure Python, no Qt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import polycut as pc
from .shapes import Placement, _next_id, carry_placement

Pt = Tuple[float, float]
Box = Tuple[float, float, float, float]
# a map of the plane, (a, b, c, d, e, f): x' = a x + b y + e and
# y' = c x + d y + f
Map = Tuple[float, float, float, float, float, float]

# an edge this near level (mm) with the main piece's is level with it: after
# a move the piece is tied to that edge, and stays level when either changes
FLUSH = 0.005
# a fold line ending this near (mm) a piece's outline ends on it: what
# ``foldsolid.split_piece`` snaps at
SNAP = 0.3
# the least share of the piece it is drawn in that lets an outline read as
# a cutout be put on as a piece: a pocket drawn on its panel is a third of
# it or more, a buckle slot or a rivet hole a hundredth
OFFER_SHARE = 0.15
# drawn overlapping the main piece by more than this (mm2), a piece is put
# on where it is drawn against it
AS_DRAWN = 1.0
# to lie wholly on another piece, a piece's box lies within the other's (to
# ``FLUSH``) and this share of it is on the other's leather: a square
# corner over a rounded one still counts, a piece in a ring's hole does not
# (a cutout is no leather)
ON_SHARE = 0.9

# a closed outline round less than this (mm2) encloses no leather: it is no
# piece, and is never put on a stack
NO_AREA = 1e-6

SIDES = ("left", "centre", "right", "bottom", "middle", "top")


# -- maps and boxes -----------------------------------------------------------

def _cs(degrees: float) -> Tuple[float, float]:
    """Cosine and sine of a turn, exact at the quarter turns (so a piece
    turned 90 degrees has edges as level as it had)."""
    q = degrees / 90.0
    r = round(q)
    if abs(q - r) < 1e-12:
        return ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))[int(r) % 4]
    a = math.radians(degrees)
    return math.cos(a), math.sin(a)


def _norm(degrees: float) -> float:
    """A turn brought into 0 <= turn < 360, a quarter turn made exact."""
    t = float(degrees) % 360.0
    q = round(t / 90.0)
    if abs(t - 90.0 * q) < 1e-9:
        t = 90.0 * (q % 4)
    return t + 0.0


def map_point(m: Map, p) -> Pt:
    """Where the map ``m`` takes the point ``p``."""
    x, y = p[0], p[1]
    return (m[0] * x + m[1] * y + m[4], m[2] * x + m[3] * y + m[5])


def _then(first: Map, second: Map) -> Map:
    """The one map that does ``first`` and then ``second``."""
    a, b, c, d, e, f = first
    g, h, i, j, k, m = second
    return (g * a + h * c, g * b + h * d, i * a + j * c, i * b + j * d,
            g * e + h * f + k, i * e + j * f + m)


def own_frame(shape) -> Map:
    """The map from the drawing to a piece's own frame: its place and turn
    on the drawing taken off, its mirror kept."""
    t = shape.transform
    c, s = _cs(-t.rotation)
    return (c, -s, s, c, -(c * t.x - s * t.y), -(s * t.x + c * t.y))


def _lie(turn: float, mirrored: bool) -> Map:
    """An own outline as it lies on the bench, before it is put in its
    place: turned over like a page if it lies mirrored, then turned."""
    c, s = _cs(turn)
    k = -1.0 if mirrored else 1.0
    return (c * k, -s, s * k, c, 0.0, 0.0)


def _box(pts: Sequence[Pt]) -> Box:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _open(ring: List[Pt]) -> List[Pt]:
    """A ring without its closing point said twice."""
    if len(ring) > 3 and math.dist(ring[0], ring[-1]) < 1e-9:
        return ring[:-1]
    return ring


def _overlap(a: Sequence[Pt], b: Sequence[Pt]) -> float:
    """The area (mm2) two outlines share, whichever way round they go."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    p, q = _box(a), _box(b)
    if p[2] <= q[0] or q[2] <= p[0] or p[3] <= q[1] or q[3] <= p[1]:
        return 0.0
    a, b = pc.ccw(a), pc.ccw(b)
    shared = 0.0
    for part, _flags in pc.convex_parts(b, [pc.OUTLINE] * len(b)):
        cut = pc.clip_to_convex(a, part)
        if cut:
            shared += max(0.0, pc.area(cut))
    return shared


def _off_ring(p: Pt, ring: Sequence[Pt]) -> float:
    """How far a point is from an outline (0 on it)."""
    best = float("inf")
    x0, y0 = ring[-1]
    for x1, y1 in ring:
        dx, dy = x1 - x0, y1 - y0
        ll = dx * dx + dy * dy
        t = 0.0
        if ll > 1e-18:
            t = ((p[0] - x0) * dx + (p[1] - y0) * dy) / ll
            t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        best = min(best, math.hypot(x0 + dx * t - p[0], y0 + dy * t - p[1]))
        x0, y0 = x1, y1
    return best


def _within(p: Pt, ring: Sequence[Pt], box: Box) -> bool:
    return (box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]
            and pc.inside(p, ring))


def _fine(p: Pt) -> Pt:
    """A point brought from the drawing into a piece's own frame, to a
    millionth of a millimetre: the same numbers wherever the piece and
    what is drawn on it are moved to together."""
    return (round(p[0], 6) + 0.0, round(p[1], 6) + 0.0)


# -- a piece in its own frame -------------------------------------------------

def _own_ring(shape) -> Optional[List[Pt]]:
    """A shape's outline in its own frame, as drawn (every point of the
    flattened path) -- or None if it encloses no leather (open, or closed
    round less than ``NO_AREA``: a rectangle of no width, three points in
    a line, a circle of no radius). Kept on the shape
    until its outline changes; not to be changed by the caller."""
    from .pieces import closes
    path = shape.local_path()
    flipped = bool(shape.transform.mirror_x)
    kept = shape.__dict__.get("_own_cache")
    if kept is not None and kept[0] is path and kept[1] == flipped:
        return kept[2]
    pts = path.flatten()
    ring = None
    if closes(pts, path.closed):
        k = -1.0 if flipped else 1.0
        ring = _open([(k * p.x + 0.0, p.y) for p in pts])
        if len(ring) < 3 or abs(pc.area(ring)) < NO_AREA:
            ring = None             # a closed line round no leather
    # the path is held, not its id: a later path can be given a dead
    # one's id, and would then pass for the outline kept here
    shape.__dict__["_own_cache"] = [path, flipped, ring, None]
    return ring


def _model_ring(shape) -> Optional[List[Pt]]:
    """The own outline made ready for the model, as the 3D preview makes
    one: counter-clockwise, a flattened curve thinned."""
    ring = _own_ring(shape)
    if ring is None:
        return None
    kept = shape.__dict__["_own_cache"]
    if kept[3] is None:
        from .foldsolid import model_outline
        kept[3] = model_outline(ring)
    return kept[3]


def own_outline(shape) -> List[Pt]:
    """A piece's outline in its own frame, every point of it as drawn
    (empty for a shape that encloses no leather). A piece's box is taken
    from this one, not from the thinned outline the model is built on."""
    return list(_own_ring(shape) or ())


def _fold_ends(shape):
    """The two ends of a fold line on the drawing, or None if it has no
    length: a fold line is used as the straight line between them."""
    pts = shape.world_polyline()[0]
    if len(pts) < 2:
        return None
    a, b = (pts[0].x, pts[0].y), (pts[-1].x, pts[-1].y)
    return (a, b) if math.dist(a, b) > 1e-6 else None


@dataclass
class Fold:
    """A fold line across a piece: the straight line from ``a`` to ``b``.

    ``direction`` is 1 to fold the faces turned to the eye together, -1
    the faces turned away: in a piece's own frame that is the direction
    drawn (1 for "front"), on the bench the direction it folds there,
    which is the other one when the piece lies mirrored. ``angle`` is in
    degrees, 0 to 180; ``radius`` the inside radius of its bend (below 0:
    none of its own); ``order`` its step in the folding (0: not set)."""

    a: Pt
    b: Pt
    angle: float = 90.0
    direction: int = 1
    radius: float = -1.0
    order: int = 0
    name: str = ""
    key: str = ""               # the fold line's shape id
    shape: object = None        # ... and the fold line itself


@dataclass
class Piece:
    """A piece of the drawing in its own frame, with all that belongs to
    it: its outline and cutouts as the 3D preview reads the piece it
    folds; a slit or scored line if it is the smallest piece both its ends
    lie on; a fold line as ``stacks`` gives them out; the stitch holes cut
    in it, by owner (its own, and those of no piece that lie on it)."""

    key: str                    # the shape id
    name: str
    shape: object               # the outline's Shape
    outline: List[Pt]           # counter-clockwise, thinned for the model
    box: Box                    # of the outline as drawn
    area: float
    cutouts: List[List[Pt]] = field(default_factory=list)
    marks: List[tuple] = field(default_factory=list)   # (kind, points)
    folds: List[Fold] = field(default_factory=list)
    holes: List[tuple] = field(default_factory=list)   # (x, y, diameter)


def _sort_folds(folds: List[Fold]) -> List[Fold]:
    """Left to right, top to bottom where they line up: the order the
    preview letters fold lines in."""
    folds.sort(key=lambda f: (round(0.5 * (f.a[0] + f.b[0]), 1),
                              -round(0.5 * (f.a[1] + f.b[1]), 1)))
    return folds


class _Drawing:
    """A drawing read once, as the 3D preview reads it: its closed outlines
    on cut layers, its slits and its scored or engraved lines, its fold
    lines. Which outlines are pieces, and what belongs to each of them, is
    worked out when first asked for."""

    def __init__(self, doc):
        from .pieces import on_cut_layer
        cut = on_cut_layer(doc)
        self.doc = doc
        self.rings, self.owners = [], []
        self.drawn = []             # slits, and lines scored or engraved
        self.folds = []
        for sh in doc.shapes:
            if getattr(sh, "is_fold_line", False):
                if _fold_ends(sh) is not None:
                    self.folds.append(sh)
                continue
            if getattr(sh, "construction", False):
                continue
            pts, _corners, closed = sh.world_polyline()
            if not cut(sh):
                if len(pts) >= 2:
                    line = [(p.x, p.y) for p in pts]
                    self.drawn.append(("line", line + ([line[0]] if closed
                                                       else [])))
            elif _own_ring(sh) is not None:     # it encloses leather
                self.rings.append(pts)
                self.owners.append(sh)
            elif len(pts) >= 2:
                self.drawn.append(("cut", [(p.x, p.y) for p in pts]))
        self.at = {id(sh): i for i, sh in enumerate(self.owners)}
        self._judged = None
        self._frames: Dict[int, Map] = {}
        self._boxes: Dict[int, Box] = {}
        self._models: Dict[int, List[Pt]] = {}
        self._marks = None
        self._folds = None
        self._loose = None
        self._mains = None

    # -- which outlines are pieces ---------------------------------------

    def judged(self):
        """For each outline: whether it is a piece, the outline it lies
        in (or None) and its area. As ``pieces.classify`` has it, but that
        an outline put on a stack is a piece wherever it is drawn -- and
        so the outlines drawn inside it are judged against a piece -- and
        that an outline drawn inside one that is no piece (a window cut in
        a panel, a pocket drawn on its panel and not put on yet) is no
        piece either: it can be put on only from ``drawn_inside``."""
        if self._judged is None:
            from .pieces import classify, is_own_piece
            _flags, parent, areas = classify(self.rings)
            n = len(self.rings)
            piece = [True] * n
            for i in sorted(range(n), key=lambda k: (areas[k], -k),
                            reverse=True):                  # outer first
                p = parent[i]
                if _stack_key(self.owners[i]) or p is None:
                    continue
                piece[i] = piece[p] and is_own_piece(areas[i], areas[p])
            self._judged = (piece, parent, areas)
        return self._judged

    def nested(self, i: int) -> bool:
        """Whether outline ``i`` is drawn inside an outline that is no
        piece and lies in a piece itself: a fob nested in the waste of a
        window cut in a panel, the thumb slot of a pocket drawn on its
        panel and not put on yet. It is offered from ``drawn_inside``,
        to be put on when it is asked for, and is never among the pieces
        put on as drawn (``drawn_on_main``)."""
        piece, parent, _areas = self.judged()
        p = parent[i]
        return (not piece[i] and p is not None and not piece[p]
                and parent[p] is not None and piece[parent[p]])

    def inside(self, i: int) -> bool:
        """Whether outline ``i``, read as a cutout, can still be put on:
        it is at least ``OFFER_SHARE`` of the outline it is drawn in (a
        pocket drawn on its panel, not a slot or a rivet hole), or it is
        ``nested`` in an outline that is no piece."""
        piece, parent, areas = self.judged()
        p = parent[i]
        return not piece[i] and p is not None and (
            areas[i] >= OFFER_SHARE * areas[p] - 1e-9 or self.nested(i))

    def pieces(self) -> List[int]:
        piece = self.judged()[0]
        return [i for i in range(len(self.rings)) if piece[i]]

    # -- a piece's own frame ----------------------------------------------

    def frame(self, i: int) -> Map:
        if i not in self._frames:
            self._frames[i] = own_frame(self.owners[i])
        return self._frames[i]

    def ring(self, i: int) -> List[Pt]:
        if i not in self._models:
            self._models[i] = _model_ring(self.owners[i])
        return self._models[i]

    def box(self, i: int) -> Box:
        if i not in self._boxes:
            self._boxes[i] = _box(self.ring(i))
        return self._boxes[i]

    def holds(self, i: int, world: Pt) -> bool:
        """Whether a point of the drawing lies on piece ``i``."""
        return _within(map_point(self.frame(i), world), self.ring(i),
                       self.box(i))

    # -- what belongs to which piece ---------------------------------------

    def marks(self, i: int) -> List[tuple]:
        """The slits and scored lines on piece ``i``, in its own frame: a
        line belongs to the smallest piece both its ends lie on."""
        if self._marks is None:
            areas = self.judged()[2]
            self._marks = {}
            for kind, pts in self.drawn:
                on = [k for k in self.pieces() if self.holds(k, pts[0])
                      and self.holds(k, pts[-1])]
                if on:
                    k = min(on, key=lambda k: (areas[k], -k))
                    self._marks.setdefault(k, []).append((kind, pts))
        m = self.frame(i)
        return [(kind, [_fine(map_point(m, p)) for p in pts])
                for kind, pts in self._marks.get(i, ())]

    def fold_owner(self, shape) -> Optional[int]:
        """The piece a fold line belongs to (None: it crosses no piece).
        It is the piece both its ends lie on the outline of; when they lie
        on the outlines of several (a liner as tall as the shell, drawn on
        it), the main piece of a stack they share, else the biggest of
        them. A fold line that reaches no outline at both ends is the
        smallest piece's it crosses."""
        from .foldsolid import FoldLine, split_piece
        a, b = _fold_ends(shape)
        areas = self.judged()[2]
        ends_on, crossed = [], []
        for i in self.pieces():
            m = self.frame(i)
            p, q = map_point(m, a), map_point(m, b)
            x0, y0, x1, y1 = self.box(i)
            if (max(p[0], q[0]) < x0 - SNAP or min(p[0], q[0]) > x1 + SNAP
                    or max(p[1], q[1]) < y0 - SNAP
                    or min(p[1], q[1]) > y1 + SNAP):
                continue
            ring = self.ring(i)
            if split_piece(ring, [FoldLine(p, q)])[1][0] is None:
                continue
            if _off_ring(p, ring) <= SNAP and _off_ring(q, ring) <= SNAP:
                ends_on.append(i)
            else:
                crossed.append(i)
        if len(ends_on) > 1:
            mains = self.mains()
            keys = [_stack_key(self.owners[i]) for i in ends_on]
            for i, key in zip(ends_on, keys):
                if key and mains.get(key) is self.owners[i] \
                        and keys.count(key) > 1:
                    return i
            return max(ends_on, key=lambda i: (areas[i], -i))
        if ends_on:
            return ends_on[0]
        if crossed:
            return min(crossed, key=lambda i: (areas[i], -i))
        return None

    def mains(self) -> dict:
        """The main piece of each stack of the drawing, by stack id."""
        if self._mains is None:
            self._mains = {key: pile.main.shape
                           for key, pile in _piles(self.doc).items()
                           if not pile.main.out}
        return self._mains

    def folds_of(self, i: int) -> List[Fold]:
        """The fold lines of piece ``i`` in its own frame, as drawn."""
        if self._folds is None:
            self._folds = {}
            for sh in self.folds:
                k = self.fold_owner(sh)
                if k is not None:
                    self._folds.setdefault(k, []).append(sh)
        m = self.frame(i)
        out = []
        for sh in self._folds.get(i, ()):
            a, b = _fold_ends(sh)
            out.append(Fold(
                _fine(map_point(m, a)), _fine(map_point(m, b)),
                angle=min(180.0, max(0.0, float(sh.fold_angle or 0.0))),
                direction=-1 if sh.fold_dir == "back" else 1,
                radius=float(getattr(sh, "fold_radius", -1.0)),
                order=int(getattr(sh, "fold_order", 0) or 0),
                name=sh.name or "", key=sh.shape_id, shape=sh))
        return _sort_folds(out)

    def holes(self, i: int) -> List[tuple]:
        """The stitch holes cut in piece ``i``, in its own frame: its own
        stitching (attached holes included), and the holes that belong to
        no piece and lie on its leather as drawn -- a seam's, loose holes,
        and the stitching of a shape that is not a piece (a stitched line
        drawn on it, a window stitched round). Never the stitching of
        another piece it happens to be drawn over, nor a hole that falls in
        one of its cutouts."""
        sh = self.owners[i]
        m = self.frame(i)
        try:
            out = _own_holes(sh, self.doc)
        except Exception:
            out = []
        if self._loose is None:
            piece = self.judged()[0]
            pieces = {id(self.owners[k]) for k in range(len(self.owners))
                      if piece[k]}
            self._loose = _loose_holes(self.doc, pieces)
        ring, box = self.ring(i), self.box(i)
        # (a hole off the piece's box on the drawing is not mapped at all)
        x0, y0, x1, y1 = _box([(p.x, p.y) for p in self.rings[i]])
        holes = []
        for x, y, dia in self._loose:
            if not (x0 - SNAP <= x <= x1 + SNAP
                    and y0 - SNAP <= y <= y1 + SNAP):
                continue
            p = _fine(map_point(m, (x, y)))
            if _within(p, ring, box):
                holes.append(p + (dia,))
        if holes:
            for cut in self.cutouts(i):
                cb = _box(cut)
                holes = [h for h in holes
                         if not _within(h[:2], cut, cb)]
        return out + holes

    def cutouts(self, i: int) -> List[List[Pt]]:
        """The outlines cut out of piece ``i``, in its own frame,
        counter-clockwise."""
        piece, parent, _areas = self.judged()
        m = self.frame(i)
        return [pc.ccw(_open([_fine(map_point(m, (p.x, p.y)))
                              for p in self.rings[j]]))
                for j in range(len(self.rings))
                if parent[j] == i and not piece[j]]

    def piece(self, i: int) -> Piece:
        """Piece ``i`` in its own frame, with all that belongs to it."""
        sh = self.owners[i]
        areas = self.judged()[2]
        return Piece(sh.shape_id, sh.name or "", sh, list(self.ring(i)),
                     _box(_own_ring(sh)), areas[i], self.cutouts(i),
                     self.marks(i),
                     self.folds_of(i), self.holes(i))


def _own_holes(shape, doc) -> List[tuple]:
    """A shape's own stitch holes in its own frame, as ``(x, y,
    diameter)``: the holes attached to it, else its stitching fitted to
    its outline -- what ``stitching.holes_for_shape`` gives, before the
    shape's place on the drawing is put on them."""
    from .stitching import stitch_shape_path
    st = getattr(shape, "stitch", None)
    baked = getattr(shape, "baked_holes", None)
    if baked:
        pts = [h.point for h in baked]
    elif st and st.enabled:
        from .hardware import keepouts_for_shape
        keep = keepouts_for_shape(shape, doc) or None
        pts = [h.point for h in stitch_shape_path(
            shape.local_path(), st, getattr(shape, "stitch_edges", None),
            keep).holes]
    else:
        return []
    k = -1.0 if shape.transform.mirror_x else 1.0
    dia = float(getattr(st, "hole_diameter", 1.0) or 1.0)
    return [(k * p.x + 0.0, p.y, dia) for p in pts]


def _loose_holes(doc, pieces=frozenset()) -> List[tuple]:
    """The holes of a drawing that belong to no piece, as ``(x, y,
    diameter)`` on the drawing: its seams' (clear of hardware, as they
    are cut), its loose holes, and the stitching of every shape that is
    not one of ``pieces`` (given by ``id``) -- a stitched line or curve,
    a stitched cutout -- as ``stitching.holes_for_shape`` cuts it."""
    from .hardware import keepouts_near
    from .stitching import holes_for_shape
    out = []
    for sh in doc.shapes:
        if id(sh) in pieces or getattr(sh, "construction", False):
            continue
        if not (getattr(sh, "baked_holes", None)
                or getattr(getattr(sh, "stitch", None), "enabled", False)):
            continue
        try:
            holes = holes_for_shape(sh, doc).holes
        except Exception:
            continue
        dia = float(getattr(sh.stitch, "hole_diameter", 1.0) or 1.0) \
            if sh.stitch is not None else 1.0
        out += [(h.point.x, h.point.y, dia) for h in holes]
    for sl in getattr(doc, "stitch_lines", None) or []:
        try:
            holes = sl.result(keepouts_near(sl.points, doc)).holes
        except Exception:
            continue
        dia = float(getattr(sl.settings, "hole_diameter", 1.0) or 1.0)
        out += [(h.point.x, h.point.y, dia) for h in holes]
    for h in getattr(doc, "holes", None) or []:
        out.append((h.point.x, h.point.y,
                    float(getattr(h, "hole_diameter", 1.0) or 1.0)))
    return out


def _stack_key(shape) -> str:
    """The stack a shape is in ("" for none)."""
    p = getattr(shape, "placed", None)
    return p.of if p is not None and p.of else ""


def shape_by_id(doc, key: str):
    """The shape of a drawing with that id, or None."""
    for sh in doc.shapes:
        if sh.shape_id == key:
            return sh
    return None


def _shape(doc, piece):
    """A piece given as its shape or as its id."""
    return shape_by_id(doc, piece) if isinstance(piece, str) else piece


def _shapes(doc, pieces) -> list:
    """One piece or several, each given as its shape or as its id: the
    shapes, once each, in the order given."""
    if isinstance(pieces, str) or not isinstance(pieces, Iterable):
        pieces = [pieces]
    out = []
    for p in pieces:
        sh = _shape(doc, p)
        if sh is not None and all(sh is not o for o in out):
            out.append(sh)
    return out


def offered(doc) -> list:
    """The outlines of a drawing that are pieces to put together, in the
    order drawn: what ``pieces.classify`` calls a piece, and any closed
    outline already on a stack (a pocket drawn on its panel is a cutout
    of it to ``classify``, and a piece here once it is put on). A
    construction shape -- the rectangle Nest on sheet leaves -- is never
    one."""
    d = _Drawing(doc)
    return [d.owners[i] for i in d.pieces()]


def drawn_inside(doc) -> List[tuple]:
    """The outlines that are read as cutouts but can still be put on, each
    with the outline it is drawn in: ``[(outline, around)]``. A cutout of
    a piece is offered when it is at least ``OFFER_SHARE`` of the piece
    round it, so a pocket drawn a little inside its panel is, and a slot
    or a rivet hole is not; a slot cut in a piece of a stack is that
    piece's, and never offered. An outline drawn inside one that is no
    piece -- a fob nested in the waste of a window cut in a panel, the
    thumb slot of a pocket drawn on its panel and not put on yet -- is
    offered whatever its size: it is put on only when it is asked for."""
    d = _Drawing(doc)
    parent = d.judged()[1]
    return [(d.owners[i], d.owners[parent[i]])
            for i in range(len(d.rings)) if d.inside(i)]


def read_piece(doc, piece) -> Optional[Piece]:
    """One piece in its own frame with its cutouts, the slits and scored
    lines on it, its fold lines and its stitch holes -- or None for a
    shape that is no closed outline on a cut layer."""
    sh = _shape(doc, piece)
    d = _Drawing(doc)
    i = d.at.get(id(sh))
    return d.piece(i) if i is not None else None


# -- a stack as it lies -------------------------------------------------------

@dataclass
class Laid:
    """A piece as it lies on the bench, in a stack."""

    key: str                    # the shape id
    name: str
    shape: object               # the outline's Shape
    order: int                  # its place in the pile, 1 at the bottom
    main: bool                  # the stack's main piece
    above: bool                 # over the main piece in the pile (else
    #                             under it; the main piece itself: False)
    grain_up: bool              # else its flesh side is up
    mirrored: bool              # it shows the other way round than drawn
    thickness: float            # mm of leather; 0 = not given
    leather: str                # "" = the usual one
    outline: List[Pt]           # counter-clockwise, thinned for the model
    box: Box                    # of the outline as drawn, where it lies
    from_own: Map               # its own frame -> the bench
    from_drawing: Map           # the drawing -> the bench
    placed: Placement           # its place, as the next change writes it
    cutouts: List[List[Pt]] = field(default_factory=list)
    marks: List[tuple] = field(default_factory=list)   # (kind, points)
    holes: List[tuple] = field(default_factory=list)   # (x, y, diameter)
    folds: List[Fold] = field(default_factory=list)

    def fold_dir(self, direction: int) -> str:
        """What a fold line of this piece is to be marked as on the
        drawing ("front" or "back") to fold ``direction`` on the bench:
        the other one when the piece lies mirrored."""
        return "front" if (direction < 0) == self.mirrored else "back"

    def signature(self) -> tuple:
        """Everything a model of the piece is built from, and its name
        (the report and the window's list show it)."""
        return (self.key, self.name, self.thickness, self.leather,
                self.above, self.grain_up, tuple(self.outline),
                tuple(tuple(c) for c in self.cutouts),
                tuple((k, tuple(pts)) for k, pts in self.marks),
                tuple(self.holes),
                tuple((f.a, f.b, f.angle, f.direction, f.radius, f.order,
                       f.name, f.key) for f in self.folds))


@dataclass
class Stack:
    """The pieces of one stack, bottom first, as they lie on the bench.

    The stack folds along the fold lines of ``main``; a fold line of any
    other piece is not made. ``lost`` says the piece the others were
    placed against is not on the drawing as a piece: the lowest piece left
    stands in as the main piece and every piece lies where it last lay.
    ``cut_open`` holds the shapes of the stack that are no closed outline
    just now (cut open by Trim or Break apart): they keep their places and
    come back when they are closed again. When the main piece is among
    them it is cut open, not deleted, and it stays the main piece: the
    stand-in is never written as one."""

    key: str                    # "stack7"
    pieces: List[Laid]
    main: Laid
    lost: bool = False
    cut_open: list = field(default_factory=list)

    def piece(self, key: str) -> Optional[Laid]:
        """The piece with that shape id, or None."""
        for p in self.pieces:
            if p.key == key:
                return p
        return None

    def over(self) -> List[Laid]:
        """The pieces over the main piece, as they are laid on it: the
        nearest to it first."""
        return [p for p in self.pieces if p.above and not p.main]

    def under(self) -> List[Laid]:
        """The pieces under the main piece, the nearest to it first."""
        return [p for p in reversed(self.pieces)
                if not p.above and not p.main]

    def signature(self) -> tuple:
        """Everything a model of the stack is built from: unchanged,
        nothing to redo."""
        return (self.key, self.main.key, self.lost,
                tuple(sh.shape_id for sh in self.cut_open),
                tuple(p.signature() for p in self.pieces))


class _Item:
    """One piece of a pile being read or changed: how it lies, the size
    of its box and where the middle of its box is on the bench.

    A piece that is ``out`` is a shape of the stack that is not a closed
    outline just now (cut open by Trim or Break apart). It keeps its
    place in the pile, and its place is written with the others, but it
    is not laid: the size of its box is worked out from its stored place
    where that can be done (None where it cannot, and need not be)."""

    def __init__(self, shape, index: int, out: bool = False):
        p = shape.placed
        self.shape = shape
        self.index = index          # its place in the drawing
        self.out = out
        self.turn, self.over, self.flip = p.turn, p.over, p.flip
        self.ax, self.ay, self.x, self.y = p.ax, p.ay, p.x, p.y
        self.cx = self.cy = 0.0
        self.loose = False          # to be tied to the main piece again
        if out:
            self.w = self.h = None
            self.mid = (0.0, 0.0)
        else:
            self.measure()

    @property
    def mirrored(self) -> bool:
        return self.over != self.flip

    def measure(self) -> None:
        """The box of the piece as it lies, before it is put in place."""
        m = _lie(self.turn, self.mirrored)
        b = _box([map_point(m, p) for p in _own_ring(self.shape)])
        self.w, self.h = b[2] - b[0], b[3] - b[1]
        self.mid = (0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]))

    def box(self) -> Box:
        return (self.cx - 0.5 * self.w, self.cy - 0.5 * self.h,
                self.cx + 0.5 * self.w, self.cy + 0.5 * self.h)

    def from_own(self) -> Map:
        a, b, c, d, _e, _f = _lie(self.turn, self.mirrored)
        return (a, b, c, d, self.cx - self.mid[0], self.cy - self.mid[1])


def _tie(anchor: float, centre: float, size, main):
    """One axis of a piece after a move: the point of its box it is held
    by, and how far that point is from the same point of the main piece's
    box. An edge or middle within ``FLUSH`` of the main piece's is tied to
    it -- the present anchor for choice, then left, right, middle -- and
    otherwise the present anchor is kept. Where the size of either box is
    not known (a piece cut open just now), the piece is held by its
    middle, which is where it lies whatever the sizes are."""
    if size is None or main is None:
        return 0.5, round(centre, 6) + 0.0

    def off(a):
        return centre + (a - 0.5) * (size - main)
    for a in (anchor, 0.0, 1.0, 0.5):
        if abs(off(a)) < FLUSH:
            return a, 0.0
    return anchor, round(off(anchor), 6) + 0.0


class _Pile:
    """One stack being read or changed: its pieces bottom first, and which
    of them is the main piece."""

    def __init__(self, key: str, items: List[_Item]):
        self.key = key
        # equal places in the pile: the earlier in the drawing is lower
        items.sort(key=lambda it: (it.shape.placed.order, it.index))
        self.items = items
        live = self.live()
        # two marked as the main piece: the lower, then the earlier drawn
        mains = [it for it in items if it.shape.placed.main]
        self.lost = not mains
        self.main = mains[0] if mains else live[0]
        main = self.main
        if self.lost:
            # the piece they were placed against is gone: every piece
            # where its box last lay, so nothing jumps, and held from the
            # new main piece as the next change will write it
            ox, oy = main.shape.placed.cx, main.shape.placed.cy
            for it in items:
                p = it.shape.placed
                it.cx, it.cy, it.loose = p.cx - ox, p.cy - oy, True
            return
        if main.out:
            # the main piece is cut open for a while: it stays the main
            # piece, the others lie where their boxes last lay against it,
            # and the size of its box is worked out from a piece held by an
            # edge (none needs it if every one is held by its middle)
            for it in live:
                p = it.shape.placed
                it.cx, it.cy = p.cx, p.cy
                if main.w is None and abs(p.ax - 0.5) > 1e-12:
                    main.w = it.w - (p.x - p.cx) / (p.ax - 0.5)
                if main.h is None and abs(p.ay - 0.5) > 1e-12:
                    main.h = it.h - (p.y - p.cy) / (p.ay - 0.5)
        for it in items:
            if it is main:
                continue
            if it.out:
                p = it.shape.placed
                it.cx, it.cy = p.cx, p.cy
                if main.w is not None and abs(p.ax - 0.5) > 1e-12:
                    it.w = main.w + (p.x - p.cx) / (p.ax - 0.5)
                if main.h is not None and abs(p.ay - 0.5) > 1e-12:
                    it.h = main.h + (p.y - p.cy) / (p.ay - 0.5)
            elif not main.out:
                it.cx = it.x - (it.ax - 0.5) * (it.w - main.w)
                it.cy = it.y - (it.ay - 0.5) * (it.h - main.h)

    def live(self) -> List[_Item]:
        """The pieces that are closed outlines now, bottom first."""
        return [it for it in self.items if not it.out]

    def find(self, shape) -> Optional[_Item]:
        for it in self.items:
            if it.shape is shape:
                return it
        return None

    def loosen(self) -> None:
        """Every piece is to be tied to the main piece again: it has
        changed, or is another piece now."""
        for it in self.items:
            it.loose = True

    def set_main(self, item: _Item) -> None:
        """Another piece is the main piece; nothing moves."""
        ox, oy = item.cx, item.cy
        for it in self.items:
            it.cx, it.cy = it.cx - ox, it.cy - oy
        self.main = item
        self.lost = False
        self.loosen()

    def placement(self, it: _Item, k: int) -> Placement:
        """The place of the piece at ``k`` in the pile, as it is written."""
        if it is self.main:
            return Placement(of=self.key, order=k + 1, main=True,
                             turn=it.turn, over=it.over, flip=it.flip)
        if it.loose:
            ax, x = _tie(it.ax, it.cx, it.w, self.main.w)
            ay, y = _tie(it.ay, it.cy, it.h, self.main.h)
        else:
            ax, x, ay, y = it.ax, it.x, it.ay, it.y
        # the middle of its box, from where it is held (a tie to an edge
        # moves it by up to ``FLUSH``); where a size is not known, from
        # where it lies
        cx, cy = it.cx, it.cy
        if it.w is not None and self.main.w is not None:
            cx = x - (ax - 0.5) * (it.w - self.main.w)
        if it.h is not None and self.main.h is not None:
            cy = y - (ay - 0.5) * (it.h - self.main.h)
        return Placement(of=self.key, order=k + 1, main=False, ax=ax, ay=ay,
                         x=x, y=y, turn=it.turn, over=it.over, flip=it.flip,
                         cx=round(cx, 6) + 0.0, cy=round(cy, 6) + 0.0)

    def write(self) -> None:
        """Put every piece's place on its shape: the pile numbered from 1,
        one main piece, and where each box centre lies now. A piece cut
        open for a while is written too, in its place in the pile, so that
        it comes back where it lay when it is closed again."""
        for k, it in enumerate(self.items):
            it.shape.placed = self.placement(it, k)


def _is_outline(shape, cut) -> bool:
    """A closed outline on a cut layer: what can be a piece of a stack."""
    return (not getattr(shape, "is_fold_line", False) and cut(shape)
            and _own_ring(shape) is not None)


def is_outline(doc, shape) -> bool:
    """Whether a shape is a closed outline on a cut layer: a piece of
    leather, which has a thickness and can be put on a stack."""
    from .pieces import on_cut_layer
    return shape is not None and _is_outline(shape, on_cut_layer(doc))


def _piles(doc, only: str = "") -> Dict[str, _Pile]:
    """The stacks of a drawing (or the one called ``only``): each with
    its pieces, and the shapes of it that are cut open for a while. A
    stack with no closed outline left is none."""
    from .pieces import on_cut_layer
    cut = on_cut_layer(doc)
    found: Dict[str, List[_Item]] = {}
    for index, sh in enumerate(doc.shapes):
        key = _stack_key(sh)
        if key and (not only or key == only):
            found.setdefault(key, []).append(
                _Item(sh, index, out=not _is_outline(sh, cut)))
    return {key: _Pile(key, items) for key, items in found.items()
            if any(not it.out for it in items)}


def _pile_of(doc, shape) -> Optional[_Pile]:
    """The stack a piece is in, with the piece's item -- or None (also for
    a shape of a stack that is cut open just now)."""
    key = _stack_key(shape)
    pile = _piles(doc, key).get(key) if key else None
    it = pile.find(shape) if pile is not None else None
    return pile if it is not None and not it.out else None


def _by_pile(doc, pieces, out: bool = False
             ) -> List[Tuple[_Pile, List[_Item]]]:
    """The pieces given, stack by stack: each stack with its items among
    them. A piece in no stack is passed over. Unless ``out`` is asked for,
    so is one cut open just now, and so is every piece of a stack whose
    main piece is cut open, which is not to be rearranged (see
    ``main_cut_open``)."""
    piles: Dict[str, _Pile] = {}
    found: Dict[str, List[_Item]] = {}
    for sh in _shapes(doc, pieces):
        key = _stack_key(sh)
        if not key:
            continue
        if key not in piles:
            piles.update(_piles(doc, key))
        it = piles[key].find(sh) if key in piles else None
        if it is not None and (out or not (it.out or piles[key].main.out)):
            found.setdefault(key, []).append(it)
    return [(piles[key], items) for key, items in found.items()]


def main_cut_open(doc, stack: str) -> bool:
    """Whether the main piece of a stack is cut open just now (by Trim or
    Break apart, so that it is no closed outline). Until it is closed
    again the stack can be looked at but not rearranged: every call here
    that would change the places or the order of its pieces does nothing
    and says so, and a piece can still be taken off it, or be given
    another thickness or leather. So nothing is written against the
    piece that stands in for the main piece meanwhile, and closed again,
    every piece lies where it lay."""
    pile = _piles(doc, stack).get(stack) if stack else None
    return pile is not None and pile.main.out


def _stack_rank(key: str):
    """Stacks in the order they were started ("stack7" before "stack12")."""
    digits = "".join(ch for ch in key if ch.isdigit())
    return (int(digits) if digits else 0, key)


def _laid(pile: _Pile, it: _Item, k: int, drawing: Optional[_Drawing],
          main: _Item) -> Laid:
    """The piece at ``k`` in the pile as it lies. ``main`` is the piece
    laid as the main piece -- while the main piece is cut open, the
    lowest piece left, which stands in for it -- and over or under is
    told from it, so that no piece is laid twice."""
    sh = it.shape
    m = it.from_own()
    mirrored = it.mirrored
    outline = [map_point(m, p) for p in _model_ring(sh)]
    if mirrored:
        outline.reverse()           # a mirror turns it clockwise
    laid = Laid(
        key=sh.shape_id, name=sh.name or "", shape=sh, order=k + 1,
        main=it is main, above=k > pile.items.index(main),
        grain_up=not it.over, mirrored=mirrored,
        thickness=float(getattr(sh, "thickness", 0.0) or 0.0),
        leather=getattr(sh, "leather", "") or "", outline=outline,
        box=it.box(), from_own=m, from_drawing=_then(own_frame(sh), m),
        placed=pile.placement(it, k))
    i = drawing.at.get(id(sh)) if drawing is not None else None
    if i is None:
        return laid
    own = drawing.piece(i)
    laid.cutouts = [pc.ccw([map_point(m, p) for p in ring])
                    for ring in own.cutouts]
    laid.marks = [(kind, [map_point(m, p) for p in pts])
                  for kind, pts in own.marks]
    laid.holes = [map_point(m, h) + (h[2],) for h in own.holes]
    for f in own.folds:
        laid.folds.append(Fold(
            map_point(m, f.a), map_point(m, f.b), f.angle,
            -f.direction if mirrored else f.direction, f.radius, f.order,
            f.name, f.key, f.shape))
    _sort_folds(laid.folds)
    return laid


def stacks(doc, detail: bool = True) -> List[Stack]:
    """The stacks of a drawing, in the order they were started, each with
    its pieces bottom first as they lie on the bench.

    With ``detail`` every piece comes with its cutouts, the slits and
    scored lines on it, its stitch holes and its fold lines, all on the
    bench; without, only with its outline and its place (enough for a
    list of the pieces, and much quicker).

    Each oddity has one answer. Two pieces marked as the main piece: the
    lower in the pile, then the earlier in the drawing, is it. Equal
    places in the pile: the earlier in the drawing is lower. A stack with
    one piece left is still a stack. No main piece left (it was deleted
    on the drawing, or is cut open just now): see ``Stack.lost``. A
    placed shape that is no longer a closed outline on a cut layer is in
    no stack until it is one again (``Stack.cut_open`` names it).
    """
    piles = _piles(doc)
    drawing = _Drawing(doc) if detail and piles else None
    out = []
    for key in sorted(piles, key=_stack_rank):
        pile = piles[key]
        live = pile.live()
        main = live[0] if pile.main.out else pile.main
        laid = [_laid(pile, it, k, drawing, main)
                for k, it in enumerate(pile.items) if not it.out]
        out.append(Stack(key, laid, laid[live.index(main)],
                         pile.lost or pile.main.out,
                         [it.shape for it in pile.items if it.out]))
    return out


def stack_of(doc, piece, detail: bool = True) -> Optional[Stack]:
    """The stack a piece is in, read as ``stacks`` reads it, or None."""
    key = _stack_key(_shape(doc, piece))
    for st in stacks(doc, detail) if key else ():
        if st.key == key:
            return st
    return None


def refresh(doc) -> bool:
    """Write afresh where the middle of each piece's box lies (``cx``,
    ``cy``) where it no longer matches its place, and nothing else: a
    piece resized on the drawing since the stack was last changed. That is
    what a stack whose main piece is deleted falls back on, so that then
    nothing jumps. A stack whose main piece is gone or cut open is left
    alone. True if anything was written. Meant to be called as each change
    of the drawing is committed."""
    from dataclasses import replace
    done = False
    for pile in _piles(doc).values():
        if pile.lost or pile.main.out:
            continue
        for k, it in enumerate(pile.items):
            if it is pile.main or it.out:
                continue
            p = it.shape.placed
            want = pile.placement(it, k)
            if abs(want.cx - p.cx) > 1e-6 or abs(want.cy - p.cy) > 1e-6:
                it.shape.placed = replace(p, cx=want.cx, cy=want.cy)
                done = True
    return done


# -- putting a piece on, taking it off ----------------------------------------

def _drawn_place(pile: _Pile, shape):
    """Where a piece lies if it is put on as it is drawn against the main
    piece: ``(turn, box centre x, y)`` -- or None when it is not drawn on
    the main piece, or the main piece lies mirrored or is cut open."""
    main = pile.main
    if main.out or main.mirrored or shape is main.shape:
        return None
    ring = _model_ring(shape)
    if ring is None:
        return None
    # the drawing -> the bench, as the main piece goes
    m = _then(own_frame(main.shape), main.from_own())
    back = shape.transform
    c, s = _cs(back.rotation)
    on_bench = [map_point(m, (back.x + c * x - s * y, back.y + s * x + c * y))
                for x, y in ring]
    main_ring = [map_point(main.from_own(), p)
                 for p in _model_ring(main.shape)]
    if _overlap(on_bench, main_ring) <= AS_DRAWN:
        return None
    lies = _norm(main.turn - main.shape.transform.rotation + back.rotation)
    b = _box([map_point(_lie(lies, False), p) for p in _own_ring(shape)])
    # the piece's own origin on the bench, then the middle of its box
    ox, oy = map_point(m, (back.x, back.y))
    return lies, ox + 0.5 * (b[0] + b[2]), oy + 0.5 * (b[1] + b[3])


def drawn_on_main(doc, piece, stack: str = "") -> bool:
    """Whether a piece can be put on as drawn: it is drawn overlapping the
    main piece of ``stack`` (of its own stack, if none is named) by more
    than ``AS_DRAWN``, and the main piece lies unmirrored. An outline
    nested in one that is no piece (a fob in a window's waste, a pocket's
    thumb slot) never is: it is put on only when it is asked for."""
    sh = _shape(doc, piece)
    key = stack or _stack_key(sh)
    pile = _piles(doc, key).get(key) if sh is not None and key else None
    if pile is None or _drawn_place(pile, sh) is None:
        return False
    if not _stack_key(sh):
        d = _Drawing(doc)
        i = d.at.get(id(sh))
        if i is not None and d.nested(i):
            return False
    return True


def _can_go_on(doc, shape) -> bool:
    """Whether an outline can be put on: offered as a piece, or from
    ``drawn_inside``."""
    d = _Drawing(doc)
    i = d.at.get(id(shape))
    if i is None:
        return False
    return d.judged()[0][i] or d.inside(i)


def put_on(doc, piece, stack: str = "", on=None, thickness: float = 0.0,
           as_drawn: bool = True) -> str:
    """Put a piece on a stack, on top of its pile; returns the stack's id
    ("" if the shape is no closed outline on a cut layer).

    With no ``stack`` named the piece goes on the stack ``on`` is in, and
    with neither it starts a stack of its own and is its main piece. A
    piece drawn overlapping the main piece comes in where it is drawn
    against it (``as_drawn``; see ``drawn_on_main``). Otherwise it lands
    on ``on`` -- on the top piece, if none is given -- their bottom left
    corners together. Landing on the main piece it is held by its bottom
    left corner, but on an axis on which the two are as wide (or as
    tall), within ``FLUSH``, by its middle, so that it stays centred when
    either changes size. Landing on another piece it is held by the same
    point of the main piece's box as that piece is, so that the two move
    together whatever happens to the main piece. A piece with no
    thickness of its own is given ``thickness``. One already on that stack
    stays as it is; one on another stack is taken off that one first. An
    outline that is neither offered nor drawn inside a piece (``offered``,
    ``drawn_inside``: a slot, a rivet hole) is not put on, and nothing is
    put on a stack whose main piece is cut open (``main_cut_open``) or
    that has no closed outline left."""
    from .pieces import on_cut_layer
    sh = _shape(doc, piece)
    if sh is None or not _is_outline(sh, on_cut_layer(doc)):
        return ""
    if not _stack_key(sh) and not _can_go_on(doc, sh):
        return ""
    under = _shape(doc, on) if on is not None else None
    key = stack or _stack_key(under)
    if key and _stack_key(sh) == key:
        return key
    if key and any(_stack_key(s) == key for s in doc.shapes):
        pile = _piles(doc, key).get(key)
        if pile is None or pile.main.out:
            # a stack with no closed outline left, or one whose main piece
            # is cut open: nothing is placed against it until it is closed
            return ""
    if _stack_key(sh):
        take_off(doc, sh)
    if thickness > 0.0 and not sh.thickness > 0.0:
        sh.thickness = float(thickness)
    pile = _piles(doc, key).get(key) if key else None
    if pile is None:
        # the first piece of a stack is its main piece
        key = key or _next_id("stack")
        sh.placed = Placement(of=key, order=1, main=True)
        return key
    sh.placed = Placement(of=key)
    it = _Item(sh, len(doc.shapes))
    it.loose = True
    drawn = _drawn_place(pile, sh) if as_drawn else None
    if drawn is not None:
        it.turn, it.cx, it.cy = drawn
        it.measure()
    else:
        there = pile.find(under) if under is not None else None
        if there is None or there.out:
            there = pile.live()[-1]
        b = there.box()
        it.cx, it.cy = b[0] + 0.5 * it.w, b[1] + 0.5 * it.h
        if there is pile.main:
            it.ax = 0.5 if abs(it.w - there.w) < FLUSH else 0.0
            it.ay = 0.5 if abs(it.h - there.h) < FLUSH else 0.0
        else:
            # held by the point of the main piece's box that the piece it
            # lands on is held by, the two move together whatever happens
            # to the main piece
            main = pile.main
            it.ax, it.ay, it.loose = there.ax, there.ay, False
            it.x = round(it.cx + (it.ax - 0.5) * (it.w - main.w), 6) + 0.0
            it.y = round(it.cy + (it.ay - 0.5) * (it.h - main.h), 6) + 0.0
    pile.items.append(it)
    pile.write()
    return key


def take_off(doc, pieces) -> bool:
    """Take pieces off their stacks. Nothing else moves: when the main
    piece goes, the lowest piece left becomes the main piece and the
    others are placed against it where they lie. While the main piece is
    cut open (``main_cut_open``) the others are left where they were
    written, renumbered only.
    The pieces keep their thickness and their leather. False if none was
    on a stack."""
    from dataclasses import replace
    done = False
    for pile, items in _by_pile(doc, pieces, out=True):
        for it in items:
            pile.items.remove(it)
            it.shape.placed = None
        done = True
        if pile.main.out and pile.main not in items:
            # the rest stay where they were written, the pile numbered
            # from 1 again
            for k, it in enumerate(pile.items):
                it.shape.placed = replace(it.shape.placed, order=k + 1)
            continue
        live = pile.live()
        if live and pile.main in items:
            pile.set_main(live[0])
        if live:
            pile.write()
        else:                       # only shapes cut open are left
            for it in pile.items:
                it.shape.placed = None
    # a placed shape that is no outline now is in no pile: clear it too
    for sh in _shapes(doc, pieces):
        if sh.placed is not None:
            sh.placed = None
            done = True
    return done


def lay_as_drawn(doc, pieces) -> bool:
    """Lay pieces of a stack where they are drawn against its main piece,
    grain up, where that applies (see ``drawn_on_main``). False if it
    applied to none of them."""
    done = False
    for pile, items in _by_pile(doc, pieces):
        for it in items:
            drawn = _drawn_place(pile, it.shape)
            if drawn is not None:
                it.turn, it.cx, it.cy = drawn
                it.over = it.flip = False
                it.loose = done = True
                it.measure()
        pile.write()
    return done


# -- the pile -----------------------------------------------------------------

def _reorder(pile: _Pile, row: List[_Item]) -> bool:
    """Give a pile its pieces in a new order, ``row`` being the pieces
    that are outlines now (the main piece among them). A piece cut open for
    a while stays on the piece it lay on (the next one towards the main
    piece), on the side away from the main piece, so it comes back on what
    it lay on. True if the order changed."""
    old, main = pile.items, pile.main
    m = old.index(main)
    new = list(row)
    for k in sorted((k for k, it in enumerate(old)
                     if it.out and it is not main),
                    key=lambda k: abs(k - m)):
        on = old[k - 1] if k > m else old[k + 1]
        if on is main:
            far = k > m
        else:
            far = new.index(on) > new.index(main)
        j = new.index(on)
        new.insert(j + 1 if far else j, old[k])
    changed = new != old
    pile.items = new
    return changed


def _shift(doc, pieces, up: bool) -> bool:
    done = False
    for pile, items in _by_pile(doc, pieces):
        row = pile.live()
        n = len(row)
        # from the end they move towards, so several go as one block
        for k in (range(n - 2, -1, -1) if up else range(1, n)):
            j = k + 1 if up else k - 1
            if row[k] in items and row[j] not in items:
                row[k], row[j] = row[j], row[k]
        done = _reorder(pile, row) or done
        pile.write()
    return done


def higher(doc, pieces) -> bool:
    """Move pieces one place up their pile; several go as one block. A
    piece that lies just under the main piece goes past it, and lies on
    it: the pile is one pile, the main piece a piece in it. False if none
    could go higher."""
    return _shift(doc, pieces, True)


def lower(doc, pieces) -> bool:
    """Move pieces one place down their pile; several go as one block. A
    piece that lies just on the main piece goes past it, and lies under
    it. False if none could go lower."""
    return _shift(doc, pieces, False)


def _to_side(doc, pieces, over: bool) -> bool:
    done = False
    for pile, items in _by_pile(doc, pieces):
        row = pile.live()
        main = pile.main
        if main in items:
            continue
        going = [it for it in row if it in items]
        rest = [it for it in row if it not in items]
        k = rest.index(main)
        row = (rest[:k + 1] + going + rest[k + 1:] if over
               else rest[:k] + going + rest[k:])
        done = _reorder(pile, row) or done
        pile.write()
    return done


def put_under(doc, pieces) -> bool:
    """Put pieces under the main piece of their stack, next to it (the
    nearest to it of all that lie under it), whichever side they lay on;
    several keep their order among themselves. The main piece itself, and
    a stack whose main piece is cut open just now (``main_cut_open``), are
    passed over. Where each lies on the bench does not change. False if the
    pile is as it was."""
    return _to_side(doc, pieces, False)


def put_over(doc, pieces) -> bool:
    """Put pieces on the main piece of their stack, next to it (the
    nearest to it of all that lie on it); see ``put_under``."""
    return _to_side(doc, pieces, True)


def make_main(doc, piece) -> bool:
    """Make another piece of a stack its main piece: the stack folds along
    this one's fold lines now, and every piece is placed against it where
    it lies, so nothing moves. False if the piece is in no stack, or its
    stack's main piece is cut open just now (``main_cut_open``)."""
    sh = _shape(doc, piece)
    pile = _pile_of(doc, sh) if sh is not None else None
    if pile is None or pile.main.out:
        return False
    pile.set_main(pile.find(sh))
    pile.write()
    return True


# -- moving -------------------------------------------------------------------

def _moved(pile: _Pile, items: List[_Item], dx: float, dy: float) -> None:
    """Pieces moved on the bench. The main piece is where the bench is
    measured from, so moving it is moving every other piece the other
    way."""
    if pile.main in items:
        items, dx, dy = [it for it in pile.items if it not in items], \
            -dx, -dy
    for it in items:
        it.cx, it.cy, it.loose = it.cx + dx, it.cy + dy, True


def move_by(doc, pieces, dx: float, dy: float) -> bool:
    """Move pieces by a distance on the bench (mm, across and up). After
    the move each is tied again to the main piece by the anchor rule: an
    edge or middle within ``FLUSH`` of the main piece's stays level with
    it for good. False if none of them is in a stack."""
    done = False
    for pile, items in _by_pile(doc, pieces):
        _moved(pile, items, dx, dy)
        pile.write()
        done = True
    return done


def _gap(size, main) -> float:
    """How much wider (or taller) a box is than the main piece's; 0 where
    either is not known (see ``_tie``)."""
    return 0.0 if size is None or main is None else size - main


def set_offset(doc, pieces, x: Optional[float] = None,
               y: Optional[float] = None) -> bool:
    """Put pieces at a typed distance from the main piece: ``x`` across
    and ``y`` up (None: as it is), each measured between the point of the
    piece's box it is held by now (``placed.ax``, ``ay``) and the same
    point of the main piece's box. The main piece itself is where the
    distances are measured from, and stays. False if nothing moved."""
    done = False
    for pile, items in _by_pile(doc, pieces):
        for it in items:
            if it is pile.main or (x is None and y is None):
                continue
            if x is not None:
                it.cx = x - (it.ax - 0.5) * _gap(it.w, pile.main.w)
            if y is not None:
                it.cy = y - (it.ay - 0.5) * _gap(it.h, pile.main.h)
            it.loose = done = True
        pile.write()
    return done


def line_up(doc, pieces, side: str, against=None) -> bool:
    """Line pieces up with the main piece of their stack, or with the
    piece ``against`` (of the same stack): ``side`` is one of "left",
    "centre", "right" (across) or "bottom", "middle", "top" (up). The
    piece is then held by that side, so what was lined up stays lined up
    when either piece changes size. False if nothing was lined up."""
    if side not in SIDES:
        raise ValueError(f"line up: no side called {side!r}")
    across = side in SIDES[:3]
    a = SIDES.index(side) % 3 * 0.5             # 0, 0.5 or 1
    done = False
    for pile, items in _by_pile(doc, pieces):
        to = pile.main
        if against is not None:
            to = pile.find(_shape(doc, against))
        if to is None or to.out:
            continue
        for it in items:
            if it is to:
                continue
            if across:
                d = (to.cx + (a - 0.5) * to.w) - (it.cx + (a - 0.5) * it.w)
                _moved(pile, [it], d, 0.0)
                it.ax = a
            else:
                d = (to.cy + (a - 0.5) * to.h) - (it.cy + (a - 0.5) * it.h)
                _moved(pile, [it], 0.0, d)
                it.ay = a
            done = True
        pile.write()
    return done


# -- turning ------------------------------------------------------------------

def _turned(doc, pieces, change) -> bool:
    done = False
    for pile, items in _by_pile(doc, pieces):
        for it in items:
            w, h = it.w, it.h
            change(it)
            it.turn = _norm(it.turn)
            it.measure()
            it.loose = True
            if it is pile.main and (abs(it.w - w) > 1e-9
                                    or abs(it.h - h) > 1e-9):
                pile.loosen()       # its box is what the others go by
            done = True
        pile.write()
    return done


def turn(doc, pieces, degrees: float) -> bool:
    """Turn pieces counter-clockwise as they lie, each about the middle of
    its box, which stays where it is. False if none is in a stack."""
    def change(it):
        it.turn += degrees
    return _turned(doc, pieces, change)


def set_turn(doc, pieces, degrees: float) -> bool:
    """Lay pieces turned ``degrees`` counter-clockwise from the way they
    are cut, each about the middle of its box. False if none is in a
    stack."""
    def change(it):
        it.turn = degrees
    return _turned(doc, pieces, change)


def turn_over(doc, pieces) -> bool:
    """Turn pieces over like a page, about the upright line through the
    middle of each one's box: grain up becomes flesh up, left becomes
    right, and the box stays where it is. False if none is in a stack."""
    def change(it):
        it.over, it.turn = not it.over, -it.turn
    return _turned(doc, pieces, change)


def mirror(doc, pieces) -> bool:
    """Show pieces the other way round without turning them over, for a
    piece drawn as seen from its flesh side: mirrored about the upright
    line through the middle of its box, the same side up. False if none
    is in a stack."""
    def change(it):
        it.flip, it.turn = not it.flip, -it.turn
    return _turned(doc, pieces, change)


# -- moving by hand -----------------------------------------------------------

def remember(doc, stack: str) -> Dict[str, Placement]:
    """The place of every piece of a stack, by shape id, to go back to
    with ``put_back``: for a drag given up half way, and for a drag
    worked out afresh from where it began at each move of the mouse, so
    that where a piece ends does not depend on the way it took there."""
    from dataclasses import replace
    if not stack:
        return {}
    return {sh.shape_id: replace(sh.placed) for sh in doc.shapes
            if _stack_key(sh) == stack}


def put_back(doc, places: Dict[str, Placement]) -> None:
    """Give pieces the places ``remember`` kept for them. A piece that is
    gone from the drawing is passed over."""
    from dataclasses import replace
    for sh in doc.shapes:
        if sh.shape_id in places:
            sh.placed = replace(places[sh.shape_id])


# -- what goes with what ------------------------------------------------------

def lying_on(doc, piece) -> list:
    """The pieces lying wholly on this one, later in its pile: what goes
    with a piece when it is moved by hand. Later is further from the main
    piece on the same side of it; for the main piece itself, everything
    that lies wholly on it, over or under. Wholly on: its box within this
    one's, and nearly all of it (``ON_SHARE``) on this one's leather: its
    outline less its cutouts, so a piece in a ring's hole is not on the
    ring."""
    sh = _shape(doc, piece)
    pile = _pile_of(doc, sh) if sh is not None else None
    if pile is None:
        return []
    row = pile.items
    k, m = row.index(pile.find(sh)), row.index(pile.main)
    later = (row[k + 1:] if k > m else row[:k] if k < m
             else row[:k] + row[k + 1:])
    under = row[k]
    b = under.box()
    ring = [map_point(under.from_own(), p) for p in _model_ring(sh)]
    holes = None
    out = []
    for it in later:
        if it.out:
            continue
        q = it.box()
        if (q[0] < b[0] - FLUSH or q[1] < b[1] - FLUSH
                or q[2] > b[2] + FLUSH or q[3] > b[3] + FLUSH):
            continue
        mine = [map_point(it.from_own(), p) for p in _model_ring(it.shape)]
        if holes is None:           # its cutouts, read once if needed
            d = _Drawing(doc)
            holes = [[map_point(under.from_own(), p) for p in c]
                     for c in d.cutouts(d.at[id(sh)])]
        on = _overlap(mine, ring) - sum(_overlap(mine, c) for c in holes)
        if on >= ON_SHARE * abs(pc.area(mine)):
            out.append(it.shape)
    return out


# -- commands that rebuild a shape --------------------------------------------

def _bulk(shape):
    """How much of a piece a shape is, to choose among what one command
    made of it: a closed outline by its area, ahead of any open run, which
    goes by its length."""
    path = shape.local_path()
    pts = [(p.x, p.y) for p in path.flatten()]
    if len(pts) < 2:
        return (0, 0.0)
    if path.closed:
        return (1, abs(pc.area(pts)))
    return (0, sum(math.dist(p, q) for p, q in zip(pts, pts[1:])))


def carry_spot(src, dst):
    """Give ``dst``, a shape a command made from ``src`` where it is drawn
    (with its transform already), the place ``src`` has in its stack, so
    that the leather they share stays where it lay on the bench: the turn
    is re-based as ``shapes.carry_placement`` does, and the point of the
    box the piece is held by moves by as much as that point of its box
    moved on the drawing -- a pocket held by its right edge that loses its
    right end lies where its left end lay. The main piece keeps its place
    as it is (the others are placed against it, as against a piece
    resized). Returns ``dst``."""
    carry_placement(src, dst)
    p = dst.placed
    if p is None or p.main:
        return dst
    lie = _then(own_frame(src), _lie(src.placed.turn,
                                     src.placed.over != src.placed.flip))
    boxes = []
    for sh in (src, dst):
        pts = sh.world_polyline()[0]
        if not pts:
            return dst
        boxes.append(_box([map_point(lie, (q.x, q.y)) for q in pts]))
    (a0, b0, a1, b1), (c0, d0, c1, d1) = boxes
    dx = (c0 + p.ax * (c1 - c0)) - (a0 + p.ax * (a1 - a0))
    dy = (d0 + p.ay * (d1 - d0)) - (b0 + p.ay * (b1 - b0))
    mx = 0.5 * (c0 + c1 - a0 - a1)
    my = 0.5 * (d0 + d1 - b0 - b1)
    p.x, p.y = round(p.x + dx, 6) + 0.0, round(p.y + dy, 6) + 0.0
    p.cx, p.cy = round(p.cx + mx, 6) + 0.0, round(p.cy + my, 6) + 0.0
    return dst


def keep_placement(sources, made) -> None:
    """One piece of leather, one place in its stack. Of the shapes a
    command ``made`` from the shapes ``sources`` (each given its transform
    already), the biggest keeps the place of a placed source -- the main
    piece of its stack if it is among them, else the first -- so that the
    leather stays where it lay (``carry_spot``), and takes that source's
    name, thickness and leather with it, also where it has none (no name
    of its own, no thickness given, the usual leather); the others have
    no place: Subtract leaving two parts, a trimmed outline in two runs,
    Break apart's edges, a Union or a weld of a placed piece with another
    shape. Where no source is placed, nothing is changed."""
    made = list(made)
    placed = [s for s in sources if getattr(s, "placed", None) is not None]
    src = next((s for s in placed if s.placed.main),
               placed[0] if placed else None)
    for sh in made:
        sh.placed = None
    if src is not None and made:
        big = max(made, key=_bulk)
        carry_spot(src, big)
        big.name = src.name
        big.thickness = float(src.thickness or 0.0)
        big.leather = src.leather or ""

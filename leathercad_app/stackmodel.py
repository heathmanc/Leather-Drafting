"""From a stack of pieces in the drawing to a model the 3D view can show.

``leathercad.assembly`` reads a drawing into its stacks, each piece as it
lies on the bench. ``leathercad.sheets`` lays pieces on a main piece and
makes a model of them. This module stands between the two, and puts what
the model has to tell into words:

* ``lay_out`` brings a stack into the frame its model is built in and
  makes a ``sheets.Sheet`` of every piece but the main one; ``stack_folds``
  gives the main piece's fold lines in that frame; ``build`` makes the
  model of them.
* ``rows`` is the list of pieces the window shows: those of the stack,
  top first, with where each lies in words; then the pieces not put on
  yet; then the outlines drawn inside a piece.
* ``report`` words what ``sheets.settle`` noted of the stack lying flat,
  and ``fold_report`` what the fold engine noted of it folded; ``marks``
  says which pieces to mark on the model for it, ``drawn_through`` and
  ``closes_tip`` put the rest of it in a few words for the caption and
  for a fold's row.

**The frame of the model.** The fold engine builds the main piece as it
is drawn, its front the side the pattern is drawn on, and never mirrored.
So the model is built on the bench as it is seen from above when the
main piece lies there as it is drawn, and on the bench mirrored left to
right when the main piece lies mirrored (turned over, or drawn from its
flesh side): in the model the main piece is then again as it is drawn,
and its fold lines fold the way they are marked. The view is told to
look at such a model from its back, turned like a page
(``Parts.turned``), which is the bench from above once more.

Pieces are numbered as the model numbers them: 0 is the main piece,
then the pieces over it on the bench from the nearest outwards, then
those under it likewise. No widgets here, and nothing is written to the
drawing.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from leathercad import assembly as asm
from leathercad import foldsolid as fs
from leathercad import polycut as pc
from leathercad import sheets as sh
from leathercad.shapes import Placement

from . import units as U

# a stack lying flat is as thick as its thickest place: where the leather
# there comes to less than this (mm) short of it, it is leather all through
_SOLID = 0.005


# -- lengths and names in words -----------------------------------------------

def size(w: float, h: float) -> str:
    """How big a piece is, for a piece with no name: 80 × 50 mm."""
    if U.is_inches():
        return f"{U.to_display(w):.1f} × {U.to_display(h):.1f} in"
    return f"{w:.0f} × {h:.0f} mm"


def length(mm: float) -> str:
    """A length for reading: 1 mm, 3.14 mm, 0.124 in."""
    text = U.fmt_num(mm)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return f"{text} {U.current()}"


def title_of(shape) -> str:
    """What a piece is called: its name, or its size if it has none (the
    size it is cut at, which moving or turning it on the drawing does
    not change)."""
    if shape.name:
        return shape.name
    ring = asm.own_outline(shape)
    if ring:
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    else:
        x0, y0, x1, y1 = shape.bounds()
    return f"Piece {size(x1 - x0, y1 - y0)}"


def stack_title(stack) -> str:
    """What a stack is called in the list of things to show: its main
    piece and how many pieces are put on it."""
    more = len(stack.pieces) - 1
    name = title_of(stack.main.shape)
    if not more:
        return name
    return f"{name} + {more} piece{'s' if more != 1 else ''}"


def plain(text: str) -> str:
    """A tooltip that shows its text as it is written, a name with a "<"
    in it too (a tooltip with what looks like a tag in it is read as
    rich text)."""
    if "<" not in text:
        return text
    return f"<p>{html.escape(text, quote=False)}</p>"


def escaped(text: str) -> str:
    """A name as the report, which is rich text, is to show it."""
    return html.escape(text, quote=False)


def listed(names: List[str]) -> str:
    """Names in a sentence: A; A and B; A, B and C."""
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# -- the stack in the frame of its model --------------------------------------

@dataclass
class Parts:
    """A stack ready for the model, everything in the model's frame.

    ``outline``, ``holes``, ``cutouts`` and ``marks`` are the main
    piece's, as ``foldsolid.fold_piece`` takes them, ``thickness`` its
    leather and ``grain`` 1 if it is drawn from its grain side, -1 from
    its flesh side. ``sheets`` are the other pieces, the nearest the main
    piece first on each of its faces. ``keys`` and ``names`` are the shape
    id and the name of each piece by its number in the model.
    ``turned`` says the model is to be looked at turned like a page, and
    ``from_bench`` is the map from the bench into the model's frame (see
    ``leathercad.assembly`` for a map)."""

    key: str                    # the stack's id
    outline: list
    thickness: float
    grain: int = 1
    turned: bool = False
    holes: list = field(default_factory=list)
    cutouts: list = field(default_factory=list)
    marks: list = field(default_factory=list)
    sheets: List[sh.Sheet] = field(default_factory=list)
    keys: List[str] = field(default_factory=list)
    names: List[str] = field(default_factory=list)
    from_bench: tuple = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    leather: str = ""           # the main piece's own, "" for the usual

    def number(self, key: str) -> int:
        """The number in the model of the piece with that shape id, or -1
        if it is no piece of the stack."""
        return self.keys.index(key) if key in self.keys else -1

    def to_bench(self, p) -> tuple:
        """A point of the model's pattern, on the bench (the map is its
        own inverse: a mirror, or nothing)."""
        return asm.map_point(self.from_bench, p)


def from_bench(stack) -> tuple:
    """The map from the bench to the frame a stack's model is built in:
    the bench itself, mirrored left to right when the main piece lies
    mirrored there."""
    k = -1.0 if stack.main.mirrored else 1.0
    return (k, 0.0, 0.0, 1.0, 0.0, 0.0)


def lettered(folds: List[fs.FoldLine]) -> List[fs.FoldLine]:
    """Fold lines put in order and lettered A, B, ...: left to right, and
    top to bottom where they line up."""
    folds.sort(key=lambda f: (round(0.5 * (f.a[0] + f.b[0]), 1),
                              -round(0.5 * (f.a[1] + f.b[1]), 1)))
    for k, f in enumerate(folds):
        f.label = fs.letter(k)
    return folds


def stack_folds(stack) -> List[fs.FoldLine]:
    """The fold lines of a stack's main piece as folds of its model,
    lettered, each with its fold-line shape as ``key``. They fold the way
    they are marked on the drawing: the model is built with the main
    piece as it is drawn."""
    main = stack.main
    m = from_bench(stack)
    return lettered([
        fs.FoldLine(asm.map_point(m, f.a), asm.map_point(m, f.b),
                    angle=f.angle,
                    direction=-f.direction if main.mirrored else f.direction,
                    radius=f.radius, order=f.order, name=f.name, key=f.shape)
        for f in main.folds])


def lay_out(stack, usual: float = 2.0) -> Parts:
    """A stack brought into the frame of its model (see the top of this
    module). A piece with no thickness of its own is taken to be ``usual``
    mm thick; nothing is written to it."""
    main = stack.main
    m = from_bench(stack)
    flipped = main.mirrored

    def ring(pts):                  # an outline, still counter-clockwise
        out = [asm.map_point(m, p) for p in pts]
        if flipped:
            out.reverse()
        return out

    def spots(holes):
        return [asm.map_point(m, h) + (h[2],) for h in holes]

    def lines(marks):
        return [(kind, [asm.map_point(m, p) for p in pts])
                for kind, pts in marks]

    def grain(p):
        # the front of the model looks up from the bench unless the main
        # piece lies mirrored
        return 1 if p.grain_up != flipped else -1

    parts = Parts(stack.key, ring(main.outline), main.thickness or usual,
                  grain(main), flipped, spots(main.holes),
                  [ring(c) for c in main.cutouts], lines(main.marks),
                  keys=[main.key], names=[title_of(main.shape)],
                  from_bench=m, leather=main.leather or "")
    for p in stack.over() + stack.under():
        name = title_of(p.shape)
        parts.sheets.append(sh.Sheet(
            ring(p.outline), p.thickness or usual,
            side=1 if p.above != flipped else -1, grain=grain(p),
            name=name, key=p.key, leather=p.leather, holes=spots(p.holes),
            cutouts=[ring(c) for c in p.cutouts], marks=lines(p.marks)))
        parts.keys.append(p.key)
        parts.names.append(name)
    return parts


def build(parts: Parts, folds=(), radius: float = 0.0,
          base: Optional[int] = None):
    """The model of a stack: its main piece laid out for folding by the
    fold engine along its fold lines, with the other pieces on it, which
    fold with it (``foldsolid.fold_piece`` with ``sheets``; it settles
    ``parts.sheets`` where they lie). A stack of one piece folds as a
    piece shown alone does, and a stack whose main piece has no fold
    lines lies flat. The model carries the main piece's name, id and
    leather, and says with ``grain`` which side of its leather the main
    piece is drawn from."""
    layout = fs.fold_piece(parts.outline, folds, cutouts=parts.cutouts,
                           marks=parts.marks, thickness=parts.thickness,
                           radius=radius, base=base, holes=parts.holes,
                           sheets=parts.sheets)
    layout.name, layout.key = parts.names[0], parts.keys[0]
    layout.leather, layout.grain = parts.leather, parts.grain
    return layout


# -- the list of pieces -------------------------------------------------------

# where a row of the list stands
IN, SHOWN, OUT, INSIDE = "in", "shown", "out", "inside"


@dataclass
class Row:
    """One piece in the window's list of pieces.

    ``where`` is ``IN`` for a piece of the stack shown, ``SHOWN`` for the
    piece shown when it is in no stack, ``OUT`` for a piece not put on
    yet and ``INSIDE`` for an outline drawn inside a piece (a cutout of
    it as the rest of the program reads it) that can still be put on.
    ``lies`` says where a piece of the stack lies, in a few words, and
    ``tip`` the same in full. ``number`` is its number in the model (-1
    for a piece that is not in it), ``placed`` its place as the next
    change will write it."""

    key: str                    # the shape id
    name: str
    where: str = OUT
    main: bool = False
    above: bool = False
    thickness: float = 0.0      # 0: none of its own
    leather: str = ""           # "": the usual one
    lies: str = ""
    tip: str = ""
    number: int = -1
    placed: Optional[Placement] = None

    def signature(self) -> tuple:
        """Everything the list shows of the piece."""
        p = self.placed
        return (self.key, self.name, self.where, self.main, self.above,
                self.thickness, self.leather, self.lies, self.tip,
                self.number, None if p is None else
                (p.ax, p.ay, p.x, p.y, p.turn, p.over, p.flip))


def _across(a: float) -> str:
    return "left" if a < 0.25 else "right" if a > 0.75 else "centre"


def _up(a: float) -> str:
    return "bottom" if a < 0.25 else "top" if a > 0.75 else "middle"


def held_by(placed) -> str:
    """The point of its box a piece is held by, in words: bottom left,
    top centre, middle right, centre."""
    across, up = _across(placed.ax), _up(placed.ay)
    if across == "centre" and up == "middle":
        return "centre"
    return f"{up} {across}"


def measured_from(placed) -> str:
    """What a piece's distances across and up are measured from, for
    beside the boxes they are typed in: "from its left and bottom edges"
    (the main piece's)."""
    across, up = _across(placed.ax), _up(placed.ay)
    if across == "centre" and up == "middle":
        return "from its centre"
    a = "centre" if across == "centre" else f"{across} edge"
    b = "middle" if up == "middle" else f"{up} edge"
    if a.endswith("edge") and b.endswith("edge"):
        return f"from its {across} and {up} edges"
    return f"from its {a} and {b}"


def _lies(stack, parts: Parts) -> Dict[str, tuple]:
    """Where each piece of a stack lies, by shape id: a few words for
    the list, and a sentence or two that say it in full."""
    names = parts.names
    down = 1 if parts.turned else -1    # the face that is down on the bench
    out = {}
    for p in stack.pieces:
        if p.main:
            # it lies on the piece under it that it has most leather on
            best, lap = 0, -1.0
            for j, s in enumerate(parts.sheets, 1):
                if s.side == down and 0 in s.rests and s.lap > lap:
                    best, lap = j, s.lap
            where = f"on {names[best]}" if best else "on the bench"
        else:
            rests = parts.sheets[parts.number(p.key) - 1].rests
            if not rests:
                where = "on nothing"
            else:
                where = ("on " if p.above else "under ") + names[rests[0]]
        ways = (["mirrored"] if p.placed.flip else []) \
            + ([f"turned {p.placed.turn:g}°"] if p.placed.turn else [])
        short = " · ".join(([] if p.grain_up else ["grain down"]) + ways
                           + [where])
        full = f"Lies {where}, " + ", ".join(
            ["grain up" if p.grain_up else "grain down"] + ways) + "."
        if p.main:
            full = ("The main piece: the others are placed against it, and "
                    "the stack folds along its fold lines. ") + full
        else:
            at = held_by(p.placed)
            x, y = p.placed.x, p.placed.y
            moved = ([f"{length(x)} across"] if x else []) \
                + ([f"{length(y)} up"] if y else [])
            if moved:
                short += f" · {at}, " + ", ".join(moved)
                full += (f" Held by its {at}, " + " and ".join(moved)
                         + f" from the {at} of {names[0]}.")
            else:
                short += f" · {at}"
                full += (f" Held by its {at}, level with the {at} of "
                         f"{names[0]}.")
        out[p.key] = (short, full)
    return out


def rows(doc, item=None, parts: Optional[Parts] = None) -> List[Row]:
    """The window's list of pieces for what it shows.

    ``item`` is what is shown, as ``preview3d.showables`` gives it: for a
    stack (``item.stack``, with ``parts`` laid out and built, so that
    its sheets are settled) its pieces come first, the top of the pile
    first; for a piece in no stack, that piece. Then every other piece
    that is in no stack, in the order drawn, and last the outlines drawn
    inside a piece that can still be put on."""
    out: List[Row] = []
    stack = getattr(item, "stack", None)
    taken = {p.key for st in asm.stacks(doc, detail=False)
             for p in st.pieces}
    if stack is not None and parts is not None:
        words = _lies(stack, parts)
        for p in reversed(stack.pieces):
            short, full = words[p.key]
            out.append(Row(p.key, title_of(p.shape), IN, p.main, p.above,
                           p.thickness, p.leather, short, full,
                           parts.number(p.key), p.placed))
    elif item is not None and item.kind in ("piece", "plain"):
        shape = asm.shape_by_id(doc, item.key)
        if shape is not None and shape.shape_id not in taken:
            out.append(Row(shape.shape_id, title_of(shape), SHOWN, True,
                           thickness=float(shape.thickness or 0.0),
                           leather=shape.leather or "", number=0,
                           tip="The piece shown. Tick another piece to put "
                               "it on this one."))
            taken.add(shape.shape_id)
    for shape in asm.offered(doc):
        if shape.shape_id not in taken:
            out.append(Row(shape.shape_id, title_of(shape), OUT,
                           thickness=float(shape.thickness or 0.0),
                           leather=shape.leather or ""))
    for shape, around in asm.drawn_inside(doc):
        if shape.shape_id not in taken:
            inside = f"drawn inside {title_of(around)}"
            out.append(Row(shape.shape_id, title_of(shape), INSIDE,
                           thickness=float(shape.thickness or 0.0),
                           leather=shape.leather or "", lies=inside,
                           tip=f"It is {inside}, where the rest of the "
                               "program reads it as a hole cut in that "
                               "piece. Tick it to put it on as a piece of "
                               "its own."))
    return out


# -- what there is to tell of a stack lying flat ------------------------------

def report(stack, parts: Parts, model) -> List[str]:
    """What there is to tell of a stack lying flat, a sentence or two to
    each thing, in the order the window says them: how many pieces and
    how thick, a main piece that is gone, the pieces that stand past the
    main piece's edge, that stand clear of what is under them, that rest
    on a sliver, that lie on nothing, and the fold lines that are not
    the main piece's. Lengths are in the unit shown. Nothing, for a
    stack of one piece. (What there is to tell of it folded is
    ``fold_report``'s.)"""
    notes = getattr(model, "notes", None)
    if not parts.sheets or not notes:
        return []
    names = [escaped(n) for n in parts.names]
    main = names[0]
    out = []
    for note in notes:
        if note.kind != "thick":
            continue
        # the pieces at the thickest place, from the bench up
        up = 1 if not parts.turned else -1
        over = [k for k in note.other if k and parts.sheets[k - 1].side == up]
        under = [k for k in note.other
                 if k and parts.sheets[k - 1].side != up]
        there = under[::-1] + [k for k in note.other if not k] + over
        text = (f"<b>Put together:</b> {len(stack.pieces)} pieces, "
                f"{length(note.a)} at the thickest ("
                + ", ".join(names[k] for k in there) + ").")
        if note.b < note.a - _SOLID:
            text += (f" Only {length(note.b)} of that is leather: a piece "
                     "there stands clear of what lies under it.")
        out.append(text)
    opened = [sh for sh in getattr(stack, "cut_open", ())
              if sh.placed is not None and sh.placed.main]
    if opened:
        out.append(f"{escaped(opened[0].name or 'The main piece')}, the "
                   "piece the others are placed against, is cut open on "
                   "the drawing. "
                   f"Until it is a closed outline again {main} stands in "
                   "for it; closed again, it comes back where it lay. "
                   "Close its outline to go on placing pieces.")
    elif stack.lost:
        out.append("The piece the others were placed against is gone from "
                   f"the drawing. {main} stands in for it, and every piece "
                   "lies where it last lay.")
    others = [escaped(sh.name or "A piece")
              for sh in getattr(stack, "cut_open", ())
              if sh.placed is not None and not sh.placed.main]
    if others:
        out.append(listed(others) + (" is" if len(others) == 1 else " are")
                   + " cut open on the drawing and left out until "
                   + ("it is" if len(others) == 1 else "they are")
                   + " a closed outline again.")
    # pieces that stand the same distance past the edge are told together
    past: Dict[str, List[int]] = {}
    for note in notes:
        if note.kind == "past":
            past.setdefault(length(note.a), []).append(note.sheet)
    for far, who in past.items():
        if len(who) > 2:
            text = f"{names[who[0]]} (and {len(who) - 1} more) stand"
        elif len(who) == 2:
            text = f"{names[who[0]]} and {names[who[1]]} stand"
        else:
            text = f"{names[who[0]]} stands"
        out.append(f"{text} {far} past the edge of {main}.")
    for note in notes:
        s = parts.sheets[note.sheet - 1] if note.sheet else None
        if note.kind == "clear":
            on = names[s.rests[0]] if s.rests else main
            more = len(note.other) - 1
            out.append(
                f"{names[note.sheet]} rests on {on}; beyond it, it stands "
                f"{length(note.a)} clear of {names[note.other[0]]}"
                + (f" (and of {more} more by less)" if more else "")
                + ". On the bench it beds down over that step: the picture "
                  "keeps it flat.")
        elif note.kind == "sliver":
            out.append(
                f"{names[note.sheet]} rides up onto {names[note.other[0]]} "
                f"on only {length(note.a)} of overlap. If they are meant to "
                "meet edge to edge, use Line up.")
        elif note.kind == "loose":
            out.append(
                f"{names[note.sheet]} lies on nothing: no part of it is "
                f"over {main} or over a piece put on it. It is shown at "
                f"the level of {main}'s face.")
    for p in stack.pieces:
        if p.main or not p.folds:
            continue
        name = names[parts.number(p.key)]
        if len(p.folds) == 1:
            what = escaped(p.folds[0].name or "A fold line")
            out.append(f"{what} is on {name}, which is not the main "
                       "piece: its fold is not made here.")
        else:
            out.append(f"{len(p.folds)} fold lines are on {name}, which is "
                       "not the main piece: its folds are not made here.")
    return out


# -- what there is to tell of a stack folded ----------------------------------

# the colours of the report's warnings: leather in the way of a fold (as
# it is marked on the model), and leather drawn through leather
AMBER = "#b35c00"
RED = "#c0392b"
WARN, BAD = "warn", "bad"       # how ``marks`` marks a piece

# a fold line this near upright on the bench (the sine of its slant) is
# across a piece's width; this near level, across its length
_UPRIGHT = 0.17

#: Leather found drawn less than this far (mm) into leather is not told,
#: counted in the caption or marked: it is what a fold that has moved off
#: its line is let keep (the fold engine moves a fold to within 0.02 mm).
THROUGH = 0.05

#: A piece parted where a fold bends it on one side and not the other
#: (a ``"slit"`` note), or torn where two folds cross (``"crossing"``),
#: and drawn into leather there less deep than this (mm), is where real
#: leather would crimp -- the fold engine's own word for the 0.14 mm of a
#: liner folded past the end of a flap's hinge: told plainly, not in red.
CRIMP = 0.25


def _num(mm: float) -> str:
    """A length without its unit: 97.66, 3.845."""
    return length(mm).rsplit(" ", 1)[0]


def folded_size(layout) -> str:
    """How big a stack is folded, for the caption and the report:
    "97.66 × 90 × 11.2 mm". "" if the fold engine has not said."""
    notes = getattr(layout, "notes", None) or ()
    size = next((n for n in notes if n.kind == "size"), None)
    shut = next((n for n in notes if n.kind == "shut"), None)
    if size is None or shut is None:
        return ""
    return (f"{_num(size.a)} × {_num(size.b)} × {_num(shut.a)} "
            f"{U.current()}")


def _warn(text: str, colour: str = AMBER) -> str:
    return f"<span style='color:{colour}'>⚠ {text}</span>"


def _drawn_through(layout) -> list:
    """The notes of leather drawn into leather that are said in red
    (``_alarm``): pieces into each other or themselves (``"clash"``,
    ``"limit"``), parts of the main piece a moved fold brings down in
    each other (``"landing"``), and pieces parted or torn by the folds
    and drawn into leather there (``"slit"``, ``"crossing"``)."""
    return [n for n in getattr(layout, "notes", None) or () if _alarm(n)]


def _crossed(layout) -> bool:
    """Whether two fold lines of the main piece cross on it."""
    live = [h for h in layout.hinges if layout.steps[h.fold]]
    for i, h in enumerate(live):
        for g in live[i + 1:]:
            if g.fold == h.fold:
                continue
            (ax, ay), (ex, ey), _l = layout.lines[h.fold]
            (bx, by), (fx, fy), _m = layout.lines[g.fold]
            den = ex * fy - ey * fx
            if abs(den) < 1e-9:
                continue
            s = ((bx - ax) * fy - (by - ay) * fx) / den
            r = ((bx - ax) * ey - (by - ay) * ex) / den
            if h.lo - 1e-6 <= s <= h.hi + 1e-6 \
                    and g.lo - 1e-6 <= r <= g.hi + 1e-6:
                return True
    return False


def _reach_across(ring, line) -> tuple:
    """How far an outline reaches each way across a fold line (mm, from
    the line)."""
    (ax, ay), (ex, ey), _l = line
    us = [(p[1] - ay) * ex - (p[0] - ax) * ey for p in ring]
    return min(us), max(us)


def _to_its_ends(layout, k: int, fold: int, over: float) -> bool:
    """Whether the piece ``k`` that goes round a fold reaches the main
    piece's ends either side of it, as a lining does: then what it has
    too much or too little round the bend is what it is to be cut
    shorter or longer by."""
    line = layout.lines[fold]
    lo, hi = _reach_across(layout.sheets[k - 1].ring, line)
    m0, m1 = _reach_across(layout.outline, line)
    reach = abs(over) + 0.5
    return abs(m1 - hi) <= reach and abs(lo - m0) <= reach


def _stop_at(layout, n) -> float:
    """How far from the fold line as it is drawn a piece that reaches
    into a fold's bend (a ``"reach"`` note) is to stop: where the bend
    begins on the piece's side, half the bend from where the fold forms
    -- which is off the drawn line by as much as the fold has moved, one
    way or the other. (It is where the dashed line is drawn.) Below 0
    where the bend begins on the far side of the drawn line."""
    if n.at is None:
        return n.b
    (ax, ay), (ex, ey), _l = layout.lines[n.fold]
    # the side of the line the fold forms on that the piece is on, and
    # where the drawn line is across it
    side = 1.0 if (n.at[1] - ay) * ex - (n.at[0] - ax) * ey >= 0.0 else -1.0
    f = layout.folds[n.fold]
    drawn = (f.a[1] - ay) * ex - (f.a[0] - ax) * ey
    return n.b - side * drawn


def _narrower(stack, parts: Parts, layout, k: int, fold: int) -> str:
    """How a piece is made to stop short of a fold, in the words of the
    drawing: narrower where the fold runs up the piece as it is drawn,
    shorter where it runs across it, and otherwise cut back there."""
    (_a, (ex, ey), _l) = layout.lines[fold]
    turn = 0.0
    piece = stack.piece(parts.keys[k]) if stack is not None else None
    if piece is not None and piece.placed is not None:
        turn = math.radians(piece.placed.turn)
    # the fold's way in the piece's own frame
    ux = abs(ex * math.cos(turn) + ey * math.sin(turn))
    uy = abs(-ex * math.sin(turn) + ey * math.cos(turn))
    if ux <= _UPRIGHT:
        return "narrower"
    if uy <= _UPRIGHT:
        return "shorter"
    return ""


def _pieces(names: List[str], who: List[int]) -> tuple:
    """Pieces named in a sentence, and whether they are more than one:
    A; A and B; A (and 7 more)."""
    if len(who) > 2:
        return f"{names[who[0]]} (and {len(who) - 1} more)", True
    if len(who) == 2:
        return f"{names[who[0]]} and {names[who[1]]}", True
    return names[who[0]], False


# how far through a step a thing is first seen, in words
_PARTS = ((0.25, "a quarter of the way"), (0.5, "half way"),
          (0.75, "three quarters of the way"))


def _when(layout, moment: float) -> str:
    """When in the folding a thing is first seen: lying flat, when the
    folds of a step have been made, or part of the way through a step
    (``moment`` as ``Layout.fractions`` takes it: 1.5 is half way
    through step 2)."""
    step = int(round(moment))
    if abs(moment - step) > 1e-6:
        whole = int(math.floor(moment))
        part = next((words for at, words in _PARTS
                     if abs(moment - whole - at) < 1e-6), "part of the way")
        return f"{part} through step {whole + 1}"
    if step <= 0:
        return "lying flat"
    doing = [f for k, f in enumerate(layout.folds)
             if layout.steps[k] == step]
    if not doing:
        return f"at step {step}"
    shut = all(f.angle >= 175.0 for f in doing)
    which = ("fold " if len(doing) == 1 else "folds ") \
        + listed([f.label for f in doing])
    return (f"when {which} "
            + ("has" if len(doing) == 1 else "have")
            + (" closed" if shut else " folded") + f" (step {step})")


def _deep(n) -> float:
    """How deep a note says leather is drawn into leather (mm)."""
    return n.b if n.kind in ("slit", "crossing") else n.a


def _alarm(n) -> bool:
    """Whether a note tells of leather drawn into leather deep enough to
    be said in red and marked so: a piece drawn into another or itself, a
    part of the main piece a moved fold brings down in another, as far as
    ``THROUGH`` or more; a piece parted or torn by its folds and drawn
    into leather there as far as ``CRIMP``."""
    if n.kind in ("clash", "limit", "landing"):
        return n.a >= THROUGH
    if n.kind in ("slit", "crossing"):
        return n.b >= CRIMP
    return False


def _parted(layout, notes, names, k: int) -> List[str]:
    """What is said of the pieces a fold parts or tears (``"slit"`` and
    ``"crossing"`` notes): where, how long, what lies across it, and how
    deep it is drawn into leather there, in red when that is ``CRIMP`` or
    more."""
    main = names[0]
    label = layout.folds[k].label
    out = []

    def deep(n):
        # how deep it is drawn into leather there, in the sentence
        if n.b >= CRIMP:
            return f", and is drawn {length(n.b)} into leather there"
        return ""

    def crimp(n):
        if THROUGH <= n.b < CRIMP:
            return (f" The picture shows it {length(n.b)} into leather "
                    "there, where real leather would crimp.")
        return ""

    # a piece parted the same way at one fold in several places is told
    # once
    slits: Dict[tuple, List] = {}
    for n in notes:
        if n.kind == "slit":
            across = listed([names[p] for p in n.other
                             if 0 <= p < len(names)])
            slits.setdefault((n.sheet, length(n.a), across, length(n.b),
                              _alarm(n)), []).append(n)
    for (piece, long, across, _d, bad), same in slits.items():
        n = same[0]
        name = names[piece] if 0 <= piece < len(names) else ""
        where = f"along {long}" + (f" in {len(same)} places"
                                   if len(same) > 1 else "")
        over = f", with {across} across it" if across else ""
        text = (f"{name} is parted {where} at fold {label}{over}"
                + deep(n)
                + ": the fold bends it on one side and leaves it flat on "
                "the other. In leather it would have to be slit there, or "
                "stop short of the fold." + crimp(n))
        out.append(_warn(text, RED) if bad else text)
    for n in notes:
        if n.kind != "crossing":
            continue
        name = names[n.sheet] if 0 <= n.sheet < len(names) else ""
        other = layout.folds[n.other[0]].label
        on = n.at is None or pc.inside(n.at, layout.outline)
        if on:
            text = (f"{name} lies over the place where folds {label} and "
                    f"{other} cross" + deep(n) + f". {main} is "
                    "folded along one and then the other and passes "
                    "through itself there: the preview cannot show how "
                    f"{name} would lie." + crimp(n))
        else:
            text = (f"{name} lies over the place where folds {label} and "
                    f"{other} cross, past the edge of {main}"
                    + deep(n) + ": folded along one and then "
                    "the other, it is torn there, and the preview cannot "
                    "show how it would lie." + crimp(n))
        out.append(_warn(text, RED) if _alarm(n) else text)
    return out


def _fold_words(stack, parts: Parts, layout, k: int, notes) -> List[str]:
    """What there is to tell of one fold of a stack."""
    names = [escaped(n) for n in parts.names]
    main = names[0]
    fold = layout.folds[k]
    label = fold.label
    title = f"Fold {label}" + (f" ({escaped(fold.name)})" if fold.name
                               else "")

    def first(kind):
        return next((n for n in notes if n.kind == kind), None)

    def height(piece):
        return layout.sheets[piece - 1].hi if piece else 0.0

    out = []
    bend, moved = first("bend"), first("moved")
    where = "where it forms" if moved is not None else "the fold line"
    takes = "takes nothing" if bend is None else (
        f"takes {length(bend.a)} of {main}, {length(bend.b)} each side of "
        f"{where}")
    radius = length(layout.radius[k])
    rule = first("ride") or first("meet") or first("corner")
    if rule is not None and rule.kind == "meet" and not rule.sheet \
            and not rule.other[0]:
        rule = None                    # the main piece on itself
    if rule is None:
        if layout.wrap[k] > 1e-6:
            out.append(f"{title} bends at {radius} to go round what is "
                       f"folded in before it, and {takes}.")
        else:
            out.append(f"{title} {takes}.")
    elif rule.kind == "ride":
        out.append(f"{title} goes round what rides inside it: "
                   f"{names[rule.sheet]} stands {length(rule.b)} off the "
                   f"face of {main} there. It bends at {radius} and "
                   f"{takes}.")
    elif rule.kind == "meet":
        one, other = rule.sheet, rule.other[0]
        h1, h2 = height(one), height(other)
        split = ""
        if abs(h1 + h2 - rule.b) < 1e-6:
            if abs(h1 - h2) < 1e-6:
                split = f", {length(h1)} on each half"
            elif min(h1, h2) < 1e-6:
                split = " on one half"
            else:
                split = (f", {length(max(h1, h2))} on one half and "
                         f"{length(min(h1, h2))} on the other")
        out.append(f"{title} closes over {length(rule.b)} of leather"
                   f"{split}: {names[one]} comes to rest on {names[other]}. "
                   f"It bends at {radius} and {takes}.")
    else:
        out.append(f"{title} stands open with leather on both sides of it, "
                   f"and bends at {radius} so that {names[rule.sheet]} and "
                   f"{names[rule.other[0]]} stand clear of each other at "
                   f"its corner. It {takes}.")
    if moved is not None:
        text = (f"Fold {label} folds {length(moved.a)} off its line, "
                f"towards panel {moved.other[0]}")
        stuck = getattr(layout, "stuck", None)
        text += ("." if stuck and stuck[k]
                 else ", to go round what is folded inside it.")
        # (what to change in the drawing, as for a piece shown alone: the
        # fold engine gives it for a stack only once it has tried it)
        remedy = layout.remedy(k, length) if hasattr(layout, "remedy") \
            else ""
        out.append(" ".join(filter(None, (text, remedy))))
    closes = first("closes")
    if closes is not None and closes.a == 0 < closes.b:
        if len(closes.other) == 1:
            out.append(f"Fold {label} closes away from "
                       f"{names[closes.other[0]]}, laid on {main}: it ends "
                       "up on the outside. If it belongs inside, press "
                       "Flip.")
        else:
            out.append(f"Fold {label} closes away from the "
                       f"{len(closes.other)} pieces laid on {main}: they "
                       "end up on the outside. If they belong inside, "
                       "press Flip.")
    # pieces that reach into the bend the same way are told together
    reach: Dict[tuple, List[int]] = {}
    for n in notes:
        if n.kind == "reach" and warns(layout, n):
            way = _narrower(stack, parts, layout, n.sheet, k)
            stop = _stop_at(layout, n)
            stop = f"{length(stop)} from" if stop >= 0.0 \
                else f"{length(-stop)} past"
            reach.setdefault((length(n.a), stop, way), []).append(n.sheet)
    for (deep, stop, way), who in reach.items():
        them, many = _pieces(names, who)
        it = "them" if many else "it"
        cut = f"make {it} {deep} {way}" if way \
            else f"cut {deep} off {it} there"
        out.append(_warn(
            f"{them} {'reach' if many else 'reaches'} {deep} into the "
            f"bend of fold {label}, where leather cannot lie flat. "
            f"{'They are' if many else 'It is'} shown bent with it. Stop "
            f"{it} {stop} the fold line: {cut}."))
    for n in notes:
        name = names[n.sheet] if 0 <= n.sheet < len(names) else ""
        if n.kind in ("cross", "level"):
            text = _crossing_words(layout, n, name, main, label)
            out.append(_warn(text) if warns(layout, n) else text)
    return out + _parted(layout, notes, names, k)


# how a piece that goes round a fold comes out (``_crossing``): round
# the inside or the outside, held at both ends or free at one (a strap),
# and with too much leather, too little, or as much as it needs
LONG_IN, SHORT_IN, STRAP_IN = "long in", "short in", "strap in"
LONG_OUT, SHORT_OUT, STRAP_OUT = "long out", "short out", "strap out"
LEVEL_IN, LEVEL_OUT = "level in", "level out"


def _crossing(layout, n) -> tuple:
    """How a piece that goes round a fold (a ``"cross"`` or ``"level"``
    note) comes out, and by how much (mm): ``(how, mm)``. Whether it
    comes level when shut is the fold engine's to say (``"level"``: as
    much shorter round the inside, or longer round the outside, as the
    bend takes, within ``sheets.LEVEL`` either way, its ends with the
    main piece's). Otherwise a piece that reaches the main piece's ends
    either side of the fold, as a lining does, is too long or too short
    by ``mm``, and one that does not reach them (a strap) has ``mm`` too
    much or too little leather round the bend."""
    over, short = n.a, n.b
    if n.kind == "level":
        return (LEVEL_IN, short) if over > 0.0 else (LEVEL_OUT, -short)
    lining = _to_its_ends(layout, n.sheet, n.fold, over)
    if over > 0.0:
        if not lining:
            return STRAP_IN, over
        more = over - short
        return (LONG_IN, more) if more > 0.0 else (SHORT_IN, -more)
    if not lining:
        return STRAP_OUT, -over
    less = short - over                 # what it is short of what it needs
    return (SHORT_OUT, less) if less > 0.0 else (LONG_OUT, -less)


def warns(layout, n) -> bool:
    """Whether what a note of a fold tells is a warning: said with a
    warning sign in the report and marked amber on the piece's leather in
    the bend. A piece that reaches into a bend, and one that goes round
    it with leather to spare or too little; not one that comes level."""
    return n.kind in ("reach", "cross")


def _longer(mm: float, than: str, way: str) -> str:
    """How much shorter (``way`` "shorter") or longer a piece is than the
    main piece across a fold, said of one that comes level."""
    if mm > 0.0:
        return f"already {length(mm)} {way} than {than}"
    if mm < 0.0:
        other = "longer" if way == "shorter" else "shorter"
        return f"{length(-mm)} {other} than {than}"
    return f"as long as {than}"


def _crossing_words(layout, n, name: str, main: str, label: str) -> str:
    """What is said of a piece that goes round a fold (a ``"cross"``
    note): how much leather it has too much or too little there, and
    what that comes to however it is held. (The warning sign is
    ``warns``'s to give.)"""
    how, mm = _crossing(layout, n)
    x = length(mm)
    if how == STRAP_IN:
        return (f"{name} goes round the inside of fold {label} and has {x} "
                "more leather there than the bend has room for: free at an "
                "end, it stands that much further out when shut; held at "
                "both ends, it wrinkles inside the fold. The picture "
                "squeezes it to fit (marked amber).")
    if how == LONG_IN:
        return (f"{name} goes round the inside of fold {label} and has {x} "
                f"too much leather there. Cut it {x} shorter across the "
                "fold and it lies smooth and comes level when shut; sewn in "
                "flat at this length it wrinkles inside the fold. The "
                "picture squeezes it to fit (marked amber).")
    if how == SHORT_IN:
        return (f"{name} goes round the inside of fold {label} and is "
                f"{length(n.b)} shorter than {main} across it, {x} more "
                f"than the bend takes: shut, it stops short of the ends of "
                f"{main}. Made {x} longer across the fold it comes level. "
                "The picture squeezes it to fit (marked amber).")
    if how == STRAP_OUT:
        return (f"{name} goes round the outside of fold {label} and needs "
                f"{x} more leather there than it has: free at an end, it "
                "ends that much short when shut; held at both ends, it "
                "pulls tight round the fold. The picture stretches it to "
                "fit (marked amber).")
    if how == SHORT_OUT:
        return (f"{name} goes round the outside of fold {label} and has {x} "
                f"too little leather there. Cut it {x} longer across the "
                "fold and it lies smooth and comes level when shut; sewn on "
                "flat at this length it pulls tight round the fold. The "
                "picture stretches it to fit (marked amber).")
    if how == LONG_OUT:
        return (f"{name} goes round the outside of fold {label} and is "
                f"{length(-n.b)} longer than {main} across it, {x} more "
                f"than the bend takes: shut, it stands {x} past the ends of "
                f"{main}. Cut it {x} shorter across the fold and it comes "
                "level when shut. The picture stretches it round the fold "
                "and does not slide it (marked amber).")
    side, way = ("inside", "shorter") if how == LEVEL_IN \
        else ("outside", "longer")
    return (f"{name} goes round the {side} of fold {label} and is "
            f"{_longer(mm, main, way)} across it: shut, it comes level (the "
            "picture does not slide it).")


def fold_report(stack, parts: Parts, layout) -> List[str]:
    """What there is to tell of a stack folded, after what ``report``
    tells of it lying flat, in the order the window says it: each fold
    of the main piece in the order they are made -- why it bends where it
    does and what it takes, a fold that forms off its line, one that
    closes away from the pieces laid on it, the pieces that reach into
    its bend, go round it, are cut at the end of it or lie over a
    crossing -- then its size folded, then what is drawn through what
    (in red). Lengths are in the unit shown. Nothing, for a stack of one
    piece, or one with nothing to fold."""
    notes = getattr(layout, "notes", None)
    if not parts.sheets or not notes or not layout.n_steps:
        return []
    names = [escaped(n) for n in parts.names]
    main = names[0]
    out = []
    order = sorted((k for k in range(len(layout.folds)) if layout.steps[k]),
                   key=lambda k: (layout.steps[k], k))
    for k in order:
        out += _fold_words(stack, parts, layout, k,
                           [n for n in notes if n.fold == k])
    folded = folded_size(layout)
    if folded:
        text = f"<b>Folded</b> it measures {folded}"
        # (a fold that stands open has no spine, and a needle through it
        # slants: the size is all there is to say)
        open_ = any(f.angle < 175.0 for k, f in enumerate(layout.folds)
                    if layout.steps[k])
        spines = [] if open_ else [n for n in notes if n.kind == "spine"]
        if len(spines) == 1:
            text += f"; the spine is {length(spines[0].a)} across"
        elif spines:
            text += ("; the rounds of folds "
                     + listed([layout.folds[n.fold].label for n in spines])
                     + " are " + listed([length(n.a) for n in spines])
                     + " across")
        text += "."
        leather = next((n for n in notes if n.kind == "leather"), None)
        if leather is not None and not open_:
            text += (f" A needle through it goes through {length(leather.a)}"
                     f" of leather at the most and {length(leather.b)} at "
                     "the least.")
        out.append(text)
    for n in _drawn_through(layout):
        when = _when(layout, n.b)
        if n.kind == "clash":
            one, two = n.sheet, n.other[0]
            if one == two:
                out.append(_warn(f"{names[one]} is drawn {length(n.a)} into "
                                 f"itself {when}.", RED))
            else:
                out.append(_warn(f"{names[one]} and {names[two]} are drawn "
                                 f"{length(n.a)} into each other {when}.",
                                 RED))
        elif n.kind == "limit":
            # (where its folds cross, that is why; otherwise the judge
            # says only how deep and when)
            why = (": folds that cross are folded one after the other, "
                   "and the preview cannot open the leather for the "
                   "second." if _crossed(layout) else ".")
            out.append(_warn(f"{main} is drawn {length(n.a)} into itself "
                             f"{when}{why}", RED))
        # (a part of the main piece that a moved fold brings down in
        # another is in the fold engine's own warnings, below)
    out += [_warn(w, RED) for w in _warned(layout, names)]
    return out


def _warned(layout, names) -> List[str]:
    """The fold engine's own warnings of the main piece, with the pieces
    named that are in the way of a fold that cannot move out of the way
    of what is folded inside it (a ``"stuck"`` note)."""
    out = list(layout.warned(length))
    for n in getattr(layout, "notes", None) or ():
        if n.kind != "stuck" or not 0 <= n.fold < len(layout.folds):
            continue
        who = [names[p] for p in n.other if 0 < p < len(names)]
        if not who:
            continue                # its own leather: its sentence says so
        label = layout.folds[n.fold].label
        said = (f"{listed(who)} {'is' if len(who) == 1 else 'are'} in its "
                "way.")
        said_of = re.compile(rf"\bfold {re.escape(label)}\b", re.I)
        at = next((i for i, w in enumerate(out) if said_of.search(w)), None)
        if at is None:
            out.append(f"Fold {label} cannot move out of the way of what is "
                       f"folded inside it: {said}")
        else:
            out[at] += " " + said
    return out


def marks(layout) -> Dict[tuple, str]:
    """How the pieces of a stack are marked on the model, from its notes:
    ``{(piece, fold): WARN}`` on the leather of a piece in the bend of a
    fold where the report warns of it (``warns``), and ``BAD`` where it
    says in red that leather is drawn into leather (``_alarm``) -- on
    the whole of each piece it names (``(piece, None)``), or on the parts
    of the main piece a moved fold brings down in each other (its panel,
    ``(0, ("panel", i))``, or its bend, ``(0, fold)``) -- and on the
    pieces in the way of a fold that cannot close (``"stuck"``: a piece
    whole, the main piece in that fold's bend)."""
    out: Dict[tuple, str] = {}
    for n in getattr(layout, "notes", None) or ():
        if warns(layout, n):
            out[(n.sheet, n.fold)] = WARN
    for n in getattr(layout, "notes", None) or ():
        if n.kind == "stuck":
            for piece in n.other:
                out[(piece, None) if piece else (0, n.fold)] = BAD
    for n in _drawn_through(layout):
        for key in _bad_keys(n):
            out[key] = BAD
    return out


def _bad_keys(n) -> list:
    """Where a note of leather drawn into leather is marked: see
    ``marks``."""
    if n.kind == "landing":
        keys = []
        for part in n.other:
            if part and part[0] == "panel":
                keys.append((0, ("panel", part[1])))
            elif part and part[0] == "bend":
                keys.append((0, part[1]))
        return keys
    if n.kind == "limit":
        return [(0, None)]
    if n.kind in ("slit", "crossing"):
        return [(n.sheet, None)]
    return [(n.sheet, None), (n.other[0], None)]


def drawn_through(parts: Parts, layout) -> str:
    """The second line of the caption when the report says in red that
    leather is drawn into leather, for the report to say more: how many
    pieces, or the one. "" when nothing is."""
    found = _drawn_through(layout)
    if not found:
        return ""
    who = set()
    for n in found:
        who.update({key[0] for key in _bad_keys(n)})
    # (a piece parted by a fold may be drawn into the main piece there,
    # not only into itself)
    through = all(n.kind in ("clash", "limit", "landing") for n in found)
    if len(who) == 1:
        name = parts.names[next(iter(who))]
        return (f"{name} is drawn through itself: see the report" if through
                else f"{name} is drawn into leather: see the report")
    if through:
        return (f"{len(who)} things are drawn through each other: see the "
                "report")
    return f"{len(who)} pieces are drawn into leather: see the report"


def closes_tip(parts: Parts, layout, k: int) -> str:
    """What the way a fold closes means for the pieces laid on the main
    piece, for its row's tooltip: "Closes over the 9 pieces laid on Outer
    shell." "" for a fold that closes over none, or does not close."""
    note = next((n for n in getattr(layout, "notes", None) or ()
                 if n.kind == "closes" and n.fold == k), None)
    if note is None:
        return ""
    main = parts.names[0]
    inside, outside = int(note.a), int(note.b)
    if not inside:
        if outside == 1:
            return (f"Closes away from {parts.names[note.other[0]]}, laid on "
                    f"{main}.")
        return f"Closes away from the {outside} pieces laid on {main}."
    over = "the piece" if inside == 1 else f"the {inside} pieces"
    if not outside:
        return f"Closes over {over} laid on {main}."
    return (f"Closes over {inside} of the pieces laid on {main}; "
            f"{outside} end{'s' if outside == 1 else ''} up on the "
            "outside.")

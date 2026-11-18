"""The 3D assembly preview: put pieces together, fold a piece, and look.

A piece folds along the lines marked as fold lines across it (right-click a
line > Mark as fold line). Every fold line has a row in the dialog's table:
the step it folds at, the way it folds, how far, and how tightly; the
leather's thickness and bend radius are set once for the piece. Panels
drawn edge to edge with no fold lines fold up along the edges they share
(a paper net).

Pieces are put together on the dialog's Pieces tab: ticked there, a piece
is laid on the stack, each piece with its own thickness and leather, and
the stack is shown as it lies on the bench (``stackpanel`` is that tab,
``stackmodel`` makes the model of a stack and words what it has to tell).

The model itself comes from ``leathercad.foldsolid`` and
``leathercad.sheets`` and is painted by ``foldview``; this module reads
the drawing, builds the dialog, and writes what is set in it back to the
drawing -- the fold settings to the fold lines, a piece's thickness,
leather and place to the piece -- so that it is saved with the file.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QFormLayout, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QScrollArea, QSlider,
                               QSplitter, QStyle, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolButton,
                               QVBoxLayout, QWidget)

from leathercad import assembly as asm
from leathercad import foldsolid as fs
from leathercad import polycut as pc

from . import stackmodel as sm
from . import units as U
from .foldview import (DEFAULT_LEATHER, LEATHERS, FoldView, fold_colour,
                       hole_colour)
from .mathspin import MathSpinBox, NoWheelComboBox, NoWheelSpinBox
from .stackmodel import length as _len
from .stackmodel import size as _size
from .stackpanel import PiecesPanel

_SLIDER = 200                     # slider ticks to a folding step
_APART = 10.0                     # mm a layer is lifted, pulled right apart
_CLOSE = 0.4                      # seconds the layers take to come together


# -- reading the drawing ------------------------------------------------------

class Foldable:
    """Something in the drawing that the preview can show: a piece with
    fold lines across it (``kind`` "piece"), panels drawn edge to edge
    ("net"), pieces put together ("stack": ``stack`` is the
    ``assembly.Stack`` as it was read, and ``key`` its id), or a piece no
    fold line crosses ("plain"), which is shown so that others can be put
    on it."""

    def __init__(self, kind, key, title):
        self.kind = kind
        self.key = key
        self.title = title
        self.stack = None          # a stack: its pieces as they lie
        self._later = None         # what is read only when it is shown
        self.outline = []          # a piece: its outline
        self.cutouts = []
        self.marks = []            # slits and scored lines on it
        self.lines = []            # the fold-line shapes across it
        self.ends = []             # ... and where each ran when it was read
        self.outlines = []         # a net: its panels
        self.rings = []            # ... thinned for the model
        self.holes = []
        self.area = 0.0

    def fill(self):
        """Read what was left for when it is shown: the cutouts, marks and
        stitch holes of a piece no fold line crosses."""
        later, self._later = self._later, None
        if later is not None:
            later(self)

    def folds_signature(self):
        """What the rows of the fold table are made from: the fold lines
        and their settings (for a stack, its main piece's)."""
        if self.stack is not None:
            main = self.stack.main
            return (main.key, main.mirrored, tuple(
                (f.a, f.b, f.angle, f.direction, f.radius, f.order, f.name,
                 f.key) for f in main.folds))
        return (tuple(tuple(o) for o in self.outlines),
                tuple((ends, s.fold_dir, s.fold_angle, s.fold_order,
                       s.fold_radius, s.name)
                      for ends, s in zip(self.ends, self.lines)))

    def signature(self):
        """Everything the model is built from: unchanged, nothing to redo.
        (A fold line goes by where it ran when the drawing was read: moved
        since, it is still the same line.) A stack goes by how its pieces
        lie on the bench, so that moving, turning or nesting them on the
        drawing changes nothing."""
        if self.stack is not None:
            st = self.stack
            return (self.kind, self.key, st.signature(), st.lost,
                    tuple((p.name, p.mirrored) for p in st.pieces))
        self.fill()
        return (self.kind, self.key, tuple(self.outline),
                tuple(tuple(c) for c in self.cutouts),
                tuple((k, tuple(pts)) for k, pts in self.marks),
                tuple(tuple(o) for o in self.outlines), tuple(self.holes),
                tuple((ends, s.fold_dir, s.fold_angle, s.fold_order,
                       s.fold_radius, s.name)
                      for ends, s in zip(self.ends, self.lines)))


def _ring(pts) -> list:
    return [(p.x, p.y) for p in pts]


def _fold_ends(shape):
    pts = shape.world_polyline()[0]
    if len(pts) < 2:
        return None
    a, b = (pts[0].x, pts[0].y), (pts[-1].x, pts[-1].y)
    return (a, b) if math.dist(a, b) > 1e-6 else None


def fold_lines(shapes) -> List[fs.FoldLine]:
    """The fold-line shapes as folds, lettered left to right (top to bottom
    where they line up). Each keeps its shape as ``key``."""
    out = []
    for sh in shapes:
        ends = _fold_ends(sh)
        if ends is None:
            continue
        out.append(fs.FoldLine(
            ends[0], ends[1],
            angle=min(180.0, max(0.0, float(sh.fold_angle or 0.0))),
            direction=-1 if sh.fold_dir == "back" else 1,
            radius=float(getattr(sh, "fold_radius", -1.0)),
            order=int(getattr(sh, "fold_order", 0) or 0),
            name=sh.name or "", key=sh))
    return sm.lettered(out)


def document_holes(doc) -> list:
    """Every stitch hole in the drawing as ``(x, y, diameter)``."""
    from leathercad.stitching import holes_for_shape
    out = []
    for sh in doc.shapes:
        if getattr(sh, "construction", False):
            continue
        try:
            res = holes_for_shape(sh, doc)
        except Exception:
            continue
        if res.holes:
            st = getattr(sh, "stitch", None)
            dia = float(getattr(st, "hole_diameter", 1.0) or 1.0)
            out += [(h.point.x, h.point.y, dia) for h in res.holes]
    for sl in getattr(doc, "stitch_lines", None) or []:
        try:
            holes = sl.result().holes
        except Exception:
            continue
        dia = float(getattr(sl.settings, "hole_diameter", 1.0) or 1.0)
        out += [(h.point.x, h.point.y, dia) for h in holes]
    for h in getattr(doc, "holes", None) or []:
        out.append((h.point.x, h.point.y,
                    float(getattr(h, "hole_diameter", 1.0) or 1.0)))
    return out


def _holes_in(holes, ring) -> list:
    x0, y0, x1, y1 = pc.bounds(ring)
    return [h for h in holes if x0 <= h[0] <= x1 and y0 <= h[1] <= y1
            and pc.inside((h[0], h[1]), ring)]


def _marks_in(drawn, ring) -> list:
    """The drawn lines that lie on a piece (the ends of each on it)."""
    x0, y0, x1, y1 = pc.bounds(ring)
    out = []
    for kind, pts in drawn:
        a, b = pts[0], pts[-1]
        if (x0 <= a[0] <= x1 and y0 <= a[1] <= y1 and pc.inside(a, ring)
                and pc.inside(b, ring)):
            out.append((kind, pts))
    return out


def foldables(doc) -> List[Foldable]:
    """What can be folded in a drawing: each piece with fold lines across
    it, biggest first, then the net of panels that share edges (if any)."""
    return _read(doc)


def _read(doc, loose: Optional[list] = None) -> List[Foldable]:
    """What can be folded in a drawing (see ``foldables``). ``loose``, if
    given, is filled with the pieces no fold line crosses, in the order
    drawn, each as a "plain" ``Foldable``."""
    from leathercad.pieces import classify, closes, on_cut_layer
    cut = on_cut_layer(doc)
    folds = [s for s in doc.shapes if getattr(s, "is_fold_line", False)
             and _fold_ends(s) is not None]
    rings, owners = [], []
    drawn = []                      # slits, and lines scored or engraved
    for sh in doc.shapes:
        if getattr(sh, "is_fold_line", False) \
                or getattr(sh, "construction", False):
            continue
        pts, _corners, closed = sh.world_polyline()
        if not cut(sh):
            if len(pts) >= 2:
                drawn.append(("line", _ring(pts) + ([(pts[0].x, pts[0].y)]
                                                    if closed else [])))
        elif closes(pts, closed):
            rings.append(pts)
            owners.append(sh)
        elif len(pts) >= 2:
            drawn.append(("cut", _ring(pts)))
    if not rings:
        return []
    is_piece, parent, areas = classify(rings)
    found = []

    def holes():                   # read once, and only if something folds
        if not found:
            found.append(document_holes(doc))
        return found[0]

    out: List[Foldable] = []
    plain = []                      # pieces no fold line crosses
    for i, sh in enumerate(owners):
        if not is_piece[i]:
            continue
        ring = fs.model_outline(_ring(rings[i]))    # thinned once, here
        across = [s for s in folds
                  if fs.split_piece(ring, fold_lines([s]))[1][0] is not None]
        x0, y0, x1, y1 = pc.bounds(ring)
        f = Foldable("piece" if across else "plain", sh.shape_id,
                     sh.name or f"Piece {_size(x1 - x0, y1 - y0)}")
        f.outline = ring
        f.area = areas[i]

        def rest(f, i=i, ring=ring):
            f.cutouts = [_ring(rings[j]) for j in range(len(rings))
                         if parent[j] == i and not is_piece[j]]
            f.marks = _marks_in(drawn, ring)
            f.holes = _holes_in(holes(), ring)

        if not across:
            plain.append(i)
            if loose is not None:
                f._later = rest        # read if it comes to be shown
                loose.append(f)
            continue
        f.lines = across
        f.ends = [_fold_ends(s) for s in across]
        rest(f)
        out.append(f)
    out.sort(key=lambda f: -f.area)
    if len(plain) >= 2:
        outlines = [pc.ccw(_ring(rings[i])) for i in plain]
        regions, nfolds, _lines = fs.net_regions(outlines)
        if nfolds:
            joined = [k for k, (_p, flags) in enumerate(regions)
                      if any(fl >= 0 for fl in flags)]
            f = Foldable("net", "net", f"{len(joined)} panels that share "
                                       f"edges")
            f.outlines = [outlines[k] for k in joined]
            for o in f.outlines:
                f.holes += _holes_in(holes(), o)
            out.append(f)
    return out


def showables(doc) -> List[Foldable]:
    """What the preview can show of a drawing, in the order its "Show"
    box lists them: each stack of pieces put together, titled from its
    main piece ("Outer shell + 9 pieces"); then what folds and is in no
    stack, as ``foldables`` gives it -- each piece with fold lines across
    it, and the net; then every other piece ("plain"), on which a stack
    can be started."""
    out: List[Foldable] = []
    taken = set()
    for st in asm.stacks(doc):
        f = Foldable("stack", st.key, sm.stack_title(st))
        f.stack = st
        out.append(f)
        taken.update(p.key for p in st.pieces)
    loose: List[Foldable] = []
    out += [f for f in _read(doc, loose)
            if f.kind == "net" or f.key not in taken]
    return out + [f for f in loose if f.key not in taken]


def worth_showing(doc) -> bool:
    """Whether the preview has anything to do for a drawing: a stack in
    it, something that folds, or two pieces at least of which one can be
    put on the other (a pocket drawn inside its panel counts as one).
    One plain piece alone is not worth opening for."""
    shown = showables(doc)
    if any(f.kind != "plain" for f in shown):
        return True
    return len(shown) + len(asm.drawn_inside(doc)) >= 2


# -- the dialog ---------------------------------------------------------------

def _bullet(state: int) -> str:
    """A dot of the colour the view gives a stitch hole in that state."""
    return f"<span style='color:{hole_colour(state).name()}'>●</span>"


def _tally(off: int, worst: float, met: int, gap: float, lone: int,
           more: int = 0) -> str:
    """So many stitch holes are out of line, line up and have nothing to
    meet, in words, each count with a dot of its colour in the view.
    (``more`` holes that are told of elsewhere: those that line up are
    then not "all" of them.)"""
    bits = []
    if off:
        bits.append(f"{_bullet(fs.OFF)} {off} "
                    + ("is out of line, by " if off == 1
                       else "are out of line, by up to ") + _len(worst))
    if met:
        close = f", to within {_len(gap)}" if gap > 0.05 else ""
        some = f"all {met}" if met > 1 and not (off or lone or more) \
            else f"{met}"
        bits.append(f"{_bullet(fs.MEETS)} {some} "
                    + ("lines up" if met == 1 else "line up") + close)
    if lone:
        bits.append(f"{_bullet(fs.LONE)} "
                    + (f"none of the {lone} has a hole to meet"
                       if lone > 1 and not (off or met)
                       else f"{lone} " + ("has" if lone == 1 else "have")
                       + " no hole to meet"))
    return "; ".join(bits) + "."


def _finding(f) -> str:
    """One pair of panels lying together, in words: how many of their
    stitch holes are out of line, line up, and have nothing to meet."""
    return f"Panels {f.a} and {f.b}: " \
        + _tally(f.off, f.worst, f.met, f.gap, f.lone)


def _hole_report(check) -> str:
    """What the check of the stitch holes found, for under the table. One
    pair of panels is said in full. With more, a hole lies in a stack of
    several and is out of line if any two of them miss: so first what was
    found for the holes themselves -- which is what the view shows -- and
    then which panels have holes that meet or miss."""
    head = "<b>Stitch holes where panels lie on each other:</b> "
    found = check.findings
    if len(found) == 1:
        return head + _finding(found[0])
    out = head + _tally(len(check.off),
                        max(f.worst for f in found),
                        len(check.holes(fs.MEETS)),
                        max(f.gap for f in found),
                        len(check.holes(fs.LONE)))
    for f in found:
        out += f"<br>Panels {f.a} and {f.b}: {_pair(f)}."
    return out


def _pair(f) -> str:
    """How many holes of two layers miss and how many line up, in a few
    words, for one line of a list of pairs."""
    bits = []
    if f.off:
        bits.append(f"{f.off} " + ("misses, by " if f.off == 1
                                   else "miss, by up to ")
                    + _len(f.worst))
    if f.met:
        bits.append(f"{f.met} " + ("lines up" if f.met == 1
                                   else "line up")
                    + (f", to within {_len(f.gap)}" if f.gap > 0.05
                       else ""))
    return "; ".join(bits) or "none line up"


def _laid_report(stack, check) -> str:
    """What the check of the stitch holes found in a stack lying flat
    (``sheets.FlatStack.lineup``), for the report.

    First, as plain counts, the holes of one piece that pass through
    another where it has none: the laser cuts each piece only its own
    holes, so this is how the pattern stands, not a fault. Then the holes
    that are meant to meet, worded as for a folded piece, with the names
    of the pieces where that says which panels."""
    found = [f for f in check.findings if getattr(f, "laid", False)]
    through = {}                # a piece: the pieces its holes pass through
    for f in found:
        for mine, other, n in ((f.a, f.b, f.through[0]),
                               (f.b, f.a, f.through[1])):
            if n:
                through.setdefault(mine, []).append((other, n))
    names = {}
    for f in found:
        names[f.a], names[f.b] = f.names
    out = []
    for mine, where in through.items():
        if len(where) == 1:
            (other, n), = where
            total = stack.hole_sheet.count(mine)
            some = f"All {n} of {names[mine]}'s" if n >= total \
                else f"{n} of {names[mine]}'s {total}"
            out.append(f"{some} holes pass through {names[other]}, which "
                       "has no holes there. The laser will not cut them "
                       "in that piece.")
        else:
            out.append(
                f"{names[mine]}'s holes pass through "
                + sm.listed([f"{names[other]} ({n})" for other, n in where])
                + ", which have no holes there. The laser will not cut "
                  "them in those pieces.")
    sewn = [f for f in found if f.met or f.off]
    passing = sum(n for where in through.values() for _o, n in where)
    if len(sewn) == 1:
        f, = sewn
        out.append(f"{f.names[0]} and {f.names[1]}: "
                   + _tally(f.off, f.worst, f.met, f.gap, 0, f.lone))
    elif sewn:
        out.append(_tally(len(check.off), max(f.worst for f in sewn),
                          len(check.holes(fs.MEETS)),
                          max(f.gap for f in sewn), 0, passing)
                   + "".join(f"<br>{f.names[0]} and {f.names[1]}: "
                             f"{_pair(f)}." for f in sewn))
    if not out:
        return ""
    return "<b>Stitch holes through the layers:</b> " + " ".join(out)


class _RadiusSpin(MathSpinBox):
    """A bend radius, or Auto (the value below 0)."""

    def valueFromText(self, text: str) -> float:   # noqa: N802
        if text.strip().lower().startswith("auto") or not text.strip():
            return self.minimum()
        return super().valueFromText(text)


class FoldPreviewDialog(QDialog):
    """Put pieces together and fold a piece in 3D.

    The Pieces tab lists the pieces of the stack shown and those not put
    on yet: what is ticked, moved, lined up or turned there goes to the
    pieces in the drawing. The Folds tab has one row per fold line: when
    it folds, which way, how far, how tightly; changes go straight to the
    fold lines. All of it can be undone in the drawing, a run of changes
    made within a moment of each other as one step.

    The dialog keeps the ids of pieces, not their shapes, for what it
    writes: undo in the main window replaces every shape of the drawing,
    and the dialog reads it again a moment after any change to it."""

    def __init__(self, doc=None, canvas=None, parent=None, dark=False,
                 settings=None):
        super().__init__(parent)
        self.setWindowTitle("3D assembly preview")
        self.resize(1220, 740)
        self.canvas = canvas
        self._doc = doc
        self._settings = settings
        self.items: List[Foldable] = []
        self.current: Optional[Foldable] = None
        self.folds: List[fs.FoldLine] = []
        self.layout_ = None           # the model shown: a Layout, or a stack
        self.parts: Optional[sm.Parts] = None    # a stack as it is built
        self._net = None              # (regions, lines) of the net shown
        self._base: Optional[int] = None
        self._busy = False            # filling widgets: ignore their signals
        self._writing = False         # our own change to the drawing
        self._playing = False
        self._closing = 0.0           # how far apart the layers were at Play
        self._last_tick = 0.0
        self._unsaved = False         # a change not yet an undo step
        self._listening = False       # to the drawing, and for the unit
        self._main: Optional[str] = None    # the piece shown, by its id
        self._built = None            # what the model shown was built from
        self._fold_rows = None        # ... and the rows of the fold table

        self.view = FoldView(self, dark)
        self.view.foldPicked.connect(self._pick_fold)
        self.view.piecePicked.connect(self._pick_piece)

        # -- under the view: the folding, then the view --------------------
        SP = QStyle.StandardPixmap
        self.btn_first = self._tool(SP.SP_MediaSkipBackward,
                                    "Open it out flat (Home)",
                                    lambda: self._go(0.0))
        self.btn_prev = self._tool(SP.SP_MediaSeekBackward,
                                   "Back one fold (Left)",
                                   lambda: self._step(-1))
        self.btn_play = self._tool(SP.SP_MediaPlay,
                                   "Fold it up, one fold after another "
                                   "(Space)", self._toggle_play)
        self.btn_next = self._tool(SP.SP_MediaSeekForward,
                                   "On one fold (Right)",
                                   lambda: self._step(1))
        self.btn_last = self._tool(SP.SP_MediaSkipForward,
                                   "Fold it right up (End)",
                                   lambda: self._go(float("inf")))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setToolTip("Drag to fold and unfold")
        self.slider.valueChanged.connect(self._slid)
        run = QHBoxLayout()
        for b in (self.btn_first, self.btn_prev, self.btn_play, self.btn_next,
                  self.btn_last):
            run.addWidget(b)
        run.addWidget(self.slider, 1)

        look = QHBoxLayout()
        look.addWidget(QLabel("View"))
        self.btn_views = {}
        for text, where in (("3D", "iso"), ("Front", "front"),
                            ("Back", "back"), ("Side", "side")):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, w=where: self._look(w))
            look.addWidget(b)
            self.btn_views[where] = b
        fit = QPushButton("Fit")
        fit.setToolTip("Bring the piece back to fill the view "
                       "(or double-click the view)")
        fit.setAutoDefault(False)
        fit.clicked.connect(self.view.fit)
        look.addWidget(fit)
        self._name_views()
        self.chk_labels = QCheckBox("Letters and numbers")
        self.chk_labels.setChecked(True)
        self.chk_labels.toggled.connect(self._labels)
        look.addWidget(self.chk_labels)
        self.chk_holes = QCheckBox("Hole check")
        self.chk_holes.setToolTip(
            "Where panels lie on each other when folded and both have "
            "stitch holes, mark each hole:\n"
            "green if the holes stacked there line up, red if any of them "
            "is out of line (a ring shows where the other hole is),\n"
            "amber if there is no hole there for it to meet.\n"
            "In pieces put together the holes are checked through the "
            "layers: a thin ring is where a hole of another piece comes "
            "through a piece that has none there.")
        shown = True
        if settings is not None:
            shown = settings.value("fold3dHoleCheck", True, type=bool)
        self.chk_holes.setChecked(shown)
        self.view.show_check(shown)
        self.chk_holes.toggled.connect(self._holes_shown)
        look.addWidget(self.chk_holes)
        look.addStretch(1)

        self.slider_apart = QSlider(Qt.Horizontal)
        self.slider_apart.setRange(0, 100)
        self.slider_apart.setToolTip("Lift the layers of the stack off each "
                                     "other, to see what lies on what")
        self.slider_apart.valueChanged.connect(self._apart_slid)
        self._apart_label = QLabel("Pull apart")
        apart = QHBoxLayout()
        apart.addWidget(self._apart_label)
        apart.addWidget(self.slider_apart, 1)
        apart.addStretch(2)

        left = QVBoxLayout()
        left.addWidget(self.view, 1)
        left.addLayout(run)
        left.addLayout(look)
        left.addLayout(apart)

        # -- beside it: what is shown, and the leather ---------------------
        self.cmb_piece = NoWheelComboBox()
        self.cmb_piece.currentIndexChanged.connect(self._piece_chosen)
        self.spin_t = self._length(
            0.2, 12.0, 2.0, "fold3dThickness",
            "How thick the leather is: of the pieces chosen on the Pieces "
            "tab, or else of the piece shown.\n"
            "A thickness typed here is written to those pieces in the "
            "drawing.", self._thickness_typed)
        # the thickness of a piece that has been given none: the last typed
        self._usual = self.spin_t.value()
        self.spin_r = self._length(
            0.0, 50.0, 0.0, "fold3dRadius",
            "The inside radius of every fold whose own radius is Auto.\n"
            "0 is a sharp crease: the leather still takes half its "
            "thickness to get round.", self._measured)
        self.cmb_base = NoWheelComboBox()
        self.cmb_base.setToolTip("The panel that stays where it is while "
                                 "the rest folds")
        self.cmb_base.currentIndexChanged.connect(self._base_chosen)
        self.cmb_leather = NoWheelComboBox()
        self.cmb_leather.setToolTip("The leather the pieces are shown in, "
                                    "but for a piece given one of its own. "
                                    "Only the preview changes: nothing is "
                                    "saved in the drawing")
        for name, (front, _back, _edge) in LEATHERS.items():
            chip = QPixmap(14, 14)
            chip.fill(QColor(*front))
            self.cmb_leather.addItem(QIcon(chip), name)
        leather = DEFAULT_LEATHER
        if settings is not None:
            leather = str(settings.value("fold3dLeather", DEFAULT_LEATHER))
        if leather not in LEATHERS:
            leather = DEFAULT_LEATHER
        self.cmb_leather.setCurrentText(leather)
        self.view.set_leather(leather)
        self.cmb_leather.currentTextChanged.connect(self._leather_chosen)
        form = QFormLayout()
        self._piece_label = QLabel("Show")
        form.addRow(self._piece_label, self.cmb_piece)
        form.addRow("Leather thickness", self.spin_t)
        form.addRow("Leather colour", self.cmb_leather)

        # -- the pieces put together ---------------------------------------
        self.pieces = PiecesPanel(lambda: self.doc, self.spin_t.value,
                                  settings)
        self.pieces.acting.connect(self._flush_commit)
        self.pieces.changed.connect(self._pieces_changed)
        self.pieces.chosen.connect(self._pieces_chosen)

        # -- the folds -----------------------------------------------------
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Fold", "Step", "Folds to", "Angle", "Radius", "Leather used"])
        for col, tip in enumerate((
                "Untick a fold to leave it flat",
                "When it folds: 1 first. Folds with the same step fold "
                "together",
                "Front: the front faces of the leather (the side the "
                "pattern is drawn on) come together. Back: the backs do",
                "How far it folds: 90° stands a panel up, 180° lays it "
                "flat on the next",
                "The inside radius of this bend. Auto uses the piece's "
                "bend radius, and more where the fold has to go round "
                "leather folded in before it",
                "The length of leather the bend takes up, and the radius "
                "it bends at")):
            self.table.horizontalHeaderItem(col).setToolTip(tip)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.table.itemChanged.connect(self._item_changed)

        self.btn_earlier = QPushButton("▲ Earlier")
        self.btn_earlier.setToolTip("Fold the chosen fold one step sooner")
        self.btn_later = QPushButton("▼ Later")
        self.btn_later.setToolTip("Fold the chosen fold one step later")
        self.btn_flip = QPushButton("Flip")
        self.btn_flip.setToolTip("Fold the chosen fold the other way")
        self.btn_earlier.clicked.connect(lambda: self._move(-1))
        self.btn_later.clicked.connect(lambda: self._move(1))
        self.btn_flip.clicked.connect(self._flip)
        order = QHBoxLayout()
        for b in (self.btn_earlier, self.btn_later, self.btn_flip):
            b.setAutoDefault(False)
            order.addWidget(b)
        order.addStretch(1)

        bend = QFormLayout()
        bend.addRow("Bend radius", self.spin_r)
        bend.addRow("Stays still", self.cmb_base)
        folding = QVBoxLayout()
        folding.setContentsMargins(6, 6, 6, 6)
        folding.addLayout(bend)
        folding.addWidget(self.table, 1)
        folding.addLayout(order)
        self.folds_tab = QWidget()
        self.folds_tab.setLayout(folding)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.pieces, "Pieces")
        self.tabs.addTab(self.folds_tab, "Folds")

        # -- under the tabs: the report, with room of its own ---------------
        self.readout = QLabel()
        self.readout.setWordWrap(True)
        self.readout.setTextFormat(Qt.RichText)
        self.readout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)
        page = QWidget()
        said = QVBoxLayout(page)
        said.setContentsMargins(6, 6, 6, 6)
        said.addWidget(self.readout)
        said.addStretch(1)
        self.report = QScrollArea()
        self.report.setWidgetResizable(True)
        self.report.setFrameShape(QFrame.StyledPanel)
        self.report.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.report.setWidget(page)
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.tabs)
        self.split.addWidget(self.report)
        self.split.setChildrenCollapsible(False)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([390, 230])

        side = QVBoxLayout()
        side.addLayout(form)
        side.addWidget(self.split, 1)
        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(500)
        side.setContentsMargins(0, 0, 0, 0)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(panel)

        for key, fn in ((Qt.Key_Space, self._toggle_play),
                        (Qt.Key_Left, lambda: self._step(-1)),
                        (Qt.Key_Right, lambda: self._step(1)),
                        (Qt.Key_Home, lambda: self._go(0.0)),
                        (Qt.Key_End, lambda: self._go(float("inf")))):
            sc = QShortcut(QKeySequence(key), self.view)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(fn)

        self._ticker = QTimer(self)
        self._ticker.setInterval(30)
        self._ticker.timeout.connect(self._tick)
        self._commit = QTimer(self)
        self._commit.setSingleShot(True)
        self._commit.setInterval(700)
        self._commit.timeout.connect(self._send_commit)
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(200)
        self._refresh.timeout.connect(self.reload)
        self.reload(first=True)

    # -- small builders -------------------------------------------------------

    def _tool(self, icon, tip, fn):
        b = QToolButton()
        b.setIcon(self.style().standardIcon(icon))
        b.setToolTip(tip)
        b.setAutoRaise(False)
        b.setMinimumWidth(34)
        b.clicked.connect(fn)
        return b

    def _length(self, lo, hi, default, key, tip, changed):
        w = MathSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(4)
        w.trim_zeros = True
        w.physical = True
        w.setSingleStep(0.5)
        U.register_spin(w)
        v = default
        if self._settings is not None:
            try:
                v = float(self._settings.value(key, default, type=float))
            except Exception:
                v = default
        w.setValue(min(hi, max(lo, v)))
        w.setToolTip(tip)
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda _v, k=key, s=w: changed(k, s))
        return w

    @property
    def doc(self):
        return self.canvas.doc if self.canvas is not None else self._doc

    # -- reading the drawing --------------------------------------------------

    def reload(self, first: bool = False, show: str = "",
               again: bool = False, front: bool = True):
        """Read the drawing again: what there is to show in it, and what
        the one shown is made of. ``show`` names another to show now (a
        stack's id, or a piece's); ``again`` makes its model and its
        words afresh though nothing they are made from has changed;
        without ``front`` the tab in front stays in front whatever comes
        to be shown."""
        doc = self.doc
        keep = show or (self.current.key if self.current is not None
                        else None)
        self.items = showables(doc) if doc is not None else []
        if self._main and all(f.key != keep for f in self.items):
            # the piece shown was put on a stack, or its stack is no
            # more: stay with the piece
            keep = next((f.key for f in self.items if f.stack is not None
                         and f.stack.piece(self._main) is not None),
                        self._main)
        if first and self.canvas is not None:
            # start on what's selected: its stack, or the piece if it folds
            try:
                picked = {it.model.shape_id for it in
                          self.canvas.selected_items()
                          if hasattr(it.model, "shape_id")}
            except Exception:
                picked = set()
            for f in self.items:
                if f.stack is not None:
                    mine = {p.key for p in f.stack.pieces} \
                        | {g.key for g in f.stack.main.folds}
                else:
                    mine = {f.key} | {s.shape_id for s in f.lines}
                if mine & picked:
                    keep = f.key
                    break
        self._busy = True
        self.cmb_piece.clear()
        for k, f in enumerate(self.items):
            if k and f.kind == "plain" and self.items[k - 1].kind != "plain":
                self.cmb_piece.insertSeparator(self.cmb_piece.count())
            self.cmb_piece.addItem(f.title, k)
        index = next((k for k, f in enumerate(self.items) if f.key == keep),
                     0)
        if self.items:
            self.cmb_piece.setCurrentIndex(self.cmb_piece.findData(index))
        self.cmb_piece.setVisible(len(self.items) > 1)
        self._piece_label.setVisible(len(self.items) > 1)
        self._busy = False
        item = self.items[index] if self.items else None
        was = self.current
        same = item is not None and was is not None and not first \
            and (item.key, item.kind) == (was.key, was.kind)
        if same and item.folds_signature() == self._fold_rows:
            # the same folds: their rows as they are, the lines read afresh
            item.fill()
            item.rings = was.rings             # (a net's, as they were made)
            self.current = item
            fresh = sm.stack_folds(item.stack) if item.stack is not None \
                else fold_lines(item.lines)
            for f, g in zip(self.folds, fresh):
                f.key = g.key
            if again or self._signature(item) != self._built:
                self.rebuild()
            else:                              # ... and the same model
                self._fill_pieces()
            return
        self._show(item, same=same, front=front)

    def _given(self, item) -> tuple:
        """The thickness and the leather the piece shown has been given
        in the drawing: 0 and "" for none of its own, and for anything
        but one piece (the pieces of a stack each have their own)."""
        if item is not None and item.kind in ("piece", "plain") \
                and self.doc is not None:
            shape = asm.shape_by_id(self.doc, item.key)
            if shape is not None:
                return float(shape.thickness or 0.0), shape.leather or ""
        return 0.0, ""

    def _signature(self, item) -> tuple:
        """Everything the model of ``item`` is built from: what is read
        of it from the drawing, and the thickness of a piece with none."""
        return (item.signature(), self._given(item), self._usual)

    def _show(self, item: Optional[Foldable], same: bool = False,
              front: bool = True):
        """Put something in the view, its folds in the table and its
        pieces in the list. With ``front``, something that was not shown
        before brings the tab it is for to the front."""
        def ident(f):               # a fold line's own id; a net's letter
            return getattr(f.key, "shape_id", None) or f.label

        old = {ident(f): f for f in self.folds} if same else {}
        chosen = self.selected_fold() if same else -1
        chosen = ident(self.folds[chosen]) if chosen >= 0 else None
        self.current = item
        self._net = None
        main = None
        if item is None:
            self.folds = []
        elif item.kind == "stack":
            self.folds = sm.stack_folds(item.stack)
            main = item.stack.main.key
        elif item.kind == "net":
            rings = [fs.model_outline(o) for o in item.outlines]
            regions, folds, lines = fs.net_regions(rings)
            item.rings = rings
            for f in folds:                    # a net's settings live here
                prev = old.get(f.label)
                if prev is not None:
                    f.angle, f.direction = prev.angle, prev.direction
                    f.radius, f.order = prev.radius, prev.order
                    f.active = prev.active
            self.folds = folds
            self._net = (regions, lines)
        else:
            item.fill()
            self.folds = fold_lines(item.lines)
            main = item.key
        if same:
            for f in self.folds:               # ticks aren't in the drawing
                prev = old.get(ident(f))
                if prev is not None:
                    f.active = prev.active
        if not same or main != self._main:
            self._base = None
        self._main = main
        self._fold_rows = item.folds_signature() if item is not None \
            else None
        self._number_steps()
        self._fill_table()
        for row, f in enumerate(self.folds):
            if chosen is not None and ident(f) == chosen:
                self.table.selectRow(row)
        if not same:
            # something else is shown: its layers together, and none of
            # its pieces chosen
            self.view.set_apart(0.0)
            self.pieces.choose(())
            self._name_views()
            if front:
                self.tabs.setCurrentWidget(self._tab_for(item))
        self.rebuild(refit=not same)

    def _tab_for(self, item):
        """The tab something is first shown on: Pieces for pieces put
        together and for a piece nothing folds, Folds for what folds."""
        if item is None or item.kind in ("piece", "net"):
            return self.folds_tab
        if item.kind == "stack" and self.folds \
                and len(item.stack.pieces) == 1:
            return self.folds_tab
        return self.pieces

    def _number_steps(self):
        """Give every fold a step: those with one keep their order (the
        same number still means together), the rest follow, one at a time."""
        steps = fs.fold_steps(self.folds, [True] * len(self.folds))
        for f, s in zip(self.folds, steps):
            f.order = s

    def _drawing_changed(self):
        if not self._writing and self.isVisible():
            self._refresh.start()

    def _units_changed(self, _unit=None):
        """Lengths are shown in another unit: say everything again."""
        try:
            self.reload(again=True)
        except RuntimeError:                   # the window is gone
            U.off_change(self._units_changed)

    # -- the table ------------------------------------------------------------

    def _fill_table(self):
        self._busy = True
        t = self.table
        t.setRowCount(len(self.folds))
        n = max(1, len(self.folds))
        for row, f in enumerate(self.folds):
            it = QTableWidgetItem(f.label + (f"  {f.name}" if f.name else ""))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                        | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if f.active else Qt.Unchecked)
            it.setToolTip(f"Fold {f.label}" + (f", {f.name}" if f.name else "")
                          + ". Untick to leave it flat")
            t.setItem(row, 0, it)

            step = NoWheelSpinBox()
            step.setRange(1, n)
            step.setValue(max(1, min(n, f.order)))
            step.setToolTip("When it folds: 1 is first. Give two folds the "
                            "same step to fold them together")
            step.valueChanged.connect(lambda v, r=row: self._set(r, "order",
                                                                 int(v)))
            t.setCellWidget(row, 1, self._own(step, row))

            way = NoWheelComboBox()
            way.addItem("Front", 1)
            way.addItem("Back", -1)
            way.setItemData(0, "The front faces of the leather come "
                               "together", Qt.ToolTipRole)
            way.setItemData(1, "The back faces of the leather come together",
                            Qt.ToolTipRole)
            way.setCurrentIndex(0 if f.direction >= 0 else 1)
            way.currentIndexChanged.connect(
                lambda _i, r=row, w=way: self._set(r, "direction",
                                                   int(w.currentData())))
            t.setCellWidget(row, 2, self._own(way, row))

            ang = MathSpinBox()
            ang.setRange(0.0, 180.0)
            ang.setDecimals(1)
            ang.trim_zeros = True
            ang.setSingleStep(15.0)
            ang.setSuffix("°")
            ang.setValue(f.angle)
            ang.setKeyboardTracking(False)
            ang.setToolTip("90° stands the panel up; 180° lays it flat on "
                           "the next")
            ang.valueChanged.connect(lambda v, r=row: self._set(r, "angle",
                                                                float(v)))
            t.setCellWidget(row, 3, self._own(ang, row))

            rad = _RadiusSpin()
            rad.setRange(-0.5, 50.0)
            rad.setDecimals(4)
            rad.trim_zeros = True
            rad.physical = True
            rad.setSingleStep(0.5)
            rad.setSpecialValueText("Auto")
            U.register_spin(rad)
            rad.setValue(f.radius if f.radius >= 0.0 else -0.5)
            rad.setKeyboardTracking(False)
            rad.setToolTip("The inside radius of this bend. Auto (step down "
                           "past 0, or type auto) uses the piece's bend "
                           "radius, and more where the fold goes round "
                           "leather folded in before it")
            rad.valueChanged.connect(lambda v, r=row: self._set(
                r, "radius", float(v) if v >= 0.0 else -1.0))
            t.setCellWidget(row, 4, self._own(rad, row))

            used = QTableWidgetItem("")
            used.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            t.setItem(row, 5, used)
        self._busy = False
        self._buttons()

    def _own(self, w, row):
        """A widget in a table row: using it chooses the row."""
        w.setProperty("fold_row", row)
        w.installEventFilter(self)
        return w

    def eventFilter(self, obj, event):   # noqa: N802
        if event.type() == QEvent.FocusIn:
            row = obj.property("fold_row")
            if row is not None and 0 <= int(row) < self.table.rowCount() \
                    and self.table.currentRow() != int(row):
                self.table.selectRow(int(row))
        return super().eventFilter(obj, event)

    def _set(self, row: int, what: str, value):
        """A value changed in the table."""
        if self._busy or not 0 <= row < len(self.folds):
            return
        f = self.folds[row]
        if getattr(f, what) == value:
            return
        setattr(f, what, value)
        if what == "direction":
            self._swatch(row)
        self.rebuild()
        self._store()

    def _item_changed(self, item):
        if self._busy or item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self.folds):
            on = item.checkState() == Qt.Checked
            if self.folds[row].active != on:
                self.folds[row].active = on
                self.rebuild()

    def _swatch(self, row: int):
        f = self.folds[row]
        live = bool(self.layout_ and row < len(self.layout_.steps)
                    and self.layout_.steps[row])
        it = self.table.item(row, 0)
        if it is not None:
            was = self._busy
            self._busy = True
            it.setData(Qt.DecorationRole, fold_colour(f, live))
            self._busy = was

    def selected_fold(self) -> int:
        row = self.table.currentRow()
        return row if 0 <= row < len(self.folds) \
            and self.table.selectionModel().hasSelection() else -1

    def _row_selected(self):
        self.view.set_selected(self.selected_fold())
        self._buttons()

    def _pick_fold(self, fold: int):
        """A fold line was clicked in the picture (or, with -1, something
        that is none): its row is chosen, on the tab it is on."""
        if 0 <= fold < len(self.folds):
            self.table.selectRow(fold)
            self.tabs.setCurrentWidget(self.folds_tab)
        else:
            self.table.clearSelection()

    def _pick_piece(self, piece: int, add: bool):
        """A piece of the stack was clicked in the picture: its row is
        chosen, on the tab it is on -- with Ctrl or Shift, one more. A
        click on nothing chooses none. (A piece shown on its own is all
        there is to click on: nothing is chosen by it.)"""
        if self.current is None or self.current.kind != "stack" \
                or self.parts is None:
            return
        if not 0 <= piece < len(self.parts.keys):
            if not add:
                self.pieces.choose(())
            return
        self.pieces.pick(self.parts.keys[piece], add)
        self.tabs.setCurrentWidget(self.pieces)

    def _buttons(self):
        on = self.selected_fold() >= 0
        for b in (self.btn_earlier, self.btn_later, self.btn_flip):
            b.setEnabled(on)

    def _move(self, by: int):
        """Fold the chosen fold a step sooner (-1) or later (1): past the
        folds of the next step, or out on its own if it shared a step."""
        row = self.selected_fold()
        if row < 0:
            return
        f = self.folds[row]
        alone = sum(1 for g in self.folds if g.order == f.order) == 1
        keys = [float(g.order) for g in self.folds]
        keys[row] += by * (1.5 if alone else 0.5)
        ranks = {v: k + 1 for k, v in enumerate(sorted(set(keys)))}
        for g, v in zip(self.folds, keys):
            g.order = ranks[v]
        self._sync_steps()
        self.rebuild()
        self._store()

    def _sync_steps(self):
        self._busy = True
        for row, f in enumerate(self.folds):
            w = self.table.cellWidget(row, 1)
            if w is not None:
                w.setValue(f.order)
        self._busy = False

    def _flip(self):
        row = self.selected_fold()
        if row < 0:
            return
        w = self.table.cellWidget(row, 2)
        w.setCurrentIndex(1 - w.currentIndex())

    # -- the pieces -----------------------------------------------------------

    def _fill_pieces(self):
        """The list on the Pieces tab, from the drawing as it stands and
        the model of what is shown."""
        doc, item = self.doc, self.current
        rows = sm.rows(doc, item, self.parts) if doc is not None else []
        if rows and rows[0].where == sm.SHOWN:
            rows[0].name = item.title          # as the Show box has it
        stack = item.key if item is not None and item.kind == "stack" else ""
        self.pieces.fill(rows, stack, self._usual, self.view.look.leather)
        self._pieces_chosen()

    def _pieces_chosen(self):
        """Other pieces are chosen in the list: those of the stack are
        lit in the picture, and the thickness shown is theirs."""
        rows = self.pieces.chosen_rows()
        stack = self.current is not None and self.current.kind == "stack"
        self.view.set_chosen([r.number for r in rows if stack
                              and r.where == sm.IN and r.number >= 0])
        if rows:
            t = rows[0].thickness
        elif stack:
            t = self.current.stack.main.thickness
        else:
            t = self._given(self.current)[0]
        self.spin_t.blockSignals(True)         # shown, not typed
        self.spin_t.setValue(t or self._usual)
        self.spin_t.blockSignals(False)

    def _pieces_changed(self, soon: bool, show: str):
        """The Pieces tab changed the drawing: an undo step, and the
        drawing read again -- with the stack ``show`` to the front, if a
        piece was put on one that is not shown."""
        self._wrote(soon)
        self.reload(show=show, front=False)

    def _thickness_typed(self, key, spin):
        """A thickness was typed. It goes to the pieces chosen on the
        Pieces tab or, with none chosen, to the piece shown (the main
        piece of a stack), and it is the thickness of every piece that
        has none of its own -- kept for next time."""
        t = float(spin.value())
        self._usual = t
        if self._settings is not None:
            self._settings.setValue(key, t)
        if self._busy:
            return
        doc = self.doc
        keys = self.pieces.chosen_keys() or ([self._main] if self._main
                                             else [])
        wrote = False
        for k in keys:
            shape = asm.shape_by_id(doc, k) if doc is not None else None
            if shape is not None and float(shape.thickness or 0.0) != t:
                shape.thickness = t
                wrote = True
        if wrote:
            self._wrote(soon=True, seen=True)
        self.reload()

    # -- the model ------------------------------------------------------------

    def rebuild(self, refit: bool = False):
        """Make the model again with the settings as they stand: the
        piece folded, or the stack laid flat."""
        item = self.current
        self.parts = None
        if item is None:
            self.layout_ = None
            self._built = None
            self.view.set_layout(None)
            self.readout.setText("")
            self.folds_tab.setEnabled(True)
            self._fill_pieces()
            self._sync_run()
            return
        thickness, leather = self._given(item)
        kw = dict(thickness=thickness or self._usual,
                  radius=self.spin_r.value(), base=self._base,
                  holes=item.holes)
        turned = False
        if item.kind == "stack":
            self.parts = sm.lay_out(item.stack, self._usual)
            layout = sm.build(self.parts, self.folds, kw["radius"],
                              self._base)
            turned = self.parts.turned
            leather = item.stack.main.leather
        elif item.kind == "net":
            regions, lines = self._net
            sharp = set()
            for o in item.rings:
                sharp |= fs.sharp_corners(o)
            layout = fs.Layout(regions, self.folds, lines, sharp=sharp, **kw)
        else:
            layout = fs.fold_piece(item.outline, self.folds,
                                   cutouts=item.cutouts, marks=item.marks,
                                   **kw)
        was_end = self.layout_ is not None and self.layout_.n_steps > 0 \
            and self.view.t >= self.layout_.n_steps - 1e-9
        self.layout_ = layout
        self._built = self._signature(item)
        check = layout.lineup()
        t = float(layout.n_steps) if was_end else None
        self.view.set_turned(turned)
        self.view.set_leathers({0: leather} if leather else None)
        self.view.set_layout(layout, t, check, refit=refit)
        # (the folds of a stack with pieces on it are not made yet)
        self.folds_tab.setEnabled(not getattr(layout, "sheets", None))
        self._fill_base()
        self._fill_used()
        self._fill_readout(check)
        self._fill_pieces()
        self._sync_run()

    def _fill_base(self):
        self._busy = True
        self.cmb_base.clear()
        for p in self.layout_.panels:
            self.cmb_base.addItem(f"Panel {p.number}")
        self.cmb_base.setCurrentIndex(self.layout_.base)
        self._busy = False

    def _fill_used(self):
        lay = self.layout_
        self._busy = True
        for row, f in enumerate(self.folds):
            it = self.table.item(row, 5)
            if it is None:
                continue
            if not lay.crosses[row]:
                it.setText("not on the piece")
            elif not lay.steps[row]:
                it.setText("flat")
            else:
                it.setText(_len(lay.used[row]))
            rad = self.table.cellWidget(row, 4)
            if rad is not None:                # what Auto comes to
                rad.setSpecialValueText(
                    f"Auto ({_len(lay.radius[row])})" if lay.steps[row]
                    else "Auto")
            self._swatch(row)
        self._busy = False

    def _fill_readout(self, check):
        lay = self.layout_
        if getattr(lay, "sheets", None):
            # a stack lying flat: what there is to tell of it, then its
            # stitch holes through the layers
            out = sm.report(self.current.stack, self.parts, lay)
            holes = _laid_report(lay, check)
            if holes:
                out.append(holes)
            self.readout.setText("<br><br>".join(out))
            return
        out = []
        used = [(f.label, lay.used[k]) for k, f in enumerate(self.folds)
                if lay.steps[k]]
        if used:
            total = sum(u for _l, u in used)
            # (a fold that has moved is drawn where it forms)
            where = "where a fold forms" if any(lay.shift) \
                else "a fold line"
            out.append("<b>Leather the bends take:</b> "
                       + ", ".join(f"{lbl} {_len(u)}" for lbl, u in used)
                       + (f" — {_len(total)} in all" if len(used) > 1
                          else "")
                       + f". Half comes from each side of {where}. "
                         "Edit → Fold allowance… adds it to a piece drawn "
                         "at its finished size.")
        for k, f in enumerate(self.folds):
            if lay.steps[k] and lay.wrap[k] > 1e-6:
                # so many layers, if the room is just their thickness (a
                # fold inside with a radius of its own holds them apart)
                n = lay.layers[k]
                tight = n and abs(lay.wrap[k] - 0.5 * n * lay.thickness) \
                    < 1e-6
                what = "what is" if not tight else "the layer" if n == 1 \
                    else f"{n} layers"
                out.append(
                    f"Fold {f.label} bends at {_len(lay.radius[k])} to "
                    f"go round {what} folded in before it.")
            if lay.steps[k] and lay.shift[k] > 0.0:
                # what is folded inside it was too long for it to form
                # on its line: where it formed, and how to put it back
                text = (f"Fold {f.label} folds {_len(lay.shift[k])} off "
                        f"its line, towards panel {lay.shift_toward[k]}")
                text += "." if lay.stuck[k] \
                    else ", to go round what is folded inside it."
                out.append(" ".join(filter(None, (text,
                                                  lay.remedy(k, _len)))))
        for w in lay.warned(_len):
            out.append(f"<span style='color:#c0392b'>⚠ {w}</span>")
        if check.findings:
            out.append(_hole_report(check))
        self.readout.setText("<br><br>".join(out))

    def _measured(self, key, spin):
        """The bend radius was changed: fold it again."""
        if self._settings is not None:
            self._settings.setValue(key, spin.value())
        if not self._busy:
            self.rebuild()

    def _leather_chosen(self, name):
        """Show the pieces in another leather (kept for next time)."""
        if name not in LEATHERS:
            return
        self.view.set_leather(name)
        if self._settings is not None:
            self._settings.setValue("fold3dLeather", name)
        self._fill_pieces()                    # the chips in the list

    def _base_chosen(self, index):
        if self._busy or index < 0:
            return
        self._base = index
        self.rebuild()

    def _piece_chosen(self, index):
        k = self.cmb_piece.itemData(index)
        if self._busy or k is None or not 0 <= k < len(self.items):
            return
        self._flush_commit()
        self._show(self.items[k])

    def _labels(self, on):
        self.view.labels = bool(on)
        self.view.update()

    def _holes_shown(self, on):
        """Mark the stitch holes by whether they line up, or not (kept
        for next time)."""
        self.view.show_check(on)
        if self._settings is not None:
            self._settings.setValue("fold3dHoleCheck", bool(on))

    def _on_bench(self) -> bool:
        """Whether what is shown lies on the bench: a stack."""
        return self.current is not None and self.current.kind == "stack"

    def _name_views(self):
        """The buttons for the set views: a piece is looked at from its
        front and its back, a stack on the bench from on top and from
        underneath."""
        bench = self._on_bench()
        for where, text, tip in (
                ("iso", "3D", "Three-quarter view"),
                ("front", "Top" if bench else "Front",
                 "Straight down on the stack as it lies on the bench"
                 if bench else "Straight on to the front of the leather "
                               "(as drawn)"),
                ("back", "Under" if bench else "Back",
                 "From underneath, as if the stack were turned over like "
                 "a page" if bench else "Straight on to the back"),
                ("side", "Side", "Edge on")):
            self.btn_views[where].setText(text)
            self.btn_views[where].setToolTip(tip)

    def _look(self, where: str):
        if self._on_bench():
            where = {"front": "top", "back": "under"}.get(where, where)
        self.view.look_from(where)

    # -- folding and unfolding ------------------------------------------------

    def _apart(self) -> float:
        """How far the layers of the stack shown are lifted apart (0 for
        a piece on its own)."""
        scene = self.view.scene
        return scene.apart if scene is not None else 0.0

    def _sync_run(self):
        """The sliders and the caption follow the view's place in the
        folding."""
        lay = self.layout_
        n = lay.n_steps if lay is not None else 0
        apart = self._apart()
        layers = bool(lay is not None and getattr(lay, "sheets", None))
        self._busy = True
        self.slider.setRange(0, max(1, n * _SLIDER))
        self.slider.setValue(int(round(self.view.t * _SLIDER)))
        self.slider_apart.setValue(int(round(100.0 * apart / _APART)))
        self._busy = False
        for b in (self.btn_first, self.btn_prev, self.btn_next,
                  self.btn_last, self.slider):
            b.setEnabled(n > 0)
        # (Play also brings the layers of a stack together)
        self.btn_play.setEnabled(n > 0 or apart > 0.0)
        for w in (self.slider_apart, self._apart_label):
            w.setEnabled(layers and self.view.t <= 1e-9)
        self.btn_play.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause if self._playing
            else QStyle.StandardPixmap.SP_MediaPlay))
        self.view.caption = self._caption()
        self.view.update()

    def _caption(self) -> str:
        lay = self.layout_
        if lay is None:
            return ("Nothing to fold yet. Draw a line across a piece, "
                    "right-click it and choose Mark as fold line.")
        if getattr(lay, "sheets", None):
            thick = next((n.a for n in lay.notes if n.kind == "thick"), 0.0)
            waits = any(n.kind == "flat" for n in lay.notes)
            return (f"Put together  ·  {len(lay.sheets) + 1} pieces, "
                    f"{_len(thick)} thick  ·  nothing folds"
                    + (" yet" if waits else ""))
        n, t = lay.n_steps, self.view.t
        if not n and not self.folds:
            return "Nothing folds: no fold line crosses this piece"
        if not n:
            return "Nothing folds: tick a fold and give it an angle"
        if t <= 1e-9:
            return f"Flat  ·  {n} step{'s' if n != 1 else ''} to fold"
        if t >= n - 1e-9:
            return "Folded"
        step = int(math.floor(t + 1e-9)) + 1
        doing = [f for k, f in enumerate(self.folds) if lay.steps[k] == step]
        what = ", ".join(
            f"{f.label} to the {'front' if f.direction >= 0 else 'back'} "
            f"{f.angle:g}°" for f in doing)
        return f"Step {step} of {n}:  fold {what}"

    def _fold_to(self, t: float):
        """Go to a moment of the folding: the layers of a stack come
        together before it folds."""
        lay = self.layout_
        if t > 1e-9 and lay is not None and lay.n_steps:
            self.view.set_apart(0.0)
        self.view.set_time(t)

    def _go(self, t: float):
        self._stop()
        self._fold_to(t)
        self._sync_run()

    def _step(self, by: int):
        t = self.view.t
        self._go(math.floor(t + 1e-9) + 1 if by > 0
                 else math.ceil(t - 1e-9) - 1)

    def _slid(self, value):
        if self._busy:
            return
        self._stop()
        self._fold_to(value / _SLIDER)
        self._sync_run()

    def _apart_slid(self, value):
        """The Pull apart slider: lift the layers that far."""
        if self._busy:
            return
        self._stop()
        self.view.set_apart(_APART * value / 100.0)
        self._sync_run()

    def _toggle_play(self):
        lay = self.layout_
        apart = self._apart()
        if lay is None or not (lay.n_steps or apart > 0.0):
            return
        if self._playing:
            self._stop()
            self._sync_run()
            return
        if lay.n_steps and self.view.t >= lay.n_steps - 1e-9:
            self.view.set_time(0.0)
        self._closing = apart
        self._playing = True
        self._last_tick = time.monotonic()
        self._ticker.start()
        self._sync_run()

    def _stop(self):
        if self._playing:
            self._playing = False
            self._ticker.stop()

    def _tick(self):
        lay = self.layout_
        if lay is None:
            self._stop()
            return
        now = time.monotonic()
        dt, self._last_tick = min(0.25, now - self._last_tick), now
        apart = self._apart()
        if apart > 0.0:
            # pulled apart: first the layers come together
            apart -= max(self._closing, apart) * dt / _CLOSE
            if apart <= 1e-6:
                apart = 0.0
                if not lay.n_steps:
                    self._stop()
            self.view.set_apart(apart)
            self._sync_run()
            return
        t = self.view.t + dt / 1.1             # a fold takes about a second
        if t >= lay.n_steps:
            t = float(lay.n_steps)
            self._stop()
        self.view.set_time(t)
        self._sync_run()

    # -- writing back to the drawing ------------------------------------------

    def _store(self):
        """Put the fold settings on the fold lines in the drawing, so they
        are saved with it; one undo step for a run of changes."""
        item = self.current
        if item is None or item.kind not in ("piece", "stack"):
            return
        doc = self.doc
        changed = []
        for f in self.folds:
            sh = f.key
            if sh is None:
                continue
            # (the line as the drawing has it now: an undo since it was
            # read has put another shape in its place)
            live = asm.shape_by_id(doc, sh.shape_id) if doc is not None \
                else None
            if live is not None:
                sh = f.key = live
            new = ("front" if f.direction >= 0 else "back", float(f.angle),
                   int(f.order), float(f.radius) if f.radius >= 0.0 else -1.0)
            if (sh.fold_dir, float(sh.fold_angle), int(sh.fold_order),
                    float(sh.fold_radius)) != new:
                sh.fold_dir, sh.fold_angle, sh.fold_order, sh.fold_radius = new
                changed.append(sh)
        if item.stack is not None:
            # ... and on the stack as it was read, which goes by them
            main = item.stack.main
            by_id = {f.key.shape_id: f for f in self.folds
                     if f.key is not None}
            for g in main.folds:
                f = by_id.get(g.key)
                if f is not None:
                    g.angle, g.radius, g.order = f.angle, f.radius, f.order
                    g.direction = -f.direction if main.mirrored \
                        else f.direction
        self._built, self._fold_rows = self._signature(item), \
            item.folds_signature()
        if not changed or self.canvas is None:
            return
        self._writing = True
        try:
            for it in list(getattr(self.canvas, "_live", ())):
                model = getattr(it, "model", None)
                if any(model is sh for sh in changed):
                    self.canvas.refresh_item(it)
        finally:
            self._writing = False
        self._unsaved = True
        self._commit.start()

    def _wrote(self, soon: bool, seen: bool = False):
        """A piece's thickness, leather or place was written to the
        drawing: it becomes an undo step, at once or -- ``soon`` -- when
        a run of changes is over. ``seen`` is for a change the Properties
        panel shows (a thickness): it reads the selection again."""
        if self.canvas is None:
            return
        self._unsaved = True
        if seen:
            self._writing = True
            try:
                self.canvas.selectionChangedSig.emit()
            finally:
                self._writing = False
        if soon:
            self._commit.start()
        else:
            self._flush_commit()

    def _flush_commit(self):
        self._commit.stop()
        self._send_commit()

    def _send_commit(self):
        if self._unsaved and self.canvas is not None:
            self._unsaved = False
            self._writing = True
            try:
                self.canvas.commitRequested.emit()
            finally:
                self._writing = False

    def event(self, event):
        # going back to the drawing (to undo, say): what was changed here
        # is an undo step by then, not still waiting to become one
        if event.type() == QEvent.WindowDeactivate:
            self._flush_commit()
        return super().event(event)

    def showEvent(self, event):   # noqa: N802
        # while it is open the window follows the drawing, and is told
        # when lengths are to be shown in another unit
        if not self._listening:
            self._listening = True
            U.on_change(self._units_changed)
            if self.canvas is not None:
                self.canvas.documentChangedSig.connect(self._drawing_changed)
        super().showEvent(event)

    def _leave(self):
        """The window is shut, by its close button or by Esc: what was
        changed in it last is an undo step, and it listens no more."""
        self._stop()
        self._flush_commit()
        if self._listening:
            self._listening = False
            U.off_change(self._units_changed)
            if self.canvas is not None:
                try:
                    self.canvas.documentChangedSig.disconnect(
                        self._drawing_changed)
                except (RuntimeError, TypeError):
                    pass

    def done(self, result):
        # (Esc comes here and not to closeEvent)
        self._leave()
        super().done(result)

    def closeEvent(self, event):   # noqa: N802
        self._leave()
        super().closeEvent(event)

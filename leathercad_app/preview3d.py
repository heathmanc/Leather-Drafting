"""The 3D assembly preview: fold a piece along its fold lines and look at it.

A piece folds along the lines marked as fold lines across it (right-click a
line > Mark as fold line). Every fold line has a row in the dialog's table:
the step it folds at, the way it folds, how far, and how tightly; the
leather's thickness and bend radius are set once for the piece. Panels
drawn edge to edge with no fold lines fold up along the edges they share
(a paper net).

The model itself comes from ``leathercad.foldsolid`` and is painted by
``foldview``; this module reads the drawing, builds the dialog, and writes
the fold settings back to the fold lines so they are saved with the file.
"""

from __future__ import annotations

import math
import time
from typing import List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSlider, QStyle, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout,
                               QWidget)

from leathercad import foldsolid as fs
from leathercad import polycut as pc

from . import units as U
from .foldview import FoldView
from .mathspin import MathSpinBox, NoWheelComboBox, NoWheelSpinBox

_SLIDER = 200                     # slider ticks to a folding step


# -- reading the drawing ------------------------------------------------------

class Foldable:
    """Something in the drawing that can be folded: a piece with fold lines
    across it (``kind`` "piece"), or panels drawn edge to edge ("net")."""

    def __init__(self, kind, key, title):
        self.kind = kind
        self.key = key
        self.title = title
        self.outline = []          # a piece: its outline
        self.cutouts = []
        self.lines = []            # the fold-line shapes across it
        self.outlines = []         # a net: its panels
        self.rings = []            # ... thinned for the model
        self.holes = []
        self.area = 0.0

    def signature(self):
        """Everything the model is built from: unchanged, nothing to redo."""
        return (self.kind, self.key, tuple(self.outline),
                tuple(tuple(c) for c in self.cutouts),
                tuple(tuple(o) for o in self.outlines), tuple(self.holes),
                tuple((_fold_ends(s), s.fold_dir, s.fold_angle, s.fold_order,
                       s.fold_radius, s.name) for s in self.lines))


def _ring(pts) -> list:
    return [(p.x, p.y) for p in pts]


def _fold_ends(shape):
    pts = shape.world_polyline()[0]
    if len(pts) < 2:
        return None
    a, b = (pts[0].x, pts[0].y), (pts[-1].x, pts[-1].y)
    return (a, b) if math.dist(a, b) > 1e-6 else None


def _size(w: float, h: float) -> str:
    if U.is_inches():
        return f"{U.to_display(w):.1f} × {U.to_display(h):.1f} in"
    return f"{w:.0f} × {h:.0f} mm"


def _len(mm: float) -> str:
    """A length for reading: 1 mm, 3.14 mm, 0.124 in."""
    text = U.fmt_num(mm)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text or '0'} {U.current()}"


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
    out.sort(key=lambda f: (round(0.5 * (f.a[0] + f.b[0]), 1),
                            -round(0.5 * (f.a[1] + f.b[1]), 1)))
    for k, f in enumerate(out):
        f.label = fs.letter(k)
    return out


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


def foldables(doc) -> List[Foldable]:
    """What can be folded in a drawing: each piece with fold lines across
    it, biggest first, then the net of panels that share edges (if any)."""
    from leathercad.pieces import classify, closes, on_cut_layer
    cut = on_cut_layer(doc)
    folds = [s for s in doc.shapes if getattr(s, "is_fold_line", False)
             and _fold_ends(s) is not None]
    rings, owners = [], []
    for sh in doc.shapes:
        if getattr(sh, "is_fold_line", False) or not cut(sh):
            continue
        pts, _corners, closed = sh.world_polyline()
        if closes(pts, closed):
            rings.append(pts)
            owners.append(sh)
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
        if not across:
            plain.append(i)
            continue
        x0, y0, x1, y1 = pc.bounds(ring)
        f = Foldable("piece", sh.shape_id,
                     sh.name or f"Piece {_size(x1 - x0, y1 - y0)}")
        f.outline = ring
        f.cutouts = [_ring(rings[j]) for j in range(len(rings))
                     if parent[j] == i and not is_piece[j]]
        f.lines = across
        f.holes = _holes_in(holes(), ring)
        f.area = areas[i]
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


# -- the dialog ---------------------------------------------------------------

def _finding(f) -> str:
    """One pair of panels lying together, in words."""
    both = f"Panels {f.a} and {f.b}"
    lone = f" {f.lone} more have no hole near them on the other panel." \
        if f.lone else ""
    if f.off:
        return (f"{both}: {f.off} of {f.holes} holes are off, by up to "
                f"{_len(f.worst)}.{lone}")
    if f.lone == f.holes:
        return f"{both}: none of the {f.holes} holes meet."
    return f"{both}: {f.holes - f.lone} holes meet.{lone}"


class _RadiusSpin(MathSpinBox):
    """A bend radius, or Auto (the value below 0)."""

    def valueFromText(self, text: str) -> float:   # noqa: N802
        if text.strip().lower().startswith("auto") or not text.strip():
            return self.minimum()
        return super().valueFromText(text)


class FoldPreviewDialog(QDialog):
    """Fold a piece in 3D. One row per fold line: when it folds, which
    way, how far, how tightly. Changes go straight to the fold lines in the
    drawing (and can be undone there)."""

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
        self.layout_: Optional[fs.Layout] = None
        self._net = None              # (regions, lines) of the net shown
        self._base: Optional[int] = None
        self._busy = False            # filling widgets: ignore their signals
        self._writing = False         # our own change to the drawing
        self._playing = False
        self._last_tick = 0.0
        self._unsaved = False         # a change not yet an undo step

        self.view = FoldView(self, dark)
        self.view.foldPicked.connect(self._pick_fold)

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
        for text, where, tip in (
                ("3D", "iso", "Three-quarter view"),
                ("Front", "front", "Straight on to the front of the leather "
                                   "(as drawn)"),
                ("Back", "back", "Straight on to the back"),
                ("Side", "side", "Edge on")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, w=where: self.view.look_from(w))
            look.addWidget(b)
        fit = QPushButton("Fit")
        fit.setToolTip("Bring the piece back to fill the view "
                       "(or double-click the view)")
        fit.setAutoDefault(False)
        fit.clicked.connect(self.view.fit)
        look.addWidget(fit)
        self.chk_labels = QCheckBox("Letters and numbers")
        self.chk_labels.setChecked(True)
        self.chk_labels.toggled.connect(self._labels)
        look.addWidget(self.chk_labels)
        look.addStretch(1)
        hint = QLabel("Drag to turn · right-drag to slide · wheel to zoom")
        hint.setEnabled(False)
        look.addWidget(hint)

        left = QVBoxLayout()
        left.addWidget(self.view, 1)
        left.addLayout(run)
        left.addLayout(look)

        # -- beside it: the piece, the leather, the folds ------------------
        self.cmb_piece = NoWheelComboBox()
        self.cmb_piece.currentIndexChanged.connect(self._piece_chosen)
        self.spin_t = self._length(0.2, 12.0, 2.0, "fold3dThickness",
                                   "How thick the leather is")
        self.spin_r = self._length(
            0.0, 50.0, 0.0, "fold3dRadius",
            "The inside radius of every fold whose own radius is Auto.\n"
            "0 is a sharp crease: the leather still takes half its "
            "thickness to get round.")
        self.cmb_base = NoWheelComboBox()
        self.cmb_base.setToolTip("The panel that stays where it is while "
                                 "the rest folds")
        self.cmb_base.currentIndexChanged.connect(self._base_chosen)
        form = QFormLayout()
        self._piece_label = QLabel("Piece")
        form.addRow(self._piece_label, self.cmb_piece)
        form.addRow("Leather thickness", self.spin_t)
        form.addRow("Bend radius", self.spin_r)
        form.addRow("Stays still", self.cmb_base)

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

        self.readout = QLabel()
        self.readout.setWordWrap(True)
        self.readout.setTextFormat(Qt.RichText)
        self.readout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.readout.setTextInteractionFlags(Qt.TextSelectableByMouse)

        side = QVBoxLayout()
        side.addLayout(form)
        side.addWidget(self.table, 1)
        side.addLayout(order)
        side.addWidget(self.readout)
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
        if canvas is not None:
            canvas.documentChangedSig.connect(self._drawing_changed)
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

    def _length(self, lo, hi, default, key, tip):
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
        w.valueChanged.connect(lambda _v, k=key, s=w: self._leather(k, s))
        return w

    @property
    def doc(self):
        return self.canvas.doc if self.canvas is not None else self._doc

    # -- reading the drawing --------------------------------------------------

    def reload(self, first: bool = False):
        """Read the drawing again: the pieces that fold, and the settings
        of the fold lines on the one shown."""
        doc = self.doc
        keep = self.current.key if self.current is not None else None
        was = self.current.signature() if self.current is not None else None
        self.items = foldables(doc) if doc is not None else []
        if first and self.canvas is not None:
            # start on the piece that's selected, if it folds
            try:
                picked = {it.model.shape_id for it in
                          self.canvas.selected_items()
                          if hasattr(it.model, "shape_id")}
            except Exception:
                picked = set()
            for f in self.items:
                if f.key in picked or any(s.shape_id in picked
                                          for s in f.lines):
                    keep = f.key
                    break
        self._busy = True
        self.cmb_piece.clear()
        for f in self.items:
            self.cmb_piece.addItem(f.title)
        index = next((k for k, f in enumerate(self.items) if f.key == keep),
                     0)
        if self.items:
            self.cmb_piece.setCurrentIndex(index)
        self.cmb_piece.setVisible(len(self.items) > 1)
        self._piece_label.setVisible(len(self.items) > 1)
        self._busy = False
        item = self.items[index] if self.items else None
        same = item is not None and item.key == keep and not first
        if same and item.signature() == was:
            self.current.lines = item.lines    # the same lines, read afresh
            for f, sh in zip(self.folds, fold_lines(item.lines)):
                f.key = sh.key
            return
        self._show(item, same=same)

    def _show(self, item: Optional[Foldable], same: bool = False):
        """Put a foldable in the view and its folds in the table."""
        old = {f.label: f for f in self.folds} if same else {}
        chosen = self.selected_fold() if same else -1
        chosen = self.folds[chosen].label if chosen >= 0 else None
        self.current = item
        self._net = None
        if item is None:
            self.folds = []
        elif item.kind == "piece":
            self.folds = fold_lines(item.lines)
        else:
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
        if same:
            for f in self.folds:               # ticks aren't in the drawing
                prev = old.get(f.label)
                if prev is not None:
                    f.active = prev.active
        else:
            self._base = None
        self._number_steps()
        self._fill_table()
        for row, f in enumerate(self.folds):
            if f.label == chosen:
                self.table.selectRow(row)
        self.rebuild(refit=not same)

    def _number_steps(self):
        """Give every fold a step: those with one keep their order (the
        same number still means together), the rest follow, one at a time."""
        steps = fs.fold_steps(self.folds, [True] * len(self.folds))
        for f, s in zip(self.folds, steps):
            f.order = s

    def _drawing_changed(self):
        if not self._writing and self.isVisible():
            self._refresh.start()

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
            it.setData(Qt.DecorationRole,
                       QColor(self.view.look.fold_colour(f, live)))
            self._busy = was

    def selected_fold(self) -> int:
        row = self.table.currentRow()
        return row if 0 <= row < len(self.folds) \
            and self.table.selectionModel().hasSelection() else -1

    def _row_selected(self):
        self.view.set_selected(self.selected_fold())
        self._buttons()

    def _pick_fold(self, fold: int):
        if 0 <= fold < len(self.folds):
            self.table.selectRow(fold)
        else:
            self.table.clearSelection()

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

    # -- the model ------------------------------------------------------------

    def rebuild(self, refit: bool = False):
        """Fold the piece again with the settings as they stand."""
        item = self.current
        if item is None:
            self.layout_ = None
            self.view.caption = ("Nothing to fold yet. Draw a line across a "
                                 "piece, right-click it and choose Mark as "
                                 "fold line.")
            self.view.set_layout(None)
            self.readout.setText("")
            self._sync_run()
            return
        kw = dict(thickness=self.spin_t.value(), radius=self.spin_r.value(),
                  base=self._base, holes=item.holes)
        if item.kind == "piece":
            layout = fs.fold_piece(item.outline, self.folds,
                                   cutouts=item.cutouts, **kw)
        else:
            regions, lines = self._net
            sharp = set()
            for o in item.rings:
                sharp |= fs.sharp_corners(o)
            layout = fs.Layout(regions, self.folds, lines, sharp=sharp, **kw)
        was_end = self.layout_ is not None and self.layout_.n_steps > 0 \
            and self.view.t >= self.layout_.n_steps - 1e-9
        self.layout_ = layout
        findings, bad = layout.lineup()
        t = float(layout.n_steps) if was_end else None
        self.view.set_layout(layout, t, bad, refit=refit)
        self._fill_base()
        self._fill_used()
        self._fill_readout(findings)
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

    def _fill_readout(self, findings):
        lay = self.layout_
        out = []
        used = [(f.label, lay.used[k]) for k, f in enumerate(self.folds)
                if lay.steps[k]]
        if used:
            total = sum(u for _l, u in used)
            out.append("<b>Leather the bends take:</b> "
                       + ", ".join(f"{lbl} {_len(u)}" for lbl, u in used)
                       + (f" — {_len(total)} in all" if len(used) > 1
                          else "")
                       + ". Half comes from each side of a fold line. "
                         "Edit → Fold allowance… adds it to a piece drawn "
                         "at its finished size.")
        for k, f in enumerate(self.folds):
            if lay.steps[k] and lay.wrap[k] > 1e-6:
                layers = lay.wrap[k] / (0.5 * lay.thickness)
                n = int(round(layers))
                out.append(
                    f"Fold {f.label} bends at {_len(lay.radius[k])} to "
                    f"go round {'the layer' if n == 1 else f'{n} layers'} "
                    f"folded in before it.")
        for w in lay.warnings:
            out.append(f"<span style='color:#c0392b'>⚠ {w}</span>")
        if findings:
            head = "<b>Stitch holes</b> (the ones that are off are red): " \
                if any(f.off for f in findings) else "<b>Stitch holes:</b> "
            out.append(head + " ".join(_finding(f) for f in findings))
        self.readout.setText("<br><br>".join(out))

    def _leather(self, key, spin):
        if self._settings is not None:
            self._settings.setValue(key, spin.value())
        if not self._busy:
            self.rebuild()

    def _base_chosen(self, index):
        if self._busy or index < 0:
            return
        self._base = index
        self.rebuild()

    def _piece_chosen(self, index):
        if self._busy or not 0 <= index < len(self.items):
            return
        self._flush_commit()
        self._show(self.items[index])

    def _labels(self, on):
        self.view.labels = bool(on)
        self.view.update()

    # -- folding and unfolding ------------------------------------------------

    def _sync_run(self):
        """The slider and the caption follow the view's place in the
        folding."""
        lay = self.layout_
        n = lay.n_steps if lay is not None else 0
        self._busy = True
        self.slider.setRange(0, max(1, n * _SLIDER))
        self.slider.setValue(int(round(self.view.t * _SLIDER)))
        self._busy = False
        for b in (self.btn_first, self.btn_prev, self.btn_play, self.btn_next,
                  self.btn_last, self.slider):
            b.setEnabled(n > 0)
        self.btn_play.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause if self._playing
            else QStyle.StandardPixmap.SP_MediaPlay))
        self.view.caption = self._caption()
        self.view.update()

    def _caption(self) -> str:
        lay = self.layout_
        if lay is None:
            return ""
        n, t = lay.n_steps, self.view.t
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

    def _go(self, t: float):
        self._stop()
        self.view.set_time(t)
        self._sync_run()

    def _step(self, by: int):
        t = self.view.t
        self._go(math.floor(t + 1e-9) + 1 if by > 0
                 else math.ceil(t - 1e-9) - 1)

    def _slid(self, value):
        if self._busy:
            return
        self._stop()
        self.view.set_time(value / _SLIDER)
        self._sync_run()

    def _toggle_play(self):
        lay = self.layout_
        if lay is None or not lay.n_steps:
            return
        if self._playing:
            self._stop()
            self._sync_run()
            return
        if self.view.t >= lay.n_steps - 1e-9:
            self.view.set_time(0.0)
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
        if self.current is None or self.current.kind != "piece":
            return
        changed = []
        for f in self.folds:
            sh = f.key
            if sh is None:
                continue
            new = ("front" if f.direction >= 0 else "back", float(f.angle),
                   int(f.order), float(f.radius) if f.radius >= 0.0 else -1.0)
            if (sh.fold_dir, float(sh.fold_angle), int(sh.fold_order),
                    float(sh.fold_radius)) != new:
                sh.fold_dir, sh.fold_angle, sh.fold_order, sh.fold_radius = new
                changed.append(sh)
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

    def closeEvent(self, event):   # noqa: N802
        self._stop()
        self._flush_commit()
        if self.canvas is not None:
            try:
                self.canvas.documentChangedSig.disconnect(
                    self._drawing_changed)
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(event)

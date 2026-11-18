"""The Pieces tab of the 3D assembly preview: the pieces put together.

A list of the pieces -- those of the stack shown, the top of the pile
first, then the pieces not put on yet -- and under it what can be done to
the pieces chosen in it: tick one to put it on the stack, move it up or
down the pile, turn it over, line it up with the main piece, set it a
distance off, turn it, give it a leather of its own.

Every change is one of ``leathercad.assembly``'s, made to the drawing as
it stands at that moment: the panel keeps the ids of pieces, never the
shapes, since undo in the main window replaces every one of them. It
commits nothing itself. It says ``acting`` before a change that is one
undo step on its own (a button, a tick) and ``changed`` after any change;
the window it is in makes the undo steps and reads the drawing again.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import (QEvent, QItemSelectionModel, QRect, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QIcon, QPalette,
                           QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout,
                               QHeaderView, QLabel, QSizePolicy, QStyle,
                               QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)

from leathercad import assembly as asm
from leathercad import foldsolid as fs
from leathercad import polycut as pc

from . import stackmodel as sm
from . import units as U
from .foldview import DEFAULT_LEATHER, LEATHERS
from .mathspin import MathSpinBox, NoWheelComboBox

USUAL = "Usual"                   # a piece in the leather chosen for them all
NOT_YET = "Not in the stack yet"
INSIDE = "Drawn inside a piece"


def drawn_over(doc, under: str, piece: str) -> bool:
    """Whether a piece is drawn overlapping another on the drawing, by
    more than ``assembly.AS_DRAWN``: put on a stack started on ``under``,
    it comes in where it is drawn."""
    rings = []
    for key in (under, piece):
        shape = asm.shape_by_id(doc, key)
        if shape is None:
            return False
        pts = [(p.x, p.y) for p in shape.world_polyline()[0]]
        if len(pts) < 3:
            return False
        rings.append(fs.model_outline(pts))
    a, b = rings
    shared = 0.0
    for part, _flags in pc.convex_parts(b, [pc.OUTLINE] * len(b)):
        cut = pc.clip_to_convex(a, part)
        if cut:
            shared += max(0.0, pc.area(cut))
    return shared > asm.AS_DRAWN


class PiecesPanel(QWidget):
    """The list of pieces and what is done to them.

    ``doc`` gives the drawing as it stands (it is called before every
    change) and ``thickness`` the leather thickness showing in the
    window, which a piece with none of its own is given as it is put on.
    ``fill`` shows the rows ``stackmodel.rows`` makes."""

    acting = Signal()               # a change of one undo step is coming
    # the drawing was changed: whether it is one of a run of changes (a
    # value typed or stepped) and the stack to show now ("": as it is)
    changed = Signal(bool, str)
    chosen = Signal()               # other rows are chosen

    def __init__(self, doc: Callable, thickness: Callable, settings=None,
                 parent=None):
        super().__init__(parent)
        self._doc = doc
        self._thickness = thickness
        self._settings = settings
        self._rows: List[Optional[sm.Row]] = []    # None: a heading
        self._shown = None            # what the table was last filled with
        self._stack = ""              # the stack shown ("": none)
        self._usual = 2.0             # the thickness of a piece with none
        self._leather = DEFAULT_LEATHER
        self._drawable = set()        # the pieces "As drawn" applies to
        self._fixed = False           # its main piece is cut open just now
        self._busy = False            # filling widgets: ignore their signals

        t = self.pieces = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["Piece", "Thickness", "Lies"])
        for col, tip in enumerate((
                "Tick a piece to put it on the stack; untick it to take it "
                "off. The top of the pile is first, and the main piece is "
                "in bold",
                "How thick its leather is. In brackets: it has none of its "
                "own yet, and is shown at the leather thickness above",
                "Where it lies: what it rests on, which way up, and the "
                "point of the main piece it is held by")):
            t.horizontalHeaderItem(col).setToolTip(tip)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setMinimumHeight(96)
        # rows no taller than they need be (a wallet has ten pieces), but
        # for one that has more to say of where it lies than fits a line
        t.setWordWrap(True)
        t.verticalHeader().setMinimumSectionSize(
            t.fontMetrics().height() + 8)
        t.verticalHeader().setDefaultSectionSize(
            t.fontMetrics().height() + 8)
        head = t.horizontalHeader()
        head.setHighlightSections(False)
        head.setSectionResizeMode(QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        # (the rows are fitted again once a change of width has settled:
        # not in the middle of it, when the columns are half way there)
        self._refit = QTimer(self)
        self._refit.setSingleShot(True)
        self._refit.setInterval(0)
        self._refit.timeout.connect(self._fit_rows)
        head.sectionResized.connect(lambda *_a: self._refit.start())
        t.viewport().installEventFilter(self)
        t.itemChanged.connect(self._item_changed)
        t.itemSelectionChanged.connect(self._selected)

        # -- the pile, and which way up -----------------------------------
        self.btn_higher = self._button(
            "▲ Higher", "Move the chosen pieces one place up the pile",
            lambda: self._do(lambda doc: asm.higher(doc, self._ins())))
        self.btn_lower = self._button(
            "▼ Lower", "Move the chosen pieces one place down the pile",
            lambda: self._do(lambda doc: asm.lower(doc, self._ins())))
        self.btn_over = self._button(
            "Turn over", "Turn the chosen pieces over like a page: grain up "
                         "becomes grain down",
            lambda: self._do(lambda doc: asm.turn_over(doc, self._ins())))
        self.btn_under = self._button(
            "Underneath", "Put the chosen pieces under the main piece, next "
                          "to it -- or, if they lie under it, on it",
            self._to_side)
        self.btn_mirror = self._button(
            "Mirror", "Show the chosen pieces the other way round without "
                      "turning them over: for a piece drawn as it is seen "
                      "from its flesh side",
            lambda: self._do(lambda doc: asm.mirror(doc, self._ins())))
        self.btn_main = self._button(
            "Main piece", "Make the chosen piece the main piece: the others "
                          "are placed against it, and the stack folds "
                          "along its fold lines. Nothing moves",
            self._make_main)
        pile = QHBoxLayout()
        for b in (self.btn_higher, self.btn_lower, self.btn_over,
                  self.btn_under, self.btn_mirror, self.btn_main):
            pile.addWidget(b)

        # -- lining up ----------------------------------------------------
        line = QHBoxLayout()
        line.addWidget(QLabel("Line up"))
        for side, text, tip in (
                ("left", "Left", "left edges level"),
                ("centre", "Centre", "centred from side to side"),
                ("right", "Right", "right edges level"),
                ("bottom", "Bottom", "bottom edges level"),
                ("middle", "Middle", "centred from top to bottom"),
                ("top", "Top", "top edges level")):
            b = self._button(
                text, f"Line the chosen pieces up with the main piece (or "
                      f"with the piece chosen in “Line up with”): {tip}. "
                      "They stay lined up when either piece changes size",
                lambda _=False, s=side: self._line_up(s))
            setattr(self, f"btn_{side}", b)
            line.addWidget(b)
        self.btn_drawn = self._button(
            "As drawn", "Lay the chosen pieces where they are drawn on the "
                        "main piece. With none chosen: put on every piece "
                        "that is drawn on it. (Only while the main piece "
                        "lies as it is drawn, not turned over or mirrored.)",
            self._as_drawn)
        line.addSpacing(8)
        line.addWidget(self.btn_drawn)

        self.cmb_with = NoWheelComboBox()
        self.cmb_with.setToolTip("The piece the Line up buttons line the "
                                 "chosen pieces up with")
        self.cmb_with.currentIndexChanged.connect(lambda _i: self._sync())
        self.chk_along = QCheckBox("Take along what lies on it")
        self.chk_along.setToolTip("When a piece is moved, the pieces lying "
                                  "wholly on it move with it")
        along = True
        if settings is not None:
            along = settings.value("fold3dAlong", True, type=bool)
        self.chk_along.setChecked(along)
        self.chk_along.toggled.connect(self._along_set)
        with_ = QHBoxLayout()
        with_.addWidget(QLabel("Line up with"))
        with_.addWidget(self.cmb_with, 1)
        with_.addWidget(self.chk_along)

        # -- a distance off, and a turn -----------------------------------
        self.spin_x = self._length(
            "How far the chosen piece lies across from the main piece: "
            "between the edge (or middle) it is held by and the same edge "
            "of the main piece. 0 is level",
            lambda v: self._offset(x=float(v)))
        self.spin_y = self._length(
            "How far the chosen piece lies up from the main piece: between "
            "the edge (or middle) it is held by and the same edge of the "
            "main piece. 0 is level",
            lambda v: self._offset(y=float(v)))
        self.lbl_from = QLabel("")
        self.lbl_from.setToolTip("What Across and Up are measured from: "
                                 "that edge, or middle, of the main piece "
                                 "to the same one of the chosen piece")
        off = QHBoxLayout()
        off.addWidget(QLabel("Across"))
        off.addWidget(self.spin_x)
        off.addWidget(QLabel("Up"))
        off.addWidget(self.spin_y)
        off.addWidget(self.lbl_from, 1)

        self.spin_turn = MathSpinBox()
        self.spin_turn.setRange(-360.0, 360.0)
        self.spin_turn.setDecimals(1)
        self.spin_turn.trim_zeros = True
        self.spin_turn.setSingleStep(15.0)
        self.spin_turn.setSuffix("°")
        self.spin_turn.setKeyboardTracking(False)
        self.spin_turn.setToolTip("How far the chosen pieces lie turned, "
                                  "counter-clockwise from the way they are "
                                  "cut")
        self.spin_turn.valueChanged.connect(self._turn_to)
        self.btn_ccw = self._button(
            "⟲ 90°", "Turn the chosen pieces a quarter turn "
                     "counter-clockwise",
            lambda: self._do(lambda doc: asm.turn(doc, self._ins(), 90.0)))
        self.btn_cw = self._button(
            "⟳ 90°", "Turn the chosen pieces a quarter turn clockwise",
            lambda: self._do(lambda doc: asm.turn(doc, self._ins(), -90.0)))
        self.cmb_colour = NoWheelComboBox()
        self.cmb_colour.setToolTip(
            "The leather the chosen pieces are shown in. Usual is the "
            "Leather colour above. A piece's own leather is saved in the "
            "drawing")
        self.cmb_colour.addItem(USUAL)
        for name, (front, _back, _edge) in LEATHERS.items():
            self.cmb_colour.addItem(self._chip(front), name)
        self.cmb_colour.currentIndexChanged.connect(self._leather_set)
        turn = QHBoxLayout()
        turn.addWidget(QLabel("Turned"))
        turn.addWidget(self.spin_turn)
        turn.addWidget(self.btn_ccw)
        turn.addWidget(self.btn_cw)
        turn.addSpacing(8)
        turn.addWidget(QLabel("This piece's leather"))
        turn.addWidget(self.cmb_colour, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        lay.addWidget(t, 1)
        for row in (pile, line, with_, off, turn):
            row.setSpacing(4)
            lay.addLayout(row)
        self._sync()

    # -- small builders -------------------------------------------------------

    def _button(self, text, tip, fn):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(False)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.clicked.connect(fn)
        return b

    def _length(self, tip, fn):
        w = MathSpinBox()
        w.setRange(-2000.0, 2000.0)
        w.setDecimals(4)
        w.trim_zeros = True
        w.physical = True
        w.setSingleStep(U.nudge_step())
        U.register_spin(w)
        w.setKeyboardTracking(False)
        w.setToolTip(tip)
        w.valueChanged.connect(fn)
        return w

    @staticmethod
    def _chip(rgb) -> QIcon:
        chip = QPixmap(14, 14)
        chip.fill(QColor(*rgb))
        return QIcon(chip)

    # -- the list -------------------------------------------------------------

    def fill(self, rows: List[sm.Row], stack: str = "", usual: float = 2.0,
             leather: str = DEFAULT_LEATHER):
        """Show these pieces (see ``stackmodel.rows``). ``stack`` is the
        stack shown, "" for none; ``usual`` the thickness a piece with
        none of its own is shown at and ``leather`` the leather of a
        piece with none of its own. The rows chosen stay chosen."""
        self._stack, self._usual = stack, float(usual)
        self._leather = leather if leather in LEATHERS else DEFAULT_LEATHER
        doc = self._doc()
        self._fixed = bool(stack) and doc is not None \
            and asm.main_cut_open(doc, stack)
        self._find_drawable(rows)
        shown = (tuple(r.signature() for r in rows), stack, self._usual,
                 self._leather, U.current(), self._fixed)
        if shown == self._shown:
            self._sync()
            return
        keep = self.chosen_keys()
        self._shown = shown
        self._busy = True
        t = self.pieces
        at = t.verticalScrollBar().value()
        t.clearSpans()
        t.setRowCount(0)
        self._rows = []
        part = None
        for r in rows:
            if r.where in (sm.OUT, sm.INSIDE) and r.where != part:
                part = r.where
                self._heading(NOT_YET if part == sm.OUT else INSIDE)
            self._add(r)
        # the pieces a piece can be lined up with: the main piece first
        was = self.cmb_with.currentData()
        self.cmb_with.clear()
        ins = [r for r in rows if r.where == sm.IN]
        for r in sorted(ins, key=lambda r: not r.main):
            self.cmb_with.addItem(
                f"{r.name} (the main piece)" if r.main else r.name,
                "" if r.main else r.key)
        self.cmb_with.setCurrentIndex(max(0, self.cmb_with.findData(was)))
        for w in (self.spin_x, self.spin_y):
            w.setSingleStep(U.nudge_step())
        self._fit_rows()
        self._refit.start()
        t.verticalScrollBar().setValue(at)     # the list stays where it was
        self._busy = False
        self.choose(keep)

    def eventFilter(self, obj, event):   # noqa: N802
        if event.type() == QEvent.Resize and obj is self.pieces.viewport():
            self._refit.start()
        return super().eventFilter(obj, event)

    def _fit_rows(self):
        """Every row one line high, but for one whose "Lies" does not fit
        its column: that one is as high as its words need, wrapped."""
        t = self.pieces
        margin = t.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None,
                                       t) + 1
        # (what the cell has for its words, less a pixel to be sure of it)
        room = max(40, t.columnWidth(2) - 2 * margin - 2)
        least = t.verticalHeader().minimumSectionSize()
        for row in range(t.rowCount()):
            item = t.item(row, 2)
            high = least
            if item is not None and item.text():
                font = QFont(t.font())
                font.setBold(item.font().bold())
                need = QFontMetrics(font).boundingRect(
                    QRect(0, 0, room, 10000), Qt.TextWordWrap,
                    item.text()).height()
                high = max(least, need + 8)
            if t.rowHeight(row) != high:
                t.setRowHeight(row, high)

    def _heading(self, text: str):
        """A rule across the list with a heading under it."""
        t = self.pieces
        row = t.rowCount()
        t.insertRow(row)
        it = QTableWidgetItem("")
        it.setFlags(Qt.NoItemFlags)
        t.setItem(row, 0, it)
        t.setSpan(row, 0, 1, 3)
        label = QLabel(text)
        label.setStyleSheet("border-top: 1px solid palette(mid); "
                            "font-style: italic; padding-left: 4px;")
        t.setCellWidget(row, 0, label)
        self._rows.append(None)

    def _add(self, r: sm.Row):
        t = self.pieces
        row = t.rowCount()
        t.insertRow(row)
        it = QTableWidgetItem(r.name)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if r.where == sm.IN else Qt.Unchecked)
        it.setIcon(self._chip(LEATHERS[r.leather if r.leather in LEATHERS
                                       else self._leather][0]))
        it.setToolTip(sm.plain(
            f"{r.name}: untick to take it off the stack" if r.where == sm.IN
            else f"{r.name}: tick to put it on the stack"))
        if self._fixed and r.where != sm.IN:
            # nothing is put on while the main piece is cut open
            it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setToolTip(sm.plain(
                f"{r.name}: it can be put on once the main piece is a "
                "closed outline on a cut layer again"))
        thick = QTableWidgetItem(
            sm.length(r.thickness) if r.thickness > 0.0
            else f"({sm.length(self._usual)})")
        if not r.thickness > 0.0:
            thick.setForeground(self.palette().color(
                QPalette.ColorRole.PlaceholderText))
            thick.setToolTip("No thickness of its own yet: shown at the "
                             "leather thickness above. It is given that "
                             "when it is put on the stack")
        lies = QTableWidgetItem(r.lies)
        lies.setToolTip(sm.plain(r.tip))
        if r.main:
            bold = QFont(it.font())
            bold.setBold(True)
            for cell in (it, thick, lies):
                cell.setFont(bold)
        for cell in (thick, lies):
            cell.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        t.setItem(row, 0, it)
        t.setItem(row, 1, thick)
        t.setItem(row, 2, lies)
        self._rows.append(r)

    def _ticks(self):
        """Every row ticked or not as its piece is in the stack or not."""
        was = self._busy
        self._busy = True
        for row, r in enumerate(self._rows):
            if r is not None:
                self.pieces.item(row, 0).setCheckState(
                    Qt.Checked if r.where == sm.IN else Qt.Unchecked)
        self._busy = was

    def rows(self) -> List[sm.Row]:
        """The pieces in the list, from its top (the headings left out)."""
        return [r for r in self._rows if r is not None]

    def row_of(self, key: str) -> int:
        """The row of the table the piece with that shape id is in, or -1."""
        for k, r in enumerate(self._rows):
            if r is not None and r.key == key:
                return k
        return -1

    def chosen_rows(self) -> List[sm.Row]:
        """The pieces chosen in the list, from its top."""
        picked = sorted({i.row() for i in
                         self.pieces.selectionModel().selectedRows()})
        return [self._rows[k] for k in picked
                if k < len(self._rows) and self._rows[k] is not None]

    def chosen_keys(self) -> List[str]:
        return [r.key for r in self.chosen_rows()]

    def choose(self, keys):
        """Choose the rows of the pieces with these shape ids, and no
        others."""
        keys = set(keys)
        t = self.pieces
        was = self._busy
        self._busy = True
        t.clearSelection()
        sel = t.selectionModel()
        for row, r in enumerate(self._rows):
            if r is not None and r.key in keys:
                sel.select(t.model().index(row, 0),
                           QItemSelectionModel.Select
                           | QItemSelectionModel.Rows)
        self._busy = was
        self._sync()
        self.chosen.emit()

    def pick(self, key: str, add: bool = False):
        """Choose the piece with that shape id, as a click on it in the
        picture does: alone, or with ``add`` one more (or one fewer, if
        it was chosen)."""
        keys = [key]
        if add:
            keys = self.chosen_keys()
            if key in keys:
                keys.remove(key)
            else:
                keys.append(key)
        self.choose(keys)
        row = self.row_of(key)
        if row >= 0:
            self.pieces.scrollToItem(self.pieces.item(row, 0))

    def _selected(self):
        if self._busy:
            return
        self._sync()
        self.chosen.emit()

    def _item_changed(self, item):
        if self._busy or item.column() != 0 \
                or not 0 <= item.row() < len(self._rows):
            return
        r = self._rows[item.row()]
        on = item.checkState() == Qt.Checked
        if r is not None and on != (r.where == sm.IN):
            self._tick(r, on)

    # -- what can be done to the pieces chosen --------------------------------

    def _ins(self) -> List[str]:
        """The chosen pieces that are in the stack."""
        return [r.key for r in self.chosen_rows() if r.where == sm.IN]

    def _movers(self) -> List[sm.Row]:
        """The chosen pieces that are placed against the main piece."""
        return [r for r in self.chosen_rows()
                if r.where == sm.IN and not r.main]

    def _sync(self):
        """The buttons and boxes follow the pieces chosen."""
        rows = self.chosen_rows()
        ins = [r for r in rows if r.where == sm.IN]
        movers = [r for r in ins if not r.main]
        was = self._busy
        self._busy = True
        for b in (self.btn_higher, self.btn_lower, self.btn_over,
                  self.btn_mirror, self.spin_turn, self.btn_ccw, self.btn_cw):
            b.setEnabled(bool(ins))
        self.btn_under.setEnabled(bool(movers))
        self.btn_under.setText(
            "On top" if movers and not any(r.above for r in movers)
            else "Underneath")
        self.btn_main.setEnabled(len(ins) == 1 and not ins[0].main)
        against = self.cmb_with.currentData() or ""
        lined = [r for r in (ins if against else movers) if r.key != against]
        for side in asm.SIDES:
            getattr(self, f"btn_{side}").setEnabled(bool(lined))
        self.cmb_with.setEnabled(self.cmb_with.count() > 1)
        first = movers[0] if movers else None
        for w in (self.spin_x, self.spin_y):
            w.setEnabled(first is not None)
        self.spin_x.setValue(first.placed.x if first else 0.0)
        self.spin_y.setValue(first.placed.y if first else 0.0)
        self.lbl_from.setText(sm.measured_from(first.placed) if first
                              else "")
        self.spin_turn.setValue(ins[0].placed.turn if ins else 0.0)
        self.cmb_colour.setEnabled(bool(rows))
        self.cmb_colour.setCurrentIndex(max(0, self.cmb_colour.findText(
            rows[0].leather)) if rows and rows[0].leather else 0)
        self.btn_drawn.setEnabled(bool(self._to_draw()))
        if self._fixed:
            # its main piece is cut open: the stack is looked at, not
            # rearranged, until that outline is closed again
            for w in (self.btn_higher, self.btn_lower, self.btn_over,
                      self.btn_under, self.btn_mirror, self.btn_main,
                      self.btn_drawn, self.spin_x, self.spin_y,
                      self.spin_turn, self.btn_ccw, self.btn_cw):
                w.setEnabled(False)
            for side in asm.SIDES:
                getattr(self, f"btn_{side}").setEnabled(False)
        self._busy = was

    def _do(self, change, soon: bool = False) -> bool:
        """Make one change to the drawing as it stands. ``change`` is
        given the drawing and says whether it changed anything -- with
        the id of a stack, if that stack is to be shown now. ``soon``
        marks a value typed or stepped: one of a run that is one undo
        step, where anything else is a step on its own."""
        doc = self._doc()
        if doc is None or self._busy:
            return False
        if not soon:
            self.acting.emit()
        done = change(doc)
        if not done:
            return False
        self.changed.emit(soon, done if isinstance(done, str) else "")
        return True

    def _moving(self, doc, keys, change) -> bool:
        """Make a change that moves pieces on the bench, and take along
        what lies wholly on each of them, if that is ticked
        (``assembly.take_along``)."""
        if self.chk_along.isChecked():
            return asm.take_along(doc, keys, change)
        return change()

    def _to_side(self):
        """The chosen pieces go under the main piece, next to it -- or,
        if they all lie under it, on it, next to it."""
        movers = self._movers()
        keys = [r.key for r in movers]
        if any(r.above for r in movers):
            self._do(lambda doc: asm.put_under(doc, keys))
        else:
            self._do(lambda doc: asm.put_over(doc, keys))

    def _make_main(self):
        ins = self._ins()
        if len(ins) == 1:
            self._do(lambda doc: asm.make_main(doc, ins[0]))

    def _line_up(self, side: str):
        against = self.cmb_with.currentData() or None
        keys = [r.key for r in self.chosen_rows()
                if r.where == sm.IN and r.key != against
                and (against or not r.main)]
        if keys:
            self._do(lambda doc: self._moving(
                doc, keys, lambda: asm.line_up(doc, keys, side, against)))

    def _offset(self, x=None, y=None):
        keys = [r.key for r in self._movers()]
        if keys and not self._busy:
            self._do(lambda doc: self._moving(
                doc, keys, lambda: asm.set_offset(doc, keys, x, y)),
                soon=True)

    def _turn_to(self, degrees):
        keys = self._ins()
        if keys and not self._busy:
            self._do(lambda doc: asm.set_turn(doc, keys, float(degrees)),
                     soon=True)

    def _leather_set(self, index: int):
        """Give the chosen pieces a leather of their own, or the usual
        one again."""
        if self._busy:
            return
        name = "" if index <= 0 else self.cmb_colour.itemText(index)
        keys = self.chosen_keys()

        def change(doc):
            done = False
            for key in keys:
                shape = asm.shape_by_id(doc, key)
                if shape is not None and (shape.leather or "") != name:
                    shape.leather = name
                    done = True
            return done
        self._do(change)

    def _along_set(self, on: bool):
        if self._settings is not None:
            self._settings.setValue("fold3dAlong", bool(on))

    # -- putting on, taking off -----------------------------------------------

    def _shown_piece(self) -> str:
        """The piece shown when it is in no stack ("" if there is none)."""
        return next((r.key for r in self._rows
                     if r is not None and r.where == sm.SHOWN), "")

    def _tick(self, r: sm.Row, on: bool):
        """A piece was ticked: put it on the stack shown, on the chosen
        piece if one is chosen (else on the top of the pile). With no
        stack shown it starts one, on the piece shown if there is one.
        Unticked, it is taken off."""
        t = float(self._thickness())
        if not on:
            if not self._do(lambda doc: asm.take_off(doc, r.key)):
                self._ticks()
            return
        if self._fixed:               # (its tick is grey: nothing to do)
            self._ticks()
            return
        ins = [k for k in self._ins() if k != r.key]
        shown = self._shown_piece()

        def change(doc):
            stack = self._stack
            if not stack and shown and shown != r.key:
                stack = asm.put_on(doc, shown, thickness=t)
                if not stack:
                    return False
            if not stack:
                return asm.put_on(doc, r.key, thickness=t)
            return asm.put_on(doc, r.key, stack, on=ins[0] if ins else None,
                              thickness=t)
        if not self._do(change):
            self._ticks()             # nothing came of it: as it was

    def _find_drawable(self, rows):
        """Which of the pieces can be laid as they are drawn: those drawn
        overlapping the main piece (of the stack shown, while it lies as
        it is drawn) or, with no stack yet, the piece shown."""
        self._drawable = set()
        doc = self._doc()
        if doc is None:
            return
        shown = next((r.key for r in rows if r.where == sm.SHOWN), "")
        for r in rows:
            if r.main:
                continue
            if self._stack:
                ok = asm.drawn_on_main(doc, r.key, self._stack)
            else:
                ok = bool(shown) and drawn_over(doc, shown, r.key)
            if ok:
                self._drawable.add(r.key)

    def _to_draw(self) -> List[sm.Row]:
        """The pieces "As drawn" would lay now: the chosen ones it
        applies to or, with none chosen, every piece not in the stack
        that is drawn on the main piece."""
        rows = self.chosen_rows() or [r for r in self.rows()
                                      if r.where in (sm.OUT, sm.INSIDE)]
        return [r for r in rows if r.key in self._drawable]

    def _as_drawn(self):
        rows = self._to_draw()
        t = float(self._thickness())
        shown = self._shown_piece()

        def change(doc):
            stack = self._stack
            if not stack:
                stack = asm.put_on(doc, shown, thickness=t) if shown else ""
                if not stack:
                    return False
            done = asm.lay_as_drawn(
                doc, [r.key for r in rows if r.where == sm.IN])
            for r in rows:
                if r.where != sm.IN and asm.drawn_on_main(doc, r.key, stack):
                    done = bool(asm.put_on(doc, r.key, stack, thickness=t)) \
                        or done
            return stack if done or not self._stack else False
        if rows:
            self._do(change)

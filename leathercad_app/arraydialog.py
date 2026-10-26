"""Small dialog to gather parameters for a grid, circular or along-path array.

Lengths are shown and typed in the display unit (View → Units) and returned
in millimetres. The dialog remembers its last values; the grid spacing
defaults to the selection's own size plus a small gap so copies never land
on top of each other.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QComboBox, QSpinBox, QCheckBox, QFormLayout,
    QVBoxLayout, QStackedWidget, QWidget, QDialogButtonBox, QLabel,
)

from . import units as U
from .mathspin import MathSpinBox

DEFAULT_GAP = 3.0        # mm between copies when the spacing is derived


class ArrayDialog(QDialog):
    """Returns (mode, params) via ``result_params()`` after accept.

    mode == "grid":     params = dict(rows, cols, dx, dy)           (mm)
    mode == "circular": params = dict(count, cx, cy, total_deg, rotate_items)
    mode == "path":     params = dict(count, spacing, follow)       (mm)
    """

    def __init__(self, parent=None, center=(0.0, 0.0), size=None,
                 settings=None):
        super().__init__(parent)
        self.setWindowTitle("Array")
        self._settings = settings
        root = QVBoxLayout(self)

        def remembered(key, default, typ=float):
            if settings is None:
                return default
            try:
                return settings.value("array_" + key, default, type=typ)
            except Exception:
                return default

        def length(lo, hi, value_mm, tip=""):
            sp = MathSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(4)                # exact mm behind a trimmed display
            sp.trim_zeros = True
            sp.physical = True
            U.register_spin(sp)
            sp.setValue(value_mm)
            if tip:
                sp.setToolTip(tip)
            return sp

        self.mode = QComboBox()
        self.mode.addItems(["Grid", "Circular", "Along path"])
        mrow = QFormLayout()
        mrow.addRow("Type", self.mode)
        root.addLayout(mrow)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # -- grid page ---------------------------------------------------
        w, h = size if size else (0.0, 0.0)
        grid = QWidget()
        gf = QFormLayout(grid)
        self.rows = QSpinBox(); self.rows.setRange(1, 200)
        self.rows.setValue(int(remembered("rows", 1, int)))
        self.cols = QSpinBox(); self.cols.setRange(1, 200)
        self.cols.setValue(int(remembered("cols", 3, int)))
        # centre-to-centre, so the default is the piece plus a gap -- a fixed
        # 15 mm overlapped anything wider than 15 mm
        self.dx = length(-5000, 5000, (w + DEFAULT_GAP) if w > 0 else 15.0,
                         "Centre-to-centre step between columns (the piece's "
                         "width plus a gap keeps the copies apart)")
        self.dy = length(-5000, 5000, (h + DEFAULT_GAP) if (h > 0 and
                                                           remembered("rows", 1, int) > 1)
                         else 0.0,
                         "Centre-to-centre step between rows (0 = one row)")
        gf.addRow("Rows", self.rows)
        gf.addRow("Columns", self.cols)
        gf.addRow("X spacing (centre to centre)", self.dx)
        gf.addRow("Y spacing (centre to centre)", self.dy)
        self.stack.addWidget(grid)
        self._size = (w, h)
        self.rows.valueChanged.connect(self._rows_changed)

        # -- circular page ----------------------------------------------
        circ = QWidget()
        cf = QFormLayout(circ)
        self.count = QSpinBox(); self.count.setRange(2, 360)
        self.count.setValue(int(remembered("count", 6, int)))
        self.cx = length(-100000, 100000, center[0],
                         "Centre of the circle the copies sit on (defaults to "
                         "the selection's centre)")
        self.cy = length(-100000, 100000, center[1])
        self.angle = MathSpinBox(); self.angle.setRange(-360, 360)
        self.angle.setValue(remembered("angle", 360.0)); self.angle.setSuffix(" °")
        self.angle.setToolTip("Arc the copies are spread over; 360 = a full ring")
        self.rotate = QCheckBox("Rotate copies to face out")
        self.rotate.setChecked(bool(remembered("rotate", True, bool)))
        cf.addRow("Count", self.count)
        cf.addRow("Centre X", self.cx)
        cf.addRow("Centre Y", self.cy)
        cf.addRow("Total angle", self.angle)
        cf.addRow("", self.rotate)
        self.stack.addWidget(circ)

        # -- along-path page ----------------------------------------------
        path = QWidget()
        pf = QFormLayout(path)
        self.pcount = QSpinBox(); self.pcount.setRange(1, 500)
        self.pcount.setValue(int(remembered("pcount", 5, int)))
        self.pspacing = length(0, 10000, remembered("pspacing", 0.0),
                               "0 = spread the copies evenly along the path")
        self.pfollow = QCheckBox("Turn copies to follow the path")
        self.pfollow.setChecked(bool(remembered("pfollow", True, bool)))
        pf.addRow("Count", self.pcount)
        pf.addRow("Spacing (0 = even)", self.pspacing)
        pf.addRow("", self.pfollow)
        pf.addRow("", QLabel("Select the pieces AND an open line / curve / "
                             "guide to copy along. Originals stay put; a copy "
                             "that would land on an original is skipped."))
        self.stack.addWidget(path)

        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.mode.setCurrentIndex(int(remembered("mode", 0, int)))
        self.stack.setCurrentIndex(self.mode.currentIndex())

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _rows_changed(self, n: int) -> None:
        # a second row needs a Y step: offer the piece's height plus the gap
        # when the user hasn't typed one
        if n > 1 and abs(self.dy.value()) < 1e-9 and self._size[1] > 0:
            self.dy.setValue(self._size[1] + DEFAULT_GAP)

    def accept(self) -> None:          # noqa: D401 - Qt slot
        s = self._settings
        if s is not None:
            s.setValue("array_mode", self.mode.currentIndex())
            s.setValue("array_rows", self.rows.value())
            s.setValue("array_cols", self.cols.value())
            s.setValue("array_count", self.count.value())
            s.setValue("array_angle", self.angle.value())
            s.setValue("array_rotate", self.rotate.isChecked())
            s.setValue("array_pcount", self.pcount.value())
            s.setValue("array_pspacing", self.pspacing.value())
            s.setValue("array_pfollow", self.pfollow.isChecked())
        super().accept()

    def result_params(self):
        if self.mode.currentIndex() == 0:
            return "grid", dict(rows=self.rows.value(), cols=self.cols.value(),
                                dx=self.dx.value(), dy=self.dy.value())
        if self.mode.currentIndex() == 2:
            return "path", dict(count=self.pcount.value(),
                                spacing=self.pspacing.value(),
                                follow=self.pfollow.isChecked())
        return "circular", dict(count=self.count.value(), cx=self.cx.value(),
                                cy=self.cy.value(), total_deg=self.angle.value(),
                                rotate_items=self.rotate.isChecked())

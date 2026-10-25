"""Small dialog to gather parameters for a grid or circular array."""

from __future__ import annotations

from .mathspin import MathSpinBox
from PySide6.QtWidgets import (
    QDialog, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFormLayout,
    QVBoxLayout, QStackedWidget, QWidget, QDialogButtonBox, QLabel,
)


class ArrayDialog(QDialog):
    """Returns (mode, params) via ``result_params()`` after accept.

    mode == "grid":     params = dict(rows, cols, dx, dy)
    mode == "circular": params = dict(count, cx, cy, total_deg, rotate_items)
    """

    def __init__(self, parent=None, center=(0.0, 0.0)):
        super().__init__(parent)
        self.setWindowTitle("Array")
        root = QVBoxLayout(self)

        self.mode = QComboBox()
        self.mode.addItems(["Grid", "Circular", "Along path"])
        mrow = QFormLayout()
        mrow.addRow("Type", self.mode)
        root.addLayout(mrow)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # -- grid page ---------------------------------------------------
        grid = QWidget()
        gf = QFormLayout(grid)
        self.rows = QSpinBox(); self.rows.setRange(1, 200); self.rows.setValue(1)
        self.cols = QSpinBox(); self.cols.setRange(1, 200); self.cols.setValue(3)
        self.dx = MathSpinBox(); self.dx.setRange(-500, 500); self.dx.setValue(15.0)
        self.dx.setSuffix(" mm")
        self.dy = MathSpinBox(); self.dy.setRange(-500, 500); self.dy.setValue(0.0)
        self.dy.setSuffix(" mm")
        gf.addRow("Rows", self.rows)
        gf.addRow("Columns", self.cols)
        gf.addRow("X spacing", self.dx)
        gf.addRow("Y spacing", self.dy)
        self.stack.addWidget(grid)

        # -- circular page ----------------------------------------------
        circ = QWidget()
        cf = QFormLayout(circ)
        self.count = QSpinBox(); self.count.setRange(2, 360); self.count.setValue(6)
        self.cx = MathSpinBox(); self.cx.setRange(-100000, 100000)
        self.cx.setValue(center[0]); self.cx.setSuffix(" mm")
        self.cy = MathSpinBox(); self.cy.setRange(-100000, 100000)
        self.cy.setValue(center[1]); self.cy.setSuffix(" mm")
        self.angle = MathSpinBox(); self.angle.setRange(-360, 360)
        self.angle.setValue(360.0); self.angle.setSuffix(" °")
        self.rotate = QCheckBox("Rotate copies to face out")
        self.rotate.setChecked(True)
        cf.addRow("Count", self.count)
        cf.addRow("Centre X", self.cx)
        cf.addRow("Centre Y", self.cy)
        cf.addRow("Total angle", self.angle)
        cf.addRow("", self.rotate)
        self.stack.addWidget(circ)

        # -- along-path page ----------------------------------------------
        path = QWidget()
        pf = QFormLayout(path)
        self.pcount = QSpinBox(); self.pcount.setRange(1, 500); self.pcount.setValue(5)
        self.pspacing = MathSpinBox(); self.pspacing.setRange(0, 10000)
        self.pspacing.setValue(0.0); self.pspacing.setSuffix(" mm")
        self.pspacing.setToolTip("0 = spread the copies evenly along the path")
        self.pfollow = QCheckBox("Turn copies to follow the path")
        self.pfollow.setChecked(True)
        pf.addRow("Count", self.pcount)
        pf.addRow("Spacing (0 = even)", self.pspacing)
        pf.addRow("", self.pfollow)
        pf.addRow("", QLabel("Select the pieces AND an open line / curve / "
                             "guide to copy along. Originals stay put."))
        self.stack.addWidget(path)

        self.mode.currentIndexChanged.connect(self.stack.setCurrentIndex)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

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

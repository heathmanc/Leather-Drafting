"""The lettering edit dialog (double-click a text on the canvas).

Edits the source string plus its font family, size, bold/italic and tracking.
The caller re-bakes the glyph contours from the returned values -- baking needs
Qt, so it stays out of the engine.
"""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                               QVBoxLayout, QPlainTextEdit, QCheckBox)

from .mathspin import NoWheelComboBox, MathSpinBox


class TextEditDialog(QDialog):
    """Edit a ``TextShape``'s string / font / size / style / tracking."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text")
        lay = QVBoxLayout(self)

        self.text = QPlainTextEdit()
        self.text.setPlainText(model.text)
        self.text.setTabChangesFocus(True)
        self.text.setFixedHeight(70)
        lay.addWidget(self.text)

        form = QFormLayout()
        self.family = NoWheelComboBox()
        self.family.addItems(QFontDatabase.families())
        # fall back to the bundled default (not index 0) for an empty / missing
        # family, so OK never rewrites the typeface to an unrelated font.
        from .fonts import default_family
        i = self.family.findText(model.font_family or default_family())
        if i < 0:
            i = self.family.findText(default_family())
        self.family.setCurrentIndex(max(0, i))
        self.size = MathSpinBox()
        self.size.setRange(0.5, 1000.0)
        self.size.setDecimals(1)
        self.size.setSuffix(" mm")
        self.size.setValue(model.size)
        self.tracking = MathSpinBox()
        self.tracking.setRange(-50.0, 200.0)
        self.tracking.setDecimals(1)
        self.tracking.setSuffix(" %")
        self.tracking.setValue(model.tracking)
        self.bold = QCheckBox("Bold")
        self.bold.setChecked(model.bold)
        self.italic = QCheckBox("Italic")
        self.italic.setChecked(model.italic)
        form.addRow("Font", self.family)
        form.addRow("Cap height", self.size)
        form.addRow("Tracking", self.tracking)
        form.addRow("", self.bold)
        form.addRow("", self.italic)
        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {
            "text": self.text.toPlainText(),
            "font_family": self.family.currentText(),
            "size": self.size.value(),
            "tracking": self.tracking.value(),
            "bold": self.bold.isChecked(),
            "italic": self.italic.isChecked(),
        }

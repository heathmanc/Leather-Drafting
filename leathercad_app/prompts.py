"""Small unit-aware prompts.

``QInputDialog.getDouble`` knows nothing about the display unit, so every
"Distance (mm)" prompt asked for millimetres even with View → Units set to
inches. These prompts use a MathSpinBox registered with the units module:
the value shown and typed is in the display unit (math and explicit units
such as ``1/2in`` work), the value returned is millimetres.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QVBoxLayout)

from . import units as U
from .mathspin import MathSpinBox


def length_spin(lo_mm: float, hi_mm: float, decimals: int = 2,
                value_mm: Optional[float] = None) -> MathSpinBox:
    """A length field in the display unit whose value() is millimetres."""
    sp = MathSpinBox()
    sp.setRange(lo_mm, hi_mm)
    sp.setDecimals(decimals)
    sp.physical = True
    U.register_spin(sp)
    if value_mm is not None:
        sp.setValue(value_mm)
    return sp


def ask_length(parent, title: str, label: str, default_mm: float,
               lo_mm: float, hi_mm: float, decimals: int = 2,
               hint: str = "") -> Tuple[float, bool]:
    """Ask for one length. Returns ``(mm, ok)``."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    if hint:
        h = QLabel(hint)
        h.setWordWrap(True)
        lay.addWidget(h)
    form = QFormLayout()
    sp = length_spin(lo_mm, hi_mm, decimals, default_mm)
    form.addRow(label, sp)
    lay.addLayout(form)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                          | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    sp.selectAll()
    sp.setFocus()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return default_mm, False
    return sp.value(), True

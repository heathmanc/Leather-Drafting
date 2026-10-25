"""Display units: millimetres or inches.

Everything is stored and computed in millimetres; this is only how lengths
are SHOWN and TYPED. Switch with View → Units. Physical spinboxes register
here so a switch re-labels them, and the rulers, status bar, measure tool,
dimensions and readouts format through ``fmt``.
"""

from __future__ import annotations

import re
import weakref

MM = "mm"
IN = "in"
MM_PER_IN = 25.4

_state = {"unit": MM}
_spins: "weakref.WeakSet" = weakref.WeakSet()
_listeners = []


def current() -> str:
    return _state["unit"]


def is_inches() -> bool:
    return _state["unit"] == IN


def factor() -> float:
    """Millimetres per display unit."""
    return MM_PER_IN if is_inches() else 1.0


def suffix() -> str:
    return " in" if is_inches() else " mm"


def to_display(mm: float) -> float:
    return mm / factor()


def from_display(v: float) -> float:
    return v * factor()


def fmt_num(mm: float, decimals: int | None = None) -> str:
    """The number alone, in display units; inches get more decimals (a
    thousandth of an inch is 0.025 mm) and trailing zeros trimmed."""
    if decimals is None:
        decimals = 3 if is_inches() else 2
    v = to_display(mm)
    text = f"{v:.{decimals}f}"
    if is_inches() and "." in text:          # 0.5 in, not 0.500 in
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def fmt(mm: float, decimals: int | None = None) -> str:
    """A length with its unit, e.g. ``12.7 mm`` or ``0.5 in``."""
    return f"{fmt_num(mm, decimals)} {current()}"


def fmt_area(mm2: float) -> str:
    if is_inches():
        return f"{mm2 / (MM_PER_IN ** 2):.2f} sq in"
    return f"{mm2 / 100.0:.1f} cm²"


_EXPLICIT_UNIT = re.compile(r"\d\s*(mm|cm|in)\b", re.IGNORECASE)


def has_explicit_unit(text: str) -> bool:
    """True if a typed value names its own unit (``10mm``, ``1/2in``), so it
    must not be read as the display unit."""
    return bool(_EXPLICIT_UNIT.search(text))


def register_spin(spin) -> None:
    """A spinbox whose value is a length in mm: it shows the display unit."""
    _spins.add(spin)
    spin.setSuffix(suffix())


def on_change(callback) -> None:
    _listeners.append(callback)


def set_current(unit: str) -> None:
    unit = IN if unit == IN else MM
    if unit == _state["unit"]:
        return
    _state["unit"] = unit
    for spin in list(_spins):
        try:
            spin.setSuffix(suffix())
            spin.setValue(spin.value())          # re-render in the new unit
        except RuntimeError:
            pass                                 # widget already gone
    for cb in list(_listeners):
        cb(unit)

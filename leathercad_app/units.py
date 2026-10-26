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


def fmt_long(mm: float) -> str:
    """A long length (thread): metres / centimetres, or feet / inches."""
    if is_inches():
        inches = mm / MM_PER_IN
        if inches >= 36.0:
            return f"{inches / 12.0:.1f} ft"
        return f"{inches:.1f} in"
    if mm >= 1000.0:
        return f"{mm / 1000.0:.2f} m"
    return f"{mm / 10.0:.1f} cm"


def nudge_step(big: bool = False, fine: bool = False) -> float:
    """The arrow-key nudge in mm: 1 / 10 / 0.1 mm, or with inches showing
    1/16 in (Shift 1/2 in, Ctrl 1/64 in)."""
    if is_inches():
        return MM_PER_IN * (0.5 if big else (1.0 / 64.0 if fine else 1.0 / 16.0))
    return 10.0 if big else (0.1 if fine else 1.0)


def has_explicit_unit(text: str) -> bool:
    """True if a typed value names its own unit (``10mm``, ``1/2in``,
    ``(1+1)in``), so it must not be read as the display unit. Decided by the
    evaluator itself, so the two can never disagree (a parenthesised inch
    value used to be converted twice)."""
    from leathercad.expr import has_unit
    return has_unit(text)


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

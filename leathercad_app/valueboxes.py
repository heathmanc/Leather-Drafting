"""Value boxes beside the cursor while drawing, as in Fusion.

Once a drawing tool has its first point, the sizes the next click would
give are shown beside the cursor: a line's length and angle, a box's width
and height, a circle's diameter and radius, a centre arc's radius and then
its sweep, an offset's distance. Typing goes into the box that is lit (the
first one to begin with); Tab keeps what was typed and moves to the next;
Enter places the point. A value typed or kept holds while the mouse sets
the rest -- type a length and the line still turns with the mouse.

A length is read as the fields of Properties read one: in the display unit
unless it names its own (``1/2in``, ``12mm``), with arithmetic and the
drawing's parameters. An angle is in degrees.

The boxes never take the keyboard from the canvas: the canvas hands them
the keys, so Esc, Enter and the mouse keep working as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from leathercad.expr import evaluate, names_in

from . import units as U
from .mathspin import current_params

LENGTH = "length"
ANGLE = "angle"


def read_length(text: str) -> float:
    """A typed length in millimetres. Raises ValueError if it can't be
    read. A bare number is in the display unit; ``10mm`` / ``1/2in`` name
    their own, and a parameter is in millimetres already."""
    s = text.strip()
    if not s:
        raise ValueError("empty")
    params = current_params()
    try:
        v = float(evaluate(s, params))
    except ValueError:
        v = float(s.replace(",", "."))
    if not U.has_explicit_unit(s) and not (names_in(s) & set(params)):
        v = U.from_display(v)
    return v


def read_angle(text: str) -> float:
    """A typed angle in degrees (a trailing ``°`` or ``deg`` is allowed).
    Raises ValueError if it can't be read."""
    s = text.strip()
    for tail in ("°", "deg"):
        if s.lower().endswith(tail):
            s = s[: -len(tail)].strip()
    if not s:
        raise ValueError("empty")
    try:
        return float(evaluate(s, current_params()))
    except ValueError:
        return float(s.replace(",", "."))


@dataclass
class Field:
    """One value box. ``live`` is what the mouse gives; ``text`` what is
    being typed (None when nothing is); ``kept`` a typed value kept by Tab
    or Enter. Lengths are in millimetres, angles in degrees."""

    key: str
    label: str
    kind: str = LENGTH
    live: float = 0.0
    text: Optional[str] = None
    kept: Optional[float] = None

    def typed(self) -> Optional[float]:
        """The value being typed, if it can be read."""
        if self.text is None:
            return None
        try:
            v = read_angle(self.text) if self.kind == ANGLE \
                else read_length(self.text)
        except ValueError:
            return None
        if self.kind == LENGTH and v <= 0.0 and self.key != "dist":
            return None
        return v

    def value(self) -> Optional[float]:
        """What this box holds the shape to: what is typed, if it can be
        read, else what was kept; None when it follows the mouse."""
        v = self.typed()
        return v if v is not None else self.kept

    def unreadable(self) -> bool:
        return (self.text is not None and self.text.strip() != ""
                and self.typed() is None)


# two boxes that are the same size said two ways: typing one sets the other
_LINKED = {"dia": "rad", "rad": "dia"}


class ValueBoxes(QWidget):
    """The boxes, drawn in the canvas's viewport beside the cursor. They
    take no mouse and no keyboard focus: the canvas passes them keys."""

    def __init__(self, canvas):
        super().__init__(canvas.viewport())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fields: List[Field] = []
        self.active = 0
        self.stage = None            # what the boxes were made for
        self.dark = False
        self._font = QFont(self.font())
        self._font.setPointSizeF(max(8.0, self._font.pointSizeF() * 0.95))
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self.hide()

    # -- the boxes ----------------------------------------------------------
    def set_fields(self, specs: Sequence[Tuple[str, str, str]],
                   stage) -> None:
        """New boxes ``(key, label, kind)`` for a new step of a tool; any
        typing of the step before is dropped."""
        self.fields = [Field(k, label, kind) for k, label, kind in specs]
        self.active = 0
        self.stage = stage
        self._relayout()
        self.update()

    def clear(self) -> None:
        self.fields = []
        self.stage = None
        self.hide()

    def keys(self) -> List[str]:
        return [f.key for f in self.fields]

    def field(self, key: str) -> Optional[Field]:
        return next((f for f in self.fields if f.key == key), None)

    def value(self, key: str) -> Optional[float]:
        f = self.field(key)
        return None if f is None else f.value()

    def set_live(self, key: str, v: float) -> None:
        f = self.field(key)
        if f is not None and f.live != v:
            f.live = v
            if self.isVisible():
                self._relayout()
                self.update()

    def any_value(self) -> bool:
        """Whether any box holds the shape (something typed or kept)."""
        return any(f.value() is not None for f in self.fields)

    def any_typing(self) -> bool:
        """Whether anything has been typed or kept, readable or not."""
        return any(f.text is not None or f.kept is not None
                   for f in self.fields)

    # -- typing -------------------------------------------------------------
    def type_text(self, s: str) -> None:
        if not self.fields:
            return
        f = self.fields[self.active]
        f.text = (f.text or "") + s
        self._unlink(f)
        self._changed()

    def backspace(self) -> None:
        if not self.fields:
            return
        f = self.fields[self.active]
        if f.text:
            f.text = f.text[:-1]
        elif f.text is None and f.kept is not None:
            f.kept = None                  # nothing typed: unkeep the box
        if f.text == "":
            f.text = None
        self._changed()

    def clear_active(self) -> None:
        """Delete: the lit box follows the mouse again."""
        if self.fields:
            f = self.fields[self.active]
            f.text, f.kept = None, None
            self._changed()

    def keep_active(self) -> bool:
        """Keep what was typed in the lit box (Tab, Enter). False, and
        nothing kept, if it can't be read."""
        if not self.fields:
            return True
        f = self.fields[self.active]
        if f.text is None:
            return True
        if f.text.strip() == "":
            f.text = None
            self._changed()
            return True
        v = f.typed()
        if v is None:
            self.update()
            return False
        f.kept, f.text = v, None
        self._changed()
        return True

    def move_active(self, step: int = 1) -> None:
        if self.fields:
            self.active = (self.active + step) % len(self.fields)
            self._changed()

    def unkeep_all(self) -> bool:
        """Esc: every box follows the mouse again. True if any held."""
        held = self.any_typing()
        for f in self.fields:
            f.text, f.kept = None, None
        self.active = 0
        self._changed()
        return held

    def _unlink(self, f: Field) -> None:
        other = self.field(_LINKED.get(f.key, ""))
        if other is not None:
            other.text, other.kept = None, None

    def _changed(self) -> None:
        self._relayout()
        self.update()

    # -- where and how they show ------------------------------------------
    def place_near(self, at: QPoint) -> None:
        """Beside the cursor (viewport coordinates), kept inside the view."""
        if not self.fields:
            self.hide()
            return
        self._relayout()
        vp = self.parentWidget().rect()
        w, h = self.width(), self.height()
        x, y = at.x() + 18, at.y() + 16
        if x + w > vp.right() - 4:
            x = at.x() - 18 - w
        if y + h > vp.bottom() - 4:
            y = at.y() - 16 - h
        self.move(max(vp.left() + 4, x), max(vp.top() + 4, y))
        if not self.isVisible():
            self.show()
        self.raise_()

    def _texts(self) -> List[Tuple[str, str]]:
        out = []
        for i, f in enumerate(self.fields):
            if f.text is not None:
                shown = f.text + ("▏" if i == self.active else "")
            else:
                v = f.kept if f.kept is not None else f.live
                shown = (f"{v:.1f}°" if f.kind == ANGLE
                         else U.fmt(abs(v) if f.key == "dist" else v))
            out.append((f.label, shown))
        return out

    def _relayout(self) -> None:
        fm, fb = QFontMetricsF(self._font), QFontMetricsF(self._bold)
        pad, gap = 6.0, 8.0
        width = pad
        for label, shown in self._texts():
            width += fm.horizontalAdvance(label) + 5.0
            width += max(fb.horizontalAdvance(shown), fm.horizontalAdvance(
                "000.00 mm")) + 2 * 5.0 + gap
        width += pad - gap
        self.resize(int(width + 1), int(fm.height() + 2 * 4.0 + 2 * pad - 4))

    def paintEvent(self, _event):   # noqa: N802 (Qt naming)
        if not self.fields:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.dark:
            bg, edge = QColor(38, 40, 44, 235), QColor(105, 108, 115)
            ink, faint = QColor(235, 235, 235), QColor(160, 163, 170)
            box = QColor(28, 30, 33)
        else:
            bg, edge = QColor(255, 255, 255, 238), QColor(150, 155, 162)
            ink, faint = QColor(25, 25, 25), QColor(105, 108, 115)
            box = QColor(246, 247, 249)
        lit, bad = QColor(30, 140, 255), QColor(210, 40, 40)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(edge, 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(r, 5.0, 5.0)
        fm, fb = QFontMetricsF(self._font), QFontMetricsF(self._bold)
        pad, gap = 6.0, 8.0
        x, top = pad, pad - 2.0
        hgt = fm.height() + 2 * 4.0 - 4.0
        for i, ((label, shown), f) in enumerate(zip(self._texts(),
                                                    self.fields)):
            p.setFont(self._font)
            p.setPen(faint)
            lw = fm.horizontalAdvance(label)
            p.drawText(QRectF(x, top, lw + 1, hgt),
                       Qt.AlignmentFlag.AlignVCenter, label)
            x += lw + 5.0
            held = f.text is not None or f.kept is not None
            font, met = (self._bold, fb) if held else (self._font, fm)
            bw = max(met.horizontalAdvance(shown), fm.horizontalAdvance(
                "000.00 mm")) + 2 * 5.0
            br = QRectF(x, top, bw, hgt)
            p.setBrush(box)
            p.setPen(QPen(lit if i == self.active else edge,
                          1.6 if i == self.active else 1.0))
            p.drawRoundedRect(br, 3.0, 3.0)
            p.setFont(font)
            p.setPen(bad if f.unreadable() else (ink if held else faint))
            p.drawText(br.adjusted(5.0, 0, -5.0, 0),
                       Qt.AlignmentFlag.AlignVCenter, shown)
            x += bw + gap
        p.end()

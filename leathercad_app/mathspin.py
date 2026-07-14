"""Numeric fields that evaluate arithmetic -- and user parameters.

Type ``105/2 + 3`` or ``4*25.4`` into any dimension box and it just works;
unit suffixes typed by hand are understood too (``1in`` -> 25.4). When a
document defines parameters (Edit -> Parameters), their names work in any
field: ``strap_w * 2 + 5``. The evaluation core lives in ``leathercad.expr``
(a whitelisted AST walk -- safe by construction).

A field remembers WHETHER the user's last entry used a parameter
(``last_expr``), so the Properties panel can record a live binding: change
the parameter later and every bound field re-evaluates.
"""

from __future__ import annotations

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox

from leathercad.expr import evaluate, names_in  # noqa: F401  (re-exported)

# module-level hook: returns {param name: value} for the current document.
# Installed by the main window; None -> plain arithmetic only.
_params_provider = None


def set_params_provider(fn) -> None:
    global _params_provider
    _params_provider = fn


def current_params() -> dict:
    try:
        return dict(_params_provider()) if _params_provider else {}
    except Exception:
        return {}


class MathSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that accepts arithmetic expressions while typing.

    ``last_expr`` tracks the user's latest committed entry:
      * ``None``  -- untouched since the last programmatic setValue (a load)
      * ``""``    -- the user typed a plain number (clears any binding)
      * ``"..."`` -- the user typed an expression that uses a parameter
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.last_expr = None
        self._user_edited = False
        self.lineEdit().textEdited.connect(self._mark_edited)

    def _mark_edited(self, _text) -> None:
        self._user_edited = True

    def setValue(self, v) -> None:            # programmatic load
        self.last_expr = None
        self._user_edited = False
        super().setValue(v)

    def validate(self, text: str, pos: int):
        # accept anything while typing; valueFromText decides on commit
        return QValidator.State.Acceptable, text, pos

    def fixup(self, text: str) -> str:
        return text

    def valueFromText(self, text: str) -> float:
        s = text
        suffix = self.suffix()
        if suffix and s.endswith(suffix):
            s = s[: -len(suffix)]
        s = s.strip()
        params = current_params()
        try:
            v = float(evaluate(s, params))
        except Exception:
            try:
                v = float(s.replace(",", "."))
            except ValueError:
                return self.value()          # unparseable: keep the old value
        if self._user_edited:
            used = names_in(s) & set(params)
            self.last_expr = s if used else ""
        return v

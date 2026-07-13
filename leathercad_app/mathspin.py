"""Numeric fields that evaluate arithmetic.

Type ``105/2 + 3`` or ``4*25.4`` into any dimension box and it just works.
Evaluation is a whitelisted AST walk -- numbers and + - * / // % ** ( ) only,
no names, no calls -- so it is safe. Unit suffixes typed by hand are also
understood: ``1in`` -> 25.4, ``3cm`` -> 30 (the field's own display suffix is
stripped first).
"""

from __future__ import annotations

import ast
import re

from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QDoubleSpinBox

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                   ast.Mod, ast.Pow)
_UNIT = re.compile(r"(\d(?:[\d.]*))\s*(mm|cm|in)\b", re.IGNORECASE)
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4}


def _eval_node(node) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        a = _eval_node(node.left)
        b = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.FloorDiv):
            return a // b
        if isinstance(node.op, ast.Mod):
            return a % b
        return a ** b
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval_node(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    raise ValueError("not a plain arithmetic expression")


def evaluate(text: str) -> float:
    """Evaluate ``text`` as safe arithmetic (with mm/cm/in suffixes) in mm."""
    s = text.strip().replace(",", ".")
    s = _UNIT.sub(lambda m: f"({m.group(1)}*{_UNIT_MM[m.group(2).lower()]})", s)
    return _eval_node(ast.parse(s, mode="eval"))


class MathSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that accepts arithmetic expressions while typing."""

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
        # a degree/px suffix stripped may still leave the unit word
        try:
            return float(evaluate(s))
        except Exception:
            try:
                return float(s.replace(",", "."))
            except ValueError:
                return self.value()          # unparseable: keep the old value

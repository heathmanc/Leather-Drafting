"""Safe arithmetic expressions, with units and named user parameters.

The evaluator is a whitelisted AST walk -- numbers, + - * / // % ** ( ),
unit suffixes (``1in`` -> 25.4, ``3cm`` -> 30) and, when a values dict is
passed, bare parameter names (``strap_w * 2 + 5``). No calls, no attributes,
nothing else -- so evaluating user input is safe.

``resolve`` turns the document's ordered {name: expression} parameter table
into concrete values; parameters may reference each other in any order, and
cycles are reported by name instead of hanging.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, Optional, Set

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                   ast.Mod, ast.Pow)
# a number followed by a unit word -- but not inside an identifier (strap2in)
_UNIT = re.compile(r"(?<![\w.])(\d(?:[\d.]*))\s*(mm|cm|in)\b", re.IGNORECASE)
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4}
_IDENT = re.compile(r"[A-Za-z_]\w*")

#: words that can never be parameter names (they'd collide with unit suffixes)
RESERVED = {"mm", "cm", "in"}


def valid_name(name: str) -> bool:
    return bool(_IDENT.fullmatch(name)) and name.lower() not in RESERVED


def _eval_node(node, values: Optional[Dict[str, float]]) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, values)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and values is not None:
        if node.id in values:
            return float(values[node.id])
        raise ValueError(f"unknown parameter: {node.id}")
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        a = _eval_node(node.left, values)
        b = _eval_node(node.right, values)
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
        v = _eval_node(node.operand, values)
        return -v if isinstance(node.op, ast.USub) else v
    raise ValueError("not a plain arithmetic expression")


def evaluate(text: str, values: Optional[Dict[str, float]] = None) -> float:
    """Evaluate ``text`` as safe arithmetic in mm. ``values`` (if given)
    supplies parameter names the expression may use."""
    s = text.strip().replace(",", ".")
    s = _UNIT.sub(lambda m: f"({m.group(1)}*{_UNIT_MM[m.group(2).lower()]})", s)
    return _eval_node(ast.parse(s, mode="eval"), values)


def names_in(text: str) -> Set[str]:
    """Identifiers referenced by ``text`` (unit words excluded)."""
    return {n for n in _IDENT.findall(text) if n.lower() not in RESERVED}


def resolve(params: Dict[str, str]) -> Dict[str, float]:
    """Evaluate an (ordered) {name: expression} table; parameters may use each
    other in any definition order. Raises ValueError naming a cycle or a bad
    expression."""
    values: Dict[str, float] = {}
    visiting: Set[str] = set()

    def value_of(name: str) -> float:
        if name in values:
            return values[name]
        if name not in params:
            raise ValueError(f"unknown parameter: {name}")
        if name in visiting:
            raise ValueError(f"parameter cycle involving: {name}")
        visiting.add(name)
        try:
            expr = str(params[name])
            for dep in names_in(expr):
                value_of(dep)
            try:
                values[name] = evaluate(expr, values)
            except ValueError as e:
                raise ValueError(f"{name}: {e}")
        finally:
            visiting.discard(name)
        return values[name]

    for n in params:
        value_of(n)
    return values

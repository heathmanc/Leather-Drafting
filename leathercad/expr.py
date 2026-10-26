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
# a number -- or a simple fraction, "1/2in" -- followed by a unit word, but
# not inside an identifier (strap2in)
_NUM = r"(?:\d[\d.]*|\.\d+)(?:[eE][-+]?\d+)?"
_UNIT = re.compile(r"(?<![\w.])(" + _NUM + r"(?:\s*/\s*" + _NUM + r")?)\s*(mm|cm|in)\b",
                   re.IGNORECASE)
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4}
# the whole text, ending in one unit word: "(1/2) in", "(1+1)in", "2*3 cm"
_TAIL_UNIT = re.compile(r"(.*?[\d.)])\s*(mm|cm|in)", re.IGNORECASE | re.DOTALL)
# a letter that is not the exponent of a number ("1e2")
_HAS_LETTER = re.compile(r"(?<![\d.])[A-Za-z_]|[A-DF-Za-df-z_]")


def has_unit(text: str) -> bool:
    """True when ``evaluate`` will read a unit out of ``text`` -- so a field
    must not apply its display unit on top. The one place that decides it."""
    s = text.strip()
    tail = _TAIL_UNIT.fullmatch(s)
    if tail and not _UNIT.search(tail.group(1)) and not _HAS_LETTER.search(tail.group(1)):
        return True
    return bool(_UNIT.search(s))
_IDENT = re.compile(r"[A-Za-z_]\w*")
# a name as it appears inside an expression: not the exponent of 1e2
_NAME_REF = re.compile(r"(?<![\w.])[A-Za-z_]\w*")

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
    # One unit word at the very end of a purely numeric expression scales the
    # whole of it: "1/2in" is half an inch (12.7), not 1/(2 in). Anything with
    # a parameter name in it ("strap_w + 1in"), or more than one unit
    # ("1in+10mm"), keeps the per-number form -- parameters are millimetres.
    tail = _TAIL_UNIT.fullmatch(s)
    if (tail and not _UNIT.search(tail.group(1))
            and not _HAS_LETTER.search(tail.group(1))):
        s = f"({tail.group(1)})*{_UNIT_MM[tail.group(2).lower()]}"
    else:
        s = _UNIT.sub(lambda m: f"(({m.group(1)})*{_UNIT_MM[m.group(2).lower()]})", s)
    # every way bad input can fail is a ValueError, so callers (the
    # Parameters dialog, typed fields) only need to catch that one
    try:
        v = _eval_node(ast.parse(s, mode="eval"), values)
    except (SyntaxError, ZeroDivisionError, OverflowError, TypeError,
            RecursionError) as e:
        msg = "division by zero" if isinstance(e, ZeroDivisionError) else \
            "not a valid expression"
        raise ValueError(msg) from None
    if not isinstance(v, float) or v != v or v in (float("inf"), float("-inf")):
        raise ValueError("not a real number")      # (-8)**0.5, inf, nan
    return v


def names_in(text: str) -> Set[str]:
    """Identifiers referenced by ``text`` (unit words excluded)."""
    return {n for n in _NAME_REF.findall(text) if n.lower() not in RESERVED}


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

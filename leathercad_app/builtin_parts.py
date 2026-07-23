"""Built-in library templates: real-world currency + card sizes.

Each template is an outline at the true size with a two-line size label engraved
inside (short name + ``W x H mm``), and the label is GROUPED to the outline so
they move together. They are generated in code -- the label is baked with the
bundled font -- so they always appear in the Parts dock, organised by category,
ready to build a wallet slot or pocket around.

Sizes:
  * US banknotes -- every denomination shares one size (156 x 66 mm).
  * Euro banknotes -- size grows with denomination (EUR5..EUR200).
  * UK banknotes -- current polymer series (GBP5..GBP50).
  * Cards -- ISO/IEC 7810 ID-1 (credit / bank / ID card) and a US business card.
"""

from __future__ import annotations

from leathercad.geometry import Vec2
from leathercad.shapes import Rectangle, Transform, _next_id
from leathercad.text import TextShape

# key, category, list name, short label drawn inside, width_mm, height_mm, radius
_TEMPLATES = [
    ("us_bill",  "US currency",   "US bill (all notes)", "US BILL", 156.0, 66.0, 0.0),

    ("eur_5",    "Euro currency", "€5",   "EURO €5",   120.0, 62.0, 0.0),
    ("eur_10",   "Euro currency", "€10",  "EURO €10",  127.0, 67.0, 0.0),
    ("eur_20",   "Euro currency", "€20",  "EURO €20",  133.0, 72.0, 0.0),
    ("eur_50",   "Euro currency", "€50",  "EURO €50",  140.0, 77.0, 0.0),
    ("eur_100",  "Euro currency", "€100", "EURO €100", 147.0, 82.0, 0.0),
    ("eur_200",  "Euro currency", "€200", "EURO €200", 153.0, 77.0, 0.0),

    ("gbp_5",    "UK currency",   "£5",   "GBP £5",   125.0, 65.0, 0.0),
    ("gbp_10",   "UK currency",   "£10",  "GBP £10",  132.0, 69.0, 0.0),
    ("gbp_20",   "UK currency",   "£20",  "GBP £20",  139.0, 73.0, 0.0),
    ("gbp_50",   "UK currency",   "£50",  "GBP £50",  146.0, 77.0, 0.0),

    ("card_id1", "Cards", "Credit / bank card (ID-1)", "CREDIT CARD",   85.6, 54.0, 3.18),
    ("card_biz", "Cards", "US business card",          "BUSINESS CARD", 88.9, 50.8, 0.0),
]

# category display order in the Parts dock
CATEGORY_ORDER = ["US currency", "Euro currency", "UK currency", "Cards"]


def categories():
    """Ordered ``[(category, [(display_name, key), ...])]`` for the Parts dock."""
    out = []
    for cat in CATEGORY_ORDER:
        rows = [(f"{name}  ·  {w:g} × {h:g} mm", key)
                for key, c, name, _s, w, h, _r in _TEMPLATES if c == cat]
        if rows:
            out.append((cat, rows))
    return out


def _centered_text(s, size, cx, cy, family, gid):
    """A TextShape whose glyph bounding box is centred on ``(cx, cy)``."""
    from .items import bake_text_contours
    contours = bake_text_contours(s, family, size)
    pts = [p for c in contours for p in c]
    if not pts:
        return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    bx = (min(xs) + max(xs)) / 2.0
    by = (min(ys) + max(ys)) / 2.0
    return TextShape(
        text=s, font_family=family, size=size,
        contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
        transform=Transform(x=cx - bx, y=cy - by), layer="Engrave",
        group_id=gid)


def build_builtin(key: str):
    """``(shapes, texts)`` for a template key -- the outline plus its size label,
    centred on the origin (``place_shapes`` recentres to the view). The outline
    and label share a fresh group_id so they move together as one template."""
    from . import fonts
    spec = next((t for t in _TEMPLATES if t[0] == key), None)
    if spec is None:
        return [], []
    _key, _cat, name, short, w, h, r = spec
    family = fonts.default_family()
    gid = _next_id("group")
    rect = Rectangle(name=name, width=w, height=h, corner_radius=r,
                     transform=Transform(x=0.0, y=0.0), layer="Cut",
                     stitch=None, group_id=gid)
    size = max(3.0, min(5.0, h * 0.11))     # fits inside the shorter dimension
    gap = size * 0.95
    texts = [t for t in (_centered_text(short, size, 0.0, gap, family, gid),
                         _centered_text(f"{w:g} × {h:g} mm", size, 0.0,
                                        -gap, family, gid))
             if t is not None]
    return [rect], texts

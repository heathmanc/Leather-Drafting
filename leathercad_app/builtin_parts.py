"""Built-in library templates: real-world currency + card sizes.

Each template is an outline at the true size with a two-line size label engraved
inside (short name + ``W x H mm``). They are generated in code -- the label is
baked with the bundled font -- so they always appear in the Parts dock next to
your own saved parts, ready to build a wallet slot or pocket around.

Sizes:
  * US banknotes -- every denomination shares one size (156 x 66 mm).
  * Euro banknotes -- size grows with denomination (EUR5..EUR200).
  * Cards -- ISO/IEC 7810 ID-1 (credit / bank / ID card) and a US business card.
"""

from __future__ import annotations

from leathercad.geometry import Vec2
from leathercad.shapes import Rectangle, Transform
from leathercad.text import TextShape

# key, list name, short label drawn inside, width_mm, height_mm, corner_radius_mm
_TEMPLATES = [
    ("us_bill",  "US bill (all notes)",         "US BILL",       156.0, 66.0, 0.0),
    ("eur_5",    "Euro €5",                "EURO €5",  120.0, 62.0, 0.0),
    ("eur_10",   "Euro €10",               "EURO €10", 127.0, 67.0, 0.0),
    ("eur_20",   "Euro €20",               "EURO €20", 133.0, 72.0, 0.0),
    ("eur_50",   "Euro €50",               "EURO €50", 140.0, 77.0, 0.0),
    ("eur_100",  "Euro €100",              "EURO €100", 147.0, 82.0, 0.0),
    ("eur_200",  "Euro €200",              "EURO €200", 153.0, 77.0, 0.0),
    ("card_id1", "Credit / bank card (ID-1)",   "CREDIT CARD",   85.6, 54.0, 3.18),
    ("card_biz", "US business card",            "BUSINESS CARD", 88.9, 50.8, 0.0),
]


def list_builtins():
    """``[(display_name, key)]`` for the Parts dock, in listing order."""
    return [(f"{name}  ·  {w:g} × {h:g} mm", key)
            for key, name, _short, w, h, _r in _TEMPLATES]


def _centered_text(s: str, size: float, cx: float, cy: float, family: str):
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
        transform=Transform(x=cx - bx, y=cy - by), layer="Engrave")


def build_builtin(key: str):
    """``(shapes, texts)`` for a template key -- the outline plus its size
    label, centred on the origin (``place_shapes`` recentres to the view)."""
    from . import fonts
    spec = next((t for t in _TEMPLATES if t[0] == key), None)
    if spec is None:
        return [], []
    _key, name, short, w, h, r = spec
    family = fonts.default_family()
    rect = Rectangle(name=name, width=w, height=h, corner_radius=r,
                     transform=Transform(x=0.0, y=0.0), layer="Cut", stitch=None)
    # label size: fits comfortably inside the shorter dimension
    size = max(3.0, min(5.0, h * 0.11))
    gap = size * 0.95
    texts = [t for t in (_centered_text(short, size, 0.0, gap, family),
                         _centered_text(f"{w:g} × {h:g} mm", size, 0.0,
                                        -gap, family))
             if t is not None]
    return [rect], texts

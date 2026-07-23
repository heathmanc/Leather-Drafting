"""Built-in library templates: real-world currency, coin, card, passport and
device sizes.

Each template is an outline at the true size with a two-line size label engraved
inside (short name + dimensions), and the label is GROUPED to the outline so
they move together. They are generated in code -- the label is baked with the
bundled font -- so they always appear in the Parts dock, organised by category,
ready to build a wallet slot, coin pocket or device sleeve around.

Sizes:
  * US banknotes -- every denomination shares one size (156 x 66 mm).
  * Euro / UK banknotes -- size grows with denomination.
  * US coins -- mint diameters (round templates).
  * Cards -- ISO/IEC 7810 ID-1; passports -- ID-3 book.
  * Apple devices -- iPhone / iPad / AirPods / AirTag body footprints. These are
    model-specific and approximate (thickness ignored); add your own ease.
"""

from __future__ import annotations

from leathercad.geometry import Vec2
from leathercad.shapes import Rectangle, Circle, Transform, _next_id
from leathercad.text import TextShape

# key, category, list name, short label, kind ("rect"/"circle"), w, h, radius.
# For a circle, w is the diameter (h == w, radius unused).
_TEMPLATES = [
    ("us_bill",  "US currency",   "US bill (all notes)", "US BILL", "rect", 156.0, 66.0, 0.0),

    ("eur_5",    "Euro currency", "€5",   "EURO €5",   "rect", 120.0, 62.0, 0.0),
    ("eur_10",   "Euro currency", "€10",  "EURO €10",  "rect", 127.0, 67.0, 0.0),
    ("eur_20",   "Euro currency", "€20",  "EURO €20",  "rect", 133.0, 72.0, 0.0),
    ("eur_50",   "Euro currency", "€50",  "EURO €50",  "rect", 140.0, 77.0, 0.0),
    ("eur_100",  "Euro currency", "€100", "EURO €100", "rect", 147.0, 82.0, 0.0),
    ("eur_200",  "Euro currency", "€200", "EURO €200", "rect", 153.0, 77.0, 0.0),

    ("gbp_5",    "UK currency",   "£5",   "GBP £5",   "rect", 125.0, 65.0, 0.0),
    ("gbp_10",   "UK currency",   "£10",  "GBP £10",  "rect", 132.0, 69.0, 0.0),
    ("gbp_20",   "UK currency",   "£20",  "GBP £20",  "rect", 139.0, 73.0, 0.0),
    ("gbp_50",   "UK currency",   "£50",  "GBP £50",  "rect", 146.0, 77.0, 0.0),

    ("coin_dime",    "US coins", "Dime (10¢)",        "DIME",        "circle", 17.91, 17.91, 0.0),
    ("coin_penny",   "US coins", "Penny (1¢)",        "PENNY",       "circle", 19.05, 19.05, 0.0),
    ("coin_nickel",  "US coins", "Nickel (5¢)",       "NICKEL",      "circle", 21.21, 21.21, 0.0),
    ("coin_quarter", "US coins", "Quarter (25¢)",     "QUARTER",     "circle", 24.26, 24.26, 0.0),
    ("coin_dollar",  "US coins", "Dollar coin ($1)",  "DOLLAR",      "circle", 26.49, 26.49, 0.0),
    ("coin_half",    "US coins", "Half dollar (50¢)", "HALF DOLLAR", "circle", 30.61, 30.61, 0.0),

    ("card_id1", "Cards", "Credit / bank card (ID-1)", "CREDIT CARD",   "rect", 85.6, 54.0, 3.18),
    ("card_biz", "Cards", "US business card",          "BUSINESS CARD", "rect", 88.9, 50.8, 0.0),

    ("pass_id3",  "Passports", "Passport (ID-3, closed)", "PASSPORT",      "rect", 125.0, 88.0, 3.0),
    ("pass_open", "Passports", "Passport (ID-3, open)",   "PASSPORT OPEN", "rect", 250.0, 88.0, 3.0),

    ("ip_se",      "Apple devices", "iPhone SE (2022)",      "iPHONE SE",       "rect", 67.3, 138.4, 8.0),
    ("ip_15",      "Apple devices", "iPhone 15 / 15 Pro",    "iPHONE 15",       "rect", 71.6, 147.6, 12.0),
    ("ip_15pm",    "Apple devices", "iPhone 15 Pro Max",     "iPHONE 15 PRO MAX", "rect", 76.7, 159.9, 12.0),
    ("ipad_mini",  "Apple devices", "iPad mini (6th gen)",   "iPAD MINI",       "rect", 134.8, 195.4, 12.0),
    ("ipad_11",    "Apple devices", "iPad Air / Pro 11\"",   "iPAD 11\"",       "rect", 178.5, 247.6, 12.0),
    ("ipad_13",    "Apple devices", "iPad Pro 13\" (M4)",    "iPAD PRO 13\"",   "rect", 215.5, 281.6, 14.0),
    ("airpods_pro", "Apple devices", "AirPods Pro case",     "AIRPODS PRO",     "rect", 60.6, 45.2, 20.0),
    ("airpods_3",  "Apple devices", "AirPods (3rd gen) case", "AIRPODS",        "rect", 54.4, 46.4, 20.0),
    ("airtag",     "Apple devices", "AirTag",                "AIRTAG",          "circle", 31.9, 31.9, 0.0),
]

# category display order in the Parts dock
CATEGORY_ORDER = ["US currency", "Euro currency", "UK currency", "US coins",
                  "Cards", "Passports", "Apple devices"]


def _dims_label(kind: str, w: float, h: float) -> str:
    return f"Ø {w:g} mm" if kind == "circle" else f"{w:g} × {h:g} mm"


def categories():
    """Ordered ``[(category, [(display_name, key), ...])]`` for the Parts dock."""
    out = []
    for cat in CATEGORY_ORDER:
        rows = [(f"{name}  ·  {_dims_label(kind, w, h)}", key)
                for key, c, name, _s, kind, w, h, _r in _TEMPLATES if c == cat]
        if rows:
            out.append((cat, rows))
    return out


def _label_width_per_size(s, family):
    """Width of ``s`` at a font size of 1 mm (glyph width scales linearly)."""
    from .items import bake_text_contours
    ref = 10.0
    contours = bake_text_contours(s, family, ref)
    xs = [p.x for c in contours for p in c]
    return (max(xs) - min(xs)) / ref if xs else 0.0


def _fit_label_size(short, dims, family, kind, w, h):
    """Largest label height (mm) at which BOTH stacked lines stay inside the
    outline. The two lines are centred at y = ±0.95·size and are ~1·size tall,
    so the stack reaches ±1.45·size vertically and ±(width/2) horizontally.

    * rect  -- fit within 90 % of the width and height.
    * circle -- fit within the inscribed disc (every corner inside 0.45·D).
    """
    import math
    wps = max(_label_width_per_size(short, family),
              _label_width_per_size(dims, family), 1e-6)
    half_top = 1.45          # outer edge of the stack, in units of ``size``
    if kind == "circle":
        r_eff = 0.45 * w                       # w is the diameter
        # (wps·size/2)² + (1.45·size)² ≤ r_eff²
        denom = math.sqrt((0.5 * wps) ** 2 + half_top ** 2)
        size = r_eff / denom if denom > 1e-9 else 5.0
    else:
        size_w = (0.9 * w) / wps
        size_h = (0.9 * h) / (2.0 * half_top)
        size = min(size_w, size_h)
    return max(1.4, min(6.0, size))


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
    _key, _cat, name, short, kind, w, h, r = spec
    family = fonts.default_family()
    gid = _next_id("group")
    if kind == "circle":
        shape = Circle(name=name, rx=w / 2.0, ry=w / 2.0,
                       transform=Transform(x=0.0, y=0.0), layer="Cut",
                       stitch=None, group_id=gid)
    else:
        shape = Rectangle(name=name, width=w, height=h, corner_radius=r,
                          transform=Transform(x=0.0, y=0.0), layer="Cut",
                          stitch=None, group_id=gid)
    dims = _dims_label(kind, w, h)
    size = _fit_label_size(short, dims, family, kind, w, h)
    gap = size * 0.95
    texts = [t for t in (_centered_text(short, size, 0.0, gap, family, gid),
                         _centered_text(dims, size, 0.0, -gap, family, gid))
             if t is not None]
    return [shape], texts

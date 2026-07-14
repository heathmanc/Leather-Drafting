"""Stitching-punch catalog and hole geometry.

Real stitching punches come in four cutting-edge styles, and each leaves a
differently shaped hole:

    round    -- a round hole (round-hole pricking irons / drive punches)
    oblique  -- a flat, shallow-slanted slit
    french   -- a fine, steeper slanted slit (the Blanchard look)
    diamond  -- a diamond / lozenge, angled like the awl

Punches are then organised maker -> size, where "size" is the stitch *pitch*
(tooth spacing). Bench makers sell by millimetre (2.7 .. 4.0 mm); the harness
and French traditions sell by SPI / points-per-inch. Pitch drives the geometry,
so two makers' 3.85 mm diamond irons are interchangeable here -- the maker is a
convenience label.

The catalog groups it all as ``style -> maker -> [Punch]`` and
:func:`geometry_for` hands back the hole dimensions a style implies (which the
size fields can still override). Line-ups shift and this is not exhaustive;
treat it as a curated starting point and type any pitch you like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

MM_PER_INCH = 25.4

# the four cutting-edge styles, in catalogue order
ROUND, OBLIQUE, FRENCH, DIAMOND = "round", "oblique", "french", "diamond"
STYLES = (ROUND, OBLIQUE, FRENCH, DIAMOND)

# diamond hole width as a fraction of its length (a slim lozenge)
DIAMOND_WIDTH_RATIO = 0.4


def spi_to_mm(spi: float) -> float:
    """Stitches per inch -> pitch in millimetres."""
    return MM_PER_INCH / spi


def mm_to_spi(pitch_mm: float) -> float:
    """Pitch in millimetres -> stitches per inch."""
    return MM_PER_INCH / pitch_mm


@dataclass(frozen=True)
class Punch:
    style: str
    brand: str
    pitch_mm: float
    unit: str = "mm"          # how this maker labels sizes: "mm" or "spi"

    @property
    def spi(self) -> float:
        return mm_to_spi(self.pitch_mm)

    @property
    def size_label(self) -> str:
        if self.unit == "spi":
            return f"{round(self.spi)} SPI"
        return f"{self.pitch_mm:g} mm"

    @classmethod
    def from_spi(cls, style: str, brand: str, spi: float) -> "Punch":
        return cls(style, brand, spi_to_mm(spi), "spi")


# Per-style hole geometry. ``len_ratio`` scales the slit / diamond length to the
# pitch; ``angle`` is the slant off the seam tangent. Round holes are a fixed
# small diameter. These are defaults -- the size fields override them.
STYLE_DEFAULTS: Dict[str, dict] = {
    ROUND:   {"hole_style": "round",   "hole_diameter": 1.0,
              "len_ratio": 0.0,  "angle": 0.0},
    OBLIQUE: {"hole_style": "slit",    "hole_diameter": 1.0,
              "len_ratio": 0.42, "angle": 22.0},
    FRENCH:  {"hole_style": "slit",    "hole_diameter": 1.0,
              "len_ratio": 0.38, "angle": 42.0},
    DIAMOND: {"hole_style": "diamond", "hole_diameter": 1.0,
              "len_ratio": 0.42, "angle": 35.0},
}


def geometry_for(style: str, pitch_mm: float) -> dict:
    """Hole dimensions a ``style`` implies at ``pitch_mm``: a dict of the
    StitchSettings hole fields (hole_style / hole_diameter / slit_length /
    slit_angle) ready to apply."""
    d = STYLE_DEFAULTS.get(style, STYLE_DEFAULTS[ROUND])
    slit_len = round(max(0.4, d["len_ratio"] * pitch_mm), 2) if d["len_ratio"] \
        else 1.6
    return {
        "hole_style": d["hole_style"],
        "hole_diameter": d["hole_diameter"],
        "slit_length": slit_len,
        "slit_angle": d["angle"],
    }


def _mm(pitches: List[float]) -> str:
    return ("mm", list(pitches))


def _spi(spis: List[float]) -> str:
    return ("spi", list(spis))


# style -> maker -> (unit, [sizes in that unit]). Curated + editable.
_RAW: Dict[str, Dict[str, tuple]] = {
    ROUND: {
        "KS Blade Punch": _mm([3.0, 3.38, 3.85, 4.0]),
        "Amy Roke": _mm([2.7, 3.0, 3.38, 3.85]),
        "Wuta": _mm([3.0, 3.38, 3.85, 4.0]),
        "Sinabroks": _mm([3.0, 3.85]),
    },
    OBLIQUE: {
        "KS Blade Punch": _mm([2.7, 3.0, 3.38, 3.85, 4.0]),
        "Amy Roke": _mm([2.7, 3.0, 3.38, 3.85]),
        "Wuta": _mm([3.0, 3.38, 3.85, 4.0]),
        "Seiwa": _mm([3.0, 3.5, 4.0]),
    },
    FRENCH: {
        "Blanchard": _spi([8, 9, 10, 11, 12]),
        "Vergez-Blanchard": _spi([8, 9, 10, 11]),
        "Joseph Dixon": _spi([6, 7, 8, 9]),
    },
    DIAMOND: {
        "KS Blade Punch": _mm([2.7, 3.0, 3.38, 3.85, 4.0]),
        "Amy Roke": _mm([2.0, 2.45, 2.7, 3.0, 3.38, 3.85]),
        "Crimson Hides": _mm([3.0, 3.38, 3.85]),
        "Sinabroks": _mm([3.0, 3.38, 3.85]),
        "Wuta": _mm([3.0, 3.38, 3.85, 4.0]),
        "Weaver Leather": _spi([5, 6, 7]),
    },
}


def _build() -> Dict[str, Dict[str, List[Punch]]]:
    catalog: Dict[str, Dict[str, List[Punch]]] = {}
    for style, makers in _RAW.items():
        catalog[style] = {}
        for brand, (unit, sizes) in makers.items():
            if unit == "spi":
                catalog[style][brand] = [Punch.from_spi(style, brand, s)
                                         for s in sizes]
            else:
                catalog[style][brand] = [Punch(style, brand, p, "mm")
                                         for p in sizes]
    return catalog


CATALOG: Dict[str, Dict[str, List[Punch]]] = _build()


def styles() -> List[str]:
    return list(STYLES)


def brands_for(style: str) -> List[str]:
    """Makers that offer ``style`` (catalogue order)."""
    return list(CATALOG.get(style, {}).keys())


def punches_for(style: str, brand: str) -> List[Punch]:
    """The sizes a maker sells in ``style`` (empty if unknown)."""
    return CATALOG.get(style, {}).get(brand, [])


def all_punches() -> List[Punch]:
    return [p for makers in CATALOG.values() for ps in makers.values()
            for p in ps]


def nearest(pitch_mm: float, style: Optional[str] = None,
            tol: float = 0.03) -> Optional[Punch]:
    """The catalogued punch whose pitch matches within ``tol`` mm (optionally
    restricted to ``style``), used to label a saved pitch. None if none close."""
    best, best_d = None, tol
    for p in all_punches():
        if style is not None and p.style != style:
            continue
        d = abs(p.pitch_mm - pitch_mm)
        if d <= best_d:
            best, best_d = p, d
    return best

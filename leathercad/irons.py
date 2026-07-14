"""Pricking iron / stitching chisel pitch helpers and a maker catalog.

Irons are sold two ways:
  * by pitch in millimetres (most bench makers -- e.g. 3.0, 3.38, 3.85, 4.0 mm)
  * by SPI, stitches per inch (US/harness + French tradition -- 5..12 SPI)

``pitch_mm = 25.4 / spi``. These helpers keep the two straight.

The :data:`CATALOG` groups the pitches the well-known makers actually sell so
"KS Blade 3.85 mm" is one pick instead of a number to look up. Pitch is what
drives the geometry, so two makers' 3.85 mm irons are interchangeable here --
the brand is a convenience label. Line-ups shift over time and this is not
exhaustive; treat it as a starting point, and type any pitch you like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

MM_PER_INCH = 25.4


def spi_to_mm(spi: float) -> float:
    """Stitches per inch -> pitch in millimetres."""
    return MM_PER_INCH / spi


def mm_to_spi(pitch_mm: float) -> float:
    """Pitch in millimetres -> stitches per inch."""
    return MM_PER_INCH / pitch_mm


@dataclass(frozen=True)
class Iron:
    name: str
    pitch_mm: float
    brand: str = ""

    @property
    def spi(self) -> float:
        return mm_to_spi(self.pitch_mm)

    @classmethod
    def from_spi(cls, spi: float, name: Optional[str] = None,
                 brand: str = "") -> "Iron":
        return cls(name or f"{spi:g} SPI", spi_to_mm(spi), brand)


# A handful of commonly sold pitches, indexed for round-tripping a saved
# pitch back to a friendly label. Not brand-specific -- see CATALOG for that.
PRESETS = {
    "2.0mm": Iron("2.0 mm (fine, ~12.7 SPI)", 2.0),
    "2.45mm": Iron("2.45 mm (~10.4 SPI)", 2.45),
    "2.7mm": Iron("2.7 mm (~9.4 SPI)", 2.7),
    "3.0mm": Iron("3.0 mm (~8.5 SPI)", 3.0),
    "3.38mm": Iron("3.38 mm (~7.5 SPI)", 3.38),
    "3.85mm": Iron("3.85 mm (~6.6 SPI)", 3.85),
    "4.0mm": Iron("4.0 mm (~6.4 SPI)", 4.0),
    "4.5mm": Iron("4.5 mm (~5.6 SPI)", 4.5),
    "5.0mm": Iron("5.0 mm (~5.1 SPI)", 5.0),
    "5spi": Iron.from_spi(5),
    "6spi": Iron.from_spi(6),
    "7spi": Iron.from_spi(7),
}


def _mm(brand: str, pitches: List[float]) -> List[Iron]:
    return [Iron(f"{p:g} mm", p, brand) for p in pitches]


def _spi(brand: str, spis: List[float]) -> List[Iron]:
    return [Iron.from_spi(s, brand=brand) for s in spis]


# Maker line-ups. mm makers first (Japanese/Chinese/Korean bench irons), then
# the SPI/points-per-inch tradition (US harness + French).
CATALOG: Dict[str, List[Iron]] = {
    # Japanese bench irons -- the de-facto fine-leather standard pitches.
    "KS Blade Punch": _mm("KS Blade Punch", [2.7, 3.0, 3.38, 3.85, 4.0]),
    # Hong Kong; sold straight by millimetre.
    "Amy Roke": _mm("Amy Roke", [2.0, 2.45, 2.7, 3.0, 3.38, 3.85]),
    # Korea.
    "Sinabroks": _mm("Sinabroks", [3.0, 3.38, 3.85]),
    # US maker, popular mm irons.
    "Crimson Hides": _mm("Crimson Hides", [3.0, 3.38, 3.85]),
    # Budget China irons, wide mm range.
    "Wuta": _mm("Wuta", [2.7, 3.0, 3.38, 3.85, 4.0, 5.0]),
    # US harness/saddlery, sold by stitches-per-inch.
    "Weaver Leather": _spi("Weaver Leather", [5, 6, 7, 8]),
    "Tandy Pro": _spi("Tandy Pro", [5, 6, 7]),
    # French tradition -- points per inch (finer at the high end).
    "Blanchard": _spi("Blanchard", [8, 9, 10, 11, 12]),
}


def brands() -> List[str]:
    """Maker names in catalogue order."""
    return list(CATALOG.keys())


def irons_for(brand: str) -> List[Iron]:
    """The irons a maker sells (empty list if unknown)."""
    return CATALOG.get(brand, [])


def all_irons() -> List[Iron]:
    """Every catalogued iron, flattened."""
    return [iron for irons in CATALOG.values() for iron in irons]


def nearest(pitch_mm: float, tol: float = 0.03) -> Optional[Iron]:
    """The catalogued iron whose pitch matches ``pitch_mm`` within ``tol`` mm
    (used to label a saved pitch), or None if nothing is close."""
    best, best_d = None, tol
    for iron in all_irons():
        d = abs(iron.pitch_mm - pitch_mm)
        if d <= best_d:
            best, best_d = iron, d
    return best


def get(name: str) -> Iron:
    if name in PRESETS:
        return PRESETS[name]
    raise KeyError(f"unknown iron preset {name!r}; available: {sorted(PRESETS)}")

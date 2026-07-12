"""Pricking iron / stitching chisel pitch helpers.

Irons are sold two ways:
  * by pitch in millimetres (European makers -- e.g. 3.0, 3.38, 3.85, 4.0 mm)
  * by SPI, stitches per inch (mostly US/harness work -- e.g. 5, 6, 7 SPI)

``pitch_mm = 25.4 / spi``. These helpers keep the two straight and provide a
few common presets so you don't have to look them up.
"""

from __future__ import annotations

from dataclasses import dataclass

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

    @property
    def spi(self) -> float:
        return mm_to_spi(self.pitch_mm)

    @classmethod
    def from_spi(cls, spi: float, name: str | None = None) -> "Iron":
        return cls(name or f"{spi} SPI", spi_to_mm(spi))


# A handful of commonly sold pitches. Not brand-exhaustive -- just convenient.
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


def get(name: str) -> Iron:
    if name in PRESETS:
        return PRESETS[name]
    raise KeyError(f"unknown iron preset {name!r}; available: {sorted(PRESETS)}")

"""Per-shape / per-seam stitching configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StitchSettings:
    enabled: bool = True
    pitch_mm: float = 3.85          # the pricking-iron pitch
    mode: str = "chord"             # "chord" (iron-accurate) or "arclength"
    fit: str = "auto"               # auto / endpoints / closed / none
    max_dev: float = 0.12           # max fractional pitch nudge when fitting
    inset: float = 3.5              # stitch-line distance in from the edge (mm)
    # which real stitching punch this piece is cut for. ``punch_style`` picks
    # the catalogue cascade (style -> maker -> size) and the hole SHAPE; it maps
    # to the low-level ``hole_style`` render/export primitive as
    # round->round, oblique/french->slit, diamond->diamond.
    punch_style: str = "round"      # round | oblique | french | diamond
    punch_brand: str = ""           # maker label (free text; catalogue-driven)
    # hole appearance for render + export (the primitive geometry)
    hole_style: str = "round"       # "round" | "slit" | "diamond"
    hole_diameter: float = 1.0      # mm (round holes)
    slit_length: float = 1.6        # mm (slit / diamond length)
    slit_angle: float = 30.0        # deg off tangent (slit / diamond slant)
    # where hole marching starts around a closed loop (mm along the inset
    # outline). Keeping this equal between two pieces guarantees identical
    # holes -> perfect registration.
    start_offset: float = 0.0
    # saddle stitch: 2 -> a second parallel row of holes (aligned rungs)
    rows: int = 1
    row_spacing: float = 3.0        # mm between the two rows
    # backstitch reinforcement zone at each open-seam end (marker only; you
    # sew back through these existing holes, so no extra holes are cut).
    backstitch: int = 0
    # force flip-symmetric holes so a flipped piece lines up back-to-back:
    # "none" | "vertical" (mirror left<->right) | "horizontal" (top<->bottom).
    symmetry: str = "none"
    # how holes sit on a rounded corner's arc. Every rounded corner is always
    # its own span, so the holes are symmetric about the arc midpoint either
    # way; this only forces the parity:
    #   "auto"     -> whichever count best matches the iron pitch (symmetric)
    #   "midpoint" -> a hole exactly on the arc midpoint (even count)
    #   "straddle" -> an even pair straddling the midpoint, none on it (odd)
    corner_style: str = "auto"

    def iron_label(self) -> str:
        from .irons import mm_to_spi
        return f"{self.pitch_mm:.2f} mm ({mm_to_spi(self.pitch_mm):.1f} SPI)"

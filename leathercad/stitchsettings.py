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
    # hole appearance for export
    hole_style: str = "round"       # "round" or "slit"
    hole_diameter: float = 1.0      # mm (round holes)
    slit_length: float = 1.6        # mm (slit holes)
    slit_angle: float = 30.0        # deg off tangent (diamond-awl slant)
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

    def iron_label(self) -> str:
        from .irons import mm_to_spi
        return f"{self.pitch_mm:.2f} mm ({mm_to_spi(self.pitch_mm):.1f} SPI)"

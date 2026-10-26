"""Per-shape / per-seam stitching configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StitchSettings:
    enabled: bool = True
    pitch_mm: float = 3.85          # the pricking-iron pitch
    mode: str = "chord"             # "chord" (iron-accurate) or "arclength"
    # "auto": whole holes on every corner and both ends (the pitch is nudged
    # to the nearest count that lands them). "none": march at the exact pitch
    # and let the last gap fall where it may. ("endpoints" / "closed" are
    # legacy spellings of "auto" -- the engine reads the path's closedness.)
    fit: str = "auto"
    # Legacy; no effect. Fitting always takes the whole-hole count nearest
    # the pitch, so a tolerance cannot change the result. Kept so old files
    # and callers still load.
    max_dev: float = 0.12
    inset: float = 3.5              # stitch-line distance in from the edge (mm)
    # which real stitching punch this piece is cut for. ``punch_style`` picks
    # the hole SHAPE and maps to the low-level ``hole_style`` render/export
    # primitive as round->round, oblique/french->slit, diamond->diamond.
    punch_style: str = "round"      # round | oblique | french | diamond
    # hole appearance for render + export (the primitive geometry)
    hole_style: str = "round"       # "round" | "slit" | "diamond"
    hole_diameter: float = 1.0      # mm (round holes)
    slit_length: float = 1.6        # mm (slit / diamond length)
    slit_angle: float = 30.0        # deg off tangent (slit / diamond slant)
    # where an UNFITTED march (fit == "none") starts around a closed loop (mm
    # along the inset outline). A fitted outline anchors on its corners, so
    # its holes are the same whatever the start; this only moves the one
    # floating gap of an exact-pitch march.
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

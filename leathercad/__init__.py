"""leathercad -- a Python CAD core for laser-cut leather patterns.

The defining feature is pricking-iron-accurate stitch hole spacing: holes are
placed at a constant *chord* (straight-line) distance, exactly like the rigid
teeth of a physical iron, instead of the arc-length spacing most tools use.

See ``leathercad.stitching`` for the why-and-how.
"""

from .geometry import Vec2, distance
from .path import Path, PathBuilder, Line, Arc, CubicBezier, QuadraticBezier
from .stitching import (
    Polyline,
    Hole,
    StitchResult,
    stitch_path,
    march_chord,
    march_arclength,
    polyline_from_path,
)
from .irons import Iron, spi_to_mm, mm_to_spi, get as get_iron, PRESETS
from .svg import SvgDocument, export_svg

__version__ = "0.1.0"

__all__ = [
    "Vec2", "distance",
    "Path", "PathBuilder", "Line", "Arc", "CubicBezier", "QuadraticBezier",
    "Polyline", "Hole", "StitchResult", "stitch_path", "march_chord",
    "march_arclength", "polyline_from_path",
    "Iron", "spi_to_mm", "mm_to_spi", "get_iron", "PRESETS",
    "SvgDocument", "export_svg",
    "__version__",
]

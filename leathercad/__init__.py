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
    stitch_polyline,
    march_chord,
    march_arclength,
    polyline_from_path,
    flip_symmetry,
    holes_for_shape,
)
from .stitchsettings import StitchSettings
from .irons import (Punch, spi_to_mm, mm_to_spi, geometry_for, STYLES,
                    brands_for, punches_for)
from .offset import offset_closed_inward
from .shapes import (Transform, Shape, Rectangle, Ellipse, Circle, Polygon,
                     PathShape, EditablePath, Edge, arc_through)
from .layers import Layer, default_layers, CUT, SCORE, ENGRAVE, STITCH
from .stitchline import StitchLine
from .holes import LooseHole
from .document import Document
from .svg import SvgDocument
from .trim import trim, intersection_arclengths
from . import export

__version__ = "0.2.0"

__all__ = [
    "Vec2", "distance",
    "Path", "PathBuilder", "Line", "Arc", "CubicBezier", "QuadraticBezier",
    "Polyline", "Hole", "StitchResult", "stitch_path", "stitch_polyline",
    "march_chord", "march_arclength", "polyline_from_path",
    "flip_symmetry", "holes_for_shape",
    "StitchSettings",
    "Punch", "spi_to_mm", "mm_to_spi", "geometry_for", "STYLES",
    "brands_for", "punches_for",
    "offset_closed_inward",
    "Transform", "Shape", "Rectangle", "Ellipse", "Circle", "Polygon",
    "PathShape", "EditablePath", "Edge", "arc_through",
    "Layer", "default_layers", "CUT", "SCORE", "ENGRAVE", "STITCH",
    "StitchLine", "LooseHole", "Document",
    "SvgDocument", "trim", "intersection_arclengths", "export",
    "__version__",
]

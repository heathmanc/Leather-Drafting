"""SVG export sized in real millimetres for laser cutting.

Two layers are emitted with distinct stroke colours so they map to laser jobs:
  * ``cut``   -- the pattern outline(s)            (red   hairline)
  * ``holes`` -- the stitch holes                  (blue)

Stitch holes render either as round holes (a circle of a chosen diameter) or as
slanted slits oriented to the local path tangent -- the diamond-awl look. The
document uses ``mm`` units and a 1:1 user-unit-to-mm mapping, and flips Y so the
drawing reads the same as your CAD view (Y up).
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

from .geometry import Vec2
from .stitching import StitchResult


CUT_COLOR = "#ff0000"
HOLE_COLOR = "#0066ff"
HAIRLINE = 0.05  # mm


def _bbox(points: Sequence[Vec2]) -> Tuple[float, float, float, float]:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


class SvgDocument:
    def __init__(self, margin: float = 5.0):
        self.margin = margin
        self._cut_polylines: List[List[Vec2]] = []
        self._holes: List[Tuple[Vec2, Vec2]] = []  # (point, tangent)
        self._hole_diameter = 1.0
        self._slit_length: Optional[float] = None
        self._slit_angle_deg = 0.0

    def add_cut_polyline(self, points: Sequence[Vec2]) -> "SvgDocument":
        self._cut_polylines.append(list(points))
        return self

    def add_stitches(self, result: StitchResult, *, hole_diameter: float = 1.0,
                     slit_length: Optional[float] = None,
                     slit_angle_deg: float = 0.0) -> "SvgDocument":
        """Add stitch holes.

        ``slit_length`` None -> round holes of ``hole_diameter``.
        ``slit_length`` set  -> slit holes of that length, rotated
        ``slit_angle_deg`` off the path tangent (diamond-awl slant).
        """
        self._hole_diameter = hole_diameter
        self._slit_length = slit_length
        self._slit_angle_deg = slit_angle_deg
        for h in result.holes:
            self._holes.append((h.point, h.tangent))
        return self

    # -- rendering ------------------------------------------------------
    def to_string(self) -> str:
        all_pts: List[Vec2] = []
        for pl in self._cut_polylines:
            all_pts.extend(pl)
        all_pts.extend(p for p, _ in self._holes)
        if not all_pts:
            return "<svg xmlns='http://www.w3.org/2000/svg'></svg>"

        minx, miny, maxx, maxy = _bbox(all_pts)
        minx -= self.margin
        miny -= self.margin
        maxx += self.margin
        maxy += self.margin
        w = maxx - minx
        h = maxy - miny

        def X(x: float) -> float:
            return x - minx

        def Y(y: float) -> float:
            return maxy - y  # flip so Y points up in source coords

        out: List[str] = []
        out.append(
            f"<svg xmlns='http://www.w3.org/2000/svg' version='1.1' "
            f"width='{_fmt(w)}mm' height='{_fmt(h)}mm' "
            f"viewBox='0 0 {_fmt(w)} {_fmt(h)}'>")
        out.append(
            f"  <!-- leathercad export. Units: mm. 1 user unit = 1 mm. -->")

        # cut layer
        out.append(f"  <g id='cut' fill='none' stroke='{CUT_COLOR}' "
                   f"stroke-width='{HAIRLINE}'>")
        for pl in self._cut_polylines:
            if len(pl) < 2:
                continue
            d = "M " + " L ".join(f"{_fmt(X(p.x))} {_fmt(Y(p.y))}" for p in pl)
            out.append(f"    <path d='{d}' />")
        out.append("  </g>")

        # holes layer
        out.append(f"  <g id='holes' fill='none' stroke='{HOLE_COLOR}' "
                   f"stroke-width='{HAIRLINE}'>")
        if self._slit_length is None:
            r = self._hole_diameter / 2.0
            for p, _ in self._holes:
                out.append(
                    f"    <circle cx='{_fmt(X(p.x))}' cy='{_fmt(Y(p.y))}' "
                    f"r='{_fmt(r)}' />")
        else:
            half = self._slit_length / 2.0
            slant = math.radians(self._slit_angle_deg)
            for p, tan in self._holes:
                d = tan.rotate(slant)
                a = p - d * half
                b = p + d * half
                out.append(
                    f"    <line x1='{_fmt(X(a.x))}' y1='{_fmt(Y(a.y))}' "
                    f"x2='{_fmt(X(b.x))}' y2='{_fmt(Y(b.y))}' "
                    f"stroke-linecap='round' />")
        out.append("  </g>")

        out.append("</svg>")
        return "\n".join(out)

    def save(self, filename: str) -> None:
        with open(filename, "w", encoding="utf-8") as fh:
            fh.write(self.to_string())


def export_svg(filename: str, cut_polylines: Iterable[Sequence[Vec2]],
               stitches: StitchResult, *, hole_diameter: float = 1.0,
               slit_length: Optional[float] = None,
               slit_angle_deg: float = 0.0, margin: float = 5.0) -> None:
    """Convenience one-shot export."""
    doc = SvgDocument(margin=margin)
    for pl in cut_polylines:
        doc.add_cut_polyline(pl)
    doc.add_stitches(stitches, hole_diameter=hole_diameter,
                     slit_length=slit_length, slit_angle_deg=slit_angle_deg)
    doc.save(filename)

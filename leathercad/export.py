"""Export a Document to laser-ready SVG or DXF.

Both formats are millimetre-accurate. Geometry is grouped by layer so the cutter
can map colour -> operation (cut / score / engrave), and stitch holes render as
round holes or slanted slits per each item's stitch settings.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2
from .document import Document
from .stitching import StitchResult, stitch_polyline


# ---------------------------------------------------------------------------
# shared: collect drawable geometry from a document
# ---------------------------------------------------------------------------
def _layer_color(doc: Document, name: str, default: str) -> str:
    lyr = doc.layer(name)
    return lyr.color if lyr else default


def collect(doc: Document):
    """Return (outlines, stitch_results) in world coordinates.

    outlines: list of (points, color)
    stitch_results: list of (StitchResult, settings, color)
    """
    outlines: List[Tuple[List[Vec2], str]] = []
    stitches: List[Tuple[StitchResult, object, str]] = []
    stitch_color = _layer_color(doc, "Stitch", "#0066ff")

    from .stitching import Hole, holes_for_shape
    from .stitchsettings import StitchSettings

    for sh in doc.shapes:
        if getattr(sh, "construction", False):
            continue                      # guides are references, never cut
        lyr = doc.layer(sh.layer)
        if lyr is not None and not lyr.visible:
            continue
        color = lyr.color if lyr else "#ff0000"
        pts, corners, closed = sh.world_polyline()
        outlines.append((pts, color))
        res = holes_for_shape(sh)
        if res.count:
            style = sh.stitch or StitchSettings()
            stitches.append((res, style, stitch_color))

    for sl in doc.stitch_lines:
        res = sl.result()
        if res.count:
            stitches.append((res, sl.settings, stitch_color))

    # individual (ungrouped) holes
    for h in getattr(doc, "holes", []):
        res = StitchResult(holes=[Hole(h.point, h.tangent)])
        stitches.append((res, h, stitch_color))  # LooseHole has hole_* fields
    return outlines, stitches


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _bbox_all(outlines, stitches):
    pts: List[Vec2] = []
    for pl, _ in outlines:
        pts.extend(pl)
    for res, _, _ in stitches:
        pts.extend(res.points)
    if not pts:
        return 0.0, 0.0, 100.0, 100.0
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def export_svg(doc: Document, path: str, *, margin: float = 6.0,
               hairline: float = 0.05) -> None:
    outlines, stitches = collect(doc)
    minx, miny, maxx, maxy = _bbox_all(outlines, stitches)
    minx -= margin; miny -= margin; maxx += margin; maxy += margin
    w = maxx - minx
    h = maxy - miny

    def X(x):
        return x - minx

    def Y(y):
        return maxy - y

    out: List[str] = []
    out.append(
        f"<svg xmlns='http://www.w3.org/2000/svg' version='1.1' "
        f"width='{_fmt(w)}mm' height='{_fmt(h)}mm' "
        f"viewBox='0 0 {_fmt(w)} {_fmt(h)}'>")
    out.append("  <!-- leathercad export. Units: mm. 1 user unit = 1 mm. -->")

    out.append("  <g id='outlines' fill='none'>")
    for pl, color in outlines:
        if len(pl) < 2:
            continue
        d = "M " + " L ".join(f"{_fmt(X(p.x))} {_fmt(Y(p.y))}" for p in pl)
        out.append(f"    <path d='{d}' stroke='{color}' "
                   f"stroke-width='{hairline}' />")
    out.append("  </g>")

    out.append("  <g id='stitches' fill='none'>")
    for res, settings, color in stitches:
        if settings.hole_style == "slit":
            half = settings.slit_length / 2.0
            slant = math.radians(settings.slit_angle)
            for hle in res.holes:
                d = hle.tangent.rotate(slant)
                a = hle.point - d * half
                b = hle.point + d * half
                out.append(
                    f"    <line x1='{_fmt(X(a.x))}' y1='{_fmt(Y(a.y))}' "
                    f"x2='{_fmt(X(b.x))}' y2='{_fmt(Y(b.y))}' "
                    f"stroke='{color}' stroke-width='{hairline}' "
                    f"stroke-linecap='round' />")
        else:
            r = settings.hole_diameter / 2.0
            for hle in res.holes:
                out.append(
                    f"    <circle cx='{_fmt(X(hle.point.x))}' "
                    f"cy='{_fmt(Y(hle.point.y))}' r='{_fmt(r)}' "
                    f"stroke='{color}' stroke-width='{hairline}' />")
    out.append("  </g>")
    out.append("</svg>")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# ---------------------------------------------------------------------------
# DXF (minimal R12 -- widely accepted by laser software)
# ---------------------------------------------------------------------------
_ACI = {  # hex -> AutoCAD Color Index (nearest of the standard 7)
    "#ff0000": 1, "#ffff00": 2, "#00ff00": 3, "#00ffff": 4,
    "#0000ff": 5, "#ff00ff": 6, "#ffffff": 7, "#0066ff": 5,
    "#00aa00": 3, "#888888": 8,
}


def _aci(color: str) -> int:
    return _ACI.get(color.lower(), 7)


def _dxf_pair(code: int, value) -> str:
    return f"{code}\n{value}\n"


def export_dxf(doc: Document, path: str) -> None:
    outlines, stitches = collect(doc)

    # gather layer names/colors actually used
    used = {}
    for _, color in outlines:
        used.setdefault(color, _aci(color))
    stitch_color = _layer_color(doc, "Stitch", "#0066ff")
    used.setdefault(stitch_color, _aci(stitch_color))

    s = []
    s.append(_dxf_pair(0, "SECTION"))
    s.append(_dxf_pair(2, "TABLES"))
    s.append(_dxf_pair(0, "TABLE"))
    s.append(_dxf_pair(2, "LAYER"))
    s.append(_dxf_pair(70, len(used)))
    for i, (color, aci) in enumerate(used.items()):
        s.append(_dxf_pair(0, "LAYER"))
        s.append(_dxf_pair(2, f"L{aci}"))
        s.append(_dxf_pair(70, 0))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(6, "CONTINUOUS"))
    s.append(_dxf_pair(0, "ENDTAB"))
    s.append(_dxf_pair(0, "ENDSEC"))

    s.append(_dxf_pair(0, "SECTION"))
    s.append(_dxf_pair(2, "ENTITIES"))

    def line(a: Vec2, b: Vec2, aci: int):
        s.append(_dxf_pair(0, "LINE"))
        s.append(_dxf_pair(8, f"L{aci}"))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(10, f"{a.x:.4f}"))
        s.append(_dxf_pair(20, f"{a.y:.4f}"))
        s.append(_dxf_pair(11, f"{b.x:.4f}"))
        s.append(_dxf_pair(21, f"{b.y:.4f}"))

    def circle(c: Vec2, r: float, aci: int):
        s.append(_dxf_pair(0, "CIRCLE"))
        s.append(_dxf_pair(8, f"L{aci}"))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(10, f"{c.x:.4f}"))
        s.append(_dxf_pair(20, f"{c.y:.4f}"))
        s.append(_dxf_pair(40, f"{r:.4f}"))

    for pl, color in outlines:
        aci = _aci(color)
        for i in range(len(pl) - 1):
            line(pl[i], pl[i + 1], aci)

    saci = _aci(stitch_color)
    for res, settings, _ in stitches:
        if settings.hole_style == "slit":
            half = settings.slit_length / 2.0
            slant = math.radians(settings.slit_angle)
            for hle in res.holes:
                d = hle.tangent.rotate(slant)
                line(hle.point - d * half, hle.point + d * half, saci)
        else:
            r = settings.hole_diameter / 2.0
            for hle in res.holes:
                circle(hle.point, r, saci)

    s.append(_dxf_pair(0, "ENDSEC"))
    s.append(_dxf_pair(0, "EOF"))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(s))

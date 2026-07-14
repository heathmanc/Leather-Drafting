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
from .holes import diamond_points
from .irons import DIAMOND_WIDTH_RATIO
from .stitching import StitchResult, stitch_polyline


# ---------------------------------------------------------------------------
# shared: collect drawable geometry from a document
# ---------------------------------------------------------------------------
def _layer_color(doc: Document, name: str, default: str) -> str:
    lyr = doc.layer(name)
    return lyr.color if lyr else default


def collect(doc: Document, kerf: float = 0.0):
    """Return (outlines, stitch_results) in world coordinates.

    outlines: list of (points, color)
    stitch_results: list of (StitchResult, settings, color)

    ``kerf`` > 0 applies laser-kerf compensation to closed *cut* outlines:
    outer boundaries grow by kerf/2 and nested cutouts (a slot fully inside a
    strap) shrink by kerf/2, so cut pieces come out drawn-size. Score/engrave
    geometry and open paths are never offset.
    """
    outlines: List[Tuple[List[Vec2], str]] = []
    cuttable: List[bool] = []            # closed + on a cut layer (kerf targets)
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
        role = getattr(lyr, "role", "cut") if lyr else "cut"
        pts, corners, closed = sh.world_polyline()
        outlines.append((pts, color))
        cuttable.append(bool(closed) and role == "cut" and len(pts) >= 4)
        res = holes_for_shape(sh)
        if res.count:
            style = sh.stitch or StitchSettings()
            stitches.append((res, style, stitch_color))

    for tx in getattr(doc, "texts", []):
        lyr = doc.layer(tx.layer)
        if lyr is not None and not lyr.visible:
            continue
        color = lyr.color if lyr else "#888888"
        for contour in tx.world_contours():
            if len(contour) >= 2:
                outlines.append((list(contour) + [contour[0]], color))
                cuttable.append(False)    # lettering is engraved, never offset

    if kerf and kerf > 1e-9:
        outlines = _apply_kerf(outlines, cuttable, kerf)

    for sl in doc.stitch_lines:
        res = sl.result()
        if res.count:
            stitches.append((res, sl.settings, stitch_color))

    # individual (ungrouped) holes
    for h in getattr(doc, "holes", []):
        res = StitchResult(holes=[Hole(h.point, h.tangent)])
        stitches.append((res, h, stitch_color))  # LooseHole has hole_* fields
    return outlines, stitches


def _apply_kerf(outlines, cuttable, kerf: float):
    """Offset closed cut outlines by half the kerf, direction by nesting.

    A closed cut outline whose sampled vertices all sit strictly inside an odd
    number of other closed cut outlines is a *cutout* (buckle slot, hardware
    hole) and shrinks; everything else is an outer boundary and grows. Assumes
    a physical cut layout (pieces side by side, cutouts nested inside their
    piece) -- overlapping pieces parked on top of each other for fit-checking
    aren't a cuttable layout in the first place.
    """
    from .geometry import point_in_polygon
    from .offset import offset_closed

    rings = [i for i, cut in enumerate(cuttable) if cut]
    out = list(outlines)
    for i in rings:
        pts_i = outlines[i][0]
        ring_i = pts_i[:-1]
        step = max(1, len(ring_i) // 12)
        sample = ring_i[::step]
        depth = 0
        for j in rings:
            if j == i:
                continue
            ring_j = outlines[j][0][:-1]
            if all(point_in_polygon(p, ring_j) for p in sample):
                depth += 1
        d = kerf / 2.0 if depth % 2 == 0 else -kerf / 2.0
        out[i] = (offset_closed(pts_i, d), outlines[i][1])
    return out


def _resolve_kerf(doc: Document, kerf) -> float:
    return float(getattr(doc, "kerf", 0.0) or 0.0) if kerf is None else float(kerf)


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
               hairline: float = 0.05, kerf: float | None = None) -> None:
    k = _resolve_kerf(doc, kerf)
    outlines, stitches = collect(doc, kerf=k)
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
        if settings.hole_style == "diamond":
            # the beam enlarges the hole: shrink the length by ~a kerf
            length = max(0.1, settings.slit_length - k)
            for hle in res.holes:
                pts = diamond_points(hle.point, hle.tangent, length,
                                     settings.slit_angle, DIAMOND_WIDTH_RATIO)
                pstr = " ".join(f"{_fmt(X(p.x))},{_fmt(Y(p.y))}" for p in pts)
                out.append(
                    f"    <polygon points='{pstr}' stroke='{color}' "
                    f"stroke-width='{hairline}' />")
        elif settings.hole_style == "slit":
            # the beam widens/lengthens the slit by ~a kerf: cut it shorter
            half = max(0.05, (settings.slit_length - k) / 2.0)
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
            # the beam enlarges holes: cut a smaller circle so the finished
            # hole comes out at the drawn diameter
            r = max(0.05, (settings.hole_diameter - k) / 2.0)
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


def export_dxf(doc: Document, path: str, *, kerf: float | None = None) -> None:
    k = _resolve_kerf(doc, kerf)
    outlines, stitches = collect(doc, kerf=k)

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

    def polyline(pts: Sequence[Vec2], aci: int, closed: bool):
        # A single (R12) POLYLINE per outline so importers see one connected
        # contour -- and, when closed, a genuine closed loop (needed for correct
        # cut ordering / fill in LightBurn, Illustrator, etc.) rather than a heap
        # of disconnected LINE segments.
        s.append(_dxf_pair(0, "POLYLINE"))
        s.append(_dxf_pair(8, f"L{aci}"))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(66, 1))                 # vertices-follow flag
        s.append(_dxf_pair(70, 1 if closed else 0))
        for p in pts:
            s.append(_dxf_pair(0, "VERTEX"))
            s.append(_dxf_pair(8, f"L{aci}"))
            s.append(_dxf_pair(10, f"{p.x:.4f}"))
            s.append(_dxf_pair(20, f"{p.y:.4f}"))
        s.append(_dxf_pair(0, "SEQEND"))

    for pl, color in outlines:
        if len(pl) < 2:
            continue
        aci = _aci(color)
        closed = len(pl) > 2 and (pl[0] - pl[-1]).length() < 1e-6
        polyline(pl[:-1] if closed else pl, aci, closed)

    saci = _aci(stitch_color)
    for res, settings, _ in stitches:
        if settings.hole_style == "diamond":
            length = max(0.1, settings.slit_length - k)
            for hle in res.holes:
                pts = diamond_points(hle.point, hle.tangent, length,
                                     settings.slit_angle, DIAMOND_WIDTH_RATIO)
                polyline(pts, saci, True)
        elif settings.hole_style == "slit":
            half = max(0.05, (settings.slit_length - k) / 2.0)
            slant = math.radians(settings.slit_angle)
            for hle in res.holes:
                d = hle.tangent.rotate(slant)
                line(hle.point - d * half, hle.point + d * half, saci)
        else:
            r = max(0.05, (settings.hole_diameter - k) / 2.0)
            for hle in res.holes:
                circle(hle.point, r, saci)

    s.append(_dxf_pair(0, "ENDSEC"))
    s.append(_dxf_pair(0, "EOF"))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(s))

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


def _wanted(doc, obj, selection, include, part):
    """Export filters: ``selection`` (ids to keep, or None for all) and
    ``include`` (which parts: 'outlines', 'holes', 'text')."""
    if include is not None and part not in include:
        return False
    if selection is not None:
        oid = getattr(obj, "shape_id", None) or getattr(obj, "text_id", None) \
            or getattr(obj, "line_id", None) or getattr(obj, "hole_id", None)
        if oid not in selection:
            return False
    return True


def collect(doc: Document, kerf: float = 0.0, selection=None, include=None):
    """Return (outlines, stitch_results) in world coordinates.

    ``selection`` -- ids of the objects to export (None = everything);
    ``include`` -- a set of 'outlines', 'holes', 'text' (None = all three).

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

    # What's exported is what's shown: an outline goes with its own layer and
    # its stitch holes with the Stitch layer (the canvas's rule), so hiding Cut
    # exports just the holes and hiding Stitch just the outlines.
    def shown(name):
        lyr = doc.layer(name)
        return lyr is None or lyr.visible
    holes_shown = shown("Stitch")

    for sh in doc.shapes:
        if getattr(sh, "construction", False):
            continue                      # guides are references, never cut
        lyr = doc.layer(sh.layer)
        if (lyr is None or lyr.visible) and _wanted(doc, sh, selection,
                                                   include, "outlines"):
            color = lyr.color if lyr else "#ff0000"
            role = getattr(lyr, "role", "cut") if lyr else "cut"
            pts, corners, closed = sh.world_polyline()
            outlines.append((pts, color))
            cuttable.append(bool(closed) and role == "cut" and len(pts) >= 4)
        res = (holes_for_shape(sh, doc)
               if holes_shown and _wanted(doc, sh, selection, include, "holes")
               else None)
        if res is not None and res.count:
            style = sh.stitch or StitchSettings()
            # a mirrored piece reverses the slant sense of slit/diamond holes;
            # negate the exported angle so the lasered back registers with the
            # front (round holes are unaffected)
            if sh.transform.mirror_x and style.hole_style in ("slit", "diamond"):
                from dataclasses import replace
                style = replace(style, slit_angle=-style.slit_angle)
            stitches.append((res, style, stitch_color))

    for tx in getattr(doc, "texts", []):
        lyr = doc.layer(tx.layer)
        if lyr is not None and not lyr.visible:
            continue
        if not _wanted(doc, tx, selection, include, "text"):
            continue
        color = lyr.color if lyr else "#888888"
        for contour in tx.world_contours():
            if len(contour) >= 2:
                outlines.append((list(contour) + [contour[0]], color))
                cuttable.append(False)    # lettering is engraved, never offset

    if kerf and kerf > 1e-9:
        outlines = _apply_kerf(outlines, cuttable, kerf)

    for sl in doc.stitch_lines:
        if not shown(sl.layer) or not _wanted(doc, sl, selection, include,
                                              "holes"):
            continue
        from .hardware import keepouts_near
        res = sl.result(keepouts_near(sl.points, doc))
        if res.count:
            stitches.append((res, sl.settings, stitch_color))

    # individual (ungrouped) holes
    for h in getattr(doc, "holes", []):
        if not shown(h.layer) or not _wanted(doc, h, selection, include,
                                             "holes"):
            continue
        res = StitchResult(holes=[Hole(h.point, h.tangent)])
        stitches.append((res, h, stitch_color))  # LooseHole has hole_* fields
    return outlines, stitches


def collect_paths(doc: Document, selection=None, include=None):
    """The visible outlines as TRUE segments in world coordinates -- for an
    export without kerf, so a laser gets real arcs instead of chords:
    ``[(segments, color, closed)]`` where a segment is ``("line", a, b)``,
    ``("arc", a, mid, b)`` or ``("bezier", a, c1, c2, b)``. Text contours
    come as line segments. Same order and filters as ``collect``."""
    from .path import Arc, CubicBezier, QuadraticBezier
    from .shapes import Circle
    out = []
    for sh in doc.shapes:
        if getattr(sh, "construction", False):
            continue
        lyr = doc.layer(sh.layer)
        if lyr is not None and not lyr.visible:
            continue
        if not _wanted(doc, sh, selection, include, "outlines"):
            continue
        color = lyr.color if lyr else "#ff0000"
        t = sh.transform
        segs = []
        if isinstance(sh, Circle) and abs(sh.rx - sh.ry) < 1e-9:
            r = sh.rx
            a, m1, b, m2 = (t.apply(Vec2(r, 0)), t.apply(Vec2(0, r)),
                            t.apply(Vec2(-r, 0)), t.apply(Vec2(0, -r)))
            segs = [("arc", a, m1, b), ("arc", b, m2, a)]
            closed = True
        else:
            path = sh.local_path()
            closed = path.closed
            for sg in path.segments:
                if isinstance(sg, Arc):
                    mid = sg._point(sg.a0 + sg._sweep() / 2.0)
                    segs.append(("arc", t.apply(sg.start()), t.apply(mid),
                                 t.apply(sg.end())))
                elif isinstance(sg, (CubicBezier, QuadraticBezier)):
                    cb = sg.to_cubic() if isinstance(sg, QuadraticBezier) else sg
                    segs.append(("bezier", t.apply(cb.p0), t.apply(cb.p1),
                                 t.apply(cb.p2), t.apply(cb.p3)))
                else:
                    segs.append(("line", t.apply(sg.start()), t.apply(sg.end())))
            if closed and segs and (segs[-1][-1] - segs[0][1]).length() > 1e-9:
                segs.append(("line", segs[-1][-1], segs[0][1]))
        if segs:
            out.append((segs, color, closed))
    for tx in getattr(doc, "texts", []):
        lyr = doc.layer(tx.layer)
        if lyr is not None and not lyr.visible:
            continue
        if not _wanted(doc, tx, selection, include, "text"):
            continue
        color = lyr.color if lyr else "#888888"
        for contour in tx.world_contours():
            if len(contour) >= 2:
                pts = list(contour) + [contour[0]]
                out.append(([("line", a, b) for a, b in zip(pts, pts[1:])],
                            color, True))
    return out


def _arc_params(a: Vec2, mid: Vec2, b: Vec2):
    """(centre, radius, sweep in radians, ccw) of the arc a -> b through
    mid, or None when the three points are collinear."""
    from .shapes import arc_through
    arc = arc_through(a, mid, b)
    if arc is None:
        return None
    c, r, a0, a1, ccw = arc
    two_pi = 2.0 * math.pi
    sweep = (a1 - a0) % two_pi if ccw else (a0 - a1) % two_pi
    return c, r, sweep, ccw


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

    def bbox(pts):
        return (min(p.x for p in pts), min(p.y for p in pts),
                max(p.x for p in pts), max(p.y for p in pts))

    boxes = {i: bbox(outlines[i][0]) for i in rings if outlines[i][0]}
    for i in rings:
        pts_i = outlines[i][0]
        ring_i = pts_i[:-1]
        step = max(1, len(ring_i) // 12)
        sample = ring_i[::step]
        depth = 0
        # the box of the SAMPLED vertices (the ones the containment test
        # looks at), so the prefilter never rules out a pair the full test
        # would have accepted
        bi = bbox(sample) if sample else None
        for j in rings:
            if j == i:
                continue
            bj = boxes.get(j)
            # a ring can only sit inside one whose box contains its box --
            # which rules out all but a handful of the N^2 pairs cheaply
            if (bi is None or bj is None or bi[0] < bj[0] or bi[1] < bj[1]
                    or bi[2] > bj[2] or bi[3] > bj[3]):
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
               hairline: float = 0.05, kerf: float | None = None,
               selection=None, include=None) -> None:
    """Write ``doc`` as a laser-ready SVG. Without kerf compensation the
    outlines are written as true arcs and curves (``A`` / ``C`` commands);
    with it, as the compensated polylines. ``selection`` / ``include``: see
    ``collect``."""
    k = _resolve_kerf(doc, kerf)
    outlines, stitches = collect(doc, kerf=k, selection=selection,
                                 include=include)
    exact = collect_paths(doc, selection, include) if k <= 1e-9 else None
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
    if exact is not None:
        for segs, color, closed in exact:
            d = [f"M {_fmt(X(segs[0][1].x))} {_fmt(Y(segs[0][1].y))}"]
            for sg in segs:
                if sg[0] == "arc":
                    prm = _arc_params(sg[1], sg[2], sg[3])
                    if prm is None:
                        d.append(f"L {_fmt(X(sg[3].x))} {_fmt(Y(sg[3].y))}")
                        continue
                    _c, r, sweep, ccw = prm
                    # Y is flipped on the page: a world-CCW arc still LOOKS
                    # counter-clockwise, which in SVG's y-down frame is the
                    # negative-angle direction (sweep-flag 0)
                    d.append(f"A {_fmt(r)} {_fmt(r)} 0 "
                             f"{1 if sweep > math.pi else 0} {0 if ccw else 1} "
                             f"{_fmt(X(sg[3].x))} {_fmt(Y(sg[3].y))}")
                elif sg[0] == "bezier":
                    d.append(f"C {_fmt(X(sg[2].x))} {_fmt(Y(sg[2].y))} "
                             f"{_fmt(X(sg[3].x))} {_fmt(Y(sg[3].y))} "
                             f"{_fmt(X(sg[4].x))} {_fmt(Y(sg[4].y))}")
                else:
                    d.append(f"L {_fmt(X(sg[2].x))} {_fmt(Y(sg[2].y))}")
            if closed:
                d.append("Z")
            out.append(f"    <path d='{' '.join(d)}' stroke='{color}' "
                       f"stroke-width='{hairline}' />")
    else:
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
_ACI_RGB = {1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255),
            5: (0, 0, 255), 6: (255, 0, 255), 7: (255, 255, 255),
            8: (128, 128, 128), 9: (192, 192, 192)}


def _aci(color: str) -> int:
    """The AutoCAD colour index for a hex colour: exact for the standard
    ones, else the nearest -- a custom red stays red rather than every custom
    colour collapsing to white."""
    c = color.lower()
    if c in _ACI:
        return _ACI[c]
    try:
        rgb = tuple(int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return 7
    return min(_ACI_RGB, key=lambda k: sum((a - b) ** 2 for a, b in
                                           zip(_ACI_RGB[k], rgb)))


def _dxf_layer_names(doc: Document, colors) -> dict:
    """colour -> DXF layer name, after the document layer of that colour (so
    two layers whose colours share a nearest ACI still arrive as separate
    layers the laser software can map to different operations)."""
    import re
    names, taken = {}, set()
    for color in colors:
        lyr = next((l for l in doc.layers
                    if l.color.lower() == color.lower()), None)
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", lyr.name).strip("_") \
            if lyr else ""
        name = base or f"L{_aci(color)}"
        while name in taken:
            name += "_"
        taken.add(name)
        names[color] = name
    return names


def _dxf_pair(code: int, value) -> str:
    return f"{code}\n{value}\n"


def export_dxf(doc: Document, path: str, *, kerf: float | None = None,
               selection=None, include=None) -> None:
    """Write ``doc`` as an R12 DXF. Without kerf compensation each outline
    is one POLYLINE whose arcs are bulge vertices (true arcs, one connected
    contour); with it, the compensated polylines."""
    k = _resolve_kerf(doc, kerf)
    outlines, stitches = collect(doc, kerf=k, selection=selection,
                                 include=include)
    exact = collect_paths(doc, selection, include) if k <= 1e-9 else None

    # gather layer names/colors actually used
    used = {}
    for _, color in outlines:
        used.setdefault(color, _aci(color))
    stitch_color = _layer_color(doc, "Stitch", "#0066ff")
    used.setdefault(stitch_color, _aci(stitch_color))
    lname = _dxf_layer_names(doc, used)

    s = []
    s.append(_dxf_pair(0, "SECTION"))
    s.append(_dxf_pair(2, "TABLES"))
    s.append(_dxf_pair(0, "TABLE"))
    s.append(_dxf_pair(2, "LAYER"))
    s.append(_dxf_pair(70, len(used)))
    for i, (color, aci) in enumerate(used.items()):
        s.append(_dxf_pair(0, "LAYER"))
        s.append(_dxf_pair(2, lname[color]))
        s.append(_dxf_pair(70, 0))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(6, "CONTINUOUS"))
    s.append(_dxf_pair(0, "ENDTAB"))
    s.append(_dxf_pair(0, "ENDSEC"))

    s.append(_dxf_pair(0, "SECTION"))
    s.append(_dxf_pair(2, "ENTITIES"))

    def line(a: Vec2, b: Vec2, color: str):
        aci = used[color]
        s.append(_dxf_pair(0, "LINE"))
        s.append(_dxf_pair(8, lname[color]))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(10, f"{a.x:.4f}"))
        s.append(_dxf_pair(20, f"{a.y:.4f}"))
        s.append(_dxf_pair(11, f"{b.x:.4f}"))
        s.append(_dxf_pair(21, f"{b.y:.4f}"))

    def circle(c: Vec2, r: float, color: str):
        aci = used[color]
        s.append(_dxf_pair(0, "CIRCLE"))
        s.append(_dxf_pair(8, lname[color]))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(10, f"{c.x:.4f}"))
        s.append(_dxf_pair(20, f"{c.y:.4f}"))
        s.append(_dxf_pair(40, f"{r:.4f}"))

    def polyline(pts: Sequence[Vec2], color: str, closed: bool, bulges=None):
        # A single (R12) POLYLINE per outline so importers see one connected
        # contour -- and, when closed, a genuine closed loop (needed for correct
        # cut ordering / fill in LightBurn, Illustrator, etc.) rather than a heap
        # of disconnected LINE segments. ``bulges[i]`` (tan of a quarter of
        # the sweep, + for CCW) makes the edge leaving vertex i a true arc.
        aci = used[color]
        s.append(_dxf_pair(0, "POLYLINE"))
        s.append(_dxf_pair(8, lname[color]))
        s.append(_dxf_pair(62, aci))
        s.append(_dxf_pair(66, 1))                 # vertices-follow flag
        s.append(_dxf_pair(70, 1 if closed else 0))
        for i, p in enumerate(pts):
            s.append(_dxf_pair(0, "VERTEX"))
            s.append(_dxf_pair(8, lname[color]))
            s.append(_dxf_pair(10, f"{p.x:.4f}"))
            s.append(_dxf_pair(20, f"{p.y:.4f}"))
            if bulges and abs(bulges[i]) > 1e-12:
                s.append(_dxf_pair(42, f"{bulges[i]:.6f}"))
        s.append(_dxf_pair(0, "SEQEND"))

    def exact_polyline(segs, color, closed):
        pts, bulges = [], []
        for sg in segs:
            if sg[0] == "arc":
                prm = _arc_params(sg[1], sg[2], sg[3])
                pts.append(sg[1])
                if prm is None:
                    bulges.append(0.0)
                else:
                    _c, _r, sweep, ccw = prm
                    bulges.append(math.tan(sweep / 4.0) * (1 if ccw else -1))
            elif sg[0] == "bezier":
                from .path import CubicBezier
                fl = [sg[1]] + CubicBezier(sg[1], sg[2], sg[3], sg[4]).flatten(0.02)
                for p in fl[:-1]:
                    pts.append(p)
                    bulges.append(0.0)
            else:
                pts.append(sg[1])
                bulges.append(0.0)
        if not closed:
            pts.append(segs[-1][-1])
            bulges.append(0.0)
        polyline(pts, color, closed, bulges)

    if exact is not None:
        for segs, color, closed in exact:
            exact_polyline(segs, color, closed)
    else:
        for pl, color in outlines:
            if len(pl) < 2:
                continue
            closed = len(pl) > 2 and (pl[0] - pl[-1]).length() < 1e-6
            polyline(pl[:-1] if closed else pl, color, closed)

    scol = stitch_color
    for res, settings, _ in stitches:
        if settings.hole_style == "diamond":
            length = max(0.1, settings.slit_length - k)
            for hle in res.holes:
                pts = diamond_points(hle.point, hle.tangent, length,
                                     settings.slit_angle, DIAMOND_WIDTH_RATIO)
                polyline(pts, scol, True)
        elif settings.hole_style == "slit":
            half = max(0.05, (settings.slit_length - k) / 2.0)
            slant = math.radians(settings.slit_angle)
            for hle in res.holes:
                d = hle.tangent.rotate(slant)
                line(hle.point - d * half, hle.point + d * half, scol)
        else:
            r = max(0.05, (settings.hole_diameter - k) / 2.0)
            for hle in res.holes:
                circle(hle.point, r, scol)

    s.append(_dxf_pair(0, "ENDSEC"))
    s.append(_dxf_pair(0, "EOF"))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(s))

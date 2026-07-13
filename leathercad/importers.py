"""Import SVG and DXF drawings as editable shapes.

Curves are flattened to fine polylines (same tolerance as the stitch engine),
closed contours become :class:`Polygon` (smooth -- no forced corner holes) and
open ones :class:`PathShape`. Imported geometry lands on the Cut layer with
stitching off; enable and tune stitching per piece afterwards.

SVG: <path> (M/L/H/V/C/S/Q/T/A/Z, absolute & relative), rect, circle, ellipse,
line, polyline, polygon; nested group transforms (translate/scale/rotate/
matrix). Millimetre scaling comes from the root width/viewBox (px assumed
96 dpi); SVG's Y-down axis is flipped to our Y-up world.

DXF: LINE, CIRCLE, ARC, LWPOLYLINE and POLYLINE/VERTEX (incl. bulge arcs) from
the ENTITIES section. Coordinates are taken as millimetres.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from .geometry import Vec2
from .path import CubicBezier, QuadraticBezier, DEFAULT_FLATNESS
from .shapes import Shape, Polygon, PathShape, Circle, Transform

_FLAT = 0.05     # import flattening tolerance (mm) -- fine enough for lasers


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _shape_from_ring(pts: List[Vec2], closed: bool,
                     layer: str = "Cut") -> Optional[Shape]:
    """Centre a world polyline and wrap it as a Polygon/PathShape."""
    # a ring closed by repeating its first point (no explicit Z) counts too
    if not closed and len(pts) >= 4 and (pts[0] - pts[-1]).length() < 1e-6:
        closed = True
    if closed and len(pts) >= 2 and (pts[0] - pts[-1]).length() < 1e-6:
        pts = pts[:-1]
    if len(pts) < 2:
        return None
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    local = [Vec2(p.x - cx, p.y - cy) for p in pts]
    t = Transform(x=cx, y=cy)
    if closed and len(local) >= 3:
        return Polygon(points=local, close_path=True, sharp_corners=False,
                       transform=t, layer=layer)
    return PathShape(points=local, close_path=False, transform=t, layer=layer)


def _normalize(shapes: List[Shape]) -> List[Shape]:
    """Translate everything so the pattern's bottom-left sits at the origin."""
    if not shapes:
        return shapes
    minx = min(s.bounds()[0] for s in shapes)
    miny = min(s.bounds()[1] for s in shapes)
    for s in shapes:
        s.transform.x -= minx
        s.transform.y -= miny
    return shapes


def _arc_points(c: Vec2, r: float, a0: float, a1: float, ccw: bool = True,
                flat: float = _FLAT) -> List[Vec2]:
    """Flatten a circular arc (radians) including both endpoints."""
    if ccw:
        while a1 < a0 - 1e-12:
            a1 += 2 * math.pi
    else:
        while a1 > a0 + 1e-12:
            a1 -= 2 * math.pi
    sweep = a1 - a0
    if r <= 1e-9 or abs(sweep) < 1e-9:
        return [Vec2(c.x + r * math.cos(a0), c.y + r * math.sin(a0))]
    ratio = max(-1.0, min(1.0, 1.0 - flat / r))
    step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi
    n = max(2, int(math.ceil(abs(sweep) / max(step, 1e-3))))
    return [Vec2(c.x + r * math.cos(a0 + sweep * i / n),
                 c.y + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

# affine transform as (a, b, c, d, e, f):  x' = a x + c y + e ; y' = b x + d y + f
_IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat_mul(m, n):
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def _mat_apply(m, p: Vec2) -> Vec2:
    a, b, c, d, e, f = m
    return Vec2(a * p.x + c * p.y + e, b * p.x + d * p.y + f)


def _parse_transform(text: str):
    m = _IDENT
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "translate":
            n = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            n = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            ca, sa = math.cos(a), math.sin(a)
            n = (ca, sa, -sa, ca, 0, 0)
            if len(v) >= 3:                       # rotate about a point
                cx, cy = v[1], v[2]
                n = _mat_mul(_mat_mul((1, 0, 0, 1, cx, cy), n),
                             (1, 0, 0, 1, -cx, -cy))
        elif name == "matrix" and len(v) == 6:
            n = tuple(v)
        else:
            continue
        m = _mat_mul(m, n)
    return m


def _svg_len_mm(text: str) -> Optional[float]:
    """A width/height attribute in mm (None if unitless/unknown)."""
    m = re.match(r"\s*([0-9.eE+-]+)\s*([a-z%]*)\s*$", text or "")
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    scale = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.0,
             "px": 25.4 / 96.0, "": None, "%": None}.get(unit, None)
    return v * scale if scale else None


def _endpoint_arc(p0: Vec2, rx, ry, rot_deg, large, sweep, p1: Vec2):
    """SVG elliptical-arc endpoint parameterisation -> flattened points
    (W3C implementation notes B.2.4)."""
    if rx <= 0 or ry <= 0 or (p0 - p1).length() < 1e-12:
        return [p1]
    phi = math.radians(rot_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0.x - p1.x) / 2.0, (p0.y - p1.y) / 2.0
    x1p = cp * dx + sp * dy
    y1p = -sp * dx + cp * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (p0.x + p1.x) / 2.0
    cy = sp * cxp + cp * cyp + (p0.y + p1.y) / 2.0

    def ang(ux, uy, vx, vy):
        d = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / d)))
        return -a if ux * vy - uy * vx < 0 else a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry,
              (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(2, int(math.ceil(abs(dth) / (math.pi / 32))))
    out = []
    for i in range(1, n + 1):
        t = th1 + dth * i / n
        x = cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp
        y = cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp
        out.append(Vec2(x, y))
    return out


def _parse_path_d(d: str) -> List[Tuple[List[Vec2], bool]]:
    """SVG path data -> list of (flattened points, closed)."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUM.pattern, d or "")
    i = 0
    cmd = None
    cur = Vec2(0, 0)
    start = Vec2(0, 0)
    prev_c = None            # reflection point for S/T
    prev_cmd = ""
    pts: List[Vec2] = []
    subpaths: List[Tuple[List[Vec2], bool]] = []

    def num():
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    def flush(closed):
        nonlocal pts
        if len(pts) >= 2:
            subpaths.append((pts, closed))
        pts = []

    while i < len(tokens):
        t = tokens[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t
            i += 1
            if cmd in "Zz":
                flush(True)
                cur = start
                prev_cmd = cmd
                continue
        # implicit repeat: a number where a command is allowed re-runs cmd
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = num(), num()
            p = Vec2(cur.x + x, cur.y + y) if rel else Vec2(x, y)
            flush(False)
            cur = start = p
            pts = [p]
            cmd = "l" if rel else "L"       # subsequent pairs are line-tos
        elif c == "L":
            x, y = num(), num()
            cur = Vec2(cur.x + x, cur.y + y) if rel else Vec2(x, y)
            pts.append(cur)
        elif c == "H":
            x = num()
            cur = Vec2(cur.x + x if rel else x, cur.y)
            pts.append(cur)
        elif c == "V":
            y = num()
            cur = Vec2(cur.x, cur.y + y if rel else y)
            pts.append(cur)
        elif c in ("C", "S"):
            if c == "C":
                x1, y1 = num(), num()
                c1 = Vec2(cur.x + x1, cur.y + y1) if rel else Vec2(x1, y1)
            else:
                c1 = (Vec2(2 * cur.x - prev_c.x, 2 * cur.y - prev_c.y)
                      if prev_cmd.upper() in "CS" and prev_c else cur)
            x2, y2 = num(), num()
            c2 = Vec2(cur.x + x2, cur.y + y2) if rel else Vec2(x2, y2)
            x, y = num(), num()
            end = Vec2(cur.x + x, cur.y + y) if rel else Vec2(x, y)
            pts.extend(CubicBezier(cur, c1, c2, end).flatten(_FLAT))
            prev_c = c2
            cur = end
        elif c in ("Q", "T"):
            if c == "Q":
                x1, y1 = num(), num()
                c1 = Vec2(cur.x + x1, cur.y + y1) if rel else Vec2(x1, y1)
            else:
                c1 = (Vec2(2 * cur.x - prev_c.x, 2 * cur.y - prev_c.y)
                      if prev_cmd.upper() in "QT" and prev_c else cur)
            x, y = num(), num()
            end = Vec2(cur.x + x, cur.y + y) if rel else Vec2(x, y)
            pts.extend(QuadraticBezier(cur, c1, end).flatten(_FLAT))
            prev_c = c1
            cur = end
        elif c == "A":
            rx, ry, rot = num(), num(), num()
            large, sweep = bool(num()), bool(num())
            x, y = num(), num()
            end = Vec2(cur.x + x, cur.y + y) if rel else Vec2(x, y)
            pts.extend(_endpoint_arc(cur, rx, ry, rot, large, sweep, end))
            cur = end
        else:
            i += 1                               # unknown -- skip token
        prev_cmd = cmd or ""
    flush(False)
    return subpaths


def _element_polylines(el, tag) -> List[Tuple[List[Vec2], bool]]:
    """Basic SVG shape elements -> (points, closed) in local coords."""
    def f(name, default="0"):
        return float(el.get(name, default))
    if tag == "path":
        return _parse_path_d(el.get("d", ""))
    if tag == "rect":
        x, y, w, h = f("x"), f("y"), f("width"), f("height")
        return [([Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h),
                  Vec2(x, y + h)], True)]
    if tag in ("circle", "ellipse"):
        cx, cy = f("cx"), f("cy")
        rx = f("r") if tag == "circle" else f("rx")
        ry = f("r") if tag == "circle" else f("ry")
        n = max(24, int(2 * math.pi * max(rx, ry) / _FLAT ** 0.5 / 4))
        n = min(n, 720)
        ring = [Vec2(cx + rx * math.cos(2 * math.pi * i / n),
                     cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return [(ring, True)]
    if tag == "line":
        return [([Vec2(f("x1"), f("y1")), Vec2(f("x2"), f("y2"))], False)]
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in _NUM.findall(el.get("points", ""))]
        pts = [Vec2(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        return [(pts, tag == "polygon")]
    return []


def _element_color(el, inherited: str) -> str:
    """The element's stroke (falling back to fill, then the group's colour),
    normalised to lowercase '#rrggbb' where possible."""
    val = el.get("stroke") or None
    style = el.get("style") or ""
    m = re.search(r"stroke\s*:\s*([^;]+)", style)
    if m:
        val = m.group(1).strip()
    if not val or val == "none":
        fill = el.get("fill")
        m = re.search(r"fill\s*:\s*([^;]+)", style)
        if m:
            fill = m.group(1).strip()
        if fill and fill != "none":
            val = fill
    return (val or inherited).lower().strip()


def _true_round(el, tag, m, scale) -> Optional[Shape]:
    """A <circle>/<ellipse> under an axis-aligned transform -> a REAL
    Circle/Ellipse shape instead of a dense polygon. This keeps imported
    stitch holes light: 5 snap nodes instead of hundreds of vertices."""
    if tag not in ("circle", "ellipse"):
        return None
    a, b, c, d, _e, _f = m
    if abs(b) > 1e-9 or abs(c) > 1e-9 or a <= 0 or d <= 0:
        return None                       # rotated/skewed: fall back to polygon
    cx = float(el.get("cx", 0))
    cy = float(el.get("cy", 0))
    rx = float(el.get("r", el.get("rx", 0)))
    ry = float(el.get("r", el.get("ry", 0)))
    if rx <= 0 or ry <= 0:
        return None
    centre = _mat_apply(m, Vec2(cx, cy))
    wrx, wry = rx * a * scale, ry * d * scale
    t = Transform(x=centre.x * scale, y=-centre.y * scale)   # Y-flip
    if abs(wrx - wry) < 1e-9:
        return Circle(rx=wrx, ry=wry, transform=t, layer="Cut")
    from .shapes import Ellipse
    return Ellipse(rx=wrx, ry=wry, transform=t, layer="Cut")


def import_svg(path: str, color_layers: Optional[dict] = None) -> List[Shape]:
    """Import an SVG. ``color_layers`` maps lowercase '#rrggbb' stroke/fill
    colours to layer names (e.g. {'#0066ff': 'Stitch'}); unmatched colours
    land on Cut."""
    root = ET.parse(path).getroot()
    color_layers = {k.lower(): v for k, v in (color_layers or {}).items()}

    # unit scale: root width + viewBox -> mm per user unit
    scale = 1.0
    vb = root.get("viewBox")
    w_mm = _svg_len_mm(root.get("width", ""))
    if vb and w_mm:
        parts = [float(v) for v in _NUM.findall(vb)]
        if len(parts) == 4 and parts[2] > 0:
            scale = w_mm / parts[2]

    shapes: List[Shape] = []

    def walk(el, m, color):
        tag = el.tag.split("}")[-1]
        if tag in ("defs", "clipPath", "symbol", "style", "text"):
            return
        m = _mat_mul(m, _parse_transform(el.get("transform", "")))
        color = _element_color(el, color)
        layer = color_layers.get(color, "Cut")
        rnd = _true_round(el, tag, m, scale)
        if rnd is not None:                   # real circle/ellipse, kept light
            rnd.layer = layer
            shapes.append(rnd)
        else:
            for pts, closed in _element_polylines(el, tag):
                world = [_mat_apply(m, p) for p in pts]
                # scale to mm and flip SVG's Y-down to our Y-up
                world = [Vec2(p.x * scale, -p.y * scale) for p in world]
                sh = _shape_from_ring(world, closed, layer)
                if sh is not None:
                    shapes.append(sh)
        for child in el:
            walk(child, m, color)

    walk(root, _IDENT, "")
    return _normalize(shapes)


# ---------------------------------------------------------------------------
# DXF
# ---------------------------------------------------------------------------
def _dxf_pairs(text: str):
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        try:
            yield int(lines[i].strip()), lines[i + 1].strip()
        except ValueError:
            continue


def _bulge_points(a: Vec2, b: Vec2, bulge: float) -> List[Vec2]:
    """Points along the arc from a to b with the given DXF bulge (excl. a)."""
    if abs(bulge) < 1e-12:
        return [b]
    theta = 4.0 * math.atan(bulge)              # included angle, signed
    chord = (b - a).length()
    if chord < 1e-12:
        return [b]
    r = chord / (2.0 * math.sin(abs(theta) / 2.0))
    mid = a.lerp(b, 0.5)
    # centre sits perpendicular to the chord
    d = (b - a) * (1.0 / chord)
    n = Vec2(-d.y, d.x)
    h = r * math.cos(abs(theta) / 2.0)
    c = mid + n * (h if bulge > 0 else -h)
    a0 = (a - c).angle()
    a1 = (b - c).angle()
    return _arc_points(c, r, a0, a1, ccw=bulge > 0)[1:]


# nearest standard hex for the 7 classic AutoCAD colour indices (+ grey)
_ACI_HEX = {1: "#ff0000", 2: "#ffff00", 3: "#00aa00", 4: "#00ffff",
            5: "#0066ff", 6: "#ff00ff", 7: "#ffffff", 8: "#888888"}


def import_dxf(path: str, color_layers: Optional[dict] = None) -> List[Shape]:
    """Import a DXF. Entity colours (ACI, code 62) are mapped through
    ``color_layers`` ('#rrggbb' -> layer name) like the SVG importer."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        pairs = list(_dxf_pairs(fh.read()))
    color_layers = {k.lower(): v for k, v in (color_layers or {}).items()}

    def layer_for(attrs) -> str:
        try:
            aci = int(attrs.get(62, 0))
        except (TypeError, ValueError):
            aci = 0
        return color_layers.get(_ACI_HEX.get(aci, ""), "Cut")

    shapes: List[Shape] = []
    i = 0
    in_entities = False
    while i < len(pairs):
        code, val = pairs[i]
        if code == 2 and val == "ENTITIES":
            in_entities = True
        elif code == 0 and val == "ENDSEC":
            in_entities = False
        elif in_entities and code == 0:
            ent = val
            attrs = {}
            verts: List[Tuple[float, float, float]] = []   # x, y, bulge
            j = i + 1
            pending = None      # collect per-vertex codes for LWPOLYLINE
            while j < len(pairs) and pairs[j][0] != 0:
                c, v = pairs[j]
                if ent == "LWPOLYLINE" and c in (10, 20, 42):
                    if c == 10:
                        verts.append([float(v), 0.0, 0.0])
                    elif c == 20 and verts:
                        verts[-1][1] = float(v)
                    elif c == 42 and verts:
                        verts[-1][2] = float(v)
                else:
                    attrs[c] = v
                j += 1
            if ent == "LINE":
                a = Vec2(float(attrs.get(10, 0)), float(attrs.get(20, 0)))
                b = Vec2(float(attrs.get(11, 0)), float(attrs.get(21, 0)))
                sh = _shape_from_ring([a, b], False, layer_for(attrs))
                if sh:
                    shapes.append(sh)
            elif ent == "CIRCLE":
                r = float(attrs.get(40, 0))
                if r > 0:
                    shapes.append(Circle(
                        rx=r, ry=r, layer=layer_for(attrs),
                        transform=Transform(x=float(attrs.get(10, 0)),
                                            y=float(attrs.get(20, 0)))))
            elif ent == "ARC":
                c = Vec2(float(attrs.get(10, 0)), float(attrs.get(20, 0)))
                r = float(attrs.get(40, 0))
                a0 = math.radians(float(attrs.get(50, 0)))
                a1 = math.radians(float(attrs.get(51, 360)))
                sh = _shape_from_ring(_arc_points(c, r, a0, a1, True),
                                      False, layer_for(attrs))
                if sh:
                    shapes.append(sh)
            elif ent == "LWPOLYLINE":
                closed = int(attrs.get(70, 0) or 0) & 1
                pts: List[Vec2] = []
                n = len(verts)
                for k in range(n):
                    x, y, bulge = verts[k]
                    p = Vec2(x, y)
                    if not pts:
                        pts.append(p)
                    elif (p - pts[-1]).length() > 1e-12:
                        pts.append(p)
                    if bulge and (k + 1 < n or closed):
                        nx, ny, _ = verts[(k + 1) % n]
                        pts.extend(_bulge_points(p, Vec2(nx, ny), bulge))
                sh = _shape_from_ring(pts, bool(closed), layer_for(attrs))
                if sh:
                    shapes.append(sh)
            elif ent == "POLYLINE":
                closed = int(attrs.get(70, 0) or 0) & 1
                pts = []
                prev = None      # (point, bulge)
                while j < len(pairs):
                    if pairs[j][0] == 0 and pairs[j][1] == "VERTEX":
                        vat = {}
                        j += 1
                        while j < len(pairs) and pairs[j][0] != 0:
                            vat[pairs[j][0]] = pairs[j][1]
                            j += 1
                        p = Vec2(float(vat.get(10, 0)), float(vat.get(20, 0)))
                        if prev is not None and abs(prev[1]) > 1e-12:
                            pts.extend(_bulge_points(prev[0], p, prev[1]))
                        elif not pts or (p - pts[-1]).length() > 1e-12:
                            pts.append(p)
                        prev = (p, float(vat.get(42, 0) or 0))
                    elif pairs[j][0] == 0 and pairs[j][1] == "SEQEND":
                        j += 1
                        break
                    else:
                        j += 1
                if closed and prev is not None and abs(prev[1]) > 1e-12 and pts:
                    pts.extend(_bulge_points(prev[0], pts[0], prev[1]))
                sh = _shape_from_ring(pts, bool(closed), layer_for(attrs))
                if sh:
                    shapes.append(sh)
            i = j
            continue
        i += 1
    return _normalize(shapes)


def import_file(path: str,
                color_layers: Optional[dict] = None) -> List[Shape]:
    """Import an SVG or DXF file (dispatch by extension)."""
    low = path.lower()
    if low.endswith(".svg"):
        return import_svg(path, color_layers)
    if low.endswith(".dxf"):
        return import_dxf(path, color_layers)
    raise ValueError("Unsupported file type (use .svg or .dxf)")

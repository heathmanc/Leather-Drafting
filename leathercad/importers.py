"""Import SVG and DXF drawings as editable shapes.

Curves are flattened to fine polylines (same tolerance as the stitch engine),
closed contours become :class:`Polygon` (smooth -- no forced corner holes) and
open ones :class:`PathShape`. Imported geometry lands on the Cut layer with
stitching off; enable and tune stitching per piece afterwards.

SVG: <path> (M/L/H/V/C/S/Q/T/A/Z, absolute & relative), rect, circle, ellipse,
line, polyline, polygon; nested group transforms (translate/scale/rotate/
matrix). Millimetre scaling comes from the root width/viewBox (px assumed
96 dpi); SVG's Y-down axis is flipped to our Y-up world.

DXF: LINE, CIRCLE, ARC, ELLIPSE, SPLINE (NURBS, flattened), LWPOLYLINE and
POLYLINE/VERTEX (incl. bulge arcs), plus block references (INSERT, including
arrays) expanded from the BLOCKS section. Per-entity extrusion directions
(OCS, codes 210/220/230) are resolved to world space -- CAD tools write a
(0, 0, -1) normal for anything drawn or mirrored from the back, and ignoring
it lands those entities mirrored against everything else. Drawing units come
from the header's $INSUNITS (unitless is assumed to be millimetres).
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
    """(group code, value) pairs. A DXF is strictly code line / value line, so
    one stray line would swap the two for the whole rest of the file (turning
    the drawing to noise). Resynchronise by skipping a SINGLE bad line instead
    of blindly stepping in twos."""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i + 1 < n:
        try:
            code = int(lines[i].strip())
        except ValueError:
            i += 1                      # junk/blank: realign, don't stay skewed
            continue
        yield code, lines[i + 1].strip()
        i += 2


def _ocs_mapper(nx: float, ny: float, nz: float):
    """Map a point in an entity's Object Coordinate System to world space.

    DXF stores LWPOLYLINE/POLYLINE/CIRCLE/ARC coordinates relative to the
    entity's extrusion direction (codes 210/220/230), not in world space. A
    plane normal of (0, 0, -1) -- which Rhino writes routinely for anything
    drawn or mirrored from the back -- means the X axis is FLIPPED. Ignoring it
    mirrors those entities relative to everything else, which is exactly what a
    "jumbled" import looks like. This is AutoCAD's Arbitrary Axis Algorithm."""
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln < 1e-12 or (abs(nx) < 1e-12 and abs(ny) < 1e-12 and nz > 0):
        return None                     # the ordinary (0,0,1) case: world = OCS
    nx, ny, nz = nx / ln, ny / ln, nz / ln

    def cross(u, v):
        return (u[1] * v[2] - u[2] * v[1],
                u[2] * v[0] - u[0] * v[2],
                u[0] * v[1] - u[1] * v[0])

    n = (nx, ny, nz)
    # pick the reference axis the algorithm prescribes, so the basis is stable
    ax = cross((0.0, 1.0, 0.0), n) if (abs(nx) < 1.0 / 64 and abs(ny) < 1.0 / 64) \
        else cross((0.0, 0.0, 1.0), n)
    la = math.sqrt(sum(c * c for c in ax))
    if la < 1e-12:
        return None
    ax = tuple(c / la for c in ax)
    ay = cross(n, ax)
    la = math.sqrt(sum(c * c for c in ay))
    if la < 1e-12:
        return None
    ay = tuple(c / la for c in ay)

    def to_world(x: float, y: float, z: float = 0.0) -> Vec2:
        return Vec2(ax[0] * x + ay[0] * y + n[0] * z,
                    ax[1] * x + ay[1] * y + n[1] * z)
    return to_world


# -- entity records ---------------------------------------------------------
# Group codes repeat within one entity (a SPLINE has many 10s, 40s, 41s), so
# entities are kept as their ordered pair list rather than a flat dict.

def _ent_all(ent, code) -> List[str]:
    return [v for c, v in ent["pairs"] if c == code]


def _ent_str(ent, code, default: str = "") -> str:
    for c, v in ent["pairs"]:
        if c == code:
            return v
    return default


def _ent_num(ent, code, default: float = 0.0) -> float:
    try:
        return float(_ent_str(ent, code, str(default)))
    except ValueError:
        return default


def _ent_int(ent, code, default: int = 0) -> int:
    try:
        return int(float(_ent_str(ent, code, str(default))))
    except ValueError:
        return default


def _dxf_records(pairs, i: int, end: int):
    """Group flat pairs into entity records. A POLYLINE swallows its VERTEX
    children (up to SEQEND) so they don't look like top-level entities."""
    out = []
    while i < end:
        code, val = pairs[i]
        if code != 0:
            i += 1
            continue
        if val in ("ENDSEC", "EOF"):
            break
        ent = {"type": val, "pairs": [], "verts": []}
        i += 1
        while i < end and pairs[i][0] != 0:
            ent["pairs"].append(pairs[i])
            i += 1
        if ent["type"] == "POLYLINE":
            while i < end and pairs[i][0] == 0 and pairs[i][1] == "VERTEX":
                v = {"type": "VERTEX", "pairs": [], "verts": []}
                i += 1
                while i < end and pairs[i][0] != 0:
                    v["pairs"].append(pairs[i])
                    i += 1
                ent["verts"].append(v)
            if i < end and pairs[i][0] == 0 and pairs[i][1] == "SEQEND":
                i += 1
                while i < end and pairs[i][0] != 0:
                    i += 1
        out.append(ent)
    return out, i


def _dxf_sections(pairs) -> dict:
    """Section name -> (start, end) index range over ``pairs``."""
    out, i, n = {}, 0, len(pairs)
    while i < n:
        if pairs[i][0] == 0 and pairs[i][1] == "SECTION":
            j = i + 1
            name = ""
            if j < n and pairs[j][0] == 2:
                name = pairs[j][1]
                j += 1
            start = j
            while j < n and not (pairs[j][0] == 0 and pairs[j][1] == "ENDSEC"):
                j += 1
            out[name] = (start, j)
            i = j
        i += 1
    return out


def _nurbs_points(ctrl: List[Vec2], weights: List[float], knots: List[float],
                  degree: int) -> List[Vec2]:
    """Flatten a (rational) B-spline via de Boor. Rhino exports almost every
    freeform curve as a SPLINE, so without this they vanish from the import."""
    n = len(ctrl) - 1
    p = max(1, min(degree, n))
    if n < 1:
        return list(ctrl)
    if len(knots) != n + p + 2:         # rebuild a clamped uniform vector
        inner = n - p + 1
        knots = ([0.0] * (p + 1)
                 + [k / inner for k in range(1, inner)]
                 + [1.0] * (p + 1))
    if len(weights) != len(ctrl):
        weights = [1.0] * len(ctrl)
    cw = [(c.x * w, c.y * w, w) for c, w in zip(ctrl, weights)]

    def span_of(u: float) -> int:
        if u >= knots[n + 1]:
            return n
        if u <= knots[p]:
            return p
        lo, hi = p, n + 1
        mid = (lo + hi) // 2
        while u < knots[mid] or u >= knots[mid + 1]:
            if u < knots[mid]:
                hi = mid
            else:
                lo = mid
            mid = (lo + hi) // 2
            if mid <= lo and mid >= hi:
                break
        return mid

    def at(u: float) -> Vec2:
        s = span_of(u)
        d = [cw[s - p + k] for k in range(p + 1)]
        for r in range(1, p + 1):
            for k in range(p, r - 1, -1):
                idx = s - p + k
                den = knots[idx + p - r + 1] - knots[idx]
                a = 0.0 if abs(den) < 1e-12 else (u - knots[idx]) / den
                d[k] = tuple((1.0 - a) * d[k - 1][t] + a * d[k][t]
                             for t in range(3))
        x, y, w = d[p]
        return Vec2(x / w, y / w) if abs(w) > 1e-12 else Vec2(x, y)

    u0, u1 = knots[p], knots[n + 1]
    if not (u1 > u0):
        return list(ctrl)
    # sample proportional to the control polygon -- fine enough for a laser
    span = sum((ctrl[k + 1] - ctrl[k]).length() for k in range(n))
    steps = max(16, min(512, int(span / max(_FLAT * 8.0, 1e-6))))
    return [at(u0 + (u1 - u0) * k / steps) for k in range(steps + 1)]


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


#: DXF layer names we recognise by name (Rhino users organise by layer)
_DXF_LAYER_NAMES = {"cut": "Cut", "stitch": "Stitch", "score": "Score",
                    "fold": "Score", "engrave": "Engrave"}

#: $INSUNITS code -> millimetres per drawing unit (0/unitless -> assume mm)
_DXF_UNITS_MM = {1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0,
                 8: 2.54e-5, 9: 0.0254, 10: 914.4, 14: 100.0, 15: 10000.0}


def _dxf_unit_scale(pairs, sections) -> float:
    """Millimetres per drawing unit, from the header's ``$INSUNITS``. A Rhino
    model built in inches would otherwise import 25.4x too small."""
    rng = sections.get("HEADER")
    if not rng:
        return 1.0
    start, end = rng
    for i in range(start, min(end, len(pairs))):
        if pairs[i][0] == 9 and pairs[i][1] == "$INSUNITS":
            for j in range(i + 1, min(i + 4, end)):
                if pairs[j][0] == 70:
                    try:
                        return _DXF_UNITS_MM.get(int(pairs[j][1]), 1.0)
                    except ValueError:
                        return 1.0
            break
    return 1.0


def _dxf_entity_shapes(ent, m, layer: str, blocks: dict, depth: int
                       ) -> List[Shape]:
    """One DXF entity -> shapes, placed through the accumulated transform ``m``
    (identity at top level; an INSERT's placement inside a block)."""
    kind = ent["type"]
    ocs = _ocs_mapper(_ent_num(ent, 210, 0.0), _ent_num(ent, 220, 0.0),
                      _ent_num(ent, 230, 1.0))
    elev = _ent_num(ent, 38, 0.0)

    def unrotate(x: float, y: float) -> Vec2:
        """OCS -> the entity's own world plane (NOT through ``m``)."""
        return ocs(x, y, elev) if ocs else Vec2(x, y)

    def place(x: float, y: float) -> Vec2:
        """OCS -> world -> the caller's transform."""
        return _mat_apply(m, unrotate(x, y))

    def ring(pts: List[Vec2], closed: bool) -> List[Shape]:
        sh = _shape_from_ring(pts, closed, layer)
        return [sh] if sh is not None else []

    if kind == "LINE":                       # LINE is WCS, never OCS
        a = _mat_apply(m, Vec2(_ent_num(ent, 10), _ent_num(ent, 20)))
        b = _mat_apply(m, Vec2(_ent_num(ent, 11), _ent_num(ent, 21)))
        return ring([a, b], False)

    if kind == "CIRCLE":
        r = _ent_num(ent, 40)
        if r <= 0:
            return []
        c = place(_ent_num(ent, 10), _ent_num(ent, 20))
        sx = math.hypot(m[0], m[1])
        sy = math.hypot(m[2], m[3])
        if abs(sx - sy) < 1e-9:              # uniform: keep it parametric
            return [Circle(rx=r * sx, ry=r * sx, layer=layer,
                           transform=Transform(x=c.x, y=c.y))]
        return ring([Vec2(c.x + r * sx * math.cos(2 * math.pi * k / 64),
                          c.y + r * sy * math.sin(2 * math.pi * k / 64))
                     for k in range(64)], True)

    if kind == "ARC":
        r = _ent_num(ent, 40)
        cx, cy = _ent_num(ent, 10), _ent_num(ent, 20)
        a0 = math.radians(_ent_num(ent, 50, 0.0))
        a1 = math.radians(_ent_num(ent, 51, 360.0))
        # build in the entity's own plane, then map out -- so a mirrored OCS
        # correctly reverses the sweep instead of drawing the wrong arc
        return ring([place(cx + r * math.cos(a), cy + r * math.sin(a))
                     for a in _arc_angles(a0, a1, r)], False)

    if kind == "ELLIPSE":                    # centre/major axis are WCS
        c = Vec2(_ent_num(ent, 10), _ent_num(ent, 20))
        mx, my = _ent_num(ent, 11), _ent_num(ent, 21)
        ratio = _ent_num(ent, 40, 1.0)
        t0 = _ent_num(ent, 41, 0.0)
        t1 = _ent_num(ent, 42, 2 * math.pi)
        major = math.hypot(mx, my)
        if major < 1e-12:
            return []
        rot = math.atan2(my, mx)
        minor = major * ratio
        if t1 <= t0:
            t1 += 2 * math.pi
        n = max(16, min(512, int(abs(t1 - t0) * major / max(_FLAT * 4, 1e-6))))
        pts = []
        for k in range(n + 1):
            t = t0 + (t1 - t0) * k / n
            ex, ey = major * math.cos(t), minor * math.sin(t)
            pts.append(_mat_apply(m, Vec2(
                c.x + ex * math.cos(rot) - ey * math.sin(rot),
                c.y + ex * math.sin(rot) + ey * math.cos(rot))))
        closed = abs((t1 - t0) - 2 * math.pi) < 1e-6
        return ring(pts, closed)

    if kind == "SPLINE":
        xs, ys = _ent_all(ent, 10), _ent_all(ent, 20)
        ctrl = [Vec2(float(a), float(b)) for a, b in zip(xs, ys)]
        flags = _ent_int(ent, 70, 0)
        if len(ctrl) < 2:                    # control points absent: fit points
            fx, fy = _ent_all(ent, 11), _ent_all(ent, 21)
            pts = [_mat_apply(m, Vec2(float(a), float(b)))
                   for a, b in zip(fx, fy)]
            return ring(pts, bool(flags & 1))
        knots = [float(v) for v in _ent_all(ent, 40)]
        weights = [float(v) for v in _ent_all(ent, 41)]
        deg = _ent_int(ent, 71, 3)
        pts = [_mat_apply(m, p)
               for p in _nurbs_points(ctrl, weights, knots, deg)]
        return ring(pts, bool(flags & 1))

    if kind == "LWPOLYLINE":
        closed = bool(_ent_int(ent, 70, 0) & 1)
        verts: List[List[float]] = []        # x, y, bulge -- 10/20/42 in order
        for c, v in ent["pairs"]:
            try:
                fv = float(v)
            except ValueError:
                continue
            if c == 10:
                verts.append([fv, 0.0, 0.0])
            elif c == 20 and verts:
                verts[-1][1] = fv
            elif c == 42 and verts:
                verts[-1][2] = fv
        return ring(_polyline_points(verts, closed, place), closed)

    if kind == "POLYLINE":
        flags = _ent_int(ent, 70, 0)
        if flags & (16 | 64):                # a 3D mesh, not a contour
            return []
        closed = bool(flags & 1)
        verts = []
        for v in ent["verts"]:
            if _ent_int(v, 70, 0) & (16 | 64):
                continue
            verts.append([_ent_num(v, 10), _ent_num(v, 20), _ent_num(v, 42)])
        return ring(_polyline_points(verts, closed, place), closed)

    if kind == "INSERT" and depth < 8:
        blk = blocks.get(_ent_str(ent, 2, ""))
        if not blk:
            return []
        base = blk["base"]
        # build the placement in the INSERT's own space; ``m`` is composed on
        # top below, so going through ``place`` here would apply it twice
        ins = unrotate(_ent_num(ent, 10), _ent_num(ent, 20))
        sx = _ent_num(ent, 41, 1.0) or 1.0
        sy = _ent_num(ent, 42, 1.0) or 1.0
        rot = math.radians(_ent_num(ent, 50, 0.0))
        ca, sa = math.cos(rot), math.sin(rot)
        cols = max(1, _ent_int(ent, 70, 1))
        rows = max(1, _ent_int(ent, 71, 1))
        dx, dy = _ent_num(ent, 44, 0.0), _ent_num(ent, 45, 0.0)
        out: List[Shape] = []
        for cx in range(cols):
            for ry in range(rows):
                # translate(insert + array step) . rotate . scale . -blockbase
                local = _mat_mul(
                    (1, 0, 0, 1, ins.x + cx * dx, ins.y + ry * dy),
                    _mat_mul((ca, sa, -sa, ca, 0, 0),
                             (sx, 0, 0, sy, -base.x * sx, -base.y * sy)))
                for sub in blk["ents"]:
                    # block contents adopt the reference's layer (DXF's
                    # "layer 0 inherits" rule, and the useful default anyway)
                    out += _dxf_entity_shapes(sub, _mat_mul(m, local), layer,
                                              blocks, depth + 1)
        return out

    return []


def _arc_angles(a0: float, a1: float, r: float) -> List[float]:
    """Angles stepping CCW from a0 to a1, fine enough for the flatten tolerance."""
    while a1 < a0 - 1e-12:
        a1 += 2 * math.pi
    sweep = a1 - a0
    if r <= 1e-9 or abs(sweep) < 1e-9:
        return [a0]
    ratio = max(-1.0, min(1.0, 1.0 - _FLAT / r))
    step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi
    n = max(2, int(math.ceil(abs(sweep) / max(step, 1e-3))))
    return [a0 + sweep * k / n for k in range(n + 1)]


def _polyline_points(verts, closed: bool, place) -> List[Vec2]:
    """Vertices (x, y, bulge) -> a flattened ring, mapped through ``place``."""
    pts: List[Vec2] = []
    n = len(verts)
    for k in range(n):
        x, y, bulge = verts[k]
        p = place(x, y)
        if not pts or (p - pts[-1]).length() > 1e-12:
            pts.append(p)
        if bulge and (k + 1 < n or closed):
            nx, ny, _b = verts[(k + 1) % n]
            # bulge the arc in the entity's own plane, then map it out
            pts.extend(_bulge_points(p, place(nx, ny), bulge))
    return pts


def import_dxf(path: str, color_layers: Optional[dict] = None) -> List[Shape]:
    """Import a DXF. Entity colours (ACI, code 62) are mapped through
    ``color_layers`` ('#rrggbb' -> layer name) like the SVG importer; failing
    that, a DXF layer literally named Cut/Stitch/Score/Engrave is honoured.

    Handles what CAD tools (Rhino especially) actually emit: LINE, CIRCLE, ARC,
    ELLIPSE, SPLINE, LWPOLYLINE and POLYLINE/VERTEX with bulge arcs, block
    references (INSERT, including arrays), and per-entity extrusion directions
    (OCS) -- without which mirrored entities land flipped against the rest."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        pairs = list(_dxf_pairs(fh.read()))
    color_layers = {k.lower(): v for k, v in (color_layers or {}).items()}

    def layer_for(ent) -> str:
        aci = _ent_int(ent, 62, 0)
        by_color = color_layers.get(_ACI_HEX.get(aci, ""))
        if by_color:
            return by_color
        return _DXF_LAYER_NAMES.get(_ent_str(ent, 8, "").strip().lower(), "Cut")

    sections = _dxf_sections(pairs)
    blocks: dict = {}
    if "BLOCKS" in sections:
        recs, _ = _dxf_records(pairs, *sections["BLOCKS"])
        cur = None
        for r in recs:
            if r["type"] == "BLOCK":
                cur = _ent_str(r, 2, "")
                blocks[cur] = {"base": Vec2(_ent_num(r, 10), _ent_num(r, 20)),
                               "ents": []}
            elif r["type"] == "ENDBLK":
                cur = None
            elif cur is not None and cur in blocks:
                blocks[cur]["ents"].append(r)

    rng = sections.get("ENTITIES")
    if rng is None:                          # no section header: scan it all
        rng = (0, len(pairs))
    ents, _ = _dxf_records(pairs, *rng)

    # drawing units -> mm, carried as the root transform so radii scale too
    u = _dxf_unit_scale(pairs, sections)
    root = _IDENT if u == 1.0 else (u, 0.0, 0.0, u, 0.0, 0.0)

    shapes: List[Shape] = []
    for ent in ents:
        shapes += _dxf_entity_shapes(ent, root, layer_for(ent), blocks, 0)
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

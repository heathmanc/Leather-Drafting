"""Stitching-punch styles, pitches and hole geometry.

Real stitching punches come in four cutting-edge styles, and each leaves a
differently shaped hole:

    round    -- a round hole (round-hole pricking irons / chisels)
    oblique  -- a flat, shallow-slanted slit
    french   -- a fine, steeper slanted slit (the Blanchard look)
    diamond  -- a diamond / lozenge, angled like the awl

You pick a punch by its **pitch** (tooth spacing). Pitch is standardised across
makers -- 2.7 / 3.0 / 3.38 / 3.85 / 4.0 mm are the common ladder (with 2.0/2.45
fine and 4.5/5.0 coarse); SPI = 25.4 / pitch. Round-hole irons carry an extra
spec -- the hole **diameter** -- because a round hole's size is chosen
independently of the spacing; the slit styles take their hole size from the
tooth, so it just scales with pitch.

``geometry_for`` hands back the hole dimensions a style implies (which the size
fields still override).
"""

from __future__ import annotations

from typing import Dict, List

MM_PER_INCH = 25.4

# the four cutting-edge styles, in menu order
ROUND, OBLIQUE, FRENCH, DIAMOND = "round", "oblique", "french", "diamond"
STYLES = (ROUND, OBLIQUE, FRENCH, DIAMOND)

# diamond hole width as a fraction of its length (a slim lozenge)
DIAMOND_WIDTH_RATIO = 0.4

# the standard pitch ladder (mm), shared across makers
STANDARD_PITCHES: List[float] = [2.0, 2.45, 2.7, 3.0, 3.38, 3.85, 4.0, 4.5, 5.0]

# common round-hole diameters (mm) -- leather chisels take thread up to ~1 mm
ROUND_DIAMETERS: List[float] = [0.8, 1.0, 1.2, 1.5]


def spi_to_mm(spi: float) -> float:
    """Stitches per inch -> pitch in millimetres."""
    return MM_PER_INCH / spi


def mm_to_spi(pitch_mm: float) -> float:
    """Pitch in millimetres -> stitches per inch."""
    return MM_PER_INCH / pitch_mm


# Per-style hole geometry. ``len_ratio`` scales the slit / diamond length to the
# pitch; ``angle`` is the slant off the seam tangent. Round holes take a fixed
# default diameter. These are defaults -- the size fields override them.
STYLE_DEFAULTS: Dict[str, dict] = {
    ROUND:   {"hole_style": "round",   "hole_diameter": 1.0,
              "len_ratio": 0.0,  "angle": 0.0},
    OBLIQUE: {"hole_style": "slit",    "hole_diameter": 1.0,
              "len_ratio": 0.42, "angle": 22.0},
    FRENCH:  {"hole_style": "slit",    "hole_diameter": 1.0,
              "len_ratio": 0.38, "angle": 42.0},
    DIAMOND: {"hole_style": "diamond", "hole_diameter": 1.0,
              "len_ratio": 0.42, "angle": 35.0},
}


def geometry_for(style: str, pitch_mm: float) -> dict:
    """Hole dimensions a ``style`` implies at ``pitch_mm``: a dict of the
    StitchSettings hole fields (hole_style / hole_diameter / slit_length /
    slit_angle) ready to apply. Diameter is only meaningful for round holes;
    slit_length / slit_angle only for the slit / diamond styles."""
    d = STYLE_DEFAULTS.get(style, STYLE_DEFAULTS[ROUND])
    slit_len = round(max(0.4, d["len_ratio"] * pitch_mm), 2) if d["len_ratio"] \
        else 1.6
    return {
        "hole_style": d["hole_style"],
        "hole_diameter": d["hole_diameter"],
        "slit_length": slit_len,
        "slit_angle": d["angle"],
    }


def measured_pitch(holes, reach: float = 12.0):
    """The spacing a row of holes was punched at, mm: for each hole the
    distance to the nearest hole in line with it -- along its tangent, so not
    the hole across in a second row -- and the middle value of those. None
    when no two holes are in line within ``reach``. Each hole needs
    ``.point`` and ``.tangent``."""
    import math
    pts = [(h.point.x, h.point.y) for h in holes]
    if len(pts) < 2:
        return None
    grid: Dict[tuple, list] = {}
    for k, (x, y) in enumerate(pts):
        grid.setdefault((math.floor(x / reach), math.floor(y / reach)),
                        []).append(k)
    gaps = []
    for k, h in enumerate(holes):
        tx, ty = h.tangent.x, h.tangent.y
        tl = math.hypot(tx, ty)
        if tl < 1e-12:
            continue
        tx, ty = tx / tl, ty / tl
        x, y = pts[k]
        cx, cy = math.floor(x / reach), math.floor(y / reach)
        best = None
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if j == k:
                        continue
                    dx, dy = pts[j][0] - x, pts[j][1] - y
                    along = abs(dx * tx + dy * ty)
                    across = abs(dx * ty - dy * tx)
                    if across > 0.35 * along:
                        continue                 # another row, not this one
                    d = math.hypot(dx, dy)
                    if d > 1e-6 and (best is None or d < best):
                        best = d
        if best is not None and best <= reach:
            gaps.append(best)
    if not gaps:
        return None
    gaps.sort()
    return gaps[len(gaps) // 2]


def nearest_pitch(value: float, extra=(), share: float = 0.04) -> float:
    """A measured spacing put on the iron it was punched with: the nearest
    standard pitch (or one of ``extra``) within ``share`` of it; otherwise
    the value itself, to the nearest 0.05 mm."""
    best = None
    for p in list(STANDARD_PITCHES) + [float(e) for e in extra]:
        if abs(value - p) <= max(0.05, share * p):
            if best is None or abs(value - p) < abs(value - best):
                best = p
    return best if best is not None else round(value * 20.0) / 20.0

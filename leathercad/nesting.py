"""Shape-aware nesting: pack pattern pieces onto a leather sheet.

Every piece's REAL outline (not its bounding box) is rasterised into per-row
integer bitmasks a couple of millimetres per cell, dilated by half the part
gap, then greedily placed bottom-left-first onto an occupancy grid of the
sheet. Integers make the collision test a handful of ANDs per row, so a
concave piece (a gusset, a flap) can genuinely tuck into another piece's
hollow instead of blocking out its whole bounding box.

This is a greedy first-fit packer, not an optimiser: it gets close to what a
patient human does laying out pattern pieces, deterministically and in
milliseconds. Pieces are tried biggest-first, each in 0° and (optionally)
90°, and take the lowest-leftmost pocket they fit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2

_EPS = 1e-9


@dataclass
class NestPiece:
    """One thing to place: all its outlines in world mm, moved as a rigid unit.

    ``outlines`` is a list of (points, closed) — a piece may be several shapes
    (a panel plus its slots) that travel together.
    """
    key: object
    outlines: List[Tuple[List[Vec2], bool]]
    allow_rotate: bool = True


@dataclass
class Placement:
    key: object
    dx: float = 0.0          # world translation to apply (after any rotation)
    dy: float = 0.0
    rotated: bool = False    # rotate 90 deg CCW about ``pivot`` first
    pivot: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))


def _bbox(outlines) -> Tuple[float, float, float, float]:
    xs = [p.x for pts, _c in outlines for p in pts]
    ys = [p.y for pts, _c in outlines for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def rotate90(p: Vec2, c: Vec2) -> Vec2:
    """90 deg counter-clockwise about ``c``."""
    return Vec2(c.x - (p.y - c.y), c.y + (p.x - c.x))


def _rot_outlines(outlines, c: Vec2):
    return [([rotate90(p, c) for p in pts], closed) for pts, closed in outlines]


def _rasterize(outlines, pad_cells: int, cell: float):
    """Rasterise a piece into per-row int bitmasks (bit k = column k).

    Returns (rows, x0, y0, w, h): grid geometry in world mm. The grid is
    padded by ``pad_cells`` on every side so the gap dilation stays inside.
    Closed rings are filled by even-odd scanline; every edge is also walked
    so features thinner than a cell still mark their cells.
    """
    xmin, ymin, xmax, ymax = _bbox(outlines)
    x0 = xmin - pad_cells * cell
    y0 = ymin - pad_cells * cell
    w = int(math.ceil((xmax - x0) / cell)) + pad_cells + 1
    h = int(math.ceil((ymax - y0) / cell)) + pad_cells + 1
    rows = [0] * h

    for pts, closed in outlines:
        if closed and len(pts) >= 3:
            ring = pts[:-1] if (pts[0] - pts[-1]).length() < 1e-9 else pts
            n = len(ring)
            for r in range(h):
                y = y0 + (r + 0.5) * cell
                xs = []
                for i in range(n):
                    a, b = ring[i], ring[(i + 1) % n]
                    if (a.y <= y < b.y) or (b.y <= y < a.y):
                        xs.append(a.x + (y - a.y) * (b.x - a.x) / (b.y - a.y))
                xs.sort()
                for i in range(0, len(xs) - 1, 2):
                    c0 = max(0, int((xs[i] - x0) / cell))
                    c1 = min(w - 1, int((xs[i + 1] - x0) / cell))
                    if c1 >= c0:
                        rows[r] |= ((1 << (c1 - c0 + 1)) - 1) << c0
        # walk the edges (covers open polylines and razor-thin fill misses)
        seq = pts
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            steps = max(1, int((b - a).length() / (cell * 0.5)))
            for s in range(steps + 1):
                t = s / steps
                cx = int((a.x + t * (b.x - a.x) - x0) / cell)
                cy = int((a.y + t * (b.y - a.y) - y0) / cell)
                if 0 <= cy < h and 0 <= cx < w:
                    rows[cy] |= 1 << cx
    return rows, x0, y0, w, h


def _dilate(rows: List[int], d: int) -> List[int]:
    """Grow the mask by ``d`` cells in every direction (square element)."""
    if d <= 0:
        return rows
    horiz = []
    for m in rows:
        g = m
        for _ in range(d):
            g |= (g << 1) | (g >> 1)
        horiz.append(g)
    h = len(rows)
    out = []
    for r in range(h):
        g = 0
        for k in range(max(0, r - d), min(h, r + d + 1)):
            g |= horiz[k]
        out.append(g)
    return out


def _trim(rows: List[int]):
    """Drop empty leading/trailing rows and columns; return (rows, dc, dr, w)."""
    r0 = 0
    while r0 < len(rows) and rows[r0] == 0:
        r0 += 1
    r1 = len(rows)
    while r1 > r0 and rows[r1 - 1] == 0:
        r1 -= 1
    rows = rows[r0:r1]
    if not rows:
        return [], 0, r0, 0
    combined = 0
    for m in rows:
        combined |= m
    c0 = (combined & -combined).bit_length() - 1     # lowest set bit
    rows = [m >> c0 for m in rows]
    w = max(m.bit_length() for m in rows)
    return rows, c0, r0, w


def nest(pieces: Sequence[NestPiece], sheet_w: float, sheet_h: float, *,
         margin: float = 5.0, spacing: float = 3.0, cell: float = 2.0,
         sheet_x: float = 0.0, sheet_y: float = 0.0):
    """Pack ``pieces`` into the sheet. Returns (placements, unplaced_keys,
    used_fraction). Coordinates are world mm; the sheet's lower-left corner
    sits at (sheet_x, sheet_y). Pieces keep >= ``spacing`` between outlines
    and >= ``margin`` from the sheet edge.
    """
    usable_w = sheet_w - 2.0 * margin
    usable_h = sheet_h - 2.0 * margin
    if usable_w <= cell or usable_h <= cell:
        return [], [p.key for p in pieces], 0.0
    # keep the grid a sane size whatever the sheet dimensions
    while usable_w / cell > 1200 or usable_h / cell > 1200:
        cell *= 2.0
    cols = int(usable_w // cell)
    rows_n = int(usable_h // cell)
    occ = [0] * rows_n
    gap = max(1, int(math.ceil(spacing / 2.0 / cell)))

    def mask_for(outlines):
        raw, x0, y0, _w, _h = _rasterize(outlines, gap, cell)
        fat = _dilate(raw, gap)
        m, dc, dr, w = _trim(fat)
        # world position of the trimmed mask's cell (0,0)
        return m, x0 + dc * cell, y0 + dr * cell, w

    def find_spot(mask, w):
        h = len(mask)
        if w > cols or h > rows_n:
            return None
        for oy in range(rows_n - h + 1):
            for ox in range(cols - w + 1):
                ok = True
                for k in range(h):
                    if (occ[oy + k] >> ox) & mask[k]:
                        ok = False
                        break
                if ok:
                    return ox, oy
        return None

    order = sorted(pieces, key=lambda p: -(lambda b: (b[2] - b[0])
                                           * (b[3] - b[1]))(_bbox(p.outlines)))
    placements, unplaced = [], []
    cells_used = 0
    for piece in order:
        xmin, ymin, xmax, ymax = _bbox(piece.outlines)
        pivot = Vec2(0.5 * (xmin + xmax), 0.5 * (ymin + ymax))
        options = [(piece.outlines, False)]
        if piece.allow_rotate:
            options.append((_rot_outlines(piece.outlines, pivot), True))
        best = None                     # (oy, ox, rotated, mask, mx0, my0)
        for outlines, rot in options:
            mask, mx0, my0, w = mask_for(outlines)
            if not mask:
                continue
            spot = find_spot(mask, w)
            if spot is not None:
                cand = (spot[1], spot[0], rot, mask, mx0, my0)
                if best is None or cand[:2] < best[:2]:
                    best = cand
        if best is None:
            unplaced.append(piece.key)
            continue
        oy, ox, rot, mask, mx0, my0 = best
        for k in range(len(mask)):      # claim the cells
            occ[oy + k] |= mask[k] << ox
            cells_used += bin(mask[k]).count("1")
        placements.append(Placement(
            key=piece.key,
            dx=(sheet_x + margin + ox * cell) - mx0,
            dy=(sheet_y + margin + oy * cell) - my0,
            rotated=rot, pivot=pivot))
    used = cells_used * cell * cell / max(sheet_w * sheet_h, _EPS)
    return placements, unplaced, min(1.0, used)

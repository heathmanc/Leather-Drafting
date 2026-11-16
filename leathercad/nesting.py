"""Shape-aware nesting: pack pattern pieces onto a leather sheet -- or onto
printer pages for a 1:1 print.

Every piece's REAL outline (not its bounding box) is rasterised into per-row
integer bitmasks a millimetre or two per cell, dilated by half the part
gap, then greedily placed bottom-left-first (or top-left-first) onto an
occupancy grid of the sheet. Integers make the collision test a handful of
ANDs per row, so a concave piece (a gusset, a flap) can genuinely tuck into
another piece's hollow instead of blocking out its whole bounding box.

The gap is a true minimum: every cell an outline passes through is marked,
so two placed pieces are never closer than twice the dilation, which is at
least the gap asked for. (Marking cells sampled along each edge could step
over the corner of a cell and let a neighbour in up to a cell closer.)

One pass is a greedy first-fit packer: pieces are placed one after another,
each in the lowest-leftmost pocket it fits (``Packer``; ``nest`` is a single
pass, biggest first). Which pocket a piece finds depends on what went
before, so ``nest_best`` packs many ways -- the pieces in different orders,
filled row by row and column by column, every piece in all four quarter
turns -- and keeps the best layout: the most pieces, then the most leather,
then the tightest. ``nest_smallest`` does that on strips of several widths
and keeps the layout that needs the smallest sheet. The tries are the same
every time -- about a second for a wallet's worth of pieces -- and a time
budget cuts a big job short. Progress is reported and the work can be
cancelled.

``group_pieces`` decides what travels together -- a move-group, a panel and
the slots inside it, the holes, seams, lettering and fold lines on a piece.
``layout_pages`` lays pieces out on printer pages: every piece that fits on
a page is printed whole on one page, and pieces too big for a page are
tiled across as few pages as possible, sharing them with whatever else fits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import Vec2, point_in_polygon

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
    dx: float = 0.0          # world translation to apply (after any turning)
    dy: float = 0.0
    rotated: bool = False    # turned a quarter (90 or 270 deg): sides swap
    pivot: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))
    turns: int = -1          # quarter turns CCW about ``pivot``; -1: from
                             # ``rotated`` (one turn when set)

    def __post_init__(self):
        if self.turns < 0:
            self.turns = 1 if self.rotated else 0
        self.turns %= 4
        self.rotated = self.turns % 2 == 1

    @property
    def degrees(self) -> int:
        return 90 * self.turns

    def apply(self, p: Vec2) -> Vec2:
        """Where the point ``p`` of the piece goes."""
        for _ in range(self.turns):
            p = rotate90(p, self.pivot)
        return Vec2(p.x + self.dx, p.y + self.dy)

    def turn(self, v: Vec2) -> Vec2:
        """Where the direction ``v`` (a hole's tangent) points afterwards."""
        for _ in range(self.turns):
            v = Vec2(-v.y, v.x)
        return v


def _bbox(outlines) -> Tuple[float, float, float, float]:
    xs = [p.x for pts, _c in outlines for p in pts]
    ys = [p.y for pts, _c in outlines for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def rotate90(p: Vec2, c: Vec2) -> Vec2:
    """90 deg counter-clockwise about ``c``."""
    return Vec2(c.x - (p.y - c.y), c.y + (p.x - c.x))


def _rot_outlines(outlines, c: Vec2):
    return [([rotate90(p, c) for p in pts], closed) for pts, closed in outlines]


def _turned(outlines, c: Vec2, k: int):
    """``outlines`` turned ``k`` quarter turns CCW about ``c`` (exactly:
    no trigonometry, so a piece turned twice is the same piece)."""
    for _ in range(k % 4):
        outlines = _rot_outlines(outlines, c)
    return outlines


def _on_grid(v: float) -> float:
    """``v`` (in cells) put exactly on a cell boundary when it is within
    rounding noise of one. A piece placed on the grid and marked again where
    it now lies has its edges a hair either side of the boundaries they sat
    on; without this, those edges took the next cell too, and a gap the
    piece had fitted exactly was one cell too narrow for it to go back."""
    r = round(v)
    return float(r) if abs(v - r) < 1e-7 else v


def _mark_segment(rows: List[int], ax: float, ay: float, bx: float, by: float,
                  w: int, h: int) -> None:
    """Set the bit of every cell the segment (ax, ay)-(bx, by), given in cell
    units, passes through or touches -- a supercover walk, so no point of
    the segment lies in an unmarked cell."""
    ax, ay, bx, by = _on_grid(ax), _on_grid(ay), _on_grid(bx), _on_grid(by)
    ix, iy = math.floor(ax), math.floor(ay)
    jx, jy = math.floor(bx), math.floor(by)

    def mark(cx, cy):
        if 0 <= cy < h and 0 <= cx < w:
            rows[cy] |= 1 << cx

    mark(ix, iy)
    dx, dy = bx - ax, by - ay
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    inf = float("inf")
    if abs(dx) > 1e-12:
        tmx = ((ix + 1 if dx > 0 else ix) - ax) / dx     # to the next column line
        tdx = 1.0 / abs(dx)
    else:
        tmx = tdx = inf
    if abs(dy) > 1e-12:
        tmy = ((iy + 1 if dy > 0 else iy) - ay) / dy     # to the next row line
        tdy = 1.0 / abs(dy)
    else:
        tmy = tdy = inf
    steps = abs(jx - ix) + abs(jy - iy)
    while steps > 0:
        if tmx < tmy - 1e-12:
            ix += sx
            tmx += tdx
            steps -= 1
        elif tmy < tmx - 1e-12:
            iy += sy
            tmy += tdy
            steps -= 1
        else:                       # through a cell's corner: its sides too
            mark(ix + sx, iy)
            mark(ix, iy + sy)
            ix += sx
            iy += sy
            tmx += tdx
            tmy += tdy
            steps -= 2
        mark(ix, iy)
    mark(jx, jy)


def _paint(rows: List[int], outlines, x0: float, y0: float, cell: float,
           w: int, h: int) -> None:
    """Fill each closed ring (even-odd scanline over its own rows) and mark
    every cell an edge -- closing edge included -- passes through."""
    for pts, closed in outlines:
        if closed and len(pts) >= 3:
            ring = pts[:-1] if (pts[0] - pts[-1]).length() < 1e-9 else pts
            n = len(ring)
            ylo = min(p.y for p in ring)
            yhi = max(p.y for p in ring)
            r_lo = max(0, int((ylo - y0) / cell) - 1)
            r_hi = min(h - 1, int((yhi - y0) / cell) + 1)
            for r in range(r_lo, r_hi + 1):
                y = y0 + (r + 0.5) * cell
                xs = []
                for i in range(n):
                    a, b = ring[i], ring[(i + 1) % n]
                    if (a.y <= y < b.y) or (b.y <= y < a.y):
                        xs.append(a.x + (y - a.y) * (b.x - a.x) / (b.y - a.y))
                xs.sort()
                for i in range(0, len(xs) - 1, 2):
                    c0 = max(0, math.floor(_on_grid((xs[i] - x0) / cell)))
                    c1 = min(w - 1, math.floor(_on_grid((xs[i + 1] - x0) / cell)))
                    if c1 >= c0:
                        rows[r] |= ((1 << (c1 - c0 + 1)) - 1) << c0
        seq = list(pts)
        if closed and len(seq) >= 2 and (seq[0] - seq[-1]).length() > 1e-9:
            seq.append(seq[0])          # the closing edge counts too
        if len(seq) == 1:
            seq = seq * 2               # a lone point marks its own cell
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            _mark_segment(rows, (a.x - x0) / cell, (a.y - y0) / cell,
                          (b.x - x0) / cell, (b.y - y0) / cell, w, h)


def _rasterize(outlines, pad_cells: int, cell: float):
    """Rasterise a piece into per-row int bitmasks (bit k = column k).

    Returns (rows, x0, y0, w, h): grid geometry in world mm. The grid is
    padded by ``pad_cells`` on every side so the gap dilation stays inside.
    Closed rings are filled by even-odd scanline; every edge also marks each
    cell it passes through, so features thinner than a cell still count and
    no outline point is ever left in an unmarked cell.
    """
    xmin, ymin, xmax, ymax = _bbox(outlines)
    x0 = xmin - pad_cells * cell
    y0 = ymin - pad_cells * cell
    w = int(math.ceil((xmax - x0) / cell)) + pad_cells + 1
    h = int(math.ceil((ymax - y0) / cell)) + pad_cells + 1
    rows = [0] * h
    _paint(rows, outlines, x0, y0, cell, w, h)
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


class NestCancelled(Exception):
    """Raised by ``nest`` when its progress callback asks it to stop."""


def _bit_runs(m: int):
    """The runs of set bits in ``m`` as (first_bit, length) pairs."""
    out = []
    base = 0
    while m:
        low = (m & -m).bit_length() - 1        # first set bit
        m >>= low
        base += low
        length = (~m & (m + 1)).bit_length() - 1   # first clear bit = run end
        out.append((base, length))
        m >>= length
        base += length
    return out


def _spread_right(occ: int, length: int) -> int:
    """``occ | occ>>1 | ... | occ>>(length-1)``: bit ``i`` is set when any of
    the ``length`` columns from ``i`` is occupied. Doubling keeps it to
    log2(length) shifts."""
    d = occ
    done = 1
    while done * 2 <= length:
        d |= d >> done
        done *= 2
    if done < length:
        d |= d >> (length - done)
    return d


def cell_for_gap(spacing: float, target: float = 2.0) -> float:
    """A grid cell near ``target`` mm that half the gap is a whole number
    of, so the gap kept between pieces is the one asked for instead of
    being rounded up to the next whole cell (an 8 mm gap on 2.5 mm cells
    would come out 10 mm)."""
    half = spacing / 2.0
    if half <= _EPS:
        return target
    return half / max(1, math.ceil(half / target - 1e-9))


def _grid_cell(usable_w: float, usable_h: float, cell: float) -> float:
    """The cell a sheet really uses: doubled until the grid is a sane size."""
    while usable_w / cell > 1200 or usable_h / cell > 1200:
        cell *= 2.0
    return cell


class Packer:
    """One sheet being filled, a few pieces at a time: what's placed so far
    stays put, ``block_rect`` keeps pieces out of parts of it and
    ``block_outlines`` round pieces left where they are.

    Pieces keep >= ``spacing`` between outlines and >= ``margin`` from the
    sheet's edge, whose lower-left corner sits at (sheet_x, sheet_y).
    ``from_top`` fills the sheet from its top edge down instead of from its
    bottom edge up (a printed page reads from the top). ``turns`` are the
    quarter turns a piece that may turn is tried in: 0 and 90 deg, or all
    four."""

    def __init__(self, sheet_w: float, sheet_h: float, *, margin: float = 5.0,
                 spacing: float = 3.0, cell: float = 2.0, sheet_x: float = 0.0,
                 sheet_y: float = 0.0, from_top: bool = False,
                 mask_cache: Optional[dict] = None,
                 turns: Sequence[int] = (0, 1)):
        self.sheet_w, self.sheet_h = sheet_w, sheet_h
        usable_w = sheet_w - 2.0 * margin
        usable_h = sheet_h - 2.0 * margin
        self.ok = usable_w > cell and usable_h > cell
        cell = _grid_cell(usable_w, usable_h, cell)
        self.cell = cell
        self.cols = int(usable_w // cell) if self.ok else 0
        self.rows_n = int(usable_h // cell) if self.ok else 0
        self.occ = [0] * self.rows_n
        # rows below ``lo`` and from ``hi`` up are known to be empty
        self.lo, self.hi = self.rows_n, 0
        self.gap = max(1, int(math.ceil(spacing / 2.0 / cell - 1e-9)))
        self.x0, self.y0 = sheet_x + margin, sheet_y + margin
        self.from_top = from_top
        self.turns = tuple(turns)
        self.cells_used = 0
        self._masks = mask_cache if mask_cache is not None else {}

    def used(self) -> float:
        """The share of the sheet blocked out, gaps included."""
        return min(1.0, self.cells_used * self.cell * self.cell
                   / max(self.sheet_w * self.sheet_h, _EPS))

    def block_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Keep pieces out of the world rectangle (x0, y0)-(x1, y1): every
        cell it reaches is taken (a piece's own half gap still applies)."""
        if not self.ok:
            return
        c0 = max(0, int(math.floor((x0 - self.x0) / self.cell)))
        c1 = min(self.cols - 1, int(math.ceil((x1 - self.x0) / self.cell)) - 1)
        r0 = max(0, int(math.floor((y0 - self.y0) / self.cell)))
        r1 = min(self.rows_n - 1, int(math.ceil((y1 - self.y0) / self.cell)) - 1)
        if c1 < c0 or r1 < r0:
            return
        band = ((1 << (c1 - c0 + 1)) - 1) << c0
        for r in range(r0, r1 + 1):
            self.occ[r] |= band
        self.lo = min(self.lo, r0)
        self.hi = max(self.hi, r1 + 1)

    def block_outlines(self, outlines) -> None:
        """Pieces staying where they are (outlines in world mm, anywhere --
        not on the grid's cells): what's placed keeps the full gap from
        them."""
        if not self.ok or not outlines:
            return
        g = self.gap
        w, h = self.cols + 2 * g, self.rows_n + 2 * g
        rows = [0] * h
        _paint(rows, outlines, self.x0 - g * self.cell, self.y0 - g * self.cell,
               self.cell, w, h)
        rows = _dilate(rows, g)
        keep = (1 << self.cols) - 1
        for r in range(self.rows_n):
            m = (rows[r + g] >> g) & keep
            if m:
                self.occ[r] |= m
                self.lo = min(self.lo, r)
                self.hi = max(self.hi, r + 1)

    def collides(self, outlines, slack: int = 1) -> bool:
        """Whether these outlines, where they are, come closer than the gap
        (less ``slack`` cells, for pieces not lined up with this grid) to
        what's blocked out."""
        if not self.ok or not outlines:
            return False
        g = max(0, self.gap - slack)
        w, h = self.cols + 2 * g, self.rows_n + 2 * g
        rows = [0] * h
        _paint(rows, outlines, self.x0 - g * self.cell, self.y0 - g * self.cell,
               self.cell, w, h)
        rows = _dilate(rows, g)
        keep = (1 << self.cols) - 1
        return any((rows[r + g] >> g) & keep & self.occ[r]
                   for r in range(self.rows_n))

    def take_occupancy(self, other: "Packer") -> None:
        """Start from what ``other`` (the same sheet) has blocked out."""
        self.occ = list(other.occ)
        self.lo, self.hi = other.lo, other.hi

    def _mask(self, piece, k, pivot):
        key = (id(piece), k, self.cell, self.gap)
        got = self._masks.get(key)
        if got is not None and got[0] is piece:
            return got[1]
        raw, x0, y0, _w, _h = _rasterize(_turned(piece.outlines, pivot, k),
                                          self.gap, self.cell)
        fat = _dilate(raw, self.gap)
        m, dc, dr, w = _trim(fat)
        out = (m, x0 + dc * self.cell, y0 + dr * self.cell, w)
        self._masks[key] = (piece, out)
        return out

    def _spot(self, mask, w, prefer: str = "row"):
        """Where ``mask`` fits: the first free row (lowest, or highest
        ``from_top``), then its leftmost spot -- or with ``prefer="col"`` the
        leftmost free column, then its first row.

        Each sheet row answers "which columns can this mask row start at"
        as one bitmask -- a run of ``L`` mask bits is blocked wherever any of
        the next ``L`` sheet cells is taken, which is the occupancy spread
        right by ``L`` -- so a row is cleared or rejected in a few big-int
        operations instead of a Python loop over every column."""
        h = len(mask)
        cols, rows_n, occ = self.cols, self.rows_n, self.occ
        if w > cols or h > rows_n:
            return None
        runs = [_bit_runs(mk) for mk in mask]
        full = (1 << (cols - w + 1)) - 1
        spread_memo: dict = {}
        rng = (range(rows_n - h, -1, -1) if self.from_top
               else range(rows_n - h + 1))
        best = None                      # column-first: (ox, oy) so far
        for oy in rng:
            if oy >= self.hi or oy + h <= self.lo:
                return 0, oy              # nothing placed in these rows: free
            valid = full if best is None else full & ((1 << best[0]) - 1)
            if not valid:
                break                     # nothing left of the best so far
            for k in range(h):
                row = occ[oy + k]
                if not row:
                    continue
                for j0, length in runs[k]:
                    key = (oy + k, length)
                    d = spread_memo.get(key)
                    if d is None:
                        d = _spread_right(row, length)
                        spread_memo[key] = d
                    valid &= ~(d >> j0)
                    if not valid:
                        break
                if not valid:
                    break
            if valid:
                ox = (valid & -valid).bit_length() - 1
                if prefer != "col" or ox == 0:
                    return ox, oy
                best = (ox, oy)
        return best

    def place(self, pieces: Sequence[NestPiece], progress=None, order=None,
              prefer: str = "row"):
        """Place as many of ``pieces`` as fit. Returns (placements,
        unplaced_keys). ``order``: a sort key for the pieces, or None for
        biggest first; ``prefer``: fill row by row ("row") or column by
        column ("col"). ``progress(done, total)`` is called before each
        piece; returning False raises ``NestCancelled``."""
        if not self.ok:
            return [], [p.key for p in pieces]
        if order is None:
            def order(p):
                b = _bbox(p.outlines)
                return -(b[2] - b[0]) * (b[3] - b[1])
        seq = sorted(pieces, key=order)
        placements, unplaced = [], []
        for n_done, piece in enumerate(seq):
            if progress is not None and not progress(n_done, len(seq)):
                raise NestCancelled()
            xmin, ymin, xmax, ymax = _bbox(piece.outlines)
            pivot = Vec2(0.5 * (xmin + xmax), 0.5 * (ymin + ymax))
            best = None              # (rank, oy, ox, k, mask, mx0, my0)
            for k in (self.turns if piece.allow_rotate else (0,)):
                mask, mx0, my0, w = self._mask(piece, k, pivot)
                if not mask:
                    continue
                spot = self._spot(mask, w, prefer)
                if spot is None:
                    continue
                ox, oy = spot
                row = -oy if self.from_top else oy
                rank = (ox, row) if prefer == "col" else (row, ox)
                if best is None or rank < best[0]:
                    best = (rank, oy, ox, k, mask, mx0, my0)
            if best is None:
                unplaced.append(piece.key)
                continue
            _rank, oy, ox, k, mask, mx0, my0 = best
            for i in range(len(mask)):      # claim the cells
                self.occ[oy + i] |= mask[i] << ox
                self.cells_used += bin(mask[i]).count("1")
            self.lo = min(self.lo, oy)
            self.hi = max(self.hi, oy + len(mask))
            placements.append(Placement(
                key=piece.key,
                dx=(self.x0 + ox * self.cell) - mx0,
                dy=(self.y0 + oy * self.cell) - my0,
                pivot=pivot, turns=k))
        if progress is not None:
            progress(len(seq), len(seq))    # done: the bar reaches the end
        return placements, unplaced


def nest(pieces: Sequence[NestPiece], sheet_w: float, sheet_h: float, *,
         margin: float = 5.0, spacing: float = 3.0, cell: float = 2.0,
         sheet_x: float = 0.0, sheet_y: float = 0.0, progress=None,
         from_top: bool = False):
    """One greedy pass (biggest first, lowest-leftmost spot, 0 or 90 deg):
    Returns (placements, unplaced_keys, used_fraction). Coordinates are
    world mm; the sheet's lower-left corner sits at (sheet_x, sheet_y).
    Pieces keep >= ``spacing`` between outlines and >= ``margin`` from the
    sheet edge. ``nest_best`` tries many passes and keeps the best.

    ``progress(done, total)`` is called after each piece; returning False
    stops the packing with ``NestCancelled``.
    """
    pk = Packer(sheet_w, sheet_h, margin=margin, spacing=spacing, cell=cell,
                sheet_x=sheet_x, sheet_y=sheet_y, from_top=from_top)
    if not pk.ok:
        return [], [p.key for p in pieces], 0.0
    placements, unplaced = pk.place(pieces, progress)
    return placements, unplaced, pk.used()


# ---------------------------------------------------------------------------
# Many passes, the best kept
# ---------------------------------------------------------------------------
ALL_TURNS = (0, 1, 2, 3)


def sheet_packer(sheet_w: float, sheet_h: float, *, margin: float,
                 spacing: float, cell: float, sheet_x: float = 0.0,
                 sheet_y: float = 0.0, from_top: bool = False,
                 mask_cache: Optional[dict] = None,
                 turns: Sequence[int] = ALL_TURNS) -> Packer:
    """A ``Packer`` for a sheet whose pieces keep ``margin`` from its edge,
    outline to edge. A piece's mask carries half the gap round its outline,
    so the grid reaches that half gap past the margin; a ``Packer`` given
    the margin itself keeps the outlines a further half gap in (9 mm from
    the edge for a 5 mm margin and an 8 mm gap)."""
    half = spacing / 2.0
    return Packer(sheet_w - 2.0 * margin + spacing,
                  sheet_h - 2.0 * margin + spacing, margin=0.0,
                  spacing=spacing, cell=cell, sheet_x=sheet_x + margin - half,
                  sheet_y=sheet_y + margin - half, from_top=from_top,
                  mask_cache=mask_cache, turns=turns)


@dataclass
class NestResult:
    """The best layout found: where each placed piece goes, what didn't fit,
    how much of the sheet it blocks out, how many layouts were tried, and
    the sheet it was made for ``(x, y, width, height)``."""
    placements: List[Placement] = field(default_factory=list)
    unplaced: list = field(default_factory=list)
    used: float = 0.0
    tries: int = 0
    sheet: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    envelope: Optional[Tuple[float, float, float, float]] = None


def _sizes(pieces):
    out = {}
    for p in pieces:
        b = _bbox(p.outlines)
        out[id(p)] = (b[2] - b[0], b[3] - b[1])
    return out


def _orders(pieces) -> List[list]:
    """Piece orders worth a try: biggest area first, longest side first,
    biggest round the edge, widest, tallest -- then the area order shaken
    up a little, again and again (a different first few pieces often frees
    a pocket the next one needed)."""
    import random
    S = _sizes(pieces)
    keys = [lambda p: -S[id(p)][0] * S[id(p)][1],
            lambda p: (-max(S[id(p)]), -min(S[id(p)])),
            lambda p: -(S[id(p)][0] + S[id(p)][1]),
            lambda p: (-S[id(p)][0], -S[id(p)][1]),
            lambda p: (-S[id(p)][1], -S[id(p)][0])]
    out, seen = [], set()

    def add(seq):
        sig = tuple(id(p) for p in seq)
        if sig not in seen:
            seen.add(sig)
            out.append(seq)
    for k in keys:
        add(sorted(pieces, key=k))
    base = out[0] if out else list(pieces)
    rng = random.Random(7919 + len(pieces))       # the same tries every time
    for _ in range(24):
        seq = list(base)
        for i in range(len(seq) - 1):
            if rng.random() < 0.35:
                seq[i], seq[i + 1] = seq[i + 1], seq[i]
        add(seq)
    return out


def leather_area(outlines) -> float:
    """The leather a piece takes, mm²: the area inside its closed outlines,
    a slot inside a panel taken out (its bounding box's, when it has no
    closed outline)."""
    rings = []
    for pts, closed in outlines:
        if closed and len(pts) >= 3:
            xs, ys = [p.x for p in pts], [p.y for p in pts]
            rings.append((_ring_area(pts), (min(xs), min(ys), max(xs), max(ys)),
                          pts))
    rings.sort(key=lambda r: -r[0])          # only a bigger ring holds one
    a = 0.0
    for i, (area, _box, pts) in enumerate(rings):
        p = pts[0]
        depth = 0
        for _a, (x0, y0, x1, y1), other in rings[:i]:
            if x0 <= p.x <= x1 and y0 <= p.y <= y1 and point_in_polygon(p, other):
                depth += 1
        a += -area if depth % 2 else area
    if a > _EPS:
        return a
    b = _bbox(outlines)
    return (b[2] - b[0]) * (b[3] - b[1])


def _piece_area(p) -> float:
    a = getattr(p, "_leather", None)          # worked out once a piece
    if a is None:
        a = p._leather = leather_area(p.outlines)
    return a


def _envelope(pieces_by_key, placements, extra=None):
    box = extra
    for pl in placements:
        p = pieces_by_key[pl.key]
        b = _bbox(p.outlines)
        corners = [pl.apply(Vec2(x, y)) for x in (b[0], b[2]) for y in (b[1], b[3])]
        xs, ys = [q.x for q in corners], [q.y for q in corners]
        nb = (min(xs), min(ys), max(xs), max(ys))
        box = nb if box is None else (min(box[0], nb[0]), min(box[1], nb[1]),
                                      max(box[2], nb[2]), max(box[3], nb[3]))
    return box


def nest_best(pieces: Sequence[NestPiece], sheet_w: float, sheet_h: float, *,
              margin: float = 5.0, spacing: float = 3.0,
              cell: Optional[float] = None, sheet_x: float = 0.0,
              sheet_y: float = 0.0, obstacles=(),
              budget: Optional[float] = 1.5, progress=None,
              from_top: bool = False, turns: Sequence[int] = ALL_TURNS,
              mask_cache: Optional[dict] = None) -> NestResult:
    """Pack ``pieces`` many ways -- different orders, row by row and column
    by column, every quarter turn -- and keep the best: the most pieces
    placed, then the most leather placed (a big piece before a small one
    when only one fits), then the tightest layout, then the fewest pieces
    turned. ``obstacles`` are outlines (world mm) of pieces staying where
    they are. Tries stop once ``budget`` seconds have gone (after at least
    one; None: every try, the same answer however fast the machine).
    ``progress(done, total)`` counts the tries; returning False raises
    ``NestCancelled``. ``mask_cache``: a dict shared between calls for the
    same pieces, so each is rasterised once."""
    import time
    cell = cell or cell_for_gap(spacing)
    sheet = (sheet_x, sheet_y, sheet_w, sheet_h)
    by_key = {p.key: p for p in pieces}
    leather = {p.key: _piece_area(p) for p in pieces}
    masks: dict = mask_cache if mask_cache is not None else {}

    def packer():
        return sheet_packer(sheet_w, sheet_h, margin=margin, spacing=spacing,
                            cell=cell, sheet_x=sheet_x, sheet_y=sheet_y,
                            from_top=from_top, mask_cache=masks, turns=turns)
    base = packer()
    if not base.ok:
        return NestResult(unplaced=[p.key for p in pieces], sheet=sheet)
    if obstacles:
        base.block_outlines(obstacles)
    attempts = [(seq, prefer) for seq in _orders(pieces)
                for prefer in ("row", "col")]
    best, best_score = None, None
    t0 = time.perf_counter()
    tries = 0
    for i, (seq, prefer) in enumerate(attempts):
        if progress is not None and not progress(i, len(attempts)):
            raise NestCancelled()
        pk = packer()
        pk.take_occupancy(base)
        pl, un = pk.place(seq, order=lambda p, _o={id(q): j for j, q in
                                                    enumerate(seq)}: _o[id(p)],
                          prefer=prefer)
        tries += 1
        env = _envelope(by_key, pl)
        area = 0.0 if env is None else (env[2] - env[0]) * (env[3] - env[1])
        longest = 0.0 if env is None else max(env[2] - env[0], env[3] - env[1])
        placed = sum(leather[q.key] for q in pl)
        turned = sum(1 for q in pl if q.turns)
        score = (len(pl), round(placed, 1), -round(area, 3), -round(longest, 3),
                 -turned)
        if best is None or score > best_score:
            used = pk.cells_used * pk.cell * pk.cell / max(sheet_w * sheet_h,
                                                           _EPS)
            best = NestResult(pl, un, min(1.0, used), 0, sheet, env)
            best_score = score
        if budget is not None and time.perf_counter() - t0 > budget:
            break
    if progress is not None:
        progress(len(attempts), len(attempts))
    best.tries = tries
    return best


def _long_way(s) -> float:
    return max(s[2], s[3]) / max(min(s[2], s[3]), _EPS)


def _leather_needed(s) -> float:
    """A sheet's area, weighed for its shape: a strip longer than twice its
    width counts a quarter more for each width past that -- a 110 x 757 mm
    strip of a wallet's pieces is no piece of leather anyone buys."""
    return s[2] * s[3] * (1.0 + 0.25 * max(0.0, _long_way(s) - 2.0))


def _smaller(a, b) -> bool:
    """Whether sheet ``a`` (x, y, w, h) beats sheet ``b``: less leather (see
    ``_leather_needed``), or within half a percent of it and squarer (easier
    to buy and handle)."""
    area_a, area_b = _leather_needed(a), _leather_needed(b)
    if area_a < area_b * 0.995:
        return True
    if area_a > area_b * 1.005:
        return False
    if abs(_long_way(a) - _long_way(b)) > 1e-9:
        return _long_way(a) < _long_way(b)
    return area_a < area_b - 1e-6


def nest_smallest(pieces: Sequence[NestPiece], *, margin: float = 5.0,
                  spacing: float = 3.0, cell: Optional[float] = None,
                  sheet_x: float = 0.0, sheet_y: float = 0.0,
                  max_w: Optional[float] = None, max_h: Optional[float] = None,
                  obstacles=(), budget: Optional[float] = 2.5, progress=None,
                  turns: Sequence[int] = ALL_TURNS) -> NestResult:
    """The smallest sheet that takes every piece, ``margin`` clear all
    round: strips of several widths are each packed many ways
    (``nest_best``), and the layout needing the least area is kept. Its
    ``sheet`` is that border, its lower-left corner at (sheet_x, sheet_y);
    ``max_w`` / ``max_h`` cap it (the leather you have). A layout with
    pieces left over is returned only when no width takes them all.
    ``obstacles``: outlines of pieces staying where they are, kept clear
    of. ``budget`` (seconds, or None for every try) is shared out between
    the widths, the likeliest first; ``progress(done, total)`` counts the
    tries."""
    cell = cell or cell_for_gap(spacing)
    if not pieces:
        return NestResult(sheet=(sheet_x, sheet_y, 0.0, 0.0))
    S = _sizes(pieces)
    may_turn = {id(p): p.allow_rotate for p in pieces}
    narrow = max((min(w, h) if may_turn[k] else w) for k, (w, h) in S.items())
    area = sum((w + spacing) * (h + spacing) for w, h in S.values())
    tallest = max(max(w, h) for w, h in S.values())
    room_w = None if max_w is None else max_w - 2 * margin
    room_h = None if max_h is None else max_h - 2 * margin
    widths = {narrow + 2 * cell}
    for f in (1.2, 1.45, 1.75, 2.1, 2.6, 3.2):
        widths.add(narrow * f + 2 * cell)
    for f in (0.8, 0.95, 1.1, 1.3):
        widths.add(max(narrow + 2 * cell, math.sqrt(area) * f))
    if room_w is not None:
        widths = {min(w, room_w) for w in widths} | {room_w}
    # the likeliest widths first -- near square -- so a budget running out
    # cuts the long shots
    square = math.sqrt(area)
    widths = sorted((w for w in widths if w > 2 * cell),
                    key=lambda w: (abs(math.log(w / square)), w))
    import time
    t0 = time.perf_counter()
    masks: dict = {}                  # each piece rasterised once, not a width
    best = best_left = None
    tries = 0
    steps = 100                       # progress steps a width

    def share(n):
        """The time left, shared out between the widths left."""
        if budget is None:
            return None
        left = budget - (time.perf_counter() - t0)
        return max(0.0, left) / max(1, len(widths) - n)

    def step(n):
        if progress is None:
            return None

        def tick(done, total):
            return progress(n * steps + (steps * done) // max(1, total),
                            len(widths) * steps)
        return tick
    for n, W in enumerate(widths):
        if progress is not None and not progress(n * steps,
                                                 len(widths) * steps):
            raise NestCancelled()
        if (budget is not None and best is not None
                and time.perf_counter() - t0 > budget):
            break                     # out of time, with a layout in hand
        H = area / W * 1.6 + tallest + spacing
        if room_h is not None:
            H = min(H, room_h)
        res = nest_best(pieces, W + 2 * margin, H + 2 * margin, margin=margin,
                        spacing=spacing, cell=cell, sheet_x=sheet_x,
                        sheet_y=sheet_y, obstacles=obstacles, budget=share(n),
                        turns=turns, progress=step(n), mask_cache=masks)
        tries += res.tries
        if res.unplaced and room_h is None:
            res = nest_best(pieces, W + 2 * margin, 2 * H + 2 * margin,
                            margin=margin, spacing=spacing, cell=cell,
                            sheet_x=sheet_x, sheet_y=sheet_y,
                            obstacles=obstacles, budget=share(n), turns=turns,
                            mask_cache=masks)
            tries += res.tries
        env = res.envelope
        if env is None:
            continue
        # the border round the layout, to the next whole millimetre
        sw = float(math.ceil(env[2] - sheet_x + margin - 1e-6))
        sh = float(math.ceil(env[3] - sheet_y + margin - 1e-6))
        if max_w is not None:
            sw = min(sw, max_w)
        if max_h is not None:
            sh = min(sh, max_h)
        res.sheet = (sheet_x, sheet_y, sw, sh)
        if res.unplaced:
            if best_left is None or len(res.placements) > len(best_left.placements):
                best_left = res
            continue
        if best is None or _smaller(res.sheet, best.sheet):
            best = res
    if progress is not None:
        progress(len(widths) * steps, len(widths) * steps)
    out = best or best_left or NestResult(unplaced=[p.key for p in pieces],
                                          sheet=(sheet_x, sheet_y, 0.0, 0.0))
    out.tries = tries
    return out


# ---------------------------------------------------------------------------
# What travels together
# ---------------------------------------------------------------------------
@dataclass
class PieceGroup:
    """What moves as one when pieces are nested: a piece, or the pieces of a
    move-group, with whatever sits on it. ``outlines`` are the shapes' world
    outlines as (points, closed); ``ring`` is the biggest closed one, what
    "inside the piece" is tested against."""
    shapes: list = field(default_factory=list)
    outlines: list = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    ring: Optional[list] = None
    holes: list = field(default_factory=list)
    seams: list = field(default_factory=list)
    texts: list = field(default_factory=list)

    def riders(self) -> list:
        return self.holes + self.seams + self.texts


def _ring_area(pts) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        a += p.x * q.y - q.x * p.y
    return abs(a) / 2.0


def _refresh(g: PieceGroup) -> None:
    g.bbox = _bbox(g.outlines)
    rings = [pts for pts, closed in g.outlines if closed and len(pts) >= 3]
    g.ring = max(rings, key=_ring_area) if rings else None


def _box_area(b) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


def group_pieces(shapes, holes=(), seams=(), texts=(), is_cut=None):
    """Sort what's being nested into the groups that travel as one.

    * Shapes of one move-group (``Ctrl+G``) are one piece.
    * A piece lying wholly inside another (a slot in a panel) joins it --
      unless, given ``is_cut`` (a test: is this shape on a cut layer?), it is
      a cut outline nearly as big as the one it lies in -- a copy of the
      piece, a lining -- which is a piece of its own (see ``pieces``).
    * An open line that runs onto a piece (a fold or score line, even one
      drawn a little past its edges) joins the piece it crosses.
    * Loose holes, seams and lettering go with their move-group, else with
      the smallest piece they sit inside.

    Returns ``(groups, (holes, seams, texts))``: the second part is what lies
    on no piece."""
    order, members = [], {}
    for sh in shapes:
        key = getattr(sh, "group_id", None) or ("one", id(sh))
        if key not in members:
            members[key] = []
            order.append(key)
        members[key].append(sh)
    groups: List[PieceGroup] = []
    by_gid: Dict[str, PieceGroup] = {}
    for key in order:
        outlines = []
        for m in members[key]:
            pts, _corners, closed = m.world_polyline()
            outlines.append(([Vec2(p.x, p.y) for p in pts], bool(closed)))
        if sum(len(pts) for pts, _c in outlines) < 2:
            continue
        g = PieceGroup(shapes=list(members[key]), outlines=outlines)
        _refresh(g)
        groups.append(g)
        if not isinstance(key, tuple):
            by_gid[key] = g
    host_of: Dict[int, PieceGroup] = {}       # a merged group -> where it went

    def home(g):
        while id(g) in host_of:
            g = host_of[id(g)]
        return g

    def containing(x, y, exclude=None):
        best = None
        for g in groups:
            if g is exclude or g.ring is None:
                continue
            b = g.bbox
            if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
                continue
            if point_in_polygon(Vec2(x, y), g.ring):
                area = _box_area(b)
                if best is None or area < best[0]:
                    best = (area, g)
        return best[1] if best else None

    def merge(g, host):
        host.shapes += g.shapes
        host.outlines += g.outlines
        _refresh(host)
        groups.remove(g)
        host_of[id(g)] = host

    from .pieces import is_own_piece

    def own_piece(g, host) -> bool:
        """``g`` (inside ``host``) is a piece of its own, not a hole in it."""
        if is_cut is None or g.ring is None or host.ring is None:
            return False
        if not all(is_cut(sh) for sh in g.shapes):
            return False
        return is_own_piece(_ring_area(g.ring), _ring_area(host.ring))

    # a piece fully inside another (a slot in a panel) merges into it
    for g in sorted(groups, key=lambda g: _box_area(g.bbox)):
        if g not in groups:
            continue
        b = g.bbox
        host = containing(0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]), exclude=g)
        if host is not None and host.bbox[0] <= b[0] and host.bbox[1] <= b[1] \
                and host.bbox[2] >= b[2] and host.bbox[3] >= b[3] \
                and not own_piece(g, host):
            merge(g, host)
    # an open line running onto a piece goes with it (a fold line drawn a
    # little past both edges has neither end on the piece: test along it)
    for g in list(groups):
        if g.ring is not None or g not in groups:
            continue
        probes = []
        for pts, _c in g.outlines:
            probes += pts[:1]
            for a, b in zip(pts, pts[1:]):
                probes += [a.lerp(b, t) for t in (0.25, 0.5, 0.75, 1.0)]
        hosts = [h for h in (containing(p.x, p.y, exclude=g) for p in probes)
                 if h is not None]
        if hosts:
            merge(g, min(hosts, key=lambda h: _box_area(h.bbox)))

    def attach(r, x, y, bucket) -> bool:
        gid = getattr(r, "group_id", None)
        g = home(by_gid[gid]) if gid in by_gid else None
        if g is None:
            g = containing(x, y)
        if g is None:
            return False
        getattr(g, bucket).append(r)
        return True

    left_h = [h for h in holes if not attach(h, h.point.x, h.point.y, "holes")]
    left_s = []
    for sl in seams:
        if not sl.points:
            continue
        mx = sum(p.x for p in sl.points) / len(sl.points)
        my = sum(p.y for p in sl.points) / len(sl.points)
        if not attach(sl, mx, my, "seams"):
            left_s.append(sl)
    left_t = [tx for tx in texts
              if not attach(tx, tx.transform.x, tx.transform.y, "texts")]
    return groups, (left_h, left_s, left_t)


# ---------------------------------------------------------------------------
# Printer pages
# ---------------------------------------------------------------------------
@dataclass
class PageSheet:
    """Printer pages for a 1:1 print: one page, or a grid of pages tiled
    with an overlap for pieces too big for one. In its own frame (0, 0) is
    the lower-left corner of the printed area, ``width`` x ``height`` mm
    across all its pages; page (row, col) counts from the top-left."""
    cols: int
    rows: int
    width: float
    height: float
    placements: List[Placement] = field(default_factory=list)


def _tiles(extent: float, content: float, overlap: float) -> int:
    """Pages needed to cover ``extent`` mm, ``content`` mm a page, the pages
    overlapping by ``overlap``."""
    if extent <= content + _EPS:
        return 1
    step = max(1.0, content - overlap)
    return max(1, math.ceil((extent - overlap) / step - 1e-9))


def layout_pages(pieces: Sequence[NestPiece], content_w: float,
                 content_h: float, *, overlap: float = 8.0,
                 spacing: float = 8.0, pad: float = 2.0):
    """Lay ``pieces`` out on printer pages whose printable area is
    ``content_w`` x ``content_h`` mm, keeping >= ``spacing`` between pieces
    and ``pad`` inside the printable area's edge.

    A piece that fits on a page is printed whole on one page. A piece too
    big for one gets a grid of pages tiled with ``overlap`` (as few pages as
    it can, turned if that takes fewer); other big pieces share a grid when
    they fit in its spare room, and so do small ones -- kept off the strips
    where its pages overlap, so each is still whole on one page. What's left
    fills pages one at a time.

    Returns ``(sheets, unplaced_keys)``; each sheet's placements put the
    pieces in that sheet's own frame. Piece keys must be hashable."""
    cell = cell_for_gap(spacing)
    step_w = max(1.0, content_w - overlap)
    step_h = max(1.0, content_h - overlap)
    masks: dict = {}

    def packer(sheet: PageSheet) -> Packer:
        half = spacing / 2.0
        return Packer(sheet.width - 2 * pad + spacing,
                      sheet.height - 2 * pad + spacing, margin=0.0,
                      spacing=spacing, cell=cell, sheet_x=pad - half,
                      sheet_y=pad - half, from_top=True, mask_cache=masks)

    def size(p):
        b = _bbox(p.outlines)
        return b[2] - b[0], b[3] - b[1]

    # what one page holds -- a cell short, for the grid's rounding
    one = PageSheet(1, 1, content_w, content_h)
    page_cell = packer(one).cell
    room_w = content_w - 2 * pad - page_cell - 1e-6
    room_h = content_h - 2 * pad - page_cell - 1e-6

    def fits_page(p):
        w, h = size(p)
        return ((w <= room_w and h <= room_h)
                or (p.allow_rotate and h <= room_w and w <= room_h))

    small = [p for p in pieces if fits_page(p)]
    big = sorted((p for p in pieces if not fits_page(p)),
                 key=lambda p: -size(p)[0] * size(p)[1])
    grids: List[Tuple[PageSheet, Packer]] = []
    unplaced = []

    def new_grid(p):
        w, h = size(p)
        shapes = [(w, h)] + ([(h, w)] if p.allow_rotate else [])
        best = None
        for ww, hh in shapes:
            cols = _tiles(ww + 2 * pad + 2 * cell, content_w, overlap)
            rows = _tiles(hh + 2 * pad + 2 * cell, content_h, overlap)
            rank = (cols * rows, rows)
            if best is None or rank < best[0]:
                best = (rank, cols, rows, ww, hh)
        _rank, cols, rows, ww, hh = best
        for _grow in range(4):              # the grid's rounding, rarely
            sheet = PageSheet(cols, rows, content_w + (cols - 1) * step_w,
                              content_h + (rows - 1) * step_h)
            pk = packer(sheet)
            pl, _un = pk.place([p])
            if pl:
                sheet.placements += pl
                return sheet, pk
            if sheet.width - ww <= sheet.height - hh:    # grow the tight way
                cols += 1
            else:
                rows += 1
        return None

    for p in big:
        for sheet, pk in grids:
            pl, _un = pk.place([p])
            if pl:
                sheet.placements += pl
                break
        else:
            got = new_grid(p)
            if got is None:
                unplaced.append(p.key)
            else:
                grids.append(got)

    remaining = list(small)
    for sheet, pk in grids:
        if not remaining:
            break
        for c in range(1, sheet.cols):       # where its pages overlap
            x = c * step_w
            pk.block_rect(x, -1e9, x + overlap, 1e9)
        for r in range(1, sheet.rows):
            y = sheet.height - r * step_h
            pk.block_rect(-1e9, y - overlap, 1e9, y)
        pl, un = pk.place(remaining)
        sheet.placements += pl
        left = set(un)
        remaining = [p for p in remaining if p.key in left]

    pages: List[PageSheet] = []
    while remaining:
        sheet = PageSheet(1, 1, content_w, content_h)
        pl, un = packer(sheet).place(remaining)
        if not pl:
            unplaced += [p.key for p in remaining]
            break
        sheet.placements += pl
        pages.append(sheet)
        left = set(un)
        remaining = [p for p in remaining if p.key in left]
    return [s for s, _pk in grids] + pages, unplaced


def smallest_sheet(pieces: Sequence[NestPiece], spacing: float = 8.0,
                   budget: float = 1.0):
    """The smallest rectangle found that holds every piece at least
    ``spacing`` apart, pieces flush with its edges: ``(width, height)`` mm,
    or None (see ``nest_smallest``) -- an estimate of the leather a pattern
    takes, not a proof that no smaller one exists."""
    if not pieces:
        return None
    res = nest_smallest(pieces, margin=0.0, spacing=spacing, budget=budget)
    if res.unplaced or res.envelope is None:
        return None
    e = res.envelope
    return e[2] - e[0], e[3] - e[1]

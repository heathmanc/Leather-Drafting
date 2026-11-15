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

This is a greedy first-fit packer, not an optimiser: it gets close to what a
patient human does laying out pattern pieces, deterministically, in well under
a second for a wallet's worth of pieces and a few seconds for a hundred on a
whole hide. Pieces are tried biggest-first, each in 0° and (optionally) 90°,
and take the lowest-leftmost pocket they fit. ``nest`` reports progress and
can be cancelled.

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
    dx: float = 0.0          # world translation to apply (after any rotation)
    dy: float = 0.0
    rotated: bool = False    # rotate 90 deg CCW about ``pivot`` first
    pivot: Vec2 = field(default_factory=lambda: Vec2(0.0, 0.0))

    def apply(self, p: Vec2) -> Vec2:
        """Where the point ``p`` of the piece goes."""
        if self.rotated:
            p = rotate90(p, self.pivot)
        return Vec2(p.x + self.dx, p.y + self.dy)

    def turn(self, v: Vec2) -> Vec2:
        """Where the direction ``v`` (a hole's tangent) points afterwards."""
        return Vec2(-v.y, v.x) if self.rotated else v


def _bbox(outlines) -> Tuple[float, float, float, float]:
    xs = [p.x for pts, _c in outlines for p in pts]
    ys = [p.y for pts, _c in outlines for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def rotate90(p: Vec2, c: Vec2) -> Vec2:
    """90 deg counter-clockwise about ``c``."""
    return Vec2(c.x - (p.y - c.y), c.y + (p.x - c.x))


def _rot_outlines(outlines, c: Vec2):
    return [([rotate90(p, c) for p in pts], closed) for pts, closed in outlines]


def _mark_segment(rows: List[int], ax: float, ay: float, bx: float, by: float,
                  w: int, h: int) -> None:
    """Set the bit of every cell the segment (ax, ay)-(bx, by), given in cell
    units, passes through or touches -- a supercover walk, so no point of
    the segment lies in an unmarked cell."""
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
                    c0 = max(0, int((xs[i] - x0) / cell))
                    c1 = min(w - 1, int((xs[i + 1] - x0) / cell))
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
    stays put, and ``block_rect`` can keep pieces out of parts of it.

    Pieces keep >= ``spacing`` between outlines and >= ``margin`` from the
    sheet's edge, whose lower-left corner sits at (sheet_x, sheet_y).
    ``from_top`` fills the sheet from its top edge down instead of from its
    bottom edge up (a printed page reads from the top)."""

    def __init__(self, sheet_w: float, sheet_h: float, *, margin: float = 5.0,
                 spacing: float = 3.0, cell: float = 2.0, sheet_x: float = 0.0,
                 sheet_y: float = 0.0, from_top: bool = False,
                 mask_cache: Optional[dict] = None):
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

    def _mask(self, piece, outlines, rotated):
        key = (id(piece), rotated, self.cell, self.gap)
        got = self._masks.get(key)
        if got is not None and got[0] is piece:
            return got[1]
        raw, x0, y0, _w, _h = _rasterize(outlines, self.gap, self.cell)
        fat = _dilate(raw, self.gap)
        m, dc, dr, w = _trim(fat)
        out = (m, x0 + dc * self.cell, y0 + dr * self.cell, w)
        self._masks[key] = (piece, out)
        return out

    def _spot(self, mask, w):
        """The first row (lowest, or highest ``from_top``), then leftmost
        column, where ``mask`` fits.

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
        for oy in rng:
            if oy >= self.hi or oy + h <= self.lo:
                return 0, oy              # nothing placed in these rows: free
            valid = full
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
                return (valid & -valid).bit_length() - 1, oy
        return None

    def place(self, pieces: Sequence[NestPiece], progress=None):
        """Place as many of ``pieces`` as fit, biggest first. Returns
        (placements, unplaced_keys). ``progress(done, total)`` is called
        before each piece; returning False raises ``NestCancelled``."""
        if not self.ok:
            return [], [p.key for p in pieces]
        order = sorted(pieces, key=lambda p: -(lambda b: (b[2] - b[0])
                                               * (b[3] - b[1]))(_bbox(p.outlines)))
        placements, unplaced = [], []
        for n_done, piece in enumerate(order):
            if progress is not None and not progress(n_done, len(order)):
                raise NestCancelled()
            xmin, ymin, xmax, ymax = _bbox(piece.outlines)
            pivot = Vec2(0.5 * (xmin + xmax), 0.5 * (ymin + ymax))
            options = [(piece.outlines, False)]
            if piece.allow_rotate:
                options.append((_rot_outlines(piece.outlines, pivot), True))
            best = None              # (rank, oy, ox, rotated, mask, mx0, my0)
            for outlines, rot in options:
                mask, mx0, my0, w = self._mask(piece, outlines, rot)
                if not mask:
                    continue
                spot = self._spot(mask, w)
                if spot is not None:
                    rank = (-spot[1] if self.from_top else spot[1], spot[0])
                    if best is None or rank < best[0]:
                        best = (rank, spot[1], spot[0], rot, mask, mx0, my0)
            if best is None:
                unplaced.append(piece.key)
                continue
            _rank, oy, ox, rot, mask, mx0, my0 = best
            for k in range(len(mask)):      # claim the cells
                self.occ[oy + k] |= mask[k] << ox
                self.cells_used += bin(mask[k]).count("1")
            self.lo = min(self.lo, oy)
            self.hi = max(self.hi, oy + len(mask))
            placements.append(Placement(
                key=piece.key,
                dx=(self.x0 + ox * self.cell) - mx0,
                dy=(self.y0 + oy * self.cell) - my0,
                rotated=rot, pivot=pivot))
        if progress is not None:
            progress(len(order), len(order))    # done: the bar reaches the end
        return placements, unplaced


def nest(pieces: Sequence[NestPiece], sheet_w: float, sheet_h: float, *,
         margin: float = 5.0, spacing: float = 3.0, cell: float = 2.0,
         sheet_x: float = 0.0, sheet_y: float = 0.0, progress=None,
         from_top: bool = False):
    """Pack ``pieces`` into the sheet. Returns (placements, unplaced_keys,
    used_fraction). Coordinates are world mm; the sheet's lower-left corner
    sits at (sheet_x, sheet_y). Pieces keep >= ``spacing`` between outlines
    and >= ``margin`` from the sheet edge.

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


def group_pieces(shapes, holes=(), seams=(), texts=()):
    """Sort what's being nested into the groups that travel as one.

    * Shapes of one move-group (``Ctrl+G``) are one piece.
    * A piece lying wholly inside another (a slot in a panel) joins it.
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

    # a piece fully inside another (a slot in a panel) merges into it
    for g in sorted(groups, key=lambda g: _box_area(g.bbox)):
        if g not in groups:
            continue
        b = g.bbox
        host = containing(0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]), exclude=g)
        if host is not None and host.bbox[0] <= b[0] and host.bbox[1] <= b[1] \
                and host.bbox[2] >= b[2] and host.bbox[3] >= b[3]:
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


def smallest_sheet(pieces: Sequence[NestPiece], spacing: float = 8.0):
    """The smallest rectangle found that holds every piece at least
    ``spacing`` apart, pieces flush with its edges: ``(width, height)`` mm,
    or None. A handful of strip widths are packed and the tightest kept --
    an estimate of the leather a pattern takes, not a proof that no smaller
    one exists."""
    if not pieces:
        return None
    sizes = []
    for p in pieces:
        b = _bbox(p.outlines)
        sizes.append((b[2] - b[0], b[3] - b[1], p.allow_rotate))
    cell = cell_for_gap(spacing)
    narrow = max((min(w, h) if rot else w) for w, h, rot in sizes) + 2 * cell
    area = sum((w + spacing) * (h + spacing) for w, h, _r in sizes)
    tallest = max(max(w, h) for w, h, _r in sizes)
    widths = {narrow}
    for f in (1.25, 1.6, 2.2, 3.0):
        widths.add(narrow * f)
    for f in (0.75, 1.0, 1.35):
        widths.add(max(narrow, math.sqrt(area) * f))
    by_key = {p.key: p for p in pieces}
    masks: dict = {}
    best = None
    for W in sorted(widths):
        H = area / W * 1.5 + tallest + spacing
        placed, left = [], list(pieces)
        for _try in range(3):
            pk = Packer(W + spacing, H + spacing, margin=0.0, spacing=spacing,
                        cell=cell, sheet_x=-spacing / 2.0,
                        sheet_y=-spacing / 2.0, from_top=True,
                        mask_cache=masks)
            placed, left = pk.place(pieces)
            if not left:
                break
            H *= 2.0
        if left:
            continue
        xs, ys = [], []
        for pl in placed:
            for pts, _c in by_key[pl.key].outlines:
                for q in pts:
                    r = pl.apply(q)
                    xs.append(r.x)
                    ys.append(r.y)
        w_used, h_used = max(xs) - min(xs), max(ys) - min(ys)
        if best is None or w_used * h_used < best[0]:
            best = (w_used * h_used, w_used, h_used)
    return (best[1], best[2]) if best else None

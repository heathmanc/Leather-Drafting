"""Print / export a pattern to PDF at true 1:1 scale.

Leatherworkers print patterns to cut or check by hand, and sell them as
PDFs. The one thing that must be exact is *scale*: 1 mm in the document must
be 1 mm on paper.

Two layouts:

* **nested** -- the pieces packed onto as few pages as they will go, at
  least a chosen gap apart (8 mm unless set otherwise). A piece that fits on
  a page is printed whole on one page; a piece bigger than a page is tiled
  across pages that overlap so they can be taped together, and shares them
  with whatever else fits. The drawing itself is not changed.
* **as drawn** -- the drawing as it is laid out, tiled across pages.

Every page has a header (the pattern's name, the designer) and a footer (a
note such as a copyright line, the scale check, the page number). The first
page can be a details page: pieces, stitch holes, the largest piece, the
leather and thread needed, the stitching, squares to check the print's
scale, and a list of the pieces with the pages they are on.

Rendering is shared between a PDF file (``export_pdf``) and the OS print
dialog (``render`` onto any ``QPagedPaintDevice``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import Qt, QRectF, QPointF, QSizeF, QMarginsF
from PySide6.QtGui import (QPainter, QPen, QColor, QPainterPath, QFont,
                           QPdfWriter, QPageSize, QPageLayout)

from leathercad.export import collect
from leathercad.geometry import Vec2

# The page frame (mm): header and footer live in the top and bottom margins.
MARGIN_X = 10.0
MARGIN_TOP = 16.0
MARGIN_BOTTOM = 16.0
PAD = 2.0          # pieces stay this far inside the printed area's border
LINE_SLACK = 0.25  # the printed line's width and the curves' flattening,
                   # kept on top of the gap so the gap holds edge to edge
INCH = 25.4


@dataclass
class PrintOptions:
    page_w: float = 210.0
    page_h: float = 297.0
    nested: bool = True
    spacing: float = 8.0          # mm between pieces, when nested
    allow_rotate: bool = True
    overlap: float = 8.0          # mm a tiled page overlaps its neighbour
    title: str = ""               # header, left ("" = "Pattern")
    designer: str = ""            # header, right
    note: str = ""                # footer, left: a copyright / licence line
    details: bool = True          # a details page first
    usable_pct: float = 75.0      # hide yield, for the leather to buy

    @property
    def content_w(self) -> float:
        return self.page_w - 2 * MARGIN_X

    @property
    def content_h(self) -> float:
        return self.page_h - MARGIN_TOP - MARGIN_BOTTOM


def _hole_reach(st) -> float:
    """How far a hole reaches from its centre."""
    if getattr(st, "hole_style", "round") in ("slit", "diamond"):
        return float(getattr(st, "slit_length", 1.6) or 1.6) / 2.0
    return float(getattr(st, "hole_diameter", 1.0) or 1.0) / 2.0


@dataclass
class Part:
    """One piece as printed: its outlines ``(points, colour, closed)`` and
    stitch holes ``(StitchResult, settings, colour)`` in world mm."""
    name: str = ""
    outlines: list = field(default_factory=list)
    stitches: list = field(default_factory=list)
    pages: list = field(default_factory=list)       # pages it's printed on
    # per stitch entry: holes put there by hand -- attached to the piece or
    # loose -- whose settings don't say the spacing they were punched at
    placed: list = field(default_factory=list)

    @property
    def holes(self) -> int:
        return sum(res.count for res, _s, _c in self.stitches)

    def bbox(self):
        xs, ys = [], []
        for pts, _c, _cl in self.outlines:
            xs += [p.x for p in pts]
            ys += [p.y for p in pts]
        for res, st, _c in self.stitches:
            r = _hole_reach(st)
            for h in res.holes:
                xs += [h.point.x - r, h.point.x + r]
                ys += [h.point.y - r, h.point.y + r]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def size(self):
        b = self.bbox()
        return (0.0, 0.0) if b is None else (b[2] - b[0], b[3] - b[1])


@dataclass
class Page:
    kind: str                     # "details" or "pattern"
    sheet: object = None          # its PageSheet
    sheet_no: int = 0             # 1-based, counting tiled sheets only
    row: int = 0
    col: int = 0


@dataclass
class PrintPlan:
    """What goes on which page, and the numbers for the details page."""
    opts: PrintOptions
    parts: List[Part]
    sheets: list
    pages: List[Page]
    drawn_pages: int              # what the drawing takes printed as drawn
    unplaced: list = field(default_factory=list)
    title: str = "Pattern"

    @property
    def pattern_pages(self) -> int:
        return sum(1 for p in self.pages if p.kind == "pattern")


# ---------------------------------------------------------------------------
# What is printed, piece by piece
# ---------------------------------------------------------------------------
def _rider_box(r):
    from leathercad.holes import LooseHole
    from leathercad.stitchline import StitchLine
    if isinstance(r, LooseHole):
        pts = [r.point]
    elif isinstance(r, StitchLine):
        pts = list(r.points)
    else:                                       # lettering
        pts = [p for c in r.world_contours() for p in c]
        if not pts:
            pts = [Vec2(r.transform.x, r.transform.y)]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _clusters(items, gap: float):
    """Loose things within ``gap`` of each other, as lists."""
    boxes = [_rider_box(r) for r in items]
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        a = boxes[i]
        for j in range(i + 1, len(items)):
            b = boxes[j]
            if (a[0] - gap <= b[2] and b[0] - gap <= a[2]
                    and a[1] - gap <= b[3] and b[1] - gap <= a[3]):
                parent[find(i)] = find(j)
    out: dict = {}
    for i, r in enumerate(items):
        out.setdefault(find(i), []).append(r)
    return list(out.values())


def _loose_name(cluster) -> str:
    from leathercad.holes import LooseHole
    from leathercad.stitchline import StitchLine
    if all(isinstance(r, LooseHole) for r in cluster):
        return "Holes"
    if all(isinstance(r, StitchLine) for r in cluster):
        return "Seam"
    if not any(isinstance(r, (LooseHole, StitchLine)) for r in cluster):
        return "Lettering"
    return "Marks"


def make_parts(doc, spacing: float = 8.0) -> List[Part]:
    """The drawing's pieces as printed, grouped as Nest on sheet groups them:
    a move-group is one piece, and a piece's slots, holes, seams, lettering
    and fold lines come with it. Loose things on no piece stay with whatever
    lies within a gap of them. Only what's shown (visible layers) is
    printed; guides never are."""
    from leathercad.nesting import group_pieces
    from leathercad.pieces import on_cut_layer
    outlines, stitches, out_own, st_own = collect(doc, owners=True)
    shapes = [sh for sh in doc.shapes if not getattr(sh, "construction", False)]
    groups, (lh, ls, lt) = group_pieces(
        shapes, getattr(doc, "holes", []), getattr(doc, "stitch_lines", []),
        getattr(doc, "texts", []), is_cut=on_cut_layer(doc))
    parts: List[Part] = []
    of: dict = {}
    for g in groups:
        named = next((sh.name.strip() for sh in g.shapes
                      if (getattr(sh, "name", "") or "").strip()), "")
        for x in g.shapes + g.riders():
            of[id(x)] = len(parts)
        parts.append(Part(name=named))
    for cluster in _clusters(lh + ls + lt, spacing):
        for x in cluster:
            of[id(x)] = len(parts)
        parts.append(Part(name=_loose_name(cluster)))

    def part_for(own):
        i = of.get(id(own))
        if i is None:                         # nothing claimed it: its own
            i = of[id(own)] = len(parts)
            parts.append(Part(name=(getattr(own, "name", "") or "").strip()))
        return parts[i]

    from leathercad.text import TextShape
    for (pts, color), own in zip(outlines, out_own):
        if isinstance(own, TextShape):
            closed = True
        else:
            closed = bool(own.local_path().closed) if hasattr(own, "local_path") \
                else len(pts) >= 4 and (pts[0] - pts[-1]).length() < 1e-6
        part_for(own).outlines.append((list(pts), color, closed))
    for entry, own in zip(stitches, st_own):
        part = part_for(own)
        part.stitches.append(entry)
        part.placed.append(bool(getattr(own, "baked_holes", None))
                           or getattr(entry[1], "pitch_mm", None) is None)
    shown = [p for p in parts if p.outlines or p.stitches]
    n = 0
    named: dict = {}              # copies of an unnamed piece share its name
    for p in shown:
        if not p.name:
            key = _same(p)
            if key not in named:
                n += 1
                named[key] = f"Piece {n}"
            p.name = named[key]
    return shown


def _same(part):
    """What makes two printed pieces copies of one another: the same size
    (either way round), outlines and holes."""
    w, h = part.size()
    return (round(max(w, h), 1), round(min(w, h), 1), len(part.outlines),
            part.holes)


def _piece_rows(parts):
    """The pieces table: copies of a piece (same name, size and holes) on one
    row, with how many to cut and every page they're on. Returns the rows
    ``[part, how many, pages]`` and each part's row number (by ``id``)."""
    rows, at, number = [], {}, {}
    for part in parts:
        key = (part.name,) + _same(part)
        if key in at:
            row = rows[at[key]]
            row[1] += 1
            row[2].update(part.pages)
        else:
            at[key] = len(rows)
            rows.append([part, 1, set(part.pages)])
        number[id(part)] = at[key] + 1
    return rows, number


# ---------------------------------------------------------------------------
# Planning the pages
# ---------------------------------------------------------------------------
def _tiles(extent: float, content: float, overlap: float) -> int:
    from leathercad.nesting import _tiles as t
    return t(extent, content, overlap)


def _drawing_box(parts):
    boxes = [b for b in (p.bbox() for p in parts) if b is not None]
    if not boxes:
        return 0.0, 0.0, 1.0, 1.0
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _as_drawn_sheet(parts, opts):
    """The drawing as laid out: one grid of pages over its extent, every
    part where it is (its top-left at the first page's top-left)."""
    from leathercad.nesting import PageSheet, Placement
    minx, miny, maxx, maxy = _drawing_box(parts)
    cw, ch = opts.content_w, opts.content_h
    step_w, step_h = max(1.0, cw - opts.overlap), max(1.0, ch - opts.overlap)
    cols = _tiles(maxx - minx, cw, opts.overlap)
    rows = _tiles(maxy - miny, ch, opts.overlap)
    sheet = PageSheet(cols, rows, cw + (cols - 1) * step_w,
                      ch + (rows - 1) * step_h)
    sheet.placements = [Placement(key=i, dx=-minx, dy=sheet.height - maxy)
                        for i in range(len(parts))]
    return sheet


def _nest_pieces(parts, opts):
    from leathercad.nesting import NestPiece
    pieces = []
    for i, part in enumerate(parts):
        geo = [(pts, closed) for pts, _c, closed in part.outlines if pts]
        for res, st, _c in part.stitches:
            r = _hole_reach(st)
            for h in res.holes:
                x, y = h.point.x, h.point.y
                geo.append(([Vec2(x - r, y - r), Vec2(x + r, y - r),
                             Vec2(x + r, y + r), Vec2(x - r, y + r),
                             Vec2(x - r, y - r)], True))
        if geo:
            pieces.append(NestPiece(key=i, outlines=geo,
                                    allow_rotate=opts.allow_rotate))
    return pieces


def _placed_box(part, pl):
    """A part's box once ``pl`` has moved it."""
    b = part.bbox()
    if b is None:
        return None
    pts = [pl.apply(Vec2(x, y)) for x in (b[0], b[2]) for y in (b[1], b[3])]
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _page_rect(sheet, opts, row, col):
    """Page (row, col)'s printed area in its sheet's frame."""
    cw, ch = opts.content_w, opts.content_h
    step_w, step_h = max(1.0, cw - opts.overlap), max(1.0, ch - opts.overlap)
    x0 = col * step_w
    top = sheet.height - row * step_h
    return x0, top - ch, x0 + cw, top


def _meets(a, b, tol=1e-6) -> bool:
    return (a[0] < b[2] - tol and b[0] < a[2] - tol
            and a[1] < b[3] - tol and b[1] < a[3] - tol)


def _printed_tiles(sheet, parts, opts) -> int:
    """How many of a sheet's pages have something on them."""
    if sheet.cols * sheet.rows == 1:
        return 1
    boxes = [b for b in (_placed_box(parts[pl.key], pl)
                         for pl in sheet.placements) if b is not None]
    return sum(1 for r in range(sheet.rows) for c in range(sheet.cols)
               if any(_meets(b, _page_rect(sheet, opts, r, c)) for b in boxes))


def plan(doc, opts: Optional[PrintOptions] = None) -> PrintPlan:
    """Decide what goes on which page (nothing is drawn)."""
    from leathercad.nesting import layout_pages
    opts = opts or PrintOptions()
    parts = make_parts(doc, opts.spacing)
    title = (opts.title or "").strip() or "Pattern"
    drawn = _as_drawn_sheet(parts, opts)
    drawn_pages = _printed_tiles(drawn, parts, opts)
    unplaced: list = []
    if opts.nested and parts:
        sheets, unplaced = layout_pages(
            _nest_pieces(parts, opts), opts.content_w, opts.content_h,
            overlap=opts.overlap, spacing=opts.spacing + LINE_SLACK, pad=PAD)
        if unplaced:              # never expected: print those as drawn
            ks = list(unplaced)
            extra = _as_drawn_sheet([parts[k] for k in ks], opts)
            for pl, k in zip(extra.placements, ks):
                pl.key = k
            sheets.append(extra)
    else:
        sheets = [drawn]
    pages: List[Page] = []
    if opts.details:
        pages.append(Page("details"))
    tiled_no = 0
    for sheet in sheets:
        many = sheet.cols * sheet.rows > 1
        if many:
            tiled_no += 1
        boxes = [(pl.key, _placed_box(parts[pl.key], pl))
                 for pl in sheet.placements]
        for r in range(sheet.rows):
            for c in range(sheet.cols):
                rect = _page_rect(sheet, opts, r, c)
                hit = [k for k, b in boxes if b is not None and _meets(b, rect)]
                if many and not hit:
                    continue            # an empty tile: leave it out
                pages.append(Page("pattern", sheet, tiled_no if many else 0,
                                  r, c))
                for k in hit:
                    parts[k].pages.append(len(pages))
    if not pages:
        pages.append(Page("pattern", sheets[0] if sheets else drawn))
    return PrintPlan(opts, parts, sheets, pages, drawn_pages, unplaced, title)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------
def _font(D, size_mm, bold=False):
    f = QFont()
    f.setPixelSize(max(1, int(round(size_mm * D))))
    f.setBold(bold)
    return f


def _text(painter, D, x, y, w, h, text, size_mm, *, bold=False,
          align=Qt.AlignLeft, color=QColor(40, 40, 40), wrap=False):
    """Text in the box (x, y, w, h) mm; returns the height it took (mm)."""
    painter.setFont(_font(D, size_mm, bold))
    painter.setPen(color)
    flags = int(align) | int(Qt.AlignTop) | (int(Qt.TextWordWrap) if wrap else 0)
    rect = QRectF(x * D, y * D, w * D, h * D)
    used = painter.boundingRect(rect, flags, text)
    painter.drawText(rect, flags, text)
    return used.height() / D


def _mm_in(w: float, h: Optional[float] = None) -> str:
    if h is None:
        return f"{w:.0f} mm ({w / INCH:.2f} in)"
    return f"{w:.0f} × {h:.0f} mm ({w / INCH:.2f} × {h / INCH:.2f} in)"


def _num(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def _decorate(painter, D, plan_, page_no, total, *, tile="", scale=True):
    """Header and footer of one page: the pattern's name and the designer
    (and, on a tiled page, where it goes) above; the scale check, the note
    and the page number below."""
    o = plan_.opts
    W, H = o.page_w, o.page_h
    grey = QColor(120, 120, 120)
    rule = QPen(QColor(170, 170, 170))
    rule.setWidthF(0.2 * D)
    side = (W - 2 * MARGIN_X) * (0.36 if tile else 0.58)
    _text(painter, D, MARGIN_X, 6.5, side, 6, plan_.title, 3.8, bold=True)
    if tile:
        _text(painter, D, W * 0.5 - 45, 6.8, 90, 6, tile, 3.2, bold=True,
              align=Qt.AlignHCenter, color=QColor(60, 60, 60))
    if o.designer:
        _text(painter, D, W - MARGIN_X - side, 7.0, side, 6, o.designer, 3.3,
              align=Qt.AlignRight, color=grey)
    painter.setPen(rule)
    painter.drawLine(QPointF(MARGIN_X * D, 13.0 * D),
                     QPointF((W - MARGIN_X) * D, 13.0 * D))
    painter.drawLine(QPointF(MARGIN_X * D, (H - 14.0) * D),
                     QPointF((W - MARGIN_X) * D, (H - 14.0) * D))
    if scale:
        cw, ch = o.content_w, o.content_h
        _text(painter, D, MARGIN_X, H - 13.2, W - 2 * MARGIN_X, 4,
              f"Print at 100% (actual size): this box is {_num(cw)} × "
              f"{_num(ch)} mm ({cw / INCH:.2f} × {ch / INCH:.2f} in)", 2.8,
              align=Qt.AlignHCenter, color=grey)
    if o.note:
        _text(painter, D, MARGIN_X, H - 9.4, W - 2 * MARGIN_X - 34, 4, o.note,
              2.8, color=grey)
    _text(painter, D, W - MARGIN_X - 32, H - 9.4, 32, 4,
          f"Page {page_no} of {total}", 2.8, align=Qt.AlignRight, color=grey)


def _crop_marks(painter, D, content_w, content_h, pen):
    """Corner crop ticks + a light border round the printed area, so tiles
    can be trimmed and lined up on their overlap (and the scale checked)."""
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x0, y0 = MARGIN_X * D, MARGIN_TOP * D
    x1, y1 = (MARGIN_X + content_w) * D, (MARGIN_TOP + content_h) * D
    painter.drawRect(QRectF(x0, y0, content_w * D, content_h * D))
    t = 4.0 * D                                   # 4 mm tick
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                             (x0, y1, 1, -1), (x1, y1, -1, -1)):
        painter.drawLine(QPointF(cx, cy), QPointF(cx + sx * t, cy))
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy + sy * t))


def _ring_dist(p, ring) -> float:
    best = float("inf")
    for a, b in zip(ring, ring[1:]):
        ab = b - a
        L = ab.length_sq()
        t = 0.0 if L < 1e-18 else max(0.0, min(1.0, (p - a).dot(ab) / L))
        best = min(best, (a.lerp(b, t) - p).length())
    return best


def _label_spot(part):
    """Where a part's name goes (world mm) and how much room it has there:
    a point well inside its biggest outline, away from the edges."""
    from leathercad.geometry import point_in_polygon
    rings = [pts for pts, _c, closed in part.outlines if closed and len(pts) >= 4]
    b = part.bbox()
    if b is None:
        return None, 0.0
    centre = Vec2(0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]))
    if not rings:
        return centre, 0.5 * min(b[2] - b[0], b[3] - b[1]) + 3.0
    from leathercad.nesting import _ring_area
    ring = max(rings, key=_ring_area)
    best = (_ring_dist(centre, ring), centre) \
        if point_in_polygon(centre, ring) else (-1.0, centre)
    rb = (min(p.x for p in ring), min(p.y for p in ring),
          max(p.x for p in ring), max(p.y for p in ring))
    for i in range(1, 8):
        for j in range(1, 8):
            q = Vec2(rb[0] + (rb[2] - rb[0]) * i / 8.0,
                     rb[1] + (rb[3] - rb[1]) * j / 8.0)
            if point_in_polygon(q, ring):
                d = _ring_dist(q, ring)
                if d > best[0] * 1.15:          # the middle, unless much roomier
                    best = (d, q)
    return best[1], max(best[0], 0.0)


def _draw_pattern_page(painter, D, plan_, page, page_no_of=None):
    o = plan_.opts
    sheet = page.sheet
    cw, ch = o.content_w, o.content_h
    x0, _yb, _x1, top = _page_rect(sheet, o, page.row, page.col)

    def M(p):
        # sheet frame (Y-up) -> page (Y-down), inside the margins, in dots
        return QPointF((MARGIN_X + (p.x - x0)) * D, (MARGIN_TOP + (top - p.y)) * D)

    outline_pen = QPen()
    outline_pen.setWidthF(0.20 * D)
    stitch_pen = QPen()
    stitch_pen.setWidthF(0.20 * D)
    painter.setClipRect(QRectF(MARGIN_X * D, MARGIN_TOP * D, cw * D, ch * D))
    painter.setBrush(Qt.NoBrush)
    for pl in sheet.placements:
        part = plan_.parts[pl.key]
        for pts, color, _closed in part.outlines:
            if len(pts) < 2:
                continue
            outline_pen.setColor(QColor(color))
            painter.setPen(outline_pen)
            path = QPainterPath(M(pl.apply(pts[0])))
            for p in pts[1:]:
                path.lineTo(M(pl.apply(p)))
            painter.drawPath(path)
        for res, settings, color in part.stitches:
            stitch_pen.setColor(QColor(color))
            painter.setPen(stitch_pen)
            if getattr(settings, "hole_style", "round") == "slit":
                half = settings.slit_length / 2.0
                slant = math.radians(settings.slit_angle)
                for h in res.holes:
                    c = pl.apply(h.point)
                    d = pl.turn(h.tangent).rotate(slant)
                    painter.drawLine(M(c - d * half), M(c + d * half))
            else:
                rr = settings.hole_diameter / 2.0 * D
                for h in res.holes:
                    painter.drawEllipse(M(pl.apply(h.point)), rr, rr)
        # its name, inside it, so a cut-out piece can be told from the next
        spot, room = _label_spot(part)
        if spot is not None and part.name:
            size = max(1.8, min(4.5, room * 0.45,
                                1.8 * room / max(1, 0.55 * len(part.name))))
            c = M(pl.apply(spot))
            painter.setFont(_font(D, size))
            painter.setPen(QColor(110, 110, 110))
            box = QRectF(c.x() - 60 * D, c.y() - size * 0.7 * D, 120 * D,
                         size * 1.4 * D)
            painter.drawText(box, int(Qt.AlignCenter), part.name)
    painter.setClipping(False)
    mark_pen = QPen(QColor(150, 150, 150))
    mark_pen.setWidthF(0.15 * D)
    _crop_marks(painter, D, cw, ch, mark_pen)
    if sheet.cols * sheet.rows > 1 and page_no_of is not None:
        _overlap_guides(painter, D, plan_, page, page_no_of)


def _overlap_guides(painter, D, plan_, page, page_no_of):
    """On a tiled page: a dashed line where the page to the right (or below)
    is laid -- its box edge goes on the line -- and the neighbouring page's
    number in each overlap strip."""
    o = plan_.opts
    cw, ch, ov = o.content_w, o.content_h, o.overlap
    sheet = page.sheet
    key = id(sheet)
    dash = QPen(QColor(140, 140, 140))
    dash.setWidthF(0.2 * D)
    dash.setStyle(Qt.DashLine)
    grey = QColor(120, 120, 120)
    L, T = MARGIN_X, MARGIN_TOP
    right = page_no_of.get((key, page.row, page.col + 1))
    below = page_no_of.get((key, page.row + 1, page.col))
    left = page_no_of.get((key, page.row, page.col - 1))
    above = page_no_of.get((key, page.row - 1, page.col))
    painter.setPen(dash)
    if right:
        x = (L + cw - ov) * D
        painter.drawLine(QPointF(x, T * D), QPointF(x, (T + ch) * D))
    if below:
        y = (T + ch - ov) * D
        painter.drawLine(QPointF(L * D, y), QPointF((L + cw) * D, y))
    painter.setFont(_font(D, 2.6))
    painter.setPen(grey)

    def upright(cx, cy, text):          # text turned to run up a side strip
        painter.save()
        painter.translate(cx * D, cy * D)
        painter.rotate(-90)
        painter.drawText(QRectF(-30 * D, -2 * D, 60 * D, 4 * D),
                         int(Qt.AlignCenter), text)
        painter.restore()

    if right:
        upright(L + cw - ov / 2.0, T + ch / 2.0, f"page {right} goes here")
    if left:
        upright(L + ov / 2.0, T + ch / 2.0, f"over page {left}")
    if below:
        painter.drawText(QRectF(L * D, (T + ch - ov) * D, cw * D, ov * D),
                         int(Qt.AlignCenter), f"page {below} goes here")
    if above:
        painter.drawText(QRectF(L * D, T * D, cw * D, ov * D),
                         int(Qt.AlignCenter), f"over page {above}")


def _hole_size(st) -> str:
    style = getattr(st, "hole_style", "round")
    if style in ("slit", "diamond"):
        return f"{st.slit_length:g} mm {style} holes"
    return f"Ø{st.hole_diameter:g} mm round holes"


def _stitching_lines(parts) -> List[str]:
    """How the pattern is punched: one line per iron spacing and hole, most
    holes first -- what tells a maker which pricking iron to use.

    The spacing is the one the holes are really at. A piece's stitching (and
    a seam) says its iron. Holes put there by hand -- attached to a piece, or
    loose -- carry no spacing of their own (attached ones kept a default
    3.85 mm that nobody punched), so theirs is measured from the holes and
    put on the iron it matches."""
    from leathercad.irons import measured_pitch, nearest_pitch
    live, by_hand = [], []
    for part in parts:
        flags = list(part.placed) + [False] * (len(part.stitches)
                                               - len(part.placed))
        pooled: dict = {}
        for (res, st, _c), placed in zip(part.stitches, flags):
            if not res.count:
                continue
            if placed:                        # pooled by hole, per piece
                pooled.setdefault(_hole_size(st), [[], st])[0].extend(res.holes)
            else:
                live.append((res.count, st))
        by_hand += [(holes, st) for holes, st in pooled.values()]
    irons = {float(st.pitch_mm) for _n, st in live}
    seen: dict = {}

    def add(pitch, st, rows, n):
        size = _hole_size(st)
        rows = ", double row" if rows else ""
        key = (round(pitch, 3), size, rows)
        line = f"{pitch:g} mm spacing ({pitch / INCH:.3f} in), {size}{rows}"
        seen.setdefault(key, [line, 0])[1] += n
    for n, st in live:
        add(float(st.pitch_mm), st, getattr(st, "rows", 1) == 2, n)
    for holes, st in by_hand:
        m = measured_pitch(holes)
        if m is not None:                     # lone holes: no spacing to tell
            add(nearest_pitch(m, irons), st, False, len(holes))
    return [f"{line} — {n} holes"
            for line, n in sorted(seen.values(), key=lambda v: -v[1])]


def _cutting_layout(plan_):
    """Every piece on the smallest piece of leather found, the gap apart: the
    ``NestResult`` (its sheet at the origin), or None. Only the pieces --
    loose holes, seams and lettering on no piece aren't cut from it."""
    from leathercad.nesting import nest_smallest
    o = plan_.opts
    pieces = [pc for pc in _nest_pieces(plan_.parts, o)
              if any(cl for _p, _c, cl in plan_.parts[pc.key].outlines)]
    if not pieces:
        return None
    res = nest_smallest(pieces, margin=0.0, spacing=o.spacing + LINE_SLACK,
                        budget=1.0)
    if res.unplaced or not res.placements:
        return None
    return res


def _draw_layout(painter, D, plan_, layout, number, bx, by, bw, bh):
    """The cutting layout drawn small in the box (bx, by, bw, bh) mm: the
    piece of leather, every piece on it, numbered as in the pieces table."""
    _sx, _sy, sw, sh = layout.sheet
    if sw <= 0 or sh <= 0:
        return
    s = min(bw / sw, bh / sh)
    ox, oy = bx + (bw - sw * s) / 2.0, by

    def M(p):                                 # sheet (Y-up) -> page (Y-down)
        return QPointF((ox + p.x * s) * D, (oy + (sh - p.y) * s) * D)
    edge = QPen(QColor(150, 128, 98))
    edge.setWidthF(0.2 * D)
    painter.setPen(edge)
    painter.setBrush(QColor(246, 238, 224))
    painter.drawRect(QRectF(ox * D, oy * D, sw * s * D, sh * s * D))
    cut = QPen(QColor(60, 50, 40))
    cut.setWidthF(0.15 * D)
    fold = QPen(QColor(150, 150, 150))
    fold.setWidthF(0.1 * D)
    for pl in layout.placements:
        part = plan_.parts[pl.key]
        # the piece's closed outlines as one shape, so a slot in it shows as
        # a hole (odd-even fill), then its open lines (a fold) over it
        piece = QPainterPath()
        piece.setFillRule(Qt.OddEvenFill)
        lines = []
        for pts, _color, closed in part.outlines:
            if len(pts) < 2:
                continue
            path = QPainterPath(M(pl.apply(pts[0])))
            for p in pts[1:]:
                path.lineTo(M(pl.apply(p)))
            if closed:
                path.closeSubpath()
                piece.addPath(path)
            else:
                lines.append(path)
        painter.setPen(cut)
        painter.setBrush(QColor(222, 196, 158))
        painter.drawPath(piece)
        painter.setPen(fold)
        painter.setBrush(Qt.NoBrush)
        for path in lines:
            painter.drawPath(path)
        b = _placed_box(part, pl)
        n = number.get(id(part))
        if b is None or n is None:
            continue
        across = min(b[2] - b[0], b[3] - b[1]) * s
        if across < 3.0:
            continue                          # too small to number
        c = M(Vec2(0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])))
        size = max(1.6, min(3.0, across * 0.45))
        painter.setFont(_font(D, size, bold=True))
        painter.setPen(QColor(70, 55, 40))
        painter.drawText(QRectF(c.x() - 10 * D, c.y() - size * D, 20 * D,
                                2 * size * D), int(Qt.AlignCenter), f"{n}")


def _details(painter, D, plan_, doc):
    """The first page: what the pattern is and what it takes to make."""
    from leathercad.estimate import MM2_PER_SQFT, estimate_project
    o = plan_.opts
    x, w = MARGIN_X + 2.0, o.content_w - 4.0
    y = MARGIN_TOP + 3.0
    bottom = MARGIN_TOP + o.content_h - 2.0
    y += _text(painter, D, x, y, w, 14, plan_.title, 8.0, bold=True,
               wrap=True) + 1.0
    if o.designer:
        y += _text(painter, D, x, y, w, 7, o.designer, 4.2,
                   color=QColor(90, 90, 90)) + 1.0
    y += 3.0

    # scale check
    y += _text(painter, D, x, y, w, 12,
               "Print at 100% (actual size): turn off \"Fit to page\" / "
               "\"Scale to fit\". Then measure the squares below — they must "
               "be exactly 50 mm and 1 inch across, or the pieces won't fit "
               "together.", 3.3, wrap=True) + 3.0
    box = QPen(QColor(30, 30, 30))
    box.setWidthF(0.25 * D)
    painter.setPen(box)
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(QRectF(x * D, y * D, 50.0 * D, 50.0 * D))
    painter.drawRect(QRectF((x + 58.0) * D, (y + 50.0 - INCH) * D,
                            INCH * D, INCH * D))
    _text(painter, D, x, y + 22.0, 50.0, 6, "50 mm", 3.6,
          align=Qt.AlignHCenter)
    _text(painter, D, x + 58.0, y + 50.0 - INCH / 2 - 2.2, INCH, 6, "1 in",
          3.3, align=Qt.AlignHCenter)

    # the cutting layout, beside the squares when there's room
    table, number = _piece_rows(plan_.parts)
    layout = _cutting_layout(plan_)
    beside = w - 95.0 >= 55.0
    if layout is not None and beside:
        lx = x + 95.0
        _text(painter, D, lx, y - 1.0, w - 95.0, 6,
              "Cutting layout (not to scale)", 3.3, bold=True,
              align=Qt.AlignHCenter)
        _draw_layout(painter, D, plan_, layout, number, lx, y + 4.5,
                     w - 95.0, 45.5)
    y += 50.0 + 6.0

    # the numbers
    est = estimate_project(doc, usable_pct=o.usable_pct)
    rows = []
    rows.append(("Pieces to cut", f"{est.pieces or len(plan_.parts)}"))
    rows.append(("Stitch holes", f"{est.holes}"))
    if plan_.parts:
        big = max(plan_.parts, key=lambda p: p.size()[0] * p.size()[1])
        bw, bh = big.size()
        rows.append(("Largest piece", f"{big.name}: {_mm_in(bw, bh)}"))
    if est.parts_area_mm2 > 0:
        sqft = est.parts_area_mm2 / MM2_PER_SQFT
        buy = sqft / (max(o.usable_pct, 1.0) / 100.0)
        rows.append(("Leather in the pieces",
                     f"{est.parts_area_mm2 / 10000.0:.1f} dm²  ({sqft:.2f} sq ft)"))
        rows.append(("Leather to buy",
                     f"about {buy:.2f} sq ft  ({o.usable_pct:g}% of a hide usable)"))
    if layout is not None:
        rows.append(("Fits on a piece of",
                     f"{_mm_in(layout.sheet[2], layout.sheet[3])}, pieces "
                     f"{_num(o.spacing)} mm apart"
                     + ("" if beside else " (layout below)")))
    stitching = _stitching_lines(plan_.parts)
    for i, line in enumerate(stitching[:3]):
        rows.append(("Stitching" if i == 0 else "", line))
    pp = plan_.pattern_pages
    rows.append(("Pattern pages", f"{pp} ({_num(o.page_w)} × {_num(o.page_h)} mm"
                 f" paper){', nested' if o.nested else ''}"))
    y += _text(painter, D, x, y, w, 7, "Pattern details", 4.6, bold=True) + 1.5
    label_w = 44.0
    for label, value in rows:
        if y > bottom - 6:
            break
        _text(painter, D, x, y, label_w, 6, label, 3.3, bold=True)
        y += max(5.0, _text(painter, D, x + label_w, y, w - label_w, 12, value,
                            3.3, wrap=True) + 1.0)
    if any(s.cols * s.rows > 1 for s in plan_.sheets):
        y += 1.0
        y += _text(painter, D, x, y, w, 16,
                   "Large pieces are tiled over several pages; each page's "
                   "header says which row and column it is. Trim the left and "
                   "top edges of a page along its box, lay it over the page "
                   "to its left (or above) so its box edge meets the dashed "
                   "line, and tape. The pages overlap by "
                   f"{_num(o.overlap)} mm and each overlap names the page "
                   "that joins there.", 3.0, wrap=True,
                   color=QColor(80, 80, 80)) + 2.0
    if layout is not None and not beside and bottom - y > 50.0:
        y += 2.0
        y += _text(painter, D, x, y, w, 6, "Cutting layout (not to scale)",
                   3.3, bold=True) + 1.0
        tall = min(70.0, bottom - y - 24.0)
        _draw_layout(painter, D, plan_, layout, number, x, y, w, tall)
        y += tall + 2.0

    # the pieces
    y += 3.0
    if y > bottom - 12 or not table:
        return
    y += _text(painter, D, x, y, w, 7, "Pieces", 4.6, bold=True) + 1.5
    cols = [(0.0, 8.0, "#"), (8.0, 66.0, "Piece"), (74.0, 12.0, "Cut"),
            (86.0, 56.0, "Size"), (142.0, 16.0, "Holes"),
            (158.0, max(18.0, w - 158.0), "Page")]
    for cx, cw_, head in cols:
        _text(painter, D, x + cx, y, cw_, 6, head, 3.1, bold=True)
    y += 5.0
    for i, (part, many, pages) in enumerate(table):
        if y > bottom - 5:
            left = len(table) - i
            _text(painter, D, x, y, w, 6, f"… and {left} more", 3.1,
                  color=QColor(90, 90, 90))
            break
        pw, ph = part.size()
        pages = sorted(pages)
        where = (f"{pages[0]}" if len(pages) == 1 else
                 f"{pages[0]}–{pages[-1]}" if pages == list(range(pages[0], pages[-1] + 1))
                 else ", ".join(map(str, pages))) if pages else ""
        cells = [f"{i + 1}", part.name, f"{many}", _mm_in(pw, ph),
                 f"{part.holes}", where]
        for (cx, cw_, _h), text in zip(cols, cells):
            _text(painter, D, x + cx, y, cw_, 6, text, 3.1)
        y += 4.6


def render(device, doc, opts: Optional[PrintOptions] = None) -> PrintPlan:
    """Paint ``doc`` onto ``device`` (a QPdfWriter / QPrinter whose page size
    is already set) at 1:1 mm scale. Returns the plan it printed."""
    opts = opts or PrintOptions()
    p = plan(doc, opts)
    D = device.resolution() / 25.4        # device dots per mm (painter not scaled)
    painter = QPainter(device)
    painter.setRenderHint(QPainter.Antialiasing, True)
    total = len(p.pages)
    page_no_of = {(id(pg.sheet), pg.row, pg.col): n
                  for n, pg in enumerate(p.pages, start=1)
                  if pg.kind == "pattern"}
    for n, page in enumerate(p.pages, start=1):
        if n > 1:
            device.newPage()
        tile = ""
        if page.kind == "details":
            _details(painter, D, p, doc)
        else:
            _draw_pattern_page(painter, D, p, page, page_no_of)
            sheet = page.sheet
            if sheet.cols * sheet.rows > 1:
                tag = f"Sheet {page.sheet_no} · " if page.sheet_no else ""
                tile = (f"{tag}row {page.row + 1} of {sheet.rows}, "
                        f"column {page.col + 1} of {sheet.cols}")
        _decorate(painter, D, p, n, total, tile=tile,
                  scale=page.kind != "details")
    painter.end()
    return p


def export_pdf(doc, path: str, opts: Optional[PrintOptions] = None,
               dpi: int = 300) -> PrintPlan:
    """Write ``doc`` to a 1:1 PDF at ``path``. Returns the plan it printed."""
    opts = opts or PrintOptions()
    writer = QPdfWriter(path)
    writer.setResolution(dpi)
    writer.setPageSize(QPageSize(QSizeF(opts.page_w, opts.page_h),
                                 QPageSize.Unit.Millimeter))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    writer.setTitle(opts.title or "Stitch Hero pattern (1:1)")
    if opts.designer:
        writer.setCreator(opts.designer)
    return render(writer, doc, opts)


# -- the original entry points, kept for callers and tests -------------------
def render_tiled(device, doc, *, page_w=210.0, page_h=297.0,
                 margin=10.0, overlap=8.0):
    """The drawing as laid out, tiled; returns (rows, cols)."""
    p = render(device, doc, PrintOptions(page_w=page_w, page_h=page_h,
                                         nested=False, details=False,
                                         overlap=overlap))
    s = p.sheets[0]
    return s.rows, s.cols


def export_pdf_tiled(doc, path: str, *, page_w=210.0, page_h=297.0,
                     margin=10.0, overlap=8.0, dpi=300):
    """Write ``doc`` as laid out to a 1:1, tiled PDF. Returns (rows, cols)."""
    p = export_pdf(doc, path, PrintOptions(page_w=page_w, page_h=page_h,
                                           nested=False, details=False,
                                           overlap=overlap), dpi)
    s = p.sheets[0]
    return s.rows, s.cols

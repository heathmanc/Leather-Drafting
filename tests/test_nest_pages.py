"""Nesting engine: a gap that's a true minimum, what travels together, and
laying pieces out on printer pages for a 1:1 print."""

import math

from leathercad.geometry import Vec2
from leathercad.nesting import (NestPiece, PageSheet, _mark_segment,
                                cell_for_gap, group_pieces, layout_pages,
                                nest, smallest_sheet)
from leathercad.shapes import PathShape, Rectangle, Transform


def _ring(x, y, w, h):
    return ([Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h), Vec2(x, y + h),
             Vec2(x, y)], True)


def _blob(r, n, rot):
    pts = [Vec2(r * math.cos(rot + 2 * math.pi * k / n),
                0.6 * r * math.sin(rot + 2 * math.pi * k / n)) for k in range(n)]
    return (pts + [pts[0]], True)


def _seg_d(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_sq(), 1e-18)))
    return (a.lerp(b, t) - p).length()


def _ring_d(A, B):
    return min(min(_seg_d(p, B[j], B[j + 1]) for p in A for j in range(len(B) - 1)),
               min(_seg_d(q, A[i], A[i + 1]) for q in B for i in range(len(A) - 1)))


def _placed(piece, pl):
    return [[pl.apply(p) for p in pts] for pts, _c in piece.outlines]


# -- the gap ------------------------------------------------------------------
def test_every_cell_a_segment_passes_through_is_marked():
    """Sampling an edge every half cell could step over a cell's corner and
    let another piece in up to a cell closer than the gap asked for."""
    for ax, ay, bx, by in ((0.3, 0.2, 7.9, 3.1), (5.5, 0.5, 0.5, 5.5),
                           (0.9, 0.1, 1.1, 6.9), (2.0, 2.0, 6.0, 6.0),
                           (6.7, 4.2, 0.2, 0.9)):
        rows = [0] * 10
        _mark_segment(rows, ax, ay, bx, by, 10, 10)
        for k in range(2001):
            t = k / 2000
            x, y = ax + t * (bx - ax), ay + t * (by - ay)
            assert rows[int(math.floor(y))] >> int(math.floor(x)) & 1, (x, y)


def test_nested_pieces_keep_the_gap_on_their_true_outlines():
    pieces = [NestPiece(k, [_blob(9 + (k * 7) % 23, 3 + k % 6, 0.37 * k)])
              for k in range(14)]
    for cell in (2.0, cell_for_gap(8.0)):
        pl, unplaced, _used = nest(pieces, 260, 200, margin=5, spacing=8.0,
                                   cell=cell)
        assert not unplaced
        shapes = [_placed(pieces[p.key], p)[0] for p in pl]
        closest = min(_ring_d(shapes[i], shapes[j])
                      for i in range(len(shapes))
                      for j in range(i + 1, len(shapes)))
        assert closest >= 8.0 - 1e-9


def test_a_cell_the_half_gap_divides_keeps_the_gap_it_was_asked_for():
    c = cell_for_gap(8.0)
    assert 1.0 <= c <= 2.0
    assert abs(4.0 / c - round(4.0 / c)) < 1e-9


# -- what travels together ----------------------------------------------------
def test_pieces_bring_what_lies_on_them():
    from leathercad.holes import LooseHole
    from leathercad.text import TextShape
    panel = Rectangle(width=100, height=80, layer="Cut",
                      transform=Transform(x=0, y=0))
    slot = Rectangle(width=30, height=6, layer="Cut",
                     transform=Transform(x=0, y=20))
    pocket = Rectangle(width=60, height=40, layer="Cut",
                       transform=Transform(x=200, y=0))
    # a fold line drawn a little past both of the pocket's edges
    fold = PathShape(points=[Vec2(-35, 0), Vec2(35, 0)], close_path=False,
                     layer="Score", transform=Transform(x=200, y=0))
    hole = LooseHole(point=Vec2(10, -10))
    tag = TextShape(text="A", transform=Transform(x=500, y=500),
                    group_id="g1")            # far away, but grouped
    pocket.group_id = "g1"
    groups, (lh, ls, lt) = group_pieces([panel, slot, pocket, fold],
                                        holes=[hole], texts=[tag])
    assert len(groups) == 2
    by_first = {id(g.shapes[0]): g for g in groups}
    p = by_first[id(panel)]
    q = by_first[id(pocket)]
    assert slot in p.shapes and hole in p.holes
    assert fold in q.shapes and tag in q.texts
    assert (lh, ls, lt) == ([], [], [])


# -- printer pages ------------------------------------------------------------
_W, _H, _OV = 190.0, 265.0, 8.0


def _page_rects(sheet):
    sw, sh = _W - _OV, _H - _OV
    return [(c * sw, sheet.height - r * sh - _H, c * sw + _W, sheet.height - r * sh)
            for r in range(sheet.rows) for c in range(sheet.cols)]


def _box(pts_list):
    xs = [p.x for pts in pts_list for p in pts]
    ys = [p.y for pts in pts_list for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _inside(b, r, pad=0.0):
    return (b[0] >= r[0] + pad - 1e-6 and b[1] >= r[1] + pad - 1e-6
            and b[2] <= r[2] - pad + 1e-6 and b[3] <= r[3] - pad + 1e-6)


def test_small_pieces_are_printed_whole_one_page_each_and_apart():
    pieces = [NestPiece(i, [_ring(400 * i, 0, 50 + 7 * (i % 5), 30 + 11 * (i % 3))])
              for i in range(18)]
    sheets, unplaced = layout_pages(pieces, _W, _H, overlap=_OV, spacing=8.0,
                                    pad=2.0)
    assert not unplaced
    assert all(s.cols == s.rows == 1 for s in sheets)
    placed = sum(len(s.placements) for s in sheets)
    assert placed == len(pieces)
    assert len(sheets) <= 3                 # 18 small pieces, a few pages
    for s in sheets:
        shapes = [_placed(pieces[p.key], p)[0] for p in s.placements]
        for pts in shapes:
            assert _inside(_box([pts]), (0, 0, _W, _H), pad=2.0)
        for i in range(len(shapes)):
            for j in range(i + 1, len(shapes)):
                assert _ring_d(shapes[i], shapes[j]) >= 8.0 - 1e-9


def test_a_long_piece_is_tiled_and_shares_its_pages():
    gusset = NestPiece("gusset", [_ring(0, 0, 90, 700)])
    straps = [NestPiece(f"strap{i}", [_ring(0, 1000 + 100 * i, 600, 25)])
              for i in range(2)]
    tabs = [NestPiece(f"tab{i}", [_ring(2000 + 100 * i, 0, 40, 25)])
            for i in range(3)]
    pieces = [gusset] + straps + tabs
    sheets, unplaced = layout_pages(pieces, _W, _H, overlap=_OV, spacing=8.0,
                                    pad=2.0)
    assert not unplaced
    tiled = [s for s in sheets if s.cols * s.rows > 1]
    assert len(tiled) == 1                  # the straps ride along the gusset
    keys = {p.key for p in tiled[0].placements}
    assert {"gusset", "strap0", "strap1"} <= keys
    by_key = {p.key: p for p in pieces}
    # a small piece on a tiled sheet still lies whole on one page, off the
    # strips where the pages overlap
    rects = _page_rects(tiled[0])
    for pl in tiled[0].placements:
        if pl.key.startswith("tab"):
            b = _box(_placed(by_key[pl.key], pl))
            assert sum(1 for r in rects if _inside(b, r)) == 1
            assert sum(1 for r in rects
                       if b[0] < r[2] and r[0] < b[2] and b[1] < r[3] and r[1] < b[3]) == 1


def test_smallest_sheet_holds_every_piece():
    pieces = [NestPiece(i, [_ring(0, 0, 40 + 10 * i, 30)]) for i in range(6)]
    w, h = smallest_sheet(pieces, 8.0)
    area = sum((40 + 10 * i) * 30 for i in range(6))
    assert w * h >= area
    assert max(w, h) >= 90 - 1e-6           # the longest piece fits
    assert w * h <= 3.0 * area              # and it is a fair estimate


def test_page_sheet_is_a_plain_record():
    s = PageSheet(2, 3, 372.0, 788.0)
    assert (s.cols, s.rows, s.placements) == (2, 3, [])

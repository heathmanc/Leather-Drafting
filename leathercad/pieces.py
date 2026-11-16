"""Which closed outlines are pieces of leather, and which are holes cut out
of one.

An outline lying inside another is usually a cutout -- a buckle slot, a
thumb hole, a card window, the middle of a ring -- cut out of the piece
round it. But a pattern is often drawn with one piece over another: a copy
of a piece left on top of the original, a lining laid on its outer. Those
are pieces of their own, to be counted, printed and nested apart.

The rule: an outline inside another is a piece of its own when it's nearly
as big -- at least ``PIECE_SHARE`` of the outline it lies in: a copy, or a
lining a few millimetres in from its outer edge -- and an outline inside a
cutout is a piece again (what drops out of a hole is cut apart from the
piece, as a laser cuts it). Everything else inside a piece is a cutout of
it (a bangle's middle is a hole, not a piece). A piece drawn where it's
sewn on, smaller than that --
a pocket on its panel -- is read as a cutout, as a laser would cut it: draw
it apart.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2, point_in_polygon

# the least share of the outline it lies in that makes an outline a piece of
# its own rather than a hole in it: a lining 3 mm in from a 100 x 70 mm
# piece is 86 % of it; a leather ring 70 mm across with a 3 mm band, 84 %
PIECE_SHARE = 0.85


def ring_area(pts: Sequence[Vec2]) -> float:
    """The area inside a closed outline (a repeated closing point is fine)."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        a += p.x * q.y - q.x * p.y
    return abs(a) / 2.0


def _box(pts):
    xs, ys = [p.x for p in pts], [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def is_own_piece(inner_area: float, host_area: float) -> bool:
    """Whether an outline of ``inner_area`` lying inside one of
    ``host_area`` is a piece of its own (else a hole cut out of it)."""
    return inner_area >= PIECE_SHARE * host_area - 1e-9


def classify(rings: Sequence[Sequence[Vec2]]
             ) -> Tuple[List[bool], List[Optional[int]], List[float]]:
    """For each closed outline (cut outlines, world mm): whether it is a
    piece, the outline it lies in (or None) and its area.

    An outline lies in the smallest bigger outline that holds it (of two the
    same size, in the earlier one, so a copy laid exactly on another has
    somewhere to be: it is a piece either way)."""
    n = len(rings)
    areas = [ring_area(r) for r in rings]
    boxes = [_box(r) for r in rings]
    samples = [list(r[::max(1, len(r) // 12)]) for r in rings]

    def key(k):
        return (areas[k], -k)

    parent: List[Optional[int]] = [None] * n
    eps = 1e-6
    for i in range(n):
        bi = boxes[i]
        best = None
        for j in range(n):
            if j == i or key(j) <= key(i):
                continue
            bj = boxes[j]
            if not (bj[0] <= bi[0] + eps and bj[1] <= bi[1] + eps
                    and bj[2] >= bi[2] - eps and bj[3] >= bi[3] - eps):
                continue
            if best is not None and key(j) >= key(best):
                continue
            if all(point_in_polygon(p, rings[j]) for p in samples[i]):
                best = j
        parent[i] = best
    piece = [True] * n
    for i in sorted(range(n), key=key, reverse=True):       # outer first
        p = parent[i]
        if p is not None and piece[p]:
            piece[i] = is_own_piece(areas[i], areas[p])
    return piece, parent, areas


def on_cut_layer(doc):
    """A test: is a shape on a layer that's cut (the pieces themselves),
    rather than scored, engraved or a guide?"""
    from .layers import CUT

    def test(sh) -> bool:
        if getattr(sh, "construction", False):
            return False
        lyr = doc.layer(getattr(sh, "layer", ""))
        return getattr(lyr, "role", CUT) == CUT if lyr is not None else True
    return test

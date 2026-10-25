"""Fold allowance in 2D.

A fold consumes material: the leather's neutral fibre travels an arc round
the bend, so a flat pattern must be LONGER across every fold than the
finished piece by ``angle * (inside radius + k * thickness)`` (see
``fold3d.fold_bend_allowance``). ``apply_fold_allowance`` does that to a
drawn piece: everything on the flap side of a fold line slides away from
the fold by the allowance, and the fold line moves to the middle of the
bend zone, so the piece is drawn finished-size and cut true-size.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2
from .shapes import EditablePath, PathShape, Polygon, copy_shape_attrs


def fold_allowance(angle_deg: float, thickness: float, radius: float = 0.0,
                   k: float = 0.5) -> float:
    """Extra length (mm) one fold of ``angle_deg`` needs in ``thickness`` mm
    leather with an inside bend radius ``radius`` (0 = a sharp crease). ``k``
    places the neutral fibre (0.5 = mid-thickness)."""
    return math.radians(abs(angle_deg)) * (radius + k * thickness)


def _fold_axis(fold) -> Optional[Tuple[Vec2, Vec2]]:
    """World (point, unit direction) of a fold line shape."""
    pts = fold.world_polyline()[0]
    if len(pts) < 2:
        return None
    a, b = pts[0], pts[-1]
    d = b - a
    if d.length() < 1e-9:
        return None
    return a, d.normalized()


def _side(p: Vec2, a: Vec2, u: Vec2) -> float:
    """Signed distance of ``p`` from the axis (positive on the left of u)."""
    return (p.x - a.x) * -u.y + (p.y - a.y) * u.x


def folds_crossing(piece, folds: Sequence) -> List:
    """The fold-line shapes among ``folds`` whose line crosses ``piece``."""
    pts = piece.world_polyline()[0]
    out = []
    for f in folds:
        ax = _fold_axis(f)
        if ax is None or not pts:
            continue
        sides = [_side(p, ax[0], ax[1]) for p in pts]
        if min(sides) < -1e-6 < 1e-6 < max(sides):
            out.append(f)
    return out


def apply_fold_allowance(doc, piece, folds: Sequence, thickness: float,
                         radius: float = 0.0, k: float = 0.5) -> List[Tuple]:
    """Lengthen ``piece`` across each of ``folds`` (fold-line shapes crossing
    it) by that fold's allowance. The smaller side of the piece is the flap
    that moves, together with any holes, seams and shapes of ``doc`` lying
    wholly on that side within the piece; the fold line moves half way. A
    parametric piece is converted to editable nodes first (the caller
    swaps the returned shape in). Returns ``[(fold, allowance, moved_piece)]``.
    """
    from .modify import to_editable
    from .shapes import Rectangle, Ellipse

    done = []
    for fold in folds:
        ax = _fold_axis(fold)
        if ax is None:
            continue
        a, u = ax
        n = Vec2(-u.y, u.x)                      # the flap moves along +/- n
        allow = fold_allowance(getattr(fold, "fold_angle", 90.0) or 90.0,
                               thickness, radius, k)
        if allow <= 1e-9:
            continue
        # the piece as nodes we can move
        if isinstance(piece, (Rectangle, Ellipse)):
            ep = (EditablePath.from_rounded_rect(piece.width, piece.height,
                                                 piece.corner_radius)
                  if isinstance(piece, Rectangle)
                  else EditablePath.from_ellipse(piece.rx, piece.ry))
            ep.transform = piece.transform
            copy_shape_attrs(piece, ep, holes=True)
            ep.stitch = piece.stitch
            ep.stitch_edges = None               # edge count changed
            piece = ep
        elif isinstance(piece, (Polygon, PathShape)):
            piece = to_editable(piece)
        if not isinstance(piece, EditablePath):
            continue
        t = piece.transform
        # which side is the flap: the one with less of the outline
        pts = piece.world_polyline()[0]
        pos = sum(1 for p in pts if _side(p, a, u) > 0)
        sign = 1.0 if pos <= len(pts) - pos else -1.0
        shift = Vec2(n.x * allow * sign, n.y * allow * sign)

        def moved(p):                            # a world point on the flap?
            return _side(p, a, u) * sign > 1e-6

        def shove(local):
            w = t.apply(local)
            return t.inverse_apply(Vec2(w.x + shift.x, w.y + shift.y)) \
                if moved(w) else local
        piece.nodes = [shove(p) for p in piece.nodes]
        for e in piece.edges:
            for attr in ("mid", "c1", "c2"):
                q = getattr(e, attr)
                if q is not None:
                    setattr(e, attr, shove(q))
        if piece.baked_holes:
            for h in piece.baked_holes:
                h.point = shove(h.point)
        # the rest of the document on the flap, inside the piece
        b = piece.bounds()

        def inside(p):
            return b[0] - 1e-6 <= p.x <= b[2] + 1e-6 and \
                b[1] - 1e-6 <= p.y <= b[3] + 1e-6
        for sh in doc.shapes:
            if sh is piece or sh is fold or sh in folds:
                continue
            c = sh.transform.apply(Vec2(0.0, 0.0))
            spts = sh.world_polyline()[0]
            if spts and all(moved(p) and inside(p) for p in spts):
                sh.transform.x += shift.x
                sh.transform.y += shift.y
        for h in getattr(doc, "holes", []):
            if moved(h.point) and inside(h.point):
                h.point = Vec2(h.point.x + shift.x, h.point.y + shift.y)
        for sl in getattr(doc, "stitch_lines", []):
            if sl.points and all(moved(p) and inside(p) for p in sl.points):
                sl.points = [Vec2(p.x + shift.x, p.y + shift.y)
                             for p in sl.points]
                sl.corner_points = [Vec2(p.x + shift.x, p.y + shift.y)
                                    for p in sl.corner_points]
        # the crease sits in the middle of the bend zone
        fold.transform.x += shift.x / 2.0
        fold.transform.y += shift.y / 2.0
        done.append((fold, allow, piece))
    return done

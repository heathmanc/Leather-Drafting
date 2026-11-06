"""Trim: remove the span of an outline between the two intersections that
bracket a picked point -- the classic 2D-sketch trim (Fusion 360 / LightBurn).

It works on the same segment representation the app already uses for
break-apart: a list of ``(kind, [world points])`` where a line is
``("line", [a, b])``, an arc is ``("arc", [a, mid, b])`` (``mid`` is a point
the arc passes through) and a cubic bezier ``("bezier", [a, c1, c2, b])``.
Curve type is preserved -- trimming an arc yields arcs.

The picked entity is intersected against *cutters*: every other outline, as
its true lines, arcs and curves (a :class:`Cutter`; a plain list of points is
taken as a polyline). Cutting against the flattened polygon the screen draws
put every cut on a chord, up to the flattening tolerance inside a curve: the
trimmed end then sat a hair off the outline it was trimmed to, and where two
curves cross at a shallow angle that left a nub. The intersections split the
entity into cells; the cell containing the pick is removed and the remaining
cells are returned as new chains (an open path, or two of them if the entity
was open and cut in the middle). Returns ``None`` to signal "delete the whole
entity" (a closed loop with nothing crossing it, or an open one with no
bracketing cut).

Three rules keep the ends clean:

* a cut line whose loose end stops within ``TOUCH_TOL`` of the outline (or
  runs a hair past it) cuts at that end, and the trimmed outline is made to
  end exactly on it -- the two share one point;
* a crossing within ``SLIVER`` of an open end of the picked outline doesn't
  leave a separate sliver: trimming the span next to it takes the sliver too;
* the hover preview (``removed_cell_polyline`` given ``display``) follows the
  outline as the screen draws it, so it lies on the line it highlights.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2
from .shapes import arc_through

_EPS = 1e-9
# How close (mm) a cut line's loose end must come to an outline to cut it.
TOUCH_TOL = 0.05
# A crossing of that same cut line this close (mm) to such an end is the end
# itself: a line trimmed against a flattened curve stops a hair inside it and
# then crosses the true curve just beyond its end.
_NEAR = 0.3
# A cut this close (mm) to an open end of the picked outline doesn't leave a
# separate sliver there (unless the sliver itself is what was clicked).
SLIVER = 0.3
# Geometric tolerance (mm): a point this close to an arc's end is on the arc,
# a line passing this close to a circle touches it.
_ON = 1e-6
# A curve is split until its control points lie this close (mm) to its chord.
_FLAT = 1e-7

Segment = Tuple[str, List[Vec2]]
Box = Tuple[float, float, float, float]


# ---------------------------------------------------------------------------
# One primitive piece of an outline (a line or a circular arc)
# ---------------------------------------------------------------------------
class _Piece:
    bez = None                   # a _BezPiece's control points

    def __init__(self, kind: str, a: Vec2, b: Vec2, mid: Optional[Vec2] = None):
        self.a = a
        self.b = b
        arc = arc_through(a, mid, b) if (kind == "arc" and mid is not None) else None
        if arc is None:
            self.center = None
            self.length = (b - a).length()
            self.box = (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
        else:
            self.center, self.r, self.ang0, self.ang1, self.ccw = arc
            self.sweep = _signed_sweep(self.ang0, self.ang1, self.ccw)
            self.length = abs(self.sweep) * self.r
            self.box = _arc_box(self)

    def point_at(self, f: float) -> Vec2:
        f = max(0.0, min(1.0, f))
        if self.center is None:
            return self.a.lerp(self.b, f)
        ang = self.ang0 + self.sweep * f
        return Vec2(self.center.x + self.r * math.cos(ang),
                    self.center.y + self.r * math.sin(ang))

    def frac_of(self, p: Vec2) -> float:
        """Approximate fraction in [0, 1] of a point lying on the piece."""
        if self.center is None:
            ab = self.b - self.a
            d = ab.length_sq()
            if d <= _EPS:
                return 0.0
            return max(0.0, min(1.0, (p - self.a).dot(ab) / d))
        ang = math.atan2(p.y - self.center.y, p.x - self.center.x)
        rel = _frac_along(ang - self.ang0, self.sweep)
        return max(0.0, min(1.0, rel))

    def flatten(self, flatness: float = 0.05) -> List[Vec2]:
        if self.center is None:
            return [self.a, self.b]
        ratio = max(-1.0, min(1.0, 1.0 - flatness / max(self.r, 1e-6)))
        step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi
        n = max(2, int(math.ceil(abs(self.sweep) / max(step, 1e-3))) + 1)
        return [self.point_at(i / (n - 1)) for i in range(n)]

    def sub(self, f0: float, f1: float) -> Segment:
        p0, p1 = self.point_at(f0), self.point_at(f1)
        if self.center is None:
            return ("line", [p0, p1])
        return ("arc", [p0, self.point_at(0.5 * (f0 + f1)), p1])


class _BezPiece(_Piece):
    """A cubic bezier piece. Fractions are by arc length, through a fine
    sample table; ``sub`` splits the curve exactly (de Casteljau), so a span
    the trim keeps whole comes back with its very own control points."""
    _N = 128

    def __init__(self, a: Vec2, c1: Vec2, c2: Vec2, b: Vec2):
        self.a, self.b = a, b
        self.center = None
        self.bez = (a, c1, c2, b)
        self.box = _ctrl_box(self.bez)
        self._pts = [self._at(i / self._N) for i in range(self._N + 1)]
        self._cum = [0.0]
        for i in range(self._N):
            self._cum.append(self._cum[-1]
                             + (self._pts[i + 1] - self._pts[i]).length())
        self.length = self._cum[-1]

    def _at(self, t: float) -> Vec2:
        a, c1, c2, b = self.bez
        u = 1.0 - t
        return (a * (u * u * u) + c1 * (3 * u * u * t)
                + c2 * (3 * u * t * t) + b * (t * t * t))

    def _t_of(self, f: float) -> float:
        s = max(0.0, min(1.0, f)) * self.length
        lo, hi = 0, self._N
        while hi - lo > 1:
            m = (lo + hi) // 2
            if self._cum[m] <= s:
                lo = m
            else:
                hi = m
        seg = self._cum[lo + 1] - self._cum[lo]
        k = (s - self._cum[lo]) / seg if seg > _EPS else 0.0
        return (lo + k) / self._N

    def frac_of_t(self, t: float) -> float:
        """The arc-length fraction of curve parameter ``t`` -- the exact
        inverse of ``_t_of``, so a split made at a crossing's ``t`` lands on
        that very parameter."""
        if self.length <= _EPS:
            return 0.0
        x = max(0.0, min(1.0, t)) * self._N
        i = min(int(x), self._N - 1)
        k = x - i
        s = self._cum[i] + k * (self._cum[i + 1] - self._cum[i])
        return max(0.0, min(1.0, s / self.length))

    def point_at(self, f: float) -> Vec2:
        return self._at(self._t_of(f))

    def frac_of(self, p: Vec2) -> float:
        best, bs = float("inf"), 0.0
        for i in range(self._N):
            a, b = self._pts[i], self._pts[i + 1]
            ab = b - a
            d2 = ab.length_sq()
            k = 0.0 if d2 <= _EPS else max(0.0, min(1.0, (p - a).dot(ab) / d2))
            d = (a + ab * k - p).length()
            if d < best:
                best, bs = d, self._cum[i] + k * ab.length()
        return bs / self.length if self.length > _EPS else 0.0

    def flatten(self, flatness: float = 0.05) -> List[Vec2]:
        return list(self._pts)

    def sub(self, f0: float, f1: float) -> Segment:
        t0, t1 = self._t_of(f0), self._t_of(f1)
        a, c1, c2, b = self.bez
        # head [0, t1] ...
        p01, p12, p23 = a.lerp(c1, t1), c1.lerp(c2, t1), c2.lerp(b, t1)
        p012, p123 = p01.lerp(p12, t1), p12.lerp(p23, t1)
        h = (a, p01, p012, p012.lerp(p123, t1))
        # ... then its tail from t0 (rescaled into the head's own range)
        u = t0 / t1 if t1 > _EPS else 0.0
        q01, q12, q23 = h[0].lerp(h[1], u), h[1].lerp(h[2], u), h[2].lerp(h[3], u)
        q012, q123 = q01.lerp(q12, u), q12.lerp(q23, u)
        return ("bezier", [q012.lerp(q123, u), q123, q23, h[3]])


def _piece_from(kind: str, pts: List[Vec2]) -> _Piece:
    if kind == "bezier" and len(pts) >= 4:
        return _BezPiece(pts[0], pts[1], pts[2], pts[-1])
    return _Piece(kind, pts[0], pts[-1],
                  pts[1] if kind == "arc" and len(pts) >= 3 else None)


def _signed_sweep(a0: float, a1: float, ccw: bool) -> float:
    two_pi = 2.0 * math.pi
    if ccw:
        d = (a1 - a0) % two_pi
    else:
        d = -((a0 - a1) % two_pi)
    if abs(d) < 1e-12:
        d = two_pi if ccw else -two_pi
    return d


def _frac_along(dang: float, sweep: float) -> float:
    two_pi = 2.0 * math.pi
    if sweep >= 0:
        dd = dang % two_pi
    else:
        dd = -((-dang) % two_pi)
    return dd / sweep if abs(sweep) > _EPS else 0.0


def _arc_frac(p: _Piece, x: Vec2) -> Optional[float]:
    """Fraction along arc piece ``p`` of a point ``x`` on its circle, or None
    when ``x`` is off the arc's sweep. A point within ``_ON`` of either end
    counts as that end (it can come out a hair before the start, which a
    plain angle test reads as the far side of the circle)."""
    ang = math.atan2(x.y - p.center.y, x.x - p.center.x)
    two_pi = 2.0 * math.pi
    dd = ((ang - p.ang0) if p.sweep >= 0 else (p.ang0 - ang)) % two_pi
    sw = abs(p.sweep)
    tol = _ON / max(p.r, _EPS)
    if dd <= sw + tol:
        return min(1.0, dd / sw) if sw > _EPS else 0.0
    if two_pi - dd <= tol:
        return 0.0
    return None


def _arc_box(p: _Piece) -> Box:
    xs, ys = [p.a.x, p.b.x], [p.a.y, p.b.y]
    for k in range(4):                     # the circle's extremes on the sweep
        ang = k * 0.5 * math.pi
        x = Vec2(p.center.x + p.r * math.cos(ang), p.center.y + p.r * math.sin(ang))
        if _arc_frac(p, x) is not None:
            xs.append(x.x)
            ys.append(x.y)
    return (min(xs), min(ys), max(xs), max(ys))


def _ctrl_box(c) -> Box:
    xs = [q.x for q in c]
    ys = [q.y for q in c]
    return (min(xs), min(ys), max(xs), max(ys))


def _union_box(boxes) -> Box:
    boxes = list(boxes)
    if not boxes:
        return (math.inf, math.inf, -math.inf, -math.inf)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _boxes_meet(a: Box, b: Box, tol: float) -> bool:
    return (a[0] <= b[2] + tol and b[0] <= a[2] + tol
            and a[1] <= b[3] + tol and b[1] <= a[3] + tol)


def _in_box(p: Vec2, b: Box, tol: float) -> bool:
    return b[0] - tol <= p.x <= b[2] + tol and b[1] - tol <= p.y <= b[3] + tol


# ---------------------------------------------------------------------------
# Exact intersections
# ---------------------------------------------------------------------------
def _seg_intersect(a1: Vec2, a2: Vec2, b1: Vec2, b2: Vec2) -> Optional[Vec2]:
    """Proper intersection point of segments a1a2 and b1b2, or None."""
    r = a2 - a1
    s = b2 - b1
    rxs = r.cross(s)
    if abs(rxs) < 1e-12:
        return None
    qp = b1 - a1
    t = qp.cross(s) / rxs
    u = qp.cross(r) / rxs
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return a1 + r * t
    return None


def _line_line(p: _Piece, q: _Piece):
    """[(fraction on p, fraction on q, point)] where two line pieces cross."""
    r = p.b - p.a
    s = q.b - q.a
    rxs = r.cross(s)
    if abs(rxs) <= 1e-12 * p.length * q.length:
        return []                                  # parallel (or overlapping)
    qp = q.a - p.a
    t = qp.cross(s) / rxs
    u = qp.cross(r) / rxs
    tp, tq = _ON / p.length, _ON / q.length
    if -tp <= t <= 1.0 + tp and -tq <= u <= 1.0 + tq:
        t = max(0.0, min(1.0, t))
        return [(t, max(0.0, min(1.0, u)), p.a + r * t)]
    return []


def _line_arc(ln: _Piece, arc: _Piece):
    """[(fraction on the line, fraction on the arc, point)]."""
    d = ln.b - ln.a
    L = ln.length
    f = ln.a - arc.center
    h = abs(f.cross(d)) / L                       # centre-to-line distance
    if h > arc.r + _ON:
        return []
    tc = -f.dot(d) / (L * L)                      # foot of the perpendicular
    w = math.sqrt(max(0.0, (arc.r - h) * (arc.r + h))) / L
    ts = [tc] if w * L <= _ON else [tc - w, tc + w]     # tangent: one point
    tol = _ON / L
    out = []
    for t in ts:
        if -tol <= t <= 1.0 + tol:
            t = max(0.0, min(1.0, t))
            x = ln.a + d * t
            fa = _arc_frac(arc, x)
            if fa is not None:
                out.append((t, fa, x))
    return out


def _circle_circle(c1: Vec2, r1: float, c2: Vec2, r2: float) -> List[Vec2]:
    dv = c2 - c1
    d = dv.length()
    if d <= _EPS:
        return []                                 # concentric: none (or all)
    if d > r1 + r2 + _ON or d < abs(r1 - r2) - _ON:
        return []                                 # apart, or one inside
    a = (r1 * r1 - r2 * r2 + d * d) / (2.0 * d)
    h2 = r1 * r1 - a * a
    u = dv * (1.0 / d)
    base = c1 + u * a
    if h2 <= 0.0:
        return [base]                             # touching
    h = math.sqrt(h2)
    if h <= _ON:
        return [base]
    perp = Vec2(-u.y, u.x)
    return [base + perp * h, base - perp * h]


def _arc_arc(p: _Piece, q: _Piece):
    out = []
    for x in _circle_circle(p.center, p.r, q.center, q.r):
        fp = _arc_frac(p, x)
        if fp is None:
            continue
        fq = _arc_frac(q, x)
        if fq is not None:
            out.append((fp, fq, x))
    return out


def _prim(p: _Piece, q: _Piece):
    """Crossings of two line/arc pieces: [(fraction on p, on q, point)]."""
    if p.center is None and q.center is None:
        return _line_line(p, q)
    if p.center is None:
        return _line_arc(p, q)
    if q.center is None:
        return [(fp, fq, x) for fq, fp, x in _line_arc(q, p)]
    return _arc_arc(p, q)


def _split(c, t: float = 0.5):
    a, b, cc, d = c
    ab, bc, cd = a.lerp(b, t), b.lerp(cc, t), cc.lerp(d, t)
    abc, bcd = ab.lerp(bc, t), bc.lerp(cd, t)
    m = abc.lerp(bcd, t)
    return (a, ab, abc, m), (m, bcd, cd, d)


def _flat(c) -> bool:
    """Whether a cubic is its own chord, to within ``_FLAT``."""
    a, b, cc, d = c
    ad = d - a
    L2 = ad.length_sq()
    if L2 <= _EPS * _EPS:
        return max((b - a).length(), (cc - a).length()) <= _FLAT
    L = math.sqrt(L2)
    for q in (b, cc):
        aq = q - a
        if abs(aq.cross(ad)) > _FLAT * L:
            return False
        k = aq.dot(ad)
        if k < -_FLAT * L or k > L2 + _FLAT * L:   # a handle past the chord
            return False
    return True


def _box_meets_circle(bx: Box, c: Vec2, r: float) -> bool:
    nx = min(max(c.x, bx[0]), bx[2])
    ny = min(max(c.y, bx[1]), bx[3])
    if math.hypot(nx - c.x, ny - c.y) > r + _ON:
        return False                              # all outside the circle
    fx = max(abs(bx[0] - c.x), abs(bx[2] - c.x))
    fy = max(abs(bx[1] - c.y), abs(bx[3] - c.y))
    return math.hypot(fx, fy) >= r - _ON          # not all inside it


def _misses_line(c, frame) -> bool:
    """The cubic's control hull lies wholly to one side of the line, or wholly
    past one of its ends: the curve, inside that hull, can't meet it. (A box
    test alone let every bit of curve inside a long diagonal's box through,
    to be split all the way down.)"""
    o, u, n, L = frame
    sd = [n.dot(p - o) for p in c]
    if min(sd) > _ON or max(sd) < -_ON:
        return True
    along = [u.dot(p - o) for p in c]
    return min(along) > L + _ON or max(along) < -_ON


def _misses_fat_line(a, b) -> bool:
    """Cubic ``a``'s hull lies wholly outside the band round cubic ``b``'s
    chord that holds all of ``b``'s control points (b's 'fat line')."""
    d = b[3] - b[0]
    L = d.length()
    if L <= _EPS:
        return False
    n = Vec2(-d.y / L, d.x / L)
    db = [n.dot(q - b[0]) for q in b]
    da = [n.dot(q - b[0]) for q in a]
    return min(da) > max(db) + _ON or max(da) < min(db) - _ON


_BUDGET = 20000          # sub-curves examined per pair, a guard on overlaps


def _bez_vs(ctrl, q: _Piece):
    """A cubic (control points) against a line/arc piece, by subdividing the
    cubic until it is flat: [(cubic parameter t, fraction on q, point)]."""
    out = []
    stack = [(ctrl, 0.0, 1.0, 0)]
    budget = _BUDGET
    frame = None
    if q.center is None:
        u = (q.b - q.a) * (1.0 / q.length)
        frame = (q.a, u, Vec2(-u.y, u.x), q.length)
    while stack and budget > 0:
        budget -= 1
        c, t0, t1, depth = stack.pop()
        bx = _ctrl_box(c)
        if not _boxes_meet(bx, q.box, _ON):
            continue
        if frame is not None:
            if _misses_line(c, frame):
                continue
        elif (not _box_meets_circle(bx, q.center, q.r)
              or max((p - q.center).length() for p in c) < q.r - _ON):
            continue                  # wholly outside, or inside, the circle
        if depth >= 48 or _flat(c):
            chord = _Piece("line", c[0], c[3])
            if chord.length > _EPS:
                for u, fq, x in _prim(chord, q):
                    out.append((t0 + (t1 - t0) * u, fq, x))
            continue
        left, right = _split(c)
        tm = 0.5 * (t0 + t1)
        stack.append((right, tm, t1, depth + 1))
        stack.append((left, t0, tm, depth + 1))
    return out


def _bez_bez(ca, cb):
    """Two cubics: [(parameter on a, parameter on b, point)]."""
    out = []
    stack = [(ca, 0.0, 1.0, cb, 0.0, 1.0, 0)]
    budget = _BUDGET
    while stack and budget > 0:
        budget -= 1
        a, a0, a1, b, b0, b1, depth = stack.pop()
        ba, bb = _ctrl_box(a), _ctrl_box(b)
        if not _boxes_meet(ba, bb, _ON):
            continue
        if _misses_fat_line(a, b) or _misses_fat_line(b, a):
            continue
        fa, fb = _flat(a), _flat(b)
        if (fa and fb) or depth >= 96:
            la, lb = _Piece("line", a[0], a[3]), _Piece("line", b[0], b[3])
            if la.length > _EPS and lb.length > _EPS:
                for u, v, x in _line_line(la, lb):
                    out.append((a0 + (a1 - a0) * u, b0 + (b1 - b0) * v, x))
            continue
        size_a = (ba[2] - ba[0]) + (ba[3] - ba[1])
        size_b = (bb[2] - bb[0]) + (bb[3] - bb[1])
        if not fa and (fb or size_a >= size_b):
            left, right = _split(a)
            m = 0.5 * (a0 + a1)
            stack.append((right, m, a1, b, b0, b1, depth + 1))
            stack.append((left, a0, m, b, b0, b1, depth + 1))
        else:
            left, right = _split(b)
            m = 0.5 * (b0 + b1)
            stack.append((a, a0, a1, right, m, b1, depth + 1))
            stack.append((a, a0, a1, left, b0, m, depth + 1))
    return out


def _cross(p: _Piece, q: _Piece):
    """Where entity piece ``p`` meets cutter piece ``q``:
    [(arc-length fraction along p, point)]."""
    if p.bez is None and q.bez is None:
        return [(fp, x) for fp, _fq, x in _prim(p, q)]
    if q.bez is None:
        return [(p.frac_of_t(t), x) for t, _fq, x in _bez_vs(p.bez, q)]
    if p.bez is None:
        return [(fp, x) for _t, fp, x in _bez_vs(q.bez, p)]
    return [(p.frac_of_t(ta), x) for ta, _tb, x in _bez_bez(p.bez, q.bez)]


# ---------------------------------------------------------------------------
# Cutters
# ---------------------------------------------------------------------------
class Cutter:
    """Another outline the picked one is cut against: its exact segments
    (the same ``(kind, pts)`` form), whether it closes, and its loose ends."""
    __slots__ = ("pieces", "closed", "ends", "box")

    def __init__(self, segments: Sequence[Segment], closed: Optional[bool] = None):
        self.pieces = [p for p in (_piece_from(k, list(pts))
                                   for k, pts in segments if len(pts) >= 2)
                       if p.length > _EPS]
        if closed is None:
            closed = (bool(self.pieces)
                      and (self.pieces[0].a - self.pieces[-1].b).length() <= _EPS)
        self.closed = bool(closed)
        self.ends = ([] if self.closed or not self.pieces
                     else [self.pieces[0].a, self.pieces[-1].b])
        self.box = _union_box(p.box for p in self.pieces)

    @classmethod
    def from_polyline(cls, pts: Sequence[Vec2],
                      closed: Optional[bool] = None) -> "Cutter":
        pts = list(pts)
        if closed is None:
            closed = len(pts) >= 2 and (pts[0] - pts[-1]).length() <= _EPS
        if closed and len(pts) >= 2 and (pts[0] - pts[-1]).length() > _EPS:
            pts.append(pts[0])                    # the closing edge cuts too
        return cls([("line", [pts[i], pts[i + 1]]) for i in range(len(pts) - 1)],
                   closed)


def _as_cutters(cutters) -> List[Cutter]:
    return [c if isinstance(c, Cutter) else Cutter.from_polyline(c)
            for c in cutters]


class _Hit:
    """A cut at arc-length ``s``; ``anchor`` is the cut line's loose end it
    came from, which the trimmed outline is made to end on."""
    __slots__ = ("s", "anchor")

    def __init__(self, s: float, anchor: Optional[Vec2] = None):
        self.s = s
        self.anchor = anchor


def _find_hits(pieces, cum, total, closed, cutters) -> List[_Hit]:
    ebox = _union_box(p.box for p in pieces)
    hits: List[_Hit] = []
    for cut in cutters:
        if not _boxes_meet(ebox, cut.box, TOUCH_TOL):
            continue
        # A cut line drawn to END on the outline counts as touching it even
        # when it stops a hair short: a field rounded to 4 decimals, a move,
        # or an earlier trim against a flattened curve leaves it a little off.
        ends = []
        for end in cut.ends:
            if not _in_box(end, ebox, TOUCH_TOL):
                continue
            s, d = _nearest(pieces, cum, end)
            if d <= TOUCH_TOL:
                ends.append(end)
                hits.append(_Hit(s, end))
        for pi, p in enumerate(pieces):
            if not _boxes_meet(p.box, cut.box, _ON):
                continue
            for q in cut.pieces:
                if not _boxes_meet(p.box, q.box, _ON):
                    continue
                for f, x in _cross(p, q):
                    if any((x - e).length() <= _NEAR for e in ends):
                        continue          # that end's own cut, taken above
                    hits.append(_Hit(cum[pi] + f * p.length))
    return _dedup(hits, total, closed)


def _dedup(hits: List[_Hit], total: float, closed: bool) -> List[_Hit]:
    """Merge cuts closer than a hair, keeping an anchored one (a cut line's
    end) over a plain crossing."""
    tol = max(0.02, 1e-4 * total)
    hits = sorted(hits, key=lambda h: h.s)
    out: List[_Hit] = []
    first = None
    for h in hits:
        if out and h.s - first <= tol:
            if h.anchor is not None and out[-1].anchor is None:
                out[-1] = h
            continue
        out.append(h)
        first = h.s
    if closed and len(out) >= 2 and out[0].s + total - out[-1].s <= tol:
        # the same cut, either side of the seam
        if out[-1].anchor is not None and out[0].anchor is None:
            out.pop(0)
        else:
            out.pop()
    return out


def intersection_arclengths(segments: Sequence[Segment], cutters,
                            flatness: float = 0.05) -> Tuple[List[float], float]:
    """Arc-length positions along the entity where it crosses any cutter, plus
    the entity's total length. Exposed for previewing/highlighting."""
    pieces, cum, total = _build(segments)
    if total <= _EPS:
        return [], total
    closed = (pieces[0].a - pieces[-1].b).length() <= 1e-6
    hits = _find_hits(pieces, cum, total, closed, _as_cutters(cutters))
    return [h.s for h in hits], total


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _cell_to_remove(pieces, cum, total, hits, click_s, closed):
    """(start_arclength, length) of the cell containing the pick, or None.
    For a closed entity with nothing crossing it, the whole loop is the cell."""
    if closed:
        if len(hits) < 2:
            return (0.0, total)                       # whole loop
        cells = [(hits[i], hits[i + 1]) for i in range(len(hits) - 1)]
        cells.append((hits[-1], hits[0] + total))     # the wrap cell
        for lo, hi in cells:
            cs = click_s if click_s >= lo - 1e-9 else click_s + total
            if lo - 1e-6 <= cs <= hi + 1e-6:
                return (lo % total, hi - lo)
        return None
    bounds = sorted(set([0.0, total] + hits))
    for i in range(len(bounds) - 1):
        if bounds[i] - 1e-6 <= click_s <= bounds[i + 1] + 1e-6:
            return (bounds[i], bounds[i + 1] - bounds[i])
    return None


def _absorb_slivers(hits: List[_Hit], total: float, click_s: float) -> List[_Hit]:
    """Drop the cuts that would leave a sliver at an open end of the picked
    outline -- unless the click is in that sliver (then it is what goes)."""
    sliver = min(SLIVER, 0.25 * total)
    out = []
    for h in hits:
        if h.s <= 1e-7 or h.s >= total - 1e-7:
            continue                              # a cut at the end itself
        if h.s <= sliver and click_s > h.s:
            continue
        if total - h.s <= sliver and click_s < h.s:
            continue
        out.append(h)
    return out


def _plan(segments, cutters, click_point, closed):
    pieces, cum, total = _build(segments)
    if total <= _EPS:
        return None
    hits = _find_hits(pieces, cum, total, closed, _as_cutters(cutters))
    click_s, _d = _nearest(pieces, cum, click_point)
    if not closed:
        hits = _absorb_slivers(hits, total, click_s)
    cell = _cell_to_remove(pieces, cum, total, [h.s for h in hits], click_s,
                           closed)
    return pieces, cum, total, hits, cell


def _anchor_at(hits: List[_Hit], s: float, total: float) -> Optional[Vec2]:
    for h in hits:
        d = abs(h.s - s)
        if min(d, abs(total - d)) <= 1e-7:
            return h.anchor
    return None


def trim(segments: Sequence[Segment], cutters,
         click_point: Vec2, closed: bool,
         flatness: float = 0.05) -> Optional[List[List[Segment]]]:
    """Trim the cell under ``click_point``. Returns the surviving chains (each a
    list of ``(kind, pts)`` segments), or ``None`` to delete the whole entity."""
    plan = _plan(segments, cutters, click_point, closed)
    if plan is None:
        return None
    pieces, cum, total, hits, cell = plan
    if cell is None:
        return None
    s0, clen = cell
    if closed:
        if clen >= total - 1e-6:
            return None                               # nothing brackets it
        start = (s0 + clen) % total
        chain = _walk(pieces, cum, total, start, total - clen)
        if not chain:
            return None
        _snap_ends(chain, _anchor_at(hits, start, total), _anchor_at(hits, s0, total))
        return [chain]
    chains: List[List[Segment]] = []
    if s0 > 1e-6:
        head = _walk(pieces, cum, total, 0.0, s0)
        if head:
            _snap_ends(head, None, _anchor_at(hits, s0, total))
            chains.append(head)
    end = s0 + clen
    if total - end > 1e-6:
        tail = _walk(pieces, cum, total, end, total - end)
        if tail:
            _snap_ends(tail, _anchor_at(hits, end, total), None)
            chains.append(tail)
    return chains or None


def removed_cell_polyline(segments: Sequence[Segment], cutters,
                          click_point: Vec2, closed: bool,
                          flatness: float = 0.05,
                          display: Optional[Sequence[Vec2]] = None
                          ) -> Optional[List[Vec2]]:
    """The portion that ``trim`` would remove for this pick, as a polyline for
    highlighting. ``None`` if nothing is under the pick.

    ``display`` is the outline as the screen draws it (the same world
    polyline): the highlight is then cut from it, so it lies exactly on the
    drawn line instead of beside it on a differently flattened copy."""
    plan = _plan(segments, cutters, click_point, closed)
    if plan is None:
        return None
    pieces, cum, total, _hits, cell = plan
    if cell is None:
        return None
    s0, clen = cell
    if display is not None and len(display) >= 2:
        poly = list(display)
        if closed and (poly[0] - poly[-1]).length() > 1e-9:
            poly.append(poly[0])
        if closed and clen >= total - 1e-6:
            return poly
        a = _point_at(pieces, cum, s0 % total)
        b = _point_at(pieces, cum, (s0 + clen) % total if closed else s0 + clen)
        m = _point_at(pieces, cum, (s0 + 0.5 * clen) % total if closed
                      else s0 + 0.5 * clen)
        out = _display_span(poly, a, b, m, closed, clen / total)
        if out and len(out) >= 2:
            return out
    removed = _walk(pieces, cum, total, s0, clen)
    out: List[Vec2] = []
    for kind, pts in removed:
        piece = _piece_from(kind, pts)
        fl = piece.flatten(flatness)
        if out and (out[-1] - fl[0]).length() < 1e-6:
            out.extend(fl[1:])
        else:
            out.extend(fl)
    return out or None


def first_hit(origin: Vec2, direction: Vec2, cutters,
              max_len: float = 10000.0) -> Optional[Tuple[float, Vec2]]:
    """(distance, point) of the nearest place, ahead of ``origin`` along
    ``direction``, where the ray meets any cutter's true lines, arcs or curves
    (Extend's target). None if nothing is ahead within ``max_len``."""
    dl = direction.length()
    if dl <= _EPS:
        return None
    ray = _Piece("line", origin, origin + direction * (max_len / dl))
    best = None
    for cut in _as_cutters(cutters):
        if not _boxes_meet(ray.box, cut.box, _ON):
            continue
        for q in cut.pieces:
            if not _boxes_meet(ray.box, q.box, _ON):
                continue
            for f, x in _cross(ray, q):
                d = f * max_len
                if d > 1e-6 and (best is None or d < best[0]):
                    best = (d, x)
    return best


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _build(segments):
    pieces = []
    for kind, pts in segments:
        if len(pts) < 2:
            continue
        piece = _piece_from(kind, list(pts))
        if piece.length > _EPS:
            pieces.append(piece)
    cum = [0.0]
    for p in pieces:
        cum.append(cum[-1] + p.length)
    return pieces, cum, cum[-1]


def _piece_at(cum, s):
    for i in range(len(cum) - 1):
        if cum[i] - 1e-9 <= s < cum[i + 1] - 1e-9:
            return i
    return len(cum) - 2


def _point_at(pieces, cum, s) -> Vec2:
    i = _piece_at(cum, s)
    p = pieces[i]
    return p.point_at((s - cum[i]) / p.length if p.length > _EPS else 0.0)


def _nearest(pieces, cum, target):
    """(arc-length, distance) of the point on the entity nearest ``target``,
    measured on the true lines and arcs rather than their flattening."""
    best_s, best_d = 0.0, float("inf")
    for pi, p in enumerate(pieces):
        if p.bez is not None:
            f = p.frac_of(target)
            q = p.point_at(f)
        elif p.center is None:
            f = p.frac_of(target)
            q = p.a.lerp(p.b, f)
        else:
            f = _arc_frac(p, p.center + _unit(target - p.center) * p.r)
            if f is None:                     # off the sweep: nearer end
                f = 0.0 if (target - p.a).length() < (target - p.b).length() else 1.0
            q = p.point_at(f)
        d = (q - target).length()
        if d < best_d:
            best_d, best_s = d, cum[pi] + f * p.length
    return best_s, best_d


def _unit(v: Vec2) -> Vec2:
    n = v.length()
    return v * (1.0 / n) if n > _EPS else Vec2(1.0, 0.0)


def _walk(pieces, cum, total, start, length):
    """Collect (kind, pts) sub-segments covering ``length`` forward from
    arc-length ``start``, wrapping past the seam if needed."""
    out: List[Segment] = []
    remaining = length
    s = start % total
    i = _piece_at(cum, s)
    guard = 0
    while remaining > 1e-7 and guard < 4 * len(pieces) + 4:
        guard += 1
        take = min(cum[i + 1] - s, remaining)
        if take > 1e-7:
            plen = pieces[i].length
            out.append(pieces[i].sub((s - cum[i]) / plen, (s - cum[i] + take) / plen))
            remaining -= take
        s += take
        if s >= total - 1e-7:
            s = 0.0
            i = 0
        else:
            i = _piece_at(cum, s)
    return out


def _snap_ends(chain: List[Segment], start: Optional[Vec2],
               end: Optional[Vec2]) -> None:
    """Make a kept chain begin / end exactly on the cut line's end it was cut
    at, so the two share one point (the cut is within ``TOUCH_TOL``)."""
    if start is not None:
        chain[0] = _moved_end(chain[0], 0, start)
    if end is not None:
        chain[-1] = _moved_end(chain[-1], -1, end)


def _moved_end(seg: Segment, which: int, p: Vec2) -> Segment:
    kind, pts = seg
    pts = list(pts)
    delta = p - pts[which]
    move = delta.length()
    if move <= 1e-12:
        return seg
    if kind == "bezier" and len(pts) >= 4:
        pts[which] = p                            # the handle moves with it
        k = 1 if which == 0 else -2
        pts[k] = pts[k] + delta
        return (kind, pts)
    if kind == "arc" and len(pts) >= 3:
        if move <= 0.25 * _piece_from(kind, pts).length:
            pts[which] = p                        # refit through its midpoint
            return (kind, pts)
        return ("line", [p, pts[-1]] if which == 0 else [pts[0], p])
    pts[which] = p
    return (kind, pts)


def _display_span(poly: List[Vec2], a: Vec2, b: Vec2, m: Vec2, closed: bool,
                  share: float) -> List[Vec2]:
    """The run of the drawn polyline ``poly`` from the point nearest ``a`` to
    the point nearest ``b`` -- on a closed one, the way round that passes
    ``m`` (or, failing a clear answer, the one whose share of the loop is
    nearest ``share``)."""
    cum = [0.0]
    for i in range(len(poly) - 1):
        cum.append(cum[-1] + (poly[i + 1] - poly[i]).length())
    L = cum[-1]
    if L <= _EPS:
        return []
    sa, sb, sm = (_poly_project(poly, cum, q) for q in (a, b, m))
    if not closed:
        lo, hi = min(sa, sb), max(sa, sb)
        return _poly_run(poly, cum, lo, hi - lo, False)
    ab = (sb - sa) % L
    ba = (sa - sb) % L
    via_m = (sm - sa) % L <= ab
    if abs(ab - ba) > 0.2 * L:                    # one way is clearly the cell
        want = share * L
        via_m = abs(ab - want) <= abs(ba - want)
    return (_poly_run(poly, cum, sa, ab, True) if via_m
            else _poly_run(poly, cum, sb, ba, True))


def _poly_project(poly, cum, target) -> float:
    best_s, best_d = 0.0, float("inf")
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        ab = b - a
        d2 = ab.length_sq()
        t = 0.0 if d2 <= _EPS * _EPS else max(0.0, min(1.0, (target - a).dot(ab) / d2))
        d = (a + ab * t - target).length_sq()
        if d < best_d:
            best_d, best_s = d, cum[i] + t * (cum[i + 1] - cum[i])
    return best_s


def _poly_point(poly, cum, s) -> Vec2:
    s = max(0.0, min(cum[-1], s))
    for i in range(len(poly) - 1):
        if s <= cum[i + 1] or i == len(poly) - 2:
            seg = cum[i + 1] - cum[i]
            t = (s - cum[i]) / seg if seg > _EPS else 0.0
            return poly[i].lerp(poly[i + 1], max(0.0, min(1.0, t)))
    return poly[-1]


def _poly_run(poly, cum, start, length, closed) -> List[Vec2]:
    L = cum[-1]
    end = start + length
    out = [_poly_point(poly, cum, start)]
    if closed and end > L:
        out += [poly[i] for i in range(len(poly) - 1) if start < cum[i] < L]
        out.append(poly[-1])                      # round the seam
        out += [poly[i] for i in range(1, len(poly)) if cum[i] < end - L]
        out.append(_poly_point(poly, cum, end - L))
    else:
        out += [poly[i] for i in range(len(poly)) if start < cum[i] < end]
        out.append(_poly_point(poly, cum, end))
    clean = [out[0]]
    for q in out[1:]:
        if (q - clean[-1]).length() > 1e-12:
            clean.append(q)
    return clean

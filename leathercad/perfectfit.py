"""Perfect fit: the piece sizes at which the stitch line takes the iron cleanly.

A fitted stitch line ("Fit to corners & ends", the default) is cut into spans
at its corners -- each straight run, and each rounded corner on its own --
and every span gets the whole number of holes whose spacing is nearest the
iron's pitch. Only a span that is a whole number of pitches long comes out
at exactly the pitch; on any other the stitcher nudges the spacing a little
so the holes still land on its ends. This module finds the sizes where
nothing needs nudging: every gap between neighbouring holes is exactly the
pitch, all the way round.

* A straight run of the stitch line is a whole number of pitches long.
* A rounded 90 degree corner has stitch radius ``pitch / (2 sin(45deg / n))``
  for ``n`` gaps round it; the outline radius is that plus the inset. An
  outline corner radius no bigger than the inset leaves the stitch line with
  a sharp corner, which takes a hole on the corner itself.
* A circle or an oval takes ``n`` equal chords round its stitch line. Both are
  drawn as a fine polygon, so their sizes are solved against the stitcher's
  own stitch line rather than the ideal curve: the two agree to a few
  hundredths of a millimetre, and it is the stitcher's holes that get cut.

So a rectangle with sharp stitch corners is perfect at ``2 * inset + k * pitch``
wide, a rounded one at ``2 * R + k * pitch`` with ``R`` one of the corner radii
above. ``fit_shape`` is what the resize grips and drawing tools call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional, Tuple


@dataclass
class Fit:
    """A perfect size. ``hx`` / ``hy`` are half the width and height (the two
    radii of an oval, the radius twice for a circle)."""
    hx: float
    hy: float
    holes: int
    corner_radius: Optional[float] = None   # rectangles: the outline radius
    corner_gaps: int = 0                    # gaps round each corner; 0 = sharp
    side_gaps: Tuple[int, int] = (0, 0)     # gaps along each straight (w, h)


# -- rectangles ---------------------------------------------------------------
def corner_radius(n: int, pitch: float, inset: float,
                  angle_deg: float = 90.0) -> float:
    """Outline radius of a corner turning ``angle_deg`` whose stitch line
    takes exactly ``n`` gaps of ``pitch`` between its tangent points."""
    return inset + pitch / (2.0 * math.sin(math.radians(angle_deg) / (2.0 * n)))


def _corner_count_ok(n: int, style: str) -> bool:
    """Whether a corner of ``n`` gaps stitches at exactly the pitch under the
    piece's Corners setting (see ``stitching._plan_corners``)."""
    if style == "midpoint":
        return n >= 2 and n % 2 == 0        # a hole on the apex: even halves
    if style == "straddle":
        # odd keeps the apex bare. Two gaps is too short to straddle, so the
        # stitcher fits that corner freely -- and it lands exactly as well.
        return n % 2 == 1 or n == 2
    return n >= 1


def snap_corner_radius(radius: float, pitch: float, inset: float,
                       style: str = "auto") -> Tuple[float, int]:
    """The perfect outline corner radius nearest ``radius``, as
    ``(radius, gaps round the corner)``. Any radius up to the inset leaves
    the stitch line a sharp corner and is perfect as it is (gaps 0); a radius
    a little over the inset rounds back down to the inset itself."""
    if radius <= inset + 1e-9:
        return radius, 0
    r = radius - inset
    n0 = max(1, int(round(math.pi * r / (2.0 * pitch))))   # r_n ~ 2 n p / pi
    best_d, best_r, best_n = radius - inset, inset, 0       # the sharp corner
    for n in range(max(1, n0 - 3), n0 + 4):
        if not _corner_count_ok(n, style):
            continue
        cand = corner_radius(n, pitch, inset)
        d = abs(cand - radius)
        if d < best_d - 1e-12:
            best_d, best_r, best_n = d, cand, n
    return best_r, best_n


def snap_run(length: float, base: float, pitch: float,
             k_min: int = 0) -> Tuple[float, int]:
    """``base + k * pitch`` nearest ``length`` (k >= ``k_min``), and k."""
    k = max(k_min, int(round((length - base) / pitch)))
    return base + k * pitch, k


def fit_rectangle(width: float, height: float, radius: float, pitch: float,
                  inset: float, corner_style: str = "auto") -> Fit:
    """The perfect rectangle nearest ``width`` x ``height``: the corner radius
    snapped first (see ``snap_corner_radius``), then each straight run of the
    stitch line to a whole number of pitches. Never smaller than one pitch of
    straight (sharp corners) or the corners alone (rounded ones)."""
    R, n = snap_corner_radius(max(0.0, radius), pitch, inset, corner_style)
    if n == 0:
        base, k_min = 2.0 * inset, 1        # stitch line: (w - 2i) x (h - 2i)
    else:
        base, k_min = 2.0 * R, 0            # straights: w - 2R, h - 2R
    w, kw = snap_run(width, base, pitch, k_min)
    h, kh = snap_run(height, base, pitch, k_min)
    return Fit(w / 2.0, h / 2.0, 2 * (kw + kh) + 4 * n, corner_radius=R,
               corner_gaps=n, side_gaps=(kw, kh))


# -- circles and ovals ----------------------------------------------------------
def _ring(path, inset: float, start_offset: float):
    """The stitch line the stitcher marches round a cornerless closed path:
    its inset ring, started where ``start_offset`` starts it."""
    from .offset import inset_path_with_corners
    from .stitching import Polyline, _rotate_closed_ring
    pts, _corners, _arcs = inset_path_with_corners(path, max(0.0, inset), [])
    if len(pts) < 4:
        return None                         # narrower than twice the inset
    if start_offset:
        pts = _rotate_closed_ring(pts, start_offset)
    poly = Polyline(pts)
    return poly if poly.length > 1e-9 else None


def _closing_gap(poly, n: int, pitch: float) -> Optional[float]:
    """March ``n - 1`` chords of exactly ``pitch`` round the ring from its
    start, as the iron would; the gap left back to the first hole. It is the
    pitch exactly when ``n`` holes at the pitch close the ring -- the size at
    which the stitcher's fitted pitch for a cornerless ring of ``n`` holes
    IS the pitch. None if the ring is too short for the march."""
    from .stitching import _march_n_final_s
    s_last = _march_n_final_s(poly, pitch, n - 1, 0.0, poly.length)
    if s_last is None:
        return None
    return (poly.point_at(s_last) - poly.points[0]).length()


def _solve_scale(make_path: Callable[[float], object], inset: float,
                 start_offset: float, n: int, pitch: float,
                 guess: float) -> Optional[float]:
    """The scale ``s`` (1 = the size asked for) at which ``n`` chords of
    exactly ``pitch`` close the stitch line of ``make_path(s)``. Secant
    steps: the closing gap grows all but linearly with the size."""
    def resid(s: float) -> Optional[float]:
        if s <= 1e-6:
            return None
        poly = _ring(make_path(s), inset, start_offset)
        if poly is None:
            return None
        g = _closing_gap(poly, n, pitch)
        return None if g is None else g - pitch

    # two starting sizes that still have a stitch line: the estimate first
    # (a size asked for may be too small to have one at all)
    seeds = []
    for s in (guess, 1.0, guess * 1.01, guess * 0.99, 1.01, 1.1):
        if any(abs(s - q) < 1e-6 for q, _g in seeds):
            continue
        g = resid(s)
        if g is not None:
            seeds.append((s, g))
            if len(seeds) == 2:
                break
    if len(seeds) < 2:
        return None
    (b, gb), (a, ga) = seeds
    for _ in range(16):
        if abs(gb) < 1e-9 or gb == ga:
            break
        c = b - gb * (b - a) / (gb - ga)
        if c <= 1e-6:
            c = b / 2.0
        a, ga = b, gb
        b, gb = c, resid(c)
        if gb is None:
            return None
    return b if abs(gb) < 1e-6 else None


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def fit_circle(radius: float, pitch: float, inset: float,
               start_offset: float = 0.0, even: bool = False) -> Optional[Fit]:
    """The perfect circle nearest ``radius``: ``n`` holes, every gap the
    pitch. ``even`` keeps the count even (mirror-symmetric stitching)."""
    r = radius - inset
    if r > pitch / 2.0 + 1e-9:
        nr = math.pi / math.asin(min(1.0, pitch / (2.0 * r)))
    else:
        nr = 3.0
    cands = {max(3, int(math.floor(nr))), max(3, int(math.ceil(nr)))}
    if even:
        cands = {max(4, _even(c)) for c in cands} | {max(4, _even(c) - 2)
                                                     for c in cands}

    def ideal(n: int) -> float:
        return inset + pitch / (2.0 * math.sin(math.pi / n))

    n = min(sorted(cands), key=lambda c: abs(ideal(c) - radius))
    rr = _circle_radius(n, float(pitch), float(inset), float(start_offset))
    return Fit(rr, rr, n)


@lru_cache(maxsize=1024)
def _circle_radius(n: int, pitch: float, inset: float,
                   start_offset: float) -> float:
    """The radius at which a circle's stitch line takes exactly ``n`` holes
    at ``pitch``. It depends on nothing else, so a grip dragged across the
    same sizes again (every move of a drag lands on one of a few) solves
    each once -- and comes back to the very same number, not one a hair
    off from a different starting guess."""
    from .shapes import Circle
    base = inset + pitch / (2.0 * math.sin(math.pi / n))     # the ideal one
    s = _solve_scale(lambda k: Circle(rx=base * k, ry=base * k).local_path(),
                     inset, start_offset, n, pitch, 1.0)
    # the true circle's radius if the polygon one won't solve: within a few
    # hundredths, and a grip that stopped snapping now and then would be worse
    return base * s if s is not None else base


def fit_ellipse(rx: float, ry: float, pitch: float, inset: float,
                axis: str = "both", start_offset: float = 0.0,
                even: bool = False) -> Optional[Fit]:
    """The perfect oval nearest ``rx`` x ``ry``. ``axis`` says what may give:
    'x' finds rx with ry kept, 'y' the other way round, 'both' scales the
    two together (a corner grip). ``n`` is the hole count the oval asked for
    holds nearest the pitch. None if it can't be solved (too small)."""
    from .shapes import Ellipse
    rx, ry = max(rx, 1e-3), max(ry, 1e-3)

    def make(s: float):
        if axis == "x":
            return Ellipse(rx=rx * s, ry=ry).local_path()
        if axis == "y":
            return Ellipse(rx=rx, ry=ry * s).local_path()
        return Ellipse(rx=rx * s, ry=ry * s).local_path()

    poly = _ring(make(1.0), inset, start_offset)
    if poly is None:
        return None
    n = max(3, int(round(poly.length / pitch)))
    if even:
        n = max(4, 2 * int(round(poly.length / (2.0 * pitch))))
    s = _solve_scale(make, inset, start_offset, n, pitch,
                     n * pitch / poly.length)
    if s is None:
        return None
    if axis == "x":
        return Fit(rx * s, ry, n)
    if axis == "y":
        return Fit(rx, ry * s, n)
    return Fit(rx * s, ry * s, n)


# -- shapes ---------------------------------------------------------------------
def can_fit(shape) -> bool:
    """Whether ``shape`` has perfect sizes at all: a stitched rectangle,
    circle or oval whose holes are still worked out from its outline (not
    attached ones, which only scale)."""
    from .shapes import Rectangle, Ellipse
    if not isinstance(shape, (Rectangle, Ellipse)):
        return False
    st = getattr(shape, "stitch", None)
    if st is None or not st.enabled or getattr(shape, "baked_holes", None):
        return False
    if getattr(st, "mode", "chord") != "chord" or not st.pitch_mm > 0:
        return False
    edges = getattr(shape, "stitch_edges", None)
    if (isinstance(shape, Ellipse) and edges
            and len(edges) == len(shape.local_path().segments)
            and not all(edges)):
        return False          # an oval cut open at arbitrary facets
    return True


def fit_shape(shape, hx: float, hy: float, axis: str = "both") -> Optional[Fit]:
    """The perfect size nearest the half-extents ``hx`` x ``hy`` asked of
    ``shape`` (width / 2 and height / 2; rx and ry), using the shape's own
    pitch, inset and corner style. ``axis`` is which way a grip is pulling
    ('x', 'y' or 'both'). None when the shape has no perfect sizes."""
    from .shapes import Rectangle, Circle
    if not can_fit(shape):
        return None
    st = shape.stitch
    pitch, inset = float(st.pitch_mm), max(0.0, float(st.inset or 0.0))
    if isinstance(shape, Rectangle):
        return fit_rectangle(2.0 * hx, 2.0 * hy, shape.corner_radius, pitch,
                             inset, getattr(st, "corner_style", "auto"))
    even = getattr(st, "symmetry", "none") in ("vertical", "horizontal")
    start = float(getattr(st, "start_offset", 0.0) or 0.0)
    if isinstance(shape, Circle):
        # follow the axis being pulled, as the grips do
        r = hx if abs(hx - shape.rx) >= abs(hy - shape.ry) else hy
        return fit_circle(r, pitch, inset, start, even)
    return fit_ellipse(hx, hy, pitch, inset, axis, start, even)

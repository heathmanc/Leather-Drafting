"""Path model: lines, arcs and Bezier curves flattened to a polyline.

A ``Path`` is an ordered list of segments plus an optional ``closed`` flag and
a set of *corners* (arc-length positions where a stitch hole must be forced --
think of the four corners of a card wallet, where you always want a hole).

Curves are flattened to a fine polyline for all downstream stitch computation.
Flattening tolerance defaults to 0.02 mm, which is far below any laser kerf, so
the chord-spacing engine treats the polyline as ground truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence

from .geometry import Vec2

DEFAULT_FLATNESS = 0.02  # mm


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------
class Segment:
    """Base class. ``flatten`` returns points *excluding* the start vertex so
    segments can be concatenated without duplicating shared endpoints."""

    def start(self) -> Vec2:  # pragma: no cover - interface
        raise NotImplementedError

    def end(self) -> Vec2:  # pragma: no cover - interface
        raise NotImplementedError

    def flatten(self, flatness: float = DEFAULT_FLATNESS) -> List[Vec2]:  # pragma: no cover
        raise NotImplementedError


@dataclass
class Line(Segment):
    p0: Vec2
    p1: Vec2

    def start(self) -> Vec2:
        return self.p0

    def end(self) -> Vec2:
        return self.p1

    def flatten(self, flatness: float = DEFAULT_FLATNESS) -> List[Vec2]:
        return [self.p1]


@dataclass
class Arc(Segment):
    """Circular arc defined by centre, radius and start/end angle (radians).

    ``ccw`` chooses sweep direction. Angles are standard math convention.
    """

    center: Vec2
    radius: float
    a0: float
    a1: float
    ccw: bool = True

    def _point(self, a: float) -> Vec2:
        return Vec2(self.center.x + self.radius * math.cos(a),
                    self.center.y + self.radius * math.sin(a))

    def _sweep(self) -> float:
        a0, a1 = self.a0, self.a1
        if self.ccw:
            while a1 < a0:
                a1 += 2 * math.pi
            return a1 - a0
        else:
            while a1 > a0:
                a1 -= 2 * math.pi
            return a1 - a0  # negative

    def start(self) -> Vec2:
        return self._point(self.a0)

    def end(self) -> Vec2:
        return self._point(self.a1)

    def flatten(self, flatness: float = DEFAULT_FLATNESS) -> List[Vec2]:
        sweep = self._sweep()
        # Max angular step so sagitta (bulge) stays under `flatness`.
        if self.radius <= 0:
            return [self.end()]
        ratio = max(-1.0, min(1.0, 1.0 - flatness / self.radius))
        max_step = 2.0 * math.acos(ratio) if ratio < 1.0 else math.pi
        max_step = max(max_step, 1e-3)
        n = max(1, int(math.ceil(abs(sweep) / max_step)))
        pts: List[Vec2] = []
        for i in range(1, n + 1):
            a = self.a0 + sweep * (i / n)
            pts.append(self._point(a))
        return pts


@dataclass
class CubicBezier(Segment):
    p0: Vec2
    p1: Vec2
    p2: Vec2
    p3: Vec2

    def start(self) -> Vec2:
        return self.p0

    def end(self) -> Vec2:
        return self.p3

    def _at(self, t: float) -> Vec2:
        u = 1.0 - t
        return (self.p0 * (u * u * u)
                + self.p1 * (3 * u * u * t)
                + self.p2 * (3 * u * t * t)
                + self.p3 * (t * t * t))

    def flatten(self, flatness: float = DEFAULT_FLATNESS) -> List[Vec2]:
        out: List[Vec2] = []
        self._recurse(self.p0, self.p1, self.p2, self.p3, flatness, out, 0)
        out.append(self.p3)
        return out

    def _recurse(self, p0, p1, p2, p3, flat, out, depth):
        # Flatness test: distance of control points from the p0-p3 chord.
        if depth > 24 or _bezier_flat_enough(p0, p1, p2, p3, flat):
            return  # chord p0->p3 is good enough; caller appends endpoint
        # de Casteljau split at t=0.5
        p01 = p0.lerp(p1, 0.5)
        p12 = p1.lerp(p2, 0.5)
        p23 = p2.lerp(p3, 0.5)
        p012 = p01.lerp(p12, 0.5)
        p123 = p12.lerp(p23, 0.5)
        mid = p012.lerp(p123, 0.5)
        self._recurse(p0, p01, p012, mid, flat, out, depth + 1)
        out.append(mid)
        self._recurse(mid, p123, p23, p3, flat, out, depth + 1)


@dataclass
class QuadraticBezier(Segment):
    p0: Vec2
    p1: Vec2
    p2: Vec2

    def start(self) -> Vec2:
        return self.p0

    def end(self) -> Vec2:
        return self.p2

    def to_cubic(self) -> CubicBezier:
        c1 = self.p0 + (self.p1 - self.p0) * (2.0 / 3.0)
        c2 = self.p2 + (self.p1 - self.p2) * (2.0 / 3.0)
        return CubicBezier(self.p0, c1, c2, self.p2)

    def flatten(self, flatness: float = DEFAULT_FLATNESS) -> List[Vec2]:
        return self.to_cubic().flatten(flatness)


def _bezier_flat_enough(p0, p1, p2, p3, flat) -> bool:
    # Perpendicular distance of p1 and p2 from the p0-p3 line.
    dx = p3.x - p0.x
    dy = p3.y - p0.y
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        d1 = (p1 - p0).length()
        d2 = (p2 - p0).length()
        return max(d1, d2) <= flat
    d1 = abs((p1.x - p0.x) * dy - (p1.y - p0.y) * dx)
    d2 = abs((p2.x - p0.x) * dy - (p2.y - p0.y) * dx)
    return (max(d1, d2) * max(d1, d2)) <= (flat * flat) * denom


# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
@dataclass
class Path:
    segments: List[Segment] = field(default_factory=list)
    closed: bool = False
    # Arc-length positions (mm) at which a stitch hole must be forced.
    corners: List[float] = field(default_factory=list)
    # The same corners as geometric points -- robust under insetting/transform.
    corner_points: List[Vec2] = field(default_factory=list)

    def flatten(self, flatness: float = DEFAULT_FLATNESS) -> List[Vec2]:
        if not self.segments:
            return []
        pts: List[Vec2] = [self.segments[0].start()]
        for seg in self.segments:
            pts.extend(seg.flatten(flatness))
        if self.closed:
            first, last = pts[0], pts[-1]
            if (first - last).length() > 1e-9:
                pts.append(first)
        return pts


class PathBuilder:
    """Turtle-style builder that records corner positions as it goes.

    Every ``line_to`` / ``move_to`` vertex may be tagged a corner; ``corner()``
    tags the most recent point. Corner arc-length positions are resolved when
    ``build`` flattens the path.
    """

    def __init__(self) -> None:
        self._segments: List[Segment] = []
        self._current: Vec2 | None = None
        self._start: Vec2 | None = None
        self._closed = False
        # Points (as Vec2) the user tagged as corners.
        self._corner_points: List[Vec2] = []

    def move_to(self, x: float, y: float) -> "PathBuilder":
        self._current = Vec2(x, y)
        self._start = self._current
        return self

    def line_to(self, x: float, y: float, corner: bool = False) -> "PathBuilder":
        assert self._current is not None, "call move_to first"
        p1 = Vec2(x, y)
        self._segments.append(Line(self._current, p1))
        self._current = p1
        if corner:
            self._corner_points.append(p1)
        return self

    def cubic_to(self, x1, y1, x2, y2, x, y) -> "PathBuilder":
        assert self._current is not None, "call move_to first"
        p = Vec2(x, y)
        self._segments.append(
            CubicBezier(self._current, Vec2(x1, y1), Vec2(x2, y2), p))
        self._current = p
        return self

    def quad_to(self, x1, y1, x, y) -> "PathBuilder":
        assert self._current is not None, "call move_to first"
        p = Vec2(x, y)
        self._segments.append(QuadraticBezier(self._current, Vec2(x1, y1), p))
        self._current = p
        return self

    def arc_to(self, center_x, center_y, a0, a1, ccw=True) -> "PathBuilder":
        c = Vec2(center_x, center_y)
        r = (self._current - c).length() if self._current else 0.0
        arc = Arc(c, r, a0, a1, ccw)
        self._segments.append(arc)
        self._current = arc.end()
        return self

    def corner(self) -> "PathBuilder":
        """Tag the current point as a corner (force a hole there)."""
        if self._current is not None:
            self._corner_points.append(self._current)
        return self

    def close(self, corner: bool = True) -> "PathBuilder":
        assert self._current is not None and self._start is not None
        if (self._current - self._start).length() > 1e-9:
            self._segments.append(Line(self._current, self._start))
        self._closed = True
        if corner and self._start is not None:
            self._corner_points.append(self._start)
        self._current = self._start
        return self

    def build(self, flatness: float = DEFAULT_FLATNESS) -> Path:
        path = Path(segments=list(self._segments), closed=self._closed)
        # Resolve corner arc-length positions against the flattened polyline,
        # and keep the geometric corner points (robust under inset/transform).
        if self._corner_points:
            pts = path.flatten(flatness)
            cum = _cumulative_lengths(pts)
            positions: List[float] = []
            for cp in self._corner_points:
                positions.append(_nearest_arclen(pts, cum, cp))
            path.corners = sorted(set(round(p, 6) for p in positions))
            path.corner_points = list(self._corner_points)
        return path


def _cumulative_lengths(pts: Sequence[Vec2]) -> List[float]:
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + (pts[i] - pts[i - 1]).length())
    return cum


def _nearest_arclen(pts: Sequence[Vec2], cum: Sequence[float], target: Vec2) -> float:
    best_len = 0.0
    best_d = float("inf")
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        ab = b - a
        denom = ab.length_sq()
        t = 0.0 if denom == 0 else max(0.0, min(1.0, (target - a).dot(ab) / denom))
        proj = a.lerp(b, t)
        d = (proj - target).length_sq()
        if d < best_d:
            best_d = d
            best_len = cum[i] + (b - a).length() * t
    return best_len

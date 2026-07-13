"""Minimal 2D geometry primitives.

Dependency-free on purpose: the whole point of this project is that it should
run anywhere with just a stock Python install. Everything is in millimetres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Vec2:
    """An immutable 2D point / vector in millimetres."""

    x: float
    y: float

    # -- vector algebra -------------------------------------------------
    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, s: float) -> "Vec2":
        return Vec2(self.x * s, self.y * s)

    __rmul__ = __mul__

    def __truediv__(self, s: float) -> "Vec2":
        return Vec2(self.x / s, self.y / s)

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        return self.x * other.y - self.y * other.x

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def length_sq(self) -> float:
        return self.x * self.x + self.y * self.y

    def normalized(self) -> "Vec2":
        n = self.length()
        if n == 0.0:
            return Vec2(0.0, 0.0)
        return Vec2(self.x / n, self.y / n)

    def perp(self) -> "Vec2":
        """90 degrees counter-clockwise."""
        return Vec2(-self.y, self.x)

    def rotate(self, radians: float) -> "Vec2":
        c, s = math.cos(radians), math.sin(radians)
        return Vec2(self.x * c - self.y * s, self.x * s + self.y * c)

    def angle(self) -> float:
        return math.atan2(self.y, self.x)

    def lerp(self, other: "Vec2", t: float) -> "Vec2":
        return Vec2(self.x + (other.x - self.x) * t,
                    self.y + (other.y - self.y) * t)


def distance(a: Vec2, b: Vec2) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def point_in_polygon(p: Vec2, poly) -> bool:
    """Ray-cast point-in-polygon test. ``poly`` is a ring of Vec2 (a repeated
    closing point is harmless). Points on an edge are implementation-defined."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        a, b = poly[i], poly[j]
        if (a.y > p.y) != (b.y > p.y):
            x = (b.x - a.x) * (p.y - a.y) / ((b.y - a.y) or 1e-30) + a.x
            if p.x < x:
                inside = not inside
        j = i
    return inside

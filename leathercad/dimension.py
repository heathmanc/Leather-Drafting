"""Linear dimension annotations.

A :class:`Dimension` measures the straight distance between two world points and
draws a dimension line (offset perpendicular to the span) with the length shown.
Dimensions are annotations: they are never cut, scored or exported to the laser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import Vec2

_dim_counter = [0]


def _next_id() -> str:
    _dim_counter[0] += 1
    return f"dim{_dim_counter[0]}"


@dataclass
class Dimension:
    p1: Vec2                      # world start (mm)
    p2: Vec2                      # world end (mm)
    offset: float = 8.0           # perpendicular offset of the dim line (mm)
    layer: str = "Dimension"
    dim_id: str = field(default_factory=_next_id)
    kind: str = "dimension"

    def length(self) -> float:
        return (self.p2 - self.p1).length()

    def angle_deg(self) -> float:
        d = self.p2 - self.p1
        return math.degrees(math.atan2(d.y, d.x))

    def label(self) -> str:
        return f"{self.length():.1f} mm"

    def normal(self) -> Vec2:
        """Unit perpendicular (left of p1->p2)."""
        d = self.p2 - self.p1
        n = d.length()
        if n < 1e-9:
            return Vec2(0.0, 1.0)
        d = d.normalized()
        return Vec2(-d.y, d.x)

    def line_points(self):
        """(a, b) endpoints of the offset dimension line in world coords."""
        off = self.normal() * self.offset
        return self.p1 + off, self.p2 + off

    def to_dict(self) -> dict:
        return {
            "p1": [self.p1.x, self.p1.y],
            "p2": [self.p2.x, self.p2.y],
            "offset": self.offset,
            "layer": self.layer,
            "dim_id": self.dim_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Dimension":
        return cls(
            p1=Vec2(*d.get("p1", [0, 0])),
            p2=Vec2(*d.get("p2", [0, 0])),
            offset=d.get("offset", 8.0),
            layer=d.get("layer", "Dimension"),
            dim_id=d.get("dim_id", _next_id()),
        )

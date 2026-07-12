"""Baked stitch holes -- an explicit, hand-editable set of holes.

When you "ungroup" a shape's stitching, its holes are frozen into a
:class:`HoleGroup`: a plain list of hole positions that is *never* recomputed
from a path. That is what lets you delete individual holes without the spacing
engine redistributing the remainder -- the holes are now data, not a formula.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .geometry import Vec2
from .stitching import Hole


_hg_counter = [0]


def _next_id() -> str:
    _hg_counter[0] += 1
    return f"holes{_hg_counter[0]}"


@dataclass
class HoleGroup:
    holes: List[Hole] = field(default_factory=list)   # world coordinates
    hole_style: str = "round"
    hole_diameter: float = 1.0
    slit_length: float = 1.6
    slit_angle: float = 30.0
    layer: str = "Stitch"
    name: str = ""
    group_id: str = field(default_factory=_next_id)
    kind: str = "holegroup"

    @property
    def count(self) -> int:
        return len(self.holes)

    def bounds(self):
        if not self.holes:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [h.point.x for h in self.holes]
        ys = [h.point.y for h in self.holes]
        return min(xs), min(ys), max(xs), max(ys)

    def translate(self, dx: float, dy: float) -> None:
        self.holes = [Hole(Vec2(h.point.x + dx, h.point.y + dy), h.tangent)
                      for h in self.holes]

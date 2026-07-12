"""A standalone stitch line -- the tool for cross-piece registration.

Two overlapping pieces (say a wallet's front and its lining) must share the
exact same holes along their common seam, or they will not line up when you
stitch them together -- and it must hold whichever side you laser from.

A :class:`StitchLine` is one shared seam drawn once in world coordinates. Its
holes are computed a single time, so every piece positioned against that seam
receives *identical* holes by construction. (For two pieces with the *same*
outline, deterministic perimeter stitching already guarantees this; the stitch
line covers the harder case of differently shaped pieces sharing one edge.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .geometry import Vec2
from .stitchsettings import StitchSettings
from .stitching import stitch_polyline, StitchResult


_sl_counter = [0]


def _next_id() -> str:
    _sl_counter[0] += 1
    return f"seam{_sl_counter[0]}"


@dataclass
class StitchLine:
    points: List[Vec2] = field(default_factory=list)
    closed: bool = False
    corner_points: List[Vec2] = field(default_factory=list)
    settings: StitchSettings = field(default_factory=StitchSettings)
    name: str = ""
    layer: str = "Stitch"
    line_id: str = field(default_factory=_next_id)
    kind: str = "stitchline"

    def __post_init__(self):
        # A drawn seam is the stitch line itself -- never inset it.
        self.settings.inset = 0.0

    def result(self) -> StitchResult:
        if len(self.points) < 2:
            return StitchResult(closed=self.closed)
        return stitch_polyline(list(self.points), list(self.corner_points),
                               self.closed, self.settings)

    def bounds(self):
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

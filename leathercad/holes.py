"""Individual, hand-placed stitch holes (the "ungrouped" state).

A :class:`LooseHole` is one hole that stands on its own -- selectable, movable
and deletable like any shape. Ungrouping a shape's stitching turns its computed
holes into loose holes; grouping bakes loose holes back into a shape (see
``Shape.baked_holes``). Loose holes are never redistributed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Vec2


_hole_counter = [0]


def _next_id() -> str:
    _hole_counter[0] += 1
    return f"hole{_hole_counter[0]}"


@dataclass
class LooseHole:
    point: Vec2                      # world coordinates (mm)
    tangent: Vec2 = field(default_factory=lambda: Vec2(1.0, 0.0))
    hole_style: str = "round"        # "round" or "slit"
    hole_diameter: float = 1.0
    slit_length: float = 1.6
    slit_angle: float = 30.0
    layer: str = "Stitch"
    hole_id: str = field(default_factory=_next_id)
    # Move-group membership: items sharing a group_id are selected and moved
    # together. None -> not grouped.
    group_id: str | None = None

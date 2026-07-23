"""Individual, hand-placed stitch holes (the "ungrouped" state).

A :class:`LooseHole` is one hole that stands on its own -- selectable, movable
and deletable like any shape. Ungrouping a shape's stitching turns its computed
holes into loose holes; grouping bakes loose holes back into a shape (see
``Shape.baked_holes``). Loose holes are never redistributed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import Vec2


def mirrored_slit_angle(slit_angle_deg: float, mirrored: bool) -> float:
    """The slit/diamond slant angle to draw for a (possibly mirrored) piece.

    A reflection reverses the *sense* of the slant: it maps a slit rotated
    ``+a`` off the seam to one rotated ``-a``. The tangent alone can't carry
    this (rotation and reflection don't commute), so a mirrored piece must
    negate its slit angle, or the slants come out rotated the wrong way and the
    front / back stitch patterns won't register. Round holes have no angle, so
    this is a no-op for them."""
    return -slit_angle_deg if mirrored else slit_angle_deg


def diamond_points(point: Vec2, tangent: Vec2, length: float,
                   angle_deg: float, width_ratio: float = 0.4):
    """The 4 corners of a diamond/lozenge hole centred at ``point``: a slim
    rhombus of ``length`` along the seam tangent rotated ``angle_deg``, and
    ``length * width_ratio`` across. Shared by the canvas render and the SVG /
    DXF exporters so the shape matches everywhere."""
    import math
    d = tangent.rotate(math.radians(angle_deg)).normalized()
    n = d.perp()
    hl = length / 2.0
    hw = length * width_ratio / 2.0
    return [point + d * hl, point + n * hw, point - d * hl, point - n * hw]


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

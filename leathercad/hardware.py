"""Hardware catalogue: the cut a piece of hardware needs, its footprint, and
the stitch keep-out around it.

Each entry says what to cut for the hardware (a post hole for a rivet or
snap, a slot for a buckle tongue, prong slits for a magnetic snap, a keyhole
for a Sam Browne stud), how big the visible part is (``footprint``, the cap
or flange diameter) and how far stitch holes should keep away from its
centre (``keepout``: the footprint radius plus a little clearance, so no
hole ends up under a cap). Sizes are the common nominal ones from the
usual suppliers; they're starting points -- a hole can be resized like any
shape and the keep-out set per part.

Pure Python, no Qt. ``build(key)`` returns the cut shapes (on the Cut layer,
grouped) plus a construction circle showing the footprint, all centred on
the origin; the first cut shape carries the ``hardware`` tag and ``keepout``
radius the stitcher looks at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .geometry import Vec2
from .shapes import Circle, Polygon, Rectangle, Shape, Transform, _next_id

#: stitch holes keep this much clear of a cap / flange edge (mm)
CLEARANCE = 1.0


@dataclass(frozen=True)
class Hardware:
    key: str
    category: str
    name: str
    # what to cut: ("hole", d) | ("slot", w, h, r) | ("slits", n, L, w, gap)
    # | ("keyhole", d, slot_w, slot_len) | ("none",)
    cut: tuple
    footprint: float            # cap / flange / body diameter (mm) -- or the
    footprint_h: float = 0.0    # longer side of a rectangular footprint
    note: str = ""

    @property
    def keepout(self) -> float:
        """Default keep-out radius: the footprint's half-diagonal + clearance."""
        w = self.footprint
        h = self.footprint_h or self.footprint
        return 0.5 * math.hypot(w, h) + CLEARANCE if h != w else 0.5 * w + CLEARANCE

    def dims(self) -> str:
        c = self.cut
        if c[0] == "hole":
            return f"hole Ø {c[1]:g} · cap Ø {self.footprint:g} mm"
        if c[0] == "slot":
            return f"slot {c[1]:g} × {c[2]:g} mm"
        if c[0] == "slits":
            return f"{c[1]} slits {c[2]:g} × {c[3]:g} · Ø {self.footprint:g} mm"
        if c[0] == "keyhole":
            return f"keyhole Ø {c[1]:g} + {c[2]:g} × {c[3]:g} mm"
        if self.footprint_h:
            return f"{self.footprint:g} × {self.footprint_h:g} mm footprint"
        return f"Ø {self.footprint:g} mm footprint"


CATALOGUE: List[Hardware] = [
    # -- rivets (double cap / rapid): post hole, cap diameter
    Hardware("rivet_s", "Rivets", "Double-cap rivet, small", ("hole", 3.0), 7.0),
    Hardware("rivet_m", "Rivets", "Double-cap rivet, medium", ("hole", 3.0), 9.0),
    Hardware("rivet_l", "Rivets", "Double-cap rivet, large", ("hole", 3.5), 12.0),
    Hardware("rivet_tubular", "Rivets", "Tubular rivet", ("hole", 3.0), 6.0),
    # -- snaps: post hole, cap diameter (same hole for socket / stud side)
    Hardware("snap_16", "Snaps", "Snap, Line 16 (Segma / baby dot)",
             ("hole", 3.0), 10.0),
    Hardware("snap_20", "Snaps", "Snap, Line 20", ("hole", 3.5), 12.5),
    Hardware("snap_24", "Snaps", "Snap, Line 24", ("hole", 4.0), 15.0),
    # -- screws / studs
    Hardware("chicago_s", "Screws & studs", "Chicago screw, small (1/4\")",
             ("hole", 5.0), 8.0),
    Hardware("chicago_m", "Screws & studs", "Chicago screw, medium",
             ("hole", 6.0), 10.0),
    Hardware("sam_stud", "Screws & studs", "Sam Browne stud (screw-back)",
             ("hole", 3.0), 10.0),
    Hardware("sam_keyhole", "Screws & studs", "Sam Browne stud buttonhole",
             ("keyhole", 8.0, 4.0, 10.0), 10.0,
             note="the keyhole the stud head passes through"),
    Hardware("button_stud", "Screws & studs", "Button stud, 8 mm head",
             ("hole", 3.0), 8.0),
    # -- magnetic snaps: two prong slits either side of the centre
    Hardware("mag_14", "Magnetic snaps", "Magnetic snap, 14 mm",
             ("slits", 2, 4.0, 1.2, 10.0), 14.0),
    Hardware("mag_18", "Magnetic snaps", "Magnetic snap, 18 mm",
             ("slits", 2, 4.5, 1.4, 13.0), 18.0),
    # -- eyelets / grommets: hole, flange
    Hardware("eyelet_4", "Eyelets & grommets", "Eyelet, 4 mm", ("hole", 4.0), 8.0),
    Hardware("eyelet_0", "Eyelets & grommets", "Eyelet #0 (6 mm)",
             ("hole", 6.0), 11.0),
    Hardware("grommet_2", "Eyelets & grommets", "Grommet #2 (9.5 mm)",
             ("hole", 9.5), 17.0),
    # -- buckles, belts, locks: slots
    Hardware("tongue_20", "Buckles & locks", "Buckle tongue slot, 4 × 20",
             ("slot", 4.0, 20.0, 2.0), 4.0, 20.0,
             note="along the strap; the tongue hinges through it"),
    Hardware("tongue_30", "Buckles & locks", "Buckle tongue slot, 4 × 30",
             ("slot", 4.0, 30.0, 2.0), 4.0, 30.0),
    Hardware("belt_hole", "Buckles & locks", "Belt hole, Ø 4.5",
             ("hole", 4.5), 4.5),
    Hardware("belt_oval", "Buckles & locks", "Belt hole, oval 4 × 8",
             ("slot", 4.0, 8.0, 2.0), 4.0, 8.0),
    Hardware("turnlock_m", "Buckles & locks", "Turn lock, medium (tongue slot)",
             ("slot", 5.0, 18.0, 1.0), 20.0, 30.0,
             note="the slot the lock's tongue passes through"),
    # -- footprints only (nothing to cut): rings, for laying out tabs
    Hardware("dring_20", "Rings (footprint)", "D-ring, 20 mm", ("none",), 20.0, 6.0),
    Hardware("dring_25", "Rings (footprint)", "D-ring, 25 mm", ("none",), 25.0, 6.0),
    Hardware("oring_25", "Rings (footprint)", "O-ring, 25 mm", ("none",), 25.0),
]

CATEGORY_ORDER = ["Rivets", "Snaps", "Screws & studs", "Magnetic snaps",
                  "Eyelets & grommets", "Buckles & locks", "Rings (footprint)"]


def get(key: str) -> Optional[Hardware]:
    return next((h for h in CATALOGUE if h.key == key), None)


def categories():
    """Ordered ``[(category, [(display_name, key), ...])]``."""
    out = []
    for cat in CATEGORY_ORDER:
        rows = [(f"{h.name}  ·  {h.dims()}", h.key)
                for h in CATALOGUE if h.category == cat]
        if rows:
            out.append((cat, rows))
    return out


def _keyhole(d: float, slot_w: float, slot_len: float) -> List[Vec2]:
    """A keyhole: a round hole of diameter ``d`` with a slot ``slot_w`` wide
    running ``slot_len`` down from its centre (the stud head drops in the
    round, the neck slides down the slot)."""
    r, hw = d / 2.0, slot_w / 2.0
    a0 = math.asin(min(1.0, hw / r))              # where the slot meets the circle
    pts = []
    n = 24
    for i in range(n + 1):                        # round part, from the slot's
        a = -math.pi / 2 + a0 + (2 * math.pi - 2 * a0) * i / n   # right lip round
        pts.append(Vec2(r * math.cos(a), r * math.sin(a)))
    pts.append(Vec2(-hw, -slot_len))
    pts.append(Vec2(hw, -slot_len))
    return pts


def build(key: str, label: bool = True):
    """``(shapes, texts)`` for a catalogue entry, centred on the origin: the
    cut(s) on the Cut layer, tagged as hardware and carrying the keep-out,
    plus a dashed construction circle for the footprint. All one group."""
    h = get(key)
    if h is None:
        return [], []
    gid = _next_id("group")
    shapes: List[Shape] = []
    c = h.cut
    if c[0] == "hole":
        shapes.append(Circle(name=h.name, rx=c[1] / 2, ry=c[1] / 2, layer="Cut"))
    elif c[0] == "slot":
        shapes.append(Rectangle(name=h.name, width=c[1], height=c[2],
                                corner_radius=min(c[3], c[1] / 2), layer="Cut"))
    elif c[0] == "slits":
        n, length, width, gap = c[1:]
        for i in range(n):
            x = (i - (n - 1) / 2.0) * gap
            shapes.append(Rectangle(name=h.name, width=width, height=length,
                                    corner_radius=width / 2, layer="Cut",
                                    transform=Transform(x=x, y=0.0)))
    elif c[0] == "keyhole":
        shapes.append(Polygon(name=h.name, points=_keyhole(c[1], c[2], c[3]),
                              close_path=True, sharp_corners=False, layer="Cut"))
    for sh in shapes:
        sh.stitch = None
        sh.group_id = gid
    # the footprint: a guide, never cut, so you can see what the cap covers
    if h.footprint_h and abs(h.footprint_h - h.footprint) > 1e-9:
        fp = Rectangle(name=f"{h.name} footprint", width=h.footprint,
                       height=h.footprint_h, corner_radius=min(
                           h.footprint, h.footprint_h) / 2 * 0.3, layer="Cut")
    else:
        fp = Circle(name=f"{h.name} footprint", rx=h.footprint / 2,
                    ry=h.footprint / 2, layer="Cut")
    fp.construction = True
    fp.group_id = gid
    fp.stitch = None
    tagged = shapes[0] if shapes else fp
    tagged.hardware = h.key
    tagged.keepout = h.keepout
    return shapes + [fp], []


# ---------------------------------------------------------------------------
# keep-outs for the stitcher
# ---------------------------------------------------------------------------
def keepouts_in(doc, exclude=()) -> List[Tuple[Vec2, float]]:
    """``(world centre, radius)`` of every stitch keep-out in ``doc``: each
    shape with a ``keepout`` radius (hardware, or anything given one)."""
    out = []
    for sh in doc.shapes:
        r = float(getattr(sh, "keepout", 0.0) or 0.0)
        if r <= 0.0 or sh in exclude:
            continue
        out.append((sh.transform.apply(Vec2(0.0, 0.0)), r))
    return out


def keepouts_for_shape(shape, doc) -> List[Tuple[Vec2, float]]:
    """The keep-outs of ``doc`` that can touch ``shape``'s stitching, in the
    shape's LOCAL frame (what ``stitch_shape_path`` takes)."""
    if doc is None or getattr(shape, "stitch", None) is None:
        return []
    b = shape.bounds()
    t = shape.transform
    out = []
    for c, r in keepouts_in(doc, exclude=(shape,)):
        if (b[0] - r <= c.x <= b[2] + r) and (b[1] - r <= c.y <= b[3] + r):
            out.append((t.inverse_apply(c), r))
    return out


def keepouts_near(points: Sequence[Vec2], doc) -> List[Tuple[Vec2, float]]:
    """The keep-outs of ``doc`` near a world polyline (a seam)."""
    if doc is None or not points:
        return []
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    out = []
    for c, r in keepouts_in(doc):
        if (min(xs) - r <= c.x <= max(xs) + r) and (min(ys) - r <= c.y <= max(ys) + r):
            out.append((c, r))
    return out

"""Parametric part generators: card-pocket stacks and zipper openings.

These produce correctly-sized, named pieces from a few real-world numbers
(card size, zip gauge) -- the sizing arithmetic is the tedious, error-prone
part of drafting wallet interiors, so the program does it.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .geometry import Vec2
from .shapes import Rectangle, Transform, _next_id
from .stitchline import StitchLine
from .stitchsettings import StitchSettings

# CR80 (every bank card): 85.6 x 54.0 mm
CARD_W = 85.6
CARD_H = 54.0

# zipper "window" (the visible slot) width by zip gauge -- the number is the
# chain size; the window shows the teeth plus a whisker of tape each side.
ZIP_WINDOW = {"#3": 6.0, "#5": 8.0, "#8": 10.0}


def card_pocket_stack(card_w: float = CARD_W, card_h: float = CARD_H,
                      count: int = 4, reveal: float = 12.0,
                      depth: float = 38.0, ease: float = 2.0,
                      allowance: float = 7.0,
                      gap: float = 15.0) -> List[Rectangle]:
    """Pieces for a stepped card-pocket stack (a wallet interior).

    Every pocket is wide enough that a card slides between the side seams:
    width = card_w + 2*ease + 2*allowance (allowance = seam/stitch margin per
    side). Pocket i is ``depth + i*reveal`` tall, so each pocket's top edge
    steps ``reveal`` above the one in front of it when their bottoms align.
    The backing panel is tall enough that a card in the LAST pocket still
    hides its bottom and shows its top. Pieces are laid out in a row,
    bottom-aligned, ``gap`` mm apart, ready to arrange or nest.
    """
    count = max(1, int(count))
    w = card_w + 2.0 * ease + 2.0 * allowance
    pieces: List[Rectangle] = []
    x = 0.0
    for i in range(count):
        h = depth + i * reveal
        r = Rectangle(name=f"Pocket {i + 1}" + (" (front)" if i == 0 else ""),
                      width=w, height=h,
                      transform=Transform(x=x + w / 2.0, y=h / 2.0),
                      layer="Cut")
        r.shape_id = _next_id("shape")
        pieces.append(r)
        x += w + gap
    # backing: the last pocket's height plus the card's exposed head + margin
    back_h = depth + (count - 1) * reveal + (card_h - depth) + allowance
    back = Rectangle(name="Pocket backing", width=w, height=back_h,
                     transform=Transform(x=x + w / 2.0, y=back_h / 2.0),
                     layer="Cut")
    back.shape_id = _next_id("shape")
    pieces.append(back)
    return pieces


def zipper_opening(size: str = "#5", length: float = 150.0,
                   stitch_offset: float = 3.0,
                   pitch_mm: float = 3.85) -> Tuple[Rectangle, StitchLine]:
    """A zipper window (stadium slot, correctly sized for the zip gauge) plus
    the closed stitch line running ``stitch_offset`` outside it. Centred on
    the origin; the caller positions both together.
    """
    win = ZIP_WINDOW.get(size, ZIP_WINDOW["#5"])
    slot = Rectangle(name=f"Zip window {size}", width=length, height=win,
                     corner_radius=win / 2.0, transform=Transform(x=0, y=0),
                     layer="Cut")
    slot.shape_id = _next_id("shape")

    # stitch ring: a bigger stadium, offset_stitch out from the window edge --
    # built directly (straights + arc ends) so the holes fit a clean ring
    L = length / 2.0 - win / 2.0            # straight half-length
    r = win / 2.0 + stitch_offset           # ring end radius
    pts: List[Vec2] = []
    steps = max(8, int(math.ceil(math.pi * r / 1.5)))
    for i in range(steps + 1):              # right end cap (top -> bottom)
        a = math.pi / 2 - math.pi * i / steps
        pts.append(Vec2(L + r * math.cos(a), r * math.sin(a)))
    for i in range(steps + 1):              # left end cap (bottom -> top)
        a = -math.pi / 2 - math.pi * i / steps
        pts.append(Vec2(-L + r * math.cos(a), r * math.sin(a)))
    pts.append(Vec2(pts[0].x, pts[0].y))    # close the ring
    ring = StitchLine(points=pts, closed=True,
                      settings=StitchSettings(pitch_mm=pitch_mm,
                                              fit="closed"))
    ring.name = f"Zip stitch {size}"
    return slot, ring

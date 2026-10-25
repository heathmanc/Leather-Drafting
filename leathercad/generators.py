"""Parametric part generators: card-pocket stacks, zipper openings,
straps and belts, gussets, a bifold wallet, key tabs and hemmed pockets.

These produce correctly-sized, named pieces from a few real-world numbers
(card size, zip gauge) -- the sizing arithmetic is the tedious, error-prone
part of drafting wallet interiors, so the program does it.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .geometry import Vec2
from .shapes import (Circle, EditablePath, Edge, PathShape, Rectangle,
                     Transform, _next_id)
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


# ---------------------------------------------------------------------------
# straps, gussets, wallets, tabs, hemmed pockets
# ---------------------------------------------------------------------------
def _fold_line(name: str, x0: float, y0: float, x1: float, y1: float,
               angle: float = 90.0, direction: str = "front") -> PathShape:
    """A score line marked as a fold (the 3D preview folds about it)."""
    ln = PathShape(name=name, points=[Vec2(x0, y0), Vec2(x1, y1)],
                   close_path=False, transform=Transform(), layer="Score")
    ln.fold_dir = direction
    ln.fold_angle = angle
    ln.shape_id = _next_id("shape")
    return ln


def _strap_outline(length: float, width: float, tip: str,
                   buckle_end: str = "square") -> EditablePath:
    """A strap along +x from x = 0 (buckle end) to x = length (tip end).
    ``tip``: 'round' (semicircle), 'point' (English point: chamfers meeting a
    small arc), 'square'. The buckle end is 'square' or 'round'."""
    hw = width / 2.0
    # edges[i] leaves nodes[i]; an arc is set on the edge that LEADS to the
    # arc's end node
    nodes: List[Vec2] = []
    edges: List[Edge] = []

    def start(p):
        nodes.append(p)
        edges.append(Edge("line"))

    def line_to(p):
        nodes.append(p)
        edges.append(Edge("line"))

    def arc_to(p, mid):
        edges[-1] = Edge("arc", mid=mid)
        nodes.append(p)
        edges.append(Edge("line"))

    start(Vec2(hw if buckle_end == "round" else 0.0, -hw))
    if tip == "round":
        line_to(Vec2(length - hw, -hw))
        arc_to(Vec2(length - hw, hw), Vec2(length, 0.0))
    elif tip == "point":
        # chamfers run in from the sides and meet in a small arc at the point
        run = hw * 1.6
        r = width * 0.18
        line_to(Vec2(length - run, -hw))
        line_to(Vec2(length - r, -r * 0.55))
        arc_to(Vec2(length - r, r * 0.55), Vec2(length, 0.0))
        line_to(Vec2(length - run, hw))
    else:
        line_to(Vec2(length, -hw))
        line_to(Vec2(length, hw))
    if buckle_end == "round":
        line_to(Vec2(hw, hw))
        edges[-1] = Edge("arc", mid=Vec2(0.0, 0.0))    # closes back to start
    else:
        line_to(Vec2(0.0, hw))
    return EditablePath(nodes=nodes, edges=edges, closed=True, layer="Cut")


def strap(length: float = 1050.0, width: float = 38.0, tip: str = "round",
          buckle_end: str = "square", slot_len: float = 0.0,
          slot_w: float = 5.0, slot_from_end: float = 50.0,
          fold_from_end: float = 0.0, holes: int = 5,
          hole_spacing: float = 25.0, hole_d: float = 4.5,
          hole_from_tip: float = 90.0, oval_holes: bool = False,
          stitched: bool = False, pitch_mm: float = 3.85) -> List:
    """A strap / belt: outline with the chosen tip, an optional buckle-tongue
    slot and turn-back fold line at the buckle end, and a ladder of sizing
    holes from the tip. Centred on the origin; everything grouped."""
    pieces: List = []
    body = _strap_outline(length, width, tip, buckle_end)
    body.name = "Strap"
    body.shape_id = _next_id("shape")
    body.stitch = (StitchSettings(pitch_mm=pitch_mm, inset=3.5)
                   if stitched else StitchSettings(enabled=False))
    pieces.append(body)
    if slot_len > 0:
        s = Rectangle(name="Buckle slot", width=slot_len, height=slot_w,
                      corner_radius=slot_w / 2.0,
                      transform=Transform(x=slot_from_end, y=0.0), layer="Cut")
        s.shape_id = _next_id("shape")
        pieces.append(s)
    if fold_from_end > 0:
        pieces.append(_fold_line("Buckle fold", fold_from_end, -width / 2.0,
                                 fold_from_end, width / 2.0, 180.0))
    for i in range(max(0, int(holes))):
        x = length - hole_from_tip - i * hole_spacing
        if oval_holes:
            h = Rectangle(name=f"Hole {i + 1}", width=hole_d * 1.8, height=hole_d,
                          corner_radius=hole_d / 2.0,
                          transform=Transform(x=x, y=0.0), layer="Cut")
        else:
            h = Circle(name=f"Hole {i + 1}", rx=hole_d / 2.0, ry=hole_d / 2.0,
                       transform=Transform(x=x, y=0.0), layer="Cut")
        h.shape_id = _next_id("shape")
        pieces.append(h)
    # centre the whole thing on the origin
    for p in pieces:
        p.transform.x -= length / 2.0
    return pieces


def box_gusset(bag_w: float = 200.0, bag_h: float = 150.0, depth: float = 60.0,
               seam: float = 3.5, stitched: bool = True,
               pitch_mm: float = 3.85) -> List:
    """A box (square-cornered) gusset: one strip up one side, across the
    bottom and up the other side of a ``bag_w`` x ``bag_h`` bag, ``depth``
    wide, with fold lines where it turns the bag's bottom corners."""
    length = 2.0 * bag_h + bag_w
    strip = Rectangle(name="Box gusset", width=length, height=depth,
                      transform=Transform(), layer="Cut",
                      stitch=(StitchSettings(pitch_mm=pitch_mm, inset=seam)
                              if stitched else StitchSettings(enabled=False)))
    strip.shape_id = _next_id("shape")
    out = [strip]
    for x in (-length / 2.0 + bag_h, length / 2.0 - bag_h):
        out.append(_fold_line("Gusset corner", x, -depth / 2.0, x, depth / 2.0,
                              90.0))
    return out


def curved_gusset(bag_w: float = 200.0, bag_h: float = 150.0,
                  corner_r: float = 40.0, depth: float = 60.0,
                  seam: float = 3.5, stitched: bool = True,
                  pitch_mm: float = 3.85) -> List:
    """A gusset strip for a bag with rounded bottom corners of ``corner_r``:
    length = the sides + bottom + the two quarter-circle corners, with score
    marks where each curve begins and ends (ease the strip round between
    them). Its stitch holes then match a body stitched all the way round
    only if both use the same pitch and the body's corners are fitted --
    use Check seam mates."""
    r = max(0.0, min(corner_r, bag_w / 2.0, bag_h))
    arc = math.pi * r / 2.0
    length = 2.0 * (bag_h - r) + (bag_w - 2.0 * r) + 2.0 * arc
    strip = Rectangle(name="Curved gusset", width=length, height=depth,
                      transform=Transform(), layer="Cut",
                      stitch=(StitchSettings(pitch_mm=pitch_mm, inset=seam)
                              if stitched else StitchSettings(enabled=False)))
    strip.shape_id = _next_id("shape")
    out = [strip]
    x0 = -length / 2.0
    marks = [x0 + (bag_h - r), x0 + (bag_h - r) + arc,
             x0 + (bag_h - r) + arc + (bag_w - 2.0 * r),
             x0 + length - (bag_h - r)]
    for i, x in enumerate(marks):
        m = PathShape(name=f"Curve {'start' if i % 2 == 0 else 'end'}",
                      points=[Vec2(x, -depth / 2.0), Vec2(x, -depth / 2.0 + 4.0)],
                      close_path=False, transform=Transform(), layer="Score")
        m.shape_id = _next_id("shape")
        out.append(m)
    return out


def bifold_wallet(card_w: float = CARD_W, card_h: float = CARD_H,
                  pockets_per_side: int = 3, reveal: float = 10.0,
                  margin: float = 6.0, corner_r: float = 6.0,
                  bill_h: float = 90.0, pitch_mm: float = 3.85,
                  seam: float = 3.5) -> List:
    """The pieces of a classic bifold: an outer shell (two panels wide, a
    centre fold line), a bill-pocket liner the same size, and a stepped
    card-pocket stack for each side. Laid out in a row, grouped per piece."""
    panel_w = card_w + 2.0 * margin
    shell_w = 2.0 * panel_w + 4.0            # a little ease across the spine
    shell_h = max(bill_h, card_h + (pockets_per_side - 1) * reveal + 2 * margin)
    out: List = []
    shell = Rectangle(name="Outer shell", width=shell_w, height=shell_h,
                      corner_radius=corner_r, transform=Transform(),
                      layer="Cut",
                      stitch=StitchSettings(pitch_mm=pitch_mm, inset=seam))
    shell.shape_id = _next_id("shape")
    out.append(shell)
    out.append(_fold_line("Spine fold", 0.0, -shell_h / 2.0, 0.0, shell_h / 2.0,
                          180.0))
    liner = Rectangle(name="Bill pocket liner", width=shell_w,
                      height=shell_h - 2.0, corner_radius=corner_r,
                      transform=Transform(x=0.0, y=-(shell_h + 20.0)),
                      layer="Cut", stitch=StitchSettings(enabled=False))
    liner.shape_id = _next_id("shape")
    out.append(liner)
    depth = max(25.0, card_h - 2.0 * reveal)
    x = shell_w / 2.0 + 30.0
    for side in ("Left", "Right"):
        stack = card_pocket_stack(card_w, card_h, count=pockets_per_side,
                                  reveal=reveal, depth=depth, ease=1.0,
                                  allowance=margin - 1.0, gap=10.0)
        for p in stack:
            p.name = f"{side} {p.name.lower()}"
            p.transform.x += x
            p.transform.y -= shell_h / 2.0
            out.append(p)
        x += sum(p.width for p in stack) + 10.0 * len(stack) + 20.0
    return out


def key_tab(width: float = 20.0, length: float = 80.0, ring_slot: float = 0.0,
            rivet_hole: float = 3.0, rivet_from_end: float = 10.0,
            tip: str = "round") -> List:
    """A fold-over key tab: a strap that folds in half round a split ring,
    with a rivet (or Chicago screw) hole through both ends and a fold line
    at the middle. ``ring_slot`` > 0 cuts a slot at the fold for a wider
    ring or a D-ring tab."""
    out: List = []
    body = _strap_outline(length, width, tip, "round" if tip == "round"
                          else "square")
    body.name = "Key tab"
    body.shape_id = _next_id("shape")
    body.stitch = StitchSettings(enabled=False)
    out.append(body)
    out.append(_fold_line("Ring fold", length / 2.0, -width / 2.0,
                          length / 2.0, width / 2.0, 180.0))
    for x in (rivet_from_end, length - rivet_from_end):
        h = Circle(name="Rivet hole", rx=rivet_hole / 2.0, ry=rivet_hole / 2.0,
                   transform=Transform(x=x, y=0.0), layer="Cut")
        h.shape_id = _next_id("shape")
        out.append(h)
    if ring_slot > 0:
        s = Rectangle(name="Ring slot", width=3.0, height=ring_slot,
                      corner_radius=1.5, transform=Transform(x=length / 2.0),
                      layer="Cut")
        s.shape_id = _next_id("shape")
        out.append(s)
    for p in out:
        p.transform.x -= length / 2.0
    return out


def hemmed_pocket(width: float = 100.0, height: float = 60.0, hem: float = 12.0,
                  corner_r: float = 5.0, seam: float = 3.5,
                  pitch_mm: float = 3.85, thickness: float = 1.5) -> List:
    """A pocket whose top edge is folded over (a hem / edge turn): the piece
    is cut ``hem`` taller plus the fold's bend allowance, with a fold line
    where the hem turns and stitching on the three sewn edges only -- the
    folded mouth stays open."""
    from .folds import fold_allowance
    extra = fold_allowance(180.0, thickness)
    total_h = height + hem + extra
    body = Rectangle(name="Hemmed pocket", width=width, height=total_h,
                     corner_radius=corner_r, transform=Transform(),
                     layer="Cut",
                     stitch=StitchSettings(pitch_mm=pitch_mm, inset=seam))
    body.shape_id = _next_id("shape")
    n = len(body.local_path().segments)
    # the top straight (and its two corner arcs) is the folded mouth: unsewn
    flags = []
    for seg in body.local_path().segments:
        top = min(seg.start().y, seg.end().y) >= total_h / 2.0 - corner_r - 1e-6
        flags.append(not top)
    body.stitch_edges = flags if len(flags) == n else None
    y_fold = total_h / 2.0 - hem - extra / 2.0
    fold = _fold_line("Hem fold", -width / 2.0, y_fold, width / 2.0, y_fold,
                      180.0, "back")
    return [body, fold]

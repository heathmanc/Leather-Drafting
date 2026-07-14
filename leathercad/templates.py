"""Starter templates: small, complete documents to open and adapt.

Each template is built in code (deterministic, always in sync with the shape
model) and doubles as a worked example of good pattern structure. They match
the tutorials in the user guide.
"""

from __future__ import annotations

from .document import Document
from .geometry import Vec2
from .shapes import (Rectangle, Circle, Polygon, PathShape, EditablePath,
                     Transform)
from .stitchline import StitchLine
from .stitchsettings import StitchSettings


def card_holder() -> Document:
    """Tutorial 1: a 105x70 back panel with a 45 mm pocket, flush bottoms."""
    doc = Document("Card holder")
    doc.add_shape(Rectangle(
        name="Back panel", width=105, height=70, corner_radius=6,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=3.85, inset=3.5), layer="Cut"))
    # pocket bottom sits flush with the back panel's bottom (y = -35)
    doc.add_shape(Rectangle(
        name="Pocket", width=105, height=45, corner_radius=6,
        transform=Transform(x=0, y=-12.5),
        stitch=StitchSettings(enabled=False), layer="Cut"))
    return doc


def belt() -> Document:
    """A 1050x38 strap with rounded ends, buckle slot, fold line and 5 holes."""
    doc = Document("Belt")
    doc.add_shape(Rectangle(
        name="Strap", width=1050, height=38, corner_radius=19,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False), layer="Cut"))
    # buckle-tongue slot near the buckle (left) end
    doc.add_shape(Rectangle(
        name="Buckle slot", width=40, height=7, corner_radius=3.5,
        transform=Transform(x=-450, y=0), layer="Cut"))
    # fold line for the buckle turn-back
    doc.add_shape(PathShape(
        name="Fold", points=[Vec2(0, -19), Vec2(0, 19)], close_path=False,
        transform=Transform(x=-470, y=0), layer="Score"))
    # five sizing holes, 25 mm apart, from 100 mm inside the tip (right) end
    for i in range(5):
        doc.add_shape(Circle(
            name=f"Hole {i + 1}", rx=2.0, ry=2.0,
            transform=Transform(x=425 - i * 25, y=0), layer="Cut"))
    return doc


def key_fob() -> Document:
    """Tutorial 2: a stitched bezier teardrop with a ring hole."""
    doc = Document("Key fob")
    anchors = [Vec2(0, 26), Vec2(15, 2), Vec2(0, -20), Vec2(-15, 2)]
    outs = [Vec2(9, -7), Vec2(-4, -12), Vec2(-9, 7), Vec2(4, 12)]
    fob = EditablePath.from_bezier(anchors, outs, closed=True)
    fob.name = "Fob"
    fob.transform = Transform(x=0, y=0)
    fob.layer = "Cut"
    fob.stitch = StitchSettings(pitch_mm=3.85, inset=3.0)
    doc.add_shape(fob)
    doc.add_shape(Circle(name="Ring hole", rx=2.5, ry=2.5,
                         transform=Transform(x=0, y=16), layer="Cut"))
    return doc


def vertical_wallet() -> Document:
    """A minimalist SINGLE-PIECE vertical wallet in the style of the Oldis
    One: one continuous cross-shaped piece of leather, ~219 x 290 mm flat,
    that folds down to a ~70 x 100 mm (2.75 x 3.95 in) vertical wallet.

    The flat pattern is a plus / cross:

      * a central spine (~70 mm wide -- the finished width) running top to
        bottom, ending in a pointed envelope FLAP at the top and a small
        tapered TAB at the bottom;
      * two wide horizontal WINGS at the crossing. Each wing folds forward
        along the spine edge and wraps around to form the front pocket; the
        right wing's top edge is cut down on a diagonal for right-handed
        thumb access (mirror the piece, Ctrl+M, for a left-hander).

    The wings are stitched around their top / end / bottom edges (one seam
    per wing); the spine, flap and tab are never stitched. Score lines mark
    the four folds around the central pocket panel.
    """
    doc = Document("Vertical wallet")
    hw = 35.0                          # central spine half-width (70 mm)
    tip = 110.0                        # wing tip half-span (220 mm overall)
    apex, shoulder = 290.0, 248.0      # top flap point / where it squares off
    band_top, band_bot = 130.0, 45.0   # the horizontal wing band
    r_top = 108.0                      # right wing top-outer (diagonal cut)
    lower, tab = 22.0, 26.0            # lower spine / bottom tab half-width

    body = Polygon(
        name="Wallet body (one piece)",
        points=[
            Vec2(0.0, apex),                 # flap point
            Vec2(hw, shoulder),              # right shoulder
            Vec2(hw, band_top),              # right spine, band top
            Vec2(tip, r_top),                # right wing tip (sloped)
            Vec2(tip, band_bot),             # right wing bottom
            Vec2(hw, band_bot),              # back to spine
            Vec2(hw, lower),                 # lower spine right
            Vec2(tab, 0.0),                  # bottom tab right
            Vec2(-tab, 0.0),                 # bottom tab left
            Vec2(-hw, lower),                # lower spine left
            Vec2(-hw, band_bot),             # spine, band bottom
            Vec2(-tip, band_bot),            # left wing bottom
            Vec2(-tip, band_top),            # left wing tip (square)
            Vec2(-hw, band_top),             # back to spine
            Vec2(-hw, shoulder),             # left shoulder
        ],
        close_path=True, corner_radius=3.0,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False), layer="Cut")
    doc.add_shape(body)

    # fold lines: the rectangle around the central pocket panel (wings fold
    # up along the two verticals; flap folds down, tab folds up)
    folds = [("Left wing fold", [Vec2(-hw, band_bot), Vec2(-hw, band_top)]),
             ("Right wing fold", [Vec2(hw, band_bot), Vec2(hw, band_top)]),
             ("Flap fold", [Vec2(-hw, band_top), Vec2(hw, band_top)]),
             ("Bottom fold", [Vec2(-hw, band_bot), Vec2(hw, band_bot)])]
    for name, pts in folds:
        doc.add_shape(PathShape(name=name, points=pts, close_path=False,
                                transform=Transform(x=0, y=0), layer="Score"))

    # one seam per wing: a U of holes 6 mm in from the top / end / bottom
    # edges. The left wing is square; the right follows its diagonal top.
    d = 6.0
    left = StitchLine(
        points=[Vec2(-hw, band_top - d), Vec2(-tip + d, band_top - d),
                Vec2(-tip + d, band_bot + d), Vec2(-hw, band_bot + d)],
        corner_points=[Vec2(-tip + d, band_top - d),
                       Vec2(-tip + d, band_bot + d)],
        settings=StitchSettings(pitch_mm=3.85, fit="endpoints"))
    left.name = "Left wing seam"
    right = StitchLine(
        points=[Vec2(hw, band_top - d), Vec2(tip - d, r_top - d),
                Vec2(tip - d, band_bot + d), Vec2(hw, band_bot + d)],
        corner_points=[Vec2(tip - d, r_top - d),
                       Vec2(tip - d, band_bot + d)],
        settings=StitchSettings(pitch_mm=3.85, fit="endpoints"))
    right.name = "Right wing seam"
    doc.add_stitch_line(left)
    doc.add_stitch_line(right)
    return doc


#: (menu label, builder) — the File → New from template entries.
TEMPLATES = [
    ("Card holder", card_holder),
    ("Belt", belt),
    ("Key fob", key_fob),
    ("Vertical wallet", vertical_wallet),
]

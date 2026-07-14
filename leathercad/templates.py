"""Starter templates: small, complete documents to open and adapt.

Each template is built in code (deterministic, always in sync with the shape
model) and doubles as a worked example of good pattern structure. They match
the tutorials in the user guide.
"""

from __future__ import annotations

import math

from .document import Document
from .geometry import Vec2
from .shapes import (Rectangle, Circle, Polygon, PathShape, EditablePath, Edge,
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


def _card_panel(name, side_top, top_mid, layer="Cut", stitch=None,
                hw=35.0, r=9.0):
    """A card-holder panel: width 2*hw, straight sides, softly rounded bottom
    corners (radius r), and a single curved top edge through (0, top_mid).
    top_mid > side_top gives a convex arched top (the back); top_mid < side_top
    a concave thumb scoop (a pocket mouth). Built as an EditablePath so the top
    stays a true arc -- the chord-spacing engine flows evenly around it with no
    forced corners."""
    o = r * math.cos(math.radians(45))         # fillet arc-midpoint offset
    nodes = [
        Vec2(-hw, r),                # 0 left side, above bottom fillet
        Vec2(-hw, side_top),         # 1 left side top
        Vec2(hw, side_top),          # 2 right side top
        Vec2(hw, r),                 # 3 right side, above bottom fillet
        Vec2(hw - r, 0.0),           # 4 bottom, after right fillet
        Vec2(-hw + r, 0.0),          # 5 bottom, before left fillet
    ]
    edges = [
        Edge("line"),                                   # left side
        Edge("arc", Vec2(0.0, top_mid)),                # curved top edge
        Edge("line"),                                   # right side
        Edge("arc", Vec2(hw - r + o, r - o)),           # bottom-right fillet
        Edge("line"),                                   # bottom
        Edge("arc", Vec2(-hw + r - o, r - o)),          # bottom-left fillet
    ]
    return EditablePath(nodes=nodes, edges=edges, closed=True,
                        transform=Transform(x=0, y=0), layer=layer,
                        stitch=stitch, name=name)


def curved_card_holder() -> Document:
    """Flagship demo: an elegant vertical card holder, ~70 x 106 mm, with a
    softly ARCHED top and three stepped, thumb-scooped card pockets.

    One continuous run of fine saddle stitching wraps the whole curved
    perimeter of the back panel -- this is the piece that shows off the
    program's signature: pricking-iron-accurate chord spacing that stays
    even as it flows around the arch and the rounded corners, where naive
    'space along the contour' stitching would bunch up.

    The three front pockets stack bottom-aligned; their concave scoop
    mouths step up so each card's head is easy to thumb out. The pockets
    share the back panel's width, so the perimeter stitch passes through
    all layers down the sides and across the bottom, holding the stack
    together -- their mouths stay open and unstitched.
    """
    doc = Document("Curved card wallet")
    hw = 35.0
    fine = StitchSettings(pitch_mm=3.5, inset=3.5, hole_diameter=1.0)
    # back panel: a semicircular dome (radius = half-width) sits tangent on
    # the straight sides, so the shoulders flow smoothly with no kink; the
    # perimeter saddle stitch is the hero curve.
    doc.add_shape(_card_panel("Back panel", side_top=75.0,
                              top_mid=75.0 + hw, stitch=fine, hw=hw))
    # three stepped pockets, tallest first (drawn back-to-front), scooped tops
    steps = [("Card pocket (back)", 73.0), ("Card pocket (middle)", 57.0),
             ("Card pocket (front)", 41.0)]
    for name, side_top in steps:
        doc.add_shape(_card_panel(name, side_top=side_top,
                                  top_mid=side_top - 12.0,
                                  stitch=StitchSettings(enabled=False), hw=hw))
    return doc


#: (menu label, builder) — the File → New from template entries.
TEMPLATES = [
    ("Card holder", card_holder),
    ("Belt", belt),
    ("Key fob", key_fob),
    ("Curved card wallet", curved_card_holder),
]

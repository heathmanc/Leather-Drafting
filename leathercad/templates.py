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


def bifold_wallet() -> Document:
    """A clean SINGLE-PIECE horizontal bifold (my own design), cut from one
    rectangle, ~200 x 140 mm flat, folding to a ~100 x 95 mm bifold.

    Two folds, no separate pieces:

      * a horizontal POCKET fold -- the bottom 45 mm turns up over the front
        to make a full-width pocket for cards and folded cash;
      * a vertical CENTRE fold -- the whole thing then closes like a book,
        and that crease splits the pocket into a left and a right
        compartment on its own (no seam needed down the middle).

    Stitching is one straight seam up each outer side, through both the
    pocket layer and the shell, closing the two pocket sides; the pocket
    bottom is the fold (already closed) and the top is left open so cards
    slide in. Everything registers because each seam is a single straight
    line -- fold the pocket up and its holes sit directly over the shell's.
    """
    doc = Document("Bifold wallet")
    W, H = 200.0, 140.0                # flat sheet; closed ~100 x 95 mm
    POCKET = 45.0                      # bottom strip that folds up
    hw, hh = W / 2.0, H / 2.0
    fold_y = -hh + POCKET              # pocket-fold height (from the bottom)

    doc.add_shape(Rectangle(
        name="Wallet body (one piece)", width=W, height=H, corner_radius=8.0,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False), layer="Cut"))
    # fold lines
    doc.add_shape(PathShape(
        name="Pocket fold", points=[Vec2(-hw, fold_y), Vec2(hw, fold_y)],
        close_path=False, transform=Transform(x=0, y=0), layer="Score"))
    doc.add_shape(PathShape(
        name="Centre fold", points=[Vec2(0, -hh), Vec2(0, hh)],
        close_path=False, transform=Transform(x=0, y=0), layer="Score"))
    # one straight seam up each outer side of the pocket region
    d = 4.0
    for sign, name in ((-1, "Left pocket seam"), (1, "Right pocket seam")):
        seam = StitchLine(
            points=[Vec2(sign * (hw - d), -hh + d), Vec2(sign * (hw - d),
                                                         fold_y - d)],
            settings=StitchSettings(pitch_mm=3.85, fit="endpoints"))
        seam.name = name
        doc.add_stitch_line(seam)
    return doc


#: (menu label, builder) — the File → New from template entries.
TEMPLATES = [
    ("Card holder", card_holder),
    ("Belt", belt),
    ("Key fob", key_fob),
    ("Bifold wallet", bifold_wallet),
]

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
    """A minimalist one-piece vertical fold wallet (in the style of the
    Oldis One): 70 x 100 mm closed, the front panel folded up from the
    bottom with a diagonal (right-handed) opening edge, an inner divider
    slot, and two side seams.

    The pattern is drawn FLAT: back panel above the fold line, front panel
    below it. Each side seam is one straight stitch line centred on the
    fold, so the fitted holes come out mirror-symmetric about the fold --
    fold the front up and every front hole lands exactly on its back hole.
    The divider is glued into the seams and pricked through the same holes.
    Mirror the main piece (Ctrl+M) for a left-handed version.
    """
    doc = Document("Vertical wallet")
    W, BACK_H = 70.0, 100.0            # closed size: 2.75 x 3.95 in
    F_LEFT, F_RIGHT = 88.0, 70.0       # front panel side heights (diagonal
    #                                    opening: lower on the right = thumb
    #                                    access for a right-hander)
    hw = W / 2.0
    body = Polygon(
        name="Body (fold up over the line)",
        points=[Vec2(-hw, BACK_H), Vec2(hw, BACK_H),      # back top
                Vec2(hw, -F_RIGHT), Vec2(-hw, -F_LEFT)],  # front, diagonal
        close_path=True, corner_radius=6.0,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False), layer="Cut")
    doc.add_shape(body)
    # the fold: front panel turns up over the back here
    doc.add_shape(PathShape(
        name="Fold", points=[Vec2(-hw, 0), Vec2(hw, 0)], close_path=False,
        transform=Transform(x=0, y=0), layer="Score"))
    # side seams, drawn flat: one straight run centred on the fold, ending
    # 4 mm short of each opening edge. fit="endpoints" spaces the holes
    # symmetrically about the middle, i.e. about the fold.
    inset = 3.5
    for x, half in ((-(hw - inset), F_LEFT - 4.0),
                    (hw - inset, F_RIGHT - 4.0)):
        seam = StitchLine(points=[Vec2(x, -half), Vec2(x, half)],
                          settings=StitchSettings(pitch_mm=3.85,
                                                  fit="endpoints"))
        seam.name = "Side seam"
        doc.add_stitch_line(seam)
    # inner divider: the ~2-card slot; no holes of its own -- it is glued in
    # and pricked through the seam holes.
    doc.add_shape(Rectangle(
        name="Divider (2-card slot)", width=W, height=62, corner_radius=4,
        transform=Transform(x=W + 25.0, y=31.0),
        stitch=StitchSettings(enabled=False), layer="Cut"))
    return doc


#: (menu label, builder) — the File → New from template entries.
TEMPLATES = [
    ("Card holder", card_holder),
    ("Belt", belt),
    ("Key fob", key_fob),
    ("Vertical wallet", vertical_wallet),
]

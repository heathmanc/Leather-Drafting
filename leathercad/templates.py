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
    """A minimalist SINGLE-PIECE vertical wallet with a fold-down top flap
    (in the style of the Oldis One): 70 x 100 mm closed.

    One continuous piece, drawn FLAT, bottom to top: front pocket panel
    (folds UP at the lower score line, diagonal opening edge -- lower on
    the right for right-handed thumb access), back panel, and a tapered
    flap above the upper score line that folds DOWN over the pocket to
    keep everything in. No hardware: the leather's memory holds it closed.

    Each side seam is one straight stitch line centred on the pocket fold,
    so the fitted holes come out mirror-symmetric about it -- fold the
    front up and every front hole lands exactly on its back hole. The flap
    is never stitched. Mirror the piece (Ctrl+M) for a left-handed version.
    """
    doc = Document("Vertical wallet")
    W, BACK_H = 70.0, 100.0            # closed size: 2.75 x 3.95 in
    F_LEFT, F_RIGHT = 78.0, 62.0       # front pocket side heights (diagonal
    #                                    opening: lower on the right = thumb
    #                                    access for a right-hander)
    FLAP_H, FLAP_W = 48.0, 62.0        # flap length + tapered tip width
    hw, ht = W / 2.0, FLAP_W / 2.0
    body = Polygon(
        name="Wallet body (one piece)",
        points=[Vec2(-hw, -F_LEFT), Vec2(hw, -F_RIGHT),   # front, diagonal
                Vec2(hw, BACK_H),                         # up the right side
                Vec2(ht, BACK_H + FLAP_H),                # tapered flap tip
                Vec2(-ht, BACK_H + FLAP_H),
                Vec2(-hw, BACK_H)],                       # down the left side
        close_path=True, corner_radius=6.0,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False), layer="Cut")
    doc.add_shape(body)
    # fold lines: pocket fold (front turns UP) and flap fold (turns DOWN)
    doc.add_shape(PathShape(
        name="Pocket fold", points=[Vec2(-hw, 0), Vec2(hw, 0)],
        close_path=False, transform=Transform(x=0, y=0), layer="Score"))
    doc.add_shape(PathShape(
        name="Flap fold", points=[Vec2(-hw, BACK_H), Vec2(hw, BACK_H)],
        close_path=False, transform=Transform(x=0, y=0), layer="Score"))
    # side seams, drawn flat: one straight run centred on the pocket fold,
    # ending 4 mm short of the opening edge. fit="endpoints" spaces the
    # holes symmetrically about the middle, i.e. about the fold.
    inset = 3.5
    for x, half in ((-(hw - inset), F_LEFT - 4.0),
                    (hw - inset, F_RIGHT - 4.0)):
        seam = StitchLine(points=[Vec2(x, -half), Vec2(x, half)],
                          settings=StitchSettings(pitch_mm=3.85,
                                                  fit="endpoints"))
        seam.name = "Side seam"
        doc.add_stitch_line(seam)
    return doc


#: (menu label, builder) — the File → New from template entries.
TEMPLATES = [
    ("Card holder", card_holder),
    ("Belt", belt),
    ("Key fob", key_fob),
    ("Vertical wallet", vertical_wallet),
]

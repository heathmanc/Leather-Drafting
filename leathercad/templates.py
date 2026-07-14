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


def slim_card_holder() -> Document:
    """A slim, flat-top front-pocket card holder, ~70 x 92 mm -- compact
    enough to disappear in a front pocket, with DEEP pockets that actually
    grip a card.

    A flat rectangular back panel and three stacked front pockets, all the
    same width with lightly rounded corners (no domed top to snag). The
    pockets are deep -- their mouths step up only ~8 mm apart, so every card
    is held on both sides for ~68-84 mm of its 86 mm height (it won't fall
    out) while each card's head still clears the pocket in front of it for an
    easy thumb-out.

    One saddle-stitch seam runs down both sides and across the bottom -- a U,
    NOT a closed loop -- so the top stays open for the cards. The pockets
    share the back's width, so that one seam passes through every layer and
    holds the whole stack together.
    """
    doc = Document("Slim card holder")
    W, r = 70.0, 5.0                    # card 54 wide + 8 each side; soft corners
    BACK_H = 92.0                       # card 86 tall + a little; flat top
    disabled = lambda: StitchSettings(enabled=False)

    doc.add_shape(Rectangle(name="Back panel", width=W, height=BACK_H,
                            corner_radius=r, transform=Transform(x=0, y=BACK_H / 2),
                            stitch=disabled(), layer="Cut"))
    # three deep pockets, tallest (back-most) first, small 8 mm steps
    for name, h in (("Card pocket (back)", 84.0),
                    ("Card pocket (middle)", 76.0),
                    ("Card pocket (front)", 68.0)):
        doc.add_shape(Rectangle(name=name, width=W, height=h, corner_radius=r,
                                transform=Transform(x=0, y=h / 2),
                                stitch=disabled(), layer="Cut"))

    # perimeter saddle stitch: a U down the sides and across the bottom,
    # 3.5 mm in; the top edge is left open so cards slide in.
    d = 3.5
    hw = W / 2
    seam = StitchLine(
        points=[Vec2(-(hw - d), BACK_H - 6), Vec2(-(hw - d), d),
                Vec2(hw - d, d), Vec2(hw - d, BACK_H - 6)],
        corner_points=[Vec2(-(hw - d), d), Vec2(hw - d, d)],
        settings=StitchSettings(pitch_mm=3.5, fit="endpoints"))
    seam.name = "Perimeter seam"
    doc.add_stitch_line(seam)
    return doc


def fold_over_wallet() -> Document:
    """A one-piece fold-over pouch wallet in the style of the Oldis One
    (originally "The Lucais" by JJ Leathersmith): a T-shaped flat pattern,
    219 x 290 mm, that folds into a ~70 x 100 mm vertical flap wallet.

    The T, drawn flat:

      * a full-width BLOCK along the bottom (219 x 90) -- the pouch. The two
        wings fold inward along the column edges to make the internal card /
        cash compartments; the right wing's top edge slopes down (the pouch
        entrance) and carries a DIAGONAL quick-access card slot with a round
        relief hole punched at each end so the cut can't tear;
      * a 70 mm COLUMN rising from the block -- the back panel and, above
        the flap fold line, the long closing FLAP. Its pointed tip (with a
        small step-notch on the right shoulder that acts as the catch) folds
        over the entrance and tucks down the front; the small notch in the
        block's bottom edge gives your thumb room to lift the tip back out.

    Stitching is 3 mm pitch, as on the original pattern sheet: one seam
    around the left wing (top edge, outer edge, then the bottom run to the
    thumb notch) and one along the bottom of the right wing. The sloped top
    and the diagonal slot stay open -- that's where cards go in and out.
    Mirror everything (Ctrl+M) for a left-handed version.
    """
    doc = Document("Fold-over wallet")
    CL, CR = 78.0, 148.0               # column edges (70 mm wide)
    body = Polygon(
        name="Wallet body (one piece)",
        points=[
            Vec2(0, 0),                          # block bottom-left
            Vec2(95, 0), Vec2(104, 16),          # thumb notch (tuck access)
            Vec2(122, 16), Vec2(131, 0),
            Vec2(219, 0),                        # block bottom-right
            Vec2(219, 62),                       # right edge (shorter side)
            Vec2(CR, 90),                        # sloped pouch entrance
            Vec2(CR, 238),                       # up the column
            Vec2(143, 238), Vec2(143, 246),      # step-notch (flap catch)
            Vec2(113, 290),                      # the flap point
            Vec2(CL, 246),                       # left shoulder
            Vec2(CL, 90),                        # down the column
            Vec2(0, 90),                         # left wing top
        ],
        close_path=True, corner_radius=3.0,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(enabled=False), layer="Cut")
    doc.add_shape(body)

    # the diagonal quick-access card slot: a cut with relief holes at the ends
    slot_a, slot_b = Vec2(160, 58), Vec2(205, 30)
    doc.add_shape(PathShape(name="Quick-access slot",
                            points=[slot_a, slot_b], close_path=False,
                            transform=Transform(x=0, y=0), layer="Cut"))
    for i, p in enumerate((slot_a, slot_b)):
        doc.add_shape(Circle(name=f"Slot relief {i + 1}", rx=2.0, ry=2.0,
                             transform=Transform(x=p.x, y=p.y), layer="Cut"))

    # fold lines: both wings fold in along the column edges; the flap folds
    # over at the top of the pouch
    for name, pts in (("Left wing fold", [Vec2(CL, 0), Vec2(CL, 90)]),
                      ("Right wing fold", [Vec2(CR, 0), Vec2(CR, 90)]),
                      ("Flap fold", [Vec2(CL, 185), Vec2(CR, 185)])):
        doc.add_shape(PathShape(name=name, points=pts, close_path=False,
                                transform=Transform(x=0, y=0), layer="Score"))

    # seams, 3 mm pitch like the original sheet. Left wing: top -> outer
    # edge -> bottom run to the thumb notch. Right wing: bottom run only --
    # its sloped top and the slot stay open.
    left = StitchLine(
        points=[Vec2(74, 86), Vec2(4, 86), Vec2(4, 4), Vec2(91, 4)],
        corner_points=[Vec2(4, 86), Vec2(4, 4)],
        settings=StitchSettings(pitch_mm=3.0, fit="endpoints"))
    left.name = "Left wing seam"
    right = StitchLine(
        points=[Vec2(135, 4), Vec2(215, 4)],
        settings=StitchSettings(pitch_mm=3.0, fit="endpoints"))
    right.name = "Right wing seam"
    doc.add_stitch_line(left)
    doc.add_stitch_line(right)
    return doc


#: (menu label, builder) — the File → New from template entries.
TEMPLATES = [
    ("Card holder", card_holder),
    ("Belt", belt),
    ("Key fob", key_fob),
    ("Slim card holder", slim_card_holder),
    ("Fold-over wallet", fold_over_wallet),
]

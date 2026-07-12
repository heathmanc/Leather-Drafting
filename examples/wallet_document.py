"""Example (no GUI): build a document, prove registration, export SVG + DXF.

Run it:  python examples/wallet_document.py

Two identical wallet panels (front + lining) get *byte-identical* hole layouts,
so every hole lines up with its partner when the panels are stitched together --
including when one is lasered mirrored (from the back).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leathercad import (Document, Rectangle, Transform, StitchSettings,
                        get_iron, export)
from leathercad.stitching import stitch_polyline


def panel(x, y, mirror=False):
    return Rectangle(
        width=95, height=65, corner_radius=10,
        transform=Transform(x=x, y=y, mirror_x=mirror),
        stitch=StitchSettings(pitch_mm=get_iron("3.85mm").pitch_mm,
                              inset=3.5, hole_style="slit", slit_angle=30),
        layer="Cut")


def holes_local(shape):
    res = stitch_polyline(*shape.world_polyline(), shape.stitch)
    ox, oy = shape.transform.x, shape.transform.y
    return sorted((round(h.point.x - ox, 3), round(h.point.y - oy, 3))
                  for h in res.holes)


def main():
    doc = Document("wallet")
    front = panel(0, 0)
    lining = panel(120, 0)            # same size, placed apart on the sheet
    back_mirrored = panel(0, -90, mirror=True)  # lasered from the back
    for p in (front, lining, back_mirrored):
        doc.add_shape(p)

    # Registration proof
    same = holes_local(front) == holes_local(lining)
    mirrored_ok = (sorted((round(-x, 3), y) for x, y in holes_local(front))
                   == holes_local(back_mirrored))
    print(f"front and lining have identical holes: {same}")
    print(f"mirrored back lines up hole-for-hole:  {mirrored_ok}")

    here = os.path.dirname(os.path.abspath(__file__))
    svg = os.path.join(here, "wallet.svg")
    dxf = os.path.join(here, "wallet.dxf")
    export.export_svg(doc, svg)
    export.export_dxf(doc, dxf)

    doc.save(os.path.join(here, "wallet.leathercad.json"))
    print(f"Wrote {os.path.basename(svg)}, {os.path.basename(dxf)}, "
          f"and wallet.leathercad.json")


if __name__ == "__main__":
    main()

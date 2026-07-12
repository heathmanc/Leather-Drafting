"""Example: a rounded-corner card sleeve with a stitched border.

Run it:  python examples/card_holder.py
Outputs: card_holder.svg  (open in a browser or your laser software)

It also prints a side-by-side of chord vs arc-length spacing so you can see the
difference the project is built around.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leathercad import PathBuilder, stitch_path, get_iron, Vec2
from leathercad.svg import export_svg
from leathercad.stitching import polyline_from_path, march_chord, march_arclength


def rounded_rect(w, h, r):
    """Closed path: rounded rectangle, corners tagged as stitch anchors."""
    b = PathBuilder()
    b.move_to(r, 0)
    b.line_to(w - r, 0)
    b.arc_to(w - r, r, -math.pi / 2, 0.0).corner()
    b.line_to(w, h - r)
    b.arc_to(w - r, h - r, 0.0, math.pi / 2).corner()
    b.line_to(r, h)
    b.arc_to(r, h - r, math.pi / 2, math.pi).corner()
    b.line_to(0, r)
    b.arc_to(r, r, math.pi, 1.5 * math.pi).corner()
    b.close(corner=False)
    return b.build()


def main():
    iron = get_iron("3.85mm")           # a common European pitch
    path = rounded_rect(90, 60, 8)

    stitches = stitch_path(path, pitch=iron.pitch_mm, fit="closed")

    outline = path.flatten()
    export_svg(
        "card_holder.svg",
        cut_polylines=[outline],
        stitches=stitches,
        slit_length=1.6,          # slit holes...
        slit_angle_deg=30.0,      # ...slanted 30 deg for the diamond-awl look
        margin=6.0,
    )

    print(f"Iron: {iron.name}  ({iron.pitch_mm:.2f} mm, {iron.spi:.1f} SPI)")
    print(f"Placed {stitches.count} holes around a {90}x{60} mm sleeve.")
    print(f"Effective pitches per edge (mm): "
          f"{[round(p, 3) for p in stitches.pitches]}")
    gaps = stitches.chord_spacings()
    print(f"Chord spacing min/max: {min(gaps):.3f} / {max(gaps):.3f} mm")
    print("Wrote card_holder.svg")

    # --- illustrate the difference where it actually bites: a tight curve --
    # A pricking iron cares about STRAIGHT-LINE gaps. On a 6 mm-radius bend,
    # arc-length spacing quietly makes every hole sit closer than the iron.
    print("\nWhy chord spacing matters -- a 6 mm-radius bend, 3.85 mm iron:")
    bend = PathBuilder().move_to(6, 0).arc_to(0, 0, 0.0, math.pi).build()
    bpoly = polyline_from_path(bend)
    c = march_chord(bpoly, iron.pitch_mm)
    a = march_arclength(bpoly, iron.pitch_mm)
    cpts = [bpoly.point_at(s) for s in c]
    apts = [bpoly.point_at(s) for s in a]
    cg = [(cpts[i + 1] - cpts[i]).length() for i in range(len(cpts) - 1)]
    ag = [(apts[i + 1] - apts[i]).length() for i in range(len(apts) - 1)]
    for i in range(min(len(cg), len(ag))):
        drift = (iron.pitch_mm - ag[i]) / iron.pitch_mm * 100
        print(f"  gap {i}: chord={cg[i]:.3f} mm   arc-length={ag[i]:.3f} mm "
              f"({drift:+.1f}% vs the iron)")


if __name__ == "__main__":
    main()

# Leather-Drafting (`leathercad`)

A Python CAD core for drawing **laser-cut leather patterns**, built around the
one thing most pattern software gets wrong for hand-stitchers: **stitch hole
spacing that matches a real pricking iron.**

> Status: early foundation (v0.1). The stitch-spacing engine, path model, iron
> presets, and laser-ready SVG export are working and tested. A GUI and more
> pattern primitives are on the roadmap below.

---

## The problem this solves

Ask most CAD / pattern tools for "5 mm stitch spacing" and they place a hole
every **5 mm of arc length** — they walk *along the contour*. On a curve, the
actual straight-line gap between two neighbouring holes then comes out **shorter
than 5 mm**. So the holes never line up with your physical pricking iron or
stitching chisel, whose teeth are rigid and a fixed **straight-line (chord)**
distance apart.

`leathercad` does **chord marching** instead: from each hole it finds the next
point *forward along the path* whose straight-line distance is exactly the
iron's pitch. That reproduces what an iron physically does as you rotate it to
follow a curve — consecutive holes are always `pitch` apart point-to-point.

![chord vs arc-length spacing](docs/spacing_comparison.svg)

On a 6 mm-radius bend with a 3.85 mm iron:

```
  gap 0: chord=3.850 mm   arc-length=3.786 mm  (+1.7% short vs the iron)
  gap 1: chord=3.850 mm   arc-length=3.783 mm  (+1.7% short vs the iron)
  ...
```

Small per hole, but it accumulates over a seam and it means the software's holes
drift out of register with the tool in your hand. Chord spacing stays locked to
the iron.

It also does what leatherworkers do by hand: **nudges the effective pitch a few
percent** so a whole number of holes lands cleanly on every corner and on both
ends of an open seam (the `fit` modes).

## Quick start

```bash
git clone <this repo>
cd Leather-Drafting
pip install -e .          # no dependencies; Python 3.9+
python examples/card_holder.py
```

```python
import math
from leathercad import PathBuilder, stitch_path, get_iron, export_svg

iron = get_iron("3.85mm")          # or Iron.from_spi(7), or a raw pitch in mm

# A rounded-corner card sleeve, corners tagged so a hole lands on each one.
b = PathBuilder().move_to(8, 0)
b.line_to(82, 0)
b.arc_to(82, 8, -math.pi/2, 0.0).corner()
b.line_to(90, 52)
b.arc_to(82, 52, 0.0, math.pi/2).corner()
b.line_to(8, 60)
b.arc_to(8, 52, math.pi/2, math.pi).corner()
b.line_to(0, 8)
b.arc_to(8, 8, math.pi, 1.5*math.pi).corner()
b.close(corner=False)
path = b.build()

stitches = stitch_path(path, pitch=iron.pitch_mm, fit="closed")

export_svg("sleeve.svg",
           cut_polylines=[path.flatten()],
           stitches=stitches,
           slit_length=1.6, slit_angle_deg=30)   # diamond-awl slant
```

The SVG is sized in real millimetres (1 user unit = 1 mm), with the outline on a
red `cut` layer and holes on a blue `holes` layer, ready to drop into your laser
software.

## Key concepts

| Concept | What it does |
|---|---|
| `PathBuilder` | Turtle-style builder: `move_to`, `line_to`, `arc_to`, `cubic_to`, `quad_to`, `close`. Curves flatten to a fine polyline automatically. |
| `.corner()` | Tags the current point as a corner — a hole is forced there and each edge between corners is fitted independently. |
| `stitch_path(..., mode=)` | `"chord"` = pricking-iron accurate (default). `"arclength"` = the naive method, for comparison. |
| `stitch_path(..., fit=)` | `"auto"` / `"endpoints"` / `"closed"` / `"none"`. Fitting nudges the pitch (within `max_dev`, default 12%) so holes land on corners and ends. |
| `irons` | `spi_to_mm`, `mm_to_spi`, `Iron.from_spi`, and a table of common pitches (`get_iron("4.0mm")`). |
| SVG holes | Round holes (`hole_diameter`) or slanted slits (`slit_length` + `slit_angle_deg`) oriented to the local tangent. |

## How the spacing engine works

1. The path (lines, arcs, Béziers) is flattened to a fine polyline (0.02 mm
   tolerance — well below any laser kerf).
2. **Chord marching:** from the current hole, intersect a circle of radius =
   pitch with the path, forward, and take the outward crossing. That point is
   the next hole. Repeat. (`leathercad/stitching.py`)
3. **Fitting:** for each span between corners (or between the two ends of an
   open seam), binary-search the pitch so an integer number of chord steps lands
   exactly on the far anchor — within `max_dev`, else fall back to exact pitch.

Run the tests to see the guarantees, including "chord spacing equals the pitch
exactly on a circle" and "arc-length spacing is measurably short":

```bash
pip install pytest && pytest
```

## Roadmap

- [x] Path model (lines, arcs, quadratic/cubic Béziers) + adaptive flattening
- [x] Chord (pricking-iron) stitch spacing + arc-length for comparison
- [x] Corner/endpoint fitting (integer hole counts, per-edge pitch nudging)
- [x] Iron / SPI presets and conversions
- [x] Laser-ready SVG export (mm-accurate, cut + holes layers, round or slit holes)
- [ ] Two-sided seam registration (holes aligned across a fold/join)
- [ ] Backstitch / start-stop hole conventions
- [ ] DXF export
- [ ] Boolean ops and offset (seam allowance / stitch-line offset from the edge)
- [ ] Interactive GUI (draw, tag corners, live hole preview)

## Layout

```
leathercad/
  geometry.py    Vec2 and vector math (dependency-free)
  path.py        Segments (Line/Arc/Bezier), Path, PathBuilder, flattening
  stitching.py   Polyline + chord/arc marching + fitting  <- the core
  irons.py       Pricking-iron pitch / SPI presets
  svg.py         mm-accurate laser SVG export
examples/card_holder.py
tests/test_stitching.py
```

## License

MIT.

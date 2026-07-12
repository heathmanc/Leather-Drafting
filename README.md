# Leather-Drafting

A Python desktop **CAD program for laser-cut leather patterns**, built around the
one thing most pattern software gets wrong for hand-stitchers: **stitch hole
spacing that matches a real pricking iron**, and holes that **line up perfectly
across overlapping pieces** no matter which side you laser.

![the app](docs/app.png)

> Status: v0.2. The desktop app (draw / radius / move-overlay / per-iron holes /
> layers / registration / SVG+DXF export) is working and tested. Roadmap below.

---

## Two problems this is designed around

### 1. Pricking-iron spacing, not contour spacing
Ask most tools for "3.85 mm spacing" and they drop a hole every 3.85 mm *of arc
length* along the contour. On a curve the actual straight-line gap between
neighbouring holes then comes out **short**, so the holes never match your
physical iron, whose teeth are rigid and a fixed **straight-line (chord)**
distance apart.

Leather-Drafting does **chord marching**: from each hole it finds the next point
forward along the path whose straight-line distance is exactly the iron's pitch.
Consecutive holes are always `pitch` apart point-to-point — exactly what the iron
does as you rotate it around a curve.

![chord vs arc-length spacing](docs/spacing_comparison.svg)

It also nudges the pitch a few percent (within a limit you set) so a whole number
of holes lands cleanly on **every corner** and on **both ends** of an open seam —
the thing leatherworkers do by hand.

### 2. Registration across overlapping pieces
Two pieces stitched together (front + lining, gusset + panel) must have their
holes in **identical positions** or they won't line up. Leather-Drafting
guarantees this three ways:

- **Deterministic perimeter stitching** — two pieces with the same outline and
  settings get *byte-identical* hole layouts automatically. Duplicate a panel and
  the holes already match.
- **Mirror-safe** — flip a piece to laser it from the back (`Mirror`) and the
  holes stay registered, hole-for-hole.
- **Shared stitch line (seam)** — for pieces with *different* outlines that share
  one edge, draw one seam and every piece against it gets the same holes.

Verify any of it visually by dragging one piece on top of another and dropping the
opacity — the tool is built for exactly that overlay check.

## Install & run

```bash
git clone <this repo>
cd Leather-Drafting
pip install -e ".[gui]"        # installs PySide6; Python 3.9+
python -m leathercad_app       # or: leather-drafting
```

On **Windows/macOS** the PySide6 wheel is self-contained. On headless **Linux**
you may need system libs: `sudo apt-get install libegl1 libgl1 libxkbcommon0`.

The engine alone (spacing, geometry, export) has **zero dependencies** — you only
need PySide6 for the GUI.

## Using the app

The drawing tools live in a **vertical tool palette docked on the left**. It is
a normal draggable toolbar — grab its handle to move or float it, drop it on any
edge, and use the **pin** button at the top to lock it in place. Its position
(and the window layout) is remembered between sessions.

| | |
|---|---|
| **Draw** | Rectangle `R`, Rounded rect `O`, Ellipse `E`, Circle `C`, Polygon `P` (click points, double-click to finish) |
| **Holes / slots / fold lines** | Hole `H` (hardware), Slot `T` (stadium), Score line `K` (fold/skive on the Score layer) |
| **Seam** | Stitch line `L` — a shared seam for cross-piece registration |
| **Select / move** | `S` — drag to move, drag one piece over another to check fit |
| **Snapping** | toolbar *Snap* toggle + grid size; snaps to grid and to other shapes' corners while drawing and moving |
| **Exact sizes** | live W×H shown while dragging; after drawing, the size field is focused so you can type an exact value |
| **Select by outline** | shapes are grabbed by clicking their outline, not the filled interior — click "inside" to reach shapes behind or draw there |
| **Edit nodes** | double-click a polygon/seam, or right-click a shape → *Convert to editable nodes* (`Ctrl+K`). Rounded corners keep their **arcs**: each arc shows its two endpoints plus a midpoint handle you drag to reshape the curve. The shape locks while editing so clicks grab the nodes. |
| **Snap to nodes** | with Snap on, dragging a shape (or a node while editing) magnetically snaps to other shapes'/holes' nodes |
| **Break apart** | right-click a shape → *Break apart into segments* (`Ctrl+B`) — each edge (line or arc) becomes its own movable piece |
| **Group / ungroup holes** | right-click (or `Ctrl+G` / `Ctrl+Shift+G`) — ungroup to delete individual holes (below) |
| **Right-click menu** | Group / Ungroup / Convert to nodes / Duplicate / Delete on the selection |
| **Radius corners** | select a rectangle/polygon, set *Corner radius* in Properties |
| **Iron & holes** | per shape: pick an iron (mm or SPI), inset, round or slanted-slit holes, single or **double row** (saddle stitch) + backstitch, live hole count + spacing readout |
| **Layers → laser jobs** | colour-coded Cut / Score / Engrave / Stitch layers with visibility |
| **Arrange** | align (left/centre/right/top/middle/bottom) and distribute selected shapes |
| **Undo / redo** | `Ctrl+Z` / `Ctrl+Shift+Z` |
| **Duplicate / Delete / Fit** | `Ctrl+D` / `Del` / `F` |
| **Zoom / pan** | mouse wheel / middle-drag |
| **Save / Open** | `Ctrl+S` / `Ctrl+O` (JSON project files) |
| **Export** | SVG `Ctrl+E` or DXF — millimetre-accurate, layer-coloured |

Everything is in **millimetres**, Y-up, and the canvas is WYSIWYG with the export.

### Group / ungroup holes (removing individual ones)

By default a shape's holes are *computed* from its stitch settings, so they stay
evenly spaced when you change the iron or resize — but that means the engine
would redistribute them if you deleted one. To hand-edit holes (say, to clear a
spot for a rivet):

- **Ungroup** (right-click → *Ungroup stitching*, or `Ctrl+Shift+G`) explodes the
  shape's holes into **individual holes** and turns its auto-spacing off. Each
  hole is now an ordinary object — **click one to select it** (rubber-band to
  select many), drag to nudge, press **Delete** to remove. Nothing reflows; the
  gap stays exactly where you made it.
- **Group** (right-click → *Group holes into shape*, or `Ctrl+G`) does the
  reverse: select a shape **and** the loose holes, and they get baked back into
  the shape so they move and rotate with it as one unit — still without
  redistribution.

Right-click anywhere for the context menu (Group / Ungroup / Duplicate / Delete).

![ungroup to individual selectable holes](docs/individual_holes.png)

## Scripting API (no GUI needed)

The whole model is usable headless — handy for parametric patterns:

```python
from leathercad import Document, Rectangle, Transform, StitchSettings, get_iron, export

doc = Document("wallet")
iron = get_iron("3.85mm")
for x in (0, 120):                       # front + lining, placed apart
    doc.add_shape(Rectangle(
        width=95, height=65, corner_radius=10,
        transform=Transform(x=x, y=0),
        stitch=StitchSettings(pitch_mm=iron.pitch_mm, inset=3.5,
                              hole_style="slit", slit_angle=30),
        layer="Cut"))

export.export_svg(doc, "wallet.svg")
export.export_dxf(doc, "wallet.dxf")
doc.save("wallet.leathercad.json")
```

Runnable examples:

```bash
python examples/wallet_document.py   # builds a doc, proves registration, exports
python examples/card_holder.py       # engine demo: chord vs arc-length on a curve
```

## Tests

```bash
pip install -e ".[dev]" && QT_QPA_PLATFORM=offscreen pytest
```

The suite proves the guarantees that matter: chord spacing equals the iron pitch
on curves while arc-length spacing is measurably short; identical and mirrored
pieces get registered holes; save/load and SVG/DXF export round-trip.

## Project layout

```
leathercad/            pure-Python model + engine (no dependencies)
  geometry.py          Vec2 / vector math
  path.py              segments (line/arc/bezier), Path, PathBuilder, flattening
  offset.py            inward polygon offset (stitch-line inset)
  stitching.py         chord/arc marching + fitting + registration  <- the core
  stitchsettings.py    per-shape/seam stitch config
  shapes.py            Rectangle/Ellipse/Circle/Polygon/PathShape + transform + fillet
  layers.py            colour layers -> laser jobs
  stitchline.py        shared seam for cross-piece registration
  document.py          the CAD document + JSON save/load
  irons.py             pricking-iron pitch / SPI presets
  export.py            SVG + DXF exporters
leathercad_app/        PySide6 desktop app
  canvas.py            mm-accurate Y-up QGraphicsView, tools, grid, zoom/pan
  items.py             shape/seam graphics items with live hole rendering
  panels.py            properties + layers docks
  mainwindow.py        window, toolbar, menus, export wiring
  __main__.py          python -m leathercad_app
examples/  tests/  docs/
```

## Roadmap

- [x] Draw rectangles / rounded rects / ellipses / circles / polygons
- [x] Radius (fillet) corners
- [x] Move & overlay shapes (opacity) to verify sizing
- [x] Per-shape pricking-iron sizes (mm or SPI), round or slanted-slit holes
- [x] Chord (iron-accurate) spacing with corner/endpoint fitting
- [x] Cross-piece registration (deterministic + mirror-safe + shared seams)
- [x] Colour layers → laser jobs
- [x] SVG + DXF export, JSON project save/load
- [x] Undo/redo history
- [x] Grid + vertex snapping, live and type-in dimensions
- [x] Align / distribute
- [x] Edit polygon/seam vertices; add holes / slots / skive (score) lines
- [x] Two-row saddle stitch + backstitch markers
- [x] Left tool palette (draggable / floatable / pinnable, layout remembered)
- [x] Group / ungroup holes (individual selectable holes; group back to a shape)
- [x] Right-click context menu (group / ungroup / convert-to-nodes / duplicate / delete)
- [x] Convert a shape to editable nodes (rounded corners keep arcs: midpoint +
      endpoint handles); outline-based selection; node-to-node snapping (move + edit)
- [x] Break a shape apart into individually movable line/arc segments
- [ ] Boolean ops (windows, cut-outs) and true seam-allowance offset
- [ ] Alignment guides (smart snapping lines) while dragging
- [ ] Import reference images / trace an existing pattern
- [ ] Print-to-scale PDF tiling for hand cutting

## License

MIT.

# Changelog

Downloads are on the [Releases](https://github.com/heathmanc/leather-drafting/releases)
page. Windows builds ship with every release; the macOS build is paused and is
made only when the release workflow is run by hand with it ticked.

## 0.4.3 — 2026-11-09

### Export
* **Holes first in LightBurn.** LightBurn files an SVG's shapes into its
  layers by colour and lists them in palette order (C01 blue before C02 red).
  The stock stitch blue isn't one of its colours, so the holes landed on C14,
  after the red outline. They are now written in LightBurn's own blue
  (`#0000FF`) and land on C01, ahead of the outline on C02, so they are cut
  first. A stitch colour you picked yourself is written as it is; colours on
  screen are unchanged.
* **Named SVG layers:** "holes" first, then any score / engrave layers, then
  "outline", for programs that read layer names. LightBurn doesn't, so its
  layers still show as C01 / C02: name them once in LightBurn and save that as
  your starting project (the user guide has the steps).
* Importing one of our SVGs back still puts the holes on the Stitch layer.

## 0.4.2 — 2026-11-08

### Holes
* **Select everything and the holes go home.** After exploding a piece's
  stitching, select everything and press `Ctrl+Shift+A`: each loose hole is
  attached to the piece it lies inside. Where pieces overlap it goes to the
  smaller one (a pocket's holes to the pocket, not the panel under it), a
  hole on an open line goes to that line, and guides never take holes. Holes
  inside no piece stay loose and stay selected. With one piece selected,
  every selected hole still goes to it.
* **Break apart, select all, Join** brings a stitched piece back whole: Join
  (`Ctrl+J`) welds the outline and attaches the loose holes inside it, as one
  undo step. It attaches holes even when there is nothing to weld.

### Drawing and editing
* **3-point arc:** the status-bar prompts and the dashed preview now follow
  start, a point on the arc, then the end, the order the finished arc already
  used. After the second click the preview treated that point as the end, so
  a point snapped onto another node looked as if it had become the arc's end.
* **Node editing:** a dragged node also snaps to the other nodes of the shape
  being edited (an arc's middle onto a node elsewhere on the same outline, an
  open path's end onto its start), never onto the neighbours it would
  collapse onto.

## 0.4.1 — 2026-11-06

Fixes from a report on trimming and rounding a bookmark outline: two circles
joined by two arcs, with the circles' inner arcs trimmed away and the four
corners rounded at 35 mm.

### Trim
* **Cuts land on the true outline.** Trim cut against a flattened copy of the
  other outlines, so a cut on a curve stopped up to 0.02 mm inside it. Where
  two curves cross at a shallow angle, that left a nub (0.08 mm at a circle's
  quadrant) or an end running past the other piece. Trim now cuts against the
  real lines, arcs and curves.
* **One shared end point.** A cut line that stops a hair short of the outline,
  or runs a hair past it (up to 0.05 mm), cuts at its end, and the trimmed
  outline ends exactly there: the two pieces share one point.
* **No slivers at a free end.** A crossing within 0.3 mm of the free end of
  the piece you trim goes with the span you click instead of staying behind.
  Click the sliver itself to remove just that.
* **The red preview lies on the line.** The hover highlight is cut from the
  outline as it is drawn, so it covers the line it marks at any zoom.
* Trimming a small span out of a closed outline leaves it open. Under 0.6 mm,
  it closed straight back up.
* A closed seam cuts along its closing edge too.

### Fillet
* **The last corner closes the outline.** Rounding an outline made of pieces
  corner by corner welds them into one path; at the last corner, where that
  path's own two ends meet, the fillet now closes the loop and rounds the
  corner instead of calling it a loose end.
* Welded pieces whose far ends meet within 0.05 mm close into a loop.

### Extend
* Extend lands exactly on curves, not on their flattened copy.

## 0.4.0 — 2026-10-29

A review of the GUI's performance, the tools and the stitch-spacing options,
followed by five rounds of fixes, each re-checked by an independent pass.

### Stitching
* **The stitch options mean what they say.** *Fit* chooses between fitting to
  corners and ends or marching at the exact pitch; *Corners* and *Symmetry*
  only show when they apply; the readout explains why a fit came out the way
  it did (a floating closing gap, a symmetry that could not be met, a piece
  too small for a second row).
* **Double rows are a true second row.** The inner row runs a row spacing
  inside the fitted row, round corners and arcs too; a piece sewn in runs
  (an unsewn edge, a hardware keep-out) still gets its second row inward; a
  stretch too narrow for the row loses it there; a piece too small keeps one
  row and says so.
* **Per-edge stitching**: leave an edge of a piece unsewn (a pocket mouth).
* **Hardware library** with stitch keep-outs the seam flows round; drag a
  piece under a rivet and the holes part on release.
* Seam stitching: end gaps, closing edges, corner holes and the thread
  estimate corrected; baked double rows are costed as two runs.
* Imported and drawn paths anchor their real corners (octagons, chamfers,
  a doubled vertex at a corner), not the facets of a traced curve.

### Drawing and editing
* Node tools on arcs and curves, radius dimensions, copy along a path,
  grips on a rotated group, export options.
* Nudge, scale, flip and mirror, multi-edit, layer lock; align and
  distribute move groups whole; turn, mirror and array parts about their
  own centre; resizing a group stretches every member.
* Trim and Extend are exact on curves; Booleans handle shared edges,
  several operands and chains of overlapping pieces, and leave pieces that
  do not meet alone; Offset no longer folds or collapses; Modify commands
  keep curves, rounding and the shape's identity, so attached dimensions
  stay attached.
* Drawing tools: flat boxes, circle by radius, Shift lock, undo mid-draw;
  the Width field is focused for typing as soon as a shape is drawn.

### Units, dialogs and files
* **Inches as a display unit, everywhere**: every length field, dialog,
  readout, dimension label and arrow-key nudge; typed values take math and
  explicit units (`1/2in`, `10mm`, `1in+10mm`).
* Dialogs remember their last values exactly; the parts library keeps a
  part's seams and loose holes.
* Fold allowance and six more generators.
* File and export fixes from the audit; kerf nesting is exact and quick.

### Performance
Measured on a 150-piece traced import and a 100-piece nest:

| | before | after |
|---|---|---|
| Click on a stack of traced pieces | 1.8 s | 1 ms |
| First press after any edit (snap index) | 42 ms | 3 ms |
| Pan or box-select, zoomed out | 92 ms per frame | 2 ms |
| Nest on sheet, 100 pieces on a hide | 3.6 s | 0.08 s |
| Undo | 295 ms | 173 ms |
| Open a file of 20 distinct pieces (stitch fitting) | 1.05 s | 0.31 s |
| Layer tick, layer colour, Ungroup, export | seconds | instant |

Nest on sheet shows a progress bar and can be cancelled; opening, importing
and undo show a wait cursor.

## 0.3.2 — 2026-10-25
Fillet rounds a second corner without Join; duplicated groups stay grouped
with their seams; panel edits no longer round untouched fields; Trim cuts at
lines ending a hair short; exploded stitching stays in its group.

## 0.3.1 — 2026-10-25
Blend fillets into curves; undo no longer moves parts to places they never
were; double-click edits a just-drawn curve through its resize grips; cusp
insets no longer hang; Join welds the closest end; nodes can be added on arcs
and beziers.

## 0.3.0 — 2026-10-25
Renamed to Stitch Hero, with an app icon and a downloadable PDF manual.

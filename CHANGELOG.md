# Changelog

Downloads are on the [Releases](https://github.com/heathmanc/leather-drafting/releases)
page. Windows builds ship with every release; the macOS build is paused and is
made only when the release workflow is run by hand with it ticked.

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

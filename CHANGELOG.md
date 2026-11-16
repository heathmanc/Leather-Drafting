# Changelog

Downloads are on the [Releases](https://github.com/heathmanc/leather-drafting/releases)
page. Windows builds ship with every release; the macOS build is paused and is
made only when the release workflow is run by hand with it ticked.

## 0.4.9 — 2026-11-16

### Nest on sheet
* **Many layouts, the best kept.** A nest used to be one pass, biggest
  piece first, so where each piece landed hung on what went before. It
  now makes dozens of passes — the pieces in different orders, filled row
  by row and column by column, every piece in all four quarter turns —
  and keeps the layout that gets the most pieces on, then the most
  leather, then the tightest. A test set that used to get 7 of 10 pieces
  onto its sheet now gets 9.
* **Find the smallest sheet that takes every piece.** A new option sizes
  the border to the layout: strips of many widths are tried and the sheet
  is drawn round the layout that needs the least leather, to the next
  whole millimetre. The width and height you give become the most it may
  be.
* **Nesting again onto the same sheet.** The next nest goes onto the sheet
  the last one drew. Pieces already on it stay where they are and only
  the rest are fitted into the gaps, so a piece dragged off goes back in
  and a good layout isn't torn up. When the gaps can't take them all, the
  whole sheet is laid out afresh, if that gets more on. Before, a piece
  dragged off to the left or below took the sheet with it, and nesting one
  piece ignored the pieces already on the sheet.
* Pieces you aren't nesting that lie on the sheet stay put, and nothing is
  laid on them.
* The edge margin is measured from the outline to the sheet's edge. Pieces
  used to sit half a gap further in: 9 mm from the edge for a 5 mm margin
  and an 8 mm gap.
* The report names the pieces that were turned, and how far, and says how
  much of the sheet the pieces take up.
* A change typed into the dialog just before pressing Apply (or Enter) is
  now laid out before the layout is kept; Apply used to keep the layout
  from before the change.

## 0.4.8 — 2026-11-15

### Printing at 1:1
* **Nested PDFs.** Export PDF (1:1) and Print pack the pieces onto as few
  pages as they'll go, by their real outlines, at least 8 mm apart (or the
  gap you set, outline to outline), turning them 90° where that saves
  paper. A sample tote that took 30 pages as drawn now takes 13.
  * A piece that fits on a page is printed whole on one page.
  * A piece too big for a page is tiled across as few pages as it needs.
    Other pieces share those pages, each still whole on one page: the
    straps ride along the gusset.
  * Your drawing isn't changed. **As drawn** is still there, and now
    leaves out empty pages.
* **Ready to sell.**
  * Every page has a header (pattern name, designer) and a footer (your
    copyright or licence note, the scale check, page n of N).
  * A **details page** comes first: 50 mm and 1 inch squares to check the
    print, pieces to cut, stitch holes, the largest piece, the leather in
    the pieces and to buy, the smallest piece of leather they fit on, the
    thread, the iron spacing and hole size, and a list of the pieces with
    the pages they're on.
  * Every piece is printed with its name.
* **Tiled pages** say which row and column they are. A dashed line shows
  where the next page's border goes, and each overlap names the page that
  joins there.
* Paper: A4, US Letter, US Legal, A3, Tabloid, A2–A0 or a custom size,
  portrait or landscape. All the options are remembered.
* **Properties → Name**: name a piece (or several at once). The name is
  printed on it and used in the PDF's piece list and the nesting report.

### Nesting
* **Nest on sheet previews live.** The pieces move on the drawing as soon as
  the dialog opens, inside a dashed outline of the sheet, and again whenever
  a setting changes. **Apply** keeps the layout as one undo step; **Cancel**
  puts every piece back exactly. The gap starts at 8 mm.
* **The gap is a true minimum.** An edge was marked on the packing grid only
  at points every half cell along it, so it could skip the corner of a cell
  and let a neighbour in up to a cell closer than the gap. Every cell an
  outline passes through now counts, and the grid is sized so the gap isn't
  rounded up either (8 mm stays 8 mm, not 10).
* What travels with a piece: a fold or score line drawn a little past its
  edges, and lettering, loose holes or seams in its `Ctrl+G` group even when
  they lie outside it. A group's "inside" is its biggest outline, not the
  one with the most points.

### Parameters
* **A line's length and angle follow a parameter.** With `sidelength`
  = 10 mm, typing `sidelength` into a line's Length set it to 10 mm once.
  The line's Length and Angle weren't among the fields a parameter keeps
  linked, so changing `sidelength` later left the line as it was. They now
  stay linked like a rectangle's width: change the parameter and the line
  keeps its first end and moves the other.
* **Every straight line has a Line box.** A straight segment left by Trim,
  Join or Break apart is an editable path, and Properties showed no Length
  or Angle for it at all.
* **A parameter typed into a box takes, however you enter the box.** A
  click left the cursor where it landed, so `sidelength` typed into a
  width went in beside the old number ("40 mmsidelength"). That reads as
  nothing, and the box went back to 40 without a word. A click into a
  number box now selects all of it, so what you type replaces it (click
  again to place the cursor). An entry that can't be read is pointed out
  beside the box, e.g. "There's no parameter called sidelenght", instead
  of quietly reverting.
* **A circle's radius and diameter link to parameters.** The radius took a
  parameter's value but never stayed linked: refilling the radius and
  diameter boxes forgot where it came from. The diameter couldn't be linked
  at all. Either can now, one link per circle: the box you typed in last
  decides.

### Number boxes
* **"mm" no longer shows up twice.** The unit was held at the end of every
  number box, with whatever you typed going in front of it. Typing a unit
  (which the boxes accept: `25mm`, `1in`, `3cm`) showed `25mm mm`, and
  typing at the very end of a box gave `27 mmmm mm`. The value always came
  out right, but the box looked broken. The unit is now part of the box's
  text: select and type, and you see exactly what you typed (`25mm`, or
  just `25`), shown as `25 mm` once entered. Edit just the number and the
  unit stays put.

## 0.4.7 — 2026-11-15

### Selecting and snapping
* **Pick a piece up by one of its holes.** Only a piece's outline was
  clickable. Its stitch holes, attached or worked out from its stitching,
  were no part of it, so a press on a hole started a rubber band and a piece
  could never be dragged or snapped by a hole. A click on one of a piece's
  holes now selects the piece. Drag from there and **that hole's centre is
  the point that snaps**, onto a corner, a line's end or another piece's
  hole. So two pieces' stitching can be lined up hole for hole. A piece in a
  `Ctrl+G` group brings the whole group with it.
* Grabbed by its outline, a piece still snaps by its nearest corner, end,
  midpoint or centre. A rubber band still has to touch a piece's outline to
  take it, so a box over just its holes doesn't. When a hole and a line are
  both under the pointer, whichever is nearer takes the click.

### Drawing
* **End a line or arc right on the edge it runs into.** Drawing a 3-point
  arc out of a square, its end couldn't be snapped where the arc went
  through the square's top edge. Only an edge's corners, its midpoint and
  its crossings with other outlines snapped, so the arc ended 1.22 mm past
  the edge. While you draw a line, polyline, seam, pen path, measurement or
  arc, the point you're placing now also snaps to where the dashed preview
  crosses an outline near the pointer, anywhere along it.
  * A 3-point arc's end lands where the arc it shows goes through the
    edge, so the arc keeps the curve you saw. A centre arc's end lands where
    its circle does.
  * It's worked out on the outline's true lines, arcs and curves, not the
    flattened copy that sat up to 0.02 mm inside a curve. So an arc ended on
    a circle lies on that circle. The Shift lock's crossing snap
    (drawing straight across to an edge) is now exact on curves too.
  * Corners, midpoints and centres still win when the pointer is near one,
    and a shape's first point doesn't stick to edges.

## 0.4.6 — 2026-11-15

### Snapping
* **A dragged shape snaps by the point you grabbed it by.** Every corner,
  midpoint and centre of a dragged shape used to be tried against every
  target on each mouse move. The shape jumped to whichever pair happened to
  line up, often a far corner 50-70 mm from the pointer, so what snapped to
  what looked random. On interlocking pieces the tab latched onto the notch a
  few millimetres into the drag, and the corner you held never snapped at
  all. Now only the shape's point nearest where you grabbed it snaps, onto
  the target nearest that point. Grab near another point to
  snap by that one; a faint orange mark shows which point will snap, and the
  status bar names it.
* Circles and loose holes still snap by their centre; grab a circle right on
  a quarter point to snap by that instead. A moved group snaps by its own
  point nearest the grab.

### Selecting
* **Things drawn close together can be picked.** A construction line between
  two 1 mm holes 5 mm apart, with a line crossing its middle, could not be
  selected at all. Each item's pick zone was a fixed 3.5 mm band round a line
  or a 1.6 mm disk round a hole, and a click went to whichever item was on
  top. Those zones covered the whole construction line, at any zoom.
  * A click now picks the item whose drawn line or hole is nearest the
    pointer (a tie goes to the one on top). Right-click and double-click pick
    the same way.
  * Pick zones shrink when you zoom in, to 5 pixels either side of a line, so
    zooming in pulls small things apart. They never get bigger than before.
  * **Alt+click** steps to the next item under the pointer, for things lying
    exactly on top of each other.
  * With several things selected, pressing one still drags them all, and a
    plain click still picks the nearest thing on its own.

### Export
* **Every piece arrives in LightBurn as its own group.** LightBurn makes a
  group of each group in an SVG, and the export had one group of every hole
  and one of every outline. So after importing, each piece had to be
  ungrouped and regrouped with its own holes by hand. The SVG is now grouped
  by piece: each piece's holes, then its outline, as one group. Pieces grouped
  with `Ctrl+G` (a pocket with its engraved label, say) come in as one group
  around them, and a seam's holes or a lettering's letters as theirs.
  LightBurn still sorts by colour, so the holes stay on its blue layer C01
  and are cut before the red outlines on C02.
* In the file, loose holes, seams and score / engrave lines come before
  anything cut, for software that cuts in file order.
* The export dialog's new **Group the file** choice keeps the old layout:
  **By layer** writes named holes / score / outline layers, for Inkscape. The
  choice is remembered.

## 0.4.5 — 2026-11-11

### Fixed
* **Resizing a circle by a corner grip no longer flickers.** A circle has to
  stay round, so the grip used to pick whichever of width or height had moved
  more since the last mouse move. That choice flipped on every move, so the
  circle jumped back and forth between two sizes as you dragged. A corner grip
  now follows the point on the box's diagonal nearest the cursor, and grows or
  shrinks the circle smoothly. With Perfect fit on it steps through the
  perfect sizes in order, and each size is worked out once and remembered.
* The grip you drag now stays on the shape's actual corner or edge, instead of
  following the cursor away from it (a circle's corner, an edge grip pulled
  sideways, a locked aspect ratio or a Perfect fit size).

## 0.4.4 — 2026-11-10

### Stitching
* **Perfect fit.** A new toolbar toggle. With it on, dragging a resize grip on
  a stitched rectangle, rounded rectangle, circle or oval snaps it to the
  nearest size where every gap between holes is exactly the pitch, at that
  piece's own pitch and inset, so the iron walks the whole outline with no
  fudged stitch. Straight runs of the stitch line become whole numbers of
  pitches. A rounded corner snaps to a radius the iron turns in whole steps
  (R 10.0328 takes a 5 mm iron round a 90° corner at a 3.5 mm inset in two).
  Circles and ovals get a whole number of equal gaps. The status bar shows
  the size, corner radius and hole count as you drag. Drawing a rectangle,
  rounded rectangle, circle or ellipse snaps the same way.
* **Edit → Snap to perfect fit** does the same for every selected piece at
  once, about its centre, with or without the toggle.
* **Use for new shapes** (Properties → Stitching): a piece's punch, pitch,
  inset and fit become what every new shape starts with, and are remembered.
  Before this, new shapes always started at 3.85 mm / 3.5 mm.

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

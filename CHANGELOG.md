# Changelog

Downloads are on [stitch-hero.com](https://stitch-hero.com) and the
[Releases](https://github.com/heathmanc/leather-drafting/releases) page.
Windows and macOS builds ship with every release from 0.4.16 on (before it,
a macOS build was made only for some).

## Unreleased

### Resize from a corner; the aspect lock and Perfect fit work together
* **Choose where a typed size resizes from.** A little box of nine points
  before the toolbar's W box picks the point of the selection that stays
  put when you type a width or height: a corner, the middle of a side, or
  the centre (as before, and the first choice). Click a point to choose it;
  it's remembered.
* **The aspect lock and Perfect fit no longer fight over a dragged grip.**
  With both on, the lock re-read the piece's proportions after every move,
  just after Perfect fit had rounded each side to whole stitches, so they
  wandered off as you dragged. A 2:1 rectangle dragged out and back came
  back 78 × 54, its width shrank now and then while you pulled it out, and
  an edge grip never moved the other side. The lock now keeps the
  proportions the piece had when you grabbed it, and Perfect fit takes the
  perfect size nearest them: an oval keeps its proportions exactly, a
  rectangle each side within half a pitch of them.
* A size typed in W or H is kept as typed with Perfect fit on, as it is
  when drawing. The status bar now says so, and that Edit → Snap to
  perfect fit takes it to the nearest perfect size.
* Array's spacing again measures only what's selected, which is what it
  copies. Since 0.4.20, with part of a group selected (box-selected, say),
  it measured the whole group and offered too wide a spacing.

## 0.4.20 — 2026-11-22

### Type a size on the toolbar; the aspect lock is there too
* **W and H boxes on the top toolbar** show the width and height of what's
  selected. Type a size and press Enter to resize it to that, about its
  centre, as one undo step. A group, or several pieces, resizes as one,
  with its holes, seams and lettering; the stitch pitch and inset stay as
  set, as with Scale. A sum, another unit (`20mm`, `3in`) or a parameter
  can be typed too.
* **The aspect ratio lock is a chain button between them.** With it on, a
  typed width brings the height along in proportion, and the other way
  round, as a dragged resize grip already did. It's the same switch as
  View → Lock aspect ratio on resize, and Shift still does the opposite
  while you drag a grip.
* A circle or a piece of lettering keeps its proportions whichever size is
  typed. A level line has no height to type (its H box is greyed), and an
  upright one no width.
* **Scale sizes a turned rectangle, oval or circle right.** One turned off
  the page axes, on its own or with its group, grew by too much: Arrange →
  Scale… at 200% made a rectangle turned 30° 2.7 times its size, not
  twice. At 0° or 90° they weren't affected, nor were other pieces.

## 0.4.19 — 2026-11-22

### Construction lines catch what you move
* **A piece you move lands on a construction line anywhere along it.** Only
  a construction line's two ends, its middle and its crossings caught a
  moved piece. So a piece taken to a guide, or a piece grouped with a
  construction line, snapped to nothing, though the guide said construction
  lines snap along their whole length while moving too. Now, with no
  corner, end, midpoint or crossing in reach, the point you grabbed goes
  onto the line and slides along it freely. A corner or end on the line
  still wins when it's near. Open lines and seams catch it too, as they do
  when drawing.
* **A construction line grabbed along its length snaps by the spot you
  grabbed.** It snapped by whichever end, middle or corner of its group was
  nearest the grab, which on a long line could be far from the pointer.
  Grab it near an end or its middle to snap by that.
* **A resize grip lands on a construction line anywhere along it**, too.
  Resizing a group, the grip leaves the group's own lines alone: they
  stretch with it.
* A moved group's snapping point is found among all of its points. A group
  with more than 600 (a traced piece, say) dropped most of them first, so
  the point nearest the grab could be missing.

## 0.4.18 — 2026-11-21

### Node editing snaps the way drawing does
* **You can see what a node will snap to.** Dragging a node marks the snap
  points near it, as drawing does, and the status bar names the one it's on.
  Before, nothing showed until the node had already snapped.
* **Corners win over stitch holes.** A node brought towards a stitched
  piece's corner from inside landed on the corner's stitch hole, which sits
  just inside it. A hole now takes a node only when it's brought right onto
  the hole.
* **A node lands on a line anywhere along it**, where its own edge meets
  that line, so a line's end can be dragged onto the middle of another line.
  Before, only a line's ends, middle and crossings caught it.
* **A curve's middle is on the curve.** Its middle snap point was the
  middle of the straight line between its ends, out on the empty paper
  beside it.
* **Shift and the grid work as they do when drawing.** With Shift held, a
  node keeps to 0/45/90° but still stops on a line it meets; before, Shift
  turned snapping off. With **Grid** on, a node that isn't near anything
  snaps to the grid. **Ctrl** puts a node exactly where the pointer is.

## 0.4.17 — 2026-11-21

### Keeping in touch
* **Stitch Hero says when a newer version is out.** A few seconds after it
  opens, at most once a day, it looks at stitch-hero.com. When a newer
  version is out, a strip above the drawing says so, with **What's new**,
  **Download** and **Not now**. *Not now* puts that version off; a newer
  one is said again. **Help → Check for updates now** looks straight away
  and says the answer either way, and **Help → Tell me when a new version
  is out** turns the daily look off. The look sends nothing about you: it
  asks for one small file, saying only which version of Stitch Hero is
  asking.
* **What's new, the first time a new version opens:** these notes, for the
  version you have. **Help → What's new in this version** shows them again.
* **The way to the Stitch Hero group, from Help.** **Join the Stitch Hero
  group** opens it. **Report a problem…** and **Suggest a feature…** write
  the post for you, with the version of Stitch Hero and of Windows or macOS
  already in it: copy, open the group, paste. The About box links the group
  and stitch-hero.com.

### Installing
* **The guide's install steps are for downloading from stitch-hero.com**,
  with Windows' *More info → Run anyway* and the Mac's *Privacy & Security →
  Open Anyway* (right-click → Open no longer does it on recent macOS).

### Building and testing
* **A test run on Windows or a Mac leaves your own settings and library
  alone.** The tests kept settings in files of their own only where the
  system keeps settings in files (Linux): on Windows a run read and wrote
  Stitch Hero's settings in the registry, and could reach the library in
  Documents. Now every run has its own settings and folders, everywhere.
* **A build checks it can make https connections and has these notes**, so
  one that could never hear of a newer version doesn't ship.

## 0.4.16 — 2026-11-21

### The library: a pattern in it is the drawing
* **Open a pattern from the library and it is that piece's drawing.** It
  used to open as an *Untitled* copy, and changes reached the library only
  through *Use the open drawing*. Now the title bar shows the piece's
  name — *Card wallet (in the library)* — and **Save** (`Ctrl+S`) keeps
  your changes there, with no file to choose. Closing with changes not
  saved asks to save them to the piece; renamed in the library, the title
  bar follows. **Open a copy** is still there, for starting another piece
  from an old pattern.
* **Add to library does the same.** Once added, the drawing is the
  piece's: saving it keeps it in the library. A drawing added from a file
  of its own is saved in both — *wallet.leathercad.json (also in the
  library)* — and is the piece's again when that file is opened another
  day. **Save As** makes a drawing of its own; the piece keeps what was
  last saved there.
* **Every day's pattern is kept.** What a piece had is set aside in its
  *earlier* folder when it was put in on an earlier day — the last of each
  day it was saved on — where before only a pattern more than 24 hours old
  was kept, so saving in the evening and again next morning lost one.
* **A 1:1 PDF older than its pattern says so**, in amber, and *Make…*
  becomes **Make again…**.
* A piece deleted from the library while its pattern is open leaves the
  drawing open, saved nowhere — the title bar and the next Save say so.
  Work recovered after a crash is still the piece's.

### Round circles and arcs
* **Circles, arcs and rounded corners are drawn as true curves** on the
  canvas, round however far you zoom in. They used to be drawn as the
  fine polyline they are worked out on — a 5 mm circle in 25 straight
  sides — whose corners showed close up. The arc tools' previews are true
  arcs too, and so is the dotted line over an unsewn curved edge. (What is
  cut never had the corners: SVG and DXF exports write true arcs, unless
  kerf compensation is on.)

### Photos and a 3D view in the library
* **A piece with no picture asks for photos** of the finished piece: a
  large space, click it to choose them, or drop them on it (**Add
  photos…** and `Ctrl+V` still work).
* **Keep this view in the library**, in the 3D assembly preview, keeps a
  picture of the model as you have it folded and turned — the leather
  alone, without fold lines, letters or hole marks — with the drawing's
  piece; a drawing not in the library yet is put in first, once you say
  so. 3D views are marked **3D**, and your photos come first: the first
  photo you add takes the cover from a 3D view, unless you chose that
  view as the cover.

## 0.4.15 — 2026-11-20

### Drawing as in Fusion
* **Type sizes while you draw.** After the first click, boxes beside the
  cursor show what the next click gives: a line's **length** and
  **angle**, a box's **width** and **height**, a circle's **diameter**
  (Tab: radius), a centre arc's **radius** and then **sweep**, an
  offset's **distance**. Type, `Tab` to the next box, `Enter` to place. A
  typed value holds while the mouse sets the rest, and is read as
  Properties reads one: units, sums and parameters.
* **Keys go into the boxes.** Typing "25" in the middle of a line used to
  switch to the 2-point circle and then the centre arc and lose the line;
  `Backspace` deleted the shape drawn before. While the boxes show, keys
  are theirs.
* **Tools stay on.** Every drawing tool stays on after a shape, for the
  next one; `Esc` goes back to Select. (Text and the tracing photo's
  calibration still go back by themselves.)
* **Lines keep going.** The Line tool adds a segment with each click and
  makes **one joined path**; click its start to close it into a stitched
  piece, `Enter` or `Esc` to end it open. With Drag to draw on, a drag is
  still one line.
* **Right-click** while a shape is half-drawn to **Finish** or **Cancel**
  it, or **Close outline** a line. With nothing half-drawn, the menu's
  first entry goes back to the Select tool.
* **Hold Ctrl** to place a point exactly where the cursor is, without
  snapping.
* While a line is drawn the status bar gives its **length and angle**
  (it gave the width and height of its box).

## 0.4.14 — 2026-11-20

### Putting pieces together
* **Stack the pieces of a pattern and see the finished thing.** The 3D
  assembly preview has a new **Pieces** tab. Show the main piece — a
  wallet's shell — and tick the other pieces of the drawing on to it:
  they are placed against it, and shown lying on each other as they
  will be sewn, each at its own thickness and in its own leather. Line a piece up
  with the main piece or another piece (left, centre, right, bottom,
  middle, top), type how far across and up it lies, turn it, turn it over
  grain down, put it underneath, or lay it where it is drawn. **Pull
  apart** lifts the layers to show what lies on what, **See through**
  shows a piece under others, and clicking a piece in the picture chooses
  it. The drawing stays the cutting pattern: where a piece lies in the
  stack is kept with the piece, so moving pieces or **Nest on sheet**
  changes nothing in it.
* **Every piece has its leather.** A piece's **Leather thickness** is in
  Properties and on the right-click menu, and is saved with the drawing,
  like the place it lies at and the leather it is shown in.
* **Fold the whole thing.** A stack folds along the main piece's fold
  lines, every layer round the bend together, and the fold is given the
  room the pile inside it needs. The generated bifold in 1.6 mm leather
  with 0.8 mm lining and pockets closes over 8 mm of leather: its spine
  bends at 4 mm, takes 15.08 mm of the shell, and the wallet comes out
  97.66 × 90 × 11.2 mm.
* **The report says what the fold needs — in what to cut.** Pieces that
  reach into a bend, where leather cannot lie flat, are marked amber and
  told where to stop (*Stop them 7.54 mm from the fold line: make them
  5.54 mm narrower*; dashed lines on the flat stack show where each bend
  begins). A lining that goes round the inside of a fold has less far to
  go than the shell: *Cut it 3.77 mm shorter across the fold and it lies
  smooth and comes level when shut…* — and once it is, the report says so.
  It gives the size folded and, once every fold is shut, the most and
  least leather a needle goes through (windows and other cut-outs left
  out); and in red, by name and with how deep, anything still drawn
  through other leather where it ends up — two piles that meet, or a
  piece that crosses a fold and still runs into the leather there. A
  piece the fold only parts, like a strap laid across the end of a
  narrow flap's fold, is named in plain words, with how long a slit it
  would need.
* **At rest, and on the way.** Red is kept for what stays drawn through
  something else where the leather ends up, when a step of the folding
  is done. What the picture draws through something only on the way
  through a step is said plainly — *at rest they are clear* — because
  the preview turns each panel as one stiff piece, where leather gives.
* **Stitch holes through the layers.** Where pieces lie on each other the
  holes are checked hole against hole, and where a hole goes through a
  piece that has none there the report counts them: *The laser will not
  cut them in those pieces.* A lining with holes of its own that goes
  round a fold is read both ways — punched to meet lying flat, or
  punched to meet when shut — and the report says which reading the
  marks show and how many holes meet in each. Pieces the fold shuts on
  to each other, a pocket on each half, are checked as well. Each pair
  of pieces is told what it does with the other, so the pair to put
  right is named with its own distance, and a hole is red when anything
  on its needle line is out of line.
* **Generated wallets arrive put together.** The Bifold wallet and Card
  pocket stack generators and the Card holder and Slim card holder
  templates come as stacks, ready to look at. The bifold dialog asks for
  the **Shell leather** and the **Lining and pocket leather**, and **Make
  room for the fold** sizes the spine, the lining and the pockets for the
  bend those leathers need (210.28 mm shell, 206.51 mm lining, pockets
  ending at the edge of the bend, for 1.6 and 0.8 mm).
* **Generated folds close grain out.** The bifold's spine, the key tab's
  ring fold and the strap's turn-back now fold to the back, so the grain
  side ends up outside; they folded to the front.
* **Older versions.** 0.4.13 and earlier open a drawing made with stacks
  and show its pieces as before, but leave the thickness, the leather and
  the place of each piece out when they save it.
* **Esc no longer applies a half-typed number.** In the 3D assembly
  preview, a number still being typed when the window was shut reached
  the fold line with no undo step. It is now let go; what was changed
  before stays one undo step.

### Folds that close over other folds
* **A fold makes room for what is inside it — all of it.** The 3D assembly
  preview already bent a fold that closes flat at a bigger radius, to go
  round the layers folded in before it. But those layers can be too long
  for it as well: a flap as long as the panel it lies on, or a fold inside
  the fold, whose rounded end needs room. The preview used to draw the
  outer fold on its line, straight through them. Now the fold forms where
  it clears, a little further along the leather, and the fold line, the
  edges, the stitch holes and the hole check all go by where the leather
  really ends up.
* **The report says how far, and what to change.** *Fold C folds 3.14 mm
  off its line, towards panel 4, to go round what is folded inside it. To
  have it fold on its line, shorten panel 2 by 3.14 mm where it reaches
  into the fold.* The preview tries that on a copy of the piece before it
  says it, so the fold is on its line when you have done it. Where one
  panel isn't enough it names each — rolled up, a panel made shorter
  pushes the one folded on to it into the fold before: *… and then panel 1
  by 3.14 mm where it reaches into fold B* — and of a fold that closes
  over another one off its line it says only to put that one right first.
  The panels keep their numbers.
* **Where a fold can't move** — its bend would run off the leather or into
  the next fold, it is at the foot of a flap, or leather comes into it
  from both sides — the preview says so in red, with what to shorten if
  that puts it right. Panels drawn edge to edge (a net) fold where they
  are drawn, and say the same.
* **A fold across a hem says what it has always shown.** Leather folded in
  first and then folded across — a hem, a turned edge, a square folded in
  four — is shown cut along the second fold, its cut ends inside that
  fold's bend. The report now says so, and by how much to cut it back
  there to clear the bend: *Fold B runs across panels 1 and 3, which are
  folded inside it: each is shown cut along the fold and 4.71 mm into its
  bend. Cutting each back by that much at the fold clears it.*
* **What a moved fold brings down in other leather is said too.** A lip
  that came down just past a folded edge may come down in it once the
  fold forms further along: *With fold B off its line, panel 4 and fold A
  are drawn 1.5 mm into each other.*
* **A gap is in the way as much as a layer.** With a bend radius, a fold
  holds its two panels apart. A fold that closed over one took that gap
  for room, and a panel came out on the wrong side of another. It now goes
  round the fold inside and the gap it holds open; what ends short of the
  round of such a fold still lies in the gap.
* **Two tabs along one edge are no longer said to be too close.** Where
  two tabs stand side by side along one edge, each folding on its own
  stretch of one line, the preview warned *Folds A and B are 0.0 mm
  apart, but their bends need 3.1 mm between them*. Each bend is on its
  own tab and they never meet, so the warning is gone; two folds that
  do lie side by side are still warned of when they are too close.
* **Every length in the report is in the unit you work in** — the
  warnings too, which stayed in millimetres with inches chosen.
* **The preview follows a fold line you move.** With the preview open, a
  fold line moved in the drawing was not noticed until the preview was
  opened again.

### Fold allowance
* **The box says which angle it is adding for.** Fold allowance goes by
  the angle each fold line has, and a line just marked is a 90° fold —
  so a wallet's spine, marked and lengthened at once, got only half the
  length its fold needs (1.57 mm instead of 3.14 mm in 2 mm leather),
  and nothing said so. The box now names the angle of every fold line
  it found and where to change it (the **Angle** column of the 3D
  assembly preview), and the status bar says what was added, in the
  unit you work in: *Fold allowance: 1 fold of 180°, 3.14 mm added in
  all*.
* **A fold set to 0° adds nothing.** A fold left flat in the preview —
  its Angle set to 0° — was lengthened as if it were a 90° fold. It is
  now left alone, and the box says so.

## 0.4.13 — 2026-11-17

### A library of the things you've made
* **File → Library of finished pieces…** (`Ctrl+L`) keeps what you've
  made: for each piece its photographs, your notes, the pattern it was
  cut from and that pattern's 1:1 PDF. The pieces are shown as a wall of
  pictures, the latest first, with a search through names and notes.
* **Pictures** go in with *Add pictures…*, by dropping the files on the
  window, or by pasting. The first is the piece's cover, and any other can
  be made the cover; a piece with no picture yet is shown by its pattern,
  drawn small. A photograph taken with the phone upright is shown upright.
* **Name, date and notes** are kept as you type them: there is nothing to
  save.
* **File → Add to library…** files the drawing you have open, with a copy
  of the pattern as it stands. **Export PDF (1:1)** has a new box, *Keep a
  copy in the library*, that files the PDF with the pattern. In the
  library, *Open a copy* starts a new drawing from a piece's pattern
  (the library's own stays as it was), and *Make…* makes its 1:1 PDF
  again.
* **What a piece was made from isn't overwritten.** When a later pattern
  or PDF is filed with a piece, the one it had is moved to a folder called
  *earlier* in the piece's folder (unless it was only put there in the
  last day, and so was still being worked on).
* **It's a folder.** The library is *Stitch Hero Library* in your
  Documents: a folder for each piece, with its pictures, pattern and PDF
  as ordinary files. Back it up, or keep it in a folder that syncs between
  your computers (*Library folder…* moves it). Nothing is uploaded.
  *Delete entry…* sends a piece to the recycle bin.

### The 3D assembly preview looks like leather
* **Grain, flesh and edge.** The front of the leather (the side you draw
  on) has a pebbled grain, with a sheen that moves as you turn the piece;
  the back is the paler, fibrous flesh side; the cut edge is darker; and
  an edge throws a soft shadow on the layer under it. The grain lies where
  the pattern puts it, so it runs on from a panel round a bend to the next.
* **Leather colour.** Show the piece in natural, tan, chestnut, dark
  brown, black, burgundy, navy or olive. It changes the preview only —
  nothing is saved in the drawing — and the choice is remembered. Stitch
  holes, scored lines, panel numbers and fold lines switch to light
  colours on a dark leather.

### Do the stitch holes line up?
* **Every hole says so.** Where panels come to lie on each other when
  folded and both have stitch holes, the 3D preview now marks each of
  those holes, flat or folded: **green** if it lines up with the holes
  stacked with it, **red** if something in the stack is out of line, and
  **amber** if there is no hole there for it to meet. Before, only the
  near misses were marked; a hole that lined up looked like any other.
* **Which way it's out.** Beside a red hole, a red ring shows where the
  hole it should have met comes down.
* **Judged by the whole stack.** With three or more layers, a hole is red
  if any two holes stacked with it miss each other — so a fault between
  the lower layers shows on the top one, where you can see it.
* The report under the table counts each kind and says how far out the
  worst is, and which panels are involved. **Hole check**, under the view,
  hides the marks for a clean picture.
* The preview window is narrower, and fits a 1366-pixel-wide screen: the
  reminder of how to turn and zoom moved into the view.

### Fixed
* The pattern name offered for a print or a PDF no longer ends in
  ".leathercad" when the drawing was saved as `name.leathercad.json`.
* The pictures of the 3D assembly preview were missing from the guide
  inside the app (Help → User guide). They're there now.

## 0.4.12 — 2026-11-17

### The 3D assembly preview, rebuilt
* **Solid leather.** The piece folds as leather with its real thickness:
  bends are rounded, layers lie a thickness apart, and the front (the side
  you draw on) and the back are different colours. It's drawn right from
  every side — layers, standing flaps and all; the old view sorted flat
  panels by distance and often drew them in the wrong order.
* **Fold lines you can see.** Each is drawn on the leather and lettered,
  solid blue if it folds to the front and dashed magenta to the back, and
  the panels are numbered. Click a fold line to choose it: it lights up
  through whatever is folded over it. A fold line no longer has to be
  drawn exactly edge to edge to count, and folds that cross now fold.
* **A row for each fold line** — the step it folds at, the way it folds,
  how far and how tightly — instead of a row for each panel. Folds with
  the same step fold together; Earlier, Later and Flip work on the chosen
  fold; untick one to leave it flat.
* **Thickness and bend radius work.** They change the model, not just a
  number under it: the layers move apart, the bends open out, and each
  bend's **Leather used** follows — angle × (inside radius + ½ thickness),
  the sum Fold allowance adds. A fold can have a radius of its own.
* **The order of folding matters.** A fold that closes over leather folded
  in before it bends at the radius it needs to go round that leather, and
  the preview says which fold goes round what.
* **Step through it.** Fold a step at a time, drag the slider, or play it;
  the view follows the piece as it closes up.
* **Stitch holes that should meet and don't** are drawn red, with how far
  out they are.
* **What's on the piece is on the model**: stitch holes, cutouts, slits,
  and lines scored or engraved on it.
* **Saved with the drawing.** Step, direction, angle and radius are kept on
  the fold lines; a run of changes is one Undo in the main window, and the
  preview follows the drawing while it's open.
* Choose which piece to fold when several have fold lines (it opens on the
  selected one). Panels drawn edge to edge still fold up as a net.

## 0.4.11 — 2026-11-17

### The PDF's details page
* **The best fit, with an edge margin.** The cutting layout was a quick
  search with the pieces right up to the edge of the leather, so it
  differed from Nest on sheet's in layout and size. It's now a proper
  best-fit search, as Nest on sheet's smallest sheet does, a few seconds
  long, with an **Edge margin** you set on the export options page and the
  Space between pieces. A Nest on sheet layout on your drawing that holds
  every piece, keeps that margin and gap and needs no more leather is the
  one shown, so the page never shows worse than the drawing.
* **Cutting layout: As laid out with Nest on sheet.** A new choice on the
  export options page shows the sheet Nest on sheet drew, just as it is:
  its size, edge margin and the pieces on it (it says so when some
  aren't). Use it for a layout arranged by hand.

## 0.4.10 — 2026-11-17

### The PDF's details page
* **Every piece to cut is counted.** A piece drawn on top of another — a
  copy left on the original, a lining laid on its outer — was taken for a
  hole cut out of it: it wasn't counted, its leather was taken off the
  total, and it was printed inside the other piece. An outline inside
  another is now a piece of its own when it's nearly as big (85 % of it or
  more; a slot, a thumb hole, a window or a ring's middle is smaller), and
  it's printed as its own pattern piece. The same goes for the Job
  estimate and Nest on sheet.
* **How many of each.** The list of pieces has a Cut column: copies of a
  piece share a line. Unnamed copies share their number on the pattern
  pages too.
* **Only the irons you punched with.** Holes placed by hand and attached
  to a piece kept a 3.85 mm spacing in its settings that nobody punched,
  and the details page listed it. Their spacing is now measured from the
  holes. Attaching holes sets the piece's spacing to the one they're at.
* **A cutting layout.** Beside the scale squares, every piece is drawn on
  the smallest piece of leather it all fits on, numbered as in the list —
  like Nest on sheet on the drawing. A long thin strip (a wallet's pieces
  fitted on 110 × 757 mm) is passed over for a squarer piece unless it
  saves a lot of leather; Nest on sheet's smallest sheet does the same.
* The thread is left off the details page.

### Nest on sheet
* **Pieces are never nested stuck together.** A piece outlined with an
  open path drawn back round to its start (finished a little past it, as a
  traced or joined outline often is) counted as a line. Lying across
  another piece, it was nested stuck to it, as a fold line is. Lying
  alone, it was nested as a hollow outline that other pieces could land
  inside. Such a path is now a piece. A `Ctrl+G` group holding several
  pieces is split for nesting: each piece goes on its own, with what's on
  it. The PDF, the Job estimate and the leather-usage report count these
  pieces too.

### Drawing
* The Stitch line (seam) tool drew every seam at 3.85 mm, whatever iron
  the pattern was punched with. A seam now takes the punch and spacing set
  with Use for new shapes. Seams already drawn keep theirs: select one to
  see or change it.
* A triangle counts as a piece in the Job estimate.

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

# Stitch Hero — marketing screenshots

Every image here is a **real screenshot of the running application** (captured
by `tools/marketing_shots.py` — no mockups, no retouching). Regenerate any
time with:

```bash
python tools/marketing_shots.py
```

Suggested tagline for the page header:

> **Stitch Hero** — CAD for laser-cut leather. Draw the pattern; get stitch
> holes that match your pricking iron, hole-for-hole, on every piece.

---

## 01_hero_wallet.png — the workspace (hero image)

A complete bifold-wallet cutting sheet in the full workspace: outer shell,
interior panel, card pockets, T-pocket, coin pouch, key strap — **689 stitch
holes placed automatically**, plus a scored fold line and engraved lettering.
Layers map straight to laser jobs (cut / stitch / score / engrave) and export
to SVG or DXF.

**Webpage caption:**
> Design a whole project on one sheet. Each piece carries its own iron —
> oblique 3.85 mm on the shell, French 3.38 mm on the pockets, round holes on
> the strap — and the status bar counts every hole for you.

## 02_corner_perfection.png — spacing you can't get by hand

A strap end, close up. Most software spaces holes along the *curve*, so gaps
shrink wherever the path bends. Stitch Hero marches holes by **straight-line
(chord) distance — exactly what a pricking iron does** — so every gap around
the semicircular cap is identical, and the layout lands symmetric about the
apex. No lone hole drifting off a corner.

**Webpage caption:**
> Chord-spaced, corner-symmetric stitch holes — the gap your iron actually
> makes, even around a full 180° curve.

## 03_punch_styles.png — your iron, your holes

The four punch families, side by side on identical squares: **round, oblique,
French, diamond**. Pick the style and pitch that matches the iron on your
bench (standard pitches from 2.0 to 5.0 mm, or type your own) and the holes
render — and export — with the true tooth shape.

**Webpage caption:**
> Round Ø, oblique slit, French slant, or diamond chisel — pick your punch,
> pick your pitch, done.

## 04_dark_studio.png — comfortable to work in

The same workspace in the built-in dark theme: a curved strap end, a diamond-
stitched loop and a round-hole concho backer. Curves get the same treatment as
straights — the holes march right around the taper.

**Webpage caption:**
> A proper dark mode for late-night bench sessions. Curves included.

## 05_registration.png — front and back, hole-for-hole

One click of **Make back piece** mirrors a panel and regenerates its stitching
so the two pieces line up **hole-for-hole** — laser one from the front and one
from the back and they still register. Deterministic stitching means two
pieces with the same outline always get byte-identical hole layouts.

**Webpage caption:**
> Mirror a piece and the holes come with it — front and back register
> perfectly, no matter which side you laser.

## 06_stitch_settings.png — pick your iron on a live pattern

The full workspace with two pieces loaded (front panel + card pocket) and one
selected, the **Properties** panel scrolled to the **Stitching** group. This is
where you dial the job in: **Punch style** (round / oblique / French / diamond),
**Pitch** shown in both mm and SPI (here 3.85 mm ≈ 6.6 SPI), inset from the
edge, slit length and angle, saddle-stitch rows, backstitch, symmetry and corner
handling — and the pattern re-fits live as you change any of them.

**Webpage caption:**
> Set the punch style and pitch to match your iron, and the holes re-space
> instantly — 3.85 mm oblique here, or type any pitch you own.

## 07_cost_estimator.png — quote a job in one click

The **Job estimate** dialog over a real 6-piece wallet (611 holes). From the
drawing alone it works out pieces, stitch holes, thread length, cut/score/
engrave lengths, leather used vs. leather to *buy* (accounting for hide yield),
layout waste, and — with your prices plugged in — a full cost breakdown with a
total. Everything a maker needs to quote a job.

**Webpage caption:**
> Know the number before you cut: thread, leather and waste turned into a real
> cost — this wallet, $9.96 in materials.

---

### Selling points to repeat on the page

* **Pricking-iron-accurate spacing** (chord marching, not arc-length) — the
  headline differentiator; no mainstream tool does this.
* **Registration across pieces** — duplicated / mirrored panels stitch up
  hole-for-hole.
* Real drawing tools: rectangles, circles, polygons, bezier pen, arcs, trim,
  fillet, boolean ops, node editing, smart snapping, photo tracing.
* Laser-ready output: SVG + DXF with layers mapped to cut / score / engrave,
  kerf compensation, 1:1 printing.
* Extras worth a bullet: job cost estimator, parts library, seam checker,
  dark theme, autosave + crash recovery, Windows / macOS downloads.

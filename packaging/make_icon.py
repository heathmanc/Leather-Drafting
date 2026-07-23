#!/usr/bin/env python3
"""Generate the Stitch Hero app icon.

The mark is the app's own signature: a run of evenly spaced, chord-marched
oblique pricking-iron slits laid out along an "S" (for Stitch) on a warm
leather-to-amber field, finished with a needle + thread tail.

    python packaging/make_icon.py

Outputs (into packaging/icons/ and leathercad_app/resources/):
    icon.svg              vector master
    icon_1024.png ... 16  raster sizes
    StitchHero.ico        Windows  (PyInstaller EXE)
    StitchHero.icns       macOS    (PyInstaller BUNDLE)
    appicon.png (256)     in-app window icon

Rendering SVG -> PNG uses the same pre-installed Chromium as the manual
builder, so no extra rasteriser is required.
"""

from __future__ import annotations

import glob
import math
import os
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "packaging" / "icons"
RES = ROOT / "leathercad_app" / "resources"

SIZE = 1024


def _find_chromium() -> str | None:
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for pat in (f"{base}/chromium-*/chrome-linux/chrome",
                f"{base}/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                f"{base}/chromium-*/chrome-win/chrome.exe"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


# ---- geometry -------------------------------------------------------------

def _s_centreline(n: int, cx: float, y0: float, y1: float, amp: float):
    """Densely sample the S centreline (single-valued in y)."""
    pts = []
    for i in range(n):
        u = i / (n - 1)
        y = y0 + (y1 - y0) * u
        x = cx + amp * math.sin(2 * math.pi * u)   # one clean wiggle -> "S"
        pts.append((x, y))
    return pts


def _path_d(pts) -> str:
    d = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"]
    d += [f"L {x:.1f} {y:.1f}" for x, y in pts[1:]]
    return " ".join(d)


def build_svg() -> str:
    cx = SIZE / 2
    s = _s_centreline(160, cx, 300, 724, 150)
    d = _path_d(s)

    # top end of the S -- where the needle threads in
    hx, hy = s[0]
    na = 150.0  # needle angle (deg), pointing down-right into the top hook
    ndx, ndy = math.cos(math.radians(na)), math.sin(math.radians(na))
    tip = (hx + ndx * 40, hy + ndy * 40)
    tail = (hx - ndx * 300, hy - ndy * 300)
    eye = (hx - ndx * 250, hy - ndy * 250)

    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='{SIZE}' height='{SIZE}'
     viewBox='0 0 {SIZE} {SIZE}'>
  <defs>
    <linearGradient id='bg' x1='0' y1='0' x2='0.3' y2='1'>
      <stop offset='0' stop-color='#5c300f'/>
      <stop offset='0.6' stop-color='#8a4a17'/>
      <stop offset='1' stop-color='#b56a22'/>
    </linearGradient>
    <linearGradient id='strap' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0' stop-color='#f3e6c8'/>
      <stop offset='1' stop-color='#dcc292'/>
    </linearGradient>
    <linearGradient id='steel' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0' stop-color='#fbfcfe'/>
      <stop offset='0.5' stop-color='#c4ccd6'/>
      <stop offset='1' stop-color='#8892a0'/>
    </linearGradient>
    <filter id='sh' x='-30%' y='-30%' width='160%' height='160%'>
      <feDropShadow dx='0' dy='9' stdDeviation='12'
                    flood-color='#2a1403' flood-opacity='0.5'/>
    </filter>
  </defs>

  <rect x='40' y='40' width='{SIZE-80}' height='{SIZE-80}' rx='214'
        fill='url(#bg)'/>
  <rect x='72' y='72' width='{SIZE-144}' height='{SIZE-144}' rx='180'
        fill='none' stroke='#3a1d06' stroke-opacity='0.4'
        stroke-width='4' stroke-dasharray='2 22' stroke-linecap='round'/>

  <!-- leather S strap: cream fill, dark edge, stitched centre -->
  <g filter='url(#sh)'>
    <path d='{d}' fill='none' stroke='#6b3a15' stroke-width='176'
          stroke-linecap='round' stroke-linejoin='round'/>
    <path d='{d}' fill='none' stroke='url(#strap)' stroke-width='156'
          stroke-linecap='round' stroke-linejoin='round'/>
    <path d='{d}' fill='none' stroke='#7a4a1e' stroke-width='16'
          stroke-linecap='round' stroke-dasharray='34 30'
          stroke-dashoffset='17'/>
  </g>

  <!-- needle + thread threading the top of the S -->
  <path d='M {tail[0]:.1f} {tail[1]:.1f} Q {tail[0]+60:.1f} {tail[1]+120:.1f}
           {hx:.1f} {hy:.1f}' fill='none' stroke='#efe0be'
        stroke-width='12' stroke-linecap='round' filter='url(#sh)'/>
  <g filter='url(#sh)'>
    <line x1='{tail[0]:.1f}' y1='{tail[1]:.1f}'
          x2='{tip[0]:.1f}' y2='{tip[1]:.1f}'
          stroke='url(#steel)' stroke-width='30' stroke-linecap='round'/>
    <ellipse cx='{eye[0]:.1f}' cy='{eye[1]:.1f}' rx='8' ry='20'
             transform='rotate({na-90:.1f} {eye[0]:.1f} {eye[1]:.1f})'
             fill='#4a3a22'/>
  </g>
</svg>"""


# ---- rasterise ------------------------------------------------------------

def render_png(svg: str, out: Path, px: int) -> None:
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>html,body{{margin:0}}svg{{display:block;"
            f"width:{px}px;height:{px}px}}</style></head><body>{svg}</body></html>")
    tmp = ICONS / "_icon_tmp.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as pw:
            exe = _find_chromium()
            browser = (pw.chromium.launch(executable_path=exe) if exe
                       else pw.chromium.launch())
            pg = browser.new_page(viewport={"width": px, "height": px},
                                  device_scale_factor=1)
            pg.goto(tmp.as_uri(), wait_until="networkidle")
            pg.screenshot(path=str(out), omit_background=True,
                          clip={"x": 0, "y": 0, "width": px, "height": px})
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    RES.mkdir(parents=True, exist_ok=True)

    svg = build_svg()
    svg_path = ICONS / "icon.svg"
    svg_path.write_text(svg, encoding="utf-8")

    master = ICONS / "icon_1024.png"
    render_png(svg, master, SIZE)
    base = Image.open(master).convert("RGBA")

    sizes = [512, 256, 128, 64, 48, 32, 16]
    imgs = {SIZE: base}
    for s in sizes:
        im = base.resize((s, s), Image.LANCZOS)
        imgs[s] = im
        im.save(ICONS / f"icon_{s}.png")

    # Windows .ico (multi-size) and macOS .icns
    imgs[256].save(ICONS / "StitchHero.ico",
                   sizes=[(16, 16), (32, 32), (48, 48), (64, 64),
                          (128, 128), (256, 256)])
    base.save(ICONS / "StitchHero.icns")

    # in-app window icon
    imgs[256].save(RES / "appicon.png")

    print("icon written:")
    for p in sorted(ICONS.glob("StitchHero.*")) + [RES / "appicon.png"]:
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

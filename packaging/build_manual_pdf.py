#!/usr/bin/env python3
"""Render the README into a nice downloadable PDF manual.

    python packaging/build_manual_pdf.py [SOURCE.md] [OUTPUT.pdf]

Defaults: README.md -> docs/Stitch-Hero-Manual.pdf

Markdown -> styled HTML -> Chromium (Playwright) print-to-PDF, so tables,
code blocks, PNGs and the SVG diagram all render with real browser fidelity.
Relative image paths (docs/...) resolve because the temp HTML is written at
the repo root.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def _find_chromium() -> str | None:
    """Locate a usable Chromium, preferring a pre-installed one.

    Playwright's bundled-browser version can drift from what's on disk
    (e.g. a CI image ships build 1194 but the pip wheel wants 1228). When
    that happens ``launch()`` fails with "Executable doesn't exist"; pointing
    it at the on-disk binary via ``executable_path`` sidesteps the mismatch.
    Returns ``None`` to let Playwright use its own managed browser.
    """
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    patterns = [
        f"{base}/chromium-*/chrome-linux/chrome",
        f"{base}/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        f"{base}/chromium-*/chrome-win/chrome.exe",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.5; color: #1c2024; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1, h2, h3, h4 { line-height: 1.25; color: #11151a; margin: 1.4em 0 .5em; }
h1 { font-size: 24pt; margin-top: 0; border-bottom: 2px solid #d0d4da;
     padding-bottom: .25em; }
h2 { font-size: 16pt; border-bottom: 1px solid #e2e5ea; padding-bottom: .2em;
     page-break-after: avoid; }
h3 { font-size: 12.5pt; page-break-after: avoid; }
p, ul, ol, table, pre, blockquote { margin: .55em 0; }
a { color: #1b6ec2; text-decoration: none; }
code { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
       font-size: 9pt; background: #f2f3f5; padding: .1em .35em;
       border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e2e5ea; border-radius: 6px;
      padding: 10px 12px; overflow-x: auto; page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.6pt; line-height: 1.45; }
blockquote { border-left: 3px solid #cdd2d8; margin-left: 0; padding-left: 12px;
             color: #4a5058; }
img { max-width: 100%; height: auto; display: block; margin: .6em auto;
      border: 1px solid #e6e8ec; border-radius: 6px; page-break-inside: avoid; }
table { border-collapse: collapse; width: 100%; font-size: 9.3pt;
        page-break-inside: avoid; }
th, td { border: 1px solid #d7dbe0; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #f2f4f7; }
tr:nth-child(even) td { background: #fafbfc; }
hr { border: 0; border-top: 1px solid #e2e5ea; margin: 1.4em 0; }
"""

FOOTER = (
    '<div style="width:100%;font-size:8px;color:#8a9099;'
    'padding:0 16mm;font-family:Helvetica,Arial,sans-serif;'
    'display:flex;justify-content:space-between;">'
    '<span>Stitch Hero — user manual</span>'
    '<span>Page <span class="pageNumber"></span> of '
    '<span class="totalPages"></span></span></div>'
)


def build(src: Path, out: Path) -> None:
    html_body = markdown.markdown(
        src.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists", "attr_list",
                    "toc", "md_in_html"],
    )
    page = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body>{html_body}</body></html>")
    tmp = ROOT / "_manual_tmp.html"       # at repo root so docs/* resolve
    tmp.write_text(page, encoding="utf-8")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            exe = _find_chromium()
            browser = pw.chromium.launch(executable_path=exe) if exe \
                else pw.chromium.launch()
            pg = browser.new_page()
            pg.goto(tmp.as_uri(), wait_until="networkidle")
            pg.pdf(path=str(out), format="A4", print_background=True,
                   display_header_footer=True, header_template="<span></span>",
                   footer_template=FOOTER,
                   margin={"top": "18mm", "bottom": "20mm",
                           "left": "16mm", "right": "16mm"})
            browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "README.md"
    out = (Path(sys.argv[2]) if len(sys.argv) > 2
           else ROOT / "docs" / "Stitch-Hero-Manual.pdf")
    build(src, out)

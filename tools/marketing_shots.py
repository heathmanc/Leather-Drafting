#!/usr/bin/env python3
"""Render real-app marketing screenshots for the Stitch Hero webpage.

    python tools/marketing_shots.py

Every image is a genuine grab of the running application (offscreen QPA) —
no mockups: the documents are staged with the model API, opened in the real
MainWindow, and captured with QWidget.grab().

Outputs into docs/marketing/:
    01_hero_wallet.png        full window, light — bifold wallet cutting sheet
    02_corner_perfection.png  canvas close-up — symmetric corner stitching
    03_punch_styles.png       canvas — round / oblique / french / diamond
    04_dark_studio.png        full window, dark theme — curved pieces
    05_registration.png       canvas — front + mirrored back, holes aligned
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication            # noqa: E402
from PySide6.QtCore import QPointF                   # noqa: E402
from PySide6.QtGui import QPainter, QColor           # noqa: E402

from leathercad.document import Document             # noqa: E402
from leathercad.geometry import Vec2                 # noqa: E402
from leathercad.shapes import (Rectangle, Circle, Polygon, PathShape,
                               Transform)            # noqa: E402
from leathercad.stitchsettings import StitchSettings # noqa: E402
from leathercad.irons import geometry_for            # noqa: E402

OUT = ROOT / "docs" / "marketing"


def _st(pitch: float, style: str, inset: float = 3.5) -> StitchSettings:
    """StitchSettings for a punch style at a pitch, geometry filled in the
    same way the Stitching panel does it."""
    g = geometry_for(style, pitch)
    return StitchSettings(enabled=True, pitch_mm=pitch, inset=inset,
                          punch_style=style, hole_style=g["hole_style"],
                          hole_diameter=g["hole_diameter"],
                          slit_length=g["slit_length"],
                          slit_angle=g["slit_angle"])


def _label(doc: Document, text: str, x: float, y: float, size: float = 6.0):
    """Centred engraved lettering (the app's own Text tool output)."""
    from leathercad.text import TextShape
    from leathercad_app.canvas import bake_text_contours
    contours = bake_text_contours(text, "Sans", size)
    xs = [p.x for c in contours for p in c] or [0.0]
    w = max(xs) - min(xs)
    doc.texts.append(TextShape(
        text=text, contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
        size=size, font_family="Sans",
        transform=Transform(x=x - w / 2.0, y=y),
        layer="Engrave"))


def _open(doc: Document, w: int = 1680, h: int = 1050, dark: bool = False):
    from leathercad_app.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    win = MainWindow(doc)
    win.act_dark.setChecked(dark)
    win.line_width_spin.setValue(1.6)    # crisp strokes regardless of settings
    win.resize(w, h)
    win.show()
    for _ in range(6):
        app.processEvents()
    win.canvas.fit_to_content()
    for _ in range(6):
        app.processEvents()
    return app, win


def _save_window(win, name: str):
    pm = win.grab()
    pm.save(str(OUT / name))
    print(f"  {name}  {pm.width()}x{pm.height()}")


def _save_canvas(win, name: str):
    pm = win.canvas.viewport().grab()
    pm.save(str(OUT / name))
    print(f"  {name}  {pm.width()}x{pm.height()}")


def _close(app, win):
    win.act_dark.setChecked(False)       # don't leave dark mode in QSettings
    win.close()
    app.processEvents()


# ---------------------------------------------------------------------------
# 1. Hero: a complete bifold-wallet cutting sheet
# ---------------------------------------------------------------------------
def shot_hero():
    doc = Document("Bifold wallet — cutting sheet")
    add = doc.add_shape
    # outer shell + interior panel
    add(Rectangle(width=212, height=95, corner_radius=9,
                  transform=Transform(x=0, y=128), layer="Cut",
                  stitch=_st(3.85, "oblique"), name="Outer shell"))
    add(Rectangle(width=204, height=88, corner_radius=8,
                  transform=Transform(x=0, y=22), layer="Cut",
                  stitch=_st(3.85, "oblique"), name="Interior panel"))
    # fold line (scored, not cut)
    doc.add_shape(PathShape(points=[Vec2(0, -26), Vec2(0, 70)],
                            close_path=False,
                            transform=Transform(x=0, y=128), layer="Score",
                            stitch=None, name="Fold"))
    # card pockets (left column)
    for i in range(2):
        add(Rectangle(width=98, height=60, corner_radius=6,
                      transform=Transform(x=-168, y=98 - i * 74), layer="Cut",
                      stitch=_st(3.38, "french", inset=3.0),
                      name=f"Card pocket {i + 1}"))
    # T-pocket + coin pouch (right column)
    add(Rectangle(width=98, height=70, corner_radius=6,
                  transform=Transform(x=168, y=92), layer="Cut",
                  stitch=_st(3.38, "french", inset=3.0), name="T-pocket"))
    add(Circle(rx=31, ry=31,
               transform=Transform(x=168, y=-2), layer="Cut",
               stitch=_st(3.0, "round", inset=3.0), name="Coin pouch"))
    # key strap (pill) with round holes
    add(Rectangle(width=112, height=24, corner_radius=11,
                  transform=Transform(x=-30, y=-64), layer="Cut",
                  stitch=_st(3.0, "round", inset=3.0), name="Key strap"))
    _label(doc, "BIFOLD No.2", 96, -70, size=7.5)

    app, win = _open(doc)
    _save_window(win, "01_hero_wallet.png")
    _close(app, win)


# ---------------------------------------------------------------------------
# 2. Corner perfection close-up
# ---------------------------------------------------------------------------
def shot_corner():
    # a strap end: slits wrap the semicircular cap at a perfectly even chord
    # pitch and land symmetric about the apex — the signature feature, close up
    doc = Document("Corner detail")
    doc.add_shape(Rectangle(width=76, height=26, corner_radius=13,
                            transform=Transform(x=0, y=0), layer="Cut",
                            stitch=_st(3.5, "oblique", inset=3.2)))
    app, win = _open(doc, w=1800, h=900)
    _save_canvas(win, "02_corner_perfection.png")
    _close(app, win)


# ---------------------------------------------------------------------------
# 3. Four punch styles, side by side
# ---------------------------------------------------------------------------
def shot_punches():
    doc = Document("Punch styles")
    styles = ["round", "oblique", "french", "diamond"]
    for i, s in enumerate(styles):
        x = -105 + i * 70
        doc.add_shape(Rectangle(width=46, height=46, corner_radius=7,
                                transform=Transform(x=x, y=10), layer="Cut",
                                stitch=_st(3.5, s, inset=4.0), name=s))
        _label(doc, s.upper(), x, -26, size=5.5)
    app, win = _open(doc, w=1800, h=900)
    _save_canvas(win, "03_punch_styles.png")
    _close(app, win)


# ---------------------------------------------------------------------------
# 4. Dark studio: curved pieces, dark theme, full window
# ---------------------------------------------------------------------------
def shot_dark():
    doc = Document("Curved key fob")
    # strap with a rounded tip (drop shape via filleted polygon)
    pts = [Vec2(-70, -14), Vec2(50, -14), Vec2(78, 0), Vec2(50, 14),
           Vec2(-70, 14)]
    doc.add_shape(Polygon(points=pts, corner_radius=10,
                          transform=Transform(x=-20, y=44), layer="Cut",
                          stitch=_st(3.38, "oblique", inset=3.2),
                          name="Strap end"))
    doc.add_shape(Circle(rx=27, ry=27,
                         transform=Transform(x=64, y=-38), layer="Cut",
                         stitch=_st(3.0, "round", inset=3.0),
                         name="Concho backer"))
    doc.add_shape(Rectangle(width=124, height=34, corner_radius=16,
                            transform=Transform(x=-58, y=-40), layer="Cut",
                            stitch=_st(3.85, "diamond", inset=3.5),
                            name="Loop"))
    app, win = _open(doc, dark=True)
    _save_window(win, "04_dark_studio.png")
    _close(app, win)


# ---------------------------------------------------------------------------
# 5. Registration: front + mirrored back, holes hole-for-hole
# ---------------------------------------------------------------------------
def shot_registration():
    doc = Document("Registration")
    # an asymmetric flap so the mirror is obvious
    pts = [Vec2(-55, -35), Vec2(55, -35), Vec2(55, 15), Vec2(20, 35),
           Vec2(-55, 35)]
    doc.add_shape(Polygon(points=pts, corner_radius=8,
                          transform=Transform(x=0, y=0), layer="Cut",
                          stitch=_st(3.85, "oblique"), name="Front"))
    app, win = _open(doc, w=1800, h=900)
    c = win.canvas
    it = next(i for i in c.scene_obj.items()
              if getattr(getattr(i, "model", None), "name", "") == "Front")
    c.scene_obj.clearSelection()
    it.setSelected(True)
    c.make_back_piece_selected()
    c.scene_obj.clearSelection()
    c.fit_to_content()
    for _ in range(4):
        app.processEvents()
    _save_canvas(win, "05_registration.png")
    _close(app, win)


# ---------------------------------------------------------------------------
# 6. Properties panel: choosing punch style + stitch pitch on a live pattern
# ---------------------------------------------------------------------------
def shot_stitch_settings():
    doc = Document("Card holder")
    # a couple of real pattern pieces on the sheet
    front = Rectangle(width=104, height=66, corner_radius=7,
                      transform=Transform(x=-8, y=44), layer="Cut",
                      stitch=_st(3.85, "oblique"), name="Front panel")
    doc.add_shape(front)
    doc.add_shape(Rectangle(width=98, height=60, corner_radius=6,
                            transform=Transform(x=-8, y=-40), layer="Cut",
                            stitch=_st(3.38, "french", inset=3.0),
                            name="Card pocket"))
    app, win = _open(doc, w=1680, h=1000)
    c = win.canvas

    # give the Properties dock room, then select a piece so its stitch settings
    # populate the panel
    win.parts_dock.hide()
    it = next(i for i in c.scene_obj.items()
              if getattr(getattr(i, "model", None), "name", "") == "Front panel")
    c.scene_obj.clearSelection()
    it.setSelected(True)
    win.properties.show_selection(c.selected_items())
    for _ in range(6):
        app.processEvents()

    # scroll the panel to the Stitching group (Punch style + Pitch dropdowns)
    scroll = win.properties_dock.widget()
    scroll.ensureWidgetVisible(win.properties.g_stitch, 0, 0)
    for _ in range(6):
        app.processEvents()
    _save_window(win, "06_stitch_settings.png")
    _close(app, win)


# ---------------------------------------------------------------------------
# 7. Job / material cost estimator
# ---------------------------------------------------------------------------
def shot_estimator():
    from leathercad.estimate import estimate_project
    doc = Document("Bifold wallet")
    add = doc.add_shape
    add(Rectangle(width=212, height=95, corner_radius=9,
                  transform=Transform(x=0, y=120), layer="Cut",
                  stitch=_st(3.85, "oblique"), name="Outer shell"))
    add(Rectangle(width=204, height=88, corner_radius=8,
                  transform=Transform(x=0, y=14), layer="Cut",
                  stitch=_st(3.85, "oblique"), name="Interior panel"))
    for i in range(2):
        add(Rectangle(width=98, height=60, corner_radius=6,
                      transform=Transform(x=-170, y=92 - i * 74), layer="Cut",
                      stitch=_st(3.38, "french", inset=3.0),
                      name=f"Card pocket {i + 1}"))
    add(Rectangle(width=98, height=70, corner_radius=6,
                  transform=Transform(x=170, y=88), layer="Cut",
                  stitch=_st(3.38, "french", inset=3.0), name="T-pocket"))
    add(Circle(rx=31, ry=31, transform=Transform(x=170, y=-6), layer="Cut",
               stitch=_st(3.0, "round", inset=3.0), name="Coin pouch"))

    app, win = _open(doc, w=1680, h=1000)

    # real numbers, priced up so the cost block appears
    est = estimate_project(doc, thickness_mm=3.4, tail_mm=150.0,
                           usable_pct=75.0, price_per_sqft=9.5,
                           price_thread_per_m=0.18)

    # the app's own reworked estimate dialog, exactly as _job_estimate shows it
    dlg = win._build_estimate_dialog(est, 75.0)
    dlg.adjustSize()
    for _ in range(4):
        app.processEvents()

    # lay the real dialog over the workspace with a soft drop shadow (no scrim
    # -- a modal box doesn't dim the window), so it reads like it's just open
    win_pm = win.grab()
    dlg_pm = dlg.grab()
    x = (win_pm.width() - dlg_pm.width()) // 2
    y = (win_pm.height() - dlg_pm.height()) // 2
    p = QPainter(win_pm)
    for i, alpha in ((10, 22), (6, 34), (3, 48)):        # layered shadow
        p.fillRect(x - i, y - i + 4, dlg_pm.width() + 2 * i,
                   dlg_pm.height() + 2 * i, QColor(20, 24, 32, alpha))
    p.drawPixmap(x, y, dlg_pm)
    p.end()
    win_pm.save(str(OUT / "07_cost_estimator.png"))
    print(f"  07_cost_estimator.png  {win_pm.width()}x{win_pm.height()}")
    _close(app, win)


if __name__ == "__main__":
    QApplication.instance() or QApplication([])   # fonts need a QGuiApplication
    OUT.mkdir(parents=True, exist_ok=True)
    print("rendering real-app marketing shots:")
    shot_hero()
    shot_corner()
    shot_punches()
    shot_dark()
    shot_registration()
    shot_stitch_settings()
    shot_estimator()
    print("done ->", OUT)

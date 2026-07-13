"""Light / dark theming.

Widget chrome is themed the standard Qt way: the Fusion style plus a palette.
Canvas-specific colours (paper, grid, axis, rulers) don't come from the
palette, so they live here as two swatch sets the canvas and rulers read
through their ``dark`` flag.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

# -- canvas swatches ---------------------------------------------------------
CANVAS = {
    False: {  # light
        "bg": QColor(250, 250, 248),
        "minor": QColor(230, 230, 226),
        "major": QColor(210, 210, 205),
        "axis": QColor(200, 160, 160),
    },
    True: {   # dark
        "bg": QColor(33, 34, 38),
        "minor": QColor(48, 50, 55),
        "major": QColor(64, 67, 74),
        "axis": QColor(150, 95, 95),
    },
}

RULER = {
    False: {
        "bg": QColor(246, 246, 244),
        "fg": QColor(110, 110, 112),
        "tick": QColor(150, 150, 152),
        "corner_css": "background:#f6f6f4;color:#6e6e70;font-size:9px;",
    },
    True: {
        "bg": QColor(42, 44, 48),
        "fg": QColor(168, 170, 175),
        "tick": QColor(110, 112, 118),
        "corner_css": "background:#2a2c30;color:#a8aaaf;font-size:9px;",
    },
}


def dark_palette() -> QPalette:
    p = QPalette()
    window = QColor(46, 47, 51)
    base = QColor(38, 39, 43)
    text = QColor(222, 224, 228)
    disabled = QColor(120, 122, 128)
    highlight = QColor(42, 130, 218)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, window)
    p.setColor(QPalette.ToolTipBase, QColor(60, 62, 66))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, window)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 90, 90))
    p.setColor(QPalette.Link, highlight)
    p.setColor(QPalette.Highlight, highlight)
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, disabled)
    p.setColor(QPalette.Mid, QColor(140, 142, 148))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


def apply_app_theme(dark: bool) -> None:
    """Style + palette for every widget in the application."""
    app = QApplication.instance()
    if app is None:
        return
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(dark_palette() if dark else
                   app.style().standardPalette())

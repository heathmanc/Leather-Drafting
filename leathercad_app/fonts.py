"""Bundled fonts.

Text baking must use a KNOWN, cleanly-extractable outline font, not whatever the
OS resolves a bare ``QFont()`` to (on macOS that's the system UI font, whose
glyphs don't decompose into tidy vectors). We ship DejaVu Sans and register it at
startup so lettering looks the same on every machine; the baked contours are also
stored in the model, so saved files stay laser-safe even where the font is
missing.
"""

from __future__ import annotations

from pathlib import Path

# The family the bundled TTFs register under, and the app-wide text default.
DEFAULT_FAMILY = "DejaVu Sans"

_FONT_FILES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")

_registered = False
_default_family = DEFAULT_FAMILY


def _fonts_dir() -> Path:
    """The bundled fonts directory (works in a source checkout and a PyInstaller
    build -- same idiom as ``mainwindow.app_icon``)."""
    return Path(__file__).resolve().parent / "resources" / "fonts"


def register_bundled_fonts() -> str:
    """Register the bundled TTFs with Qt once and return the default family.

    Safe to call repeatedly (guarded by a module flag). Must run after a
    ``QApplication`` exists and before the first text bake."""
    global _registered, _default_family
    if _registered:
        return _default_family
    from PySide6.QtGui import QFontDatabase   # static in Qt6/PySide6
    fam = DEFAULT_FAMILY
    for name in _FONT_FILES:
        path = _fonts_dir() / name
        if not path.exists():
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if fams:
            fam = fams[0]              # both TTFs share the "DejaVu Sans" family
    _default_family = fam
    _registered = True
    return _default_family


def default_family() -> str:
    """The default text family (the bundled one once registered)."""
    return _default_family

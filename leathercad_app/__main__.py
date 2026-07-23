"""Entry point:  python -m leathercad_app"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from leathercad.document import Document
from leathercad.shapes import Rectangle, Transform
from leathercad.stitchsettings import StitchSettings
from .mainwindow import MainWindow


def _starter_document() -> Document:
    """A small starter piece so the window isn't empty on first launch."""
    doc = Document("Untitled")
    doc.add_shape(Rectangle(
        width=90, height=60, corner_radius=8,
        transform=Transform(x=0, y=0),
        stitch=StitchSettings(pitch_mm=3.85, inset=3.5, hole_style="slit"),
        layer="Cut"))
    return doc


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("Stitch Hero")
    from . import fonts                   # register bundled fonts before any bake
    fonts.register_bundled_fonts()
    from .mainwindow import app_icon
    app.setWindowIcon(app_icon())
    doc = None
    if len(argv) > 1 and argv[1].endswith(".json"):
        doc = Document.load(argv[1])
    win = MainWindow(doc or _starter_document())
    win.canvas.fit_to_content()

    # Crash safety: unhandled errors are logged + auto-saved + reported in a
    # dialog instead of dying in a hidden console; a leftover autosave from a
    # crash is offered back on the next launch.
    from .robustness import CrashHandler
    win._crash_handler = CrashHandler(win).install()

    if "--smoke" in argv:                # build verification: open + exit clean
        return 0
    if doc is None:                      # don't offer over an explicit file
        win.maybe_recover_autosave()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

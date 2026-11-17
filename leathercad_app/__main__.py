"""Entry point:  python -m leathercad_app"""

from __future__ import annotations

import os
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


def _smoke() -> int:
    """Build verification for a packaged app, once its main window has
    opened: the windows that are only loaded when asked for are in the
    bundle, photographs can be read, and the user guide has its pictures.

    Returns the exit code -- 0, or a number that says which check failed,
    with the reason written to ``smoke-failure.txt`` where it was run from.
    Nothing is raised: an error here would go to the crash dialog, and the
    build would wait on that for ever."""
    import re
    import tempfile
    import traceback

    def failed(code: int, why: str) -> int:
        try:
            with open("smoke-failure.txt", "w", encoding="utf-8") as fh:
                fh.write(why)
        except OSError:
            pass
        return code

    try:
        from PySide6.QtGui import QImageReader
        from . import helpdialog, preview3d      # noqa: F401
        from .librarywindow import LibraryWindow, Thumbs
        with tempfile.TemporaryDirectory() as tmp:
            LibraryWindow(root=os.path.join(tmp, "library"),
                          thumbs=Thumbs(os.path.join(tmp, "thumbs"))).close()
        can = {bytes(f).decode().lower()
               for f in QImageReader.supportedImageFormats()}
        if not {"jpg", "png"} <= can:
            return failed(3, "This build can't read JPEG and PNG pictures: "
                             f"it reads {', '.join(sorted(can))}")
        guide = helpdialog.guide_path()
        shown = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)",
                           guide.read_text(encoding="utf-8"))
        gone = [n for n in shown if not (guide.parent / n).is_file()]
        if gone or not shown:
            return failed(4, "The user guide's pictures aren't in this "
                             f"build: {', '.join(gone) or 'it shows none'}")
    except Exception:
        return failed(2, traceback.format_exc())
    return 0


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
        return _smoke()
    if doc is None:                      # don't offer over an explicit file
        win.maybe_recover_autosave()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

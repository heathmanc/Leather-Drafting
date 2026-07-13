"""Crash safety: friendly error dialogs, an error log, and emergency saves.

A hobbyist sharing this app with family can't ask them to read a console
traceback. So: every unhandled exception is (1) appended to an error log in
the app-data folder, (2) answered with an emergency autosave of the open
document, and (3) reported in a plain-language dialog — and the app keeps
running. The autosave file doubles as crash recovery: if it still exists at
the next launch (it is removed on a clean exit), the app offers to restore it.
"""

from __future__ import annotations

import datetime
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def app_data_dir() -> Path:
    """Writable per-user data folder (autosave + error log live here)."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation)
    p = Path(base or (Path.home() / ".leather-drafting"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class CrashHandler:
    """sys.excepthook that logs, emergency-saves, and shows a friendly dialog.

    ``interactive=False`` (used by tests / headless runs) skips the dialog but
    still logs and saves. Re-entrant failures fall back to the default hook.
    """

    def __init__(self, window=None, interactive: bool = True,
                 data_dir: Path | None = None):
        self.window = window
        self.interactive = interactive
        self.data_dir = Path(data_dir) if data_dir else None
        self._previous = None
        self._handling = False

    # -- paths -----------------------------------------------------------
    def _dir(self) -> Path:
        if self.data_dir is not None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            return self.data_dir
        return app_data_dir()

    def log_path(self) -> Path:
        return self._dir() / "error.log"

    # -- hook --------------------------------------------------------------
    def install(self) -> "CrashHandler":
        self._previous = sys.excepthook
        sys.excepthook = self.handle
        return self

    def handle(self, exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            (self._previous or sys.__excepthook__)(exc_type, exc, tb)
            return
        if self._handling:          # an error while handling an error
            sys.__excepthook__(exc_type, exc, tb)
            return
        self._handling = True
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
            self._log(text)
            sys.stderr.write(text)              # keep the console trail too
            saved = self._emergency_save()
            if self.interactive:                # pragma: no cover - GUI dialog
                self._show_dialog(exc, saved)
        finally:
            self._handling = False

    # -- pieces ------------------------------------------------------------
    def _log(self, text: str) -> None:
        try:
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            with open(self.log_path(), "a", encoding="utf-8") as fh:
                fh.write(f"\n=== {stamp} ===\n{text}")
        except OSError:
            pass

    def _emergency_save(self) -> bool:
        """Snapshot the open document into the autosave file (crash recovery)."""
        win = self.window
        try:
            if win is not None and hasattr(win, "write_autosave"):
                win.write_autosave()
                return True
        except Exception:
            pass
        return False

    def _show_dialog(self, exc, saved: bool) -> None:  # pragma: no cover - GUI
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is None:
                return
            box = QMessageBox(self.window)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Something went wrong")
            box.setText("Leather-Drafting hit an unexpected error, but it is "
                        "still running." +
                        ("<br><br><b>Your work was just auto-saved.</b>"
                         if saved else ""))
            box.setInformativeText(
                f"{type(exc).__name__}: {exc}<br><br>"
                f"Details were written to:<br><code>{self.log_path()}</code>")
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()
        except Exception:
            pass

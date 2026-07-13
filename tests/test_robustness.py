"""Crash safety: error logging, emergency save, autosave, crash recovery."""

import json
import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _win(tmp_path):
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    doc.add_shape(Rectangle(width=33, height=22, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = MainWindow(doc)
    win.autosave_dir = str(tmp_path)      # keep test autosaves out of app data
    return win


def test_commit_marks_unsaved_and_title(qapp, tmp_path):
    win = _win(tmp_path)
    assert not win._unsaved_changes
    win.commit()
    assert win._unsaved_changes and win._dirty_for_autosave
    assert "•" in win.windowTitle()


def test_autosave_tick_writes_and_clears(qapp, tmp_path):
    win = _win(tmp_path)
    win.commit()
    win._autosave_tick()
    p = tmp_path / "autosave.json"
    assert p.exists()
    wrapper = json.loads(p.read_text())
    assert wrapper["autosave"] == 1
    assert wrapper["doc"]["shapes"], "document content is inside the autosave"
    assert not win._dirty_for_autosave    # clean until the next edit
    win._autosave_tick()                  # nothing dirty -> no rewrite needed
    assert p.exists()


def test_recovery_accept_restores_document(qapp, tmp_path):
    win = _win(tmp_path)
    win.commit()
    win.write_autosave()
    # a fresh window (same autosave dir) finds and restores it
    from leathercad_app.mainwindow import MainWindow
    win2 = MainWindow(Document())
    win2.autosave_dir = str(tmp_path)
    assert win2.maybe_recover_autosave(ask=lambda src, when: True) is True
    assert len(win2.doc.shapes) == 1
    assert round(win2.doc.shapes[0].width, 1) == 33.0
    assert win2._unsaved_changes          # recovered work isn't on disk yet
    assert not (tmp_path / "autosave.json").exists()   # consumed


def test_recovery_decline_discards(qapp, tmp_path):
    win = _win(tmp_path)
    win.write_autosave()
    from leathercad_app.mainwindow import MainWindow
    win2 = MainWindow(Document())
    win2.autosave_dir = str(tmp_path)
    assert win2.maybe_recover_autosave(ask=lambda src, when: False) is False
    assert len(win2.doc.shapes) == 0                   # untouched
    assert not (tmp_path / "autosave.json").exists()   # still consumed


def test_no_recovery_when_no_autosave(qapp, tmp_path):
    win = _win(tmp_path)
    assert win.maybe_recover_autosave(ask=lambda *a: True) is False


def test_clean_close_removes_autosave(qapp, tmp_path):
    from PySide6.QtGui import QCloseEvent
    win = _win(tmp_path)
    win.write_autosave()
    assert (tmp_path / "autosave.json").exists()
    win.closeEvent(QCloseEvent())         # hidden window: closes silently
    assert not (tmp_path / "autosave.json").exists()


def test_save_clears_dirty_and_autosave(qapp, tmp_path):
    win = _win(tmp_path)
    win.commit()
    win.write_autosave()
    win.path = str(tmp_path / "work.json")
    win.save_document()
    assert not win._unsaved_changes
    assert "•" not in win.windowTitle()
    assert not (tmp_path / "autosave.json").exists()


def test_crash_handler_logs_and_emergency_saves(qapp, tmp_path):
    from leathercad_app.robustness import CrashHandler
    win = _win(tmp_path)
    handler = CrashHandler(win, interactive=False, data_dir=tmp_path)
    try:
        raise ValueError("boom-for-test")
    except ValueError:
        handler.handle(*sys.exc_info())
    log = (tmp_path / "error.log").read_text()
    assert "boom-for-test" in log and "Traceback" in log
    assert (tmp_path / "autosave.json").exists()       # work was saved

    # a second error appends rather than overwrites
    try:
        raise RuntimeError("second-error")
    except RuntimeError:
        handler.handle(*sys.exc_info())
    log = (tmp_path / "error.log").read_text()
    assert "boom-for-test" in log and "second-error" in log


def test_smoke_flag_launches_and_exits_clean():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-m", "leathercad_app", "--smoke"],
                       env=env, capture_output=True, timeout=120,
                       cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.returncode == 0, r.stderr.decode()

"""Keeping in touch: a newer version, What's new, the way to the group.

No test here goes on the internet: the main window's look at the website
is replaced by a stand-in that answers at once, and LatestCheck itself is
tried against a little web server on this computer.
"""

import json
import os
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QSettings  # noqa: E402
from PySide6.QtGui import QDesktopServices, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from leathercad import __version__  # noqa: E402
from leathercad.document import Document  # noqa: E402
from leathercad_app import updates  # noqa: E402
from leathercad_app.updates import Latest  # noqa: E402

_KEYS = ("updatesCheck", "updatesLastCheck", "updatesLatest",
         "updatesNotNow", "lastVersionRun", "geometry")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(qapp):
    """The app's settings (this run's own: see conftest), with nothing in
    them about new versions to begin with or to leave behind."""
    s = QSettings("Stitch Hero", "Stitch Hero")
    for key in _KEYS:
        s.remove(key)
    yield s
    for key in _KEYS:
        s.remove(key)


@pytest.fixture
def opened(monkeypatch):
    """The pages Stitch Hero opens, where a browser would have opened."""
    pages = []
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: pages.append(url.toString()) or True)
    return pages


def _window(monkeypatch, answer=None, why=""):
    """A main window whose look at the website answers ``win.answer`` at
    once, counting its looks in ``win.looks``."""
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(Document())
    win.answer = (answer, why)
    win.looks = 0

    def look(answered):
        win.looks += 1
        answered(*win.answer)
    monkeypatch.setattr(win, "_look_for_latest", look)
    return win


def _settle(qapp, ready=lambda: False, ms=300):
    """Run the event loop until ``ready()`` or ``ms`` have gone."""
    end = time.monotonic() + ms / 1000.0
    while not ready() and time.monotonic() < end:
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    return ready()


def _action(win, text):
    help_menu = next(a.menu() for a in win.menuBar().actions()
                     if a.text() == "&Help")
    return next(a for a in help_menu.actions() if a.text() == text)


def _plain(markdown):
    doc = QTextDocument()
    doc.setMarkdown(markdown, QTextDocument.MarkdownDialectGitHub)
    return doc.toPlainText()


# -- the pieces ---------------------------------------------------------------

def test_versions_compare_as_numbers():
    assert updates.is_newer("0.4.17", "0.4.16")
    assert updates.is_newer("0.4.10", "0.4.9")          # not as text would
    assert updates.is_newer("0.5", "0.4.99")
    assert updates.is_newer("1.0", "0.9.9")
    assert not updates.is_newer("0.4.16", "0.4.16")
    assert not updates.is_newer("0.4.16.0", "0.4.16")
    assert not updates.is_newer("0.4.15", "0.4.16")
    for odd in ("", None, "v0.5", "0.5-beta", "<b>9</b>", "9.9.9.9.9"):
        assert updates.version_key(odd) is None
        assert not updates.is_newer(odd, "0.4.16")


def test_latest_json_is_read_carefully():
    got = updates.parse_latest(json.dumps({
        "version": "0.4.17", "date": "2026-11-21",
        "notes": ["One", "  ", 7, "Two"],
        "download": "https://elsewhere.example/stitch-hero.exe",
    }).encode())
    # (only the version, date and notes are taken: no link from the file)
    assert got == Latest("0.4.17", "2026-11-21", ["One", "Two"])
    long = updates.parse_latest(json.dumps(
        {"version": "1.0", "notes": ["x" * 999] * 50}))
    assert len(long.notes) == 12 and all(len(n) == 300 for n in long.notes)
    for bad in (b"", b"not json", b"[1, 2]", b'{"version": 5}',
                b'{"version": "five"}', b'{"notes": []}', b"\xff\xfe",
                b"[" * 100_000):
        assert updates.parse_latest(bad) is None
    assert updates.day_said("2026-11-22") == "22 Nov 2026"
    assert updates.day_said("2026-12-05") == "5 Dec 2026"
    assert updates.day_said("soon") == "soon"


def test_a_look_at_most_once_a_day():
    morning = datetime(2026, 11, 21, 8, 0)
    assert updates.due("", morning)
    assert updates.due("not a time", morning)
    assert not updates.due(updates.now_text(datetime(2026, 11, 21, 7, 59)),
                           morning)
    assert updates.due(updates.now_text(datetime(2026, 11, 20, 23, 59)),
                       morning)                          # yesterday
    assert updates.due(updates.now_text(datetime(2026, 11, 21, 9, 0)),
                       morning)                          # the clock was wrong


def test_whats_new_comes_from_the_changelog():
    text = ("# Changelog\n\n## Unreleased\n\n* coming\n\n"
            "## 0.4.17 — 2026-11-22\n\n### Keeping in touch\n* a\n* b\n\n"
            "## 0.4.16 — 2026-11-21\n\n* older\n")
    assert updates.changelog_section(text, "0.4.17") == \
        "### Keeping in touch\n* a\n* b"
    assert updates.changelog_section(text, "0.4.16") == "* older"
    assert updates.changelog_section(text, "Unreleased") == "* coming"
    assert updates.changelog_section(text, "0.4.1") == ""
    # the version that is running has its part: What's new is never empty
    assert updates.whats_new_text(__version__)


def test_the_changelog_ships_with_the_app():
    root = Path(__file__).resolve().parent.parent
    spec = (root / "packaging" / "leather-drafting.spec").read_text()
    assert '"CHANGELOG.md"' in spec
    assert "PySide6.QtNetwork" in spec
    assert updates.changelog_path() == root / "CHANGELOG.md"


def test_a_post_for_the_group_says_what_its_featured_post_asks():
    bug = updates.group_post("bug", "0.4.17", "Windows 11 (x86_64)")
    assert bug.splitlines() == [
        "#Bug", "Version: Stitch Hero 0.4.17",
        "Windows or Mac: Windows 11 (x86_64)",
        "What I did:", "What I expected:", "What happened:"]
    idea = updates.group_post("feature", "0.4.17", "")
    assert idea.splitlines() == [
        "#FeatureRequest", "Version: Stitch Hero 0.4.17",
        "What I'm trying to make or do:", "What gets in the way today:",
        "What would help:"]


# -- the main window ----------------------------------------------------------

def test_a_newer_version_shows_the_bar_until_not_now(qapp, settings,
                                                     monkeypatch, opened):
    win = _window(monkeypatch, Latest("99.0", "2099-01-01", ["Rounder"]))
    assert win.update_bar.isHidden()
    win.check_for_updates()
    assert not win.update_bar.isHidden()
    assert "Stitch Hero 99.0 is out" in win.update_bar.label.text()
    assert updates.parse_latest(settings.value("updatesLatest", "", type=str)) \
        == Latest("99.0", "2099-01-01", ["Rounder"])     # kept for next time
    assert win._whats_new_dialog is None                 # (not asked: no box)
    win.update_bar.buttons["download"].click()
    assert opened == [updates.DOWNLOAD_URL]
    win.update_bar.buttons["whats_new"].click()
    assert "Rounder" in win._whats_new_dialog.browser.toPlainText()
    win._whats_new_dialog.close()
    win.update_bar.buttons["not_now"].click()
    assert win.update_bar.isHidden()
    assert settings.value("updatesNotNow", "", type=str) == "99.0"
    win.check_for_updates()                  # the same version: not again
    assert win.update_bar.isHidden()
    win.answer = (Latest("99.1"), "")        # a newer one is said again
    win.check_for_updates()
    assert not win.update_bar.isHidden()
    assert "99.1" in win.update_bar.label.text()


def test_nothing_newer_is_said_only_when_asked(qapp, settings, monkeypatch):
    said = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.append(a[2]))
    win = _window(monkeypatch, Latest(__version__))
    win.check_for_updates()
    assert win.update_bar.isHidden() and not said
    win.check_for_updates(asked=True)
    assert said == [f"You have the newest version: Stitch Hero {__version__}."]
    # not able to look: said only when asked, and a bar already up stays
    win.answer = (Latest("99.0"), "")
    win.check_for_updates()
    win.answer = (None, "Host stitch-hero.com not found")
    win.check_for_updates()
    assert not win.update_bar.isHidden() and len(said) == 1
    win.check_for_updates(asked=True)
    assert "couldn't find out" in said[-1]
    assert "Host stitch-hero.com not found" in said[-1]


def test_asking_says_whats_new_in_the_newer_one_as_plain_text(qapp, settings,
                                                             monkeypatch,
                                                             opened):
    win = _window(monkeypatch, Latest("99.0", "2099-01-05",
                                      ["Holes <b>rounder</b>", "Faster"]))
    settings.setValue("updatesNotNow", "99.0")           # put off before...
    win.check_for_updates(asked=True)                    # ... but asked now
    assert not win.update_bar.isHidden()
    dlg = win._whats_new_dialog
    assert dlg.windowTitle() == "What's new in Stitch Hero 99.0"
    shown = dlg.browser.toPlainText()
    assert shown.startswith("Stitch Hero 99.0 · 5 Jan 2099")
    assert "Holes <b>rounder</b>" in shown and "Faster" in shown
    dlg.download_button.click()
    assert opened == [updates.DOWNLOAD_URL]


def test_opening_looks_once_a_day_a_moment_later(qapp, settings, monkeypatch):
    win = _window(monkeypatch, Latest("99.0"))
    win.update_check_delay_ms = 0
    win.after_launch()
    assert win.looks == 0                    # not while the window opens
    assert _settle(qapp, lambda: win.looks == 1)
    assert not win.update_bar.isHidden()
    # opened again the same day: the version heard of is said, no new look
    again = _window(monkeypatch, Latest("99.0"))
    again.update_check_delay_ms = 0
    again.after_launch()
    _settle(qapp)
    assert again.looks == 0
    assert not again.update_bar.isHidden()
    # the next day, a look again
    settings.setValue("updatesLastCheck", updates.now_text(
        datetime(2000, 1, 1)))
    later = _window(monkeypatch, Latest("99.0"))
    later.update_check_delay_ms = 0
    later.after_launch()
    assert _settle(qapp, lambda: later.looks == 1)


def test_the_daily_look_can_be_turned_off(qapp, settings, monkeypatch):
    win = _window(monkeypatch, Latest("99.0"))
    win.update_check_delay_ms = 0
    assert win.act_updates.isChecked()                   # on to begin with
    win.check_for_updates()
    assert not win.update_bar.isHidden()
    win.act_updates.setChecked(False)
    assert win.update_bar.isHidden()
    assert settings.value("updatesCheck", True, type=bool) is False
    settings.remove("updatesLastCheck")
    again = _window(monkeypatch, Latest("99.0"))
    assert not again.act_updates.isChecked()
    again.update_check_delay_ms = 0
    again.after_launch()
    _settle(qapp)
    assert again.looks == 0 and again.update_bar.isHidden()
    again.check_for_updates(asked=True)                  # asking still does
    assert again.looks == 1 and not again.update_bar.isHidden()


def test_whats_new_is_said_once_after_an_update(qapp, settings, monkeypatch):
    settings.setValue("updatesCheck", False)             # (no looks here)
    win = _window(monkeypatch)
    win.after_launch()                       # a first install: nothing said
    assert win._whats_new_dialog is None
    assert settings.value("lastVersionRun", "", type=str) == __version__
    settings.setValue("lastVersionRun", "0.1")           # an older one ran
    win.after_launch()
    dlg = win._whats_new_dialog
    assert dlg.windowTitle() == f"What's new in Stitch Hero {__version__}"
    assert dlg.browser.toPlainText() == \
        _plain(updates.whats_new_text(__version__))
    assert dlg.download_button is None
    dlg.close()
    win._whats_new_dialog = None
    win.after_launch()                       # the same version: once only
    assert win._whats_new_dialog is None
    # Stitch Hero ran here before this was kept (its window place is
    # saved): that was an older version
    settings.remove("lastVersionRun")
    settings.setValue("geometry", win.saveGeometry())
    win.after_launch()
    assert win._whats_new_dialog is not None
    win._whats_new_dialog.close()


def test_help_reaches_the_group(qapp, settings, monkeypatch, opened):
    win = _window(monkeypatch)
    help_menu = next(a.menu() for a in win.menuBar().actions()
                     if a.text() == "&Help")
    texts = [a.text() for a in help_menu.actions()]
    for text in ("User guide", "What's new in this version",
                 "Join the Stitch Hero group", "Report a problem…",
                 "Suggest a feature…", "Check for updates now",
                 "Tell me when a new version is out", "About"):
        assert text in texts
    _action(win, "Join the Stitch Hero group").trigger()
    assert opened == [updates.GROUP_URL]
    _action(win, "What's new in this version").trigger()
    assert win._whats_new_dialog.windowTitle().endswith(__version__)
    win._whats_new_dialog.group_button.click()
    assert opened == [updates.GROUP_URL] * 2
    win._whats_new_dialog.close()


def test_report_a_problem_fills_in_the_post(qapp, settings, monkeypatch,
                                            opened):
    seen = []

    def shown(dlg):
        seen.append(dlg.text.toPlainText())
        dlg.text.setPlainText(dlg.text.toPlainText() + "It stopped.")
        dlg.go_button.click()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", shown)
    win = _window(monkeypatch)
    _action(win, "Report a problem…").trigger()
    assert seen[0] == updates.group_post("bug", __version__,
                                         updates.system_text())
    assert QApplication.clipboard().text() == seen[0] + "It stopped."
    assert opened == [updates.GROUP_URL]
    _action(win, "Suggest a feature…").trigger()
    assert seen[1].startswith("#FeatureRequest\n")
    assert opened == [updates.GROUP_URL] * 2


def test_about_says_where_to_find_us(qapp, settings, monkeypatch):
    said = []
    monkeypatch.setattr(QMessageBox, "about",
                        lambda parent, title, text: said.append(text))
    _window(monkeypatch)._about()
    assert updates.SITE in said[0] and updates.GROUP_URL in said[0]


def test_the_bar_follows_the_theme(qapp, settings, monkeypatch):
    win = _window(monkeypatch, Latest("99.0"))
    win.check_for_updates()
    win.act_dark.setChecked(True)
    win.act_dark.setChecked(False)
    assert not win.update_bar.isHidden()
    assert "palette(alternate-base)" in win.update_bar.styleSheet()


# -- the look itself, against a web server on this computer -------------------

class _Site(BaseHTTPRequestHandler):
    pages = {}
    asked = []

    def do_GET(self):
        _Site.asked.append(dict(self.headers))
        body = _Site.pages.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Site.asked = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _look(qapp, url):
    from PySide6.QtNetwork import QNetworkProxy
    got = []
    check = updates.LatestCheck(url, timeout_ms=5000)
    check._net.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    check.done.connect(lambda latest, why: got.append((latest, why)))
    check.start()
    assert _settle(qapp, lambda: got, ms=8000), "no answer"
    return got[0]


def test_the_look_reads_latest_json_and_sends_nothing_more(qapp, site):
    _Site.pages = {
        "/latest.json": json.dumps({"version": "0.9.1",
                                    "notes": ["n"]}).encode(),
        "/big.json": b" " * (70 * 1024),
        "/odd.json": b"<html>not this</html>",
    }
    latest, why = _look(qapp, site + "/latest.json")
    assert latest == Latest("0.9.1", "", ["n"]) and why == ""
    sent = _Site.asked[0]
    assert sent["User-Agent"] == f"Stitch Hero/{__version__}"
    assert "Cookie" not in sent
    assert sent.get("Accept-Language", "*") == "*"       # not this computer's
    latest, why = _look(qapp, site + "/gone.json")
    assert latest is None and why                        # (a 404, in Qt's words)
    latest, why = _look(qapp, site + "/big.json")
    assert latest is None and "longer" in why
    latest, why = _look(qapp, site + "/odd.json")
    assert latest is None and "read" in why


def test_smoke_wants_the_changelog_and_https(qapp, tmp_path, monkeypatch):
    from PySide6.QtNetwork import QSslSocket
    from leathercad_app import __main__ as entry
    monkeypatch.chdir(tmp_path)
    assert entry._smoke() == 0
    real = updates.changelog_path
    monkeypatch.setattr(updates, "changelog_path",
                        lambda: tmp_path / "missing.md")
    assert entry._smoke() == 6
    assert "changelog" in (tmp_path / "smoke-failure.txt").read_text()
    monkeypatch.setattr(updates, "changelog_path", real)
    monkeypatch.setattr(QSslSocket, "supportsSsl",
                        staticmethod(lambda: False))
    assert entry._smoke() == 5
    assert "https" in (tmp_path / "smoke-failure.txt").read_text()

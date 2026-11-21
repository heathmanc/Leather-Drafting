"""Keeping in touch: a newer version, what is new in this one, and the way
to the Stitch Hero group on Facebook.

Whether a newer version is out is read from one small file on the
website, latest.json:

    {"version": "0.4.17", "date": "2026-11-21",
     "notes": ["What it means at the bench, in a line", "..."]}

Nothing is sent but the request for it. It is asked for at most once a
day, a few seconds after Stitch Hero opens, and when you ask (Help →
Check for updates now); Help → Tell me when a new version is out turns
the daily look off. Only the version number, the date and the notes are
taken from it, and shown as plain text: Download always opens
stitch-hero.com itself, whatever the file says.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QSysInfo, QUrl, Signal
from PySide6.QtGui import (QDesktopServices, QGuiApplication, QTextCursor,
                           QTextDocument)
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QTextBrowser,
                               QVBoxLayout)

SITE = "https://stitch-hero.com"
LATEST_URL = SITE + "/latest.json"
DOWNLOAD_URL = SITE + "/#download"
GROUP_URL = "https://www.facebook.com/groups/stitchhero"

_MAX_BYTES = 64 * 1024            # latest.json is a few hundred bytes
_MAX_NOTES = 12
_MAX_NOTE = 300
_VERSION = re.compile(r"^\d+(?:\.\d+){0,3}$")


# -- versions ---------------------------------------------------------------

def version_key(text) -> Optional[Tuple[int, ...]]:
    """``"0.4.17"`` as ``(0, 4, 17)``; None for anything that isn't a
    version number."""
    text = str(text or "").strip()
    if not _VERSION.match(text):
        return None
    return tuple(int(part) for part in text.split("."))


def is_newer(version, than) -> bool:
    """Whether ``version`` comes after ``than``: 0.4.17 after 0.4.16,
    0.5 after 0.4.99. Anything that isn't a version is never newer."""
    a, b = version_key(version), version_key(than)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# -- latest.json ------------------------------------------------------------

@dataclass
class Latest:
    """What latest.json says: the newest version out, when, its notes."""
    version: str
    date: str = ""
    notes: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({"version": self.version, "date": self.date,
                           "notes": self.notes})


def parse_latest(raw) -> Optional[Latest]:
    """latest.json read, or None if it isn't one. Only a version number, a
    short date and a few lines of plain text are taken from it."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8")
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    if not isinstance(version, str) or version_key(version) is None:
        return None
    date = data.get("date")
    date = date.strip()[:20] if isinstance(date, str) else ""
    notes = data.get("notes")
    if isinstance(notes, list):
        notes = [n.strip()[:_MAX_NOTE] for n in notes
                 if isinstance(n, str) and n.strip()][:_MAX_NOTES]
    else:
        notes = []
    return Latest(version.strip(), date, notes)


def day_said(date: str) -> str:
    """``"2026-11-22"`` as ``"22 Nov 2026"``; anything else as it is."""
    try:
        return datetime.date.fromisoformat(date).strftime("%d %b %Y") \
            .lstrip("0")
    except (TypeError, ValueError):
        return date


def now_text(now: Optional[datetime.datetime] = None) -> str:
    """When, as the settings keep it."""
    return (now or datetime.datetime.now()).isoformat(timespec="seconds")


def due(last: str, now: Optional[datetime.datetime] = None) -> bool:
    """Whether to look again: never looked, or not yet today. A last look
    later than now (the clock was wrong) counts as never."""
    now = now or datetime.datetime.now()
    try:
        then = datetime.datetime.fromisoformat(str(last))
    except (TypeError, ValueError):
        return True
    return then.date() != now.date() or then > now


class LatestCheck(QObject):
    """One look at latest.json, in the background. ``done`` carries the
    answer: a Latest, or None and why not."""

    done = Signal(object, str)

    def __init__(self, url: str = LATEST_URL, parent=None,
                 timeout_ms: int = 10_000):
        super().__init__(parent)
        from PySide6.QtNetwork import (QNetworkAccessManager,
                                       QNetworkProxyFactory)
        # (through a proxy, where this computer is set up to use one)
        QNetworkProxyFactory.setUseSystemConfiguration(True)
        self._net = QNetworkAccessManager(self)
        self._url = url
        self._timeout = timeout_ms
        self._reply = None

    def start(self) -> "LatestCheck":
        from PySide6.QtNetwork import QNetworkRequest
        from leathercad import __version__
        req = QNetworkRequest(QUrl(self._url))
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader,
                      f"Stitch Hero/{__version__}")
        # (Qt would otherwise send the language this computer is set to)
        req.setRawHeader(b"Accept-Language", b"*")
        req.setTransferTimeout(self._timeout)
        req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                         QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        self._reply = self._net.get(req)
        self._reply.finished.connect(self._finished)
        return self

    def _finished(self):
        from PySide6.QtNetwork import QNetworkReply
        reply, self._reply = self._reply, None
        if reply is None:
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            why = reply.errorString()
            reply.deleteLater()
            self.done.emit(None, why)
            return
        body = bytes(reply.read(_MAX_BYTES + 1))
        reply.deleteLater()
        if len(body) > _MAX_BYTES:
            self.done.emit(None, "the answer was far longer than it should be")
            return
        latest = parse_latest(body)
        self.done.emit(latest, "" if latest is not None
                       else "the answer wasn't one Stitch Hero can read")


# -- what's new in this version ---------------------------------------------

def changelog_path() -> Path:
    """CHANGELOG.md: in the repo's top folder in a checkout, inside the
    bundle in a built app (as the user guide is: see helpdialog)."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "CHANGELOG.md"
    return Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def changelog_section(text: str, version: str) -> str:
    """What the changelog says under ``## <version> — <date>``: the lines
    after that heading, up to the next heading of its size."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if start is None:
            m = re.match(r"^##\s+(\S+)", line)
            if m and m.group(1) == version:
                start = i + 1
        elif line.startswith("## "):
            return "\n".join(lines[start:i]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else ""


def whats_new_text(version: str) -> str:
    """This version's part of the changelog, or '' where it can't be read."""
    try:
        text = changelog_path().read_text(encoding="utf-8")
    except OSError:
        return ""
    return changelog_section(text, version)


# -- the group --------------------------------------------------------------

def open_url(url: str) -> bool:
    """Open a page in the browser."""
    return QDesktopServices.openUrl(QUrl(url))


def system_text() -> str:
    """This computer, for a report: 'Windows 11 Version 24H2 (x86_64)'."""
    return f"{QSysInfo.prettyProductName()} ({QSysInfo.currentCpuArchitecture()})"


def group_post(kind: str, version: str, system: str) -> str:
    """The words for a new post in the group, as its Featured post asks for
    them: a problem (``"bug"``) or an idea (``"feature"``)."""
    if kind == "bug":
        return ("#Bug\n"
                f"Version: Stitch Hero {version}\n"
                f"Windows or Mac: {system}\n"
                "What I did:\n"
                "What I expected:\n"
                "What happened:\n")
    return ("#FeatureRequest\n"
            f"Version: Stitch Hero {version}\n"
            "What I'm trying to make or do:\n"
            "What gets in the way today:\n"
            "What would help:\n")


# -- the windows ------------------------------------------------------------

class UpdateBar(QFrame):
    """The strip above the drawing that says a newer version is out."""

    whatsNew = Signal()
    download = Signal()
    notNow = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBar")
        self.version = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 6, 4)
        self.label = QLabel()
        row.addWidget(self.label, 1)
        self.buttons = {}
        for name, text, signal in (("whats_new", "What's new", self.whatsNew),
                                   ("download", "Download", self.download),
                                   ("not_now", "Not now", self.notNow)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, s=signal: s.emit())
            row.addWidget(b)
            self.buttons[name] = b
        self.restyle()
        self.hide()

    def restyle(self):
        """Tinted from the palette in use, so it follows the theme."""
        self.setStyleSheet(
            "QFrame#updateBar { background: palette(alternate-base);"
            " border-bottom: 1px solid palette(mid);"
            " border-left: 4px solid palette(highlight); }")

    def say(self, version: str):
        self.version = version
        self.label.setText(f"<b>Stitch Hero {html.escape(version)} is "
                           "out.</b> See what's new, or download it from "
                           "stitch-hero.com.")
        self.show()


class WhatsNewDialog(QDialog):
    """What's new: in the version you have (its part of the changelog), or
    in a newer one that is out (the notes from latest.json), with the way
    to get it."""

    def __init__(self, title: str, parent=None, markdown: str = "",
                 notes: Optional[List[str]] = None, download: bool = False,
                 heading: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 540)
        root = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        if notes is not None:                  # from the website: plain text
            items = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
            self.browser.setHtml(
                (f"<h3>{html.escape(heading)}</h3>" if heading else "")
                + (f"<ul>{items}</ul>" if items else
                   "<p>The Stitch Hero group on Facebook has the news of "
                   "each new version.</p>"))
        else:
            self.browser.document().setMarkdown(
                markdown, QTextDocument.MarkdownDialectGitHub)
        # (from the top: setting the text leaves the view at its foot)
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        root.addWidget(self.browser, 1)
        row = QHBoxLayout()
        self.group_button = QPushButton("Join the Stitch Hero group")
        self.group_button.setAutoDefault(False)
        self.group_button.clicked.connect(lambda: open_url(GROUP_URL))
        row.addWidget(self.group_button)
        row.addStretch(1)
        self.download_button = None
        if download:
            self.download_button = QPushButton("Download")
            self.download_button.setAutoDefault(False)
            self.download_button.clicked.connect(self._download)
            row.addWidget(self.download_button)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    def _download(self):
        open_url(DOWNLOAD_URL)
        self.accept()


class GroupPostDialog(QDialog):
    """Help → Report a problem / Suggest a feature: the words for a new
    post in the group, filled in as far as Stitch Hero can, ready to
    paste."""

    def __init__(self, kind: str, version: str, system: str, parent=None):
        super().__init__(parent)
        bug = kind == "bug"
        self.setWindowTitle("Report a problem" if bug else "Suggest a feature")
        self.resize(540, 400)
        root = QVBoxLayout(self)
        intro = QLabel(
            "Problems are tracked and fixed from the Stitch Hero group on "
            "Facebook. Fill this in, here or in the post, then <b>Copy and "
            "open the group</b>: make a new post there, paste, and add a "
            "screenshot if you can." if bug else
            "Ideas are gathered in the Stitch Hero group on Facebook, and "
            "the most wanted are made first. Fill this in, here or in the "
            "post, then <b>Copy and open the group</b>: make a new post "
            "there and paste.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.text = QPlainTextEdit(group_post(kind, version, system))
        root.addWidget(self.text, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.go_button = QPushButton("Copy and open the group")
        self.go_button.setDefault(True)
        self.go_button.clicked.connect(self._copy_and_go)
        row.addWidget(self.go_button)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        root.addLayout(row)

    def _copy_and_go(self):
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        open_url(GROUP_URL)
        self.accept()

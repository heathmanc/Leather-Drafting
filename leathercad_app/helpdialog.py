"""In-app user guide: renders docs/USER_GUIDE.md in a searchable viewer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser,
                               QLineEdit, QLabel, QPushButton)


def guide_path() -> Path:
    """docs/USER_GUIDE.md next to the package (repo layout)."""
    return Path(__file__).resolve().parent.parent / "docs" / "USER_GUIDE.md"


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leather-Drafting — User Guide")
        self.resize(760, 640)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)

        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Find:"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("search the guide…  (Enter = next)")
        self.find_edit.returnPressed.connect(self._find_next)
        bar.addWidget(self.find_edit, 1)
        nxt = QPushButton("Next")
        nxt.clicked.connect(self._find_next)
        bar.addWidget(nxt)
        root.addLayout(bar)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        gp = guide_path()
        if gp.exists():
            # let ![](x.png) references resolve against the docs folder
            self.browser.setSearchPaths([str(gp.parent)])
            self.browser.document().setMarkdown(
                gp.read_text(encoding="utf-8"),
                QTextDocument.MarkdownDialectGitHub)
        else:  # pragma: no cover - only when docs are missing
            self.browser.setPlainText(
                "USER_GUIDE.md not found.\n\nExpected at: " + str(gp))
        root.addWidget(self.browser, 1)

    def _find_next(self):
        text = self.find_edit.text()
        if not text:
            return
        if not self.browser.find(text):           # wrap around
            cur = self.browser.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            self.browser.setTextCursor(cur)
            self.browser.find(text)

"""The library window: the pieces you have made, each with its pictures,
your notes on it, the pattern it was cut from and that pattern's 1:1 PDF.

``leathercad.library`` keeps them, as plain folders; this is the window on
them. On the left the pieces as a wall of pictures, the latest made first;
on the right the one that is chosen. What you type is kept as you type it:
there is nothing to save. A piece's pattern is a drawing like any other:
opened from here (Open), saving it in the main window keeps it here.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import (QDate, QFile, QLocale, QPointF, QRectF, QSize,
                            QStandardPaths, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication,
                           QIcon, QImage, QImageReader, QKeySequence,
                           QPainter, QPalette, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QDateEdit, QDialog, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from leathercad.library import PICTURES, Item, Library

from .robustness import app_data_dir

_TILE = 168                       # a piece on the wall: its picture, square
_SMALL = 60                       # ... and one of its pictures in the strip
_LARGE = 1400                     # the chosen picture is read this big
_NO_DAY = QDate(1900, 1, 1)       # the date box's way of saying "not set"
_OURS = re.compile(r"^[0-9a-f]{24}\.(jpg|png)$")   # a small copy's name


def default_folder() -> Path:
    """Where the library is kept unless another folder is chosen: with
    your documents, where you can find it."""
    docs = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(docs or Path.home()) / "Stitch Hero Library"


def library_folder(settings=None) -> Path:
    """The folder the library is in: the one chosen, or the usual one."""
    if settings is not None:
        saved = settings.value("libraryFolder", "", type=str)
        if saved:
            return Path(saved)
    return default_folder()


def picture_endings() -> List[str]:
    """The kinds of picture file this computer's Qt can show."""
    can = {"." + bytes(f).decode().lower()
           for f in QImageReader.supportedImageFormats()}
    return [e for e in PICTURES if e in can]


def can_show(path) -> bool:
    """Whether a file is a picture that can be shown."""
    return str(path).lower().endswith(tuple(picture_endings())) \
        and QImageReader(str(path)).canRead()


def day_text(made: str) -> str:
    """``2026-11-17`` as a day to read: ``17 Nov 2026``."""
    day = QDate.fromString(made or "", Qt.ISODate)
    return QLocale().toString(day, "d MMM yyyy") if day.isValid() else ""


class Thumbs:
    """Small copies of pictures, made once and kept. Reading a phone's
    photograph whole to show it a thumb's width across takes a moment, and
    a library has many: so each is read small (and turned the way the
    camera was held), and the small copy is saved for next time."""

    def __init__(self, folder: Optional[Path] = None):
        self.folder = Path(folder) if folder \
            else app_data_dir() / "library-thumbs"
        self._small = {}

    def key(self, path, what) -> Optional[str]:
        """What a small copy of the file at ``path`` is kept under (it
        changes when the file does), or None if there is no such file."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        return hashlib.sha1(
            f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}|{what}"
            .encode("utf-8")).hexdigest()[:24]

    def _kept(self, name: str) -> QImage:
        """The small copy saved as ``name``, if there is one. It is marked
        as wanted, so that ``tidy`` leaves it be."""
        kept = self.folder / name
        try:
            age = time.time() - kept.stat().st_mtime
        except OSError:
            return QImage()
        img = QImage(str(kept))
        if not img.isNull() and age > 86400.0:
            try:
                os.utime(kept)
            except OSError:
                pass
        return img

    def _keep(self, img: QImage, name: str, kind: str, quality: int = -1):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            img.save(str(self.folder / name), kind, quality)
        except OSError:
            pass

    def ready(self, path, size: int, sketch: bool = False) -> bool:
        """Whether asking for this small copy takes no time to speak of:
        it is made already, or there is nothing to make it from."""
        key = self.key(path, f"sketch {size}" if sketch else size)
        return key is None or key in self._small or (
            self.folder / f"{key}{'.png' if sketch else '.jpg'}").is_file()

    def image(self, path, size: int) -> Optional[QImage]:
        """The picture at ``path`` no bigger than ``size`` either way, or
        None if it can't be read."""
        key = self.key(path, size)
        if key is None:
            return None
        img = self._small.get(key)
        if img is not None:
            return img
        img = self._kept(f"{key}.jpg")
        if img.isNull():
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            full = reader.size()
            if full.isValid() and max(full.width(), full.height()) > size:
                reader.setScaledSize(full.scaled(size, size,
                                                 Qt.KeepAspectRatio))
            img = reader.read()
            if img.isNull():
                return None
            if max(img.width(), img.height()) > size:
                img = img.scaled(size, size, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            if img.hasAlphaChannel():          # on white, as paper would be
                flat = QImage(img.size(), QImage.Format_RGB32)
                flat.fill(Qt.white)
                p = QPainter(flat)
                p.drawImage(0, 0, img)
                p.end()
                img = flat
            self._keep(img, f"{key}.jpg", "JPG", 88)
        if size <= 256:
            self._small[key] = img
        return img

    def sketch(self, pattern, size: int) -> Optional[QImage]:
        """A pattern drawn small, its outlines on white: what stands for a
        piece that has no picture yet. None if it can't be read or has
        nothing in it."""
        key = self.key(pattern, f"sketch {size}")
        if key is None:
            return None
        img = self._small.get(key)
        if img is not None:
            return img
        img = self._kept(f"{key}.png")
        if img.isNull():
            img = _sketch(pattern, size)
            if img is None:
                return None
            self._keep(img, f"{key}.png", "PNG")
        self._small[key] = img
        return img

    def tidy(self, days: float = 90.0) -> int:
        """Throw away the small copies nobody has asked for in a long
        while: those of pictures since removed, renamed or changed, mostly.
        Returns how many went."""
        limit = time.time() - days * 86400.0
        gone = 0
        try:
            found = list(os.scandir(self.folder))
        except OSError:
            return 0
        for f in found:
            try:
                if _OURS.match(f.name) and f.is_file() \
                        and f.stat().st_mtime < limit:
                    os.remove(f.path)
                    gone += 1
            except OSError:
                pass
        return gone


def _sketch(pattern, size: int) -> Optional[QImage]:
    """Draw the outlines of a pattern file to fit a square (guide lines
    left out)."""
    from leathercad.document import Document
    try:
        doc = Document.load(str(pattern))
        lines = []
        for shape in doc.shapes:
            if getattr(shape, "construction", False):
                continue
            pts, _corners, closed = shape.world_polyline()
            if len(pts) >= 2:
                lines.append(([(p.x, p.y) for p in pts], closed))
    except Exception:              # not a pattern this version can read
        return None
    if not lines:
        return None
    xs = [x for pts, _c in lines for x, _y in pts]
    ys = [y for pts, _c in lines for _x, y in pts]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0, max(ys) - y0
    edge = 0.08 * size
    k = (size - 2 * edge) / max(w, h, 1e-6)
    ox = 0.5 * (size - k * w)
    oy = 0.5 * (size - k * h)
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(Qt.white)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor(60, 66, 80), max(1.0, size / 170.0)))
    for pts, closed in lines:
        poly = QPolygonF([QPointF(ox + k * (x - x0), oy + k * (h - (y - y0)))
                          for x, y in pts])            # y runs up the page
        if closed:
            p.drawPolygon(poly)
        else:
            p.drawPolyline(poly)
    p.end()
    return img


def _tile(img: Optional[QImage], size: int, palette: QPalette,
          text: str = "") -> QPixmap:
    """A square for the wall: the picture fitted in it, or -- for a piece
    with no picture yet -- a plain card that says so."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    card = palette.color(QPalette.AlternateBase)
    line = palette.color(QPalette.Mid)
    p.setPen(QPen(line, 1.0))
    p.setBrush(card)
    p.drawRoundedRect(0.5, 0.5, size - 1.0, size - 1.0, 6.0, 6.0)
    if img is not None and not img.isNull():
        fit = img.size().scaled(size - 8, size - 8, Qt.KeepAspectRatio)
        x, y = (size - fit.width()) // 2, (size - fit.height()) // 2
        p.drawImage(x, y, img.scaled(fit, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation))
    elif text:
        font = p.font()                        # (drawn at twice its size)
        font.setPointSizeF(font.pointSizeF() * 2.0)
        p.setFont(font)
        p.setPen(palette.color(QPalette.PlaceholderText))
        p.drawText(pm.rect().adjusted(16, 16, -16, -16),
                   Qt.AlignCenter | Qt.TextWordWrap, text)
    p.end()
    return pm


def _badge(tile: QPixmap, text: str = "3D") -> QPixmap:
    """A tile with a small label in its corner: "3D" on a picture from the
    3D assembly preview, to tell it from a photograph of the piece."""
    p = QPainter(tile)
    p.setRenderHint(QPainter.Antialiasing, True)
    font = QFont(p.font())                     # (tiles are drawn at twice
    font.setBold(True)                         # their size)
    font.setPointSizeF(max(8.0, font.pointSizeF()) * 1.6)
    p.setFont(font)
    fm = p.fontMetrics()
    w, h = fm.horizontalAdvance(text) + 14, fm.height() + 4
    box = QRectF(tile.width() - w - 8, tile.height() - h - 8, w, h)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(20, 20, 20, 175))
    p.drawRoundedRect(box, 6.0, 6.0)
    p.setPen(QColor(255, 255, 255))
    p.drawText(box, Qt.AlignCenter, text)
    p.end()
    return tile


def _icon(tile: QPixmap) -> QIcon:
    """A tile as an icon that looks the same chosen or not (the frame
    round it says which is chosen; Qt would tint the picture blue)."""
    tile.setDevicePixelRatio(2.0)
    icon = QIcon()
    for mode in (QIcon.Normal, QIcon.Selected, QIcon.Active):
        icon.addPixmap(tile, mode)
    return icon


_CHOSEN = """
QListWidget::item { border: 2px solid transparent; border-radius: 8px;
                    padding: 2px; color: palette(text); }
QListWidget::item:selected { border-color: palette(highlight);
                             background: transparent;
                             color: palette(text); }
"""


class _Picture(QFrame):
    """A picture as large as fits, or a line of help where there is none
    -- or, for a piece with no picture at all, a large invitation to add
    photographs of it: clicked, it asks for them (``asked``)."""

    opened = Signal()
    asked = Signal()

    #: what the invitation says
    INVITE = "Add photos of the finished piece"
    HOW = ("Click here to choose them, or drop them on this window. "
           "You can paste one with Ctrl+V, too.")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img: Optional[QImage] = None
        self.hint = ""
        self.invite = False
        self.caption = ""             # a line under a picture: what it is
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def show_image(self, img: Optional[QImage], hint: str = "",
                   invite: bool = False, caption: str = ""):
        self._img = img if img is not None and not img.isNull() else None
        self.hint = hint
        self.caption = caption if self._img is not None else ""
        self.invite = bool(invite) and self._img is None
        self.setCursor(Qt.PointingHandCursor if self.invite
                       else Qt.ArrowCursor)
        self.setToolTip(self.HOW if self.invite else "")
        self.update()

    def has_image(self) -> bool:
        return self._img is not None

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        area = self.contentsRect().adjusted(4, 4, -4, -4)
        p.fillRect(self.contentsRect(), self.palette().color(QPalette.Base))
        if self._img is None and self.invite:
            self._paint_invite(p, QRectF(area))
        elif self._img is None:
            p.setPen(self.palette().color(QPalette.PlaceholderText))
            p.drawText(area.adjusted(12, 12, -12, -12),
                       Qt.AlignCenter | Qt.TextWordWrap, self.hint)
        else:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            fit = self._img.size().scaled(area.size(), Qt.KeepAspectRatio)
            if fit.width() > self._img.width():        # never blown up
                fit = self._img.size()
            x = area.x() + (area.width() - fit.width()) // 2
            y = area.y() + (area.height() - fit.height()) // 2
            p.drawImage(x, y, self._img.scaled(fit, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
            if self.caption:
                self._paint_caption(p, QRectF(area))
        p.end()

    def _paint_caption(self, p: QPainter, area: QRectF) -> None:
        """The caption on a dark band along the foot of the picture."""
        p.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(self.font())
        p.setFont(font)
        fm = p.fontMetrics()
        room = area.width() - 40
        lines = fm.boundingRect(QRectF(0, 0, room, 200).toRect(),
                                int(Qt.AlignHCenter | Qt.TextWordWrap),
                                self.caption)
        band = QRectF(area.left() + 12, area.bottom() - lines.height() - 22,
                      area.width() - 24, lines.height() + 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 20, 170))
        p.drawRoundedRect(band, 6.0, 6.0)
        p.setPen(QColor(255, 255, 255))
        p.drawText(band.adjusted(8, 6, -8, -6),
                   Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap,
                   self.caption)

    def _paint_invite(self, p: QPainter, area: QRectF) -> None:
        """A dashed place to drop photographs on, with a camera in it and
        the words saying what it is for."""
        pal = self.palette()
        accent = pal.color(QPalette.Highlight)
        p.setRenderHint(QPainter.Antialiasing, True)
        zone = area.adjusted(10, 10, -10, -10)
        wash = QColor(accent)
        wash.setAlpha(22)
        p.setPen(QPen(accent, 2.0, Qt.DashLine))
        p.setBrush(wash)
        p.drawRoundedRect(zone, 12.0, 12.0)
        big = QFont(self.font())
        big.setBold(True)
        big.setPointSizeF(max(8.0, big.pointSizeF()) * 1.35)
        small = QFont(self.font())
        # camera, words and how-to, as one block in the middle
        cam_h, gap = 52.0, 14.0
        top = zone.center().y() - (cam_h + gap + 30 + 44) / 2
        cx, cy = zone.center().x(), top + 26
        p.setPen(QPen(accent, 3.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(cx - 34, cy - 18, 68, 44), 8.0, 8.0)
        p.drawRoundedRect(QRectF(cx - 13, cy - 26, 26, 9), 3.0, 3.0)
        p.drawEllipse(QPointF(cx, cy + 4), 13.0, 13.0)
        y = top + cam_h + gap
        p.setFont(big)
        p.setPen(pal.color(QPalette.Text))
        p.drawText(QRectF(zone.left() + 16, y, zone.width() - 32, 30),
                   Qt.AlignHCenter | Qt.AlignTop, self.INVITE)
        p.setFont(small)
        p.setPen(pal.color(QPalette.PlaceholderText))
        p.drawText(QRectF(zone.left() + 24, y + 30, zone.width() - 48, 60),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, self.HOW)

    def mouseReleaseEvent(self, event):
        if self.invite and event.button() == Qt.LeftButton \
                and self.rect().contains(event.position().toPoint()):
            event.accept()
            self.asked.emit()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, _event):
        if not self.invite:
            self.opened.emit()


class LibraryWindow(QDialog):
    """The library of finished pieces. ``host`` is the main window, which
    the pattern buttons need (they are greyed without it), and ``select``
    the id of the piece to open on. ``root`` and ``thumbs`` are for tests,
    and ``trash`` what deletes a folder (the recycle bin, unless a test
    says otherwise)."""

    def __init__(self, parent=None, *, root=None, settings=None, host=None,
                 thumbs: Optional[Thumbs] = None, trash=None,
                 select: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Library of finished pieces")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(1120, 720)
        self.setAcceptDrops(True)
        self._settings = settings
        self.host = host
        self.library = Library(root or library_folder(settings))
        self.thumbs = thumbs or Thumbs()
        self._trash = trash or QFile.moveToTrash
        self._items: List[Item] = []
        self._known: Dict[str, Item] = {}      # ... by id
        self._entries: Dict[str, QListWidgetItem] = {}   # their squares
        self._icons: Dict[str, QIcon] = {}     # the squares made so far
        self._waiting: List[str] = []          # squares still to be made
        self.current: Optional[Item] = None
        self._filling = False         # setting the boxes: not the user typing
        self._shown = ""              # the picture shown large

        # -- along the top ------------------------------------------------
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names and notes")
        self.search.setClearButtonEnabled(True)
        self.btn_new = QPushButton("New entry")
        self.btn_new.setToolTip("Start an entry for something you've made")
        self.btn_new.setAutoDefault(False)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_new)

        # -- the wall of pieces -------------------------------------------
        self.wall = QListWidget()
        self.wall.setViewMode(QListView.IconMode)
        self.wall.setMovement(QListView.Static)
        self.wall.setResizeMode(QListView.Adjust)
        self.wall.setIconSize(QSize(_TILE, _TILE))
        self.wall.setGridSize(QSize(_TILE + 20, _TILE + 52))
        self.wall.setWordWrap(True)
        self.wall.setUniformItemSizes(True)
        self.wall.setSpacing(4)
        self.wall.setMinimumWidth(_TILE + 60)
        self.wall.setStyleSheet(_CHOSEN)
        self.nothing = QLabel()
        self.nothing.setWordWrap(True)
        self.nothing.setAlignment(Qt.AlignCenter)
        self.nothing.setMargin(24)
        self.count = QLabel()
        self.btn_place = QPushButton("Library folder…")
        self.btn_place.setToolTip("Where the library is kept, and keeping "
                                  "it somewhere else")
        self.btn_place.setAutoDefault(False)
        foot = QHBoxLayout()
        foot.addWidget(self.count, 1)
        foot.addWidget(self.btn_place)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addLayout(top)
        left.addWidget(self.wall, 1)
        left.addWidget(self.nothing, 1)
        left.addLayout(foot)
        left_w = QWidget()
        left_w.setLayout(left)

        # -- the chosen piece ---------------------------------------------
        self.picture = _Picture()
        self.strip = QListWidget()
        self.strip.setViewMode(QListView.IconMode)
        self.strip.setFlow(QListView.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListView.Static)
        self.strip.setIconSize(QSize(_SMALL, _SMALL))
        self.strip.setGridSize(QSize(_SMALL + 14, _SMALL + 14))
        self.strip.setFixedHeight(_SMALL + 28)
        self.strip.setStyleSheet(_CHOSEN)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.btn_add = QPushButton("Add photos…")
        self.btn_add.setToolTip("Add photographs of the finished piece from "
                                "this computer. You can also drop them on "
                                "this window, or paste one (Ctrl+V)")
        self.btn_cover = QPushButton("Make cover")
        self.btn_cover.setToolTip("Show this picture for the piece on the "
                                  "wall")
        self.btn_drop = QPushButton("Remove picture")
        pics = QHBoxLayout()
        for b in (self.btn_add, self.btn_cover, self.btn_drop):
            b.setAutoDefault(False)
            pics.addWidget(b)
        pics.addStretch(1)

        self.ed_title = QLineEdit()
        self.ed_title.setPlaceholderText("What it is")
        self.ed_made = QDateEdit()
        self.ed_made.setCalendarPopup(True)
        self.ed_made.setDisplayFormat("d MMM yyyy")
        self.ed_made.setMinimumDate(_NO_DAY)
        self.ed_made.setSpecialValueText("Not set")
        self.ed_made.setToolTip("The day you made it")
        self.ed_notes = QPlainTextEdit()
        self.ed_notes.setPlaceholderText(
            "Notes: the leather and thread you used, hardware, how long it "
            "took, what you'd change next time…")
        self.ed_notes.setTabChangesFocus(True)
        self.ed_notes.setMinimumHeight(90)

        self.lbl_pattern = QLabel()
        self.lbl_pattern.setWordWrap(True)
        self.btn_open_pattern = QPushButton("Open")
        self.btn_open_pattern.setToolTip(
            "Open the pattern in the main window to work on it. Saving it "
            "there (Ctrl+S) keeps your changes here")
        self.btn_open_copy = QPushButton("Open a copy")
        self.btn_open_copy.setToolTip(
            "Open a copy of the pattern as a new, untitled drawing, to start "
            "another piece from it. This piece keeps its own as it is")
        self.btn_keep_drawing = QPushButton("Use the open drawing")
        self.btn_keep_drawing.setToolTip(
            "Make the drawing that is open in the main window this piece's "
            "pattern. From then on, saving that drawing keeps it here")
        self.lbl_pdf = QLabel()
        self.lbl_pdf.setWordWrap(True)
        self.btn_open_pdf = QPushButton("Open")
        self.btn_make_pdf = QPushButton("Make…")
        self.btn_attach_pdf = QPushButton("Attach…")
        self.btn_attach_pdf.setToolTip("Keep a PDF you already have with "
                                       "this piece")
        self.btn_remove_pdf = QPushButton("Remove")
        pat, pdf = QVBoxLayout(), QVBoxLayout()
        for box, label, buttons in (
                (pat, self.lbl_pattern, (self.btn_open_pattern,
                                         self.btn_open_copy,
                                         self.btn_keep_drawing)),
                (pdf, self.lbl_pdf, (self.btn_open_pdf, self.btn_make_pdf,
                                     self.btn_attach_pdf,
                                     self.btn_remove_pdf))):
            row = QHBoxLayout()
            for b in buttons:
                b.setAutoDefault(False)
                row.addWidget(b)
            row.addStretch(1)
            box.setSpacing(4)
            box.addWidget(label)
            box.addLayout(row)

        named = QHBoxLayout()
        named.addWidget(self.ed_title, 1)
        named.addWidget(QLabel("Made"))
        named.addWidget(self.ed_made)
        form = QFormLayout()
        form.addRow("Name", named)
        files = QFormLayout()
        files.addRow("Pattern", pat)
        files.addRow("1:1 PDF", pdf)

        self.btn_folder = QPushButton("Show folder")
        self.btn_folder.setToolTip("Open this piece's folder: its pictures, "
                                   "pattern and PDF are ordinary files")
        self.btn_delete = QPushButton("Delete entry…")
        ends = QHBoxLayout()
        for b in (self.btn_folder, self.btn_delete):
            b.setAutoDefault(False)
        ends.addStretch(1)
        ends.addWidget(self.btn_folder)
        ends.addWidget(self.btn_delete)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.picture, 5)
        right.addWidget(self.strip)
        right.addLayout(pics)
        right.addLayout(form)
        right.addWidget(self.ed_notes, 2)
        right.addLayout(files)
        right.addLayout(ends)
        self.details = QWidget()
        self.details.setLayout(right)
        self.details.setMinimumWidth(420)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left_w)
        split.addWidget(self.details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        # (room for three pieces abreast, to begin with)
        abreast = 3 * (_TILE + 20) + 40
        split.setSizes([abreast, max(420, self.width() - abreast - 30)])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        # -- what does what -----------------------------------------------
        self._notes_soon = QTimer(self)
        self._notes_soon.setSingleShot(True)
        self._notes_soon.setInterval(700)
        self._notes_soon.timeout.connect(self._keep_text)
        self._search_soon = QTimer(self)
        self._search_soon.setSingleShot(True)
        self._search_soon.setInterval(250)
        self._search_soon.timeout.connect(self.reload)
        self._covers_soon = QTimer(self)       # (0: whenever there is time)
        self._covers_soon.timeout.connect(self._next_cover)
        self.search.textChanged.connect(lambda _t: self._search_soon.start())
        self.btn_new.clicked.connect(self.new_entry)
        self.btn_place.clicked.connect(self.change_folder)
        self.wall.currentItemChanged.connect(self._chosen)
        self.strip.currentItemChanged.connect(self._picture_chosen)
        self.picture.opened.connect(self.open_picture)
        self.picture.asked.connect(lambda: self.add_pictures())
        self.btn_add.clicked.connect(lambda: self.add_pictures())
        self.btn_cover.clicked.connect(self.make_cover)
        self.btn_drop.clicked.connect(self.remove_picture)
        self.ed_title.editingFinished.connect(self._keep_text)
        self.ed_made.dateChanged.connect(self._day_changed)
        self.ed_notes.textChanged.connect(self._typing)
        self.btn_open_pattern.clicked.connect(self.open_pattern)
        self.btn_open_copy.clicked.connect(self.open_copy)
        self.btn_keep_drawing.clicked.connect(self.keep_drawing)
        self.btn_open_pdf.clicked.connect(self.open_pdf)
        self.btn_make_pdf.clicked.connect(self.make_pdf)
        self.btn_attach_pdf.clicked.connect(lambda: self.attach_pdf())
        self.btn_remove_pdf.clicked.connect(self.remove_pdf)
        self.btn_folder.clicked.connect(self.show_folder)
        self.btn_delete.clicked.connect(self.delete_entry)
        self.reload(select)
        self.thumbs.tidy()

    # -- asking and telling (tests answer for the user) -------------------

    def _ask(self, title: str, text: str, yes: str = "") -> bool:
        box = QMessageBox(QMessageBox.Question, title, text,
                          QMessageBox.Cancel, self)
        box.addButton(yes or "OK", QMessageBox.AcceptRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is not None \
            and box.buttonRole(box.clickedButton()) == QMessageBox.AcceptRole

    def _tell(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _open_file(self, path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # -- the wall ----------------------------------------------------------

    def reload(self, select: Optional[str] = None) -> None:
        """Read the library again and show it: the piece with the id
        ``select`` chosen, else the one that was, else the first."""
        self._keep_text()
        want = select or (self.current.id if self.current else None)
        words = self.search.text().strip()
        self._items = self.library.items(words)
        if select and words and all(i.id != select for i in self._items):
            # asked for by name, but the search hides it: let go the search
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            words = ""
            self._items = self.library.items()
        self._known = {item.id: item for item in self._items}
        self._filling = True
        self.wall.clear()
        self._entries = {}
        self._waiting = []
        blank = None
        for item in self._items:
            if self._cover_ready(item):
                icon = self._cover(item)
            else:                  # a picture to be read: when there is time
                if blank is None:
                    blank = _icon(_tile(None, _TILE * 2, self.palette(), "…"))
                icon = blank
                self._waiting.append(item.id)
            day = day_text(item.made)
            entry = QListWidgetItem(icon,
                                    item.title + (f"\n{day}" if day else ""))
            entry.setData(Qt.UserRole, item.id)
            entry.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            entry.setToolTip(item.title + (f" — {day}" if day else ""))
            self.wall.addItem(entry)
            self._entries[item.id] = entry
        chosen = self._entries.get(want)
        self._filling = False
        if self._waiting:
            self._covers_soon.start(0)
        else:
            self._covers_soon.stop()
        total = len(self._items)
        if words:
            self.count.setText(f"{total} found")
        else:
            self.count.setText(f"{total} piece{'' if total == 1 else 's'}")
        self.count.setToolTip(str(self.library.root))
        self.wall.setVisible(total > 0)
        self.nothing.setVisible(total == 0)
        self.nothing.setText(
            f"Nothing in the library matches “{words}”." if words else
            "Nothing here yet.\n\nThis is where the things you make are "
            "kept: a picture of each, your notes on it, and the pattern and "
            "1:1 PDF it was made from.\n\nFile → Add to library puts the "
            "drawing you have open here, or start with New entry.")
        if chosen is None and total:
            chosen = self.wall.item(0)
        if chosen is not None:
            self.wall.setCurrentItem(chosen)        # -> _chosen
        else:
            self._show(None)

    @staticmethod
    def _stands_for(item: Item):
        """What a piece's square on the wall is made from: (the file,
        whether that is its pattern, to be drawn). Its cover, or with no
        picture yet its pattern, or (None, False)."""
        if item.cover is not None:
            return item.cover, False
        return item.pattern_path, item.pattern_path is not None

    def _cover_key(self, item: Item) -> str:
        path, drawn = self._stands_for(item)
        key = self.thumbs.key(
            path, f"sketch {_TILE * 2}" if drawn else _TILE * 2) \
            if path is not None else None
        return key or ""

    def _cover_ready(self, item: Item) -> bool:
        """Whether a piece's square can be had at once."""
        path, drawn = self._stands_for(item)
        return path is None or self._cover_key(item) in self._icons \
            or self.thumbs.ready(path, _TILE * 2, drawn)

    def _cover(self, item: Item) -> QIcon:
        """A piece's square on the wall: its cover, or a card that says
        why there is none to see."""
        key = self._cover_key(item)
        icon = self._icons.get(key)
        if icon is None:
            path, drawn = self._stands_for(item)
            if path is None:
                cover = None
            elif drawn:
                cover = self.thumbs.sketch(path, _TILE * 2)
            else:
                cover = self.thumbs.image(path, _TILE * 2)
            words = "No picture yet" if path is None or drawn \
                else "This picture can't be shown here"
            tile = _tile(cover, _TILE * 2, self.palette(), words)
            if not drawn and item.photos and item.is_view(item.photos[0]):
                tile = _badge(tile)
            icon = _icon(tile)
            self._icons[key] = icon
        return icon

    def _next_cover(self) -> None:
        """Make one of the squares still waiting -- one at a time, so that
        the window answers meanwhile."""
        while self._waiting:
            ident = self._waiting.pop(0)
            entry, item = self._entries.get(ident), self._known.get(ident)
            if entry is not None and item is not None:
                entry.setIcon(self._cover(item))
                break
        if not self._waiting:
            self._covers_soon.stop()

    def finish_covers(self) -> None:
        """Make every square still waiting, now."""
        while self._waiting:
            self._next_cover()

    def _item(self, entry: Optional[QListWidgetItem]) -> Optional[Item]:
        return self._known.get(entry.data(Qt.UserRole)) \
            if entry is not None else None

    def _chosen(self, entry, _was=None) -> None:
        if self._filling:
            return
        self._keep_text()
        self._show(self._item(entry))

    def _show(self, item: Optional[Item]) -> None:
        """Fill the right-hand side with a piece (or empty it)."""
        self.current = item
        self._filling = True
        self.details.setEnabled(item is not None)
        self.ed_title.setText(item.title if item else "")
        day = QDate.fromString(item.made, Qt.ISODate) if item else QDate()
        self.ed_made.setDate(day if day.isValid() else _NO_DAY)
        self.ed_notes.setPlainText(item.notes if item else "")
        self._filling = False
        self._fill_pictures()
        self._fill_files()

    def _fill_pictures(self, show: Optional[str] = None) -> None:
        item = self.current
        self._filling = True
        self.strip.clear()
        chosen = None
        for name in (item.photos if item else ()):
            small = self.thumbs.image(item.path(name), _SMALL * 2)
            tile = _tile(small, _SMALL * 2, self.palette(), "?")
            view = item.is_view(name)
            entry = QListWidgetItem(_icon(_badge(tile) if view else tile), "")
            entry.setData(Qt.UserRole, name)
            entry.setToolTip(name + (" — a picture from the 3D assembly "
                                     "preview" if view else ""))
            self.strip.addItem(entry)
            if name == show:
                chosen = entry
        self._filling = False
        self.strip.setVisible(bool(item and len(item.photos) > 1))
        if chosen is None and self.strip.count():
            chosen = self.strip.item(0)
        if chosen is not None:
            self.strip.setCurrentItem(chosen)
        self._picture_chosen(chosen)

    def _picture_chosen(self, entry, _was=None) -> None:
        if self._filling:
            return
        item = self.current
        name = entry.data(Qt.UserRole) if entry is not None else ""
        self._shown = name or ""
        if item is None or not name:
            # no picture yet: the place for them asks for photographs
            self.picture.show_image(None, "", invite=item is not None)
        else:
            img = self.thumbs.image(item.path(name), _LARGE)
            caption = ""
            if item.is_view(name):
                caption = "A 3D view, from the 3D assembly preview"
                if all(item.is_view(n) for n in item.photos):
                    caption += (". No photos of the finished piece yet: "
                                "Add photos…, or drop them here")
            self.picture.show_image(
                img, f"{name} is kept with this piece, but it is a kind of "
                     "picture that can't be shown here.\n\nDouble-click to "
                     "open it in another program.", caption=caption)
        has = bool(item and name)
        self.btn_drop.setEnabled(has)
        self.btn_cover.setEnabled(has and item.photos[0] != name)

    def open_here(self, item: Optional[Item]) -> bool:
        """Whether a piece's pattern is the drawing open in the main
        window -- the one that saving there keeps here."""
        return item is not None and self.host is not None \
            and getattr(self.host, "_library_id", None) == item.id

    def _fill_files(self) -> None:
        item = self.current
        host = self.host is not None
        pattern = bool(item and item.pattern_path
                       and item.pattern_path.is_file())
        pdf = bool(item and item.pdf_path and item.pdf_path.is_file())
        self.lbl_pattern.setText(
            (item.pattern + (" — open in the main window"
                             if self.open_here(item) else ""))
            if pattern else "None kept")
        self.lbl_pattern.setEnabled(pattern)
        stale = pdf and item.pdf_outdated
        self.lbl_pdf.setText(
            (item.pdf + (" — made before the pattern was last changed"
                         if stale else "")) if pdf else "None kept")
        self.lbl_pdf.setStyleSheet("color: #c27c0e;" if stale else "")
        self.lbl_pdf.setEnabled(pdf)
        self.btn_make_pdf.setText("Make again…" if stale else "Make…")
        self.btn_make_pdf.setToolTip(
            "The pattern has been changed since this PDF was made: make it "
            "again from the pattern as it is now" if stale else
            "Make the 1:1 PDF of this piece's pattern and keep it here")
        self.btn_open_pattern.setEnabled(pattern and host)
        self.btn_open_copy.setEnabled(pattern and host)
        self.btn_keep_drawing.setEnabled(bool(item) and host)
        self.btn_open_pdf.setEnabled(pdf)
        self.btn_make_pdf.setEnabled(pattern and host)
        self.btn_remove_pdf.setEnabled(pdf)
        self.btn_attach_pdf.setEnabled(bool(item))

    def _retitle(self, item: Item) -> None:
        """Bring a piece's square on the wall up to date."""
        entry = self._entries.get(item.id)
        if entry is None:
            return
        day = day_text(item.made)
        entry.setText(item.title + (f"\n{day}" if day else ""))
        entry.setToolTip(item.title + (f" — {day}" if day else ""))
        entry.setIcon(self._cover(item))
        if item.id in self._waiting:
            self._waiting.remove(item.id)

    # -- what you type is kept --------------------------------------------

    def _typing(self) -> None:
        if not self._filling and self.current is not None:
            self._notes_soon.start()

    def _day_changed(self, _day) -> None:
        if not self._filling:
            self._keep_text()

    def _keep_text(self) -> None:
        """Write the name, the day and the notes if they have changed."""
        self._notes_soon.stop()
        item = self.current
        if item is None or self._filling:
            return
        day = self.ed_made.date()
        made = "" if day == _NO_DAY else day.toString(Qt.ISODate)
        notes = self.ed_notes.toPlainText()
        title = " ".join(self.ed_title.text().split()) or "Untitled"
        if title != self.ed_title.text():
            self._filling = True
            self.ed_title.setText(title)
            self._filling = False
        if (title, made, notes) == (item.title, item.made, item.notes):
            return
        item.made, item.notes = made, notes
        try:
            if title != item.title:
                item.rename(title)                 # saves, too
            else:
                item.save()
        except OSError as err:
            self._tell("Library", f"Couldn't save “{item.title}”:\n{err}")
            return
        self._retitle(item)
        if self.host is not None and hasattr(self.host, "library_renamed"):
            self.host.library_renamed(item)        # (its title bar)

    # -- entries -----------------------------------------------------------

    def new_entry(self) -> Optional[Item]:
        """Start an entry and put the cursor in its name."""
        self._keep_text()
        try:
            item = self.library.add("Untitled")
        except OSError as err:
            self._tell("Library", "Couldn't make an entry in "
                                  f"{self.library.root}:\n{err}")
            return None
        if self.search.text():
            self.search.clear()                    # or it might not show
        self.reload(select=item.id)
        self.ed_title.setFocus()
        self.ed_title.selectAll()
        return item

    def delete_entry(self) -> None:
        item = self.current
        if item is None:
            return
        self._keep_text()
        n = len(item.photos)
        with_it = ", ".join(
            x for x in (f"{n} picture{'' if n == 1 else 's'}" if n else "",
                        "its pattern" if item.pattern else "",
                        "its PDF" if item.pdf else "") if x)
        if not self._ask(
                "Delete entry",
                f"Delete “{item.title}” from the library"
                + (f", with {with_it}" if with_it else "") + "?\n\n"
                "It goes to the recycle bin.", "Delete"):
            return
        folder = str(item.folder)
        binned = self._trash(folder)
        if isinstance(binned, tuple):          # (done, where it went)
            binned = binned[0]
        if not binned and os.path.exists(folder):
            if not self._ask(
                    "Delete entry",
                    "It couldn't be moved to the recycle bin.\n\nDelete "
                    f"“{item.title}” for good instead?", "Delete for good"):
                return
            try:
                self.library.delete(item)
            except (OSError, ValueError) as err:
                self._tell("Library", f"Couldn't delete it:\n{err}")
                return
        self.current = None
        self.reload()
        if self.host is not None and hasattr(self.host, "library_deleted"):
            self.host.library_deleted(item.id)

    def show_folder(self) -> None:
        if self.current is not None:
            self._open_file(self.current.folder)

    def change_folder(self) -> None:
        """Say where the library is, and offer to keep it elsewhere."""
        self._keep_text()
        was = self.library.root
        chosen = QFileDialog.getExistingDirectory(
            self, f"The library is in {was} — choose a folder to keep it in",
            str(was if was.is_dir() else was.parent))
        if chosen:
            self.use_folder(Path(chosen))

    def use_folder(self, folder: Path) -> None:
        """Keep the library in ``folder`` from now on. The pieces go with
        it, if you say so -- unless that folder is a library already."""
        was = self.library.root
        if folder.resolve() == was.resolve():
            return
        here = len(self.library.items())
        there = len(Library(folder).items())
        try:
            if here and not there and self._ask(
                    "Library folder",
                    f"Move the {here} piece{'' if here == 1 else 's'} in "
                    f"the library to\n{folder}?\n\nIf not, they stay where "
                    "they are and the library starts empty in the new "
                    "folder.", "Move them"):
                self.library.move_to(folder)
            else:
                self.library = Library(folder)
        except OSError as err:
            self._tell("Library folder", f"Couldn't move them:\n{err}")
            self.library = Library(was)
        if self._settings is not None:
            self._settings.setValue("libraryFolder", str(self.library.root))
        self.current = None
        self.reload()
        if self.host is not None and hasattr(self.host, "library_moved"):
            self.host.library_moved()

    # -- pictures ----------------------------------------------------------

    def add_pictures(self, paths=None) -> List[str]:
        """Copy pictures into the chosen piece: the ones given, or the ones
        picked in a file box. Returns the names they were given."""
        item = self.current
        if item is None:
            return []
        if paths is None:
            ends = " ".join(f"*{e}" for e in picture_endings())
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Add photos of the finished piece", self._last_folder(),
                f"Pictures ({ends});;All files (*)")
            if paths and self._settings is not None:
                self._settings.setValue("libraryPictures",
                                        os.path.dirname(paths[0]))
        added, strange = [], []
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:                       # (copying, then reading each one small)
            for path in paths or ():
                if not str(path).lower().endswith(PICTURES):
                    strange.append(os.path.basename(str(path)))
                    continue
                try:
                    added.append(item.add_photo(path))
                except (OSError, ValueError) as err:
                    strange.append(f"{os.path.basename(str(path))} ({err})")
            if added:
                self._fill_pictures(show=added[-1])
                self._retitle(item)
        finally:
            QGuiApplication.restoreOverrideCursor()
        unseen = [n for n in added if not can_show(item.path(n))]
        if strange or unseen:
            self._tell(
                "Add photos",
                ("These aren't pictures, and were left out:\n"
                 + "\n".join(strange) + "\n\n" if strange else "")
                + ("These are kept with the piece, but are a kind of "
                   "picture that can't be shown here (a phone's HEIC "
                   "photograph, say — save it as a JPEG to see it):\n"
                   + "\n".join(unseen) if unseen else ""))
        return added

    def _last_folder(self) -> str:
        if self._settings is not None:
            got = self._settings.value("libraryPictures", "", type=str)
            if got and os.path.isdir(got):
                return got
        return QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation)

    def paste_picture(self) -> bool:
        """Add the picture on the clipboard to the chosen piece (or the
        picture files copied in a file manager)."""
        item = self.current
        if item is None:
            return False
        data = QGuiApplication.clipboard().mimeData()
        if data is None:                       # an empty clipboard
            return False
        files = [u.toLocalFile() for u in data.urls() if u.isLocalFile()] \
            if data.hasUrls() else []
        if files:
            return bool(self.add_pictures(files))
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            return False
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            if not img.save(tmp, "PNG"):
                return False
            name = item.add_photo(tmp, name="Pasted picture.png")
        except (OSError, ValueError) as err:
            self._tell("Paste", f"Couldn't keep the picture:\n{err}")
            return False
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self._fill_pictures(show=name)
        self._retitle(item)
        return True

    def make_cover(self) -> None:
        item = self.current
        if item is not None and self._shown:
            item.set_cover(self._shown)
            self._fill_pictures(show=self._shown)
            self._retitle(item)

    def remove_picture(self) -> None:
        item = self.current
        name = self._shown
        if item is None or not name:
            return
        if not self._ask("Remove picture",
                         f"Remove {name} from “{item.title}”?\n\nThe "
                         "library's copy of it is deleted.", "Remove"):
            return
        item.remove_photo(name)
        self._fill_pictures()
        self._retitle(item)

    def open_picture(self) -> None:
        if self.current is not None and self._shown:
            self._open_file(self.current.path(self._shown))

    # -- the pattern and its PDF ------------------------------------------

    def open_pattern(self) -> None:
        """Open the chosen piece's pattern in the main window to work on --
        saving it there keeps it here -- and get out of its way."""
        item = self.current
        if item is None or self.host is None or item.pattern_path is None \
                or not item.pattern_path.is_file():
            return
        self._keep_text()
        if self.host.library_open_pattern(item):
            self.close()

    def open_copy(self) -> None:
        """Open a copy of the chosen piece's pattern in the main window,
        as a new drawing that is no piece's yet."""
        item = self.current
        if item is None or self.host is None or item.pattern_path is None \
                or not item.pattern_path.is_file():
            return
        self._keep_text()
        if self.host.library_open_copy(item):
            self.close()

    def keep_drawing(self) -> None:
        item = self.current
        if item is None or self.host is None:
            return
        self._keep_text()
        if item.pattern and not self.open_here(item) and not self._ask(
                "Use the open drawing",
                f"“{item.title}” has a pattern already.\n\nReplace it with "
                "the drawing that is open in the main window? From then on, "
                "saving that drawing keeps it here.", "Replace"):
            return
        if self.host.library_keep_drawing(item):
            self._fill_files()

    def open_pdf(self) -> None:
        item = self.current
        if item is not None and item.pdf_path is not None:
            self._open_file(item.pdf_path)

    def make_pdf(self) -> None:
        item = self.current
        if item is None or self.host is None:
            return
        self._keep_text()
        if self.host.library_make_pdf(item):
            self._fill_files()

    def attach_pdf(self, path=None) -> bool:
        item = self.current
        if item is None:
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Attach a PDF", "", "PDF (*.pdf)")
            if not path:
                return False
        if not str(path).lower().endswith(".pdf"):
            self._tell("Attach a PDF", f"{os.path.basename(str(path))} "
                                       "isn't a PDF.")
            return False
        self._keep_text()
        try:
            item.attach_pdf(path)
        except OSError as err:
            self._tell("Attach a PDF", f"Couldn't keep it:\n{err}")
            return False
        self._fill_files()
        return True

    def remove_pdf(self) -> None:
        item = self.current
        if item is None or not item.pdf:
            return
        if self._ask("Remove PDF",
                     f"Remove {item.pdf} from “{item.title}”?\n\nThe "
                     "library's copy of it is deleted.", "Remove"):
            item.remove_pdf()
            self._fill_files()

    # -- dropping, pasting, closing ---------------------------------------

    @staticmethod
    def _dropped(event) -> List[str]:
        data = event.mimeData()
        if data is None or not data.hasUrls():
            return []
        return [u.toLocalFile() for u in data.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event):
        if any(f.lower().endswith(PICTURES + (".pdf",))
               for f in self._dropped(event)):
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Pictures and a PDF dropped on the window go to the chosen piece;
        with none chosen, they start an entry of their own."""
        files = self._dropped(event)
        pictures = [f for f in files if f.lower().endswith(PICTURES)]
        pdfs = [f for f in files if f.lower().endswith(".pdf")]
        if (pictures or pdfs) and self.current is None \
                and self.new_entry() is None:
            return
        if pictures:
            self.add_pictures(pictures)
        if pdfs:
            self.attach_pdf(pdfs[0])
        event.acceptProposedAction()

    def keyPressEvent(self, event):
        # (a box you can type in takes Ctrl+V for itself: this is for when
        # the cursor is anywhere else)
        if event.matches(QKeySequence.Paste):
            self.paste_picture()
            return
        super().keyPressEvent(event)

    def _leave(self) -> None:
        self._keep_text()
        self._covers_soon.stop()
        self._waiting = []

    def done(self, result):
        self._leave()
        super().done(result)

    def closeEvent(self, event):
        self._leave()
        super().closeEvent(event)

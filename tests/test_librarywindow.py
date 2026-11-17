"""The library window, and the library in the main window: a wall of the
pieces you have made, each with its pictures, notes, pattern and 1:1 PDF."""

import os
import shutil
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import (QDate, QEvent, QMimeData, QPoint,  # noqa: E402
                            QSettings, Qt, QUrl)
from PySide6.QtGui import (QColor, QDropEvent, QGuiApplication,  # noqa: E402
                           QImage)
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.library import Library  # noqa: E402
from leathercad.shapes import Rectangle  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _picture(path, colour=(200, 40, 40), size=(640, 480)):
    """A plain picture file of one colour."""
    img = QImage(size[0], size[1], QImage.Format_RGB32)
    img.fill(QColor(*colour))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path))
    return path


def _drawing(path=None):
    """A drawing with one piece in it (saved to ``path``, if given)."""
    doc = Document("Test piece")
    r = Rectangle(width=90, height=60, name="Panel")
    r.transform.x, r.transform.y = 45, 30
    doc.shapes.append(r)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(path))
    return doc


class _Answers:
    """Stands in for the user at the window's questions and messages."""

    def __init__(self, win, yes=True):
        self.yes = yes
        self.asked, self.told, self.opened = [], [], []
        win._ask = self._ask
        win._tell = lambda title, text: self.told.append((title, text))
        win._open_file = lambda path: self.opened.append(str(path))

    def _ask(self, title, text, yes=""):
        self.asked.append((title, text))
        return self.yes(title, text) if callable(self.yes) else self.yes


def _open(tmp_path, *, host=None, settings=None, trash=None, wait=True):
    """The library window on the library in ``tmp_path``, with every
    picture on its wall made (unless told not to ``wait`` for them)."""
    from leathercad_app.librarywindow import LibraryWindow, Thumbs
    win = LibraryWindow(root=tmp_path / "lib", host=host, settings=settings,
                        thumbs=Thumbs(tmp_path / "thumbs"),
                        trash=trash or (lambda folder: False))
    win.show()
    if wait:
        win.finish_covers()
    return win, _Answers(win)


def _near(c, rgb, tol=40):
    return abs(c.red() - rgb[0]) + abs(c.green() - rgb[1]) \
        + abs(c.blue() - rgb[2]) < tol


def _tile(win, row):
    """The picture a piece has on the wall."""
    return win.wall.item(row).icon().pixmap(336, 336).toImage()


def _dark(img, step=4):
    """How many of the points looked at in a tile are dark."""
    return sum(1 for x in range(20, 316, step) for y in range(20, 316, step)
               if img.pixelColor(x, y).lightness() < 140)


# -- the wall ----------------------------------------------------------------

def test_an_empty_library_says_how_to_start(qapp, tmp_path):
    win, _user = _open(tmp_path)
    try:
        assert win.wall.count() == 0 and win.current is None
        assert not win.wall.isVisible() and win.nothing.isVisible()
        assert "Add to library" in win.nothing.text()
        assert not win.details.isEnabled()
        assert win.count.text() == "0 pieces"
        # New entry makes one, chosen, with its name ready to type over
        item = win.new_entry()
        assert item is not None and win.current.id == item.id
        assert win.wall.count() == 1 and win.wall.isVisible()
        assert win.details.isEnabled() and not win.nothing.isVisible()
        assert win.ed_title.text() == "Untitled"
        assert win.ed_title.selectedText() == "Untitled"
        assert win.ed_made.date() == QDate.currentDate()
        assert win.count.text() == "1 piece"
        assert "No picture yet" in win.picture.hint
    finally:
        win.close()


def test_the_wall_shows_the_pieces_latest_first_each_with_its_cover(
        qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    belt = shelf.add("Belt", made="2024-05-01")
    wallet = shelf.add("Card wallet", made="2025-02-20", notes="Veg tan")
    wallet.add_photo(_picture(tmp_path / "a.jpg", (200, 40, 40)))
    wallet.add_photo(_picture(tmp_path / "b.png", (40, 40, 200)))
    tray = shelf.add("Tray", made="2024-09-09")
    _drawing(tmp_path / "t.leathercad.json")
    tray.attach_pattern(tmp_path / "t.leathercad.json")
    win, _user = _open(tmp_path)
    try:
        assert [win.wall.item(i).text().split("\n")[0]
                for i in range(3)] == ["Card wallet", "Tray", "Belt"]
        assert "20 Feb 2025" in win.wall.item(0).text()
        assert win.count.text() == "3 pieces"
        # the first is chosen and shown
        assert win.current.id == wallet.id
        assert win.ed_title.text() == "Card wallet"
        assert win.ed_notes.toPlainText() == "Veg tan"
        assert win.ed_made.date() == QDate(2025, 2, 20)
        assert win.picture.has_image() and win.strip.count() == 2
        # its cover is its first picture
        assert _near(_tile(win, 0).pixelColor(168, 168), (200, 40, 40))
        # with no picture, a piece is shown by its pattern, drawn: dark
        # lines on white
        sketch = _tile(win, 1)
        assert 0 < _dark(sketch) < 800
        assert _near(sketch.pixelColor(168, 168), (255, 255, 255), 30)
        # ... and with neither, by a plain card that says so: one colour
        # all over but for the words across its middle
        card = _tile(win, 2)
        paper = card.pixelColor(168, 60)
        assert all(card.pixelColor(x, y) == paper
                   for x in range(20, 316, 8) for y in (30, 60, 280, 300))
        words = sum(1 for x in range(60, 276) for y in range(140, 196)
                    if card.pixelColor(x, y) != paper)
        assert words > 200
        assert card != sketch and card != _tile(win, 0)
        # choosing another shows that one
        win.wall.setCurrentRow(2)
        assert win.current.id == belt.id and win.ed_title.text() == "Belt"
        assert not win.picture.has_image() and not win.strip.isVisible()
        assert win.lbl_pattern.text() == "None kept"
        win.wall.setCurrentRow(1)
        assert win.lbl_pattern.text() == "Tray.leathercad.json"
    finally:
        win.close()


def test_searching_narrows_the_wall(qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    shelf.add("Belt", notes="Bridle leather, brass buckle")
    shelf.add("Card wallet", notes="Veg tan, blue thread")
    win, _user = _open(tmp_path)
    try:
        win.search.setText("brass")
        win._search_soon.timeout.emit()
        assert win.wall.count() == 1 and win.current.title == "Belt"
        assert win.count.text() == "1 found"
        win.search.setText("suede")
        win._search_soon.timeout.emit()
        assert win.wall.count() == 0 and win.current is None
        assert "suede" in win.nothing.text() and win.nothing.isVisible()
        assert not win.details.isEnabled()
        # a new entry would be hidden by the search: so the search is let go
        item = win.new_entry()
        assert win.search.text() == "" and win.wall.count() == 3
        assert win.current.id == item.id
        # ... and so it is when a piece is asked for that the search hides
        win.search.setText("brass")
        win._search_soon.timeout.emit()
        win.reload(select=item.id)
        assert win.search.text() == "" and win.current.id == item.id
    finally:
        win.close()


def test_pictures_on_the_wall_are_read_as_there_is_time(qapp, tmp_path):
    """A picture not yet read small takes a moment to read: a library
    opened for the first time shows its wall at once and fills the pictures
    in after, one at a time. From then on they are there at once."""
    shelf = Library(tmp_path / "lib")
    for i, colour in enumerate([(200, 40, 40), (40, 160, 60), (40, 40, 200)]):
        item = shelf.add(f"Piece {i}", made=f"2025-01-0{3 - i}")
        item.add_photo(_picture(tmp_path / f"{i}.jpg", colour))
    bare = shelf.add("No picture", made="2024-01-01")
    win, _user = _open(tmp_path, wait=False)
    try:
        assert win.wall.count() == 4 and win._waiting == [
            it.id for it in shelf.items()[:3]]
        assert win._covers_soon.isActive()
        # the chosen piece is shown in full meanwhile
        assert win.current.title == "Piece 0" and win.picture.has_image()
        # the squares still to come are all the same plain card
        waiting = _tile(win, 0)
        assert _tile(win, 1) == waiting and _tile(win, 2) == waiting
        assert not _near(waiting.pixelColor(168, 100), (200, 40, 40))
        # (a piece with no picture has nothing to wait for)
        assert _tile(win, 3) != waiting and bare.id not in win._waiting
        # one at a time, the topmost first
        win._covers_soon.timeout.emit()
        assert _near(_tile(win, 0).pixelColor(168, 168), (200, 40, 40))
        assert _tile(win, 1) == waiting and len(win._waiting) == 2
        win._covers_soon.timeout.emit()
        win._covers_soon.timeout.emit()
        assert _near(_tile(win, 1).pixelColor(168, 168), (40, 160, 60))
        assert _near(_tile(win, 2).pixelColor(168, 168), (40, 40, 200))
        assert win._waiting == [] and not win._covers_soon.isActive()
        # reading the library again has them at once
        win.reload()
        assert win._waiting == []
        assert _near(_tile(win, 2).pixelColor(168, 168), (40, 40, 200))
    finally:
        win.close()
    # ... and so has the window, the next time it is opened
    win, _user = _open(tmp_path, wait=False)
    try:
        assert win._waiting == [] and not win._covers_soon.isActive()
        assert _near(_tile(win, 1).pixelColor(168, 168), (40, 160, 60))
        # a picture changed outside is read again
        _picture(shelf.items()[0].cover, (250, 220, 30))
        win.reload()
        assert win._waiting == [shelf.items()[0].id]
        win.finish_covers()
        assert _near(_tile(win, 0).pixelColor(168, 168), (250, 220, 30))
        # closing with some still to come leaves nothing running
        _picture(shelf.items()[1].cover, (20, 20, 20))
        win.reload()
        assert win._covers_soon.isActive()
    finally:
        win.close()
    assert not win._covers_soon.isActive() and win._waiting == []


# -- what you type is kept ---------------------------------------------------

def test_what_is_typed_is_kept_without_saving(qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    a = shelf.add("Wallet", made="2025-01-01")
    b = shelf.add("Belt", made="2024-01-01")
    win, _user = _open(tmp_path)
    try:
        assert win.current.id == a.id
        # the notes: a moment after the last key
        win.ed_notes.setPlainText("0.45 mm thread")
        assert win._notes_soon.isActive()
        assert shelf.find(a.id).notes == ""
        win._notes_soon.timeout.emit()
        assert shelf.find(a.id).notes == "0.45 mm thread"
        # the name: when you leave the box. The folder follows it
        win.ed_title.setText("  Bifold   wallet ")
        win.ed_title.editingFinished.emit()
        back = shelf.find(a.id)
        assert back.title == "Bifold wallet"
        assert back.folder.name == "Bifold wallet"
        assert win.ed_title.text() == "Bifold wallet"
        assert win.wall.item(0).text().startswith("Bifold wallet\n")
        # the day, at once; and no day at all
        win.ed_made.setDate(QDate(2025, 3, 9))
        assert shelf.find(a.id).made == "2025-03-09"
        assert "9 Mar 2025" in win.wall.item(0).text()
        win.ed_made.setDate(win.ed_made.minimumDate())
        assert shelf.find(a.id).made == ""
        assert win.ed_made.text() == "Not set"
        # choosing another piece keeps what was being typed, without the wait
        win.ed_notes.setPlainText("typed and left")
        win.wall.setCurrentRow(1)
        assert win.current.id == b.id
        assert shelf.find(a.id).notes == "typed and left"
        assert win.ed_notes.toPlainText() == ""
        # ... and so does closing the window
        win.ed_notes.setPlainText("typed and closed")
    finally:
        win.close()
    assert shelf.find(b.id).notes == "typed and closed"
    # a name left empty is Untitled, not nothing
    win, _user = _open(tmp_path)
    try:
        win.ed_title.setText("   ")
        win.ed_title.editingFinished.emit()
        assert win.current.title == "Untitled"
        assert win.ed_title.text() == "Untitled"
    finally:
        win.close()


# -- pictures ----------------------------------------------------------------

def test_pictures_are_added_shown_made_the_cover_and_removed(qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    win, user = _open(tmp_path)
    try:
        red = _picture(tmp_path / "phone" / "IMG_1.jpg", (200, 40, 40),
                       (2000, 1500))
        blue = _picture(tmp_path / "phone" / "IMG_2.png", (40, 40, 200))
        assert win.add_pictures([str(red), str(blue)]) \
            == ["IMG_1.jpg", "IMG_2.png"]
        assert shelf.find(item.id).photos == ["IMG_1.jpg", "IMG_2.png"]
        assert red.exists()                         # copied, not moved
        # the one just added is shown; the strip has both
        assert win.strip.isVisible() and win.strip.count() == 2
        assert win._shown == "IMG_2.png" and win.picture.has_image()
        assert _near(win.picture._img.pixelColor(5, 5), (40, 40, 200))
        # the wall shows the cover: the first
        assert _near(_tile(win, 0).pixelColor(168, 168), (200, 40, 40))
        assert win.btn_cover.isEnabled() and win.btn_drop.isEnabled()
        # each is read small, once, and the small copy kept: the two in
        # the strip, the cover for the wall, and the one shown large
        assert len(list((tmp_path / "thumbs").glob("*.jpg"))) == 4
        # make the second the cover
        win.make_cover()
        assert shelf.find(item.id).photos == ["IMG_2.png", "IMG_1.jpg"]
        assert _near(_tile(win, 0).pixelColor(168, 168), (40, 40, 200))
        assert not win.btn_cover.isEnabled()        # it is the cover now
        # clicking one in the strip shows it large
        win.strip.setCurrentRow(1)
        assert win._shown == "IMG_1.jpg"
        assert _near(win.picture._img.pixelColor(5, 5), (200, 40, 40))
        # double-clicking the large picture opens the file itself
        win.picture.opened.emit()
        assert user.opened == [str(item.folder / "IMG_1.jpg")]
        # removing asks first
        user.yes = False
        win.remove_picture()
        assert len(shelf.find(item.id).photos) == 2 and user.asked
        user.yes = True
        win.remove_picture()
        assert shelf.find(item.id).photos == ["IMG_2.png"]
        assert not (item.folder / "IMG_1.jpg").exists()
        assert not win.strip.isVisible() and win._shown == "IMG_2.png"
        win.remove_picture()
        assert shelf.find(item.id).photos == [] and not win.picture.has_image()
        assert not win.btn_drop.isEnabled()
    finally:
        win.close()


def test_files_that_are_not_pictures_and_pictures_that_cannot_be_shown(
        qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    win, user = _open(tmp_path)
    try:
        notes = tmp_path / "notes.txt"
        notes.write_text("x")
        heic = tmp_path / "IMG_9.heic"              # as an iPhone saves them
        heic.write_bytes(b"not something Qt reads")
        assert win.add_pictures([str(notes), str(heic)]) == ["IMG_9.heic"]
        (title, text), = user.told
        assert "notes.txt" in text and "left out" in text
        assert "IMG_9.heic" in text and "can't be shown" in text
        # it is kept all the same, and says so where the picture would be
        assert shelf.find(item.id).photos == ["IMG_9.heic"]
        assert not win.picture.has_image()
        assert "can't be shown" in win.picture.hint
        # nothing chosen in the file box: nothing happens
        assert win.add_pictures([]) == []
    finally:
        win.close()


def test_pictures_can_be_dropped_on_the_window_and_pasted(qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    win, user = _open(tmp_path)
    try:
        pic = _picture(tmp_path / "drop.jpg", (30, 160, 60))
        pdf = tmp_path / "pattern.pdf"
        pdf.write_bytes(b"%PDF-1.4 dropped")
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(pic)),
                      QUrl.fromLocalFile(str(pdf))])
        win.dropEvent(QDropEvent(QPoint(50, 50), Qt.CopyAction, data,
                                 Qt.LeftButton, Qt.NoModifier))
        back = shelf.find(item.id)
        assert back.photos == ["drop.jpg"] and back.pdf == "Wallet.pdf"
        assert back.pdf_path.read_bytes() == b"%PDF-1.4 dropped"
        assert win.lbl_pdf.text() == "Wallet.pdf"
        # a picture on the clipboard
        img = QImage(300, 200, QImage.Format_RGB32)
        img.fill(QColor(250, 200, 20))
        QGuiApplication.clipboard().setImage(img)
        assert win.paste_picture()
        back = shelf.find(item.id)
        assert back.photos == ["drop.jpg", "Pasted picture.png"]
        assert win._shown == "Pasted picture.png"
        assert _near(win.picture._img.pixelColor(10, 10), (250, 200, 20))
        # nothing to paste
        QGuiApplication.clipboard().clear()
        assert not win.paste_picture()
        assert len(shelf.find(item.id).photos) == 2 and not user.told
    finally:
        win.close()


def test_a_picture_dropped_on_an_empty_library_starts_an_entry(qapp,
                                                              tmp_path):
    win, user = _open(tmp_path)
    try:
        assert win.current is None
        pic = _picture(tmp_path / "wallet done.jpg", (30, 160, 60))
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(pic))])
        win.dropEvent(QDropEvent(QPoint(50, 50), Qt.CopyAction, data,
                                 Qt.LeftButton, Qt.NoModifier))
        (item,) = win.library.items()
        assert item.photos == ["wallet done.jpg"]
        assert win.current.id == item.id and win.picture.has_image()
        assert win.ed_title.selectedText() == "Untitled"    # to type over
        assert _near(_tile(win, 0).pixelColor(168, 168), (30, 160, 60))
        # something that is neither picture nor PDF starts nothing
        win.wall.setCurrentRow(-1)
        other = tmp_path / "notes.txt"
        other.write_text("x")
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(other))])
        win.dropEvent(QDropEvent(QPoint(50, 50), Qt.CopyAction, data,
                                 Qt.LeftButton, Qt.NoModifier))
        assert len(win.library.items()) == 1 and not user.told
    finally:
        win.close()


def test_small_copies_of_pictures_and_drawings_of_patterns(qapp, tmp_path):
    from leathercad_app.librarywindow import Thumbs
    thumbs = Thumbs(tmp_path / "t")
    big = _picture(tmp_path / "big.jpg", (10, 120, 200), (3000, 2000))
    small = thumbs.image(big, 200)
    assert (small.width(), small.height()) == (200, 133)
    assert _near(small.pixelColor(100, 60), (10, 120, 200))
    kept = list((tmp_path / "t").iterdir())
    assert len(kept) == 1
    # asked again -- by another window, another day -- it is read from there
    again = Thumbs(tmp_path / "t").image(big, 200)
    assert again.size() == small.size()
    assert len(list((tmp_path / "t").iterdir())) == 1
    # a picture never grows; one that isn't there, or isn't one, is None
    tiny = _picture(tmp_path / "tiny.png", (5, 5, 5), (40, 30))
    assert thumbs.image(tiny, 200).width() == 40
    assert thumbs.image(tmp_path / "gone.jpg", 200) is None
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"junk")
    assert thumbs.image(junk, 200) is None
    # a phone held upright says so in its photograph, and it is turned
    # to match: here 400 x 200 as stored, to be seen 200 x 400, the red
    # end at the top
    from PySide6.QtGui import QPainter
    lying = QImage(400, 200, QImage.Format_RGB32)
    lying.fill(QColor(20, 20, 200))
    p = QPainter(lying)
    p.fillRect(0, 0, 120, 200, QColor(200, 20, 20))     # its left end
    p.end()
    assert lying.save(str(tmp_path / "lying.jpg"))
    stored = (tmp_path / "lying.jpg").read_bytes()
    turn = (b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x01"   # one tag:
            b"\x01\x12\x00\x03\x00\x00\x00\x01\x00\x06\x00\x00"    # turn it
            b"\x00\x00\x00\x00")                              # a quarter
    (tmp_path / "upright.jpg").write_bytes(
        stored[:2] + b"\xff\xe1" + (len(turn) + 2).to_bytes(2, "big") + turn
        + stored[2:])
    flat = thumbs.image(tmp_path / "lying.jpg", 200)
    assert (flat.width(), flat.height()) == (200, 100)
    stood = thumbs.image(tmp_path / "upright.jpg", 200)
    assert (stood.width(), stood.height()) == (100, 200)
    assert _near(stood.pixelColor(50, 20), (200, 20, 20))
    assert _near(stood.pixelColor(50, 180), (20, 20, 200))
    big = thumbs.image(tmp_path / "upright.jpg", 1000)      # not made small
    assert (big.width(), big.height()) == (200, 400)
    # see-through parts come out white, as on paper
    clear = QImage(100, 100, QImage.Format_ARGB32)
    clear.fill(Qt.transparent)
    clear.save(str(tmp_path / "clear.png"))
    assert _near(thumbs.image(tmp_path / "clear.png", 64).pixelColor(30, 30),
                 (255, 255, 255), 10)
    # a pattern, drawn: its outline in the square
    doc = _drawing()
    slot = Rectangle(width=10, height=10, name="Slot")   # top left of it
    slot.transform.x, slot.transform.y = 15, 50
    doc.shapes.append(slot)
    doc.save(str(tmp_path / "p.leathercad.json"))
    sketch = thumbs.sketch(tmp_path / "p.leathercad.json", 300)
    assert sketch.width() == sketch.height() == 300

    def dark(x, y):
        return sketch.pixelColor(x, y).lightness() < 140

    rows = [y for y in range(300) if any(dark(x, y)
                                         for x in range(0, 300, 3))]
    cols = [x for x in range(300) if any(dark(x, y)
                                         for y in range(0, 300, 3))]
    # 90 x 60 mm: as wide as the square allows, two thirds as tall
    assert cols[0] < 30 and cols[-1] > 270
    assert 40 < rows[0] < 70 and 230 < rows[-1] < 260
    # ... and the way it is drawn on the screen, where y runs up the page:
    # the slot is at the top left, not the bottom left
    assert sum(dark(x, y) for x in range(45, 88) for y in range(73, 116)) \
        > 60
    assert not any(dark(x, y) for x in range(45, 88)
                   for y in range(185, 228))
    assert thumbs.sketch(junk, 300) is None
    assert thumbs.sketch(tmp_path / "gone.json", 300) is None
    empty = tmp_path / "empty.leathercad.json"
    Document().save(str(empty))
    assert thumbs.sketch(empty, 300) is None


def test_small_copies_not_asked_for_in_a_long_while_are_thrown_away(
        qapp, tmp_path):
    from leathercad_app.librarywindow import Thumbs
    store = tmp_path / "t"
    thumbs = Thumbs(store)
    a = _picture(tmp_path / "a.jpg", (10, 120, 200), (900, 600))
    b = _picture(tmp_path / "b.jpg", (200, 120, 10), (900, 600))
    _drawing(tmp_path / "p.leathercad.json")
    # whether one is made already (so that asking costs nothing)
    assert not thumbs.ready(a, 200)
    assert not thumbs.ready(tmp_path / "p.leathercad.json", 200, True)
    thumbs.image(a, 200)
    thumbs.image(b, 200)
    thumbs.sketch(tmp_path / "p.leathercad.json", 200)
    assert thumbs.ready(a, 200) and Thumbs(store).ready(a, 200)
    assert Thumbs(store).ready(tmp_path / "p.leathercad.json", 200, True)
    assert not thumbs.ready(a, 300)            # another size is another copy
    assert thumbs.ready(tmp_path / "gone.jpg", 200)     # nothing to make
    kept = sorted(store.iterdir())
    assert len(kept) == 3
    # nothing is old yet
    assert thumbs.tidy() == 0 and len(list(store.iterdir())) == 3
    # make them all four months old, and put a stranger among them
    old = time.time() - 120 * 86400
    for f in kept:
        os.utime(f, (old, old))
    stranger = store / "holiday.jpg"
    stranger.write_bytes(b"not one of ours")
    os.utime(stranger, (old, old))
    # one of them is asked for again -- by a window opened today
    assert Thumbs(store).image(a, 200) is not None
    assert Thumbs(store).tidy() == 2
    left = sorted(p.name for p in store.iterdir())
    assert len(left) == 2 and "holiday.jpg" in left
    assert Thumbs(store).ready(a, 200) and not Thumbs(store).ready(b, 200)
    # ... and whatever was thrown away is simply made again
    assert Thumbs(store).image(b, 200).width() == 200
    # no folder at all is no trouble
    assert Thumbs(tmp_path / "nowhere").tidy() == 0


# -- the PDF, the folder, deleting -------------------------------------------

def test_a_pdf_is_attached_opened_and_removed(qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    win, user = _open(tmp_path)
    try:
        assert win.lbl_pdf.text() == "None kept"
        assert not win.btn_open_pdf.isEnabled()
        assert not win.btn_remove_pdf.isEnabled()
        # (without the main window there is no pattern to make one from)
        assert not win.btn_make_pdf.isEnabled()
        assert not win.btn_open_pattern.isEnabled()
        assert not win.btn_keep_drawing.isEnabled()
        pdf = tmp_path / "out" / "wallet A4.pdf"
        pdf.parent.mkdir()
        pdf.write_bytes(b"%PDF-1.4 one")
        assert win.attach_pdf(str(pdf))
        assert win.lbl_pdf.text() == "Wallet.pdf"
        assert win.btn_open_pdf.isEnabled() and win.btn_remove_pdf.isEnabled()
        win.open_pdf()
        assert user.opened == [str(item.folder / "Wallet.pdf")]
        # not a PDF
        assert not win.attach_pdf(str(tmp_path / "x.txt"))
        assert "isn't a PDF" in user.told[-1][1]
        # the folder is an ordinary one, and can be opened
        win.show_folder()
        assert user.opened[-1] == str(item.folder)
        # removing asks
        user.yes = False
        win.remove_pdf()
        assert shelf.find(item.id).pdf == "Wallet.pdf"
        user.yes = True
        win.remove_pdf()
        assert shelf.find(item.id).pdf == ""
        assert win.lbl_pdf.text() == "None kept"
    finally:
        win.close()


def test_deleting_an_entry_asks_and_uses_the_recycle_bin(qapp, tmp_path):
    shelf = Library(tmp_path / "lib")
    a = shelf.add("Wallet", made="2025-01-01")
    a.add_photo(_picture(tmp_path / "a.jpg"))
    b = shelf.add("Belt", made="2024-01-01")
    binned = []

    def trash(folder):
        binned.append(folder)
        shutil.move(folder, str(tmp_path / "bin"))
        return True

    win, user = _open(tmp_path, trash=trash)
    try:
        user.yes = False
        win.delete_entry()
        assert len(shelf.items()) == 2 and not binned
        assert "1 picture" in user.asked[-1][1]
        assert "recycle bin" in user.asked[-1][1]
        user.yes = True
        win.delete_entry()
        assert binned == [str(a.folder)] and (tmp_path / "bin").is_dir()
        assert [i.id for i in shelf.items()] == [b.id]
        assert win.wall.count() == 1 and win.current.id == b.id
    finally:
        win.close()
    # where there is no recycle bin, it is said, and asked again
    win, user = _open(tmp_path, trash=lambda folder: (False, ""))
    try:
        user.yes = lambda title, text: "for good" not in text
        win.delete_entry()
        assert len(shelf.items()) == 1 and len(user.asked) == 2
        user.yes = True
        win.delete_entry()
        assert shelf.items() == [] and win.current is None
        assert win.nothing.isVisible()
    finally:
        win.close()


def test_the_library_can_be_kept_in_another_folder(qapp, tmp_path):
    ini = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    shelf = Library(tmp_path / "lib")
    a = shelf.add("Wallet")
    win, user = _open(tmp_path, settings=ini)
    try:
        # the pieces go with it, if you say so
        win.use_folder(tmp_path / "cloud" / "Library")
        assert "Move the 1 piece" in user.asked[-1][1]
        assert win.library.root == tmp_path / "cloud" / "Library"
        assert [i.id for i in win.library.items()] == [a.id]
        assert Library(tmp_path / "lib").items() == []
        assert win.wall.count() == 1
        assert ini.value("libraryFolder") == str(tmp_path / "cloud"
                                                 / "Library")
        # a folder that is a library already is just used
        other = Library(tmp_path / "other")
        other.add("Belt")
        asked = len(user.asked)
        win.use_folder(tmp_path / "other")
        assert len(user.asked) == asked
        assert win.current.title == "Belt"
        assert len(Library(tmp_path / "cloud" / "Library").items()) == 1
        # or the pieces can stay behind, and the library start empty
        user.yes = False
        win.use_folder(tmp_path / "fresh")
        assert win.wall.count() == 0
        assert len(Library(tmp_path / "other").items()) == 1
        win.use_folder(tmp_path / "fresh")          # there already
    finally:
        win.close()
    from leathercad_app.librarywindow import default_folder, library_folder
    assert library_folder(ini) == tmp_path / "fresh"
    assert library_folder(None) == default_folder()
    assert default_folder().name == "Stitch Hero Library"


# -- in the main window ------------------------------------------------------

def _main(qapp, tmp_path, monkeypatch, doc=None):
    """The main window, with its library and all the library keeps (its
    settings, the small copies of its pictures) in ``tmp_path``."""
    from leathercad_app import librarywindow
    from leathercad_app.mainwindow import MainWindow
    monkeypatch.setattr(librarywindow, "app_data_dir",
                        lambda: tmp_path / "appdata")
    ini = str(tmp_path / "settings.ini")
    QSettings(ini, QSettings.Format.IniFormat).setValue(
        "libraryFolder", str(tmp_path / "lib"))
    win = MainWindow(doc or _drawing())
    monkeypatch.setattr(win, "_settings",
                        lambda: QSettings(ini, QSettings.Format.IniFormat))
    win.canvas.rebuild()
    return win


def _close(win):
    lw = getattr(win, "_library_win", None)
    if lw is not None:
        lw.close()
    win.close()


def test_the_open_drawing_is_added_to_the_library(qapp, tmp_path,
                                                 monkeypatch):
    win = _main(qapp, tmp_path, monkeypatch)
    try:
        assert win.library().root == tmp_path / "lib"
        assert win.library_entry() is None
        item = win.add_to_library()
        (kept,) = win.library().items()
        assert kept.id == item.id and kept.title == "Test piece"
        # a copy of the pattern as it stands is in it
        assert kept.pattern == "Test piece.leathercad.json"
        assert len(Document.load(str(kept.pattern_path)).shapes) == 1
        # the library opens on it, its name ready to change
        lw = win._library_win
        assert lw.isVisible() and lw.current.id == item.id
        assert lw.ed_title.selectedText() == "Test piece"
        assert "Added to the library" in win.statusBar().currentMessage()
        # the drawing is that entry's now: adding it again shows it
        assert win.library_entry().id == item.id
        assert win.add_to_library().id == item.id
        assert len(win.library().items()) == 1
        # File -> Library... brings the same window forward
        assert win.show_library() is lw
        # the small copies of its pictures are kept with the app's data,
        # not in the library
        lw.add_pictures([str(_picture(tmp_path / "done.jpg"))])
        assert lw.thumbs.folder == tmp_path / "appdata" / "library-thumbs"
        assert len(list(lw.thumbs.folder.glob("*.jpg"))) == 3
        assert sorted(p.name for p in kept.folder.iterdir()) == [
            "Test piece.leathercad.json", "done.jpg", "item.json"]
        # a new drawing is no entry's
        win.new_document()
        assert win.library_entry() is None
        # closed, the library opens afresh the next time -- on the piece
        # asked for
        other = win.library().add("Another", made="2030-01-01")
        lw.close()
        assert win.show_library(select=item.id) is not lw
        again = win._library_win
        assert again.isVisible() and again.current.id == item.id
        assert again.wall.count() == 2 and other.id in again._known
        again.close()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        third = win.show_library()
        assert third.isVisible() and third.current.id == other.id
    finally:
        _close(win)


def test_a_saved_drawing_is_known_by_its_file(qapp, tmp_path, monkeypatch):
    path = tmp_path / "work" / "wallet.leathercad.json"
    _drawing(path)
    win = _main(qapp, tmp_path, monkeypatch)
    try:
        assert win.open_path(str(path))
        # it is called what its file is, without the ending
        assert win._pattern_title() == "wallet"
        item = win.add_to_library()
        assert item.title == "wallet"
        assert item.pattern == "wallet.leathercad.json"
        assert win.library().find(item.id).source == str(path)
    finally:
        _close(win)
    # another day, the same file open: the library still knows whose it is
    again = _main(qapp, tmp_path, monkeypatch)
    try:
        assert again.library_entry() is None
        assert again.open_path(str(path))
        assert again.library_entry().id == item.id
        # ... until the entry is deleted
        again.library().delete(again.library().find(item.id))
        assert again.library_entry() is None
    finally:
        _close(again)


def test_exporting_the_pdf_can_keep_a_copy_in_the_library(qapp, tmp_path,
                                                         monkeypatch):
    from leathercad_app.printing import PrintOptions
    win = _main(qapp, tmp_path, monkeypatch)
    try:
        out = tmp_path / "out" / "piece.pdf"
        out.parent.mkdir()
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        # without the box ticked, the library is left alone
        monkeypatch.setattr(win, "_print_options", lambda *a, **k:
                            PrintOptions(title="Piece", layout_budget=0.2))
        win.export_pdf()
        assert out.read_bytes().startswith(b"%PDF")
        assert win.library().items() == []
        # with it, the pattern is added to the library and the PDF with it
        monkeypatch.setattr(win, "_print_options", lambda *a, **k:
                            PrintOptions(title="Piece", layout_budget=0.2,
                                         file_in_library=True))
        win.export_pdf()
        (item,) = win.library().items()
        assert item.title == "Piece" and item.pdf == "Piece.pdf"
        assert item.pdf_path.read_bytes() == out.read_bytes()
        assert item.pattern and win.library_entry().id == item.id
        assert "library" in win.statusBar().currentMessage()
        # the next one made takes its place, in the same entry
        first = out.read_bytes()
        monkeypatch.setattr(win, "_print_options", lambda *a, **k:
                            PrintOptions(title="Piece, again",
                                         layout_budget=0.2,
                                         file_in_library=True))
        win.export_pdf()
        (item,) = win.library().items()
        assert out.read_bytes() != first
        assert item.pdf_path.read_bytes() == out.read_bytes()
        assert item.title == "Piece" and item.pdf == "Piece.pdf"
        assert sorted(p.name for p in item.folder.iterdir()) == [
            "Piece.leathercad.json", "Piece.pdf", "item.json"]
    finally:
        _close(win)


def test_the_box_in_the_pdf_options(qapp, tmp_path, monkeypatch):
    win = _main(qapp, tmp_path, monkeypatch)
    try:
        seen = []

        def run(dlg):
            from PySide6.QtWidgets import QCheckBox
            boxes = [b for b in dlg.findChildren(QCheckBox)
                     if "library" in b.text()]
            seen.append([(b.text(), b.isChecked()) for b in boxes])
            for b in boxes:
                if tick[0] is not None:
                    b.setChecked(tick[0])
            return QDialog.Accepted

        monkeypatch.setattr(QDialog, "exec", run)
        tick = [None]
        # printing has no such box; the PDF has, unticked to begin with
        assert win._print_options("Print", paper=False).file_in_library \
            is False
        assert seen[-1] == []
        assert win._print_options("PDF", library=True).file_in_library \
            is False
        (text, on), = seen[-1]
        assert "this pattern is added to it" in text and not on
        # ticked once, it stays ticked for the next pattern
        tick[0] = True
        assert win._print_options("PDF", library=True).file_in_library
        tick[0] = None
        assert seen[-1][0][1] is False
        assert win._print_options("PDF", library=True).file_in_library
        assert seen[-1][0][1] is True
        # for a drawing that has an entry it says which, and is ticked
        tick[0] = False
        win._print_options("PDF", library=True)
        tick[0] = None
        item = win.add_to_library()
        opts = win._print_options("PDF", library=True)
        (text, on), = seen[-1]
        assert item.title in text and on and opts.file_in_library
    finally:
        _close(win)


def test_a_pattern_is_opened_from_the_library_as_a_copy(qapp, tmp_path,
                                                       monkeypatch):
    from leathercad_app.printing import PrintOptions
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    _drawing(tmp_path / "p.leathercad.json")
    item.attach_pattern(tmp_path / "p.leathercad.json")
    bare = shelf.add("Sketch only")
    win = _main(qapp, tmp_path, monkeypatch, Document())
    try:
        lw = win.show_library(select=item.id)
        assert lw.current.id == item.id
        assert lw.btn_open_pattern.isEnabled()
        lw.open_pattern()
        # it is a new drawing, not the library's file
        assert win.path is None and len(win.doc.shapes) == 1
        assert "copy" in win.statusBar().currentMessage()
        assert win._pattern_title() == "Test piece"
        # the library gets out of the way of it
        assert not lw.isVisible()
        # ... but the drawing is still that entry's: its PDF goes there,
        # and shows there at once if the library is open
        assert win.library_entry().id == item.id
        lw = win.show_library(select=bare.id)
        assert lw.current.id == bare.id
        out = tmp_path / "x.pdf"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        monkeypatch.setattr(win, "_print_options", lambda *a, **k:
                            PrintOptions(layout_budget=0.2,
                                         file_in_library=True))
        win.export_pdf()
        assert [i.title for i in shelf.items() if i.pdf] == ["Card wallet"]
        assert lw.current.id == item.id
        assert lw.lbl_pdf.text() == "Card wallet.pdf"
        # changing the copy doesn't change the library's pattern
        win.doc.shapes.clear()
        assert len(Document.load(str(shelf.find(item.id).pattern_path)
                                 ).shapes) == 1
        # an entry with no pattern has none to open
        lw.reload(select=bare.id)
        assert not lw.btn_open_pattern.isEnabled()
        assert not lw.btn_make_pdf.isEnabled()
        lw.open_pattern()
        assert lw.isVisible() and len(win.doc.shapes) == 0
        # closing the main window closes the library with it, and what
        # was being typed there is kept
        lw.ed_notes.setPlainText("to do: draw it")
        win.close()
        assert not lw.isVisible()
        assert shelf.find(bare.id).notes == "to do: draw it"
    finally:
        _close(win)


def test_the_pdf_of_an_entry_is_made_from_its_own_pattern(qapp, tmp_path,
                                                         monkeypatch):
    from leathercad_app.printing import PrintOptions
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    _drawing(tmp_path / "p.leathercad.json")
    item.attach_pattern(tmp_path / "p.leathercad.json")
    win = _main(qapp, tmp_path, monkeypatch, Document())   # nothing open
    try:
        lw = win.show_library(select=item.id)
        asked = []

        def options(title, **kw):
            asked.append((title, kw.get("name"), len(kw["doc"].shapes)))
            return PrintOptions(title=kw.get("name") or "",
                                layout_budget=0.2)

        monkeypatch.setattr(win, "_print_options", options)
        from leathercad_app import printing
        real, printed = printing.export_pdf, []

        def export(doc, *a, **k):
            printed.append(len(doc.shapes))
            return real(doc, *a, **k)

        monkeypatch.setattr(printing, "export_pdf", export)
        assert lw.btn_make_pdf.isEnabled()
        lw.make_pdf()
        # the options were for the entry's pattern, and so is the PDF:
        # not for the drawing that happens to be open
        assert asked == [("Make 1:1 PDF", "Card wallet", 1)]
        assert printed == [1] and len(win.doc.shapes) == 0
        back = shelf.find(item.id)
        assert back.pdf == "Card wallet.pdf"
        assert back.pdf_path.read_bytes().startswith(b"%PDF")
        assert lw.lbl_pdf.text() == "Card wallet.pdf"
        assert lw.btn_open_pdf.isEnabled()
        # cancelled: nothing changes
        monkeypatch.setattr(win, "_print_options", lambda *a, **k: None)
        before = back.pdf_path.read_bytes()
        lw.make_pdf()
        assert shelf.find(item.id).pdf_path.read_bytes() == before
    finally:
        _close(win)


def test_an_entry_takes_the_open_drawing_as_its_pattern(qapp, tmp_path,
                                                       monkeypatch):
    shelf = Library(tmp_path / "lib")
    item = shelf.add("Made by hand")
    win = _main(qapp, tmp_path, monkeypatch)
    try:
        lw = win.show_library(select=item.id)
        user = _Answers(lw)
        assert lw.btn_keep_drawing.isEnabled()
        lw.keep_drawing()                           # none yet: nothing asked
        assert not user.asked
        back = shelf.find(item.id)
        assert back.pattern == "Made by hand.leathercad.json"
        assert lw.lbl_pattern.text() == back.pattern
        assert win.library_entry().id == item.id
        # replacing one asks
        win.doc.shapes.clear()
        user.yes = False
        lw.keep_drawing()
        assert "has a pattern already" in user.asked[-1][1]
        assert len(Document.load(str(back.pattern_path)).shapes) == 1
        user.yes = True
        lw.keep_drawing()
        assert len(Document.load(str(back.pattern_path)).shapes) == 0
    finally:
        _close(win)


def test_the_library_is_in_the_file_menu(qapp, tmp_path, monkeypatch):
    win = _main(qapp, tmp_path, monkeypatch)
    try:
        file_menu = win.menuBar().actions()[0].menu()
        acts = {a.text(): a for a in file_menu.actions()}
        shelf = acts["Library of finished pieces…"]
        assert shelf.shortcut().toString() == "Ctrl+L"
        assert "Add to library…" in acts
        shelf.trigger()
        assert win._library_win.isVisible()
        assert win._library_win.wall.count() == 0
        assert win._library_win.library.root == tmp_path / "lib"
        assert win._library_win.host is win
    finally:
        _close(win)

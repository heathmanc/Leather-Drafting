"""The library of finished pieces: folders of pictures, notes, the pattern
and its 1:1 PDF."""

import datetime
import json
import os
import time

import pytest

from leathercad import library as lib


def _file(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_a_new_piece_is_a_folder_with_its_name_notes_and_day(tmp_path):
    shelf = lib.Library(tmp_path / "Library")        # made when first used
    assert shelf.items() == []
    item = shelf.add("Card wallet", notes="2 oz veg tan, 0.45 thread")
    assert item.folder == tmp_path / "Library" / "Card wallet"
    assert item.made == datetime.date.today().isoformat()
    assert (item.folder / "item.json").is_file()
    (back,) = shelf.items()
    assert (back.id, back.title, back.notes, back.made) \
        == (item.id, "Card wallet", "2 oz veg tan, 0.45 thread", item.made)
    assert back.photos == [] and back.pattern == "" and back.pdf == ""
    assert back.cover is None and back.pdf_path is None
    # a second of the same name gets a folder of its own
    other = shelf.add("Card wallet")
    assert other.folder.name == "Card wallet (2)" and other.id != item.id
    assert shelf.find(other.id).folder == other.folder
    assert shelf.find("no such id") is None
    # with no name it is Untitled; a day can be given, or none
    assert shelf.add("  ").title == "Untitled"
    assert shelf.add("Belt", made="2024-05-01").made == "2024-05-01"
    assert shelf.add("Strap", made="").made == ""


def test_names_that_folders_cannot_have(tmp_path):
    assert lib.safe_name('A/B: "C" <D>?*|\\') == "A B C D"
    assert lib.safe_name("  dots and spaces . . ") == "dots and spaces"
    assert lib.safe_name("") == "Untitled"
    assert lib.safe_name("...") == "Untitled"
    assert lib.safe_name("con") == "_con"
    assert lib.safe_name("LPT1.x") == "_LPT1.x"
    assert lib.safe_name("x" * 200) == "x" * 60
    assert lib.safe_name("財布 ★") == "財布 ★"            # any script will do
    # a file keeps its ending, however long its name
    assert lib.safe_file("IMG:2041?.JPG") == "IMG 2041.jpg"
    assert lib.safe_file("p" * 200 + ".jpeg") == "p" * 75 + ".jpeg"
    assert lib.safe_file("Wallet.LeatherCAD.json") == "Wallet.leathercad.json"
    shelf = lib.Library(tmp_path)
    item = shelf.add('Wallet 3": the "good" one?')
    assert item.title == 'Wallet 3": the "good" one?'    # the name is kept
    assert item.folder.name == "Wallet 3 the good one"   # the folder is safe
    assert shelf.items()[0].title == item.title


def test_pictures_are_copied_in_and_the_first_is_the_cover(tmp_path):
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    a = _file(tmp_path / "phone" / "IMG_1.JPG", b"one")
    b = _file(tmp_path / "phone" / "IMG_2.png", b"two")
    assert item.add_photo(a) == "IMG_1.jpg"
    assert item.add_photo(b) == "IMG_2.png"
    assert item.add_photo(a) == "IMG_1 (2).jpg"          # the same again
    assert item.photos == ["IMG_1.jpg", "IMG_2.png", "IMG_1 (2).jpg"]
    assert item.cover == item.folder / "IMG_1.jpg"
    assert item.cover.read_bytes() == b"one" and a.exists()   # a copy
    assert item.add_photo(b, name="Pasted picture.png") == "Pasted picture.png"
    with pytest.raises(ValueError):
        item.add_photo(_file(tmp_path / "notes.txt"))
    # another cover; a picture taken out is deleted
    item.set_cover("IMG_2.png")
    assert item.photos[0] == "IMG_2.png"
    item.remove_photo("IMG_1.jpg")
    assert not (item.folder / "IMG_1.jpg").exists()
    item.remove_photo("IMG_1.jpg")                       # twice does no harm
    item.set_cover("gone.jpg")
    back = shelf.find(item.id)
    assert back.photos == ["IMG_2.png", "IMG_1 (2).jpg", "Pasted picture.png"]
    assert back.path("IMG_2.png").read_bytes() == b"two"
    assert back.path("") is None


def test_photographs_come_before_3d_views_and_take_the_cover(tmp_path):
    """A picture from the 3D assembly preview is a view, not a photograph:
    the first photograph of the piece takes the cover from it, and the
    photographs stay ahead of the views -- unless a cover was chosen."""
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    render = _file(tmp_path / "render.png", b"3d")
    shot = _file(tmp_path / "phone" / "IMG_1.jpg", b"photo")
    assert item.add_photo(render, name=lib.VIEW_NAME, view=True) \
        == "3D view.png"
    assert item.photos == ["3D view.png"] and item.is_view("3D view.png")
    assert item.cover.name == "3D view.png"         # better than nothing
    assert item.add_photo(shot) == "IMG_1.jpg"
    assert item.photos == ["IMG_1.jpg", "3D view.png"]
    assert item.add_photo(render, name=lib.VIEW_NAME, view=True) \
        == "3D view (2).png"
    assert item.add_photo(shot, name="IMG_2.jpg") == "IMG_2.jpg"
    assert item.photos == ["IMG_1.jpg", "IMG_2.jpg", "3D view.png",
                           "3D view (2).png"]
    assert not item.is_view("IMG_2.jpg")
    # read back, the library still knows which are views
    back = shelf.find(item.id)
    assert back.views == ["3D view.png", "3D view (2).png"]
    assert back.photos == item.photos and not back.cover_chosen
    # a view chosen as the cover by hand stays the cover
    back.set_cover("3D view (2).png")
    assert back.cover_chosen
    back.add_photo(shot, name="IMG_3.jpg")
    assert back.photos == ["3D view (2).png", "IMG_1.jpg", "IMG_2.jpg",
                           "IMG_3.jpg", "3D view.png"]
    # the chosen cover taken out: the next picture is the cover, chosen
    # by no one
    back.remove_photo("3D view (2).png")
    assert back.views == ["3D view.png"] and not back.cover_chosen
    assert shelf.find(item.id).photos[0] == "IMG_1.jpg"
    # a cover chosen when there were only views keeps its place too
    other = shelf.add("Belt")
    other.add_photo(render, name=lib.VIEW_NAME, view=True)
    other.add_photo(render, name="3D view.png", view=True)
    other.set_cover("3D view (2).png")
    other.add_photo(shot)
    assert other.photos == ["3D view (2).png", "IMG_1.jpg", "3D view.png"]
    # a picture dropped in the folder by hand is a photograph
    _file(other.folder / "by hand.jpg", b"hand")
    assert not shelf.find(other.id).is_view("by hand.jpg")


def test_a_pdf_made_before_the_pattern_changed_is_out_of_date(tmp_path):
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    assert not item.pdf_outdated                       # nothing to say
    item.attach_pattern(_file(tmp_path / "a.leathercad.json", b"pattern 1"))
    item.attach_pdf(_file(tmp_path / "a.pdf", b"%PDF 1"))
    assert not item.pdf_outdated
    # the same pattern saved again: still the PDF of it
    item.attach_pattern(_file(tmp_path / "b.leathercad.json", b"pattern 1"))
    assert not shelf.find(item.id).pdf_outdated
    # another pattern: the PDF is of the one before
    item.attach_pattern(_file(tmp_path / "c.leathercad.json", b"pattern 2"))
    assert item.pdf_outdated and shelf.find(item.id).pdf_outdated
    # made again, it is the PDF of this one
    item.attach_pdf(_file(tmp_path / "c.pdf", b"%PDF 2"))
    assert not shelf.find(item.id).pdf_outdated
    item.remove_pdf()
    assert item.pdf_of == "" and not item.pdf_outdated
    # a PDF kept before the library said what it was made of is taken to
    # be the pattern's
    old = shelf.add("Old one")
    old.attach_pattern(_file(tmp_path / "d.leathercad.json", b"pattern"))
    old.attach_pdf(_file(tmp_path / "d.pdf", b"%PDF"))
    info = json.loads((old.folder / "item.json").read_text("utf-8"))
    del info["pdf_of"]
    (old.folder / "item.json").write_text(json.dumps(info), "utf-8")
    old = shelf.find(old.id)
    old.attach_pattern(_file(tmp_path / "e.leathercad.json", b"new"))
    assert not shelf.find(old.id).pdf_outdated


def test_saving_again_keeps_the_last_pattern_of_each_day(tmp_path,
                                                         monkeypatch):
    """What a piece had is set aside when it was put in on an earlier day
    -- even one only an hour ago, before midnight -- and not when it was
    put in today."""
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Wallet")
    item.attach_pattern(_file(tmp_path / "1.leathercad.json", b"one"))
    # put in at 23:30 yesterday, replaced at 00:30 today
    today = datetime.date(2026, 11, 21)
    then = datetime.datetime(2026, 11, 20, 23, 30).timestamp()
    os.utime(item.pattern_path, (then, then))
    monkeypatch.setattr(lib, "_date_today", lambda: today)
    item.attach_pattern(_file(tmp_path / "2.leathercad.json", b"two"))
    assert [p.name for p in item.earlier] == [
        "2026-11-20 Wallet.leathercad.json"]
    # put in this morning, replaced this afternoon: just replaced
    morning = datetime.datetime(2026, 11, 21, 9, 0).timestamp()
    os.utime(item.pattern_path, (morning, morning))
    item.attach_pattern(_file(tmp_path / "3.leathercad.json", b"three"))
    assert len(item.earlier) == 1
    assert item.pattern_path.read_bytes() == b"three"


def test_which_files_are_a_pieces_drawing(tmp_path):
    """The file of a piece's pattern, and the drawing it was copied from,
    are the piece's drawing; an earlier pattern kept with it is not."""
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    drawing = _file(tmp_path / "work" / "wallet.leathercad.json", b"p")
    item.attach_pattern(drawing, origin=str(drawing))
    assert shelf.drawing_of(drawing).id == item.id
    assert shelf.drawing_of(item.pattern_path).id == item.id
    assert item.is_pattern(item.pattern_path)
    assert item.is_pattern(str(item.pattern_path).replace(
        "Card wallet.leathercad.json",
        "./Card wallet.leathercad.json"))
    assert not item.is_pattern(drawing)
    earlier = _file(item.folder / lib.EARLIER / "2020-01-01 x.leathercad.json")
    assert shelf.holding(earlier).id == item.id       # it is in the folder,
    assert shelf.drawing_of(earlier) is None          # but not the drawing
    assert shelf.drawing_of(tmp_path / "other.json") is None
    assert shelf.drawing_of(None) is None and shelf.drawing_of("") is None
    # a pattern put in from no file: the drawing it was copied from is no
    # longer the piece's
    item.attach_pattern(_file(tmp_path / "x.leathercad.json"), origin="")
    assert item.source == "" and shelf.drawing_of(drawing) is None


def test_the_pattern_and_its_pdf_are_kept_with_the_piece(tmp_path):
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    drawing = _file(tmp_path / "work" / "wallet v3.leathercad.json", b"{}")
    assert item.attach_pattern(drawing, origin=drawing) \
        == "Card wallet.leathercad.json"
    assert item.pattern_path.read_bytes() == b"{}"
    assert item.source == str(drawing)
    pdf = _file(tmp_path / "out" / "wallet-A4.pdf", b"%PDF one")
    assert item.attach_pdf(pdf) == "Card wallet.pdf"
    assert item.pdf_path.read_bytes() == b"%PDF one"
    # a newer one takes its place
    item.attach_pdf(_file(tmp_path / "out" / "wallet-letter.pdf", b"%PDF two"))
    assert item.pdf_path.read_bytes() == b"%PDF two"
    assert sorted(p.name for p in item.folder.iterdir()) \
        == ["Card wallet.leathercad.json", "Card wallet.pdf", "item.json"]
    # its own file given back to it is left alone
    item.attach_pdf(item.pdf_path)
    assert item.pdf_path.read_bytes() == b"%PDF two"
    back = shelf.find(item.id)
    assert (back.pattern, back.pdf) == (item.pattern, item.pdf)
    # which piece a file belongs to: the drawing it came from, or anything
    # in its folder
    assert shelf.holding(drawing).id == item.id
    assert shelf.holding(item.pattern_path).id == item.id
    assert shelf.holding(tmp_path / "work" / "other.json") is None
    assert shelf.holding(None) is None and shelf.holding("") is None
    item.remove_pdf()
    assert item.pdf == "" and not (item.folder / "Card wallet.pdf").exists()
    item.remove_pdf()


def _aged(path, days):
    """Make a file look as if it was put there ``days`` ago."""
    then = time.time() - days * 86400
    os.utime(path, (then, then))
    return datetime.date.fromtimestamp(then).isoformat()


def test_a_pattern_or_pdf_that_is_replaced_is_kept_not_lost(tmp_path):
    """A piece in the library is a record of something made. Filing a later
    pattern or PDF with it must not make the one it was made from vanish:
    that one goes into the piece's ``earlier`` folder."""
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    item.attach_pattern(_file(tmp_path / "a.leathercad.json", b"pattern 1"))
    item.attach_pdf(_file(tmp_path / "a.pdf", b"%PDF 1"))
    assert item.earlier == []
    # the day it is put in, a pattern is still being worked on: the next
    # one just takes its place
    item.attach_pattern(_file(tmp_path / "b.leathercad.json", b"pattern 2"))
    item.attach_pdf(_file(tmp_path / "b.pdf", b"%PDF 2"))
    assert item.earlier == [] and not (item.folder / lib.EARLIER).exists()
    assert item.pattern_path.read_bytes() == b"pattern 2"
    # months on, it is the record of the piece
    day = _aged(item.pattern_path, 200)
    _aged(item.pdf_path, 200)
    item.attach_pattern(_file(tmp_path / "c.leathercad.json", b"pattern 3"))
    _aged(_file(tmp_path / "c.pdf", b"%PDF 3"), 400)   # a PDF made long ago
    item.attach_pdf(tmp_path / "c.pdf")
    assert item.pattern_path.read_bytes() == b"pattern 3"
    assert item.pdf_path.read_bytes() == b"%PDF 3"
    assert [p.name for p in item.earlier] == [
        f"{day} Card wallet.leathercad.json", f"{day} Card wallet.pdf"]
    assert [p.read_bytes() for p in item.earlier] == [b"pattern 2", b"%PDF 2"]
    # (what is put in is dated when it was put in, however old the file)
    assert time.time() - item.pdf_path.stat().st_mtime < 60
    # the same one filed again changes nothing, old as it is
    _aged(item.pdf_path, 30)
    item.attach_pdf(_file(tmp_path / "c again.pdf", b"%PDF 3"))
    assert len(item.earlier) == 2
    # two replaced that were put in on the same day are both kept
    _aged(item.pdf_path, 200)
    item.attach_pdf(_file(tmp_path / "d.pdf", b"%PDF 4"))
    assert [p.name for p in item.earlier] == [
        f"{day} Card wallet (2).pdf",
        f"{day} Card wallet.leathercad.json", f"{day} Card wallet.pdf"]
    assert item.earlier[0].read_bytes() == b"%PDF 3"
    # a piece renamed since: the new PDF has the new name, and the one it
    # replaces doesn't lie about under the old -- put in today, it is gone
    item.rename("Bifold")
    assert item.attach_pdf(_file(tmp_path / "e.pdf", b"%PDF 5")) \
        == "Bifold.pdf"
    assert sorted(p.name for p in item.folder.iterdir() if p.is_file()) \
        == ["Bifold.pdf", "Card wallet.leathercad.json", "item.json"]
    assert len(item.earlier) == 3
    # ... and put in long ago, it is kept under the name it had
    item.rename("Card wallet")
    _aged(item.pdf_path, 50)
    item.attach_pdf(_file(tmp_path / "f.pdf", b"%PDF 6"))
    assert not (item.folder / "Bifold.pdf").exists()
    assert len(item.earlier) == 4
    assert [p.read_bytes() for p in item.earlier
            if p.name.endswith("Bifold.pdf")] == [b"%PDF 5"]
    # the folder of earlier ones is nothing to the library: not a piece,
    # not this piece's pattern or PDF
    back = shelf.find(item.id)
    assert (back.pattern, back.pdf) == ("Card wallet.leathercad.json",
                                        "Card wallet.pdf")
    assert back.photos == [] and len(shelf.items()) == 1
    assert len(back.earlier) == 4


def test_a_copy_that_fails_changes_nothing(tmp_path, monkeypatch):
    shelf = lib.Library(tmp_path / "lib")
    item = shelf.add("Card wallet")
    item.attach_pdf(_file(tmp_path / "a.pdf", b"%PDF 1"))
    _aged(item.pdf_path, 200)

    def full(src, dst, *a, **k):
        raise OSError("No space left on device")

    # the disk fills as the new one is copied in
    monkeypatch.setattr(lib.shutil, "copyfile", full)
    with pytest.raises(OSError):
        item.attach_pdf(_file(tmp_path / "b.pdf", b"%PDF 2"))
    monkeypatch.undo()
    assert item.pdf == "Card wallet.pdf"
    assert item.pdf_path.read_bytes() == b"%PDF 1" and item.earlier == []
    assert sorted(p.name for p in item.folder.iterdir()) \
        == ["Card wallet.pdf", "item.json"]
    # ... or the old one can't be moved aside (open in a viewer, on Windows)
    real = lib.os.replace

    def locked(src, dst, *a, **k):
        if lib.EARLIER in str(dst):
            raise PermissionError("in use")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(lib.os, "replace", locked)
    with pytest.raises(OSError):
        item.attach_pdf(_file(tmp_path / "b.pdf", b"%PDF 2"))
    monkeypatch.undo()
    assert item.pdf_path.read_bytes() == b"%PDF 1"
    assert [p.name for p in item.folder.iterdir() if p.is_file()
            and p.name != "item.json"] == ["Card wallet.pdf"]
    assert shelf.find(item.id).pdf == "Card wallet.pdf"
    # nor what the piece says of its pattern and PDF
    drawing = _file(tmp_path / "w.leathercad.json", b"pattern 1")
    item.attach_pattern(drawing, origin=str(drawing))
    item.attach_pdf(_file(tmp_path / "c.pdf", b"%PDF 3"))
    was = (item.source, item.pdf_of)
    monkeypatch.setattr(lib.shutil, "copyfile", full)
    with pytest.raises(OSError):
        item.attach_pattern(_file(tmp_path / "x.leathercad.json", b"2"),
                            origin="")
    with pytest.raises(OSError):
        item.attach_pdf(_file(tmp_path / "x.pdf", b"%PDF 4"))
    monkeypatch.undo()
    assert (item.source, item.pdf_of) == was
    assert not item.pdf_outdated


def test_renaming_a_piece_renames_its_folder_and_keeps_its_files(tmp_path):
    shelf = lib.Library(tmp_path)
    item = shelf.add("Wallet")
    item.add_photo(_file(tmp_path / "x" / "a.jpg"))
    item.attach_pdf(_file(tmp_path / "x" / "w.pdf"))
    was = item.id
    item.rename("  Bifold   wallet ")
    assert item.title == "Bifold wallet"
    assert item.folder == tmp_path / "Bifold wallet"
    assert not (tmp_path / "Wallet").exists()
    assert item.cover.is_file() and item.pdf_path.is_file()
    assert shelf.find(was).title == "Bifold wallet"
    # to a name another piece has: a folder beside it
    shelf.add("Belt")
    item.rename("Belt")
    assert item.folder.name == "Belt (2)" and item.title == "Belt"
    # only the case of the letters; and nothing at all
    item.rename("BELT")
    assert item.title == "BELT" and item.folder.name == "BELT (2)"
    item.rename("")
    assert item.title == "Untitled"
    assert len(shelf.items()) == 2


def test_the_latest_made_comes_first_and_words_find_pieces(tmp_path):
    shelf = lib.Library(tmp_path)
    shelf.add("Belt", made="2024-05-01", notes="Bridle leather, brass buckle")
    shelf.add("Card wallet", made="2025-01-20", notes="Veg tan, blue thread")
    shelf.add("Apron", made="2025-01-20")
    shelf.add("Sketch", made="")
    assert [i.title for i in shelf.items()] \
        == ["Apron", "Card wallet", "Belt", "Sketch"]
    assert [i.title for i in shelf.items("wallet")] == ["Card wallet"]
    assert [i.title for i in shelf.items("BRASS belt")] == ["Belt"]
    assert [i.title for i in shelf.items("2025")] == ["Apron", "Card wallet"]
    assert shelf.items("brass wallet") == []


def test_files_dropped_into_a_folder_by_hand_are_picked_up(tmp_path):
    shelf = lib.Library(tmp_path)
    item = shelf.add("Wallet")
    item.add_photo(_file(tmp_path / "x" / "b.jpg"))
    _file(item.folder / "a from the phone.JPEG")
    _file(item.folder / "pattern.leathercad.json")
    _file(item.folder / "print me.pdf")
    _file(item.folder / "another.pdf")
    _file(item.folder / "readme.txt")
    _file(item.folder / ".hidden.jpg")
    back = shelf.find(item.id)
    assert back.photos == ["b.jpg", "a from the phone.JPEG"]   # named first
    assert back.pattern == "pattern.leathercad.json"
    assert back.pdf == "another.pdf"                    # the first by name
    # ... and one that has gone is left out
    os.remove(item.folder / "b.jpg")
    assert shelf.find(item.id).photos == ["a from the phone.JPEG"]


def test_a_damaged_or_foreign_folder_does_not_break_the_library(tmp_path):
    shelf = lib.Library(tmp_path)
    good = shelf.add("Good")
    (tmp_path / "Not a piece").mkdir()                 # no item.json: ignored
    _file(tmp_path / "stray.txt")
    _file(tmp_path / "Broken" / "item.json", b"{ not json")
    _file(tmp_path / "Odd" / "item.json", json.dumps(
        {"title": 7, "photos": "x", "pdf": ["y"], "notes": None}).encode())
    _file(tmp_path / "List" / "item.json", b"[1, 2]")
    found = {i.title: i for i in shelf.items()}
    assert set(found) == {"Good", "Broken", "Odd", "List"}
    assert found["Odd"].photos == [] and found["Odd"].pdf == ""
    assert found["Odd"].notes == ""
    # a piece without an id of its own keeps the same one from read to read
    assert shelf.items("Broken")[0].id == found["Broken"].id != good.id
    # saving one makes it whole
    found["Broken"].notes = "found again"
    found["Broken"].save()
    assert json.loads((tmp_path / "Broken" / "item.json").read_text(
        encoding="utf-8"))["notes"] == "found again"


def test_deleting_and_moving_the_library(tmp_path):
    shelf = lib.Library(tmp_path / "old")
    a = shelf.add("A")
    a.add_photo(_file(tmp_path / "x" / "a.jpg"))
    b = shelf.add("B")
    shelf.delete(a)
    assert not a.folder.exists() and [i.id for i in shelf.items()] == [b.id]
    with pytest.raises(ValueError):
        shelf.delete(a)                                 # gone already
    outside = lib.Item(tmp_path / "elsewhere")
    outside.save()
    with pytest.raises(ValueError):
        shelf.delete(outside)                           # not this library's
    assert outside.folder.exists()
    # moving takes every piece, and steps round a folder of the same name
    (tmp_path / "new" / "B").mkdir(parents=True)
    assert shelf.move_to(tmp_path / "new") == 1
    assert shelf.root == tmp_path / "new"
    (moved,) = shelf.items()
    assert moved.id == b.id and moved.folder.name == "B (2)"
    assert lib.Library(tmp_path / "old").items() == []
    assert shelf.move_to(tmp_path / "new") == 0         # there already

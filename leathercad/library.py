"""A library of finished pieces: what was made, kept with what made it.

The library is a folder, and each finished piece is a folder in it:

    Card wallet/
        item.json                    its name, when it was made, the notes
        IMG_2041.jpg ...             pictures of it (the first is its cover)
        Card wallet.leathercad.json  the pattern it was cut from
        Card wallet.pdf              the 1:1 PDF of that pattern

Plain files in plain folders: a piece's pictures and its PDF can be got at
without the app, and the library can be backed up, moved, or kept in a
folder that syncs between computers. A picture, a pattern or a PDF dropped
into a piece's folder by hand is picked up the next time the library is
read. Pure Python -- the app puts a window on it.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

INFO = "item.json"
#: What counts as a picture, by the end of its name.
PICTURES = (".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp",
            ".tif", ".tiff", ".heic", ".heif")
PATTERN_END = ".leathercad.json"

_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} \
    | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def safe_name(text: str, fallback: str = "Untitled", most: int = 60) -> str:
    """``text`` as a name a file or folder can have on any system: none of
    the characters Windows forbids, no dot or space at its end, not one of
    the names Windows keeps for itself, and not too long."""
    s = _BAD.sub(" ", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > most:
        s = s[:most].rstrip(" .")
    if not s:
        s = fallback
    if s.split(".")[0].upper() in _RESERVED:
        s = "_" + s
    return s


def _split(name: str):
    """A file's name as (the name, its ending): ``.leathercad.json`` is one
    ending, not two."""
    if name.lower().endswith(PATTERN_END):
        return name[:-len(PATTERN_END)], name[-len(PATTERN_END):]
    return os.path.splitext(name)


def safe_file(name: str, fallback: str = "file", most: int = 80) -> str:
    """A file's name made safe (see ``safe_name``), its ending kept."""
    stem, ext = _split(str(name or ""))
    ext = _BAD.sub("", ext).strip().lower()
    return safe_name(stem, fallback, max(8, most - len(ext))) + ext


def _free(folder: Path, name: str, keep: Optional[str] = None) -> str:
    """``name``, or ``name (2)`` and so on: one nothing in ``folder`` has
    yet, whatever the case of its letters (``keep`` may stay as it is)."""
    taken = {p.name.lower() for p in folder.iterdir()} \
        if folder.is_dir() else set()
    if keep:
        taken.discard(keep.lower())
    stem, ext = _split(name)
    out, n = name, 1
    while out.lower() in taken:
        n += 1
        out = f"{stem} ({n}){ext}"
    return out


def _today() -> str:
    return datetime.date.today().isoformat()


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class Item:
    """One finished piece: a folder of the library and what is in it.

    ``title``, ``made`` (the day, as ``YYYY-MM-DD``, or "") and ``notes``
    are yours to set -- then ``save()``. ``photos`` are the names of its
    pictures, its cover first; ``pattern`` and ``pdf`` the names of the
    pattern it was cut from and of that pattern's 1:1 PDF ("" for none).
    ``source`` is where the pattern was when it was put in, for filing a
    PDF made from it later. ``id`` never changes, though the folder is
    renamed with the title."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.id = uuid.uuid4().hex
        self.title = self.folder.name
        self.made = ""
        self.notes = ""
        self.created = _now()
        self.modified = self.created
        self.photos: List[str] = []
        self.pattern = ""
        self.pdf = ""
        self.source = ""

    # -- reading and writing ----------------------------------------------

    @classmethod
    def read(cls, folder: Path) -> "Item":
        """The piece kept in ``folder``. What its ``item.json`` names but
        isn't there is left out; pictures, a pattern and a PDF that are
        there but not named are taken in."""
        item = cls(folder)
        try:
            d = json.loads((item.folder / INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}

        def text(key, default=""):
            v = d.get(key, default)
            return v if isinstance(v, str) else default

        # (a folder whose item.json has no id keeps the same one each time)
        item.id = text("id") or uuid.uuid5(uuid.NAMESPACE_URL,
                                           item.folder.name).hex
        item.title = text("title").strip() or item.folder.name
        item.made = text("made")
        item.notes = text("notes")
        item.created = text("created") or item.created
        item.modified = text("modified") or item.created
        item.source = text("source")
        here = {p.name.lower(): p.name for p in item.folder.iterdir()
                if p.is_file()} if item.folder.is_dir() else {}

        def kept(name):
            return here.get(name.lower(), "") if isinstance(name, str) else ""

        named = d.get("photos") if isinstance(d.get("photos"), list) else []
        item.photos = []
        for name in named:
            name = kept(name)
            if name and name not in item.photos:
                item.photos.append(name)
        item.pattern = kept(d.get("pattern"))
        item.pdf = kept(d.get("pdf"))
        for name in sorted(here.values(), key=str.lower):
            low = name.lower()
            if low.endswith(PICTURES) and not low.startswith("."):
                if name not in item.photos:
                    item.photos.append(name)
            elif low.endswith(PATTERN_END) and not item.pattern:
                item.pattern = name
            elif low.endswith(".pdf") and not item.pdf:
                item.pdf = name
        return item

    def save(self) -> None:
        """Write what is known of the piece into its folder."""
        self.folder.mkdir(parents=True, exist_ok=True)
        self.modified = _now()
        d = {"library": 1, "id": self.id, "title": self.title,
             "made": self.made, "notes": self.notes,
             "created": self.created, "modified": self.modified,
             "photos": list(self.photos), "pattern": self.pattern,
             "pdf": self.pdf, "source": self.source}
        tmp = self.folder / (INFO + ".tmp")
        tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.folder / INFO)

    # -- its name ---------------------------------------------------------

    def rename(self, title: str) -> None:
        """Give the piece another name, and its folder too if that can be
        done (a file of it open in another program can stop that on
        Windows: the folder then keeps its old name, which does no harm)."""
        title = re.sub(r"\s+", " ", str(title or "")).strip() or "Untitled"
        if title == self.title:
            return
        self.title = title
        want = safe_name(title)
        want = _free(self.folder.parent, want, keep=self.folder.name)
        if want != self.folder.name:
            target = self.folder.with_name(want)
            try:
                self.folder.rename(target)
                self.folder = target
            except OSError:
                pass
        self.save()

    # -- its pictures -----------------------------------------------------

    def path(self, name: str) -> Optional[Path]:
        """Where a file of the piece is, or None for no name."""
        return self.folder / name if name else None

    @property
    def cover(self) -> Optional[Path]:
        """The picture that stands for the piece, if it has one."""
        return self.folder / self.photos[0] if self.photos else None

    def add_photo(self, source, name: Optional[str] = None) -> str:
        """Copy a picture into the piece. Returns the name it has there."""
        source = Path(source)
        name = safe_file(name or source.name, "picture")
        if not name.lower().endswith(PICTURES):
            raise ValueError(f"{source.name} isn't a picture")
        self.folder.mkdir(parents=True, exist_ok=True)
        name = _free(self.folder, name)
        shutil.copy2(source, self.folder / name)
        self.photos.append(name)
        self.save()
        return name

    def remove_photo(self, name: str) -> None:
        """Take a picture out of the piece, and delete its copy."""
        if name not in self.photos:
            return
        self.photos.remove(name)
        try:
            (self.folder / name).unlink()
        except OSError:
            pass
        self.save()

    def set_cover(self, name: str) -> None:
        """Make one of the pictures the one that stands for the piece."""
        if name in self.photos and self.photos[0] != name:
            self.photos.remove(name)
            self.photos.insert(0, name)
            self.save()

    # -- its pattern and the pattern's PDF --------------------------------

    def _keep(self, slot: str, source, name: str) -> str:
        """Copy ``source`` in as the piece's pattern or PDF, in place of
        the one it had."""
        source = Path(source)
        old = getattr(self, slot)
        self.folder.mkdir(parents=True, exist_ok=True)
        name = _free(self.folder, name, keep=old)
        target = self.folder / name
        if source.resolve() != target.resolve():
            tmp = self.folder / (name + ".tmp")
            shutil.copy2(source, tmp)
            os.replace(tmp, target)
        if old and old.lower() != name.lower():
            try:
                (self.folder / old).unlink()
            except OSError:
                pass
        setattr(self, slot, name)
        self.save()
        return name

    def attach_pattern(self, source, origin: Optional[str] = None) -> str:
        """Keep a copy of a pattern file as the one the piece was cut
        from. ``origin`` is where the drawing itself lives, if anywhere."""
        if origin is not None:
            self.source = str(origin)
        return self._keep("pattern", source,
                          safe_name(self.title) + PATTERN_END)

    def attach_pdf(self, source) -> str:
        """Keep a copy of a PDF as the piece's 1:1 pattern."""
        return self._keep("pdf", source, safe_name(self.title) + ".pdf")

    def remove_pdf(self) -> None:
        if self.pdf:
            try:
                (self.folder / self.pdf).unlink()
            except OSError:
                pass
            self.pdf = ""
            self.save()

    @property
    def pattern_path(self) -> Optional[Path]:
        return self.folder / self.pattern if self.pattern else None

    @property
    def pdf_path(self) -> Optional[Path]:
        return self.folder / self.pdf if self.pdf else None

    # -- finding it -------------------------------------------------------

    def matches(self, words: str) -> bool:
        """Whether every word of ``words`` is in its name, notes or date."""
        hay = f"{self.title}\n{self.notes}\n{self.made}".casefold()
        return all(w in hay for w in words.casefold().split())

    def holds(self, path) -> bool:
        """Whether a file is this piece's: in its folder, or the drawing
        its pattern was copied from."""
        if not path:
            return False
        p = os.path.normcase(os.path.abspath(str(path)))
        if self.source and p == os.path.normcase(os.path.abspath(self.source)):
            return True
        mine = os.path.normcase(os.path.abspath(str(self.folder)))
        return p.startswith(mine + os.sep)


class Library:
    """The pieces kept in one folder (made if it isn't there)."""

    def __init__(self, root):
        self.root = Path(root)

    def items(self, words: str = "") -> List[Item]:
        """The pieces, the latest made first; with ``words``, the ones that
        have every one of them in their name, notes or date."""
        out = []
        if self.root.is_dir():
            for folder in self.root.iterdir():
                if folder.is_dir() and (folder / INFO).is_file():
                    item = Item.read(folder)
                    if not words or item.matches(words):
                        out.append(item)
        out.sort(key=lambda it: it.title.casefold())
        out.sort(key=lambda it: (it.made or "", it.created), reverse=True)
        return out

    def add(self, title: str = "", notes: str = "",
            made: Optional[str] = None) -> Item:
        """A new piece, made today unless told otherwise."""
        title = re.sub(r"\s+", " ", str(title or "")).strip() or "Untitled"
        self.root.mkdir(parents=True, exist_ok=True)
        item = Item(self.root / _free(self.root, safe_name(title)))
        item.title = title
        item.notes = notes
        item.made = _today() if made is None else made
        item.save()
        return item

    def find(self, item_id: str) -> Optional[Item]:
        """The piece with that id, wherever its folder is now."""
        for item in self.items():
            if item.id == item_id:
                return item
        return None

    def holding(self, path) -> Optional[Item]:
        """The piece a file belongs to (see ``Item.holds``), if any."""
        if not path:
            return None
        for item in self.items():
            if item.holds(path):
                return item
        return None

    def delete(self, item: Item) -> None:
        """Remove a piece and everything kept with it, for good."""
        folder = item.folder.resolve()
        if folder.parent != self.root.resolve() or not folder.is_dir():
            raise ValueError(f"{item.folder} isn't a piece of this library")
        shutil.rmtree(folder)

    def move_to(self, root) -> int:
        """Move every piece into another folder, which becomes the
        library's. Returns how many were moved."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        moved = 0
        if root.resolve() != self.root.resolve():
            for item in self.items():
                target = root / _free(root, item.folder.name)
                shutil.move(str(item.folder), str(target))
                moved += 1
        self.root = root
        return moved

"""Personal parts library: save any selection as a named, reusable part.

Parts are JSON files in the per-user app-data folder (``parts/``), so they
survive across documents and app updates. Placing a part clones its shapes
with fresh ids at the centre of the current view.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from leathercad.document import _shape_to_dict, _shape_from_dict
from leathercad.shapes import Shape, _next_id

from .robustness import app_data_dir


def parts_dir(base: Optional[str] = None) -> Path:
    d = Path(base) if base else app_data_dir() / "parts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    # \w keeps letters of any script, so "財布" isn't reduced to "part"
    s = re.sub(r"[^\w. -]", "", name).strip().replace(" ", "_")
    return s or "part"


def _part_path(name: str, base: Optional[str]) -> Path:
    """The file for part ``name``: its slug, unless that file already holds a
    DIFFERENT part (names differing only in symbols, e.g. "Strap ★" and
    "Strap ☆") -- then a short hash of the name keeps them apart instead of
    one silently overwriting the other."""
    import hashlib
    d = parts_dir(base)
    p = d / f"{_slug(name)}.part.json"
    if p.exists():
        try:
            other = json.loads(p.read_text(encoding="utf-8")).get("name")
        except Exception:
            other = None
        if other is not None and other != name:
            tag = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
            p = d / f"{_slug(name)}-{tag}.part.json"
    return p


def save_part(name: str, shapes: List[Shape], texts=None,
              base: Optional[str] = None) -> Path:
    payload = {"part": 1, "name": name,
               "shapes": [_shape_to_dict(s) for s in shapes],
               "texts": [t.to_dict() for t in (texts or [])]}
    p = _part_path(name, base)
    p.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return p


def list_parts(base: Optional[str] = None) -> List[Tuple[str, Path]]:
    out = []
    for p in sorted(parts_dir(base).glob("*.part.json")):
        try:
            name = json.loads(p.read_text(encoding="utf-8")).get("name", p.stem)
        except Exception:
            continue
        out.append((name, p))
    return out


def load_part(path: Path) -> List[Shape]:
    """The part's shapes as fresh objects (new ids, no group ties)."""
    return load_part_full(path)[0]


def load_part_full(path: Path):
    """The part's ``(shapes, texts)`` as fresh objects (new ids, no group ties).
    Texts are the size labels / lettering saved with the part."""
    from leathercad.text import TextShape
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    shapes = []
    for d in data.get("shapes", []):
        sh = _shape_from_dict(d)
        sh.shape_id = _next_id("shape")     # never collide with existing shapes
        sh.group_id = None
        shapes.append(sh)
    texts = [TextShape.from_dict(d) for d in data.get("texts", [])]
    for t in texts:
        t.text_id = _next_id("text")
    return shapes, texts


def delete_part(path: Path) -> None:
    Path(path).unlink(missing_ok=True)

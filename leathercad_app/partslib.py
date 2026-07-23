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
    s = re.sub(r"[^A-Za-z0-9._ -]", "", name).strip().replace(" ", "_")
    return s or "part"


def save_part(name: str, shapes: List[Shape], texts=None,
              base: Optional[str] = None) -> Path:
    payload = {"part": 1, "name": name,
               "shapes": [_shape_to_dict(s) for s in shapes],
               "texts": [t.to_dict() for t in (texts or [])]}
    p = parts_dir(base) / f"{_slug(name)}.part.json"
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

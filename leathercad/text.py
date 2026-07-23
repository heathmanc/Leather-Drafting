"""Text / lettering as engrave paths.

Glyph outlines are baked into plain polygon contours (done in the GUI layer,
which has Qt) and stored here as data, so the engine stays Qt-free and the text
exports as ordinary engrave polylines. The source string / font / size are kept
so the GUI can re-bake the contours when they are edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .geometry import Vec2
from .shapes import Transform, _next_id


@dataclass
class TextShape:
    text: str = "Text"
    contours: List[List[Vec2]] = field(default_factory=list)   # baked, local
    size: float = 8.0                 # cap height in mm
    font_family: str = ""             # "" -> the GUI's bundled default family
    bold: bool = False
    italic: bool = False
    tracking: float = 0.0             # extra letter spacing (percent of em)
    align: str = "left"               # left / center / right
    transform: Transform = field(default_factory=Transform)
    layer: str = "Engrave"
    opacity: float = 1.0
    text_id: str = field(default_factory=lambda: _next_id("text"))
    kind: str = "text"

    def world_contours(self) -> List[List[Vec2]]:
        return [[self.transform.apply(p) for p in c] for c in self.contours]

    def bounds(self):
        pts = [p for c in self.world_contours() for p in c]
        if not pts:
            x, y = self.transform.x, self.transform.y
            return x, y, x, y
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "contours": [[[p.x, p.y] for p in c] for c in self.contours],
            "size": self.size,
            "font_family": self.font_family,
            "bold": self.bold,
            "italic": self.italic,
            "tracking": self.tracking,
            "align": self.align,
            "layer": self.layer,
            "opacity": self.opacity,
            "text_id": self.text_id,
            "transform": {"x": self.transform.x, "y": self.transform.y,
                          "rotation": self.transform.rotation,
                          "mirror_x": self.transform.mirror_x},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TextShape":
        t = d.get("transform", {})
        return cls(
            text=d.get("text", ""),
            contours=[[Vec2(x, y) for x, y in c] for c in d.get("contours", [])],
            size=d.get("size", 8.0),
            font_family=d.get("font_family", ""),
            bold=d.get("bold", False),
            italic=d.get("italic", False),
            tracking=d.get("tracking", 0.0),
            align=d.get("align", "left"),
            layer=d.get("layer", "Engrave"),
            opacity=d.get("opacity", 1.0),
            text_id=d.get("text_id", _next_id("text")),
            transform=Transform(x=t.get("x", 0.0), y=t.get("y", 0.0),
                                rotation=t.get("rotation", 0.0),
                                mirror_x=t.get("mirror_x", False)),
        )

"""The CAD document: layers, shapes and stitch lines, with JSON save/load.

Coordinates are millimetres throughout, Y-up. The document is the single source
of truth the GUI edits and the exporters read.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import List, Optional

from .geometry import Vec2
from .layers import Layer, default_layers
from .shapes import (Shape, Rectangle, Ellipse, Circle, Polygon, PathShape,
                     Transform)
from .stitchsettings import StitchSettings
from .stitchline import StitchLine


FILE_VERSION = 1


# ---------------------------------------------------------------------------
# (de)serialisation helpers
# ---------------------------------------------------------------------------
def _transform_to_dict(t: Transform) -> dict:
    return {"x": t.x, "y": t.y, "rotation": t.rotation, "mirror_x": t.mirror_x}


def _transform_from_dict(d: dict) -> Transform:
    return Transform(x=d.get("x", 0.0), y=d.get("y", 0.0),
                     rotation=d.get("rotation", 0.0),
                     mirror_x=d.get("mirror_x", False))


def _stitch_to_dict(s: Optional[StitchSettings]) -> Optional[dict]:
    return asdict(s) if s is not None else None


def _stitch_from_dict(d: Optional[dict]) -> Optional[StitchSettings]:
    if d is None:
        return None
    return StitchSettings(**d)


def _shape_to_dict(sh: Shape) -> dict:
    base = {
        "kind": sh.kind,
        "name": sh.name,
        "layer": sh.layer,
        "opacity": sh.opacity,
        "shape_id": sh.shape_id,
        "transform": _transform_to_dict(sh.transform),
        "stitch": _stitch_to_dict(sh.stitch),
    }
    if isinstance(sh, Rectangle):
        base.update(width=sh.width, height=sh.height,
                    corner_radius=sh.corner_radius)
    elif isinstance(sh, Circle):
        base.update(rx=sh.rx, ry=sh.ry)
    elif isinstance(sh, Ellipse):
        base.update(rx=sh.rx, ry=sh.ry)
    elif isinstance(sh, Polygon):
        base.update(points=[[p.x, p.y] for p in sh.points],
                    corner_radius=sh.corner_radius,
                    close_path=sh.close_path,
                    sharp_corners=sh.sharp_corners)
    elif isinstance(sh, PathShape):
        base.update(points=[[p.x, p.y] for p in sh.points],
                    close_path=sh.close_path)
    return base


def _shape_from_dict(d: dict) -> Shape:
    kind = d.get("kind", "rectangle")
    common = dict(
        name=d.get("name", ""),
        layer=d.get("layer", "cut"),
        opacity=d.get("opacity", 1.0),
        transform=_transform_from_dict(d.get("transform", {})),
        stitch=_stitch_from_dict(d.get("stitch")),
    )
    if "shape_id" in d:
        common["shape_id"] = d["shape_id"]
    if kind == "rectangle":
        return Rectangle(width=d.get("width", 50), height=d.get("height", 30),
                         corner_radius=d.get("corner_radius", 0.0), **common)
    if kind == "circle":
        return Circle(rx=d.get("rx", 20), ry=d.get("ry", d.get("rx", 20)), **common)
    if kind == "ellipse":
        return Ellipse(rx=d.get("rx", 25), ry=d.get("ry", 15), **common)
    if kind == "polygon":
        pts = [Vec2(x, y) for x, y in d.get("points", [])]
        return Polygon(points=pts, corner_radius=d.get("corner_radius", 0.0),
                       close_path=d.get("close_path", True),
                       sharp_corners=d.get("sharp_corners", True), **common)
    if kind == "path":
        pts = [Vec2(x, y) for x, y in d.get("points", [])]
        return PathShape(points=pts, close_path=d.get("close_path", False),
                         **common)
    raise ValueError(f"unknown shape kind {kind!r}")


def _stitchline_to_dict(sl: StitchLine) -> dict:
    return {
        "points": [[p.x, p.y] for p in sl.points],
        "closed": sl.closed,
        "corner_points": [[p.x, p.y] for p in sl.corner_points],
        "settings": asdict(sl.settings),
        "name": sl.name,
        "layer": sl.layer,
        "line_id": sl.line_id,
    }


def _stitchline_from_dict(d: dict) -> StitchLine:
    sl = StitchLine(
        points=[Vec2(x, y) for x, y in d.get("points", [])],
        closed=d.get("closed", False),
        corner_points=[Vec2(x, y) for x, y in d.get("corner_points", [])],
        settings=StitchSettings(**d.get("settings", {})),
        name=d.get("name", ""),
        layer=d.get("layer", "Stitch"),
    )
    if "line_id" in d:
        sl.line_id = d["line_id"]
    return sl


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class Document:
    def __init__(self, name: str = "Untitled"):
        self.name = name
        self.units = "mm"
        self.layers: List[Layer] = default_layers()
        self.shapes: List[Shape] = []
        self.stitch_lines: List[StitchLine] = []

    # -- collection helpers --------------------------------------------
    def add_shape(self, shape: Shape) -> Shape:
        self.shapes.append(shape)
        return shape

    def remove_shape(self, shape: Shape) -> None:
        if shape in self.shapes:
            self.shapes.remove(shape)

    def add_stitch_line(self, line: StitchLine) -> StitchLine:
        self.stitch_lines.append(line)
        return line

    def remove_stitch_line(self, line: StitchLine) -> None:
        if line in self.stitch_lines:
            self.stitch_lines.remove(line)

    def layer(self, name: str) -> Optional[Layer]:
        for lyr in self.layers:
            if lyr.name == name:
                return lyr
        return None

    def add_layer(self, layer: Layer) -> Layer:
        self.layers.append(layer)
        return layer

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": FILE_VERSION,
            "name": self.name,
            "units": self.units,
            "layers": [lyr.to_dict() for lyr in self.layers],
            "shapes": [_shape_to_dict(s) for s in self.shapes],
            "stitch_lines": [_stitchline_to_dict(sl) for sl in self.stitch_lines],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        doc = cls(name=d.get("name", "Untitled"))
        doc.units = d.get("units", "mm")
        if d.get("layers"):
            doc.layers = [Layer.from_dict(x) for x in d["layers"]]
        doc.shapes = [_shape_from_dict(x) for x in d.get("shapes", [])]
        doc.stitch_lines = [_stitchline_from_dict(x)
                            for x in d.get("stitch_lines", [])]
        return doc

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "Document":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

"""The CAD document: layers, shapes and stitch lines, with JSON save/load.

Coordinates are millimetres throughout, Y-up. The document is the single source
of truth the GUI edits and the exporters read.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields as dc_fields
from typing import List, Optional

from .geometry import Vec2
from .layers import Layer, default_layers
from .shapes import (Shape, Rectangle, Ellipse, Circle, Polygon, PathShape,
                     EditablePath, Edge, Transform)
from .stitchsettings import StitchSettings
from .stitchline import StitchLine
from .holes import LooseHole
from .stitching import Hole


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
    # tolerate keys from other versions (added/removed fields) so documents
    # round-trip across releases instead of raising on an unexpected kwarg
    valid = {f.name for f in dc_fields(StitchSettings)}
    return StitchSettings(**{k: v for k, v in d.items() if k in valid})


def _shape_to_dict(sh: Shape) -> dict:
    base = {
        "kind": sh.kind,
        "name": sh.name,
        "layer": sh.layer,
        "opacity": sh.opacity,
        "shape_id": sh.shape_id,
        "transform": _transform_to_dict(sh.transform),
        "stitch": _stitch_to_dict(sh.stitch),
        "construction": sh.construction,
        "group_id": sh.group_id,
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
    elif isinstance(sh, EditablePath):
        base.update(
            nodes=[[p.x, p.y] for p in sh.nodes],
            edges=[{"kind": e.kind,
                    "mid": [e.mid.x, e.mid.y] if e.mid is not None else None,
                    "c1": [e.c1.x, e.c1.y] if e.c1 is not None else None,
                    "c2": [e.c2.x, e.c2.y] if e.c2 is not None else None}
                   for e in sh.edges],
            closed=sh.closed)
    if sh.baked_holes:
        base["baked_holes"] = [[h.point.x, h.point.y, h.tangent.x, h.tangent.y]
                               for h in sh.baked_holes]
    return base


def _apply_baked(sh: Shape, d: dict) -> Shape:
    if d.get("baked_holes"):
        sh.baked_holes = [Hole(Vec2(x, y), Vec2(tx, ty))
                          for x, y, tx, ty in d["baked_holes"]]
    return sh


def _shape_from_dict(d: dict) -> Shape:
    kind = d.get("kind", "rectangle")
    common = dict(
        name=d.get("name", ""),
        layer=d.get("layer", "cut"),
        opacity=d.get("opacity", 1.0),
        transform=_transform_from_dict(d.get("transform", {})),
        stitch=_stitch_from_dict(d.get("stitch")),
        construction=d.get("construction", False),
        group_id=d.get("group_id"),
    )
    if "shape_id" in d:
        common["shape_id"] = d["shape_id"]
    if kind == "rectangle":
        sh = Rectangle(width=d.get("width", 50), height=d.get("height", 30),
                       corner_radius=d.get("corner_radius", 0.0), **common)
    elif kind == "circle":
        sh = Circle(rx=d.get("rx", 20), ry=d.get("ry", d.get("rx", 20)), **common)
    elif kind == "ellipse":
        sh = Ellipse(rx=d.get("rx", 25), ry=d.get("ry", 15), **common)
    elif kind == "polygon":
        pts = [Vec2(x, y) for x, y in d.get("points", [])]
        sh = Polygon(points=pts, corner_radius=d.get("corner_radius", 0.0),
                     close_path=d.get("close_path", True),
                     sharp_corners=d.get("sharp_corners", True), **common)
    elif kind == "path":
        pts = [Vec2(x, y) for x, y in d.get("points", [])]
        sh = PathShape(points=pts, close_path=d.get("close_path", False), **common)
    elif kind == "editpath":
        nodes = [Vec2(x, y) for x, y in d.get("nodes", [])]
        edges = [Edge(e.get("kind", "line"),
                      Vec2(*e["mid"]) if e.get("mid") else None,
                      c1=Vec2(*e["c1"]) if e.get("c1") else None,
                      c2=Vec2(*e["c2"]) if e.get("c2") else None)
                 for e in d.get("edges", [])]
        sh = EditablePath(nodes=nodes, edges=edges,
                          closed=d.get("closed", True), **common)
    else:
        raise ValueError(f"unknown shape kind {kind!r}")
    return _apply_baked(sh, d)


def _hole_to_dict(h: LooseHole) -> dict:
    return {
        "point": [h.point.x, h.point.y],
        "tangent": [h.tangent.x, h.tangent.y],
        "hole_style": h.hole_style,
        "hole_diameter": h.hole_diameter,
        "slit_length": h.slit_length,
        "slit_angle": h.slit_angle,
        "layer": h.layer,
        "group_id": h.group_id,
    }


def _hole_from_dict(d: dict) -> LooseHole:
    return LooseHole(
        point=Vec2(*d.get("point", [0, 0])),
        tangent=Vec2(*d.get("tangent", [1, 0])),
        hole_style=d.get("hole_style", "round"),
        hole_diameter=d.get("hole_diameter", 1.0),
        slit_length=d.get("slit_length", 1.6),
        slit_angle=d.get("slit_angle", 30.0),
        layer=d.get("layer", "Stitch"),
        group_id=d.get("group_id"),
    )


def _stitchline_to_dict(sl: StitchLine) -> dict:
    return {
        "points": [[p.x, p.y] for p in sl.points],
        "closed": sl.closed,
        "corner_points": [[p.x, p.y] for p in sl.corner_points],
        "settings": asdict(sl.settings),
        "name": sl.name,
        "layer": sl.layer,
        "line_id": sl.line_id,
        "group_id": sl.group_id,
    }


def _stitchline_from_dict(d: dict) -> StitchLine:
    sl = StitchLine(
        points=[Vec2(x, y) for x, y in d.get("points", [])],
        closed=d.get("closed", False),
        corner_points=[Vec2(x, y) for x, y in d.get("corner_points", [])],
        settings=StitchSettings(**d.get("settings", {})),
        name=d.get("name", ""),
        layer=d.get("layer", "Stitch"),
        group_id=d.get("group_id"),
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
        self.holes: List[LooseHole] = []   # individual, ungrouped holes
        self.dimensions: list = []         # linear dimension annotations
        self.texts: list = []              # engrave lettering
        # laser kerf compensation (mm) applied to cut geometry on SVG/DXF
        # export: outer outlines grow by kerf/2, cutouts and holes shrink.
        self.kerf: float = 0.0
        # tracing underlay: a reference photo behind the drawing (never
        # exported). {path, x, y, scale, opacity, visible} or None.
        self.underlay: Optional[dict] = None
        # user parameters (Fusion-style): ordered {name: expression}, usable
        # in any numeric field. bindings remember which shape fields were set
        # FROM a parameter expression ("shape_id:field" -> expression), so a
        # parameter edit re-drives every field that used it.
        self.params: dict = {}
        self.bindings: dict = {}

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

    def add_hole(self, hole: LooseHole) -> LooseHole:
        self.holes.append(hole)
        return hole

    def remove_hole(self, hole: LooseHole) -> None:
        if hole in self.holes:
            self.holes.remove(hole)

    def param_values(self) -> dict:
        """Concrete values of all user parameters (may reference each other)."""
        from .expr import resolve
        return resolve(self.params)

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
            "holes": [_hole_to_dict(h) for h in self.holes],
            "dimensions": [dm.to_dict() for dm in self.dimensions],
            "texts": [tx.to_dict() for tx in self.texts],
            "kerf": self.kerf,
            "underlay": self.underlay,
            "params": dict(self.params),
            "bindings": dict(self.bindings),
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
        doc.holes = [_hole_from_dict(x) for x in d.get("holes", [])]
        from .dimension import Dimension
        doc.dimensions = [Dimension.from_dict(x) for x in d.get("dimensions", [])]
        from .text import TextShape
        doc.texts = [TextShape.from_dict(x) for x in d.get("texts", [])]
        doc.kerf = float(d.get("kerf", 0.0))
        doc.underlay = d.get("underlay") or None
        doc.params = dict(d.get("params", {}))
        doc.bindings = dict(d.get("bindings", {}))
        return doc

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "Document":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

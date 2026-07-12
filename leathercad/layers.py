"""Color layers mapped to laser jobs.

Laser cutters run different operations by colour/layer: vector CUT, vector SCORE
(a light cut / fold line), and raster ENGRAVE. Each :class:`Layer` carries a
display colour and a laser role so export can group geometry the way the cutter
expects.
"""

from __future__ import annotations

from dataclasses import dataclass


# Laser roles
CUT = "cut"
SCORE = "score"
ENGRAVE = "engrave"
STITCH = "stitch"   # the stitch holes themselves

ROLES = (CUT, SCORE, ENGRAVE, STITCH)


@dataclass
class Layer:
    name: str
    color: str = "#ff0000"     # display + export stroke colour (hex)
    role: str = CUT
    visible: bool = True
    locked: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "color": self.color,
            "role": self.role,
            "visible": self.visible,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        return cls(name=d["name"], color=d.get("color", "#ff0000"),
                   role=d.get("role", CUT), visible=d.get("visible", True),
                   locked=d.get("locked", False))


def default_layers() -> list:
    return [
        Layer("Cut", "#ff0000", CUT),
        Layer("Stitch", "#0066ff", STITCH),
        Layer("Score", "#00aa00", SCORE),
        Layer("Engrave", "#888888", ENGRAVE),
    ]

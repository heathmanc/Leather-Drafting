"""Offset / seam-allowance engine + command."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.geometry import Vec2
from leathercad.offset import offset_closed, offset_open


def _bounds(ring):
    xs = [p.x for p in ring]
    ys = [p.y for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def test_offset_closed_outward_and_inward():
    sq = [Vec2(0, 0), Vec2(40, 0), Vec2(40, 30), Vec2(0, 30)]
    out = offset_closed(sq, 5.0)          # grow 5 mm
    assert _bounds(out[:-1]) == (-5.0, -5.0, 45.0, 35.0)
    inn = offset_closed(sq, -5.0)         # shrink 5 mm
    assert _bounds(inn[:-1]) == (5.0, 5.0, 35.0, 25.0)


def test_offset_open_shifts_sideways():
    line = [Vec2(0, 0), Vec2(10, 0)]
    shifted = offset_open(line, 2.0)      # right-hand normal of +x travel is -y
    assert all(abs(p.y - (-2.0)) < 1e-9 for p in shifted)


def test_offset_command_makes_new_shape(qapp=None):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from leathercad_app import canvas as cm
    from leathercad.shapes import Rectangle, Transform
    from leathercad.document import Document
    from leathercad_app.items import ShapeItem

    doc = Document()
    doc.add_shape(Rectangle(width=40, height=30,
                            transform=Transform(x=50, y=50), layer="Cut"))
    c = cm.Canvas(doc)
    c.rebuild()
    item = next(it for it in c.scene_obj.items() if isinstance(it, ShapeItem))
    item.setSelected(True)
    c.offset_selected(5.0)
    assert len(doc.shapes) == 2
    b = doc.shapes[-1].bounds()
    assert tuple(round(v, 1) for v in b) == (25.0, 30.0, 75.0, 70.0)

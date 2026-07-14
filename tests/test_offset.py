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


def test_offset_shape_circle_stays_parametric():
    from leathercad.offset import offset_shape
    from leathercad.shapes import Circle, Transform
    c = Circle(rx=20, ry=20, transform=Transform(x=5, y=7), layer="Cut")
    out = offset_shape(c, 4.0)
    assert isinstance(out, Circle) and out.rx == 24.0 and out.ry == 24.0
    assert (out.transform.x, out.transform.y) == (5, 7)
    inn = offset_shape(c, -4.0)
    assert isinstance(inn, Circle) and inn.rx == 16.0
    assert offset_shape(c, -20.0) is None          # collapses -> refused


def test_offset_shape_rounded_rect_keeps_corners():
    from leathercad.offset import offset_shape
    from leathercad.shapes import Rectangle, Transform
    r = Rectangle(width=40, height=30, corner_radius=6,
                  transform=Transform(x=0, y=0), layer="Cut")
    out = offset_shape(r, 3.0)
    assert isinstance(out, Rectangle)
    assert (out.width, out.height) == (46.0, 36.0)
    assert out.corner_radius == 9.0                # true offset arc: r + d
    inn = offset_shape(r, -3.0)
    assert (inn.width, inn.height) == (34.0, 24.0)
    assert inn.corner_radius == 3.0
    deep = offset_shape(r, -8.0)                   # past the radius -> sharp
    assert deep.corner_radius == 0.0
    sharp = Rectangle(width=40, height=30, transform=Transform(x=0, y=0))
    assert offset_shape(sharp, 5.0).corner_radius == 0.0   # sharp stays sharp
    assert offset_shape(sharp, -16.0) is None      # would collapse the height


def test_offset_tool_interactive_inside_outside():
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
    c.resize(600, 450)
    c.tool = cm.OFFSET
    c.snap_to_grid = False

    # arm on the shape's outline, cursor 5 mm OUTSIDE the right edge
    c._arm_offset(Vec2(70, 50))
    assert c._offset_item is not None
    c._update_offset_preview(Vec2(75, 50))
    assert abs(c._offset_dist - 5.0) < 1e-6        # outward = positive
    assert c._offset_preview is not None           # dashed preview live
    c._commit_offset()
    assert len(doc.shapes) == 2
    grown = doc.shapes[-1]
    assert (grown.width, grown.height) == (50.0, 40.0)
    assert c._offset_item is None                  # state cleared, tool ready

    # inside the outline -> negative distance -> smaller copy
    c._arm_offset(Vec2(70, 50))
    c._update_offset_preview(Vec2(65, 50))
    assert abs(c._offset_dist - (-5.0)) < 1e-6
    c._commit_offset()
    assert (doc.shapes[-1].width, doc.shapes[-1].height) == (30.0, 20.0)

    # arming on empty space does nothing
    c._arm_offset(Vec2(500, 500))
    assert c._offset_item is None

    # Esc cancels a live preview without creating anything
    n = len(doc.shapes)
    c._arm_offset(Vec2(70, 50))
    c._update_offset_preview(Vec2(80, 50))
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent, Qt
    c.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert c._offset_item is None and c._offset_preview is None
    assert len(doc.shapes) == n


def test_offset_tool_snaps_distance_to_grid():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from leathercad_app import canvas as cm
    from leathercad.shapes import Rectangle, Transform
    from leathercad.document import Document

    doc = Document()
    doc.add_shape(Rectangle(width=40, height=30,
                            transform=Transform(x=50, y=50), layer="Cut"))
    c = cm.Canvas(doc)
    c.rebuild()
    c.tool = cm.OFFSET
    c.snap_to_grid = True
    c.snap_grid = 1.0
    c._arm_offset(Vec2(70, 50))
    c._update_offset_preview(Vec2(75.4, 50))       # 5.4 mm out -> 5.0 on grid
    assert abs(c._offset_dist - 5.0) < 1e-9
    c._cancel_offset()


def test_array_grid_and_circular():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import leathercad_app.canvas as cm
    from leathercad.document import Document, LooseHole
    from leathercad.geometry import Vec2

    doc = Document()
    doc.holes.append(LooseHole(point=Vec2(0, 0)))
    c = cm.Canvas(doc)
    c.rebuild()
    h = next(it for it in c.scene_obj.items() if getattr(it, "hole", None))
    h.setSelected(True)
    c.array_grid(2, 3, 10, 8)
    pts = {(round(x.point.x), round(x.point.y)) for x in doc.holes}
    assert len(doc.holes) == 6
    assert (20, 8) in pts and (0, 0) in pts

    doc2 = Document()
    doc2.holes.append(LooseHole(point=Vec2(20, 0)))
    c2 = cm.Canvas(doc2)
    c2.rebuild()
    h2 = next(it for it in c2.scene_obj.items() if getattr(it, "hole", None))
    h2.setSelected(True)
    c2.array_circular(4, 0, 0, 360, True)
    pts2 = {(round(x.point.x), round(x.point.y)) for x in doc2.holes}
    assert pts2 == {(20, 0), (0, 20), (-20, 0), (0, -20)}

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


# ---------------------------------------------------------------------------
# Inside-out loops: a corner tighter than the offset used to tie a small
# backwards loop into the ring (the stitch marcher then piled holes into it).
# ---------------------------------------------------------------------------
def _rounded_rect_pts(w, h, r):
    from leathercad.shapes import Rectangle
    return Rectangle(width=w, height=h, corner_radius=r).local_path().flatten()


def test_inset_past_a_tight_fillet_gives_a_clean_mitred_corner():
    from leathercad.offset import offset_closed_inward, _self_crossings
    for inset in (3.5, 8.0):
        ring = offset_closed_inward(_rounded_rect_pts(80, 50, 3.0), inset)
        assert _self_crossings(ring[:-1]) == []
        # the r=3 corner shrunk past its radius -> a sharp inset corner
        hx, hy = 40 - inset, 25 - inset
        near = [p for p in ring if p.x > hx - 1 and p.y > hy - 1]
        assert near and all(abs(p.x - hx) < 1e-6 and abs(p.y - hy) < 1e-6
                            for p in near)


def test_outward_offset_of_a_tight_concave_fillet_has_no_loop():
    from leathercad.offset import _self_crossings
    from leathercad.shapes import Polygon
    pts = Polygon(points=[Vec2(0, 0), Vec2(60, 0), Vec2(60, 20), Vec2(20, 20),
                          Vec2(20, 50), Vec2(0, 50)],
                  corner_radius=1.0).local_path().flatten()
    out = offset_closed(pts, 4.0)
    assert _self_crossings(out[:-1]) == []


def test_inset_wider_than_the_piece_has_no_stitch_line():
    from leathercad.offset import offset_closed_inward
    sq = [Vec2(0, 0), Vec2(5, 0), Vec2(5, 5), Vec2(0, 5)]
    assert offset_closed_inward(sq, 3.0) == []


def test_kerf_offset_never_drops_a_tiny_cut():
    # a collapse keeps the raw miter ring: export must not lose a cut outline
    sq = [Vec2(0, 0), Vec2(1, 0), Vec2(1, 1), Vec2(0, 1)]
    assert len(offset_closed(sq, -2.0)) >= 4


def test_rippled_edge_trims_fast_and_clean():
    # a scalloped edge whose ripples are far tighter than the offset: every
    # ripple ties a loop (thousands of crossings) -- this took ~45 s with
    # loop-at-a-time removal and still left crossings behind
    import math
    import time
    from leathercad.offset import offset_closed_inward, _self_crossings
    n, waves, amp = 4000, 120, 2.0
    pts = [Vec2((60 + amp * math.sin(waves * 2 * math.pi * i / n))
                * math.cos(2 * math.pi * i / n),
                (60 + amp * math.sin(waves * 2 * math.pi * i / n)) * 0.7
                * math.sin(2 * math.pi * i / n)) for i in range(n)]
    for d in (3.5, 8.0):
        t0 = time.perf_counter()
        ring = offset_closed_inward(pts, d)
        assert time.perf_counter() - t0 < 5.0
        assert len(ring) > 3 and _self_crossings(ring[:-1]) == []


def test_ellipse_narrower_than_twice_the_inset_has_no_stitch_line():
    # folds into a ring whose backwards edges never cross each other
    from leathercad.offset import offset_closed_inward
    from leathercad.shapes import Ellipse
    pts = Ellipse(rx=6.5, ry=4.0).local_path().flatten()
    assert offset_closed_inward(pts, 5.0) == []
    assert len(offset_closed_inward(pts, 3.0)) > 3


def test_cusp_in_a_pen_curve_cannot_blow_up_the_inset():
    # Two pen anchors a hair apart (one dragged, one clicked 0.00005 mm away)
    # leave a microscopic edge pointing any which way: a cusp, whose
    # neighbouring offsets meet hundreds of metres away. The 185 mm outline's
    # 2 mm inset came out ~1.9 km long and marching holes along it hung the
    # app. The mitre limit bevels such joins.
    import time
    from leathercad.offset import inset_path_with_corners, offset_closed_inward
    from leathercad.shapes import EditablePath
    ep = EditablePath.from_bezier(
        [Vec2(49.105893881064134, 24.94855314913302),
         Vec2(-22.011279912865902, 46.93375132336571),
         Vec2(-22.01133437517618, 46.93375132336571)],
        [Vec2(8.324121720118725, -16.903495336316972),
         Vec2(17.283022524038508, 18.719414941902784), None], closed=True)
    path = ep.local_path()
    src = path.flatten()
    outline = sum((src[i + 1] - src[i]).length() for i in range(len(src) - 1))
    for ring in (inset_path_with_corners(path, 2.0, [])[0],
                 offset_closed_inward(src, 2.0)):
        length = sum((ring[i + 1] - ring[i]).length()
                     for i in range(len(ring) - 1))
        assert 0 < length < 1.5 * outline
    from leathercad.stitching import stitch_shape_path
    from leathercad.stitchsettings import StitchSettings
    t0 = time.perf_counter()
    res = stitch_shape_path(path, StitchSettings(inset=2.0, pitch_mm=3.85))
    assert time.perf_counter() - t0 < 2.0 and 0 < len(res.holes) < 100

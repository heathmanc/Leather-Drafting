"""Perfect fit: piece sizes at which every stitch gap is exactly the pitch.

The engine half (leathercad.perfectfit) is checked against the stitcher
itself -- a size is only perfect if the holes it actually places are all a
pitch apart. The app half checks the toolbar toggle, the resize grips, the
drawing tools, Edit > Snap to perfect fit and "Use for new shapes"."""

import math
import os

import pytest

from leathercad import perfectfit as pf
from leathercad.geometry import Vec2
from leathercad.shapes import (Circle, Ellipse, Polygon, Rectangle,
                               Transform)
from leathercad.stitching import Hole, holes_for_shape
from leathercad.stitchsettings import StitchSettings


def _worst(shape, pitch):
    """(holes, largest |gap - pitch|) of the stitcher's own holes."""
    res = holes_for_shape(shape)
    gaps = res.chord_spacings()
    return res.count, max(abs(g - pitch) for g in gaps)


# -- the formulas ----------------------------------------------------------
def test_corner_radius_is_the_iron_chord_formula():
    # the same corner test_stitching pins: R 10.5328 at a 4 mm inset turns
    # a 5 mm iron round a 90 degree corner in exactly two gaps
    assert pf.corner_radius(2, 5.0, 4.0) == pytest.approx(
        4.0 + 5.0 / (2 * math.sin(math.radians(22.5))))
    assert pf.corner_radius(1, 5.0, 3.5) == pytest.approx(3.5 + 5.0 / math.sqrt(2))


def test_snap_corner_radius():
    p, i = 5.0, 3.5
    # up to the inset the stitch line keeps a sharp corner: any radius fits
    assert pf.snap_corner_radius(0.0, p, i) == (0.0, 0)
    assert pf.snap_corner_radius(2.0, p, i) == (2.0, 0)
    # just over the inset rounds back down to it
    assert pf.snap_corner_radius(4.0, p, i) == (i, 0)
    # otherwise the nearest radius a whole number of gaps turns
    r2 = pf.corner_radius(2, p, i)
    assert pf.snap_corner_radius(r2 + 0.4, p, i) == (pytest.approx(r2), 2)
    # Corners: midpoint wants an even count, straddle an odd one (or 2,
    # which is too short to straddle and fitted freely)
    r3 = pf.corner_radius(3, p, i)
    _, n = pf.snap_corner_radius(r3, p, i, "midpoint")
    assert n % 2 == 0
    _, n = pf.snap_corner_radius(pf.corner_radius(4, p, i), p, i, "straddle")
    assert n % 2 == 1


def test_snap_run_never_goes_below_its_minimum():
    assert pf.snap_run(3.0, 7.0, 5.0, 1) == (12.0, 1)
    assert pf.snap_run(31.0, 7.0, 5.0, 1) == (32.0, 5)


# -- every perfect size really is perfect -----------------------------------
@pytest.mark.parametrize("pitch,inset", [(5.0, 3.5), (3.85, 3.0),
                                         (2.7, 4.0), (25.4 / 7, 3.2)])
@pytest.mark.parametrize("style", ["auto", "midpoint", "straddle"])
def test_rectangles_are_exact(pitch, inset, style):
    st = StitchSettings(pitch_mm=pitch, inset=inset, corner_style=style)
    for radius in (0.0, 2.0, inset + 0.3, 6.0, 12.5, 31.0):
        for w, h in ((100, 60), (37, 81), (12, 12)):
            fit = pf.fit_shape(Rectangle(width=w, height=h,
                                         corner_radius=radius, stitch=st),
                               w / 2, h / 2)
            snapped = Rectangle(width=2 * fit.hx, height=2 * fit.hy,
                                corner_radius=fit.corner_radius, stitch=st)
            n, worst = _worst(snapped, pitch)
            assert worst < 1e-3, (pitch, inset, style, radius, w, h, fit)
            assert n == fit.holes
            # and it is the NEAREST perfect size: within half a pitch
            assert abs(2 * fit.hx - w) <= pitch / 2 + 1e-9 or fit.side_gaps[0] <= 1
            assert abs(2 * fit.hy - h) <= pitch / 2 + 1e-9 or fit.side_gaps[1] <= 1


def test_rectangle_sizes_are_the_textbook_ones():
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    sharp = pf.fit_shape(Rectangle(width=100, height=63, stitch=st), 50, 31.5)
    assert (2 * sharp.hx, 2 * sharp.hy) == (pytest.approx(102.0),
                                            pytest.approx(62.0))
    assert sharp.side_gaps == (19, 11) and sharp.holes == 60
    st4 = StitchSettings(pitch_mm=5.0, inset=4.0)
    rr = pf.fit_shape(Rectangle(width=190, height=170, corner_radius=10,
                                stitch=st4), 95, 85)
    # test_stitching's exact piece: 191.0656 x 171.0656, R10.5328
    assert rr.corner_radius == pytest.approx(10.532815, abs=1e-6)
    assert 2 * rr.hx == pytest.approx(191.065630, abs=1e-5)
    assert 2 * rr.hy == pytest.approx(171.065630, abs=1e-5)


def test_tiny_rectangles_stop_at_the_smallest_perfect_size():
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    sharp = pf.fit_shape(Rectangle(width=2, height=2, stitch=st), 1, 1)
    assert 2 * sharp.hx == pytest.approx(12.0)           # 2 x inset + pitch
    r = pf.corner_radius(2, 5.0, 3.5)
    rnd = pf.fit_shape(Rectangle(width=2, height=2, corner_radius=r,
                                 stitch=st), 1, 1)
    assert 2 * rnd.hx == pytest.approx(2 * r)            # corners only
    snapped = Rectangle(width=2 * rnd.hx, height=2 * rnd.hy,
                        corner_radius=rnd.corner_radius, stitch=st)
    assert _worst(snapped, 5.0)[1] < 1e-3                 # a circle of 8 gaps


def test_unsewn_edge_keeps_a_rectangle_exact():
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    for radius in (0.0, 14.0):
        fit = pf.fit_shape(Rectangle(width=100, height=60,
                                     corner_radius=radius, stitch=st), 50, 30)
        sh = Rectangle(width=2 * fit.hx, height=2 * fit.hy,
                       corner_radius=fit.corner_radius, stitch=st)
        segs = len(sh.local_path().segments)
        for skip in range(segs):
            sh.stitch_edges = [k != skip for k in range(segs)]
            assert _worst(sh, 5.0)[1] < 1e-3, (radius, skip)


@pytest.mark.parametrize("pitch,inset", [(5.0, 3.5), (3.85, 3.0), (2.7, 4.0)])
def test_circles_are_exact(pitch, inset):
    for radius in (5.0, 9.3, 22.2, 60.0):
        for sym in ("none", "vertical"):
            st = StitchSettings(pitch_mm=pitch, inset=inset, symmetry=sym)
            fit = pf.fit_shape(Circle(rx=radius, ry=radius, stitch=st),
                               radius, radius)
            assert fit.hx == fit.hy
            n, worst = _worst(Circle(rx=fit.hx, ry=fit.hx, stitch=st), pitch)
            assert worst < 1e-6 and n == fit.holes, (radius, sym, fit)
            if sym != "none":
                assert n % 2 == 0
            # within a few hundredths of the ideal circle's radius
            ideal = inset + pitch / (2 * math.sin(math.pi / n))
            assert abs(fit.hx - ideal) < 0.05


@pytest.mark.parametrize("axis", ["both", "x", "y"])
def test_ovals_are_exact(axis):
    for pitch, inset in ((5.0, 3.5), (3.85, 3.0)):
        for rx, ry in ((40, 25), (15, 9), (25, 40)):
            for sym in ("none", "horizontal"):
                st = StitchSettings(pitch_mm=pitch, inset=inset, symmetry=sym)
                fit = pf.fit_shape(Ellipse(rx=rx, ry=ry, stitch=st), rx, ry,
                                   axis)
                n, worst = _worst(Ellipse(rx=fit.hx, ry=fit.hy, stitch=st),
                                  pitch)
                assert worst < 1e-6 and n == fit.holes, (rx, ry, axis, sym)
                if axis == "x":
                    assert fit.hy == ry          # only the pulled axis gives
                if axis == "y":
                    assert fit.hx == rx
                if axis == "both":               # proportions kept
                    assert fit.hx / fit.hy == pytest.approx(rx / ry)


def test_shapes_without_perfect_sizes():
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    assert pf.fit_shape(Rectangle(width=50, height=30), 25, 15) is None
    off = StitchSettings(pitch_mm=5.0, inset=3.5, enabled=False)
    assert pf.fit_shape(Rectangle(width=50, height=30, stitch=off), 25, 15) is None
    baked = Rectangle(width=50, height=30, stitch=st,
                      baked_holes=[Hole(Vec2(0, 0), Vec2(1, 0))])
    assert pf.fit_shape(baked, 25, 15) is None
    tri = Polygon(points=[Vec2(0, 0), Vec2(40, 0), Vec2(20, 30)], stitch=st)
    assert pf.fit_shape(tri, 20, 15) is None
    oval = Ellipse(rx=30, ry=20, stitch=st)
    segs = len(oval.local_path().segments)
    oval.stitch_edges = [k != 3 for k in range(segs)]
    assert pf.fit_shape(oval, 30, 20) is None


# -- the app ------------------------------------------------------------------
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp):
    """A main window whose Perfect fit / new-shape settings are put back
    afterwards (they persist in the user's settings)."""
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    w = MainWindow(Document())
    s = w._settings()
    saved = (s.value("perfectFit", None), s.value("newShapeStitch", None))
    yield w
    for key, val in zip(("perfectFit", "newShapeStitch"), saved):
        if val is None:
            s.remove(key)
        else:
            s.setValue(key, val)
    w.hide()


def _add(c, shape):
    """Add ``shape`` (an undo step, like drawing it) and select it alone;
    its item and resize grips."""
    from leathercad_app.items import ResizeHandle
    item = c.add_shape(shape)
    c.scene_obj.clearSelection()
    item.setSelected(True)
    c.selection_changed()
    grips = {g.grip: g for g in c.scene_obj.items()
             if isinstance(g, ResizeHandle)}
    return item, grips


def test_toggle_is_off_by_default_and_remembered(win):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    win._settings().remove("perfectFit")
    fresh = MainWindow(Document())
    assert fresh.act_perfect_fit.isChecked() is False
    assert fresh.canvas.perfect_fit is False
    fresh.act_perfect_fit.setChecked(True)
    assert fresh.canvas.perfect_fit is True
    again = MainWindow(Document())
    assert again.canvas.perfect_fit is True        # saved for next time
    fresh.act_perfect_fit.setChecked(False)
    fresh.hide()
    again.hide()


def test_grips_snap_a_rounded_rectangle(win):
    c = win.canvas
    sh = Rectangle(width=100, height=60, corner_radius=12.0,
                   transform=Transform(x=300, y=0), layer="Cut",
                   stitch=StitchSettings(pitch_mm=3.85, inset=3.0))
    item, grips = _add(c, sh)
    msgs = []
    c.statusMessage.connect(msgs.append)
    win.act_perfect_fit.setChecked(True)
    grips[(1, 1)]._apply_resize(Vec2(361.3, 33.2))
    item.sync_from_model()
    r4 = pf.corner_radius(4, 3.85, 3.0)
    assert sh.corner_radius == pytest.approx(r4)       # corners snapped too
    assert ((sh.width - 2 * r4) / 3.85) == pytest.approx(
        round((sh.width - 2 * r4) / 3.85))
    assert _worst(sh, 3.85)[1] < 1e-3
    # the opposite corner stayed put
    assert sh.transform.x - sh.width / 2 == pytest.approx(250.0)
    assert sh.transform.y - sh.height / 2 == pytest.approx(-30.0)
    assert msgs[-1].startswith("Perfect fit:") and "3.85 mm" in msgs[-1]
    # an edge grip moves only its own size (the other is already perfect)
    h0 = sh.height
    grips[(1, 0)]._apply_resize(Vec2(380, 0))
    item.sync_from_model()
    assert sh.height == pytest.approx(h0)
    assert _worst(sh, 3.85)[1] < 1e-3
    # off again: the grip resizes freely
    win.act_perfect_fit.setChecked(False)
    grips[(1, 0)]._apply_resize(Vec2(390.123, 0))
    assert _worst(sh, 3.85)[1] > 1e-3


def test_dragging_a_grip_with_the_mouse(win):
    """The real press / move / release: no node snapping, the grip glyph
    rides on the snapped corner, and the drag is one undo step."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent
    c = win.canvas
    sh = Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                   layer="Cut", stitch=StitchSettings(pitch_mm=5.0, inset=3.5))
    item, grips = _add(c, sh)
    win.act_perfect_fit.setChecked(True)
    g = grips[(1, 1)]

    def ev(kind, x, y):
        e = QGraphicsSceneMouseEvent(kind)
        e.setScenePos(QPointF(x, y))
        e.setButton(Qt.LeftButton)
        e.setButtons(Qt.LeftButton)
        e.setModifiers(Qt.NoModifier)
        return e

    g.mousePressEvent(ev(QEvent.GraphicsSceneMousePress, 30, 20))
    assert c._snap_cache is None               # Perfect fit sizes it instead
    g.mouseMoveEvent(ev(QEvent.GraphicsSceneMouseMove, 41.3, 27.9))
    assert (sh.width, sh.height) == (pytest.approx(72.0), pytest.approx(47.0))
    # the opposite corner stays at (-30, -20); the glyph sits on the new one
    assert (g.pos().x(), g.pos().y()) == (pytest.approx(42.0),
                                          pytest.approx(27.0))
    g.mouseReleaseEvent(ev(QEvent.GraphicsSceneMouseRelease, 41.3, 27.9))
    assert _worst(sh, 5.0)[1] < 1e-9
    win.undo()
    back = win.doc.shapes[-1]
    assert (back.width, back.height) == (60, 40)


def test_grips_snap_circles_and_ovals(win):
    c = win.canvas
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    ci = Circle(rx=20, ry=20, transform=Transform(x=0, y=0), layer="Cut",
                stitch=st)
    win.act_perfect_fit.setChecked(True)
    item, grips = _add(c, ci)
    grips[(1, 0)]._apply_resize(Vec2(26.0, 0))       # pull the right edge
    assert ci.rx == ci.ry
    assert _worst(ci, 5.0)[1] < 1e-6
    ov = Ellipse(rx=40, ry=25, transform=Transform(x=200, y=0), layer="Cut",
                 stitch=StitchSettings(pitch_mm=5.0, inset=3.5))
    item, grips = _add(c, ov)
    grips[(0, 1)]._apply_resize(Vec2(200, 31.0))     # pull the top edge
    assert _worst(ov, 5.0)[1] < 1e-6


def test_grips_leave_other_outlines_free(win):
    c = win.canvas
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    tri = Polygon(points=[Vec2(0, 0), Vec2(40, 0), Vec2(20, 30)],
                  transform=Transform(x=0, y=0), layer="Cut", stitch=st)
    win.act_perfect_fit.setChecked(True)
    item, grips = _add(c, tri)
    msgs = []
    c.statusMessage.connect(msgs.append)
    assert not c.perfect_fit_applies(item)
    grips[(1, 0)]._apply_resize(Vec2(52.345, 15))
    xs = [p.x for p in tri.points]
    assert max(xs) - min(xs) == pytest.approx(52.345)    # exactly where pulled
    assert any("resizes freely" in m for m in msgs)


def test_drawing_tools_snap_to_the_new_shape_stitching(win):
    from PySide6.QtCore import QPointF
    from leathercad_app import canvas as cm
    c = win.canvas
    win._save_new_stitch(StitchSettings(pitch_mm=5.0, inset=3.5))
    win.act_perfect_fit.setChecked(True)
    c.tool = cm.RECT
    # the rubber band shows the size it will make
    end, fit = c._perfect_draw(QPointF(0, 0), QPointF(100, 63))
    assert (end.x(), end.y()) == (pytest.approx(102.0), pytest.approx(62.0))
    c._finalize_drag(QPointF(0, 0), QPointF(100, 63))
    r = win.doc.shapes[-1]
    assert (r.width, r.height) == (pytest.approx(102.0), pytest.approx(62.0))
    assert r.transform.x == pytest.approx(51.0)         # drawn from the click
    assert _worst(r, 5.0)[1] < 1e-9
    c.tool = cm.ROUNDED
    c._finalize_drag(QPointF(0, 0), QPointF(-120, 70))
    rr = win.doc.shapes[-1]
    assert rr.corner_radius == pytest.approx(pf.corner_radius(3, 5.0, 3.5))
    assert _worst(rr, 5.0)[1] < 1e-3
    c.tool = cm.CIRCLE
    c._finalize_drag(QPointF(0, 0), QPointF(30, 0))
    assert _worst(win.doc.shapes[-1], 5.0)[1] < 1e-6
    c.tool = cm.ELLIPSE
    c._finalize_drag(QPointF(0, 0), QPointF(80, 50))
    assert _worst(win.doc.shapes[-1], 5.0)[1] < 1e-6
    # a mis-click is still refused, not turned into the smallest perfect box
    n = len(win.doc.shapes)
    c.tool = cm.RECT
    c._finalize_drag(QPointF(0, 0), QPointF(0.2, 0.3))
    assert len(win.doc.shapes) == n
    # with the toggle off, drawing is free again
    win.act_perfect_fit.setChecked(False)
    c._finalize_drag(QPointF(0, 0), QPointF(100, 63))
    assert (win.doc.shapes[-1].width, win.doc.shapes[-1].height) == (100, 63)


def test_snap_selection_to_perfect_fit(win):
    c = win.canvas
    st = StitchSettings(pitch_mm=5.0, inset=3.5)
    sh = Rectangle(width=100, height=60, corner_radius=9.0,
                   transform=Transform(x=10, y=20), layer="Cut", stitch=st)
    _add(c, sh)
    assert not c.perfect_fit                 # works with the toggle off
    c.perfect_fit_selected()
    assert _worst(sh, 5.0)[1] < 1e-3
    assert (sh.transform.x, sh.transform.y) == (10, 20)   # about its centre
    win.undo()                               # one undo step
    restored = win.doc.shapes[-1]
    assert (restored.width, restored.height,
            restored.corner_radius) == (100, 60, 9.0)


def test_use_for_new_shapes(win):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    c = win.canvas
    sh = Rectangle(width=80, height=50, transform=Transform(x=0, y=0),
                   layer="Cut", stitch=StitchSettings(
                       pitch_mm=5.0, inset=4.0, punch_style="french",
                       hole_style="slit", symmetry="vertical"))
    item, _grips = _add(c, sh)
    win.properties.show_selection([item])
    assert win.properties._stitch_form.isRowVisible(
        win.properties.use_for_new)
    win.properties.use_for_new.click()
    new = c._default_stitch()
    assert (new.pitch_mm, new.inset, new.punch_style) == (5.0, 4.0, "french")
    assert new.symmetry == "none"            # one piece's own, not carried
    assert new is not c._default_stitch()    # every shape gets its own copy
    # and next time
    again = MainWindow(Document())
    st = again.canvas.new_stitch
    assert (st.pitch_mm, st.inset, st.hole_style) == (5.0, 4.0, "slit")
    again.hide()

"""Branded iron catalog + whole-project job estimate."""

import math

from leathercad import irons
from leathercad.document import Document
from leathercad.geometry import Vec2 as _V
from leathercad.holes import diamond_points
from leathercad.estimate import estimate_project, format_report
from leathercad.geometry import Vec2
from leathercad.holes import LooseHole
from leathercad.layers import Layer, SCORE, ENGRAVE
from leathercad.shapes import Rectangle, Transform
from leathercad.stitchsettings import StitchSettings


# -- iron catalog ------------------------------------------------------------
def test_spi_mm_roundtrip():
    assert math.isclose(irons.spi_to_mm(irons.mm_to_spi(3.85)), 3.85)
    assert math.isclose(irons.mm_to_spi(25.4), 1.0)


def test_standard_pitch_ladder_and_round_diameters():
    assert irons.STYLES == ("round", "oblique", "french", "diamond")
    # the common ladder is present and ordered
    for p in (2.7, 3.0, 3.38, 3.85, 4.0):
        assert p in irons.STANDARD_PITCHES
    assert irons.STANDARD_PITCHES == sorted(irons.STANDARD_PITCHES)
    # round-hole diameters exist and stay in the thread-friendly range
    assert 1.0 in irons.ROUND_DIAMETERS
    assert all(0.5 <= d <= 2.0 for d in irons.ROUND_DIAMETERS)


def test_geometry_for_maps_style_to_hole_shape():
    assert irons.geometry_for("round", 3.85)["hole_style"] == "round"
    assert irons.geometry_for("oblique", 3.85)["hole_style"] == "slit"
    assert irons.geometry_for("french", 3.85)["hole_style"] == "slit"
    assert irons.geometry_for("diamond", 3.85)["hole_style"] == "diamond"
    # oblique and french are both slits but at visibly different slants
    assert (irons.geometry_for("french", 3.85)["slit_angle"]
            != irons.geometry_for("oblique", 3.85)["slit_angle"])
    # slit length scales with pitch
    assert (irons.geometry_for("diamond", 4.0)["slit_length"]
            > irons.geometry_for("diamond", 3.0)["slit_length"])


def test_diamond_points_make_a_slim_rhombus():
    # horizontal tangent, no slant: long axis along X, short axis along Y
    pts = diamond_points(_V(0, 0), _V(1, 0), length=2.0, angle_deg=0.0,
                         width_ratio=0.4)
    assert len(pts) == 4
    length = math.hypot(pts[0].x - pts[2].x, pts[0].y - pts[2].y)
    width = math.hypot(pts[1].x - pts[3].x, pts[1].y - pts[3].y)
    assert abs(length - 2.0) < 1e-9
    assert abs(width - 0.8) < 1e-9          # 2.0 * 0.4
    assert width < length                    # slim


def test_diamond_exports_as_polygon_and_closed_polyline(tmp_path):
    from leathercad import export
    from leathercad.shapes import Rectangle, Transform
    doc = Document()
    doc.add_shape(Rectangle(width=80, height=50, transform=Transform(x=0, y=0),
                            layer="Cut",
                            stitch=StitchSettings(pitch_mm=4.0, inset=4.0,
                                                  punch_style="diamond",
                                                  hole_style="diamond")))
    svg = tmp_path / "d.svg"
    dxf = tmp_path / "d.dxf"
    export.export_svg(doc, str(svg))
    export.export_dxf(doc, str(dxf))
    assert "<polygon" in svg.read_text()     # diamonds, not circles or lines
    assert "POLYLINE" in dxf.read_text()


# -- job estimate ------------------------------------------------------------
def _stitched_rect(w=100.0, h=60.0, x=0.0, y=0.0, layer="Cut"):
    return Rectangle(width=w, height=h, transform=Transform(x=x, y=y),
                     layer=layer, stitch=StitchSettings(pitch_mm=4.0, inset=4.0))


def test_estimate_counts_pieces_holes_thread_and_cut():
    doc = Document()
    doc.add_shape(_stitched_rect())
    est = estimate_project(doc)
    assert est.pieces == 1
    assert est.holes > 0
    assert est.thread_mm > 0.0
    # perimeter of a 100x60 rect is 320 mm
    assert abs(est.cut_mm - 320.0) < 1.0
    assert est.parts_area_mm2 > 0.0


def test_score_and_engrave_go_to_their_own_buckets():
    doc = Document()
    doc.layers.append(Layer(name="Fold", role=SCORE))
    doc.layers.append(Layer(name="Logo", role=ENGRAVE))
    doc.add_shape(Rectangle(width=50, height=50, layer="Cut"))
    doc.add_shape(Rectangle(width=20, height=20, layer="Fold"))
    doc.add_shape(Rectangle(width=10, height=10, layer="Logo"))
    est = estimate_project(doc)
    assert est.pieces == 1                 # only the cut rect is a "piece"
    assert abs(est.cut_mm - 200.0) < 1.0
    assert abs(est.score_mm - 80.0) < 1.0
    assert abs(est.engrave_mm - 40.0) < 1.0


def test_loose_holes_count_and_expand_footprint():
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=40, layer="Cut"))
    doc.holes.append(LooseHole(point=Vec2(500.0, 0.0)))   # far away
    est = estimate_project(doc)
    assert est.holes == 1
    # footprint spans from the rect out to the lone hole
    assert est.footprint_mm2 > 40.0 * 40.0


def test_laser_time_and_cost_only_when_asked():
    doc = Document()
    doc.add_shape(_stitched_rect())
    base = estimate_project(doc)
    assert base.laser_seconds is None and base.cost == {}

    priced = estimate_project(doc, feed_mm_s=20.0, pierce_s=0.05,
                              price_per_sqft=8.0, price_thread_per_m=0.5,
                              price_laser_per_min=1.0)
    assert priced.laser_seconds is not None and priced.laser_seconds > 0.0
    assert set(priced.cost) == {"leather", "thread", "laser", "total"}
    assert abs(priced.cost["total"]
               - (priced.cost["leather"] + priced.cost["thread"]
                  + priced.cost["laser"])) < 1e-9


def test_waste_pct_between_zero_and_hundred():
    doc = Document()
    doc.add_shape(Rectangle(width=50, height=50, transform=Transform(x=0, y=0),
                            layer="Cut"))
    doc.add_shape(Rectangle(width=50, height=50, transform=Transform(x=200, y=0),
                            layer="Cut"))
    est = estimate_project(doc)
    # two 50x50 parts spread across a wide footprint -> real waste
    assert 0.0 < est.waste_pct < 100.0


def test_report_is_readable_and_guards_empty():
    assert "draw a cut piece" in format_report(estimate_project(Document()))
    doc = Document()
    doc.add_shape(_stitched_rect())
    text = format_report(estimate_project(doc))
    assert "Pieces" in text and "Thread needed" in text and "Cut length" in text


# -- GUI: punch cascade (style -> maker -> size) -----------------------------
def _win_with_stitched_rect():
    import os
    import pytest
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    QApplication.instance() or QApplication([])
    doc = Document()
    doc.add_shape(_stitched_rect())
    win = MainWindow(doc)
    win.canvas.rebuild()
    shp = [it for it in win.canvas.scene_obj.items()
           if isinstance(it, ShapeItem)][0]
    shp.setSelected(True)
    win.properties.show_selection([shp])
    return win, shp


def test_punch_style_sets_shape_and_toggles_diameter_vs_slit():
    win, shp = _win_with_stitched_rect()
    p = win.properties
    f = p._stitch_form

    # Round -> circle primitive, Hole ø dropdown shown, slit rows hidden
    p.punch_style.setCurrentIndex(p.punch_style.findData("round"))
    assert shp.model.stitch.hole_style == "round"
    assert f.isRowVisible(p.hole_dia_combo)
    assert not f.isRowVisible(p.slit_len)
    # the exact-mm ø box stays hidden while a preset diameter is selected
    assert not f.isRowVisible(p.hole_dia)

    # Diamond -> diamond primitive, slit rows shown, Hole ø hidden
    p.punch_style.setCurrentIndex(p.punch_style.findData("diamond"))
    assert shp.model.stitch.punch_style == "diamond"
    assert shp.model.stitch.hole_style == "diamond"
    assert f.isRowVisible(p.slit_len) and f.isRowVisible(p.slit_angle)
    assert not f.isRowVisible(p.hole_dia_combo)


def test_pitch_dropdown_sets_pitch_and_custom_shows():
    win, shp = _win_with_stitched_rect()
    p = win.properties
    # pick 4 mm from the pitch dropdown
    for k in range(p.pitch_combo.count()):
        if p.pitch_combo.itemData(k) and abs(p.pitch_combo.itemData(k) - 4.0) < 1e-6:
            p.pitch_combo.setCurrentIndex(k)
            break
    assert abs(shp.model.stitch.pitch_mm - 4.0) < 1e-6
    assert "SPI" in p.pitch_combo.currentText()      # mm + SPI shown
    f = p._stitch_form
    assert not f.isRowVisible(p.pitch)               # mm box hidden on a preset
    # picking "Custom…" reveals the exact-mm box; typing there sets the pitch
    p.pitch_combo.setCurrentIndex(p.pitch_combo.count() - 1)
    assert p.pitch_combo.currentText().startswith("Custom")
    assert f.isRowVisible(p.pitch)
    p.pitch.setValue(3.55)
    assert abs(shp.model.stitch.pitch_mm - 3.55) < 1e-6


def test_round_diameter_dropdown_sets_diameter():
    win, shp = _win_with_stitched_rect()
    p = win.properties
    p.punch_style.setCurrentIndex(p.punch_style.findData("round"))
    for k in range(p.hole_dia_combo.count()):
        if p.hole_dia_combo.itemData(k) and abs(p.hole_dia_combo.itemData(k) - 1.2) < 1e-6:
            p.hole_dia_combo.setCurrentIndex(k)
            break
    assert abs(shp.model.stitch.hole_diameter - 1.2) < 1e-6
    # picking "Custom…" reveals the exact-mm ø box; typing there sets it
    f = p._stitch_form
    p.hole_dia_combo.setCurrentIndex(p.hole_dia_combo.count() - 1)
    assert f.isRowVisible(p.hole_dia)
    p.hole_dia.setValue(0.9)
    assert abs(shp.model.stitch.hole_diameter - 0.9) < 1e-6


def _hole_fingerprint(item):
    """A cheap signature of an item's rendered holes: count + path bbox."""
    holes = getattr(item, "_holes", None)
    n = holes.count if holes else len(getattr(item, "_rel_holes", []) or [])
    path = getattr(item, "_holes_path", None)
    br = path.boundingRect() if path is not None else None
    box = (round(br.width(), 3), round(br.height(), 3)) if br else None
    return (n, box)


def test_every_stitching_control_recomputes_the_pattern():
    """Each Stitching-box control must recompute + redraw the holes on change --
    across an auto-spaced shape, its baked form, and a drawn seam."""
    win, shp = _win_with_stitched_rect()
    p = win.properties

    def changes(item, action):
        before = _hole_fingerprint(item)
        action()
        return _hole_fingerprint(item) != before

    # auto-spaced shape: geometry AND appearance controls both redraw
    assert changes(shp, lambda: _pick(p.pitch_combo, 3.0))
    assert changes(shp, lambda: p.inset.setValue(6.0))
    assert changes(shp, lambda: p.fit.setCurrentText("none"))
    assert changes(shp, lambda: p.punch_style.setCurrentText("Diamond"))
    assert changes(shp, lambda: p.slit_angle.setValue(12))
    assert changes(shp, lambda: p.slit_len.setValue(2.5))
    assert changes(shp, lambda: p.punch_style.setCurrentText("Round"))
    assert changes(shp, lambda: _pick(p.hole_dia_combo, 1.5))
    assert changes(shp, lambda: p.rows.setCurrentIndex(1))

    # a drawn seam recomputes on a pitch change
    from leathercad.stitchline import StitchLine
    from leathercad.geometry import Vec2
    from leathercad_app.items import StitchLineItem
    win.doc.add_stitch_line(StitchLine(points=[Vec2(0, 200), Vec2(90, 200)],
                                       settings=StitchSettings(pitch_mm=4.0)))
    win.canvas.rebuild()
    seam = [it for it in win.canvas.scene_obj.items()
            if isinstance(it, StitchLineItem)][0]
    seam.setSelected(True)
    p.show_selection([seam])
    assert changes(seam, lambda: _pick(p.pitch_combo, 2.7))


def _pick(combo, value):
    for k in range(combo.count()):
        if combo.itemData(k) is not None and abs(combo.itemData(k) - value) < 1e-6:
            combo.setCurrentIndex(k)
            return


def test_wheel_does_not_change_panel_fields():
    """Scrolling over a spin box or combo box must not nudge its value -- the
    event is ignored (not consumed) so the panel scroll area still scrolls."""
    win, shp = _win_with_stitched_rect()
    p = win.properties
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF, QPoint, Qt
    from PySide6.QtWidgets import QApplication

    def wheel(w):
        ev = QWheelEvent(QPointF(3, 3), QPointF(3, 3), QPoint(0, 0),
                         QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(w, ev)
        return ev.isAccepted()

    before = (p.pitch.value(), p.inset.value(), p.fit.currentIndex(),
              p.punch_style.currentIndex())
    for w in (p.pitch, p.inset, p.fit, p.punch_style, p.backstitch):
        assert not wheel(w)              # ignored -> bubbles to the scroll area
    after = (p.pitch.value(), p.inset.value(), p.fit.currentIndex(),
             p.punch_style.currentIndex())
    assert before == after               # nothing moved


def test_set_tool_always_refreshes_the_status_hint():
    import os
    import pytest
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    import leathercad_app.canvas as cm
    QApplication.instance() or QApplication([])
    win = MainWindow(Document())

    seen = []
    win.canvas.statusMessage.connect(seen.append)
    # a spread of tools that previously had NO hint -> stale status bar
    for mode in (cm.LINE, cm.RECT, cm.POLYGON, cm.SELECT, cm.PEN, cm.DIMENSION):
        win._set_tool(mode)
        assert seen and seen[-1] == win._tool_hint(mode) and win._tool_hint(mode)
    # switching tools actually changes the message (not left stale)
    win._set_tool(cm.LINE)
    win._set_tool(cm.SELECT)
    assert seen[-1] != win._tool_hint(cm.LINE)

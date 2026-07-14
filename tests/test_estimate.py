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


def test_catalog_is_style_then_maker_then_size():
    assert list(irons.styles()) == ["round", "oblique", "french", "diamond"]
    # Blanchard is a French maker; KS Blade offers diamond
    assert "Blanchard" in irons.brands_for("french")
    ks = irons.punches_for("diamond", "KS Blade Punch")
    assert any(abs(p.pitch_mm - 3.85) < 1e-9 for p in ks)
    assert all(p.style == "diamond" and p.brand == "KS Blade Punch" for p in ks)


def test_spi_makers_store_correct_pitch_and_label():
    # Weaver 6 SPI == 25.4/6 mm, labelled in SPI
    weaver = {round(p.spi): p for p in irons.punches_for("diamond",
                                                         "Weaver Leather")}
    assert math.isclose(weaver[6].pitch_mm, 25.4 / 6.0)
    assert weaver[6].size_label == "6 SPI"
    # mm makers label in mm
    ks = irons.punches_for("diamond", "KS Blade Punch")[0]
    assert ks.size_label.endswith("mm")


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


def test_nearest_labels_a_saved_pitch():
    hit = irons.nearest(3.86)          # a hair off 3.85
    assert hit is not None and abs(hit.pitch_mm - 3.85) < 0.03
    assert irons.nearest(3.55) is None  # nothing that close -> no false label


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


def test_punch_cascade_style_maker_size_sets_shape_and_pitch():
    win, shp = _win_with_stitched_rect()
    p = win.properties

    # pick Diamond -> hole primitive becomes diamond
    p.punch_style.setCurrentIndex(p.punch_style.findData("diamond"))
    assert shp.model.stitch.punch_style == "diamond"
    assert shp.model.stitch.hole_style == "diamond"
    assert "KS Blade Punch" in [p.punch_brand.itemText(i)
                                for i in range(p.punch_brand.count())]

    # switch maker to a maker offering this style, then pick a size
    i = p.punch_brand.findData("Wuta")
    if i >= 0:
        p.punch_brand.setCurrentIndex(i)
    # choose the 4 mm size if present
    for k in range(p.punch_size.count()):
        if p.punch_size.itemData(k) and abs(p.punch_size.itemData(k) - 4.0) < 1e-6:
            p.punch_size.setCurrentIndex(k)
            break
    assert abs(shp.model.stitch.pitch_mm - 4.0) < 1e-6

    # French maker labels sizes in SPI
    p.punch_style.setCurrentIndex(p.punch_style.findData("french"))
    assert shp.model.stitch.hole_style == "slit"
    assert "Blanchard" in [p.punch_brand.itemText(i)
                           for i in range(p.punch_brand.count())]


def test_saved_pitch_round_trips_and_custom_shows():
    win, shp = _win_with_stitched_rect()
    p = win.properties
    p.punch_style.setCurrentIndex(p.punch_style.findData("diamond"))
    # a 3.85 KS Blade size is a real catalogue entry, not Custom
    p.punch_brand.setCurrentIndex(p.punch_brand.findData("KS Blade Punch"))
    p._select_size_for_pitch(3.85)
    assert "3.85" in p.punch_size.currentText()
    # a pitch no maker offers falls back to Custom
    p._select_size_for_pitch(3.55)
    assert p.punch_size.currentText().startswith("Custom")

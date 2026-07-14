"""Branded iron catalog + whole-project job estimate."""

import math

from leathercad import irons
from leathercad.document import Document
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


def test_catalog_has_named_makers_with_branded_irons():
    b = irons.brands()
    assert "KS Blade Punch" in b and "Blanchard" in b
    ks = irons.irons_for("KS Blade Punch")
    assert any(abs(i.pitch_mm - 3.85) < 1e-9 for i in ks)
    assert all(i.brand == "KS Blade Punch" for i in ks)


def test_spi_makers_store_correct_pitch():
    # Weaver 6 SPI == 25.4/6 mm
    weaver = {round(i.spi): i for i in irons.irons_for("Weaver Leather")}
    assert math.isclose(weaver[6].pitch_mm, 25.4 / 6.0)


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


# -- GUI: branded iron dropdown drives the pitch -----------------------------
def test_iron_dropdown_is_brand_grouped_and_sets_pitch():
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
    p = win.properties
    p.show_selection([shp])

    labels = [p.iron.itemText(i) for i in range(p.iron.count())]
    assert any("KS Blade Punch" in t for t in labels)
    assert any("Blanchard" in t for t in labels)
    assert labels[-1].startswith("Custom")

    # a saved 3.85 mm pitch round-trips to a branded label, not "Custom"
    p._sync_iron_combo(3.85)
    assert "3.85 mm" in p.iron.currentText() and "·" in p.iron.currentText()

    # picking a catalogue entry writes its pitch onto the shape
    idx = next(i for i, t in enumerate(labels) if "4 mm" in t)
    p.iron.setCurrentIndex(idx)
    assert abs(shp.model.stitch.pitch_mm - 4.0) < 1e-6

"""Laser-kerf compensation on export: sizes come out drawn-size after the burn."""

import os

import pytest

from leathercad.document import Document
from leathercad.export import collect, export_svg, export_dxf
from leathercad.geometry import Vec2
from leathercad.shapes import Rectangle, Circle, PathShape, Transform
from leathercad.stitchsettings import StitchSettings


def _bbox(pts):
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def test_outer_outline_grows_by_half_kerf():
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    outlines, _ = collect(doc, kerf=0.3)
    minx, miny, maxx, maxy = _bbox(outlines[0][0])
    assert round(maxx - minx, 3) == 60.3           # +0.15 per side
    assert round(maxy - miny, 3) == 40.3


def test_nested_cutout_shrinks():
    doc = Document()
    doc.add_shape(Rectangle(width=100, height=60, transform=Transform(x=0, y=0),
                            layer="Cut"))
    doc.add_shape(Circle(rx=5, ry=5, transform=Transform(x=0, y=0),
                         layer="Cut"))             # hardware hole inside piece
    outlines, _ = collect(doc, kerf=0.3)
    rect_w = _bbox(outlines[0][0])[2] - _bbox(outlines[0][0])[0]
    circ = outlines[1][0]
    r = max((p.x ** 2 + p.y ** 2) ** 0.5 for p in circ)
    assert round(rect_w, 3) == 100.3               # outer grew
    assert abs(r - 4.85) < 0.02                    # cutout shrank by kerf/2


def test_open_and_score_paths_never_offset():
    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(0, 0), Vec2(30, 0)], close_path=False,
                            transform=Transform(x=0, y=0), layer="Cut"))
    doc.add_shape(Rectangle(width=20, height=10, transform=Transform(x=100, y=0),
                            layer="Score"))        # closed, but not a cut layer
    outlines, _ = collect(doc, kerf=0.4)
    a = _bbox(outlines[0][0])
    assert round(a[2] - a[0], 6) == 30.0           # open path untouched
    b = _bbox(outlines[1][0])
    assert round(b[2] - b[0], 6) == 20.0           # score layer untouched


def test_zero_kerf_is_identity():
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, layer="Cut"))
    plain, _ = collect(doc)
    zero, _ = collect(doc, kerf=0.0)
    assert [_bbox(o[0]) for o in plain] == [_bbox(o[0]) for o in zero]


def test_stitch_holes_shrink_in_svg_and_dxf(tmp_path):
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40,
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0,
                                                  hole_diameter=1.0),
                            layer="Cut"))
    doc.kerf = 0.2
    svg = tmp_path / "k.svg"
    export_svg(doc, str(svg))                      # kerf pulled from the doc
    assert "r='0.4'" in svg.read_text()            # 1.0mm hole cut at ø0.8
    dxf = tmp_path / "k.dxf"
    export_dxf(doc, str(dxf))
    assert "0.4000" in dxf.read_text()

    doc.kerf = 0.0                                 # off -> true size
    export_svg(doc, str(svg))
    assert "r='0.5'" in svg.read_text()


def test_kerf_roundtrips_with_document():
    doc = Document()
    doc.kerf = 0.25
    d2 = Document.from_dict(doc.to_dict())
    assert d2.kerf == 0.25
    assert Document.from_dict(Document().to_dict()).kerf == 0.0


def test_printing_is_never_kerf_compensated():
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, layer="Cut"))
    doc.kerf = 0.4
    # printing calls collect() without kerf -> true drawn geometry
    outlines, _ = collect(doc)
    b = _bbox(outlines[0][0])
    assert round(b[2] - b[0], 6) == 60.0


def test_kerf_spinbox_binds_to_document():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    QApplication.instance() or QApplication([])
    win = MainWindow(Document())
    assert win.kerf_spin.value() == 0.0
    win.kerf_spin.setValue(0.25)
    assert win.doc.kerf == 0.25
    assert win._unsaved_changes                    # a document edit
    # a fresh document resets the control
    win.new_document()
    assert win.kerf_spin.value() == 0.0

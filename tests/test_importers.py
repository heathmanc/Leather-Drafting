"""SVG + DXF import: geometry fidelity, units, Y-flip, and export roundtrips."""

import math
import os

import pytest

from leathercad.document import Document
from leathercad.geometry import Vec2
from leathercad.importers import import_svg, import_dxf, import_file
from leathercad.shapes import Rectangle, Circle, Polygon, PathShape, Transform
from leathercad import export


def _w(sh):
    b = sh.bounds()
    return b[2] - b[0], b[3] - b[1]


def test_svg_basic_shapes_scale_and_flip(tmp_path):
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='100mm' height='60mm'
                  viewBox='0 0 100 60'>
      <rect x='0' y='0' width='40' height='20'/>
      <circle cx='70' cy='50' r='6'/>
      <line x1='0' y1='55' x2='30' y2='55'/>
    </svg>"""
    p = tmp_path / "a.svg"
    p.write_text(svg)
    shapes = import_svg(str(p))
    assert len(shapes) == 3
    rect = shapes[0]
    assert isinstance(rect, Polygon) and rect.close_path
    assert tuple(round(v, 3) for v in _w(rect)) == (40.0, 20.0)   # 1 unit = 1 mm
    # SVG y-down flips: the rect (top of the SVG) ends up ABOVE the circle
    circ = shapes[1]
    assert rect.bounds()[1] > circ.bounds()[1]
    assert isinstance(shapes[2], PathShape)                       # open line


def test_svg_px_units_convert_to_mm(tmp_path):
    # 96 px wide document mapped to 96 px viewBox -> 1 px = 25.4/96 mm
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='96px' height='96px'
                  viewBox='0 0 96 96'><rect x='0' y='0' width='96' height='96'/></svg>"""
    p = tmp_path / "px.svg"
    p.write_text(svg)
    w, h = _w(import_svg(str(p))[0])
    assert abs(w - 25.4) < 1e-6 and abs(h - 25.4) < 1e-6


def test_svg_path_beziers_arcs_and_transforms(tmp_path):
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='100mm' viewBox='0 0 100 100'>
      <g transform='translate(10,10)'>
        <path d='M 0 0 c 10 0 10 20 0 20 l -20 0 z'/>
      </g>
      <path d='M 50 50 A 10 10 0 0 1 70 50'/>
    </svg>"""
    p = tmp_path / "b.svg"
    p.write_text(svg)
    shapes = import_svg(str(p))
    closed = [s for s in shapes if getattr(s, "close_path", False)]
    opened = [s for s in shapes if not getattr(s, "close_path", True)]
    assert len(closed) == 1 and len(opened) == 1
    assert len(closed[0].points) > 10             # the cubic was flattened
    aw, ah = _w(opened[0])
    assert abs(aw - 20.0) < 0.1                   # arc spans its endpoints
    assert 9.0 < ah <= 10.05                      # ...and bulges ~the radius


def test_svg_roundtrip_through_our_exporter(tmp_path):
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    svg = tmp_path / "rt.svg"
    export.export_svg(doc, str(svg))
    shapes = import_svg(str(svg))
    assert len(shapes) == 1
    w, h = _w(shapes[0])
    assert abs(w - 60) < 0.01 and abs(h - 40) < 0.01
    assert shapes[0].close_path


def test_dxf_roundtrip_through_our_exporter(tmp_path):
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    doc.add_shape(Circle(rx=5, ry=5, transform=Transform(x=100, y=0),
                         layer="Cut"))
    dxf = tmp_path / "rt.dxf"
    export.export_dxf(doc, str(dxf))
    shapes = import_dxf(str(dxf))
    assert len(shapes) == 2
    w, h = _w(shapes[0])
    assert abs(w - 60) < 0.01 and abs(h - 40) < 0.01
    assert shapes[0].close_path
    cw, ch = _w(shapes[1])
    assert abs(cw - 10) < 0.05 and abs(ch - 10) < 0.05      # the o5 circle


def test_dxf_entities_and_bulge(tmp_path):
    def pair(c, v):
        return f"{c}\n{v}\n"
    body = (pair(0, "SECTION") + pair(2, "ENTITIES")
            + pair(0, "LINE") + pair(10, 0) + pair(20, 0)
            + pair(11, 30) + pair(21, 0)
            + pair(0, "CIRCLE") + pair(10, 50) + pair(20, 0) + pair(40, 4)
            + pair(0, "ARC") + pair(10, 80) + pair(20, 0) + pair(40, 10)
            + pair(50, 0) + pair(51, 90)
            # semicircular bulge: 2-vertex LWPOLYLINE, bulge 1
            + pair(0, "LWPOLYLINE") + pair(90, 2) + pair(70, 0)
            + pair(10, 100) + pair(20, 0) + pair(42, 1)
            + pair(10, 120) + pair(20, 0)
            + pair(0, "ENDSEC") + pair(0, "EOF"))
    p = tmp_path / "e.dxf"
    p.write_text(body)
    shapes = import_dxf(str(p))
    kinds = sorted(type(s).__name__ for s in shapes)
    assert kinds == ["Circle", "PathShape", "PathShape", "PathShape"]
    bulge = [s for s in shapes if isinstance(s, PathShape)][-1]
    bw, bh = _w(bulge)
    assert abs(bw - 20) < 0.05 and abs(bh - 10) < 0.05      # true semicircle


def test_import_file_dispatch_and_reject(tmp_path):
    with pytest.raises(ValueError):
        import_file(str(tmp_path / "x.txt"))


def test_gui_import_adds_shapes(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    QApplication.instance() or QApplication([])

    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='50mm' viewBox='0 0 50 50'>
      <rect x='5' y='5' width='30' height='20'/></svg>"""
    p = tmp_path / "in.svg"
    p.write_text(svg)
    win = MainWindow(Document())
    assert win.import_path(str(p)) == 1
    assert len(win.doc.shapes) == 1
    assert win._unsaved_changes                    # import is an edit


def test_circle_elements_import_as_real_circles(tmp_path):
    """<circle> becomes a parametric Circle (5 snap nodes), not a dense
    polygon -- the fix for laggy dragging after importing stitched SVGs."""
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='100mm' viewBox='0 0 100 100'>
      <circle cx='20' cy='20' r='6'/>
      <g transform='rotate(30)'><ellipse cx='60' cy='20' rx='8' ry='4'/></g>
    </svg>"""
    p = tmp_path / "c.svg"
    p.write_text(svg)
    shapes = import_svg(str(p))
    kinds = sorted(type(s).__name__ for s in shapes)
    assert "Circle" in kinds                        # axis-aligned: parametric
    assert "Polygon" in kinds                       # rotated: safe fallback
    circ = [s for s in shapes if isinstance(s, Circle)][0]
    assert abs(circ.rx - 6.0) < 1e-6


def test_svg_stroke_colors_map_to_layers(tmp_path):
    svg = """<svg xmlns='http://www.w3.org/2000/svg' width='100mm' viewBox='0 0 100 100'>
      <rect x='0' y='0' width='40' height='20' stroke='#ff0000'/>
      <circle cx='60' cy='10' r='2' stroke='#0066FF'/>
      <g stroke='#00aa00'><line x1='0' y1='50' x2='30' y2='50'/></g>
      <path d='M 0 70 L 20 70' style='stroke:#888888'/>
    </svg>"""
    p = tmp_path / "cl.svg"
    p.write_text(svg)
    cl = {"#ff0000": "Cut", "#0066ff": "Stitch", "#00aa00": "Score",
          "#888888": "Engrave"}
    layers = [s.layer for s in import_svg(str(p), color_layers=cl)]
    assert layers == ["Cut", "Stitch", "Score", "Engrave"]


def test_dxf_aci_colors_map_to_layers(tmp_path):
    def pair(c, v):
        return f"{c}\n{v}\n"
    body = (pair(0, "SECTION") + pair(2, "ENTITIES")
            + pair(0, "CIRCLE") + pair(62, 5) + pair(10, 0) + pair(20, 0)
            + pair(40, 2)
            + pair(0, "LINE") + pair(62, 1) + pair(10, 10) + pair(20, 0)
            + pair(11, 40) + pair(21, 0)
            + pair(0, "ENDSEC") + pair(0, "EOF"))
    p = tmp_path / "aci.dxf"
    p.write_text(body)
    cl = {"#0066ff": "Stitch", "#ff0000": "Cut"}
    shapes = import_dxf(str(p), color_layers=cl)
    assert sorted(s.layer for s in shapes) == ["Cut", "Stitch"]


def test_stitched_export_reimports_as_holes(tmp_path):
    """The golden roundtrip: export a stitched piece to SVG, import it back --
    the outline returns as a shape and every blue hole circle returns as a
    real LooseHole (classified by colour), not a dense red polygon."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad.stitchsettings import StitchSettings
    QApplication.instance() or QApplication([])

    src = Document()
    src.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    svg = tmp_path / "stitched.svg"
    export.export_svg(src, str(svg))

    win = MainWindow(Document())
    n = win.import_path(str(svg))
    assert n > 10
    assert len(win.doc.shapes) == 1                 # just the outline
    assert len(win.doc.holes) > 20                  # every hole classified
    assert all(h.layer == "Stitch" for h in win.doc.holes)
    assert abs(win.doc.holes[0].hole_diameter - 1.0) < 0.05


def test_convert_circles_to_holes_command():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(Circle(rx=0.6, ry=0.6, transform=Transform(x=5, y=5),
                         layer="Cut"))
    doc.add_shape(Rectangle(width=30, height=20, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)
    c.convert_circles_to_holes()                    # circles only; rect stays
    assert len(win.doc.shapes) == 1
    assert len(win.doc.holes) == 1
    h = win.doc.holes[0]
    assert (round(h.point.x, 3), round(h.point.y, 3)) == (5.0, 5.0)
    assert abs(h.hole_diameter - 1.2) < 1e-6

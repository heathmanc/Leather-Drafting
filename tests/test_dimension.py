"""Measure tool + dimension annotations."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.geometry import Vec2
from leathercad.dimension import Dimension
from leathercad.document import Document


def test_dimension_length_and_roundtrip():
    d = Dimension(p1=Vec2(0, 0), p2=Vec2(40, 30))
    assert round(d.length(), 1) == 50.0
    assert d.label() == "50.0 mm"
    doc = Document()
    doc.dimensions.append(d)
    doc2 = Document.from_dict(doc.to_dict())
    assert len(doc2.dimensions) == 1
    assert doc2.dimensions[0].label() == "50.0 mm"


def test_dimension_not_exported():
    # dimensions live outside shapes/holes/stitch_lines, so export.collect skips
    from leathercad import export
    doc = Document()
    doc.dimensions.append(Dimension(p1=Vec2(0, 0), p2=Vec2(10, 0)))
    outlines, stitches = export.collect(doc)
    assert not outlines and not stitches


def test_measure_readout(qapp=None):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import leathercad_app.canvas as cm
    from PySide6.QtCore import QPointF
    c = cm.Canvas(Document())
    msg = c._measure_text(QPointF(0, 0), QPointF(30, 40))
    assert "length 50.00 mm" in msg and "dx 30.00" in msg


def test_properties_panel_handles_annotation_selection():
    # Regression: selecting a Dimension or Text must not crash the Properties
    # panel (it used to assume any non-shape/hole item was a seam with .line).
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    from leathercad.dimension import Dimension
    from leathercad.text import TextShape
    from leathercad.shapes import Transform
    from leathercad.geometry import Vec2
    from leathercad_app.items import bake_text_contours, DimensionItem, TextItem

    doc = Document()
    doc.dimensions.append(Dimension(p1=Vec2(0, 0), p2=Vec2(40, 30)))
    doc.texts.append(TextShape(
        text="Hi",
        contours=[[Vec2(p.x, p.y) for p in c] for c in bake_text_contours("Hi", "Sans", 8)],
        transform=Transform(x=5, y=5), layer="Engrave"))
    win = MainWindow(doc)
    win.canvas.rebuild()
    dim = next(it for it in win.canvas.scene_obj.items() if isinstance(it, DimensionItem))
    txt = next(it for it in win.canvas.scene_obj.items() if isinstance(it, TextItem))
    dim.setSelected(True)
    win._selection_changed()
    assert "Dimension" in win.properties.readout.text()
    win.canvas.scene_obj.clearSelection()
    txt.setSelected(True)
    win._selection_changed()
    assert "Text" in win.properties.readout.text()
    win.properties._apply()   # must be a no-op, not a crash


def test_dimension_tracks_shape_resize():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QPointF
    QApplication.instance() or QApplication([])
    import leathercad_app.canvas as cm
    from leathercad_app.items import ShapeItem, ResizeHandle
    from leathercad.shapes import Rectangle, Transform
    from leathercad.geometry import Vec2

    doc = Document()
    rect = Rectangle(width=40, height=30, transform=Transform(x=50, y=50),
                     layer="Cut")
    doc.add_shape(rect)
    c = cm.Canvas(doc)
    c.snap_to_nodes = True
    c.snap_to_grid = False
    c.rebuild()
    # dimension across the bottom edge (BL 30,35 -> BR 70,35)
    c._add_dimension(QPointF(30, 35), QPointF(70, 35))
    dm = doc.dimensions[0]
    assert dm.a_ref is not None and dm.b_ref is not None
    assert dm.label() == "40.0 mm"
    # resize to 60 wide with the bottom-left corner pinned
    item = next(it for it in c.scene_obj.items() if isinstance(it, ShapeItem))
    item.setSelected(True)
    c.selection_changed()
    tr = next(h for h in c.scene_obj.items()
              if isinstance(h, ResizeHandle) and h.grip == (1, 1))
    tr._apply_resize(Vec2(90, 85))
    c.update_dimensions()
    assert dm.label() == "60.0 mm"        # length followed the resize


def test_line_width_control():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import leathercad_app.canvas as cm
    c = cm.Canvas(Document())
    assert c.outline_width(False) == 1.0
    c.set_line_width(3.0)
    assert c.line_width == 3.0
    assert c.outline_width(False) == 3.0 and c.outline_width(True) == 3.8

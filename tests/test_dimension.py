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

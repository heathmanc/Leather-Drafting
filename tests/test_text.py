"""Text / lettering feature."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.geometry import Vec2
from leathercad.text import TextShape
from leathercad.shapes import Transform
from leathercad.document import Document


def _bake(text, size=8.0):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app.items import bake_text_contours
    return bake_text_contours(text, "Sans", size)


def test_text_bakes_to_contours_at_size():
    contours = _bake("AB", 8.0)
    assert len(contours) >= 3            # A (2) + B (3) counters
    ys = [p.y for c in contours for p in c]
    assert abs((max(ys) - min(ys)) - 8.0) < 1.5   # ~cap height mm


def test_text_exports_as_engrave_paths():
    from leathercad import export
    contours = _bake("A", 8.0)
    doc = Document()
    doc.texts.append(TextShape(text="A",
                               contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                               transform=Transform(x=0, y=0), layer="Engrave"))
    outlines, _stitches = export.collect(doc)
    assert len(outlines) == len(contours)   # each contour is an engrave polyline


def test_text_survives_save_load():
    contours = _bake("Hi", 6.0)
    doc = Document()
    doc.texts.append(TextShape(text="Hi",
                               contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                               size=6.0, transform=Transform(x=5, y=5),
                               layer="Engrave"))
    doc2 = Document.from_dict(doc.to_dict())
    assert len(doc2.texts) == 1
    assert doc2.texts[0].text == "Hi"
    assert len(doc2.texts[0].contours) == len(contours)

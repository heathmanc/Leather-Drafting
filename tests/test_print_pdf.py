"""The 1:1 PDF: pieces nested onto as few pages as they'll go, at least a
gap apart; a header and footer on every page; a details page first."""

import os
import re

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Circle, PathShape, Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _tote():
    """Pieces spread out the way a drawing is laid out, as a pattern is."""
    def st():
        return StitchSettings(pitch_mm=3.85, inset=3.5)
    doc = Document()

    def add(sh, name):
        sh.name = name
        doc.add_shape(sh)
        return sh
    add(Rectangle(width=320, height=380, corner_radius=12, layer="Cut",
                  transform=Transform(x=0, y=0), stitch=st()), "Front panel")
    add(Rectangle(width=600, height=28, layer="Cut",
                  transform=Transform(x=0, y=-400), stitch=st()), "Strap")
    add(Rectangle(width=90, height=700, layer="Cut",
                  transform=Transform(x=900, y=0), stitch=st()), "Gusset")
    add(Rectangle(width=180, height=140, corner_radius=8, layer="Cut",
                  transform=Transform(x=1200, y=300), stitch=st()), "Pocket")
    add(Circle(rx=20, ry=20, layer="Cut",
               transform=Transform(x=1200, y=-300)), "Rivet cap")
    for i in range(4):
        add(Rectangle(width=60, height=30, corner_radius=5, layer="Cut",
                      transform=Transform(x=1400 + 120 * i, y=-300),
                      stitch=st()), "Strap tab")
    # a fold line on the pocket, drawn a little past both its edges
    add(PathShape(points=[Vec2(-95, 0), Vec2(95, 0)], close_path=False,
                  layer="Score", transform=Transform(x=1200, y=300)), "")
    return doc


def _pages(path):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", open(path, "rb").read()))


def _text(path):
    from PySide6.QtPdf import QPdfDocument
    d = QPdfDocument(None)
    d.load(path)
    return [d.getAllText(i).text() for i in range(d.pageCount())]


def test_nested_takes_far_fewer_pages_than_as_drawn(qapp, tmp_path):
    from leathercad_app.printing import PrintOptions, export_pdf
    doc = _tote()
    nested = export_pdf(doc, str(tmp_path / "n.pdf"), PrintOptions())
    drawn = export_pdf(doc, str(tmp_path / "d.pdf"),
                       PrintOptions(nested=False))
    assert nested.pattern_pages < drawn.pattern_pages / 2
    assert nested.drawn_pages == drawn.pattern_pages
    assert _pages(str(tmp_path / "n.pdf")) == len(nested.pages)
    # every piece is on some page, nothing left out
    assert not nested.unplaced
    assert all(p.pages for p in nested.parts)


def test_every_piece_on_a_page_is_whole_and_8mm_from_the_next(qapp):
    from leathercad_app.printing import (LINE_SLACK, PrintOptions,
                                         _page_rect, plan)
    p = plan(_tote(), PrintOptions())
    o = p.opts
    for sheet in p.sheets:
        if sheet.cols * sheet.rows != 1:
            continue
        outs = []
        for pl in sheet.placements:
            part = p.parts[pl.key]
            pts = [pl.apply(q) for pts, _c, _cl in part.outlines for q in pts]
            r = _page_rect(sheet, o, 0, 0)
            assert min(q.x for q in pts) >= r[0] and max(q.x for q in pts) <= r[2]
            assert min(q.y for q in pts) >= r[1] and max(q.y for q in pts) <= r[3]
            outs.append(pts)
        for i in range(len(outs)):
            for j in range(i + 1, len(outs)):
                d = min((a - b).length() for a in outs[i] for b in outs[j])
                assert d >= 8.0 + LINE_SLACK - 0.5  # vertex to vertex: no less


def test_the_fold_line_stays_with_its_pocket(qapp):
    from leathercad_app.printing import PrintOptions, plan
    p = plan(_tote(), PrintOptions())
    names = [part.name for part in p.parts]
    assert names.count("Pocket") == 1
    assert not any(n.startswith("Piece ") for n in names)


def test_header_footer_on_every_page_and_a_details_page_first(qapp, tmp_path):
    from leathercad_app.printing import PrintOptions, export_pdf
    o = PrintOptions(title="Weekender Tote", designer="Hollow Oak Leather",
                     note="(c) 2026 Hollow Oak Leather - personal use only")
    path = str(tmp_path / "tote.pdf")
    plan = export_pdf(_tote(), path, o)
    pages = _text(path)
    assert len(pages) == len(plan.pages)
    for n, t in enumerate(pages, start=1):
        assert "Weekender Tote" in t
        assert "Hollow Oak Leather" in t
        assert "personal use only" in t
        assert f"Page {n} of {len(pages)}" in t
    first = pages[0]
    for words in ("Pattern details", "Pieces to cut", "Stitch holes",
                  "Largest piece", "Leather in the pieces", "Leather to buy",
                  "Fits on a piece of", "3.85 mm spacing", "Cutting layout",
                  "50 mm", "1 in", "Front panel", "Gusset"):
        assert words in first, words
    assert "Thread" not in first                 # left out, as asked
    holes = sum(part.holes for part in plan.parts)
    assert f"Stitch holes {holes}" in first.replace("\r\n", " ").replace("\n", " ")
    # the pattern pages carry the scale check and the pieces' names
    rest = " ".join(pages[1:])
    assert "this box is 190" in rest
    for name in ("Front panel", "Strap", "Gusset", "Pocket", "Rivet cap",
                 "Strap tab"):
        assert name in rest, name
    # tiled pages say where they go, and name their neighbours
    assert "row 1 of" in rest and "goes here" in rest


def test_no_details_page_and_no_designer_when_not_asked(qapp, tmp_path):
    from leathercad_app.printing import PrintOptions, export_pdf
    path = str(tmp_path / "plain.pdf")
    plan = export_pdf(_tote(), path, PrintOptions(details=False))
    pages = _text(path)
    assert plan.pages[0].kind == "pattern"
    assert "Pattern details" not in pages[0]
    assert "Pattern" in pages[0] and "Page 1 of" in pages[0]


def test_the_old_tiled_export_still_works(qapp, tmp_path):
    from leathercad_app.printing import export_pdf_tiled
    doc = Document()
    doc.add_shape(Rectangle(width=400, height=300, layer="Cut",
                            transform=Transform(x=0, y=0)))
    rows, cols = export_pdf_tiled(doc, str(tmp_path / "big.pdf"))
    assert rows >= 2 and cols >= 2


def test_naming_a_piece_in_properties_labels_it_in_the_pdf(qapp, tmp_path):
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.printing import PrintOptions, export_pdf
    doc = Document()
    sh = Rectangle(width=80, height=50, layer="Cut", transform=Transform(0, 0))
    doc.add_shape(sh)
    win = MainWindow(doc)
    win.show()
    qapp.processEvents()
    try:
        c = win.canvas
        it = next(i for i in c.scene_obj.items()
                  if getattr(i, "model", None) is sh)
        it.setSelected(True)
        qapp.processEvents()
        panel = win.properties
        steps = len(win.history._stack)
        panel.piece_name.setText("Card slot")
        panel.piece_name.editingFinished.emit()
        assert sh.name == "Card slot"
        assert len(win.history._stack) == steps + 1          # one undo step
        panel.piece_name.editingFinished.emit()              # no change: none
        assert len(win.history._stack) == steps + 1
        path = str(tmp_path / "named.pdf")
        export_pdf(doc, path, PrintOptions())
        assert "Card slot" in " ".join(_text(path))
    finally:
        win.hide()

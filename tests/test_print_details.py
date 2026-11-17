"""The PDF's details page tells a maker what the pattern takes: every piece
to cut, the irons it's punched with, and how the pieces lie on one piece of
leather.

The reports: "it has the number of pieces to cut wrong, for duplicate
pieces, it looks like it only counts them once" -- a lining drawn on its
outer, or a copy left on top of the original, was taken for a hole cut out
of it: not counted, and printed inside it. "I only used 5mm punching, yet it
gives me details for 3.85 also" -- holes attached to a piece kept a default
3.85 mm in its settings, and the seam tool always drew at 3.85 mm. "Leave
the thread requirements out." And: "put a snapshot of the layout on a
single piece of material, like the program does on screen"."""

import os
import re

import pytest

from leathercad.document import Document
from leathercad.estimate import estimate_project
from leathercad.geometry import Vec2
from leathercad.holes import LooseHole
from leathercad.irons import measured_pitch, nearest_pitch
from leathercad.pieces import classify
from leathercad.shapes import Circle, Rectangle, Transform
from leathercad.stitching import Hole
from leathercad.stitchsettings import StitchSettings


def _st(pitch=5.0, **kw):
    return StitchSettings(pitch_mm=pitch, inset=3.5, **kw)


def _add(doc, sh, name=""):
    sh.name = name
    doc.add_shape(sh)
    return sh


def _wallet():
    """A card wallet as a designer might draw it: the lining laid on the
    body, two copies of the card pocket and of the gusset, a strap whose
    holes were placed by hand at 5 mm and attached (its settings left at
    the default 3.85 mm), and a thumb hole cut in the body."""
    doc = Document()
    _add(doc, Rectangle(width=220, height=110, corner_radius=8, layer="Cut",
                        transform=Transform(0, 0), stitch=_st()), "Body")
    _add(doc, Rectangle(width=220, height=110, corner_radius=8, layer="Cut",
                        transform=Transform(0, 0)), "Lining")
    for i in range(2):
        _add(doc, Rectangle(width=100, height=70, corner_radius=5,
                            layer="Cut", transform=Transform(300 + 130 * i, 0),
                            stitch=_st()), "Card pocket")
        _add(doc, Rectangle(width=30, height=110, layer="Cut",
                            transform=Transform(600 + 50 * i, 0),
                            stitch=_st()), "Gusset")
    strap = _add(doc, Rectangle(width=200, height=25, layer="Cut",
                                transform=Transform(0, 200),
                                stitch=StitchSettings(enabled=False)), "Strap")
    strap.baked_holes = [Hole(Vec2(-95 + 5 * k, 0), Vec2(1, 0))
                         for k in range(39)]
    _add(doc, Circle(rx=8, ry=8, layer="Cut", transform=Transform(-80, 20)))
    return doc


# -- what's a piece --------------------------------------------------------------
def _ring(x, y, w, h):
    return [Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h), Vec2(x, y + h)]


def test_a_piece_drawn_on_another_is_a_piece_and_a_slot_is_not():
    rings = [_ring(0, 0, 200, 100),          # a panel
             _ring(0, 0, 200, 100),          # a copy laid exactly on it
             _ring(2, 2, 196, 96),           # a lining a little inside it
             _ring(20, 40, 30, 6),           # a slot in the panel
             _ring(300, 0, 50, 50),          # a piece on its own
             _ring(310, 10, 30, 30),         # a window in it...
             _ring(315, 15, 20, 20)]         # ...and what drops out of it
    piece, _parent, _areas = classify(rings)
    assert piece == [True, True, True, False, True, False, True]


def test_a_ring_keeps_its_hole():
    import math

    def circle(r, cx=0.0):
        return [Vec2(cx + r * math.cos(2 * math.pi * k / 72),
                     r * math.sin(2 * math.pi * k / 72)) for k in range(72)]
    # a bangle (90 mm, a 12 mm band) and a thin ring (70 mm, a 3 mm band):
    # their middles are holes. A lining 3 mm in from a 100 x 70 card piece
    # is a piece.
    rings = [circle(45), circle(33), circle(35, 200), circle(32, 200),
             _ring(400, 0, 100, 70), _ring(403, 3, 94, 64)]
    piece, _parent, _areas = classify(rings)
    assert piece == [True, False, True, False, True, True]


def test_the_estimate_counts_copies_and_linings():
    doc = _wallet()
    est = estimate_project(doc)
    assert est.pieces == 7            # body, lining, 2 pockets, 2 gussets, strap
    import math
    body = 220 * 110 - (4 - math.pi) * 8 * 8
    pocket = 100 * 70 - (4 - math.pi) * 5 * 5
    thumb = math.pi * 8 * 8
    # the lining is leather too; the thumb hole isn't
    want = 2 * body + 2 * pocket + 2 * 30 * 110 + 200 * 25 - thumb
    assert est.parts_area_mm2 == pytest.approx(want, rel=0.005)


def test_a_triangle_is_a_piece():
    from leathercad.shapes import Polygon
    doc = Document()
    doc.add_shape(Polygon(points=[Vec2(0, 0), Vec2(60, 0), Vec2(0, 40)],
                          layer="Cut"))
    assert estimate_project(doc).pieces == 1


# -- the irons ----------------------------------------------------------------
def test_the_spacing_is_measured_along_the_row():
    row = [Hole(Vec2(5.0 * k, 0), Vec2(1, 0)) for k in range(20)]
    assert measured_pitch(row) == pytest.approx(5.0)
    # a second row 3 mm across is another row, not a 3 mm spacing
    two = row + [Hole(Vec2(5.0 * k, 3.0), Vec2(1, 0)) for k in range(20)]
    assert measured_pitch(two) == pytest.approx(5.0)
    # round a curve, too
    import math
    arc = []
    for k in range(30):
        a = k * 3.85 / 40.0
        arc.append(Hole(Vec2(40 * math.cos(a), 40 * math.sin(a)),
                        Vec2(-math.sin(a), math.cos(a))))
    assert measured_pitch(arc) == pytest.approx(3.85, abs=0.01)
    assert measured_pitch(row[:1]) is None
    assert nearest_pitch(4.93) == 5.0 and nearest_pitch(3.83) == 3.85
    assert nearest_pitch(6.3) == 6.3


def test_the_stitching_names_only_the_irons_punched(qapp):
    from leathercad_app.printing import PrintOptions, _stitching_lines, plan
    p = plan(_wallet(), PrintOptions())
    lines = _stitching_lines(p.parts)
    assert len(lines) == 1 and lines[0].startswith("5 mm spacing"), lines
    total = sum(part.holes for part in p.parts)
    assert lines[0].endswith(f"— {total} holes")


def test_loose_holes_are_measured_too(qapp):
    from leathercad_app.printing import PrintOptions, _stitching_lines, plan
    doc = Document()
    _add(doc, Rectangle(width=120, height=40, layer="Cut",
                        transform=Transform(0, 0)), "Tab")
    for k in range(20):
        doc.add_hole(LooseHole(point=Vec2(-50 + 4.0 * k, 0)))
    lines = _stitching_lines(plan(doc, PrintOptions()).parts)
    assert lines == ["4 mm spacing (0.157 in), Ø1 mm round holes — 20 holes"]


def test_a_seam_really_at_3_85_is_still_named(qapp):
    from leathercad.stitchline import StitchLine
    from leathercad_app.printing import PrintOptions, _stitching_lines, plan
    doc = _wallet()
    doc.add_stitch_line(StitchLine(points=[Vec2(-100, -60), Vec2(100, -60)],
                                   settings=StitchSettings(pitch_mm=3.85,
                                                           inset=0.0)))
    lines = _stitching_lines(plan(doc, PrintOptions()).parts)
    assert [ln.split(" mm spacing")[0] for ln in lines] == ["5", "3.85"]


# -- in the app ------------------------------------------------------------------
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _window(qapp, doc):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    return win


def test_a_seam_is_drawn_with_the_spacing_chosen_for_new_shapes(qapp):
    from PySide6.QtCore import QPointF
    from leathercad_app import canvas as cm
    win = _window(qapp, Document())
    try:
        c = win.canvas
        c.new_stitch = StitchSettings(pitch_mm=5.0, inset=3.5,
                                      hole_diameter=1.2)
        c.tool = cm.STITCHLINE
        c._poly_pts = [QPointF(0, 0), QPointF(60, 0)]
        c._finalize_poly()
        (sl,) = win.doc.stitch_lines
        assert sl.settings.pitch_mm == 5.0
        assert sl.settings.hole_diameter == 1.2
        assert sl.settings.inset == 0.0 and sl.settings.enabled
        assert c.new_stitch.inset == 3.5          # its own copy
    finally:
        win.hide()


def test_attached_holes_take_the_spacing_they_were_punched_at(qapp):
    doc = Document()
    strap = _add(doc, Rectangle(width=200, height=25, layer="Cut",
                                transform=Transform(0, 0)), "Strap")
    for k in range(30):
        doc.add_hole(LooseHole(point=Vec2(-72.5 + 5.0 * k, 0)))
    win = _window(qapp, doc)
    try:
        c = win.canvas
        for it in c.scene_obj.items():
            if getattr(it, "model", None) is strap or hasattr(it, "hole"):
                it.setSelected(True)
        qapp.processEvents()
        c.group_selected()
        assert len(strap.baked_holes) == 30
        assert strap.stitch.pitch_mm == 5.0       # not the default 3.85
    finally:
        win.hide()


def _pdf_text(path):
    from PySide6.QtPdf import QPdfDocument
    d = QPdfDocument(None)
    d.load(path)
    return [d.getAllText(i).text() for i in range(d.pageCount())]


def test_the_details_page(qapp, tmp_path):
    from leathercad_app.printing import PrintOptions, export_pdf
    path = str(tmp_path / "wallet.pdf")
    p = export_pdf(_wallet(), path, PrintOptions(title="Card Wallet"))
    first = _pdf_text(path)[0].replace("\r\n", "\n")
    flat = first.replace("\n", " ")
    assert "Pieces to cut 7" in flat
    assert "Thread" not in first and "thread" not in first
    assert "3.85" not in first
    assert "Stitching 5 mm spacing" in flat
    # copies on one row, with how many to cut
    rows = {m.group(2): int(m.group(3)) for m in re.finditer(
        r"^(\d+) (Body|Lining|Card pocket|Gusset|Strap) (\d+) ", first, re.M)}
    assert rows == {"Body": 1, "Lining": 1, "Card pocket": 2, "Gusset": 2,
                    "Strap": 1}
    # the lining is printed as a piece of its own, not inside the body
    names = [part.name for part in p.parts]
    assert names.count("Lining") == 1 and names.count("Body") == 1
    assert "Cutting layout" in first


def test_the_cutting_layout_holds_every_piece_on_one_piece(qapp):
    """The page lays the pieces out on the best fit it finds: the gap
    between pieces, the edge margin chosen all round."""
    from leathercad_app.printing import (LINE_SLACK, PrintOptions,
                                         _cutting_layout, plan)
    p = plan(_wallet(), PrintOptions(edge_margin=5.0, spacing=8.0,
                                     layout_budget=1.0))
    layout = _cutting_layout(p, _wallet())
    assert layout is not None and not layout.drawn
    pieces = [i for i, part in enumerate(p.parts)
              if any(cl for _p, _c, cl in part.outlines)]
    assert sorted(pl.key for pl in layout.placements) == pieces
    assert layout.on == layout.of == len(pieces)
    _x, _y, w, h = layout.sheet
    assert max(w, h) / min(w, h) <= 2.0       # a piece of leather, not a strip
    outs = []
    for pl in layout.placements:
        pts = [pl.apply(q) for pts, _c, _cl in p.parts[pl.key].outlines
               for q in pts]
        assert min(q.x for q in pts) >= 5.0 - 1e-6     # the edge margin
        assert min(q.y for q in pts) >= 5.0 - 1e-6
        assert max(q.x for q in pts) <= w - 5.0 + 1e-6
        assert max(q.y for q in pts) <= h - 5.0 + 1e-6
        outs.append(pts)
    for i in range(len(outs)):
        for j in range(i + 1, len(outs)):
            d = min((a - b).length() for a in outs[i] for b in outs[j])
            assert d >= 8.0 + LINE_SLACK - 0.5


def test_the_best_fit_keeps_the_edge_margin_chosen(qapp):
    """The reports: "why does the pdf export not look like the same nesting
    as in the app, nor is it the same size? ... and has no edge clearance",
    then "why wouldn't we try for the best fit for the pdf? with an edge
    margin selectable on the options page before export?" -- the page's
    layout was a quick one with no edge margin at all."""
    from leathercad_app.printing import PrintOptions, _cutting_layout, plan
    for margin in (0.0, 5.0, 12.0):
        p = plan(_wallet(), PrintOptions(edge_margin=margin,
                                         layout_budget=0.5))
        layout = _cutting_layout(p, _wallet())
        _x, _y, w, h = layout.sheet
        for pl in layout.placements:
            b = p.parts[pl.key].bbox()
            lo, hi = pl.apply(Vec2(b[0], b[1])), pl.apply(Vec2(b[2], b[3]))
            xs, ys = sorted((lo.x, hi.x)), sorted((lo.y, hi.y))
            assert xs[0] >= margin - 1e-6 and ys[0] >= margin - 1e-6
            assert xs[1] <= w - margin + 1e-6 and ys[1] <= h - margin + 1e-6


def test_the_cutting_layout_can_be_the_one_laid_out_on_the_drawing(
        qapp, tmp_path):
    """Chosen on the options page, the page shows the sheet Nest on sheet
    drew, just as it is: the same layout, size and edge margin."""
    from leathercad.nesting import drawn_sheet
    from leathercad_app.printing import (PrintOptions, _cutting_layout,
                                         export_pdf, plan)
    win = _window(qapp, _wallet())
    try:
        win.canvas.nest_selected(600, 450, margin=5, spacing=8,
                                 smallest=True, budget=0)
        doc = win.doc
        sheet = drawn_sheet(doc)
        x0, y0, x1, y1 = sheet.bounds()
        p = plan(doc, PrintOptions(cut_layout="drawn"))
        layout = _cutting_layout(p, doc)
        assert layout.drawn and layout.on == layout.of == 7
        assert layout.sheet[2:] == (pytest.approx(x1 - x0),
                                    pytest.approx(y1 - y0))
        for pl in layout.placements:           # just where they lie
            b = p.parts[pl.key].bbox()
            moved = pl.apply(Vec2(b[0], b[1]))
            assert (moved.x, moved.y) == (pytest.approx(b[0] - x0),
                                          pytest.approx(b[1] - y0))
            assert b[0] - x0 >= 5.0 - 1e-6 and b[1] - y0 >= 5.0 - 1e-6
        # the best fit otherwise: the drawing's, only when no bigger
        best = _cutting_layout(plan(doc, PrintOptions(layout_budget=0.5)), doc)
        assert best.best
        if best.drawn:
            assert best.sheet == layout.sheet
        path = str(tmp_path / "nested.pdf")
        export_pdf(doc, path, PrintOptions(cut_layout="drawn"))
        first = _pdf_text(path)[0].replace("\r\n", " ").replace("\n", " ")
        size = f"{x1 - x0:.0f} × {y1 - y0:.0f} mm"
        assert f"Fits on a piece of {size}" in first.replace("\ue09f", "×")
        assert "as laid out with Nest on sheet" in first
    finally:
        win.hide()


def _on_a_sheet(x, y, w, h, pieces):
    """A drawing with a sheet laid out by hand: pieces (w, h, cx, cy) on a
    "Sheet" rectangle at (x, y)."""
    doc = Document()
    sheet = Rectangle(width=w, height=h, layer="Cut",
                      transform=Transform(x + w / 2.0, y + h / 2.0))
    sheet.name, sheet.construction = "Sheet", True
    doc.add_shape(sheet)
    for i, (pw, ph, cx, cy) in enumerate(pieces):
        _add(doc, Rectangle(width=pw, height=ph, layer="Cut",
                            transform=Transform(cx, cy)), f"Piece {i + 1}")
    return doc


def test_a_layout_on_the_drawing_as_good_as_any_is_the_best_fit(qapp):
    from leathercad_app.printing import PrintOptions, _cutting_layout, plan
    # one 100 x 50 piece 5 mm in from every edge: nothing beats it
    doc = _on_a_sheet(1000, 1000, 110, 60, [(100, 50, 1055, 1030)])
    layout = _cutting_layout(plan(doc, PrintOptions(edge_margin=5.0,
                                                    layout_budget=0.5)), doc)
    assert layout.best and layout.drawn
    assert layout.sheet[2:] == (pytest.approx(110), pytest.approx(60))
    # asked for more clearance than it has, the page finds its own
    roomy = _cutting_layout(plan(doc, PrintOptions(edge_margin=8.0,
                                                   layout_budget=0.5)), doc)
    assert roomy.best and not roomy.drawn
    assert sorted(roomy.sheet[2:]) == [pytest.approx(66), pytest.approx(116)]


def test_a_layout_on_the_drawing_too_tight_is_passed_over(qapp):
    from leathercad_app.printing import PrintOptions, _cutting_layout, plan
    # two pieces 3 mm apart: not the 8 mm asked for
    doc = _on_a_sheet(0, 0, 213, 60, [(100, 50, 55, 30), (100, 50, 158, 30)])
    layout = _cutting_layout(plan(doc, PrintOptions(spacing=8.0,
                                                    layout_budget=0.5)), doc)
    assert layout.best and not layout.drawn


def test_pieces_left_off_the_sheet_are_told(qapp):
    from leathercad.nesting import drawn_sheet
    from leathercad_app.printing import PrintOptions, _cutting_layout, plan
    win = _window(qapp, _wallet())
    try:
        win.canvas.nest_selected(600, 450, margin=5, spacing=8, budget=0)
        doc = win.doc
        strap = next(s for s in doc.shapes if s.name == "Strap")
        strap.transform.x += 2000                # dragged off the sheet
        assert drawn_sheet(doc) is not None
        layout = _cutting_layout(plan(doc, PrintOptions(cut_layout="drawn")),
                                 doc)
        assert layout.drawn and (layout.on, layout.of) == (6, 7)
    finally:
        win.hide()


def test_nest_on_sheet_lays_a_copy_left_on_its_original_apart(qapp):
    doc = Document()
    a = _add(doc, Rectangle(width=80, height=50, layer="Cut",
                            transform=Transform(0, 0)), "Tab")
    b = _add(doc, Rectangle(width=80, height=50, layer="Cut",
                            transform=Transform(0, 0)), "Tab")
    win = _window(qapp, doc)
    try:
        report = win.canvas.nest_selected(300, 200, margin=5, spacing=8,
                                          budget=0)
        assert report.startswith("Placed 2 of 2 pieces"), report
        ba, bb = a.bounds(), b.bounds()
        apart = max(bb[0] - ba[2], ba[0] - bb[2], bb[1] - ba[3], ba[1] - bb[3])
        assert apart >= 8.0 - 1e-6
    finally:
        win.hide()

"""Nesting that iterates: many layouts with the best kept, every quarter
turn, the smallest sheet, and nesting again onto a sheet already laid out.

The report: "i just had a good nest, moved one of the pieces away to see
what it would do, it couldn't figure it out" -- and "i would think it would
need to go through some iterations, choose a border size based on the
optimal layout". A nest was one greedy pass, biggest piece first. Its sheet
sat at the lower-left corner of the pieces, so dragging one off below or to
the left moved the sheet, and nesting one piece paid no heed to the pieces
already on the sheet."""

import os

import pytest

from leathercad.geometry import Vec2
from leathercad.nesting import (ALL_TURNS, NestCancelled, NestPiece,
                                cell_for_gap, leather_area, nest, nest_best,
                                nest_smallest, sheet_packer)
from leathercad.shapes import Circle, Polygon, Rectangle, Transform


# -- helpers ----------------------------------------------------------------
def _box(key, w, h, x=0.0, y=0.0):
    return NestPiece(key=key, outlines=[(
        [Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h), Vec2(x, y + h)],
        True)])


def _ring(pts):
    """A closed outline as a list of points with its closing point."""
    pts = list(pts)
    if (pts[0] - pts[-1]).length() > 1e-9:
        pts.append(pts[0])
    return pts


def _seg_d(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_sq(), 1e-18)))
    return (a.lerp(b, t) - p).length()


def _apart(A, B):
    """The least distance between two closed outlines (edges to edges)."""
    A, B = _ring(A), _ring(B)
    return min(min(_seg_d(p, B[j], B[j + 1]) for p in A
                   for j in range(len(B) - 1)),
               min(_seg_d(q, A[i], A[i + 1]) for q in B
                   for i in range(len(A) - 1)))


def _placed_outline(piece, pl):
    return [pl.apply(p) for p in piece.outlines[0][0]]


def _closest(rings, gap):
    """The least distance between any two of ``rings`` that come near."""
    best = float("inf")
    boxes = [(min(p.x for p in r), min(p.y for p in r),
              max(p.x for p in r), max(p.y for p in r)) for r in rings]
    for i in range(len(rings)):
        for j in range(i + 1, len(rings)):
            a, b = boxes[i], boxes[j]
            if (a[0] > b[2] + gap or b[0] > a[2] + gap
                    or a[1] > b[3] + gap or b[1] > a[3] + gap):
                continue
            best = min(best, _apart(rings[i], rings[j]))
    return best


def _wallet_shapes():
    """Ten pieces of a small bag, laid out anyhow."""
    out = []

    def add(sh, name):
        sh.name = name
        out.append(sh)
    add(Rectangle(width=220, height=120, corner_radius=10, layer="Cut",
                  transform=Transform(0, 0)), "Body")
    add(Rectangle(width=200, height=95, corner_radius=8, layer="Cut",
                  transform=Transform(300, 0)), "Lining")
    for i in range(3):
        add(Rectangle(width=100, height=75, corner_radius=6, layer="Cut",
                      transform=Transform(600 + 130 * i, 0)), f"Pocket {i + 1}")
    for i in range(2):
        add(Rectangle(width=30, height=240, layer="Cut",
                      transform=Transform(60 * i, 300)), f"Gusset {i + 1}")
    ell = [Vec2(0, 0), Vec2(90, 0), Vec2(90, 25), Vec2(25, 25), Vec2(25, 70),
           Vec2(0, 70)]
    for i in range(2):
        add(Polygon(points=list(ell), layer="Cut",
                    transform=Transform(200 + 150 * i, 300)), f"Corner {i + 1}")
    add(Circle(rx=18, ry=18, layer="Cut", transform=Transform(500, 300)),
        "Snap cap")
    return out


def _as_pieces(shapes):
    return [NestPiece(key=i, outlines=[(
        [Vec2(p.x, p.y) for p in sh.world_polyline()[0]], True)])
        for i, sh in enumerate(shapes)]


# -- the engine --------------------------------------------------------------
def test_many_layouts_get_more_on_than_one_pass():
    pieces = _as_pieces(_wallet_shapes())
    one, _left, _used = nest(pieces, 330, 360, margin=5, spacing=8,
                             cell=cell_for_gap(8))
    best = nest_best(pieces, 330, 360, margin=5, spacing=8, budget=None)
    assert best.tries > 40
    assert len(best.placements) >= 9 > len(one)
    rings = [_placed_outline(pieces[pl.key], pl) for pl in best.placements]
    assert _closest(rings, 8) >= 8.0 - 1e-6
    for r in rings:                                  # the margin, all round
        assert min(p.x for p in r) >= 5 - 1e-6 and max(p.x for p in r) <= 325 + 1e-6
        assert min(p.y for p in r) >= 5 - 1e-6 and max(p.y for p in r) <= 355 + 1e-6


def test_every_quarter_turn_is_tried():
    # two right triangles share a 106 x 58 sheet only with one turned 180°
    def tri(k):
        return NestPiece(key=k, outlines=[(
            [Vec2(0, 0), Vec2(100, 0), Vec2(0, 50)], True)])
    got = nest_best([tri("a"), tri("b")], 106, 58, margin=0, spacing=2,
                    budget=None, turns=ALL_TURNS)
    assert sorted(pl.turns for pl in got.placements) == [0, 2]
    assert got.placements[1].degrees in (0, 180)
    half = nest_best([tri("a"), tri("b")], 106, 58, margin=0, spacing=2,
                     budget=None, turns=(0, 1))
    assert len(half.placements) == 1


def test_the_bigger_piece_wins_when_only_one_fits():
    # placing the small square alone gives the tighter layout; the big one
    # is the leather that matters
    got = nest_best([_box("small", 60, 60), _box("big", 90, 90)], 104, 104,
                    margin=0, spacing=2, budget=None)
    assert [pl.key for pl in got.placements] == ["big"]
    assert got.unplaced == ["small"]


def test_the_margin_is_measured_from_the_outline():
    # a 100 mm piece, a 5 mm margin and an 8 mm gap: a 112 mm sheet takes it
    # (the grid's cell aside), its outline 5 mm in -- it used to sit 9 mm in
    got = nest_best([_box("p", 100, 50, 300, -40)], 112, 70, margin=5,
                    spacing=8, budget=None, turns=(0,))
    assert len(got.placements) == 1
    corner = got.placements[0].apply(Vec2(300, -40))
    assert (corner.x, corner.y) == (pytest.approx(5.0), pytest.approx(5.0))


def test_pieces_staying_put_are_kept_clear_of():
    stay = [Vec2(60, 40), Vec2(140, 40), Vec2(140, 120), Vec2(60, 120)]
    pieces = [_box(i, 50, 30) for i in range(6)]
    got = nest_best(pieces, 200, 160, margin=5, spacing=8, budget=None,
                    obstacles=[(stay, True)])
    assert got.placements
    for pl in got.placements:
        assert _apart(_placed_outline(pieces[pl.key], pl), stay) >= 8.0 - 1e-6


def test_a_piece_marked_where_it_lies_takes_the_cells_it_was_placed_in():
    # how a piece left on the sheet is blocked out the next time: marked
    # where it now lies, it must take just the cells it was placed in, or
    # the gap it fitted is a cell too narrow for it to go back into
    gap = 8.05
    cell = cell_for_gap(gap)
    ell = [Vec2(0.3, 0.7), Vec2(90.3, 0.7), Vec2(90.3, 25.7), Vec2(25.3, 25.7),
           Vec2(25.3, 70.7), Vec2(0.3, 70.7)]
    piece = NestPiece(key=0, outlines=[(ell, True)])
    for turns in range(4):
        a = sheet_packer(330, 360, margin=5, spacing=gap, cell=cell,
                         sheet_x=-110.0, sheet_y=-60.0, turns=(turns,))
        a.block_rect(-1e9, -1e9, 1e9, -60 + 5 + 37.3)   # a ledge below
        (pl,), _ = a.place([piece])
        b = sheet_packer(330, 360, margin=5, spacing=gap, cell=cell,
                         sheet_x=-110.0, sheet_y=-60.0)
        b.block_rect(-1e9, -1e9, 1e9, -60 + 5 + 37.3)
        b.block_outlines([([pl.apply(p) for p in ell], True)])
        assert b.occ == a.occ


def test_the_smallest_sheet_hugs_the_layout():
    shapes = _wallet_shapes()
    pieces = _as_pieces(shapes[:4] + shapes[7:8])
    got = nest_smallest(pieces, margin=5, spacing=8, sheet_x=-20.0,
                        sheet_y=10.0, budget=None)
    assert not got.unplaced and len(got.placements) == len(pieces)
    sx, sy, sw, sh = got.sheet
    assert (sx, sy) == (-20.0, 10.0)
    assert sw == int(sw) and sh == int(sh)           # whole millimetres
    rings = [_placed_outline(pieces[pl.key], pl) for pl in got.placements]
    assert _closest(rings, 8) >= 8.0 - 1e-6
    left = min(p.x for r in rings for p in r) - sx
    low = min(p.y for r in rings for p in r) - sy
    right = sx + sw - max(p.x for r in rings for p in r)
    top = sy + sh - max(p.y for r in rings for p in r)
    assert left == pytest.approx(5.0, abs=0.01)
    assert low == pytest.approx(5.0, abs=0.01)
    assert 5.0 - 1e-6 <= right < 6.0 and 5.0 - 1e-6 <= top < 6.0
    leather = sum(leather_area(p.outlines) for p in pieces)
    assert sw * sh < 1.6 * leather                   # a snug sheet
    # capped: no bigger than the leather you have
    capped = nest_smallest(pieces, margin=5, spacing=8, max_w=300,
                           max_h=600, budget=None)
    assert not capped.unplaced
    assert capped.sheet[2] <= 300 and capped.sheet[3] <= 600


def test_the_tries_report_progress_and_can_be_stopped():
    pieces = [_box(i, 40, 30) for i in range(5)]
    seen = []
    nest_best(pieces, 200, 150, spacing=8, budget=None,
              progress=lambda d, t: seen.append((d, t)) or True)
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1] > 2
    with pytest.raises(NestCancelled):
        nest_smallest(pieces, spacing=8, budget=None,
                      progress=lambda d, t: d < 150)


# -- on the drawing ------------------------------------------------------------
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _rig(qapp, shapes):
    from leathercad.document import Document
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    for sh in shapes:
        doc.add_shape(sh)
    win = MainWindow(doc)
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    return win, win.canvas, doc


def _sheet(doc):
    return next(s for s in doc.shapes if s.construction and s.name == "Sheet")


def _real(doc):
    return [s for s in doc.shapes if not s.construction]


def _outline(sh):
    return [Vec2(p.x, p.y) for p in sh.world_polyline()[0]]


def _inside(sh, sheet, margin):
    b, s = sh.bounds(), sheet.bounds()
    return (b[0] >= s[0] + margin - 1e-6 and b[1] >= s[1] + margin - 1e-6
            and b[2] <= s[2] - margin + 1e-6 and b[3] <= s[3] - margin + 1e-6)


def _drag(c, sh, dx, dy):
    sh.transform.x += dx
    sh.transform.y += dy
    c.rebuild()


def _select(qapp, c, shapes):
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if getattr(it, "model", None) in shapes:
            it.setSelected(True)
    qapp.processEvents()


def test_a_piece_dragged_off_the_sheet_goes_back_in(qapp):
    win, c, doc = _rig(qapp, _wallet_shapes())
    try:
        report = c.nest_selected(450, 330, margin=5, spacing=8, budget=0)
        assert report.startswith("Placed 10 of 10 pieces")
        sheet = _sheet(doc)
        frame = sheet.bounds()
        where = {s.name: (s.transform.x, s.transform.y, s.transform.rotation)
                 for s in _real(doc)}
        pocket = next(s for s in doc.shapes if s.name == "Pocket 2")
        _drag(c, pocket, -900, -500)        # off below and to the left
        report = c.nest_selected(450, 330, margin=5, spacing=8, budget=0)
        assert report.startswith("Placed 10 of 10 pieces")
        assert "(9 left where they were)" in report
        assert _sheet(doc) is sheet                                 # it stayed
        assert sheet.bounds() == pytest.approx(frame)
        for s in _real(doc):
            assert _inside(s, sheet, 5)
            if s is not pocket:
                assert (s.transform.x, s.transform.y,
                        s.transform.rotation) == where[s.name]
        assert _closest([_outline(s) for s in _real(doc)], 8) >= 8.0
    finally:
        win.hide()


def test_nesting_one_piece_fits_it_round_the_others(qapp):
    win, c, doc = _rig(qapp, _wallet_shapes())
    try:
        c.nest_selected(450, 330, margin=5, spacing=8, budget=0)
        sheet = _sheet(doc)
        body = next(s for s in doc.shapes if s.name == "Body")
        _drag(c, body, 1200, 0)
        where = {s.name: (s.transform.x, s.transform.y) for s in _real(doc)}
        _select(qapp, c, [body])
        report = c.nest_selected(450, 330, margin=5, spacing=8, budget=0)
        assert report.startswith("Placed 1 of 1 piece")
        assert _inside(body, sheet, 5)
        for s in _real(doc):
            if s is not body:
                assert (s.transform.x, s.transform.y) == where[s.name]
        assert _closest([_outline(s) for s in _real(doc)], 8) >= 8.0
    finally:
        win.hide()


def _messy():
    """Four squares spread over a sheet by hand, so no gap left takes the
    strip -- though laid out afresh, all five fit."""
    sheet = Rectangle(width=250, height=210, layer="Cut",
                      transform=Transform(125, 105))
    sheet.name, sheet.construction = "Sheet", True
    out = [sheet]
    for i, (x, y) in enumerate([(75, 52), (195, 52), (75, 157), (195, 157)]):
        sq = Rectangle(width=90, height=90, layer="Cut",
                       transform=Transform(x, y))
        sq.name = f"Square {i + 1}"
        out.append(sq)
    strip = Rectangle(width=40, height=180, layer="Cut",
                      transform=Transform(400, 100))
    strip.name = "Strip"
    return out + [strip]


def test_when_the_gaps_cannot_take_them_the_sheet_is_laid_out_afresh(qapp):
    win, c, doc = _rig(qapp, _messy())
    try:
        report = c.nest_selected(250, 210, margin=5, spacing=8, budget=0)
        assert report.startswith("Placed 5 of 5 pieces")
        assert "Laid out afresh" in report
        sheet = _sheet(doc)
        assert all(_inside(s, sheet, 5) for s in _real(doc))
        assert _closest([_outline(s) for s in _real(doc)], 8) >= 8.0
    finally:
        win.hide()


def test_keep_off_lays_everything_out_afresh(qapp):
    win, c, doc = _rig(qapp, _messy())
    try:
        report = c.nest_selected(250, 210, margin=5, spacing=8, budget=0,
                                 keep=False)
        assert report.startswith("Placed 5 of 5 pieces on the")
        assert "left where they were" not in report
        assert "Laid out afresh" not in report
    finally:
        win.hide()


def test_pieces_not_being_nested_stay_put_and_nothing_lands_on_them(qapp):
    shapes = _messy()
    win, c, doc = _rig(qapp, shapes)
    try:
        stay = shapes[1]                                  # Square 1
        before = (stay.transform.x, stay.transform.y)
        _select(qapp, c, shapes[2:])
        report = c.nest_selected(250, 210, margin=5, spacing=8, budget=0,
                                 keep=False)
        assert "Placed" in report
        assert (stay.transform.x, stay.transform.y) == before
        others = [s for s in _real(doc) if s is not stay
                  and _inside(s, _sheet(doc), 5)]
        assert others
        for s in others:
            assert _apart(_outline(s), _outline(stay)) >= 8.0
    finally:
        win.hide()


def test_the_smallest_sheet_is_drawn_round_the_layout(qapp):
    shapes = _wallet_shapes()
    win, c, doc = _rig(qapp, shapes[:5])
    try:
        report = c.nest_selected(600, 450, margin=5, spacing=8, budget=0,
                                 smallest=True)
        assert report.startswith("Smallest sheet found: ")
        sheet = _sheet(doc)
        assert sheet.width <= 600 and sheet.height <= 450
        assert sheet.width == int(sheet.width)
        assert sheet.height == int(sheet.height)
        assert all(_inside(s, sheet, 5) for s in _real(doc))
        assert _closest([_outline(s) for s in _real(doc)], 8) >= 8.0
        # nesting again onto it changes nothing
        where = [(s.transform.x, s.transform.y) for s in _real(doc)]
        report = c.nest_selected(sheet.width, sheet.height, margin=5,
                                 spacing=8, budget=0)
        assert "(5 left where they were)" in report
        assert [(s.transform.x, s.transform.y) for s in _real(doc)] == where
    finally:
        win.hide()


def test_the_report_names_what_was_turned(qapp):
    strap = Rectangle(width=30, height=200, layer="Cut",
                      transform=Transform(0, 0))
    strap.name = "Strap"
    win, c, _doc = _rig(qapp, [strap])
    try:
        report = c.nest_selected(260, 60, margin=5, spacing=8, budget=0)
        assert "Turned: Strap 90°" in report or "Turned: Strap 270°" in report
    finally:
        win.hide()


def test_a_change_made_just_before_apply_is_laid_out_first(qapp, monkeypatch,
                                                           tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QCheckBox, QDialog
    from leathercad_app.mathspin import MathSpinBox
    win, c, doc = _rig(qapp, _wallet_shapes()[:4])
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(win, "_settings",
                        lambda: QSettings(ini, QSettings.Format.IniFormat))

    def run(dlg):
        qapp.processEvents()                     # the first look
        assert c.nest_previewing()
        boxes = {b.text(): b for b in dlg.findChildren(QCheckBox)}
        boxes["Find the smallest sheet that takes every piece"].setChecked(False)
        qapp.processEvents()
        spins = {s.toolTip(): s for s in dlg.findChildren(MathSpinBox)}
        spins["Width of the leather you're cutting from"].setValue(520.0)
        return QDialog.DialogCode.Accepted       # Apply, at once
    monkeypatch.setattr(QDialog, "exec", run)
    try:
        win._nest_dialog()
        assert not c.nest_previewing()
        assert _sheet(doc).width == pytest.approx(520.0)
        assert all(_inside(s, _sheet(doc), 0) for s in _real(doc))
    finally:
        win.hide()

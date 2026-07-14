"""Starter templates and the recent-files menu."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def win(qapp):
    from leathercad_app.mainwindow import MainWindow
    w = MainWindow(Document())
    w._settings().setValue("recentFiles", [])       # isolate from user config
    w._rebuild_recent_menu()
    yield w
    w._settings().setValue("recentFiles", [])


# -- templates ---------------------------------------------------------------
def test_templates_build_and_have_content():
    from leathercad.templates import TEMPLATES
    from leathercad.stitching import holes_for_shape
    assert len(TEMPLATES) >= 3
    for label, builder in TEMPLATES:
        doc = builder()
        assert doc.shapes, f"{label} is empty"
        d2 = Document.from_dict(doc.to_dict())      # save/load safe
        assert len(d2.shapes) == len(doc.shapes)
    by_name = dict(TEMPLATES)
    holes = sum(holes_for_shape(s).count for s in by_name["Card holder"]().shapes)
    assert holes > 20                               # the tutorial piece stitches
    belt = by_name["Belt"]()
    assert sum(1 for s in belt.shapes if s.kind == "circle") == 5   # sizing holes


def test_slim_card_holder_practical():
    """Flat, compact front-pocket card holder: deep pockets that grip a card,
    and a U seam (open top) so cards can be inserted."""
    from leathercad.templates import slim_card_holder
    doc = slim_card_holder()

    cut = [s for s in doc.shapes if s.layer == "Cut"]
    assert len(cut) == 4 and all(s.kind == "rectangle" for s in cut)
    # all one width; flat rectangular back (no domed top to snag a pocket)
    assert len({round(s.width, 6) for s in cut}) == 1
    back, pockets = cut[0], cut[1:]
    assert back.height == 92.0                      # compact, flat top

    # deep pockets in small steps -> a card (86 mm) is gripped, not loose
    heights = [p.height for p in pockets]
    assert heights == [84.0, 76.0, 68.0]
    assert min(heights) >= 66                       # >= ~66 mm card engagement
    assert max(heights) - min(heights) <= 20        # gentle staircase

    # one U seam down the sides + bottom, top left OPEN for inserting cards
    assert len(doc.stitch_lines) == 1
    seam = doc.stitch_lines[0]
    assert not seam.closed
    holes = seam.result().holes
    assert len(holes) >= 40
    ys = [h.point.y for h in holes]
    assert min(ys) < 6                              # stitched across the bottom
    assert max(ys) < back.height - 4                # top edge unstitched (open)


def test_fold_over_wallet_matches_source_pattern():
    """The Oldis One / Lucais-style T pattern: 219 x 290 flat, symmetric
    pointed flap, smooth arched thumb notch, sloped entrance with a
    relief-holed diagonal slot, and 3 mm seams (including the middle
    vertical + horizontal rows) that register when the wing folds over."""
    from leathercad.templates import fold_over_wallet
    doc = fold_over_wallet()
    body = doc.shapes[0]

    # overall printed-sheet size and the 70 mm column
    x0, y0, x1, y1 = body.bounds()
    assert abs((x1 - x0) - 219) < 0.05 and abs((y1 - y0) - 290) < 0.05
    col_x = sorted({p.x for p in body.nodes if p.y > 100})
    assert col_x[-1] - col_x[0] == 70.0

    # the flap tip is SYMMETRIC about the column centreline: peak dead
    # centre, shoulders at equal height, no stray step nodes
    peak = max(body.nodes, key=lambda p: p.y)
    assert peak.y == 290.0 and peak.x == (78.0 + 148.0) / 2.0
    shoulders = [p for p in body.nodes if p.y == 238.0]
    assert sorted(p.x for p in shoulders) == [78.0, 148.0]
    assert not any(238.0 < p.y < 290.0 for p in body.nodes)

    # the thumb notch is ONE smooth arc through its apex, centred under
    # the column
    i = next(k for k, p in enumerate(body.nodes) if p.x == 95.0 and p.y == 0)
    notch_edge = body.edges[i]
    assert notch_edge.kind == "arc"
    assert notch_edge.mid.x == (95.0 + 131.0) / 2.0 and notch_edge.mid.y == 16.0

    # right wing top slopes down to the entrance; left wing top is straight
    assert any(abs(p.x - 219.0) < 1e-9 and abs(p.y - 58.0) < 1e-9
               for p in body.nodes)
    assert any(p.y == 90.0 and p.x < 10 for p in body.nodes)

    # diagonal quick-access slot with a relief circle at each end
    slot = [s for s in doc.shapes if s.name == "Quick-access slot"][0]
    a, b = slot.points
    assert a.y != b.y and a.x != b.x                        # genuinely diagonal
    reliefs = [s for s in doc.shapes if s.kind == "circle"]
    assert len(reliefs) == 2
    ends = {(p.x, p.y) for p in (a, b)}
    assert {(c.transform.x, c.transform.y) for c in reliefs} == ends

    # three folds; five 3 mm seams confined to the block, every row 4 mm
    # inside its panel -- no hole on or across a fold line
    assert sum(1 for s in doc.shapes if s.layer == "Score") == 3
    assert len(doc.stitch_lines) == 5
    for sl in doc.stitch_lines:
        assert sl.settings.pitch_mm == 3.0
        assert sl.result().count >= 18
        assert max(p.y for p in sl.points) <= 90.0
    top_w, top_m, left, middle, bottom = doc.stitch_lines
    assert max(p.x for p in top_w.points) < 78.0            # wing side only
    assert 78.0 < min(p.x for p in top_m.points)            # middle only...
    assert max(p.x for p in top_m.points) < 148.0           # ...inside the fold
    # the middle vertical row sits just INSIDE the right wing fold
    assert all(p.x == 144.0 for p in middle.points)
    # wing and middle vertical rows match hole-for-hole in height
    L, M = left.result().holes, middle.result().holes
    assert len(L) == len(M)
    assert all(abs(a.point.y - b.point.y) < 1e-6 for a, b in zip(L, M))

    # the bottom seam is ONE continuous run that arcs AROUND the thumb
    # notch: holes climb over the arch and the chords stay iron-even
    holes = bottom.result().holes
    over = [h for h in holes if h.point.y > 10.0]
    assert len(over) >= 8
    assert all(95.0 < h.point.x < 131.0 for h in over)
    assert max(h.point.y for h in holes) > 18.0             # clears the apex
    gaps = bottom.result().chord_spacings()
    assert max(gaps) < 1.15 * min(gaps)


def test_new_from_template_adopts_document(win):
    from leathercad.templates import card_holder
    win._new_from_template(card_holder)
    assert len(win.doc.shapes) == 2
    assert win.path is None                         # unsaved: Save prompts
    assert not win._unsaved_changes                 # fresh baseline, no bullet


# -- recent files -------------------------------------------------------------
def _menu_texts(menu):
    return [a.text() for a in menu.actions() if a.text()]


def test_open_and_save_record_recents(win, tmp_path):
    doc = Document()
    p1 = tmp_path / "one.json"
    p2 = tmp_path / "two.json"
    doc.save(str(p1))
    doc.save(str(p2))

    assert win.open_path(str(p1))
    assert win.open_path(str(p2))
    rec = win.recent_files()
    assert rec[0].endswith("two.json") and rec[1].endswith("one.json")

    # re-opening an old file bubbles it to the top, without duplicating
    assert win.open_path(str(p1))
    rec = win.recent_files()
    assert rec[0].endswith("one.json") and len(
        [p for p in rec if p.endswith("one.json")]) == 1

    # menu shows them, newest first, plus Clear
    texts = _menu_texts(win.recent_menu)
    assert texts[0] == "one.json" and texts[1] == "two.json"
    assert "Clear menu" in texts

    # saving under a new name records it too
    p3 = tmp_path / "three.json"
    win.path = str(p3)
    win.save_document()
    assert win.recent_files()[0].endswith("three.json")


def test_missing_recent_is_pruned(win, tmp_path):
    p = tmp_path / "gone.json"
    Document().save(str(p))
    assert win.open_path(str(p))
    assert win.recent_files()
    p.unlink()                                      # file deleted outside app
    win._rebuild_recent_menu()
    assert "(empty)" in _menu_texts(win.recent_menu)
    assert not [q for q in win.recent_files() if q.endswith("gone.json")]


def test_recent_list_is_capped(win, tmp_path):
    for i in range(12):
        p = tmp_path / f"f{i}.json"
        Document().save(str(p))
        win._add_recent(str(p))
    assert len(win.recent_files()) == win._RECENT_MAX
    assert win.recent_files()[0].endswith("f11.json")

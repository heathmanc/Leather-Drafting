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


def test_vertical_wallet_is_cross_single_piece():
    """The Oldis One is one cross-shaped piece: narrow spine with a pointed
    flap on top, a bottom tab, and two wide wings that wrap into the pocket."""
    from leathercad.templates import vertical_wallet
    doc = vertical_wallet()
    body = doc.shapes[0]

    # SINGLE piece cut: only the body is on the Cut layer
    assert [s for s in doc.shapes if s.layer == "Cut"] == [body]
    xs = [p.x for p in body.points]
    ys = [p.y for p in body.points]
    # cross: much wider at the wing band than at the top spine
    full_w = max(xs) - min(xs)
    top_w = (max(p.x for p in body.points if p.y > 200)
             - min(p.x for p in body.points if p.y > 200))
    assert full_w > 200 and top_w < 90 and full_w > 2 * top_w
    # a pointed flap at the very top, centred on the spine
    apex = max(body.points, key=lambda p: p.y)
    assert abs(apex.x) < 1e-6 and apex.y == max(ys)
    # bottom tab narrower than the spine (70 mm)
    tab_w = (max(p.x for p in body.points if p.y < 1)
             - min(p.x for p in body.points if p.y < 1))
    assert 0 < tab_w < 70
    # right-handed: the right wing's top is cut lower than the left wing's
    left_wing_top = max(p.y for p in body.points if p.x < -80)
    right_wing_top = max(p.y for p in body.points if p.x > 80)
    assert right_wing_top < left_wing_top

    # four fold lines (Score) framing the central pocket panel
    assert sum(1 for s in doc.shapes if s.layer == "Score") == 4
    # one seam per wing, each a real run of holes that reaches out to the tip
    assert len(doc.stitch_lines) == 2
    for sl in doc.stitch_lines:
        holes = sl.result().holes
        assert len(holes) >= 20
        assert max(abs(h.point.x) for h in holes) > 90    # wraps the wing tip


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

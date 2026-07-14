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


def test_curved_card_holder_flagship():
    """Flagship demo: arched-top card holder whose fine perimeter stitch flows
    evenly around the curve -- the program's signature -- with three stepped,
    thumb-scooped pockets."""
    from leathercad.templates import curved_card_holder
    from leathercad.stitching import holes_for_shape
    doc = curved_card_holder()

    # four one-piece curved panels; all share one width
    assert len(doc.shapes) == 4
    assert all(s.layer == "Cut" and s.kind == "editpath" for s in doc.shapes)
    widths = {round(s.bounds()[2] - s.bounds()[0], 6) for s in doc.shapes}
    assert len(widths) == 1

    back, pockets = doc.shapes[0], doc.shapes[1:]
    # only the back panel is stitched; it is the tallest (domed top)
    assert back.stitch and back.stitch.enabled
    assert all(not p.stitch.enabled for p in pockets)
    assert back.bounds()[3] == max(s.bounds()[3] for s in doc.shapes)
    # dome is tangent: peak = straight-side height + half-width (semicircle),
    # give or take the arc-flattening sample step
    assert abs(back.bounds()[3] - (75.0 + 35.0)) < 0.1

    # pockets step up (side tops decreasing) and each mouth is a concave scoop
    tops = [p.nodes[1].y for p in pockets]
    assert tops == sorted(tops, reverse=True)
    for p in pockets:
        assert p.edges[1].mid.y < p.nodes[1].y     # scoop dips below the sides

    # the hero: dense stitching that wraps the dome with even chord spacing
    res = holes_for_shape(back)
    assert res.count >= 60
    assert max(h.point.y for h in res.holes) > 100          # holes on the dome
    gaps = res.chord_spacings()
    assert max(gaps) < 1.35 * min(gaps)                     # pricking-iron even


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

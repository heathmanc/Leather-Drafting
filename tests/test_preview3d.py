"""The 3D assembly preview: what in a drawing folds, the dialog's table of
fold lines, the view, and its changes going back to the drawing."""

import math
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from leathercad import foldsolid as fs  # noqa: E402
from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Circle, Polygon, Rectangle,  # noqa: E402
                               copy_shape_attrs)
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _line(a, b, name="", way="front", angle=180.0, order=0):
    ln = Polygon(points=[Vec2(*a), Vec2(*b)], close_path=False, name=name)
    ln.fold_dir, ln.fold_angle, ln.fold_order = way, angle, order
    return ln


def _rect(x, y, w, h, name="", r=0.0):
    s = Rectangle(width=w, height=h, corner_radius=r, name=name)
    s.transform.x, s.transform.y = x + w / 2, y + h / 2
    return s


def _trifold():
    """A 240 x 100 strip with fold lines a third and two thirds along."""
    doc = Document("trifold")
    doc.shapes.append(_rect(0, 0, 240, 100, "Tri-fold"))
    doc.shapes.append(_line((80, 0), (80, 100), "Left fold", order=1))
    doc.shapes.append(_line((160, 0), (160, 100), "Right fold", order=2))
    return doc


def _tray():
    """A 60 x 40 base with a 30 mm side to fold up on each of its edges."""
    doc = Document("tray")
    pts = [(0, 0), (0, -30), (60, -30), (60, 0), (90, 0), (90, 40), (60, 40),
           (60, 70), (0, 70), (0, 40), (-30, 40), (-30, 0)]
    doc.shapes.append(Polygon(points=[Vec2(*p) for p in pts], close_path=True,
                              name="Tray"))
    for a, b in (((0, 0), (0, 40)), ((60, 0), (60, 40)), ((0, 0), (60, 0)),
                 ((0, 40), (60, 40))):
        doc.shapes.append(_line(a, b, angle=90.0))
    return doc


def _wallet(right="front"):
    """The fold-over wallet template with its three folds made: all to
    the front, or the right wing the way ``right`` says."""
    from leathercad.templates import fold_over_wallet
    doc = fold_over_wallet()
    order = 0
    for sh in doc.shapes:
        if sh.name.endswith("fold"):
            order += 1
            sh.fold_dir, sh.fold_angle, sh.fold_order = "front", 180.0, order
            if sh.name == "Right wing fold":
                sh.fold_dir = right
    assert order == 3
    return doc


def _four_panels():
    """A 90 x 60 piece with rounded corners, stitched all round, and three
    fold lines down it: the first to the front, then the others to the
    back. Its middle two panels are drawn the same width, 18.5 mm."""
    doc = Document("four panels")
    body = _rect(-45, -30, 90, 60, "Body", r=8)
    body.stitch = StitchSettings(enabled=True, pitch_mm=3.85, inset=3.5)
    doc.shapes.append(body)
    for x, way, order in ((-18.5, "front", 1), (0.0, "back", 2),
                          (18.5, "back", 3)):
        doc.shapes.append(_line((x, 30), (x, -30), way=way, order=order))
    return doc


def _strip(widths, ways, orders=None, angles=None, high=60.0):
    """A strip ``high`` mm high with fold lines across it that leave
    panels as wide as ``widths``: each to the front (1) or the back (-1),
    right over unless ``angles`` says, one after the other from the left
    unless ``orders`` does."""
    doc = Document("strip")
    doc.shapes.append(_rect(0, 0, sum(widths), high, "Strip"))
    for k, way in enumerate(ways):
        x = sum(widths[:k + 1])
        doc.shapes.append(_line(
            (x, 0), (x, high), way="front" if way > 0 else "back",
            angle=angles[k] if angles else 180.0,
            order=orders[k] if orders else k + 1))
    return doc


def _folds(doc):
    return [s for s in doc.shapes if s.is_fold_line]


def _heights(lay):
    fr = lay.frames([1.0] * len(lay.folds))
    return [round(fs.place(fr[p.index], p.spot)[2], 6) for p in lay.panels]


def _dialog(doc, **kw):
    from leathercad_app.preview3d import FoldPreviewDialog
    dlg = FoldPreviewDialog(doc=doc, **kw)
    dlg.show()
    QApplication.processEvents()
    return dlg


# -- the drawing --------------------------------------------------------------

def test_fold_order_and_radius_are_saved_with_the_drawing():
    doc = _trifold()
    a, b = _folds(doc)
    a.fold_order, a.fold_radius, a.fold_dir, a.fold_angle = 3, 1.5, "back", 120
    again = Document.from_dict(doc.to_dict())
    a2, b2 = _folds(again)
    assert (a2.fold_dir, a2.fold_angle, a2.fold_order, a2.fold_radius) == \
        ("back", 120, 3, 1.5)
    assert (b2.fold_order, b2.fold_radius) == (2, -1.0)
    # a fold with nothing set writes nothing new, and an old file reads
    b.fold_order = 0
    saved = doc.to_dict()["shapes"][2]
    assert "fold_order" not in saved and "fold_radius" not in saved
    assert Document.from_dict(doc.to_dict()).shapes[2].fold_order == 0
    # a command that rebuilds a line keeps its fold settings
    copy = copy_shape_attrs(a, Polygon(points=[Vec2(0, 0), Vec2(1, 0)],
                                       close_path=False))
    assert (copy.fold_order, copy.fold_radius) == (3, 1.5)
    # none of it is geometry: changing it doesn't rebuild the outline
    key = a.geometry_key()
    a.fold_order, a.fold_radius = 7, 4.0
    assert a.geometry_key() == key


def test_foldables_are_the_pieces_fold_lines_cross(qapp):
    from leathercad_app.preview3d import fold_lines, foldables
    doc = _trifold()
    body = doc.shapes[0]
    body.stitch = StitchSettings(pitch_mm=5.0, inset=4.0, hole_diameter=1.2)
    doc.shapes.append(_rect(0, 200, 50, 50, "Loose panel"))    # no folds
    doc.shapes.append(_line((400, 0), (400, 50), "Stray"))     # on nothing
    hole = Circle(rx=4, ry=4, name="Snap")
    hole.transform.x, hole.transform.y = 40, 50
    doc.shapes.append(hole)
    found = foldables(doc)
    assert [f.title for f in found] == ["Tri-fold"]
    f = found[0]
    assert f.kind == "piece" and f.key == body.shape_id
    assert [s.name for s in f.lines] == ["Left fold", "Right fold"]
    assert len(f.cutouts) == 1 and len(f.outline) >= 4
    assert f.marks == []
    assert len(f.holes) > 100 and {h[2] for h in f.holes} == {1.2}
    lines = fold_lines(f.lines)
    assert [(ln.label, ln.name, ln.angle, ln.direction, ln.order)
            for ln in lines] == [("A", "Left fold", 180.0, 1, 1),
                                 ("B", "Right fold", 180.0, 1, 2)]
    assert lines[0].key is f.lines[0]
    assert foldables(Document("empty")) == []


def test_slits_and_scored_lines_on_a_piece_are_shown_on_it(qapp):
    from leathercad_app.preview3d import foldables
    doc = _trifold()
    slit = Polygon(points=[Vec2(20, 30), Vec2(60, 30)], close_path=False,
                   name="Card slot")
    score = Polygon(points=[Vec2(100, 60), Vec2(120, 80), Vec2(140, 60)],
                    close_path=False, name="V")
    score.layer = "Score"
    guide = Polygon(points=[Vec2(10, 90), Vec2(230, 90)], close_path=False)
    guide.construction = True
    off = Polygon(points=[Vec2(300, 0), Vec2(340, 0)], close_path=False)
    doc.shapes += [slit, score, guide, off]
    f = foldables(doc)[0]
    assert [(k, len(pts)) for k, pts in f.marks] == [("cut", 2), ("line", 3)]
    dlg = _dialog(doc)
    try:
        assert sum(len(c.lines) for c in dlg.layout_.cells) >= 3
        # they are part of what the model is built from
        before = dlg.layout_
        slit.points[1] = Vec2(70, 30)
        dlg.reload()
        assert dlg.layout_ is not before
    finally:
        dlg.close()


def test_each_piece_with_fold_lines_is_offered_biggest_first(qapp):
    from leathercad_app.preview3d import foldables
    doc = _trifold()
    doc.shapes.append(_rect(0, 120, 160, 20, "Strap", r=4))
    # drawn past both edges, and stopping short of them
    doc.shapes.append(_line((40, 110), (40, 150), "Buckle fold", way="back"))
    doc.shapes.append(_line((120, 122), (120, 138), "Keeper fold",
                            angle=90.0))
    found = foldables(doc)
    assert [f.title for f in found] == ["Tri-fold", "Strap"]
    assert [s.name for s in found[1].lines] == ["Buckle fold", "Keeper fold"]
    lay = fs.fold_piece(found[1].outline,
                        __import__("leathercad_app.preview3d", fromlist=["x"])
                        .fold_lines(found[1].lines))
    assert len(lay.panels) == 3 and lay.crosses == [True, True]


def test_panels_drawn_edge_to_edge_are_offered_as_a_net(qapp):
    from leathercad_app.preview3d import foldables
    doc = Document("net")
    doc.shapes += [_rect(0, 0, 60, 40, "Base"), _rect(-30, 0, 30, 40),
                   _rect(60, 0, 30, 40), _rect(0, 40, 60, 30),
                   _rect(0, -30, 60, 30), _rect(300, 300, 20, 20, "Apart")]
    found = foldables(doc)
    assert len(found) == 1 and found[0].kind == "net"
    assert found[0].title == "5 panels that share edges"
    dlg = _dialog(doc)
    try:
        assert [f.label for f in dlg.folds] == ["A", "B", "C", "D"]
        assert all(f.angle == 90.0 for f in dlg.folds)
        assert len(dlg.layout_.panels) == 5 and dlg.layout_.n_steps == 4
        dlg.table.cellWidget(2, 3).setValue(45.0)    # kept here, not saved
        assert dlg.folds[2].angle == 45.0
        dlg.reload()
        assert dlg.folds[2].angle == 45.0
    finally:
        dlg.close()


# -- the dialog ---------------------------------------------------------------

def test_the_table_has_a_row_for_every_fold_line(qapp):
    doc = _trifold()
    dlg = _dialog(doc)
    try:
        t = dlg.table
        assert t.rowCount() == 2
        assert [t.item(r, 0).text() for r in range(2)] == ["A  Left fold",
                                                           "B  Right fold"]
        assert [t.cellWidget(r, 1).value() for r in range(2)] == [1, 2]
        assert [t.cellWidget(r, 2).currentText() for r in range(2)] == \
            ["Front", "Front"]
        assert [t.cellWidget(r, 3).value() for r in range(2)] == [180, 180]
        # nothing bends tighter than asked; B goes round the first flap
        assert t.cellWidget(0, 4).text() == "Auto (0 mm)"
        assert t.cellWidget(1, 4).text() == "Auto (1 mm)"
        assert [t.item(r, 5).text() for r in range(2)] == ["3.14 mm",
                                                           "6.28 mm"]
        assert dlg.layout_.steps == [1, 2]
        assert dlg.view.caption == "Flat  ·  2 steps to fold"
        assert "Leather the bends take" in dlg.readout.text()
        assert "Fold B bends at 1 mm to go round the layer" \
            in dlg.readout.text()
        assert not dlg.cmb_piece.isVisible()          # only one piece
        assert dlg.cmb_base.count() == 3
    finally:
        dlg.close()


def test_order_direction_angle_and_radius_change_the_model_and_the_lines(qapp):
    doc = _trifold()
    a, b = _folds(doc)
    dlg = _dialog(doc)
    try:
        t = dlg.table
        dlg.cmb_base.setCurrentIndex(1)               # the middle stays
        assert _heights(dlg.layout_) == [2.0, 0.0, 4.0]
        # fold B first: now A is the one that goes round
        t.cellWidget(1, 1).setValue(1)
        t.cellWidget(0, 1).setValue(2)
        assert dlg.layout_.steps == [2, 1]
        assert _heights(dlg.layout_) == [4.0, 0.0, 2.0]
        assert (a.fold_order, b.fold_order) == (2, 1)
        assert t.cellWidget(0, 4).text() == "Auto (1 mm)"
        # the same step: together
        t.cellWidget(0, 1).setValue(1)
        assert dlg.layout_.steps == [1, 1] and dlg.layout_.n_steps == 1
        t.cellWidget(0, 1).setValue(2)
        # A to the back: it goes under instead, and nothing is in its way
        t.cellWidget(0, 2).setCurrentIndex(1)
        assert dlg.folds[0].direction == -1 and a.fold_dir == "back"
        assert _heights(dlg.layout_) == [-2.0, 0.0, 2.0]
        assert t.cellWidget(0, 4).text() == "Auto (0 mm)"
        # how far, and how tightly
        t.cellWidget(1, 3).setValue(90.0)
        assert b.fold_angle == 90.0 and dlg.folds[1].angle == 90.0
        assert abs(dlg.layout_.used[1] - 1.5708) < 1e-3
        t.cellWidget(0, 4).setValue(2.5)
        assert a.fold_radius == 2.5 and dlg.layout_.radius[0] == 2.5
        assert _heights(dlg.layout_)[0] == -7.0       # 2 x (2.5 + 1) down
        assert t.item(0, 5).text() == "11 mm"
        t.cellWidget(0, 4).setValue(-0.5)             # back to Auto
        assert a.fold_radius == -1.0 and dlg.layout_.radius[0] == 0.0
    finally:
        dlg.close()


def test_thickness_and_bend_radius_change_the_model(qapp, tmp_path):
    ini = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    dlg = _dialog(_trifold(), settings=ini)
    try:
        assert (dlg.spin_t.value(), dlg.spin_r.value()) == (2.0, 0.0)
        dlg.cmb_base.setCurrentIndex(1)
        dlg.spin_t.setValue(4.0)
        assert dlg.layout_.thickness == 4.0
        assert _heights(dlg.layout_) == [4.0, 0.0, 8.0]
        assert dlg.table.item(0, 5).text() == "6.28 mm"
        dlg.spin_r.setValue(1.0)
        assert dlg.layout_.radius[0] == 1.0
        assert _heights(dlg.layout_) == [6.0, 0.0, 10.0]
        assert dlg.table.cellWidget(0, 4).text() == "Auto (1 mm)"
        assert dlg.table.cellWidget(1, 4).text() == "Auto (3 mm)"
    finally:
        dlg.close()
    # and they are remembered for next time
    again = _dialog(_trifold(), settings=ini)
    try:
        assert (again.spin_t.value(), again.spin_r.value()) == (4.0, 1.0)
    finally:
        again.close()


def test_the_leather_it_is_shown_in_is_chosen_and_remembered(qapp, tmp_path):
    from leathercad_app import foldview as fv
    ini = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    doc = _trifold()
    dlg = _dialog(doc, settings=ini)
    try:
        box = dlg.cmb_leather
        assert [box.itemText(i) for i in range(box.count())] \
            == list(fv.LEATHERS)
        assert box.currentText() == dlg.view.look.leather \
            == fv.DEFAULT_LEATHER
        before = dlg.view.grab().toImage()
        lay = dlg.layout_
        box.setCurrentText("Black")
        QApplication.processEvents()
        assert dlg.view.look.leather == "Black"
        assert dlg.view.grab().toImage() != before
        # only the picture changes: the model and the drawing are as they were
        assert dlg.layout_ is lay and not dlg._unsaved
    finally:
        dlg.close()
    again = _dialog(_trifold(), settings=ini)
    try:
        assert again.cmb_leather.currentText() == "Black"
        assert again.view.look.leather == "Black"
    finally:
        again.close()
    # a leather saved by some other version, that this one doesn't have
    ini.setValue("fold3dLeather", "Snakeskin")
    other = _dialog(_trifold(), settings=ini)
    try:
        assert other.cmb_leather.currentText() == fv.DEFAULT_LEATHER
        assert other.view.look.leather == fv.DEFAULT_LEATHER
    finally:
        other.close()


def test_a_fold_that_forms_off_its_line_is_reported(qapp):
    from leathercad_app import units
    doc = _four_panels()
    dlg = _dialog(doc)
    try:
        dlg.cmb_base.setCurrentIndex(3)               # panel 4 stays still
        # folded up, panels 1 and 2 lie inside fold C with the fold between
        # them too far along: fold C forms 3.14 mm on, at the radius and
        # with the leather it would have had on its line
        assert abs(dlg.layout_.shift[2] - math.pi) < 1e-6
        assert dlg.table.cellWidget(2, 4).text() == "Auto (2 mm)"
        assert dlg.table.item(2, 5).text() == "9.42 mm"
        says = _words(dlg)
        assert "Fold C bends at 2 mm to go round 2 layers folded in " \
            "before it." in says
        sentence = ("Fold C folds 3.14 mm off its line, towards panel 4, to "
                    "go round what is folded inside it. To have it fold on "
                    "its line, shorten panel 2 by 3.14 mm where it reaches "
                    "into the fold.")
        assert sentence in says.split("\n")
        # (the guide and the changelog quote it as it is said)
        root = Path(__file__).resolve().parent.parent
        for name in ("docs/USER_GUIDE.md", "CHANGELOG.md"):
            text = (root / name).read_text(encoding="utf-8")
            assert f"*{sentence}*" in " ".join(text.split())
        assert says.count("off its line") == 1        # the other two don't
        # (the leather of a bend is taken about the place it forms at)
        assert "Half comes from each side of where a fold forms." in says
        # the panels are numbered as they are on the lines as drawn
        assert [dlg.cmb_base.itemText(i) for i in range(4)] \
            == ["Panel 1", "Panel 2", "Panel 3", "Panel 4"]
        assert dlg.cmb_base.currentIndex() == dlg.layout_.base == 3
        # ... and the holes are checked where the leather really puts
        # them: the outer panels no longer finish level, so the holes
        # along their edges, drawn to meet, don't
        found = {(f.a, f.b): f for f in dlg.view.check.findings}
        assert found[1, 4].met == 0 and found[1, 4].off > 0
        assert found[2, 3].off == 0 and found[2, 3].met > 0
        # in inches
        units.set_current("in")
        try:
            dlg.rebuild()
            assert ("Fold C folds 0.124 in off its line, towards panel 4, "
                    "to go round what is folded inside it. To have it fold "
                    "on its line, shorten panel 2 by 0.124 in where it "
                    "reaches into the fold.") in _words(dlg)
        finally:
            units.set_current("mm")
        # with a bend radius the folds hold the layers apart, so there is
        # more for fold C to go round, further off its line: layers are
        # not counted then
        dlg.spin_r.setValue(1.0)
        assert dlg.table.cellWidget(2, 4).text() == "Auto (4 mm)"
        says = _words(dlg)
        assert "Fold C bends at 4 mm to go round what is folded in " \
            "before it." in says
        assert "Fold C folds 4.71 mm off its line, towards panel 4" in says
        assert "shorten panel 2 by 4.71 mm where it reaches into" in says
        dlg.spin_r.setValue(0.0)
        # take the 3.14 mm it names off panel 2 -- move the first fold line
        # towards the second -- and fold C is on its line, with no more
        # said about it
        first = _folds(doc)[0]
        first.points[:] = [Vec2(-18.5 + 3.14, 30), Vec2(-18.5 + 3.14, -30)]
        dlg.reload()
        assert dlg.layout_.shift == [0.0, 0.0, 0.0]
        assert dlg.layout_.base == 3                  # still panel 4
        says = _words(dlg)
        assert "off its line" not in says and "⚠" not in says
        assert "Fold C bends at 2 mm to go round 2 layers" in says
        assert "Half comes from each side of a fold line." in says
    finally:
        dlg.close()
    # layers are counted wherever the room is just their thickness: a
    # letter fold whose flap stops 6 mm short, each fold bending at 1 mm
    dlg = _dialog(_strip([74, 80, 80], (1, 1)))
    try:
        dlg.spin_r.setValue(1.0)
        assert _near_all(dlg.layout_.radius, [1.0, 2.0])
        assert dlg.layout_.layers == [0, 1]
        assert "Fold B bends at 2 mm to go round the layer folded in " \
            "before it." in _words(dlg)
    finally:
        dlg.close()


def test_the_wallet_folds_its_right_wing_round_the_left_one(qapp):
    # The template's left wing is 78 mm and the column it folds on to 70:
    # it runs 8 mm past the line the right wing folds on. Folded over it,
    # the right wing can only fold where the left one ends
    dlg = _dialog(_wallet())
    try:
        lay = dlg.layout_
        assert [f.name for f in dlg.folds] == ["Left wing fold", "Flap fold",
                                               "Right wing fold"]
        assert lay.steps == [1, 3, 2]
        # (8 mm, and half the 6.28 mm of leather its bend takes)
        assert _near_all(lay.shift, [0.0, 0.0, 8.0 + math.pi])
        assert lay.shift_toward == [None, None, 4]    # the right wing
        assert lay.shift_for == [None, None, 1]       # the left
        assert ("Fold C folds 11.14 mm off its line, towards panel 4, to "
                "go round what is folded inside it. To have it fold on its "
                "line, shorten panel 1 by 11.14 mm where it reaches into the "
                "fold.") in _words(dlg)
        assert not lay.warnings
    finally:
        dlg.close()
    # with the right wing folded to the back instead nothing is inside it
    dlg = _dialog(_wallet(right="back"))
    try:
        assert dlg.layout_.shift == [0.0, 0.0, 0.0]
        assert "off its line" not in _words(dlg)
    finally:
        dlg.close()


def test_what_is_left_in_the_way_of_a_fold_is_said_in_red(qapp):
    from leathercad_app import units
    # the four panels' folds on a strip whose last panel is only 6 mm:
    # fold C has not the room there to clear what is inside it
    dlg = _dialog(_strip([26.5, 18.5, 18.5, 6], (1, -1, -1)))
    try:
        dlg.cmb_base.setCurrentIndex(3)
        assert dlg.layout_.stuck == [False, False, True]
        says = _words(dlg).split("\n")
        # (it has moved, but not round what is inside it)
        assert "Fold C folds 1.29 mm off its line, towards panel 4." in says
        warning = ("⚠ What is folded inside fold C reaches 3.14 mm into its "
                   "bend, and the fold cannot move further out of its way: "
                   "its bend would not fit before the edge of panel 4. "
                   "Shorten panel 2 by 3.14 mm where it reaches into the "
                   "fold.")
        assert warning in says
        assert f"<span style='color:#c0392b'>{warning}</span>" \
            in dlg.readout.text()
        # every length of it in inches when those are chosen
        units.set_current("in")
        try:
            dlg.rebuild()
            says = _words(dlg).split("\n")
            assert "Fold C folds 0.051 in off its line, towards panel 4." \
                in says
            assert ("⚠ What is folded inside fold C reaches 0.124 in into "
                    "its bend, and the fold cannot move further out of its "
                    "way: its bend would not fit before the edge of panel "
                    "4. Shorten panel 2 by 0.124 in where it reaches into "
                    "the fold.") in says
            assert not any(" mm" in line for line in says)
        finally:
            units.set_current("mm")
    finally:
        dlg.close()
    # flaps of 38 and 40 mm folded in from either side of fold B: it goes
    # half way between them, and there is no one panel to shorten for it
    dlg = _dialog(_strip([38, 40, 40, 40], (1, 1, 1), orders=(1, 3, 2)))
    try:
        dlg.cmb_base.setCurrentIndex(1)
        says = _words(dlg).split("\n")
        assert "Fold B folds 1 mm off its line, towards panel 2." in says
        assert ("⚠ What is folded inside fold B reaches into its bend from "
                "both sides: panel 1 by 2.71 mm and panel 4 by 4.71 mm. "
                "Shorten each by that much where it reaches into the fold.") \
            in says
        assert "None" not in _words(dlg)
    finally:
        dlg.close()
    # a wall stood up first, and a stack folded under that ends short of
    # it: with fold B off its line the stack ends in the wall
    dlg = _dialog(_strip([28, 28, 30, 15], (-1, -1, -1), orders=(2, 3, 1),
                         angles=(180, 180, 90), high=30))
    try:
        dlg.cmb_base.setCurrentIndex(2)
        says = _words(dlg).split("\n")
        assert ("Fold B folds 3.14 mm off its line, towards panel 3, to go "
                "round what is folded inside it. To have it fold on its "
                "line, shorten panel 1 by 3.14 mm where it reaches into the "
                "fold.") in says
        assert ("⚠ With fold B off its line, panel 2 and panel 4 are drawn "
                "3.5 mm into each other (and they are not the only ones).") \
            in says
    finally:
        dlg.close()
    # folds too close for their bends, in inches too
    dlg = _dialog(_strip([80, 2, 158], (1, -1)))
    try:
        dlg.spin_r.setValue(1.0)
        assert ("⚠ Folds A and B are 2 mm apart, but their bends need 6.28 "
                "mm between them.") in _words(dlg)
        units.set_current("in")
        try:
            dlg.rebuild()
            assert ("⚠ Folds A and B are 0.079 in apart, but their bends "
                    "need 0.247 in between them.") in _words(dlg)
        finally:
            units.set_current("mm")
    finally:
        dlg.close()


def test_a_fold_is_not_said_to_go_round_what_is_still_in_its_bend(qapp):
    # a 15 mm hem folded down, and then a letter fold in thirds across it:
    # the cover's fold moves for the flap, but the hem is cut by it and
    # lies in its bend wherever it forms
    doc = Document("hemmed")
    doc.shapes.append(_rect(0, 0, 120, 75, "Hemmed"))
    doc.shapes.append(_line((0, 60), (120, 60), order=1))
    doc.shapes.append(_line((40, 0), (40, 75), order=2))
    doc.shapes.append(_line((80, 0), (80, 75), order=3))
    dlg = _dialog(doc)
    try:
        lay = dlg.layout_
        assert [f.order for f in dlg.folds] == [2, 1, 3]     # A, hem, C
        assert lay.stuck == [True, False, True]
        says = _words(dlg).split("\n")
        assert "Fold C folds 4.39 mm off its line, towards panel 6." in says
        assert "to go round what is folded inside it" not in _words(dlg)
        assert "To have it fold on its line" not in _words(dlg)
        assert ("⚠ Fold C runs across panels 3 and 5, which are folded "
                "inside it: each is shown cut along the fold and 7.85 mm "
                "into its bend. Cutting each back by that much at the fold "
                "clears it.") in says
        assert _words(dlg).count("⚠") == 2            # fold A the same
    finally:
        dlg.close()


def test_a_fold_that_closes_over_one_off_its_line_says_to_see_to_that(qapp):
    # three 18.5 mm panels rolled up on a longer one: fold B is off its
    # line for panel 1, and fold C, which closes over fold B, further --
    # with no measure of its own for the drawing
    dlg = _dialog(_strip([18.5, 18.5, 18.5, 60], (1, 1, 1)))
    try:
        dlg.cmb_base.setCurrentIndex(3)
        says = _words(dlg).split("\n")
        assert ("Fold B folds 3.14 mm off its line, towards panel 3, to go "
                "round what is folded inside it. To have it fold on its "
                "line, shorten panel 1 by 3.14 mm where it reaches into the "
                "fold.") in says
        assert ("Fold C folds 9.42 mm off its line, towards panel 4, to go "
                "round what is folded inside it. It closes over fold B, "
                "which is off its line as well: put that right first.") \
            in says
        assert "⚠" not in _words(dlg)
    finally:
        dlg.close()
    # panel 1 that much shorter: fold C says the rest (panel 2, and then
    # panel 1 again for fold B) -- and with that done nothing is said
    dlg = _dialog(_strip([18.5 - 3.14, 18.5, 18.5, 60], (1, 1, 1)))
    try:
        dlg.cmb_base.setCurrentIndex(3)
        says = _words(dlg)
        assert says.count("off its line") == 1
        assert ("Fold C folds 3.14 mm off its line, towards panel 4, to go "
                "round what is folded inside it. To have it fold on its "
                "line, shorten panel 2 by 3.14 mm where it reaches into the "
                "fold, and then panel 1 by 3.14 mm where it reaches into "
                "fold B.") in says.split("\n")
    finally:
        dlg.close()
    dlg = _dialog(_strip([18.5 - 6.28, 18.5 - 3.14, 18.5, 60], (1, 1, 1)))
    try:
        dlg.cmb_base.setCurrentIndex(3)
        assert dlg.layout_.shift == [0.0, 0.0, 0.0]
        assert "off its line" not in _words(dlg) and "⚠" not in _words(dlg)
    finally:
        dlg.close()
    # the four panels with fold A switched to the back like the others:
    # panel 2 is 18.5 mm, and no more than that is asked of any panel
    doc = _four_panels()
    _folds(doc)[0].fold_dir = "back"
    dlg = _dialog(doc)
    try:
        dlg.cmb_base.setCurrentIndex(3)
        says = _words(dlg).split("\n")
        assert "Fold B folds 10.65 mm off its line, towards panel 3." in says
        assert ("⚠ What is folded inside fold B reaches 11.14 mm into its "
                "bend, and the fold cannot move further out of its way: its "
                "bend would not fit before fold C. Shorten panel 1 by 11.14 "
                "mm where it reaches into the fold.") in says
        assert ("⚠ Fold C closes over what is left in the way of fold B: "
                "put fold B right first.") in says
        assert dlg.layout_.shift[2] == 0.0 and "panel 2 by" not in _words(dlg)
    finally:
        dlg.close()


def test_a_net_says_what_is_in_the_way_and_stays_as_it_is_drawn(qapp):
    # three 80 mm panels drawn edge to edge and folded as a letter: the
    # flap reaches the line the cover folds on, but the edges panels share
    # are where they are
    doc = Document("net")
    doc.shapes += [_rect(x, 0, 80, 100) for x in (0, 80, 160)]
    dlg = _dialog(doc)
    try:
        for row in range(2):
            dlg.table.cellWidget(row, 3).setValue(180.0)
        lay = dlg.layout_
        assert lay.steps == [1, 2] and _near_all(lay.radius, [0.0, 1.0])
        assert lay.shift == [0.0, 0.0] and lay.stuck == [False, True]
        assert lay.lines[1][0] == (160.0, 0.0)
        says = _words(dlg)
        assert ("⚠ What is folded inside fold B reaches 3.14 mm into its "
                "bend. Shorten panel 1 by 3.14 mm where it reaches into the "
                "fold.") in says.split("\n")
        assert "off its line" not in says
    finally:
        dlg.close()


def _near_all(a, b, tol=1e-6):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def test_earlier_later_flip_and_untick(qapp):
    doc = _trifold()
    doc.shapes.append(_line((40, 0), (40, 100), "Extra", angle=90.0, order=3))
    dlg = _dialog(doc)
    try:
        labels = [f.label + f.name for f in dlg.folds]
        assert labels == ["AExtra", "BLeft fold", "CRight fold"]
        assert [f.order for f in dlg.folds] == [3, 1, 2]
        assert not dlg.btn_earlier.isEnabled()        # nothing chosen
        dlg.table.selectRow(0)
        assert dlg.btn_earlier.isEnabled() and dlg.view.selected == 0
        dlg._move(-1)                                 # A before C
        assert [f.order for f in dlg.folds] == [2, 1, 3]
        dlg._move(-1)                                 # and before B
        assert [f.order for f in dlg.folds] == [1, 2, 3]
        dlg._move(1)
        assert [f.order for f in dlg.folds] == [2, 1, 3]
        assert [dlg.table.cellWidget(r, 1).value() for r in range(3)] == \
            [2, 1, 3]
        assert [s.fold_order for s in sorted(
            _folds(doc), key=lambda s: s.name)] == [2, 1, 3]
        # sharing a step, then stepping out of it on its own
        dlg.table.cellWidget(0, 1).setValue(1)
        assert [f.order for f in dlg.folds] == [1, 1, 3]
        dlg._move(1)
        assert [f.order for f in dlg.folds] == [2, 1, 3]
        dlg._flip()
        assert dlg.folds[0].direction == -1
        assert dlg.table.cellWidget(0, 2).currentText() == "Back"
        # unticked, a fold stays flat here; the drawing isn't touched
        extra = next(s for s in _folds(doc) if s.name == "Extra")
        before = (extra.fold_dir, extra.fold_angle)
        dlg.table.item(0, 0).setCheckState(Qt.Unchecked)
        assert dlg.layout_.steps == [0, 1, 2] and dlg.layout_.n_steps == 2
        assert dlg.table.item(0, 5).text() == "flat"
        assert (extra.fold_dir, extra.fold_angle) == before
    finally:
        dlg.close()


def test_stepping_and_playing_through_the_folding(qapp):
    dlg = _dialog(_trifold())
    try:
        v = dlg.view
        assert v.t == 0.0 and dlg.slider.maximum() == 400
        dlg._step(1)
        assert v.t == 1.0 and dlg.slider.value() == 200
        dlg.slider.setValue(300)
        assert v.t == 1.5
        assert v.caption == "Step 2 of 2:  fold B to the front 180°"
        dlg._step(1)
        assert v.t == 2.0 and v.caption == "Folded"
        dlg._step(-1)
        assert v.t == 1.0
        dlg._go(0.3)
        dlg._step(-1)
        assert v.t == 0.0
        dlg._go(float("inf"))
        assert v.t == 2.0
        # play starts again from flat, and runs to the end
        dlg._toggle_play()
        assert dlg._playing and v.t == 0.0
        dlg._last_tick -= 0.2
        dlg._tick()
        assert 0.1 < v.t < 0.4
        dlg._toggle_play()                            # pause
        assert not dlg._playing
        dlg._toggle_play()
        for _ in range(40):
            dlg._last_tick -= 0.2
            dlg._tick()
        assert v.t == 2.0 and not dlg._playing
        # still folded after a setting changes
        dlg.spin_t.setValue(3.0)
        assert v.t == 2.0
    finally:
        dlg.close()


def test_clicking_a_fold_line_chooses_its_row(qapp):
    dlg = _dialog(_trifold())
    try:
        dlg.view.repaint()
        drawn = dlg.view._drawn
        assert {d[0] for d in drawn} == {0, 1}
        _f, x0, y0, x1, y1 = next(d for d in drawn if d[0] == 1)
        assert dlg.view.fold_at((x0 + x1) / 2, (y0 + y1) / 2) == 1
        assert dlg.view.fold_at(3, 3) == -1
        dlg.view.foldPicked.emit(1)
        assert dlg.table.currentRow() == 1 and dlg.selected_fold() == 1
        assert dlg.view.selected == 1
        dlg.view.foldPicked.emit(-1)
        assert dlg.selected_fold() == -1 and dlg.view.selected == -1
    finally:
        dlg.close()


def _bifold(shift=0.0, pairs=8, extra=()):
    """The strip with one fold made, and a row of loose stitch holes on
    each side of it that meet when it is folded -- or miss by ``shift``."""
    from leathercad.holes import LooseHole
    doc = _trifold()
    doc.shapes[2].fold_dir = ""                       # one fold: a bifold
    for k in range(pairs):
        doc.holes.append(LooseHole(Vec2(20 + 5 * k, 10)))
        doc.holes.append(LooseHole(Vec2(160 - (20 + 5 * k) + shift, 10)))
    for x, y in extra:
        doc.holes.append(LooseHole(Vec2(x, y)))
    return doc


def _words(dlg) -> str:
    """What the dialog says under the table, without its markup."""
    import re
    return re.sub(r"<[^>]+>", "", dlg.readout.text().replace("<br>", "\n"))


def test_holes_that_miss_are_reported(qapp):
    dlg = _dialog(_bifold(shift=1.5))
    try:
        assert "Panels 1 and 2: ● 16 are out of line, by up to 1.5 mm." \
            in _words(dlg)
        assert len(dlg.view.check.off) == 16
    finally:
        dlg.close()


def test_holes_that_line_up_are_reported_too(qapp):
    from leathercad import foldsolid
    dlg = _dialog(_bifold())
    try:
        assert "Panels 1 and 2: ● all 16 line up." in _words(dlg)
        check = dlg.view.check
        assert len(check.holes(foldsolid.MEETS)) == 16 and not check.off
    finally:
        dlg.close()
    # near enough is said with how near; a hole with nothing to meet apart
    dlg = _dialog(_bifold(shift=0.5, extra=[(40, 60)]))
    try:
        assert ("Panels 1 and 2: ● 16 line up, to within 0.5 mm; "
                "● 1 has no hole to meet.") in _words(dlg)
    finally:
        dlg.close()
    # with no holes lying on each other there is nothing to say
    dlg = _dialog(_trifold())
    try:
        assert "Stitch holes" not in _words(dlg)
        assert not dlg.view.check.state
    finally:
        dlg.close()


def test_holes_in_a_stack_of_three_are_counted_once(qapp):
    # the strip folded in three, as a zigzag, and stitched all round: its
    # three thirds come to lie on each other
    doc = _trifold()
    doc.shapes[0].stitch = StitchSettings(enabled=True, pitch_mm=5.0,
                                          inset=4.0)
    _folds(doc)[1].fold_dir = "back"
    dlg = _dialog(doc)
    try:
        check = dlg.view.check
        assert len(check.findings) == 3
        says = _words(dlg).split("Stitch holes where panels lie on each "
                                 "other: ")[1].split("\n")
        # first the holes as the view colours them, then the panels
        n = {state: len(check.holes(state)) for state in (1, 2, 3)}
        assert sum(n.values()) == len(check.state) > 60
        assert says[0].startswith(f"● {n[2]} are out of line, by up to ")
        assert f"● {n[3]} have no hole to meet." in says[0]
        assert [line.split(":")[0] for line in says[1:]] \
            == ["Panels 1 and 2", "Panels 1 and 3", "Panels 2 and 3"]
    finally:
        dlg.close()


# -- the view -----------------------------------------------------------------

def _patch(img, cx, cy, half=6):
    """The colour of a bit of a picture, on average."""
    r = g = b = n = 0
    for x in range(cx - half, cx + half + 1):
        for y in range(cy - half, cy + half + 1):
            c = img.pixelColor(x, y)
            r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
    return (r / n, g / n, b / n)


def _rgb(c):
    return (c.red(), c.green(), c.blue())


def test_the_view_paints_the_leather_and_its_fold_lines(qapp):
    from leathercad_app import foldview as fv
    from leathercad_app.preview3d import fold_lines, foldables
    doc = _trifold()
    _folds(doc)[1].fold_dir = "back"
    item = foldables(doc)[0]
    lay = fs.fold_piece(item.outline, fold_lines(item.lines))
    look = fv.Look()

    def near(c, want, tol=40):
        return (abs(c.red() - want.red()) + abs(c.green() - want.green())
                + abs(c.blue() - want.blue())) < tol

    # straight on to the front, flat: the grain side of a tan leather, with
    # a line down each fold
    img = fv.render(lay, 0.0, 600, 400, yaw=0.0, pitch=0.0, labels=False)
    cam = fv.Camera()
    cam.centre, cam.radius = lay.extent()
    cam.yaw = cam.pitch = 0.0
    ox, ax, ay, _az, oy, bx, by, _bz = cam.mapping(600, 400)

    def at(x, y):
        return (int(round(ox + ax * x + ay * y)),
                int(round(oy + bx * x + by * y)))

    front = _patch(img, *at(40, 50))
    assert math.dist(front, look.front) < 30 < math.dist(front, look.back)
    assert near(img.pixelColor(*at(80, 25)),
                fv.fold_colour(lay.folds[0]))         # fold A: to the front
    assert near(img.pixelColor(*at(160, 25)),
                fv.fold_colour(lay.folds[1]))         # fold B: to the back
    assert fv.fold_colour(lay.folds[0]) != fv.fold_colour(lay.folds[1])
    assert near(img.pixelColor(5, 5), look.ground, 5)
    # it is not one flat colour: the grain shows
    grain = {_rgb(img.pixelColor(x, at(40, 50)[1]))
             for x in range(at(20, 50)[0], at(60, 50)[0])}
    assert len(grain) > 12
    # from behind, the pale back of the leather
    back = fv.render(lay, 0.0, 600, 400, yaw=0.0, pitch=3.14159, labels=False)
    flesh = _patch(back, 300, 200)
    assert math.dist(flesh, look.back) < 50 < math.dist(flesh, look.front)
    # folded, it is a different picture
    assert fv.render(lay, 2.0, 600, 400) != fv.render(lay, 0.0, 600, 400)
    assert fv.render(lay, 0.0, 300, 200, dark=True).pixelColor(3, 3) \
        == fv.Look(True).ground
    # ... and so it is in another leather
    black = fv.render(lay, 0.0, 600, 400, yaw=0.0, pitch=0.0, labels=False,
                      leather="Black")
    assert math.dist(_patch(black, *at(40, 50)),
                     fv.LEATHERS["Black"][0]) < 30


def _shows(img, x, y, colour, reach=3, tol=45) -> bool:
    """Whether a picture has ``colour``, or near it, within ``reach``
    pixels of a spot."""
    want = (colour.red(), colour.green(), colour.blue())
    return any(math.dist(_rgb(img.pixelColor(x + dx, y + dy)), want) < tol
               for dx in range(-reach, reach + 1)
               for dy in range(-reach, reach + 1))


def test_the_view_marks_each_hole_by_whether_it_lines_up(qapp):
    from leathercad_app import foldview as fv
    # two rows that fold on to each other: the first two pairs meet, the
    # next two are 1.5 mm out, then a hole with nothing to meet -- and one
    # more that lies on no other holes at all
    holes = [(20, 10, 1.2), (140, 10, 1.2), (30, 10, 1.2), (130, 10, 1.2),
             (40, 10, 1.2), (121.5, 10, 1.2), (50, 10, 1.2), (111.5, 10, 1.2),
             (60, 40, 1.2)]
    lay = fs.fold_piece([(0, 0), (160, 0), (160, 100), (0, 100)],
                        [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                        holes=holes)
    check = lay.lineup()
    assert [check.state[k] for k in range(9)] \
        == [fs.MEETS] * 4 + [fs.OFF] * 4 + [fs.LONE]
    cam = fv.Camera()
    cam.centre, cam.radius = lay.extent()
    cam.yaw = cam.pitch = 0.0
    ox, ax, ay, _az, oy, bx, by, _bz = cam.mapping(900, 600)

    def at(x, y):                      # the pixel a point of it is in
        return (int(ox + ax * x + ay * y), int(oy + bx * x + by * y))

    look = fv.Look()
    green, red, amber = (fv.hole_colour(s)
                         for s in (fs.MEETS, fs.OFF, fs.LONE))
    assert len({green.name(), red.name(), amber.name()}) == 3
    kw = dict(yaw=0.0, pitch=0.0, labels=False)
    plain = fv.render(lay, 0.0, 900, 600, **kw)
    marked = fv.render(lay, 0.0, 900, 600, check=check, **kw)
    for k, (x, y, _dia) in enumerate(holes):
        spot = at(x, y)
        want = {fs.MEETS: green, fs.OFF: red, fs.LONE: amber}[check.state[k]]
        assert _shows(marked, *spot, want), k
        for other in (green, red, amber):
            assert not _shows(plain, *spot, other), k
            if other != want:
                assert not _shows(marked, *spot, other, reach=1), k
        # the hole itself still shows in one with nothing to meet
        dark = _shows(marked, *spot, look.ink(fv.FRONT), reach=1, tol=30)
        assert dark == (check.state[k] == fs.LONE), k
    # where the hole a red one should have met comes down, 1.5 mm from it,
    # there is a ring: red round the spot, the leather showing inside it
    scale = cam.scale(900, 600)
    for k in (4, 5, 6, 7):
        (x, y, _dia), = check.lands[k]
        cx, cy = at(x, y)
        bead = max(4.4, 1.35 * 1.2 * scale)
        r = int(round(0.5 * bead + 1.2))
        away = -1 if lay.holes[k][0] > x else 1    # the side off the hole
        assert _shows(marked, cx + away * r, cy, red, reach=1), k
        assert not _shows(plain, cx + away * r, cy, red, reach=1), k
    # folded, the holes on top show it as well, on the back of the leather
    top = fv.render(lay, 1.0, 900, 600, check=check, **kw)
    assert top != fv.render(lay, 1.0, 900, 600, **kw)
    # a hole that lies on no other holes is left as it was
    assert 8 in check.state and len(check.state) == 9
    lone = fs.fold_piece([(0, 0), (160, 0), (160, 100), (0, 100)],
                         [fs.FoldLine((80, 0), (80, 100), 180, 1)],
                         holes=holes[:1])
    assert fv.render(lone, 0.0, 300, 200, check=lone.lineup(), **kw) \
        == fv.render(lone, 0.0, 300, 200, **kw)


def test_the_hole_check_can_be_put_away_and_is_remembered(qapp, tmp_path):
    ini = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    dlg = _dialog(_bifold(shift=1.5), settings=ini)
    try:
        assert dlg.chk_holes.isChecked() and dlg.view.check_shown
        shown = dlg.view.grab().toImage()
        dlg.chk_holes.setChecked(False)
        QApplication.processEvents()
        assert not dlg.view.check_shown
        hidden = dlg.view.grab().toImage()
        assert hidden != shown
        # what it found is still there, and still said under the table
        assert len(dlg.view.check.off) == 16
        assert "16 are out of line" in _words(dlg)
        # ... and still left out after the piece is folded again
        dlg.spin_t.setValue(3.0)
        QApplication.processEvents()
        assert not dlg.view.check_shown and len(dlg.view.check.off) == 16
    finally:
        dlg.close()
    again = _dialog(_bifold(shift=1.5), settings=ini)
    try:
        assert not again.chk_holes.isChecked() and not again.view.check_shown
        again.chk_holes.setChecked(True)
        QApplication.processEvents()
        assert again.view.check_shown
        assert again.view.grab().toImage() != hidden
    finally:
        again.close()


class _Way:
    def __init__(self, direction):
        self.direction = direction


def test_marks_and_fold_lines_stand_off_every_leather(qapp):
    from PySide6.QtGui import QColor
    from leathercad_app import foldview as fv
    assert fv.DEFAULT_LEATHER in fv.LEATHERS and len(fv.LEATHERS) >= 6
    for name in fv.LEATHERS:
        look = fv.Look(leather=name)
        assert look.leather == name
        assert (look.front, look.back, look.wall) == fv.LEATHERS[name]
        for kind in (fv.FRONT, fv.BACK):
            leather = look.flat(kind)
            to_front = look.fold_colour(_Way(1), True, kind)
            to_back = look.fold_colour(_Way(-1), True, kind)
            for mark in (look.ink(kind), to_front, to_back):
                assert math.dist(_rgb(mark), leather) > 100, (name, kind)
            assert to_front != to_back
            assert look.fold_colour(_Way(1), False, kind) == look.idle
        # the line along an edge of the leather is darker than its cut
        # edge, and shows on its face
        cut = QColor(*look.wall)
        assert look.edge.lightness() < cut.lightness()
        assert math.dist(_rgb(look.edge), look.front) > 40
        assert math.dist(_rgb(fv.Look(True, name).edge), look.front) > 40
    # ... but in a dark window a dark leather has a lighter line round it,
    # which keeps the piece off the ground
    for name, lighter in (("Black", True), ("Navy", True),
                          ("Dark brown", True), ("Tan", False),
                          ("Natural", False)):
        dim = fv.Look(True, name)
        assert (dim.edge.lightness() > QColor(*dim.wall).lightness()) \
            == lighter, name
    # a dark leather takes light marks, a pale one dark marks
    assert fv.Look(leather="Black").is_dark(fv.FRONT)
    assert fv.Look(leather="Black").ink(fv.FRONT).lightness() > 150
    assert not fv.Look(leather="Natural").is_dark(fv.FRONT)
    assert fv.Look(leather="Natural").ink(fv.FRONT).lightness() < 80
    # the colour a fold has in lists and keys does not change with it
    assert fv.fold_colour(_Way(1)) == QColor(24, 104, 232)
    # a leather this version has never heard of is the usual one
    assert fv.Look(leather="Snakeskin").leather == fv.DEFAULT_LEATHER


def _lightness(tile, step=4):
    """How light a tile is on average, and how much it varies."""
    vals = []
    for x in range(0, tile.width(), step):
        for y in range(0, tile.height(), step):
            c = tile.pixelColor(x, y)
            vals.append(0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue())
    mean = sum(vals) / len(vals)
    return mean, math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))


def _jump(tile, i, j, down):
    """How much a tile changes from column (or row) ``i`` to ``j``."""
    total = 0
    for k in range(tile.width()):
        a = tile.pixelColor(i, k) if down else tile.pixelColor(k, i)
        b = tile.pixelColor(j, k) if down else tile.pixelColor(k, j)
        total += (abs(a.red() - b.red()) + abs(a.green() - b.green())
                  + abs(a.blue() - b.blue()))
    return total / tile.width()


def test_the_leather_has_a_grain_and_its_front_a_sheen(qapp):
    from leathercad import bsp
    from leathercad_app import foldview as fv
    look = fv.Look()
    last = fv._LEVELS - 1
    for kind in (fv.FRONT, fv.BACK):
        tile = look.texture(kind, 20)
        assert (tile.width(), tile.height()) == (fv._TILE, fv._TILE)
        assert _lightness(tile)[1] > 3.0          # not one flat colour
        # it joins up with itself: no line shows where it repeats
        for down in (True, False):
            usual = max(_jump(tile, i, i + 1, down) for i in (10, 100, 200))
            assert _jump(tile, fv._TILE - 1, 0, down) < 1.5 * usual
        # the fill for a face is that tile, and the pen that goes with it
        # is the colour the tile comes to
        brush, pen = look.shade(kind, 20)
        assert brush.textureImage() == tile
        assert pen.isCosmetic()
        assert math.dist(_rgb(pen.color()), _patch(tile, 128, 128, 127)) < 3
    # the more light on a face the lighter, and on the front of the leather
    # the sheen comes on top of that
    seen = [_lightness(look.texture(fv.FRONT, lv))[0]
            for lv in (0, 12, last, last + 1, last + fv._SHEEN)]
    assert seen == sorted(seen) and len(set(seen)) == 5
    assert look.texture(fv.BACK, last + fv._SHEEN) \
        == look.texture(fv.BACK, last)            # the back takes no sheen
    assert _lightness(look.texture(fv.BACK, 0))[0] \
        < _lightness(look.texture(fv.BACK, last))[0]
    # the cut edge is a plain colour
    brush, pen = look.shade(bsp.WALL, last)
    assert brush.style() == Qt.SolidPattern
    assert _rgb(brush.color()) == look.wall == _rgb(pen.color())
    # the maps the textures are made from are made once
    assert fv.texture_maps() is fv.texture_maps()
    assert set(fv.texture_maps()) == {"grain", "relief", "fibres", "front",
                                      "back"}


def _unfilled(img, scene, cam):
    """The pixels left the colour of the ground (which must have no green
    in it) though the middle of each lies inside a face that was painted:
    ``[(x, y)]``."""
    from leathercad import bsp
    w, h = img.width(), img.height()
    bits = bytes(img.constBits())
    stride = img.bytesPerLine()
    green = [bits[y * stride + 1:y * stride + 4 * w:4] for y in range(h)]
    spots = []
    for y in range(1, h - 1):
        row = green[y]
        lo = w - len(row.lstrip(b"\0"))
        hi = len(row.rstrip(b"\0"))
        x = row.find(0, lo, hi)
        while x >= 0:
            if 0 < x < w - 1:
                round_it = sum(1 for yy in (y - 1, y, y + 1)
                               for xx in (x - 1, x, x + 1) if green[yy][xx])
                if round_it >= 5:
                    spots.append((x, y))
            x = row.find(0, x + 1, hi)
    if not spots:
        return []
    ox, ax, ay, az, oy, bx, by, bz = cam.mapping(w, h)
    eye = cam.axes()[2]
    faces = []
    for cell in bsp.back_to_front(scene.tree, *eye):
        for f in cell.faces:
            n = f.plane
            if f.kind != bsp.INNER \
                    and n[0] * eye[0] + n[1] * eye[1] + n[2] * eye[2] > 1e-6:
                faces.append([(ox + ax * x + ay * y + az * z,
                               oy + bx * x + by * y + bz * z)
                              for x, y, z in f.pts])
    out = []
    for x, y in spots:
        cx, cy = x + 0.5, y + 0.5
        for pts in faces:
            sign = 0.0
            x0, y0 = pts[-1]
            for x1, y1 in pts:
                c = (x1 - x0) * (cy - y0) - (y1 - y0) * (cx - x0)
                if abs(c) > 1e-9:
                    if sign == 0.0:
                        sign = c
                    elif (c > 0.0) != (sign > 0.0):
                        break
                x0, y0 = x1, y1
            else:
                out.append((x, y))
                break
    return out


def test_no_pixel_of_a_face_is_left_unpainted(qapp):
    # The faces are filled without smoothing, in the pieces the model is
    # cut into; where a piece was cut and the one beside it was not, the
    # odd pixel between them would show the ground. Each of these views
    # does, without the line that is laid under such seams.
    from PySide6.QtGui import QColor, QImage, QPainter
    from leathercad_app import foldview as fv
    look = fv.Look()
    look.ground = QColor(255, 0, 255)
    w, h = 900, 620
    for doc, t, yaw, pitch in ((_wallet(right="back"), 0.0, -0.45, 0.3),
                               (_wallet(right="back"), 0.8, -0.45, 0.3),
                               (_tray(), 1.0, 2.6916, 0.8217),
                               (_tray(), 0.8, 0.3354, 1.213)):
        dlg = _dialog(doc)
        try:
            lay = dlg.layout_
            scene = fv.Scene(lay, lay.n_steps * t)
            cam = fv.Camera()
            cam.centre, cam.radius = lay.extent()
            cam.yaw, cam.pitch, cam.zoom = yaw, pitch, 1.7
            img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            p = QPainter(img)
            fv.paint_scene(p, w, h, scene, cam, look, labels=False)
            p.end()
            assert _unfilled(img, scene, cam) == []
        finally:
            dlg.close()


def test_turning_sliding_and_zooming_the_view_with_the_mouse(qapp):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    dlg = _dialog(_trifold())
    try:
        v = dlg.view
        v.repaint()
        yaw, pitch = v.cam.yaw, v.cam.pitch
        mid = QPoint(v.width() // 2, v.height() // 2)
        QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, mid)
        QTest.mouseMove(v, mid + QPoint(60, 30))
        QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier,
                           mid + QPoint(60, 30))
        assert v.cam.yaw != yaw and v.cam.pitch != pitch and v.follow
        assert dlg.selected_fold() == -1              # a drag picks nothing
        QTest.mousePress(v, Qt.RightButton, Qt.NoModifier, mid)
        QTest.mouseMove(v, mid + QPoint(25, 10))
        QTest.mouseRelease(v, Qt.RightButton, Qt.NoModifier,
                           mid + QPoint(25, 10))
        assert (v.cam.pan_x, v.cam.pan_y) == (25.0, 10.0) and not v.follow
        zoom = v.cam.zoom
        v.wheelEvent(QWheelEvent(QPointF(mid), QPointF(v.mapToGlobal(mid)),
                                 QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                 Qt.NoModifier, Qt.NoScrollPhase, False))
        assert v.cam.zoom > zoom
        QTest.mouseDClick(v, Qt.LeftButton, Qt.NoModifier, mid)
        assert v.follow and v.cam.zoom == 1.0 and v.cam.pan_x == 0.0
        # a click on a fold line picks it; over it, the pointer says so
        v.look_from("front")
        v.repaint()
        _f, x0, y0, x1, y1 = next(d for d in v._drawn if d[0] == 0)
        at = QPoint(int((x0 + x1) / 2), int((y0 + y1) / 2))
        QTest.mouseMove(v, at)
        assert v.hover == 0
        QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, at)
        assert dlg.selected_fold() == 0 and v.selected == 0
        QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))
        assert dlg.selected_fold() == -1
        # the keys, with the view clicked
        QTest.keyClick(v, Qt.Key_Right)
        assert v.t == 1.0
        QTest.keyClick(v, Qt.Key_End)
        assert v.t == 2.0
        QTest.keyClick(v, Qt.Key_Home)
        assert v.t == 0.0
    finally:
        dlg.close()


def test_the_view_follows_the_model_until_it_is_moved_by_hand(qapp):
    dlg = _dialog(_trifold())
    try:
        v = dlg.view
        flat = v.cam.radius
        dlg._go(2.0)
        assert v.follow and v.cam.radius < 0.6 * flat   # closed up: zoomed
        v.cam.zoom, v.follow = 2.0, False               # as the wheel does
        dlg._go(0.0)
        assert v.cam.radius < 0.6 * flat and v.cam.zoom == 2.0
        v.fit()
        assert v.follow and abs(v.cam.radius - flat) < 1.0
        assert v.cam.zoom == 1.0
        for where, pitch in (("front", 0.0), ("side", 1.5708),
                             ("back", 3.14159)):
            v.look_from(where)
            assert abs(v.cam.pitch - pitch) < 1e-3 and v.cam.yaw == 0.0
    finally:
        dlg.close()


# -- in the app ---------------------------------------------------------------

def _window(qapp, doc, tmp_path, monkeypatch):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(win, "_settings",
                        lambda: QSettings(ini, QSettings.Format.IniFormat))
    win.canvas.rebuild()
    return win


def test_changes_go_to_the_drawing_as_one_undo_step(qapp, tmp_path,
                                                    monkeypatch):
    win = _window(qapp, _trifold(), tmp_path, monkeypatch)
    try:
        win._assembly_preview()
        dlg = win._assembly_dlg
        assert dlg.isVisible() and dlg.canvas is win.canvas
        assert not win.history.can_undo() and not win._unsaved_changes
        dlg.table.cellWidget(0, 2).setCurrentIndex(1)     # A to the back
        dlg.table.cellWidget(1, 3).setValue(90.0)
        dlg.table.cellWidget(1, 1).setValue(1)
        a, b = _folds(win.doc)
        assert (a.fold_dir, b.fold_angle, b.fold_order) == ("back", 90.0, 1)
        assert not win.history.can_undo()                 # not yet: a run
        dlg._flush_commit()
        assert win.history.can_undo() and win._unsaved_changes
        win.undo()                                        # all of it, once
        a, b = _folds(win.doc)
        assert (a.fold_dir, b.fold_angle, b.fold_order) == ("front", 180.0, 2)
        assert not win.history.can_undo()
        # the preview follows the drawing
        dlg.reload()
        assert [(f.direction, f.angle, f.order) for f in dlg.folds] == \
            [(1, 180.0, 1), (1, 180.0, 2)]
        assert dlg.table.cellWidget(0, 2).currentText() == "Front"
        assert dlg.folds[0].key is a
        # asking for the preview again brings the same one forward
        win._assembly_preview()
        assert win._assembly_dlg is dlg
        dlg.close()
    finally:
        win._unsaved_changes = False
        win.hide()


def test_the_preview_keeps_up_with_the_drawing(qapp, tmp_path, monkeypatch):
    win = _window(qapp, _trifold(), tmp_path, monkeypatch)
    try:
        win._assembly_preview()
        dlg = win._assembly_dlg
        dlg.table.selectRow(1)
        dlg.table.item(0, 0).setCheckState(Qt.Unchecked)
        dlg._go(1.0)
        # a third fold line drawn while the preview is open
        win.doc.shapes.append(_line((200, 0), (200, 100), "New fold",
                                    angle=90.0))
        win.canvas.rebuild()
        assert dlg._refresh.isActive()                    # noticed
        dlg._refresh.stop()
        dlg.reload()
        assert [f.name for f in dlg.folds] == ["Left fold", "Right fold",
                                               "New fold"]
        assert dlg.table.rowCount() == 3
        assert dlg.selected_fold() == 1                   # still chosen
        assert dlg.table.item(0, 0).checkState() == Qt.Unchecked
        # nothing to redo when the drawing changes somewhere else
        layout = dlg.layout_
        win.doc.shapes.append(_rect(500, 500, 10, 10, "Elsewhere"))
        win.canvas.rebuild()
        dlg._refresh.stop()
        dlg.reload()
        assert dlg.layout_ is layout
        # a fold line that is moved in the drawing is followed
        from leathercad_app.items import ShapeItem
        win.canvas.scene_obj.clearSelection()
        for it in win.canvas.scene_obj.items():
            if isinstance(it, ShapeItem) and it.model.name == "Right fold":
                it.setSelected(True)
        win.canvas.nudge_selected(-5.0, 0.0)
        assert dlg._refresh.isActive()
        dlg._refresh.stop()
        dlg.reload()
        assert dlg.layout_ is not layout
        assert [f.a for f in dlg.folds] == [(80.0, 0.0), (155.0, 0.0),
                                            (200.0, 0.0)]
        assert dlg.layout_.lines[1][0] == (155.0, 0.0)
        assert dlg.selected_fold() == 1                   # still chosen
        assert dlg.table.item(0, 0).checkState() == Qt.Unchecked
        # and when the piece goes, the preview says how to start again
        win.doc.shapes[:] = [s for s in win.doc.shapes if s.is_fold_line]
        win.canvas.rebuild()
        dlg._refresh.stop()
        dlg.reload()
        assert dlg.current is None and dlg.table.rowCount() == 0
        assert dlg.view.scene is None
        assert "Mark as fold line" in dlg.view.caption
        assert not dlg.btn_play.isEnabled()
        dlg.close()
    finally:
        win._unsaved_changes = False
        win.hide()


def test_the_preview_opens_on_the_selected_piece(qapp, tmp_path, monkeypatch):
    doc = _trifold()
    doc.shapes.append(_rect(0, 120, 160, 20, "Strap"))
    doc.shapes.append(_line((40, 120), (40, 140), "Buckle fold"))
    win = _window(qapp, doc, tmp_path, monkeypatch)
    try:
        from leathercad_app.items import ShapeItem
        for it in win.canvas.scene_obj.items():
            if isinstance(it, ShapeItem) and it.model.name == "Strap":
                it.setSelected(True)
        win._assembly_preview()
        dlg = win._assembly_dlg
        assert dlg.cmb_piece.isVisibleTo(dlg) and dlg.cmb_piece.count() == 2
        assert dlg.current.title == "Strap"
        assert [f.name for f in dlg.folds] == ["Buckle fold"]
        dlg.cmb_piece.setCurrentIndex(0)
        assert dlg.current.title == "Tri-fold" and len(dlg.folds) == 2
        dlg.close()
    finally:
        win._unsaved_changes = False
        win.hide()


def test_nothing_to_fold_says_how_to_start(qapp, tmp_path, monkeypatch):
    doc = Document("plain")
    doc.shapes.append(_rect(0, 0, 80, 50, "Panel"))
    win = _window(qapp, doc, tmp_path, monkeypatch)
    said = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.append(a[2]))
    try:
        win._assembly_preview()
        assert len(said) == 1 and "Mark as fold line" in said[0]
        assert getattr(win, "_assembly_dlg", None) is None
    finally:
        win.hide()


def _half_typed(box, text):
    """Type ``text`` into a box over what it shows, and go no further."""
    from PySide6.QtTest import QTest
    box.setFocus()
    box.lineEdit().selectAll()
    QTest.keyClicks(box.lineEdit(), text)
    QApplication.processEvents()


def _escape(dlg, box):
    from PySide6.QtTest import QTest
    QTest.keyClick(box.lineEdit(), Qt.Key_Escape)
    QApplication.processEvents()


@pytest.mark.parametrize("col, typed", [(3, "9"), (4, "7"), (1, "1")],
                         ids=["angle", "radius", "step"])
def test_a_number_half_typed_when_esc_shuts_the_window_is_let_go(
        qapp, tmp_path, monkeypatch, col, typed):
    win = _window(qapp, _trifold(), tmp_path, monkeypatch)
    try:
        win.show()
        was = win.doc.to_dict()
        win._assembly_preview()
        dlg = win._assembly_dlg
        dlg.activateWindow()
        box = dlg.table.cellWidget(1, col)
        _half_typed(box, typed)
        _escape(dlg, box)
        a, b = _folds(win.doc)
        assert (b.fold_angle, b.fold_radius, b.fold_order) == \
            (180.0, -1.0, 2)
        assert win.doc.to_dict() == was
        assert win.history._index == 0 and not win.history.can_undo()
    finally:
        win._unsaved_changes = False
        win.hide()


def test_esc_keeps_what_was_changed_before_and_lets_go_what_is_half_typed(
        qapp, tmp_path, monkeypatch):
    win = _window(qapp, _trifold(), tmp_path, monkeypatch)
    try:
        win.show()
        win._assembly_preview()
        dlg = win._assembly_dlg
        dlg.activateWindow()
        dlg.table.cellWidget(0, 2).setCurrentIndex(1)     # A to the back
        dlg.spin_r.setValue(3.0)
        box = dlg.table.cellWidget(1, 3)
        _half_typed(box, "45")
        _escape(dlg, box)
        a, b = _folds(win.doc)
        assert (a.fold_dir, b.fold_angle) == ("back", 180.0)
        # the change made is one undo step, and only that
        assert win.history._index == 1
        win.undo()
        a, b = _folds(win.doc)
        assert (a.fold_dir, b.fold_angle) == ("front", 180.0)
        assert not win.history.can_undo()
    finally:
        win._unsaved_changes = False
        win.hide()

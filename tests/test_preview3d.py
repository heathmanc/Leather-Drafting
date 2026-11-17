"""The 3D assembly preview: what in a drawing folds, the dialog's table of
fold lines, the view, and its changes going back to the drawing."""

import math
import os

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


def _wallet():
    """The fold-over wallet template with its three folds made."""
    from leathercad.templates import fold_over_wallet
    doc = fold_over_wallet()
    order = 0
    for sh in doc.shapes:
        if sh.name.endswith("fold"):
            order += 1
            sh.fold_dir, sh.fold_angle, sh.fold_order = "front", 180.0, order
    assert order == 3
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


def test_holes_that_miss_are_reported(qapp):
    doc = _trifold()
    doc.shapes[2].fold_dir = ""                       # one fold: a bifold
    from leathercad.holes import LooseHole
    for k in range(8):
        doc.holes.append(LooseHole(Vec2(20 + 5 * k, 10)))
        doc.holes.append(LooseHole(Vec2(160 - (20 + 5 * k) + 1.5, 10)))
    dlg = _dialog(doc)
    try:
        assert "16 of 16 holes are off, by up to 1.5 mm" in dlg.readout.text()
        assert len(dlg.view.bad) == 16
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
        # the line along an edge of the leather shows on its cut edge
        assert math.dist(_rgb(look.edge), look.wall) > 60
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
    for doc, t, yaw, pitch in ((_wallet(), 0.0, -0.45, 0.3),
                               (_wallet(), 0.8, -0.45, 0.3),
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

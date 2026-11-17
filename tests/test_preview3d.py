"""The 3D assembly preview: what in a drawing folds, the dialog's table of
fold lines, the view, and its changes going back to the drawing."""

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
    assert len(f.holes) > 100 and {h[2] for h in f.holes} == {1.2}
    lines = fold_lines(f.lines)
    assert [(ln.label, ln.name, ln.angle, ln.direction, ln.order)
            for ln in lines] == [("A", "Left fold", 180.0, 1, 1),
                                 ("B", "Right fold", 180.0, 1, 2)]
    assert lines[0].key is f.lines[0]
    assert foldables(Document("empty")) == []


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

    # straight on to the front, flat: tan leather with a line down each fold
    img = fv.render(lay, 0.0, 600, 400, yaw=0.0, pitch=0.0, labels=False)
    cam = fv.Camera()
    cam.centre, cam.radius = lay.extent()
    cam.yaw = cam.pitch = 0.0
    ox, ax, ay, _az, oy, bx, by, _bz = cam.mapping(600, 400)

    def pixel(x, y):
        return img.pixelColor(int(round(ox + ax * x + ay * y)),
                              int(round(oy + bx * x + by * y)))

    assert near(pixel(40, 50), look.shade(fv.FRONT, 23)[0].color(), 60)
    assert near(pixel(80, 25), look.to_front)         # fold A: to the front
    assert near(pixel(160, 25), look.to_back)         # fold B: to the back
    assert near(img.pixelColor(5, 5), look.ground, 5)
    # from behind, the pale back of the leather
    back = fv.render(lay, 0.0, 600, 400, yaw=0.0, pitch=3.14159, labels=False)
    assert near(back.pixelColor(300, 200), look.shade(fv.BACK, 23)[0].color(),
                60)
    # folded, it is a different picture
    assert fv.render(lay, 2.0, 600, 400) != fv.render(lay, 0.0, 600, 400)
    assert fv.render(lay, 0.0, 300, 200, dark=True).pixelColor(3, 3) \
        == fv.Look(True).ground


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

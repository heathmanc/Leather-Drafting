"""Pieces put together in the 3D assembly preview: what the window lists
to show, how a stack gets from the drawing to the model, the Pieces tab
and what each thing on it does to the drawing, the report of a stack
lying flat, and undo."""

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from leathercad import assembly as asm  # noqa: E402
from leathercad import foldsolid as fs  # noqa: E402
from leathercad import sheets as sh  # noqa: E402
from leathercad import templates  # noqa: E402
from leathercad.document import Document  # noqa: E402
from leathercad.generators import bifold_wallet  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import PathShape, Polygon, Rectangle  # noqa: E402
from leathercad.shapes import Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402

POCKETS = ("pocket backing", "pocket 3", "pocket 2", "pocket 1 (front)")
# the bifold's ten pieces as the window lists them: the top of the pile
# first, the shell they lie on last
ROWS = ["Right pocket 1 (front)", "Right pocket 2", "Right pocket 3",
        "Right pocket backing", "Left pocket 1 (front)", "Left pocket 2",
        "Left pocket 3", "Left pocket backing", "Bill pocket liner",
        "Outer shell"]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def mm(qapp):
    """Lengths in millimetres, whatever a test before this one chose."""
    from leathercad_app import units
    units.set_current("mm")
    yield units
    units.set_current("mm")


def _piece(name, w, h, at=(0.0, 0.0), r=0.0, **kw):
    return Rectangle(name=name, width=w, height=h, corner_radius=r,
                     layer="Cut", transform=Transform(x=at[0], y=at[1]),
                     **kw)


_ELL = [(-40, -25), (40, -25), (40, 5), (10, 5), (10, 25), (-40, 25)]


def _ell(at=(400.0, 300.0)):
    """A pocket drawn with the polygon tool: an L 80 x 50 mm, its notch
    at the top right."""
    return Polygon(name="L pocket", points=[Vec2(*p) for p in _ELL],
                   close_path=True, layer="Cut",
                   transform=Transform(x=at[0], y=at[1]))


def _line(a, b, name="", way="front", angle=180.0):
    ln = PathShape(points=[Vec2(*a), Vec2(*b)], close_path=False, name=name,
                   layer="Score")
    ln.fold_dir, ln.fold_angle = way, angle
    return ln


def _drawing(*shapes):
    doc = Document("pieces")
    for s in shapes:
        doc.add_shape(s)
    return doc


def _apart(doc):
    """A drawing as its pieces are when nothing is put together: no
    place and no thickness on any of them (whatever a generator or a
    template gives them)."""
    for s in doc.shapes:
        s.placed, s.thickness, s.leather = None, 0.0, ""
    return doc


def _bifold_drawn():
    """The generated bifold as it is drawn: eleven shapes in a row."""
    doc = _apart(_drawing(*bifold_wallet()))
    return doc, {s.name: s for s in doc.shapes}


def _bifold():
    """The generated bifold put together: the shell at the bottom, turned
    over; the liner on it, centred, bottoms level; then on each side the
    backing and pockets 3, 2, 1, level with the bottom and with that side
    of the shell. The shell is 1.6 mm, the rest 0.8 mm."""
    doc, by = _bifold_drawn()
    shell, liner = by["Outer shell"], by["Bill pocket liner"]
    key = asm.put_on(doc, shell, thickness=1.6)
    asm.turn_over(doc, shell)
    asm.put_on(doc, liner, key, thickness=0.8)
    asm.line_up(doc, liner, "centre")
    asm.line_up(doc, liner, "bottom")
    for side in ("Left", "Right"):
        for name in POCKETS:
            piece = by[f"{side} {name}"]
            asm.put_on(doc, piece, key, thickness=0.8)
            asm.line_up(doc, piece, side.lower())
            asm.line_up(doc, piece, "bottom")
    return doc, by


def _card_holder():
    """The Card holder template put together: the back panel at the
    bottom, turned over, the pocket on it with bottoms level; 1.2 mm
    each."""
    doc = _apart(templates.card_holder())
    panel, pocket = doc.shapes
    key = asm.put_on(doc, panel, thickness=1.2)
    asm.turn_over(doc, panel)
    asm.put_on(doc, pocket, key, thickness=1.2)
    asm.line_up(doc, pocket, "centre")
    asm.line_up(doc, pocket, "bottom")
    return doc


def _slim():
    """The Slim card holder template put together as it is drawn: three
    pockets on the back panel, 1.2 mm each."""
    doc = _apart(templates.slim_card_holder())
    key = asm.put_on(doc, doc.shapes[0], thickness=1.2)
    for s in doc.shapes[1:]:
        asm.put_on(doc, s, key, thickness=1.2)
    return doc


def _panel_and_pocket():
    """A 120 x 90 panel with a fold line 10 mm right of its middle, and
    an L-shaped pocket drawn apart from it and put on it, level with its
    left and bottom edges."""
    panel = _piece("Panel", 120, 90)
    pocket = _ell()
    doc = _drawing(panel, pocket, _line((10, -45), (10, 45), "Panel fold"))
    key = asm.put_on(doc, panel, thickness=2.0)
    asm.put_on(doc, pocket, key, thickness=1.0)
    return doc, panel, pocket


def _dialog(doc, **kw):
    from leathercad_app.preview3d import FoldPreviewDialog
    dlg = FoldPreviewDialog(doc=doc, **kw)
    dlg.show()
    QApplication.processEvents()
    return dlg


def _window(qapp, doc, tmp_path, monkeypatch):
    """A main window on a drawing, its settings in a file of the test's
    own."""
    from leathercad_app.mainwindow import MainWindow
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(MainWindow, "_settings",
                        lambda self: QSettings(ini,
                                               QSettings.Format.IniFormat))
    win = MainWindow(doc)
    win.canvas.rebuild()
    return win


def _shut(win):
    dlg = getattr(win, "_assembly_dlg", None)
    if dlg is not None:
        dlg.close()
    win._unsaved_changes = False
    win.hide()


def _named(doc, name):
    return next(s for s in doc.shapes if s.name == name)


def _words(dlg) -> str:
    """What the dialog says in its report, without its markup."""
    import re
    return re.sub(r"<[^>]+>", "", dlg.readout.text().replace("<br>", "\n"))


def _lines(dlg):
    return [line for line in _words(dlg).split("\n") if line]


def _names(dlg):
    return [r.name for r in dlg.pieces.rows()]


def _lies(dlg):
    return {r.name: r.lies for r in dlg.pieces.rows()}


def _row(dlg, name):
    """The row of the Pieces table a piece is in."""
    panel = dlg.pieces
    return next(k for k, r in enumerate(panel._rows)
                if r is not None and r.name == name)


def _tick(dlg, name, on=True):
    dlg.pieces.pieces.item(_row(dlg, name), 0).setCheckState(
        Qt.Checked if on else Qt.Unchecked)


def _choose(dlg, *names):
    by = {r.name: r.key for r in dlg.pieces.rows()}
    dlg.pieces.choose([by[n] for n in names])


def _near(values, abs=1e-9):
    return pytest.approx(values, abs=abs)


def _rgb(c):
    return (c.red(), c.green(), c.blue())


def _patch(img, cx, cy, half=4):
    """The colour of a bit of a picture, on average."""
    r = g = b = n = 0
    for x in range(int(cx) - half, int(cx) + half + 1):
        for y in range(int(cy) - half, int(cy) + half + 1):
            c = img.pixelColor(x, y)
            r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
    return (r / n, g / n, b / n)


def _seen(dlg, point, half=4):
    """The colour the view shows at a point of the model (over a patch
    ``half`` pixels each way), and the pixel it is at."""
    view = dlg.view
    view.repaint()
    img = view.grab().toImage()
    w, h = view.width(), view.height()
    ox, ax, ay, az, oy, bx, by, bz = view.cam.mapping(w, h)
    x = ox + ax * point[0] + ay * point[1] + az * point[2]
    y = oy + bx * point[0] + by * point[1] + bz * point[2]
    k = img.width() / w
    return _patch(img, x * k, y * k, half), (x, y)


# -- what there is to show ----------------------------------------------------

def test_the_show_box_lists_stacks_then_what_folds_then_the_other_pieces(
        qapp, mm):
    from leathercad_app.preview3d import foldables, showables
    doc, by = _bifold()
    strap = doc.add_shape(_piece("Strap", 160, 20, at=(0, 300), r=4))
    doc.add_shape(_line((-40, 285), (-40, 315), "Buckle fold", way="back"))
    doc.add_shape(_piece("Tab", 30, 20, at=(300, 300)))
    doc.add_shape(_piece("", 80, 50, at=(400, 300)))
    was = doc.to_dict()
    found = showables(doc)
    assert [(f.kind, f.title) for f in found] == [
        ("stack", "Outer shell + 9 pieces"), ("piece", "Strap"),
        ("plain", "Tab"), ("plain", "Piece 80 × 50 mm")]
    stack = found[0]
    assert stack.key == by["Outer shell"].placed.of and stack.stack.main.key \
        == by["Outer shell"].shape_id
    assert found[1].key == strap.shape_id
    assert [s.name for s in found[1].lines] == ["Buckle fold"]
    # what folds is what it was: the shell is still one of them, with
    # its spine fold, and a piece no fold line crosses is none
    assert [(f.kind, f.title) for f in foldables(doc)] == [
        ("piece", "Outer shell"), ("piece", "Strap")]
    # a plain piece is read in full only when it comes to be shown
    tab = found[2]
    assert tab.outline and tab.cutouts == [] and tab.holes == []
    assert tab.signature()[:2] == ("plain", tab.key)
    # a second stack is listed after the first; one piece is its own name
    asm.put_on(doc, _named(doc, "Tab"))
    found = showables(doc)
    assert [(f.kind, f.title) for f in found] == [
        ("stack", "Outer shell + 9 pieces"), ("stack", "Tab"),
        ("piece", "Strap"), ("plain", "Piece 80 × 50 mm")]
    asm.take_off(doc, _named(doc, "Tab"))
    assert doc.to_dict() == was                   # reading writes nothing
    assert showables(Document("empty")) == []
    # panels drawn edge to edge are a net, and each is a piece as well
    net = _drawing(_piece("Base", 60, 40, at=(30, 20)),
                   _piece("Side", 30, 40, at=(-15, 20)),
                   _piece("Apart", 20, 20, at=(300, 300)))
    assert [(f.kind, f.title) for f in showables(net)] == [
        ("net", "2 panels that share edges"), ("plain", "Base"),
        ("plain", "Side"), ("plain", "Apart")]


# -- from the drawing to the model --------------------------------------------

def test_a_stack_is_built_as_its_main_piece_is_drawn_however_it_lies(mm):
    from leathercad_app import stackmodel as sm
    doc, panel, pocket = _panel_and_pocket()
    asm.line_up(doc, pocket, "left")
    asm.line_up(doc, pocket, "bottom")

    def built():
        stack, = asm.stacks(doc)
        parts = sm.lay_out(stack, 2.0)
        fold, = sm.stack_folds(stack)
        sheet, = parts.sheets
        return stack, parts, fold, sheet

    # as it is drawn: the bench is the frame, the pocket on the front
    stack, parts, fold, sheet = built()
    assert (parts.turned, parts.grain) == (False, 1)
    assert (sheet.side, sheet.grain) == (1, 1)
    assert parts.keys == [panel.shape_id, pocket.shape_id]
    assert parts.names == ["Panel", "L pocket"]
    assert fs.pc.bounds(parts.outline) == _near((-60, -45, 60, 45))
    assert fs.pc.bounds(sheet.ring) == _near((-60, -45, 20, 5))
    assert (fold.a, fold.b, fold.direction) == ((10, -45), (10, 45), 1)
    assert (fold.label, fold.name, fold.key.name) == ("A", "Panel fold",
                                                      "Panel fold")
    assert (parts.thickness, sheet.thickness) == (2.0, 1.0)
    # turned over like a page, the panel lies grain down with its fold
    # line 10 mm left of the middle of the bench and the pocket still at
    # the left of it ...
    asm.turn_over(doc, panel)
    stack, parts, fold, sheet = built()
    assert stack.main.mirrored and not stack.main.grain_up
    assert stack.main.folds[0].a == _near((-10, -45))
    assert stack.piece(pocket.shape_id).box == _near((-60, -45, 20, 5))
    # ... and the model is built on the bench mirrored back: the panel
    # as it is drawn, its fold to the front as it is marked, the pocket
    # on its back at the right, and all of it seen turned like a page
    assert (parts.turned, parts.grain) == (True, 1)
    assert (sheet.side, sheet.grain) == (-1, -1)
    assert fs.pc.bounds(sheet.ring) == _near((-20, -45, 60, 5))
    assert fs.pc.area(sheet.ring) > 0                # still the same way round
    assert (fold.a, fold.b, fold.direction) == ((10, -45), (10, 45), 1)
    assert parts.from_bench == (-1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    assert parts.to_bench((60.0, 5.0)) == (-60.0, 5.0)
    assert parts.number(pocket.shape_id) == 1 and parts.number("x") == -1
    # drawn as seen from its flesh side and laid grain up: mirrored too,
    # and the front of the model is the panel's flesh side
    asm.turn_over(doc, panel)
    asm.mirror(doc, panel)
    stack, parts, fold, sheet = built()
    assert stack.main.mirrored and stack.main.grain_up
    assert (parts.turned, parts.grain) == (True, -1)
    assert (sheet.side, sheet.grain) == (-1, -1)
    # ... and that, turned over: as drawn again, flesh side up
    asm.turn_over(doc, panel)
    stack, parts, fold, sheet = built()
    assert (parts.turned, parts.grain) == (False, -1)
    assert (sheet.side, sheet.grain) == (1, 1)
    asm.mirror(doc, panel)
    asm.turn_over(doc, panel)
    # a piece under the main piece is on its other face, and one turned
    # over shows its flesh
    asm.lower(doc, pocket)
    asm.turn_over(doc, pocket)
    stack, parts, fold, sheet = built()
    assert [p.name for p in stack.pieces] == ["L pocket", "Panel"]
    assert (parts.turned, sheet.side, sheet.grain) == (False, -1, -1)
    assert parts.keys == [panel.shape_id, pocket.shape_id]
    # a fold marked to the back folds to the back, whichever way it lies
    doc.shapes[2].fold_dir = "back"
    asm.turn_over(doc, panel)
    assert built()[2].direction == -1
    # a piece with no thickness of its own is as thick as the usual one,
    # and nothing is written to it
    pocket.thickness = 0.0
    assert sm.lay_out(asm.stacks(doc)[0], 1.5).sheets[0].thickness == 1.5
    assert pocket.thickness == 0.0


def test_the_pieces_of_a_stack_are_laid_nearest_the_main_piece_first(mm):
    from leathercad_app import stackmodel as sm
    doc, by = _bifold()
    stack, = asm.stacks(doc)
    parts = sm.lay_out(stack, 2.0)
    assert parts.names == ROWS[::-1] and parts.turned
    assert parts.keys == [by[n].shape_id for n in ROWS[::-1]]
    assert all((s.side, s.grain) == (-1, -1) for s in parts.sheets)
    model = sm.build(parts, sm.stack_folds(stack))
    # the stack folds along the shell's spine fold, the pieces with it
    assert isinstance(model, fs.Layout) and len(model.sheets) == 9
    assert model.name == "Outer shell" and model.grain == 1
    assert model.key == by["Outer shell"].shape_id
    assert [round(s.lo, 6) for s in model.sheets] == [
        0.0, 0.8, 1.6, 2.4, 3.2, 0.8, 1.6, 2.4, 3.2]
    assert model.notes[0].kind == "thick"
    assert model.notes[0].a == _near(5.6)
    assert model.n_steps == 1 and model.layout is model
    assert model.radius == _near([4.0]) and model.used == _near([4.8 *
                                                                 math.pi])
    # the left stack is at the left of the bench: at the right of the
    # shell as it is drawn, which is how the model is built
    left = parts.sheets[1]
    assert left.name == "Left pocket backing"
    assert fs.pc.bounds(left.ring)[2] == _near(99.6)
    assert stack.piece(left.key).box[0] == _near(-99.6)
    # the shell's own 142 holes, and no other piece's
    assert len(model.holes) == 142 and set(model.hole_sheet) == {0}
    # with the pieces taken off again it is the shell's own layout, which
    # folds as a piece shown alone does
    asm.take_off(doc, [by[n] for n in ROWS[:-1]])
    stack, = asm.stacks(doc)
    alone = sm.build(sm.lay_out(stack, 2.0), sm.stack_folds(stack))
    assert isinstance(alone, fs.Layout) and alone.n_steps == 1
    assert alone.thickness == 1.6 and alone.grain == 1
    assert len(alone.holes) == 142


def test_a_main_piece_drawn_from_its_flesh_side_shows_flesh_on_its_front(
        qapp, mm):
    from leathercad_app import foldview as fv
    from leathercad_app import stackmodel as sm
    doc, panel, pocket = _panel_and_pocket()
    stack, = asm.stacks(doc)
    plain = sm.build(sm.lay_out(stack, 2.0))
    scene = fv.Scene(plain, 0.0)
    assert plain.grain == 1 and scene.grain == 1
    assert scene.face_of(0, True) == fv.FRONT
    assert scene.face_of(0, False) == fv.BACK
    asm.mirror(doc, panel)
    stack, = asm.stacks(doc)
    parts = sm.lay_out(stack, 2.0)
    model = sm.build(parts)
    assert parts.grain == -1 and model.grain == -1
    scene = fv.Scene(model, 0.0)
    assert scene.grain == -1
    assert scene.face_of(0, True) == fv.BACK
    assert scene.face_of(0, False) == fv.FRONT
    assert scene.face_of(1, False) == fv.FRONT        # the pocket: grain up
    # the faces of the main piece's cells are painted the other way
    # round, the pocket's as they were
    kinds = {}
    for cell in fv.bsp.back_to_front(scene.tree, 0.0, 0.0, 1.0):
        for f in cell.faces:
            if f.kind in (fv.FRONT, fv.BACK):
                kinds.setdefault((sh.sheet_of(cell.tag), f.plane[2] > 0),
                                 set()).add(f.kind)
    assert kinds == {(0, True): {fv.BACK}, (0, False): {fv.FRONT},
                     (1, True): {fv.BACK}, (1, False): {fv.FRONT}}
    # a stack of one piece says it as well, on its layout
    asm.take_off(doc, pocket)
    stack, = asm.stacks(doc)
    alone = sm.build(sm.lay_out(stack, 2.0), sm.stack_folds(stack))
    assert isinstance(alone, fs.Layout) and alone.grain == -1
    look = fv.Look()
    # seen from above the bench (the model turned like a page) it is the
    # panel's grain that shows, though that is the back of the model ...
    top = fv.render(alone, 0.0, 300, 200, yaw=0.0, pitch=0.0, turned=True,
                    labels=False)
    assert math.dist(_patch(top, 150, 100, 20), look.front) < 30
    # ... and from under it, its flesh
    under = fv.render(alone, 0.0, 300, 200, yaw=math.pi, pitch=math.pi,
                      turned=True, labels=False)
    assert math.dist(_patch(under, 150, 100, 20), look.back) < 50
    # a layout that says nothing of it is painted as it always was
    lay = fs.fold_piece(parts.outline, [])
    assert math.dist(_patch(fv.render(lay, 0.0, 300, 200, yaw=0.0,
                                      pitch=0.0, labels=False),
                            150, 100, 20), look.front) < 30


# -- the window on a stack ----------------------------------------------------

def test_the_bifold_put_together_opens_on_its_stack_lying_flat(qapp, mm):
    doc, by = _bifold()
    was = doc.to_dict()
    dlg = _dialog(doc)
    try:
        assert doc.to_dict() == was               # opening writes nothing
        assert (dlg.current.kind, dlg.current.title) == \
            ("stack", "Outer shell + 9 pieces")
        assert not dlg.cmb_piece.isVisible()      # there is nothing else
        assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == \
            ["Pieces", "Folds"]
        assert dlg.tabs.currentWidget() is dlg.pieces
        # ten rows, the top of the pile first, each ticked: in the stack
        t = dlg.pieces.pieces
        assert [t.horizontalHeaderItem(c).text() for c in range(3)] == \
            ["Piece", "Thickness", "Lies"]
        assert t.rowCount() == 10 and _names(dlg) == ROWS
        assert [t.item(r, 0).text() for r in range(10)] == ROWS
        assert all(t.item(r, 0).checkState() == Qt.Checked
                   for r in range(10))
        assert [t.item(r, 1).text() for r in range(10)] == \
            ["0.8 mm"] * 9 + ["1.6 mm"]
        assert [t.item(r, 2).text() for r in range(10)] == [
            "on Right pocket 2 · bottom right",
            "on Right pocket 3 · bottom right",
            "on Right pocket backing · bottom right",
            "on Bill pocket liner · bottom right",
            "on Left pocket 2 · bottom left",
            "on Left pocket 3 · bottom left",
            "on Left pocket backing · bottom left",
            "on Bill pocket liner · bottom left",
            "on Outer shell · bottom centre",
            "grain down · on the bench"]
        assert t.item(8, 2).toolTip() == (
            "Lies on Outer shell, grain up. Held by its bottom centre, "
            "level with the bottom centre of Outer shell.")
        # the main piece's row is in bold
        assert t.item(9, 0).font().bold() and t.item(9, 2).font().bold()
        assert not t.item(8, 0).font().bold()
        # the stack lies flat on the bench, seen from above: 5.6 mm
        lay = dlg.layout_
        assert isinstance(lay, fs.Layout) and lay.n_steps == 1
        assert [round(s.lo, 6) for s in lay.sheets] == [
            0.0, 0.8, 1.6, 2.4, 3.2, 0.8, 1.6, 2.4, 3.2]
        assert lay.notes[0].a == _near(5.6)
        assert dlg.view.cam.turned and dlg.view.t == 0.0
        assert dlg.view.caption == ("Put together  ·  10 pieces, 5.6 mm "
                                    "thick  ·  1 step to fold")
        # its fold is made with the pieces on it: the fold's row says
        # what it takes, and the folding can be run
        assert dlg.table.rowCount() == 1
        assert dlg.table.item(0, 0).text() == "A  Spine fold"
        assert dlg.table.item(0, 5).text() == "15.08 mm"
        assert dlg.folds_tab.isEnabled()
        assert dlg.btn_play.isEnabled() and dlg.slider.isEnabled()
        assert dlg.slider_apart.isEnabled()
        # with no row chosen the thickness is the main piece's
        assert dlg.spin_t.value() == 1.6
        # a stack on the bench is looked at from on top and from under
        assert [dlg.btn_views[k].text() for k in ("iso", "front", "back",
                                                  "side")] == \
            ["3D", "Top", "Under", "Side"]
        assert doc.to_dict() == was
    finally:
        dlg.close()


def test_the_card_holders_open_as_they_are_put_together(qapp, mm):
    # Card holder: 2.4 mm, and 46 of the back panel's 82 holes come
    # through the pocket
    dlg = _dialog(_card_holder())
    try:
        assert dlg.current.title == "Back panel + 1 piece"
        assert _names(dlg) == ["Pocket", "Back panel"]
        assert _lies(dlg) == {"Pocket": "on Back panel · bottom centre",
                              "Back panel": "grain down · on the bench"}
        assert dlg.layout_.notes[0].a == _near(2.4)
        # nothing folds, now or later: it has no fold line
        assert dlg.view.caption == ("Put together  ·  2 pieces, 2.4 mm "
                                    "thick  ·  nothing folds")
        assert dlg.table.rowCount() == 0
        f, = dlg.view.check.findings
        assert (f.holes, f.through, f.met, f.off) == (46, (46, 0), 0, 0)
        assert dlg.layout_.hole_sheet.count(0) == 82
        assert _lines(dlg) == [
            "Put together: 2 pieces, 2.4 mm at the thickest (Back panel, "
            "Pocket).",
            "Stitch holes through the layers: 46 of Back panel's 82 holes "
            "pass through Pocket, which has no holes there. The laser will "
            "not cut them in that piece."]
        # that is told and marked, and is no fault
        assert "⚠" not in _words(dlg) and "color:" not in dlg.readout.text()
        assert not dlg.view.check.state
        assert len(dlg.view.check.passed[1]) == 46
    finally:
        dlg.close()
    # Slim card holder: 4.8 mm, and the one seam's holes meet through
    # every layer
    dlg = _dialog(_slim())
    try:
        assert dlg.current.title == "Back panel + 3 pieces"
        assert _names(dlg) == ["Card pocket (front)", "Card pocket (middle)",
                               "Card pocket (back)", "Back panel"]
        assert dlg.layout_.notes[0].a == _near(4.8)
        assert not dlg.view.cam.turned            # the panel lies grain up
        assert [dlg.layout_.hole_sheet.count(k) for k in range(4)] == \
            [67, 65, 61, 55]
        check = dlg.view.check
        assert len(check.holes(fs.MEETS)) == 246 and not check.off
        assert _lines(dlg) == [
            "Put together: 4 pieces, 4.8 mm at the thickest (Back panel, "
            "Card pocket (back), Card pocket (middle), Card pocket "
            "(front)).",
            "Stitch holes through the layers: ● all 246 line up.",
            "Back panel and Card pocket (back): 130 line up.",
            "Back panel and Card pocket (middle): 122 line up.",
            "Back panel and Card pocket (front): 110 line up.",
            "Card pocket (back) and Card pocket (middle): 122 line up.",
            "Card pocket (back) and Card pocket (front): 110 line up.",
            "Card pocket (middle) and Card pocket (front): 110 line up."]
    finally:
        dlg.close()


def test_the_report_of_the_bifold_in_millimetres_and_in_inches(qapp, mm):
    doc, by = _bifold()
    dlg = _dialog(doc)
    try:
        assert _lines(dlg) == [
            "Put together: 10 pieces, 5.6 mm at the thickest (Outer shell, "
            "Bill pocket liner, Left pocket backing, Left pocket 3, Left "
            "pocket 2, Left pocket 1 (front)).",
            "Left pocket backing (and 7 more) stand 2.49 mm past the edge "
            "of Outer shell.",
            "Fold A (Spine fold) closes over 8 mm of leather, 4 mm on each "
            "half: Left pocket 1 (front) comes to rest on Right pocket 1 "
            "(front). It bends at 4 mm and takes 15.08 mm of Outer shell, "
            "7.54 mm each side of the fold line.",
            "⚠ Left pocket backing (and 7 more) reach 5.54 mm into the "
            "bend of fold A, where leather cannot lie flat. They are shown "
            "bent with it. Stop them 7.54 mm from the fold line: make them "
            "5.54 mm narrower.",
            "⚠ Bill pocket liner goes round the inside of fold A and has "
            "3.77 mm too much leather there. Cut it 3.77 mm shorter across "
            "the fold and it lies smooth and comes level when shut; sewn in "
            "flat at this length it wrinkles inside the fold. The picture "
            "squeezes it to fit (marked amber).",
            "Folded it measures 97.66 × 90 × 11.2 mm; the spine is 11.2 mm "
            "across. A needle through it goes through 11.2 mm of leather at "
            "the most and 3.2 mm at the least.",
            "Stitch holes through the layers: Outer shell's holes pass "
            "through Bill pocket liner (142), Left pocket backing (44), "
            "Left pocket 3 (37), Left pocket 2 (35), Left pocket 1 (front) "
            "(32), Right pocket backing (44), Right pocket 3 (37), Right "
            "pocket 2 (35) and Right pocket 1 (front) (32), which have no "
            "holes there. The laser will not cut them in those pieces."]
        assert dlg.readout.text().startswith("<b>Put together:</b> 10 ")
        # the window is told when lengths are to be shown in inches, and
        # says everything again: no millimetre is left in it
        mm.set_current("in")
        says = _lines(dlg)
        assert says[0].startswith("Put together: 10 pieces, 0.22 in at the "
                                  "thickest (Outer shell, Bill pocket liner")
        assert says[1] == ("Left pocket backing (and 7 more) stand 0.098 in "
                           "past the edge of Outer shell.")
        assert len(says) == 7 and "mm" not in _words(dlg)
        assert dlg.view.caption == ("Put together  ·  10 pieces, 0.22 in "
                                    "thick  ·  1 step to fold")
        t = dlg.pieces.pieces
        assert [t.item(r, 1).text() for r in (0, 9)] == ["0.031 in",
                                                         "0.063 in"]
        assert all("mm" not in t.item(r, c).text() + t.item(r, c).toolTip()
                   for r in range(10) for c in range(3))
        assert dlg.spin_t.text() == "0.063 in"
        mm.set_current("mm")
        assert _lines(dlg)[1].startswith("Left pocket backing (and 7 more) "
                                         "stand 2.49 mm past")
        assert dlg.pieces.pieces.item(9, 1).text() == "1.6 mm"
    finally:
        dlg.close()
    # shut, it listens no more
    from leathercad_app import units
    assert dlg._units_changed not in units._listeners


def test_what_will_not_lie_flat_is_said_in_plain_words(qapp, mm):
    # a 200 x 100 piece with five on it: A, B that overlaps A by a
    # sliver, C that hangs 30 mm over the left edge, D bridging A and B,
    # and E beside them all
    main = _piece("Main", 200, 100)
    others = [(_piece(name, w, h, at=(500 + 200 * k, 0)), t, x, y)
              for k, (name, w, h, t, x, y) in enumerate((
                  ("A", 90, 80, 1.0, 10, 10), ("B", 90.2, 80, 1.0, 99.8, 10),
                  ("C", 60, 20, 0.5, -30, 40), ("D", 80, 40, 0.5, 60, 30),
                  ("E", 40, 40, 0.5, 300, 0)))]
    doc = _drawing(main, *(piece for piece, _t, _x, _y in others))
    key = asm.put_on(doc, main, thickness=2.0)
    for piece, t, x, y in others:
        asm.put_on(doc, piece, key, thickness=t)
        asm.line_up(doc, piece, "left")
        asm.line_up(doc, piece, "bottom")
        asm.set_offset(doc, piece, x, y)
    dlg = _dialog(doc)
    try:
        assert [(n.kind, n.sheet) for n in dlg.layout_.notes] == [
            ("thick", 0), ("past", 3), ("past", 5), ("clear", 2),
            ("clear", 3), ("clear", 4), ("sliver", 2), ("loose", 5)]
        assert _lines(dlg) == [
            "Put together: 6 pieces, 4.5 mm at the thickest (Main, A, B, "
            "D).",
            "C stands 30 mm past the edge of Main.",
            "E stands 140 mm past the edge of Main.",
            "B rests on A; beyond it, it stands 1 mm clear of Main. On the "
            "bench it beds down over that step: the picture keeps it flat.",
            "C rests on A; beyond it, it stands 1 mm clear of Main. On the "
            "bench it beds down over that step: the picture keeps it flat.",
            "D rests on B; beyond it, it stands 1 mm clear of A. On the "
            "bench it beds down over that step: the picture keeps it flat.",
            "B rides up onto A on only 0.2 mm of overlap. If they are meant "
            "to meet edge to edge, use Line up.",
            "E lies on nothing: no part of it is over Main or over a piece "
            "put on it. It is shown at the level of Main's face."]
        assert _lies(dlg)["E"] == "on nothing · bottom left, 300 mm across"
        assert _lies(dlg)["C"] == "on A · middle left, -30 mm across"
        assert dlg.view.caption.endswith("4.5 mm thick  ·  nothing folds")
        mm.set_current("in")
        says = _words(dlg)
        assert "mm" not in says
        assert "C stands 1.181 in past the edge of Main." in says
        assert "B rides up onto A on only 0.008 in of overlap." in says
        assert "it stands 0.039 in clear of Main." in says
        assert _lies(dlg)["C"] == "on A · middle left, -1.181 in across"
        mm.set_current("mm")
        # the main piece deleted on the drawing: the lowest piece left
        # stands in for it, and the report says so
        doc.shapes.remove(main)
        dlg.reload()
        assert dlg.current.title == "A + 4 pieces" and dlg.current.stack.lost
        assert _lines(dlg)[:2] == [
            "Put together: 5 pieces, 2.5 mm at the thickest (A, B, D).",
            "The piece the others were placed against is gone from the "
            "drawing. A stands in for it, and every piece lies where it "
            "last lay."]
        assert "C and D stand 40 mm past the edge of A." in _lines(dlg)
    finally:
        dlg.close()
    # a fold line of a piece that is not the main piece is not made
    from leathercad.generators import hemmed_pocket
    panel = _piece("Panel", 160, 120)
    pocket, hem = hemmed_pocket()
    doc = _apart(_drawing(panel, pocket, hem))
    asm.put_on(doc, pocket, asm.put_on(doc, panel, thickness=2.0),
               thickness=1.0)
    dlg = _dialog(doc)
    try:
        assert _lines(dlg)[:2] == [
            "Put together: 2 pieces, 3 mm at the thickest (Panel, Hemmed "
            "pocket).",
            "Hem fold is on Hemmed pocket, which is not the main piece: its "
            "fold is not made here."]
        assert dlg.table.rowCount() == 0          # no fold of the stack's
        assert dlg.view.caption.endswith("nothing folds")
    finally:
        dlg.close()


# a point of the model on the outer face of some of the bifold's pieces,
# where nothing lies over them (its sheets are numbered from the liner:
# see ``ROWS``, from its end)
def _on(dlg, name, x, y):
    k = dlg.parts.names.index(name)
    s = dlg.layout_.sheets[k - 1]
    return (x, y, -(0.8 + s.hi + dlg.view.apart * s.rank))


def _pick_coloured(dlg) -> int:
    """How many pixels of the picture are the colour a chosen piece is
    marked with."""
    view = dlg.view
    view.repaint()
    img = view.grab().toImage()
    c = view.look.pick
    pick = (c.red(), c.green(), c.blue())
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            p = img.pixelColor(x, y)
            if math.dist((p.red(), p.green(), p.blue()), pick) < 12:
                n += 1
    return n


def _click(dlg, point, mods=Qt.NoModifier):
    _colour, (x, y) = _seen(dlg, point)
    QTest.mouseClick(dlg.view, Qt.LeftButton, mods, QPoint(int(x), int(y)))


def test_clicking_a_piece_chooses_its_row_and_a_fold_line_brings_folds(
        qapp, mm):
    doc, by = _bifold()
    dlg = _dialog(doc)
    try:
        dlg._look("front")                        # "Top", for a stack
        assert (dlg.view.cam.yaw, dlg.view.cam.pitch) == (0.0, 0.0)
        dlg.tabs.setCurrentWidget(dlg.folds_tab)
        _click(dlg, _on(dlg, "Left pocket 2", 50.0, -6.0))
        assert dlg.pieces.chosen_keys() == [by["Left pocket 2"].shape_id]
        assert dlg.pieces.pieces.currentRow() in (-1, 5) \
            and [i.row() for i in dlg.pieces.pieces.selectionModel()
                 .selectedRows()] == [5]
        assert dlg.tabs.currentWidget() is dlg.pieces
        # it is lit in the picture -- the pick colour runs round it,
        # whatever its leather -- and the thickness shown is its own
        assert dlg.view.chosen == (4,) and dlg.spin_t.value() == 0.8
        assert _pick_coloured(dlg) > 40
        # what can be done to it is there to be done
        panel = dlg.pieces
        assert panel.btn_higher.isEnabled() and panel.btn_over.isEnabled()
        assert panel.spin_x.isEnabled() and panel.spin_x.value() == 0.0
        assert panel.lbl_from.text() == "from its left and bottom edges"
        assert panel.btn_under.text() == "Underneath"
        # with Ctrl one more, and again to let it go
        _click(dlg, _on(dlg, "Right pocket backing", -50.0, 21.5),
               Qt.ControlModifier)
        assert set(dlg.pieces.chosen_keys()) == {
            by["Left pocket 2"].shape_id, by["Right pocket backing"].shape_id}
        assert dlg.view.chosen == (6, 4)          # down the list
        _click(dlg, _on(dlg, "Left pocket 2", 50.0, -6.0),
               Qt.ControlModifier)
        assert dlg.pieces.chosen_keys() == [
            by["Right pocket backing"].shape_id]
        # the shell itself, where its top edge shows past the liner
        _click(dlg, (50.0, 44.5, -0.8))
        assert dlg.pieces.chosen_keys() == [by["Outer shell"].shape_id]
        assert dlg.view.chosen == (0,) and dlg.spin_t.value() == 1.6
        assert not panel.spin_x.isEnabled()       # it is what they go by
        assert not panel.btn_under.isEnabled()
        # a click on nothing chooses none, and nothing is lit
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))
        assert dlg.pieces.chosen_keys() == [] and dlg.view.chosen == ()
        assert _pick_coloured(dlg) == 0
        assert not panel.btn_higher.isEnabled()
        # the fold line shows on the liner between the two pocket stacks:
        # a click on it is for the fold, and brings its tab forward
        _click(dlg, (0.0, 10.0, -1.6))
        assert dlg.tabs.currentWidget() is dlg.folds_tab
        assert dlg.selected_fold() == 0 and dlg.view.selected == 0
        assert dlg.pieces.chosen_keys() == []
    finally:
        dlg.close()
    # a piece shown on its own is all there is to click: nothing is lit,
    # and the tab in front stays
    doc = _drawing(_piece("Strip", 240, 100, at=(120, 50)),
                   _line((80, 0), (80, 100), "Left fold"))
    dlg = _dialog(doc)
    try:
        assert dlg.tabs.currentWidget() is dlg.folds_tab
        dlg.view.look_from("front")
        _click(dlg, (200.0, 50.0, 1.0))
        assert dlg.view.chosen == () and dlg.pieces.chosen_keys() == []
        assert dlg.tabs.currentWidget() is dlg.folds_tab
    finally:
        dlg.close()


def test_pull_apart_lifts_the_layers_and_play_brings_them_together_first(
        qapp, mm):
    doc, by = _bifold()
    dlg = _dialog(doc)
    try:
        v = dlg.view
        assert dlg.slider_apart.maximum() == 100 and v.apart == 0.0
        flat = v.scene.bounds()
        assert flat[0][2] == _near(-(0.8 + 4.0)) and flat[1][2] == 0.8
        assert dlg.btn_play.isEnabled()           # it folds
        dlg.slider_apart.setValue(50)             # 5 mm a layer
        assert v.apart == 5.0 and v.scene.apart == 5.0
        lo, hi = v.scene.bounds()
        # the top pocket is five layers up: 25 mm above where it lay
        assert lo[2] == _near(-(0.8 + 4.0 + 25.0)) and hi[2] == 0.8
        assert dlg.btn_play.isEnabled() and dlg.slider.isEnabled()
        # Play brings the layers together, in about 0.4 s, and then folds
        dlg._toggle_play()
        assert dlg._playing
        dlg._last_tick -= 0.2
        dlg._tick()
        assert 2.3 < v.apart < 2.7 and dlg._playing and v.t == 0.0
        assert dlg.slider_apart.value() == 25
        dlg._last_tick -= 0.2
        dlg._tick()
        assert v.apart == 0.0 and v.t == 0.0 and dlg._playing
        assert dlg.slider_apart.value() == 0 and v.scene.bounds() == flat
        for _ in range(2):
            dlg._last_tick -= 0.2
            dlg._tick()
        # (two ticks of 0.2 s and whatever more they took, up to the
        # 0.25 s a tick may be, of a fold that takes 1.1 s)
        assert 0.4 / 1.1 <= v.t <= 0.5 / 1.1 and dlg._playing
        assert not dlg.slider_apart.isEnabled()
        dlg._go(0.0)
        assert not dlg._playing and v.scene.bounds() == flat
        # the space bar does the same from the view
        dlg.slider_apart.setValue(100)
        assert v.apart == 10.0
        QTest.keyClick(v, Qt.Key_Space)
        assert dlg._playing
        dlg._toggle_play()                        # ... and stops it there
        assert not dlg._playing and v.apart == 10.0
        # something else shown starts with its layers together
        dlg._show(dlg.current)
        assert v.apart == 0.0 and dlg.slider_apart.value() == 0
    finally:
        dlg.close()
    # a piece on its own has no layers to lift
    dlg = _dialog(_drawing(_piece("Strip", 240, 100, at=(120, 50)),
                           _line((80, 0), (80, 100), "Left fold")))
    try:
        assert not dlg.slider_apart.isEnabled()
        assert dlg.btn_play.isEnabled() and dlg.layout_.n_steps == 1
        # folding on from flat lays any layers down first
        dlg.view.apart = 3.0
        dlg._go(0.5)
        assert dlg.view.apart == 0.0 and dlg.view.t == 0.5
    finally:
        dlg.close()


def test_a_leather_chosen_for_a_piece_is_on_the_shape_and_in_the_picture(
        qapp, mm):
    from leathercad_app import foldview as fv
    doc, by = _bifold()
    dlg = _dialog(doc)
    try:
        dlg._look("front")
        dlg.chk_labels.setChecked(False)          # no numbers on the leather
        panel = dlg.pieces
        box = panel.cmb_colour
        assert [box.itemText(i) for i in range(box.count())] == \
            ["Usual"] + list(fv.LEATHERS)
        assert not box.isEnabled()                # no piece chosen
        spot = _on(dlg, "Left pocket 2", 50.0, -6.0)
        other = _on(dlg, "Left pocket 3", 50.0, 4.0)
        tan = fv.LEATHERS["Tan"][0]
        _choose(dlg)
        assert math.dist(_seen(dlg, spot)[0], tan) < 30
        _choose(dlg, "Left pocket 2")
        assert box.isEnabled() and box.currentText() == "Usual"
        box.setCurrentText("Navy")
        assert by["Left pocket 2"].leather == "Navy"
        assert doc.to_dict()["shapes"][doc.shapes.index(
            by["Left pocket 2"])]["leather"] == "Navy"
        assert dlg.view.leather_of(4) == "Navy"
        _choose(dlg)                              # (not lit: its own colour)
        assert math.dist(_seen(dlg, spot)[0], fv.LEATHERS["Navy"][0]) < 30
        assert math.dist(_seen(dlg, other)[0], tan) < 30
        # its chip in the list is that leather
        chip = panel.pieces.item(5, 0).icon().pixmap(14, 14).toImage()
        assert _rgb(chip.pixelColor(7, 7)) == fv.LEATHERS["Navy"][0]
        # several at once, the main piece among them
        _choose(dlg, "Outer shell", "Bill pocket liner")
        assert box.currentText() == "Usual"
        box.setCurrentText("Black")
        assert (by["Outer shell"].leather, by["Bill pocket liner"].leather) \
            == ("Black", "Black")
        _choose(dlg)
        assert dlg.view.leather_of(0) == dlg.view.leather_of(1) == "Black"
        # from under, the shell's grain: black
        dlg._look("back")                         # "Under"
        assert (dlg.view.cam.yaw, dlg.view.cam.pitch) == (math.pi, math.pi)
        assert math.dist(_seen(dlg, (50.0, 20.0, 0.8))[0],
                         fv.LEATHERS["Black"][0]) < 30
        # the usual leather is another matter: kept for the user, not in
        # the drawing, and the pieces with none of their own follow it
        dlg._look("front")
        was = doc.to_dict()
        dlg.cmb_leather.setCurrentText("Chestnut")
        assert doc.to_dict() == was
        assert math.dist(_seen(dlg, other)[0],
                         fv.LEATHERS["Chestnut"][0]) < 30
        assert math.dist(_seen(dlg, spot)[0], fv.LEATHERS["Navy"][0]) < 30
        # ... and back to the usual one
        _choose(dlg, "Left pocket 2")
        assert box.currentText() == "Navy"
        box.setCurrentIndex(0)
        assert by["Left pocket 2"].leather == ""
        assert "leather" not in doc.to_dict()["shapes"][doc.shapes.index(
            by["Left pocket 2"])]
    finally:
        dlg.close()


def test_a_main_piece_lying_grain_down_is_seen_from_above_with_its_pieces(
        qapp, mm):
    from leathercad_app import foldview as fv
    doc, by = _bifold()
    dlg = _dialog(doc)
    try:
        look = dlg.view.look
        dlg._look("front")
        dlg.chk_labels.setChecked(False)          # no numbers on the leather
        assert dlg.view.cam.turned
        # on top: the pockets' grain, left stack at the left of the picture
        seen, (x, y) = _seen(dlg, _on(dlg, "Left pocket 1 (front)",
                                      50.0, -28.0))
        assert x < dlg.view.width() / 2
        assert math.dist(seen, look.front) < 30
        assert dlg.view.piece_at(x, y) == 5
        seen, (x, y) = _seen(dlg, _on(dlg, "Right pocket 1 (front)",
                                      -50.0, -28.0))
        assert x > dlg.view.width() / 2 and dlg.view.piece_at(x, y) == 9
        # along the top edge 2 mm of the shell show past the liner: its
        # flesh side, in the shadow of the liner's edge
        seen, (x, y) = _seen(dlg, (50.0, 44.0, -0.8), half=1)
        assert dlg.view.piece_at(x, y) == 0
        assert math.dist(seen, look.back) < 60 < math.dist(seen, look.front)
        # from under: the shell's grain, as it is drawn
        dlg._look("back")
        seen, (x, y) = _seen(dlg, (50.0, 20.0, 0.8))
        assert x > dlg.view.width() / 2 and dlg.view.piece_at(x, y) == 0
        assert math.dist(seen, look.front) < 30
        # turned grain up again the stack is seen as the model is built
        _choose(dlg, "Outer shell")
        dlg.pieces.btn_over.click()
        assert not dlg.view.cam.turned and by["Outer shell"].placed.over \
            is False
        assert _lies(dlg)["Outer shell"] == "on the bench"
        assert all(s.side == 1 and s.grain == 1 for s in dlg.layout_.sheets)
    finally:
        dlg.close()


# -- in the app: what is done on the Pieces tab goes to the drawing -----------

def _steps(win) -> int:
    """How many undo steps the drawing has behind it."""
    return win.history._index


def _reread(dlg):
    """What the window does a moment after the drawing changes."""
    dlg._refresh.stop()
    dlg.reload()


def _opened(qapp, doc, tmp_path, monkeypatch):
    win = _window(qapp, doc, tmp_path, monkeypatch)
    win._assembly_preview()
    return win, win._assembly_dlg


def test_two_pieces_open_the_window_and_opening_it_writes_nothing(
        qapp, tmp_path, monkeypatch, mm):
    said = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.append(a[2]))
    # nothing at all, and one plain piece: nothing to fold yet
    for doc in (Document("empty"), _drawing(_piece("Panel", 105, 70))):
        win = _window(qapp, doc, tmp_path, monkeypatch)
        try:
            win._assembly_preview()
            assert getattr(win, "_assembly_dlg", None) is None
        finally:
            _shut(win)
    assert len(said) == 2 and all("Nothing to fold yet." in s for s in said)
    # two pieces drawn apart: one can be put on the other
    doc = _drawing(_piece("Panel", 105, 70, r=6),
                   _piece("Pocket", 105, 45, at=(200, 0), r=6))
    win = _window(qapp, doc, tmp_path, monkeypatch)
    try:
        was = win.doc.to_dict()
        win._assembly_preview()
        dlg = win._assembly_dlg
        assert len(said) == 2 and dlg.isVisible()
        assert [(f.kind, f.title) for f in dlg.items] == [
            ("plain", "Panel"), ("plain", "Pocket")]
        assert dlg.cmb_piece.isVisibleTo(dlg) and dlg.cmb_piece.count() == 2
        assert dlg._piece_label.text() == "Show"
        assert dlg.current.title == "Panel"
        assert dlg.tabs.currentWidget() is dlg.pieces
        assert dlg.view.caption == ("Nothing folds: no fold line crosses "
                                    "this piece")
        assert dlg.readout.text() == "" and not dlg.btn_play.isEnabled()
        # the piece shown first, in bold, then those not in the stack yet
        t = dlg.pieces.pieces
        assert [(r.where, r.name) for r in dlg.pieces.rows()] == [
            ("shown", "Panel"), ("out", "Pocket")]
        assert t.rowCount() == 3
        assert t.cellWidget(1, 0).text() == "Not in the stack yet"
        assert t.item(0, 0).font().bold()
        assert [t.item(r, 0).checkState() for r in (0, 2)] == \
            [Qt.Unchecked, Qt.Unchecked]
        # neither has a thickness yet: shown at the one in the box
        assert [t.item(r, 1).text() for r in (0, 2)] == ["(2 mm)", "(2 mm)"]
        assert dlg.spin_t.value() == 2.0 and dlg.layout_.thickness == 2.0
        # nothing of all that was written
        assert win.doc.to_dict() == was
        assert _steps(win) == 0 and not win._unsaved_changes
        dlg.cmb_piece.setCurrentIndex(1)
        assert dlg.current.title == "Pocket"
        assert [(r.where, r.name) for r in dlg.pieces.rows()] == [
            ("shown", "Pocket"), ("out", "Panel")]
        dlg.cmb_piece.setCurrentIndex(0)
        assert win.doc.to_dict() == was and _steps(win) == 0
        # the pocket ticked: a stack is started on the panel, the pocket
        # lands on it corner to corner (as wide as the panel, it is held by
        # its middle across), and both are given the thickness showing --
        # one undo step
        dlg.spin_t.blockSignals(True)
        dlg.spin_t.setValue(1.2)                  # (shown, not typed)
        dlg.spin_t.blockSignals(False)
        _tick(dlg, "Pocket")
        panel, pocket = win.doc.shapes
        assert panel.placed.main and panel.placed.of == pocket.placed.of
        assert (panel.placed.order, pocket.placed.order) == (1, 2)
        assert (pocket.placed.ax, pocket.placed.ay, pocket.placed.x,
                pocket.placed.y) == (0.5, 0.0, 0.0, 0.0)
        assert (panel.thickness, pocket.thickness) == (1.2, 1.2)
        assert _steps(win) == 1 and win._unsaved_changes
        assert (dlg.current.kind, dlg.current.title) == \
            ("stack", "Panel + 1 piece")
        assert dlg.cmb_piece.count() == 1 and not dlg.cmb_piece.isVisible()
        assert _lies(dlg) == {"Pocket": "on Panel · bottom centre",
                              "Panel": "on the bench"}
        assert dlg.view.caption == ("Put together  ·  2 pieces, 2.4 mm "
                                    "thick  ·  nothing folds")
        assert [t.item(r, 1).text() for r in (0, 1)] == ["1.2 mm", "1.2 mm"]
        win.undo()
        _reread(dlg)
        assert win.doc.to_dict() == was
        assert (dlg.current.kind, dlg.current.title) == ("plain", "Panel")
    finally:
        _shut(win)


def test_ticking_a_piece_puts_it_on_the_stack_and_unticking_takes_it_off(
        qapp, tmp_path, monkeypatch, mm):
    doc, _by = _bifold_drawn()
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        was = win.doc.to_dict()
        # the shell alone, as it always was: it folds
        assert (dlg.current.kind, dlg.current.title) == ("piece",
                                                         "Outer shell")
        assert dlg.tabs.currentWidget() is dlg.folds_tab
        assert dlg.view.caption == "Flat  ·  1 step to fold"
        assert dlg.folds_tab.isEnabled() and dlg.btn_play.isEnabled()
        # the Show box: the shell, a rule, and the nine it could carry
        assert dlg.cmb_piece.count() == 11
        assert dlg.cmb_piece.itemText(0) == "Outer shell"
        assert dlg.cmb_piece.itemData(1) is None  # the rule
        # the Pieces tab lists the shell first and the nine under a rule
        t = dlg.pieces.pieces
        rows = dlg.pieces.rows()
        assert (rows[0].where, rows[0].name) == ("shown", "Outer shell")
        assert [r.where for r in rows[1:]] == ["out"] * 9
        assert t.cellWidget(1, 0).text() == "Not in the stack yet"
        dlg.tabs.setCurrentWidget(dlg.pieces)
        _tick(dlg, "Bill pocket liner")
        shell = _named(win.doc, "Outer shell")
        liner = _named(win.doc, "Bill pocket liner")
        assert shell.placed.main and liner.placed.of == shell.placed.of
        assert (shell.thickness, liner.thickness) == (2.0, 2.0)
        assert _steps(win) == 1
        assert (dlg.current.kind, dlg.current.title) == \
            ("stack", "Outer shell + 1 piece")
        assert dlg.tabs.currentWidget() is dlg.pieces
        assert _names(dlg)[:2] == ["Bill pocket liner", "Outer shell"]
        # as wide as the shell: held by its middle, not its left end
        assert _lies(dlg)["Bill pocket liner"] == \
            "on Outer shell · bottom centre"
        assert t.cellWidget(2, 0).text() == "Not in the stack yet"
        assert dlg.view.caption == ("Put together  ·  2 pieces, 4 mm thick"
                                    "  ·  1 step to fold")
        # a piece ticked lands on the chosen piece, corner to corner ...
        _choose(dlg, "Bill pocket liner")
        dlg.pieces.spin_x.setValue(20.0)
        assert liner.placed.x == 20.0
        _tick(dlg, "Left pocket backing")
        backing = _named(win.doc, "Left pocket backing")
        # (the liner was moved 20 across: landing on it, the backing is
        # level with nothing of the shell, so it is held as the liner is,
        # by the shell's middle, and moves with the liner)
        assert (backing.placed.order, backing.placed.x) == (3, -30.8)
        assert _lies(dlg)["Left pocket backing"] == \
            "on Bill pocket liner · bottom centre, -30.8 mm across"
        # ... and with none chosen on the top of the pile
        _choose(dlg)
        _choose(dlg, "Outer shell")
        _tick(dlg, "Left pocket 3")
        pocket = _named(win.doc, "Left pocket 3")
        assert (pocket.placed.order, pocket.placed.x) == (4, 0.0)
        _choose(dlg)
        _tick(dlg, "Left pocket 2")
        assert _named(win.doc, "Left pocket 2").placed.x == 0.0
        assert _names(dlg)[:5] == ["Left pocket 2", "Left pocket 3",
                                   "Left pocket backing",
                                   "Bill pocket liner", "Outer shell"]
        # (the typed distance, then three ticks: four steps more)
        assert _steps(win) == 5
        # unticked, a piece is off the stack: nothing else moves, and it
        # keeps the thickness it was given
        _tick(dlg, "Left pocket backing", False)
        assert backing.placed is None and backing.thickness == 2.0
        assert pocket.placed.order == 3 and _steps(win) == 6
        row = _row(dlg, "Left pocket backing")
        assert t.item(row, 0).checkState() == Qt.Unchecked
        assert t.item(row, 1).text() == "2 mm" and t.item(row, 2).text() == ""
        assert _lies(dlg)["Left pocket 3"] == \
            "on Bill pocket liner · bottom left"
        # each of them one step back
        win.undo()
        _reread(dlg)
        assert _named(win.doc, "Left pocket backing").placed.order == 3
        assert len(dlg.current.stack.pieces) == 5
        for _ in range(5):
            win.undo()
        _reread(dlg)
        assert win.doc.to_dict() == was and not win.history.can_undo()
        assert (dlg.current.kind, dlg.current.title) == ("piece",
                                                         "Outer shell")
        # the last piece taken off ends the stack, and its piece is shown
        _tick(dlg, "Outer shell")
        assert (dlg.current.kind, dlg.current.title) == ("stack",
                                                         "Outer shell")
        assert dlg.view.caption == "Flat  ·  1 step to fold"   # as it folds
        assert dlg.folds_tab.isEnabled()
        _tick(dlg, "Outer shell", False)
        assert (dlg.current.kind, dlg.current.title) == ("piece",
                                                         "Outer shell")
        assert _named(win.doc, "Outer shell").placed is None
    finally:
        _shut(win)


def test_higher_lower_turn_over_underneath_mirror_and_main_piece(
        qapp, tmp_path, monkeypatch, mm):
    doc, _by = _bifold()
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        was = win.doc.to_dict()
        panel = dlg.pieces
        assert dlg.current.kind == "stack" and _steps(win) == 0

        def placed(name):
            return _named(win.doc, name).placed

        # Higher: one place up the pile, on what it lay under
        _choose(dlg, "Left pocket 3")
        panel.btn_higher.click()
        assert (placed("Left pocket 3").order, placed("Left pocket 2").order) \
            == (5, 4)
        assert _names(dlg)[4:8] == ["Left pocket 1 (front)", "Left pocket 3",
                                    "Left pocket 2", "Left pocket backing"]
        assert _lies(dlg)["Left pocket 3"] == \
            "on Left pocket 2 · bottom left"
        assert _lies(dlg)["Left pocket 2"] == \
            "on Left pocket backing · bottom left"
        assert _steps(win) == 1 and panel.chosen_keys() == [
            _named(win.doc, "Left pocket 3").shape_id]    # still chosen
        # Lower: and down again
        panel.btn_lower.click()
        assert placed("Left pocket 3").order == 4 and _names(dlg) == ROWS
        assert _steps(win) == 2
        # Turn over: like a page, grain down
        panel.btn_over.click()
        assert placed("Left pocket 3").over
        assert _lies(dlg)["Left pocket 3"] == \
            "grain down · on Left pocket backing · bottom left"
        assert dlg.layout_.sheets[2].grain == 1   # its flesh to the eye
        assert _steps(win) == 3
        panel.btn_over.click()
        assert not placed("Left pocket 3").over and _steps(win) == 4
        # Mirror: the other way round, the same side up
        panel.btn_mirror.click()
        assert placed("Left pocket 3").flip and not placed(
            "Left pocket 3").over
        assert _lies(dlg)["Left pocket 3"] == \
            "mirrored · on Left pocket backing · bottom left"
        panel.btn_mirror.click()
        assert _steps(win) == 6
        # Underneath: under the main piece, next to it (here the bottom
        # of the pile, as nothing else lies under it)
        _choose(dlg, "Right pocket 1 (front)")
        assert panel.btn_under.text() == "Underneath"
        panel.btn_under.click()
        assert (placed("Right pocket 1 (front)").order,
                placed("Outer shell").order) == (1, 2)
        assert _names(dlg) == ROWS[1:] + ROWS[:1]
        assert _lies(dlg)["Right pocket 1 (front)"] == \
            "under Outer shell · bottom right"
        assert _lies(dlg)["Outer shell"] == \
            "grain down · on Right pocket 1 (front)"
        assert dlg.layout_.sheets[-1].name == "Right pocket 1 (front)"
        assert dlg.layout_.sheets[-1].side == 1   # on the shell's own front
        assert _steps(win) == 7                   # one step, all the way
        # ... and On top again: on the main piece, next to it
        assert panel.btn_under.text() == "On top"
        panel.btn_under.click()
        assert (placed("Outer shell").order,
                placed("Right pocket 1 (front)").order,
                placed("Bill pocket liner").order) == (1, 2, 3)
        assert _names(dlg) == ROWS[1:9] + [ROWS[0], ROWS[9]]
        assert _lies(dlg)["Right pocket 1 (front)"] == \
            "on Outer shell · bottom right"
        assert _steps(win) == 8
        assert panel.btn_under.text() == "Underneath"
        # Main piece: the others are placed against the liner now, and
        # nothing has moved
        boxes = {p.name: p.box for p in dlg.current.stack.pieces}
        _choose(dlg, "Bill pocket liner")
        panel.btn_main.click()
        assert placed("Bill pocket liner").main
        assert not placed("Outer shell").main and _steps(win) == 9
        assert dlg.current.title == "Bill pocket liner + 9 pieces"
        t = panel.pieces
        assert t.item(7, 0).text() == "Bill pocket liner"
        assert t.item(7, 0).font().bold() and not t.item(9, 0).font().bold()
        moved = {p.name: p.box for p in dlg.current.stack.pieces}
        dx, dy = (moved["Outer shell"][k] - boxes["Outer shell"][k]
                  for k in (0, 1))
        assert all(moved[n] == _near((b[0] + dx, b[1] + dy, b[2] + dx,
                                      b[3] + dy)) for n, b in boxes.items())
        # (the pocket put on the shell lies between the two now)
        assert _lies(dlg)["Bill pocket liner"] == "on Right pocket 1 (front)"
        assert _lies(dlg)["Outer shell"] == \
            "grain down · under Right pocket 1 (front) · bottom centre"
        # the liner lies grain up, as it is drawn: the bench is the frame
        assert not dlg.view.cam.turned
        # ... and the spine fold is the shell's, which is not the main
        # piece any more
        assert dlg.table.rowCount() == 0
        assert ("Spine fold is on Outer shell, which is not the main "
                "piece: its fold is not made here.") in _lines(dlg)
        assert not panel.btn_main.isEnabled()     # it is the main piece
        # nine steps, and nine undone is the drawing as it was
        for _ in range(9):
            win.undo()
        assert win.doc.to_dict() == was and not win.history.can_undo()
        _reread(dlg)
        assert dlg.current.title == "Outer shell + 9 pieces"
        assert _names(dlg) == ROWS
    finally:
        _shut(win)


def test_line_up_typed_distances_turns_and_as_drawn(qapp, tmp_path,
                                                    monkeypatch, mm):
    panel = _piece("Panel", 120, 90)
    doc = _drawing(panel, _ell(), _piece("Tab", 30, 20, at=(600, 300)))
    key = asm.put_on(doc, panel, thickness=2.0)
    for s in doc.shapes[1:]:
        asm.put_on(doc, s, key, thickness=1.0)
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        was = win.doc.to_dict()
        p = dlg.pieces
        pocket = _named(win.doc, "L pocket")
        tab = _named(win.doc, "Tab")

        def box(name):
            return dlg.current.stack.piece(
                _named(win.doc, name).shape_id).box

        def held(shape):
            pl = shape.placed
            return (pl.ax, pl.x, pl.ay, pl.y)

        # it landed corner to corner on the panel
        assert box("L pocket") == _near((-60, -45, 20, 5))
        _choose(dlg, "L pocket")
        assert p.cmb_with.currentText() == "Panel (the main piece)"
        # each Line up button: the piece is held by that side from then on
        for n, (button, want, lies, x0, y0) in enumerate((
                (p.btn_right, (1.0, 0.0, 0.0, 0.0), "bottom right", -20, -45),
                (p.btn_centre, (0.5, 0.0, 0.0, 0.0), "bottom centre",
                 -40, -45),
                (p.btn_top, (0.5, 0.0, 1.0, 0.0), "top centre", -40, -5),
                (p.btn_middle, (0.5, 0.0, 0.5, 0.0), "centre", -40, -25),
                (p.btn_left, (0.0, 0.0, 0.5, 0.0), "middle left", -60, -25),
                (p.btn_bottom, (0.0, 0.0, 0.0, 0.0), "bottom left",
                 -60, -45)), 1):
            button.click()
            assert held(pocket) == want
            assert box("L pocket")[:2] == _near((x0, y0))
            assert _lies(dlg)["L pocket"] == f"on Panel · {lies}"
            assert _steps(win) == n
        # a distance typed across and one up: held by the same edges, so
        # far from them -- a run of changes that is one undo step
        assert p.lbl_from.text() == "from its left and bottom edges"
        assert p.spin_x.singleStep() == 1.0       # the nudge of the drawing
        p.spin_x.setValue(12.5)
        p.spin_y.setValue(3.0)
        assert held(pocket) == (0.0, 12.5, 0.0, 3.0)
        assert box("L pocket") == _near((-47.5, -42, 32.5, 8))
        assert _lies(dlg)["L pocket"] == \
            "on Panel · bottom left, 12.5 mm across, 3 mm up"
        assert p.pieces.item(_row(dlg, "L pocket"), 2).toolTip() == (
            "Lies on Panel, grain up. Held by its bottom left, 12.5 mm "
            "across and 3 mm up from the bottom left of Panel.")
        assert (p.spin_x.value(), p.spin_y.value()) == (12.5, 3.0)
        assert _steps(win) == 6 and dlg._unsaved  # not a step yet
        dlg._flush_commit()
        assert _steps(win) == 7
        # typed to where an edge is level, it is tied to that edge
        p.spin_x.setValue(40.0)
        assert held(pocket) == (1.0, 0.0, 0.0, 3.0)
        assert p.lbl_from.text() == "from its right and bottom edges"
        dlg._flush_commit()
        # lined up with another piece than the main piece: the tab's left
        # edge on the pocket's
        p.spin_x.setValue(-25.0)
        dlg._flush_commit()
        assert box("L pocket")[0] == _near(-45.0)
        _choose(dlg, "Tab")
        p.cmb_with.setCurrentIndex(p.cmb_with.findText("L pocket"))
        p.btn_left.click()
        assert box("Tab")[0] == _near(-45.0)
        assert held(tab)[:2] == (0.0, 15.0)       # still against the panel
        p.cmb_with.setCurrentIndex(0)
        n = _steps(win)
        # turned: a quarter turn at a time, or to a typed angle
        _choose(dlg, "L pocket")
        centre = [0.5 * (box("L pocket")[k] + box("L pocket")[k + 2])
                  for k in (0, 1)]
        p.btn_ccw.click()
        assert pocket.placed.turn == 90.0 and _steps(win) == n + 1
        b = box("L pocket")
        assert (b[2] - b[0], b[3] - b[1]) == _near((50.0, 80.0))
        assert [0.5 * (b[k] + b[k + 2]) for k in (0, 1)] == _near(centre)
        assert _lies(dlg)["L pocket"].startswith("turned 90° · on Panel")
        p.btn_cw.click()
        p.btn_cw.click()
        assert pocket.placed.turn == 270.0 and p.spin_turn.value() == 270.0
        p.spin_turn.setValue(45.0)
        assert pocket.placed.turn == 45.0 and _steps(win) == n + 3
        assert _lies(dlg)["L pocket"].startswith("turned 45° · on Panel")
        dlg._flush_commit()
        assert _steps(win) == n + 4
        p.spin_turn.setValue(0.0)
        dlg._flush_commit()
        # all of it undone, a step at a time, is the drawing as it was
        while win.history.can_undo():
            win.undo()
        assert win.doc.to_dict() == was
    finally:
        _shut(win)


def test_as_drawn_lays_the_pieces_drawn_on_the_main_piece(qapp, tmp_path,
                                                          monkeypatch, mm):
    # the Card holder as its template draws it: the pocket on the panel,
    # their bottoms level, and nothing put together
    win, dlg = _opened(qapp, _apart(templates.card_holder()), tmp_path,
                       monkeypatch)
    try:
        was = win.doc.to_dict()
        p = dlg.pieces
        assert [(f.kind, f.title) for f in dlg.items] == [
            ("plain", "Back panel"), ("plain", "Pocket")]
        assert [(r.where, r.name) for r in p.rows()] == [
            ("shown", "Back panel"), ("out", "Pocket")]
        # As drawn, with nothing chosen: every piece drawn on the panel is
        # put on where it is drawn -- one undo step for the lot
        assert p.chosen_keys() == [] and p.btn_drawn.isEnabled()
        p.btn_drawn.click()
        panel, pocket = win.doc.shapes
        assert panel.placed.main and pocket.placed.of == panel.placed.of
        assert (pocket.placed.ax, pocket.placed.x, pocket.placed.ay,
                pocket.placed.y) == (0.5, 0.0, 0.0, 0.0)
        assert (panel.thickness, pocket.thickness) == (2.0, 2.0)
        assert _steps(win) == 1
        assert dlg.current.title == "Back panel + 1 piece"
        assert _lies(dlg) == {"Pocket": "on Back panel · bottom centre",
                              "Back panel": "on the bench"}
        assert dlg.current.stack.piece(pocket.shape_id).box == \
            _near((-52.5, -35, 52.5, 10))
        assert not p.btn_drawn.isEnabled()        # nothing more to put on
        # moved away, and laid as drawn again
        _choose(dlg, "Pocket")
        p.spin_y.setValue(7.0)
        dlg._flush_commit()
        assert pocket.placed.y == 7.0 and p.btn_drawn.isEnabled()
        p.btn_drawn.click()
        assert (pocket.placed.ay, pocket.placed.y) == (0.0, 0.0)
        assert _steps(win) == 3
        # with the main piece turned over it has no say
        _choose(dlg, "Back panel")
        p.btn_over.click()
        _choose(dlg, "Pocket")
        assert not p.btn_drawn.isEnabled()
        for _ in range(4):
            win.undo()
        assert win.doc.to_dict() == was
    finally:
        _shut(win)
    # a pocket drawn 1 mm inside its panel is a hole in it to the rest of
    # the program -- and so is the slot in the panel, which it really is
    said = []
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: said.append(a[2]))
    doc = _drawing(_piece("Back panel", 105, 70, r=6),
                   _piece("Pocket", 103, 45, at=(0, -11.5), r=6),
                   _piece("Slot", 20, 3, at=(0, 25), r=1.5))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        # the window opens for the two of them, the pocket listed apart
        assert said == [] and dlg is not None
        assert [(f.kind, f.title) for f in dlg.items] == [("plain",
                                                           "Back panel")]
        p = dlg.pieces
        assert [(r.where, r.name, r.lies) for r in p.rows()] == [
            ("shown", "Back panel", ""),
            ("inside", "Pocket", "drawn inside Back panel")]
        assert p.pieces.cellWidget(1, 0).text() == "Drawn inside a piece"
        assert len(dlg.layout_.cutouts) == 2      # both cut out of it
        # ticked, it is a piece of the stack, where it is drawn
        _tick(dlg, "Pocket")
        pocket = win.doc.shapes[1]
        assert (pocket.placed.ax, pocket.placed.x, pocket.placed.ay,
                pocket.placed.y) == (0.5, 0.0, 0.5, -11.5)
        assert _steps(win) == 1
        assert dlg.current.title == "Back panel + 1 piece"
        assert [(r.where, r.name) for r in p.rows()] == [
            ("in", "Pocket"), ("in", "Back panel")]
        assert dlg.current.stack.piece(pocket.shape_id).box == \
            _near((-51.5, -34, 51.5, 11))
        # ... and no longer a hole in the panel, which keeps its slot
        assert len(dlg.layout_.layout.cutouts) == 1
    finally:
        _shut(win)


def test_what_lies_on_a_piece_is_taken_along_when_it_is_moved(
        qapp, tmp_path, monkeypatch, mm):
    doc, _by = _bifold()
    ini = QSettings(str(tmp_path / "settings.ini"),
                    QSettings.Format.IniFormat)
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        p = dlg.pieces
        assert p.chk_along.isChecked()            # ticked until unticked

        def left(name):
            return dlg.current.stack.piece(
                _named(win.doc, name).shape_id).box[0]

        # the backing moved 5 mm in: its three pockets go with it
        _choose(dlg, "Left pocket backing")
        p.spin_x.setValue(5.0)
        assert [left(n) for n in ROWS[4:8]] == _near([-94.6] * 4)
        assert _named(win.doc, "Left pocket 2").placed.x == 5.0
        assert left("Right pocket backing") == _near(2.0)   # not the others
        dlg._flush_commit()
        assert _steps(win) == 1                   # one step for the four
        # lined up again, and they come back with it
        p.btn_left.click()
        assert [left(n) for n in ROWS[4:8]] == _near([-99.6] * 4)
        assert _steps(win) == 2
        # unticked, a piece moves alone -- and that is kept for next time
        p.chk_along.setChecked(False)
        assert ini.value("fold3dAlong", True, type=bool) is False
        p.spin_x.setValue(5.0)
        assert left("Left pocket backing") == _near(-94.6)
        assert [left(n) for n in ROWS[4:7]] == _near([-99.6] * 3)
    finally:
        _shut(win)
    win, dlg = _opened(qapp, _bifold()[0], tmp_path, monkeypatch)
    try:
        assert not dlg.pieces.chk_along.isChecked()
    finally:
        _shut(win)


# -- leather thickness --------------------------------------------------------

def test_a_thickness_typed_for_several_rows_is_on_the_shapes_and_one_step(
        qapp, tmp_path, monkeypatch, mm):
    doc, _by = _bifold()
    ini = QSettings(str(tmp_path / "settings.ini"),
                    QSettings.Format.IniFormat)
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        was = win.doc.to_dict()
        t = dlg.pieces.pieces

        def thick(*names):
            return [_named(win.doc, n).thickness for n in names]

        # the nine on the shell chosen at once, and 1 mm typed for them
        assert dlg.spin_t.value() == 1.6
        _choose(dlg, *ROWS[:9])
        assert dlg.spin_t.value() == 0.8          # theirs
        assert win.doc.to_dict() == was           # shown, not written
        dlg.spin_t.setValue(1.0)
        assert thick(*ROWS[:9]) == [1.0] * 9 and thick("Outer shell") == [1.6]
        assert [t.item(r, 1).text() for r in range(10)] == \
            ["1 mm"] * 9 + ["1.6 mm"]
        # the stack is that much thicker, in the model and in its words
        assert dlg.layout_.notes[0].a == _near(6.6)
        assert dlg.view.caption.startswith("Put together  ·  10 pieces, "
                                           "6.6 mm thick")
        assert _lines(dlg)[0].startswith("Put together: 10 pieces, 6.6 mm")
        assert len(dlg.pieces.chosen_keys()) == 9     # still chosen
        # a run of changes: not a step until it is over
        assert _steps(win) == 0 and dlg._unsaved and dlg._commit.isActive()
        dlg.spin_t.setValue(1.2)
        assert thick("Left pocket 2") == [1.2] and _steps(win) == 0
        dlg._flush_commit()
        assert _steps(win) == 1 and win._unsaved_changes
        # ... and it is the thickness kept for next time
        assert ini.value("fold3dThickness", type=float) == 1.2
        # with no row chosen it is the main piece's
        _choose(dlg)
        assert dlg.spin_t.value() == 1.6
        dlg.spin_t.setValue(2.4)
        assert thick("Outer shell", "Bill pocket liner") == [2.4, 1.2]
        assert dlg.layout_.thickness == 2.4
        dlg._flush_commit()
        assert _steps(win) == 2
        win.undo()
        win.undo()
        assert win.doc.to_dict() == was
        _reread(dlg)
        assert dlg.spin_t.value() == 1.6 and dlg.layout_.thickness == 1.6
        assert dlg.layout_.notes[0].a == _near(5.6)
        assert t.item(0, 1).text() == "0.8 mm"
    finally:
        _shut(win)
    # the next window starts from the thickness typed last
    win, dlg = _opened(qapp, _drawing(_piece("A", 80, 50),
                                      _piece("B", 60, 40, at=(200, 0))),
                       tmp_path, monkeypatch)
    try:
        assert dlg.spin_t.value() == 2.4 and dlg.layout_.thickness == 2.4
        assert dlg.pieces.pieces.item(0, 1).text() == "(2.4 mm)"
        assert [s.thickness for s in win.doc.shapes] == [0.0, 0.0]
        # a piece not put on yet can be given its thickness too
        _choose(dlg, "B")
        assert dlg.spin_t.value() == 2.4
        dlg.spin_t.setValue(0.9)
        assert [s.thickness for s in win.doc.shapes] == [0.0, 0.9]
        assert dlg.pieces.pieces.item(_row(dlg, "B"), 1).text() == "0.9 mm"
        # the piece shown has none of its own: it follows what was typed
        assert dlg.layout_.thickness == 0.9
        # put on, a piece with none is given the thickness showing
        _choose(dlg)
        assert dlg.spin_t.value() == 0.9
        _tick(dlg, "B")
        assert [s.thickness for s in win.doc.shapes] == [0.9, 0.9]
    finally:
        _shut(win)


def test_a_thickness_typed_for_a_piece_shown_alone_is_written_on_it(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Strip", 240, 100, at=(120, 50)),
                   _line((80, 0), (80, 100), "Left fold"))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        strip = win.doc.shapes[0]
        was = win.doc.to_dict()
        assert dlg.spin_t.value() == 2.0 and strip.thickness == 0.0
        assert "thickness" not in win.doc.to_dict()["shapes"][0]
        dlg.spin_t.setValue(3.0)
        assert strip.thickness == 3.0 and dlg.layout_.thickness == 3.0
        assert win.doc.to_dict()["shapes"][0]["thickness"] == 3.0
        assert dlg.table.item(0, 5).text() == "4.71 mm"
        dlg._flush_commit()
        assert _steps(win) == 1
        # the Properties panel shows it for the piece, if it is selected
        from leathercad_app.items import ShapeItem
        for it in win.canvas.scene_obj.items():
            if isinstance(it, ShapeItem) and it.model is strip:
                it.setSelected(True)
        win.properties.show_selection(win.canvas.selected_items())
        assert win.properties.thickness.value() == 3.0
        dlg.spin_t.setValue(1.5)
        assert win.properties.thickness.value() == 1.5
        # a thickness set in the drawing is followed here
        win.properties.thickness.setValue(2.2)
        win.properties._commit()
        assert win.doc.shapes[0].thickness == 2.2
        _reread(dlg)
        assert dlg.spin_t.value() == 2.2 and dlg.layout_.thickness == 2.2
        while win.history.can_undo():
            win.undo()
        assert win.doc.to_dict() == was
        _reread(dlg)
        # (none of its own again: the one typed last)
        assert win.doc.shapes[0].thickness == 0.0
        assert dlg.spin_t.value() == dlg.layout_.thickness == 1.5
    finally:
        _shut(win)


# -- undo, Esc, and keeping up with the drawing -------------------------------

def test_esc_keeps_the_change_made_last_as_an_undo_step(qapp, tmp_path,
                                                        monkeypatch, mm):
    doc, _by = _bifold()
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        from leathercad_app import units
        was = win.doc.to_dict()
        assert dlg._units_changed in units._listeners
        _choose(dlg, "Left pocket 2")
        dlg.pieces.spin_x.setValue(4.0)
        assert _named(win.doc, "Left pocket 2").placed.x == 4.0
        assert _steps(win) == 0 and dlg._commit.isActive()
        QTest.keyClick(dlg, Qt.Key_Escape)        # shut, within the moment
        assert not dlg.isVisible()
        assert _steps(win) == 1 and not dlg._commit.isActive()
        assert dlg._units_changed not in units._listeners
        win.undo()
        assert win.doc.to_dict() == was
    finally:
        win._assembly_dlg = None                  # (it is shut already)
        _shut(win)
    # the same for a fold's settings, which Esc used to lose
    doc = _drawing(_piece("Strip", 240, 100, at=(120, 50)),
                   _line((80, 0), (80, 100), "Left fold"))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        dlg.table.cellWidget(0, 3).setValue(90.0)
        assert _steps(win) == 0
        dlg.reject()
        assert _steps(win) == 1
        assert win.doc.shapes[1].fold_angle == 90.0
    finally:
        win._assembly_dlg = None
        _shut(win)


def test_moving_turning_and_nesting_the_drawing_rebuilds_nothing(
        qapp, tmp_path, monkeypatch, mm):
    from leathercad_app.items import ShapeItem
    doc, _by = _bifold()
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        c = win.canvas

        def select(*names):
            c.scene_obj.clearSelection()
            for it in c.scene_obj.items():
                if isinstance(it, ShapeItem) and it.model.name in names:
                    it.setSelected(True)
            QApplication.processEvents()

        model, parts = dlg.layout_, dlg.parts
        tree = dlg.view.scene.tree
        select("Left pocket 2")
        c.nudge_selected(300.0, -41.0)
        assert dlg._refresh.isActive()            # noticed ...
        _reread(dlg)
        assert dlg.layout_ is model               # ... and nothing to redo
        select("Right pocket backing")
        c.rotate_selected(90.0)
        select("Outer shell", "Spine fold")       # the fold with its piece
        c.nudge_selected(-17.0, 230.0)
        c.rotate_selected(90.0)
        _reread(dlg)
        assert dlg.layout_ is model and dlg.parts is parts
        # Nest on sheet moves every piece, turns some and leaves its sheet
        win.resize(1200, 800)
        win.show()
        c.scene_obj.clearSelection()
        QApplication.processEvents()
        assert "Placed 10 of 10" in c.nest_preview(260, 700, margin=5,
                                                   spacing=8)
        assert c.nest_apply()
        assert {round(s.transform.rotation % 180.0) for s in win.doc.shapes
                if s.placed is not None} == {0, 90}
        _reread(dlg)
        assert dlg.layout_ is model and dlg.view.scene.tree is tree
        assert _names(dlg) == ROWS and len(dlg.items) == 1
        assert dlg.layout_.notes[0].a == _near(5.6)
        # the fold line is still the shell's, where the nest has put it
        assert [f.key.name for f in dlg.folds] == ["Spine fold"]
        assert dlg.folds[0].key is _named(win.doc, "Spine fold")
        # a piece made narrower on the drawing is another matter
        _named(win.doc, "Left pocket 2").width = 90.0
        c.rebuild()
        _reread(dlg)
        assert dlg.layout_ is not model
        assert dlg.current.stack.piece(
            _named(win.doc, "Left pocket 2").shape_id).box == \
            _near((-99.6, -45, -9.6, -1))         # its outer edge kept
    finally:
        _shut(win)


def test_a_stack_survives_save_load_and_undo_with_the_window_open(
        qapp, tmp_path, monkeypatch, mm):
    doc, _by = _bifold()
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        path = str(tmp_path / "bifold.json")
        first = asm.stacks(win.doc)[0].signature()
        win.doc.save(path)
        # changed in the window, then the saved drawing opened over it
        _choose(dlg, "Left pocket 2")
        dlg.pieces.btn_over.click()
        dlg.pieces.cmb_colour.setCurrentText("Navy")
        _choose(dlg, "Right pocket 1 (front)")
        _tick(dlg, "Right pocket 1 (front)", False)
        assert len(dlg.current.stack.pieces) == 9 and _steps(win) == 3
        assert asm.stacks(win.doc)[0].signature() != first
        win._unsaved_changes = False              # (no question asked)
        assert win.open_path(path)
        assert dlg._refresh.isActive()
        _reread(dlg)
        # every shape is another object now: the window has read them all
        # again, and shows the stack as it was saved
        assert asm.stacks(win.doc)[0].signature() == first
        assert dlg.current.title == "Outer shell + 9 pieces"
        assert _names(dlg) == ROWS and dlg.layout_.notes[0].a == _near(5.6)
        assert [p.shape for p in dlg.current.stack.pieces] == [
            _named(win.doc, n) for n in ROWS[::-1]]
        # (the row chosen is still chosen: the piece has the id it had)
        assert dlg.pieces.chosen_keys() == [
            _named(win.doc, "Right pocket 1 (front)").shape_id]
        assert dlg.view.chosen == (9,) and dlg.view.leather_of(4) == "Tan"
        shell = _named(win.doc, "Outer shell")
        assert (shell.thickness, shell.placed.over, shell.placed.main) == \
            (1.6, True, True)
        # what is done now goes to the drawing that was opened
        _choose(dlg, "Bill pocket liner")
        dlg.pieces.btn_top.click()
        liner = _named(win.doc, "Bill pocket liner")
        assert (liner.placed.ay, liner.placed.y) == (1.0, 0.0)
        assert _lies(dlg)["Bill pocket liner"] == \
            "on Outer shell · top centre"
        assert win.history.can_undo()
        win.undo()
        _reread(dlg)
        assert asm.stacks(win.doc)[0].signature() == first
        assert _lies(dlg)["Bill pocket liner"] == \
            "on Outer shell · bottom centre"
        # ... and before the window has read the drawing again, too: it
        # goes by the ids of the pieces, not by the shapes it read
        win.redo()
        assert dlg.pieces.chosen_keys() == [liner.shape_id]
        dlg.pieces.btn_bottom.click()
        liner = _named(win.doc, "Bill pocket liner")
        assert (liner.placed.ay, liner.placed.y) == (0.0, 0.0)
        assert asm.stacks(win.doc)[0].signature() == first
        # saved and read back, the stack is the same
        win.doc.save(path)
        again = Document.load(path)
        assert asm.stacks(again)[0].signature() == first
    finally:
        _shut(win)


def test_the_list_is_worked_with_the_mouse_and_the_keys(qapp, mm):
    doc, by = _bifold_drawn()
    dlg = _dialog(doc)
    try:
        dlg.tabs.setCurrentWidget(dlg.pieces)
        QApplication.processEvents()
        t = dlg.pieces.pieces

        def click(name, on_tick, mods=Qt.NoModifier):
            rect = t.visualItemRect(t.item(_row(dlg, name), 0))
            QTest.mouseClick(t.viewport(), Qt.LeftButton, mods, QPoint(
                rect.left() + (9 if on_tick else 80), rect.center().y()))
            QApplication.processEvents()

        # a click on a tick box puts the piece on; the list is made again
        # under the mouse, and the next click finds its row
        for n, name in enumerate(("Bill pocket liner", "Left pocket backing",
                                  "Left pocket 3"), 1):
            click(name, True)
            assert dlg.current.title == \
                f"Outer shell + {n} piece{'s' if n > 1 else ''}"
            assert _names(dlg)[0] == name and by[name].placed is not None
        assert dlg.pieces.chosen_keys() == []     # ticking chooses nothing
        # a click on a name chooses the row, Shift takes those between
        click("Left pocket backing", False)
        assert [r.name for r in dlg.pieces.chosen_rows()] == \
            ["Left pocket backing"]
        assert dlg.view.chosen == (2,)
        click("Left pocket 3", False, Qt.ShiftModifier)
        assert [r.name for r in dlg.pieces.chosen_rows()] == \
            ["Left pocket 3", "Left pocket backing"]
        assert dlg.view.chosen == (3, 2)
        # a thickness typed for them and entered: on both, and the window
        # is still open (Enter shuts nothing)
        dlg.spin_t.setFocus()
        dlg.spin_t.selectAll()
        QTest.keyClicks(dlg.spin_t, "0.9")
        QTest.keyClick(dlg.spin_t, Qt.Key_Return)
        assert (by["Left pocket 3"].thickness,
                by["Left pocket backing"].thickness,
                by["Bill pocket liner"].thickness) == (0.9, 0.9, 2.0)
        assert dlg.isVisible()
        # ... and a click on a tick takes a piece off again
        click("Left pocket 3", True)
        assert by["Left pocket 3"].placed is None
        assert dlg.current.title == "Outer shell + 2 pieces"
    finally:
        dlg.close()


# -- more than one thing to show ----------------------------------------------

def test_a_second_stack_is_shown_from_the_show_box_and_keeps_its_pieces(
        qapp, tmp_path, monkeypatch, mm):
    from leathercad_app.items import ShapeItem
    doc, by = _bifold()
    panel = doc.add_shape(_piece("Card back", 105, 70, at=(0, 400), r=6))
    pocket = doc.add_shape(_piece("Card pocket", 105, 45, at=(200, 400),
                                  r=6))
    doc.add_shape(_piece("Tab", 30, 20, at=(400, 400)))
    asm.put_on(doc, pocket, asm.put_on(doc, panel, thickness=1.2),
               thickness=1.2)
    win = _window(qapp, doc, tmp_path, monkeypatch)
    try:
        # opened with a piece of the second stack selected on the drawing,
        # the window starts on that stack
        for it in win.canvas.scene_obj.items():
            if isinstance(it, ShapeItem) and it.model.name == "Card pocket":
                it.setSelected(True)
        was = win.doc.to_dict()
        win._assembly_preview()
        dlg = win._assembly_dlg
        assert [(f.kind, f.title) for f in dlg.items] == [
            ("stack", "Outer shell + 9 pieces"),
            ("stack", "Card back + 1 piece"), ("plain", "Tab")]
        # (two stacks, a rule, and the piece that is in neither)
        assert dlg.cmb_piece.count() == 4
        assert dlg.cmb_piece.itemData(2) is None
        assert dlg.cmb_piece.currentText() == "Card back + 1 piece"
        assert dlg.current.title == "Card back + 1 piece"
        # the pieces of the other stack are not offered to this one
        assert [(r.where, r.name) for r in dlg.pieces.rows()] == [
            ("in", "Card pocket"), ("in", "Card back"), ("out", "Tab")]
        assert dlg.view.caption.startswith("Put together  ·  2 pieces, "
                                           "2.4 mm thick")
        assert not dlg.view.cam.turned
        # the other one, from the Show box
        _choose(dlg, "Card pocket")
        dlg.cmb_piece.setCurrentIndex(0)
        assert dlg.current.title == "Outer shell + 9 pieces"
        assert _names(dlg)[:10] == ROWS and _names(dlg)[10:] == ["Tab"]
        assert dlg.pieces.chosen_keys() == []     # none of this one chosen
        assert dlg.view.cam.turned and dlg.view.chosen == ()
        assert dlg.tabs.currentWidget() is dlg.pieces
        # the piece in neither, last in the box: shown alone, with nothing
        # put on it
        dlg.cmb_piece.setCurrentIndex(3)
        assert (dlg.current.kind, dlg.current.title) == ("plain", "Tab")
        assert [(r.where, r.name) for r in dlg.pieces.rows()] == [
            ("shown", "Tab")]
        assert not dlg.slider_apart.isEnabled()
        assert [dlg.btn_views[k].text() for k in ("front", "back")] == \
            ["Front", "Back"]
        # ticked, it is a stack of its own, and still a stack with one
        # piece when shown again
        _tick(dlg, "Tab")
        assert [(f.kind, f.title) for f in dlg.items] == [
            ("stack", "Outer shell + 9 pieces"),
            ("stack", "Card back + 1 piece"), ("stack", "Tab")]
        assert dlg.current.title == "Tab" and dlg.cmb_piece.count() == 3
        assert [dlg.btn_views[k].text() for k in ("front", "back")] == \
            ["Top", "Under"]
        assert _lies(dlg) == {"Tab": "on the bench"}
        win.undo()
        _reread(dlg)
        assert win.doc.to_dict() == was
        assert (dlg.current.kind, dlg.current.title) == ("plain", "Tab")
    finally:
        _shut(win)


def test_a_net_and_a_plain_piece_are_made_again_as_they_were(qapp, mm):
    # panels drawn edge to edge: a net, shown as it always was
    doc = _drawing(_piece("Base", 60, 40, at=(30, 20)),
                   _piece("Side", 30, 40, at=(-15, 20)),
                   _piece("Apart", 20, 20, at=(300, 300)))
    dlg = _dialog(doc)
    try:
        assert [(f.kind, f.title) for f in dlg.items] == [
            ("net", "2 panels that share edges"), ("plain", "Base"),
            ("plain", "Side"), ("plain", "Apart")]
        assert dlg.current.kind == "net" and len(dlg.folds) == 1
        assert dlg.tabs.currentWidget() is dlg.folds_tab
        assert dlg.cmb_piece.count() == 5         # with the rule
        dlg.table.cellWidget(0, 3).setValue(45.0)
        cells = len(dlg.layout_.cells)
        rings = dlg.current.rings
        assert rings and dlg.layout_.thickness == 2.0
        # a net is no one piece: a thickness typed is for the window only
        was = doc.to_dict()
        dlg.spin_t.setValue(3.0)
        assert doc.to_dict() == was
        assert dlg.layout_.thickness == 3.0
        assert len(dlg.layout_.cells) == cells and dlg.current.rings == rings
        assert dlg.folds[0].angle == 45.0         # its settings as they were
        mm.set_current("in")
        assert len(dlg.layout_.cells) == cells and dlg.folds[0].angle == 45.0
        assert dlg.table.item(0, 5).text().endswith(" in")
        mm.set_current("mm")
        # none of its panels is listed as put on: they are pieces to tick
        assert [(r.where, r.name) for r in dlg.pieces.rows()] == [
            ("out", "Base"), ("out", "Side"), ("out", "Apart")]
        # one ticked starts a stack on it, and that is what is shown
        _tick(dlg, "Side")
        assert (dlg.current.kind, dlg.current.title) == ("stack", "Side")
        assert doc.shapes[1].placed.main and doc.shapes[1].thickness == 3.0
        assert dlg.items[0].kind == "stack" and dlg.items[1].kind == "net"
    finally:
        dlg.close()
    # a plain piece with a slot cut in it, shown alone: read in full, and
    # still in full when the window says everything again
    doc = _drawing(_piece("Panel", 105, 70, r=6),
                   _piece("Slot", 20, 3, at=(0, 25), r=1.5),
                   _piece("Other", 40, 40, at=(200, 0)))
    dlg = _dialog(doc)
    try:
        assert (dlg.current.kind, dlg.current.title) == ("plain", "Panel")
        assert len(dlg.layout_.cutouts) == 1
        model = dlg.layout_
        dlg.reload()
        assert dlg.layout_ is model               # nothing has changed
        mm.set_current("in")
        assert dlg.layout_ is not model and len(dlg.layout_.cutouts) == 1
        assert dlg.pieces.pieces.item(0, 1).text() == "(0.079 in)"
        mm.set_current("mm")
    finally:
        dlg.close()


# -- what the check of the window found ---------------------------------------

def test_the_generated_bifold_opens_on_its_stack_with_ten_rows(qapp, mm):
    doc = _drawing(*bifold_wallet(shell_t=1.6, inner_t=0.8))
    was = doc.to_dict()
    dlg = _dialog(doc)
    try:
        assert (dlg.current.kind, dlg.current.title) == \
            ("stack", "Outer shell + 9 pieces")
        assert _names(dlg) == ROWS
        assert [r.thickness for r in dlg.pieces.rows()] == [0.8] * 9 + [1.6]
        assert [round(s.lo, 6) for s in dlg.layout_.sheets] == [
            0.0, 0.8, 1.6, 2.4, 3.2, 0.8, 1.6, 2.4, 3.2]
        assert dlg.layout_.notes[0].a == _near(5.6)
        assert dlg.view.caption == ("Put together  ·  10 pieces, 5.6 mm "
                                    "thick  ·  1 step to fold")
        assert doc.to_dict() == was
    finally:
        dlg.close()


def test_a_stack_with_no_thickness_shows_at_the_window_s_and_writes_none(
        qapp, mm):
    doc = _drawing(*bifold_wallet())            # called with no thickness
    assert {s.thickness for s in doc.shapes} == {0.0}
    was = doc.to_dict()
    settings = QSettings(QSettings.IniFormat, QSettings.UserScope,
                         "stitch-hero-tests", "no-thickness")
    settings.setValue("fold3dThickness", 1.5)
    dlg = _dialog(doc, settings=settings)
    try:
        assert dlg.current.kind == "stack" and _names(dlg) == ROWS
        assert dlg.spin_t.value() == 1.5
        t = dlg.pieces.pieces
        assert [t.item(r, 1).text() for r in range(10)] == ["(1.5 mm)"] * 10
        # every piece at 1.5 mm: 6 layers at the thickest
        assert dlg.layout_.notes[0].a == _near(9.0)
        assert doc.to_dict() == was
    finally:
        dlg.close()
        settings.clear()


def test_underneath_puts_a_piece_next_to_the_main_piece_on_its_far_side(
        qapp, tmp_path, monkeypatch, mm):
    doc, by = _bifold()
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        dlg.tabs.setCurrentWidget(dlg.pieces)

        def order(name):
            return _named(win.doc, name).placed.order
        _choose(dlg, "Right pocket 1 (front)")
        dlg.pieces.btn_under.click()
        _choose(dlg, "Left pocket 1 (front)")
        dlg.pieces.btn_under.click()
        # the second goes between the first and the shell: next to it
        assert [order(n) for n in ("Right pocket 1 (front)",
                                   "Left pocket 1 (front)",
                                   "Outer shell")] == [1, 2, 3]
        assert _steps(win) == 2
        # both chosen, both under: On top puts them on the shell, next
        # to it, in the order they had
        _choose(dlg, "Right pocket 1 (front)", "Left pocket 1 (front)")
        assert dlg.pieces.btn_under.text() == "On top"
        dlg.pieces.btn_under.click()
        assert [order(n) for n in ("Outer shell", "Right pocket 1 (front)",
                                   "Left pocket 1 (front)",
                                   "Bill pocket liner")] == [1, 2, 3, 4]
        assert _steps(win) == 3
        win.undo()
        _reread(dlg)
        assert [order(n) for n in ("Right pocket 1 (front)",
                                   "Left pocket 1 (front)",
                                   "Outer shell")] == [1, 2, 3]
        assert _names(dlg)[-3:] == ["Outer shell", "Left pocket 1 (front)",
                                    "Right pocket 1 (front)"]
    finally:
        _shut(win)


def test_see_through_paints_what_lies_over_the_chosen_pieces_faint(
        qapp, mm):
    from leathercad_app.foldview import pieces_over
    doc, by = _bifold()
    # (the backing dark under pale pockets, to see the one through the
    # others)
    by["Left pocket backing"].leather = "Black"
    for k in ("3", "2", "1 (front)"):
        by[f"Left pocket {k}"].leather = "Natural"
    settings = QSettings(QSettings.IniFormat, QSettings.UserScope,
                         "stitch-hero-tests", "see-through")
    settings.clear()
    was = doc.to_dict()
    dlg = _dialog(doc, settings=settings)
    try:
        assert dlg.chk_through.text() == "See through"
        assert not dlg.chk_through.isChecked() and dlg.chk_through.isEnabled()
        _choose(dlg, "Left pocket backing")
        # (the strips in the spine's bend that the report warns of are
        # marked amber all the while: see test_stackfoldwindow.py)
        marked = dict(dlg.view.wash)
        assert all(f == 0 for _n, f in marked)

        def veiled():
            # the pieces the view paints faint, as it now stands
            return set(pieces_over(dlg.view.scene, dlg.view.cam,
                                   dlg.view.chosen)) \
                if dlg.view.see_through else set()
        assert veiled() == set()
        dlg.view.look_from("top")
        dlg.chk_through.setChecked(True)
        assert settings.value("fold3dSeeThrough", type=bool) is True
        # the three pockets over the backing, and none on the other side;
        # the marks are as they were
        over = {dlg.parts.number(by[f"Left pocket {k}"].shape_id)
                for k in ("3", "2", "1 (front)")}
        assert veiled() == over
        assert dlg.view.wash == marked
        # a pixel of the front pocket over it shows the dark backing
        # under it
        x = 50.8 if dlg.parts.turned else -50.8
        on, _at = _seen(dlg, (x, -28.0, 4.0))
        dlg.chk_through.setChecked(False)
        assert dlg.view.wash == marked
        off, _at = _seen(dlg, (x, -28.0, 4.0))
        assert sum(on) < sum(off) - 60
        dlg.chk_through.setChecked(True)
        # the main piece chosen, from the top: all nine lie over it
        _choose(dlg, "Outer shell")
        assert len(veiled()) == 9
        _choose(dlg)
        assert veiled() == set() and dlg.view.wash == marked
        assert doc.to_dict() == was
    finally:
        dlg.close()
    # remembered for next time
    dlg = _dialog(doc, settings=settings)
    try:
        assert dlg.chk_through.isChecked()
    finally:
        dlg.close()
        settings.clear()
    # a piece shown alone has nothing to see through
    one = _drawing(_piece("Panel", 120, 90),
                   _line((0, -45), (0, 45), "Fold"))
    dlg = _dialog(one)
    try:
        assert not dlg.chk_through.isEnabled()
    finally:
        dlg.close()


def test_a_main_piece_cut_open_is_said_to_be_cut_open_not_gone(qapp, mm):
    doc, by = _bifold()
    shell = by["Outer shell"]
    # the shell's outline opened, as Trim or Break apart leaves it (its
    # last stretch gone)
    opened = PathShape(points=[Vec2(p.x, p.y)
                               for p in shell.world_polyline()[0][:-2]],
                       close_path=False, name="Outer shell", layer="Cut")
    opened.placed = shell.placed
    doc.shapes[doc.shapes.index(shell)] = opened
    st, = asm.stacks(doc)
    assert st.lost and st.cut_open == [opened]
    dlg = _dialog(doc)
    try:
        assert dlg.current.title == "Bill pocket liner + 8 pieces"
        assert ("Outer shell, the piece the others are placed against, is "
                "cut open on the drawing. Until it is a closed outline "
                "again Bill pocket liner stands in for it, and the others "
                "are shown where they lay when it was cut open; closed "
                "again, it comes back where it lay, and they come right. "
                "Close its outline to go on placing pieces and putting "
                "more on.") in _lines(dlg)
        assert not any("gone from the drawing" in line
                       for line in _lines(dlg))
    finally:
        dlg.close()
    # a pocket cut open is left out, and said so
    doc, by = _bifold()
    pocket = by["Left pocket 2"]
    opened = PathShape(points=[Vec2(p.x, p.y)
                               for p in pocket.world_polyline()[0][:-2]],
                       close_path=False, name="Left pocket 2", layer="Cut")
    opened.placed = pocket.placed
    doc.shapes[doc.shapes.index(pocket)] = opened
    dlg = _dialog(doc)
    try:
        assert ("Left pocket 2 is cut open on the drawing and left out "
                "until it is a closed outline again.") in _lines(dlg)
    finally:
        dlg.close()


def test_the_window_fits_in_1220_by_740_with_the_see_through_tick(qapp, mm):
    doc, _by = _bifold()
    dlg = _dialog(doc)
    try:
        dlg.resize(1220, 740)
        QApplication.processEvents()
        assert dlg.minimumSizeHint().width() <= 1220
        assert (dlg.width(), dlg.height()) == (1220, 740)
        assert min(dlg.btn_views[k].width() for k in dlg.btn_views) >= 56
        assert dlg.chk_through.width() >= dlg.chk_through.sizeHint().width()
    finally:
        dlg.close()


def _select(win, *shapes):
    """Select these shapes on the canvas, as a click on them does."""
    from leathercad_app.items import ShapeItem
    c = win.canvas
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem) and any(it.model is s for s in shapes):
            it.setSelected(True)
    QApplication.processEvents()
    return [it for it in c.selected_items() if isinstance(it, ShapeItem)]


def test_a_pocket_ticked_onto_its_backing_stays_on_it_when_the_shell_widens(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Shell", 200, 90),
                   _piece("Backing", 98, 70, at=(250, 0)),
                   _piece("Pocket", 98, 40, at=(400, 0)))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        _tick(dlg, "Backing")               # a stack on the shell shown
        _choose(dlg, "Backing")
        dlg.pieces.spin_x.setValue(3.0)     # set in from the shell's edge
        dlg.pieces.spin_y.setValue(3.0)
        _choose(dlg, "Backing")
        _tick(dlg, "Pocket")                # on the backing chosen
        assert _lies(dlg)["Pocket"] == \
            "on Backing · bottom left, 3 mm across, 3 mm up"
        shell = _named(win.doc, "Shell")
        win.properties.show_selection(_select(win, shell))
        win.properties.w.setValue(220.0)
        win.properties.w.editingFinished.emit()
        _reread(dlg)
        st = dlg.current.stack
        box = {p.name: p.box for p in st.pieces}
        assert box["Backing"] == _near((-107.0, -42.0, -9.0, 28.0))
        assert box["Pocket"] == _near((-107.0, -42.0, -9.0, -2.0))
        assert box["Pocket"][0] - box["Backing"][0] == _near(0.0)
    finally:
        _shut(win)


def _break_shell(win):
    """Break the shell apart on the canvas: its edges come back, still
    chosen."""
    from leathercad_app.items import ShapeItem
    _select(win, _named(win.doc, "Outer shell"))
    win.canvas.break_apart_selected()
    return [it.model for it in win.canvas.selected_items()
            if isinstance(it, ShapeItem)]


def test_with_the_shell_cut_open_each_piece_is_laid_and_counted_once(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(*bifold_wallet(shell_t=1.6, inner_t=0.8))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        edges = _break_shell(win)
        _reread(dlg)
        st = dlg.current.stack
        assert [p.name for p in st.pieces].count("Bill pocket liner") == 1
        assert st.main.name == "Bill pocket liner" and not st.main.above
        assert len(st.over()) + len(st.under()) == 8
        # nine pieces, as many layers in the model, 4 mm at the thickest
        assert len(dlg.layout_.sheets) + 1 == 9
        assert dlg.view.caption == ("Put together  ·  9 pieces, 4 mm "
                                    "thick  ·  nothing folds")
        said = _lines(dlg)[0]
        assert said.startswith("Put together: 9 pieces, 4 mm at the "
                               "thickest (Bill pocket liner, ")
        assert said.count("Bill pocket liner") == 1
        # joined again: ten, and 5.6 mm
        _select(win, *edges)
        win.canvas.join_selected()
        _reread(dlg)
        assert len(dlg.layout_.sheets) + 1 == 10
        assert dlg.view.caption == ("Put together  ·  10 pieces, 5.6 mm "
                                    "thick  ·  1 step to fold")
    finally:
        _shut(win)


def test_with_the_main_piece_cut_open_the_controls_that_move_pieces_are_grey(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(*bifold_wallet(shell_t=1.6, inner_t=0.8))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    p = dlg.pieces
    moving = [p.btn_higher, p.btn_lower, p.btn_over, p.btn_under,
              p.btn_mirror, p.btn_main, p.spin_x, p.spin_y, p.spin_turn,
              p.btn_ccw, p.btn_cw] + [getattr(p, f"btn_{side}")
                                      for side in asm.SIDES]
    try:
        _choose(dlg, "Left pocket 2")
        assert all(w.isEnabled() for w in moving)
        edges = _break_shell(win)
        win.commit()
        _reread(dlg)
        assert asm.main_cut_open(win.doc, dlg.current.key)
        assert ("Outer shell, the piece the others are placed against, is "
                "cut open on the drawing. Until it is a closed outline "
                "again Bill pocket liner stands in for it, and the others "
                "are shown where they lay when it was cut open; closed "
                "again, it comes back where it lay, and they come right. "
                "Close its outline to go on placing pieces and putting "
                "more on.") in _lines(dlg)
        for names in (["Left pocket 2"], ["Bill pocket liner"],
                      ["Left pocket 2", "Right pocket 3"]):
            _choose(dlg, *names)
            assert [w for w in moving if w.isEnabled()] == []
            assert not p.btn_drawn.isEnabled()
            # its leather can still be chosen, and it can be taken off
            assert p.cmb_colour.isEnabled()
        steps = _steps(win)
        _tick(dlg, "Right pocket 1 (front)", False)
        assert _named(win.doc, "Right pocket 1 (front)").placed is None
        assert _steps(win) == steps + 1
        # ... but nothing is put on: the tick of a piece not in the stack
        # is grey, and ticked all the same it puts nothing on
        _reread(dlg)
        tick = p.pieces.item(_row(dlg, "Right pocket 1 (front)"), 0)
        assert not tick.flags() & Qt.ItemIsEnabled
        assert p.pieces.item(_row(dlg, "Left pocket 2"), 0).flags() \
            & Qt.ItemIsEnabled
        _tick(dlg, "Right pocket 1 (front)")
        assert _named(win.doc, "Right pocket 1 (front)").placed is None
        assert _steps(win) == steps + 1
        assert tick.checkState() == Qt.Unchecked
        # closed again, they can be used again
        _select(win, *edges)
        win.canvas.join_selected()
        _reread(dlg)
        assert not asm.main_cut_open(win.doc, dlg.current.key)
        _choose(dlg, "Left pocket 2")
        assert all(w.isEnabled() for w in moving)
        assert not any("Close its outline" in line for line in _lines(dlg))
    finally:
        _shut(win)


def test_a_fob_nested_in_a_window_s_waste_is_listed_and_put_on_by_a_tick(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Panel", 200, 120), _piece("Window", 120, 80),
                   _piece("Fob", 25, 25))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        p = dlg.pieces
        assert [(r.where, r.name, r.lies) for r in p.rows()] == [
            ("shown", "Panel", ""),
            ("inside", "Window", "drawn inside Panel"),
            ("inside", "Fob", "drawn inside Window")]
        assert p.pieces.cellWidget(1, 0).text() == "Drawn inside a piece"
        # ticked, it is put on: a stack is started on the panel shown
        _tick(dlg, "Fob")
        assert _named(win.doc, "Fob").placed is not None
        assert _named(win.doc, "Panel").placed.main
        assert _steps(win) == 1
        assert [(r.where, r.name) for r in p.rows()] == [
            ("in", "Fob"), ("in", "Panel"), ("inside", "Window")]
        # taken off again, "As drawn" passes it over
        _tick(dlg, "Fob", False)
        assert _steps(win) == 2
        _choose(dlg, "Fob")
        assert not p.btn_drawn.isEnabled()
        _choose(dlg, "Window")
        assert p.btn_drawn.isEnabled()
    finally:
        _shut(win)


def test_two_stacks_of_one_title_are_told_apart_in_the_show_box(qapp, mm):
    from leathercad_app.preview3d import showables
    doc = _drawing(*bifold_wallet(), *bifold_wallet())
    assert len(asm.stacks(doc)) == 2
    assert [f.title for f in showables(doc) if f.kind == "stack"] == [
        "Outer shell + 9 pieces", "Outer shell + 9 pieces (2)"]
    dlg = _dialog(doc)
    try:
        assert [dlg.cmb_piece.itemText(k) for k in range(2)] == [
            "Outer shell + 9 pieces", "Outer shell + 9 pieces (2)"]
    finally:
        dlg.close()


def test_a_pocket_ticked_onto_a_liner_as_wide_as_the_shell_stays_level(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Shell", 200, 90),
                   _piece("Liner", 200, 80, at=(250, 0)),
                   _piece("Pocket", 90, 40, at=(500, 0)))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        for name in ("Shell", "Liner", "Pocket"):
            _tick(dlg, name)                # each on the top of the pile
        assert _lies(dlg)["Pocket"] == "on Liner · bottom left"
        pocket = _named(win.doc, "Pocket")
        win.properties.show_selection(_select(win, pocket))
        win.properties.w.setValue(110.0)
        win.properties.w.editingFinished.emit()
        _reread(dlg)
        box = {p.name: p.box for p in dlg.current.stack.pieces}
        assert box["Pocket"][0] - box["Shell"][0] == _near(0.0)
        assert _lies(dlg)["Pocket"] == "on Liner · bottom left"
        assert not any("past the edge" in line for line in _lines(dlg))
    finally:
        _shut(win)


def test_across_typed_for_a_backing_on_the_liner_moves_it_that_far(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Shell", 200, 90),
                   _piece("Liner", 200, 80, at=(250, 0)),
                   _piece("Backing", 98, 70, at=(500, 0)))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        p = dlg.pieces
        _tick(dlg, "Shell")
        _tick(dlg, "Liner")
        _choose(dlg, "Liner")
        _tick(dlg, "Backing")
        _choose(dlg, "Backing")
        assert p.spin_x.value() == 0.0
        assert p.lbl_from.text() == "from its left and bottom edges"

        def left():
            return dlg.current.stack.piece(
                _named(win.doc, "Backing").shape_id).box[0]
        was = left()
        p.spin_x.setValue(4.0)
        _reread(dlg)
        assert left() - was == _near(4.0)
        assert _lies(dlg)["Backing"] == "on Liner · bottom left, 4 mm across"
    finally:
        _shut(win)


def test_a_main_piece_on_a_layer_that_is_not_cut_is_said_to_be_there(
        qapp, mm):
    from leathercad.layers import Layer
    doc, by = _bifold()
    doc.layers.append(Layer(name="Score", role="score"))
    by["Outer shell"].layer = "Score"
    st, = asm.stacks(doc)
    assert st.uncut == [by["Outer shell"]] and asm.main_cut_open(doc, st.key)
    dlg = _dialog(doc)
    try:
        assert ("Outer shell, the piece the others are placed against, is "
                "on a layer that is not cut. Until it is on a cut layer "
                "again Bill pocket liner stands in for it, and the others "
                "are shown where they lay when it was moved; back on a cut "
                "layer, it comes back where it lay, and they come right. "
                "Put it back on a cut layer to go on placing pieces and "
                "putting more on.") in _lines(dlg)
        assert not any("cut open" in line for line in _lines(dlg))
    finally:
        dlg.close()
    # a pocket moved there is said to be there, not cut open
    doc, by = _bifold()
    doc.layers.append(Layer(name="Score", role="score"))
    by["Left pocket 2"].layer = "Score"
    dlg = _dialog(doc)
    try:
        assert ("Left pocket 2 is on a layer that is not cut and left out "
                "until it is on a cut layer again.") in _lines(dlg)
    finally:
        dlg.close()


def _widen_shell(win, dlg, width):
    shell = _named(win.doc, "Shell")
    win.properties.show_selection(_select(win, shell))
    win.properties.w.setValue(width)
    win.properties.w.editingFinished.emit()
    _reread(dlg)
    return {p.name: p.box for p in dlg.current.stack.pieces}


def test_a_pocket_ticked_onto_a_liner_moved_across_moves_with_the_liner(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Shell", 200, 90),
                   _piece("Liner", 200, 80, at=(250, 0)),
                   _piece("Pocket", 90, 40, at=(500, 0)))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        _tick(dlg, "Liner")                 # a stack on the shell shown
        _choose(dlg, "Liner")
        dlg.pieces.spin_x.setValue(20.0)
        _choose(dlg, "Liner")
        _tick(dlg, "Pocket")
        # level with nothing of the shell: held as the liner is
        assert _lies(dlg)["Pocket"] == \
            "on Liner · bottom centre, -35 mm across"
        box = _widen_shell(win, dlg, 220.0)
        assert box["Pocket"][0] - box["Liner"][0] == _near(0.0)
    finally:
        _shut(win)


def test_pockets_taken_along_by_line_up_stay_on_their_backing(
        qapp, tmp_path, monkeypatch, mm):
    doc = _drawing(_piece("Shell", 200, 90),
                   _piece("Backing", 98, 70, at=(250, 0)),
                   _piece("Pocket", 70, 40, at=(400, 0)),
                   _piece("Pocket B", 98, 30, at=(550, 0)))
    win, dlg = _opened(qapp, doc, tmp_path, monkeypatch)
    try:
        p = dlg.pieces
        _tick(dlg, "Backing")
        _tick(dlg, "Pocket")
        _choose(dlg, "Backing")
        _tick(dlg, "Pocket B")
        assert p.chk_along.isChecked()
        _choose(dlg, "Backing")
        steps = _steps(win)
        p.btn_right.click()
        assert _steps(win) == steps + 1       # one step, the lot
        # held by the shell's right edge, as the backing is
        assert _lies(dlg)["Pocket"] == \
            "on Backing · bottom right, -28 mm across"
        assert _lies(dlg)["Pocket B"] == "on Pocket · bottom right"
        box = _widen_shell(win, dlg, 220.0)
        for name in ("Pocket", "Pocket B"):
            assert box[name][0] - box["Backing"][0] == _near(0.0)
    finally:
        _shut(win)


def test_a_stack_left_with_one_piece_still_says_what_is_left_out(qapp, mm):
    from leathercad.layers import Layer
    doc = _drawing(_piece("Shell", 200, 90),
                   _piece("Liner", 200, 80, at=(250, 0)),
                   _piece("Pocket", 90, 40, at=(500, 0)))
    shell, liner, pocket = doc.shapes
    key = asm.put_on(doc, shell, thickness=2.0)
    asm.put_on(doc, liner, key, thickness=2.0)
    asm.put_on(doc, pocket, key, thickness=2.0)
    doc.layers.append(Layer(name="Score", role="score"))
    shell.layer = pocket.layer = "Score"
    dlg = _dialog(doc)
    try:
        assert dlg.current.title == "Liner"
        assert _lines(dlg) == [
            "Shell, the piece the others are placed against, is on a layer "
            "that is not cut. Until it is on a cut layer again Liner stands "
            "in for it, and the others are shown where they lay when it was "
            "moved; back on a cut layer, it comes back where it lay, and "
            "they come right. Put it back on a cut layer to go on placing "
            "pieces and putting more on.",
            "Pocket is on a layer that is not cut and left out until it is "
            "on a cut layer again."]
    finally:
        dlg.close()

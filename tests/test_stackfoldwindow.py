"""The 3D assembly preview folds a stack: the generated bifold closes with
its card pockets on, the Folds tab works for a stack, the report says
what each fold needs, and what it warns of is marked on the model."""

import math
import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad import assembly as asm  # noqa: E402
from leathercad import foldsolid as fs  # noqa: E402
from leathercad import templates  # noqa: E402
from leathercad.document import Document  # noqa: E402
from leathercad.generators import bifold_wallet  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import PathShape, Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402

POCKETS = ("pocket backing", "pocket 3", "pocket 2", "pocket 1 (front)")


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


def _line(a, b, name="", way="front", angle=180.0):
    ln = PathShape(points=[Vec2(*a), Vec2(*b)], close_path=False, name=name,
                   layer="Score")
    ln.fold_dir, ln.fold_angle = way, angle
    return ln


def _seam(a, b, pitch=5.0):
    """A stitched line, drawn over whatever pieces it is to be sewn
    through."""
    ln = PathShape(points=[Vec2(*a), Vec2(*b)], close_path=False,
                   layer="Score")
    ln.stitch = StitchSettings(enabled=True, inset=0.0, pitch_mm=pitch)
    return ln


def _drawing(*shapes):
    doc = Document("pieces")
    for s in shapes:
        doc.add_shape(s)
    return doc


def _generated(**kw):
    """The bifold as Edit > Generate makes it: put together, the shell
    1.6 mm, the liner and pockets 0.8 mm."""
    doc = _drawing(*bifold_wallet(shell_t=1.6, inner_t=0.8, **kw))
    return doc, {s.name: s for s in doc.shapes}


def _dialog(doc, **kw):
    from leathercad_app.preview3d import FoldPreviewDialog
    dlg = FoldPreviewDialog(doc=doc, **kw)
    dlg.show()
    QApplication.processEvents()
    return dlg


def _window(qapp, doc, tmp_path, monkeypatch):
    from leathercad_app.mainwindow import MainWindow
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(MainWindow, "_settings",
                        lambda self: QSettings(ini,
                                               QSettings.Format.IniFormat))
    win = MainWindow(doc)
    win.canvas.rebuild()
    win._assembly_preview()
    return win, win._assembly_dlg


def _shut(win):
    dlg = getattr(win, "_assembly_dlg", None)
    if dlg is not None:
        dlg.close()
    win._unsaved_changes = False
    win.hide()


def _steps(win) -> int:
    return win.history._index


def _words(dlg) -> str:
    return re.sub(r"<[^>]+>", "", dlg.readout.text().replace("<br>", "\n"))


def _lines(dlg):
    return [line for line in _words(dlg).split("\n") if line]


def _near(values, abs=1e-6):
    return pytest.approx(values, abs=abs)


def _rgb(c):
    return (c.red(), c.green(), c.blue())


def _seen(dlg, point, half=2):
    """The colour the view shows at a point of the model, on average over
    a patch ``half`` pixels each way."""
    view = dlg.view
    view.repaint()
    img = view.grab().toImage()
    w, h = view.width(), view.height()
    ox, ax, ay, az, oy, bx, by, bz = view.cam.mapping(w, h)
    x = ox + ax * point[0] + ay * point[1] + az * point[2]
    y = oy + bx * point[0] + by * point[1] + bz * point[2]
    k = img.width() / w
    r = g = b = n = 0
    for i in range(int(x * k) - half, int(x * k) + half + 1):
        for j in range(int(y * k) - half, int(y * k) + half + 1):
            c = img.pixelColor(i, j)
            r, g, b, n = r + c.red(), g + c.green(), b + c.blue(), n + 1
    return (r / n, g / n, b / n)


# -- the generated bifold -----------------------------------------------------

def test_the_generated_bifold_opens_flat_and_shuts_97_66_by_90_by_11_2(
        qapp, mm):
    doc, by = _generated()
    was = doc.to_dict()
    dlg = _dialog(doc)
    try:
        assert doc.to_dict() == was               # opening writes nothing
        assert (dlg.current.kind, dlg.current.title) == \
            ("stack", "Outer shell + 9 pieces")
        lay = dlg.layout_
        assert isinstance(lay, fs.Layout) and len(lay.sheets) == 9
        assert lay.n_steps == 1 and dlg.view.t == 0.0
        assert dlg.view.caption == ("Put together  ·  10 pieces, 5.6 mm "
                                    "thick  ·  1 step to fold")
        assert dlg.view.alarm == ""
        # the fold's row: what Auto comes to for the stack, what it takes
        assert dlg.table.item(0, 0).text() == "A  Spine fold"
        assert dlg.table.cellWidget(0, 4).specialValueText() == \
            "Auto (4 mm)"
        assert dlg.table.item(0, 5).text() == "15.08 mm"
        assert lay.radius == _near([4.0]) and lay.used == _near([4.8 *
                                                                 math.pi])
        # flat: the pile on the bench, 5.6 mm thick
        lo, hi = dlg.view.scene.bounds()
        assert (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) == \
            _near((199.2, 90.0, 5.6))
        # End shuts it: the caption and the model's box say how big
        dlg._go(float("inf"))
        assert dlg.view.t == 1.0
        assert dlg.view.caption == "Folded  ·  97.66 × 90 × 11.2 mm"
        lo, hi = dlg.view.scene.bounds()
        assert (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) == \
            _near((97.660178, 90.0, 11.2))
        # half way on the slider, the step and the fold
        dlg.slider.setValue(dlg.slider.maximum() // 2)
        assert dlg.view.t == 0.5
        assert dlg.view.caption == "Step 1 of 1:  fold A to the back 180°"
        assert not dlg.slider_apart.isEnabled()
        # back to flat with Home
        dlg._go(0.0)
        assert dlg.view.caption.endswith("1 step to fold")
        assert dlg.slider_apart.isEnabled()
        assert doc.to_dict() == was
    finally:
        dlg.close()


BIFOLD_MM = [
    "Put together: 10 pieces, 5.6 mm at the thickest (Outer shell, Bill "
    "pocket liner, Left pocket backing, Left pocket 3, Left pocket 2, Left "
    "pocket 1 (front)).",
    "Left pocket backing (and 7 more) stand 2.49 mm past the edge of Outer "
    "shell.",
    "Fold A (Spine fold) closes over 8 mm of leather, 4 mm on each half: "
    "Left pocket 1 (front) comes to rest on Right pocket 1 (front). It "
    "bends at 4 mm and takes 15.08 mm of Outer shell, 7.54 mm each side of "
    "the fold line.",
    "⚠ Left pocket backing (and 7 more) reach 5.54 mm into the bend of fold "
    "A, where leather cannot lie flat. They are shown bent with it. Stop "
    "them 7.54 mm from the fold line: make them 5.54 mm narrower.",
    "⚠ Bill pocket liner goes round the inside of fold A and has 3.77 mm "
    "too much leather there. Cut it 3.77 mm shorter across the fold and it "
    "lies smooth and comes level when shut; sewn in flat at this length it "
    "wrinkles inside the fold. The picture squeezes it to fit (marked "
    "amber).",
    "Folded it measures 97.66 × 90 × 11.2 mm; the spine is 11.2 mm across. "
    "A needle through it goes through 11.2 mm of leather at the most and "
    "3.2 mm at the least.",
    "Stitch holes through the layers: Outer shell's holes pass through "
    "Bill pocket liner (142), Left pocket backing (44), Left pocket 3 (37), "
    "Left pocket 2 (35), Left pocket 1 (front) (32), Right pocket backing "
    "(44), Right pocket 3 (37), Right pocket 2 (35) and Right pocket 1 "
    "(front) (32), which have no holes there. The laser will not cut them "
    "in those pieces."]


def test_the_bifold_s_report_says_what_the_fold_needs_in_mm_and_inches(
        qapp, mm):
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        assert _lines(dlg) == BIFOLD_MM
        text = dlg.readout.text()
        # the warnings in amber, as their strips are marked; nothing red
        assert text.count("<span style='color:#b35c00'>⚠") == 2
        assert "#c0392b" not in text
        assert "<b>Folded</b> it measures" in text
        mm.set_current("in")
        says = _lines(dlg)
        assert says[2:6] == [
            "Fold A (Spine fold) closes over 0.315 in of leather, 0.157 in "
            "on each half: Left pocket 1 (front) comes to rest on Right "
            "pocket 1 (front). It bends at 0.157 in and takes 0.594 in of "
            "Outer shell, 0.297 in each side of the fold line.",
            "⚠ Left pocket backing (and 7 more) reach 0.218 in into the "
            "bend of fold A, where leather cannot lie flat. They are shown "
            "bent with it. Stop them 0.297 in from the fold line: make them "
            "0.218 in narrower.",
            "⚠ Bill pocket liner goes round the inside of fold A and has "
            "0.148 in too much leather there. Cut it 0.148 in shorter "
            "across the fold and it lies smooth and comes level when shut; "
            "sewn in flat at this length it wrinkles inside the fold. The "
            "picture squeezes it to fit (marked amber).",
            "Folded it measures 3.845 × 3.543 × 0.441 in; the spine is "
            "0.441 in across. A needle through it goes through 0.441 in of "
            "leather at the most and 0.126 in at the least."]
        assert "mm" not in _words(dlg)
        dlg._go(1.0)
        assert dlg.view.caption == "Folded  ·  3.845 × 3.543 × 0.441 in"
        assert dlg.table.item(0, 5).text() == "0.594 in"
        assert dlg.table.cellWidget(0, 4).specialValueText() == \
            "Auto (0.157 in)"
        mm.set_current("mm")
        assert _lines(dlg) == BIFOLD_MM
    finally:
        dlg.close()


def test_pockets_narrower_and_a_shorter_liner_take_the_warnings_away(
        qapp, mm):
    doc, by = _generated()
    # on the drawing: the eight pockets 5.54 mm narrower (their outer
    # edges are held level with the shell's), the liner 3.77 mm shorter
    for side in ("Left", "Right"):
        for name in POCKETS:
            by[f"{side} {name}"].width = 97.6 - 5.54
    by["Bill pocket liner"].width = 199.2 - 3.77
    dlg = _dialog(doc)
    try:
        lay = dlg.layout_
        assert lay.radius == _near([4.0])
        assert [n.kind for n in lay.notes if n.fold == 0] == [
            "meet", "bend", "closes", "level", "spine"]
        says = _lines(dlg)
        assert "⚠" not in _words(dlg)
        assert ("Bill pocket liner goes round the inside of fold A and is "
                "already 3.77 mm shorter than Outer shell across it: shut, "
                "it comes level (the picture does not slide it).") in says
        # nothing is marked on the model
        assert dlg.view.wash == {}
        # the pockets stop where the bend begins: 7.54 mm from the line
        x = [p[0] for s in lay.sheets[1:] for p in s.ring]
        assert min(abs(v) for v in x) == _near(7.54)
    finally:
        dlg.close()
    # the bifold made with room for the fold: no warning at all
    doc, _by = _generated(fit=True)
    dlg = _dialog(doc)
    try:
        lay = dlg.layout_
        assert lay.radius == _near([4.0])
        assert not [n for n in lay.notes
                    if n.kind in ("reach", "cross", "clash", "limit")]
        assert "⚠" not in _words(dlg) and dlg.view.wash == {}
        assert ("Bill pocket liner goes round the inside of fold A and is "
                "already 3.77 mm shorter than Outer shell across it: shut, "
                "it comes level (the picture does not slide it).") \
            in _lines(dlg)
        dlg._go(1.0)
        # half of 210.28 mm of shell, less half its bend, and the 5.6 mm
        # round of the spine
        assert dlg.view.caption == "Folded  ·  103.2 × 90 × 11.2 mm"
    finally:
        dlg.close()


# -- marked on the model ------------------------------------------------------

def test_amber_is_on_the_strips_the_report_warns_of_and_nowhere_else(
        qapp, mm):
    doc, by = _generated()
    dlg = _dialog(doc)
    try:
        view = dlg.view
        look = view.look
        # the bend's strips of the liner and the eight pockets, amber;
        # nothing red, and nothing over the whole of any piece
        assert set(view.wash) == {(k, 0) for k in range(1, 10)}
        assert all(_rgb(c) == _rgb(look.warn) for c in view.wash.values())
        # in the picture: amber in the bend, the leather beyond it
        dlg.chk_labels.setChecked(False)
        dlg._look("front")
        pocket = dlg.parts.names.index("Left pocket 1 (front)")
        top = -(0.8 + dlg.layout_.sheets[pocket - 1].hi)
        x = -3.0 if dlg.parts.turned else 3.0
        band, beyond = (x, -20.0, top), (x * 15, -20.0, top)
        marked = _seen(dlg, band), _seen(dlg, beyond)
        view.set_wash({})
        plain = _seen(dlg, band), _seen(dlg, beyond)
        dlg._see_through()                        # (the marks again)
        assert math.dist(marked[0], _rgb(look.warn)) < 60
        assert math.dist(marked[0], plain[0]) > 40
        assert math.dist(marked[1], plain[1]) < 3
        # chosen and seen through: the marks stay as they are, and the
        # view paints the pockets over the liner faint, marks and all
        from leathercad_app.foldview import pieces_over
        dlg.chk_through.setChecked(True)
        dlg.pieces.choose([by["Bill pocket liner"].shape_id])
        assert view.see_through
        assert set(view.wash) == {(k, 0) for k in range(1, 10)}
        assert all(_rgb(c) == _rgb(look.warn) for c in view.wash.values())
        assert pocket in pieces_over(view.scene, view.cam, view.chosen)
        seen = _seen(dlg, band)
        dlg.chk_through.setChecked(False)
        assert not view.see_through
        assert math.dist(seen, _seen(dlg, band)) > 20
    finally:
        dlg.close()


def _square():
    """A square folded in four, a patch laid over the crossing of its two
    folds: folded, the patch cannot be where it is put. 2 mm and 1 mm."""
    square = _piece("Square", 200, 200)
    patch = _piece("Patch", 80, 80, at=(400, 0))
    doc = _drawing(square, patch, _line((0, -100), (0, 100), "Across"),
                   _line((-100, 0), (100, 0), "Up"))
    doc.shapes[2].fold_order, doc.shapes[3].fold_order = 1, 2
    key = asm.put_on(doc, square, thickness=2.0)
    asm.put_on(doc, patch, key, thickness=1.0)
    asm.line_up(doc, patch, "centre")
    asm.line_up(doc, patch, "middle")
    return doc


def _channel():
    """A channel 240 x 60 mm, 2 mm: both walls stand up, the left one
    first, each with a tall pile on it (60 mm and 40 mm). Standing up,
    the right wall's pile sweeps through the left wall's on the way and
    is clear of it when the wall stands."""
    return _drawn(_box(0, 0, 240, 60),
                  [((80, 0), (80, 60), 90.0, 1, 1, -1.0),
                   ((160, 0), (160, 60), 90.0, 1, 2, -1.0)],
                  [("Left pile", _box(25, 10, 45, 50), 60.0, 1),
                   ("Right pile", _box(165, 10, 185, 50), 40.0, 1)])


def test_a_stack_built_to_clash_is_said_in_red_and_marked_red(qapp, mm):
    dlg = _dialog(_tray())
    try:
        found = [(n.sheet, n.other, round(n.a, 2), n.b, round(n.rest, 2))
                 for n in dlg.layout_.notes if n.kind == "clash"]
        assert found == [(0, (1,), 2.47, _near(2.9902, 1e-3), 1.0),
                         (1, (2,), 0.57, _near(2.9902, 1e-3), 0.57)]
        # both names, the depth where they end up, and when
        says = _lines(dlg)
        assert ("⚠ Left liner and Right liner are drawn 0.57 mm into each "
                "other at rest, from just before fold C has folded (step "
                "3).") in says
        assert ("⚠ Tray and Left liner are drawn 1 mm into each other at "
                "rest, and up to 2.47 mm on the way, from just before fold C "
                "has folded (step 3).") in says
        # both pieces marked red, whole, and counted in the caption
        bad = _rgb(dlg.view.look.bad)
        assert _rgb(dlg.view.wash[(1, None)]) == bad
        assert _rgb(dlg.view.wash[(2, None)]) == bad
        assert dlg.view.alarm.startswith("3 pieces")
    finally:
        dlg.close()
    # in inches no millimetre is left in what is said in red
    mm.set_current("in")
    dlg = _dialog(_tray())
    try:
        assert ("⚠ Left liner and Right liner are drawn 0.022 in into each "
                "other at rest, from just before fold C has folded (step "
                "3).") in _lines(dlg)
        assert "mm" not in _words(dlg)
    finally:
        dlg.close()


def test_two_piles_that_pass_only_on_the_way_are_said_plainly(qapp, mm):
    dlg = _dialog(_channel())
    try:
        clash, = [n for n in dlg.layout_.notes if n.kind == "clash"]
        assert (clash.sheet, clash.other, round(clash.a, 2), clash.b,
                clash.rest) == (1, (2,), 6.82, _near(1.3872, 1e-3), 0.0)
        # not an alarm: said plainly, nothing red, nothing counted
        assert ("On the way through step 2 the picture draws Left pile and "
                "Right pile into each other (up to 6.82 mm, from about three "
                "eighths of the way through); at rest they are clear. The "
                "preview turns each panel as one stiff piece, where leather "
                "gives.") in _lines(dlg)
        assert "⚠" not in _words(dlg) and "#c0392b" not in \
            dlg.readout.text()
        assert dlg.view.alarm == "" and dlg.view.wash == {}
        dlg._go(1.5)
        assert dlg.view.alarm == ""
    finally:
        dlg.close()
    mm.set_current("in")
    dlg = _dialog(_channel())
    try:
        assert "into each other (up to 0.269 in, from" in _words(dlg)
        assert "mm" not in _words(dlg)
    finally:
        dlg.close()


def test_a_square_folded_in_four_is_said_drawn_into_itself_and_its_patch_cut(
        qapp, mm):
    dlg = _dialog(_square())
    try:
        lay = dlg.layout_
        assert lay.n_steps == 2
        # the square in itself, a quarter of the way through its first
        # fold; nothing of two pieces in each other
        found = [(n.kind, n.sheet, n.other, round(n.a, 2), n.b,
                  round(n.rest, 2))
                 for n in lay.notes if n.kind in ("clash", "limit")]
        assert found == [("limit", 0, (0,), 3.11, _near(0.0339, 1e-3), 1.51)]
        says = _lines(dlg)
        assert ("⚠ Square is drawn 1.51 mm into itself at rest, and up to "
                "3.11 mm on the way, from about a sixteenth of the way "
                "through step 1: folds that cross are folded one after the "
                "other, and the preview cannot open the leather for the "
                "second.") in says
        # the patch is parted where each fold bends it on one side only,
        # and drawn into leather there: said with how long and how deep
        assert ("⚠ Patch is parted along 14.14 mm at fold A, with Square "
                "across it, and is drawn 1.47 mm into leather there, and up "
                "to 1.67 mm on the way: the fold bends it on one side and "
                "leaves it flat on the other. In leather it would have to "
                "be slit there, or stop short of the fold.") in says
        assert ("⚠ Patch is parted along 36.86 mm at fold B, with Square "
                "across it, and is drawn 0.51 mm into leather there, and up "
                "to 0.81 mm on the way: the fold bends it on one side and "
                "leaves it flat on the other. In leather it would have to "
                "be slit there, or stop short of the fold.") in says
        assert ("Patch is parted along 11 mm at fold B, with Square across "
                "it: the fold bends it on one side and leaves it flat on "
                "the other. In leather it would have to be slit there, or "
                "stop short of the fold.") in says
        assert ("Patch lies over the place where folds A and B cross. "
                "Square is folded along one and then the other and passes "
                "through itself there: the preview cannot show how Patch "
                "would lie.") in says
        # red: both pieces whole, and the square's own leather left in the
        # bend of fold B, which cannot close as drawn
        assert lay.stuck == [False, True]
        bad = _rgb(dlg.view.look.bad)
        assert {k: _rgb(c) for k, c in dlg.view.wash.items()
                if _rgb(c) == bad} == {(0, None): bad, (1, None): bad,
                                       (0, 1): bad}
        assert dlg.view.alarm == ("2 pieces are drawn into leather: see the "
                                  "report")
    finally:
        dlg.close()


def test_keep_clear_lines_are_drawn_flat_7_54_from_the_fold_line(qapp, mm):
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        scene = dlg.view.scene
        # flat: on each face, each side of the fold line, 7.54 mm from it
        xs = sorted({round(q[0], 4) for q, r, _n in scene.clear}
                    | {round(r[0], 4) for q, r, _n in scene.clear})
        assert xs == _near([-7.5398, 7.5398], abs=1e-4)
        assert all(q[0] == r[0] and abs(q[1] - r[1]) > 0
                   for q, r, _n in scene.clear)
        # ... lying on the top of whatever is there: the shell's own face,
        # the liner, the top of each pocket, 0.8 + 4 mm from the shell's
        # middle at the most (the pieces lie on the model's back: it is
        # looked at turned over)
        zs = {round(q[2], 6) for q, r, _n in scene.clear}
        assert zs == {0.8, -0.8, -1.6, -2.4, -3.2, -4.0, -4.8}
        # in the key
        dlg.view.repaint()
        # folded half way they are gone; flat again they are back
        dlg._go(0.5)
        assert dlg.view.scene.clear == []
        dlg._go(0.0)
        assert len(dlg.view.scene.clear) == len(scene.clear)
        # a stack whose fold is left flat has none
        it = dlg.table.item(0, 0)
        it.setCheckState(Qt.Unchecked)
        assert dlg.layout_.n_steps == 0 and dlg.view.scene.clear == []
        assert dlg.view.caption.endswith("nothing folds")
    finally:
        dlg.close()


# -- the Folds tab for a stack ------------------------------------------------

def test_flip_on_the_spine_fold_says_it_closes_away_from_the_pieces(
        qapp, mm):
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        way = dlg.table.cellWidget(0, 2)
        assert way.currentText() == "Back"
        assert way.toolTip() == ("Back: the flesh sides come together. "
                                 "Closes over the 9 pieces laid on Outer "
                                 "shell.")
        dlg.table.selectRow(0)
        dlg.btn_flip.click()
        way = dlg.table.cellWidget(0, 2)
        assert way.currentText() == "Front"
        assert way.toolTip() == ("Front: the grain sides come together. "
                                 "Closes away from the 9 pieces laid on "
                                 "Outer shell.")
        assert ("Fold A closes away from the 9 pieces laid on Outer shell: "
                "they end up on the outside. If they belong inside, press "
                "Flip.") in _lines(dlg)
        # stored on the fold line, as for a piece alone
        assert next(s for s in doc.shapes
                    if s.name == "Spine fold").fold_dir == "front"
        dlg.btn_flip.click()
        assert not any("closes away" in line for line in _lines(dlg))
    finally:
        dlg.close()


def _tee():
    """A T-shaped piece, its flap folded over along a line at the foot of
    the flap, and a strap across the end of that fold where the body
    goes on flat beside it. 2 mm and 1 mm."""
    tee = PathShape(points=[Vec2(*p) for p in (
        (0, 0), (100, 0), (100, 60), (70, 60), (70, 120), (30, 120),
        (30, 60), (0, 60))], close_path=True, name="Tee", layer="Cut")
    strap = _piece("Strap", 20, 30, at=(300, 0))
    hinge = _line((0, 65), (100, 65), "Flap fold")
    hinge.fold_radius = 6.0
    doc = _drawing(tee, strap, hinge)
    key = asm.put_on(doc, tee, thickness=2.0)
    asm.put_on(doc, strap, key, thickness=1.0)
    asm.line_up(doc, strap, "left")
    asm.line_up(doc, strap, "bottom")
    asm.set_offset(doc, strap, 20.0, 50.0)
    return doc


def test_a_strap_across_the_end_of_a_flap_s_hinge_is_said_and_shown_cut(
        qapp, mm):
    dlg = _dialog(_tee())
    try:
        lay = dlg.layout_
        assert [(n.sheet, n.fold) for n in lay.notes if n.kind == "slit"] \
            == [(1, 0)]
        slit, = [n for n in lay.notes if n.kind == "slit"]
        assert (round(slit.a, 2), round(slit.b, 4), slit.rest,
                slit.other) == (16.0, 0.3452, 0.0, (0,))
        # (into leather only on the way: plain)
        assert ("Strap is parted along 16 mm at fold A, with Tee across it: "
                "the fold bends it on one side and leaves it flat on the "
                "other. In leather it would have to be slit there, or stop "
                "short of the fold. On the way the picture draws it up to "
                "0.35 mm into leather there; it turns each panel as one "
                "stiff piece, where leather gives.") in _lines(dlg)
        assert dlg.view.alarm == ""
        assert (1, None) not in dlg.view.wash
        # shown cut: part of it bends with the flap, part stays flat on
        # the body beside it
        mine = [c for c in lay.sheet_cells if c.sheet == 1]
        assert any(c.hinge is not None for c in mine)
        body = lay.panels[lay.base].index
        assert any(c.hinge is None and c.panel == body for c in mine)
        # its leather goes round the bend and is not as long as the tee
        # across it: it is told as a strap, not a lining
        assert ("⚠ Strap goes round the inside of fold A and has 4.71 mm "
                "more leather there than the bend has room for: free at an "
                "end, it stands that much further out when shut; held at "
                "both ends, it wrinkles inside the fold. The picture "
                "squeezes it to fit (marked amber).") in _lines(dlg)
    finally:
        dlg.close()


def test_the_folds_tab_of_a_stack_stores_its_settings_on_the_fold_line(
        qapp, tmp_path, monkeypatch, mm):
    doc, by = _generated()
    win, dlg = _window(qapp, doc, tmp_path, monkeypatch)
    try:
        assert dlg.folds_tab.isEnabled() and _steps(win) == 0
        line = next(s for s in win.doc.shapes if s.name == "Spine fold")
        # angle and radius typed: on the fold line, one undo step
        dlg.table.cellWidget(0, 3).setValue(90.0)
        dlg.table.cellWidget(0, 4).setValue(6.0)
        assert (line.fold_angle, line.fold_radius) == (90.0, 6.0)
        assert _steps(win) == 0 and dlg._commit.isActive()
        dlg._flush_commit()
        assert _steps(win) == 1
        lay = dlg.layout_
        assert lay.radius == _near([6.0]) and lay.used == _near([
            0.5 * math.pi * 6.8])
        assert dlg.table.item(0, 5).text() == "10.68 mm"
        assert dlg.table.cellWidget(0, 4).text() == "6 mm"
        dlg._go(1.0)
        assert dlg.view.caption.startswith("Folded")
        # at 90 degrees nothing closes: no closing in its tooltip
        assert dlg.table.cellWidget(0, 2).toolTip() == \
            "Back: the flesh sides come together."
        win.undo()
        dlg._refresh.stop()
        dlg.reload()
        line = next(s for s in win.doc.shapes if s.name == "Spine fold")
        assert (line.fold_angle, line.fold_radius) == (180.0, -1.0)
        assert dlg.layout_.radius == _near([4.0])
        # unticked, the fold is left flat: the stack lies flat
        dlg.table.item(0, 0).setCheckState(Qt.Unchecked)
        assert dlg.layout_.n_steps == 0
        assert dlg.view.caption == ("Put together  ·  10 pieces, 5.6 mm "
                                    "thick  ·  nothing folds")
        assert not dlg.btn_play.isEnabled()
        assert dlg.table.item(0, 5).text() == "flat"
        assert not [n for n in dlg.layout_.notes if n.fold == 0]
        dlg.table.item(0, 0).setCheckState(Qt.Checked)
        assert dlg.layout_.n_steps == 1
        # "Stays still": the other half moves
        assert dlg.cmb_base.count() == 2
        dlg._go(1.0)
        lo, hi = dlg.view.scene.bounds()
        still = dlg.layout_.base
        dlg.cmb_base.setCurrentIndex(1 - still)
        assert dlg.layout_.base == 1 - still
        lo2, hi2 = dlg.view.scene.bounds()
        assert lo2[0] == _near(-hi[0]) and hi2[0] == _near(-lo[0])
        assert dlg.layout_.radius == _near([4.0])
        # (the ticks and the panel that stays still are the window's
        # own, as for a piece alone: nothing more was written)
        assert _steps(win) == 0 and not dlg._unsaved
    finally:
        _shut(win)


def test_steps_of_two_folds_typed_for_a_stack_are_one_undo_step(
        qapp, tmp_path, monkeypatch, mm):
    strip = _piece("Strip", 240, 60)
    lining = _piece("Lining", 200, 50, at=(0, 200))
    doc = _drawing(strip, lining, _line((-40, -30), (-40, 30), "Left"),
                   _line((40, -30), (40, 30), "Right"))
    key = asm.put_on(doc, strip, thickness=2.0)
    asm.put_on(doc, lining, key, thickness=1.0)
    asm.line_up(doc, lining, "centre")
    asm.line_up(doc, lining, "middle")
    win, dlg = _window(qapp, doc, tmp_path, monkeypatch)
    try:
        assert dlg.layout_.n_steps == 2
        assert [f.order for f in dlg.folds] == [1, 2]
        dlg.table.cellWidget(1, 1).setValue(1)    # together with the first
        assert [f.order for f in dlg.folds] == [1, 1]
        assert dlg.layout_.n_steps == 1
        assert [s.fold_order for s in win.doc.shapes[2:]] == [1, 1]
        dlg.table.selectRow(0)
        dlg.btn_later.click()                     # ... and after it
        assert [f.order for f in dlg.folds] == [2, 1]
        assert [s.fold_order for s in win.doc.shapes[2:]] == [2, 1]
        dlg.table.cellWidget(0, 1).setValue(1)
        assert dlg.layout_.n_steps == 1
        dlg._flush_commit()
        assert _steps(win) == 1
        assert dlg.view.caption == ("Put together  ·  2 pieces, 3 mm "
                                    "thick  ·  1 step to fold")
    finally:
        _shut(win)


def test_play_from_pulled_apart_brings_the_layers_together_then_folds(
        qapp, mm, monkeypatch):
    import types
    from leathercad_app import preview3d
    # a clock of the test's own, a tenth of a second a tick, however long
    # a tick takes on a busy machine
    clock = [100.0]
    monkeypatch.setattr(preview3d, "time",
                        types.SimpleNamespace(monotonic=lambda: clock[0]))
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        v = dlg.view
        dlg.slider_apart.setValue(100)            # 10 mm a layer
        assert v.apart == 10.0 and v.t == 0.0
        dlg._toggle_play()
        dlg._ticker.stop()                        # (ticked here by hand)
        steps = []
        for _ in range(12):
            clock[0] += 0.1
            dlg._tick()
            steps.append((v.apart, v.t))
        # the layers come together first, with nothing folded ...
        closing = [a for a, t in steps if a > 0.0]
        assert closing and all(t == 0.0 for a, t in steps if a > 0.0)
        assert closing == sorted(closing, reverse=True)
        # ... then it folds, with the layers together
        folding = [t for a, t in steps if a == 0.0]
        assert folding[-1] > 0.0 and folding == sorted(folding)
        assert dlg._playing
        # moving the fold slider lays any layers down
        dlg._stop()
        dlg._go(0.0)
        dlg.slider_apart.setValue(40)
        assert v.apart == 4.0
        dlg.slider.setValue(30)
        assert v.apart == 0.0 and dlg.slider_apart.value() == 0
    finally:
        dlg.close()


# -- what reads as before -----------------------------------------------------

def test_a_stack_with_nothing_to_fold_lies_flat_and_says_no_fold(qapp, mm):
    doc = templates.card_holder()
    dlg = _dialog(doc)
    try:
        assert dlg.current.kind == "stack" and dlg.layout_.n_steps == 0
        assert dlg.view.caption == ("Put together  ·  2 pieces, 2.4 mm "
                                    "thick  ·  nothing folds")
        says = _words(dlg)
        assert "Fold" not in says and "Folded" not in says
        assert "coming" not in says and "not made yet" not in says
        assert dlg.view.scene.clear == [] and dlg.view.alarm == ""
        # no fold lines: the Folds tab is grey, as it was
        assert not dlg.folds_tab.isEnabled() and dlg.table.rowCount() == 0
    finally:
        dlg.close()


def test_a_stack_of_one_piece_with_fold_lines_reads_as_the_piece_alone(
        qapp, mm):
    def drawn():
        return _drawing(_piece("Strip", 240, 100, at=(120, 50)),
                        _line((80, 0), (80, 100), "Left fold"),
                        _line((160, 0), (160, 100), "Right fold"))
    alone = _dialog(drawn())
    try:
        words, caption = alone.readout.text(), alone.view.caption
        alone._go(2.0)
        shut = alone.view.caption
        tips = [alone.table.cellWidget(r, 2).toolTip() for r in (0, 1)]
    finally:
        alone.close()
    doc = drawn()
    asm.put_on(doc, doc.shapes[0], thickness=2.0)
    dlg = _dialog(doc)
    try:
        assert dlg.current.kind == "stack" and not dlg.layout_.sheets
        assert dlg.readout.text() == words and words
        assert dlg.view.caption == caption == "Flat  ·  2 steps to fold"
        dlg._go(2.0)
        assert dlg.view.caption == shut == "Folded"
        assert [dlg.table.cellWidget(r, 2).toolTip() for r in (0, 1)] \
            == tips == ["", ""]
        assert dlg.view.scene.clear == [] and dlg.view.wash == {}
    finally:
        dlg.close()


# -- stitch holes of a stack that folds ---------------------------------------

def test_the_holes_of_the_generated_bifold_are_read_through_its_layers(
        qapp, mm):
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        check = dlg.view.check
        assert not [f for f in check.findings if not f.laid]
        assert sum(len(v) for v in check.blanks.values()) == 438
        assert check.readings == {}
        assert _lines(dlg)[-1].startswith("Stitch holes through the layers: "
                                          "Outer shell's holes pass")
        assert "face to face" not in _words(dlg)
        assert "brings pieces together" not in _words(dlg)
    finally:
        dlg.close()


def _card_wallet(off=0.0):
    """A 200 x 100 card wallet folded in half, a card pocket on each half,
    each sewn along its top with a row of holes; the right one's row is
    ``off`` mm along from where the fold brings the left one's."""
    body = _piece("Body", 200, 100)
    left = _piece("Left pocket", 80, 80, at=(0, 300))
    right = _piece("Right pocket", 80, 80, at=(300, 300))
    doc = _drawing(body, left, right, _line((0, -50), (0, 50), "Fold"),
                   _seam((-30, 330), (30, 330)),
                   _seam((270 + off, 330), (330 + off, 330)))
    key = asm.put_on(doc, body, thickness=2.0)
    for pocket, ax in ((left, "left"), (right, "right")):
        asm.put_on(doc, pocket, key, thickness=1.0)
        asm.line_up(doc, pocket, ax)
        asm.line_up(doc, pocket, "middle")
        asm.set_offset(doc, pocket, 10.0 if ax == "left" else -10.0, 0.0)
    return doc


def test_pieces_the_fold_brings_together_have_their_holes_said(qapp, mm):
    dlg = _dialog(_card_wallet())
    try:
        faced = [f for f in dlg.view.check.findings if not f.laid]
        assert [(f.names, f.panels, f.met, f.off) for f in faced] == [
            (("Left pocket", "Right pocket"), (1, 2), 26, 0)]
        assert ("Stitch holes where the folding brings pieces together: "
                "Left pocket (panel 1) and Right pocket (panel 2): ● all 26 "
                "line up.") in _lines(dlg)
    finally:
        dlg.close()
    dlg = _dialog(_card_wallet(1.5))
    try:
        assert ("Stitch holes where the folding brings pieces together: "
                "Left pocket (panel 1) and Right pocket (panel 2): ● 26 are "
                "out of line, by up to 1.5 mm.") in _lines(dlg)
    finally:
        dlg.close()


def _lined(pull=0.0):
    """A 200 x 100 piece folded in half, 2 mm, with a 1 mm lining as long
    as it round the inside, each sewn along its top with a row of holes
    either side of the fold. The lining's rows are ``pull`` mm nearer the
    fold."""
    shell = _piece("Shell", 200, 100)
    lining = _piece("Lining", 200, 98, at=(0, 300))
    doc = _drawing(shell, lining, _line((0, -50), (0, 50), "Fold"),
                   _seam((-90, 45), (-10, 45)), _seam((10, 45), (90, 45)),
                   _seam((-90 + pull, 346), (-10 + pull, 346)),
                   _seam((10 - pull, 346), (90 - pull, 346)))
    key = asm.put_on(doc, shell, thickness=2.0)
    asm.put_on(doc, lining, key, thickness=1.0)
    asm.line_up(doc, lining, "centre")
    asm.line_up(doc, lining, "bottom")
    return doc


# how the stitch holes of a stack whose pieces go round a fold are read:
# the whole stack one way, said once, with how many of its holes meet so
READ_FLAT = ("Stitch holes of the pieces that go round a fold: the stack is "
             "read as punched to meet lying flat, the reading in which at "
             "least as many of the whole stack's holes meet as the other: "
             "{}. The marks on the model show that reading.")
READ_SHUT = ("Stitch holes of the pieces that go round a fold: the stack is "
             "read as punched to meet when shut, each piece's length round "
             "its folds taken up, the reading in which more of the whole "
             "stack's holes meet: {}. The marks on the model show that "
             "reading.")


def test_a_lining_with_holes_round_a_fold_is_read_both_ways(qapp, mm):
    over = 1.5 * math.pi
    dlg = _dialog(_lined())
    try:
        # (held as the fold engine finds best, 33 would meet shut)
        assert dlg.view.check.readings == {1: (34, 33, False)}
        says = _lines(dlg)
        assert READ_FLAT.format("all 68 do, against 67 when shut") in says
        assert ("Lining, round fold A: 33 of its 34 holes meet when shut, 34 "
                "lying flat.") in says
        cross, = [n for n in dlg.layout_.notes if n.kind == "cross"]
        assert cross.a == _near(over)
    finally:
        dlg.close()
    dlg = _dialog(_lined(0.5 * over))
    try:
        assert dlg.view.check.readings == {1: (0, 34, True)}
        says = _lines(dlg)
        assert READ_SHUT.format("all 68 do, against none lying flat") \
            in says
        assert ("Lining, round fold A: 34 of its 34 holes meet when shut, "
                "none lying flat.") in says
    finally:
        dlg.close()


# -- writing and building -----------------------------------------------------

def test_opening_writes_nothing_and_a_typed_thickness_rebuilds_once(
        qapp, tmp_path, monkeypatch, mm):
    doc, by = _generated()
    was = doc.to_dict()
    win, dlg = _window(qapp, doc, tmp_path, monkeypatch)
    try:
        assert win.doc.to_dict() == was and _steps(win) == 0
        built = []
        real = dlg.rebuild

        def counted(*a, **k):
            built.append(1)
            return real(*a, **k)
        monkeypatch.setattr(dlg, "rebuild", counted)
        dlg.pieces.choose([by["Outer shell"].shape_id])
        dlg.spin_t.setValue(2.0)
        assert len(built) == 1
        assert by["Outer shell"].thickness == 2.0
        lay = dlg.layout_
        # 2 mm of shell: the spine takes pi times (4 + 1) on the same
        # radius, the pockets meeting as before
        assert lay.radius == _near([4.0]) and lay.used == _near([5.0 *
                                                                 math.pi])
        assert dlg.table.item(0, 5).text() == "15.71 mm"
        # reading the drawing again with nothing changed builds nothing
        dlg._refresh.stop()
        dlg.reload()
        assert len(built) == 1
    finally:
        _shut(win)


# -- a fold that has moved, and what is drawn through what --------------------

def _strip(*pockets, width=80.0, thick=0.8):
    """A 270 x 90 strip of 1.4 mm leather folded in three to the front
    along lines at x = -45 and +45, the right one first, and 50 mm deep
    pockets of ``thick`` mm laid on its panels: "L" with its inner edge
    5 mm short of the left fold line, "M" in the middle, "R" on the
    right."""
    shell = _piece("Shell", 270, 90)
    left = _line((-45, -45), (-45, 45), "Left fold")
    right = _line((45, -45), (45, 45), "Right fold")
    left.fold_order, right.fold_order = 2, 1
    at = {"L": -130 + 0.5 * width, "M": 0.0, "R": 90.0}
    laid = [_piece(f"Pocket {k}", width, 50, at=(at[k], -15))
            for k in pockets]
    doc = _drawing(shell, left, right, *laid)
    key = asm.put_on(doc, shell, thickness=1.4)
    for p in laid:
        asm.put_on(doc, p, key, thickness=thick)
    return doc


def test_a_piece_in_a_moved_fold_is_told_to_stop_where_the_dashed_line_is(
        qapp, mm):
    dlg = _dialog(_strip("L"))
    try:
        lay = dlg.layout_
        # fold A forms 2.83 mm off its line, towards the pocket's panel:
        # its bend, 5.65 mm of leather, runs from 5.65 mm left of the line
        # as drawn to the line itself
        assert lay.shift[0] == _near(0.9 * math.pi, abs=1e-3)
        assert lay.used[0] == _near(1.8 * math.pi, abs=1e-3)
        assert ("⚠ Pocket L reaches 0.65 mm into the bend of fold A, where "
                "leather cannot lie flat. It is shown bent with it. Stop it "
                "5.65 mm from the fold line: make it 0.65 mm narrower.") \
            in _lines(dlg)
        # the dashed line where the bend begins is there too: 5.65 mm left
        # of the fold line as it is drawn, and the pocket's edge (at -50)
        # stopped there lies flat
        xs = sorted({round(q[0], 3) for q, r, _n in dlg.view.scene.clear})
        assert xs[:2] == _near([-50.655, -45.0], abs=1e-3)
    finally:
        dlg.close()
    # made 0.65 mm narrower, as it says, it is clear of the bend
    dlg = _dialog(_strip("L", width=80.0 - 0.66))
    try:
        assert not [n for n in dlg.layout_.notes if n.kind == "reach"]
        assert "⚠" not in _words(dlg)
    finally:
        dlg.close()


def _noted(monkeypatch, *notes):
    """Every model the window makes is given these notes as well, as the
    fold engine would give them."""
    from leathercad_app import stackmodel
    real = stackmodel.build

    def build(*a, **k):
        lay = real(*a, **k)
        lay.notes = list(lay.notes) + list(notes)
        return lay
    monkeypatch.setattr(stackmodel, "build", build)


def test_leather_drawn_under_0_05_mm_through_itself_is_not_said_in_red(
        qapp, mm, monkeypatch):
    from leathercad.sheets import Note
    # the strip with 0.2 mm pockets: the fold engine itself now says
    # nothing of the 0.004 mm its moved fold keeps
    dlg = _dialog(_strip("L", "M", "R", thick=0.2))
    try:
        assert not [n for n in dlg.layout_.notes
                    if n.kind in ("clash", "limit", "landing")]
        assert "#c0392b" not in dlg.readout.text()
        assert dlg.view.alarm == "" and dlg.view.wash == {}
    finally:
        dlg.close()
    # ... and the window says nothing of a finding under 0.05 mm, of any
    # kind, where the engine gives one
    _noted(monkeypatch, Note("limit", 0, -1, 0.04, 2.0, (0,), rest=0.04),
           Note("clash", 1, -1, 0.049, 1.0, (2,), rest=0.049),
           Note("landing", 0, 0, 0.03, 2.0, (("panel", 0), ("bend", 0)),
                rest=0.03),
           Note("slit", 1, 0, 30.0, 0.04, (0,), at=(0.0, 0.0), rest=0.04))
    dlg = _dialog(_strip("L", "M", "R", thick=0.2))
    try:
        assert "#c0392b" not in dlg.readout.text()
        assert "into itself" not in _words(dlg)
        assert "into each other" not in _words(dlg)
        assert dlg.view.alarm == ""
        assert dlg.view.wash == {}
        dlg._go(2.0)
        assert dlg.view.alarm == ""
    finally:
        dlg.close()


def test_the_main_piece_drawn_through_itself_is_said_with_no_cause_given(
        qapp, mm):
    # the strip that cannot close: its folds do not cross
    dlg = _dialog(_no_room())
    try:
        dlg.cmb_base.setCurrentIndex(1)
        limit, = [n for n in dlg.layout_.notes if n.kind == "limit"]
        assert (round(limit.a, 2), limit.b, round(limit.rest, 2)) == (
            1.8, _near(1.0889, 1e-3), 1.21)
        says = _lines(dlg)
        assert ("⚠ Strip is drawn 1.21 mm into itself at rest, and up to "
                "1.8 mm on the way, from about a sixteenth of the way "
                "through step 2.") in says
        assert not any("cause" in line or "folds that cross" in line
                       for line in says)
        assert _rgb(dlg.view.wash[(0, None)]) == _rgb(dlg.view.look.bad)
    finally:
        dlg.close()


# -- a lining round a fold, inside and outside --------------------------------

def _lining_on(width, way, shell_w=200.0):
    """A ``shell_w`` x 90 shell of 1.6 mm folded in half along its middle
    (to the back: a piece laid on it goes round the outside of the fold;
    to the front: round the inside), a 0.8 mm lining ``width`` long
    across the fold laid on it, centred."""
    shell = _piece("Shell", shell_w, 90)
    lining = _piece("Lining", width, 86, at=(0, 300))
    doc = _drawing(shell, lining, _line((0, -45), (0, 45), "Spine", way))
    key = asm.put_on(doc, shell, thickness=1.6)
    asm.put_on(doc, lining, key, thickness=0.8)
    asm.line_up(doc, lining, "centre")
    asm.line_up(doc, lining, "middle")
    return doc


# round the bend a lining 1.2 mm from the shell's middle has pi x 1.2 mm
# more room outside and less inside
NEED = 1.2 * math.pi

LININGS = [
    # outside: too short, level within 0.25 mm either way, too long
    (200.0, "back",
     "⚠ Lining goes round the outside of fold A and has 3.77 mm too little "
     "leather there. Cut it 3.77 mm longer across the fold and it lies "
     "smooth and comes level when shut; sewn on flat at this length it "
     "pulls tight round the fold. The picture stretches it to fit (marked "
     "amber)."),
    (200.0 + NEED - 0.3, "back",
     "⚠ Lining goes round the outside of fold A and has 0.3 mm too little "
     "leather there. Cut it 0.3 mm longer across the fold and it lies "
     "smooth and comes level when shut; sewn on flat at this length it "
     "pulls tight round the fold. The picture stretches it to fit (marked "
     "amber)."),
    (200.0 + NEED - 0.2, "back",
     "Lining goes round the outside of fold A and is already 3.57 mm longer "
     "than Shell across it: shut, it comes level (the picture does not "
     "slide it)."),
    (200.0 + NEED, "back",
     "Lining goes round the outside of fold A and is already 3.77 mm longer "
     "than Shell across it: shut, it comes level (the picture does not "
     "slide it)."),
    (200.0 + NEED + 0.2, "back",
     "Lining goes round the outside of fold A and is already 3.97 mm longer "
     "than Shell across it: shut, it comes level (the picture does not "
     "slide it)."),
    (200.0 + NEED + 3.0, "back",
     "⚠ Lining goes round the outside of fold A and is 6.77 mm longer than "
     "Shell across it, 3 mm more than the bend takes: shut, it stands 3 mm "
     "past the ends of Shell. Cut it 3 mm shorter across the fold and it "
     "comes level when shut. The picture stretches it round the fold and "
     "does not slide it (marked amber)."),
    # inside: too long, level within 0.25 mm either way, too short
    (200.0, "front",
     "⚠ Lining goes round the inside of fold A and has 3.77 mm too much "
     "leather there. Cut it 3.77 mm shorter across the fold and it lies "
     "smooth and comes level when shut; sewn in flat at this length it "
     "wrinkles inside the fold. The picture squeezes it to fit (marked "
     "amber)."),
    (200.0 - NEED + 0.2, "front",
     "Lining goes round the inside of fold A and is already 3.57 mm shorter "
     "than Shell across it: shut, it comes level (the picture does not "
     "slide it)."),
    (200.0 - NEED, "front",
     "Lining goes round the inside of fold A and is already 3.77 mm shorter "
     "than Shell across it: shut, it comes level (the picture does not "
     "slide it)."),
    (200.0 - NEED - 0.2, "front",
     "Lining goes round the inside of fold A and is already 3.97 mm shorter "
     "than Shell across it: shut, it comes level (the picture does not "
     "slide it)."),
    (200.0 - NEED - 3.0, "front",
     "⚠ Lining goes round the inside of fold A and is 6.77 mm shorter than "
     "Shell across it, 3 mm more than the bend takes: shut, it stops short "
     "of the ends of Shell. Made 3 mm longer across the fold it comes level. "
     "The picture squeezes it to fit (marked amber)."),
]


@pytest.mark.parametrize("width, way, said", LININGS, ids=[
    "outside-as-long", "outside-0.3-short", "outside-0.2-short",
    "outside-level", "outside-0.2-long", "outside-3-long", "inside-as-long",
    "inside-0.2-long", "inside-level", "inside-0.2-short",
    "inside-3-short"])
def test_a_lining_round_a_fold_is_too_short_level_or_too_long_within_0_25(
        qapp, mm, width, way, said):
    dlg = _dialog(_lining_on(width, way))
    try:
        says = [line for line in _lines(dlg) if "Lining goes" in line]
        assert says == [said]
        # amber on its strip in the bend just where the sentence warns,
        # and the sentence is level just where the fold engine says so
        warned = said.startswith("⚠")
        assert [n.kind for n in dlg.layout_.notes
                if n.kind in ("cross", "level")] == \
            ["cross" if warned else "level"]
        assert set(dlg.view.wash) == ({(1, 0)} if warned else set())
        assert ("<span style='color:#b35c00'>⚠ Lining" in
                dlg.readout.text()) == warned
    finally:
        dlg.close()


def test_a_lining_cut_3_mm_too_long_stands_3_mm_past_the_ends_shut(
        qapp, mm):
    sizes = []
    for width in (200.0 + NEED, 200.0 + NEED + 3.0):
        dlg = _dialog(_lining_on(width, "back"))
        try:
            dlg._go(1.0)
            lo, hi = dlg.view.scene.bounds()
            sizes.append(hi[0] - lo[0])
        finally:
            dlg.close()
    # held at its middle, 1.5 mm at each end
    assert sizes[1] - sizes[0] == _near(1.5, abs=0.01)


def test_a_wrap_10_mm_longer_than_its_panel_round_the_outside_is_too_long(
        qapp, mm):
    panel = _piece("Panel", 100, 60)
    wrap = _piece("Wrap", 110, 60)
    doc = _drawing(panel, wrap, _line((0, -30), (0, 30), "Middle", "back"))
    key = asm.put_on(doc, panel, thickness=2.0)
    asm.put_on(doc, wrap, key, thickness=1.0)
    dlg = _dialog(doc)
    try:
        cross, = [n for n in dlg.layout_.notes if n.kind == "cross"]
        assert (cross.a, cross.b) == _near((-1.5 * math.pi, -10.0))
        assert ("⚠ Wrap goes round the outside of fold A and is 10 mm longer "
                "than Panel across it, 5.29 mm more than the bend takes: "
                "shut, it stands 5.29 mm past the ends of Panel. Cut it "
                "5.29 mm shorter across the fold and it comes level when "
                "shut. The picture stretches it round the fold and does not "
                "slide it (marked amber).") in _lines(dlg)
        assert set(dlg.view.wash) == {(1, 0)}
    finally:
        dlg.close()


# -- Esc while a number is half typed -----------------------------------------

def _in_front(qapp, doc, tmp_path, monkeypatch):
    """The main window shown, and the preview opened from it in front."""
    from leathercad_app.mainwindow import MainWindow
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(MainWindow, "_settings",
                        lambda self: QSettings(ini,
                                               QSettings.Format.IniFormat))
    win = MainWindow(doc)
    win.show()
    win.canvas.rebuild()
    win._assembly_preview()
    dlg = win._assembly_dlg
    dlg.activateWindow()
    QApplication.processEvents()
    return win, dlg

BOXES = {
    "angle": lambda dlg: dlg.table.cellWidget(0, 3),
    "radius": lambda dlg: dlg.table.cellWidget(0, 4),
    "bend radius": lambda dlg: dlg.spin_r,
    "thickness": lambda dlg: dlg.spin_t,
    "across": lambda dlg: dlg.pieces.spin_x,
    "up": lambda dlg: dlg.pieces.spin_y,
    "turned": lambda dlg: dlg.pieces.spin_turn,
}


@pytest.mark.parametrize("which", list(BOXES))
def test_esc_lets_go_of_a_number_half_typed_for_a_stack(
        qapp, tmp_path, monkeypatch, mm, which):
    from PySide6.QtTest import QTest
    doc, by = _generated()
    was = doc.to_dict()
    win, dlg = _in_front(qapp, doc, tmp_path, monkeypatch)
    try:
        if which in ("across", "up", "turned"):
            dlg.tabs.setCurrentWidget(dlg.pieces)
            dlg.pieces.choose([by["Left pocket 3"].shape_id])
        radius = dlg.layout_.radius[0]
        box = BOXES[which](dlg)
        assert box.isEnabled()
        box.setFocus()
        box.lineEdit().selectAll()
        QTest.keyClicks(box.lineEdit(), "9")
        QApplication.processEvents()
        QTest.keyClick(box.lineEdit(), Qt.Key_Escape)
        QApplication.processEvents()
        assert win.doc.to_dict() == was
        assert _steps(win) == 0 and not win.history.can_undo()
        assert next(s for s in win.doc.shapes
                    if s.name == "Spine fold").fold_angle == 180.0
        assert radius == _near(4.0)
        # nor is it kept for next time
        kept = QSettings(str(tmp_path / "settings.ini"),
                         QSettings.Format.IniFormat)
        assert float(kept.value("fold3dRadius", 0.0)) != 9.0
        assert float(kept.value("fold3dThickness", 2.0)) != 9.0
    finally:
        win._unsaved_changes = False
        win.hide()


def test_esc_keeps_a_stack_s_finished_change_as_one_step_and_no_more(
        qapp, tmp_path, monkeypatch, mm):
    from PySide6.QtTest import QTest
    doc, by = _generated()
    win, dlg = _in_front(qapp, doc, tmp_path, monkeypatch)
    try:
        dlg.table.cellWidget(0, 3).setValue(120.0)       # finished
        box = dlg.table.cellWidget(0, 4)
        box.setFocus()
        box.lineEdit().selectAll()
        QTest.keyClicks(box.lineEdit(), "6")
        QTest.keyClick(box.lineEdit(), Qt.Key_Escape)
        QApplication.processEvents()
        line = next(s for s in win.doc.shapes if s.name == "Spine fold")
        assert (line.fold_angle, line.fold_radius) == (120.0, -1.0)
        assert _steps(win) == 1
        win.undo()
        line = next(s for s in win.doc.shapes if s.name == "Spine fold")
        assert (line.fold_angle, line.fold_radius) == (180.0, -1.0)
        assert not win.history.can_undo()
    finally:
        win._unsaved_changes = False
        win.hide()


def test_a_stack_whose_fold_stands_open_is_given_its_size_and_no_more(
        qapp, mm):
    doc, by = _generated()
    by["Spine fold"].fold_angle = 120.0
    dlg = _dialog(doc)
    try:
        folded = [line for line in _lines(dlg) if line.startswith("Folded")]
        assert folded == ["Folded it measures 100.14 × 90 × 90.3 mm."]
        dlg._go(1.0)
        assert dlg.view.caption == "Folded  ·  100.14 × 90 × 90.3 mm"
    finally:
        dlg.close()


# -- names as they are written ------------------------------------------------

def _as_shown(text: str) -> str:
    """A text as a label or a tooltip shows it: rich text read, plain
    text as it is."""
    from PySide6.QtGui import Qt as Gui, QTextDocument
    if not Gui.mightBeRichText(text):
        return text
    doc = QTextDocument()
    doc.setHtml(text)
    return doc.toPlainText().replace(" ", "\n")


def test_names_with_brackets_and_ampersands_are_read_as_they_are_written(
        qapp, mm):
    panel = _piece("a & b", 100, 60)
    wrap = _piece("<b>", 90, 60, at=(0, 200))
    tag = _piece("", 30, 20, at=(200, 200))
    doc = _drawing(panel, wrap, tag,
                   _line((0, -30), (0, 30), "<i>Middle</i>", "back"))
    key = asm.put_on(doc, panel, thickness=2.0)
    for p in (wrap, tag):
        asm.put_on(doc, p, key, thickness=1.0)
        asm.line_up(doc, p, "centre")
        asm.line_up(doc, p, "middle")
    dlg = _dialog(doc)
    try:
        says = _as_shown(dlg.readout.text())
        assert ("Put together: 3 pieces, 4 mm at the thickest (a & b, <b>, "
                "Piece 30 × 20 mm).") in says
        assert ("Fold A (<i>Middle</i>) takes 3.14 mm of a & b, 1.57 mm "
                "each side of the fold line.") in says
        assert ("Fold A closes away from the 2 pieces laid on a & b: they "
                "end up on the outside. If they belong inside, press Flip."
                ) in says
        assert ("⚠ <b> goes round the outside of fold A and has 14.71 mm "
                "too little leather there.") in says
        assert ("⚠ Piece 30 × 20 mm goes round the outside of fold A and "
                "needs 7.85 mm more leather there than it has") in says
        # an empty name reads as the list of pieces shows it
        names = [dlg.pieces.pieces.item(r, 0).text()
                 for r in range(dlg.pieces.pieces.rowCount())]
        assert "Piece 30 × 20 mm" in names and "<b>" in names
        # the tooltips show the names as they are
        assert _as_shown(dlg.table.cellWidget(0, 2).toolTip()) == (
            "Back: the flesh sides come together. Closes away from the 2 "
            "pieces laid on a & b.")
        assert _as_shown(dlg.table.item(0, 0).toolTip()) == (
            "Fold A, <i>Middle</i>. Untick to leave it flat")
        row = names.index("<b>")
        assert _as_shown(dlg.pieces.pieces.item(row, 0).toolTip()) == \
            "<b>: untick to take it off the stack"
        assert _as_shown(dlg.pieces.pieces.item(row, 2).toolTip()) \
            .startswith("Lies on a & b, grain up.")
    finally:
        dlg.close()


def test_the_keep_clear_lines_in_sight_are_found_once_for_a_camera(
        qapp, mm, monkeypatch):
    from leathercad_app import foldview
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        view = dlg.view
        view.repaint()
        tried = []
        real = foldview._in_sight

        def counted(*a, **k):
            tried.append(1)
            return real(*a, **k)
        monkeypatch.setattr(foldview, "_in_sight", counted)
        view.cam.yaw += 0.05                      # a camera not seen yet
        view.repaint()
        first = view.grab().toImage()
        once = len(tried)
        del tried[:]
        view.repaint()
        again = view.grab().toImage()
        # the same picture, without looking for the dashed lines again:
        # only the fold line's stretches in sight are looked for
        assert again == first
        assert 0 < len(tried) < once
        fold_lines = len(tried)
        # turned, they are looked for afresh
        del tried[:]
        view.cam.yaw += 0.1
        view.repaint()
        assert len(tried) > fold_lines
    finally:
        dlg.close()


def test_the_backings_standing_clear_of_a_shortened_liner_are_said_plainly(
        qapp, mm):
    doc, _by = _generated(fit=True)
    dlg = _dialog(doc)
    try:
        said = [part for part in dlg.readout.text().split("<br><br>")
                if "clear of" in part]
        assert said == [
            f"{side} pocket backing rests on Bill pocket liner; beyond it, "
            "it stands 0.8 mm clear of Outer shell. On the bench it beds "
            "down over that step: the picture keeps it flat."
            for side in ("Left", "Right")]
        assert dlg.view.wash == {}
    finally:
        dlg.close()


# -- the fold engine's own cases, drawn ---------------------------------------

def _outline(name, ring):
    return PathShape(points=[Vec2(*p) for p in ring], close_path=True,
                     name=name, layer="Cut")


def _box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _drawn(ring, folds, pieces, t=2.0, name="Body"):
    """A drawing of one of ``tests/test_stackfold.py``'s stacks: the main
    piece ``ring`` ``t`` mm thick, its fold lines ``(a, b, angle, way,
    order, radius)`` (way 1 to the front), and the pieces ``(name, ring,
    thickness, side)`` laid on it in that order where ``ring`` says
    (side -1: under it). The pieces are drawn apart from the main piece,
    as the pieces of a pattern are, and placed with Across and Up."""
    def box(r):
        return (min(p[0] for p in r), min(p[1] for p in r))

    main = _outline(name, ring)
    lines = []
    for a, b, angle, way, order, radius in folds:
        ln = _line(a, b, way="front" if way > 0 else "back", angle=angle)
        ln.fold_order, ln.fold_radius = order, radius
        lines.append(ln)
    laid = [_outline(n, [(x + 1000.0 + 300.0 * k, y) for x, y in r])
            for k, (n, r, _t, _s) in enumerate(pieces)]
    doc = _drawing(main, *laid, *lines)
    key = asm.put_on(doc, main, thickness=t)
    mx, my = box(ring)
    for shape, (_n, r, thick, _s) in zip(laid, pieces):
        assert asm.put_on(doc, shape, key, thickness=thick) == key
        asm.line_up(doc, shape, "left")
        asm.line_up(doc, shape, "bottom")
        px, py = box(r)
        asm.set_offset(doc, shape, px - mx, py - my)
    under = [shape for shape, p in zip(laid, pieces) if p[3] < 0]
    if under:
        asm.put_under(doc, under)
    return doc


def _wings(way):
    """The T of ``test_stackfold``: a 100 x 60 body with a 40 x 60 flap,
    2 mm, folded flat 3 mm up the flap, and a 1 mm liner wider than the
    flap (its wings beside the flap, over nothing of the body) with a
    pocket on its left wing."""
    from test_stackfold import TEE
    return _drawn(TEE, [((0, 63), (100, 63), 180.0, way, 0, -1.0)],
                  [("Liner", _box(10, 50, 90, 110), 1.0, 1),
                   ("Pocket", _box(12, 80, 28, 100), 1.0, 1)], name="Tee")


def _ell():
    """An L-shaped piece folded across its upright arm and then across its
    foot: the two lines cross past its inside corner, under a liner that
    stands out over the corner."""
    from test_stackfold import L_SHAPE
    return _drawn(L_SHAPE, [((0, 75), (100, 75), 180.0, 1, 1, -1.0),
                            ((70, 0), (70, 60), 135.0, -1, 2, -1.0)],
                  [("Liner", _box(20, 50, 100, 100), 1.0, 1)], name="Ell")


def _tray():
    """A tray 160 mm square, 2 mm, its four walls stood up one after the
    other, a 1 mm liner along its left wall and one along its right."""
    return _drawn(_box(0, 0, 160, 160),
                  [((30, 0), (30, 160), 90.0, 1, 1, -1.0),
                   ((130, 0), (130, 160), 90.0, 1, 2, -1.0),
                   ((0, 30), (160, 30), 90.0, 1, 3, -1.0),
                   ((0, 130), (160, 130), 90.0, 1, 4, -1.0)],
                  [("Left liner", _box(2, 2, 28, 158), 1.0, 1),
                   ("Right liner", _box(132, 2, 158, 158), 1.0, 1)],
                  name="Tray")


def _landing():
    """``test_stackfold``'s piece whose fold forms off its line and
    brings a panel down in a wall, with a small patch on its back."""
    from test_stackfold import _limited
    outline, folds = _limited()
    return _drawn(outline, [(f.a, f.b, f.angle, f.direction, f.order, -1.0)
                            for f in folds],
                  [("Patch", _box(40, 5, 55, 20), 0.4, -1)], name="Piece")


def _strip_77(trial):
    """A random strip in three of ``test_stackfold`` (seed 77), drawn."""
    import random
    from test_stackfold import random_stack
    rng = random.Random(77)
    for _k in range(trial + 1):
        out, folds, sheets, t, _base = random_stack(rng, "three")
    return _drawn(out, [(f.a, f.b, f.angle, f.direction, f.order, f.radius)
                        for f in folds],
                  [(s.name, s.ring, s.thickness, s.side) for s in sheets], t)


def _two_linings():
    """A 200 x 100 piece of 2 mm folded flat in the middle, with two 1 mm
    linings round the inside, one on the other: each is punched half
    what it has too much round the bend (pi x 1.5 and pi x 2.5 mm) nearer
    the fold each side, to meet the piece's holes when shut."""
    shell = _piece("Shell", 200, 100)
    pieces = [shell]
    seams = [_seam((-90, 45), (-10, 45)), _seam((10, 45), (90, 45))]
    for k, (name, over) in enumerate((("One", 1.5 * math.pi),
                                      ("Two", 2.5 * math.pi))):
        x = 300.0 * (k + 1)
        pieces.append(_piece(name, 200, 98, at=(x, 300)))
        pull = 0.5 * over
        seams += [_seam((x - 90 + pull, 346), (x - 10 + pull, 346)),
                  _seam((x + 10 - pull, 346), (x + 90 - pull, 346))]
    doc = _drawing(*pieces, _line((0, -50), (0, 50), "Fold"), *seams)
    key = asm.put_on(doc, shell, thickness=2.0)
    for lining in pieces[1:]:
        asm.put_on(doc, lining, key, thickness=1.0)
        asm.line_up(doc, lining, "centre")
        asm.line_up(doc, lining, "bottom")
    return doc


PARTED = ("the fold bends it on one side and leaves it flat on the other. "
          "In leather it would have to be slit there, or stop short of the "
          "fold.")


def test_a_liner_wider_than_its_flap_is_parted_where_it_would_crimp(
        qapp, mm):
    dlg = _dialog(_wings(1))
    try:
        slits = [(n.sheet, n.fold, round(n.a, 4), round(n.b, 4), n.other)
                 for n in dlg.layout_.notes if n.kind == "slit"]
        assert slits == [(1, 0, 20.1416, 0.1412, (0,))] * 2
        # to the front: parted twice along the body's edge, 0.14 mm into
        # leather there -- a crimp, said plainly, not an alarm
        assert ("Liner is parted along 20.14 mm in 2 places at fold A, with "
                "Tee across it: " + PARTED + " The picture shows it 0.14 mm "
                "into leather there, where real leather would crimp.") \
            in _lines(dlg)
        assert "#c0392b" not in dlg.readout.text()
        assert dlg.view.alarm == ""
        assert set(dlg.view.wash) == {(1, 0)}       # (amber: it goes round)
    finally:
        dlg.close()
    dlg = _dialog(_wings(-1))
    try:
        # to the back the bend misses the body: nothing is parted
        assert not [n for n in dlg.layout_.notes if n.kind == "slit"]
        assert "parted" not in _words(dlg)
    finally:
        dlg.close()
    mm.set_current("in")
    dlg = _dialog(_wings(1))
    try:
        assert ("Liner is parted along 0.793 in in 2 places at fold A, with "
                "Tee across it: " + PARTED + " The picture shows it 0.006 in "
                "into leather there, where real leather would crimp.") \
            in _lines(dlg)
        assert "mm" not in _words(dlg)
    finally:
        dlg.close()


def test_a_liner_over_where_two_folds_cross_past_the_edge_is_torn_and_cut(
        qapp, mm):
    dlg = _dialog(_ell())
    try:
        lay = dlg.layout_
        crossing, = [n for n in lay.notes if n.kind == "crossing"]
        # (70, 75) on the drawing, in the frame of the model
        assert (crossing.sheet, crossing.other, crossing.at) == (
            1, (1,), _near((20.0, 5.0)))
        assert (crossing.b, crossing.rest) == (0.0, 0.0)
        slit, = [n for n in lay.notes if n.kind == "slit"]
        assert (round(slit.a, 4), round(slit.b, 4), slit.rest) == (
            34.3197, 0.5369, 0.0)
        says = _lines(dlg)
        assert ("Liner is parted along 34.32 mm at fold A: " + PARTED
                + " On the way the picture draws it up to 0.54 mm into "
                "leather there; it turns each panel as one stiff piece, "
                "where leather gives.") in says
        assert ("Liner lies over the place where folds A and B cross, past "
                "the edge of Ell: folded along one and then the other, it is "
                "torn there, and the preview cannot show how it would lie."
                ) in says
        assert (1, None) not in dlg.view.wash and dlg.view.alarm == ""
    finally:
        dlg.close()


def test_a_tray_s_wall_liner_is_parted_where_its_corners_part(qapp, mm):
    dlg = _dialog(_tray())
    try:
        lay = dlg.layout_
        slits = [(n.sheet, round(n.a, 4), n.at, n.other, round(n.b, 4),
                  round(n.rest, 4))
                 for n in lay.notes if n.kind == "slit"]
        # 26 mm at (145, 30) and (145, 130) on the drawing
        assert sorted(slits, key=lambda x: x[2][1]) == [
            (2, 26.0, _near((65.0, -50.0)), (0,), 4.8657, 0.5708),
            (2, 26.0, _near((65.0, 50.0)), (0,), 6.9317, 0.5708)]
        says = _lines(dlg)
        for fold, deep in (("B", "6.93"), ("C", "4.87")):
            assert (f"⚠ Right liner is parted along 26 mm at fold {fold}, "
                    "with Tray across it, and is drawn 0.57 mm into leather "
                    f"there, and up to {deep} mm on the way: " + PARTED) \
                in says
        limit, = [n for n in lay.notes if n.kind == "limit"]
        assert (round(limit.a, 2), limit.b, round(limit.rest, 4)) == (
            6.34, _near(0.0647, 1e-3), 2.0)
        assert ("⚠ Tray is drawn 2 mm into itself at rest, and up to 6.34 mm "
                "on the way, from about a sixteenth of the way through step "
                "1: "
                "folds that cross are folded one after the other, and the "
                "preview cannot open the leather for the second.") in says
        bad = _rgb(dlg.view.look.bad)
        assert {k for k, c in dlg.view.wash.items() if _rgb(c) == bad} == {
            (0, None), (1, None), (2, None)}
        assert dlg.view.alarm == ("3 pieces are drawn into leather: see the "
                                  "report")
    finally:
        dlg.close()


def test_a_fold_that_moves_says_once_what_it_brings_down_and_how_to_mend_it(
        qapp, mm):
    dlg = _dialog(_landing())
    try:
        lay = dlg.layout_
        landing, = [n for n in lay.notes if n.kind == "landing"]
        assert (landing.fold, landing.other, round(landing.a, 4),
                landing.b) == (0, (("panel", 4), ("bend", 0)), 1.7854,
                               _near(3.6821, 1e-3))
        says = _lines(dlg)
        # the lone piece's advice and warning, said once
        assert ("Fold A folds 3.14 mm off its line, towards panel 1, to go "
                "round what is folded inside it. To have it fold on its "
                "line, shorten panel 2 by 3.14 mm where it reaches into the "
                "fold.") in says
        assert [line for line in says if "1.79" in line] == [
            "⚠ With fold A off its line, panel 5 and fold A are drawn "
            "1.79 mm into each other."]
        # red on panel 5 and on fold A's bend, and in the caption
        bad = _rgb(dlg.view.look.bad)
        assert {k: _rgb(c) for k, c in dlg.view.wash.items()} == {
            (0, ("panel", 4)): bad, (0, 0): bad}
        assert dlg.view.alarm == ("Piece is drawn through itself: see the "
                                  "report")
        # ... in the picture: panel 5 is red, panel 3 is not
        dlg.chk_labels.setChecked(False)
        dlg._look("front")
        lo, hi = dlg.view.scene.bounds()
        five, three = (56.0, -10.0, hi[2]), (-20.0, -10.0, hi[2])
        marked = _seen(dlg, five), _seen(dlg, three)
        dlg.view.set_wash({})
        plain = _seen(dlg, five), _seen(dlg, three)
        assert math.dist(marked[0], bad) < 60
        assert math.dist(marked[0], plain[0]) > 40
        assert math.dist(marked[1], plain[1]) < 3
    finally:
        dlg.close()


def test_a_fold_that_cannot_close_names_the_piece_in_its_way(qapp, mm):
    dlg = _dialog(_no_room())
    try:
        dlg.cmb_base.setCurrentIndex(1)
        lay = dlg.layout_
        assert lay.stuck == [False, True]
        stuck, = [n for n in lay.notes if n.kind == "stuck"]
        names = dlg.parts.names
        assert (stuck.fold, round(stuck.a, 4), round(stuck.b, 4),
                [names[p] for p in stuck.other]) == (
            1, 7.854, 13.927, ["Strip", "Patch"])
        says = _lines(dlg)
        # the fold engine's sentence, and the piece in the way, by name
        assert ("⚠ What is folded inside fold B reaches 13.93 mm into its "
                "bend, and the fold cannot move further out of its way: its "
                "bend would not fit before the edge of panel 3. Patch is in "
                "its way.") in says
        assert ("⚠ Strip and Patch are drawn 0.93 mm into each other at "
                "rest, from the very start of step 2.") in says
        bad = _rgb(dlg.view.look.bad)
        # the patch whole, and the strip in fold B's bend
        assert _rgb(dlg.view.wash[(1, None)]) == bad
        assert _rgb(dlg.view.wash[(0, 1)]) == bad
    finally:
        dlg.close()


def test_two_linings_punched_for_shut_are_read_shut_as_the_marks_show(
        qapp, mm):
    dlg = _dialog(_two_linings())
    try:
        check = dlg.view.check
        assert check.readings == {1: (0, 34, True), 2: (0, 34, True)}
        assert len(check.holes(fs.MEETS)) == 102
        says = _lines(dlg)
        # one reading for the whole stack, said once
        assert says.count(READ_SHUT.format(
            "all 102 do, against none lying flat")) == 1
        for name in ("One", "Two"):
            assert (f"{name}, round fold A: 34 of its 34 holes meet when "
                    "shut, none lying flat.") in says
        assert "Stitch holes through the layers: ● all 102 line up." in says
    finally:
        dlg.close()


def test_a_lining_half_way_between_its_readings_is_told_as_it_is_marked(
        qapp, mm):
    dlg = _dialog(_lined(0.25 * 1.5 * math.pi))
    try:
        check = dlg.view.check
        assert check.readings == {1: (0, 0, False)}
        # every hole is marked out of line, and nothing says they line up
        assert len(check.off) == 68
        says = _lines(dlg)
        assert ("Stitch holes through the layers: Shell and Lining: ● 68 are "
                "out of line, by up to 1.18 mm.") in says
        assert READ_FLAT.format("none of its 68 do, against none when "
                                "shut") in says
        # (its halves, facing, line up with each other, but are marked
        # out of line with the Shell's: so said, where once nothing was)
        assert ("Lining, round fold A: none of its 34 holes meet when shut, "
                "none lying flat. Where the folding brings its panels 1 and "
                "2 face to face, ● 34 of their holes line up with each "
                "other, but are red all the same: their needle lines are out "
                "of line elsewhere.") in says
        assert _words(dlg).count("line up") == 1
    finally:
        dlg.close()


def _c_fold(*linings, pitch=11.0):
    """A 240 x 80 shell of 2 mm folded in a C: to the front at x = -45
    (fold A, step 1) and x = 45 (fold B, step 2), the middle panel
    still; with linings of its size, 1 mm, one on the other, each sewn
    along y = 30 on each panel as the shell is, at a pitch of 11 mm
    (or ``pitch``). A lining "flat" is punched to meet lying flat; one
    "shut" has the rows of its two end panels pi x (1 + what lies under
    it + 0.5) mm nearer the folds, to meet when shut."""
    spans = ((-115, -51), (-39, 39), (51, 115))
    shell = _piece("Shell", 240, 80)
    pieces, seams = [shell], []
    for a, b in spans:
        seams.append(_seam((a, 30), (b, 30), pitch))
    for k, how in enumerate(linings):
        x = 500.0 * (k + 1)
        name = "Liner" if len(linings) == 1 else f"Liner{k + 1}"
        pieces.append(_piece(name, 240, 80, at=(x, 0)))
        pull = math.pi * (1.0 + k + 0.5) if how == "shut" else 0.0
        for (a, b), d in zip(spans, (pull, 0.0, -pull)):
            seams.append(_seam((x + a + d, 30), (x + b + d, 30), pitch))
    folds = [_line((-45, -40), (-45, 40), "F1"), _line((45, -40), (45, 40),
                                                       "F2")]
    for order, fold in enumerate(folds, 1):
        fold.fold_order = order
    doc = _drawing(*pieces, *folds, *seams)
    key = asm.put_on(doc, shell, thickness=2.0)
    for lining in pieces[1:]:
        asm.put_on(doc, lining, key, thickness=1.0)
        asm.line_up(doc, lining, "centre")
        asm.line_up(doc, lining, "bottom")
    return doc


def _marked(dlg, state):
    """How many holes the model marks in a state, by the name of their
    piece and the number of the panel of the main piece they lie on
    (None in a bend)."""
    from leathercad import sheets as sh
    lay, check = dlg.layout_, dlg.view.check
    names = [lay.name] + [s.name for s in lay.sheets]
    out = {}
    for i, (x, y, _d) in enumerate(lay.holes):
        if check.state.get(i) != state:
            continue
        k = lay.hole_sheet[i]
        cell = sh._cell_at([c for c in lay.sheet_cells
                            if c.sheet == k and c.hinge is None], x, y)
        panel = lay.hole_panel[i] if k == 0 else \
            cell.panel if cell is not None else None
        at = (names[k], None if panel is None else lay.panels[panel].number)
        out[at] = out.get(at, 0) + 1
    return out


def _said_out_of_line(dlg):
    """Each count of stitch holes the report says are red -- out of line,
    or in line with each other and red all the same -- with the pieces and
    panels it says them of: ``[(count, {(piece, panel or None for all its
    panels)})]``, None for a count of the whole stack's."""
    def red(text):
        n = sum(int(got.group(1)) for got in re.finditer(
            r"(\d+) (?:of their holes )?(?:is|are) out of line", text))
        n += sum(int(got.group(1)) for got in re.finditer(
            r"(\d+) of them red all the same", text))
        return n + sum(int(got.group(1)) for got in re.finditer(
            r"(\d+) (?:of their holes )?lines? up with (?:each other|the "
            r"other's)(?:, to within [\d.]+ mm)?, but (?:is|are) red", text))

    said = []
    for line in _lines(dlg):
        line = line.replace("Stitch holes where the folding brings pieces "
                            "together: ", "").replace(
            "Stitch holes through the layers: ", "")
        own = re.match(r"(\w+), round .*? lying flat\.", line)
        if own:
            # (each two of its panels the folding brings face to face)
            for clause in line.split(" Where the folding brings its panels "
                                     )[1:]:
                p, q = re.match(r"(\d+) and (\d+)", clause).groups()
                said.append((red(clause), {(own.group(1), int(p)),
                                           (own.group(1), int(q))}))
            continue
        n = red(line)
        if not n:
            continue
        faced = re.match(r"(\w+) \(panel (\d+)\) and (\w+) \(panel "
                         r"(\d+)\):", line)
        own = re.match(r"(\w+), panels (\d+) and (\d+):", line)
        laid = re.match(r"(?:.*\. )?(\w+) and (\w+): ", line)
        if faced:
            a, p, b, q = faced.groups()
            said.append((n, {(a, int(p)), (b, int(q))}))
        elif own:
            a, p, q = own.groups()
            said.append((n, {(a, int(p)), (a, int(q))}))
        elif laid:
            said.append((n, {(laid.group(1), None), (laid.group(2), None)}))
        else:
            said.append((n, None))      # (of the holes of the whole stack)
    return said


@pytest.mark.parametrize("stack", ["C, one lining flat",
                                   "C, linings shut and flat",
                                   "C, linings flat and shut",
                                   "one fold", "one fold, pulled"])
def test_the_holes_said_to_meet_and_out_of_line_are_those_marked(
        qapp, mm, stack):
    doc = {"C, one lining flat": lambda: _c_fold("flat"),
           "C, linings shut and flat": lambda: _c_fold("shut", "flat"),
           "C, linings flat and shut": lambda: _c_fold("flat", "shut"),
           "one fold": _lined,
           "one fold, pulled": lambda: _lined(0.25 * 1.5 * math.pi)}[stack]()
    dlg = _dialog(doc)
    try:
        check, lay, says = dlg.view.check, dlg.layout_, _lines(dlg)
        words = _words(dlg)
        met, off = _marked(dlg, fs.MEETS), _marked(dlg, fs.OFF)
        shut = any(shown for _f, _s, shown in check.readings.values())
        # each piece read round its folds: what is said to meet in the
        # reading shown is what is marked meeting, hole by hole
        assert check.readings
        for k, s in enumerate(lay.sheets, 1):
            if k not in check.readings:
                continue
            line, = [x for x in says if x.startswith(f"{s.name}, round ")]
            when, flat = re.search(r"(\w+) of its \d+ holes meet when shut, "
                                   r"(\w+) lying flat", line).groups()
            said = int((when if shut else flat).replace("none", "0"))
            assert said == sum(n for (who, _p), n in met.items()
                               if who == s.name)
        assert "The marks on the model show that reading." in words
        assert "by up to 0 mm" not in words
        # every hole marked out of line is said so, once in the sentence
        # of each two pieces or panels it is counted by: as out of line
        # with the other, or as lining up with it and red all the same (a
        # hole on a needle line through the layers and across a fold is in
        # a sentence of each; once each was in one only); and a head says
        # no more than are marked at all
        said = _said_out_of_line(dlg)
        names = [lay.name] + [s.name for s in lay.sheets]
        heads = [n for n, at in said if at is None]
        said = {frozenset(at): n for n, at in said if at is not None}
        red = set()
        for f in check.findings:
            if f.laid:
                at = {(names[f.a], None), (names[f.b], None)}
            else:
                at = set(zip((names[f.a], names[f.b]), f.panels))
            n = sum(check.state.get(k) == fs.OFF for k in f.numbers)
            if frozenset(at) in said:
                assert said[frozenset(at)] == n
                red |= {k for k in f.numbers
                        if check.state.get(k) == fs.OFF}
        assert red == check.off
        assert all(0 < n <= len(check.off) for n in heads)
        for who, panel in off:
            assert any((who, panel) in at or (who, None) in at
                       for at in said)
    finally:
        dlg.close()


def test_a_c_fold_lining_punched_flat_is_told_as_the_model_marks_it(
        qapp, mm):
    dlg = _dialog(_c_fold("flat"))
    try:
        check = dlg.view.check
        # (the three panels' rows lie on the same needle lines shut: a
        # hole is marked out of line where any hole on its line misses,
        # the Shell's middle and right panels' too -- once 8 and 7 met)
        assert check.readings == {1: (4, 2, False)}
        assert _marked(dlg, fs.MEETS) == {
            ("Shell", 1): 1, ("Shell", 2): 2, ("Shell", 3): 1,
            ("Liner", 1): 1, ("Liner", 2): 2, ("Liner", 3): 1}
        assert _marked(dlg, fs.OFF) == {
            ("Shell", 1): 6, ("Shell", 2): 6, ("Shell", 3): 6,
            ("Liner", 1): 6, ("Liner", 2): 6, ("Liner", 3): 6}
        says = _lines(dlg)
        # (the Shell and the Liner line up with each other as they lie:
        # their line says so, and why 36 of those are red all the same;
        # the Shell's own panels, whose rows do not mirror about the two
        # folds, say what they do with each other, and so do the two next
        # to each other that miss, Shell 1 and Liner 3, Liner 1 and 2;
        # those further apart, whose misses those say, say nothing -- once
        # five more lines and two more clauses of the Liner's)
        assert says[-7] == (
            "Stitch holes through the layers: Shell and Liner: ● all 44 line "
            "up with each other, 36 of them red all the same: their needle "
            "lines are out of line elsewhere.")
        assert says[-6:-3] == [
            "Stitch holes where the folding brings pieces together: ● 24 are "
            "out of line, by up to 3.33 mm; ● 2 line up, to within 0.48 mm.",
            "Shell, panels 1 and 2: ● 10 are out of line, by up to 2.86 mm; "
            "● 4 line up with each other, to within 0.48 mm, 2 of them red "
            "all the same: their needle lines are out of line elsewhere.",
            "Shell, panels 1 and 3: ● 12 are out of line, by up to 3.33 mm."]
        assert says[-3] == "Shell (panel 1) and Liner (panel 3): ● 12 are " \
            "out of line, by up to 3.33 mm."
        assert says[-2:] == [
            "Stitch holes of the pieces that go round a fold: the stack is "
            "read as punched to meet lying flat, the reading in which at "
            "least as many of the whole stack's holes meet as the other: 8 "
            "of its 44 do, against 6 when shut. The marks on the model show "
            "that reading.",
            "Liner, round folds A and B: 2 of its 22 holes meet when shut, 4 "
            "lying flat. Where the folding brings its panels 1 and 2 face to "
            "face, ● 10 of their holes are out of line, by up to 2.86 mm; ● "
            "2 line up with each other, but are red all the same: their "
            "needle lines are out of line elsewhere."]
    finally:
        dlg.close()


def test_holes_put_out_of_line_by_the_folding_are_said_of_the_layers_too(
        qapp, mm):
    dlg = _dialog(_c_fold("flat", "flat"))
    try:
        check = dlg.view.check
        # every pair of layers lines up as it lies; but 54 holes are marked
        # out of line, on needle lines that what the folding brings onto
        # them misses: the layers' head counts them as marked, and each
        # pair of layers says it lines up, and how many of those are red
        # all the same (once none was said, 22 marked)
        assert [(f.off, f.met, f.red) for f in check.findings if f.laid] \
            == [(0, 44, 36)] * 3
        assert len(check.off) == 54
        says = _lines(dlg)
        assert ("Stitch holes through the layers: ● 54 are out of line, by "
                "up to 3.33 mm; ● 12 line up.") in says
        assert "Shell and Liner1: 44 line up with each other, 36 of them " \
            "red all the same: their needle lines are out of line " \
            "elsewhere." in says
        assert ("Shell (panel 1) and Liner2 (panel 3): ● 12 are out of line, "
                "by up to 3.33 mm.") in says
        assert ("Liner2, round folds A and B: 2 of its 22 holes meet when "
                "shut, 4 lying flat. Where the folding brings its panels 1 "
                "and 2 face to face, ● 10 of their holes are out of line, by "
                "up to 2.86 mm; ● 2 line up with each other, but are red all "
                "the same: their needle lines are out of line elsewhere.") \
            in " ".join(says)
    finally:
        dlg.close()


def test_the_marks_are_said_to_show_the_reading_only_where_they_do(
        qapp, mm, monkeypatch):
    from leathercad_app import preview3d
    dlg = _dialog(_c_fold("flat"))
    try:
        check = dlg.view.check
        assert check.readings == {1: (4, 2, False)}
        said = preview3d._readings_report(dlg.parts, dlg.layout_, check)
        assert "The marks on the model show that reading." in said[0]
        # counted as the marks were before the folding brought the Liner's
        # panels face to face (22 meeting lying flat, 4 marked): not said
        monkeypatch.setattr(check, "readings", {1: (22, 8, False)})
        said = preview3d._readings_report(dlg.parts, dlg.layout_, check)
        assert "22 lying flat" in said[0]
        assert "The marks on the model show" not in said[0]
    finally:
        dlg.close()


def _goes():
    """A strip 240 x 60 mm, 2 mm: fold A to the front (step 1) and fold B
    to the back (step 2), 90 degrees each, a pile on each side of them
    (60 mm and 20 mm). When fold A has folded the piles are 15.79 mm into
    each other; when fold B has, they stand clear."""
    return _drawn(_box(0, 0, 240, 60),
                  [((80, 0), (80, 60), 90.0, 1, 1, -1.0),
                   ((120, 0), (120, 60), 90.0, -1, 2, -1.0)],
                  [("Left pile", _box(40, 10, 75, 50), 60.0, 1),
                   ("Right pile", _box(125, 10, 160, 50), 20.0, 1)])


def test_leather_drawn_in_at_one_step_and_clear_at_the_next_is_said_so(
        qapp, mm):
    dlg = _dialog(_goes())
    try:
        clash, = [n for n in dlg.layout_.notes if n.kind == "clash"]
        assert (round(clash.rest, 2), clash.last) == (15.79, 1.0)
        assert clash.b == _near(0.7891, 1e-3)
        # in red, as the rule is (drawn in at a whole step), with when it
        # comes clear again
        assert ("⚠ Left pile and Right pile are drawn 15.79 mm into each "
                "other when fold A has folded (step 1), from about three "
                "quarters of the way through it; from when fold B has "
                "folded (step 2) they are clear again.") in _lines(dlg)
        # the caption's count and the red wash are of the whole folding
        assert dlg.view.alarm == ("2 things are drawn through each "
                                  "other: see the report")
        dlg._go(2.0)
        assert dlg.view.alarm == ("2 things are drawn through each "
                                  "other: see the report")
        bad = _rgb(dlg.view.look.bad)
        assert _rgb(dlg.view.wash[(1, None)]) == bad
        assert _rgb(dlg.view.wash[(2, None)]) == bad
    finally:
        dlg.close()
    mm.set_current("in")
    dlg = _dialog(_goes())
    try:
        assert ("⚠ Left pile and Right pile are drawn 0.621 in into each "
                "other when fold A has folded (step 1), from about three "
                "quarters of the way through it; from when fold B has "
                "folded (step 2) they are clear again.") in _lines(dlg)
        assert "mm" not in _words(dlg)
    finally:
        dlg.close()


def test_a_kind_of_note_the_window_does_not_know_is_passed_over(
        qapp, mm, monkeypatch):
    from leathercad.sheets import Note
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        said = _lines(dlg)
    finally:
        dlg.close()
    _noted(monkeypatch, Note("mystery", 1, 0, 9.0, 9.0, (2,), at=(0, 0)),
           Note("mystery", 0, -1, 9.0, 9.0),
           Note("clash", 1, -1, 1.0, 0.75, (2,), rest=1.0))
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        # read as before, with the clash given, a moment inside a step
        assert _lines(dlg) == said[:-1] + [
            "⚠ Bill pocket liner and Left pocket backing are drawn 1 mm "
            "into each other at rest, from three quarters of the way "
            "through step 1.",
            said[-1]]
        dlg._go(1.0)
        assert dlg.view.alarm.startswith("2 things")
    finally:
        dlg.close()


def _no_room():
    """``test_stackfold``'s strip in three that cannot close: 160 x 60,
    2 mm, its first flap with a 1 mm patch on it folded in over the
    middle, reaching 10 mm past the second fold, whose last panel is too
    narrow for the bend wherever it forms. The middle panel stays
    still."""
    return _drawn(_box(0, 0, 160, 60),
                  [((80, 0), (80, 60), 180.0, 1, 1, -1.0),
                   ((150, 0), (150, 60), 180.0, 1, 2, -1.0)],
                  [("Patch", _box(5, 5, 75, 55), 1.0, 1)], name="Strip")


def test_what_is_drawn_through_only_on_the_way_is_said_plainly_once_a_step(
        qapp, mm, monkeypatch):
    from leathercad.sheets import Note
    # the strip in three, which folds clean, given what the fold engine
    # would say of leather the picture draws through leather on the way
    # through step 2 and clear at rest: the strip in itself, a pocket in
    # another, a panel a moved fold brings down; a pocket torn on the way
    _noted(monkeypatch,
           Note("limit", 0, -1, 3.0, 1.25, (0,), rest=0.0),
           Note("clash", 1, -1, 0.8, 1.5, (2,), rest=0.0),
           Note("landing", 0, 0, 1.2, 1.75, (("panel", 0), ("bend", 0)),
                rest=0.0),
           Note("crossing", 1, 0, 0.0, 0.6, (1,), at=(0.0, 0.0), rest=0.0))
    dlg = _dialog(_strip("L", "M", "R"))
    try:
        says = _lines(dlg)
        assert ("On the way through step 2 the picture draws Shell into "
                "itself (up to 3 mm, from a quarter of the way through), "
                "Pocket L and Pocket M into each other (up to 0.8 mm, from "
                "half way through) and panel 1 and fold A of Shell into each "
                "other (up to 1.2 mm, from three quarters of the way "
                "through); at rest each is clear. The preview turns each "
                "panel as one stiff piece, where leather gives.") in says
        assert ("Pocket L lies over the place where folds A and B cross. "
                "Shell is folded along one and then the other and passes "
                "through itself there: the preview cannot show how Pocket L "
                "would lie. On the way the picture draws it up to 0.6 mm "
                "into leather there; it turns each panel as one stiff "
                "piece, where leather gives.") in says
        # no alarm: nothing in red, nothing counted, nothing painted red
        assert "#c0392b" not in dlg.readout.text()
        assert dlg.view.alarm == ""
        assert dlg.view.wash == {(1, 0): dlg.view.wash[(1, 0)]}
        assert _rgb(dlg.view.wash[(1, 0)]) == _rgb(dlg.view.look.warn)
    finally:
        dlg.close()


def test_a_crossing_drawn_through_at_rest_is_said_in_red_and_marked(
        qapp, mm, monkeypatch):
    from leathercad.sheets import Note
    _noted(monkeypatch, Note("crossing", 1, 0, 0.0, 0.9, (1,),
                             at=(0.0, 0.0), rest=0.4))
    dlg = _dialog(_strip("L", "M", "R"))
    try:
        assert ("⚠ Pocket L lies over the place where folds A and B cross, "
                "and is drawn 0.4 mm into leather there, and up to 0.9 mm "
                "on the way. Shell is folded along one and then the other "
                "and passes through itself there: the preview cannot show "
                "how Pocket L would lie.") in _lines(dlg)
        assert _rgb(dlg.view.wash[(1, None)]) == _rgb(dlg.view.look.bad)
        assert dlg.view.alarm == ("Pocket L is drawn into leather: see the "
                                  "report")
    finally:
        dlg.close()


# -- two stitch holes on one needle line meet, and are said to ---------------

def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _hole_words(lay):
    """What the report says of a stack's stitch holes, in plain text, with
    the check it was read from."""
    from leathercad_app import preview3d

    class Parts:
        names = [lay.name] + [s.name for s in lay.sheets]

    check = lay.lineup()
    said = [preview3d._laid_report(lay, check),
            preview3d._faced_report(check)] \
        + preview3d._readings_report(Parts(), lay, check)
    return check, [re.sub(r"<[^>]+>", "", x.replace("<br>", "\n"))
                   for x in said if x]


def _folded(holes, sheets, size=(200, 100), at=100.0, t=2.0, across=False):
    lay = fs.fold_piece(_rect(0, 0, *size), [fs.FoldLine(
        (0, at) if across else (at, 0),
        (size[0], at) if across else (at, size[1]), 180.0, 1,
        label="A")], thickness=t, holes=holes, sheets=sheets)
    lay.name = "Shell"
    return lay


def test_a_seam_facing_through_a_lining_with_no_hole_there_is_said_to_meet(
        qapp, mm):
    from leathercad import sheets as sh
    # 1.6 mm folded flat, 10 holes; two 0.8 mm linings outside, the inner
    # punched 6 mm off: the Shell's halves face 0 mm apart, and all 10 are
    # said to line up, as marked (once "none of the 10 has a hole to meet")
    row = [(100 - 10 - 17 * i, 90.0, 1.0) for i in range(5)] + \
        [(100 + 10 + 17 * i, 90.0, 1.0) for i in range(5)]
    check, said = _hole_words(_folded(row, [
        sh.Sheet(_rect(0, 0, 200, 97), 0.8, -1, name="Inner",
                 holes=[(x, y - 6.0, d) for x, y, d in row]),
        sh.Sheet(_rect(0, 0, 200, 97), 0.8, -1, name="Outer", holes=row)],
        t=1.6))
    assert [check.state.get(i) for i in range(10)] == [fs.MEETS] * 10
    assert ("Stitch holes where the folding brings pieces together: Shell, "
            "panels 1 and 2: ● all 10 line up.") in said
    assert "All 10 of Shell's holes pass through Inner, which has no holes " \
        "there." in said[0]
    assert not any("hole to meet" in x for x in said)


@pytest.mark.parametrize("side", [-1, 1])
def test_a_patch_with_one_snap_hole_far_off_is_told_as_one_with_none(
        qapp, mm, side):
    from leathercad import sheets as sh
    # a seam of 5 + 5 holes along x = 20 across a fold on y = 60; a 1 mm
    # patch on the lower half, outside or inside, with no holes and with
    # one 8 mm snap hole at (100, 30): the same marks and the same words
    # of the seam (with the snap hole it was once unmarked and said to
    # have no hole to meet; inside, never marked or said)
    row = [(20.0, 54.0 - 10 * i, 1.2) for i in range(5)] + \
        [(20.0, 66.0 + 10 * i, 1.2) for i in range(5)]
    got = []
    for holes in ([], [(100.0, 30.0, 8.0)]):
        check, said = _hole_words(_folded(row, [sh.Sheet(
            _rect(2, 5, 140, 58), 1.0, side, name="Patch", holes=holes)],
            size=(150, 120), at=60.0, across=True))
        assert [check.state.get(i) for i in range(10)] == [fs.MEETS] * 10
        # (inside, the patch lies between the halves when shut, and the
        # other half's holes come through it there too)
        assert said[0].startswith(
            "Stitch holes through the layers: "
            + ("5 of Shell's 10" if side < 0 else "All 10 of Shell's")
            + " holes pass through Patch, which has no holes there. The "
            "laser will not cut them in that piece.")
        got.append([x for x in said[1].split("\n") if "Shell, panels" in x])
    # (the snap hole, facing the Shell's other half when shut, is said to
    # have no hole to meet, in a line of its own)
    assert got == [["Stitch holes where the folding brings pieces together: "
                    "Shell, panels 1 and 2: ● all 10 line up."]] * 2 \
        if side < 0 else [[
            "Stitch holes where the folding brings pieces together: Shell, "
            "panels 1 and 2: ● all 10 line up."], [
            "Shell, panels 1 and 2: ● all 10 line up."]]


def test_holes_moved_by_the_shut_reading_are_said_where_they_land(qapp, mm):
    from leathercad import sheets as sh
    # 2 mm folded flat; a 1 mm lining inside punched for shut and a patch
    # on it over the left half with the Shell's holes there: every hole
    # meets where it lands, and nothing is said out of line (14 once were,
    # by 2.36 mm; and the layers 7, "by up to 0 mm")
    row = [(100 - 8 - 13 * i, 95.0, 1.0) for i in range(7)] + \
        [(100 + 8 + 13 * i, 95.0, 1.0) for i in range(7)]
    pull = 0.75 * math.pi
    check, said = _hole_words(_folded(row, [
        sh.Sheet(_rect(0, 0, 200, 98), 1.0, 1, name="Lining",
                 holes=[(x + (pull if x < 100 else -pull), y, d)
                        for x, y, d in row]),
        sh.Sheet(_rect(5, 60, 95, 99), 1.0, 1, name="Patch",
                 holes=row[:7])]))
    assert len(check.holes(fs.MEETS)) == 35
    words = " ".join(said)
    assert "out of line" not in words and "by up to 0" not in words
    assert ("Stitch holes where the folding brings pieces together: Patch "
            "(panel 1) and Lining (panel 2): ● all 14 line up.") in said
    assert said[-1].startswith(
        "Stitch holes of the pieces that go round a fold: the stack is read "
        "as punched to meet when shut, each piece's length round its folds "
        "taken up, the reading in which more of the whole stack's holes "
        "meet: all 35 do, against none lying flat.")


def test_a_c_fold_lining_punched_for_shut_is_told_as_the_model_marks_it(
        qapp, mm):
    # read shut, the Liner's holes are compared where they land: the
    # sentence of the Shell's first panel and the Liner's third says the
    # out of line holes marked on those two panels (once 10, with 5
    # marked), and the Liner's own panels facing are said too (once left
    # out read shut)
    dlg = _dialog(_c_fold("shut"))
    try:
        check = dlg.view.check
        assert check.readings == {1: (2, 4, True)}
        off = _marked(dlg, fs.OFF)
        says = _lines(dlg)
        faced, = [x for x in says if "Shell (panel 1) and Liner (panel 3)"
                  in x]
        n = int(re.search(r"(\d+) are out of line", faced).group(1))
        assert n == off[("Shell", 1)] + off[("Liner", 3)] == 12
        liner, = [x for x in says if x.startswith("Liner, round ")]
        assert ("Where the folding brings its panels 1 and 2 face to face, "
                "● 10 of their holes are out of line, by up to 2.86 mm; ● 2 "
                "line up with each other, but are red all the same: their "
                "needle lines are out of line elsewhere.") in liner
        assert "by up to 0 mm" not in _words(dlg)
        # the dot of a count is what the two do with each other, in this
        # line as in the line of any two pieces: red before those out of
        # line, green before those that line up with each other and are
        # red on the model all the same (once red before both)
        from leathercad_app import foldview as fv
        red, green = (f"<span style='color:{fv.hole_colour(s).name()}'>●"
                      "</span> " for s in (fs.OFF, fs.MEETS))
        text = dlg.readout.text()
        assert red + "10 of their holes are out of line" in text
        assert green + "2 line up with each other, but are red" in text
        assert red + "2 line up with each other" not in text
    finally:
        dlg.close()


def test_two_pieces_in_line_are_said_to_be_and_why_they_are_red(qapp, mm):
    from leathercad import sheets as sh
    # 1.2 mm folded flat; 0.6 mm linings: one outside punched 5.5 mm off,
    # one inside where the Shell is, one inside that punched for shut.
    # Read flat, the Shell and the first inside lining line up with each
    # other hole for hole, red only because the third's halves, folded,
    # are 2.36 mm off their needle lines: said so (once "28 are out of
    # line, by up to 2.36 mm", of two pieces that coincide)
    row = [(100 - 8 - 13 * i, 95.0, 1.0) for i in range(7)] + \
        [(100 + 8 + 13 * i, 95.0, 1.0) for i in range(7)]
    pull = 0.75 * math.pi
    check, said = _hole_words(_folded(row, [
        sh.Sheet(_rect(0, 0, 200, 98), 0.6, -1, name="Out",
                 holes=[(x, y - 5.5, d) for x, y, d in row]),
        sh.Sheet(_rect(0, 0, 200, 98), 0.6, 1, name="In", holes=row),
        sh.Sheet(_rect(0, 0, 200, 98), 0.6, 1, name="Pulled",
                 holes=[(x + (pull if x < 100 else -pull), y, d)
                        for x, y, d in row])], t=1.2))
    layers = said[0].split("\n")
    assert layers[0].endswith("● 42 are out of line, by up to 2.36 mm.")
    assert layers[1:] == [
        "Shell and In: 28 line up with each other, but are red all the "
        "same: their needle lines are out of line elsewhere.",
        "Shell and Pulled: 28 are out of line, by up to 2.36 mm.",
        "In and Pulled: 28 are out of line, by up to 2.36 mm."]


def test_a_piece_between_two_halves_shut_is_said_to_be_passed_through(
        qapp, mm):
    from leathercad import sheets as sh
    # a 1 mm strip in three, folded right onto middle, then left onto
    # right; holes on the left and middle panels that land on each other
    # through the right, and a patch with no holes on the right: the
    # needle goes through both, and that is said (once nothing was)
    mid = [(110.0 + 10 * i, 50.0, 1.0) for i in range(5)]
    lay = fs.fold_piece(
        _rect(0, 0, 260, 100),
        [fs.FoldLine((80, 0), (80, 100), 180.0, 1, label="A", order=2),
         fs.FoldLine((180, 0), (180, 100), 180.0, 1, label="B", order=1)],
        thickness=1.0, base=1,
        holes=mid + [(160.0 - x, y, d) for x, y, d in mid],
        sheets=[sh.Sheet(_rect(185, 10, 255, 90), 0.6, 1, name="Patch")])
    lay.name = "Shell"
    _check, said = _hole_words(lay)
    assert said[0] == (
        "Stitch holes through the layers: All 10 of Shell's holes pass "
        "through Patch, which has no holes there. The laser will not cut "
        "them in that piece. 10 of Shell's holes pass through its own "
        "panel 3 when it is shut, which has no holes there.")
    assert said[1] == ("Stitch holes where the folding brings pieces "
                       "together: Shell, panels 1 and 2: ● all 10 line up.")


def test_one_hole_passing_through_a_piece_is_said_as_one(qapp, mm):
    from leathercad import sheets as sh
    # a patch with a single snap hole, where the Shell has no hole: said
    # of one hole (once "All 1 of Patch's holes pass through")
    row = [(20.0, 54.0 - 10 * i, 1.2) for i in range(5)] + \
        [(20.0, 66.0 + 10 * i, 1.2) for i in range(5)]
    _check, said = _hole_words(_folded(row, [sh.Sheet(
        _rect(2, 5, 140, 58), 1.0, -1, name="Patch",
        holes=[(100.0, 30.0, 8.0)])], size=(150, 120), at=60.0,
        across=True))
    assert "Patch's hole passes through Shell, which has no hole there. " \
        "The laser will not cut it in that piece." in said[0]


# -- each miss on a needle line said once: by the two next to it, or the ends -

def _hole_lines(dlg):
    """The stitch-hole section of the report, line by line."""
    says = _lines(dlg)
    first = next(i for i, x in enumerate(says)
                 if x.startswith("Stitch holes"))
    return says[first:]


@pytest.mark.parametrize("how", ["flat", "shut"])
def test_a_c_fold_s_misses_are_said_by_the_shell_and_the_pieces_next_to_them(
        qapp, mm, how):
    # the C fold shut lies, from the middle up, Shell 2 | Liner 2 | Liner 1
    # | Shell 1 | Liner 3 | Shell 3. The Shell's rows do not mirror about
    # the two folds, and the Liner copies them: the Shell's own panels say
    # so, 1 and 2, 1 and 3, whatever lies between them; of the rest, the
    # two next to each other across a fold that miss -- Shell 1 and Liner
    # 3, Liner 1 and 2 -- and no pair further apart, whose misses those
    # say (once eleven statements)
    dlg = _dialog(_c_fold(how))
    try:
        assert _hole_lines(dlg) == [
            "Stitch holes through the layers: Shell and Liner: ● all 44 line "
            "up with each other, 36 of them red all the same: their needle "
            "lines are out of line elsewhere.",
            "Stitch holes where the folding brings pieces together: ● 24 are "
            "out of line, by up to 3.33 mm; ● 2 line up, to within 0.48 mm.",
            "Shell, panels 1 and 2: ● 10 are out of line, by up to 2.86 mm; "
            "● 4 line up with each other, to within 0.48 mm, 2 of them red "
            "all the same: their needle lines are out of line elsewhere.",
            "Shell, panels 1 and 3: ● 12 are out of line, by up to 3.33 mm.",
            "Shell (panel 1) and Liner (panel 3): ● 12 are out of line, by up "
            "to 3.33 mm.",
            READ_FLAT.format("8 of its 44 do, against 6 when shut")
            if how == "flat" else
            READ_SHUT.format("8 of its 44 do, against 6 lying flat"),
            "Liner, round folds A and B: "
            + ("2 of its 22 holes meet when shut, 4 lying flat"
               if how == "flat" else
               "4 of its 22 holes meet when shut, 2 lying flat")
            + ". Where the folding brings its panels 1 and 2 face to face, ● "
            "10 of their holes are out of line, by up to 2.86 mm; ● 2 line up "
            "with each other, but are red all the same: their needle lines "
            "are out of line elsewhere."]
    finally:
        dlg.close()


def test_a_lining_off_its_shell_is_said_once_not_again_across_the_fold(
        qapp, mm):
    # a lining 3.77 mm off its shell: the layers say so; folded, the
    # Shell's halves face the Lining's other half through the Lining, and
    # that is the same miss -- said no more (once twice more, 34 a time)
    dlg = _dialog(_lined(3.77))
    try:
        assert _hole_lines(dlg) == [
            "Stitch holes through the layers: Shell and Lining: ● 68 are out "
            "of line, by up to 3.77 mm.",
            READ_FLAT.format("none of its 68 do, against none when shut"),
            "Lining, round fold A: none of its 34 holes meet when shut, none "
            "lying flat. Where the folding brings its panels 1 and 2 face to "
            "face, ● 34 of their holes line up with each other, but are red "
            "all the same: their needle lines are out of line elsewhere."]
    finally:
        dlg.close()


def test_a_creep_through_three_layers_is_said_of_the_two_ends(qapp, mm):
    from leathercad import sheets as sh
    # a seam across a fold, its upper half 1 mm along from the lower; a
    # patch inside on the lower half punched half way: each is 0.5 mm from
    # the next, the two ends 1 mm apart. The Shell's halves, with the
    # holed patch between them, are named: no two next to each other miss
    row = [(20.0, 54.0 - 10 * i, 1.2) for i in range(5)] + \
        [(21.0, 66.0 + 10 * i, 1.2) for i in range(5)]
    check, said = _hole_words(_folded(row, [sh.Sheet(
        _rect(2, 5, 140, 58), 1.0, 1, name="Patch",
        holes=[(20.5, y, d) for _x, y, d in row[:5]])], size=(150, 120),
        at=60.0, across=True))
    assert len(check.off) == 15
    assert said == [
        "Stitch holes through the layers: Shell and Patch: ● all 10 line up "
        "with each other, to within 0.5 mm, but are red all the same: their "
        "needle lines are out of line elsewhere.",
        "Stitch holes where the folding brings pieces together: ● 15 are out "
        "of line, by up to 1 mm.\n"
        "Shell, panels 1 and 2: ● 10 are out of line, by up to 1 mm.\n"
        "Shell (panel 1) and Patch (panel 2): ● all 10 line up with each "
        "other, to within 0.5 mm, but are red all the same: their needle "
        "lines are out of line elsewhere."]


@pytest.mark.parametrize("top, told", [(60.0, 10), (50.03, 0), (50.0, 0),
                                       (49.97, 0)])
def test_a_patch_whose_edge_runs_along_a_seam_is_not_passed_through(
        qapp, mm, top, told):
    from leathercad import sheets as sh
    # the strip in three whose left and middle panels' holes (along y =
    # 50) land on each other through the right, with a patch with no holes
    # on the right, its top edge at ``top``: the needle comes through the
    # patch's leather only where the layers' rule has it so -- more than a
    # hair inside its edge (once 10 on the very edge and 0.03 mm inside)
    mid = [(110.0 + 10 * i, 50.0, 1.0) for i in range(5)]
    lay = fs.fold_piece(
        _rect(0, 0, 260, 100),
        [fs.FoldLine((80, 0), (80, 100), 180.0, 1, label="A", order=2),
         fs.FoldLine((180, 0), (180, 100), 180.0, 1, label="B", order=1)],
        thickness=1.0, base=1,
        holes=mid + [(160.0 - x, y, d) for x, y, d in mid],
        sheets=[sh.Sheet(_rect(185, 10, 255, top), 0.6, 1, name="Patch")])
    lay.name = "Shell"
    _check, said = _hole_words(lay)
    assert said[0].startswith(
        "Stitch holes through the layers: All 10 of Shell's holes pass "
        "through Patch, which has no holes there." if told else
        "Stitch holes through the layers: 10 of Shell's holes pass through "
        "its own panel 3 when it is shut")


def test_a_lining_s_one_hole_and_one_hole_meeting_are_said_as_one(qapp, mm):
    from leathercad import sheets as sh
    # a lining with a single snap hole, far from the seam, round a fold:
    # "its one hole" (once "none of its 1 holes meet"); and a lining round
    # a C fold of which one hole meets when shut: "1 of its 18 holes
    # meets" (once "meet")
    row = [(100 - 8 - 13 * i, 50.0, 1.0) for i in range(7)] + \
        [(100 + 8 + 13 * i, 50.0, 1.0) for i in range(7)]
    _check, said = _hole_words(_folded(row, [sh.Sheet(
        _rect(0, 0, 200, 98), 1.0, 1, name="Lining",
        holes=[(20.0, 20.0, 8.0)])]))
    assert said[-1].split("\n")[-1] == (
        "Lining, round fold A: its one hole does not meet when shut, nor "
        "lying flat.")
    holes = [(6 + 13 * i, 95.0, 1.0) for i in range(20)
             if abs(6 + 13 * i - 80) > 7 and abs(6 + 13 * i - 180) > 7]
    lay = fs.fold_piece(
        _rect(0, 0, 260, 100),
        [fs.FoldLine((80, 0), (80, 100), 180.0, 1, label="A", order=2),
         fs.FoldLine((180, 0), (180, 100), 180.0, 1, label="B", order=1)],
        thickness=1.6, base=1, holes=holes,
        sheets=[sh.Sheet(_rect(0, 0, 260, 98), 0.8, name="Lining",
                         holes=holes)])
    lay.name = "Strip"
    _check, said = _hole_words(lay)
    assert said[-1].split("\n")[-1].startswith(
        "Lining, round folds A and B: 1 of its 18 holes meets when shut, 8 "
        "lying flat.")


def test_a_creep_through_a_lining_round_the_fold_is_said_of_the_shell(
        qapp, mm):
    # a 2 mm Shell folded in half, a 1 mm Lining round the inside, each
    # row 0.5 mm along from the next: Shell 10, Lining 10.5 and 11, Shell
    # 11.5 from the fold. Each two next to each other line up; the Shell's
    # own halves are 1.5 mm apart, said so, and nothing else across the
    # fold (once the Shell's lower half with the Lining's upper, 1 mm, and
    # the 1.5 mm said nowhere)
    shell = _piece("Shell", 200, 100)
    lining = _piece("Lining", 200, 98, at=(0, 300))
    doc = _drawing(shell, lining, _line((0, -50), (0, 50), "Fold"),
                   _seam((-90, 45), (-10, 45)), _seam((11.5, 45), (91.5, 45)),
                   _seam((-90.5, 346), (-10.5, 346)),
                   _seam((11.0, 346), (91.0, 346)))
    key = asm.put_on(doc, shell, thickness=2.0)
    asm.put_on(doc, lining, key, thickness=1.0)
    asm.line_up(doc, lining, "centre")
    asm.line_up(doc, lining, "bottom")
    dlg = _dialog(doc)
    try:
        assert _hole_lines(dlg) == [
            "Stitch holes through the layers: Shell and Lining: ● all 68 line "
            "up with each other, to within 0.5 mm, but are red all the same: "
            "their needle lines are out of line elsewhere.",
            "Stitch holes where the folding brings pieces together: Shell, "
            "panels 1 and 2: ● 34 are out of line, by up to 1.5 mm.",
            READ_FLAT.format("none of its 68 do, against none when shut"),
            "Lining, round fold A: none of its 34 holes meet when shut, none "
            "lying flat. Where the folding brings its panels 1 and 2 face to "
            "face, ● 34 of their holes line up with each other, but are red "
            "all the same: their needle lines are out of line elsewhere."]
    finally:
        dlg.close()


@pytest.mark.parametrize("side", [-1, 1])
def test_a_patch_over_part_of_a_seam_leaves_the_halves_said_in_line(
        qapp, mm, side):
    from leathercad import sheets as sh
    # a 1 mm Shell folded in half, 9 holes a side mirrored; a 0.6 mm
    # Patch, holed as the Shell, over the first four of the right half,
    # outside or between the halves: the Shell's halves face each other
    # directly beside the Patch, and all 18 are said to line up
    right = [(110.0 + 10 * i, 50.0, 1.0) for i in range(9)]
    lay = fs.fold_piece(
        _rect(0, 0, 200, 100),
        [fs.FoldLine((100, 0), (100, 100), 180.0, 1, label="A")],
        thickness=1.0, base=1,
        holes=right + [(200.0 - x, y, d) for x, y, d in right],
        sheets=[sh.Sheet(_rect(105, 10, 145, 90), 0.6, side, name="Patch",
                         holes=right[:4])])
    lay.name = "Shell"
    check, said = _hole_words(lay)
    assert len(check.off) == 0
    assert said[0] == "Stitch holes through the layers: Shell and Patch: ● " \
        "all 8 line up."
    assert "Shell, panels 1 and 2: ● all 18 line up." in said[1]


def test_a_stack_of_one_hole_is_read_as_one(qapp, mm):
    from leathercad import sheets as sh
    # a Shell with no holes, a Lining round its fold with one snap hole:
    # the whole stack's one hole is read as one (once ": none of its 1
    # do")
    _check, said = _hole_words(_folded([], [sh.Sheet(
        _rect(0, 0, 200, 98), 1.0, 1, name="Lining",
        holes=[(20.0, 20.0, 8.0)])]))
    assert said[-1] == READ_FLAT.format(
        "its one hole does not, against none when shut") + "\n" \
        "Lining, round fold A: its one hole does not meet when shut, nor " \
        "lying flat."


@pytest.mark.parametrize("pitch, said", [
    (10.0, "● 6 are out of line, by up to 3.2 mm; "),
    (11.0, "● 10 are out of line, by up to 3.76 mm."),
    (12.0, "● 8 are out of line, by up to 3.57 mm; ")])
def test_two_pairs_as_far_out_of_line_are_said_by_the_two_nearer(
        qapp, mm, pitch, said):
    # the C fold with two liners, the second punched for shut. Liner2 is
    # cut to copy the Shell, so on every needle line its first panel and
    # the Shell's are as far from Liner1's second, to well under a
    # thousandth of a millimetre (a few ten-millionths apart): the two
    # with the less leather between them say it, and the other two do
    # not, whatever the pitch (once the last digit of a sum chose between
    # them needle line by needle line; then the sixth decimal did, and at
    # a pitch of 10 mm the Shell's line was given, not Liner2's)
    dlg = _dialog(_c_fold("flat", "shut", pitch=pitch))
    try:
        says = _hole_lines(dlg)
        assert any(line.startswith("Liner2 (panel 1) and Liner1 (panel 2): "
                                   + said) for line in says)
        assert not any(line.startswith("Shell (panel 1) and Liner1 (panel 2)")
                       for line in says)
    finally:
        dlg.close()

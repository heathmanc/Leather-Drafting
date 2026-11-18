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
        # chosen and seen through, a mark is still there, but faint
        dlg.chk_through.setChecked(True)
        dlg.pieces.choose([by["Bill pocket liner"].shape_id])
        faint = view.wash[(pocket, 0)]
        assert faint.alpha() == 184 and _rgb(faint) != _rgb(look.warn)
        assert view.wash[(pocket, None)].alpha() == 184
        assert _rgb(view.wash[(1, 0)]) == _rgb(look.warn)  # (not over it)
        dlg.chk_through.setChecked(False)
        assert set(view.wash) == {(k, 0) for k in range(1, 10)}
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


def test_a_stack_built_to_clash_is_said_in_red_and_marked_red(qapp, mm):
    dlg = _dialog(_square())
    try:
        lay = dlg.layout_
        assert lay.n_steps == 2
        found = [(n.kind, n.sheet, n.other, round(n.a, 2), n.b)
                 for n in lay.notes if n.kind in ("clash", "limit")]
        assert found == [("limit", 0, (0,), 1.51, 2.0),
                         ("clash", 0, (1,), 1.47, 2.0),
                         ("clash", 1, (1,), 0.88, 2.0)]
        # the caption's second line, in red, from the start
        assert dlg.view.caption.startswith("Put together  ·  2 pieces")
        assert dlg.view.alarm == ("2 things are drawn through each other: "
                                  "see the report")
        says = _lines(dlg)
        assert ("⚠ Square and Patch are drawn 1.47 mm into each other when "
                "fold B has closed (step 2).") in says
        assert ("⚠ Patch is drawn 0.88 mm into itself when fold B has "
                "closed (step 2).") in says
        # the piece itself, worded as the piece's own
        assert ("⚠ Square is drawn 1.51 mm into itself at step 2: folds "
                "that cross are folded one after the other, and the preview "
                "cannot open the leather for the second.") in says
        assert ("Patch lies over the place where folds A and B cross. "
                "Square is folded along one and then the other and passes "
                "through itself there: the preview cannot show how Patch "
                "would lie.") in says
        text = dlg.readout.text()
        assert text.count("<span style='color:#c0392b'>⚠") >= 3
        # both pieces marked red, whole
        bad = _rgb(dlg.view.look.bad)
        assert {k: _rgb(c) for k, c in dlg.view.wash.items()
                if k[1] is None} == {(0, None): bad, (1, None): bad}
        dlg._go(2.0)
        assert dlg.view.alarm.startswith("2 things")
    finally:
        dlg.close()
    # in inches no millimetre is left in what is said in red
    mm.set_current("in")
    dlg = _dialog(_square())
    try:
        assert ("⚠ Square and Patch are drawn 0.058 in into each other when "
                "fold B has closed (step 2).") in _lines(dlg)
        assert "mm" not in _words(dlg)
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
        assert ("Strap lies across the end of fold A, where Tee goes on flat "
                "beside it: it is shown cut there. In leather it would have "
                "to be slit there, or stop short of the fold.") \
            in _lines(dlg)
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
        qapp, mm):
    doc, _by = _generated()
    dlg = _dialog(doc)
    try:
        v = dlg.view
        dlg.slider_apart.setValue(100)            # 10 mm a layer
        assert v.apart == 10.0 and v.t == 0.0
        dlg._toggle_play()
        steps = []
        for _ in range(12):
            dlg._last_tick -= 0.1
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
        assert dlg.folds_tab.isEnabled() and dlg.table.rowCount() == 0
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


def test_a_lining_with_holes_round_a_fold_is_read_both_ways(qapp, mm):
    over = 1.5 * math.pi
    dlg = _dialog(_lined())
    try:
        assert dlg.view.check.readings == {1: (34, 0, False)}
        assert ("Lining's holes are read as punched flat: 34 of 34 meet "
                "Shell's flat, and none when shut. The marks on the model "
                "show that reading.") in _lines(dlg)
        cross, = [n for n in dlg.layout_.notes if n.kind == "cross"]
        assert cross.a == _near(over)
    finally:
        dlg.close()
    dlg = _dialog(_lined(0.5 * over))
    try:
        assert dlg.view.check.readings == {1: (0, 34, True)}
        assert ("Lining's holes are read as punched to meet when shut, its "
                "length round fold A taken up: 34 of 34 meet Shell's shut, "
                "and none lying flat. The marks on the model show that "
                "reading.") in _lines(dlg)
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

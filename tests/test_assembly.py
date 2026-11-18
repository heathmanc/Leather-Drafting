"""Putting pieces together: what a piece keeps with the drawing (its
thickness, its leather, its place in a stack), where a placed piece lies,
what each edit on the drawing does to it, and how a drawing is read into
its stacks."""

import inspect
import json
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad import assembly as asm  # noqa: E402
from leathercad import foldsolid as fs  # noqa: E402
from leathercad import shapes as shapes_mod  # noqa: E402
from leathercad import templates  # noqa: E402
from leathercad.document import Document  # noqa: E402
from leathercad.generators import bifold_wallet, hemmed_pocket  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import (Circle, EditablePath, PathShape,  # noqa: E402
                               Placement, Polygon, Rectangle, Transform,
                               copy_shape_attrs)
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """Main windows on a drawing, their settings kept in a file of the
    test's own, shut without a question when the test is over."""
    from leathercad_app.mainwindow import MainWindow
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        MainWindow, "_settings",
        lambda self: QSettings(ini, QSettings.Format.IniFormat))
    made = []

    def make(doc):
        win = MainWindow(doc)
        win.canvas.rebuild()
        made.append(win)
        return win
    yield make
    for win in made:
        win._unsaved_changes = False
        win.hide()


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


def _line(a, b, name="", way="front", angle=180.0, layer="Score"):
    ln = PathShape(points=[Vec2(*a), Vec2(*b)], close_path=False, name=name,
                   layer=layer)
    ln.fold_dir, ln.fold_angle = way, angle
    return ln


def _drawing(*shapes):
    doc = Document("pieces")
    for sh in shapes:
        doc.add_shape(sh)
    return doc


_POCKETS = ("pocket backing", "pocket 3", "pocket 2", "pocket 1 (front)")


def _bifold():
    """The generated bifold put together: the shell is the main piece,
    the liner lies centred on it, and each stack of pockets is level with
    its end of the shell, bottoms together."""
    doc = _drawing(*bifold_wallet())
    by = {s.name: s for s in doc.shapes}
    key = asm.put_on(doc, by["Outer shell"], thickness=1.6)
    asm.put_on(doc, by["Bill pocket liner"], key, thickness=1.0)
    asm.line_up(doc, by["Bill pocket liner"], "centre")
    for side in ("Left", "Right"):
        for name in _POCKETS:
            asm.put_on(doc, by[f"{side} {name}"], key, thickness=1.0)
            asm.line_up(doc, by[f"{side} {name}"], side.lower())
    return doc, by


def _boxes(doc):
    """Where the box of each placed piece lies on the bench, by name."""
    return {p.name: p.box for st in asm.stacks(doc, detail=False)
            for p in st.pieces}


def _named(doc, name):
    return [s for s in doc.shapes if s.name == name]


def _near(values, abs=1e-9):
    return pytest.approx(values, abs=abs)


def _flat(points):
    """Points laid end to end, to compare a whole outline at once."""
    return [v for p in points for v in p]


def _same(got, want, abs=1e-9):
    """Whether two readings of where boxes lie agree, name by name."""
    assert sorted(got) == sorted(want)
    for name in got:
        assert got[name] == _near(want[name], abs)
    return True


def _select(win, *shapes):
    """Select these shapes on the canvas; their items come back."""
    from leathercad_app.items import ShapeItem
    c = win.canvas
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem) and any(it.model is s for s in shapes):
            it.setSelected(True)
    QApplication.processEvents()
    return [it for it in c.selected_items() if isinstance(it, ShapeItem)]


def _add_ell(doc, side="left"):
    """An L pocket put on a bifold's stack, level with one end of it."""
    sh = doc.add_shape(_ell())
    asm.put_on(doc, sh, doc.shapes[0].placed.of, thickness=1.2)
    asm.line_up(doc, sh, side)
    return sh


# -- kept with the drawing ----------------------------------------------------

# two shapes exactly as release 0.4.13 writes them
_RELEASED = (
    '{"kind": "rectangle", "name": "Panel", "layer": "Cut", "opacity": 1.0,'
    ' "shape_id": "shape1", "transform": {"x": 10, "y": -5, "rotation": 90,'
    ' "mirror_x": false}, "stitch": null, "construction": false,'
    ' "group_id": null, "fold_dir": "", "fold_angle": 90.0, "width": 105,'
    ' "height": 70, "corner_radius": 6}',
    '{"kind": "path", "name": "Fold", "layer": "Score", "opacity": 1.0,'
    ' "shape_id": "shape2", "transform": {"x": 0.0, "y": 0.0,'
    ' "rotation": 0.0, "mirror_x": false}, "stitch": null,'
    ' "construction": false, "group_id": null, "fold_dir": "back",'
    ' "fold_angle": 180.0, "points": [[0, -35], [0, 35]],'
    ' "close_path": false}')


def _released_drawing():
    doc = Document("old")
    doc.add_shape(Rectangle(name="Panel", width=105, height=70,
                            corner_radius=6, layer="Cut", shape_id="shape1",
                            transform=Transform(x=10, y=-5, rotation=90)))
    ln = PathShape(name="Fold", points=[Vec2(0, -35), Vec2(0, 35)],
                   close_path=False, layer="Score", shape_id="shape2")
    ln.fold_dir, ln.fold_angle = "back", 180.0
    doc.add_shape(ln)
    return doc


def test_thickness_leather_and_place_are_saved_with_the_drawing():
    shell = _piece("Shell", 200, 90)
    pocket = _piece("Pocket", 97.6, 44, at=(300, 0))
    doc = _drawing(shell, pocket)
    shell.thickness, shell.leather = 1.6, "Black"
    shell.placed = Placement(of="stack7", order=1, main=True, over=True)
    pocket.thickness = 1.0
    pocket.placed = Placement(of="stack7", order=2, ax=1.0, ay=0.0, x=-2.5,
                              turn=90.0, flip=True, cx=48.7, cy=-22.0)
    a, b = doc.to_dict()["shapes"]
    assert (a["thickness"], a["leather"]) == (1.6, "Black")
    assert b["thickness"] == 1.0 and "leather" not in b
    # a place is written as only what differs from one with nothing set
    assert a["placed"] == {"of": "stack7", "order": 1, "main": True,
                           "over": True}
    assert b["placed"] == {"of": "stack7", "order": 2, "ax": 1.0, "ay": 0.0,
                           "x": -2.5, "turn": 90.0, "flip": True,
                           "cx": 48.7, "cy": -22.0}
    # through a file and back
    a, b = Document.from_dict(json.loads(json.dumps(doc.to_dict()))).shapes
    assert (a.thickness, a.leather, a.placed) == (1.6, "Black", shell.placed)
    assert (b.thickness, b.leather, b.placed) == (1.0, "", pocket.placed)
    assert a.placed is not shell.placed
    # none of it is geometry: changing it doesn't rebuild the outline
    key = pocket.geometry_key()
    assert key == (97.6, 44, 0.0)
    pocket.thickness, pocket.leather, pocket.placed.x = 2.2, "Tan", 9.0
    assert pocket.geometry_key() == key
    assert _ell().geometry_key() == (tuple(Vec2(*p) for p in _ELL), 0.0,
                                     True, True, None)


def test_a_drawing_with_nothing_set_is_saved_as_the_release_saves_it():
    doc = _released_drawing()
    saved = doc.to_dict()["shapes"]
    # byte for byte: the same keys, in the same order, with the same values
    assert [json.dumps(s) for s in saved] == list(_RELEASED)
    for key in ("thickness", "leather", "placed"):
        assert all(key not in s for s in saved)
    # a thickness of nothing, an empty leather and no place write nothing
    doc.shapes[0].thickness, doc.shapes[0].leather = 0.0, ""
    doc.shapes[0].placed = None
    assert json.dumps(doc.to_dict()["shapes"][0]) == _RELEASED[0]


def test_an_old_file_reads_and_what_a_place_does_not_know_is_passed_over():
    old = [json.loads(s) for s in _RELEASED]
    doc = Document.from_dict({"name": "old", "shapes": old})
    for sh in doc.shapes:
        assert (sh.thickness, sh.leather, sh.placed) == (0.0, "", None)
    # a file from a later release: a place with keys this one doesn't know
    old[0]["thickness"], old[0]["leather"] = 2.4, "Chestnut"
    old[0]["placed"] = {"of": "stack3", "order": 2, "ay": 1.0, "y": -4.25,
                        "glued": True, "sewn_to": {"piece": "shape9"},
                        "turn": "a quarter", "x": None}
    panel = Document.from_dict({"shapes": old}).shapes[0]
    assert (panel.thickness, panel.leather) == (2.4, "Chestnut")
    assert panel.placed == Placement(of="stack3", order=2, ay=1.0, y=-4.25)
    # ... and it writes back only what it knows
    assert Document.from_dict({"shapes": old}).to_dict()["shapes"][0][
        "placed"] == {"of": "stack3", "order": 2, "ay": 1.0, "y": -4.25}
    # a place that isn't one at all is no place
    old[0]["placed"] = "on the shell"
    assert Document.from_dict({"shapes": old}).shapes[0].placed is None


def test_a_new_stack_never_takes_the_id_of_one_in_an_opened_file():
    high = shapes_mod._shape_counter[0] + 5000
    shell = _piece("Shell", 200, 90)
    shell.placed = Placement(of=f"stack{high}", order=1, main=True)
    doc = Document.from_dict(_drawing(shell, _piece("Strap", 150, 20,
                                                    at=(0, 200))).to_dict())
    assert shapes_mod._shape_counter[0] == high      # past the stack's id
    key = asm.put_on(doc, doc.shapes[1])
    assert key == f"stack{high + 1}"
    assert [st.key for st in asm.stacks(doc)] == [f"stack{high}", key]
    # shapes draw on the same counter: no shape takes a stack's number
    assert Rectangle().shape_id == f"shape{high + 2}"


def test_an_undo_snapshot_survives_later_changes_to_the_stack():
    doc, by = _bifold()
    snapshot = doc.to_dict()
    kept = json.dumps(snapshot)
    # the live drawing is changed in place, as the window changes it
    asm.move_by(doc, by["Left pocket 2"], 12.0, 3.0)
    asm.turn_over(doc, by["Outer shell"])
    by["Left pocket 2"].placed.order = 99
    by["Bill pocket liner"].thickness = 3.3
    assert json.dumps(snapshot) == kept
    # and so is a drawing read back from the snapshot (what Undo shows)
    back = Document.from_dict(snapshot)
    assert _boxes(back)["Left pocket 2"] == _near((-99.6, -45.0, -2.0, -1.0))
    asm.take_off(back, back.shapes)
    back.shapes[0].thickness = 9.0
    assert json.dumps(snapshot) == kept
    assert asm.stacks(back) == []
    assert len(asm.stacks(Document.from_dict(snapshot))[0].pieces) == 10


def test_a_command_that_rebuilds_a_shape_keeps_its_leather_and_place():
    pocket = _piece("Pocket", 97.6, 44, at=(300, 0))
    pocket.transform.rotation = 30.0
    pocket.thickness, pocket.leather = 1.2, "Black"
    pocket.placed = Placement(of="stack7", order=3, ax=0.0, ay=0.0, x=4.0,
                              turn=10.0, cx=-47.2, cy=-23.0)
    # rebuilt as nodes centred on its points, as Trim and Subtract leave
    # a shape: not turned on the drawing any more
    new = Polygon(points=[Vec2(0, 0), Vec2(1, 0), Vec2(1, 1)],
                  close_path=True, transform=Transform(x=300, y=0))
    assert copy_shape_attrs(pocket, new) is new
    assert (new.thickness, new.leather) == (1.2, "Black")
    # only the turn is re-based: the leather lies as it lay
    assert new.placed == Placement(of="stack7", order=3, ax=0.0, ay=0.0,
                                   x=4.0, turn=340.0, cx=-47.2, cy=-23.0)
    assert new.placed is not pocket.placed
    # lying mirrored, its own frame turns the other way on the bench
    pocket.placed.over = True
    new = copy_shape_attrs(pocket, Polygon(transform=Transform()))
    assert new.placed.turn == 40.0 and new.placed.over
    pocket.placed.flip = True                # mirrored twice: not mirrored
    new = copy_shape_attrs(pocket, Polygon(transform=Transform()))
    assert new.placed.turn == 340.0
    # a shape in no stack gives none
    pocket.placed = None
    new.placed = Placement(of="stack1")
    assert copy_shape_attrs(pocket, new).placed is None


# -- frames -------------------------------------------------------------------

def test_a_piece_s_own_frame_is_its_outline_with_its_place_and_turn_off():
    sh = _ell(at=(400, 300))
    own = asm.own_outline(sh)
    assert own == _ELL
    # moved and turned on the drawing it is the same piece, to the last
    # digit
    sh.transform.x, sh.transform.y, sh.transform.rotation = -120.0, 77.5, 30
    assert asm.own_outline(sh) == own
    m = asm.own_frame(sh)
    for world, mine in zip(sh.world_polyline()[0], own):
        assert asm.map_point(m, (world.x, world.y)) == _near(mine)
    # mirrored on the drawing it is another piece: the mirror is kept
    sh.transform.mirror_x = True
    assert asm.own_outline(sh) == [(-x, y) for x, y in _ELL]
    for world, mine in zip(sh.world_polyline()[0], asm.own_outline(sh)):
        assert asm.map_point(asm.own_frame(sh), (world.x, world.y)) == \
            _near(mine)
    # a line encloses no leather
    assert asm.own_outline(_line((0, 0), (10, 0))) == []


def test_a_piece_moved_and_turned_on_the_drawing_lies_where_it_lay():
    doc, by = _bifold()
    was = asm.stacks(doc)[0]
    for k, sh in enumerate(doc.shapes):
        sh.transform.x += 300.0             # the fold line with its shell
        if "pocket" in sh.name:             # ... and each pocket turned
            sh.transform.y -= 41.0 * k
            sh.transform.rotation += 90.0 * (k % 4)
    now = asm.stacks(doc)[0]
    assert now.signature() == was.signature()        # to the last digit
    assert [p.box for p in now.pieces] == [p.box for p in was.pieces]
    assert [p.placed for p in now.pieces] == [p.placed for p in was.pieces]
    assert {s.transform.rotation for s in doc.shapes} == {0.0, 90.0, 180.0,
                                                          270.0}


def test_a_piece_lies_on_the_bench_by_its_turn_and_which_side_is_up():
    shell, ell = _piece("Shell", 200, 90), _ell()
    doc = _drawing(shell, ell)
    key = asm.put_on(doc, shell)
    asm.put_on(doc, ell, key)
    lie = asm.stacks(doc)[0].piece(ell.shape_id)
    # corner to corner on the shell, as it is cut
    assert lie.box == _near((-100.0, -45.0, -20.0, 5.0))
    assert _flat(lie.outline) == _near(_flat((x - 60.0, y - 20.0)
                                             for x, y in _ELL))
    assert (lie.grain_up, lie.mirrored, lie.above) == (True, False, True)
    assert fs.pc.area(lie.outline) > 0               # counter-clockwise
    # the maps: its own frame, and the drawing, to the bench
    assert asm.map_point(lie.from_own, (10, 5)) == _near((-50.0, -15.0))
    assert asm.map_point(lie.from_drawing, (410, 305)) == \
        _near((-50.0, -15.0))
    # turned a quarter and over: T + R(turn) M(own), M mirroring x
    ell.placed.turn, ell.placed.over = 90.0, True
    lie = asm.stacks(doc)[0].piece(ell.shape_id)
    assert (lie.grain_up, lie.mirrored) == (False, True)
    assert lie.box == _near((-100.0, -45.0, -50.0, 35.0))
    # own (10, 5) -> mirrored (-10, 5) -> turned (-5, -10) -> moved
    assert asm.map_point(lie.from_own, (10, 5)) == _near((-80.0, -15.0))
    assert fs.pc.area(lie.outline) > 0               # still, though mirrored
    assert abs(fs.pc.area(lie.outline)) == pytest.approx(3400.0)


# -- placing ------------------------------------------------------------------

def test_the_first_piece_put_on_starts_a_stack_and_is_its_main_piece():
    shell, pocket = _piece("Shell", 200, 90), _piece("Pocket", 80, 40,
                                                     at=(300, 0))
    pocket.thickness = 1.2
    doc = _drawing(shell, pocket, _line((0, -45), (0, 45), "Spine"))
    assert asm.stacks(doc) == []
    key = asm.put_on(doc, shell, thickness=2.0)
    assert key.startswith("stack")
    assert shell.placed == Placement(of=key, order=1, main=True)
    # a piece with no thickness is given the one asked for; one that has
    # its own keeps it
    assert asm.put_on(doc, pocket.shape_id, key, thickness=2.0) == key
    assert (shell.thickness, pocket.thickness) == (2.0, 1.2)
    st, = asm.stacks(doc)
    assert (st.key, st.lost, st.main.name) == (key, False, "Shell")
    assert [(p.name, p.order, p.main, p.above) for p in st.pieces] == \
        [("Shell", 1, True, False), ("Pocket", 2, False, True)]
    assert st.main.box == _near((-100.0, -45.0, 100.0, 45.0))
    # putting it on again changes nothing; a line is no piece
    was = pocket.placed
    assert asm.put_on(doc, pocket, key) == key and pocket.placed == was
    assert asm.put_on(doc, doc.shapes[2], key) == ""
    assert doc.shapes[2].placed is None


def test_a_piece_drawn_apart_lands_corner_to_corner():
    shell, liner = _piece("Shell", 200, 90), _piece("Liner", 180, 70,
                                                    at=(0, 200))
    one, two = _piece("One", 80, 40, at=(300, 0)), _piece("Two", 60, 30,
                                                          at=(300, 100))
    doc = _drawing(shell, liner, one, two)
    key = asm.put_on(doc, shell)
    asm.put_on(doc, liner, key)
    assert _boxes(doc)["Liner"] == _near((-100.0, -45.0, 80.0, 25.0))
    p = liner.placed
    assert (p.ax, p.x, p.ay, p.y, p.order) == (0.0, 0.0, 0.0, 0.0, 2)
    asm.move_by(doc, liner, 6.0, 4.0)
    # with no piece chosen: on the top piece, bottom left corners together
    asm.put_on(doc, one, key)
    assert _boxes(doc)["One"] == _near((-94.0, -41.0, -14.0, -1.0))
    p = one.placed                      # ... and held from the main piece
    assert (p.ax, p.x, p.ay, p.y, p.order) == (0.0, 6.0, 0.0, 4.0, 3)
    assert (p.cx, p.cy) == (-54.0, -21.0)
    # on a chosen piece: its corner, and still the top of the pile
    asm.put_on(doc, two, on=shell)
    assert two.placed.of == key
    assert _boxes(doc)["Two"] == _near((-100.0, -45.0, -40.0, -15.0))
    assert [p.name for p in asm.stacks(doc)[0].pieces] == \
        ["Shell", "Liner", "One", "Two"]


def test_a_piece_drawn_on_the_main_piece_is_put_on_as_drawn():
    doc = templates.card_holder()
    panel, pocket = doc.shapes
    key = asm.put_on(doc, panel, thickness=2.0)
    assert asm.drawn_on_main(doc, pocket, key)
    asm.put_on(doc, pocket, key, thickness=1.2)
    # bottoms level as drawn, and as wide as the panel: centred on it
    assert _boxes(doc)["Pocket"] == _near((-52.5, -35.0, 52.5, 10.0))
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y, p.turn) == (0.5, 0.0, 0.0, 0.0, 0.0)
    assert (p.cx, p.cy) == (0.0, -12.5)
    # the whole pattern turned a quarter on the drawing: nothing changes
    doc = templates.card_holder()
    panel, pocket = doc.shapes
    panel.transform.rotation = pocket.transform.rotation = 90.0
    pocket.transform.x, pocket.transform.y = 12.5, 0.0
    asm.put_on(doc, pocket, asm.put_on(doc, panel))
    assert _boxes(doc)["Pocket"] == _near((-52.5, -35.0, 52.5, 10.0))
    assert pocket.placed.turn == 0.0
    # the pocket alone drawn turned on its panel: it lies turned
    doc = templates.card_holder()
    panel, pocket = doc.shapes
    pocket.transform.rotation, pocket.transform.y = 90.0, 0.0
    asm.put_on(doc, pocket, asm.put_on(doc, panel))
    assert _boxes(doc)["Pocket"] == _near((-22.5, -52.5, 22.5, 52.5))
    p = pocket.placed
    assert (p.turn, p.ax, p.x, p.ay, p.y) == (90.0, 0.5, 0.0, 0.5, 0.0)


def test_a_piece_put_on_as_drawn_lies_on_the_bench_as_on_the_drawing():
    panel, ell = _piece("Panel", 120, 90, at=(40, -25)), _ell(at=(55, -20))
    panel.transform.rotation = 20.0          # the pattern drawn askew
    ell.transform.rotation, ell.transform.mirror_x = 65.0, True
    doc = _drawing(panel, ell)
    key = asm.put_on(doc, panel)
    asm.turn(doc, panel, 90.0)               # and laid upright on the bench
    asm.put_on(doc, ell, key)
    under, on = asm.stacks(doc)[0].pieces
    assert under.box == _near((-45.0, -60.0, 45.0, 60.0))
    # the bench is the drawing as the main piece carries it: the pocket's
    # outline goes where the panel's map takes it
    drawn = [asm.map_point(under.from_drawing, (p.x, p.y))
             for p in ell.world_polyline()[0][:-1]]
    # (to the millionth of a millimetre a place is kept to)
    assert _flat(sorted(on.outline)) == _near(_flat(sorted(drawn)),
                                              abs=1e-6)
    assert (ell.placed.turn, ell.placed.over, ell.placed.flip) == \
        (135.0, False, False)
    assert fs.pc.area(on.outline) == pytest.approx(3400.0)


def test_as_drawn_needs_real_overlap_and_a_main_piece_not_mirrored():
    main = _piece("Main", 100, 50)
    grazing = _piece("Grazing", 20, 10, at=(59.96, 0))    # 0.4 mm2 on it
    lapping = _piece("Lapping", 20, 10, at=(59.8, 0))     # 2 mm2 on it
    doc = _drawing(main, grazing, lapping)
    key = asm.put_on(doc, main)
    assert not asm.drawn_on_main(doc, grazing, key)
    assert asm.drawn_on_main(doc, lapping, key)
    asm.put_on(doc, grazing, key)
    asm.put_on(doc, lapping, key)
    assert _boxes(doc)["Grazing"] == _near((-50.0, -25.0, -30.0, -15.0))
    assert _boxes(doc)["Lapping"] == _near((49.8, -5.0, 69.8, 5.0))
    assert (lapping.placed.ax, lapping.placed.x) == (0.5, 59.8)
    # asked not to: corner to corner on the top piece
    asm.take_off(doc, lapping)
    asm.put_on(doc, lapping, key, as_drawn=False)
    assert _boxes(doc)["Lapping"] == _near((-50.0, -25.0, -30.0, -15.0))
    # a main piece that lies mirrored shows the other way round than
    # drawn: nothing can come in as drawn
    doc = templates.card_holder()
    panel, pocket = doc.shapes
    key = asm.put_on(doc, panel)
    asm.turn_over(doc, panel)
    assert not asm.drawn_on_main(doc, pocket, key)
    asm.put_on(doc, pocket, key)
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y) == (0.0, 0.0, 0.0, 0.0)
    # turned over and drawn from its flesh side, it lies as drawn again
    asm.mirror(doc, panel)
    assert asm.drawn_on_main(doc, pocket)
    assert asm.lay_as_drawn(doc, pocket)
    assert (pocket.placed.ax, pocket.placed.ay) == (0.0, 0.0)
    assert _boxes(doc)["Pocket"] == _near((-52.5, -35.0, 52.5, 10.0))


def test_a_piece_moved_level_with_an_edge_is_tied_to_it_for_good():
    shell, pocket = _piece("Shell", 200, 90), _piece("Pocket", 80, 40,
                                                     at=(300, 0))
    doc = _drawing(shell, pocket)
    asm.put_on(doc, pocket, asm.put_on(doc, shell))
    asm.move_by(doc, pocket, 30.0, 12.5)
    p = pocket.placed                   # not level: held by its corner still
    assert (p.ax, p.x, p.ay, p.y) == (0.0, 30.0, 0.0, 12.5)
    assert (p.cx, p.cy) == (-30.0, -12.5)
    # dropped 3 thousandths short of the right edge: level with it
    asm.move_by(doc, pocket, 89.997, 0.0)
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y) == (1.0, 0.0, 0.0, 12.5)
    assert _boxes(doc)["Pocket"] == _near((20.0, -32.5, 100.0, 7.5))
    # ... and level it stays when either piece changes size on the drawing
    shell.width = 230.0
    assert _boxes(doc)["Pocket"] == _near((35.0, -32.5, 115.0, 7.5))
    pocket.width = 60.0
    assert _boxes(doc)["Pocket"] == _near((55.0, -32.5, 115.0, 7.5))
    # 4 hundredths from the top edge is not level: the offset is kept
    asm.move_by(doc, pocket, 0.0, 37.46)
    p = pocket.placed
    assert (p.ay, p.y) == (0.0, 49.96)
    # 4 thousandths more: tied to the top edge
    asm.move_by(doc, pocket, 0.0, 0.036)
    p = pocket.placed
    assert (p.ay, p.y, p.cy) == (1.0, 0.0, 25.0)
    # the middle of the main piece ties too, after the present anchor
    asm.move_by(doc, pocket, -84.998, -25.002)
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y) == (0.5, 0.0, 0.5, 0.0)
    assert _boxes(doc)["Pocket"] == _near((-30.0, -20.0, 30.0, 20.0))
    # only a piece that is moved is tied again: one left 4 thousandths
    # off (in a file, say) stays there while another is moved
    pocket.placed.x = 0.004
    strap = doc.add_shape(_piece("Strap", 150, 20, at=(0, 300)))
    asm.put_on(doc, strap, shell.placed.of)      # corner to corner on it
    asm.move_by(doc, strap, 5.0, 5.0)
    assert (pocket.placed.ax, pocket.placed.x) == (0.5, 0.004)
    assert (strap.placed.ax, strap.placed.x) == (0.0, 90.004)
    assert (pocket.placed.cx, strap.placed.cx) == (0.004, 50.004)
    # where the middle of each box lies is written afresh at every change:
    # the shell made 30 mm narrower on the drawing, the strap, held by its
    # left edge, lies 15 mm further from the middle
    shell.width = 200.0
    assert (pocket.placed.cx, strap.placed.cx) == (0.004, 50.004)
    asm.higher(doc, pocket)
    assert (pocket.placed.cx, strap.placed.cx) == (0.004, 65.004)


def test_a_typed_offset_is_measured_from_the_present_anchor():
    shell, pocket = _piece("Shell", 200, 90), _piece("Pocket", 80, 40,
                                                     at=(300, 0))
    doc = _drawing(shell, pocket)
    asm.put_on(doc, pocket, asm.put_on(doc, shell))
    assert asm.set_offset(doc, pocket, x=12.0)       # from its left edge
    assert asm.set_offset(doc, pocket, y=3.5)        # from its bottom edge
    assert _boxes(doc)["Pocket"] == _near((-88.0, -41.5, -8.0, -1.5))
    asm.line_up(doc, pocket, "right")
    asm.set_offset(doc, pocket, x=-7.25)             # from its right edge
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y) == (1.0, -7.25, 0.0, 3.5)
    assert _boxes(doc)["Pocket"] == _near((12.75, -41.5, 92.75, -1.5))
    # a hair typed is level
    asm.set_offset(doc, pocket, x=0.004, y=-0.001)
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y) == (1.0, 0.0, 0.0, 0.0)
    # the main piece is what the distances are measured from: it stays
    assert not asm.set_offset(doc, shell, x=5.0)
    assert shell.placed == Placement(of=shell.placed.of, order=1, main=True)


def test_line_up_holds_a_piece_by_the_side_asked_for():
    shell, liner = _piece("Shell", 200, 90), _piece("Liner", 180, 70,
                                                    at=(0, 200))
    pocket = _piece("Pocket", 80, 40, at=(300, 0))
    doc = _drawing(shell, liner, pocket)
    key = asm.put_on(doc, shell)
    asm.put_on(doc, liner, key)
    asm.move_by(doc, liner, 10.0, 5.0)       # (-90, -40) to (90, 30)
    asm.put_on(doc, pocket, key)
    for side, at, anchor in (("left", (-100.0, -20.0), 0.0),
                             ("centre", (-40.0, 40.0), 0.5),
                             ("right", (20.0, 100.0), 1.0)):
        assert asm.line_up(doc, pocket, side)
        b = _boxes(doc)["Pocket"]
        assert (b[0], b[2]) == _near(at)
        assert (pocket.placed.ax, pocket.placed.x) == (anchor, 0.0)
    for side, at, anchor in (("bottom", (-45.0, -5.0), 0.0),
                             ("middle", (-20.0, 20.0), 0.5),
                             ("top", (5.0, 45.0), 1.0)):
        assert asm.line_up(doc, pocket, side)
        b = _boxes(doc)["Pocket"]
        assert (b[1], b[3]) == _near(at)
        assert (pocket.placed.ay, pocket.placed.y) == (anchor, 0.0)
    # with another piece of the stack: held by that side, from the main
    asm.line_up(doc, pocket, "right", against=liner)
    asm.line_up(doc, pocket.shape_id, "top", against=liner.shape_id)
    assert _boxes(doc)["Pocket"] == _near((10.0, -10.0, 90.0, 30.0))
    p = pocket.placed
    assert (p.ax, p.x, p.ay, p.y) == (1.0, -10.0, 1.0, -15.0)
    # the liner is centred on the shell: lined up with its middle, the
    # pocket is tied to the shell's
    asm.line_up(doc, pocket, "centre", against=liner)
    assert (pocket.placed.ax, pocket.placed.x) == (0.5, 0.0)
    # a piece as wide as the main piece is level all three ways: the side
    # asked for is the one it is held by
    band = doc.add_shape(_piece("Band", 200, 30, at=(0, -200)))
    asm.put_on(doc, band, key)
    for side, anchor in (("right", 1.0), ("centre", 0.5), ("left", 0.0)):
        asm.line_up(doc, band, side)
        assert (band.placed.ax, band.placed.x) == (anchor, 0.0)
    # the main piece lined up with another: the bench is measured from
    # it, so it is the others that are seen to move, by 10 mm
    was = _boxes(doc)
    assert asm.line_up(doc, shell, "left", against=liner)
    now = _boxes(doc)
    assert now["Shell"] == _near(was["Shell"])
    assert now["Liner"] == _near((-100.0, -40.0, 80.0, 30.0))
    for name in ("Pocket", "Band"):
        b = was[name]
        assert now[name] == _near((b[0] - 10.0, b[1], b[2] - 10.0, b[3]))
    with pytest.raises(ValueError):
        asm.line_up(doc, pocket, "sideways")
    # a piece of another stack is nothing to line up with
    other = doc.add_shape(_piece("Other", 50, 50, at=(0, 400)))
    asm.put_on(doc, other)
    assert not asm.line_up(doc, pocket, "left", against=other)


def test_turning_turning_over_and_mirroring_keep_the_middle_of_the_box():
    shell, ell = _piece("Shell", 200, 90), _ell()
    doc = _drawing(shell, ell)
    asm.put_on(doc, ell, asm.put_on(doc, shell))
    asm.move_by(doc, ell, 50.0, 12.0)

    def lie():
        return asm.stacks(doc)[0].piece(ell.shape_id)

    def corner():                       # where the L's inside corner lies
        return asm.map_point(lie().from_own, (10, 5))
    assert lie().box == _near((-50.0, -33.0, 30.0, 17.0))
    assert corner() == _near((0.0, -3.0))
    assert asm.turn(doc, ell, 90.0)
    p = ell.placed                      # 50 x 80 now, about the same middle
    assert lie().box == _near((-35.0, -48.0, 15.0, 32.0))
    assert (p.turn, p.cx, p.cy) == (90.0, -10.0, -8.0)
    assert (p.ax, p.x, p.ay, p.y) == (0.0, 65.0, 0.0, -3.0)
    assert corner() == _near((-15.0, 2.0))
    # over like a page: left and right change places, the box stays
    assert asm.turn_over(doc, ell)
    p = ell.placed
    assert (p.over, p.flip, p.turn) == (True, False, 270.0)
    assert (lie().grain_up, lie().mirrored) == (False, True)
    assert lie().box == _near((-35.0, -48.0, 15.0, 32.0))
    assert corner() == _near((-5.0, 2.0))
    assert (p.ax, p.x, p.ay, p.y, p.cx, p.cy) == (0.0, 65.0, 0.0, -3.0,
                                                 -10.0, -8.0)
    # drawn from its flesh side: the other way round, the same side up
    assert asm.mirror(doc, ell)
    p = ell.placed
    assert (p.over, p.flip, p.turn) == (True, True, 90.0)
    assert (lie().grain_up, lie().mirrored) == (False, False)
    assert lie().box == _near((-35.0, -48.0, 15.0, 32.0))
    assert corner() == _near((-15.0, 2.0))
    # laid at a typed turn: about the middle again
    assert asm.set_turn(doc, ell, 180.0)
    assert lie().box == _near((-50.0, -33.0, 30.0, 17.0))
    assert corner() == _near((-20.0, -13.0))
    assert asm.turn(doc, ell, -540.0)
    assert ell.placed.turn == 0.0
    # 30 degrees: the box is the turned outline's
    asm.turn(doc, ell, 30.0)
    b = lie().box
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    xs = [x * c - y * s for x, y in _ELL]
    ys = [x * s + y * c for x, y in _ELL]
    assert (b[2] - b[0], b[3] - b[1]) == _near((max(xs) - min(xs),
                                                max(ys) - min(ys)))
    # (a place is kept to a millionth of a millimetre)
    assert (0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])) == _near((-10.0, -8.0),
                                                               abs=1e-6)


def test_turning_the_main_piece_leaves_the_others_where_they_lie():
    doc, by = _bifold()
    was = _boxes(doc)
    shell = by["Outer shell"]
    assert asm.turn(doc, shell, 90.0)
    now = _boxes(doc)
    assert now["Outer shell"] == _near((-45.0, -99.6, 45.0, 99.6))
    for name in was:
        if name != "Outer shell":
            assert now[name] == _near(was[name])
    # they are tied to its new box: the left pockets 54.6 mm left of it
    p = by["Left pocket 2"].placed
    assert (p.ax, p.x, p.ay, p.y) == (0.0, -54.6, 0.0, 54.6)
    # turned over it keeps its box, and nothing else is touched
    asm.turn(doc, shell, -90.0)
    places = {n: s.placed for n, s in by.items()}
    asm.turn_over(doc, shell)
    assert shell.placed.over and _same(_boxes(doc), was)
    assert all(by[n].placed == places[n] for n in by if n != "Outer shell")


def test_higher_and_lower_move_pieces_through_the_pile():
    doc = _drawing(_piece("Shell", 200, 90), _piece("Liner", 180, 70),
                   _piece("One", 80, 40), _piece("Two", 60, 30))
    shell, liner, one, two = doc.shapes
    key = asm.put_on(doc, shell)
    for sh in (liner, one, two):
        asm.put_on(doc, sh, key, as_drawn=False)
    was = _boxes(doc)

    def pile():
        return [(p.name, p.above) for p in asm.stacks(doc)[0].pieces]
    assert pile() == [("Shell", False), ("Liner", True), ("One", True),
                      ("Two", True)]
    assert asm.higher(doc, liner)
    assert pile() == [("Shell", False), ("One", True), ("Liner", True),
                      ("Two", True)]
    assert [s.placed.order for s in (shell, one, liner, two)] == [1, 2, 3, 4]
    # past the main piece: it lies under it now
    assert asm.lower(doc, one)
    assert pile() == [("One", False), ("Shell", False), ("Liner", True),
                      ("Two", True)]
    st, = asm.stacks(doc, detail=False)
    assert [p.name for p in st.over()] == ["Liner", "Two"]
    assert [p.name for p in st.under()] == ["One"]
    assert not asm.lower(doc, one)               # the bottom of the pile
    assert not asm.higher(doc, two)              # the top of it
    # several go as one block
    assert asm.lower(doc, [liner, two])
    assert pile() == [("One", False), ("Liner", False), ("Two", False),
                      ("Shell", False)]
    assert asm.higher(doc, [one, liner])
    assert pile() == [("Two", False), ("One", False), ("Liner", False),
                      ("Shell", False)]
    st, = asm.stacks(doc, detail=False)         # nearest the main first
    assert [p.name for p in st.under()] == ["Liner", "One", "Two"]
    assert st.over() == []
    # none of it moves a piece on the bench
    assert _same(_boxes(doc), was)


def test_another_piece_made_the_main_piece_moves_nothing():
    doc, by = _bifold()
    was = _boxes(doc)
    liner = by["Bill pocket liner"]
    assert asm.make_main(doc, liner)
    st = asm.stacks(doc)[0]
    assert (st.main.name, st.lost) == ("Bill pocket liner", False)
    assert liner.placed == Placement(of=st.key, order=2, main=True)
    # the bench is measured from the liner's middle now, 1 mm lower
    now = _boxes(doc)
    for name, b in was.items():
        assert now[name] == _near((b[0], b[1] + 1.0, b[2], b[3] + 1.0))
    # every place is said from the liner: the shell centred on it, bottoms
    # level, each pocket still level with its end
    p = by["Outer shell"].placed
    assert (p.main, p.ax, p.x, p.ay, p.y) == (False, 0.5, 0.0, 0.0, 0.0)
    assert (p.cx, p.cy) == (0.0, 1.0)
    p = by["Right pocket 3"].placed
    assert (p.ax, p.x, p.ay, p.y) == (1.0, 0.0, 0.0, 0.0)
    # the stack folds along the liner's fold lines, and it has none: the
    # shell's spine fold is not made
    assert st.main.folds == []
    assert [f.name for f in st.piece(by["Outer shell"].shape_id).folds] == \
        ["Spine fold"]
    assert not asm.make_main(doc, _piece("Loose", 10, 10))


def test_when_the_main_piece_is_deleted_nothing_jumps():
    doc, by = _bifold()
    was = _boxes(doc)
    stored = {n: s.placed for n, s in by.items()}
    doc.shapes.remove(by["Outer shell"])
    st, = asm.stacks(doc)
    # the lowest piece left is the main piece, and the stack says so
    assert (st.lost, st.main.name, len(st.pieces)) == \
        (True, "Bill pocket liner", 9)
    now = _boxes(doc)
    for name in now:                    # the liner's middle was 1 mm low
        b = was[name]
        assert now[name] == _near((b[0], b[1] + 1.0, b[2], b[3] + 1.0))
    # reading writes nothing: what is stored still speaks of the shell
    assert all(by[n].placed == stored[n] for n in now)
    # ... and the reading says what the next change will write
    p = st.piece(by["Left pocket 3"].shape_id).placed
    assert (p.order, p.ax, p.x, p.ay, p.y) == (3, 0.0, 0.0, 0.0, 0.0)
    assert st.main.placed == Placement(of=st.key, order=1, main=True)
    # the next change settles every place against the new main piece.
    # A distance typed is measured as the reading showed it: the backing
    # 3 mm up from the liner's bottom edge
    assert asm.set_offset(doc, by["Left pocket backing"], y=3.0)
    st, = asm.stacks(doc)
    assert (st.lost, st.main.name) == (False, "Bill pocket liner")
    assert by["Bill pocket liner"].placed.main
    assert by["Left pocket 3"].placed == p
    now2 = _boxes(doc)
    for name in now:
        lift = 3.0 if name == "Left pocket backing" else 0.0
        b = now[name]
        assert now2[name] == _near((b[0], b[1] + lift, b[2], b[3] + lift))
    assert now2["Left pocket backing"] == _near((-99.6, -41.0, -2.0, 38.0))


def test_taking_the_main_piece_off_hands_the_stack_to_the_lowest_piece():
    doc, by = _bifold()
    was = _boxes(doc)
    assert asm.take_off(doc, by["Outer shell"])
    shell = by["Outer shell"]
    assert shell.placed is None and shell.thickness == 1.6
    st, = asm.stacks(doc)
    assert (st.lost, st.main.name, len(st.pieces)) == \
        (False, "Bill pocket liner", 9)
    now = _boxes(doc)
    for name in now:
        b = was[name]
        assert now[name] == _near((b[0], b[1] + 1.0, b[2], b[3] + 1.0))
    # a piece that is not the main piece goes without a trace
    assert asm.take_off(doc, [by["Left pocket 2"], by["Right pocket 2"]])
    assert _same(_boxes(doc), {n: b for n, b in now.items()
                               if not n.endswith("pocket 2")})
    assert sorted(s.placed.order for s in doc.shapes if s.placed) == \
        list(range(1, 8))
    assert not asm.take_off(doc, shell)          # it is on no stack


def test_each_oddity_in_a_stack_has_one_answer():
    doc = _drawing(_piece("A", 100, 60), _piece("B", 80, 40),
                   _piece("C", 60, 30), _piece("D", 40, 20))
    a, b, c, d = doc.shapes
    key = asm.put_on(doc, a)
    for sh in (b, c, d):
        asm.put_on(doc, sh, key, as_drawn=False)

    def read():
        st, = asm.stacks(doc)
        return st.main.name, [p.name for p in st.pieces]
    # two marked as the main piece: the lower in the pile is it
    c.placed.main = True
    assert read() == ("A", ["A", "B", "C", "D"])
    a.placed.main = False
    assert read() == ("C", ["A", "B", "C", "D"])
    # ... and of two as low as each other, the earlier in the drawing
    b.placed.main, b.placed.order = True, 3
    assert read() == ("B", ["A", "B", "C", "D"])
    # equal places in the pile: the earlier in the drawing is lower
    for sh in doc.shapes:
        sh.placed.order = 5
    assert read() == ("B", ["A", "B", "C", "D"])
    doc.shapes.reverse()
    assert read() == ("C", ["D", "C", "B", "A"])
    # the next change writes the one answer down
    asm.higher(doc, d)
    assert [(s.name, s.placed.order, s.placed.main) for s in doc.shapes] \
        == [("D", 2, False), ("C", 1, True), ("B", 3, False),
            ("A", 4, False)]
    # a stack with one piece left is still a stack, until that goes too
    asm.take_off(doc, [a, b, d])
    assert read() == ("C", ["C"])
    asm.take_off(doc, c)
    assert asm.stacks(doc) == []


def test_a_placed_shape_that_is_no_outline_is_in_no_stack_until_it_is():
    shell, ell = _piece("Shell", 200, 90), _ell()
    doc = _drawing(shell, ell)
    asm.put_on(doc, ell, asm.put_on(doc, shell))
    was = ell.placed
    assert len(asm.stacks(doc)[0].pieces) == 2
    ell.close_path = False                  # cut open: no piece of leather
    assert [p.name for p in asm.stacks(doc)[0].pieces] == ["Shell"]
    assert not asm.is_outline(doc, ell) and ell.placed == was
    assert not asm.move_by(doc, ell, 5.0, 5.0)
    ell.close_path = True                   # closed again: where it lay
    assert asm.stacks(doc)[0].piece(ell.shape_id).box == \
        _near((-100.0, -45.0, -20.0, 5.0))
    # the same for one moved off the cut layers, or made a guide
    ell.layer = "Score"
    assert len(asm.stacks(doc)[0].pieces) == 1
    ell.layer, ell.construction = "Cut", True
    assert len(asm.stacks(doc)[0].pieces) == 1
    # it can still be taken off
    assert asm.take_off(doc, ell) and ell.placed is None


def test_moving_the_main_piece_moves_the_others_the_other_way():
    doc, by = _bifold()
    was = _boxes(doc)
    assert asm.move_by(doc, by["Outer shell"], 10.0, -2.0)
    now = _boxes(doc)
    assert now["Outer shell"] == _near(was["Outer shell"])
    for name in was:
        if name != "Outer shell":
            b = was[name]
            assert now[name] == _near((b[0] - 10.0, b[1] + 2.0, b[2] - 10.0,
                                       b[3] + 2.0))
    p = by["Right pocket 2"].placed
    assert (p.ax, p.x, p.ay, p.y) == (1.0, -10.0, 0.0, 2.0)
    # moved with everything that lies on it, nothing has moved at all
    asm.move_by(doc, list(by.values()), 33.0, 7.0)
    assert _same(_boxes(doc), now)
    # a piece in no stack has nowhere to move
    assert not asm.move_by(doc, _piece("Loose", 10, 10), 1.0, 1.0)


def test_a_drag_ends_where_it_ends_whatever_way_it_took():
    shell, pocket = _piece("Shell", 200, 90), _piece("Pocket", 80, 40,
                                                     at=(300, 0))
    doc = _drawing(shell, pocket)
    key = asm.put_on(doc, shell)
    asm.put_on(doc, pocket, key)
    asm.move_by(doc, pocket, 10.0, 10.0)
    start = asm.remember(doc, key)
    assert start[pocket.shape_id] == pocket.placed
    assert start[pocket.shape_id] is not pocket.placed
    # moved in steps, the pocket passes level with the middle on its way
    # and is held by its middle from then on
    asm.move_by(doc, pocket, 50.0, 0.0)
    assert (pocket.placed.ax, pocket.placed.x) == (0.5, 0.0)
    asm.move_by(doc, pocket, 25.0, 0.0)
    assert (pocket.placed.ax, pocket.placed.x) == (0.5, 25.0)
    # worked out from where the drag began, it is held as it was
    asm.put_back(doc, start)
    assert pocket.placed == start[pocket.shape_id]
    asm.move_by(doc, pocket, 75.0, 0.0)
    assert (pocket.placed.ax, pocket.placed.x) == (0.0, 85.0)
    assert _boxes(doc)["Pocket"] == _near((-15.0, -35.0, 65.0, 5.0))
    # given up: every piece where it was
    asm.put_back(doc, start)
    assert asm.remember(doc, key) == start
    assert asm.remember(doc, "stack0") == {}


def test_what_lies_wholly_on_a_piece_goes_with_it():
    doc, by = _bifold()

    def on(name):
        return [s.name for s in asm.lying_on(doc, by[name])]
    left = [f"Left {n}" for n in _POCKETS]
    right = [f"Right {n}" for n in _POCKETS]
    # square pocket corners over the liner's round ones still lie on it
    assert on("Bill pocket liner") == left + right
    assert on("Left pocket backing") == left[1:]
    assert on("Left pocket 3") == left[2:]
    assert on("Right pocket 1 (front)") == []
    assert on("Outer shell") == ["Bill pocket liner"] + left + right
    # a pocket that hangs over the edge of the one under it stays behind
    asm.move_by(doc, by["Left pocket 2"], 1.0, 0.0)
    assert on("Left pocket backing") == ["Left pocket 3",
                                         "Left pocket 1 (front)"]
    # under the main piece, later in the pile is further down
    patch = doc.add_shape(_piece("Patch", 30, 20, at=(0, 300)))
    key = by["Outer shell"].placed.of
    asm.put_on(doc, patch, key, on=by["Left pocket backing"])
    for _ in range(10):
        asm.lower(doc, [by["Left pocket backing"], patch])
    asm.lower(doc, patch)
    names = [p.name for p in asm.stacks(doc, detail=False)[0].pieces]
    assert names[:3] == ["Patch", "Left pocket backing", "Outer shell"]
    assert on("Left pocket backing") == ["Patch"]
    assert asm.lying_on(doc, patch) == []
    assert "Patch" in on("Outer shell")          # over it or under it
    # a piece in the hole of a ring lies on no leather of it
    ring = Polygon(name="Ring", close_path=True, layer="Cut", points=[
        Vec2(*p) for p in ((-30, -30), (30, -30), (30, 30), (-30, 30),
                           (-30, -20), (-20, -20), (-20, 20), (20, 20),
                           (20, -20), (-30, -20))])
    doc = _drawing(ring, _piece("Stud", 10, 10))
    stud = doc.shapes[1]
    asm.put_on(doc, stud, asm.put_on(doc, ring))
    asm.line_up(doc, stud, "centre")
    asm.line_up(doc, stud, "middle")
    assert asm.lying_on(doc, ring) == []
    asm.line_up(doc, stud, "bottom")
    assert asm.lying_on(doc, ring) == [stud]


def test_a_drawing_holds_several_stacks_and_a_piece_goes_between_them():
    doc = _drawing(_piece("A", 100, 60), _piece("B", 80, 40, at=(200, 0)),
                   _piece("C", 60, 30, at=(400, 0)),
                   _piece("D", 40, 20, at=(600, 0)))
    a, b, c, d = doc.shapes
    one = asm.put_on(doc, a)
    asm.put_on(doc, b, one)
    two = asm.put_on(doc, c)
    asm.put_on(doc, d, two)
    assert one != two
    assert [(st.key, [p.name for p in st.pieces]) for st in asm.stacks(doc)] \
        == [(one, ["A", "B"]), (two, ["C", "D"])]
    assert asm.stack_of(doc, d).key == two and asm.stack_of(doc, d.shape_id)
    assert asm.stack_of(doc, _piece("Loose", 10, 10)) is None
    # the main piece of one stack put on the other: the first is handed on
    assert asm.put_on(doc, a, two) == two
    assert [(st.key, st.main.name, [p.name for p in st.pieces])
            for st in asm.stacks(doc)] == \
        [(one, "B", ["B"]), (two, "C", ["C", "D", "A"])]
    assert asm.shape_by_id(doc, c.shape_id) is c
    assert asm.shape_by_id(doc, "shape0") is None


# -- what each edit on the drawing does (the real canvas) ---------------------

def test_moving_turning_and_nesting_on_the_drawing_change_nothing(window):
    doc, by = _bifold()
    win = window(doc)
    c = win.canvas
    was = asm.stacks(win.doc)[0].signature()
    _select(win, by["Left pocket 2"])
    c.nudge_selected(300.0, -41.0)
    item, = _select(win, by["Right pocket 1 (front)"])
    item.setPos(item.pos().x() + 55.5, item.pos().y() - 20.25)   # a drag
    _select(win, by["Right pocket backing"])
    c.rotate_selected(90.0)
    _select(win, by["Left pocket 3"], by["Right pocket 3"],
            by["Bill pocket liner"])
    c.align_selected("top")
    _select(win, by["Outer shell"], by["Spine fold"])    # the fold with it
    c.nudge_selected(-17.0, 230.0)
    c.rotate_selected(90.0)
    assert by["Right pocket backing"].transform.rotation == 90.0
    assert by["Left pocket 2"].transform.x == _near(586.0)
    assert asm.stacks(win.doc)[0].signature() == was     # cells identical
    # Nest on sheet moves every piece, turns some and leaves its sheet
    win.resize(1200, 800)
    win.show()
    c.scene_obj.clearSelection()
    QApplication.processEvents()
    assert "Placed 10 of 10" in c.nest_preview(260, 700, margin=5,
                                               spacing=8)
    assert c.nest_apply()
    turned = {round(s.transform.rotation % 180.0) for s in win.doc.shapes
              if s.placed is not None}
    assert turned == {0, 90}
    assert asm.stacks(win.doc)[0].signature() == was
    # the rectangle it leaves is a guide: never a piece to put on
    assert [s.name for s in win.doc.shapes if s.construction] == ["Sheet"]
    assert "Sheet" not in [s.name for s in asm.offered(win.doc)]
    assert asm.drawn_inside(win.doc) == []
    assert len(asm.offered(win.doc)) == 10


def test_pockets_narrowed_in_properties_keep_their_outer_edges(window):
    doc, by = _bifold()
    win = window(doc)
    for name in ("Left", "Right"):
        for pocket in _POCKETS:
            items = _select(win, by[f"{name} {pocket}"])
            win.properties.show_selection(items)
            win.properties.w.setValue(97.6 - 5.3)
            win.properties.w.editingFinished.emit()
    boxes = _boxes(win.doc)
    for pocket in _POCKETS:
        # 99.60 from the middle still, and 5.3 mm further from the spine
        left, right = boxes[f"Left {pocket}"], boxes[f"Right {pocket}"]
        assert (left[0], left[2]) == _near((-99.6, -7.3))
        assert (right[0], right[2]) == _near((7.3, 99.6))
        assert (left[1], right[1]) == _near((-45.0, -45.0))
    # a height typed: the bottom, which is level, stays
    items = _select(win, by["Left pocket 2"])
    win.properties.show_selection(items)
    win.properties.h.setValue(50.0)
    win.properties.h.editingFinished.emit()
    assert _boxes(win.doc)["Left pocket 2"] == _near((-99.6, -45.0, -7.3,
                                                      5.0))


def test_a_grip_a_scale_and_a_node_edit_keep_the_anchored_point(window):
    from leathercad_app.items import ResizeHandle
    doc, by = _bifold()
    ell = _add_ell(doc, "right")
    win = window(doc)
    c = win.canvas
    assert _boxes(win.doc)["L pocket"] == _near((19.6, -45.0, 99.6, 5.0))
    # dragged wider by the grip on its left: the right edge is level still
    item, = _select(win, by["Right pocket 2"])
    c.selection_changed()
    grip = next(g for g in c._resize_handles
                if isinstance(g, ResizeHandle) and g.grip == (-1, 0))
    grip._apply_resize(Vec2(grip.pos().x() + 10.0, grip.pos().y()))
    assert by["Right pocket 2"].width == pytest.approx(87.6)
    assert _boxes(win.doc)["Right pocket 2"] == _near((12.0, -45.0, 99.6,
                                                       -1.0))
    # Scale by 1.1: 88 x 55 from its bottom right corner
    _select(win, ell)
    c.scale_selected(1.1)
    assert _boxes(win.doc)["L pocket"] == _near((11.6, -45.0, 99.6, 10.0))
    # a node dragged 12 mm out to the right of a piece held by its left
    asm.line_up(win.doc, ell, "left")
    assert _boxes(win.doc)["L pocket"] == _near((-99.6, -45.0, -11.6, 10.0))
    item, = _select(win, ell)
    c.enter_vertex_edit(item)
    handle = c._handles[1]                  # the bottom right corner
    handle.setPos(handle.pos().x() + 12.0, handle.pos().y())
    c.clear_vertex_handles()
    assert _boxes(win.doc)["L pocket"] == _near((-99.6, -45.0, 0.4, 10.0))
    assert ell.placed.ax == 0.0 and ell.placed.x == 0.0


def test_fold_allowance_on_the_shell_keeps_both_stacks_level(window):
    doc, by = _bifold()
    win = window(doc)
    _select(win, by["Outer shell"])
    win._gen_dialog = lambda *a, **k: {"fa_t": 2.0, "fa_r": 0.0}
    win._fold_allowance()
    shell, = _named(win.doc, "Outer shell")
    # the shell is nodes now, 3.14 mm longer across its spine fold, and
    # is the main piece still
    assert isinstance(shell, EditablePath) and shell is not by["Outer shell"]
    assert shell.shape_id == by["Outer shell"].shape_id
    assert shell.placed == by["Outer shell"].placed and shell.placed.main
    assert shell.thickness == 1.6
    half = (199.2 + math.pi) / 2.0                      # 101.17
    boxes = _boxes(win.doc)
    assert boxes["Outer shell"] == _near((-half, -45.0, half, 45.0))
    for pocket in _POCKETS:
        left, right = boxes[f"Left {pocket}"], boxes[f"Right {pocket}"]
        assert (left[0], left[2]) == _near((-half, 97.6 - half))
        assert (right[0], right[2]) == _near((half - 97.6, half))
    # the liner was centred, and is
    assert boxes["Bill pocket liner"] == _near((-99.6, -45.0, 99.6, 43.0))
    # the fold line, moved half way, is the main piece's still
    fold, = asm.stacks(win.doc)[0].main.folds
    assert fold.a + fold.b == _near((0.0, -45.0, 0.0, 45.0), abs=1e-6)


def test_a_piece_flipped_on_the_drawing_shows_mirrored_in_the_stack(window):
    doc, by = _bifold()
    ell = _add_ell(doc, "left")
    win = window(doc)

    def lie():
        return asm.stacks(win.doc)[0].piece(ell.shape_id)
    was = lie()
    assert was.box == _near((-99.6, -45.0, -19.6, 5.0))
    assert (-49.6, -15.0) in [(round(x, 6), round(y, 6))
                              for x, y in was.outline]   # the notch
    _select(win, ell)
    win.canvas.mirror_selected()                 # Arrange > Flip horizontal
    assert ell.transform.mirror_x
    now = lie()
    # in its box as before, level with the left edge, the notch now left
    assert now.box == _near(was.box) and ell.placed == was.placed
    assert _flat(sorted(now.outline)) == _near(_flat(sorted(
        (-119.2 - x, y) for x, y in was.outline)))
    assert (-69.6, -15.0) in [(round(x, 6), round(y, 6))
                              for x, y in now.outline]
    assert now.mirrored is False                 # as it lies: not turned
    # flipped upright too, it is the piece turned half round on the
    # drawing, which changes nothing
    _select(win, ell)
    win.canvas.mirror_selected(axis_deg=0.0)     # Flip vertical
    assert not ell.transform.mirror_x
    assert abs(ell.transform.rotation) == 180.0
    assert _flat(lie().outline) == _near(_flat(was.outline))
    # Properties > Mirror (laser from back) is the same mirror
    win.properties.show_selection(_select(win, ell))
    win.properties.mirror.setChecked(True)
    assert ell.transform.mirror_x
    assert _flat(sorted(lie().outline)) == _near(_flat(sorted(now.outline)))
    assert lie().box == _near(was.box)


def test_convert_to_nodes_keeps_the_place_and_the_turn(window):
    doc, by = _bifold()
    win = window(doc)
    pocket = by["Left pocket 3"]
    _select(win, pocket)
    win.canvas.rotate_selected(30.0)             # as it happens to lie drawn
    was = asm.stacks(win.doc)[0].piece(pocket.shape_id)
    _select(win, pocket)
    win.canvas.convert_to_nodes()
    new, = _named(win.doc, "Left pocket 3")
    assert isinstance(new, Polygon) and new is not pocket
    assert new.shape_id == pocket.shape_id
    assert (new.thickness, new.placed) == (1.0, pocket.placed)
    assert new.placed is not pocket.placed
    now = asm.stacks(win.doc)[0].piece(new.shape_id)
    assert now.box == was.box == _near((-99.6, -45.0, -2.0, 9.0))
    assert now.outline == was.outline


def test_a_trimmed_outline_joined_again_lies_where_it_lay(window):
    doc, by = _bifold()
    win = window(doc)
    c = win.canvas
    pocket = by["Left pocket 3"]
    _select(win, pocket)
    c.rotate_selected(90.0)             # drawn on its side
    b = pocket.bounds()
    # a line across the corner that is top right on the drawing (bottom
    # right as the pocket is cut), 15 mm from it either way
    cutter = c.add_shape(PathShape(
        points=[Vec2(b[2] - 20, b[3] + 5), Vec2(b[2] + 5, b[3] - 20)],
        close_path=False, layer="Cut", name="Cutter")).model
    c._do_trim(Vec2(b[2], b[3] - 5))             # the corner goes
    run, = _named(win.doc, "Left pocket 3")
    # an open run now: no piece of leather, so in no stack, but it knows
    # where it lay
    assert run is not pocket and not run.local_path().closed
    assert run.thickness == 1.0 and run.placed.turn == 270.0
    assert "Left pocket 3" not in _boxes(win.doc)
    assert len(asm.stacks(win.doc)[0].pieces) == 9
    # the cutter trimmed to the corner, and the two welded
    cb = cutter.bounds()
    c._do_trim(Vec2(cb[0] + 1.0, cb[3] - 1.0))
    c._do_trim(Vec2(cb[2] - 1.0, cb[1] + 1.0))
    chord, = _named(win.doc, "Cutter")
    assert chord.placed is None and chord.thickness == 0.0
    _select(win, run, chord)
    c.join_selected()
    whole, = [s for s in win.doc.shapes if s.name in ("Left pocket 3",
                                                     "Cutter")]
    # whichever of the two gave the welded outline its name, it is the
    # pocket's leather where the pocket lay, its corner cut off
    assert whole.local_path().closed
    assert (whole.thickness, whole.placed) == (1.0, run.placed)
    lie = asm.stacks(win.doc)[0].piece(whole.shape_id)
    assert lie.box == _near((-99.6, -45.0, -2.0, 9.0))
    assert sorted((round(x, 6), round(y, 6)) for x, y in lie.outline) == \
        [(-99.6, -45.0), (-99.6, 9.0), (-17.0, -45.0), (-2.0, -30.0),
         (-2.0, 9.0)]
    assert len(asm.stacks(win.doc)[0].pieces) == 10


def test_break_apart_leaves_the_place_on_one_edge_for_join(window):
    doc, by = _bifold()
    win = window(doc)
    c = win.canvas
    pocket = by["Right pocket 2"]
    _select(win, pocket)
    c.rotate_selected(90.0)                      # drawn on its side
    was = _boxes(win.doc)["Right pocket 2"]
    _select(win, pocket)
    c.break_apart_selected()
    edges = _named(win.doc, "Right pocket 2")
    # four edges, and the place on one of them: the longest
    assert sorted(round(asm._bulk(e)[1], 1) for e in edges) == \
        [44.0, 44.0, 97.6, 97.6]
    kept, = [e for e in edges if e.placed is not None]
    assert asm._bulk(kept)[1] == pytest.approx(97.6)
    assert all(e.thickness == 1.0 for e in edges)
    assert "Right pocket 2" not in _boxes(win.doc)
    # only the turn was re-based: the edges are not turned on the drawing
    assert (kept.placed.turn, kept.placed.ax, kept.placed.x) == \
        (270.0, 1.0, 0.0)
    _select(win, *edges)
    c.join_selected()
    whole, = _named(win.doc, "Right pocket 2")
    assert whole.local_path().closed and whole.placed == kept.placed
    assert whole.thickness == 1.0
    assert _boxes(win.doc)["Right pocket 2"] == _near(was)
    assert was == _near((2.0, -45.0, 99.6, -1.0))
    # three pieces broken apart and everything joined at once: each
    # welded outline is the leather its own edges were, in its own place
    # (Join names them all after the first, so they are told by the pile)

    def pile():
        return {p.order: p.box for p in asm.stacks(win.doc)[0].pieces}
    before = pile()
    by["Left pocket 3"].thickness = 0.8
    _select(win, whole, by["Left pocket 3"], by["Bill pocket liner"])
    c.break_apart_selected()
    assert len(_boxes(win.doc)) == 7
    _select(win, *[s for s in win.doc.shapes
                   if not s.local_path().closed and not s.is_fold_line])
    c.join_selected()
    assert _same(pile(), before)
    assert before[2] == _near((-99.6, -45.0, 99.6, 43.0))    # the liner
    assert sorted((s.placed.order, s.thickness)
                  for s in win.doc.shapes[-3:]) == [(2, 1.0), (4, 0.8),
                                                    (9, 1.0)]


def test_a_thumb_notch_subtracted_from_a_pocket_leaves_it_where_it_was(
        window):
    doc, by = _bifold()
    win = window(doc)
    c = win.canvas
    pocket = by["Left pocket 1 (front)"]
    _select(win, pocket)
    c.rotate_selected(90.0)             # drawn on its side, as a nest may
    b = pocket.bounds()                 # leave it: its top edge is on the left
    notch = c.add_shape(Circle(rx=8, ry=8, layer="Cut", name="Notch",
                               transform=Transform(x=b[0],
                                                   y=(b[1] + b[3]) / 2))
                        ).model
    _select(win, pocket, notch)
    c.boolean_selected("difference")
    new, = _named(win.doc, "Left pocket 1 (front)")
    assert _named(win.doc, "Notch") == []
    assert isinstance(new, Polygon) and new.shape_id != pocket.shape_id
    # the new outline is not turned on the drawing: its place says so
    assert (pocket.transform.rotation, new.transform.rotation) == (90.0, 0.0)
    assert (new.thickness, new.placed.turn) == (1.0, 270.0)
    assert (new.placed.ax, new.placed.x, new.placed.ay, new.placed.y) == \
        (0.0, 0.0, 0.0, 0.0)
    lie = asm.stacks(win.doc)[0].piece(new.shape_id)
    assert lie.box == _near((-99.6, -45.0, -2.0, -11.0))
    # the notch is in its top edge: 8 mm deep at the middle of the pocket
    assert min(y for x, y in lie.outline if abs(x + 50.8) < 0.5) == \
        _near(-19.0, abs=0.01)
    assert len(asm.stacks(win.doc)[0].pieces) == 10
    # a tab merged onto a pocket: it grows from the corner it is held by
    backing = by["Right pocket backing"]
    b = backing.bounds()
    tab = c.add_shape(_piece("Tab", 20, 10, at=((b[0] + b[2]) / 2,
                                                b[3] + 3))).model
    _select(win, backing, tab)
    c.boolean_selected("union")
    merged, = _named(win.doc, "Right pocket backing")
    assert merged.placed == backing.placed
    assert _boxes(win.doc)["Right pocket backing"] == \
        _near((2.0, -45.0, 99.6, 42.0))


def test_of_two_parts_a_command_leaves_the_bigger_keeps_the_place(window):
    doc, by = _bifold()
    win = window(doc)
    c = win.canvas
    pocket = by["Right pocket 3"]
    b = pocket.bounds()
    # a slot right through it, 28 mm from its inner edge
    slot = c.add_shape(_piece("Slot", 4, 80, at=(b[0] + 30,
                                                 (b[1] + b[3]) / 2))).model
    _select(win, pocket, slot)
    c.boolean_selected("difference")
    big, small = sorted(_named(win.doc, "Right pocket 3"),
                        key=lambda s: s.bounds()[0] - s.bounds()[2])
    assert big.bounds()[2] - big.bounds()[0] == pytest.approx(65.6)
    assert small.bounds()[2] - small.bounds()[0] == pytest.approx(28.0)
    assert big.placed == pocket.placed and small.placed is None
    assert (big.thickness, small.thickness) == (1.0, 1.0)
    # held by its right edge, the bigger part is where that leather lay
    assert _boxes(win.doc)["Right pocket 3"] == _near((34.0, -45.0, 99.6,
                                                       9.0))
    assert len(asm.stacks(win.doc)[0].pieces) == 10


def test_the_first_fillet_of_a_polygon_keeps_its_place(window):
    doc, by = _bifold()
    ell = _add_ell(doc, "left")
    win = window(doc)
    c = win.canvas
    c.fillet_radius = 5.0
    corner = ell.transform.apply(Vec2(10, 5))    # its inside corner
    c._do_fillet(Vec2(corner.x, corner.y))
    new, = _named(win.doc, "L pocket")
    assert isinstance(new, EditablePath) and new is not ell
    assert new.shape_id != ell.shape_id          # a new shape altogether
    assert len(new.nodes) == 7
    assert (new.thickness, new.placed) == (1.2, ell.placed)
    assert _boxes(win.doc)["L pocket"] == _near((-99.6, -45.0, -19.6, 5.0))


def test_a_copy_keeps_the_leather_and_is_in_no_stack(window, tmp_path):
    from leathercad_app.partspanel import PartsPanel
    doc, by = _bifold()
    by["Left pocket 2"].leather = "Black"
    win = window(doc)
    c = win.canvas
    was = asm.stacks(win.doc)[0].signature()

    def copies(name):
        return [(s.thickness, s.leather, s.placed is not None)
                for s in win.doc.shapes if s.name.startswith(name)]
    _select(win, by["Left pocket 2"])
    c.duplicate_selected()                               # Ctrl+D
    assert copies("Left pocket 2") == [(1.0, "Black", True),
                                       (1.0, "Black", False)]
    _select(win, by["Left pocket 3"])
    c.array_grid(2, 2, 120.0, 60.0)                      # Edit > Array
    assert copies("Left pocket 3") == [(1.0, "", True)] + \
        3 * [(1.0, "", False)]
    rail = c.add_shape(PathShape(points=[Vec2(0, 200), Vec2(300, 200)],
                                 close_path=False, layer="Cut")).model
    _select(win, by["Right pocket 3"], rail)
    assert c.array_along_path(3)                         # Copy along path
    assert copies("Right pocket 3") == [(1.0, "", True)] + \
        3 * [(1.0, "", False)]
    _select(win, by["Outer shell"])
    c.make_back_piece_selected()                         # Ctrl+M
    assert copies("Outer shell") == [(1.6, "", True), (1.6, "", False)]
    assert _named(win.doc, "Outer shell (back)")[0].transform.mirror_x
    # a part saved from a piece of the stack, and placed again
    panel = PartsPanel(c, base_dir=str(tmp_path / "parts"))
    _select(win, by["Right pocket backing"])
    panel.save_selection(name="Backing")
    saved, = (tmp_path / "parts").glob("*.json")
    kept = json.loads(saved.read_text(encoding="utf-8"))["shapes"][0]
    assert kept["thickness"] == 1.0 and "placed" not in kept
    leaf = panel.tree.findItems("Backing", Qt.MatchRecursive)[0]
    panel.tree.setCurrentItem(leaf)
    panel.place_selected()                               # Place part
    assert copies("Right pocket backing") == [(1.0, "", True),
                                              (1.0, "", False)]
    # with all that, the stack is what it was
    assert asm.stacks(win.doc)[0].signature() == was
    assert len(asm.stacks(win.doc)) == 1


def test_a_placed_piece_deleted_leaves_the_stack_and_nothing_moves(window):
    doc, by = _bifold()
    win = window(doc)
    was = _boxes(win.doc)
    _select(win, by["Bill pocket liner"], by["Left pocket backing"])
    win.canvas.delete_selected()
    now = _boxes(win.doc)
    assert len(now) == 8 and "Bill pocket liner" not in now
    assert all(now[n] == was[n] for n in now)            # to the digit
    st, = asm.stacks(win.doc)
    assert (st.lost, st.main.name) == (False, "Outer shell")
    # the pile keeps its order, with the gaps the two left
    assert [p.placed.order for p in st.pieces] == list(range(1, 9))
    assert [s.placed.order for s in win.doc.shapes if s.placed] == \
        [1, 6, 5, 4, 10, 9, 8, 7]


def test_the_main_piece_deleted_on_the_drawing_and_brought_back(window):
    doc, by = _bifold()
    win = window(doc)
    win.commit()                                 # a state to come back to
    was = _boxes(win.doc)
    _select(win, by["Outer shell"])
    win.canvas.delete_selected()
    st, = asm.stacks(win.doc)
    assert (st.lost, st.main.name, len(st.pieces)) == \
        (True, "Bill pocket liner", 9)
    now = _boxes(win.doc)
    for name, b in now.items():          # nothing jumps: all 1 mm, as one
        assert b == _near((was[name][0], was[name][1] + 1.0, was[name][2],
                           was[name][3] + 1.0))
    # Undo brings the shell back, and the stack with it
    win.undo()
    st, = asm.stacks(win.doc)
    assert (st.lost, st.main.name, len(st.pieces)) == \
        (False, "Outer shell", 10)
    assert _same(_boxes(win.doc), was)


def test_changes_to_a_stack_are_undone_with_the_drawing(window):
    doc, by = _bifold()
    win = window(doc)
    c = win.canvas

    def named(name):
        return _named(win.doc, name)[0]
    first = _boxes(win.doc)
    assert not win.history.can_undo()
    # as a window on the stack does it: the shapes of the drawing are
    # changed, and the canvas is asked for an undo step
    asm.move_by(win.doc, named("Left pocket 2"), 12.0, 3.0)
    c.commitRequested.emit()
    second = _boxes(win.doc)
    asm.turn_over(win.doc, named("Outer shell"))
    asm.higher(win.doc, named("Bill pocket liner"))
    asm.take_off(win.doc, named("Right pocket 1 (front)"))
    c.commitRequested.emit()                     # three changes, one step
    third = _boxes(win.doc)
    assert len(third) == 9 and win.history.can_undo()
    win.undo()
    assert _same(_boxes(win.doc), second)
    assert second["Left pocket 2"] == _near((-87.6, -42.0, 10.0, 2.0))
    assert not named("Outer shell").placed.over
    assert named("Bill pocket liner").placed.order == 2
    win.undo()
    assert _same(_boxes(win.doc), first) and not win.history.can_undo()
    # what is changed after that, with no step asked for, is in no step:
    # the states kept share nothing with the drawing on screen
    asm.take_off(win.doc, list(win.doc.shapes))
    named("Outer shell").thickness = 9.0
    assert asm.stacks(win.doc) == []
    win.redo()
    assert _same(_boxes(win.doc), second)
    assert named("Outer shell").thickness == 1.6
    win.redo()
    assert _same(_boxes(win.doc), third)
    assert named("Outer shell").placed.over
    assert named("Right pocket 1 (front)").placed is None
    assert [p.name for p in asm.stacks(win.doc)[0].pieces][:4] == \
        ["Outer shell", "Left pocket backing", "Bill pocket liner",
         "Left pocket 3"]


def test_a_path_broken_at_a_node_keeps_one_place():
    from leathercad.modify import break_path_at, to_editable
    ell = _ell()
    ell.thickness = 1.2
    ell.placed = Placement(of="stack7", order=2, ax=0.0, ay=0.0)
    ep = to_editable(ell)
    assert ep is not ell and ep.placed == ell.placed
    assert ep.placed is not ell.placed
    run, = break_path_at(ep, 2)                  # opened: one run
    assert not run.closed and run.placed == ell.placed
    long, short = break_path_at(run, 4)          # split: 150 and 110 mm
    assert asm._bulk(long)[1] == pytest.approx(150.0)
    assert asm._bulk(short)[1] == pytest.approx(110.0)
    assert long.placed == ell.placed and short.placed is None
    assert (long.thickness, short.thickness) == (1.2, 1.2)
    # the same rule, said once: the biggest of what was made
    a, b, c = (_piece("a", 10, 10), _piece("b", 30, 30), _line((0, 0),
                                                               (500, 0)))
    asm.keep_placement([ell, ep], [a, b, c])     # closed before open
    assert (a.placed, b.placed, c.placed) == (None, ell.placed, None)
    asm.keep_placement([_piece("plain", 5, 5)], [a, b, c])
    assert (a.placed, b.placed, c.placed) == (None, None, None)


# -- Properties ---------------------------------------------------------------

@pytest.fixture
def mm(qapp):
    from leathercad_app import units
    was = units.current()
    units.set_current(units.MM)
    yield units
    units.set_current(was)


def test_leather_thickness_is_set_in_properties(window, mm):
    doc = _drawing(_piece("Shell", 200, 90), _piece("Pocket", 80, 40,
                                                    at=(300, 0)),
                   _line((0, -45), (0, 45), "Spine"),
                   _line((250, -40), (250, 40), "Slit", way="", layer="Cut"))
    win = window(doc)
    props = win.properties

    def show(*names):
        props.show_selection(_select(win, *[
            s for s in win.doc.shapes if s.name in names]))
        return props._appear_form.isRowVisible(props.thickness)
    # a row for leather: closed outlines on a cut layer
    assert show("Shell") and props.thickness.text() == "Not set"
    assert not show("Spine") and not show("Slit")
    # several pieces at once, as one undo step
    assert show("Shell", "Pocket", "Spine")
    props.thickness.setValue(1.4)
    props.thickness.editingFinished.emit()
    assert [s.thickness for s in win.doc.shapes] == [1.4, 1.4, 0.0, 0.0]
    assert win.history.can_undo()
    win.undo()
    assert [s.thickness for s in win.doc.shapes] == [0.0, 0.0, 0.0, 0.0]
    assert not win.history.can_undo()
    win.redo()
    assert [s.thickness for s in win.doc.shapes] == [1.4, 1.4, 0.0, 0.0]
    assert show("Pocket") and props.thickness.text() == "1.4 mm"
    # typed in inches, kept in millimetres
    mm.set_current(mm.IN)
    assert show("Pocket") and props.thickness.text() == "0.0551 in"
    props.thickness.setFocus()
    props.thickness.selectAll()
    QTest.keyClicks(props.thickness, "1/16")
    QTest.keyClick(props.thickness, Qt.Key_Return)
    QApplication.processEvents()
    pocket, = _named(win.doc, "Pocket")
    assert pocket.thickness == pytest.approx(1.5875)
    assert props.thickness.text() == "0.0625 in"
    assert show("Shell") and show("Pocket")      # chosen again: as typed
    assert props.thickness.text() == "0.0625 in"
    mm.set_current(mm.MM)
    assert show("Pocket") and props.thickness.text() == "1.5875 mm"
    # 0 is not set, and thinner than the preview's thinnest is that
    assert show("Pocket")
    props.thickness.setValue(0.05)
    assert pocket.thickness == 0.2 and props.thickness.value() == 0.2
    props.thickness.setValue(0.0)
    props.thickness.editingFinished.emit()
    assert pocket.thickness == 0.0 and props.thickness.text() == "Not set"
    assert "thickness" not in win.doc.to_dict()["shapes"][1]


# -- reading the drawing ------------------------------------------------------

def _wallet():
    """The fold-over wallet template with its three folds made."""
    doc = templates.fold_over_wallet()
    for sh in doc.shapes:
        if sh.name.endswith("fold"):
            sh.fold_dir, sh.fold_angle = "front", 180.0
    return doc


def test_a_stack_of_one_piece_is_what_the_preview_shows_today(qapp):
    from leathercad_app.preview3d import fold_lines, foldables
    for doc in (_wallet(), _drawing(*hemmed_pocket()),
                _drawing(*bifold_wallet())):
        asm.take_off(doc, doc.shapes)        # the bifold arrives put together
        today, = [f for f in foldables(doc) if f.kind == "piece"]
        sh = asm.shape_by_id(doc, today.key)
        asm.put_on(doc, sh)
        st, = asm.stacks(doc)
        lie = st.main
        assert lie.shape is sh and (lie.main, lie.above) == (True, False)
        m = lie.from_drawing                 # the bench is the drawing moved
        assert m[:4] == (1.0, 0.0, 0.0, 1.0)

        def moved(pts):
            return [asm.map_point(m, p) for p in pts]
        assert _flat(lie.outline) == _near(_flat(moved(today.outline)))
        assert len(lie.cutouts) == len(today.cutouts)
        for mine, theirs in zip(lie.cutouts, today.cutouts):
            assert _flat(sorted(mine)) == _near(
                _flat(sorted(moved(theirs)[:-1])), abs=1e-5)
        assert [(k, len(p)) for k, p in lie.marks] == \
            [(k, len(p)) for k, p in today.marks]
        for (_k, mine), (_k2, theirs) in zip(lie.marks, today.marks):
            assert _flat(mine) == _near(_flat(moved(theirs)), abs=1e-5)
        assert len(lie.holes) == len(today.holes) > 50
        assert _flat(sorted(lie.holes)) == _near(_flat(sorted(
            asm.map_point(m, h) + (h[2],) for h in today.holes)), abs=1e-5)
        folds = fold_lines(today.lines)
        assert [(f.name, f.angle, f.direction, f.radius, f.order)
                for f in lie.folds] == \
            [(f.name, f.angle, f.direction, f.radius, f.order)
             for f in folds]
        for mine, theirs in zip(lie.folds, folds):
            assert mine.a + mine.b == _near(
                asm.map_point(m, theirs.a) + asm.map_point(m, theirs.b),
                abs=1e-5)
            assert mine.shape is theirs.key and mine.key == \
                theirs.key.shape_id
    # the last of them, the bifold's shell: its own 142 holes, one fold
    assert (len(lie.cutouts), len(lie.marks), len(lie.holes),
            len(lie.folds)) == (0, 0, 142, 1)
    # the wallet in its own frame, in no stack: two cutouts, a slit, the
    # holes of six seams, three folds
    doc = _wallet()
    was = json.dumps(doc.to_dict())
    wallet = asm.read_piece(doc, doc.shapes[0])
    assert (len(wallet.cutouts), len(wallet.marks), len(wallet.holes),
            len(wallet.folds)) == (2, 1, 84, 3)
    assert wallet.box == _near(doc.shapes[0].bounds())
    assert asm.read_piece(doc, doc.shapes[-1]) is None       # a fold line
    assert asm.read_piece(doc, "shape0") is None
    assert json.dumps(doc.to_dict()) == was          # reading writes nothing


def test_a_fold_line_belongs_to_one_of_two_pieces_drawn_on_each_other():
    panel = _piece("Panel", 120, 90, r=6)
    pocket, hem = hemmed_pocket()            # 100 x 74.36, its hem fold
    spine = _line((0, -45), (0, 45), "Panel fold")
    short = _line((-30, -10), (30, -10), "Crease", angle=90.0)
    doc = _drawing(panel, pocket, hem, spine, short)
    # the hem fold runs from edge to edge of the pocket; cut on to the
    # outline as a fold is, it crosses the panel under it as well
    assert asm.SNAP == inspect.signature(fs.split_piece).parameters[
        "snap"].default == 0.3
    ring = fs.model_outline(asm.own_outline(panel))
    ends = [(p.x, p.y) for p in hem.world_polyline()[0]]
    assert fs.split_piece(ring, [fs.FoldLine(*ends)])[1][0] is not None
    key = asm.put_on(doc, panel, thickness=2.0)
    asm.put_on(doc, pocket, key, thickness=1.5)
    st, = asm.stacks(doc)
    under, on = st.pieces
    # ... but it is the pocket's: both its ends lie on the pocket's edge.
    # It never folds the panel
    assert [f.name for f in under.folds] == ["Panel fold"]
    assert [(f.name, f.direction) for f in on.folds] == \
        [("Hem fold", -1), ("Crease", 1)]
    # the panel's own fold crosses the pocket too, and ends on the panel
    fold, = under.folds
    assert (fold.a, fold.b) == ((0.0, -45.0), (0.0, 45.0))
    assert (fold.angle, fold.direction, fold.key) == (180.0, 1,
                                                      spine.shape_id)
    # a crease that reaches no edge is the smallest piece's it crosses
    assert on.folds[1].shape is short
    # drawn apart, each piece has the same fold lines
    pocket.transform.x = hem.transform.x = short.transform.x = 400.0
    under, on = asm.stacks(doc)[0].pieces
    assert [f.name for f in under.folds] == ["Panel fold"]
    assert [f.name for f in on.folds] == ["Hem fold", "Crease"]
    # a fold line across no piece is nobody's
    doc.add_shape(_line((900, 0), (900, 50), "Stray"))
    assert all(f.name != "Stray" for p in asm.stacks(doc)[0].pieces
               for f in p.folds)


def test_a_fold_folds_the_other_way_when_the_main_piece_lies_mirrored():
    doc, by = _bifold()
    shell = by["Outer shell"]

    def spine():
        main = asm.stacks(doc)[0].main
        return main.folds[0].direction, main.grain_up, main.mirrored
    # the generated spine folds to the back, to close with the grain outside
    assert by["Spine fold"].fold_dir == "back"
    assert spine() == (-1, True, False)
    by["Spine fold"].fold_dir = "front"
    assert spine() == (1, True, False)
    asm.turn_over(doc, shell)                # flesh up: the drawn side down
    assert spine() == (-1, False, True)
    asm.mirror(doc, shell)                   # ... drawn from the flesh side
    assert spine() == (1, False, False)
    asm.turn_over(doc, shell)                # grain up, shown mirrored
    assert spine() == (-1, True, True)
    by["Spine fold"].fold_dir = "back"
    assert spine() == (1, True, True)
    # what a fold is to be marked as on the drawing to fold a given way
    # on the bench
    main = asm.stacks(doc)[0].main
    assert (main.fold_dir(1), main.fold_dir(-1)) == ("back", "front")
    asm.mirror(doc, shell)
    main = asm.stacks(doc)[0].main
    assert (main.fold_dir(1), main.fold_dir(-1)) == ("front", "back")
    assert spine() == (-1, True, False)
    asm.mirror(doc, shell)
    # turned a quarter, the fold line lies across the bench
    asm.turn(doc, shell, 90.0)
    fold, = asm.stacks(doc)[0].main.folds
    assert _flat(sorted((fold.a, fold.b))) == _near((-45.0, 0.0, 45.0, 0.0))


def test_stitch_holes_go_to_the_piece_they_are_cut_in():
    # the Card holder as drawn: the pocket lies on the panel's stitching
    # and has none of its own
    doc = templates.card_holder()
    panel, pocket = doc.shapes
    asm.put_on(doc, pocket, asm.put_on(doc, panel))
    under, on = asm.stacks(doc)[0].pieces
    assert (len(under.holes), len(on.holes)) == (82, 0)
    inside = [h for h in under.holes if fs.pc.inside(h[:2], on.outline)]
    assert len(inside) == 46                 # what it is drawn over
    assert {h[2] for h in under.holes} == {1.0}
    # the Slim card holder as drawn: one seam through four pieces. Each
    # gets the holes of the seam that lie on it
    doc = templates.slim_card_holder()
    back = doc.shapes[0]
    key = asm.put_on(doc, back, thickness=1.5)
    for sh in doc.shapes[1:]:
        asm.put_on(doc, sh, key, thickness=1.0)
    assert len(doc.stitch_lines[0].result().holes) == 67
    assert [(p.name, len(p.holes)) for p in asm.stacks(doc)[0].pieces] == \
        [("Back panel", 67), ("Card pocket (back)", 65),
         ("Card pocket (middle)", 61), ("Card pocket (front)", 55)]
    # the pockets drawn apart, the seam left where it is: it is the back
    # panel's alone, as the laser cuts it
    for sh in doc.shapes[1:]:
        sh.transform.x += 200.0
    assert [len(p.holes) for p in asm.stacks(doc)[0].pieces] == [67, 0, 0, 0]
    # a loose hole goes to the piece it is drawn on
    from leathercad.holes import LooseHole
    doc.holes.append(LooseHole(point=Vec2(200.0, 40.0), hole_diameter=3.0))
    holes = [p.holes for p in asm.stacks(doc)[0].pieces]
    assert [len(h) for h in holes] == [67, 1, 1, 1]
    assert holes[3] == [(0.0, -6.0, 3.0)]    # 40 up from its bottom, -46


def test_a_piece_s_own_holes_are_its_stitching_wherever_it_is_drawn():
    from leathercad.stitching import holes_for_shape
    sh = _piece("Panel", 90, 60, at=(50, 60), r=8,
                stitch=StitchSettings(pitch_mm=3.85, inset=3.5,
                                      hole_diameter=1.2))
    sh.transform.rotation, sh.transform.mirror_x = 30.0, True
    doc = _drawing(sh)
    own = asm.read_piece(doc, sh).holes
    world = holes_for_shape(sh, doc).holes
    assert len(own) == len(world) == 68 and {h[2] for h in own} == {1.2}
    m = asm.own_frame(sh)
    assert _flat(h[:2] for h in own) == _near(_flat(
        asm.map_point(m, (w.point.x, w.point.y)) for w in world))
    # moved, the piece has the very same holes
    sh.transform.x, sh.transform.rotation = -400.0, 135.0
    assert asm.read_piece(doc, sh).holes == own
    # attached holes are its own as well
    sh.baked_holes = list(holes_for_shape(
        _piece("", 90, 60, stitch=StitchSettings(pitch_mm=6.0))).holes)
    assert len(asm.read_piece(doc, sh).holes) == len(sh.baked_holes) > 20


def test_a_pocket_drawn_inside_its_panel_can_be_put_on_and_a_slot_cannot():
    panel = _piece("Back panel", 105, 70, r=6)
    pocket = _piece("Pocket", 103, 45, at=(0, -11.5), r=6)   # 1 mm inside
    slot = _piece("Slot", 20, 3, at=(0, 25), r=1.5)
    rivet = Circle(name="Rivet hole", rx=1.5, ry=1.5, layer="Cut",
                   transform=Transform(x=45, y=28))
    thumb = _piece("Thumb slot", 30, 4, at=(0, 5), r=2)  # in the pocket
    doc = _drawing(panel, pocket, slot, rivet, thumb)
    # as the rest of the program reads it: one piece with three cutouts,
    # and what drops out of the pocket's own slot
    assert [s.name for s in asm.offered(doc)] == ["Back panel",
                                                  "Thumb slot"]
    assert len(asm.read_piece(doc, panel).cutouts) == 3
    # the pocket is 63 % of the panel: it can be put on from the list of
    # what is drawn inside it. The slot (0.8 %) and the rivet hole can't
    assert asm.OFFER_SHARE == 0.15
    assert [(a.name, b.name) for a, b in asm.drawn_inside(doc)] == \
        [("Pocket", "Back panel")]
    key = asm.put_on(doc, panel, thickness=2.0)
    assert asm.put_on(doc, pocket, key, thickness=1.2) == key
    # put on, it is a piece here, where it is drawn
    assert [s.name for s in asm.offered(doc)] == ["Back panel", "Pocket"]
    assert asm.drawn_inside(doc) == []
    under, on = asm.stacks(doc)[0].pieces
    assert on.box == _near((-51.5, -34.0, 51.5, 11.0))
    assert (pocket.placed.ax, pocket.placed.x, pocket.placed.ay,
            pocket.placed.y) == (0.5, 0.0, 0.5, -11.5)
    # ... no longer cut out of the panel, and its own slot is cut in it
    assert len(under.cutouts) == 2 and len(on.cutouts) == 1
    assert fs.pc.bounds(on.cutouts[0]) == _near((-15.0, 3.0, 15.0, 7.0),
                                                abs=1e-5)
    assert sorted(fs.pc.bounds(c)[1] for c in under.cutouts) == \
        _near([23.5, 26.5], abs=1e-5)
    # taken off, it is a cutout again
    asm.take_off(doc, pocket)
    assert [s.name for s in asm.offered(doc)] == ["Back panel",
                                                  "Thumb slot"]
    # a lining nearly as big as its panel is a piece without being asked
    lining = doc.add_shape(_piece("Lining", 99, 66))
    assert lining in asm.offered(doc)
    assert all(a is not lining for a, _b in asm.drawn_inside(doc))


def test_a_scored_line_is_on_the_smallest_piece_it_lies_on():
    panel = _piece("Panel", 120, 90)
    pocket = _piece("Pocket", 60, 40, at=(10, 5))
    on_pocket = _line((-10, 0), (30, 10), "Logo", way="")
    on_panel = _line((-55, -40), (-45, 40), "Edge crease", way="")
    slit = _line((0, -40), (0, -30), "Slit", way="", layer="Cut")
    across = _line((-58, 0), (-25, 0), "Half on", way="")
    doc = _drawing(panel, pocket, on_pocket, on_panel, slit, across)
    asm.put_on(doc, pocket, asm.put_on(doc, panel))
    under, on = asm.stacks(doc)[0].pieces
    assert on.marks == [("line", [(-10.0, 0.0), (30.0, 10.0)])]
    assert asm.read_piece(doc, pocket).marks == [
        ("line", [(-20.0, -5.0), (20.0, 5.0)])]      # in its own frame
    assert under.marks == [
        ("line", [(-55.0, -40.0), (-45.0, 40.0)]),
        ("cut", [(0.0, -40.0), (0.0, -30.0)]),
        ("line", [(-58.0, 0.0), (-25.0, 0.0)])]
    # a line with an end off every piece is on none
    doc.add_shape(_line((50, 0), (90, 0), "Overhang", way=""))
    assert sum(len(p.marks) for p in asm.stacks(doc)[0].pieces) == 4
    # without the detail a list of the pieces is all that is read
    quick, = asm.stacks(doc, detail=False)
    assert [(p.name, p.marks, p.holes, p.folds) for p in quick.pieces] == \
        [("Panel", [], [], []), ("Pocket", [], [], [])]
    assert [p.box for p in quick.pieces] == [p.box for p in (under, on)]

"""Select everything and the loose holes go back to the piece they're inside.

The report: after breaking holes out of a part, joining them back was a
struggle -- Attach holes wanted exactly one piece and its own holes selected,
and Join (after Break apart) left the holes loose. Now Attach with several
pieces selected, and Join, send each loose hole to the piece it lies in."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document, LooseHole  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import PathShape, Rectangle, Transform  # noqa: E402
from leathercad.stitching import holes_for_shape  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _stitched(sh):
    sh.stitch = StitchSettings(pitch_mm=4.0, inset=3.0)
    return sh


def _win(doc):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc)
    win.canvas.rebuild()
    win.canvas.resize(800, 600)
    return win


def _holes_of(sh, doc):
    return sorted((round(h.point.x, 6), round(h.point.y, 6))
                  for h in holes_for_shape(sh, doc).holes)


def _select_all(c):
    c.scene_obj.clearSelection()
    for it in c.scene_obj.items():
        if c._item_model(it) is not None:
            it.setSelected(True)


def _explode_all(c):
    _select_all(c)
    c.ungroup_selected()


def test_attach_with_everything_selected_sends_each_hole_home(qapp):
    doc = Document()
    a = _stitched(Rectangle(width=60, height=40, layer="Cut"))
    b = _stitched(Rectangle(width=40, height=30, layer="Cut",
                            transform=Transform(x=100, y=10)))
    doc.add_shape(a)
    doc.add_shape(b)
    win = _win(doc)
    c = win.canvas
    want = {a.shape_id: _holes_of(a, doc), b.shape_id: _holes_of(b, doc)}
    _explode_all(c)
    assert len(doc.holes) == len(want[a.shape_id]) + len(want[b.shape_id])
    msgs = []
    c.statusMessage.connect(msgs.append)
    _select_all(c)
    c.group_selected()                       # Attach holes (Ctrl+Shift+A)
    assert doc.holes == []                   # none left behind
    for sh in doc.shapes:
        assert _holes_of(sh, doc) == want[sh.shape_id]
    assert msgs[-1].startswith(f"Attached {len(want[a.shape_id]) + len(want[b.shape_id])} holes to 2 pieces")


def test_a_pockets_holes_go_to_the_pocket_not_the_panel_under_it(qapp):
    doc = Document()
    panel = _stitched(Rectangle(width=120, height=80, layer="Cut"))
    pocket = _stitched(Rectangle(width=50, height=30, layer="Cut",
                                 transform=Transform(x=10, y=5)))
    doc.add_shape(panel)
    doc.add_shape(pocket)
    win = _win(doc)
    c = win.canvas
    want = {panel.shape_id: _holes_of(panel, doc),
            pocket.shape_id: _holes_of(pocket, doc)}
    _explode_all(c)
    _select_all(c)
    c.group_selected()
    assert doc.holes == []
    for sh in doc.shapes:
        assert _holes_of(sh, doc) == want[sh.shape_id]


def test_break_apart_then_select_all_and_join_comes_back_whole(qapp):
    doc = Document()
    r = _stitched(Rectangle(width=60, height=40, layer="Cut"))
    doc.add_shape(r)
    win = _win(doc)
    c = win.canvas
    want = _holes_of(r, doc)
    _select_all(c)
    c.break_apart_selected()                 # Ctrl+B: 4 edges + loose holes
    assert len(doc.shapes) == 4 and len(doc.holes) == len(want)
    msgs = []
    c.statusMessage.connect(msgs.append)
    win._select_all()                        # Ctrl+A, as in the app
    c.join_selected()                        # Ctrl+J with everything selected
    assert len(doc.shapes) == 1 and doc.holes == []
    sh = doc.shapes[0]
    assert sh.world_polyline()[2]            # closed again
    assert _holes_of(sh, doc) == want        # every hole back, unmoved
    assert msgs[-1].startswith("Joined 4 pieces into 1 path — attached")
    win.undo()                               # one step undoes the lot
    assert len(win.doc.shapes) == 4 and len(win.doc.holes) == len(want)


def test_join_attaches_holes_even_with_nothing_to_weld(qapp):
    doc = Document()
    r = _stitched(Rectangle(width=60, height=40, layer="Cut"))
    doc.add_shape(r)
    win = _win(doc)
    c = win.canvas
    want = _holes_of(r, doc)
    _explode_all(c)
    _select_all(c)
    c.join_selected()                        # no segments: just the holes
    assert doc.holes == [] and _holes_of(doc.shapes[0], doc) == want


def test_a_hole_inside_no_piece_stays_loose_and_selected(qapp):
    from leathercad_app.items import HoleItem
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, layer="Cut"))
    doc.add_shape(Rectangle(width=20, height=20, layer="Cut",
                            transform=Transform(x=100)))
    doc.add_hole(LooseHole(point=Vec2(0, 0)))          # in the first
    stray = LooseHole(point=Vec2(50, 50))               # in neither
    doc.add_hole(stray)
    win = _win(doc)
    c = win.canvas
    msgs = []
    c.statusMessage.connect(msgs.append)
    _select_all(c)
    c.group_selected()
    assert doc.holes == [stray]
    still = [it for it in c.selected_items() if isinstance(it, HoleItem)]
    assert [it.hole for it in still] == [stray]
    assert "1 not inside any selected piece stayed loose" in msgs[-1]


def test_holes_on_an_open_line_go_to_the_line_and_guides_take_none(qapp):
    doc = Document()
    line = PathShape(points=[Vec2(0, 0), Vec2(50, 0)], close_path=False,
                     layer="Cut")
    guide = Rectangle(width=30, height=30, layer="Cut",
                      transform=Transform(x=0, y=60))
    guide.construction = True
    doc.add_shape(line)
    doc.add_shape(guide)
    on_line = [LooseHole(point=Vec2(10, 0.4)), LooseHole(point=Vec2(30, -0.3))]
    in_guide = LooseHole(point=Vec2(0, 60))
    for h in on_line + [in_guide]:
        doc.add_hole(h)
    win = _win(doc)
    c = win.canvas
    _select_all(c)
    c.group_selected()
    assert doc.holes == [in_guide]            # a guide is never a piece
    assert len(line.baked_holes) == 2


def test_one_piece_selected_still_takes_every_selected_hole(qapp):
    doc = Document()
    r = Rectangle(width=20, height=20, layer="Cut")
    doc.add_shape(r)
    doc.add_hole(LooseHole(point=Vec2(80, 0)))          # well outside it
    win = _win(doc)
    c = win.canvas
    _select_all(c)
    c.group_selected()
    assert doc.holes == [] and len(r.baked_holes) == 1

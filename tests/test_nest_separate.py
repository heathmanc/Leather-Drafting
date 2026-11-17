"""Nesting never leaves pieces stuck together.

The report: "if a piece overlaps another piece when the nesting starts, it
doesn't separate them, grouped items should be separated" -- with a pattern
whose side panels were outlines drawn as open paths, finished a little past
where they began. An open path counted as a line (a fold line rides on the
piece it crosses), so the panel lying across the body was nested stuck to
it, and its copy was nested as a hollow outline that other pieces could
land inside. A move-group holding several pieces was nested as one, too."""

import os

import pytest

from leathercad.document import Document
from leathercad.estimate import estimate_project
from leathercad.geometry import Vec2
from leathercad.holes import LooseHole
from leathercad.nesting import group_pieces
from leathercad.pieces import closes, on_cut_layer
from leathercad.shapes import (Edge, EditablePath, PathShape, Rectangle,
                               Transform)


def _loop_path(x, y, layer="Cut"):
    """A 50 x 100 side panel drawn as an open path: round from its top
    corner and back, then 4 mm on down its first side (as the uploaded
    pattern's panels were)."""
    nodes = [Vec2(20, 50), Vec2(20, -50), Vec2(-30, -50), Vec2(-30, 50),
             Vec2(20, 50), Vec2(20, 46)]
    return EditablePath(nodes=nodes, edges=[Edge("line")] * 5, closed=False,
                        layer=layer, transform=Transform(x, y))


def test_a_path_drawn_round_to_its_start_encloses_leather():
    sq = [Vec2(0, 0), Vec2(50, 0), Vec2(50, 50), Vec2(0, 50)]
    assert closes(sq, closed=True)
    assert closes(sq + [Vec2(0, 0)])                       # ends meet
    assert closes(sq + [Vec2(0, 0), Vec2(10, 0)])          # a little past
    # begun partway along a side and finished past where it began
    assert closes([Vec2(25, 0), Vec2(50, 0), Vec2(50, 50), Vec2(0, 50),
                   Vec2(0, 0), Vec2(40, 0)])
    assert not closes(sq)                                  # an open U
    # an L traced out and back along itself: its ends meet, round nothing
    assert not closes([Vec2(0, 0), Vec2(50, 0), Vec2(50, 30), Vec2(50, 0),
                       Vec2(0, 0)])
    assert not closes([Vec2(0, 0), Vec2(50, 0)])


def _doc(*shapes):
    doc = Document()
    for sh in shapes:
        doc.add_shape(sh)
    return doc


def test_a_panel_drawn_as_an_open_loop_is_a_piece_of_its_own():
    body = Rectangle(width=176, height=100, corner_radius=10, layer="Cut",
                     transform=Transform(0, 0))
    over = _loop_path(-50, -30)            # lying half across the body
    copy = _loop_path(-300, 0)
    doc = _doc(body, over, copy)
    groups, _left = group_pieces([body, over, copy], is_cut=on_cut_layer(doc))
    assert sorted(len(g.shapes) for g in groups) == [1, 1, 1]
    for g in groups:
        assert g.ring is not None          # each one filled, none hollow
    assert estimate_project(doc).pieces == 3
    # without the cut-layer test, the old rules: the panel rides the body
    old, _ = group_pieces([body, over, copy])
    assert len(old) == 2


def test_a_fold_line_still_rides_its_piece():
    body = Rectangle(width=100, height=60, layer="Cut",
                     transform=Transform(0, 0))
    fold = PathShape(points=[Vec2(-55, 0), Vec2(55, 0)], close_path=False,
                     layer="Score", transform=Transform(0, 0))
    slit = PathShape(points=[Vec2(-20, 10), Vec2(20, 10)], close_path=False,
                     layer="Cut", transform=Transform(0, 0))
    doc = _doc(body, fold, slit)
    groups, _ = group_pieces([body, fold, slit], is_cut=on_cut_layer(doc))
    assert len(groups) == 1 and len(groups[0].shapes) == 3


def test_a_move_group_of_pieces_is_split_and_its_marks_stay_put():
    a = Rectangle(width=100, height=60, layer="Cut", transform=Transform(0, 0))
    b = Rectangle(width=100, height=60, layer="Cut",
                  transform=Transform(30, 20))          # overlapping a
    slot = Rectangle(width=30, height=6, layer="Cut",
                     transform=Transform(-20, -10))     # a slot in a
    fold = PathShape(points=[Vec2(-25, 40), Vec2(85, 40)], close_path=False,
                     layer="Score", transform=Transform(0, 0))  # across b
    far = Rectangle(width=20, height=10, layer="Engrave",
                    transform=Transform(-80, 0))        # off both
    for sh in (a, b, slot, fold, far):
        sh.group_id = "g1"
    hole = LooseHole(point=Vec2(70, 45))                # on b only
    hole.group_id = "g1"
    doc = _doc(a, b, slot, fold, far)
    groups, left = group_pieces([a, b, slot, fold, far], holes=[hole],
                                is_cut=on_cut_layer(doc))
    assert len(groups) == 2
    ga = next(g for g in groups if a in g.shapes)
    gb = next(g for g in groups if b in g.shapes)
    assert slot in ga.shapes and far in ga.shapes      # far: nearest is a
    assert fold in gb.shapes and hole in gb.holes
    assert left == ([], [], [])


# -- on the drawing --------------------------------------------------------------
pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _apart(sa, sb):
    from leathercad.geometry import point_in_polygon
    pa = [Vec2(p.x, p.y) for p in sa.world_polyline()[0]]
    pb = [Vec2(p.x, p.y) for p in sb.world_polyline()[0]]

    def seg_d(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_sq(), 1e-18)))
        return (a.lerp(b, t) - p).length()
    if any(point_in_polygon(p, pb) for p in pa) or \
            any(point_in_polygon(p, pa) for p in pb):
        return 0.0
    return min(min(seg_d(p, pb[j], pb[j + 1]) for p in pa
                   for j in range(len(pb) - 1)),
               min(seg_d(q, pa[i], pa[i + 1]) for q in pb
                   for i in range(len(pa) - 1)))


@pytest.mark.parametrize("smallest", [False, True])
def test_overlapping_pieces_come_out_apart(qapp, smallest):
    from leathercad_app.mainwindow import MainWindow
    body = Rectangle(width=176, height=100, corner_radius=10, layer="Cut",
                     transform=Transform(0, 0))
    over = _loop_path(-50, -30)
    copy = _loop_path(-300, 0)
    a = Rectangle(width=60, height=40, layer="Cut", transform=Transform(0, 200))
    b = Rectangle(width=60, height=40, layer="Cut",
                  transform=Transform(20, 210))
    a.group_id = b.group_id = "pair"
    doc = _doc(body, over, copy, a, b)
    win = MainWindow(doc)
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    try:
        report = win.canvas.nest_selected(600, 450, margin=5, spacing=8,
                                          smallest=smallest, budget=0)
        assert "5 pieces" in report or "Placed 5 of 5" in report, report
        real = [s for s in win.doc.shapes if not s.construction]
        for i in range(len(real)):
            for j in range(i + 1, len(real)):
                assert _apart(real[i], real[j]) >= 8.0 - 0.05
    finally:
        win.hide()

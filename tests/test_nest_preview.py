"""Nest on sheet, previewed on the drawing: the pieces move as the settings
change, Apply keeps the layout as one undo step, Cancel puts every piece
(and everything riding on it) back exactly."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _rig(qapp):
    from leathercad.document import Document
    from leathercad.holes import LooseHole
    from leathercad.stitchline import StitchLine
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    pieces = [Rectangle(width=60 + 15 * i, height=40 + 5 * i, layer="Cut",
                        transform=Transform(x=300.0 * i, y=150.0 * i))
              for i in range(4)]
    for p in pieces:
        doc.add_shape(p)
    doc.holes.append(LooseHole(point=Vec2(5, 5)))            # on piece 0
    doc.stitch_lines.append(StitchLine(points=[Vec2(290, 150), Vec2(320, 150)]))
    win = MainWindow(doc)
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    return win, win.canvas, doc, pieces


def _where(doc):
    return ([(round(s.transform.x, 9), round(s.transform.y, 9), s.transform.rotation)
             for s in doc.shapes],
            [(h.point.x, h.point.y) for h in doc.holes],
            [[(p.x, p.y) for p in sl.points] for sl in doc.stitch_lines])


def test_preview_moves_the_pieces_and_cancel_puts_everything_back(qapp):
    win, c, doc, pieces = _rig(qapp)
    try:
        before = _where(doc)
        steps = len(win.history._stack)
        report = c.nest_preview(400, 300, margin=5, spacing=8)
        assert "Placed 4 of 4" in report
        assert c.nest_previewing()
        assert _where(doc) != before
        # the hole and the seam rode with their pieces
        hole_off = (doc.holes[0].point.x - pieces[0].transform.x,
                    doc.holes[0].point.y - pieces[0].transform.y)
        assert hole_off[0] == pytest.approx(5.0) or hole_off[1] == pytest.approx(5.0)
        c.nest_cancel()
        assert not c.nest_previewing()
        assert _where(doc) == before
        assert not any(s.name == "Sheet" for s in doc.shapes)
        assert len(win.history._stack) == steps       # nothing to undo
        assert not win.history.can_undo() or steps > 1
    finally:
        win.hide()


def test_each_preview_starts_from_where_the_pieces_were(qapp):
    win, c, doc, _pieces = _rig(qapp)
    try:
        c.nest_preview(250, 600, margin=5, spacing=8)
        c.nest_preview(400, 300, margin=5, spacing=8)
        twice = _where(doc)
        c.nest_cancel()
        c.nest_preview(400, 300, margin=5, spacing=8)
        assert _where(doc) == twice
        c.nest_cancel()
    finally:
        win.hide()


def test_apply_keeps_the_layout_as_one_undo_step(qapp):
    win, c, doc, pieces = _rig(qapp)
    try:
        before = _where(doc)
        c.nest_preview(400, 300, margin=5, spacing=8)
        nested = _where(doc)
        assert c.nest_apply()
        assert not c.nest_previewing()
        assert [s for s in doc.shapes if s.name == "Sheet"]
        assert _where(doc)[0][:len(pieces)] == nested[0][:len(pieces)]
        win.undo()
        qapp.processEvents()
        assert _where(win.doc)[0] == before[0]
    finally:
        win.hide()


def test_the_gap_holds_between_nested_pieces(qapp):
    win, c, doc, pieces = _rig(qapp)
    try:
        c.nest_preview(400, 300, margin=5, spacing=8)
        outs = [[Vec2(p.x, p.y) for p in s.world_polyline()[0]] for s in pieces]

        def seg_d(p, a, b):
            ab = b - a
            t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_sq(), 1e-18)))
            return (a.lerp(b, t) - p).length()

        def ring_d(A, B):
            return min(min(seg_d(p, B[j], B[j + 1]) for p in A
                           for j in range(len(B) - 1)),
                       min(seg_d(q, A[i], A[i + 1]) for q in B
                           for i in range(len(A) - 1)))
        closest = min(ring_d(outs[i], outs[j]) for i in range(4)
                      for j in range(i + 1, 4))
        assert closest >= 8.0
        c.nest_cancel()
    finally:
        win.hide()

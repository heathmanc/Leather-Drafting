"""Performance quick wins: no repeated work on a layer tick, a commit or an
ungroup; lazy corner arc-lengths; a fit cache sized to the document; a kerf
pass that skips pairs that cannot nest; dimensions re-anchored only for what
moved."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _stitched(sh, **kw):
    s = StitchSettings(pitch_mm=3.85, inset=3.5)
    for k, v in kw.items():
        setattr(s, k, v)
    sh.stitch = s
    return sh


def _win(doc=None):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc or Document())
    win.show()
    c = win.canvas
    c.rebuild()
    c.snap_to_grid = c.snap_to_nodes = False
    QApplication.processEvents()
    return win, c


def _grid_doc(n, stitched=True):
    doc = Document()
    for i in range(n):
        sh = Rectangle(width=40, height=20, layer="Cut",
                       transform=Transform(x=(i % 10) * 50.0, y=(i // 10) * 30.0))
        if stitched:
            _stitched(sh)
        doc.add_shape(sh)
    return doc


class _Counter:
    def __init__(self, monkeypatch, cls, name):
        self.n = 0
        orig = getattr(cls, name)

        def wrapped(*a, **kw):
            self.n += 1
            return orig(*a, **kw)
        monkeypatch.setattr(cls, name, wrapped)


# -- layer tick / colour ------------------------------------------------------------
def test_layer_tick_hides_without_resyncing(qapp, monkeypatch):
    from leathercad_app.items import ShapeItem
    win, c = _win(_grid_doc(20))
    syncs = _Counter(monkeypatch, ShapeItem, "sync_from_model")
    panel = win.layers
    row = next(panel.list.item(i) for i in range(panel.list.count())
               if panel.list.item(i).data(Qt.UserRole) == "Cut")
    row.setCheckState(Qt.Unchecked)
    QApplication.processEvents()
    assert syncs.n == 0
    assert not win.doc.layer("Cut").visible
    row.setCheckState(Qt.Checked)
    assert syncs.n == 0 and win.doc.layer("Cut").visible
    c.recolor_layer("Cut", "#123456")
    assert syncs.n == 0
    items = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)]
    assert all(i._color.name() == "#123456" for i in items)
    win.hide()


# -- commits with hardware present ----------------------------------------------------
def test_commit_restitches_only_what_a_moved_keepout_touches(qapp, monkeypatch):
    from leathercad.hardware import build
    from leathercad_app.items import ShapeItem
    doc = _grid_doc(30)
    parts, _t = build("snap_20")
    for sh in parts:
        sh.transform.x, sh.transform.y = 5000.0, 5000.0     # far from everything
        doc.add_shape(sh)
    win, c = _win(doc)
    syncs = _Counter(monkeypatch, ShapeItem, "sync_from_model")
    c._emit_commit()                                         # nothing changed
    assert syncs.n == 0
    snap = next(s for s in parts if getattr(s, "keepout", 0))
    snap.transform.x, snap.transform.y = 5000.0, 4000.0      # moved, still far
    c._emit_commit()
    assert syncs.n == 0
    snap.transform.x, snap.transform.y = 20.0, 0.0           # onto piece 0's seam
    c._emit_commit()
    assert 1 <= syncs.n <= 3                                 # that piece, not 30
    win.hide()


# -- ungroup is one operation, not one per hole -----------------------------------------
def test_ungroup_many_pieces_refreshes_once(qapp, monkeypatch):
    from leathercad_app.panels import PropertiesPanel
    from leathercad_app.items import ShapeItem
    win, c = _win(_grid_doc(12))
    items = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)]
    c._set_selected_many(items)
    QApplication.processEvents()
    readouts = _Counter(monkeypatch, PropertiesPanel, "_update_readout")
    c.ungroup_selected()
    QApplication.processEvents()
    assert len(win.doc.holes) > 100
    assert readouts.n <= 4
    win.hide()


# -- engine -----------------------------------------------------------------------------
def test_path_corner_arclengths_are_lazy_and_unchanged():
    from leathercad.path import PathBuilder
    b = PathBuilder().move_to(0, 0)
    b.line_to(40, 0, corner=True).line_to(40, 25, corner=True)
    b.line_to(0, 25, corner=True).close(corner=True)
    path = b.build()
    assert path._corners is None                 # nothing computed yet
    assert path.corners == [0.0, 40.0, 65.0, 105.0]
    assert path.corner_points[0] == Vec2(40, 0)
    rounded = Rectangle(width=60, height=40, corner_radius=8).local_path()
    assert rounded._corners is None
    assert len(rounded.corners) == 8             # two tangent points per arc


def test_keepouts_for_shape_answers_without_the_outline_when_no_hardware(monkeypatch):
    from leathercad.hardware import keepouts_for_shape
    doc = Document()
    sh = _stitched(Rectangle(width=60, height=40, layer="Cut"))
    doc.add_shape(sh)

    def boom(self):
        raise AssertionError("bounds() must not be needed")
    monkeypatch.setattr(Rectangle, "bounds", boom)
    assert keepouts_for_shape(sh, doc) == []


def test_fit_cache_grows_with_the_document(qapp):
    from leathercad import stitching
    win, c = _win(_grid_doc(400, stitched=False))
    assert stitching._CACHE_MAX >= 800
    win.hide()
    stitching.set_cache_capacity(0)
    assert stitching._CACHE_MAX == 512


def test_kerf_prefilter_matches_the_full_nesting_test():
    from leathercad.export import _apply_kerf
    from leathercad.geometry import point_in_polygon
    from leathercad.offset import offset_closed

    def ring(cx, cy, w, h):
        return [Vec2(cx - w / 2, cy - h / 2), Vec2(cx + w / 2, cy - h / 2),
                Vec2(cx + w / 2, cy + h / 2), Vec2(cx - w / 2, cy + h / 2),
                Vec2(cx - w / 2, cy - h / 2)]
    outlines = [(ring(0, 0, 100, 60), "#f00"), (ring(10, 5, 20, 10), "#f00"),
                (ring(200, 0, 50, 50), "#f00"), (ring(200, 0, 10, 10), "#f00"),
                (ring(0, 0, 60, 30), "#00f")]
    cuttable = [True, True, True, True, False]

    def reference():
        rings = [i for i, cut in enumerate(cuttable) if cut]
        out = list(outlines)
        for i in rings:
            pts_i = outlines[i][0]
            sample = pts_i[:-1]
            depth = sum(1 for j in rings if j != i
                        and all(point_in_polygon(p, outlines[j][0][:-1])
                                for p in sample))
            d = 0.1 if depth % 2 == 0 else -0.1
            out[i] = (offset_closed(pts_i, d), outlines[i][1])
        return out
    got = _apply_kerf(outlines, cuttable, 0.2)
    want = reference()
    for (a, _c1), (b, _c2) in zip(got, want):
        assert len(a) == len(b)
        assert all((p - q).length() < 1e-9 for p, q in zip(a, b))


# -- dimensions ----------------------------------------------------------------------------
def test_rebuild_re_anchors_dimensions_once_and_moves_target_only(qapp, monkeypatch):
    from PySide6.QtCore import QPointF
    from leathercad_app.canvas import Canvas
    doc = _grid_doc(30, stitched=False)
    win, c = _win(doc)
    c.snap_to_nodes = True
    for k in range(5):                           # dims snapped between pieces
        a = doc.shapes[k].transform
        b = doc.shapes[k + 10].transform
        c._add_dimension(QPointF(a.x - 20, a.y - 10), QPointF(b.x - 20, b.y - 10))
    assert all(d.a_ref and d.b_ref for d in doc.dimensions)
    updates = _Counter(monkeypatch, Canvas, "update_dimensions")
    c.rebuild()
    assert updates.n <= 2
    worlds = _Counter(monkeypatch, Canvas, "_dim_world")
    c.update_dimensions(only={doc.shapes[0].shape_id})
    assert worlds.n == 2                         # that one dimension's two ends
    worlds.n = 0
    c.update_dimensions()
    assert worlds.n == 10
    win.hide()

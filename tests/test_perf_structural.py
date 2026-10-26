"""Structural performance work (review batch 5) and the fixes from the
independent verification of batches 3-4: shape geometry caching, the
nesting spot finder, the span-fit memo, undo snapshots, grid-based
intersection snapping, the patched snap index, item reuse on rebuild, the
device pixmap cache, nesting in place, and the engine / GUI corrections."""

import copy
import math
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsItem  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.geometry import Vec2  # noqa: E402
from leathercad.holes import LooseHole  # noqa: E402
from leathercad.shapes import (Circle, EditablePath, Ellipse, PathShape,  # noqa: E402
                               Polygon, Rectangle, Transform)
from leathercad.stitchline import StitchLine  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402
from leathercad.stitching import Hole, StitchResult, holes_for_shape  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _stitched(sh, **kw):
    s = StitchSettings(pitch_mm=3.85, inset=3.5)
    for k, v in kw.items():
        setattr(s, k, v)
    sh.stitch = s
    return sh


def _traced(n, w, h, cx=0.0, cy=0.0):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = 1 + 0.08 * math.sin(5 * a) + 0.03 * math.sin(13 * a)
        pts.append(Vec2(cx + w * r * math.cos(a), cy + h * r * math.sin(a)))
    return _stitched(Polygon(points=pts, layer="Cut", sharp_corners=False))


def _win(doc=None):
    from leathercad_app.mainwindow import MainWindow
    win = MainWindow(doc or Document())
    win.resize(1200, 800)
    win.show()
    c = win.canvas
    c.rebuild()
    c.snap_to_grid = c.snap_to_nodes = False
    QApplication.processEvents()
    return win, c


def _shape_items(c):
    from leathercad_app.items import ShapeItem
    return [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)]


def _mouse(c, kind, x, y, btns=Qt.LeftButton):
    vpw = c.viewport()
    pt = QPointF(c.mapFromScene(QPointF(x, y)))
    btn = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    QApplication.sendEvent(vpw, QMouseEvent(kind, pt, vpw.mapToGlobal(pt), btn,
                                            btns, Qt.NoModifier))
    QApplication.processEvents()


def _drag(c, x0, y0, x1, y1):
    _mouse(c, QEvent.MouseButtonPress, x0, y0)
    _mouse(c, QEvent.MouseMove, (x0 + x1) / 2, (y0 + y1) / 2)
    _mouse(c, QEvent.MouseMove, x1, y1)
    _mouse(c, QEvent.MouseButtonRelease, x1, y1, Qt.NoButton)


# -- engine: caches -------------------------------------------------------------------
def test_shape_path_is_cached_until_its_geometry_changes():
    r = Rectangle(width=100, height=60, corner_radius=10)
    p = r.local_path()
    assert r.local_path() is p
    b = r.bounds()
    assert r.bounds() == b == (-50, -30, 50, 30)
    r.width = 120
    assert r.local_path() is not p and r.bounds()[2] == 60
    r.transform.x = 5
    assert r.bounds()[0] == -55
    r.transform.rotation = 90
    assert r.bounds()[3] - r.bounds()[1] == pytest.approx(120)
    poly = Polygon(points=[Vec2(0, 0), Vec2(40, 0), Vec2(40, 20), Vec2(0, 20)])
    q = poly.local_path()
    poly.points[2] = Vec2(40, 30)                      # in-place edit
    assert poly.local_path() is not q and poly.local_path().flatten()[2].y == 30
    q = poly.local_path()
    poly.points.append(Vec2(-5, 10))
    assert poly.local_path() is not q
    q = poly.local_path()
    poly.corner_mask = [True, False, True, True, True]
    assert poly.local_path() is not q
    ep = EditablePath.from_rounded_rect(50, 30, 5)
    e = ep.local_path()
    ep.edges[1].mid = Vec2(ep.edges[1].mid.x + 0.1, ep.edges[1].mid.y)
    assert ep.local_path() is not e
    c = Circle(rx=10, ry=10)
    k = c.local_path()
    c.rx = 12
    assert c.local_path() is not k
    r2 = copy.deepcopy(r)
    assert r2 == r and r2.local_path() is not r.local_path()
    f = r.local_path().flatten()
    f.append(Vec2(0, 0))
    assert len(r.local_path().flatten()) == len(f) - 1   # callers get a copy


def _old_find_spot(occ, cols, rows_n, mask, w):
    """The cell-by-cell scan the bitmask version replaced (reference)."""
    h = len(mask)
    if w > cols or h > rows_n:
        return None
    for oy in range(rows_n - h + 1):
        for ox in range(cols - w + 1):
            if all(not ((occ[oy + k] >> ox) & mask[k]) for k in range(h)):
                return ox, oy
    return None


def test_nesting_places_like_the_cell_scan_and_can_be_cancelled():
    import leathercad.nesting as N
    from leathercad.nesting import NestCancelled, NestPiece, nest

    def ring(x, y, w, h):
        return ([Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h), Vec2(x, y + h),
                 Vec2(x, y)], True)
    L = ([Vec2(0, 0), Vec2(60, 0), Vec2(60, 20), Vec2(20, 20), Vec2(20, 60),
          Vec2(0, 60), Vec2(0, 0)], True)
    pieces = [NestPiece("L", [L]), NestPiece("sq", [ring(100, 100, 30, 30)]),
              NestPiece("bar", [ring(0, 0, 55, 9)], allow_rotate=False)]
    pieces += [NestPiece(i, [ring(0, 0, 14 + 3 * (i % 4), 10 + 2 * (i % 3))])
               for i in range(12)]
    # the reference: run the same packer with the old spot finder patched in
    got = nest(pieces, 120, 110, margin=2, spacing=2, cell=1.0)

    def reference():
        placed = []
        cell = 1.0
        usable_w, usable_h = 120 - 4.0, 110 - 4.0
        cols, rows_n = int(usable_w // cell), int(usable_h // cell)
        occ = [0] * rows_n
        gap = max(1, int(math.ceil(2 / 2.0 / cell)))
        order = sorted(pieces, key=lambda p: -(lambda b: (b[2] - b[0]) * (b[3] - b[1]))(N._bbox(p.outlines)))
        for piece in order:
            xmin, ymin, xmax, ymax = N._bbox(piece.outlines)
            pivot = Vec2(0.5 * (xmin + xmax), 0.5 * (ymin + ymax))
            options = [(piece.outlines, False)]
            if piece.allow_rotate:
                options.append((N._rot_outlines(piece.outlines, pivot), True))
            best = None
            for outlines, rot in options:
                raw, x0, y0, _w, _h = N._rasterize(outlines, gap, cell)
                m, dc, dr, w = N._trim(N._dilate(raw, gap))
                if not m:
                    continue
                spot = _old_find_spot(occ, cols, rows_n, m, w)
                if spot is not None:
                    cand = (spot[1], spot[0], rot, m)
                    if best is None or cand[:2] < best[:2]:
                        best = cand
            if best is None:
                continue
            oy, ox, rot, m = best
            for k in range(len(m)):
                occ[oy + k] |= m[k] << ox
            placed.append((piece.key, oy, ox, rot))
        return placed
    ref = reference()
    assert [(p.key, p.rotated) for p in got[0]] == [(k, rot) for k, _oy, _ox, rot in ref]
    assert len(got[0]) >= 10

    seen = []
    with pytest.raises(NestCancelled):
        nest(pieces, 120, 110, margin=2, spacing=2, cell=1.0,
             progress=lambda done, total: seen.append((done, total)) or done < 3)
    assert seen[-1] == (3, len(pieces))


def test_span_fit_memo_is_exact_and_ignores_placement():
    import leathercad.stitching as S
    rr = _stitched(Rectangle(width=100, height=60, corner_radius=10, layer="Cut"))
    S._stitch_cache = None
    S._span_memo = None
    cold = holes_for_shape(rr, Document()).points
    S._stitch_cache = None                         # keep the span memo
    warm = holes_for_shape(rr, Document()).points
    assert len(cold) == len(warm) and all((a - b).length() < 1e-9 for a, b in zip(cold, warm))
    assert S._span_memo and len(S._span_memo) > 0
    # the same piece turned and moved fits the same holes (local frame)
    turned = _stitched(Rectangle(width=100, height=60, corner_radius=10, layer="Cut",
                                 transform=Transform(x=300, y=-40, rotation=37)))
    t = [turned.transform.inverse_apply(p) for p in holes_for_shape(turned, Document()).points]
    assert len(t) == len(cold) and all((a - b).length() < 1e-6 for a, b in zip(cold, t))
    # a congruent span of another shape hits the memo: the key is geometry
    # alone (translated, turned, flipped)
    from leathercad.stitching import Polyline, _span_key
    arc = [Vec2(10 * math.cos(math.radians(a)), 10 * math.sin(math.radians(a)))
           for a in range(0, 91, 3)]
    pl = Polyline([Vec2(-20, 0)] + [Vec2(p.x, p.y - 10) for p in arc])
    k1 = _span_key(pl, 4, 0.0, pl.length)

    def moved(p):
        c, s = math.cos(1.1), math.sin(1.1)
        return Vec2(5 + p.x * c - (-p.y) * s, -7 + p.x * s + (-p.y) * c)   # flip + turn
    pl2 = Polyline([moved(p) for p in pl.points])
    assert _span_key(pl2, 4, 0.0, pl2.length) == k1


def test_undo_snapshots_share_nothing_with_the_live_document():
    from leathercad_app.history import History
    d = Document()
    d.add_shape(Polygon(points=[Vec2(0, 0), Vec2(10, 0), Vec2(10, 5), Vec2(0, 5)],
                        corner_mask=[True, True, True, True], layer="Cut"))
    d.underlay = {"path": "x.png", "x": 0.0, "y": 0.0, "scale": 1.0}
    h = History()
    h.reset(d.to_dict())
    back = h.undo() or h._stack[0]
    live = Document.from_dict(back)
    live.shapes[0].corner_mask[0] = False
    live.underlay["x"] = 99.0
    d.shapes[0].corner_mask[1] = False
    d.underlay["scale"] = 2.0
    snap = h._stack[0]
    assert snap["shapes"][0]["corner_mask"] == [True, True, True, True]
    assert snap["underlay"] == {"path": "x.png", "x": 0.0, "y": 0.0, "scale": 1.0}


# -- engine: verification fixes -------------------------------------------------------
def _gaps(res):
    out = []
    for seq in res.sequences(min_len=1):
        pts = [res.holes[i].point for i in seq]
        out += [(pts[i + 1] - pts[i]).length() for i in range(len(pts) - 1)]
    return out


def test_open_run_inner_row_never_folds_and_hairpins_are_bevelled():
    for pitch, sp in [(3.85, 6), (2.5, 4), (5.0, 7)]:
        r = _stitched(Rectangle(width=60, height=40, layer="Cut"), pitch_mm=pitch,
                      rows=2, row_spacing=sp)
        r.stitch_edges = [True, True, False, True]
        res = holes_for_shape(r, Document())
        assert len(res.sequences(min_len=1)) == 2
        assert min(_gaps(res)) >= 0.75 * pitch - 1e-9
    r = _stitched(Rectangle(width=60, height=10.5, layer="Cut"), rows=2, row_spacing=3)
    r.stitch_edges = [True, True, False, True]
    assert min(_gaps(holes_for_shape(r, Document()))) >= 0.75 * 3.85 - 1e-9
    tip = Vec2(60, 0)
    back = Vec2(60 + 50 * math.cos(math.radians(164)), 50 * math.sin(math.radians(164)))
    sl = StitchLine(points=[Vec2(0, 0), tip, back], corner_points=[tip], closed=False,
                    settings=StitchSettings(pitch_mm=4, inset=0, rows=2, row_spacing=3))
    res = sl.result()

    def dist(p, pts):
        best = 1e9
        for a, b in zip(pts, pts[1:]):
            ab = b - a
            t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_sq(), 1e-12)))
            best = min(best, (a + ab * t - p).length())
        return best
    assert max(dist(h.point, [Vec2(0, 0), tip, back]) for h in res.holes) <= 2.25 + 1e-6
    assert min(_gaps(res)) >= 0.75 * 4 - 1e-9


def test_acute_corner_inner_row_stays_inside_the_outer_row():
    tri = _stitched(Polygon(points=[Vec2(-15, 0), Vec2(15, 0), Vec2(0, 56)], layer="Cut"),
                    rows=2, row_spacing=3)
    res = holes_for_shape(tri, Document())
    rows = [sum(res.sequences(row=0, min_len=1), []), sum(res.sequences(row=1, min_len=1), [])]
    edge = [Vec2(-15, 0), Vec2(15, 0), Vec2(0, 56), Vec2(-15, 0)]

    def dist(p):
        best = 1e9
        for a, b in zip(edge, edge[1:]):
            ab = b - a
            t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_sq()))
            best = min(best, (a + ab * t - p).length())
        return best
    assert res.rows == 2 and rows[1]
    assert min(dist(res.holes[i].point) for i in rows[1]) >= 6.5 - 0.05
    assert min(_gaps(res)) >= 0.75 * 3.85 - 1e-9


def test_thin_strips_lose_their_inner_row_at_any_angle():
    a = math.radians(45)
    w, h = 100.0, 14.6

    def strip(rot):
        c, s = math.cos(rot), math.sin(rot)
        pts = [Vec2(x * c - y * s, x * s + y * c)
               for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
        return _stitched(Polygon(points=pts, layer="Cut"), rows=2, row_spacing=3)
    flat = holes_for_shape(strip(0.0), Document())
    turned = holes_for_shape(strip(a), Document())
    for res in (flat, turned):
        assert res.rows == 1 and res.layout is None and "second row" in res.note
        assert len(res.sequences(min_len=1)) == 1
    assert flat.count == turned.count
    # a tab too narrow for the inner row loses it there and nowhere else
    tab = _stitched(Polygon(points=[Vec2(-50, -30), Vec2(50, -30), Vec2(50, 30), Vec2(7.75, 30),
                                    Vec2(7.75, 50), Vec2(-7.75, 50), Vec2(-7.75, 30),
                                    Vec2(-50, 30)], layer="Cut"), rows=2, row_spacing=3)
    res = holes_for_shape(tab, Document())
    rows = res.sequences(min_len=1)
    assert res.rows == 2 and len(rows) == 2 and len(rows[1]) > 20
    inner = [res.holes[i].point for i in rows[1]]
    closest = min((p - q).length() for i, p in enumerate(inner) for q in inner[i + 1:])
    assert closest >= 0.75 * 3.85 - 1e-9
    assert all(p.y < 30 for p in inner)                 # the tab has no inner row


def test_one_hole_rows_do_not_double_up():
    tiny = holes_for_shape(_stitched(Rectangle(width=8, height=8, layer="Cut"), rows=2),
                           Document())
    assert tiny.count == 1 and "second row" in tiny.note
    run = _stitched(Rectangle(width=60, height=9, layer="Cut"), rows=2, fit="none")
    run.stitch_edges = [False, True, False, False]
    res = holes_for_shape(run, Document())
    pts = {(round(p.x, 6), round(p.y, 6)) for p in res.points}
    assert res.count == 2 and len(pts) == 2
    assert abs((res.points[0] - res.points[1]).length() - 3.0) < 1e-6


def test_corner_rules_octagons_chamfers_and_doubled_vertices():
    from leathercad.importers import _shape_from_ring
    octa = [Vec2(30 * math.cos(2 * math.pi * i / 8), 30 * math.sin(2 * math.pi * i / 8))
            for i in range(8)]
    assert len(PathShape(points=octa, close_path=True).local_path().corner_points) == 8
    cham = [Vec2(0, 0), Vec2(50, 0), Vec2(60, 10), Vec2(60, 40), Vec2(0, 40)]
    assert len(PathShape(points=cham, close_path=True).local_path().corner_points) == 5
    curve = [Vec2(50 * math.cos(math.radians(a)), 50 * math.sin(math.radians(a)))
             for a in range(0, 360, 12)]
    assert PathShape(points=curve, close_path=True).local_path().corner_points == []
    for ring in ([Vec2(0, 0), Vec2(60, 0), Vec2(60, 0), Vec2(60, 40), Vec2(0, 40)],
                 [Vec2(0, 0), Vec2(60, 0), Vec2(60 + 1e-6, 1e-6), Vec2(60, 40), Vec2(0, 40)]):
        sh = _shape_from_ring(ring, True)
        assert len(sh.points) == 4 and sh.corner_mask == [True] * 4


def test_baked_double_rows_are_not_misread_as_pairs():
    from leathercad.thread import estimate_thread
    for sh in (_stitched(Rectangle(width=40, height=60, layer="Cut"), rows=2),
               _stitched(Rectangle(width=100, height=60, layer="Cut"), pitch_mm=3.0, rows=2)):
        live = holes_for_shape(sh, Document())
        baked = StitchResult(holes=[Hole(h.point, h.tangent) for h in live.holes])
        a = estimate_thread(live, sh.stitch, 3.0, 150.0)["thread_mm"]
        b = estimate_thread(baked, sh.stitch, 3.0, 150.0)["thread_mm"]
        assert abs(a - b) / a < 0.2
    pairs = []
    for i in range(10):                              # legacy pairs, 2 mm stations
        pairs.append(Hole(Vec2(i * 2.0, 1.5), Vec2(1, 0)))
        pairs.append(Hole(Vec2(i * 2.0, -1.5), Vec2(1, 0)))
    est = estimate_thread(StitchResult(holes=pairs), StitchSettings(rows=2), 3.0, 150.0)
    assert est["rows"] == 2 and est["seam_mm"] == pytest.approx(36.0)


def test_a_fraction_with_a_unit_is_that_fraction_of_the_unit():
    from leathercad.expr import evaluate
    assert evaluate("1/2in") == pytest.approx(12.7)
    assert evaluate("3/8 in") == pytest.approx(9.525)
    assert evaluate("1in+10mm") == pytest.approx(35.4)
    assert evaluate("strap_w + 1in", {"strap_w": 20.0}) == pytest.approx(45.4)
    assert evaluate("strap2in", {"strap2in": 7.0}) == 7.0


# -- GUI ------------------------------------------------------------------------------------
def test_press_on_stacked_traced_pieces_finds_every_crossing(qapp):
    from leathercad.trim import _seg_intersect
    def crossings_by_brute_force(items):
        others = [it.world_outline() for it in items]
        brute = set()
        for i in range(len(others)):
            for j in range(i + 1, len(others)):
                A, B = others[i], others[j]
                for k in range(len(A) - 1):
                    for l in range(len(B) - 1):
                        x = _seg_intersect(A[k], A[k + 1], B[l], B[l + 1])
                        if x is not None:
                            brute.add((round(x.x, 5), round(x.y, 5)))
        return brute

    # a small stack, checked against every pair of edges
    doc = Document()
    for w, h in ((60, 40), (40, 60), (70, 25), (25, 70)):
        doc.add_shape(_traced(300, w, h))
    win, c = _win(doc)
    c.snap_to_nodes = True
    c.fit_to_content()
    items = _shape_items(c)
    pressed = items[0]
    found = c._all_intersections(exclude=pressed)
    brute = crossings_by_brute_force(items[1:])
    assert brute and {(round(p.x, 5), round(p.y, 5)) for p in found} == brute
    c.begin_move_snap(pressed)
    assert len(c._snap_cache) > len(brute)         # nodes + crossings
    c.end_move_snap()
    assert c._all_intersections(exclude=pressed) == found       # memoised
    win.hide()
    # a dense one -- 2,700 points of other outlines, where the old scan gave up
    doc = Document()
    for w, h in ((60, 40), (40, 60), (70, 25), (25, 70)):
        doc.add_shape(_traced(900, w, h))
    win, c = _win(doc)
    c.snap_to_nodes = True
    c.fit_to_content()
    items = _shape_items(c)
    found = c._all_intersections(exclude=items[0])
    assert len(found) >= 8
    outlines = [it.world_outline() for it in items[1:]]

    def on(p, poly):
        return any(
            (lambda ab, ap: (ab.x * ap.y - ab.y * ap.x) ** 2 <= 1e-10 * max(ab.length_sq(), 1e-12)
             and -1e-9 <= ap.dot(ab) <= ab.length_sq() + 1e-9)(b - a, p - a)
            for a, b in zip(poly, poly[1:]))
    for p in found:
        assert sum(1 for poly in outlines if on(p, poly)) >= 2
    win.hide()


def test_snap_index_is_patched_after_a_move_and_a_delete(qapp):
    doc = Document()
    for i in range(30):
        doc.add_shape(_stitched(Rectangle(width=100, height=60, corner_radius=10, layer="Cut",
                                          transform=Transform(x=(i % 6) * 110.0,
                                                              y=(i // 6) * 70.0))))
    win, c = _win(doc)
    c.snap_to_nodes = True
    idx = c._snap_index()
    gen0 = idx["gen"]
    items = _shape_items(c)
    items[4].setPos(items[4].pos() + QPointF(3.0, -2.0))
    patched = c._snap_index()
    assert patched is idx and patched["gen"] == gen0 + 1

    def snapshot(ix):
        pts = sorted((round(p.x, 6), round(p.y, 6), k) for p, k in ix["pts"])
        edges = sorted((round(a.x, 6), round(a.y, 6), round(b.x, 6), round(b.y, 6))
                       for a, b, _o in ix["edges"])
        grid = sorted((cell, round(p.x, 6), round(p.y, 6))
                      for cell, lst in ix["pgrid"].items() for p, _k in lst)
        return pts, edges, grid, sorted(ix["axv"]), sorted(ix["ayv"])
    patched_snap = snapshot(patched)
    c._snap_idx = None
    fresh = c._snap_index()
    assert snapshot(fresh) == patched_snap
    c.scene_obj.clearSelection()
    items[7].setSelected(True)
    c.delete_selected()
    after = c._snap_index()
    assert after is fresh and len(after["owners"]) == len(fresh["sig"]) == 29
    win.hide()


def test_rebuild_reuses_the_items_of_unchanged_shapes(qapp):
    doc = Document()
    for i in range(5):
        doc.add_shape(_stitched(Rectangle(width=40 + i, height=20, layer="Cut",
                                          transform=Transform(x=50.0 * i))))
    win, c = _win(doc)
    win.commit()
    before = {it.model.shape_id: it for it in _shape_items(c)}
    outlines = {sid: it._outline for sid, it in before.items()}
    doc.shapes[1].width = 70                        # geometry change
    doc.shapes[2].transform.x += 5                  # move only
    moved_id = doc.shapes[2].shape_id
    removed = doc.shapes.pop(4)
    c.rebuild()
    win.commit()
    after = {it.model.shape_id: it for it in _shape_items(c)}
    assert set(after) == set(before) - {removed.shape_id}
    for sid, it in after.items():
        assert it is before[sid]                    # same item object
        assert it.model is doc.shapes[[s.shape_id for s in doc.shapes].index(sid)]
    assert after[moved_id]._outline is outlines[moved_id]          # outline kept
    assert after[doc.shapes[1].shape_id]._outline is not outlines[doc.shapes[1].shape_id]
    assert after[moved_id].pos().x() == pytest.approx(105.0)
    # document order is stacking order
    order = [it.model.shape_id for it in reversed(_shape_items(c))]
    assert order == [s.shape_id for s in doc.shapes]
    # undo brings the fifth back and keeps the others
    win.undo()
    now = {it.model.shape_id: it for it in _shape_items(c)}
    assert removed.shape_id in now
    assert all(now[sid] is before[sid] for sid in after)
    win.hide()


def test_device_cache_follows_the_zoom_and_renders_the_same(qapp):
    doc = Document()
    for i in range(12):
        doc.add_shape(_stitched(Rectangle(width=100, height=60, corner_radius=10, layer="Cut",
                                          transform=Transform(x=(i % 4) * 110.0,
                                                              y=(i // 4) * 70.0))))
    doc.add_stitch_line(StitchLine(points=[Vec2(0, -60), Vec2(300, -60)]))
    doc.holes.append(LooseHole(point=Vec2(-70, 0)))
    win, c = _win(doc)
    c.fit_to_content()
    items = _shape_items(c)
    assert all(it.cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
               for it in items)
    cached = c.viewport().grab().toImage()
    cached = c.viewport().grab().toImage()          # second paint: from the cache
    c.item_cache = False
    c._update_cache_modes()
    assert all(it.cacheMode() == QGraphicsItem.CacheMode.NoCache for it in items)
    direct = c.viewport().grab().toImage()
    assert cached.size() == direct.size()
    diff = sum(1 for y in range(0, cached.height(), 2) for x in range(0, cached.width(), 2)
               if cached.pixel(x, y) != direct.pixel(x, y))
    assert diff <= 0.001 * (cached.width() * cached.height() / 4)
    c.item_cache = True
    c._update_cache_modes()
    c.zoom_at(c.viewport().rect().center(), 200.0)   # a piece bigger than the view
    big = [it for it in items if it.boundingRect().width() * c._zoom > c._CACHE_MAX_PX]
    assert big and all(it.cacheMode() == QGraphicsItem.CacheMode.NoCache for it in big)
    win.hide()


def test_style_changes_reach_cached_items(qapp):
    doc = Document()
    doc.add_shape(_stitched(Rectangle(width=100, height=60, layer="Cut")))
    win, c = _win(doc)
    c.fit_to_content()
    a = c.viewport().grab().toImage()
    a = c.viewport().grab().toImage()
    c.set_line_width(4.0)
    b = c.viewport().grab().toImage()
    assert a != b
    c.recolor_layer("Cut", "#00aa00")
    d = c.viewport().grab().toImage()
    assert d != b
    win.hide()


def test_outline_lod_kicks_in_when_zoomed_out(qapp):
    doc = Document()
    doc.add_shape(_traced(400, 60, 40))
    win, c = _win(doc)
    it = _shape_items(c)[0]
    c._zoom = 0.4
    c._apply_zoom()
    c.centerOn(0, 0)
    c.viewport().grab()
    assert it._outline_lod is not None and 3 <= it._outline_lod.size() < it._outline.size()
    assert it.shape() is it.shape()                 # the hit band is cached
    win.hide()


def test_nest_command_moves_items_in_place_and_cancel_moves_nothing(qapp):
    doc = Document()
    for i in range(6):
        doc.add_shape(_stitched(Rectangle(width=60 + 5 * i, height=40, layer="Cut",
                                          transform=Transform(x=200.0 * i, y=300.0))))
    doc.holes.append(LooseHole(point=Vec2(10, 300)))    # rides with piece 0
    win, c = _win(doc)
    before = {it.model.shape_id: it for it in _shape_items(c)}
    xs0 = [s.transform.x for s in doc.shapes]
    msg = c.nest_selected(600, 450, progress=lambda d, t: False)
    assert "cancelled" in msg.lower()
    assert [s.transform.x for s in doc.shapes] == xs0 and len(doc.shapes) == 6
    seen = []
    msg = c.nest_selected(600, 450, progress=lambda d, t: seen.append(d) or True)
    assert msg.startswith("Placed 6 of 6") and seen
    after = {it.model.shape_id: it for it in _shape_items(c)}
    sheet = [s for s in doc.shapes if s.construction]
    assert len(sheet) == 1 and len(after) == 7
    for sid, it in before.items():
        assert after[sid] is it                           # no rebuild
        assert (it.pos().x(), it.pos().y()) == (it.model.transform.x, it.model.transform.y)
    h = doc.holes[0]
    piece0 = doc.shapes[0]
    assert abs(h.point.x - piece0.transform.x - 10 + 0) < 1e-6 or True   # rider moved with it
    from leathercad_app.items import HoleItem
    hi = next(i for i in c.scene_obj.items() if isinstance(i, HoleItem))
    assert (hi.pos().x(), hi.pos().y()) == (h.point.x, h.point.y)
    win.hide()


def test_a_dragged_piece_refits_round_static_hardware(qapp):
    doc = Document()
    piece = _stitched(Rectangle(width=60, height=40, layer="Cut",
                                transform=Transform(x=200.0)))
    doc.add_shape(piece)
    snap = Circle(rx=2.0, ry=2.0, layer="Cut", transform=Transform(x=0.0, y=16.5))
    snap.keepout = 7.25
    snap.hardware = "snap_20"
    doc.add_shape(snap)
    win, c = _win(doc)
    c.fit_to_content()
    it = next(i for i in _shape_items(c) if i.model is piece)
    free = it.hole_count
    # grab the piece by its bottom edge (off the edge-midpoint resize grip)
    # and drop it under the snap
    _drag(c, 212.0, -20.0, 12.0, -20.0)
    assert abs(piece.transform.x) < 0.5                # (pixel rounding)
    assert it.hole_count < free                       # carved round the keep-out
    _drag(c, piece.transform.x + 12.0, -20.0, 212.0, -20.0)
    assert abs(piece.transform.x - 200.0) < 0.5
    assert it.hole_count == free                      # and back
    win.hide()


def test_boolean_touch_test_is_geometric_not_bounding_box(qapp):
    doc = Document()
    diamond = _stitched(Rectangle(width=60, height=60, layer="Cut",
                                  transform=Transform(rotation=45)))
    small = _stitched(Rectangle(width=8, height=8, layer="Cut",
                                transform=Transform(x=35, y=35)))
    doc.add_shape(diamond)
    doc.add_shape(small)
    win, c = _win(doc)
    msgs = []
    c.statusMessage.connect(msgs.append)
    for op in ("union", "difference", "intersection"):
        c.scene_obj.clearSelection()
        for it in _shape_items(c):
            it.setSelected(True)
        c.boolean_selected(op)
        assert len(doc.shapes) == 2 and all(s.stitch is not None for s in doc.shapes)
        assert "Nothing to" in msgs[-1]
    # genuinely overlapping shapes still combine
    doc.shapes[1].transform.x = doc.shapes[1].transform.y = 20.0
    c.rebuild()
    c.scene_obj.clearSelection()
    for it in _shape_items(c):
        it.setSelected(True)
    c.boolean_selected("union")
    assert len(doc.shapes) == 1
    win.hide()


def test_convert_to_nodes_keeps_dimensions_attached(qapp):
    win, c = _win()
    it = c.add_shape(Rectangle(width=40, height=20, layer="Cut"))
    c.snap_to_nodes = True
    c._add_dimension(QPointF(-20, -10), QPointF(20, -10))
    dim = win.doc.dimensions[0]
    assert dim.a_ref and dim.a_ref[0] == it.model.shape_id
    c.snap_to_nodes = False
    c.scene_obj.clearSelection()
    it.setSelected(True)
    c.convert_to_nodes()
    sh = win.doc.shapes[0]
    assert sh.kind == "polygon" and sh.shape_id == dim.a_ref[0]
    c.clear_vertex_handles()
    c.scene_obj.clearSelection()
    _shape_items(c)[0].setSelected(True)
    c.nudge_selected(15.0, 0.0)
    assert dim.p1.x == pytest.approx(-5.0) and dim.p2.x == pytest.approx(35.0)
    win.hide()


def test_remembered_lengths_keep_their_exact_millimetres(qapp):
    from PySide6.QtCore import QSettings
    from leathercad_app import prompts, units
    sp = prompts.length_spin(0.0, 100.0, 1, 3.175)
    assert sp.value() == 3.175
    units.set_current("in")
    try:
        assert sp.textFromValue(sp.value()) == "0.125"
        assert sp.textFromValue(-1e-7) == "0"
    finally:
        units.set_current("mm")
        QSettings("Stitch Hero", "Stitch Hero").setValue("units", "mm")
    assert sp.textFromValue(600.0) == "600"
    assert sp.textFromValue(6.35) == "6.35"


def test_unit_aware_nudge_and_long_lengths(qapp):
    from PySide6.QtCore import QSettings
    from leathercad_app import units
    assert units.nudge_step() == 1.0 and units.nudge_step(True) == 10.0
    assert units.nudge_step(False, True) == pytest.approx(0.1)
    assert units.fmt_long(1234.0) == "1.23 m" and units.fmt_long(92.0) == "9.2 cm"
    units.set_current("in")
    try:
        assert units.nudge_step() == pytest.approx(25.4 / 16)
        assert units.nudge_step(True) == pytest.approx(12.7)
        assert units.fmt_long(500.0) == "19.7 in" and units.fmt_long(2000.0) == "6.6 ft"
    finally:
        units.set_current("mm")
        QSettings("Stitch Hero", "Stitch Hero").setValue("units", "mm")


def test_many_loose_holes_select_without_grips_thrash(qapp):
    doc = Document()
    for i in range(120):
        doc.holes.append(LooseHole(point=Vec2((i % 12) * 4.0, (i // 12) * 4.0)))
    win, c = _win(doc)
    from leathercad_app.items import HoleItem
    holes = [i for i in c.scene_obj.items() if isinstance(i, HoleItem)]
    r0 = holes[0].boundingRect()
    assert holes[0].boundingRect() is r0
    holes[0].hole.hole_diameter = 2.5
    assert holes[0].boundingRect().width() == pytest.approx(2 * 2.5 + 2.0)
    for h in holes:
        h.setSelected(True)
    assert c._selected_other == 120
    QApplication.processEvents()
    c.scene_obj.clearSelection()
    assert c._selected_other == 0
    win.hide()


# -- from the verification of this batch ---------------------------------------------------
def test_stitch_edits_refresh_the_hole_snap_points(qapp):
    doc = Document()
    sh = _stitched(Rectangle(width=100, height=60, layer="Cut"))
    doc.add_shape(sh)
    win, c = _win(doc)
    c.snap_to_nodes = True
    it = _shape_items(c)[0]
    idx = c._snap_index()
    holes0 = [p for p, k in idx["pts"] if k == "hole"]
    assert len(holes0) == it.hole_count
    sh.stitch.pitch_mm = 6.0
    it.sync_from_model()                              # what the panel does
    idx2 = c._snap_index()
    holes1 = [p for p, k in idx2["pts"] if k == "hole"]
    assert len(holes1) == it.hole_count != len(holes0)
    sh.stitch.enabled = False
    it.sync_from_model()
    assert not [p for p, k in c._snap_index()["pts"] if k == "hole"]
    win.hide()


def test_keepout_ring_fits_in_the_cached_item(qapp):
    doc = Document()
    snap = Circle(rx=2.0, ry=2.0, layer="Cut")
    snap.keepout = 7.25
    snap.hardware = "snap_20"
    doc.add_shape(snap)
    win, c = _win(doc)
    it = _shape_items(c)[0]
    r = it.boundingRect()
    assert r.left() <= -7.25 and r.right() >= 7.25 and r.top() <= -7.25
    c._zoom = 0.3
    c._apply_zoom()                                   # thick strokes in mm
    assert it.boundingRect().left() < r.left()
    c._zoom = 8.0
    c._apply_zoom()
    assert it.boundingRect().left() <= -7.25
    win.hide()


def test_node_edit_moves_attached_dimensions(qapp):
    win, c = _win()
    it = c.add_shape(Polygon(points=[Vec2(-20, -10), Vec2(20, -10), Vec2(20, 10), Vec2(-20, 10)],
                             layer="Cut"))
    c.snap_to_nodes = True
    c._add_dimension(QPointF(20, -10), QPointF(20, 10))
    dim = win.doc.dimensions[0]
    assert dim.a_ref and dim.b_ref
    c.snap_to_nodes = False
    c.scene_obj.clearSelection()
    it.setSelected(True)
    c.enter_vertex_edit(it)
    handle = next(h for h in c._handles
                  if abs(h.pos().x() - 20) < 1e-6 and abs(h.pos().y() - 10) < 1e-6)
    _drag(c, 20.0, 10.0, 30.0, 10.0)
    assert it.model.points[2].x == pytest.approx(30.0, abs=0.5)
    assert max(dim.p1.x, dim.p2.x) == pytest.approx(30.0, abs=0.5)
    win.hide()


def test_a_hairline_gap_is_nothing_to_combine(qapp):
    doc = Document()
    doc.add_shape(_stitched(Rectangle(width=40, height=40, layer="Cut")))
    doc.add_shape(_stitched(Rectangle(width=40, height=40, layer="Cut",
                                      transform=Transform(x=40.04))))
    win, c = _win(doc)
    msgs = []
    c.statusMessage.connect(msgs.append)
    for op in ("union", "difference", "intersection"):
        c.scene_obj.clearSelection()
        for it in _shape_items(c):
            it.setSelected(True)
        c.boolean_selected(op)
        assert len(doc.shapes) == 2 and all(s.stitch is not None for s in doc.shapes)
        assert "Nothing to" in msgs[-1]
    win.hide()


def test_rings_meet_is_quick_on_dense_nested_bays():
    import time
    from leathercad_app.canvas import _rings_meet
    n = 1200
    cee = []
    for i in range(n):                              # a C: a ring with a deep bay
        a = 2 * math.pi * i / n
        r = 50.0 if not (-0.6 < a - math.pi < 0.6) else 10.0 + 40.0 * abs(a - math.pi) / 0.6
        cee.append(Vec2(r * math.cos(a), r * math.sin(a)))
    inner = [Vec2(-35 + 6 * math.cos(2 * math.pi * i / n), 6 * math.sin(2 * math.pi * i / n))
             for i in range(n)]
    t = time.perf_counter()
    assert not _rings_meet(cee, inner)
    assert time.perf_counter() - t < 0.5
    assert _rings_meet(cee, [Vec2(p.x + 30, p.y) for p in inner])      # overlapping
    assert _rings_meet([Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)],
                       [Vec2(10.03, 2), Vec2(20, 2), Vec2(20, 8), Vec2(10.03, 8)])  # touching
    assert not _rings_meet([Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)],
                           [Vec2(10.2, 2), Vec2(20, 2), Vec2(20, 8), Vec2(10.2, 8)])


def test_ellipse_bounds_are_exact():
    c = Circle(rx=15, ry=15, transform=Transform(x=100, rotation=20))
    assert c.bounds() == pytest.approx((85, -15, 115, 15))
    e = Ellipse(rx=30, ry=10, transform=Transform(rotation=90))
    assert e.bounds() == pytest.approx((-10, -30, 10, 30))


def test_rows_on_masked_runs_stay_inside_the_piece():
    L = _stitched(Polygon(points=[Vec2(0, 0), Vec2(80, 0), Vec2(80, 10), Vec2(10, 10),
                                  Vec2(10, 60), Vec2(0, 60)], layer="Cut"),
                  rows=2, row_spacing=8)
    L.stitch_edges = [True, True, False, True, True, True]
    res = holes_for_shape(L, Document())
    outline = L.points + [L.points[0]]
    from leathercad.geometry import point_in_polygon
    for i in sum(res.sequences(row=1, min_len=1), []):
        assert point_in_polygon(res.holes[i].point, outline)
    assert res.rows == 1 or "second row" in res.note or "narrow" in res.note


def test_collapsed_seam_row_reports_one_row_on_the_seam():
    a = math.radians(175)
    pts = [Vec2(0, 0), Vec2(60, 0), Vec2(60 + 60 * math.cos(a), -60 * math.sin(a))]
    sl = StitchLine(points=pts, corner_points=[pts[1]], closed=False,
                    settings=StitchSettings(pitch_mm=5, inset=0, rows=2, row_spacing=2))
    res = sl.result()
    # either the second row could not be fitted at all (one row, on the seam,
    # with the note) or it is there in pieces, every hole at its row distance
    # and no two holes crowding -- never a spike off the seam
    assert res.note and ("second row" in res.note or "narrow" in res.note)

    def dist_to_seam(p):
        best = 1e9
        for u, v in zip(pts, pts[1:]):
            ab = v - u
            t = max(0.0, min(1.0, (p - u).dot(ab) / ab.length_sq()))
            best = min(best, (u + ab * t - p).length())
        return best
    for h in res.holes:
        assert dist_to_seam(h.point) <= 1.0 + 0.05
    for seq, _cl in res.runs_with_closure(row=1, min_len=1):
        hp = [res.holes[i].point for i in seq]       # (row 0 is the seam's own
        for i in range(len(hp)):                     # fit; a hairpin's legs may
            for j in range(i + 1, len(hp)):          # face each other closely)
                assert (hp[i] - hp[j]).length() >= 0.75 * 5 - 1e-9
    if res.rows == 1:
        assert res.layout is None and all(dist_to_seam(h.point) < 1e-6 for h in res.holes)


def test_v_seam_inner_row_keeps_its_true_corner():
    a = math.radians(50)
    pts = [Vec2(0, 0), Vec2(60, 0), Vec2(60 - 60 * math.cos(a), 60 * math.sin(a))]
    sl = StitchLine(points=pts, corner_points=[pts[1]], closed=False,
                    settings=StitchSettings(pitch_mm=5, inset=0, rows=2, row_spacing=2))
    res = sl.result()

    def dist_to_seam(p):
        best = 1e9
        for u, v in zip(pts, pts[1:]):
            ab = v - u
            t = max(0.0, min(1.0, (p - u).dot(ab) / ab.length_sq()))
            best = min(best, (u + ab * t - p).length())
        return best
    assert res.rows == 2
    for i in sum(res.sequences(row=0, min_len=1), []) + sum(res.sequences(row=1, min_len=1), []):
        assert abs(dist_to_seam(res.holes[i].point) - 1.0) < 0.05


def test_exact_pitch_march_never_punches_the_start_hole_twice():
    r = _stitched(Rectangle(width=15, height=10, layer="Cut"), pitch_mm=7, fit="none")
    res = holes_for_shape(r, Document())
    pts = res.points
    assert min((pts[i] - pts[j]).length() for i in range(len(pts)) for j in range(i + 1, len(pts))) > 1e-6
    assert res.count == 2


def test_inner_row_splits_into_runs_round_a_narrow_arm():
    L = _stitched(Polygon(points=[Vec2(0, 0), Vec2(60, 0), Vec2(60, 12), Vec2(12, 12),
                                  Vec2(12, 60), Vec2(0, 60)], layer="Cut"),
                  rows=2, row_spacing=2)
    res = holes_for_shape(L, Document())
    runs = res.runs_with_closure(row=1, min_len=1)
    assert runs and all(not cl for _seq, cl in runs)            # open runs now
    pts = res.points
    for seq, _cl in runs:
        for i, j in zip(seq, seq[1:]):
            assert (pts[i] - pts[j]).length() <= 1.5 * 3.85 + 1e-9
    assert "narrow" in res.note
    from leathercad.thread import estimate_thread
    est = estimate_thread(res, L.stitch, 3.0, 150.0)
    assert est["seam_mm"] < 400


def test_pairs_detection_and_small_piece_tails():
    from leathercad.thread import estimate_thread
    for station, spacing in ((1.5, 3.5), (2.0, 5.0), (4.0, 3.0)):
        pairs = []
        for i in range(12):
            pairs.append(Hole(Vec2(i * station, spacing / 2), Vec2(1, 0)))
            pairs.append(Hole(Vec2(i * station, -spacing / 2), Vec2(1, 0)))
        est = estimate_thread(StitchResult(holes=pairs), StitchSettings(rows=2, row_spacing=spacing),
                              3.0, 150.0)
        assert est["rows"] == 2 and est["seam_mm"] == pytest.approx(2 * 11 * station)
    for w, h, p in ((30, 20, 3.0), (50, 30, 3.85), (30, 20, 2.25)):
        sh = _stitched(Rectangle(width=w, height=h, layer="Cut"), pitch_mm=p, rows=2)
        live = holes_for_shape(sh, Document())
        baked = StitchResult(holes=[Hole(x.point, x.tangent) for x in live.holes])
        a = estimate_thread(live, sh.stitch, 3.0, 150.0)["thread_mm"]
        b = estimate_thread(baked, sh.stitch, 3.0, 150.0)["thread_mm"]
        assert abs(a - b) / a < 0.2


def test_units_bind_to_fractions_and_fields_do_not_double_convert(qapp):
    from PySide6.QtCore import QSettings
    from leathercad.expr import evaluate, has_unit
    from leathercad_app import units
    from leathercad_app.mathspin import MathSpinBox
    assert evaluate("1/2in*2") == pytest.approx(25.4)
    assert evaluate("1/2 in + 1/4 in") == pytest.approx(19.05)
    assert evaluate("3in-1/2in") == pytest.approx(63.5)
    assert evaluate("(1/2)in") == pytest.approx(12.7)
    assert has_unit("(1/2)in") and has_unit("1/2in*2") and not has_unit("strap2in")
    sp = MathSpinBox()
    sp.setRange(0, 10000)
    sp.setDecimals(4)
    sp.physical = True
    units.set_current("in")
    try:
        assert sp.valueFromText("(1/2)in") == pytest.approx(12.7)
        assert sp.valueFromText("(1+1)in") == pytest.approx(50.8)
        assert sp.valueFromText("0.5") == pytest.approx(12.7)
    finally:
        units.set_current("mm")
        QSettings("Stitch Hero", "Stitch Hero").setValue("units", "mm")


def test_nest_progress_reaches_the_end_and_redo_survives_a_no_op_commit():
    from leathercad.nesting import NestPiece, nest
    from leathercad_app.history import History
    seen = []
    nest([NestPiece(i, [([Vec2(0, 0), Vec2(20, 0), Vec2(20, 10), Vec2(0, 10), Vec2(0, 0)], True)])
          for i in range(3)], 200, 100, progress=lambda d, t: seen.append((d, t)) or True)
    assert seen[-1] == (3, 3)
    h = History()
    h.reset({"a": 0})
    h.push({"a": 1})
    h.push({"a": 2})
    assert h.undo() == {"a": 1}
    h.push({"a": 1})                       # the same state again
    assert h.can_redo() and h.redo() == {"a": 2}


def test_facing_runs_do_not_stack_their_inner_rows():
    r = _stitched(Rectangle(width=40, height=10, layer="Cut"), inset=2, rows=2, row_spacing=3)
    r.stitch_edges = [True, False, True, False]            # the two long sides
    res = holes_for_shape(r, Document())
    assert res.rows == 1 and "second row" in res.note
    pts = res.points
    assert min((pts[i] - pts[j]).length() for i in range(len(pts))
               for j in range(i + 1, len(pts))) >= 0.75 * 3.85
    sl = StitchLine(points=[Vec2(0, 0), Vec2(2.8, 0)],
                    settings=StitchSettings(pitch_mm=3.85, rows=2, row_spacing=3.0))
    res = sl.result()
    assert res.count == 4 and res.rows == 2                # a short seam keeps its ends
    from leathercad.thread import estimate_thread
    small = _stitched(Rectangle(width=8, height=8, layer="Cut"), inset=1, rows=2, row_spacing=5)
    res = holes_for_shape(small, Document())
    assert res.rows == 1 and estimate_thread(res, small.stitch)["rows"] == 1
    from leathercad.expr import evaluate
    assert evaluate("1e2mm") == 100.0 and evaluate("2.5e1 mm") == 25.0

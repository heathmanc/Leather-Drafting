"""Text / lettering feature."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from leathercad.geometry import Vec2
from leathercad.text import TextShape
from leathercad.shapes import Transform
from leathercad.document import Document


def _bake(text, size=8.0, **kw):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    fam = fonts.register_bundled_fonts()
    from leathercad_app.items import bake_text_contours
    return bake_text_contours(text, fam, size, **kw)


def test_text_bakes_to_contours_at_size():
    contours = _bake("AB", 8.0)
    assert len(contours) >= 3            # A (2) + B (3) counters
    ys = [p.y for c in contours for p in c]
    assert abs((max(ys) - min(ys)) - 8.0) < 1.5   # ~cap height mm


def test_text_exports_as_engrave_paths():
    from leathercad import export
    contours = _bake("A", 8.0)
    doc = Document()
    doc.texts.append(TextShape(text="A",
                               contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                               transform=Transform(x=0, y=0), layer="Engrave"))
    outlines, _stitches = export.collect(doc)
    assert len(outlines) == len(contours)   # each contour is an engrave polyline


def test_text_survives_save_load():
    contours = _bake("Hi", 6.0)
    doc = Document()
    doc.texts.append(TextShape(text="Hi",
                               contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                               size=6.0, transform=Transform(x=5, y=5),
                               layer="Engrave"))
    doc2 = Document.from_dict(doc.to_dict())
    assert len(doc2.texts) == 1
    assert doc2.texts[0].text == "Hi"
    assert len(doc2.texts[0].contours) == len(contours)


# -- baking uses the bundled family and yields clean, simplified outlines -----

def test_default_family_is_bundled_dejavu():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    assert fonts.register_bundled_fonts() == "DejaVu Sans"
    assert fonts.default_family() == "DejaVu Sans"


def _seg_intersect(a, b, c, d):
    """True if open segments ab and cd cross (shared endpoints don't count)."""
    def cross(o, p, q):
        return (p.x - o.x) * (q.y - o.y) - (p.y - o.y) * (q.x - o.x)
    d1 = cross(c, d, a)
    d2 = cross(c, d, b)
    d3 = cross(a, b, c)
    d4 = cross(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def _self_intersects(contour):
    """Any non-adjacent edge pair of a closed contour that crosses."""
    n = len(contour)
    for i in range(n - 1):
        for j in range(i + 2, n - 1):
            if i == 0 and j == n - 2:
                continue                 # first and last edges are adjacent
            if _seg_intersect(contour[i], contour[i + 1],
                              contour[j], contour[j + 1]):
                return True
    return False


def test_bake_no_self_overlap():
    # 'O' has an outer body + an inner counter; simplified() must leave two clean,
    # non-self-intersecting loops (overlapping raw subpaths would cross).
    contours = _bake("O", 8.0)
    assert len(contours) == 2
    for c in contours:
        assert not _self_intersects(c)


# -- editing re-bakes: changing size / font changes the contours --------------

def test_size_change_alters_contours():
    small = _bake("A", 6.0)
    big = _bake("A", 12.0)
    hs = max(p.y for c in small for p in c) - min(p.y for c in small for p in c)
    hb = max(p.y for c in big for p in c) - min(p.y for c in big for p in c)
    assert hb > hs + 3.0                 # taller cap height after the re-bake


def test_font_change_alters_contours():
    a_default = _bake("g", 8.0)
    a_bold = _bake("g", 8.0, bold=True)
    da = [p for c in a_default for p in c]
    db = [p for c in a_bold for p in c]
    assert len(da) != len(db) or any(
        abs(p.x - q.x) > 1e-6 for p, q in zip(da, db))


# -- canvas-level: break-apart / resize / edit --------------------------------

def _canvas_with_text(text="AB", size=8.0):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    fam = fonts.register_bundled_fonts()
    from leathercad_app.items import bake_text_contours, TextItem
    from leathercad_app.mainwindow import MainWindow
    doc = Document()
    contours = bake_text_contours(text, fam, size)
    tx = TextShape(text=text,
                   contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                   size=size, font_family=fam,
                   transform=Transform(x=10, y=10), layer="Engrave")
    doc.texts.append(tx)
    win = MainWindow(doc)
    win.canvas.rebuild()
    item = next(it for it in win.canvas.scene_obj.items()
                if isinstance(it, TextItem))
    return win, win.canvas, item


def test_break_apart_text_into_glyph_paths():
    from leathercad_app.items import ShapeItem, TextItem
    win, canvas, item = _canvas_with_text("AB", 8.0)
    n_contours = len(item.model.contours)
    canvas.scene_obj.clearSelection()
    item.setSelected(True)
    canvas.break_apart_selected()
    # the text is gone from the document...
    assert len(win.doc.texts) == 0
    assert not any(isinstance(it, TextItem)
                   for it in canvas.scene_obj.items())
    # ...and became one shape per glyph contour (A body+counter + B body+2)
    from leathercad.shapes import Polygon
    polys = [s for s in win.doc.shapes if isinstance(s, Polygon)]
    assert len(polys) == n_contours
    assert n_contours >= 5


def test_resize_text_rebakes_cap_height():
    win, canvas, item = _canvas_with_text("A", 8.0)
    assert item.resize_extents() is not None
    hx, hy = item.resize_extents()
    item.set_resize_extents(hx * 2.0, hy * 2.0)
    assert item.model.size > 12.0        # cap height grew (re-baked, not scaled)
    item.sync_from_model(recompute_holes=False)
    ys = [p.y for c in item.model.contours for p in c]
    assert (max(ys) - min(ys)) > 12.0


def test_edit_text_item_rebakes():
    win, canvas, item = _canvas_with_text("A", 8.0)
    before = len(item.model.contours)
    m = item.model
    m.text = "AA"
    canvas.rebake_text(m)
    item.sync_from_model()
    assert len(item.model.contours) > before   # two A's -> more contours


def test_text_style_fields_round_trip():
    doc = Document()
    doc.texts.append(TextShape(text="X", size=9.0, font_family="DejaVu Sans",
                               bold=True, italic=True, tracking=12.5,
                               align="center", transform=Transform(x=1, y=2),
                               layer="Engrave"))
    tx = Document.from_dict(doc.to_dict()).texts[0]
    assert tx.bold is True and tx.italic is True
    assert abs(tx.tracking - 12.5) < 1e-9
    assert tx.align == "center"
    assert tx.font_family == "DejaVu Sans"


def test_rotation_and_mirror_are_rendered(qapp=None):
    """A rotated text must actually draw rotated on the canvas (WYSIWYG vs the
    laser output), not stay upright -- the render path is oriented through the
    transform like a shape."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app.items import TextItem
    contours = _bake("Fj", 8.0)
    m0 = TextShape(text="Fj",
                   contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                   transform=Transform(x=0, y=0), layer="Engrave")
    up = TextItem(m0).boundingRect()

    m90 = TextShape(text="Fj",
                    contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                    transform=Transform(x=0, y=0, rotation=90.0), layer="Engrave")
    rot = TextItem(m90).boundingRect()
    # a 90-degree rotation swaps the drawn width/height
    assert abs(up.width() - rot.height()) < 0.5
    assert abs(up.height() - rot.width()) < 0.5
    assert abs(up.width() - rot.width()) > 1.0        # actually changed


def test_unknown_or_sentinel_family_falls_back_to_bundled(qapp=None):
    """An empty / legacy / missing family must resolve to the bundled font, never
    the OS system font (the whole reason this rework exists)."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    fam = fonts.register_bundled_fonts()
    from leathercad_app.items import bake_text_contours
    ref = bake_text_contours("Ag", fam, 8.0)
    for bad in ("", "Sans", "NoSuchFont123"):
        got = bake_text_contours("Ag", bad, 8.0)
        assert len(got) == len(ref)                    # same glyphs as the default
        # and the geometry matches the bundled family (not some other font)
        assert abs(got[0][0].x - ref[0][0].x) < 1e-6


def test_text_resize_pins_opposite_corner_to_the_pull():
    """Dragging a corner handle grows the text toward that corner while the
    OPPOSITE corner stays put -- not scaling about the middle."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    fam = fonts.register_bundled_fonts()
    from leathercad_app.items import bake_text_contours, TextItem, ResizeHandle
    from leathercad_app.mainwindow import MainWindow

    contours = bake_text_contours("Size", fam, 10.0)
    doc = Document()
    doc.texts.append(TextShape(text="Size", font_family=fam, size=10.0,
                               contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                               transform=Transform(x=20, y=15), layer="Engrave"))
    win = MainWindow(doc)
    c = win.canvas
    c.rebuild()
    it = next(i for i in c.scene_obj.items() if isinstance(i, TextItem))
    c.scene_obj.clearSelection()
    it.setSelected(True)
    c.selection_changed()

    grip = next(g for g in c.scene_obj.items()
                if isinstance(g, ResizeHandle) and g.grip == (1, 1))
    gx, gy = grip.grip

    def opposite_corner_world(item):
        hx, hy = item.resize_extents()
        cx, cy = item.resize_center()
        return item.model.transform.apply(Vec2(cx - gx * hx, cy - gy * hy))

    before = opposite_corner_world(it)
    size0 = it.model.size
    gw = grip._grip_world()
    grip._apply_resize(Vec2(gw.x + 25.0, gw.y + 18.0))   # pull the corner out
    after = opposite_corner_world(it)

    assert it.model.size > size0                          # it grew
    assert (after - before).length() < 1e-6              # opposite corner pinned


def test_text_resize_drag_is_cheap_then_rebakes_on_release():
    """During a resize drag the glyphs are scaled cheaply (a re-bake is only
    OWED, not run every frame -- that was the strobing) and one crisp re-bake
    happens on the release sync. The final contours match a true re-bake."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    from leathercad_app.items import bake_text_contours, TextItem
    fam = fonts.register_bundled_fonts()
    contours = bake_text_contours("SIZE", fam, 6.0)
    m = TextShape(text="SIZE", font_family=fam, size=6.0,
                  contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                  transform=Transform(x=0, y=0), layer="Engrave")
    it = TextItem(m)

    # a resize frame: half-extents pulled to 2x -> a re-bake is owed, not done
    hx, hy = it.resize_extents()
    it.set_resize_extents(hx * 2.0, hy * 2.0)
    assert it._needs_rebake is True
    assert abs(m.size - 12.0) < 1e-6

    # the crisp re-bake lands on the release (a full sync) and matches truth
    it.sync_from_model(recompute_holes=True)
    assert it._needs_rebake is False
    truth = bake_text_contours("SIZE", fam, m.size)
    got = m.contours
    assert len(got) == len(truth)
    tp = [p for c in truth for p in c]
    gp = [p for c in got for p in c]
    assert len(gp) == len(tp)
    assert max((a.x - b.x) ** 2 + (a.y - b.y) ** 2
               for a, b in zip(gp, tp)) < 1e-6


def test_standalone_text_resize_is_stable_not_jittery():
    """A monotonic corner drag on a plain (ungrouped) text grows the size
    smoothly -- the scale is measured against the FIXED baseline box, so the
    dominant axis can't flip frame to frame (the old jitter/double-vision)."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import canvas as cm, fonts
    from leathercad_app.items import bake_text_contours, TextItem, ResizeHandle
    fam = fonts.register_bundled_fonts()
    contours = bake_text_contours("SIZE", fam, 8.0)
    doc = Document()
    doc.texts.append(TextShape(text="SIZE", font_family=fam, size=8.0,
                     contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                     transform=Transform(x=20, y=15), layer="Engrave"))
    c = cm.Canvas(doc)
    c.rebuild()
    it = next(i for i in c.scene_obj.items() if isinstance(i, TextItem))
    c.scene_obj.clearSelection()
    it.setSelected(True)
    c.selection_changed()
    grip = next(g for g in c.scene_obj.items()
                if isinstance(g, ResizeHandle) and g.grip == (1, 1))
    gx, gy = grip.grip

    def opp_corner():
        hx, hy = it.resize_extents()
        cx, cy = it.resize_center()
        return it.model.transform.apply(Vec2(cx - gx * hx, cy - gy * hy))

    gw = grip._grip_world()
    base = opp_corner()
    prev = it.model.size
    for i in range(1, 9):
        grip._apply_resize(Vec2(gw.x + i * 3.0, gw.y + i * 2.0))
        assert it.model.size >= prev - 1e-6            # never shrinks mid-pull
        assert (opp_corner() - base).length() < 1e-4   # opposite corner pinned
        prev = it.model.size
    assert it.model.size > 8.0                         # and it actually grew


def test_text_box_exposes_bbox_snap_points():
    """A text box carries 9 snap points -- 4 corners, 4 edge midpoints and the
    centre -- so it can be grabbed/placed by a meaningful handle, not just the
    invisible baseline origin."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app import fonts
    from leathercad_app.items import bake_text_contours, TextItem
    fam = fonts.register_bundled_fonts()
    contours = bake_text_contours("AB", fam, 10.0)
    m = TextShape(text="AB", font_family=fam, size=10.0,
                  contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                  transform=Transform(x=20, y=15), layer="Engrave")
    it = TextItem(m)

    nodes = it.world_snap_nodes()
    typed = it.world_snap_nodes_typed()
    assert len(nodes) == 9 and len(typed) == 9
    kinds = [k for _p, k in typed]
    assert kinds.count("end") == 4       # corners
    assert kinds.count("mid") == 4       # edge midpoints
    assert kinds.count("center") == 1    # centre

    # the corners are the world bbox extremes; the centre is their average
    minx, miny, maxx, maxy = it._local_bbox()
    t = m.transform
    want = {(t.apply(Vec2(minx, miny)).x, t.apply(Vec2(minx, miny)).y),
            (t.apply(Vec2(maxx, maxy)).x, t.apply(Vec2(maxx, maxy)).y)}
    got = {(round(n.x, 6), round(n.y, 6)) for n in nodes}
    for wx, wy in want:
        assert (round(wx, 6), round(wy, 6)) in got
    ctr = next(p for p, k in typed if k == "center")
    assert abs(ctr.x - t.apply(Vec2((minx + maxx) / 2, (miny + maxy) / 2)).x) < 1e-6


def test_text_is_a_snap_target_and_snaps_by_its_box():
    """Other objects can snap TO a text box, and dragging the text snaps one of
    its box handles onto a nearby node."""
    from PySide6.QtCore import QPointF
    from leathercad_app import canvas as cm, fonts
    from leathercad.shapes import Rectangle
    from leathercad_app.items import bake_text_contours, TextItem, ShapeItem
    fam = fonts.register_bundled_fonts()
    contours = bake_text_contours("AB", fam, 10.0)
    doc = Document()
    doc.add_shape(Rectangle(width=40, height=20, transform=Transform(x=100, y=100)))
    doc.texts.append(TextShape(text="AB", font_family=fam, size=10.0,
                     contours=[[Vec2(p.x, p.y) for p in c] for c in contours],
                     transform=Transform(x=20, y=15), layer="Engrave"))
    c = cm.Canvas(doc)
    c.rebuild()
    c.snap_to_nodes = True
    c.snap_to_grid = False
    txt = next(i for i in c.scene_obj.items() if isinstance(i, TextItem))
    rect = next(i for i in c.scene_obj.items() if isinstance(i, ShapeItem))

    # (1) text is a TARGET: its nodes show up as snap candidates for other items
    def near(a, b):
        return abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6
    cands = c._snap_candidates(exclude=rect)
    tnodes = txt.world_snap_nodes()
    assert any(any(near(cd, n) for n in tnodes) for cd in cands)

    # (2) dragging the text snaps one of its 9 box handles onto a rect node
    rect_nodes = rect.world_snap_nodes()
    corner = rect_nodes[0]
    c.scene_obj.clearSelection()
    txt.setSelected(True)
    c.begin_move_snap(txt)
    off = txt._snap_offsets[0]                       # bring a box handle ~0.4mm off
    req = QPointF(corner.x - off.x + 0.3, corner.y - off.y + 0.3)
    snapped = c.snap_move(txt, req)
    assert (snapped.x(), snapped.y()) != (req.x(), req.y())   # it snapped
    landed = [Vec2(snapped.x() + o.x, snapped.y() + o.y)
              for o in txt._snap_offsets]
    # a box handle came to rest exactly on one of the rect's nodes
    assert any(near(p, rn) for p in landed for rn in rect_nodes)
    c.end_move_snap()

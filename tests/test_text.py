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

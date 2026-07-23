"""Seam-mate checker, parts library, and the photo tracing underlay."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.stitchsettings import StitchSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _win(doc=None):
    from leathercad_app.mainwindow import MainWindow
    w = MainWindow(doc or Document())
    w.canvas.rebuild()
    w.canvas.resize(600, 450)
    return w


def _select_all_shapes(c):
    from leathercad_app.items import ShapeItem
    for it in c.scene_obj.items():
        if isinstance(it, ShapeItem):
            it.setSelected(True)


# -- seam mates ------------------------------------------------------------
def test_seam_mates_match_and_mismatch(qapp):
    doc = Document()
    st = lambda: StitchSettings(pitch_mm=4.0, inset=3.0)  # noqa: E731
    doc.add_shape(Rectangle(name="Body", width=60, height=40,
                            transform=Transform(x=0, y=0), stitch=st(),
                            layer="Cut"))
    doc.add_shape(Rectangle(name="Twin", width=60, height=40,
                            transform=Transform(x=100, y=0), stitch=st(),
                            layer="Cut"))
    win = _win(doc)
    _select_all_shapes(win.canvas)
    rep = win.canvas.seam_mate_report()
    assert "MATCH" in rep and "Body" in rep and "Twin" in rep

    win.doc.shapes[1].width = 90                     # now a mismatch
    win.canvas.rebuild()
    _select_all_shapes(win.canvas)
    rep = win.canvas.seam_mate_report()
    assert "differ" in rep and "✗" in rep

    win.canvas.scene_obj.clearSelection()
    assert "exactly TWO" in win.canvas.seam_mate_report()


# -- parts library -----------------------------------------------------------
def test_parts_save_list_place_delete(qapp, tmp_path):
    from leathercad_app import partslib
    from leathercad_app.items import ShapeItem
    doc = Document()
    doc.add_shape(Rectangle(name="Pocket", width=50, height=30,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0), layer="Cut"))
    win = _win(doc)
    base = str(tmp_path)

    shapes = [win.doc.shapes[0]]
    partslib.save_part("My pocket", shapes, base=base)
    parts = partslib.list_parts(base)
    assert [n for n, _p in parts] == ["My pocket"]

    loaded = partslib.load_part(parts[0][1])
    assert len(loaded) == 1
    assert loaded[0].width == 50
    assert loaded[0].shape_id != shapes[0].shape_id   # fresh identity
    assert loaded[0].stitch is not None               # stitch settings kept

    n0 = len(win.doc.shapes)
    items = win.canvas.place_shapes(loaded)
    assert len(win.doc.shapes) == n0 + 1
    assert items[0].isSelected()
    assert win._unsaved_changes

    partslib.delete_part(parts[0][1])
    assert partslib.list_parts(base) == []


def _find_leaf(tree, substr):
    """The first placeable tree leaf whose label contains ``substr``."""
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    from PySide6.QtCore import Qt
    it = QTreeWidgetItemIterator(tree)
    while it.value():
        node = it.value()
        if node.data(0, Qt.UserRole) and substr in node.text(0):
            return node
        it += 1
    return None


def test_parts_panel_roundtrip(qapp, tmp_path):
    from leathercad_app.partspanel import PartsPanel
    doc = Document()
    doc.add_shape(Rectangle(width=20, height=10, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = _win(doc)
    panel = PartsPanel(win.canvas, base_dir=str(tmp_path))
    _select_all_shapes(win.canvas)
    panel.save_selection(name="Strap end")            # bypasses the dialog
    node = _find_leaf(panel.tree, "Strap end")
    assert node is not None                           # saved under "My parts"
    panel.tree.setCurrentItem(node)
    panel.place_selected()
    assert len(win.doc.shapes) == 2


# -- tracing underlay ---------------------------------------------------------
def _photo(tmp_path):
    from PySide6.QtGui import QPixmap, QColor
    pm = QPixmap(400, 300)
    pm.fill(QColor("tan"))
    p = tmp_path / "wallet.png"
    pm.save(str(p))
    return str(p)


def test_underlay_place_calibrate_persist(qapp, tmp_path):
    win = _win()
    c = win.canvas
    assert c.set_underlay(_photo(tmp_path))
    u = win.doc.underlay
    assert u and abs(u["scale"] - 0.5) < 1e-9         # 400 px -> 200 mm wide
    assert c._underlay_item is not None
    assert c._underlay_item.zValue() < -100           # behind the drawing

    # calibrate: two clicks 50 mm apart on screen are REALLY 100 mm
    assert c.calibrate_underlay(QPointF(0, 0), QPointF(50, 0), 100.0)
    assert abs(win.doc.underlay["scale"] - 1.0) < 1e-9    # doubled

    # persists through save/load and survives a rebuild
    d2 = Document.from_dict(win.doc.to_dict())
    assert d2.underlay["path"] == u["path"]
    c.rebuild()
    assert c._underlay_item is not None

    # never exported
    from leathercad.export import collect
    outlines, stitches = collect(win.doc)
    assert outlines == [] and stitches == []

    c.underlay_config(remove=True)
    assert win.doc.underlay is None and c._underlay_item is None


def test_fit_to_content_ignores_underlay(qapp, tmp_path):
    doc = Document()
    doc.add_shape(Rectangle(width=60, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = _win(doc)
    c = win.canvas
    c.set_underlay(_photo(tmp_path))
    c.fit_to_content()
    r = c.mapToScene(c.viewport().rect()).boundingRect()
    # the view fits the 60 mm rect, not the 200 mm photo
    assert r.width() < 150


# -- built-in library templates (currency + card sizes) ---------------------
def test_builtin_templates_have_currency_and_card_sizes(qapp):
    from leathercad_app import fonts, builtin_parts
    fonts.register_bundled_fonts()
    cats = dict(builtin_parts.categories())
    assert {"US currency", "Euro currency", "UK currency", "Cards"} <= set(cats)
    keys = {k for _cat, rows in builtin_parts.categories() for _n, k in rows}
    assert {"us_bill", "eur_50", "gbp_20", "card_id1", "card_biz"} <= keys

    shapes, texts = builtin_parts.build_builtin("us_bill")
    assert len(shapes) == 1
    assert abs(shapes[0].width - 156.0) < 1e-6
    assert abs(shapes[0].height - 66.0) < 1e-6
    assert shapes[0].layer == "Cut"
    # a size label is baked inside: name line + "W x H mm" line
    assert len(texts) == 2
    joined = " ".join(t.text for t in texts)
    assert "156" in joined and "66" in joined and "mm" in joined
    assert all(t.layer == "Engrave" for t in texts)
    # the label is grouped to its outline (one shared, non-null group_id)
    gids = {shapes[0].group_id} | {t.group_id for t in texts}
    assert len(gids) == 1 and None not in gids

    # the credit card is ID-1 with rounded corners
    card, _ct = builtin_parts.build_builtin("card_id1")
    assert abs(card[0].width - 85.6) < 1e-6 and abs(card[0].height - 54.0) < 1e-6
    assert card[0].corner_radius > 0.0


def test_placing_a_template_adds_shape_and_label(qapp):
    from leathercad_app import fonts, builtin_parts
    fonts.register_bundled_fonts()
    win = _win()
    shapes, texts = builtin_parts.build_builtin("card_id1")
    win.canvas.place_shapes(shapes, texts)
    assert len(win.doc.shapes) == 1
    assert len(win.doc.texts) == 2          # the size label rode along


def test_saved_part_round_trips_text(qapp, tmp_path):
    from leathercad_app import partslib, fonts, builtin_parts
    fonts.register_bundled_fonts()
    shapes, texts = builtin_parts.build_builtin("card_id1")
    partslib.save_part("Card w/ label", shapes, texts, str(tmp_path))
    (name, path), = partslib.list_parts(str(tmp_path))
    sh, tx = partslib.load_part_full(path)
    assert len(sh) == 1 and len(tx) == 2
    assert "85.6" in " ".join(t.text for t in tx)


def test_parts_panel_lists_builtins_and_blocks_their_delete(qapp, tmp_path,
                                                           monkeypatch):
    from leathercad_app import partspanel
    from leathercad_app.partspanel import PartsPanel
    from leathercad_app import fonts
    fonts.register_bundled_fonts()
    # the "can't delete a built-in" guard pops a modal box; don't block on it
    monkeypatch.setattr(partspanel.QMessageBox, "information",
                        lambda *a, **k: None)
    panel = PartsPanel(_win().canvas, base_dir=str(tmp_path))
    # categories are top-level headers
    cats = [panel.tree.topLevelItem(i).text(0)
            for i in range(panel.tree.topLevelItemCount())]
    assert "US currency" in cats and "UK currency" in cats and "Cards" in cats
    for want in ("US bill", "£20", "Credit / bank card"):
        assert _find_leaf(panel.tree, want) is not None, want

    # selecting a built-in and hitting delete must not remove it
    node = _find_leaf(panel.tree, "US bill")
    panel.tree.setCurrentItem(node)
    n_before = panel.tree.topLevelItem(0).childCount()
    panel.delete_selected()
    assert panel.tree.topLevelItem(0).childCount() == n_before
    # and placing it drops the grouped outline + label into the doc
    panel.place_selected()
    assert len(panel.canvas.doc.shapes) == 1 and len(panel.canvas.doc.texts) == 2
    gids = {panel.canvas.doc.shapes[0].group_id} | {
        t.group_id for t in panel.canvas.doc.texts}
    assert len(gids) == 1 and None not in gids       # label grouped to shape


def test_placed_template_selects_as_a_group(qapp):
    """Pressing a placed template's outline selects its size label too, so they
    move together (grouping is wired through the canvas, not just the data)."""
    from leathercad_app import fonts, builtin_parts
    from leathercad_app.items import ShapeItem, TextItem
    fonts.register_bundled_fonts()
    win = _win()
    c = win.canvas
    shapes, texts = builtin_parts.build_builtin("us_bill")
    c.place_shapes(shapes, texts)
    shp = next(i for i in c.scene_obj.items() if isinstance(i, ShapeItem))
    labels = [i for i in c.scene_obj.items() if isinstance(i, TextItem)]
    assert len(labels) == 2
    c.scene_obj.clearSelection()
    c.select_group_of(shp)                    # what a mouse-press does
    assert shp.isSelected()
    assert all(t.isSelected() for t in labels)


def test_builtin_coins_devices_and_passports(qapp):
    """US coins + AirTag are round; passports and Apple devices are present."""
    from leathercad_app import fonts, builtin_parts
    from leathercad.shapes import Circle, Rectangle
    fonts.register_bundled_fonts()
    cats = {c for c, _rows in builtin_parts.categories()}
    assert {"US coins", "Passports", "Apple devices"} <= cats

    # a coin is a circle at its mint diameter, labelled with the diameter
    coin, ct = builtin_parts.build_builtin("coin_quarter")
    assert isinstance(coin[0], Circle)
    assert abs(coin[0].rx * 2.0 - 24.26) < 1e-6
    assert any("Ø" in t.text and "24.26" in t.text for t in ct)

    # AirTag round; iPhone rectangular with its footprint in the label
    at, _atc = builtin_parts.build_builtin("airtag")
    assert isinstance(at[0], Circle) and abs(at[0].rx * 2 - 31.9) < 1e-6
    ip, ipt = builtin_parts.build_builtin("ip_15pm")
    assert isinstance(ip[0], Rectangle)
    assert abs(ip[0].width - 76.7) < 1e-6 and abs(ip[0].height - 159.9) < 1e-6
    assert any("159.9" in t.text for t in ipt)

    # everything stays grouped (label rides with the outline)
    for key in ("coin_dime", "airtag", "ipad_13", "pass_id3"):
        sh, tx = builtin_parts.build_builtin(key)
        gids = {sh[0].group_id} | {t.group_id for t in tx}
        assert len(gids) == 1 and None not in gids


def test_template_labels_stay_inside_every_outline(qapp):
    """No engraved label may spill outside its outline -- the half-dollar coin
    (the tightest round case) and all others fit within the border."""
    import math
    from leathercad_app import fonts, builtin_parts
    fonts.register_bundled_fonts()
    for key, _cat, _name, _short, kind, w, h, _r in builtin_parts._TEMPLATES:
        shapes, texts = builtin_parts.build_builtin(key)
        pts = [(p.x + t.transform.x, p.y + t.transform.y)
               for t in texts for c in t.contours for p in c]
        assert pts, key
        if kind == "circle":
            rmax = max(math.hypot(x, y) for x, y in pts)
            assert rmax <= (w / 2.0) * 0.99, (key, rmax, w / 2.0)
        else:
            assert max(abs(x) for x, y in pts) <= (w / 2.0) * 0.99, key
            assert max(abs(y) for x, y in pts) <= (h / 2.0) * 0.99, key


def test_group_move_never_leaves_a_member_behind(qapp):
    """Pressing + dragging any group member moves the WHOLE template together,
    even when Qt's selection timing would otherwise leave a label behind."""
    from leathercad_app import fonts, builtin_parts
    from leathercad_app.items import ShapeItem, TextItem
    fonts.register_bundled_fonts()
    win = _win()
    c = win.canvas
    c.snap_to_nodes = False
    shapes, texts = builtin_parts.build_builtin("coin_half")
    c.place_shapes(shapes, texts)
    coin = next(i for i in c.scene_obj.items() if isinstance(i, ShapeItem))
    labels = [i for i in c.scene_obj.items() if isinstance(i, TextItem)]

    c.scene_obj.clearSelection()
    c.press_select(coin, None)
    c.begin_move_snap(coin)                 # group is captured by group_id
    assert coin.isSelected() and all(l.isSelected() for l in labels)
    before = [(l.model.transform.x - coin.model.transform.x,
               l.model.transform.y - coin.model.transform.y) for l in labels]

    # Qt drags every selected + movable item by the same delta
    for it in [coin] + labels:
        if it.isSelected() and (it.flags()
                                & it.GraphicsItemFlag.ItemIsMovable):
            it.setPos(it.pos().x() + 30.0, it.pos().y() + 20.0)
    after = [(l.model.transform.x - coin.model.transform.x,
              l.model.transform.y - coin.model.transform.y) for l in labels]
    assert all(abs(b[0] - a[0]) < 1e-6 and abs(b[1] - a[1]) < 1e-6
               for b, a in zip(before, after))


def test_clicking_a_label_selects_the_whole_template(qapp):
    """A plain click on a label leaves the whole group highlighted -- Qt's
    release-time 'select only the pressed item' is undone by reassert."""
    from leathercad_app import fonts, builtin_parts
    from leathercad_app.items import ShapeItem, TextItem
    fonts.register_bundled_fonts()
    win = _win()
    c = win.canvas
    shapes, texts = builtin_parts.build_builtin("coin_half")
    c.place_shapes(shapes, texts)
    coin = next(i for i in c.scene_obj.items() if isinstance(i, ShapeItem))
    labels = [i for i in c.scene_obj.items() if isinstance(i, TextItem)]

    c.scene_obj.clearSelection()
    c.press_select(labels[0], None)
    # Qt's release keeps only the pressed item selected...
    for it in [coin] + labels:
        it.setSelected(it is labels[0])
    c._moved_during_press = False
    c.reassert_group_selection(labels[0])       # ...which we restore
    assert coin.isSelected() and all(l.isSelected() for l in labels)


def test_group_resize_scales_label_with_outline(qapp):
    """Dragging a template's box-resize grip scales the outline AND its engraved
    label together, and the label stays inside the enlarged border."""
    from leathercad.geometry import Vec2
    from leathercad_app import fonts, builtin_parts
    from leathercad_app.items import ShapeItem, TextItem
    fonts.register_bundled_fonts()
    win = _win()
    c = win.canvas
    shapes, texts = builtin_parts.build_builtin("card_id1")
    c.place_shapes(shapes, texts)
    shp = next(i for i in c.scene_obj.items() if isinstance(i, ShapeItem))
    labels = [i for i in c.scene_obj.items() if isinstance(i, TextItem)]
    c.scene_obj.clearSelection()
    c.select_group_of(shp)
    c._refresh_resize_handles()
    grip = next(h for h in c._resize_handles if getattr(h, "grip", None) == (1, 1))
    w0, h0 = shp.model.width, shp.model.height
    sizes0 = [t.model.size for t in labels]

    c._active_resize = grip
    c._begin_group_resize(shp)
    t = shp.model.transform
    anchor = Vec2(t.x - w0 / 2, t.y - h0 / 2)     # opposite corner stays pinned
    grip._apply_resize(Vec2(anchor.x + w0 * 1.5, anchor.y + h0 * 1.5))
    c._end_group_resize()

    assert abs(shp.model.width - w0 * 1.5) < 0.5
    assert abs(shp.model.height - h0 * 1.5) < 0.5
    for t0, lab in zip(sizes0, labels):
        assert abs(lab.model.size - t0 * 1.5) < 0.2      # label grew ~1.5x
    # and it is still inside the enlarged card
    hw, hh = shp.model.width / 2, shp.model.height / 2
    cx, cy = shp.model.transform.x, shp.model.transform.y
    for lab in labels:
        for cc in lab.model.contours:
            for p in cc:
                assert abs(p.x + lab.model.transform.x - cx) <= hw * 1.001
                assert abs(p.y + lab.model.transform.y - cy) <= hh * 1.001

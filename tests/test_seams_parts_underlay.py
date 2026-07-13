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
    partslib.save_part("My pocket", shapes, base)
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


def test_parts_panel_roundtrip(qapp, tmp_path):
    from leathercad_app.partspanel import PartsPanel
    doc = Document()
    doc.add_shape(Rectangle(width=20, height=10, transform=Transform(x=0, y=0),
                            layer="Cut"))
    win = _win(doc)
    panel = PartsPanel(win.canvas, base_dir=str(tmp_path))
    _select_all_shapes(win.canvas)
    panel.save_selection(name="Strap end")            # bypasses the dialog
    names = [panel.list.item(i).text() for i in range(panel.list.count())]
    assert "Strap end" in names
    panel.list.setCurrentRow(names.index("Strap end"))
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

"""SVG export grouped by piece -- the default.

The report: imported into LightBurn, every hole of every piece came in as
one group and every outline as another, so each piece had to be ungrouped and
regrouped with its own holes by hand. LightBurn imports each SVG group as a
group, so the export now writes one group per piece: its holes first, then
its outline. Pieces grouped together in Stitch Hero (Ctrl+G) come in as one
group around them. LightBurn still files the shapes into its layers by
colour, so the blue holes stay on C01 and are cut before the red outlines."""

import xml.etree.ElementTree as ET

import pytest

from leathercad import export
from leathercad.document import Document
from leathercad.geometry import Vec2
from leathercad.holes import LooseHole
from leathercad.shapes import PathShape, Rectangle, Transform
from leathercad.stitching import holes_for_shape
from leathercad.stitchline import StitchLine
from leathercad.stitchsettings import StitchSettings
from leathercad.text import TextShape

SVG = "{http://www.w3.org/2000/svg}"
INK = "{http://www.inkscape.org/namespaces/inkscape}"


def _st():
    return StitchSettings(pitch_mm=4.0, inset=3.0)


def _piece(x=0.0, **kw):
    return Rectangle(width=60, height=40, layer="Cut", stitch=_st(),
                     transform=Transform(x=x, y=0), **kw)


def _export(doc, tmp_path, name="out.svg", **kw):
    p = tmp_path / name
    export.export_svg(doc, str(p), **kw)
    return ET.fromstring(p.read_text(encoding="utf-8"))


def _tag(el):
    return el.tag.split("}")[-1]


def _top(root):
    """The top-level drawing elements and groups, in file order."""
    return [el for el in root if _tag(el) != "title"]


def _kinds(g):
    return [_tag(el) for el in g]


def test_each_piece_is_one_group_its_holes_then_its_outline(tmp_path):
    doc = Document()
    a, b = _piece(0), _piece(100)
    doc.add_shape(a)
    doc.add_shape(b)
    top = _top(_export(doc, tmp_path))
    assert [_tag(el) for el in top] == ["g", "g"]
    for g, sh in zip(top, (a, b)):
        n = holes_for_shape(sh).count
        assert _kinds(g) == ["circle"] * n + ["path"]
        holes, outline = g[:-1], g[-1]
        # LightBurn's colour layers are unchanged: blue C01, red C02
        assert {el.get("stroke") for el in holes} == {"#0000ff"}
        assert outline.get("stroke") == "#ff0000"


def test_a_pieces_name_labels_its_group(tmp_path):
    doc = Document()
    doc.add_shape(_piece(name="Front panel"))
    g = _top(_export(doc, tmp_path))[0]
    assert g.get(INK + "label") == "Front panel"
    assert g.get("id") == "front-panel"
    assert g.get(INK + "groupmode") is None       # a group, not a layer


def test_a_move_group_comes_in_as_one_group_around_its_pieces(tmp_path):
    doc = Document()
    a, b = _piece(0, group_id="group9"), _piece(100, group_id="group9")
    alone = _piece(250)
    for sh in (a, b, alone):
        doc.add_shape(sh)
    doc.texts.append(TextShape(
        text="LOGO", group_id="group9", transform=Transform(x=0, y=60),
        contours=[[Vec2(0, 0), Vec2(3, 0), Vec2(3, 3)],
                  [Vec2(4, 0), Vec2(6, 0), Vec2(6, 3)]]))
    top = _top(_export(doc, tmp_path))
    assert [_tag(el) for el in top] == ["g", "g"]
    group, single = top
    assert [_tag(el) for el in group] == ["g", "g", "g"]
    lettering, ga, gb = group                     # engraving before the cuts
    assert _kinds(lettering) == ["path", "path"]
    assert lettering.get(INK + "label") == "text LOGO"
    for g, sh in ((ga, a), (gb, b), (single, alone)):
        assert _kinds(g) == ["circle"] * holes_for_shape(sh).count + ["path"]


def test_a_group_of_one_is_just_its_piece(tmp_path):
    doc = Document()
    doc.add_shape(_piece(group_id="group3"))      # its partners were deleted
    top = _top(_export(doc, tmp_path))
    assert len(top) == 1 and _kinds(top[0])[-1] == "path"
    assert all(_tag(el) != "g" for el in top[0])


def test_holes_and_scoring_come_before_anything_cut(tmp_path):
    """For software that cuts in file order, as LightBurn does by layer:
    nothing is cut free before what sits on it."""
    doc = Document()
    doc.add_shape(_piece())
    doc.add_shape(PathShape(points=[Vec2(-20, 0), Vec2(20, 0)],
                            layer="Score", close_path=False))
    doc.add_stitch_line(StitchLine(points=[Vec2(0, 60), Vec2(40, 60)],
                                   settings=_st()))
    doc.holes.append(LooseHole(point=Vec2(200, 200)))
    top = _top(_export(doc, tmp_path))
    kinds = [(_tag(el), _kinds(el) if _tag(el) == "g" else None) for el in top]
    seam, loose, score, piece = kinds
    assert seam[0] == "g" and set(seam[1]) == {"circle"}
    assert loose == ("circle", None)
    assert score == ("path", None)
    assert piece[0] == "g" and piece[1][-1] == "path"


def test_kerf_compensated_export_groups_the_same(tmp_path):
    doc = Document()
    doc.add_shape(_piece(0))
    doc.add_shape(_piece(100))
    doc.kerf = 0.2                               # outlines become polylines
    top = _top(_export(doc, tmp_path))
    assert [_tag(el) for el in top] == ["g", "g"]
    assert all(_kinds(g)[-1] == "path" and _kinds(g)[0] == "circle"
               for g in top)


def test_holes_only_export_keeps_each_pieces_holes_together(tmp_path):
    doc = Document()
    doc.add_shape(_piece(0))
    doc.add_shape(_piece(100))
    top = _top(_export(doc, tmp_path, include={"holes"}))
    assert [_tag(el) for el in top] == ["g", "g"]
    assert all(set(_kinds(g)) == {"circle"} for g in top)


def test_every_element_is_unfilled_without_a_layer_around_it(tmp_path):
    doc = Document()
    doc.add_shape(_piece())
    doc.holes.append(LooseHole(point=Vec2(200, 200)))
    root = _export(doc, tmp_path)
    drawn = [el for el in root.iter()
             if _tag(el) in ("path", "circle", "line", "polygon")]
    assert drawn and all(el.get("fill") == "none" for el in drawn)


def test_the_layer_layout_is_still_there(tmp_path):
    doc = Document()
    doc.add_shape(_piece(0))
    doc.add_shape(_piece(100))
    top = _top(_export(doc, tmp_path, group="layer"))
    assert [g.get(INK + "label") for g in top] == ["holes", "outline"]


def test_an_unknown_grouping_is_refused(tmp_path):
    with pytest.raises(ValueError):
        export.export_svg(Document(), str(tmp_path / "x.svg"), group="colour")


# -- the export dialog ------------------------------------------------------
def test_export_dialog_offers_the_grouping_and_remembers_it(tmp_path,
                                                            monkeypatch):
    import os
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog,
                                   QRadioButton)
    from leathercad_app.mainwindow import MainWindow
    QApplication.instance() or QApplication([])

    win = MainWindow(Document())
    s = win._settings()
    saved = s.value("expSvgGroup", None)
    seen = {}

    def answer(pick):
        def fake_exec(dlg):
            radios = {r.text().split(" —")[0]: r
                      for r in dlg.findChildren(QRadioButton)}
            seen["checked"] = [k for k, r in radios.items() if r.isChecked()]
            if pick:
                radios[pick].setChecked(True)
            return QDialog.Accepted
        return fake_exec

    try:
        s.remove("expSvgGroup")
        win.doc.add_shape(_piece())
        out = tmp_path / "pattern.svg"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(out), "SVG (*.svg)"))
        monkeypatch.setattr(QDialog, "exec", answer(None))
        win.export_svg()
        assert seen["checked"] == ["By piece"]        # the default
        root = ET.fromstring(out.read_text(encoding="utf-8"))
        assert _kinds(_top(root)[0])[-1] == "path"     # a piece group
        # pick By layer: it's used, and offered first next time
        monkeypatch.setattr(QDialog, "exec", answer("By layer"))
        win.export_svg()
        root = ET.fromstring(out.read_text(encoding="utf-8"))
        assert [g.get(INK + "label") for g in _top(root)] == ["holes",
                                                              "outline"]
        monkeypatch.setattr(QDialog, "exec", answer(None))
        assert win._export_options("Export SVG", svg=True)[2] == "layer"
        assert seen["checked"] == ["By layer"]
        # DXF has no such choice
        assert win._export_options("Export DXF")[2] is None
    finally:
        if saved is None:
            s.remove("expSvgGroup")
        else:
            s.setValue("expSvgGroup", saved)
        win.hide()

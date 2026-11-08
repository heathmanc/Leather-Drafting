"""SVG export for LightBurn: named layers, the holes first and in LightBurn's
own blue, the outline after them.

The report: imported into LightBurn the holes and the outline came in as
"holes" / "C14", and the holes weren't first. LightBurn files an SVG's shapes
into its colour layers by stroke colour (it doesn't read layer names) and
lists them in palette order -- C00 black, C01 blue #0000ff, C02 red #ff0000
-- so the stock stitch blue #0066ff landed on C14, after the red outline."""

import xml.etree.ElementTree as ET

from leathercad import export
from leathercad.document import Document
from leathercad.geometry import Vec2
from leathercad.shapes import PathShape, Rectangle, Transform
from leathercad.stitchsettings import StitchSettings

SVG = "{http://www.w3.org/2000/svg}"
INK = "{http://www.inkscape.org/namespaces/inkscape}"


def _layers(text):
    """[(label, groupmode, stroke colours, element kinds)] per top-level
    group, in file order."""
    root = ET.fromstring(text)
    out = []
    for g in root.findall(SVG + "g"):
        strokes = {el.get("stroke") for el in g if el.get("stroke")}
        kinds = {el.tag.split("}")[-1] for el in g}
        out.append((g.get(INK + "label"), g.get(INK + "groupmode"),
                    strokes, kinds))
    return out


def _stitched_rect(**kw):
    return Rectangle(width=60, height=40, layer="Cut",
                     stitch=StitchSettings(pitch_mm=4.0, inset=3.0), **kw)


def _export(doc, tmp_path, name="out.svg", **kw):
    p = tmp_path / name
    export.export_svg(doc, str(p), **kw)
    return p.read_text(encoding="utf-8")


def test_holes_come_first_in_lightburn_blue_then_the_outline(tmp_path):
    doc = Document()
    doc.add_shape(_stitched_rect())
    layers = _layers(_export(doc, tmp_path))
    assert [lyr[0] for lyr in layers] == ["holes", "outline"]
    assert all(lyr[1] == "layer" for lyr in layers)       # real named layers
    holes, outline = layers
    assert holes[2] == {"#0000ff"} and holes[3] == {"circle"}
    assert outline[2] == {"#ff0000"} and outline[3] == {"path"}


def test_kerf_compensated_export_is_layered_the_same(tmp_path):
    doc = Document()
    doc.add_shape(_stitched_rect())
    doc.kerf = 0.2                       # outlines written as polylines
    layers = _layers(_export(doc, tmp_path))
    assert [(lyr[0], lyr[2]) for lyr in layers] == [
        ("holes", {"#0000ff"}), ("outline", {"#ff0000"})]


def test_score_and_engrave_sit_between_and_the_outline_is_cut_last(tmp_path):
    doc = Document()
    doc.add_shape(_stitched_rect())
    doc.add_shape(PathShape(points=[Vec2(-20, 0), Vec2(20, 0)], layer="Score"))
    doc.add_shape(Rectangle(width=10, height=6, layer="Engrave",
                            transform=Transform(x=0, y=10)))
    labels = [lyr[0] for lyr in _layers(_export(doc, tmp_path))]
    assert labels == ["holes", "Score", "Engrave", "outline"]


def test_a_colour_you_chose_is_written_as_it_is(tmp_path):
    doc = Document()
    doc.layer("Stitch").color = "#00ffff"
    doc.add_shape(_stitched_rect())
    holes = _layers(_export(doc, tmp_path))[0]
    assert holes[0] == "holes" and holes[2] == {"#00ffff"}


def test_holes_only_and_outlines_only_exports(tmp_path):
    doc = Document()
    doc.add_shape(_stitched_rect())
    only_holes = _layers(_export(doc, tmp_path, "h.svg", include={"holes"}))
    assert [lyr[0] for lyr in only_holes] == ["holes"]
    only_cut = _layers(_export(doc, tmp_path, "o.svg", include={"outlines"}))
    assert [lyr[0] for lyr in only_cut] == ["outline"]


def test_a_renamed_cut_layer_keeps_its_name(tmp_path):
    doc = Document()
    doc.layer("Cut").name = "Strap"
    doc.add_shape(Rectangle(width=60, height=20, layer="Strap",
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0)))
    labels = [lyr[0] for lyr in _layers(_export(doc, tmp_path))]
    assert labels == ["holes", "Strap"]


def test_the_scripting_svg_writer_is_layered_the_same():
    from leathercad.stitching import Hole, StitchResult
    from leathercad.svg import SvgDocument
    d = SvgDocument()
    d.add_cut_polyline([Vec2(0, 0), Vec2(30, 0), Vec2(30, 20), Vec2(0, 0)])
    d.add_stitches(StitchResult(holes=[Hole(Vec2(10, 5), Vec2(1, 0))]))
    root = ET.fromstring(d.to_string())
    groups = root.findall(SVG + "g")
    assert [g.get(INK + "label") for g in groups] == ["holes", "outline"]
    assert groups[0].get("stroke") == "#0000ff"

"""Regressions from the file / document audit: ids after loading, export
layers and visibility, SVG/DXF import details, estimates, parts, expressions."""

import math

import pytest

from leathercad.document import Document
from leathercad.geometry import Vec2
from leathercad.layers import Layer
from leathercad.shapes import Rectangle, Transform
from leathercad.stitchsettings import StitchSettings


def test_loading_advances_ids_so_new_shapes_never_reuse_them():
    # A new shape drawn after opening a file took the loaded "shape1" id, so
    # a parameter binding for the loaded strap drove the new square instead.
    import leathercad.shapes as shapes_mod
    from leathercad.stitchline import StitchLine
    d = Document()
    d.add_shape(Rectangle(width=10, height=10))
    d.add_shape(Rectangle(width=20, height=20, group_id="group90"))
    d.add_stitch_line(StitchLine(points=[Vec2(0, 0), Vec2(5, 0)]))
    data = d.to_dict()
    data["shapes"][0]["shape_id"] = "shape500"
    data["stitch_lines"][0]["line_id"] = "seam70"
    shapes_mod._shape_counter[0] = 0           # a fresh session
    loaded = Document.from_dict(data)
    ids = {s.shape_id for s in loaded.shapes}
    fresh = Rectangle(width=1, height=1)
    assert fresh.shape_id not in ids
    assert int(fresh.shape_id[5:]) > 500
    assert shapes_mod._next_id("group") != "group90"
    assert StitchLine(points=[]).line_id != "seam70"


def test_seam_settings_from_a_newer_version_still_load():
    from leathercad.stitchline import StitchLine
    d = Document()
    d.add_stitch_line(StitchLine(points=[Vec2(0, 0), Vec2(5, 0)]))
    data = d.to_dict()
    data["stitch_lines"][0]["settings"]["a_future_option"] = 3
    assert len(Document.from_dict(data).stitch_lines) == 1


def test_save_replaces_the_file_whole(tmp_path):
    p = tmp_path / "a.json"
    d = Document()
    d.add_shape(Rectangle(width=10, height=10))
    d.save(str(p))
    d.save(str(p))
    assert len(Document.load(str(p)).shapes) == 1
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]


def _stitched_doc():
    d = Document()
    d.add_shape(Rectangle(width=40, height=30, layer="Cut",
                          stitch=StitchSettings(pitch_mm=4.0, inset=3.0)))
    return d


def test_export_follows_layer_visibility(tmp_path):
    from leathercad.export import collect
    d = _stitched_doc()
    d.layer("Stitch").visible = False
    outlines, stitches = collect(d)
    assert len(outlines) == 1 and stitches == []       # holes hidden
    d.layer("Stitch").visible = True
    d.layer("Cut").visible = False
    outlines, stitches = collect(d)
    assert outlines == [] and stitches and stitches[0][0].count > 0


def test_dxf_keeps_custom_coloured_layers_apart(tmp_path):
    from leathercad.export import export_dxf
    d = _stitched_doc()
    d.layer("Cut").color = "#cc0000"
    score = d.layer("Score") or Layer("Score", "#ff8800", role="score")
    score.color = "#ff8800"
    if d.layer("Score") is None:
        d.layers.append(score)
    d.add_shape(Rectangle(width=10, height=10, layer="Score",
                          transform=Transform(x=60)))
    p = tmp_path / "o.dxf"
    export_dxf(d, str(p))
    lines = p.read_text().splitlines()
    on = [lines[i + 1] for i in range(len(lines) - 1)
          if lines[i] == "8"]                      # entity layer names
    assert "Cut" in on and "Score" in on and "Stitch" in on
    acis = {lines[i + 1] for i in range(len(lines) - 1) if lines[i] == "62"}
    assert "7" not in acis                         # nothing collapsed to white


def _import_svg_text(tmp_path, text):
    from leathercad.importers import import_svg
    p = tmp_path / "in.svg"
    p.write_text(text)
    return import_svg(str(p))


def _wh(sh):
    b = sh.bounds()
    return b[2] - b[0], b[3] - b[1]


@pytest.mark.parametrize("root", [
    "width='378px' height='378px' viewBox='0 0 378 378'",
    "width='378' height='378' viewBox='0 0 378 378'",
    "viewBox='0 0 378 378'",
    "",
])
def test_svg_user_units_are_css_px(tmp_path, root):
    shapes = _import_svg_text(tmp_path, f"""<svg xmlns='http://www.w3.org/2000/svg'
        {root}><rect x='0' y='0' width='378' height='378'/></svg>""")
    w, h = _wh(shapes[0])
    assert abs(w - 378 * 25.4 / 96) < 1e-6 and abs(h - 378 * 25.4 / 96) < 1e-6


def test_svg_rounded_rect_and_unit_attributes(tmp_path):
    from leathercad.offset import signed_area
    shapes = _import_svg_text(tmp_path, """<svg xmlns='http://www.w3.org/2000/svg'
        width='100mm' viewBox='0 0 100 100'>
        <rect x='0' y='0' width='60' height='40' rx='10'/>
        <circle cx='80mm' cy='80' r='5px'/></svg>""")
    pts = shapes[0].world_polyline()[0]
    area = abs(signed_area(pts[:-1] if pts[0] == pts[-1] else pts))
    # true rounded area 2314.2 (sharp would be 2400); chords shave ~2 mm²
    assert abs(area - (2400 - (4 - math.pi) * 100)) < 3.0
    # SVG units on an element are CSS units in user space (1px = 1 unit):
    # cx='80mm' = 80*96/25.4 units, r='5px' = 5 units -- here 1 unit = 1 mm
    c = shapes[1]
    left = shapes[0].bounds()[0]
    assert abs(c.transform.x - left - 80 * 96 / 25.4) < 1e-6
    assert abs(c.rx - 5.0) < 1e-6


def test_svg_big_arc_stays_within_tolerance(tmp_path):
    shapes = _import_svg_text(tmp_path, """<svg xmlns='http://www.w3.org/2000/svg'
        width='1000mm' viewBox='0 0 1000 1000'>
        <path d='M 0 300 A 300 300 0 0 1 600 300' fill='none'/></svg>""")
    pts = shapes[0].world_polyline()[0]
    cx, cy = 300.0, -300.0                     # Y-flipped centre, before origin
    xs = [p.x for p in pts]
    off = min(xs)
    sag = max(abs(math.hypot(p.x - off - cx, p.y - (min(p.y for p in pts))
                             - 0.0 - (cy + 300 + 300)) - 300) for p in pts)
    mids = [(pts[i] + pts[i + 1]) * 0.5 for i in range(len(pts) - 1)]
    c = Vec2(off + 300.0, min(p.y for p in pts))
    worst = max(abs((m - c).length() - 300.0) for m in mids)
    assert worst < 0.06 and sag >= 0


def test_svg_minified_arc_flags(tmp_path):
    shapes = _import_svg_text(tmp_path, """<svg xmlns='http://www.w3.org/2000/svg'
        width='100mm' viewBox='0 0 100 100'>
        <path d='M0 10a10 10 0 1020 0' fill='none'/></svg>""")
    w, h = _wh(shapes[0])
    assert abs(w - 20.0) < 0.01 and abs(h - 10.0) < 0.05


def test_dxf_ellipse_honours_a_back_facing_extrusion(tmp_path):
    # a quarter ellipse from the major-axis end: with a +Z normal it runs up
    # to +Y, with -Z (drawn from the back) down to -Y -- after the import
    # re-bases to the origin, that's whether the major-axis end is the low or
    # the high point of the curve
    from leathercad.importers import import_dxf

    def dxf(nz):
        ent = ["0", "ELLIPSE", "8", "0", "10", "0", "20", "0", "30", "0",
               "11", "20", "21", "0", "31", "0", "230", str(nz), "40", "0.5",
               "41", "0", "42", str(math.pi / 2)]
        return "\n".join(["0", "SECTION", "2", "ENTITIES", *ent,
                          "0", "ENDSEC", "0", "EOF"]) + "\n"
    for nz, end_is_low in ((1, True), (-1, False)):
        p = tmp_path / f"e{nz}.dxf"
        p.write_text(dxf(nz))
        pts = import_dxf(str(p))[0].world_polyline()[0]
        ys = [q.y for q in pts]
        assert max(ys) - min(ys) == pytest.approx(10.0, abs=0.05)
        tip = max(pts, key=lambda q: q.x)          # the major-axis end
        assert (abs(tip.y - min(ys)) < 1e-6) == end_is_low


def test_estimate_counts_a_cutout_as_a_hole_not_a_piece():
    from leathercad.estimate import estimate_project
    d = Document()
    d.add_shape(Rectangle(width=100, height=50, layer="Cut"))
    d.add_shape(Rectangle(width=40, height=10, layer="Cut"))     # the slot
    est = estimate_project(d)
    assert est.pieces == 1
    assert est.parts_area_mm2 == pytest.approx(5000 - 400, abs=1.0)


def test_parts_with_non_ascii_names_dont_overwrite_each_other(tmp_path):
    from leathercad_app.partslib import save_part, list_parts
    names = ["財布", "ベルト", "Strap ★", "Strap ☆"]
    for n in names:
        save_part(n, [Rectangle(width=10, height=10)], base=str(tmp_path))
    assert sorted(n for n, _p in list_parts(str(tmp_path))) == sorted(names)
    save_part("財布", [Rectangle(width=20, height=10)], base=str(tmp_path))
    assert len(list_parts(str(tmp_path))) == 4          # same name: updated


@pytest.mark.parametrize("text,value", [
    (".5in", 12.7), ("1e2", 100.0), ("1.5in + 2", 40.1), ("3cm*2", 60.0)])
def test_expressions_parse(text, value):
    from leathercad.expr import evaluate
    assert evaluate(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["10/0", "(-8)**0.5", "2**5000", "1+", "(("])
def test_bad_expressions_raise_value_error_only(text):
    from leathercad.expr import evaluate
    with pytest.raises(ValueError):
        evaluate(text)


def test_scientific_notation_is_not_a_parameter_name():
    from leathercad.expr import names_in, resolve
    assert names_in("1e2 + w") == {"w"}
    assert resolve({"a": "1e2", "b": "a / 4"}) == {"a": 100.0, "b": 25.0}

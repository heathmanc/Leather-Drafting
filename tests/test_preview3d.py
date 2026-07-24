"""3D assembly preview: document -> panels/hinges, folding, and rendering."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication          # noqa: E402

from leathercad.document import Document            # noqa: E402
from leathercad.shapes import Rectangle, Transform  # noqa: E402
from leathercad.fold3d import assemble              # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _net_doc():
    """A base panel and a wall panel that share an edge (a foldable net)."""
    doc = Document()
    # base: 40x40 centred at origin -> right edge at x = 20, y in [-20, 20]
    doc.add_shape(Rectangle(width=40, height=40, transform=Transform(x=0, y=0),
                            layer="Cut"))
    # wall: 22 wide x 40 tall, its left edge on the base's right edge (x = 20)
    doc.add_shape(Rectangle(width=22, height=40, transform=Transform(x=31, y=0),
                            layer="Cut"))
    return doc


def test_build_from_document_finds_panels_and_a_shared_hinge(qapp):
    from leathercad_app.preview3d import build_from_document
    panels, hinges = build_from_document(_net_doc())
    assert len(panels) == 2
    assert len(hinges) == 1                    # the shared edge was detected


def test_assembled_wall_lifts_off_the_base_plane(qapp):
    from leathercad_app.preview3d import build_from_document
    panels, hinges = build_from_document(_net_doc(), angle_deg=90.0)
    root = hinges[0].parent
    placed = {p.id: p for p in assemble(panels, hinges, root=root, fraction=1.0)}
    # the root stays flat; the folded wall reaches out of the z = 0 plane
    base_z = [abs(v.z) for v in placed[root].outline]
    assert max(base_z) < 1e-9
    other = next(pid for pid in placed if pid != root)
    assert max(abs(v.z) for v in placed[other].outline) > 1.0

    # and at fraction 0 the same net is perfectly flat
    flat = assemble(panels, hinges, root=root, fraction=0.0)
    assert all(abs(v.z) < 1e-9 for p in flat for v in p.outline)


def test_open_shapes_are_not_panels(qapp):
    from leathercad_app.preview3d import build_from_document
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(0, 0), Vec2(10, 0), Vec2(10, 10)],
                            close_path=False, transform=Transform(x=0, y=0)))
    panels, hinges = build_from_document(doc)
    assert panels == {} and hinges == []


def test_render_png_draws_something(qapp, tmp_path):
    from PySide6.QtGui import QImage
    from leathercad_app.preview3d import build_from_document, render_png
    panels, hinges = build_from_document(_net_doc())
    out = tmp_path / "assembly.png"
    render_png(str(out), panels, hinges, fraction=1.0, size=(240, 200))
    assert out.exists()
    img = QImage(str(out))
    assert not img.isNull() and img.width() == 240
    # some pixels differ from the flat background -> the model was drawn
    bg = img.pixel(1, 1)
    assert any(img.pixel(x, y) != bg
               for x in range(0, 240, 7) for y in range(0, 200, 7))


def test_dialog_constructs_and_slider_drives_fold(qapp):
    from leathercad_app.preview3d import build_from_document, Preview3DDialog
    panels, hinges = build_from_document(_net_doc())
    dlg = Preview3DDialog(panels, hinges)
    assert dlg.view.fraction == 1.0
    dlg.slider.setValue(0)
    assert dlg.view.fraction == 0.0
    dlg.slider.setValue(50)
    assert abs(dlg.view.fraction - 0.5) < 1e-9


def _scored_doc():
    """A single wallet blank with two vertical fold lines across it."""
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    doc = Document()
    doc.add_shape(Rectangle(width=240, height=100,
                            transform=Transform(x=120, y=50), layer="Cut"))
    for x in (80.0, 160.0):
        fl = PathShape(points=[Vec2(0, 0), Vec2(0, 100)], close_path=False,
                       transform=Transform(x=x, y=0))
        fl.fold_dir = "front"
        fl.fold_angle = 150.0
        doc.add_shape(fl)
    return doc


def test_build_scored_finds_piece_and_folds(qapp):
    from leathercad_app.preview3d import build_scored_from_document, scored_panels
    outline, folds, fold_shapes = build_scored_from_document(_scored_doc())
    assert outline and len(folds) == 2 and len(fold_shapes) == 2
    panels, hinges, order, root = scored_panels(outline, folds)
    assert [panels[o].name for o in order] == ["1", "2", "3"]
    assert root in panels


def test_scored_dialog_folds_and_reports_bend_allowance(qapp):
    from leathercad_app.preview3d import ScoredFoldDialog
    dlg = ScoredFoldDialog(_scored_doc())
    assert "Grow the flat blank" in dlg.readout.text()
    assert "panels" in dlg.readout.text()
    # changing a fold direction writes back to the shape and re-renders
    dlg._combos[0].setCurrentText("back")
    dlg._rebuild()
    fold_shapes = [s for s in dlg.doc.shapes if s.is_fold_line]
    assert any(s.fold_dir == "back" for s in fold_shapes)
    # thicker leather => larger bend allowance
    dlg.thick.setValue(1.0)
    t1 = dlg.readout.text()
    dlg.thick.setValue(6.0)
    t6 = dlg.readout.text()
    assert t1 != t6


def test_toggle_fold_line_marks_open_line(qapp):
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad_app.items import ShapeItem
    from leathercad_app import canvas as cm
    doc = Document()
    doc.add_shape(PathShape(points=[Vec2(0, 0), Vec2(0, 50)], close_path=False,
                            transform=Transform(x=10, y=0)))
    c = cm.Canvas(doc)
    c.rebuild()
    item = next(i for i in c.scene_obj.items() if isinstance(i, ShapeItem))
    assert not item.model.is_fold_line
    c.toggle_fold_line(item)
    assert item.model.is_fold_line and item.model.fold_dir == "front"
    c.toggle_fold_line(item)
    assert not item.model.is_fold_line


def test_fold_line_survives_save_load(qapp):
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    doc = Document()
    fl = PathShape(points=[Vec2(0, 0), Vec2(0, 50)], close_path=False,
                   transform=Transform(x=10, y=0))
    fl.fold_dir = "back"
    fl.fold_angle = 178.0
    doc.add_shape(fl)
    doc2 = Document.from_dict(doc.to_dict())
    folds = [s for s in doc2.shapes if s.is_fold_line]
    assert len(folds) == 1
    assert folds[0].fold_dir == "back" and folds[0].fold_angle == 178.0


def _scored_doc_closed(fold_x=90.0):
    """A wallet blank with a stitched perimeter and a fold folded flat (180)."""
    from leathercad.shapes import PathShape
    from leathercad.geometry import Vec2
    from leathercad.stitchsettings import StitchSettings
    doc = Document()
    doc.add_shape(Rectangle(width=180, height=100,
                            transform=Transform(x=90, y=50), layer="Cut",
                            stitch=StitchSettings(enabled=True, pitch_mm=5.0, inset=5.0)))
    fl = PathShape(points=[Vec2(0, 0), Vec2(0, 100)], close_path=False,
                   transform=Transform(x=fold_x, y=0))
    fl.fold_dir = "back"
    fl.fold_angle = 180.0
    doc.add_shape(fl)
    return doc


def test_scored_dialog_reports_lineup_and_flags_holes(qapp):
    from leathercad_app.preview3d import ScoredFoldDialog
    # off-centre fold => flap too short => holes flagged + a lineup warning
    dlg = ScoredFoldDialog(_scored_doc_closed(fold_x=120.0))
    assert "Lineup check" in dlg.readout.text()
    assert dlg.view.bad_holes                     # some holes flagged red
    txt = dlg.readout.text()
    assert ("short of the edge" in txt) or ("COUNT MISMATCH" in txt)


def test_scored_dialog_clean_fold_has_few_flags(qapp):
    from leathercad_app.preview3d import ScoredFoldDialog
    dlg = ScoredFoldDialog(_scored_doc_closed(fold_x=90.0))   # symmetric bifold
    flagged = sum(len(s) for s in (dlg.view.bad_holes or {}).values())
    assert flagged <= 2


def test_bend_allowance_is_shown_not_applied(qapp):
    """The dialog only REPORTS how much to grow the blank -- it never resizes
    the piece (the user grows it by hand)."""
    from leathercad.shapes import PathShape, Rectangle
    from leathercad.geometry import Vec2
    from leathercad_app.preview3d import ScoredFoldDialog
    doc = Document()
    piece = Rectangle(width=200, height=90, transform=Transform(x=100, y=45),
                      layer="Cut")
    doc.add_shape(piece)
    fl = PathShape(points=[Vec2(0, 0), Vec2(0, 90)], close_path=False,
                   transform=Transform(x=100, y=0))
    fl.fold_dir = "back"
    fl.fold_angle = 180.0
    doc.add_shape(fl)
    dlg = ScoredFoldDialog(doc)
    dlg.thick.setValue(3.0)
    # the readout tells you how much to add, and a per-fold allowance is shown
    assert "Grow the flat blank" in dlg.readout.text()
    assert any("mm" in lbl.text() for lbl in dlg._ba_labels.values())
    # ...and nothing was resized
    assert piece.width == 200 and piece.height == 90


def test_starts_flat(qapp):
    """The preview opens flat (nothing auto-folds); the slider drives it."""
    from leathercad_app.preview3d import ScoredFoldDialog
    dlg = ScoredFoldDialog(_scored_doc())
    assert dlg.slider.value() == 0 and dlg.view.fraction == 0.0


def test_fold_sequence_is_reorderable(qapp):
    """The ↑/↓ order controls the fold sequence used for nesting."""
    from leathercad_app.preview3d import ScoredFoldDialog
    dlg = ScoredFoldDialog(_scored_doc())            # two folds
    assert dlg._seq == [0, 1]
    dlg._move(1, -1)                                  # move 2nd fold up
    assert dlg._seq == [1, 0]


def test_uses_fold_line_names(qapp):
    """Rows are labelled with the fold lines' names, not generic numbers."""
    from leathercad.templates import fold_over_wallet
    from leathercad_app.preview3d import ScoredFoldDialog
    doc = fold_over_wallet()
    for s in [s for s in doc.shapes if getattr(s, "layer", "") == "Score"]:
        s.fold_dir = "front"
        s.fold_angle = 90.0
    dlg = ScoredFoldDialog(doc)
    names = {dlg._fold_name(i) for i in range(len(dlg.fold_shapes))}
    assert "Flap fold" in names and "Left wing fold" in names


def test_wallet_folds_into_four_panels_with_seam_holes(qapp):
    """The concave fold-over wallet yields 4 clean panels (no slivers) and the
    stitch holes from its SEAMS show on the model."""
    from leathercad.templates import fold_over_wallet
    from leathercad_app.preview3d import (build_scored_from_document,
                                          scored_panels, _piece_holes)
    doc = fold_over_wallet()
    for s in [s for s in doc.shapes if getattr(s, "layer", "") == "Score"]:
        s.fold_dir = "front"
        s.fold_angle = 90.0
    outline, folds, fs = build_scored_from_document(doc)
    piece = max((s for s in doc.shapes if not s.is_fold_line
                 and s.local_path().closed),
                key=lambda s: (s.bounds()[2] - s.bounds()[0]))
    holes = _piece_holes(piece, doc)
    panels, hinges, order, root = scored_panels(outline, folds, holes)
    assert len(order) == 4                            # not 6 with sliver ghosts
    assert holes and sum(len(panels[o].holes) for o in order) > 0


def test_dialog_is_per_panel_with_base_and_toggles(qapp):
    """Rows are per PANEL (labelled by the panel they move), the big piece is the
    fixed base and excluded, and a panel can be toggled off."""
    from leathercad.templates import fold_over_wallet
    from leathercad_app.preview3d import ScoredFoldDialog
    doc = fold_over_wallet()
    for s in [s for s in doc.shapes if getattr(s, "layer", "") == "Score"]:
        s.fold_dir = "front"
        s.fold_angle = 180.0
    dlg = ScoredFoldDialog(doc)
    assert dlg._base_name == "2"                       # big middle piece is base
    movers = set(dlg._mover.values())
    assert movers == {"1", "3", "4"} and "2" not in movers   # base not a mover
    # toggling a panel off marks it "(not folded)"
    fi = next(i for i in range(len(dlg.fold_shapes))
              if dlg._fold_name(i) == "Flap fold")
    dlg._checks[fi].setChecked(False)
    assert dlg._ba_labels[fi].text() == "(not folded)"


def test_fold_stack_levels_layer_the_wallet(qapp):
    """Fully folded, panels stack by where they actually land: the flap lies
    directly on the back panel (2 layers), while the two wings fold in and
    overlap so the pouch stacks 3 layers. A panel that only grazes another
    (the flap tip nicking the pouch mouth) must NOT be lifted an extra layer."""
    from leathercad.templates import fold_over_wallet
    from leathercad.fold3d import fold_stack_levels
    from leathercad_app.preview3d import build_scored_from_document, scored_panels
    doc = fold_over_wallet()
    for s in [s for s in doc.shapes if getattr(s, "layer", "") == "Score"]:
        s.fold_dir = "front"
        s.fold_angle = 180.0
    outline, folds, fs = build_scored_from_document(doc)
    panels, hinges, order, root = scored_panels(outline, folds)
    levels = fold_stack_levels(panels, hinges, folds, root)
    assert levels[root] == 0                            # base plane

    def centroid_y(pid):
        ys = [p.y for p in panels[pid].outline]
        return sum(ys) / len(ys)

    # the flap is the panel highest up the pattern (above the flap fold, y=185)
    flap = max(panels, key=centroid_y)
    assert centroid_y(flap) > 185.0
    # it folds straight onto the back panel: one layer up, i.e. 2 layers total,
    # NOT lifted over the wings
    assert levels[flap] == 1
    # the two wings fold in and overlap -> they stack on distinct levels, so the
    # pouch is 3 layers deep (base + two wings)
    wings = [p for p in panels if p != root and p != flap]
    assert sorted(levels[w] for w in wings) == [1, 2]


def test_stacked_fold_draws_bend_spines(qapp, tmp_path):
    """With levels + thickness, a fully-folded piece renders the fold "spine"
    (the bent leather that bridges two stacked layers at the fold line) so a
    fold reads as a connected fold, not a panel floating above the crease."""
    from PySide6.QtGui import QImage
    from leathercad.templates import fold_over_wallet
    from leathercad.fold3d import fold_stack_levels
    from leathercad_app.preview3d import (build_scored_from_document,
                                          scored_panels, render_png)
    doc = fold_over_wallet()
    for s in [s for s in doc.shapes if getattr(s, "layer", "") == "Score"]:
        s.fold_dir = "front"
        s.fold_angle = 180.0
    outline, folds, fs = build_scored_from_document(doc)
    panels, hinges, order, root = scored_panels(outline, folds)
    levels = fold_stack_levels(panels, hinges, folds, root)
    # the spine only shows up when neighbouring layers actually differ in z,
    # which they do once thickness > 0 and the piece is folded flat
    assert any(levels[hg.parent] != levels[hg.child] for hg in hinges)
    out = tmp_path / "stacked.png"
    render_png(str(out), panels, hinges, root=root, fraction=1.0,
               thickness=2.0, levels=levels, size=(320, 260))
    img = QImage(str(out))
    assert not img.isNull()
    bg = img.pixel(1, 1)
    assert any(img.pixel(x, y) != bg
               for x in range(0, 320, 4) for y in range(0, 260, 4))

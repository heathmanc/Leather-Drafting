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
    assert "Bend allowance" in dlg.readout.text()
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

"""Hardware library and stitch keep-outs."""

import math
import os

import pytest

from leathercad import hardware
from leathercad.document import Document
from leathercad.geometry import Vec2
from leathercad.offset import signed_area
from leathercad.shapes import Rectangle, Transform
from leathercad.stitching import holes_for_shape, stitch_shape_path
from leathercad.stitchline import StitchLine
from leathercad.stitchsettings import StitchSettings

ST = StitchSettings(pitch_mm=4.0, inset=3.0)


def test_catalogue_builds_every_entry():
    seen = set()
    for h in hardware.CATALOGUE:
        assert h.key not in seen
        seen.add(h.key)
        shapes, texts = hardware.build(h.key)
        assert shapes and not texts
        cuts = [s for s in shapes if not s.construction]
        fps = [s for s in shapes if s.construction]
        assert len(fps) == 1                      # the footprint guide
        assert len({s.group_id for s in shapes}) == 1
        tagged = [s for s in shapes if s.hardware == h.key]
        assert len(tagged) == 1 and tagged[0].keepout > 0
        if h.cut[0] == "none":
            assert not cuts
        else:
            assert cuts and all(s.layer == "Cut" and s.stitch is None
                                for s in cuts)
    assert [c for c, _rows in hardware.categories()] == hardware.CATEGORY_ORDER


def test_keyhole_is_a_round_hole_with_a_slot():
    pts = hardware._keyhole(8.0, 4.0, 10.0)
    area = abs(signed_area(pts))
    # the disc plus the slot below it (less the overlap), roughly
    assert math.pi * 16 < area < math.pi * 16 + 4 * 10
    assert min(p.y for p in pts) == -10.0


def test_stitching_keeps_clear_of_a_keepout():
    r = Rectangle(width=80, height=40, stitch=ST)
    path = r.local_path()
    full = stitch_shape_path(path, ST).count
    res = stitch_shape_path(path, ST, None, [(Vec2(0, 17), 6.0)])
    assert 0 < res.count < full and not res.closed
    d = [(h.point - Vec2(0, 17)).length() for h in res.holes]
    assert min(d) >= 6.0 - 1e-6                    # nothing under the cap
    assert sorted(d)[:2] == pytest.approx([6.0, 6.0], abs=1e-6)  # ends at it
    gaps = [(res.holes[i + 1].point - res.holes[i].point).length()
            for i in range(res.count - 1)]
    assert max(g for g in gaps if g < 11) <= 4.0 * 1.13   # the rest fitted
    # a seam stops short of it too
    sl = StitchLine(points=[Vec2(-40, 0), Vec2(40, 0)],
                    settings=StitchSettings(pitch_mm=4.0))
    kept = sl.result([(Vec2(0, 0), 6.0)])
    assert min(abs(h.point.x) for h in kept.holes) == pytest.approx(6.0)


def test_document_keepouts_follow_the_hardware():
    d = Document()
    r = Rectangle(width=80, height=40, stitch=ST)
    d.add_shape(r)
    base = holes_for_shape(r, d).count
    shapes, _ = hardware.build("snap_20")
    for s in shapes:
        s.transform.y += 17                        # on the top stitch line
        d.add_shape(s)
    assert holes_for_shape(r, d).count < base
    tag = [s for s in shapes if s.hardware][0]
    for s in shapes:
        s.transform.x += 200                       # moved far away
    assert holes_for_shape(r, d).count == base
    # the keep-out survives save / load
    back = Document.from_dict(d.to_dict())
    tb = [s for s in back.shapes if s.hardware == "snap_20"][0]
    assert tb.keepout == tag.keepout


def test_app_places_hardware_and_restitches_when_it_moves():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    win = MainWindow(Document())
    c = win.canvas
    piece = c.add_shape(Rectangle(width=80, height=40, layer="Cut", stitch=ST))
    n0 = piece.hole_count
    shapes, _ = hardware.build("rivet_m")
    c.place_shapes(shapes)                          # at the view centre
    rivet = [i for i in c.scene_obj.items() if isinstance(i, ShapeItem)
             and i.model.hardware == "rivet_m"][0]
    # put it on the top stitch line, as a user drag would
    c.scene_obj.clearSelection()
    rivet.setSelected(True)
    for s in shapes:
        s.transform.x, s.transform.y = 0.0, 17.0
    c.nudge_selected(0.0, 0.0)
    c._refresh_keepout_dependents()
    assert piece.hole_count < n0
    # the Properties panel shows (and can change) its keep-out
    props = win.properties
    props.show_selection([rivet])
    assert props.keepout.value() == pytest.approx(rivet.model.keepout)
    props.keepout.setValue(0.0)
    props.keepout.editingFinished.emit()
    assert piece.hole_count == n0
    # any shape can be given one from the canvas
    c.set_keepout([rivet], 7.0)
    assert piece.hole_count < n0

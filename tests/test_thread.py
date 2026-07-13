"""Thread length estimation for saddle stitching."""

import os

import pytest

from leathercad.geometry import Vec2
from leathercad.stitching import Hole, StitchResult
from leathercad.stitchsettings import StitchSettings
from leathercad.thread import estimate_thread, format_length


def _straight(n, pitch=4.0):
    """A straight seam of n holes, pitch apart, along X."""
    return StitchResult(holes=[Hole(Vec2(i * pitch, 0.0), Vec2(1, 0))
                               for i in range(n)])


def test_single_row_exact_math():
    # 10 holes, 4 mm pitch -> seam 36 mm; stack 3 mm; tails 150 mm each end
    est = estimate_thread(_straight(10), StitchSettings(), 3.0, 150.0)
    assert est["seam_mm"] == 36.0
    assert est["holes"] == 10
    # 2*36 (both faces) + 2*10*3 (through passes) + 2*150 (tails) = 432
    assert round(est["thread_mm"], 3) == 432.0


def test_long_seam_matches_rule_of_thumb():
    # 1 m seam: the estimate should land near the classic ~4x seam rule
    est = estimate_thread(_straight(251, 4.0), StitchSettings(), 3.0, 150.0)
    ratio = est["thread_mm"] / est["seam_mm"]
    assert 3.0 < ratio < 4.5


def test_double_row_doubles_runs():
    # aligned pairs 3 mm apart across the seam, 10 positions, 4 mm pitch
    holes = []
    for i in range(10):
        holes.append(Hole(Vec2(i * 4.0, 1.5), Vec2(1, 0)))
        holes.append(Hole(Vec2(i * 4.0, -1.5), Vec2(1, 0)))
    res = StitchResult(holes=holes)
    est = estimate_thread(res, StitchSettings(rows=2), 3.0, 150.0)
    assert est["rows"] == 2
    assert est["seam_mm"] == 72.0                     # 36 mm per row, 2 rows
    # per run: 2*36 + 2*10*3 + 2*150 = 432; two runs
    assert round(est["thread_mm"], 3) == 864.0


def test_backstitch_adds_thread():
    base = estimate_thread(_straight(10), StitchSettings(), 3.0, 150.0)
    bs = estimate_thread(_straight(10), StitchSettings(backstitch=3), 3.0, 150.0)
    assert bs["thread_mm"] > base["thread_mm"]
    # two open ends, 3 holes each: 2 * 3 * (2*4 + 2*3) = 84 extra
    assert round(bs["thread_mm"] - base["thread_mm"], 3) == 84.0


def test_empty_and_formatting():
    est = estimate_thread(StitchResult(), StitchSettings())
    assert est["thread_mm"] == 0.0
    assert format_length(432.0) == "43.2 cm"
    assert format_length(3812.0) == "3.81 m"


def test_canvas_thread_report():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from leathercad_app.mainwindow import MainWindow
    from leathercad_app.items import ShapeItem
    from leathercad.document import Document
    from leathercad.shapes import Rectangle, Transform
    QApplication.instance() or QApplication([])

    doc = Document()
    doc.add_shape(Rectangle(name="Body", width=60, height=40,
                            transform=Transform(x=0, y=0),
                            stitch=StitchSettings(pitch_mm=4.0, inset=3.0),
                            layer="Cut"))
    doc.add_shape(Rectangle(name="Plain", width=30, height=20,
                            transform=Transform(x=100, y=0), layer="Cut"))
    win = MainWindow(doc)
    win.canvas.rebuild()

    rep = win.canvas.thread_report(3.0, 150.0)
    assert "Body" in rep and "thread ≈" in rep and "Total" in rep
    assert "Plain" not in rep                        # unstitched: excluded
    assert "whole pattern" in rep                    # nothing selected

    for it in win.canvas.scene_obj.items():          # selection scopes it
        if isinstance(it, ShapeItem) and it.model.name == "Body":
            it.setSelected(True)
    assert "selection" in win.canvas.thread_report(3.0, 150.0)

    win.canvas.scene_obj.clearSelection()
    win.doc.shapes.clear()
    win.canvas.rebuild()
    assert "Nothing stitched" in win.canvas.thread_report()

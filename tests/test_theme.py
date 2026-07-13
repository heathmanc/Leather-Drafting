"""Dark theme: palette, canvas/ruler swatches, icon re-tint, persistence."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from leathercad.document import Document  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def win(qapp):
    from leathercad_app.mainwindow import MainWindow
    w = MainWindow(Document())
    yield w
    # never leave the shared QApplication (or the user's settings) dark
    w._apply_theme(False)
    w._settings().setValue("darkTheme", False)


def test_dark_palette_is_actually_dark():
    from leathercad_app.theme import dark_palette
    p = dark_palette()
    assert p.color(QPalette.Window).lightness() < 90
    assert p.color(QPalette.WindowText).lightness() > 160


def test_toggle_themes_everything(qapp, win):
    win._apply_theme(True)
    assert qapp.palette().color(QPalette.Window).lightness() < 90
    assert win.canvas.dark is True
    assert "2a2c30" in win._ruler_corner.styleSheet()
    assert win._settings().value("darkTheme", False, type=bool) is True
    win.canvas.viewport().repaint()               # dark canvas paints
    win.ruler_h.repaint()                         # dark rulers paint

    win._apply_theme(False)                       # and back
    assert qapp.palette().color(QPalette.Window).lightness() > 150
    assert win.canvas.dark is False


def test_icons_retint_for_dark(qapp, win):
    from leathercad_app import canvas as cm

    def avg_lightness(icon):
        img = icon.pixmap(22, 22).toImage()
        tot = n = 0
        for x in range(0, 22, 2):
            for y in range(0, 22, 2):
                c = img.pixelColor(x, y)
                if c.alpha() > 40:
                    tot += c.lightness()
                    n += 1
        return tot / max(n, 1)

    act = win._action_for_mode[cm.RECT]
    light = avg_lightness(act.icon())
    win._apply_theme(True)
    dark = avg_lightness(act.icon())
    assert dark > light + 60                      # strokes got much brighter


def test_theme_persists_to_next_launch(qapp):
    from leathercad_app.mainwindow import MainWindow
    w1 = MainWindow(Document())
    w1._apply_theme(True)
    try:
        w2 = MainWindow(Document())               # a "next launch"
        assert w2.act_dark.isChecked()
        assert w2.canvas.dark is True
    finally:
        w1._apply_theme(False)
        w1._settings().setValue("darkTheme", False)

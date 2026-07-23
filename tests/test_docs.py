"""The user guide must exist and stay in sync with the program.

These tests pin the documentation to the code: every tool, shortcut and key
menu command must appear in docs/USER_GUIDE.md, and defaults quoted in the
guide must match the real defaults. Change a shortcut without updating the
manual and this fails.
"""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "USER_GUIDE.md"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def guide_text():
    assert GUIDE.exists(), "docs/USER_GUIDE.md is missing"
    return GUIDE.read_text(encoding="utf-8")


def test_guide_is_substantial(guide_text):
    assert len(guide_text) > 10_000
    for section in ("Quick start", "Drawing tools", "Stitching",
                    "Keyboard shortcuts", "Troubleshooting", "Glossary",
                    "Tutorial 1", "Tutorial 2", "Tutorial 3"):
        assert section in guide_text, f"guide is missing section: {section}"


def test_every_tool_and_shortcut_documented(guide_text):
    from leathercad_app.mainwindow import TOOLS
    for label, _mode, key in TOOLS:
        assert label in guide_text, f"tool not documented: {label}"
        assert f"`{key}`" in guide_text, f"shortcut not documented: {key} ({label})"


def test_key_menu_commands_documented(guide_text):
    for cmd in ("Export PDF (1:1, tiled)", "Offset / seam allowance",
                "Make back piece (mirror)", "Ungroup stitching → individual holes",
                "Group holes into shape", "Join / weld segments",
                "Break apart into segments", "Convert to editable nodes",
                "Check back-to-back symmetry", "Reset panels", "Drag to draw",
                "New from template", "Open recent", "Dark theme",
                "Check seam mates", "Tracing image", "Parts library",
                "Thread estimate", "Circles → stitch holes",
                "Area / leather usage", "Nest on sheet", "Parameters",
                "Card pocket stack", "Zipper opening"):
        assert cmd in guide_text, f"menu command not documented: {cmd}"


def test_every_template_documented(guide_text):
    from leathercad.templates import TEMPLATES
    for label, _builder in TEMPLATES:
        assert label in guide_text, f"template not documented: {label}"


def test_documented_defaults_match_code(guide_text):
    from leathercad.stitchsettings import StitchSettings
    st = StitchSettings()
    assert f"{st.pitch_mm:g} mm" in guide_text        # default pitch quoted right
    assert f"{st.inset:g} mm" in guide_text           # default inset quoted right


def test_packaging_files_present(guide_text):
    """The standalone-app build kit exists and is documented."""
    root = GUIDE.parent.parent
    for f in ("packaging/leather-drafting.spec", "packaging/launch.py",
              "packaging/build_macos.sh", "packaging/build_windows.bat",
              "packaging/build_linux.sh"):
        assert (root / f).exists(), f"missing {f}"
    spec = (root / "packaging/leather-drafting.spec").read_text()
    assert "USER_GUIDE.md" in spec          # the guide ships inside the app
    assert "build_macos.sh" in guide_text   # and the guide explains building


def test_help_dialog_renders_guide(qapp):
    from leathercad_app.helpdialog import HelpDialog
    dlg = HelpDialog()
    body = dlg.browser.toPlainText()
    assert "pricking-iron" in body or "chord" in body.lower()
    assert len(body) > 5_000                          # the real guide, not an error


def test_mainwindow_help_menu(qapp):
    from leathercad_app.mainwindow import MainWindow
    from leathercad.document import Document
    win = MainWindow(Document())
    win.show_help()                                   # F1 target
    assert win._help_dialog is not None
    assert len(win._help_dialog.browser.toPlainText()) > 5_000


def test_branding_is_stitch_hero(qapp):
    """The product name and app icon are wired up consistently."""
    from leathercad_app.mainwindow import MainWindow, app_icon
    from leathercad.document import Document
    win = MainWindow(Document())
    assert win.windowTitle().startswith("Stitch Hero")
    assert not win.windowIcon().isNull()              # bundled resources/appicon.png
    assert not app_icon().isNull()

    root = GUIDE.parent.parent
    assert (root / "leathercad_app" / "resources" / "appicon.png").exists()
    for f in ("packaging/icons/StitchHero.ico", "packaging/icons/StitchHero.icns"):
        assert (root / f).exists(), f"missing {f}"
    spec = (root / "packaging/leather-drafting.spec").read_text()
    assert "StitchHero.ico" in spec and "StitchHero.icns" in spec
    assert 'name="Stitch Hero"' in spec               # PyInstaller output name

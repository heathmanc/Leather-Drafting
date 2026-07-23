# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build a double-clickable Stitch Hero app.

Run from the packaging/ directory:   pyinstaller --noconfirm leather-drafting.spec
Output: dist/Stitch Hero/  (and dist/Stitch Hero.app on macOS)

The user guide (docs/) and the app icon are bundled so Help -> User guide (F1)
and the window icon work offline.
"""

import os

block_cipher = None
HERE = os.path.abspath(os.getcwd())
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "packaging" else HERE

ICO = os.path.join(ROOT, "packaging", "icons", "StitchHero.ico")
ICNS = os.path.join(ROOT, "packaging", "icons", "StitchHero.icns")

# macOS build architecture. Left native for local builds; CI sets
# SH_TARGET_ARCH=universal2 so one Apple-Silicon runner produces a fat app
# that runs on both Intel and M-series Macs (PySide6 ships universal2 wheels).
TARGET_ARCH = os.environ.get("SH_TARGET_ARCH") or None

a = Analysis(
    [os.path.join(ROOT, "packaging", "launch.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "docs", "USER_GUIDE.md"), "docs"),
        (os.path.join(ROOT, "docs", "app.png"), "docs"),
        (os.path.join(ROOT, "docs", "individual_holes.png"), "docs"),
        (os.path.join(ROOT, "leathercad_app", "resources", "appicon.png"),
         "leathercad_app/resources"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Stitch Hero",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed app: no terminal appears
    disable_windowed_traceback=False,
    target_arch=TARGET_ARCH,
    icon=ICO,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Stitch Hero",
)

# macOS: also wrap the folder build into a proper .app bundle
app = BUNDLE(
    coll,
    name="Stitch Hero.app",
    icon=ICNS,
    bundle_identifier="com.stitchhero.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": "0.2.0",
        "NSHumanReadableCopyright": "MIT license",
    },
)

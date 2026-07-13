# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build a double-clickable Leather-Drafting app.

Run from the packaging/ directory:   pyinstaller --noconfirm leather-drafting.spec
Output: dist/Leather-Drafting/  (and dist/Leather-Drafting.app on macOS)

The user guide (docs/) is bundled so Help -> User guide (F1) works offline.
"""

import os

block_cipher = None
HERE = os.path.abspath(os.getcwd())
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "packaging" else HERE

a = Analysis(
    [os.path.join(ROOT, "packaging", "launch.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "docs", "USER_GUIDE.md"), "docs"),
        (os.path.join(ROOT, "docs", "app.png"), "docs"),
        (os.path.join(ROOT, "docs", "individual_holes.png"), "docs"),
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
    name="Leather-Drafting",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # windowed app: no terminal appears
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Leather-Drafting",
)

# macOS: also wrap the folder build into a proper .app bundle
app = BUNDLE(
    coll,
    name="Leather-Drafting.app",
    icon=None,
    bundle_identifier="com.leatherdrafting.app",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleShortVersionString": "0.2.0",
        "NSHumanReadableCopyright": "MIT license",
    },
)

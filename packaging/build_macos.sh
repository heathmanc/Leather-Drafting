#!/usr/bin/env bash
# Build a double-clickable Stitch Hero.app on macOS.
#
#   cd packaging && ./build_macos.sh
#
# Result: dist/Stitch Hero.app  — zip it and send it to anyone.
# (Recipients may need to right-click -> Open the first time, because the
# app is unsigned. Code signing/notarisation needs an Apple developer ID.)
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller "PySide6>=6.5"
python3 -m pip install -e ..            # the leathercad packages themselves

pyinstaller --noconfirm leather-drafting.spec

# verify the build actually launches and exits cleanly
"dist/Stitch Hero/Stitch Hero" --smoke && echo "BUILD OK: dist/Stitch Hero.app"

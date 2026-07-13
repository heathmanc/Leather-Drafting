#!/usr/bin/env bash
# Build a double-clickable Leather-Drafting.app on macOS.
#
#   cd packaging && ./build_macos.sh
#
# Result: dist/Leather-Drafting.app  — zip it and send it to anyone.
# (Recipients may need to right-click -> Open the first time, because the
# app is unsigned. Code signing/notarisation needs an Apple developer ID.)
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller "PySide6>=6.5"
python3 -m pip install -e ..            # the leathercad packages themselves

pyinstaller --noconfirm leather-drafting.spec

# verify the build actually launches and exits cleanly
"dist/Leather-Drafting/Leather-Drafting" --smoke && echo "BUILD OK: dist/Leather-Drafting.app"

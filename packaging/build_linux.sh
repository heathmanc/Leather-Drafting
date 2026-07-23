#!/usr/bin/env bash
# Build a standalone Stitch Hero binary on Linux.
#
#   cd packaging && ./build_linux.sh
#
# Result: dist/Stitch Hero/Stitch Hero
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller "PySide6>=6.5"
python3 -m pip install -e ..

pyinstaller --noconfirm leather-drafting.spec

# verify the build actually launches and exits cleanly
QT_QPA_PLATFORM=offscreen "dist/Stitch Hero/Stitch Hero" --smoke \
  && echo "BUILD OK: dist/Stitch Hero/"

#!/usr/bin/env bash
# Build a standalone Leather-Drafting binary on Linux.
#
#   cd packaging && ./build_linux.sh
#
# Result: dist/Leather-Drafting/Leather-Drafting
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --upgrade pyinstaller "PySide6>=6.5"
python3 -m pip install -e ..

pyinstaller --noconfirm leather-drafting.spec

# verify the build actually launches and exits cleanly
QT_QPA_PLATFORM=offscreen "dist/Leather-Drafting/Leather-Drafting" --smoke \
  && echo "BUILD OK: dist/Leather-Drafting/"

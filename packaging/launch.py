"""PyInstaller entry point.

A tiny absolute-import launcher: PyInstaller runs its entry script as
__main__, which would break leathercad_app/__main__.py's relative imports.
"""

import sys

from leathercad_app.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

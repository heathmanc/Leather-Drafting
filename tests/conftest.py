"""Set-up shared by every test.

The app keeps its settings (``QSettings("Stitch Hero", "Stitch Hero")``)
and its data under the user's own folders. Tests must neither read nor
write those: a setting left behind by one run (a dark theme, say) changes
the next, and two runs at once get in each other's way. So, before any Qt
object exists, both are pointed at a folder made for this run and thrown
away after it.
"""

import atexit
import os
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="stitch-hero-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_HOME, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_HOME, "data")
atexit.register(shutil.rmtree, _HOME, True)

try:                                # the engine's tests run without Qt
    from PySide6.QtCore import QSettings
except ImportError:                 # pragma: no cover
    QSettings = None

if QSettings is not None:
    # Where the native settings are not files (the registry, a plist),
    # keep them in files too, under the same folder.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat,
                      QSettings.Scope.UserScope,
                      os.environ["XDG_CONFIG_HOME"])

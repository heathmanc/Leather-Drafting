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
    from PySide6 import QtCore
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
    QSettings.setPath(QSettings.Format.IniFormat,
                      QSettings.Scope.SystemScope,
                      os.path.join(_HOME, "system-config"))

    class _InFiles(QSettings):
        """setDefaultFormat() reaches QSettings() but not the form the app
        uses, QSettings(organization, application), which always takes the
        native store: on Windows a run read and wrote the user's own
        settings in the registry. Here that form, and (scope,
        organization, application), are kept in the files above too."""

        def __init__(self, *args, **kwargs):
            if args and isinstance(args[0], str) and (
                    len(args) == 1 or isinstance(args[1], str)):
                args = (QSettings.Format.IniFormat,
                        QSettings.Scope.UserScope) + args
            elif (len(args) >= 2 and isinstance(args[0], QSettings.Scope)
                  and isinstance(args[1], str)):
                args = (QSettings.Format.IniFormat,) + args
            super().__init__(*args, **kwargs)

    # what `from PySide6.QtCore import QSettings` gives from here on
    QtCore.QSettings = _InFiles

    # The folders. Qt finds Documents without asking XDG_*, and on Windows
    # the app's data folder too -- and the library's usual folder is in
    # the user's Documents. So each standard folder is one of this run's.
    _QSP = QtCore.QStandardPaths
    _real_writable = _QSP.writableLocation
    _real_standard = _QSP.standardLocations
    _OURS = {}
    for _name in ("HomeLocation", "DocumentsLocation", "DesktopLocation",
                  "PicturesLocation", "DownloadLocation", "MusicLocation",
                  "MoviesLocation", "AppDataLocation", "AppLocalDataLocation",
                  "AppConfigLocation", "ConfigLocation", "GenericDataLocation",
                  "GenericConfigLocation", "CacheLocation",
                  "GenericCacheLocation", "StateLocation",
                  "GenericStateLocation"):
        _where = getattr(_QSP.StandardLocation, _name, None)
        if _where is not None:
            _OURS[_where] = os.path.join(_HOME, "folders",
                                         _name[:-len("Location")])

    def _writable(location):
        path = _OURS.get(location)
        if path is None:
            return _real_writable(location)
        os.makedirs(path, exist_ok=True)
        return path

    def _standard(location):
        if location in _OURS:
            return [_writable(location)]
        return _real_standard(location)

    _QSP.writableLocation = staticmethod(_writable)
    _QSP.standardLocations = staticmethod(_standard)

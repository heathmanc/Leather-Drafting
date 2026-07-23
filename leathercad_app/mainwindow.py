"""The application main window: canvas + docks + toolbar + menus + export."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, QSettings, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QIcon, QKeySequence,
                           QPixmap)
from PySide6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog, QToolBar, QLabel, QMessageBox,
    QWidget, QScrollArea, QComboBox, QInputDialog, QDoubleSpinBox,
    QToolButton, QMenu, QColorDialog,
)

from leathercad.document import Document
from leathercad import export
from . import canvas as canvas_mod
from .canvas import Canvas
from .panels import PropertiesPanel, LayersPanel
from .mathspin import NoWheelComboBox, NoWheelSpinBox
from .history import History
from .icons import tool_icon


def app_icon() -> QIcon:
    """The window/taskbar icon, loaded from the bundled resources (works in
    both a source checkout and a PyInstaller build)."""
    png = Path(__file__).resolve().parent / "resources" / "appicon.png"
    return QIcon(str(png)) if png.exists() else QIcon()


TOOLS = [
    ("Select / Move", canvas_mod.SELECT, "S"),
    ("Rectangle", canvas_mod.RECT, "R"),
    ("Rounded rect", canvas_mod.ROUNDED, "O"),
    ("Ellipse", canvas_mod.ELLIPSE, "E"),
    ("Circle", canvas_mod.CIRCLE, "C"),
    ("Polygon", canvas_mod.POLYGON, "P"),
    ("Pen (bezier curve)", canvas_mod.PEN, "B"),
    ("Circle (2-point)", canvas_mod.CIRCLE2, "2"),
    ("Circle (3-point)", canvas_mod.CIRCLE3, "3"),
    ("Arc (3-point)", canvas_mod.ARC3, "4"),
    ("Arc (centre)", canvas_mod.ARCCENTER, "5"),
    ("Line", canvas_mod.LINE, "L"),
    ("Construction line", canvas_mod.CONSTRUCTION, "G"),
    ("Hole", canvas_mod.HOLE, "H"),
    ("Slot", canvas_mod.SLOT, "T"),
    ("Score line", canvas_mod.SCORE, "K"),
    ("Stitch line (seam)", canvas_mod.STITCHLINE, "M"),
    ("Trim to intersections", canvas_mod.TRIM, "X"),
    ("Fillet / chamfer corner", canvas_mod.FILLET, "6"),
    ("Extend to intersection", canvas_mod.EXTEND, "7"),
    ("Offset outline", canvas_mod.OFFSET, "8"),
    ("Text", canvas_mod.TEXT, "A"),
    ("Measure", canvas_mod.MEASURE, "Q"),
    ("Dimension", canvas_mod.DIMENSION, "D"),
]

_ICON_FOR = {
    canvas_mod.SELECT: "select", canvas_mod.RECT: "rect",
    canvas_mod.ROUNDED: "rounded", canvas_mod.ELLIPSE: "ellipse",
    canvas_mod.CIRCLE: "circle", canvas_mod.POLYGON: "polygon",
    canvas_mod.HOLE: "hole", canvas_mod.SLOT: "slot",
    canvas_mod.SCORE: "score", canvas_mod.STITCHLINE: "stitchline",
    canvas_mod.TRIM: "trim", canvas_mod.LINE: "line",
    canvas_mod.CONSTRUCTION: "construction",
    canvas_mod.MEASURE: "measure", canvas_mod.DIMENSION: "dimension",
    canvas_mod.TEXT: "text", canvas_mod.PEN: "pen",
    canvas_mod.CIRCLE2: "circle2", canvas_mod.CIRCLE3: "circle3",
    canvas_mod.ARC3: "arc", canvas_mod.ARCCENTER: "arc",
    canvas_mod.FILLET: "fillet", canvas_mod.EXTEND: "extend",
    canvas_mod.OFFSET: "offset",
}

_TOOL_BY_MODE = {mode: (label, key) for label, mode, key in TOOLS}

# Palette layout: a bare mode is a single button; a ("label", "icon", [modes])
# tuple is a fan-out flyout button (click the arrow to pick a variant; the button
# then remembers your choice). Keeps the vertical tool palette uncluttered.
TOOL_LAYOUT = [
    canvas_mod.SELECT,
    ("Rectangles", "rect", [canvas_mod.RECT, canvas_mod.ROUNDED]),
    ("Circles & ellipse", "circle",
     [canvas_mod.CIRCLE, canvas_mod.ELLIPSE, canvas_mod.CIRCLE2, canvas_mod.CIRCLE3]),
    ("Arcs", "arc", [canvas_mod.ARC3, canvas_mod.ARCCENTER]),
    canvas_mod.POLYGON,
    canvas_mod.PEN,
    canvas_mod.LINE,
    canvas_mod.CONSTRUCTION,
    canvas_mod.HOLE,
    canvas_mod.SLOT,
    canvas_mod.SCORE,
    canvas_mod.STITCHLINE,
    canvas_mod.TRIM,
    canvas_mod.FILLET,
    canvas_mod.EXTEND,
    canvas_mod.OFFSET,
    canvas_mod.TEXT,
    canvas_mod.MEASURE,
    canvas_mod.DIMENSION,
]


class MainWindow(QMainWindow):
    def __init__(self, document: Optional[Document] = None):
        super().__init__()
        self.doc = document or Document()
        self.path: Optional[str] = None
        self.setWindowTitle("Stitch Hero")
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)

        self.canvas = Canvas(self.doc)
        self.setCentralWidget(self._wrap_with_rulers(self.canvas))
        # every MathSpinBox in the app can use this document's parameters
        from . import mathspin
        mathspin.set_params_provider(lambda: self.doc.param_values())

        self.properties = PropertiesPanel(self.canvas)
        self.layers = LayersPanel(self.canvas)
        self.properties.set_layers(self.doc.layers)

        self._make_docks()
        self._make_tool_palette()
        self._make_action_toolbar()
        self._make_menus()
        self._make_statusbar()

        self.canvas.selectionChangedSig.connect(self._selection_changed)
        self.canvas.documentChangedSig.connect(self._document_changed)
        # synchronous on every move (documentChangedSig is rate-limited during
        # drags): the geometry fields must never lag a canvas edit, or a later
        # _apply would write a stale position back
        self.canvas.geometryMovedSig.connect(
            lambda: self.properties.sync_geometry_fields())
        self.canvas.toolFinished.connect(self._tool_finished)
        self.canvas.requestSelectTool.connect(
            lambda: self._select_mode(canvas_mod.SELECT))
        self.canvas.cursorMoved.connect(self._cursor_moved)
        self.canvas.statusMessage.connect(self.sb_dims.setText)
        self.canvas.commitRequested.connect(self.commit)
        self.properties.committed.connect(self.commit)
        self.layers.committed.connect(self.commit)
        self.layers.currentLayerChanged.connect(self._layer_changed)

        # Build canvas items for any shapes already in the document.
        self.canvas.rebuild()
        self.history = History()
        self.history.reset(self.doc.to_dict())
        self._update_undo_actions()
        self._restore_ui_state()

        # crash safety: track unsaved edits and autosave them periodically.
        self._unsaved_changes = False      # since the last save/open/new
        self._dirty_for_autosave = False   # since the last autosave write
        self.autosave_dir: Optional[str] = None   # None -> app-data dir
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(120_000)          # every 2 minutes
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start()

        self._update_title()

    def _wrap_with_rulers(self, canvas) -> QWidget:
        """The canvas framed by mm rulers (top + left) and a corner box."""
        from PySide6.QtWidgets import QGridLayout
        from .rulers import Ruler, THICKNESS
        frame = QWidget()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        corner = QLabel("mm")
        corner.setFixedSize(THICKNESS, THICKNESS)
        corner.setAlignment(Qt.AlignCenter)
        corner.setStyleSheet("background:#f6f6f4;color:#6e6e70;font-size:9px;")
        self.ruler_h = Ruler(canvas, horizontal=True)
        self.ruler_v = Ruler(canvas, horizontal=False)
        grid.addWidget(corner, 0, 0)
        grid.addWidget(self.ruler_h, 0, 1)
        grid.addWidget(self.ruler_v, 1, 0)
        grid.addWidget(canvas, 1, 1)
        self._ruler_corner = corner
        return frame

    def _set_rulers_visible(self, on: bool) -> None:
        for w in (self.ruler_h, self.ruler_v, self._ruler_corner):
            w.setVisible(on)
        self._settings().setValue("rulersVisible", on)

    def _apply_theme(self, dark: bool) -> None:
        """Switch the whole app between light and dark: widget chrome (Fusion
        palette), canvas paper/grid/axis, rulers, and re-tinted tool icons."""
        from .theme import apply_app_theme, RULER
        apply_app_theme(dark)
        # Make the dock resize dividers easy to see and grab -- the default
        # separator is a nearly-invisible hairline.
        sep = "rgba(120, 124, 132, 0.75)" if dark else "rgba(150, 152, 158, 0.6)"
        self.setStyleSheet(
            "QMainWindow::separator { background: %s; width: 6px; height: 6px; }"
            "QMainWindow::separator:hover { background: rgba(42,130,218,0.85); }"
            % sep)
        self.canvas.set_dark_theme(dark)
        self._ruler_corner.setStyleSheet(RULER[dark]["corner_css"])
        self.ruler_h.update()
        self.ruler_v.update()
        for mode, act in self._action_for_mode.items():
            act.setIcon(tool_icon(_ICON_FOR.get(mode, "rect"), dark=dark))
        self.act_pin.setIcon(tool_icon("pin", dark=dark))
        self.act_ungroup.setIcon(tool_icon("ungroup", dark=dark))
        self._settings().setValue("darkTheme", dark)

    # -- persist toolbar/window layout across sessions ------------------
    def _settings(self) -> QSettings:
        return QSettings("Stitch Hero", "Stitch Hero")

    def _restore_ui_state(self):
        s = self._settings()
        geo = s.value("geometry")
        state = s.value("windowState")
        if geo is not None:
            self.restoreGeometry(geo)
        if state is not None:
            self.restoreState(state)
        self.act_pin.setChecked(s.value("toolbarPinned", False, type=bool))
        drag = s.value("dragToDraw", False, type=bool)
        self.canvas.drag_to_draw = drag
        self.act_drag_draw.setChecked(drag)

    def _confirm_discard(self, verb: str = "closing") -> bool:
        """Offer to save unsaved work before it would be lost. True = proceed.
        Only prompts when the window is actually shown to a user -- offscreen /
        test windows proceed silently."""
        if not (self._unsaved_changes and self.isVisible()):
            return True
        ans = QMessageBox.question(
            self, "Unsaved changes",
            f"Save your changes before {verb}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if ans == QMessageBox.StandardButton.Cancel:
            return False
        if ans == QMessageBox.StandardButton.Save:
            self.save_document()
            return not self._unsaved_changes   # user may cancel the Save dialog
        return True

    def closeEvent(self, event):
        if not self._confirm_discard("closing"):
            event.ignore()
            return
        s = self._settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("toolbarPinned", self.act_pin.isChecked())
        s.setValue("dragToDraw", self.act_drag_draw.isChecked())
        self.remove_autosave()                 # clean exit -> nothing to recover
        super().closeEvent(event)

    # -- autosave & crash recovery ---------------------------------------
    def _autosave_path(self) -> Path:
        if self.autosave_dir:
            d = Path(self.autosave_dir)
            d.mkdir(parents=True, exist_ok=True)
        else:
            from .robustness import app_data_dir
            d = app_data_dir()
        return d / "autosave.json"

    def write_autosave(self) -> Path:
        """Snapshot the document (with its source path) for crash recovery."""
        p = self._autosave_path()
        wrapper = {
            "autosave": 1,
            "source_path": self.path,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "doc": self.doc.to_dict(),
        }
        p.write_text(json.dumps(wrapper), encoding="utf-8")
        return p

    def remove_autosave(self) -> None:
        try:
            self._autosave_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _autosave_tick(self) -> None:
        if not self._dirty_for_autosave:
            return
        try:
            self.write_autosave()
            self._dirty_for_autosave = False
            self.statusBar().showMessage("Autosaved", 1500)
        except Exception:      # autosave must never take the app down
            pass

    def maybe_recover_autosave(self, ask=None) -> bool:
        """If a crash left an autosave behind, offer to restore it.

        ``ask(source_path, saved_at) -> bool`` decides (a dialog by default).
        The autosave file is consumed either way. Returns True if restored.
        """
        p = self._autosave_path()
        if not p.exists():
            return False
        try:
            wrapper = json.loads(p.read_text(encoding="utf-8"))
            doc = Document.from_dict(wrapper["doc"])
        except Exception:
            self.remove_autosave()             # corrupt -> discard quietly
            return False
        source = wrapper.get("source_path")
        saved_at = wrapper.get("saved_at", "")
        if ask is None:                        # pragma: no cover - GUI dialog
            name = os.path.basename(source) if source else "an unsaved pattern"
            ans = QMessageBox.question(
                self, "Recover unsaved work?",
                f"Stitch Hero didn't close cleanly last time.\n\n"
                f"Restore the auto-saved copy of {name}"
                f"{f' from {saved_at}' if saved_at else ''}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            accept = ans == QMessageBox.StandardButton.Yes
        else:
            accept = bool(ask(source, saved_at))
        self.remove_autosave()
        if not accept:
            return False
        self._adopt_document(doc, source, fit=True)
        self._unsaved_changes = True           # recovered != saved on disk
        self._update_title()
        return True

    def _adopt_document(self, doc: Document, path: Optional[str],
                        fit: bool = False) -> None:
        """Swap in a new/loaded document and refresh every view of it."""
        self.doc = doc
        self.path = path
        self.canvas.doc = doc
        self.canvas.rebuild()
        self.layers.canvas = self.canvas
        self.layers.reload()
        self.properties.set_layers(doc.layers)
        if fit:
            self.canvas.fit_to_content()
        self.history.reset(doc.to_dict())
        self._update_undo_actions()
        self._sync_kerf_spin()
        self._unsaved_changes = False
        self._dirty_for_autosave = False
        self._update_title()

    def _sync_kerf_spin(self):
        if hasattr(self, "kerf_spin"):
            self.kerf_spin.blockSignals(True)
            self.kerf_spin.setValue(getattr(self.doc, "kerf", 0.0))
            self.kerf_spin.blockSignals(False)

    # -- UI construction ------------------------------------------------
    def _make_docks(self):
        d1 = QDockWidget("Properties", self)
        d1.setObjectName("PropertiesDock")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.properties)
        d1.setWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, d1)

        d2 = QDockWidget("Layers", self)
        d2.setObjectName("LayersDock")
        d2.setWidget(self.layers)
        self.addDockWidget(Qt.RightDockWidgetArea, d2)

        from .partspanel import PartsPanel
        self.parts = PartsPanel(self.canvas)
        d3 = QDockWidget("Parts", self)
        d3.setObjectName("PartsDock")
        d3.setWidget(self.parts)
        self.addDockWidget(Qt.RightDockWidgetArea, d3)

        # keep references so the View menu can toggle them back after closing
        self.properties_dock = d1
        self.layers_dock = d2
        self.parts_dock = d3

    def _make_tool_palette(self):
        """Traditional vertical tool palette, docked left, drag/float/pinnable."""
        tb = QToolBar("Tools")
        tb.setObjectName("ToolPalette")
        tb.setMovable(True)
        tb.setFloatable(True)
        tb.setAllowedAreas(Qt.LeftToolBarArea | Qt.RightToolBarArea
                           | Qt.TopToolBarArea | Qt.BottomToolBarArea)
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.setIconSize(QSize(22, 22))
        self.addToolBar(Qt.LeftToolBarArea, tb)
        self._tool_palette = tb

        # Pin toggle: locks the palette in place (removes the drag handle).
        self.act_pin = QAction(tool_icon("pin"), "Pin toolbar", self)
        self.act_pin.setCheckable(True)
        self.act_pin.setToolTip("Pin the toolbar in place (lock/unlock dragging)")
        self.act_pin.toggled.connect(
            lambda on: self._tool_palette.setMovable(not on))
        tb.addAction(self.act_pin)
        tb.addSeparator()

        self._tool_group = QActionGroup(self)      # exclusive: one tool at a time
        self._action_for_mode = {}
        self._group_button_for_action = {}         # action -> its flyout button

        def make_action(mode):
            label, key = _TOOL_BY_MODE[mode]
            act = QAction(tool_icon(_ICON_FOR.get(mode, "rect")), label, self)
            act.setCheckable(True)
            if key:
                act.setShortcut(QKeySequence(key))
            act.setToolTip(f"{label}  ({key})")
            act.triggered.connect(
                lambda checked, m=mode, a=act: self._tool_triggered(m, a))
            self._tool_group.addAction(act)
            self.addAction(act)                    # keep the shortcut window-wide
            self._action_for_mode[mode] = act
            return act

        for entry in TOOL_LAYOUT:
            if isinstance(entry, str):
                tb.addAction(make_action(entry))
                continue
            glabel, gicon, modes = entry
            btn = QToolButton()
            btn.setPopupMode(QToolButton.MenuButtonPopup)
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
            btn.setToolTip(f"{glabel} (click ▸ for variants)")
            menu = QMenu(btn)
            acts = [make_action(m) for m in modes]
            for a in acts:
                menu.addAction(a)
                self._group_button_for_action[a] = btn
            btn.setMenu(menu)
            btn.setDefaultAction(acts[0])          # the front / remembered variant
            tb.addWidget(btn)

        self._action_for_mode[canvas_mod.SELECT].setChecked(True)

    def _make_action_toolbar(self):
        """Top toolbar for edit actions and snapping controls."""
        tb = QToolBar("Actions")
        tb.setObjectName("ActionToolbar")
        tb.setMovable(True)
        self.addToolBar(Qt.TopToolBarArea, tb)

        self.act_undo_tb = QAction(self.style().standardIcon(
            self.style().StandardPixmap.SP_ArrowBack), "Undo", self)
        self.act_undo_tb.triggered.connect(self.undo)
        self.act_redo_tb = QAction(self.style().standardIcon(
            self.style().StandardPixmap.SP_ArrowForward), "Redo", self)
        self.act_redo_tb.triggered.connect(self.redo)
        tb.addAction(self.act_undo_tb)
        tb.addAction(self.act_redo_tb)
        tb.addSeparator()

        dup = QAction("Duplicate", self)
        dup.triggered.connect(self.canvas.duplicate_selected)
        tb.addAction(dup)
        dele = QAction("Delete", self)
        dele.triggered.connect(self.canvas.delete_selected)
        tb.addAction(dele)
        self.act_group = QAction("Group", self)
        self.act_group.setToolTip("Group selected items so they move together")
        self.act_group.triggered.connect(self.canvas.make_group)
        tb.addAction(self.act_group)
        self.act_ungroup = QAction(tool_icon("ungroup"), "Ungroup", self)
        self.act_ungroup.setToolTip("Break the selected move-group apart")
        self.act_ungroup.triggered.connect(self.canvas.ungroup_group)
        tb.addAction(self.act_ungroup)
        fit = QAction("Fit", self)
        fit.triggered.connect(self.canvas.fit_to_content)
        tb.addAction(fit)

        tb.addSeparator()
        self.act_snap_nodes = QAction("Nodes", self)
        self.act_snap_nodes.setCheckable(True)
        self.act_snap_nodes.setChecked(True)
        self.act_snap_nodes.setToolTip(
            "Node snap: ends, midpoints, centres and intersections (+ alignment "
            "guides). Independent of grid snap.")
        self.act_snap_nodes.toggled.connect(
            lambda on: setattr(self.canvas, "snap_to_nodes", on))
        tb.addAction(self.act_snap_nodes)

        self.act_snap_grid = QAction("Grid", self)
        self.act_snap_grid.setCheckable(True)
        self.act_snap_grid.setChecked(True)
        self.act_snap_grid.setToolTip("Grid snap: round points to the grid. "
                                      "Turn off to use node snap only.")
        self.act_snap_grid.toggled.connect(
            lambda on: setattr(self.canvas, "snap_to_grid", on))
        tb.addAction(self.act_snap_grid)

        tb.addWidget(QLabel(" grid "))
        self.grid_combo = NoWheelComboBox()
        for mm in (0.5, 1.0, 2.0, 2.5, 5.0, 10.0):
            self.grid_combo.addItem(f"{mm:g} mm", mm)
        self.grid_combo.setCurrentIndex(1)  # 1 mm
        self.grid_combo.currentIndexChanged.connect(
            lambda: setattr(self.canvas, "snap_grid", self.grid_combo.currentData()))
        tb.addWidget(self.grid_combo)

        tb.addSeparator()
        tb.addWidget(QLabel(" line "))
        from .mathspin import MathSpinBox
        self.line_width_spin = MathSpinBox()
        self.line_width_spin.setRange(0.3, 8.0)
        self.line_width_spin.setSingleStep(0.5)
        self.line_width_spin.setDecimals(1)
        self.line_width_spin.setSuffix(" px")
        self.line_width_spin.setToolTip("On-screen outline / line stroke width")
        w = self._settings().value("lineWidth", 1.0, type=float)
        self.line_width_spin.setValue(w)
        self.canvas.line_width = w
        self.line_width_spin.valueChanged.connect(self._line_width_changed)
        tb.addWidget(self.line_width_spin)

        # on-screen drawing colour: pick a bright colour for tracing, or fall
        # back to the layer colours. Display only -- export uses layer/role.
        self.draw_color_btn = QToolButton()
        self.draw_color_btn.setToolTip(
            "Colour of the line/outline WHILE you draw — pick something bright\n"
            "for tracing. The finished shape uses its layer colour.")
        self.draw_color_btn.setPopupMode(QToolButton.InstantPopup)
        dc_menu = QMenu(self.draw_color_btn)
        dc_menu.addAction("Pick colour…", self._pick_draw_color)
        dc_menu.addAction("Default (blue)", lambda: self._set_draw_color(None))
        self.draw_color_btn.setMenu(dc_menu)
        saved = self._settings().value("drawColor", "", type=str)
        self.canvas.draw_color = QColor(saved) if saved else None
        self._update_draw_color_swatch()
        tb.addWidget(self.draw_color_btn)

        tb.addWidget(QLabel(" kerf "))
        self.kerf_spin = MathSpinBox()
        self.kerf_spin.setRange(0.0, 1.0)
        self.kerf_spin.setSingleStep(0.05)
        self.kerf_spin.setDecimals(2)
        self.kerf_spin.setSuffix(" mm")
        self.kerf_spin.setKeyboardTracking(False)
        self.kerf_spin.setToolTip(
            "Laser kerf compensation, applied on SVG/DXF export:\n"
            "outer cut lines grow by kerf/2, cutouts and stitch holes shrink,\n"
            "so pieces come out drawn-size. 0 = off (set kerf in your laser\n"
            "software instead -- never both). Printing is never compensated.")
        self.kerf_spin.setValue(getattr(self.doc, "kerf", 0.0))
        self.kerf_spin.valueChanged.connect(self._kerf_changed)
        tb.addWidget(self.kerf_spin)

        # persistent Fillet / chamfer options -- shown only while that tool is
        # active (no popup, no modifier keys)
        self._fillet_sep = tb.addSeparator()
        self._fillet_lbl = tb.addWidget(QLabel(" corner "))
        self.fillet_mode = NoWheelComboBox()
        self.fillet_mode.addItem("Round", False)
        self.fillet_mode.addItem("Chamfer", True)
        self.fillet_mode.setToolTip("Round = fillet arc · Chamfer = straight bevel")
        self.fillet_mode.currentIndexChanged.connect(
            lambda: setattr(self.canvas, "fillet_chamfer",
                            bool(self.fillet_mode.currentData())))
        self._fillet_mode_act = tb.addWidget(self.fillet_mode)
        self.fillet_spin = MathSpinBox()
        self.fillet_spin.setRange(0.1, 500.0)
        self.fillet_spin.setDecimals(2)
        self.fillet_spin.setSuffix(" mm")
        self.fillet_spin.setKeyboardTracking(False)
        self.fillet_spin.setToolTip("Corner radius (Round) / setback (Chamfer). "
                                    "Type a value, then click corners.")
        fr = self._settings().value("filletRadius", 6.0, type=float)
        self.fillet_spin.setValue(fr)
        self.canvas.fillet_radius = fr
        self.canvas.fillet_chamfer = False
        self.fillet_spin.valueChanged.connect(self._fillet_radius_changed)
        self._fillet_spin_act = tb.addWidget(self.fillet_spin)
        for a in (self._fillet_sep, self._fillet_lbl, self._fillet_mode_act,
                  self._fillet_spin_act):
            a.setVisible(False)

    def _fillet_radius_changed(self, r):
        self.canvas.fillet_radius = float(r)
        self._settings().setValue("filletRadius", float(r))

    def _show_fillet_options(self, on: bool):
        for a in (self._fillet_sep, self._fillet_lbl, self._fillet_mode_act,
                  self._fillet_spin_act):
            a.setVisible(on)

    def _toggle_aspect_lock(self, on):
        self.canvas.aspect_lock = bool(on)
        self._settings().setValue("aspectLock", bool(on))

    def _line_width_changed(self, w):
        self.canvas.set_line_width(w)
        self._settings().setValue("lineWidth", w)

    def _pick_draw_color(self):
        cur = self.canvas.preview_color()
        c = QColorDialog.getColor(cur, self, "While-drawing colour")
        if c.isValid():
            self._set_draw_color(c)

    def _set_draw_color(self, color):
        self.canvas.set_draw_color(color)
        self._settings().setValue("drawColor", color.name() if color else "")
        self._update_draw_color_swatch()

    def _update_draw_color_swatch(self):
        """Paint the toolbar button as a swatch of the current while-drawing
        colour (the bright default if none is set)."""
        from PySide6.QtGui import QPainter
        pm = QPixmap(18, 18)
        pm.fill(Qt.transparent)
        pr = QPainter(pm)
        pr.setPen(QColor(120, 120, 120))
        pr.setBrush(self.canvas.preview_color())
        pr.drawRoundedRect(1, 1, 15, 15, 3, 3)
        pr.end()
        self.draw_color_btn.setIcon(QIcon(pm))

    def _kerf_changed(self, k):
        self.doc.kerf = float(k)
        self.commit()                      # per-document, undoable, saved

    def _make_menus(self):
        m = self.menuBar()
        fm = m.addMenu("&File")
        self._add(fm, "New", "Ctrl+N", self.new_document)
        tm = fm.addMenu("New from template")
        from leathercad.templates import TEMPLATES
        for label, builder in TEMPLATES:
            act = QAction(label, self)
            act.triggered.connect(
                lambda checked=False, b=builder: self._new_from_template(b))
            tm.addAction(act)
        self._add(fm, "Open…", "Ctrl+O", self.open_document)
        self.recent_menu = fm.addMenu("Open recent")
        self._rebuild_recent_menu()
        self._add(fm, "Save", "Ctrl+S", self.save_document)
        self._add(fm, "Save As…", "Ctrl+Shift+S", self.save_document_as)
        fm.addSeparator()
        self._add(fm, "Import SVG / DXF…", "Ctrl+I", self.import_file)
        self._add(fm, "Export SVG…", "Ctrl+E", self.export_svg)
        self._add(fm, "Export DXF…", None, self.export_dxf)
        self._add(fm, "Export PDF (1:1, tiled)…", None, self.export_pdf)
        self._add(fm, "Print (1:1)…", "Ctrl+P", self.print_pattern)
        fm.addSeparator()
        self._add(fm, "Quit", "Ctrl+Q", self.close)

        em = m.addMenu("&Edit")
        self.act_undo = self._add(em, "Undo", "Ctrl+Z", self.undo)
        self.act_redo = self._add(em, "Redo", "Ctrl+Shift+Z", self.redo)
        em.addSeparator()
        self._add(em, "Group (move together)", "Ctrl+G", self.canvas.make_group)
        self._add(em, "Ungroup", "Ctrl+Shift+G", self.canvas.ungroup_group)
        self._add(em, "Attach holes to shape", "Ctrl+Shift+A",
                  self.canvas.group_selected)
        self._add(em, "Explode stitching → holes", "",
                  self.canvas.ungroup_selected)
        self._add(em, "Circles → stitch holes", "",
                  self.canvas.convert_circles_to_holes)
        self._add(em, "Convert to editable nodes", "Ctrl+K", self.canvas.convert_to_nodes)
        self._add(em, "Break apart into segments", "Ctrl+B", self.canvas.break_apart_selected)
        self._add(em, "Join / weld segments", "Ctrl+J", lambda: self.canvas.join_selected())
        self._add(em, "Offset / seam allowance…", "Ctrl+Shift+O",
                  self._offset_selected)
        self._add(em, "Array…", "Ctrl+Shift+R", self._array_selected)
        self._add(em, "Nest on sheet…", "Ctrl+Shift+N", self._nest_dialog)
        self._add(em, "Parameters…", "Ctrl+Shift+P", self._params_dialog)
        em.addSeparator()
        self._add(em, "Card pocket stack…", None, self._gen_card_pockets)
        self._add(em, "Zipper opening…", None, self._gen_zipper)
        em.addSeparator()
        self._add(em, "Union (merge shapes)", "Ctrl+U",
                  lambda: self.canvas.boolean_selected("union"))
        self._add(em, "Subtract (bottom − top)", "Ctrl+Shift+U",
                  lambda: self.canvas.boolean_selected("difference"))
        self._add(em, "Intersect", None,
                  lambda: self.canvas.boolean_selected("intersection"))
        em.addSeparator()
        self._add(em, "Make back piece (mirror)", "Ctrl+M",
                  self.canvas.make_back_piece_selected)
        self._add(em, "Check back-to-back symmetry…", None, self._check_symmetry)
        self._add(em, "Check seam mates…", None, self._check_seam_mates)
        self._add(em, "Job estimate (cut summary)…", None, self._job_estimate)
        self._add(em, "Thread estimate…", None, self._thread_estimate)
        self._add(em, "Area / leather usage…", None, self._area_report)
        em.addSeparator()
        self._add(em, "Duplicate", "Ctrl+D", self.canvas.duplicate_selected)
        self.act_del = self._add(em, "Delete", None, self.canvas.delete_selected)
        # Delete and Backspace (macOS "delete" key) both remove the selection.
        # A focused text field claims these first, so typing stays safe.
        self.act_del.setShortcuts([QKeySequence(Qt.Key_Delete),
                                   QKeySequence(Qt.Key_Backspace)])
        self._add(em, "Select all", "Ctrl+A", self._select_all)

        am = m.addMenu("&Arrange")
        self._add(am, "Align left", None, lambda: self.canvas.align_selected("left"))
        self._add(am, "Align centre", None, lambda: self.canvas.align_selected("hcenter"))
        self._add(am, "Align right", None, lambda: self.canvas.align_selected("right"))
        am.addSeparator()
        self._add(am, "Align top", None, lambda: self.canvas.align_selected("top"))
        self._add(am, "Align middle", None, lambda: self.canvas.align_selected("vcenter"))
        self._add(am, "Align bottom", None, lambda: self.canvas.align_selected("bottom"))
        am.addSeparator()
        self._add(am, "Distribute horizontally", None,
                  lambda: self.canvas.distribute_selected(True))
        self._add(am, "Distribute vertically", None,
                  lambda: self.canvas.distribute_selected(False))

        vm = m.addMenu("&View")
        self._add(vm, "Fit to content", "F", self.canvas.fit_to_content)
        self._add(vm, "Zoom in", "Ctrl++", lambda: self._zoom(1.2))
        self._add(vm, "Zoom out", "Ctrl+-", lambda: self._zoom(1 / 1.2))
        vm.addSeparator()
        # toggles to reopen the docks after they've been closed
        pa = self.properties_dock.toggleViewAction()
        pa.setText("Properties panel")
        pa.setShortcut(QKeySequence("Ctrl+1"))
        vm.addAction(pa)
        la = self.layers_dock.toggleViewAction()
        la.setText("Layers panel")
        la.setShortcut(QKeySequence("Ctrl+2"))
        vm.addAction(la)
        pa2 = self.parts_dock.toggleViewAction()
        pa2.setText("Parts library")
        pa2.setShortcut(QKeySequence("Ctrl+3"))
        vm.addAction(pa2)
        self._add(vm, "Reset panels", None, self._reset_panels)
        vm.addSeparator()
        self.act_rulers = QAction("Rulers", self)
        self.act_rulers.setCheckable(True)
        self.act_rulers.setChecked(
            self._settings().value("rulersVisible", True, type=bool))
        self.act_rulers.toggled.connect(self._set_rulers_visible)
        self._set_rulers_visible(self.act_rulers.isChecked())
        vm.addAction(self.act_rulers)
        tim = vm.addMenu("Tracing image")
        self._add(tim, "Place image…", None, self._underlay_place)
        self._add(tim, "Calibrate scale (click 2 points)…", None,
                  self._underlay_calibrate)
        for pct in (25, 50, 75):
            self._add(tim, f"Opacity {pct}%", None,
                      lambda checked=False, o=pct / 100.0:
                      self.canvas.underlay_config(opacity=o))
        self.act_underlay_show = QAction("Show tracing image", self)
        self.act_underlay_show.setCheckable(True)
        self.act_underlay_show.setChecked(True)
        self.act_underlay_show.toggled.connect(
            lambda on: self.canvas.underlay_config(visible=on))
        tim.addAction(self.act_underlay_show)
        self._add(tim, "Remove", None,
                  lambda: self.canvas.underlay_config(remove=True))
        vm.addSeparator()
        self.act_dark = QAction("Dark theme", self)
        self.act_dark.setCheckable(True)
        self.act_dark.toggled.connect(self._apply_theme)
        vm.addAction(self.act_dark)
        # setChecked only fires the toggle (and re-themes) when it was saved on
        self.act_dark.setChecked(
            self._settings().value("darkTheme", False, type=bool))
        self.act_drag_draw = QAction("Drag to draw (hold && release)", self)
        self.act_drag_draw.setCheckable(True)
        self.act_drag_draw.setChecked(self.canvas.drag_to_draw)
        self.act_drag_draw.setToolTip(
            "On: press, drag and release to draw a shape/line.\n"
            "Off: click the first point, then click the second point.")
        self.act_drag_draw.toggled.connect(
            lambda on: setattr(self.canvas, "drag_to_draw", on))
        vm.addAction(self.act_drag_draw)

        self.act_aspect_lock = QAction("Lock aspect ratio on resize", self)
        self.act_aspect_lock.setCheckable(True)
        self.act_aspect_lock.setToolTip(
            "On: dragging a resize grip keeps the shape's proportions.\n"
            "Hold Shift while dragging to invert this either way.")
        self.act_aspect_lock.setChecked(
            self._settings().value("aspectLock", False, type=bool))
        self.canvas.aspect_lock = self.act_aspect_lock.isChecked()
        self.act_aspect_lock.toggled.connect(self._toggle_aspect_lock)
        vm.addAction(self.act_aspect_lock)

        hm = m.addMenu("&Help")
        self._add(hm, "User guide", "F1", self.show_help)
        self._add(hm, "About", None, self._about)

    def show_help(self):
        from .helpdialog import HelpDialog
        dlg = getattr(self, "_help_dialog", None)
        if dlg is None:
            dlg = HelpDialog(self)
            self._help_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _about(self):
        QMessageBox.about(
            self, "Stitch Hero",
            "<b>Stitch Hero</b><br>CAD for laser-cut leather patterns "
            "with pricking-iron-accurate (chord-spaced) stitch holes.<br><br>"
            "Units are millimetres. Press <b>F1</b> for the user guide.")

    def _add(self, menu, text, shortcut, slot):
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _make_statusbar(self):
        self.sb_pos = QLabel("—")
        self.sb_dims = QLabel("")
        self.sb_holes = QLabel("0 holes")
        self.statusBar().addWidget(self.sb_pos)
        self.statusBar().addWidget(self.sb_dims, 1)
        self.statusBar().addPermanentWidget(self.sb_holes)

    # -- slots ----------------------------------------------------------
    def _set_tool(self, mode):
        self.canvas.tool = mode
        # Every tool that places a point gets the high-contrast crosshair so the
        # cursor stays visible (and precise) over a tracing photo; Select keeps
        # the arrow for grabbing/dragging.
        if mode == canvas_mod.SELECT:
            self.canvas.viewport().setCursor(Qt.ArrowCursor)
        else:
            self.canvas.viewport().setCursor(self.canvas.crosshair_cursor())
        if mode != canvas_mod.TRIM:
            self.canvas._clear_trim_hover()
        if mode != canvas_mod.OFFSET:
            self.canvas._cancel_offset()
        self._show_fillet_options(mode == canvas_mod.FILLET)
        # Always refresh the status hint so it reflects the CURRENT tool -- a
        # missing entry used to leave the previous tool's instructions up.
        self.canvas.statusMessage.emit(self._tool_hint(mode))

    def _tool_hint(self, mode) -> str:
        m = canvas_mod
        hints = {
            m.SELECT: "Select: click to pick, drag to move, drag a blank area "
                      "to rubber-band · double-click a shape to edit its nodes",
            m.RECT: "Rectangle: drag to draw · Shift = square · type W×H after",
            m.ROUNDED: "Rounded rectangle: drag to draw, then set the corner "
                       "radius in Properties",
            m.ELLIPSE: "Ellipse: drag a bounding box · Shift = circle",
            m.CIRCLE: "Circle: click the centre, drag out the radius",
            m.POLYGON: "Polygon: click each vertex · double-click or Enter to "
                       "close · Esc cancels",
            m.LINE: "Line: click the start, click the end · Shift = ortho · "
                    "snaps to nodes/edges",
            m.CONSTRUCTION: "Construction line: click two points for an infinite "
                            "guide (never exported)",
            m.STITCHLINE: "Stitch line: click each point of the seam · "
                          "double-click / Enter to finish (holes march along it)",
            m.SCORE: "Score line: click each point · double-click / Enter to "
                     "finish (a fold/score, not a cut)",
            m.HOLE: "Hole: click to drop a single stitch hole",
            m.SLOT: "Slot: drag to place a rounded slot",
            m.PEN: "Pen: click for corners, click-drag for curves · "
                   "double-click / Enter / right-click to finish",
            m.DIMENSION: "Dimension: click two points to measure · click again "
                         "to place the label",
            m.MEASURE: "Measure: click two points to read the distance",
            m.TEXT: "Text: click to place, then type in Properties",
            m.TRIM: "Trim: click the part of an outline to cut back to where it "
                    "crosses another shape",
            m.OFFSET: "Offset: click a shape, move the cursor inside or outside, "
                      "click to place · Enter = type an exact distance",
            m.FILLET: "Corner: set the radius / mode in the toolbar above, then "
                      "click corners (or the point where two line ends meet)",
            m.EXTEND: "Extend: click the END of a line/path to grow it until it "
                      "meets the next outline or guide",
            m.ARC3: "3-point arc: click start, end, then a point on the arc",
            m.ARCCENTER: "Centre arc: click the centre, the start, then the end",
            m.CIRCLE2: "2-point circle: click the two ends of a diameter",
            m.CIRCLE3: "3-point circle: click three points on the circle",
        }
        return hints.get(mode, "")

    def _tool_triggered(self, mode, act):
        # promote the chosen variant to its flyout button's face, then activate
        btn = self._group_button_for_action.get(act)
        if btn is not None:
            btn.setDefaultAction(act)
        self._set_tool(mode)

    def _select_mode(self, mode):
        """Programmatically activate a tool (e.g. dropping back to Select after a
        shape is finished), updating the palette highlight and any flyout face."""
        act = self._action_for_mode.get(mode)
        if act is not None:
            act.setChecked(True)
            btn = self._group_button_for_action.get(act)
            if btn is not None:
                btn.setDefaultAction(act)
        self._set_tool(mode)

    def _tool_finished(self):
        self._select_mode(canvas_mod.SELECT)
        # let the user type an exact size for the shape just created
        self.properties.focus_primary_dimension()

    def _selection_changed(self):
        self.properties.show_selection(self.canvas.selected_items())

    def _document_changed(self):
        self.sb_holes.setText(f"{self.canvas.total_holes()} holes")
        self.properties.set_layers(self.doc.layers)
        # keep the Properties position / size fields in step with canvas edits
        # (drag, node edit, resize) so a later _apply can't write a stale value
        self.properties.sync_geometry_fields()
        self._update_title()

    def _cursor_moved(self, x, y):
        self.sb_pos.setText(f"X {x:.1f}   Y {y:.1f}   mm")

    def _layer_changed(self, name):
        self.canvas._current_layer = name

    # -- undo / redo ----------------------------------------------------
    def commit(self):
        self.history.push(self.doc.to_dict())
        self._update_undo_actions()
        self._mark_dirty()

    def undo(self):
        state = self.history.undo()
        if state is not None:
            self._load_state(state)
            self._mark_dirty()

    def redo(self):
        state = self.history.redo()
        if state is not None:
            self._load_state(state)
            self._mark_dirty()

    def _mark_dirty(self):
        self._dirty_for_autosave = True
        if not self._unsaved_changes:
            self._unsaved_changes = True
            self._update_title()

    def _load_state(self, state):
        self.doc = Document.from_dict(state)
        self.canvas.doc = self.doc
        self.canvas.rebuild()
        self.layers.canvas = self.canvas
        self.layers.reload()
        self.properties.set_layers(self.doc.layers)
        self.properties.show_selection([])
        self._update_undo_actions()
        self._sync_kerf_spin()
        self.sb_holes.setText(f"{self.canvas.total_holes()} holes")

    def _update_undo_actions(self):
        if hasattr(self, "act_undo"):
            self.act_undo.setEnabled(self.history.can_undo())
            self.act_redo.setEnabled(self.history.can_redo())
        if hasattr(self, "act_undo_tb"):
            self.act_undo_tb.setEnabled(self.history.can_undo())
            self.act_redo_tb.setEnabled(self.history.can_redo())

    def _select_all(self):
        for it in self.canvas.scene_obj.items():
            it.setSelected(True)

    def _check_symmetry(self):
        QMessageBox.information(self, "Back-to-back symmetry",
                               self.canvas.symmetry_report_selected())

    def _check_seam_mates(self):
        QMessageBox.information(self, "Seam mates",
                                self.canvas.seam_mate_report())

    def _thread_estimate(self):
        """Ask leather thickness + needle tails (remembered), show the report."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QDialogButtonBox,
                                       QVBoxLayout)
        from .mathspin import MathSpinBox
        s = self._settings()
        dlg = QDialog(self)
        dlg.setWindowTitle("Thread estimate")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        thick = MathSpinBox()
        thick.setRange(0.2, 30.0)
        thick.setDecimals(1)
        thick.setSuffix(" mm")
        thick.setValue(s.value("threadThickness", 3.0, type=float))
        thick.setToolTip("TOTAL leather stack at the seam (all layers)")
        tail = MathSpinBox()
        tail.setRange(0.0, 1000.0)
        tail.setDecimals(0)
        tail.setSuffix(" mm")
        tail.setValue(s.value("threadTail", 150.0, type=float))
        tail.setToolTip("Needle-grip allowance at EACH end of a run")
        form.addRow("Leather stack", thick)
        form.addRow("Needle tail", tail)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s.setValue("threadThickness", thick.value())
        s.setValue("threadTail", tail.value())
        QMessageBox.information(
            self, "Thread estimate",
            self.canvas.thread_report(thick.value(), tail.value()))

    def _gen_card_pockets(self):
        """Generate a stepped card-pocket stack from real card dimensions."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QDialogButtonBox,
                                       QVBoxLayout, QSpinBox, QLabel)
        from .mathspin import MathSpinBox
        from leathercad.generators import card_pocket_stack
        s = self._settings()
        dlg = QDialog(self)
        dlg.setWindowTitle("Card pocket stack")
        lay = QVBoxLayout(dlg)
        hint = QLabel("Generates every piece of a stepped card-pocket stack "
                      "(wallet interior), sized so cards clear the side "
                      "seams. Pieces land bottom-aligned in a row — arrange "
                      "or nest them afterwards.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        form = QFormLayout()

        def _spin(key, default, lo, hi, tip, dec=1):
            sp = MathSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(dec)
            sp.setSuffix(" mm")
            sp.setValue(s.value(key, default, type=float))
            sp.setToolTip(tip)
            return sp

        cw = _spin("genCardW", 85.6, 20, 200, "Bank card: 85.6 mm")
        ch = _spin("genCardH", 54.0, 20, 200, "Bank card: 54 mm")
        n = NoWheelSpinBox()
        n.setRange(1, 10)
        n.setValue(int(s.value("genCardCount", 4, type=int)))
        reveal = _spin("genCardReveal", 12.0, 4, 40,
                       "How much of each card row shows above the next pocket")
        depth = _spin("genCardDepth", 38.0, 15, 200,
                      "How deep a card sits in the front pocket")
        allow = _spin("genCardAllow", 7.0, 3, 30,
                      "Seam/stitch margin each side (card must clear it)")
        form.addRow("Card width", cw)
        form.addRow("Card height", ch)
        form.addRow("Pockets", n)
        form.addRow("Reveal step", reveal)
        form.addRow("Pocket depth", depth)
        form.addRow("Side allowance", allow)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for key, val in (("genCardW", cw.value()), ("genCardH", ch.value()),
                         ("genCardCount", n.value()),
                         ("genCardReveal", reveal.value()),
                         ("genCardDepth", depth.value()),
                         ("genCardAllow", allow.value())):
            s.setValue(key, val)
        pieces = card_pocket_stack(card_w=cw.value(), card_h=ch.value(),
                                   count=n.value(), reveal=reveal.value(),
                                   depth=depth.value(),
                                   allowance=allow.value())
        self.canvas.insert_generated(shapes=pieces)

    def _gen_zipper(self):
        """Generate a zip window slot + its surrounding stitch line."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QDialogButtonBox,
                                       QVBoxLayout, QComboBox, QLabel)
        from .mathspin import MathSpinBox
        from leathercad.generators import zipper_opening, ZIP_WINDOW
        s = self._settings()
        dlg = QDialog(self)
        dlg.setWindowTitle("Zipper opening")
        lay = QVBoxLayout(dlg)
        hint = QLabel("A correctly-sized zipper window (stadium slot) with "
                      "its stitch line running around it. Both are grouped — "
                      "drag the pair onto your panel, then Subtract or just "
                      "cut. Opening length = usable zip, not tape length.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        form = QFormLayout()
        size = NoWheelComboBox()
        for k in ZIP_WINDOW:
            size.addItem(f"{k}   ({ZIP_WINDOW[k]:g} mm window)", k)
        size.setCurrentIndex(max(0, size.findData(
            s.value("genZipSize", "#5", type=str))))
        length = MathSpinBox()
        length.setRange(30, 1000)
        length.setDecimals(1)
        length.setSuffix(" mm")
        length.setValue(s.value("genZipLen", 150.0, type=float))
        offset = MathSpinBox()
        offset.setRange(1.5, 15)
        offset.setDecimals(1)
        offset.setSuffix(" mm")
        offset.setValue(s.value("genZipOffset", 3.0, type=float))
        form.addRow("Zip size", size)
        form.addRow("Opening length", length)
        form.addRow("Stitch offset", offset)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s.setValue("genZipSize", size.currentData())
        s.setValue("genZipLen", length.value())
        s.setValue("genZipOffset", offset.value())
        slot, ring = zipper_opening(size=size.currentData(),
                                    length=length.value(),
                                    stitch_offset=offset.value())
        self.canvas.insert_generated(shapes=[slot], stitch_lines=[ring],
                                     group=True)

    def _params_dialog(self):
        """Edit the document's named parameters (usable in any numeric field);
        applying re-drives every field that was set from a parameter."""
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                       QTableWidget, QTableWidgetItem,
                                       QPushButton, QDialogButtonBox, QLabel)
        from leathercad.expr import resolve, valid_name

        dlg = QDialog(self)
        dlg.setWindowTitle("Parameters")
        dlg.resize(430, 340)
        lay = QVBoxLayout(dlg)
        hint = QLabel(
            "Named values you can use in <b>any</b> numeric field — type "
            "<code>strap_w*2+5</code> into a width box and it stays "
            "<b>linked</b>: change the parameter here and every field that "
            "used it updates. Parameters may use each other and units "
            "(<code>1in</code>, <code>3cm</code>).")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Name", "Expression", "Value"])
        table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(table)

        def add_row(name="", expr=""):
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(name))
            table.setItem(r, 1, QTableWidgetItem(expr))
            val = QTableWidgetItem("")
            val.setFlags(val.flags() & ~Qt.ItemIsEditable)
            table.setItem(r, 2, val)

        for n, e in self.doc.params.items():
            add_row(n, e)

        def collect():
            """Rows -> ordered dict; raises ValueError on bad input."""
            out = {}
            for r in range(table.rowCount()):
                name = (table.item(r, 0).text() if table.item(r, 0) else "").strip()
                expr = (table.item(r, 1).text() if table.item(r, 1) else "").strip()
                if not name and not expr:
                    continue                       # blank row
                if not valid_name(name):
                    raise ValueError(f"bad name: {name!r} (letters, digits, _ "
                                     "— and not a unit word)")
                if name in out:
                    raise ValueError(f"duplicate name: {name}")
                if not expr:
                    raise ValueError(f"{name}: empty expression")
                out[name] = expr
            return out

        def recompute(*_a):
            try:
                vals = resolve(collect())
                err = None
            except ValueError as e:
                vals, err = {}, str(e)
            for r in range(table.rowCount()):
                name = (table.item(r, 0).text() if table.item(r, 0) else "").strip()
                if table.item(r, 2) is not None:
                    table.item(r, 2).setText(
                        f"{vals[name]:g} mm" if name in vals else "—")
            status.setText(err or "")

        table.cellChanged.connect(recompute)
        btns = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(lambda: add_row())
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(lambda: table.removeRow(table.currentRow())
                                if table.currentRow() >= 0 else None)
        btns.addWidget(add_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        status = QLabel("")
        status.setWordWrap(True)
        lay.addWidget(status)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        recompute()
        while dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                params = collect()
                resolve(params)                    # full validation
            except ValueError as e:
                QMessageBox.warning(self, "Parameters", str(e))
                continue
            self.doc.params = params
            problems = self.canvas.apply_param_bindings()
            self.commit()
            if problems:
                QMessageBox.warning(
                    self, "Parameters",
                    "Some linked fields could not update:\n"
                    + "\n".join(problems))
            return
        # cancelled: nothing written

    def _nest_dialog(self):
        """Ask the sheet size + gaps (remembered), pack the pieces, report."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QDialogButtonBox,
                                       QVBoxLayout, QCheckBox, QLabel)
        from .mathspin import MathSpinBox
        s = self._settings()
        dlg = QDialog(self)
        dlg.setWindowTitle("Nest on sheet")
        lay = QVBoxLayout(dlg)
        hint = QLabel("Packs the selected pieces (or everything, if nothing "
                      "is selected) onto one sheet of leather, using their "
                      "real outlines. Grouped shapes and anything inside a "
                      "piece (slots, holes, seams) travel with it.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        form = QFormLayout()

        def _spin(key, default, lo, hi, tip):
            sp = MathSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(1)
            sp.setSuffix(" mm")
            sp.setValue(s.value(key, default, type=float))
            sp.setToolTip(tip)
            return sp

        w = _spin("nestSheetW", 600.0, 20.0, 5000.0,
                  "Width of the leather you're cutting from")
        h = _spin("nestSheetH", 450.0, 20.0, 5000.0,
                  "Height of the leather you're cutting from")
        margin = _spin("nestMargin", 5.0, 0.0, 100.0,
                       "Keep-out border along the sheet edges")
        gap = _spin("nestGap", 3.0, 0.5, 50.0,
                    "Minimum space between neighbouring pieces")
        rot = QCheckBox("Allow 90° rotation")
        rot.setChecked(s.value("nestRotate", True, type=bool))
        rot.setToolTip("Turn off if grain / stretch direction matters "
                       "for every piece")
        form.addRow("Sheet width", w)
        form.addRow("Sheet height", h)
        form.addRow("Edge margin", margin)
        form.addRow("Gap between pieces", gap)
        form.addRow("", rot)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        s.setValue("nestSheetW", w.value())
        s.setValue("nestSheetH", h.value())
        s.setValue("nestMargin", margin.value())
        s.setValue("nestGap", gap.value())
        s.setValue("nestRotate", rot.isChecked())
        report = self.canvas.nest_selected(
            w.value(), h.value(), margin=margin.value(),
            spacing=gap.value(), allow_rotate=rot.isChecked())
        QMessageBox.information(self, "Nest on sheet", report)

    def _job_estimate(self):
        """One dialog for the numbers a maker quotes a job with: pieces, holes,
        thread, cut/score/engrave lengths, leather + waste, laser time, cost.
        Prices default to 0 and their cost lines simply don't show until set."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QDialogButtonBox,
                                       QVBoxLayout, QLabel)
        from PySide6.QtGui import QFontDatabase
        from .mathspin import MathSpinBox
        from leathercad.estimate import estimate_project, format_report
        s = self._settings()

        def _spin(key, default, lo, hi, suffix, dec, tip=""):
            sp = MathSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(dec)
            sp.setSuffix(suffix)
            sp.setValue(s.value(key, default, type=float))
            if tip:
                sp.setToolTip(tip)
            return sp

        dlg = QDialog(self)
        dlg.setWindowTitle("Job estimate")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        thick = _spin("threadThickness", 3.0, 0.2, 30.0, " mm", 1,
                      "TOTAL leather stack at the seam (all layers)")
        tail = _spin("threadTail", 150.0, 0.0, 1000.0, " mm", 0,
                     "Needle-grip allowance at EACH end of a run")
        feed = _spin("laserFeed", 20.0, 0.0, 500.0, " mm/s", 0,
                     "Cutting feed rate; 0 to skip the run-time estimate")
        usable = _spin("leatherUsable", 75.0, 10.0, 100.0, " %", 0,
                       "Usable portion of the hide — the rest is waste")
        price_l = _spin("priceLeather", 0.0, 0.0, 999.0, " $/sq ft", 2)
        price_t = _spin("priceThread", 0.0, 0.0, 99.0, " $/m", 2)
        price_j = _spin("priceLaser", 0.0, 0.0, 999.0, " $/min", 2)
        form.addRow("Leather stack", thick)
        form.addRow("Needle tail", tail)
        form.addRow("Laser feed", feed)
        form.addRow("Hide yield", usable)
        form.addRow("Leather price", price_l)
        form.addRow("Thread price", price_t)
        form.addRow("Laser price", price_j)
        lay.addLayout(form)
        hint = QLabel("Prices are optional — leave at 0 to skip a cost line.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for key, w in (("threadThickness", thick), ("threadTail", tail),
                       ("laserFeed", feed), ("leatherUsable", usable),
                       ("priceLeather", price_l), ("priceThread", price_t),
                       ("priceLaser", price_j)):
            s.setValue(key, w.value())
        est = estimate_project(
            self.doc, thickness_mm=thick.value(), tail_mm=tail.value(),
            feed_mm_s=feed.value(), usable_pct=usable.value(),
            price_per_sqft=price_l.value(), price_thread_per_m=price_t.value(),
            price_laser_per_min=price_j.value())
        box = QMessageBox(self)
        box.setWindowTitle("Job estimate")
        box.setText(format_report(est, usable_pct=usable.value()))
        box.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        box.exec()

    def _area_report(self):
        """Ask the usable-hide percentage (remembered), show material usage."""
        s = self._settings()
        usable, ok = QInputDialog.getDouble(
            self, "Area / leather usage",
            "Usable portion of the hide (%) — real cutting wastes the rest:",
            s.value("leatherUsable", 75.0, type=float), 10.0, 100.0, 0)
        if not ok:
            return
        s.setValue("leatherUsable", usable)
        QMessageBox.information(self, "Area / leather usage",
                                self.canvas.area_report(usable))

    def _underlay_place(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Place tracing image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not fn:
            return
        if self.canvas.set_underlay(fn):
            self.statusBar().showMessage(
                "Tracing image placed — now View → Tracing image → Calibrate "
                "scale, click two points a known distance apart", 8000)

    def _underlay_calibrate(self):
        if getattr(self.canvas.doc, "underlay", None) is None:
            QMessageBox.information(self, "Tracing image",
                                    "Place a tracing image first.")
            return
        self.canvas.tool = canvas_mod.UNDERLAYCAL
        self.canvas.statusMessage.emit(
            "Calibrate: click the FIRST point of a known distance on the "
            "photo (e.g. one end of a ruler in the shot)")

    def _offset_selected(self):
        if not [it for it in self.canvas.selected_items()
                if hasattr(it, "model")]:
            QMessageBox.information(self, "Offset",
                                   "Select a shape to offset first.")
            return
        dist, ok = QInputDialog.getDouble(
            self, "Offset / seam allowance",
            "Distance (mm)   —   positive = outward, negative = inward:",
            3.0, -100.0, 100.0, 2)
        if ok:
            self.canvas.offset_selected(dist)

    def _array_selected(self):
        from .arraydialog import ArrayDialog
        if not self.canvas.selected_items():
            QMessageBox.information(self, "Array",
                                   "Select something to array first.")
            return
        dlg = ArrayDialog(self, center=self.canvas.selection_center())
        if dlg.exec() != ArrayDialog.Accepted:
            return
        mode, p = dlg.result_params()
        if mode == "grid":
            self.canvas.array_grid(p["rows"], p["cols"], p["dx"], p["dy"])
        else:
            self.canvas.array_circular(p["count"], p["cx"], p["cy"],
                                       p["total_deg"], p["rotate_items"])

    def _reset_panels(self):
        """Re-dock and show the Properties and Layers panels in their default
        spot (rescues them if closed, floating or dragged off-screen)."""
        for dock in (self.properties_dock, self.layers_dock):
            dock.setFloating(False)
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
            dock.show()
            dock.raise_()

    def _zoom(self, factor):
        self.canvas._zoom = max(0.3, min(40.0, self.canvas._zoom * factor))
        self.canvas._apply_zoom()

    # -- file ops -------------------------------------------------------
    def new_document(self):
        if not self._confirm_discard("starting a new pattern"):
            return
        self._adopt_document(Document(), None)

    def _new_from_template(self, builder):
        if not self._confirm_discard("starting a new pattern"):
            return
        self._adopt_document(builder(), None, fit=True)

    def open_document(self):
        if not self._confirm_discard("opening another file"):
            return
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open", "", "Stitch Hero (*.json *.leathercad.json)")
        if not fn:
            return
        self.open_path(fn)

    def open_path(self, fn: str) -> bool:
        try:
            doc = Document.load(fn)
        except Exception as e:
            QMessageBox.critical(self, "Open failed",
                                 f"Couldn't open {os.path.basename(fn)}:\n{e}")
            self._forget_recent(fn)
            return False
        self._adopt_document(doc, fn, fit=True)
        self._add_recent(fn)
        return True

    # -- recent files ------------------------------------------------------
    _RECENT_MAX = 8

    def recent_files(self) -> list:
        val = self._settings().value("recentFiles", [])
        if isinstance(val, str):           # QSettings collapses 1-item lists
            val = [val]
        return [p for p in (val or []) if isinstance(p, str)]

    def _save_recents(self, paths) -> None:
        self._settings().setValue("recentFiles", list(paths))
        self._rebuild_recent_menu()

    def _add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        rec = [p for p in self.recent_files() if p != path]
        rec.insert(0, path)
        self._save_recents(rec[: self._RECENT_MAX])

    def _forget_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        rec = [p for p in self.recent_files() if p != path]
        self._save_recents(rec)

    def _rebuild_recent_menu(self) -> None:
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        rec = [p for p in self.recent_files() if os.path.exists(p)]
        if rec != self.recent_files():     # quietly drop deleted files
            self._settings().setValue("recentFiles", rec)
        if not rec:
            empty = self.recent_menu.addAction("(empty)")
            empty.setEnabled(False)
            return
        for p in rec:
            act = self.recent_menu.addAction(os.path.basename(p))
            act.setToolTip(p)
            act.triggered.connect(
                lambda checked=False, fn=p: self._open_recent(fn))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear menu",
                                   lambda: self._save_recents([]))

    def _open_recent(self, fn: str) -> None:
        if not self._confirm_discard("opening another file"):
            return
        if not os.path.exists(fn):
            QMessageBox.warning(self, "File moved",
                                f"{os.path.basename(fn)} no longer exists.")
            self._forget_recent(fn)
            return
        self.open_path(fn)

    def save_document(self):
        if not self.path:
            return self.save_document_as()
        self.doc.save(self.path)
        self._after_save()

    def save_document_as(self):
        fn, _ = QFileDialog.getSaveFileName(
            self, "Save As", "untitled.leathercad.json",
            "Stitch Hero (*.json *.leathercad.json)")
        if not fn:
            return
        self.path = fn
        self.doc.save(fn)
        self._after_save()

    def _after_save(self):
        self._unsaved_changes = False
        self._dirty_for_autosave = False
        self.remove_autosave()          # on disk now -> nothing to recover
        if self.path:
            self._add_recent(self.path)
        self._update_title()

    def import_file(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Import", "", "Vector drawings (*.svg *.dxf)")
        if not fn:
            return
        self.import_path(fn)

    def import_path(self, fn: str) -> int:
        """Add the shapes from an SVG/DXF into the current document. Stroke
        colours map onto this document's layers, and circles that land on a
        stitch-role layer become real stitch holes."""
        from leathercad.importers import import_file
        from leathercad.shapes import Circle
        from leathercad.holes import LooseHole
        from leathercad.geometry import Vec2
        color_layers = {lyr.color.lower(): lyr.name
                        for lyr in self.doc.layers}
        try:
            shapes = import_file(fn, color_layers=color_layers)
        except Exception as e:
            QMessageBox.critical(self, "Import failed",
                                 f"Couldn't import {os.path.basename(fn)}:\n{e}")
            return 0
        if not shapes:
            QMessageBox.information(
                self, "Nothing imported",
                "No usable outlines were found in that file.")
            return 0
        stitch_layers = {lyr.name for lyr in self.doc.layers
                         if getattr(lyr, "role", "") == "stitch"}
        n_holes = 0
        n_shapes = 0
        for sh in shapes:
            if isinstance(sh, Circle) and sh.layer in stitch_layers:
                c = sh.transform.apply(Vec2(0.0, 0.0))
                self.doc.holes.append(LooseHole(point=Vec2(c.x, c.y),
                                                hole_diameter=2.0 * sh.rx))
                n_holes += 1
            else:
                self.doc.add_shape(sh)
                n_shapes += 1
        self.canvas.rebuild()
        self.canvas.fit_to_content()
        self.commit()
        msg = f"Imported {n_shapes} shape(s)"
        if n_holes:
            msg += f" + {n_holes} stitch hole(s) (classified by colour)"
        msg += (f" from {os.path.basename(fn)} — stitching is off; enable it "
                "per piece in Properties")
        self.statusBar().showMessage(msg, 7000)
        return n_shapes + n_holes

    def export_svg(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Export SVG", "pattern.svg",
                                            "SVG (*.svg)")
        if not fn:
            return
        export.export_svg(self.doc, fn)
        self.statusBar().showMessage(f"Exported {os.path.basename(fn)}", 4000)

    def export_dxf(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Export DXF", "pattern.dxf",
                                            "DXF (*.dxf)")
        if not fn:
            return
        export.export_dxf(self.doc, fn)
        self.statusBar().showMessage(f"Exported {os.path.basename(fn)}", 4000)

    def export_pdf(self):
        from .printing import export_pdf_tiled
        fn, _ = QFileDialog.getSaveFileName(self, "Export PDF (1:1)",
                                            "pattern.pdf", "PDF (*.pdf)")
        if not fn:
            return
        rows, cols = export_pdf_tiled(self.doc, fn)
        self.statusBar().showMessage(
            f"Exported {os.path.basename(fn)} — {rows}×{cols} page(s), 1:1 scale",
            5000)

    def print_pattern(self):
        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        from PySide6.QtGui import QPageSize, QPageLayout
        from PySide6.QtCore import QSizeF, QMarginsF
        from .printing import render_tiled
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.A4))
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print pattern (1:1)")
        if dlg.exec() != QPrintDialog.Accepted:
            return
        layout = printer.pageLayout().fullRect(QPageLayout.Unit.Millimeter)
        render_tiled(printer, self.doc,
                     page_w=layout.width(), page_h=layout.height())
        self.statusBar().showMessage("Sent to printer at 1:1 scale", 4000)

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        mark = "• " if getattr(self, "_unsaved_changes", False) else ""
        self.setWindowTitle(f"Stitch Hero — {mark}{name}")

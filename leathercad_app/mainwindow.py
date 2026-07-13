"""The application main window: canvas + docks + toolbar + menus + export."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize, QSettings, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog, QToolBar, QLabel, QMessageBox,
    QWidget, QScrollArea, QComboBox, QInputDialog, QDoubleSpinBox,
    QToolButton, QMenu,
)

from leathercad.document import Document
from leathercad import export
from . import canvas as canvas_mod
from .canvas import Canvas
from .panels import PropertiesPanel, LayersPanel
from .history import History
from .icons import tool_icon


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
    canvas_mod.TEXT,
    canvas_mod.MEASURE,
    canvas_mod.DIMENSION,
]


class MainWindow(QMainWindow):
    def __init__(self, document: Optional[Document] = None):
        super().__init__()
        self.doc = document or Document()
        self.path: Optional[str] = None
        self.setWindowTitle("Leather-Drafting")
        self.resize(1200, 800)

        self.canvas = Canvas(self.doc)
        self.setCentralWidget(self._wrap_with_rulers(self.canvas))

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
        return QSettings("Leather-Drafting", "Leather-Drafting")

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
                f"Leather-Drafting didn't close cleanly last time.\n\n"
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
        self.grid_combo = QComboBox()
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

    def _line_width_changed(self, w):
        self.canvas.set_line_width(w)
        self._settings().setValue("lineWidth", w)

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
            self, "Leather-Drafting",
            "<b>Leather-Drafting</b><br>CAD for laser-cut leather patterns "
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
        cur = Qt.CrossCursor if mode == canvas_mod.TRIM else Qt.ArrowCursor
        self.canvas.viewport().setCursor(cur)
        if mode != canvas_mod.TRIM:
            self.canvas._clear_trim_hover()
        if mode == canvas_mod.TRIM:
            self.canvas.statusMessage.emit(
                "Trim: click the part of an outline to cut back to where it "
                "crosses another shape")
        elif mode == canvas_mod.FILLET:
            self.canvas.statusMessage.emit(
                "Fillet: click a corner to round it · Shift-click = chamfer · "
                "Ctrl-click = change radius")
        elif mode == canvas_mod.EXTEND:
            self.canvas.statusMessage.emit(
                "Extend: click the END of a line/path to grow it until it "
                "meets the next outline or guide")

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
            self, "Open", "", "Leather-Drafting (*.json *.leathercad.json)")
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
            "Leather-Drafting (*.json *.leathercad.json)")
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
        self.setWindowTitle(f"Leather-Drafting — {mark}{name}")

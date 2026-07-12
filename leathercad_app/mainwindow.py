"""The application main window: canvas + docks + toolbar + menus + export."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QSize, QSettings
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QDockWidget, QFileDialog, QToolBar, QLabel, QMessageBox,
    QWidget, QScrollArea, QComboBox,
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
    ("Hole", canvas_mod.HOLE, "H"),
    ("Slot", canvas_mod.SLOT, "T"),
    ("Score line", canvas_mod.SCORE, "K"),
    ("Stitch line (seam)", canvas_mod.STITCHLINE, "L"),
    ("Trim to intersections", canvas_mod.TRIM, "X"),
]

_ICON_FOR = {
    canvas_mod.SELECT: "select", canvas_mod.RECT: "rect",
    canvas_mod.ROUNDED: "rounded", canvas_mod.ELLIPSE: "ellipse",
    canvas_mod.CIRCLE: "circle", canvas_mod.POLYGON: "polygon",
    canvas_mod.HOLE: "hole", canvas_mod.SLOT: "slot",
    canvas_mod.SCORE: "score", canvas_mod.STITCHLINE: "stitchline",
    canvas_mod.TRIM: "trim",
}


class MainWindow(QMainWindow):
    def __init__(self, document: Optional[Document] = None):
        super().__init__()
        self.doc = document or Document()
        self.path: Optional[str] = None
        self.setWindowTitle("Leather-Drafting")
        self.resize(1200, 800)

        self.canvas = Canvas(self.doc)
        self.setCentralWidget(self.canvas)

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
        self.canvas.toolFinished.connect(self._tool_finished)
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
        self._update_title()

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

    def closeEvent(self, event):
        s = self._settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        s.setValue("toolbarPinned", self.act_pin.isChecked())
        super().closeEvent(event)

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

        self._tool_group = QActionGroup(self)
        self._tool_actions = []
        for i, (label, mode, key) in enumerate(TOOLS):
            act = QAction(tool_icon(_ICON_FOR.get(mode, "rect")), label, self)
            act.setCheckable(True)
            act.setShortcut(QKeySequence(key))
            act.setToolTip(f"{label}  ({key})")
            act.triggered.connect(lambda checked, m=mode: self._set_tool(m))
            self._tool_group.addAction(act)
            tb.addAction(act)
            self._tool_actions.append(act)
        self._tool_actions[0].setChecked(True)

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
        self.act_group.setToolTip("Attach selected holes to the selected shape")
        self.act_group.triggered.connect(self.canvas.group_selected)
        tb.addAction(self.act_group)
        self.act_ungroup = QAction(tool_icon("ungroup"), "Ungroup", self)
        self.act_ungroup.setToolTip(
            "Explode stitch holes into individual, deletable holes")
        self.act_ungroup.triggered.connect(self.canvas.ungroup_selected)
        tb.addAction(self.act_ungroup)
        fit = QAction("Fit", self)
        fit.triggered.connect(self.canvas.fit_to_content)
        tb.addAction(fit)

        tb.addSeparator()
        self.act_snap = QAction("Snap", self)
        self.act_snap.setCheckable(True)
        self.act_snap.setChecked(True)
        self.act_snap.setToolTip("Snap to grid and vertices while drawing/moving")
        self.act_snap.toggled.connect(
            lambda on: setattr(self.canvas, "snap_enabled", on))
        tb.addAction(self.act_snap)
        tb.addWidget(QLabel(" grid "))
        self.grid_combo = QComboBox()
        for mm in (0.5, 1.0, 2.0, 2.5, 5.0, 10.0):
            self.grid_combo.addItem(f"{mm:g} mm", mm)
        self.grid_combo.setCurrentIndex(1)  # 1 mm
        self.grid_combo.currentIndexChanged.connect(
            lambda: setattr(self.canvas, "snap_grid", self.grid_combo.currentData()))
        tb.addWidget(self.grid_combo)

    def _make_menus(self):
        m = self.menuBar()
        fm = m.addMenu("&File")
        self._add(fm, "New", "Ctrl+N", self.new_document)
        self._add(fm, "Open…", "Ctrl+O", self.open_document)
        self._add(fm, "Save", "Ctrl+S", self.save_document)
        self._add(fm, "Save As…", "Ctrl+Shift+S", self.save_document_as)
        fm.addSeparator()
        self._add(fm, "Export SVG…", "Ctrl+E", self.export_svg)
        self._add(fm, "Export DXF…", None, self.export_dxf)
        fm.addSeparator()
        self._add(fm, "Quit", "Ctrl+Q", self.close)

        em = m.addMenu("&Edit")
        self.act_undo = self._add(em, "Undo", "Ctrl+Z", self.undo)
        self.act_redo = self._add(em, "Redo", "Ctrl+Shift+Z", self.redo)
        em.addSeparator()
        self._add(em, "Group holes into shape", "Ctrl+G", self.canvas.group_selected)
        self._add(em, "Ungroup stitching", "Ctrl+Shift+G", self.canvas.ungroup_selected)
        self._add(em, "Convert to editable nodes", "Ctrl+K", self.canvas.convert_to_nodes)
        self._add(em, "Break apart into segments", "Ctrl+B", self.canvas.break_apart_selected)
        self._add(em, "Join / weld segments", "Ctrl+J", lambda: self.canvas.join_selected())
        em.addSeparator()
        self._add(em, "Make back piece (mirror)", "Ctrl+M",
                  self.canvas.make_back_piece_selected)
        self._add(em, "Check back-to-back symmetry…", None, self._check_symmetry)
        em.addSeparator()
        self._add(em, "Duplicate", "Ctrl+D", self.canvas.duplicate_selected)
        self._add(em, "Delete", None, self.canvas.delete_selected)
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

    def _select_tool_action(self, index):
        self._tool_actions[index].setChecked(True)
        self.canvas.tool = TOOLS[index][1]

    def _tool_finished(self):
        self._select_tool_action(0)
        # let the user type an exact size for the shape just created
        self.properties.focus_primary_dimension()

    def _selection_changed(self):
        self.properties.show_selection(self.canvas.selected_items())

    def _document_changed(self):
        self.sb_holes.setText(f"{self.canvas.total_holes()} holes")
        self.properties.set_layers(self.doc.layers)
        self._update_title()

    def _cursor_moved(self, x, y):
        self.sb_pos.setText(f"X {x:.1f}   Y {y:.1f}   mm")

    def _layer_changed(self, name):
        self.canvas._current_layer = name

    # -- undo / redo ----------------------------------------------------
    def commit(self):
        self.history.push(self.doc.to_dict())
        self._update_undo_actions()

    def undo(self):
        state = self.history.undo()
        if state is not None:
            self._load_state(state)

    def redo(self):
        state = self.history.redo()
        if state is not None:
            self._load_state(state)

    def _load_state(self, state):
        self.doc = Document.from_dict(state)
        self.canvas.doc = self.doc
        self.canvas.rebuild()
        self.layers.canvas = self.canvas
        self.layers.reload()
        self.properties.set_layers(self.doc.layers)
        self.properties.show_selection([])
        self._update_undo_actions()
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

    def _zoom(self, factor):
        self.canvas._zoom = max(0.3, min(40.0, self.canvas._zoom * factor))
        self.canvas._apply_zoom()

    # -- file ops -------------------------------------------------------
    def new_document(self):
        self.doc = Document()
        self.path = None
        self.canvas.doc = self.doc
        self.canvas.rebuild()
        self.layers.canvas = self.canvas
        self.layers.reload()
        self.properties.set_layers(self.doc.layers)
        self.history.reset(self.doc.to_dict())
        self._update_undo_actions()
        self._update_title()

    def open_document(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open", "", "Leather-Drafting (*.json *.leathercad.json)")
        if not fn:
            return
        try:
            self.doc = Document.load(fn)
        except Exception as e:  # pragma: no cover - GUI path
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self.path = fn
        self.canvas.doc = self.doc
        self.canvas.rebuild()
        self.layers.reload()
        self.properties.set_layers(self.doc.layers)
        self.canvas.fit_to_content()
        self.history.reset(self.doc.to_dict())
        self._update_undo_actions()
        self._update_title()

    def save_document(self):
        if not self.path:
            return self.save_document_as()
        self.doc.save(self.path)
        self._update_title()

    def save_document_as(self):
        fn, _ = QFileDialog.getSaveFileName(
            self, "Save As", "untitled.leathercad.json",
            "Leather-Drafting (*.json *.leathercad.json)")
        if not fn:
            return
        self.path = fn
        self.doc.save(fn)
        self._update_title()

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

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "Untitled"
        self.setWindowTitle(f"Leather-Drafting — {name}")

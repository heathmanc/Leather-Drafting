"""The application main window: canvas + docks + toolbar + menus + export."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
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
]


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
        self._make_toolbar()
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
        self._update_title()

    # -- UI construction ------------------------------------------------
    def _make_docks(self):
        d1 = QDockWidget("Properties", self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.properties)
        d1.setWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, d1)

        d2 = QDockWidget("Layers", self)
        d2.setWidget(self.layers)
        self.addDockWidget(Qt.RightDockWidgetArea, d2)

    def _make_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)
        self._tool_group = QActionGroup(self)
        self._tool_actions = []
        for i, (label, mode, key) in enumerate(TOOLS):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setShortcut(QKeySequence(key))
            act.setToolTip(f"{label}  ({key})")
            act.triggered.connect(lambda checked, m=mode: self._set_tool(m))
            self._tool_group.addAction(act)
            tb.addAction(act)
            self._tool_actions.append(act)
        self._tool_actions[0].setChecked(True)
        tb.addSeparator()

        dup = QAction("Duplicate", self)
        dup.setShortcut(QKeySequence("Ctrl+D"))
        dup.triggered.connect(self.canvas.duplicate_selected)
        tb.addAction(dup)

        dele = QAction("Delete", self)
        dele.setShortcut(QKeySequence(Qt.Key_Delete))
        dele.triggered.connect(self.canvas.delete_selected)
        tb.addAction(dele)

        fit = QAction("Fit", self)
        fit.setShortcut(QKeySequence("F"))
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

    def _select_all(self):
        for it in self.canvas.scene_obj.items():
            it.setSelected(True)

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

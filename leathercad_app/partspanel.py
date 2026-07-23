"""The Parts dock: built-in size templates + your personal library, by category."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget,
                               QTreeWidgetItem, QPushButton, QLabel,
                               QInputDialog, QMessageBox)

from . import partslib
from .items import ShapeItem

MY_PARTS = "My parts"


class PartsPanel(QWidget):
    def __init__(self, canvas, base_dir: str | None = None):
        super().__init__()
        self.canvas = canvas
        self.base_dir = base_dir            # tests point this at a tmp dir
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(QLabel("Parts library — templates + your saved parts"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(lambda _i, _c: self.place_selected())
        root.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.btn_save = QPushButton("Save selection…")
        self.btn_place = QPushButton("Place")
        self.btn_delete = QPushButton("Delete")
        for b in (self.btn_save, self.btn_place, self.btn_delete):
            row.addWidget(b)
        root.addLayout(row)
        self.btn_save.clicked.connect(self.save_selection)
        self.btn_place.clicked.connect(self.place_selected)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.reload()

    def _category(self, title: str) -> QTreeWidgetItem:
        cat = QTreeWidgetItem([title])
        cat.setFlags(Qt.ItemIsEnabled)      # a header: not selectable/placeable
        f = cat.font(0)
        f.setBold(True)
        cat.setFont(0, f)
        self.tree.addTopLevelItem(cat)
        cat.setExpanded(True)
        return cat

    def _leaf(self, parent, label: str, data: str, tip: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem([label])
        it.setData(0, Qt.UserRole, data)
        it.setToolTip(0, tip)
        parent.addChild(it)
        return it

    def reload(self):
        from . import builtin_parts
        self.tree.clear()
        # built-in size templates, grouped by category
        for title, rows in builtin_parts.categories():
            cat = self._category(title)
            for name, key in rows:
                self._leaf(cat, name, "builtin:" + key,
                           "Built-in size template — double-click to place")
        # the user's own saved parts
        my = self._category(MY_PARTS)
        parts = partslib.list_parts(self.base_dir)
        for name, path in parts:
            self._leaf(my, name, str(path),
                       "Double-click to place at the view centre")
        if not parts:
            hint = QTreeWidgetItem(["(select shapes → Save selection…)"])
            hint.setFlags(Qt.ItemIsEnabled)
            hint.setDisabled(True)
            my.addChild(hint)

    def save_selection(self, name: str | None = None):
        from .items import TextItem
        sel = self.canvas.selected_items()
        shapes = [it.model for it in sel if isinstance(it, ShapeItem)]
        texts = [it.model for it in sel if isinstance(it, TextItem)]
        if not shapes and not texts:
            QMessageBox.information(self, "Parts library",
                                    "Select one or more shapes first.")
            return
        if not name:
            name, ok = QInputDialog.getText(self, "Save part",
                                            "Part name:", text="My part")
            if not ok or not name.strip():
                return
        import copy
        partslib.save_part(name.strip(), copy.deepcopy(shapes),
                           copy.deepcopy(texts), self.base_dir)
        self.reload()

    def _current_data(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def place_selected(self):
        p = self._current_data()
        if not p:
            return
        if p.startswith("builtin:"):
            from . import builtin_parts
            shapes, texts = builtin_parts.build_builtin(p[len("builtin:"):])
        else:
            shapes, texts = partslib.load_part_full(p)
        self.canvas.place_shapes(shapes, texts)

    def delete_selected(self):
        p = self._current_data()
        if not p:
            return
        if p.startswith("builtin:"):
            QMessageBox.information(self, "Parts library",
                                    "Built-in size templates can't be deleted.")
            return
        partslib.delete_part(p)
        self.reload()

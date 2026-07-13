"""The Parts dock: your personal library of reusable pieces."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QPushButton, QLabel,
                               QInputDialog, QMessageBox)

from . import partslib
from .items import ShapeItem


class PartsPanel(QWidget):
    def __init__(self, canvas, base_dir: str | None = None):
        super().__init__()
        self.canvas = canvas
        self.base_dir = base_dir            # tests point this at a tmp dir
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(QLabel("Parts library (yours, across all documents)"))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.place_selected())
        root.addWidget(self.list, 1)
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

    def reload(self):
        self.list.clear()
        for name, path in partslib.list_parts(self.base_dir):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip("Double-click to place at the view centre")
            self.list.addItem(item)
        if not self.list.count():
            hint = QListWidgetItem("(select shapes → Save selection…)")
            hint.setFlags(Qt.NoItemFlags)
            self.list.addItem(hint)

    def save_selection(self, name: str | None = None):
        shapes = [it.model for it in self.canvas.selected_items()
                  if isinstance(it, ShapeItem)]
        if not shapes:
            QMessageBox.information(self, "Parts library",
                                    "Select one or more shapes first.")
            return
        if not name:
            name, ok = QInputDialog.getText(self, "Save part",
                                            "Part name:", text="My part")
            if not ok or not name.strip():
                return
        import copy
        partslib.save_part(name.strip(), copy.deepcopy(shapes), self.base_dir)
        self.reload()

    def _current_path(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it and it.data(Qt.UserRole) else None

    def place_selected(self):
        p = self._current_path()
        if not p:
            return
        self.canvas.place_shapes(partslib.load_part(p))

    def delete_selected(self):
        p = self._current_path()
        if not p:
            return
        partslib.delete_part(p)
        self.reload()

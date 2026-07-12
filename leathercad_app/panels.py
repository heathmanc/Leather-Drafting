"""Dockable panels: shape/stitch properties and the layer manager."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox, QDoubleSpinBox, QCheckBox,
    QComboBox, QSlider, QLabel, QPushButton, QListWidget, QListWidgetItem,
    QHBoxLayout, QColorDialog, QSpinBox, QAbstractSpinBox,
)

from leathercad.irons import PRESETS
from leathercad.stitchsettings import StitchSettings
from leathercad.shapes import Rectangle, Ellipse, Circle, Polygon, PathShape
from leathercad.layers import Layer, ROLES
from .items import ShapeItem, StitchLineItem, HoleItem, DimensionItem, TextItem

try:
    import shiboken6

    def _alive(obj) -> bool:
        return obj is not None and shiboken6.isValid(obj)
except Exception:  # pragma: no cover
    def _alive(obj) -> bool:
        return obj is not None


def _spin(lo, hi, step=1.0, decimals=2, suffix=" mm") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setButtonSymbols(QAbstractSpinBox.NoButtons)
    s.setKeyboardTracking(False)
    return s


def _is_line(sh) -> bool:
    """A straight 2-point open path (a line or construction line)."""
    return (isinstance(sh, PathShape) and len(sh.points) == 2
            and not sh.close_path)


class PropertiesPanel(QWidget):
    committed = Signal()   # a discrete edit finished -> caller pushes undo state

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self._item = None
        self._loading = False
        self._build()
        self.setEnabled(False)

    # -- construction ---------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Transform
        self.g_transform = QGroupBox("Transform")
        f = QFormLayout(self.g_transform)
        self.pos_x = _spin(-100000, 100000, 1.0)
        self.pos_y = _spin(-100000, 100000, 1.0)
        self.rot = _spin(-360, 360, 1.0, 1, " °")
        self.mirror = QCheckBox("Mirror (laser from back)")
        f.addRow("X", self.pos_x)
        f.addRow("Y", self.pos_y)
        f.addRow("Rotation", self.rot)
        f.addRow("", self.mirror)
        root.addWidget(self.g_transform)

        # Rectangle geometry
        self.g_rect = QGroupBox("Rectangle")
        fr = QFormLayout(self.g_rect)
        self.w = _spin(0.1, 100000, 1.0)
        self.h = _spin(0.1, 100000, 1.0)
        self.corner = _spin(0.0, 100000, 0.5)
        fr.addRow("Width", self.w)
        fr.addRow("Height", self.h)
        fr.addRow("Corner radius", self.corner)
        root.addWidget(self.g_rect)

        # Ellipse geometry
        self.g_ellipse = QGroupBox("Ellipse")
        fe = QFormLayout(self.g_ellipse)
        self.rx = _spin(0.1, 100000, 1.0)
        self.ry = _spin(0.1, 100000, 1.0)
        fe.addRow("Radius X", self.rx)
        fe.addRow("Radius Y", self.ry)
        root.addWidget(self.g_ellipse)

        # Polygon geometry
        self.g_poly = QGroupBox("Polygon")
        fp = QFormLayout(self.g_poly)
        self.poly_radius = _spin(0.0, 100000, 0.5)
        self.poly_info = QLabel("-")
        fp.addRow("Corner radius", self.poly_radius)
        fp.addRow("Points", self.poly_info)
        root.addWidget(self.g_poly)

        # Line geometry (a 2-point path): length + angle, first point pinned
        self.g_line = QGroupBox("Line")
        fl = QFormLayout(self.g_line)
        self.line_len = _spin(0.01, 100000, 1.0)
        self.line_angle = _spin(-360, 360, 1.0, 1, " °")
        fl.addRow("Length", self.line_len)
        fl.addRow("Angle", self.line_angle)
        root.addWidget(self.g_line)

        # Appearance
        self.g_appear = QGroupBox("Appearance")
        fa = QFormLayout(self.g_appear)
        self.layer_combo = QComboBox()
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(10, 100)
        fa.addRow("Layer", self.layer_combo)
        fa.addRow("Opacity", self.opacity)
        root.addWidget(self.g_appear)

        # Stitching
        self.g_stitch = QGroupBox("Stitching")
        self.g_stitch.setCheckable(True)
        fs = QFormLayout(self.g_stitch)
        self._stitch_form = fs
        self.iron = QComboBox()
        self._iron_keys = list(PRESETS.keys())
        for k in self._iron_keys:
            self.iron.addItem(f"{PRESETS[k].pitch_mm:.2f} mm  ({PRESETS[k].spi:.1f} SPI)", k)
        self.iron.addItem("Custom", "custom")
        self.pitch = _spin(0.5, 50, 0.05)
        self.inset = _spin(0.0, 100, 0.5)
        self.fit = QComboBox()
        self.fit.addItems(["auto", "endpoints", "closed", "none"])
        self.hole_style = QComboBox()
        self.hole_style.addItems(["round", "slit"])
        self.hole_dia = _spin(0.1, 10, 0.1)
        self.slit_len = _spin(0.2, 10, 0.1)
        self.slit_angle = _spin(-89, 89, 1.0, 1, " °")
        self.rows = QComboBox()
        self.rows.addItems(["1 (single)", "2 (double)"])
        self.row_spacing = _spin(0.5, 20, 0.5)
        self.backstitch = QSpinBox()
        self.backstitch.setRange(0, 8)
        self.backstitch.setSuffix(" holes")
        self.backstitch.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.symmetry = QComboBox()
        self.symmetry.addItems(["none", "vertical", "horizontal"])
        self.symmetry.setToolTip(
            "Force flip-symmetric holes so a flipped piece lines up back-to-back")
        self.corner_style = QComboBox()
        self.corner_style.addItems(["auto", "midpoint", "straddle"])
        self.corner_style.setToolTip(
            "Rounded-corner holes are always symmetric about the arc midpoint.\n"
            "auto: best-fitting count for your iron.\n"
            "midpoint: force a hole on the corner apex.\n"
            "straddle: force an even pair around the apex, none on it.")
        fs.addRow("Iron", self.iron)
        fs.addRow("Pitch", self.pitch)
        fs.addRow("Inset from edge", self.inset)
        fs.addRow("Fit", self.fit)
        fs.addRow("Hole style", self.hole_style)
        fs.addRow("Hole ø", self.hole_dia)
        fs.addRow("Slit length", self.slit_len)
        fs.addRow("Slit angle", self.slit_angle)
        fs.addRow("Rows", self.rows)
        fs.addRow("Row spacing", self.row_spacing)
        fs.addRow("Backstitch", self.backstitch)
        fs.addRow("Symmetry", self.symmetry)
        fs.addRow("Corners", self.corner_style)
        root.addWidget(self.g_stitch)

        self.readout = QLabel("")
        self.readout.setWordWrap(True)
        self.readout.setStyleSheet("color:#555;")
        root.addWidget(self.readout)
        root.addStretch(1)

        # wire up: valueChanged does live preview; editingFinished commits undo
        for wdg in (self.pos_x, self.pos_y, self.rot, self.w, self.h,
                    self.corner, self.rx, self.ry, self.poly_radius,
                    self.line_len, self.line_angle,
                    self.pitch, self.inset, self.hole_dia, self.slit_len,
                    self.slit_angle):
            wdg.valueChanged.connect(self._apply)
            wdg.editingFinished.connect(self._commit)
        self.row_spacing.valueChanged.connect(self._apply)
        self.row_spacing.editingFinished.connect(self._commit)
        self.backstitch.valueChanged.connect(self._apply)
        self.backstitch.editingFinished.connect(self._commit)
        self.rows.currentIndexChanged.connect(self._apply_commit)
        self.symmetry.currentIndexChanged.connect(self._apply_commit)
        self.corner_style.currentIndexChanged.connect(self._apply_commit)
        self.mirror.stateChanged.connect(self._apply_commit)
        self.opacity.valueChanged.connect(self._apply)
        self.opacity.sliderReleased.connect(self._commit)
        self.layer_combo.currentIndexChanged.connect(self._apply_commit)
        self.fit.currentIndexChanged.connect(self._apply_commit)
        self.hole_style.currentIndexChanged.connect(self._apply_commit)
        self.g_stitch.toggled.connect(self._apply_commit)
        self.iron.currentIndexChanged.connect(self._on_iron)

    # -- selection ------------------------------------------------------
    def set_layers(self, layers):
        cur = self.layer_combo.currentData()
        self._loading = True
        self.layer_combo.clear()
        for lyr in layers:
            self.layer_combo.addItem(lyr.name, lyr.name)
        idx = self.layer_combo.findData(cur)
        if idx >= 0:
            self.layer_combo.setCurrentIndex(idx)
        self._loading = False

    def show_selection(self, items):
        items = [it for it in items if _alive(it)]
        if len(items) != 1:
            self._item = None
            self.setEnabled(False)
            self.readout.setText(
                f"{len(items)} items selected" if items else "No selection")
            return
        self._item = items[0]
        self.setEnabled(True)
        self._populate()

    def _populate(self):
        self._loading = True
        it = self._item
        is_shape = isinstance(it, ShapeItem)
        is_hole = isinstance(it, HoleItem)
        is_baked = is_shape and bool(it.model.baked_holes)
        self.g_rect.setVisible(False)
        self.g_ellipse.setVisible(False)
        self.g_poly.setVisible(False)
        self.g_line.setVisible(False)
        self.g_appear.setVisible(is_shape)
        self.g_transform.setVisible(is_shape)
        # Only auto-spaced shapes expose path/pitch controls.
        self._set_path_rows_visible(is_shape and not is_baked)
        self.g_stitch.setCheckable(is_shape and not is_baked)

        if is_hole:
            self.g_stitch.setTitle("Hole")
            self._load_hole_style(it.hole)
            self._loading = False
            self._update_readout()
            return

        # annotations (dimension / text) have no stitch/geometry properties
        if isinstance(it, (DimensionItem, TextItem)):
            self.g_stitch.setCheckable(False)
            self.g_stitch.setTitle(
                "Dimension" if isinstance(it, DimensionItem) else "Text")
            self._loading = False
            self._update_readout()
            return

        if is_shape:
            sh = it.model
            self.pos_x.setValue(sh.transform.x)
            self.pos_y.setValue(sh.transform.y)
            self.rot.setValue(sh.transform.rotation)
            self.mirror.setChecked(sh.transform.mirror_x)
            self.opacity.setValue(int(sh.opacity * 100))
            idx = self.layer_combo.findData(sh.layer)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)
            if isinstance(sh, Rectangle):
                self.g_rect.setVisible(True)
                self.w.setValue(sh.width)
                self.h.setValue(sh.height)
                self.corner.setValue(sh.corner_radius)
            elif isinstance(sh, (Circle, Ellipse)):
                self.g_ellipse.setVisible(True)
                self.rx.setValue(sh.rx)
                self.ry.setValue(sh.ry)
            elif isinstance(sh, Polygon):
                self.g_poly.setVisible(True)
                self.poly_radius.setValue(sh.corner_radius)
                self.poly_info.setText(str(len(sh.points)))
            elif _is_line(sh):
                import math
                p0 = sh.transform.apply(sh.points[0])
                p1 = sh.transform.apply(sh.points[1])
                self.g_line.setVisible(True)
                self.line_len.setValue(((p1.x - p0.x) ** 2
                                        + (p1.y - p0.y) ** 2) ** 0.5)
                self.line_angle.setValue(
                    math.degrees(math.atan2(p1.y - p0.y, p1.x - p0.x)))
            if is_baked:
                self.g_stitch.setTitle(f"Holes (grouped · {len(sh.baked_holes)})")
                self._load_hole_style(sh.stitch or StitchSettings())
            else:
                self.g_stitch.setTitle("Stitching")
                st = sh.stitch
                self.g_stitch.setChecked(bool(st and st.enabled))
                self._load_stitch(st or StitchSettings(enabled=False))
        else:  # StitchLine
            st = it.line.settings
            self.g_stitch.setChecked(True)
            self._load_stitch(st)
        self._loading = False
        self._update_readout()

    def sync_geometry_fields(self):
        """Reload the position / size fields from the live model without firing
        _apply. Called when the shape is moved or resized on the canvas so the
        spinboxes never go stale -- otherwise the next _apply (e.g. toggling
        Stitching) would write an old position back and the shape would jump."""
        it = self._item
        if not isinstance(it, ShapeItem) or not _alive(it):
            return
        self._loading = True
        try:
            sh = it.model
            self.pos_x.setValue(sh.transform.x)
            self.pos_y.setValue(sh.transform.y)
            self.rot.setValue(sh.transform.rotation)
            self.mirror.setChecked(sh.transform.mirror_x)
            if isinstance(sh, Rectangle):
                self.w.setValue(sh.width)
                self.h.setValue(sh.height)
                self.corner.setValue(sh.corner_radius)
            elif isinstance(sh, (Circle, Ellipse)):
                self.rx.setValue(sh.rx)
                self.ry.setValue(sh.ry)
            elif _is_line(sh):
                import math
                p0 = sh.transform.apply(sh.points[0])
                p1 = sh.transform.apply(sh.points[1])
                self.line_len.setValue(((p1.x - p0.x) ** 2
                                        + (p1.y - p0.y) ** 2) ** 0.5)
                self.line_angle.setValue(
                    math.degrees(math.atan2(p1.y - p0.y, p1.x - p0.x)))
        finally:
            self._loading = False
        self._update_readout()

    def _set_path_rows_visible(self, vis: bool):
        for w in (self.iron, self.pitch, self.inset, self.fit, self.rows,
                  self.row_spacing, self.backstitch):
            self._stitch_form.setRowVisible(w, vis)

    def _sync_hole_vis(self):
        """Show Hole ø for round holes, slit length/angle for slits."""
        slit = self.hole_style.currentText() == "slit"
        self._stitch_form.setRowVisible(self.hole_dia, not slit)
        self._stitch_form.setRowVisible(self.slit_len, slit)
        self._stitch_form.setRowVisible(self.slit_angle, slit)

    def _load_hole_style(self, g):
        i = self.hole_style.findText(g.hole_style)
        self.hole_style.setCurrentIndex(i if i >= 0 else 0)
        self.hole_dia.setValue(g.hole_diameter)
        self.slit_len.setValue(g.slit_length)
        self.slit_angle.setValue(g.slit_angle)
        self._sync_hole_vis()

    def _load_stitch(self, st: StitchSettings):
        self.pitch.setValue(st.pitch_mm)
        self.inset.setValue(st.inset)
        i = self.fit.findText(st.fit)
        self.fit.setCurrentIndex(i if i >= 0 else 0)
        i = self.hole_style.findText(st.hole_style)
        self.hole_style.setCurrentIndex(i if i >= 0 else 0)
        self.hole_dia.setValue(st.hole_diameter)
        self.slit_len.setValue(st.slit_length)
        self.slit_angle.setValue(st.slit_angle)
        self.rows.setCurrentIndex(1 if getattr(st, "rows", 1) == 2 else 0)
        self.row_spacing.setValue(getattr(st, "row_spacing", 3.0))
        self.backstitch.setValue(getattr(st, "backstitch", 0))
        i = self.symmetry.findText(getattr(st, "symmetry", "none"))
        self.symmetry.setCurrentIndex(i if i >= 0 else 0)
        i = self.corner_style.findText(getattr(st, "corner_style", "auto"))
        self.corner_style.setCurrentIndex(i if i >= 0 else 0)
        self._sync_iron_combo(st.pitch_mm)
        self._sync_hole_vis()
        self.row_spacing.setVisible(self.rows.currentIndex() == 1)

    def _sync_iron_combo(self, pitch):
        for i, k in enumerate(self._iron_keys):
            if abs(PRESETS[k].pitch_mm - pitch) < 1e-6:
                self.iron.setCurrentIndex(i)
                return
        self.iron.setCurrentIndex(self.iron.count() - 1)  # Custom

    def _on_iron(self):
        if self._loading:
            return
        key = self.iron.currentData()
        if key and key != "custom":
            self._loading = True
            self.pitch.setValue(PRESETS[key].pitch_mm)
            self._loading = False
            self._apply()
            self._commit()

    def _commit(self):
        if not self._loading and _alive(self._item):
            self.committed.emit()

    def _apply_commit(self):
        self._apply()
        self._commit()

    # -- apply ----------------------------------------------------------
    def _apply(self):
        if self._loading:
            return
        if not _alive(self._item):
            self._item = None
            return
        it = self._item
        if isinstance(it, (DimensionItem, TextItem)):
            return                          # annotations have no editable props
        if isinstance(it, HoleItem):
            self._write_hole_style(it.hole)
            self._sync_hole_vis()
            self.canvas.refresh_item(it)
            return
        if isinstance(it, ShapeItem) and it.model.baked_holes:
            sh = it.model
            sh.transform.x = self.pos_x.value()
            sh.transform.y = self.pos_y.value()
            sh.transform.rotation = self.rot.value()
            sh.transform.mirror_x = self.mirror.isChecked()
            sh.opacity = self.opacity.value() / 100.0
            sh.layer = self.layer_combo.currentData() or sh.layer
            self._apply_shape_geometry(sh)
            if sh.stitch is None:
                sh.stitch = StitchSettings(enabled=False)
            self._write_hole_style(sh.stitch)
            self._sync_hole_vis()
            self.canvas.refresh_item(it)
            self._update_readout()
            return
        if isinstance(it, ShapeItem):
            sh = it.model
            sh.transform.x = self.pos_x.value()
            sh.transform.y = self.pos_y.value()
            sh.transform.rotation = self.rot.value()
            sh.transform.mirror_x = self.mirror.isChecked()
            sh.opacity = self.opacity.value() / 100.0
            sh.layer = self.layer_combo.currentData() or sh.layer
            self._apply_shape_geometry(sh)
            if self.g_stitch.isChecked():
                if sh.stitch is None:
                    sh.stitch = StitchSettings()
                self._write_stitch(sh.stitch)
                sh.stitch.enabled = True
            elif sh.stitch is not None:
                sh.stitch.enabled = False
        else:
            self._write_stitch(it.line.settings)
            it.line.settings.inset = 0.0
        self._sync_iron_combo(self.pitch.value())
        self._sync_hole_vis()
        self.row_spacing.setVisible(self.rows.currentIndex() == 1)
        self.canvas.refresh_item(it)
        self._update_readout()

    def _apply_shape_geometry(self, sh):
        if isinstance(sh, Rectangle):
            sh.width = self.w.value()
            sh.height = self.h.value()
            sh.corner_radius = self.corner.value()
        elif isinstance(sh, (Circle, Ellipse)):
            sh.rx = self.rx.value()
            sh.ry = self.ry.value()
        elif isinstance(sh, Polygon):
            sh.corner_radius = self.poly_radius.value()
        elif _is_line(sh):
            import math
            from leathercad.geometry import Vec2
            p0 = sh.transform.apply(sh.points[0])       # keep the first point
            length = self.line_len.value()
            ang = math.radians(self.line_angle.value())
            p1 = Vec2(p0.x + length * math.cos(ang),
                      p0.y + length * math.sin(ang))
            sh.points[1] = sh.transform.inverse_apply(p1)

    def _write_hole_style(self, obj):
        obj.hole_style = self.hole_style.currentText()
        obj.hole_diameter = self.hole_dia.value()
        obj.slit_length = self.slit_len.value()
        obj.slit_angle = self.slit_angle.value()

    def _write_stitch(self, st: StitchSettings):
        st.pitch_mm = self.pitch.value()
        st.inset = self.inset.value()
        st.fit = self.fit.currentText()
        st.hole_style = self.hole_style.currentText()
        st.hole_diameter = self.hole_dia.value()
        st.slit_length = self.slit_len.value()
        st.slit_angle = self.slit_angle.value()
        st.rows = 2 if self.rows.currentIndex() == 1 else 1
        st.row_spacing = self.row_spacing.value()
        st.backstitch = self.backstitch.value()
        st.symmetry = self.symmetry.currentText()
        st.corner_style = self.corner_style.currentText()

    def focus_primary_dimension(self):
        """Focus the main size field so the user can type an exact value."""
        target = None
        if self.g_rect.isVisible():
            target = self.w
        elif self.g_ellipse.isVisible():
            target = self.rx
        elif self.g_poly.isVisible():
            target = self.poly_radius
        if target is not None:
            target.setFocus()
            target.selectAll()

    def _update_readout(self):
        it = self._item
        if not _alive(it):
            self.readout.setText("")
            return
        if isinstance(it, HoleItem):
            self.readout.setText("<b>1 hole</b> (ungrouped)<br>"
                                 "move or Delete freely; right-click to group")
            return
        if isinstance(it, DimensionItem):
            self.readout.setText(f"<b>Dimension</b><br>{it.dim.label()}")
            return
        if isinstance(it, TextItem):
            self.readout.setText(f"<b>Text</b><br>“{it.model.text}”")
            return
        if isinstance(it, ShapeItem) and it.model.baked_holes:
            self.readout.setText(
                f"<b>{len(it.model.baked_holes)} holes</b> grouped to this shape"
                "<br>Ungroup (right-click) to edit them individually")
            return
        res = it._holes if isinstance(it, ShapeItem) else it.line.result()
        if res and res.count:
            gaps = res.chord_spacings()
            pitches = res.pitches or [0]
            txt = (f"<b>{res.count} holes</b><br>"
                   f"chord spacing {min(gaps):.2f}–{max(gaps):.2f} mm<br>"
                   f"effective pitch "
                   f"{min(pitches):.3f}–{max(pitches):.3f} mm")
            self.readout.setText(txt)
        else:
            self.readout.setText("No holes")


# ---------------------------------------------------------------------------
# Layers panel
# ---------------------------------------------------------------------------
class LayersPanel(QWidget):
    currentLayerChanged = Signal(str)
    committed = Signal()

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.addWidget(QLabel("Layers (colour → laser job)"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._row_changed)
        root.addWidget(self.list)

        row = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_color = QPushButton("Colour…")
        self.btn_vis = QPushButton("Show/Hide")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_color)
        row.addWidget(self.btn_vis)
        root.addLayout(row)

        self.role = QComboBox()
        self.role.addItems(list(ROLES))
        self.role.currentIndexChanged.connect(self._role_changed)
        rl = QHBoxLayout()
        rl.addWidget(QLabel("Role"))
        rl.addWidget(self.role)
        root.addLayout(rl)

        self.btn_add.clicked.connect(self._add_layer)
        self.btn_color.clicked.connect(self._pick_color)
        self.btn_vis.clicked.connect(self._toggle_vis)
        self.reload()

    def _swatch(self, color: str) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(QColor(color))
        return QIcon(pm)

    def reload(self):
        self.list.blockSignals(True)
        self.list.clear()
        for lyr in self.canvas.doc.layers:
            vis = "●" if lyr.visible else "○"
            item = QListWidgetItem(self._swatch(lyr.color),
                                   f"{vis}  {lyr.name}  [{lyr.role}]")
            item.setData(Qt.UserRole, lyr.name)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(0)

    def current_layer(self) -> Optional[Layer]:
        it = self.list.currentItem()
        if it is None:
            return None
        return self.canvas.doc.layer(it.data(Qt.UserRole))

    def _row_changed(self):
        lyr = self.current_layer()
        if lyr:
            self.role.blockSignals(True)
            self.role.setCurrentText(lyr.role)
            self.role.blockSignals(False)
            self.currentLayerChanged.emit(lyr.name)

    def _add_layer(self):
        from leathercad.layers import Layer, CUT
        n = len(self.canvas.doc.layers) + 1
        self.canvas.doc.add_layer(Layer(f"Layer {n}", "#cc00cc", CUT))
        self.reload()
        self.committed.emit()

    def _pick_color(self):
        lyr = self.current_layer()
        if not lyr:
            return
        c = QColorDialog.getColor(QColor(lyr.color), self, "Layer colour")
        if c.isValid():
            lyr.color = c.name()
            self.canvas.refresh_all()
            self.reload()
            self.committed.emit()

    def _toggle_vis(self):
        lyr = self.current_layer()
        if not lyr:
            return
        lyr.visible = not lyr.visible
        self.canvas.refresh_all()
        self.reload()
        self.committed.emit()

    def _role_changed(self):
        lyr = self.current_layer()
        if lyr:
            lyr.role = self.role.currentText()
            self.reload()
            self.committed.emit()

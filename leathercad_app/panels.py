"""Dockable panels: shape/stitch properties and the layer manager."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox, QDoubleSpinBox, QCheckBox,
    QComboBox, QSlider, QLabel, QPushButton, QListWidget, QListWidgetItem,
    QHBoxLayout, QColorDialog, QSpinBox, QAbstractSpinBox, QLineEdit,
)

from leathercad.stitchsettings import StitchSettings
from leathercad.shapes import Rectangle, Ellipse, Circle, Polygon, PathShape
from leathercad.layers import Layer, ROLES
from .items import ShapeItem, StitchLineItem, HoleItem, DimensionItem, TextItem
from .mathspin import NoWheelComboBox, NoWheelSpinBox

try:
    import shiboken6

    def _alive(obj) -> bool:
        return obj is not None and shiboken6.isValid(obj)
except Exception:  # pragma: no cover
    def _alive(obj) -> bool:
        return obj is not None


def _spin(lo, hi, step=1.0, decimals=2, suffix=" mm") -> QDoubleSpinBox:
    from .mathspin import MathSpinBox
    s = MathSpinBox()
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
        # A clearer frame around each group -- the Fusion default hairline is
        # nearly invisible. A mid grey with alpha reads on both light and dark.
        self.setStyleSheet("""
            QGroupBox {
                border: 1px solid rgba(140, 142, 148, 0.85);
                border-radius: 5px;
                margin-top: 9px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 8px;
                padding: 0 4px;
            }
        """)

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

        # Text / lettering (editable): applied live, re-bakes the glyph contours
        self.g_text = QGroupBox("Text")
        ft = QFormLayout(self.g_text)
        self.text_str = QLineEdit()
        self.text_font = NoWheelComboBox()
        from PySide6.QtGui import QFontDatabase
        self.text_font.addItems(QFontDatabase.families())
        self.text_size = _spin(0.5, 1000.0, 1.0, 1)
        self.text_tracking = _spin(-50.0, 200.0, 1.0, 1, " %")
        self.text_bold = QCheckBox("Bold")
        self.text_italic = QCheckBox("Italic")
        ft.addRow("String", self.text_str)
        ft.addRow("Font", self.text_font)
        ft.addRow("Cap height", self.text_size)
        ft.addRow("Tracking", self.text_tracking)
        ft.addRow("", self.text_bold)
        ft.addRow("", self.text_italic)
        root.addWidget(self.g_text)

        # Appearance
        self.g_appear = QGroupBox("Appearance")
        fa = QFormLayout(self.g_appear)
        self.layer_combo = NoWheelComboBox()
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
        # Punch = style + pitch. Style picks the hole SHAPE; pitch (the standard
        # ladder, shared across makers) is the primary selector. Round holes add
        # a diameter selector. ``_cur_hole_style`` is the low-level render/export
        # primitive (round | slit | diamond) the style maps to.
        self._cur_hole_style = "round"
        self._path_rows_vis = True     # pitch/fit/... shown (hidden for baked)
        self.punch_style = NoWheelComboBox()
        for key in ("round", "oblique", "french", "diamond"):
            self.punch_style.addItem(key.capitalize(), key)
        self.pitch_combo = NoWheelComboBox()
        self._build_pitch_combo()
        self.pitch = _spin(0.5, 50, 0.05)
        self.hole_dia_combo = NoWheelComboBox()
        self._build_dia_combo()
        self.inset = _spin(0.0, 100, 0.5)
        self.fit = NoWheelComboBox()
        self.fit.addItems(["auto", "endpoints", "closed", "none"])
        self.hole_dia = _spin(0.1, 10, 0.1)
        self.slit_len = _spin(0.2, 10, 0.1)
        self.slit_angle = _spin(-89, 89, 1.0, 1, " °")
        self.rows = NoWheelComboBox()
        self.rows.addItems(["1 (single)", "2 (double)"])
        self.row_spacing = _spin(0.5, 20, 0.5)
        self.backstitch = NoWheelSpinBox()
        self.backstitch.setRange(0, 8)
        self.backstitch.setSuffix(" holes")
        self.backstitch.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.symmetry = NoWheelComboBox()
        self.symmetry.addItems(["none", "vertical", "horizontal"])
        self.symmetry.setToolTip(
            "Force flip-symmetric holes so a flipped piece lines up back-to-back")
        self.corner_style = NoWheelComboBox()
        self.corner_style.addItems(["auto", "midpoint", "straddle"])
        self.corner_style.setToolTip(
            "Rounded-corner holes are always symmetric about the arc midpoint.\n"
            "auto: the arrangement with the least pitch deviation.\n"
            "midpoint: force a hole on the corner apex.\n"
            "straddle: force an even pair around the apex, none on it.")
        fs.addRow("Punch", self.punch_style)
        fs.addRow("Pitch", self.pitch_combo)
        fs.addRow("Pitch (mm)", self.pitch)
        fs.addRow("Inset from edge", self.inset)
        fs.addRow("Fit", self.fit)
        fs.addRow("Hole ø", self.hole_dia_combo)
        fs.addRow("Hole ø (mm)", self.hole_dia)
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
        # palette-based so it stays readable in both light and dark themes
        self.readout.setStyleSheet("color: palette(mid);")
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
        self.g_stitch.toggled.connect(self._apply_commit)
        self.punch_style.currentIndexChanged.connect(self._on_punch_style)
        self.pitch_combo.currentIndexChanged.connect(self._on_pitch_combo)
        self.hole_dia_combo.currentIndexChanged.connect(self._on_dia_combo)
        # Text: live preview on change; commit when the field is done being edited.
        self.text_str.textEdited.connect(self._apply)
        self.text_str.editingFinished.connect(self._commit)
        self.text_size.valueChanged.connect(self._apply)
        self.text_size.editingFinished.connect(self._commit)
        self.text_tracking.valueChanged.connect(self._apply)
        self.text_tracking.editingFinished.connect(self._commit)
        self.text_font.currentIndexChanged.connect(self._apply_commit)
        self.text_bold.stateChanged.connect(self._apply_commit)
        self.text_italic.stateChanged.connect(self._apply_commit)

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
        is_stitchline = isinstance(it, StitchLineItem)
        is_text = isinstance(it, TextItem)
        is_baked = is_shape and bool(it.model.baked_holes)
        self.g_rect.setVisible(False)
        self.g_ellipse.setVisible(False)
        self.g_poly.setVisible(False)
        self.g_line.setVisible(False)
        self.g_text.setVisible(is_text)
        self.g_stitch.setVisible(not is_text)   # text has no stitch properties
        self.g_appear.setVisible(is_shape)
        self.g_transform.setVisible(is_shape)
        # Auto-spaced shapes AND drawn seams expose the pitch / fit controls.
        self._set_path_rows_visible((is_shape and not is_baked) or is_stitchline)
        # A drawn seam is the stitch line itself -- it is never inset.
        if is_stitchline:
            self._stitch_form.setRowVisible(self.inset, False)
        self.g_stitch.setCheckable(is_shape and not is_baked)

        if is_hole:
            self.g_stitch.setTitle("Hole")
            self._load_hole_style(it.hole)
            self._loading = False
            self._update_readout()
            return

        # editable lettering: string / font / size / style / tracking
        if is_text:
            m = it.model
            self.text_str.setText(m.text)
            # select the model's family, or the bundled default when it is empty
            # / not installed -- never index 0, which would silently rewrite the
            # typeface to some unrelated font on the next edit.
            from .fonts import default_family
            i = self.text_font.findText(m.font_family or default_family())
            if i < 0:
                i = self.text_font.findText(default_family())
            self.text_font.setCurrentIndex(max(0, i))
            self.text_size.setValue(m.size)
            self.text_tracking.setValue(m.tracking)
            self.text_bold.setChecked(m.bold)
            self.text_italic.setChecked(m.italic)
            self._loading = False
            self._update_readout()
            return

        # a dimension is a read-only annotation (no stitch/geometry properties)
        if isinstance(it, DimensionItem):
            self.g_stitch.setCheckable(False)
            self.g_stitch.setTitle("Dimension")
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
            self.g_stitch.setTitle("Seam stitching")
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
        # every control that governs where holes GO (as opposed to how they
        # look). Hidden for baked/grouped holes and loose holes, whose
        # positions are fixed -- only the appearance rows (style / ø / slit)
        # stay. Symmetry and Corners belong here too: they only steer the
        # auto-distribution, so they'd do nothing on baked holes.
        self._path_rows_vis = vis
        for w in (self.pitch_combo, self.inset,
                  self.fit, self.rows, self.row_spacing, self.backstitch,
                  self.symmetry, self.corner_style):
            self._stitch_form.setRowVisible(w, vis)
        self._sync_hole_vis()          # the mm entry boxes depend on this too

    def _sync_hole_vis(self):
        """Round holes show the Hole ø dropdown; slit/diamond show slit
        length/angle. The exact mm entry boxes appear only when their dropdown
        is on 'Custom…' -- otherwise the number duplicates the dropdown."""
        slit = self._cur_hole_style in ("slit", "diamond")
        self._stitch_form.setRowVisible(self.hole_dia_combo, not slit)
        self._stitch_form.setRowVisible(self.slit_len, slit)
        self._stitch_form.setRowVisible(self.slit_angle, slit)
        pitch_custom = self.pitch_combo.currentData() is None
        self._stitch_form.setRowVisible(self.pitch,
                                        self._path_rows_vis and pitch_custom)
        dia_custom = self.hole_dia_combo.currentData() is None
        self._stitch_form.setRowVisible(self.hole_dia, (not slit) and dia_custom)

    def _load_punch(self, hole_style, punch_style, pitch):
        """Point the style + pitch selectors at a saved hole (shared by shapes,
        seams and loose holes)."""
        self._cur_hole_style = hole_style
        style = self._infer_punch_style(hole_style, punch_style)
        i = self.punch_style.findData(style)
        self.punch_style.setCurrentIndex(i if i >= 0 else 0)
        self._select_preset(self.pitch_combo, pitch)

    def _load_hole_style(self, g):
        self._load_punch(g.hole_style, getattr(g, "punch_style", ""),
                         getattr(g, "pitch_mm", self.pitch.value()))
        self.hole_dia.setValue(g.hole_diameter)
        self._select_preset(self.hole_dia_combo, g.hole_diameter)
        self.slit_len.setValue(g.slit_length)
        self.slit_angle.setValue(g.slit_angle)
        self._sync_hole_vis()

    def _load_stitch(self, st: StitchSettings):
        self.pitch.setValue(st.pitch_mm)
        self.inset.setValue(st.inset)
        i = self.fit.findText(st.fit)
        self.fit.setCurrentIndex(i if i >= 0 else 0)
        self._load_punch(st.hole_style, getattr(st, "punch_style", ""),
                         st.pitch_mm)
        self.hole_dia.setValue(st.hole_diameter)
        self._select_preset(self.hole_dia_combo, st.hole_diameter)
        self.slit_len.setValue(st.slit_length)
        self.slit_angle.setValue(st.slit_angle)
        self.rows.setCurrentIndex(1 if getattr(st, "rows", 1) == 2 else 0)
        self.row_spacing.setValue(getattr(st, "row_spacing", 3.0))
        self.backstitch.setValue(getattr(st, "backstitch", 0))
        i = self.symmetry.findText(getattr(st, "symmetry", "none"))
        self.symmetry.setCurrentIndex(i if i >= 0 else 0)
        i = self.corner_style.findText(getattr(st, "corner_style", "auto"))
        self.corner_style.setCurrentIndex(i if i >= 0 else 0)
        self._sync_hole_vis()
        self._stitch_form.setRowVisible(self.row_spacing, self.rows.currentIndex() == 1)

    # -- punch: style + pitch (+ diameter for round) --------------------
    @staticmethod
    def _infer_punch_style(hole_style, punch_style=""):
        """Best display style for a saved hole: trust an explicit oblique/french
        punch, otherwise read it off the render primitive (old files predate the
        punch field, so a stale 'round' punch on a slit hole is ignored)."""
        if hole_style == "round":
            return "round"
        if hole_style == "diamond":
            return "diamond"
        return punch_style if punch_style in ("oblique", "french") else "oblique"

    def _build_pitch_combo(self):
        from leathercad.irons import STANDARD_PITCHES, mm_to_spi
        self.pitch_combo.blockSignals(True)
        self.pitch_combo.clear()
        for p in STANDARD_PITCHES:
            self.pitch_combo.addItem(f"{p:g} mm  ({mm_to_spi(p):.1f} SPI)",
                                     round(p, 4))
        self.pitch_combo.addItem("Custom…", None)
        self.pitch_combo.blockSignals(False)

    def _build_dia_combo(self):
        from leathercad.irons import ROUND_DIAMETERS
        self.hole_dia_combo.blockSignals(True)
        self.hole_dia_combo.clear()
        for d in ROUND_DIAMETERS:
            self.hole_dia_combo.addItem(f"{d:g} mm", round(d, 4))
        self.hole_dia_combo.addItem("Custom…", None)
        self.hole_dia_combo.blockSignals(False)

    @staticmethod
    def _select_preset(combo, value):
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data is not None and abs(float(data) - value) < 0.02:
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(combo.count() - 1)          # Custom…

    def _apply_style_geometry(self, style):
        """Set the hole primitive + default hole dimensions for a punch style,
        and re-sync the dimension dropdowns to match."""
        from leathercad.irons import geometry_for
        geom = geometry_for(style, self.pitch.value())
        self._cur_hole_style = geom["hole_style"]
        if style == "round":
            self.hole_dia.setValue(geom["hole_diameter"])
            self._select_preset(self.hole_dia_combo, self.hole_dia.value())
        else:
            self.slit_len.setValue(geom["slit_length"])
            self.slit_angle.setValue(geom["slit_angle"])

    def _on_punch_style(self):
        if self._loading:
            return
        style = self.punch_style.currentData()
        self._loading = True
        self._apply_style_geometry(style)         # new hole shape, keep pitch
        self._sync_hole_vis()
        self._loading = False
        self._apply()
        self._commit()

    def _on_pitch_combo(self):
        if self._loading:
            return
        from leathercad.irons import geometry_for
        data = self.pitch_combo.currentData()
        self._loading = True
        if data is not None:
            self.pitch.setValue(float(data))
            # rescale the slit/diamond length to the new pitch (keep the slant)
            if self._cur_hole_style in ("slit", "diamond"):
                self.slit_len.setValue(
                    geometry_for(self.punch_style.currentData(), float(data))
                    ["slit_length"])
        self._sync_hole_vis()             # reveal/hide the "Custom" mm box
        self._loading = False
        if data is not None:
            self._apply()
            self._commit()
        else:
            self.pitch.setFocus()         # Custom… -> let them type

    def _on_dia_combo(self):
        if self._loading:
            return
        data = self.hole_dia_combo.currentData()
        self._loading = True
        if data is not None:
            self.hole_dia.setValue(float(data))
        self._sync_hole_vis()
        self._loading = False
        if data is not None:
            self._apply()
            self._commit()
        else:
            self.hole_dia.setFocus()

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
        if isinstance(it, DimensionItem):
            return                          # a dimension has no editable props
        if isinstance(it, TextItem):
            m = it.model
            m.text = self.text_str.text()
            m.font_family = self.text_font.currentText() or m.font_family
            m.size = self.text_size.value()
            m.tracking = self.text_tracking.value()
            m.bold = self.text_bold.isChecked()
            m.italic = self.text_italic.isChecked()
            self.canvas.rebake_text(m)       # re-bake the glyph contours
            self.canvas.refresh_item(it)
            self._update_readout()
            return
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
            self._record_bindings(sh)
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
            self._record_bindings(sh)
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
        self._sync_hole_vis()
        self._stitch_form.setRowVisible(self.row_spacing, self.rows.currentIndex() == 1)
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

    def _record_bindings(self, sh) -> None:
        """Remember which fields the user set FROM a parameter expression, so
        editing the parameter later re-drives them (see Document.bindings).
        A field re-typed as a plain number drops its binding."""
        doc = self.canvas.doc
        pairs = [(self.pos_x, "x"), (self.pos_y, "y"), (self.rot, "rot"),
                 (self.w, "w"), (self.h, "h"), (self.corner, "corner"),
                 (self.rx, "rx"), (self.ry, "ry"),
                 (self.poly_radius, "corner"),
                 (self.pitch, "pitch"), (self.inset, "inset")]
        for spin, field in pairs:
            expr = getattr(spin, "last_expr", None)
            if expr is None:
                continue                       # untouched since load
            key = f"{sh.shape_id}:{field}"
            if expr:
                doc.bindings[key] = expr
            else:
                doc.bindings.pop(key, None)    # plain number typed: unlink

    def _write_hole_style(self, obj):
        obj.hole_style = self._cur_hole_style
        obj.hole_diameter = self.hole_dia.value()
        obj.slit_length = self.slit_len.value()
        obj.slit_angle = self.slit_angle.value()

    def _write_stitch(self, st: StitchSettings):
        st.pitch_mm = self.pitch.value()
        st.inset = self.inset.value()
        st.fit = self.fit.currentText()
        st.punch_style = self.punch_style.currentData() or "round"
        st.hole_style = self._cur_hole_style
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
        area_line = ""
        if isinstance(it, ShapeItem):
            pts, _c, closed = it.model.world_polyline()
            if closed and len(pts) >= 4:
                from leathercad.offset import signed_area
                ring = pts[:-1] if (pts[0] - pts[-1]).length() < 1e-9 else pts
                area_line = (f"<br>area {abs(signed_area(ring)) / 100.0:.1f} "
                             f"cm²")
        res = it._holes if isinstance(it, ShapeItem) else it.line.result()
        if res and res.count:
            gaps = res.chord_spacings()
            pitches = res.pitches or [0]
            txt = f"<b>{res.count} holes</b>"
            if gaps:                             # a lone hole has no spacing
                txt += (f"<br>chord spacing {min(gaps):.2f}–{max(gaps):.2f} mm"
                        f"<br>effective pitch "
                        f"{min(pitches):.3f}–{max(pitches):.3f} mm")
            self.readout.setText(txt + area_line)
        else:
            self.readout.setText("No holes" + area_line)


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
        self.list.itemChanged.connect(self._item_checked)
        root.addWidget(self.list)

        row = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_color = QPushButton("Colour…")
        self.btn_vis = QPushButton("Show/Hide")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_color)
        row.addWidget(self.btn_vis)
        root.addLayout(row)

        self.role = NoWheelComboBox()
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
        cur = self.list.currentRow()
        self.list.clear()
        for lyr in self.canvas.doc.layers:
            item = QListWidgetItem(self._swatch(lyr.color),
                                   f"{lyr.name}  [{lyr.role}]")
            item.setData(Qt.UserRole, lyr.name)
            # A real checkbox toggles visibility (click to show/hide the layer).
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if lyr.visible else Qt.Unchecked)
            item.setToolTip("Tick to show this layer, untick to hide it")
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(cur if 0 <= cur < self.list.count() else 0)

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

    def _item_checked(self, item):
        """A layer's visibility checkbox was ticked/unticked in the list."""
        lyr = self.canvas.doc.layer(item.data(Qt.UserRole))
        if not lyr:
            return
        vis = item.checkState() == Qt.Checked
        if vis == lyr.visible:
            return
        lyr.visible = vis
        self.canvas.refresh_all()
        self.committed.emit()

    def _role_changed(self):
        lyr = self.current_layer()
        if lyr:
            lyr.role = self.role.currentText()
            self.reload()
            self.committed.emit()

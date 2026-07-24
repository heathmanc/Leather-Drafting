"""Assembled 3D preview: fold the flat panels into the finished object.

The folding maths lives in the Qt-free ``leathercad.fold3d`` engine; this module
only *projects and paints* it, and adds the interactive orbit widget plus a
headless PNG renderer (handy for docs / marketing shots). Panels are the closed
shapes in the document; hinges are auto-detected from edges that two panels
share in the flat layout (a paper-net), so a design drawn edge-to-edge folds up
with no extra wiring, and a fold-amount slider animates flat -> assembled.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPolygonF, QImage)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QSlider, QDialog)

from leathercad.geometry import Vec2
from leathercad.fold3d import Panel, Hinge, assemble, project, rotate_view, Vec3

_LIGHT = Vec3(0.35, -0.5, 0.78).normalized()      # a soft top-right key light


# -- building panels + hinges from a document --------------------------------

def _panel_from_shape(shape) -> Optional[Panel]:
    """A closed shape -> a flat Panel (world-mm outline + world stitch holes)."""
    from leathercad.stitching import holes_for_shape
    path = shape.local_path()
    if not path.closed:
        return None
    t = shape.transform
    outline = [t.apply(p) for p in path.flatten()]
    if len(outline) < 3:
        return None
    holes: List[Vec2] = []
    try:
        res = holes_for_shape(shape)
        holes = [t.apply(Vec2(h.point.x, h.point.y)) for h in res.holes]
    except Exception:
        holes = []
    name = getattr(shape, "name", "") or type(shape).__name__
    return Panel(id=str(id(shape)), outline=outline, holes=holes, name=name)


def _shared_edge(a: Panel, b: Panel, tol: float = 0.6
                 ) -> Optional[Tuple[Tuple[Vec2, Vec2], Tuple[Vec2, Vec2]]]:
    """If panels ``a`` and ``b`` have a (near-)coincident edge in the flat
    layout, return ``(a_edge, b_edge)`` -- the hinge they fold about."""
    def edges(p):
        n = len(p.outline)
        return [(p.outline[i], p.outline[(i + 1) % n]) for i in range(n)]

    for a0, a1 in edges(a):
        la = (a1 - a0).length()
        if la < 1e-6:
            continue
        for b0, b1 in edges(b):
            lb = (b1 - b0).length()
            if abs(la - lb) > tol:
                continue
            # same edge either orientation
            if ((a0 - b0).length() < tol and (a1 - b1).length() < tol) or \
               ((a0 - b1).length() < tol and (a1 - b0).length() < tol):
                return (a0, a1), (a0, a1)
    return None


def build_from_document(doc, angle_deg: float = 90.0
                        ) -> Tuple[Dict[str, Panel], List[Hinge]]:
    """Collect closed shapes as panels and auto-hinge panels that share an edge.
    The hinge graph is a forest; ``assemble`` roots it at the first panel."""
    panels: Dict[str, Panel] = {}
    for shape in doc.shapes:
        p = _panel_from_shape(shape)
        if p is not None:
            panels[p.id] = p
    ids = list(panels)
    hinges: List[Hinge] = []
    linked = set()
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = panels[ids[i]], panels[ids[j]]
            se = _shared_edge(a, b)
            if se is not None and (ids[i], ids[j]) not in linked:
                hinges.append(Hinge(a.id, b.id, se[0], se[1], angle_deg))
                linked.add((ids[i], ids[j]))
    return panels, hinges


# -- painting ----------------------------------------------------------------

def _panel_normal_view(outline_view: List[Vec3]) -> Vec3:
    """A face normal (in view space) from the first non-degenerate triangle."""
    if len(outline_view) < 3:
        return Vec3(0.0, 0.0, 1.0)
    a, b, c = outline_view[0], outline_view[1], outline_view[2]
    n = (b - a).cross(c - a)
    return n.normalized() if n.length() > 1e-9 else Vec3(0.0, 0.0, 1.0)


def paint_assembly(painter: QPainter, w: int, h: int, panels, hinges,
                   *, root=None, fraction=1.0, yaw=0.6, pitch=1.0,
                   zoom=1.0, dark=False, thickness=0.0, numbers=False,
                   bad_holes=None) -> None:
    """Render the folded assembly into a ``w x h`` area with a painter."""
    bg = QColor(28, 30, 34) if dark else QColor(244, 244, 246)
    painter.fillRect(0, 0, w, h, bg)
    painter.setRenderHint(QPainter.Antialiasing, True)

    placed = assemble(panels, hinges, root=root, fraction=fraction,
                      thickness=thickness)
    if not placed:
        return

    # view-space geometry for every panel (for shading + a shared fit box)
    view = []
    allx, ally = [], []
    for pl in placed:
        ov = [rotate_view(v, yaw, pitch) for v in pl.outline]
        hv = [rotate_view(v, yaw, pitch) for v in pl.holes]
        depth = sum(v.z for v in ov) / len(ov) if ov else 0.0
        view.append((pl, ov, hv, depth))
        allx += [v.x for v in ov]
        ally += [v.y for v in ov]
    if not allx:
        return
    view.sort(key=lambda t: t[3])                 # painter's algorithm, far first

    minx, maxx = min(allx), max(allx)
    miny, maxy = min(ally), max(ally)
    span = max(maxx - minx, maxy - miny, 1e-6)
    scale = 0.82 * min(w, h) / span * zoom
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    def to_screen(v: Vec3) -> QPointF:
        # centre the model; flip Y so +y is up on screen
        return QPointF(w / 2.0 + (v.x - cx) * scale,
                       h / 2.0 - (v.y - cy) * scale)

    base = QColor(181, 121, 58) if not dark else QColor(158, 104, 48)  # leather
    for pl, ov, hv, _d in view:
        n = _panel_normal_view(ov)
        lam = max(0.0, n.dot(_LIGHT))
        shade = 0.45 + 0.55 * lam                 # ambient + diffuse
        col = QColor(int(base.red() * shade),
                     int(base.green() * shade),
                     int(base.blue() * shade))
        poly = QPolygonF([to_screen(v) for v in ov])
        painter.setBrush(QBrush(col))
        painter.setPen(QPen(QColor(60, 40, 20), 1.2))
        painter.drawPolygon(poly)
        # stitch holes as small dots on the face; holes that don't register
        # (misaligned or short of the edge) are drawn red so problems stand out
        if hv:
            painter.setPen(Qt.NoPen)
            r = max(1.0, 0.7 * scale)
            flagged = (bad_holes or {}).get(pl.id, set())
            for i, v in enumerate(hv):
                if i in flagged:
                    painter.setBrush(QBrush(QColor(230, 40, 40)))
                    painter.drawEllipse(to_screen(v), r * 1.6, r * 1.6)
                else:
                    painter.setBrush(QBrush(QColor(35, 24, 12)))
                    painter.drawEllipse(to_screen(v), r, r)
        # panel number at the facet centre
        if numbers and pl.name:
            c3 = Vec3(sum(v.x for v in ov) / len(ov),
                      sum(v.y for v in ov) / len(ov),
                      sum(v.z for v in ov) / len(ov))
            painter.setPen(QPen(QColor(255, 255, 255)))
            f = painter.font()
            f.setBold(True)
            f.setPointSize(max(9, int(0.12 * min(w, h) / max(len(placed), 3))))
            painter.setFont(f)
            sp = to_screen(c3)
            painter.drawText(QPointF(sp.x() - 6, sp.y() + 6), pl.name)


# -- interactive orbit widget ------------------------------------------------

class Preview3DWidget(QWidget):
    """Orbit + fold view. Drag to rotate, wheel to zoom."""

    def __init__(self, panels, hinges, root=None, dark=False, parent=None,
                 thickness=0.0, numbers=False):
        super().__init__(parent)
        self.panels = panels
        self.hinges = hinges
        self.root = root
        self.dark = dark
        self.thickness = thickness
        self.numbers = numbers
        self.bad_holes = None
        self.fraction = 1.0
        self.yaw = 0.6
        self.pitch = 1.0
        self.zoom = 1.0
        self._last = None
        self.setMinimumSize(360, 300)
        self.setMouseTracking(True)

    def set_fraction(self, f: float) -> None:
        self.fraction = max(0.0, min(1.0, f))
        self.update()

    def set_model(self, panels, hinges, root=None, thickness=None) -> None:
        self.panels = panels
        self.hinges = hinges
        if root is not None:
            self.root = root
        if thickness is not None:
            self.thickness = thickness
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_assembly(p, self.width(), self.height(), self.panels, self.hinges,
                       root=self.root, fraction=self.fraction, yaw=self.yaw,
                       pitch=self.pitch, zoom=self.zoom, dark=self.dark,
                       thickness=self.thickness, numbers=self.numbers,
                       bad_holes=self.bad_holes)
        p.end()

    def mousePressEvent(self, e):
        self._last = e.position()

    def mouseMoveEvent(self, e):
        if self._last is None or not (e.buttons() & Qt.LeftButton):
            return
        d = e.position() - self._last
        self._last = e.position()
        self.yaw += d.x() * 0.01
        self.pitch += d.y() * 0.01
        self.pitch = max(-math.pi / 2, min(math.pi / 2, self.pitch))
        self.update()

    def mouseReleaseEvent(self, _e):
        self._last = None

    def wheelEvent(self, e):
        self.zoom *= 1.0 + (e.angleDelta().y() / 1200.0)
        self.zoom = max(0.2, min(6.0, self.zoom))
        self.update()


class Preview3DDialog(QDialog):
    """A framed window around the orbit view with a fold-amount slider."""

    def __init__(self, panels, hinges, root=None, dark=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("3D Assembly Preview")
        self.resize(560, 500)
        lay = QVBoxLayout(self)
        self.view = Preview3DWidget(panels, hinges, root, dark, self)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Fold"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(lambda v: self.view.set_fraction(v / 100.0))
        row.addWidget(self.slider, 1)
        lay.addLayout(row)
        hint = QLabel("Drag to orbit · wheel to zoom · slider folds flat → assembled")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)


# -- single-piece scored folding (the wallet case) ---------------------------

def _poly_area(pts) -> float:
    s = 0.0
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        s += a.x * b.y - b.x * a.y
    return abs(s) / 2.0


def _point_in_poly(p: Vec2, poly) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        a, b = poly[i], poly[j]
        if (a.y > p.y) != (b.y > p.y):
            xint = (b.x - a.x) * (p.y - a.y) / (b.y - a.y + 1e-30) + a.x
            if p.x < xint:
                inside = not inside
        j = i
    return inside


def build_scored_from_document(doc, piece=None):
    """Find the single piece to fold (the largest closed shape, or ``piece``)
    plus its fold lines, and return ``(outline, folds, fold_shapes)``:
    the world outline, the ``Fold`` list, and the fold-line shapes behind them
    (so the dialog can write edits back). Returns ``(None, [], [])`` if there is
    no piece with at least one fold line across it."""
    from leathercad.fold3d import Fold
    fold_shapes = [s for s in doc.shapes if getattr(s, "is_fold_line", False)]
    if not fold_shapes:
        return None, [], []
    candidates = [s for s in doc.shapes
                  if not getattr(s, "is_fold_line", False)
                  and not getattr(s, "construction", False)
                  and s.local_path().closed]
    if piece is None:
        if not candidates:
            return None, [], []
        piece = max(candidates, key=lambda s: _poly_area(
            [Vec2(p.x, p.y) for p in s.world_polyline()[0]]))
    outline = [Vec2(p.x, p.y) for p in piece.world_polyline()[0]]

    folds = []
    for fs in fold_shapes:
        pts = fs.world_polyline()[0]
        if len(pts) < 2:
            continue
        a, b = Vec2(pts[0].x, pts[0].y), Vec2(pts[-1].x, pts[-1].y)
        folds.append(Fold(a, b, fs.fold_angle, fs.fold_dir or "front"))
    return outline, folds, fold_shapes


def _piece_holes(piece, doc=None):
    """World stitch holes to show on the folded model: the piece's own stitching
    PLUS every drawn seam (StitchLine) in the document -- many patterns (e.g. the
    fold-over wallet) carry their stitching as separate seams, not on the body.
    ``holes_for_shape`` already returns world coords, so no extra transform."""
    from leathercad.stitching import holes_for_shape
    holes = []
    try:
        holes += [Vec2(h.point.x, h.point.y) for h in holes_for_shape(piece).holes]
    except Exception:
        pass
    for sl in getattr(doc, "stitch_lines", []) or []:
        try:
            holes += [Vec2(h.point.x, h.point.y) for h in sl.result().holes]
        except Exception:
            pass
    return holes


def scored_panels(outline, folds, holes=None):
    """``panels_from_scored_piece`` + distribute ``holes`` into their facets."""
    from leathercad.fold3d import panels_from_scored_piece
    panels, hinges, order = panels_from_scored_piece(outline, folds)
    if holes:
        for hp in holes:
            for pid in order:
                if _point_in_poly(hp, panels[pid].outline):
                    panels[pid].holes.append(hp)
                    break
    # root = the largest facet (the piece "stays put" on its biggest panel)
    root = max(order, key=lambda pid: _poly_area(panels[pid].outline)) \
        if order else None
    return panels, hinges, order, root


class ScoredFoldDialog(QDialog):
    """Fold a SINGLE scored piece: numbered panels, per-panel front/back + angle,
    a leather-thickness layer stack, and the bend-allowance the flat blank needs
    for the bend radius."""

    def __init__(self, doc, piece=None, dark=False, parent=None, canvas=None):
        from PySide6.QtWidgets import (QDoubleSpinBox, QGridLayout, QWidget)
        super().__init__(parent)
        self.setWindowTitle("Fold single piece (3D)")
        self.resize(720, 700)
        self.doc = doc
        self.canvas = canvas
        self.outline, self.folds, self.fold_shapes = \
            build_scored_from_document(doc, piece)
        self.piece = piece or self._auto_piece()
        self.holes = _piece_holes(self.piece, doc) if self.piece else []
        self._seq = list(range(len(self.fold_shapes)))     # fold order (indices)

        lay = QVBoxLayout(self)
        panels, hinges, order, root = scored_panels(self.outline,
                                                    self._ordered_folds(), self.holes)
        self.view = Preview3DWidget(panels, hinges, root, dark, self,
                                    thickness=0.0, numbers=True)
        self.view.fraction = 0.0                   # OPEN FLAT
        lay.addWidget(self.view, 1)

        # thickness / bend-radius / fold slider -- the slider starts FLAT so
        # nothing auto-folds; you drive it (and set each fold below)
        row = QHBoxLayout()
        row.addWidget(QLabel("Leather thickness"))
        self.thick = QDoubleSpinBox()
        self.thick.setRange(0.0, 12.0)
        self.thick.setSingleStep(0.5)
        self.thick.setValue(2.0)
        self.thick.setSuffix(" mm")
        self.thick.valueChanged.connect(self._rebuild)
        row.addWidget(self.thick)
        row.addWidget(QLabel("Bend radius"))
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0.0, 12.0)
        self.radius.setSingleStep(0.5)
        self.radius.setValue(0.0)                 # a scored crease folds ~sharp
        self.radius.setSuffix(" mm")
        self.radius.setToolTip("Base inside radius. 0 = a sharp scored crease; "
                               "wrapped layers add to it automatically.")
        self.radius.valueChanged.connect(self._rebuild)
        row.addWidget(self.radius)
        row.addSpacing(16)
        row.addWidget(QLabel("Fold"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(0)                   # OPEN FLAT -- no surprise fold
        self.slider.valueChanged.connect(lambda v: self.view.set_fraction(v / 100.0))
        row.addWidget(self.slider, 1)
        lay.addLayout(row)

        lay.addWidget(QLabel(
            "<b>Fold sequence</b> — order folds top→bottom (folded first = "
            "innermost). Set each fold's direction &amp; angle; the bend "
            "allowance is shown, never applied."))
        self._grid = QGridLayout()
        gw = QWidget()
        gw.setLayout(self._grid)
        lay.addWidget(gw)

        self.readout = QLabel()
        self.readout.setWordWrap(True)
        lay.addWidget(self.readout)
        hint = QLabel("Drag to orbit · wheel to zoom · slider folds flat → assembled")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        self._populate_rows()

    def _auto_piece(self):
        cands = [s for s in self.doc.shapes
                 if not getattr(s, "is_fold_line", False)
                 and not getattr(s, "construction", False)
                 and s.local_path().closed]
        return max(cands, key=lambda s: _poly_area(
            [Vec2(p.x, p.y) for p in s.world_polyline()[0]]), default=None)

    def _fold_name(self, i):
        return getattr(self.fold_shapes[i], "name", "") or f"Fold {i + 1}"

    def _ordered_folds(self):
        return [self.folds[i] for i in self._seq]

    def _populate_rows(self):
        """(Re)build the per-fold rows in the current sequence order."""
        from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QPushButton,
                                       QLabel as QL, QWidget, QHBoxLayout)
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        for c, h in enumerate(("#", "Fold", "Direction", "Angle",
                               "Bend allowance", "Order")):
            self._grid.addWidget(QL(f"<b>{h}</b>"), 0, c)
        self._combos, self._angles, self._ba_labels = {}, {}, {}
        for pos, fi in enumerate(self._seq):
            r = pos + 1
            fs = self.fold_shapes[fi]
            self._grid.addWidget(QL(str(pos + 1)), r, 0)
            self._grid.addWidget(QL(self._fold_name(fi)), r, 1)
            cb = QComboBox()
            cb.addItems(["front", "back"])
            cb.setCurrentText(fs.fold_dir or "front")
            cb.currentTextChanged.connect(self._on_change)
            self._grid.addWidget(cb, r, 2)
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 180.0)
            sp.setValue(fs.fold_angle)
            sp.setSuffix("°")
            sp.valueChanged.connect(self._on_change)
            self._grid.addWidget(sp, r, 3)
            bl = QL("—")
            self._grid.addWidget(bl, r, 4)
            cell = QWidget()
            hb = QHBoxLayout(cell)
            hb.setContentsMargins(0, 0, 0, 0)
            up = QPushButton("↑")
            up.setFixedWidth(30)
            up.clicked.connect(lambda _c, k=pos: self._move(k, -1))
            dn = QPushButton("↓")
            dn.setFixedWidth(30)
            dn.clicked.connect(lambda _c, k=pos: self._move(k, 1))
            hb.addWidget(up)
            hb.addWidget(dn)
            self._grid.addWidget(cell, r, 5)
            self._combos[fi] = cb
            self._angles[fi] = sp
            self._ba_labels[fi] = bl
        self._rebuild()

    def _move(self, pos, delta):
        j = pos + delta
        if 0 <= j < len(self._seq):
            self._seq[pos], self._seq[j] = self._seq[j], self._seq[pos]
            self._populate_rows()

    def _on_change(self, *_):
        for fi, cb in self._combos.items():
            self.fold_shapes[fi].fold_dir = cb.currentText()
        for fi, sp in self._angles.items():
            self.fold_shapes[fi].fold_angle = sp.value()
        self._rebuild()

    def _rebuild(self, *_):
        import math
        from leathercad.fold3d import (assemble, registration_report,
                                       fold_bend_allowance, sequence_bend_radii)
        for fold, fs in zip(self.folds, self.fold_shapes):
            fold.direction = fs.fold_dir or "front"
            fold.angle_deg = fs.fold_angle
        t, r = self.thick.value(), self.radius.value()
        of = self._ordered_folds()
        panels, hinges, order, root = scored_panels(self.outline, of, self.holes)
        placed = assemble(panels, hinges, root=root, fraction=1.0, thickness=t)
        reg_lines, bad = registration_report(placed, thickness=t, tol=1.0)
        self.view.bad_holes = bad
        self.view.set_model(panels, hinges, root=root, thickness=t)
        # per-fold inside radius derived from the layers each crease wraps IN THE
        # CURRENT SEQUENCE (reorder -> different wraps -> different material).
        radii = sequence_bend_radii(panels, hinges, of, root, t, r)
        add_w = add_h = 0.0
        for rad, fold, fi in zip(radii, of, self._seq):
            ba1 = fold_bend_allowance(fold, t, rad)
            d = fold.b - fold.a
            if abs(d.y) >= abs(d.x):
                add_w += ba1
            else:
                add_h += ba1
            if fi in self._ba_labels:
                lay = int(round((rad - r) / t)) if t > 1e-9 else 0
                txt = f"+{ba1:.1f} mm"
                if lay:
                    txt += f"  (wraps {lay})"
                self._ba_labels[fi].setText(txt)
        reg = "<br>".join(reg_lines)
        self.readout.setText(
            f"<b>{len(order)} panels · {len(self.fold_shapes)} folds.</b> "
            f"Grow the flat blank by <b>{add_w:.1f} mm</b> in width and "
            f"<b>{add_h:.1f} mm</b> in height (leather {t:g} mm) — do it by "
            f"hand. Outer folds need more (they wrap the layers inside).<br>"
            f"<b>Lineup check</b> (red = won't register):<br>{reg}")


# -- headless PNG (docs / marketing) -----------------------------------------

def render_png(path: str, panels, hinges, *, root=None, fraction=1.0, yaw=0.6,
               pitch=1.0, zoom=1.0, size=(900, 720), dark=False, thickness=0.0,
               numbers=False, bad_holes=None) -> None:
    """Render an assembled view straight to a PNG file (no window needed)."""
    w, h = size
    img = QImage(w, h, QImage.Format_ARGB32)
    p = QPainter(img)
    paint_assembly(p, w, h, panels, hinges, root=root, fraction=fraction,
                   yaw=yaw, pitch=pitch, zoom=zoom, dark=dark,
                   thickness=thickness, numbers=numbers, bad_holes=bad_holes)
    p.end()
    img.save(path)

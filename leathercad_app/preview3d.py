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


def _piece_holes(piece):
    """World stitch holes for the piece, or []. ``holes_for_shape`` already
    returns them in world space, so we do NOT re-apply the transform."""
    from leathercad.stitching import holes_for_shape
    try:
        res = holes_for_shape(piece)
        return [Vec2(h.point.x, h.point.y) for h in res.holes]
    except Exception:
        return []


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
        from PySide6.QtWidgets import (QDoubleSpinBox, QComboBox, QGridLayout,
                                       QScrollArea)
        super().__init__(parent)
        self.setWindowTitle("Fold single piece (3D)")
        self.resize(680, 640)
        self.doc = doc
        self.canvas = canvas
        self.outline, self.folds, self.fold_shapes = \
            build_scored_from_document(doc, piece)
        self.piece = piece or self._auto_piece()
        self.holes = _piece_holes(self.piece) if self.piece else []

        lay = QVBoxLayout(self)
        panels, hinges, order, root = scored_panels(self.outline, self.folds,
                                                    self.holes)
        self.view = Preview3DWidget(panels, hinges, root, dark, self,
                                    thickness=0.0, numbers=True)
        lay.addWidget(self.view, 1)

        # thickness + fold-amount
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
        self.radius.setToolTip("Inside radius of the fold. 0 = a sharp scored "
                               "crease; raise it for a rolled / padded edge.")
        self.radius.valueChanged.connect(self._rebuild)
        row.addWidget(self.radius)
        row.addSpacing(16)
        row.addWidget(QLabel("Fold"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(lambda v: self.view.set_fraction(v / 100.0))
        row.addWidget(self.slider, 1)
        lay.addLayout(row)

        # per-fold controls: which way each score folds, how far, and a manual
        # "grow the blank by this bend's allowance" button (never automatic)
        from PySide6.QtWidgets import QPushButton
        self._combos = []
        self._angles = []
        self._grow_btns = []
        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Score</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Direction</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Angle</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Bend allowance</b>"), 0, 3)
        for i, fs in enumerate(self.fold_shapes):
            grid.addWidget(QLabel(f"Fold {i + 1}"), i + 1, 0)
            cb = QComboBox()
            cb.addItems(["front", "back"])
            cb.setCurrentText(fs.fold_dir or "front")
            cb.currentTextChanged.connect(self._rebuild)
            grid.addWidget(cb, i + 1, 1)
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 180.0)
            sp.setValue(fs.fold_angle)
            sp.setSuffix("°")
            sp.valueChanged.connect(self._rebuild)
            grid.addWidget(sp, i + 1, 2)
            btn = QPushButton("Grow blank")
            btn.setToolTip("Add this bend's allowance to the flat blank "
                           "(manual — nothing resizes on its own)")
            btn.clicked.connect(lambda _c, k=i: self._grow_blank(k))
            grid.addWidget(btn, i + 1, 3)
            self._combos.append(cb)
            self._angles.append(sp)
            self._grow_btns.append(btn)
        lay.addLayout(grid)

        self.readout = QLabel()
        self.readout.setWordWrap(True)
        lay.addWidget(self.readout)
        hint = QLabel("Drag to orbit · wheel to zoom · slider folds flat → assembled")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)
        self._rebuild()

    def _auto_piece(self):
        cands = [s for s in self.doc.shapes
                 if not getattr(s, "is_fold_line", False)
                 and not getattr(s, "construction", False)
                 and s.local_path().closed]
        return max(cands, key=lambda s: _poly_area(
            [Vec2(p.x, p.y) for p in s.world_polyline()[0]]), default=None)

    def _rebuild(self, *_):
        from leathercad.fold3d import bend_allowance, assemble, registration_report
        # push control values back onto the fold lines + rebuild the fold list
        for fs, cb, sp in zip(self.fold_shapes, self._combos, self._angles):
            fs.fold_dir = cb.currentText()
            fs.fold_angle = sp.value()
        for fold, fs in zip(self.folds, self.fold_shapes):
            fold.direction = fs.fold_dir
            fold.angle_deg = fs.fold_angle
        t = self.thick.value()
        panels, hinges, order, root = scored_panels(self.outline, self.folds,
                                                    self.holes)
        # registration is judged on the FULLY folded piece (indices are the same
        # at any fold amount, so the red flags hold as the slider animates)
        placed = assemble(panels, hinges, root=root, fraction=1.0, thickness=t)
        reg_lines, bad = registration_report(placed, thickness=t, tol=1.0)
        self.view.bad_holes = bad
        self.view.set_model(panels, hinges, root=root, thickness=t)
        r = self.radius.value()
        ba = bend_allowance(self.folds, t, r)
        # each crease's inside radius is auto-derived from the layers it wraps
        from leathercad.fold3d import fold_bend_allowance, nested_bend_radii
        radii = nested_bend_radii(self.folds, t, r)
        self._radii = radii
        for fold, btn, rad in zip(self.folds, self._grow_btns, radii):
            btn.setText(f"Grow +{fold_bend_allowance(fold, t, rad):.1f} mm")
            layers = int(round((rad - r) / t)) if t > 1e-9 else 0
            btn.setToolTip(f"Inside radius {rad:.1f} mm "
                           f"(wraps {layers} inner layer(s)). "
                           "Adds this bend's allowance to the flat blank — manual.")
        reg = "<br>".join(reg_lines)
        self.readout.setText(
            f"<b>{len(order)} panels · {len(self.fold_shapes)} folds.</b> "
            f"Bend allowance (leather {t:g} mm, bend radius {r:g} mm): add "
            f"<b>{ba['width']:.1f} mm</b> to width, "
            f"<b>{ba['height']:.1f} mm</b> to height of the flat blank.<br>"
            f"<b>Lineup check</b> (red = won't register):<br>{reg}")

    def _reload_from_doc(self):
        """Re-read the piece + folds after the document changed (e.g. a grow)."""
        self.outline, self.folds, self.fold_shapes = \
            build_scored_from_document(self.doc, self.piece)
        self.holes = _piece_holes(self.piece) if self.piece else []
        self._rebuild()

    def _grow_blank(self, i: int):
        """MANUAL: add fold ``i``'s bend allowance to the flat blank. Grows the
        piece across that fold and slides everything on the far side out to make
        room for the bend radius; nothing resizes on its own."""
        from leathercad.fold3d import (fold_bend_allowance,
                                        grow_polygon_at_fold)
        from leathercad.shapes import Rectangle
        if i >= len(self.folds):
            return
        fold = self.folds[i]
        # use this crease's auto-derived inside radius (accounts for wrapped layers)
        rad = self._radii[i] if getattr(self, "_radii", None) else self.radius.value()
        ba = fold_bend_allowance(fold, self.thick.value(), rad)
        if ba <= 1e-6:
            return
        n = (fold.b - fold.a).perp().normalized()      # world fold normal
        a = fold.a
        piece = self.piece
        # grow the piece itself
        if isinstance(piece, Rectangle) and (abs(n.x) < 1e-6 or abs(n.y) < 1e-6):
            if abs(n.x) > abs(n.y):                     # vertical score -> width
                piece.width += ba
                piece.transform.x += (ba / 2.0) * (1 if n.x > 0 else -1)
            else:                                       # horizontal score -> height
                piece.height += ba
                piece.transform.y += (ba / 2.0) * (1 if n.y > 0 else -1)
        else:
            self._grow_generic_piece(piece, fold, ba)
        # slide every OTHER shape whose body is on the far side out by ba
        for s in self.doc.shapes:
            if s is piece:
                continue
            pts = s.world_polyline()[0]
            if not pts:
                continue
            cx = sum(p.x for p in pts) / len(pts)
            cy = sum(p.y for p in pts) / len(pts)
            if (Vec2(cx, cy) - a).dot(n) > 1e-6:
                s.transform.x += n.x * ba
                s.transform.y += n.y * ba
        if self.canvas is not None:
            self.canvas.rebuild()
            self.canvas.commitRequested.emit()         # one undo step
        self._reload_from_doc()

    @staticmethod
    def _grow_generic_piece(piece, fold, ba):
        """Grow a non-rectangular piece: translate its far-side local nodes."""
        from leathercad.fold3d import grow_polygon_at_fold
        pts_attr = "points" if hasattr(piece, "points") else (
            "nodes" if hasattr(piece, "nodes") else None)
        if pts_attr is None:
            return
        t = piece.transform
        world = [t.apply(p) for p in getattr(piece, pts_attr)]
        grown = grow_polygon_at_fold(world, fold, ba)
        setattr(piece, pts_attr, [t.inverse_apply(p) for p in grown])


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

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


def _model_normal(verts: List[Vec3]) -> Vec3:
    """Area-weighted face normal (Newell's method) in model space, so a panel
    can be extruded into a slab along its own orientation at any fold angle."""
    nx = ny = nz = 0.0
    n = len(verts)
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]
        nx += (a.y - b.y) * (a.z + b.z)
        ny += (a.z - b.z) * (a.x + b.x)
        nz += (a.x - b.x) * (a.y + b.y)
    v = Vec3(nx, ny, nz)
    return v.normalized() if v.length() > 1e-9 else Vec3(0.0, 0.0, 1.0)


def paint_assembly(painter: QPainter, w: int, h: int, panels, hinges,
                   *, root=None, fraction=1.0, yaw=0.6, pitch=1.0,
                   zoom=1.0, dark=False, thickness=0.0, numbers=False,
                   bad_holes=None, levels=None) -> None:
    """Render the folded assembly into a ``w x h`` area with a painter."""
    bg = QColor(28, 30, 34) if dark else QColor(244, 244, 246)
    painter.fillRect(0, 0, w, h, bg)
    painter.setRenderHint(QPainter.Antialiasing, True)

    placed = assemble(panels, hinges, root=root, fraction=fraction,
                      thickness=thickness, levels=levels)
    if not placed:
        return

    # view-space drawables, depth-sorted together: panel faces, slab side walls
    # (so stacked layers show their edges), and a rounded BEND at each fold --
    # a half-pipe of leather that curves from one layer up to the next at the
    # real bend radius, so a fold reads as a fold, not a hard vertical wall.
    draw = []                                     # (depth, kind, payload)
    allx, ally = [], []
    for pl in placed:
        ov = [rotate_view(v, yaw, pitch) for v in pl.outline]
        hv = [rotate_view(v, yaw, pitch) for v in pl.holes]
        depth = sum(v.z for v in ov) / len(ov) if ov else 0.0
        draw.append((depth, "panel", (pl, ov, hv)))
        allx += [v.x for v in ov]
        ally += [v.y for v in ov]
        # give the leather real thickness: extrude the panel into a slab along
        # its own normal so the exposed side edges of each stacked layer read as
        # distinct bands (you can see where one layer ends and the next begins),
        # instead of the stack looking like one solid block.
        if thickness > 1e-6 and len(pl.outline) >= 3:
            nrm = _model_normal(pl.outline)
            if nrm.z < 0:                          # orient the slab downward
                nrm = nrm * -1.0
            bottom = [v - nrm * thickness for v in pl.outline]
            top = pl.outline
            m = len(top)
            for i in range(m):
                a0, b0 = top[i], top[(i + 1) % m]
                a1, b1 = bottom[i], bottom[(i + 1) % m]
                wv = [rotate_view(v, yaw, pitch) for v in (a0, b0, b1, a1)]
                draw.append((sum(v.z for v in wv) / 4.0, "wall", wv))
                allx += [v.x for v in wv]
                ally += [v.y for v in wv]
    if levels is not None and thickness > 1e-6:
        t = thickness
        for hg in hinges:
            a, b = hg.parent_edge
            lp = levels.get(hg.parent, 0)
            lc = levels.get(hg.child, 0)
            if lp == lc:
                continue
            # the leather is a SLAB with thickness, so a 180 degree fold is a
            # thick curved wrap -- an outer arc (bottom of the lower layer up
            # and over to the top of the upper layer) and an inner arc, a full
            # thickness apart. Both surfaces show a radius, and the leather's
            # cross-section reads as a "C" at each end of the crease.
            ll, lu = (lp, lc) if lc > lp else (lc, lp)
            z_lt = ll * t * fraction            # lower layer: top / bottom face
            z_lb = z_lt - t
            z_ut = lu * t * fraction            # upper layer: top / bottom face
            z_ub = z_ut - t
            center = (z_lt + z_ub) / 2.0        # shared arc centre
            r_in = abs(z_ub - z_lt) / 2.0       # inside bend radius (0 = sharp)
            r_out = abs(z_ut - z_lb) / 2.0      # outside = inside + thickness
            # outboard bulge direction: unit perpendicular to the fold line,
            # pointing the way the moving panel extended before it folded back
            ex, ey = b.x - a.x, b.y - a.y
            el = math.hypot(ex, ey) or 1.0
            px, py = -ey / el, ex / el
            child = panels.get(hg.child)
            if child and child.outline:
                mx, my = (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
                ccx = sum(p.x for p in child.outline) / len(child.outline)
                ccy = sum(p.y for p in child.outline) / len(child.outline)
                if (ccx - mx) * px + (ccy - my) * py < 0.0:
                    px, py = -px, -py
            steps = 14

            def arc_pt(edge_pt, radius, theta):
                off = radius * math.sin(theta)
                return Vec3(edge_pt.x + px * off, edge_pt.y + py * off,
                            center - radius * math.cos(theta))

            # the two curved surfaces (outer visible, inner tucked underneath)
            for radius in (r_out, r_in):
                if radius < 1e-6:
                    continue
                for k in range(1, steps + 1):
                    t0 = math.pi * (k - 1) / steps
                    t1 = math.pi * k / steps
                    quad = (arc_pt(a, radius, t0), arc_pt(b, radius, t0),
                            arc_pt(b, radius, t1), arc_pt(a, radius, t1))
                    rc = [rotate_view(v, yaw, pitch) for v in quad]
                    draw.append((sum(v.z for v in rc) / 4.0, "bend", rc))
                    allx += [v.x for v in rc]
                    ally += [v.y for v in rc]
            # the leather's cross-section (the "C") at each end of the crease
            for edge_pt in (a, b):
                ring = [arc_pt(edge_pt, r_out, math.pi * k / steps)
                        for k in range(steps + 1)]
                ring += [arc_pt(edge_pt, r_in, math.pi * k / steps)
                         for k in range(steps, -1, -1)]
                rc = [rotate_view(v, yaw, pitch) for v in ring]
                draw.append((sum(v.z for v in rc) / len(rc), "bendcap", rc))
                allx += [v.x for v in rc]
                ally += [v.y for v in rc]
    if not allx:
        return
    draw.sort(key=lambda t: t[0])                 # painter's algorithm, far first

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
    edge = QColor(60, 40, 20)                     # dark line between layers

    def leather(nview, lo, hi):
        # TWO-SIDED lighting: a folded-over panel shows its back, but leather is
        # the same colour both sides -- shading by |n.L| keeps every layer the
        # same warm tone so front/back facing doesn't read as extra layers.
        s = lo + (hi - lo) * abs(nview.dot(_LIGHT))
        return QColor(int(base.red() * s), int(base.green() * s),
                      int(base.blue() * s))

    for _d, kind, payload in draw:
        if kind == "bend":
            # the rounded fold surface -- same leather, gently shaded, no hard
            # outline so the curve reads as continuous material
            painter.setBrush(QBrush(leather(_panel_normal_view(payload),
                                            0.5, 0.92)))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(QPolygonF([to_screen(v) for v in payload]))
            continue
        if kind == "wall":
            # side of the leather slab: a touch darker, outlined so each layer
            # boundary draws a crisp edge line
            painter.setBrush(QBrush(leather(_panel_normal_view(payload),
                                            0.42, 0.6)))
            painter.setPen(QPen(edge, 1.0))
            painter.drawPolygon(QPolygonF([to_screen(v) for v in payload]))
            continue
        if kind == "bendcap":
            # the leather's cross-section at the end of a crease -- the "C" that
            # shows the fold has real thickness (a radius top AND bottom)
            painter.setBrush(QBrush(leather(_panel_normal_view(payload),
                                            0.42, 0.6)))
            painter.setPen(QPen(edge, 1.0))
            painter.drawPolygon(QPolygonF([to_screen(v) for v in payload]))
            continue
        pl, ov, hv = payload
        poly = QPolygonF([to_screen(v) for v in ov])
        painter.setBrush(QBrush(leather(_panel_normal_view(ov), 0.6, 1.0)))
        painter.setPen(QPen(edge, 1.2))
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
        self.levels = None
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

    def set_model(self, panels, hinges, root=None, thickness=None,
                  levels=None) -> None:
        self.panels = panels
        self.hinges = hinges
        if root is not None:
            self.root = root
        if thickness is not None:
            self.thickness = thickness
        self.levels = levels
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_assembly(p, self.width(), self.height(), self.panels, self.hinges,
                       root=self.root, fraction=self.fraction, yaw=self.yaw,
                       pitch=self.pitch, zoom=self.zoom, dark=self.dark,
                       thickness=self.thickness, numbers=self.numbers,
                       bad_holes=self.bad_holes, levels=self.levels)
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
        self._enabled = {i: True for i in range(len(self.fold_shapes))}
        # which panel each fold MOVES, and which panel is the fixed base
        self._mover, self._base_name = self._compute_movers()

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
            f"<b>Panels to fold</b> (base = <b>Panel {self._base_name}</b>, "
            "stays flat; everything wraps around it). Tick a panel to fold it, "
            "order top→bottom = folded first (innermost). Bend allowance is "
            "shown, never applied."))
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

    def _compute_movers(self):
        """Map each fold to the PANEL it moves, and find the fixed base panel."""
        from leathercad.fold3d import fold_movers
        panels, hinges, order, root = scored_panels(self.outline, self.folds, [])
        movers = fold_movers(panels, hinges, self.folds, root)
        name = {pid: panels[pid].name for pid in panels}
        mover_name = {i: name.get(m, "?") for i, m in enumerate(movers)}
        return mover_name, name.get(root, "1")

    def _ordered_folds(self):
        """Folds in sequence; a disabled panel's fold is flattened to 0° so the
        panel stays attached but unfolded."""
        from leathercad.fold3d import Fold
        out = []
        for i in self._seq:
            f = self.folds[i]
            if not self._enabled.get(i, True):
                f = Fold(f.a, f.b, 0.0, f.direction)
            out.append(f)
        return out

    def _populate_rows(self):
        """(Re)build the per-panel rows in the current fold order."""
        from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QPushButton,
                                       QCheckBox, QLabel as QL, QWidget,
                                       QHBoxLayout)
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        for c, h in enumerate(("Fold?", "#", "Panel", "Direction", "Angle",
                               "Bend allowance", "Order")):
            self._grid.addWidget(QL(f"<b>{h}</b>"), 0, c)
        self._checks, self._combos, self._angles, self._ba_labels = {}, {}, {}, {}
        for pos, fi in enumerate(self._seq):
            r = pos + 1
            fs = self.fold_shapes[fi]
            ck = QCheckBox()
            ck.setChecked(self._enabled.get(fi, True))
            ck.toggled.connect(self._on_change)
            self._grid.addWidget(ck, r, 0)
            self._grid.addWidget(QL(str(pos + 1)), r, 1)
            self._grid.addWidget(QL(f"Panel {self._mover.get(fi, '?')}"), r, 2)
            cb = QComboBox()
            cb.addItems(["front", "back"])
            cb.setCurrentText(fs.fold_dir or "front")
            cb.currentTextChanged.connect(self._on_change)
            self._grid.addWidget(cb, r, 3)
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 180.0)
            sp.setValue(fs.fold_angle)
            sp.setSuffix("°")
            sp.valueChanged.connect(self._on_change)
            self._grid.addWidget(sp, r, 4)
            bl = QL("—")
            self._grid.addWidget(bl, r, 5)
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
            self._grid.addWidget(cell, r, 6)
            self._checks[fi] = ck
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
        for fi, ck in self._checks.items():
            self._enabled[fi] = ck.isChecked()
        for fi, cb in self._combos.items():
            self.fold_shapes[fi].fold_dir = cb.currentText()
        for fi, sp in self._angles.items():
            self.fold_shapes[fi].fold_angle = sp.value()
        self._rebuild()

    def _rebuild(self, *_):
        from leathercad.fold3d import (registration_report, fold_bend_allowance,
                                       sequence_bend_radii, fold_stack_levels)
        for fold, fs in zip(self.folds, self.fold_shapes):
            fold.direction = fs.fold_dir or "front"
            fold.angle_deg = fs.fold_angle
        t, r = self.thick.value(), self.radius.value()
        of = self._ordered_folds()                    # disabled -> 0° (stays flat)
        panels, hinges, order, root = scored_panels(self.outline, of, self.holes)
        # only the ENABLED, folding creases build the layer stack
        folding = [f for f, i in zip(of, self._seq)
                   if self._enabled.get(i, True) and f.angle_deg > 1e-6]
        levels = fold_stack_levels(panels, hinges, folding, root)
        # separate the layers by the ACTUAL leather thickness (a compact stack,
        # not an exploded one) so the top piece reads without floating away
        view_gap = t if t > 1e-6 else 1.0
        from leathercad.fold3d import assemble
        placed = assemble(panels, hinges, root=root, fraction=1.0, thickness=t,
                          levels=levels)
        reg_lines, bad = registration_report(placed, thickness=t, tol=1.0)
        self.view.bad_holes = bad
        self.view.set_model(panels, hinges, root=root, thickness=view_gap,
                            levels=levels)
        # per-fold inside radius from the layers each crease wraps in the CURRENT
        # sequence of ENABLED folds (reorder / toggle -> different wraps).
        radii = sequence_bend_radii(panels, hinges, folding, root, t, r)
        rad_of = {id(f): rad for f, rad in zip(folding, radii)}
        add_w = add_h = 0.0
        for fold, fi in zip(of, self._seq):
            if fi not in self._ba_labels:
                continue
            if not (self._enabled.get(fi, True) and fold.angle_deg > 1e-6):
                self._ba_labels[fi].setText("(not folded)")
                continue
            rad = rad_of.get(id(fold), r)
            ba1 = fold_bend_allowance(fold, t, rad)
            d = fold.b - fold.a
            if abs(d.y) >= abs(d.x):
                add_w += ba1
            else:
                add_h += ba1
            lay = int(round((rad - r) / t)) if t > 1e-9 else 0
            txt = f"+{ba1:.1f} mm" + (f"  (wraps {lay})" if lay else "")
            self._ba_labels[fi].setText(txt)
        reg = "<br>".join(reg_lines)
        self.readout.setText(
            f"<b>{len(order)} panels, base Panel {self._base_name}.</b> "
            f"Grow the flat blank by <b>{add_w:.1f} mm</b> in width and "
            f"<b>{add_h:.1f} mm</b> in height (leather {t:g} mm) — do it by "
            f"hand. Outer folds need more (they wrap the layers inside).<br>"
            f"<b>Lineup check</b> (red = won't register):<br>{reg}")


# -- headless PNG (docs / marketing) -----------------------------------------

def render_png(path: str, panels, hinges, *, root=None, fraction=1.0, yaw=0.6,
               pitch=1.0, zoom=1.0, size=(900, 720), dark=False, thickness=0.0,
               numbers=False, bad_holes=None, levels=None) -> None:
    """Render an assembled view straight to a PNG file (no window needed)."""
    w, h = size
    img = QImage(w, h, QImage.Format_ARGB32)
    p = QPainter(img)
    paint_assembly(p, w, h, panels, hinges, root=root, fraction=fraction,
                   levels=levels,
                   yaw=yaw, pitch=pitch, zoom=zoom, dark=dark,
                   thickness=thickness, numbers=numbers, bad_holes=bad_holes)
    p.end()
    img.save(path)

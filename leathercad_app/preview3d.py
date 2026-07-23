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
                   zoom=1.0, dark=False) -> None:
    """Render the folded assembly into a ``w x h`` area with a painter."""
    bg = QColor(28, 30, 34) if dark else QColor(244, 244, 246)
    painter.fillRect(0, 0, w, h, bg)
    painter.setRenderHint(QPainter.Antialiasing, True)

    placed = assemble(panels, hinges, root=root, fraction=fraction)
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
        # stitch holes as small dots on the face
        if hv:
            painter.setBrush(QBrush(QColor(35, 24, 12)))
            painter.setPen(Qt.NoPen)
            r = max(1.0, 0.7 * scale)
            for v in hv:
                painter.drawEllipse(to_screen(v), r, r)


# -- interactive orbit widget ------------------------------------------------

class Preview3DWidget(QWidget):
    """Orbit + fold view. Drag to rotate, wheel to zoom."""

    def __init__(self, panels, hinges, root=None, dark=False, parent=None):
        super().__init__(parent)
        self.panels = panels
        self.hinges = hinges
        self.root = root
        self.dark = dark
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

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_assembly(p, self.width(), self.height(), self.panels, self.hinges,
                       root=self.root, fraction=self.fraction, yaw=self.yaw,
                       pitch=self.pitch, zoom=self.zoom, dark=self.dark)
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


# -- headless PNG (docs / marketing) -----------------------------------------

def render_png(path: str, panels, hinges, *, root=None, fraction=1.0,
               yaw=0.6, pitch=1.0, zoom=1.0, size=(900, 720), dark=False) -> None:
    """Render an assembled view straight to a PNG file (no window needed)."""
    w, h = size
    img = QImage(w, h, QImage.Format_ARGB32)
    p = QPainter(img)
    paint_assembly(p, w, h, panels, hinges, root=root, fraction=fraction,
                   yaw=yaw, pitch=pitch, zoom=zoom, dark=dark)
    p.end()
    img.save(path)

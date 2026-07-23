"""Print / export a pattern to PDF at true 1:1 scale, tiled across pages.

Leatherworkers routinely print a pattern to cut or check by hand. The one thing
that must be exact is *scale*: 1 mm in the document must be 1 mm on paper. When
the pattern is bigger than a sheet it is split into overlapping tiles with crop
marks and page labels so the pages can be lined up on the overlap and taped.

Rendering is shared between a PDF file (``export_pdf_tiled``) and the OS print
dialog (``render_tiled`` onto any ``QPagedPaintDevice``).
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF, QPointF, QSizeF, QMarginsF
from PySide6.QtGui import (QPainter, QPen, QColor, QPainterPath, QFont,
                           QPdfWriter, QPageSize, QPageLayout)

from leathercad.export import collect


def _bbox(outlines, stitches):
    pts = []
    for pl, _ in outlines:
        pts.extend(pl)
    for res, _, _ in stitches:
        pts.extend(res.points)
    if not pts:
        return 0.0, 0.0, 1.0, 1.0
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _n_tiles(extent: float, content: float, step: float, overlap: float) -> int:
    if extent <= content:
        return 1
    return max(1, math.ceil((extent - overlap) / step))


def render_tiled(device, doc, *, page_w=210.0, page_h=297.0,
                 margin=10.0, overlap=8.0):
    """Paint ``doc`` onto ``device`` (a QPdfWriter / QPrinter) at 1:1 mm scale,
    tiled. Returns (rows, cols). ``device`` must already have its page size set."""
    outlines, stitches = collect(doc)
    minx, miny, maxx, maxy = _bbox(outlines, stitches)
    patt_w, patt_h = maxx - minx, maxy - miny

    content_w = page_w - 2 * margin
    content_h = page_h - 2 * margin
    step_w = max(1.0, content_w - overlap)
    step_h = max(1.0, content_h - overlap)
    cols = _n_tiles(patt_w, content_w, step_w, overlap)
    rows = _n_tiles(patt_h, content_h, step_h, overlap)

    D = device.resolution() / 25.4        # device dots per mm (painter not scaled)
    painter = QPainter(device)
    painter.setRenderHint(QPainter.Antialiasing, True)

    outline_pen = QPen()
    outline_pen.setWidthF(0.20 * D)
    stitch_pen = QPen()
    stitch_pen.setWidthF(0.20 * D)
    mark_pen = QPen(QColor(150, 150, 150))
    mark_pen.setWidthF(0.15 * D)
    label_font = QFont()
    label_font.setPixelSize(max(1, int(3.2 * D)))

    first = True
    for r in range(rows):
        for c in range(cols):
            if not first:
                device.newPage()
            first = False
            tile_x = minx + c * step_w        # pattern x at the content's left
            top_y = maxy - r * step_h         # pattern y at the content's top

            def M(p):
                # pattern (Y-up) -> page mm (Y-down), inset by the margin, * dots
                return QPointF((margin + (p.x - tile_x)) * D,
                               (margin + (top_y - p.y)) * D)

            painter.setClipRect(QRectF(margin * D, margin * D,
                                       content_w * D, content_h * D))
            painter.setBrush(Qt.NoBrush)
            for pl, color in outlines:
                if len(pl) < 2:
                    continue
                outline_pen.setColor(QColor(color))
                painter.setPen(outline_pen)
                path = QPainterPath(M(pl[0]))
                for p in pl[1:]:
                    path.lineTo(M(p))
                painter.drawPath(path)
            for res, settings, color in stitches:
                stitch_pen.setColor(QColor(color))
                painter.setPen(stitch_pen)
                if settings.hole_style == "slit":
                    half = settings.slit_length / 2.0
                    slant = math.radians(settings.slit_angle)
                    for h in res.holes:
                        d = h.tangent.rotate(slant)
                        painter.drawLine(M(h.point - d * half),
                                         M(h.point + d * half))
                else:
                    rr = settings.hole_diameter / 2.0 * D
                    for h in res.holes:
                        painter.drawEllipse(M(h.point), rr, rr)
            painter.setClipping(False)

            _crop_marks(painter, D, margin, content_w, content_h, mark_pen)
            painter.setPen(QColor(90, 90, 90))
            painter.setFont(label_font)
            painter.drawText(
                QPointF(margin * D, (margin - 3.0) * D),
                f"row {r + 1}/{rows}   col {c + 1}/{cols}   —   1:1 scale "
                f"(verify: this box is {int(content_w)}×{int(content_h)} mm)")

    painter.end()
    return rows, cols


def _crop_marks(painter, D, margin, content_w, content_h, pen):
    """Corner crop ticks + a light content border, so tiles can be trimmed and
    aligned on their overlap."""
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x0, y0 = margin * D, margin * D
    x1, y1 = (margin + content_w) * D, (margin + content_h) * D
    painter.drawRect(QRectF(x0, y0, content_w * D, content_h * D))
    t = 4.0 * D                                   # 4 mm tick
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                             (x0, y1, 1, -1), (x1, y1, -1, -1)):
        painter.drawLine(QPointF(cx, cy), QPointF(cx + sx * t, cy))
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy + sy * t))


def export_pdf_tiled(doc, path: str, *, page_w=210.0, page_h=297.0,
                     margin=10.0, overlap=8.0, dpi=300):
    """Write ``doc`` to a 1:1, tiled PDF at ``path``. Returns (rows, cols)."""
    writer = QPdfWriter(path)
    writer.setResolution(dpi)
    writer.setPageSize(QPageSize(QSizeF(page_w, page_h), QPageSize.Unit.Millimeter))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    writer.setTitle("Stitch Hero pattern (1:1)")
    return render_tiled(writer, doc, page_w=page_w, page_h=page_h,
                        margin=margin, overlap=overlap)

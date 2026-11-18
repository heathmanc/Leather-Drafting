"""Does anything in a model go through anything else?

The view paints whatever cells it is given, and the partition tree puts
them in order whether or not they share space: leather drawn through
leather is cut up and painted, and nothing says so. This module is the
judge that does. ``depth`` measures how far two convex cells go into each
other, ``clashes`` tries every pair of a model, and ``convex`` checks that
a cell is the convex solid the other two take it for. Pure Python, no Qt.
"""

from __future__ import annotations

import math
from typing import Iterator, List, Tuple

P3 = Tuple[float, float, float]

# What two cells may share (mm) and still pass: rounding, and no more.
# ``sheets.LAP`` -- the overlap at which one piece is taken to lie on
# another, and so is lifted clear of it -- is smaller, so pieces that are
# left on one level always pass.
JUDGE = 1e-4


def _key(x: float, y: float, z: float) -> P3:
    """A direction and its opposite, rounded, as one thing."""
    x, y, z = round(x, 9), round(y, 9), round(z, 9)
    if x < 0.0 or (x == 0.0 and (y < 0.0 or (y == 0.0 and z < 0.0))):
        return (-x, -y, -z)
    return (x, y, z)


def _normals(cell) -> List[P3]:
    """The ways the faces of a cell are turned, each once."""
    out, seen = [], set()
    for f in cell.faces:
        n = f.plane[:3]
        k = _key(*n)
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _edges(cell) -> List[P3]:
    """The directions the edges of a cell run in, each once."""
    out, seen = [], set()
    for f in cell.faces:
        x0, y0, z0 = f.pts[-1]
        for x1, y1, z1 in f.pts:
            dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
            ln = math.sqrt(dx * dx + dy * dy + dz * dz)
            if ln > 1e-9:
                d = (dx / ln, dy / ln, dz / ln)
                k = _key(*d)
                if k not in seen:
                    seen.add(k)
                    out.append(d)
            x0, y0, z0 = x1, y1, z1
    return out


def _axes(a, b) -> Iterator[P3]:
    """The directions to look along for a plane between two convex cells:
    the normals of the faces of each, then the cross product of every
    edge direction of one with every edge direction of the other."""
    yield from _normals(a)
    yield from _normals(b)
    others = _edges(b)
    for ux, uy, uz in _edges(a):
        for vx, vy, vz in others:
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            if ln > 1e-6:                      # not two edges that run alike
                yield (nx / ln, ny / ln, nz / ln)


def depth(a, b) -> float:
    """How deep two convex cells go into each other (mm): the least that
    either would have to be moved for them to come clear. 0 for two that a
    plane parts, whether they touch or not.

    This is exact for convex cells. If any plane parts two of them, one
    does that is square to a face of either or to an edge of each; so
    those are the directions tried, and the depth is the least overlap
    found along any of them."""
    if (a.cx - b.cx) ** 2 + (a.cy - b.cy) ** 2 + (a.cz - b.cz) ** 2 \
            >= (a.rad + b.rad) ** 2:
        return 0.0
    va, vb = a.verts, b.verts
    best = math.inf
    tried = set()
    for nx, ny, nz in _axes(a, b):
        k = _key(nx, ny, nz)
        if k in tried:
            continue
        tried.add(k)
        pa = [nx * x + ny * y + nz * z for x, y, z in va]
        pb = [nx * x + ny * y + nz * z for x, y, z in vb]
        # how far one would have to go, this way or the other, to be clear
        out = min(max(pa) - min(pb), max(pb) - min(pa))
        if out <= 0.0:
            return 0.0
        if out < best:
            best = out
    return best if best < math.inf else 0.0


def clashes(cells, eps: float = JUDGE) -> List[Tuple[float, tuple, tuple]]:
    """Every two cells of a model that go into each other by more than
    ``eps`` mm: ``[(depth, tag, tag)]``, the deepest first.

    Cells of one piece are tried against each other like any others: the
    tags say whose each is. The boxes round two cells are tried before the
    cells themselves, so a model with nothing wrong in it costs little."""
    boxed = []
    for c in cells:
        xs = [v[0] for v in c.verts]
        ys = [v[1] for v in c.verts]
        zs = [v[2] for v in c.verts]
        if xs:
            boxed.append((min(xs), max(xs), min(ys), max(ys), min(zs),
                          max(zs), c))
    boxed.sort(key=lambda box: box[0])         # along x, to try few pairs
    out = []
    for i, (_ax0, ax1, ay0, ay1, az0, az1, a) in enumerate(boxed):
        for bx0, _bx1, by0, by1, bz0, bz1, b in boxed[i + 1:]:
            if bx0 >= ax1 - eps:
                break                          # and so does every later one
            if (by0 >= ay1 - eps or ay0 >= by1 - eps
                    or bz0 >= az1 - eps or az0 >= bz1 - eps):
                continue
            d = depth(a, b)
            if d > eps:
                out.append((d, a.tag, b.tag))
    out.sort(key=lambda found: -found[0])
    return out


def convex(cell) -> float:
    """How far any corner of a cell stands in front of any of its own
    faces (mm): 0 for a convex cell, which lies wholly behind each of
    them."""
    worst = 0.0
    for f in cell.faces:
        nx, ny, nz, d = f.plane
        for x, y, z in cell.verts:
            q = nx * x + ny * y + nz * z - d
            if q > worst:
                worst = q
    return worst

"""Back-to-front order for drawing a solid model with a painter.

The model is a set of convex cells -- the slabs and wedges a folded piece of
leather is made of -- each a list of flat faces. A binary space partition
puts the cells in an order that is right from any side: layers lying on one
another, a flap standing over a panel, even leather run through leather (a
cell that straddles a dividing plane is split by it).

Within one convex cell the faces turned towards the eye never hide each
other, so only the cells need ordering, and the planes that part them are
their own faces. Pure Python, no Qt: the view paints what this orders.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

P3 = Tuple[float, float, float]
Plane = Tuple[float, float, float, float]      # unit normal, and n . p on it

# kinds of face
FRONT, BACK, WALL, INNER = 0, 1, 2, 3
# kinds of decal: marks drawn on a face, split along with it
DOTS, LINE, POLY = 0, 1, 2

EPS = 1e-6


class Face:
    """One flat face of a cell: its corners (counter-clockwise seen from
    outside), its plane (normal pointing out of the cell), and what the
    view needs to paint it. ``INNER`` faces are where two cells meet inside
    the leather: never painted, but they bound the cell.

    ``edges`` marks the edges to draw a line along (one per corner, for the
    edge leaving it); ``decals`` are ``(kind, geometry, style)`` marks on
    the face -- ``DOTS`` with a list of points, ``LINE`` with two, ``POLY``
    with a ring of them."""

    __slots__ = ("pts", "plane", "kind", "edges", "decals", "tag", "size")

    def __init__(self, pts, plane, kind, edges=None, decals=None, tag=None,
                 size=0.0):
        self.pts = pts
        self.plane = plane
        self.kind = kind
        self.edges = edges
        self.decals = decals
        self.tag = tag
        self.size = size


class Cell:
    """A convex solid: its faces, its corners, a sphere round it, and the
    planes it offers for parting cells (its biggest faces first)."""

    __slots__ = ("faces", "verts", "cx", "cy", "cz", "rad", "planes", "tag",
                 "big")

    def __init__(self, faces: List[Face], tag=None, planes=None, big=None):
        self.faces = faces
        self.tag = tag
        self.big = max((f.size for f in faces), default=0.0) \
            if big is None else big
        seen = set()
        for f in faces:
            seen.update(f.pts)
        verts = self.verts = list(seen)
        n = len(verts) or 1
        cx = sum(v[0] for v in verts) / n
        cy = sum(v[1] for v in verts) / n
        cz = sum(v[2] for v in verts) / n
        self.cx, self.cy, self.cz = cx, cy, cz
        r2 = 0.0
        for x, y, z in verts:
            q = (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2
            if q > r2:
                r2 = q
        self.rad = r2 ** 0.5
        if planes is None:
            planes = [f.plane for f in sorted(faces, key=lambda f: -f.size)]
        self.planes = planes


def plane_of(pts: Sequence[P3]) -> Optional[Plane]:
    """The plane of a flat polygon (Newell's method), its normal on the
    side the corners run counter-clockwise from. None for one with no
    area."""
    nx = ny = nz = 0.0
    x0, y0, z0 = pts[-1]
    for x1, y1, z1 in pts:
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
        x0, y0, z0 = x1, y1, z1
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    if ln < 1e-12:
        return None
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    n = len(pts)
    d = sum(nx * p[0] + ny * p[1] + nz * p[2] for p in pts) / n
    return (nx, ny, nz, d)


# -- splitting ----------------------------------------------------------------

def _split_decals(decals, plane, eps):
    nx, ny, nz, d = plane
    front, back = [], []
    for kind, geo, style in decals:
        if kind == DOTS:
            f = [p for p in geo if nx * p[0] + ny * p[1] + nz * p[2] - d >= 0.0]
            if len(f) == len(geo):
                front.append((kind, geo, style))
            elif not f:
                back.append((kind, geo, style))
            else:
                front.append((kind, f, style))
                back.append((kind, [p for p in geo if nx * p[0] + ny * p[1]
                                    + nz * p[2] - d < 0.0], style))
        elif kind == LINE:
            p, q = geo
            dp = nx * p[0] + ny * p[1] + nz * p[2] - d
            dq = nx * q[0] + ny * q[1] + nz * q[2] - d
            if dp >= -eps and dq >= -eps:
                front.append((kind, geo, style))
            elif dp <= eps and dq <= eps:
                back.append((kind, geo, style))
            else:
                t = dp / (dp - dq)
                m = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t,
                     p[2] + (q[2] - p[2]) * t)
                if dp > 0.0:
                    front.append((kind, (p, m), style))
                    back.append((kind, (m, q), style))
                else:
                    back.append((kind, (p, m), style))
                    front.append((kind, (m, q), style))
        else:
            dist = [nx * p[0] + ny * p[1] + nz * p[2] - d for p in geo]
            if min(dist) >= -eps:
                front.append((kind, geo, style))
            elif max(dist) <= eps:
                back.append((kind, geo, style))
            else:
                fp, bp = [], []
                n = len(geo)
                for i in range(n):
                    a, b = geo[i], geo[(i + 1) % n]
                    da, db = dist[i], dist[(i + 1) % n]
                    if da >= 0.0:
                        fp.append(a)
                    if da <= 0.0:
                        bp.append(a)
                    if (da > 0.0 and db < 0.0) or (da < 0.0 and db > 0.0):
                        t = da / (da - db)
                        m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                             a[2] + (b[2] - a[2]) * t)
                        fp.append(m)
                        bp.append(m)
                if len(fp) >= 3:
                    front.append((kind, fp, style))
                if len(bp) >= 3:
                    back.append((kind, bp, style))
    return front or None, back or None


def split_face(face: Face, plane: Plane, eps: float = EPS
               ) -> Tuple[Optional[Face], Optional[Face]]:
    """The parts of a face in front of and behind a plane (either may be
    None). The new edge along the plane is never one to draw a line on."""
    nx, ny, nz, d = plane
    pts = face.pts
    dist = [nx * x + ny * y + nz * z - d for x, y, z in pts]
    lo, hi = min(dist), max(dist)
    if lo >= -eps:
        if hi <= eps:                         # lying in the plane
            fn = face.plane
            if fn[0] * nx + fn[1] * ny + fn[2] * nz > 0.0:
                return None, face             # its cell is behind it
        return face, None
    if hi <= eps:
        return None, face
    edges = face.edges
    n = len(pts)
    fp, fe, bp, be = [], [], [], []
    for i in range(n):
        j = i + 1 if i + 1 < n else 0
        a, da, db = pts[i], dist[i], dist[j]
        sa = 1 if da > eps else -1 if da < -eps else 0
        sb = 1 if db > eps else -1 if db < -eps else 0
        e = edges[i] if edges is not None else False
        if sa >= 0:
            fp.append(a)
            fe.append(e if (sb >= 0 or sa > 0) else False)
        if sa <= 0:
            bp.append(a)
            be.append(e if (sb <= 0 or sa < 0) else False)
        if sa * sb < 0:
            b = pts[j]
            t = da / (da - db)
            m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                 a[2] + (b[2] - a[2]) * t)
            fp.append(m)
            bp.append(m)
            if sa > 0:                        # leaving the front at m
                fe.append(False)
                be.append(e)
            else:
                be.append(False)
                fe.append(e)
    fd = bd = None
    if face.decals:
        fd, bd = _split_decals(face.decals, plane, eps)
    front = back = None
    if len(fp) >= 3:
        front = Face(tuple(fp), face.plane, face.kind,
                     tuple(fe) if edges is not None else None, fd, face.tag,
                     face.size)
    if len(bp) >= 3:
        back = Face(tuple(bp), face.plane, face.kind,
                    tuple(be) if edges is not None else None, bd, face.tag,
                    face.size)
    return front, back


def split_cell(cell: Cell, plane: Plane, eps: float = EPS
               ) -> Tuple[Optional[Cell], Optional[Cell]]:
    """The parts of a cell in front of and behind a plane. The cut itself
    gets no face: it is inside the leather, never seen."""
    ff, bf = [], []
    for face in cell.faces:
        f, b = split_face(face, plane, eps)
        if f is not None:
            ff.append(f)
        if b is not None:
            bf.append(b)
    front = back = None
    if any(f.kind != INNER for f in ff):
        front = Cell(ff, cell.tag, cell.planes, cell.big)
    if any(f.kind != INNER for f in bf):
        back = Cell(bf, cell.tag, cell.planes, cell.big)
    return front, back


# -- the tree ----------------------------------------------------------------

def _sides(cells, plane, eps):
    """Which side of ``plane`` each cell is on: 1 in front, -1 behind, 0
    across it. Returns ``(in front, behind, across, sides)``."""
    nx, ny, nz, d = plane
    nf = nb = ns = 0
    sides = []
    add = sides.append
    for c in cells:
        q = nx * c.cx + ny * c.cy + nz * c.cz - d
        r = c.rad + eps
        if q >= r:
            nf += 1
            add(1)
            continue
        if q <= -r:
            nb += 1
            add(-1)
            continue
        f = b = False
        for x, y, z in c.verts:
            q = nx * x + ny * y + nz * z - d
            if q > eps:
                f = True
                if b:
                    break
            elif q < -eps:
                b = True
                if f:
                    break
        if f and b:
            ns += 1
            add(0)
        elif f:
            nf += 1
            add(1)
        else:
            nb += 1
            add(-1)
    return nf, nb, ns, sides


def _by_size(cell):
    return -cell.big


def _divide(cells, eps):
    """A plane that parts these cells, and the side each is on -- or None
    when none of their faces parts them (they fill the same space)."""
    n = len(cells)
    best = None
    tried = set()
    # the biggest faces first: a panel's own faces part what lies on it
    # from what lies under it without cutting through anything
    lead = cells if n <= 4 else sorted(cells, key=_by_size)[:4]
    for first, last in ((0, 3), (3, 9)):
        for cell in lead:
            for plane in cell.planes[first:last]:
                if plane in tried:
                    continue
                tried.add(plane)
                nf, nb, ns, sides = _sides(cells, plane, eps)
                if not ns and (not nf or not nb):
                    continue                  # all on one side: no use
                score = abs(nf - nb) + 6 * ns
                if best is None or score < best[0]:
                    best = (score, plane, sides)
                    if not ns and 4 * min(nf, nb) >= n:
                        return plane, sides   # a clean, even parting
        if best is not None and best[0] < n:
            break                             # little or nothing cut through
    if best is not None:
        return best[1], best[2]
    for c in cells:
        for plane in c.planes:
            if plane in tried:
                continue
            tried.add(plane)
            nf, nb, ns, sides = _sides(cells, plane, eps)
            if ns or (nf and nb):
                return plane, sides
    return None


def build(cells: Sequence[Cell], eps: float = EPS):
    """The partition tree of a list of cells. A node is ``[plane, front,
    back]``; a leaf is a tuple of cells (usually one) to paint in any
    order."""
    root = [None]
    todo = [(list(cells), root, 0)]
    while todo:
        group, parent, slot = todo.pop()
        if len(group) <= 1:
            parent[slot] = tuple(group)
            continue
        found = _divide(group, eps)
        if found is None:
            parent[slot] = tuple(group)
            continue
        plane, sides = found
        front, back = [], []
        for c, s in zip(group, sides):
            if s > 0:
                front.append(c)
            elif s < 0:
                back.append(c)
            else:
                f, b = split_cell(c, plane, eps)
                if f is not None:
                    front.append(f)
                if b is not None:
                    back.append(b)
        if not front or not back:
            # what crossed the plane had nothing to paint beyond it: they
            # are all on one side now, so another plane must part them
            todo.append((front or back, parent, slot))
            continue
        node = [plane, None, None]
        parent[slot] = node
        todo.append((front, node, 1))
        todo.append((back, node, 2))
    return root[0]


def back_to_front(tree, vx: float, vy: float, vz: float) -> List[Cell]:
    """The cells of a tree in painting order for an eye far away in the
    direction ``(vx, vy, vz)`` from the model."""
    out: List[Cell] = []
    todo = [tree]
    while todo:
        node = todo.pop()
        if node is None:
            continue
        if type(node) is tuple:
            out.extend(node)
            continue
        plane = node[0]
        if plane[0] * vx + plane[1] * vy + plane[2] * vz >= 0.0:
            todo.append(node[1])              # the eye is in front: paint
            todo.append(node[2])              # what's behind first
        else:
            todo.append(node[2])
            todo.append(node[1])
    return out


def count(tree) -> Tuple[int, int]:
    """``(dividing planes, cells)`` in a tree."""
    planes = cells = 0
    todo = [tree]
    while todo:
        node = todo.pop()
        if node is None:
            continue
        if type(node) is tuple:
            cells += len(node)
        else:
            planes += 1
            todo.append(node[1])
            todo.append(node[2])
    return planes, cells

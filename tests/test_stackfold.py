"""A stack of pieces folded with its main piece: the generated bifold
closing over its pockets, the room each fold makes for what is folded in
it, where a piece laid on the main piece bends and where it stays flat,
and four witnesses that nothing is drawn through anything.

The witnesses are written here and share nothing with the engine's own
reasoning: (a) no two cells share more than the judge lets pass, by a
separating-axis test of this file's own; (b) every corner of every cell
lies behind every face of it, the planes worked out from the corners;
(c) each sheet's cells cover it once; (d) every seam inside a sheet lies
on a neighbouring cell of it, unless the sheet is said to be slit."""

import dataclasses
import math
import random

import pytest

from leathercad import assembly as asm
from leathercad import bsp
from leathercad import foldsolid as fs
from leathercad import polycut as pc
from leathercad import sheets as sh
from leathercad.document import Document
from leathercad.generators import bifold_wallet

SHARE = 1e-4        # what two cells may share (mm): rounding, no more
FLAT_ENOUGH = 1e-7  # how far a corner may stand in front of a face (mm)


# -- the witnesses ------------------------------------------------------------

def _unit(v):
    ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return None if ln < 1e-12 else (v[0] / ln, v[1] / ln, v[2] / ln)


def _corners(cell):
    return list({p for f in cell.faces for p in f.pts})


def _normal(pts, least=1e-12):
    """The way a face looks, from its corners (Newell), and its middle:
    None for a face with less area than ``least`` (mm2, twice over)."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    mid = tuple(sum(p[k] for p in pts) / n for k in range(3))
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln <= least:
        return None, mid
    return (nx / ln, ny / ln, nz / ln), mid


def _ways(cell):
    """The face normals of a cell, from their corners, and the directions
    of its edges."""
    normals, edges = [], []
    for f in cell.faces:
        nv, _mid = _normal(f.pts)
        if nv is not None:
            normals.append(nv)
        for i in range(len(f.pts)):
            p, q = f.pts[i - 1], f.pts[i]
            d = _unit((q[0] - p[0], q[1] - p[1], q[2] - p[2]))
            if d is not None:
                edges.append(d)
    return normals, edges


def _shared(a, b, ways):
    """How far two cells share space (mm): the least overlap of their
    corners along any of ``ways``, 0 if some way parts them."""
    ca, cb = _corners(a), _corners(b)
    least = math.inf
    for v in ways:
        pa = [v[0] * p[0] + v[1] * p[1] + v[2] * p[2] for p in ca]
        pb = [v[0] * p[0] + v[1] * p[1] + v[2] * p[2] for p in cb]
        over = min(max(pa), max(pb)) - max(min(pa), min(pb))
        if over <= 0.0:
            return 0.0
        least = min(least, over)
    return least


def overlaps(cells, excuse=lambda ta, tb: False):
    """(a) Every two cells that share more than ``SHARE`` mm, by the
    separating-axis test (the normals of both, and the cross of every
    edge of one with every edge of the other): ``[(depth, tag, tag)]``."""
    boxes = []
    for c in cells:
        pts = _corners(c)
        boxes.append([min(p[k] for p in pts) for k in range(3)]
                     + [max(p[k] for p in pts) for k in range(3)])
    found = []
    ways = {}
    for i, a in enumerate(cells):
        for j in range(i + 1, len(cells)):
            ba, bb = boxes[i], boxes[j]
            if any(min(ba[k + 3], bb[k + 3]) - max(ba[k], bb[k]) <= SHARE
                   for k in range(3)):
                continue
            b = cells[j]
            if excuse(a.tag, b.tag):
                continue
            for k, c in ((i, a), (j, b)):
                if k not in ways:
                    ways[k] = _ways(c)
            (na, ea), (nb, eb) = ways[i], ways[j]
            depth = _shared(a, b, na + nb)
            if depth > SHARE:
                across = [w for u in ea for v in eb for w in [_unit((
                    u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                    u[0] * v[1] - u[1] * v[0]))] if w is not None]
                depth = min(depth, _shared(a, b, across))
            if depth > SHARE:
                found.append((depth, a.tag, b.tag))
    return found


def inside_out(cells):
    """(b) How far any corner of a cell stands in front of any face of it,
    each face's plane worked out from its own corners (mm). Every face a
    cell has is a face: one whose corners lie on a line, with an area
    made of nothing but rounding, has the plane that rounding gives it --
    which is what a painter that parts cells by their faces would go
    by."""
    worst = 0.0
    for c in cells:
        pts = _corners(c)
        for f in c.faces:
            nv, mid = _normal(f.pts, 0.0)
            if nv is None:
                continue
            worst = max(worst, max(
                nv[0] * (p[0] - mid[0]) + nv[1] * (p[1] - mid[1])
                + nv[2] * (p[2] - mid[2]) for p in pts))
    return worst


def _area(pts):
    return 0.5 * sum(pts[i - 1][0] * pts[i][1] - pts[i][0] * pts[i - 1][1]
                     for i in range(len(pts)))


def _apart2(a, b):
    """How far two convex polygons share the plane (mm), by their edges."""
    least = math.inf
    for poly in (a, b):
        for i in range(len(poly)):
            p, q = poly[i - 1], poly[i]
            nx, ny = q[1] - p[1], p[0] - q[0]
            ln = math.hypot(nx, ny)
            if ln < 1e-12:
                continue
            pa = [(x * nx + y * ny) / ln for x, y in a]
            pb = [(x * nx + y * ny) / ln for x, y in b]
            over = min(max(pa), max(pb)) - max(min(pa), min(pb))
            if over <= 0.0:
                return 0.0
            least = min(least, over)
    return least


def covered(lay):
    """(c) Each sheet whose cells do not cover its outline once: ``[(sheet,
    area of its cells, area of its outline, most two cells share)]``."""
    out = []
    for k, s in enumerate(lay.sheets, 1):
        mine = [c.pts for c in lay.sheet_cells if c.sheet == k]
        got, want = sum(_area(p) for p in mine), _area(s.ring)
        twice = max([0.0] + [_apart2(a, b) for i, a in enumerate(mine)
                             for b in mine[i + 1:]])
        if abs(got - want) > 2e-3 * want + 1e-6 or twice > 1e-6:
            out.append((k, got, want, twice))
    return out


def _on(p, cell, tol):
    for f in cell.faces:
        nv, mid = _normal(f.pts)
        if nv is not None and nv[0] * (p[0] - mid[0]) + nv[1] * (
                p[1] - mid[1]) + nv[2] * (p[2] - mid[2]) > tol:
            return False
    return True


def open_seams(cells, slit=()):
    """(d) The sheets with a seam (an inside wall) that lies on no other
    cell of the same sheet, and are not said to be slit: ``{sheet:
    how many}``."""
    by = {}
    for c in cells:
        by.setdefault(sh.sheet_of(c.tag), []).append(c)
    out = {}
    for k, group in by.items():
        if not k or k in slit:
            continue
        n = 0
        for c in group:
            for f in c.faces:
                if f.kind != bsp.INNER:
                    continue
                mid = tuple(sum(p[i] for p in f.pts) / len(f.pts)
                            for i in range(3))
                if not any(o is not c and _on(mid, o, 1e-3) for o in group):
                    n += 1
        if n:
            out[k] = n
    return out


def slit_sheets(lay):
    """The sheets a note says are parted: slit, or over a crossing of
    folds."""
    return {n.sheet for n in getattr(lay, "notes", ())
            if n.kind in ("slit", "crossing")}


def named(lay):
    """How deep the notes of a layout say each two pieces are drawn into
    each other, at the deepest: ``{(piece, piece): mm}``. A ``"clash"``
    names its two; a ``"limit"`` or a ``"landing"`` the main piece in
    itself; a ``"slit"`` or ``"crossing"`` the sheet in itself and the
    main piece in it, there."""
    out = {}

    def say(a, b, mm):
        key = tuple(sorted((a, b)))
        out[key] = max(out.get(key, 0.0), mm)

    for n in getattr(lay, "notes", ()):
        if n.kind == "clash":
            say(n.sheet, n.other[0], n.a)
        elif n.kind in ("limit", "landing"):
            say(0, 0, n.a)
        elif n.kind in ("slit", "crossing"):
            say(n.sheet, n.sheet, n.b)
            say(0, n.sheet, n.b)
    return out


def witnesses(lay, moments=9, inside=0, share=0.05, enough=0.9):
    """Everything the four witnesses find wrong with a layout, at
    ``moments`` moments of its folding (and, with ``inside``, that many
    more moments evenly inside each step), as a list of words.

    Two cells drawn into each other by no more than ``share`` mm are let
    pass: that is no finding of the engine's either (``foldsolid.
    _TOUCH``). Deeper, they are no fault of the witnesses' to find when a
    note of the layout names those two pieces at least ``enough`` as
    deep as they are found (``named``): the judge has said it. A sheet a
    note says is parted (``slit_sheets``) may have seams that open."""
    out = [f"sheet {k} cells cover {got:.3f} of {want:.3f}, {twice:.2e} "
           f"twice" for k, got, want, twice in covered(lay)]
    slit = slit_sheets(lay)
    said = named(lay)
    n = lay.n_steps
    times = [n * j / (moments - 1) for j in range(moments)] if n else [0.0]
    for step in range(n):
        times += [step + (j + 1) / (inside + 1) for j in range(inside)]
    for t in sorted(set(times)):
        cells = lay.solid(lay.fractions(t))
        io = inside_out(cells)
        if io > FLAT_ENOUGH:
            out.append(f"t {t:.2f}: a cell is inside out by {io:.2e}")
        bad = [(d, ta, tb) for d, ta, tb in overlaps(cells) if d > share
               and said.get(tuple(sorted((sh.sheet_of(ta),
                                          sh.sheet_of(tb)))), -1.0)
               < enough * d]
        if bad:
            d, ta, tb = max(bad)
            out.append(f"t {t:.2f}: {ta} in {tb} by {d:.4f} "
                       f"({len(bad)} pairs)")
        for k, cnt in open_seams(cells, slit).items():
            out.append(f"t {t:.2f}: sheet {k} has {cnt} open seams")
    return out


# -- stacks to fold -----------------------------------------------------------

def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def rounded(x0, y0, x1, y1, r, n=8):
    if r <= 0.0:
        return rect(x0, y0, x1, y1)
    pts = []
    for cx, cy, a0 in ((x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90),
                       (x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270)):
        for k in range(n + 1):
            a = math.radians(a0 + 90.0 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def turned(pts, deg, cx, cy):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s,
             cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def ell(x, y, w, h, nw, nh):
    return [(x, y), (x + w, y), (x + w, y + nh), (x + nw, y + nh),
            (x + nw, y + h), (x, y + h)]


def notched(x, y, w, h, r):
    pts = [(x, y), (x + w, y), (x + w, y + h)]
    for k in range(9):
        a = math.pi * k / 8
        pts.append((x + w / 2 + r * math.cos(a), y + h - r * math.sin(a)))
    pts.append((x, y + h))
    return pts


TEE = [(0, 0), (100, 0), (100, 60), (70, 60), (70, 120), (30, 120),
       (30, 60), (0, 60)]


def fold(x0, y0, x1, y1, angle=180.0, way=1, **kw):
    kw.setdefault("label", "A")
    return fs.FoldLine((x0, y0), (x1, y1), angle, way, **kw)


def stack(ring, folds, pieces, t=2.0, **kw):
    """``fold_piece`` on ``ring`` with sheets ``(ring, thickness[, side])``
    laid on it in that order."""
    sheets = [sh.Sheet(p[0], p[1], *p[2:3], name=f"s{k}")
              for k, p in enumerate(pieces, 1)]
    return fs.fold_piece(ring, folds, thickness=t, sheets=sheets, **kw)


def random_stack(rng, family):
    """A random stack of the family named: ``(outline, folds, sheets,
    thickness, base)``."""
    W, H = rng.uniform(120, 240), rng.uniform(60, 110)
    out = rounded(0, 0, W, H, rng.choice([0, 0, 4, 10]))
    way = rng.choice([1, -1])
    base = None
    folds = []
    if family == "shut":
        x = W * rng.uniform(0.35, 0.65)
        folds = [fold(x, 0, x, H, 180.0, way)]
    elif family == "open":
        x = W * rng.uniform(0.35, 0.65)
        folds = [fold(x, 0, x, H, rng.choice([30.0, 60.0, 90.0, 120.0,
                                              135.0, 160.0]), way)]
    elif family == "zigzag":
        folds = [fold(W / 3, 0, W / 3, H, 180.0, way, order=1),
                 fold(2 * W / 3, 0, 2 * W / 3, H, 180.0, -way, order=2,
                      label="B")]
    elif family == "four":
        # three parallel folds, each of them either way, made one after
        # another
        folds = [fold(W * k / 4, 0, W * k / 4, H,
                      rng.choice([180.0, 180.0, 90.0]), rng.choice([1, -1]),
                      order=k, label="ABC"[k - 1]) for k in (1, 2, 3)]
    elif family == "tee":
        out, W, H = TEE, 100.0, 120.0
        y = rng.choice([60.0, 65.0])
        folds = [fold(0, y, 100, y, rng.choice([180.0, 180.0, 90.0]), way,
                      radius=rng.choice([-1.0, 3.0]))]
    elif family == "three":
        # a strip in three: each flap folds in over the middle panel, one
        # after the other, and reaches the other bend
        out = rect(0, 0, W, H)
        a, b = W * rng.uniform(0.3, 0.36), W * rng.uniform(0.64, 0.7)
        folds = [fold(a, 0, a, H, 180.0, way, order=1),
                 fold(b, 0, b, H, 180.0, way, order=2, label="B")]
        base = 1
    elif family == "ell":
        # an L-shaped piece with a fold across each arm, the two not side
        # by side: their lines cross past the piece's inside corner
        H = rng.uniform(120, 200)
        nw, nh = W * rng.uniform(0.3, 0.5), H * rng.uniform(0.3, 0.5)
        out = ell(0, 0, W, H, nw, nh)
        y = rng.uniform(nh + 10, H - 10)
        x = rng.uniform(nw + 10, W - 10)
        folds = [fold(0, y, W, y, rng.choice([180.0, 135.0, 90.0]), way,
                      order=1),
                 fold(x, 0, x, H, rng.choice([180.0, 135.0, 90.0]),
                      rng.choice([1, -1]), order=2, label="B")]
    elif family == "wings":
        # a T-shaped piece with a liner wider than its flap, and pieces
        # on the liner's wings and elsewhere
        out, W, H = TEE, 100.0, 120.0
        y = rng.choice([60.0, 62.0, 65.0, 70.0])
        folds = [fold(0, y, 100, y, rng.choice([180.0, 180.0, 90.0]), way,
                      radius=rng.choice([-1.0, 3.0]))]
        x0, x1 = 30 - rng.uniform(4, 25), 70 + rng.uniform(4, 25)
        y0, y1 = y - rng.uniform(4, 20), y + rng.uniform(15, 50)
        side = rng.choice([1, 1, -1])
        sheets = [sh.Sheet(rect(x0, y0, x1, y1), rng.choice([0.6, 0.8, 1.0]),
                           side, name="liner")]
        for k in range(rng.randint(1, 4)):
            a = rng.choice([x0, 70.0])
            w = rng.uniform(4, 22)
            b = rng.uniform(y0, y1 - 8)
            sheets.append(sh.Sheet(
                rect(a + rng.uniform(-3, 3), b, a + w, b + rng.uniform(6, 30)),
                rng.choice([0.6, 1.0, 1.4]), side, name=f"w{k}"))
        return out, folds, sheets, rng.choice([1.0, 1.6, 2.0]), base
    sheets = []
    for k in range(rng.randint(1, 7)):
        w, h = rng.uniform(15, W * 1.05), rng.uniform(10, H * 1.05)
        x, y = rng.uniform(-8, W - w + 8), rng.uniform(-8, H - h + 8)
        shape = rng.choice(["rect", "rect", "round", "turn", "ell",
                            "notch"])
        if shape == "rect":
            ring = rect(x, y, x + w, y + h)
        elif shape == "round":
            ring = turned(rounded(x, y, x + w, y + h,
                                  min(3.0, w / 3, h / 3)),
                          rng.uniform(0, 180), x + w / 2, y + h / 2)
        elif shape == "turn":
            ring = turned(rect(x, y, x + w, y + h), rng.uniform(-40, 40),
                          x + w / 2, y + h / 2)
        elif shape == "ell":
            ring = ell(x, y, w, h, w * rng.uniform(0.3, 0.7),
                       h * rng.uniform(0.3, 0.7))
        else:
            ring = notched(x, y, w, h, min(w, h) * rng.uniform(0.15, 0.4))
        sheets.append(sh.Sheet(ring, rng.choice([0.4, 0.6, 0.8, 1.0, 1.4,
                                                 2.0, 2.5]),
                               rng.choice([1, 1, -1]), name=f"s{k}"))
    return out, folds, sheets, rng.choice([1.0, 1.6, 2.0, 3.0]), base


def _drawing(shapes):
    doc = Document("generated")
    for s in shapes:
        doc.add_shape(s)
    return doc


def generated(**kw):
    """The generated bifold put together as the window reads it: the main
    piece as it is drawn, every piece in its frame, the spine to the
    back. ``(outline, folds, sheets, thickness, holes)``; the sheets are
    the liner, the left backing and pockets 3, 2, 1, then the right."""
    st, = asm.stacks(_drawing(bifold_wallet(**kw)))
    main = st.main
    m = (-1.0, 0.0, 0.0, 1.0, 0.0, 0.0) if main.mirrored \
        else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def ring(pts):
        out = [asm.map_point(m, p) for p in pts]
        return out[::-1] if main.mirrored else out

    folds = [fs.FoldLine(asm.map_point(m, f.a), asm.map_point(m, f.b),
                         f.angle, -f.direction if main.mirrored
                         else f.direction, label="A")
             for f in main.folds]
    sheets = [sh.Sheet(ring(p.outline), p.thickness,
                       side=1 if p.above != main.mirrored else -1,
                       grain=1 if p.grain_up != main.mirrored else -1,
                       name=p.shape.name, key=p.key,
                       holes=[asm.map_point(m, h) + (h[2],)
                              for h in p.holes])
              for p in st.over() + st.under()]
    holes = [asm.map_point(m, h) + (h[2],) for h in main.holes]
    return ring(main.outline), folds, sheets, main.thickness, holes


def bifold(sheets_from=None, **kw):
    outline, folds, sheets, t, holes = generated(**kw)
    if sheets_from is not None:
        sheets = sheets_from(sheets)
    return fs.fold_piece(outline, folds, thickness=t, holes=holes,
                         sheets=sheets)


def _bent(lay, fold=0):
    """The sheets with leather in a fold's bend."""
    return sorted({c.sheet for c in lay.sheet_cells if c.hinge is not None
                   and lay.tree[c.hinge].fold == fold})


def _notes(lay, *kinds):
    return [n for n in getattr(lay, "notes", ()) if n.kind in kinds]


def _narrower(sheets, by=5.54):
    """The pocket pieces made ``by`` mm narrower at their inner ends."""
    for s in sheets[1:]:
        x0, y0, x1, y1 = pc.bounds(s.ring)
        s.ring = rect(x0 + by, y0, x1, y1) if x0 > 0 \
            else rect(x0, y0, x1 - by, y1)
    return sheets


def _liner_shorter(by):
    def short(sheets):
        x0, _y0, x1, _y1 = pc.bounds(sheets[0].ring)
        k = (x1 - x0 - by) / (x1 - x0)
        sheets[0].ring = [(x * k, y) for x, y in sheets[0].ring]
        return sheets
    return short


# -- the generated bifold -----------------------------------------------------

def test_the_generated_bifold_closes_at_4_mm_and_takes_15_08_of_its_shell():
    lay = bifold(shell_t=1.6, inner_t=0.8)
    assert lay.radius == pytest.approx([4.0], abs=1e-9)
    assert lay.used == pytest.approx([15.0796], abs=1e-4)
    # the pockets of the two halves come to rest on each other
    assert lay.why == [("meet", 5, 9)]
    assert len(lay.cells) + len(lay.sheet_cells) == 92
    # every piece goes into the bend: the liner round it, the pockets
    # reaching 5.54 mm in, where they would stop 7.54 mm from the line
    assert _bent(lay) == list(range(1, 10))
    reach = _notes(lay, "reach")
    assert [n.sheet for n in reach] == list(range(2, 10))
    assert all(n.a == pytest.approx(5.5398, abs=1e-4)
               and n.b == pytest.approx(7.5398, abs=1e-4) for n in reach)
    # shut: 11.2 mm thick, a spine as wide; 97.66 mm by 90 mm (the round
    # of the spine stands 5.6 mm past where the bend begins, 92.06 mm from
    # the shell's far edge)
    size, = _notes(lay, "size")
    assert (size.a, size.b) == pytest.approx((97.6602, 90.0), abs=1e-4)
    assert _notes(lay, "shut")[0].a == pytest.approx(11.2, abs=1e-9)
    assert _notes(lay, "spine")[0].a == pytest.approx(11.2, abs=1e-9)
    # through the pockets it is leather all the way; above the liner's
    # top edge only the shell, twice
    most, = _notes(lay, "leather")
    assert (most.a, most.b) == pytest.approx((11.2, 3.2), abs=1e-6)
    # it closes over all nine pieces, and nothing is in anything
    closes, = _notes(lay, "closes")
    assert (closes.a, closes.b) == (9.0, 0.0)
    assert not _notes(lay, "clash", "limit")


def test_the_bifolds_liner_has_3_77_mm_too_much_round_the_inside():
    lay = bifold(shell_t=1.6, inner_t=0.8)
    cross, = _notes(lay, "cross")
    # pi times the 1.2 mm from the shell's middle to the liner's
    assert (cross.sheet, cross.a, cross.b) == (
        1, pytest.approx(3.7699, abs=1e-4), pytest.approx(0.0, abs=1e-9))
    # cut that much shorter it comes level when shut: the plain line
    lay = bifold(_liner_shorter(3.77), shell_t=1.6, inner_t=0.8)
    assert not _notes(lay, "cross")
    level, = _notes(lay, "level")
    assert (level.a, level.b) == (pytest.approx(3.7699, abs=1e-4),
                                  pytest.approx(3.77, abs=1e-9))


def test_pockets_5_54_mm_narrower_leave_only_the_liner_in_the_bend():
    lay = bifold(_narrower, shell_t=1.6, inner_t=0.8)
    assert lay.radius == pytest.approx([4.0], abs=1e-9)
    assert _bent(lay) == [1]
    assert not _notes(lay, "reach")
    assert len(lay.cells) + len(lay.sheet_cells) == 44
    assert witnesses(lay, 9, share=SHARE) == []


def test_the_bifold_sized_from_its_leather_has_nothing_but_its_liner_bent():
    lay = bifold(shell_t=1.6, inner_t=0.8, fit=True)
    x0, _y0, x1, _y1 = pc.bounds(lay.outline)
    assert x1 - x0 == pytest.approx(210.28, abs=0.005)
    x0, _y0, x1, _y1 = pc.bounds(lay.sheets[0].ring)
    assert x1 - x0 == pytest.approx(206.51, abs=0.005)
    assert lay.radius == pytest.approx([4.0], abs=1e-9)
    assert _bent(lay) == [1]
    assert not _notes(lay, "reach", "cross", "clash", "limit")
    assert [n.kind for n in _notes(lay, "level")] == ["level"]
    assert witnesses(lay, 9, share=SHARE) == []


def test_the_generated_bifold_is_whole_at_21_moments_of_closing():
    assert witnesses(bifold(shell_t=1.6, inner_t=0.8), 21, share=SHARE) == []


# -- the room a fold needs ----------------------------------------------------

def test_a_lining_inside_a_right_angle_rides_at_0_8_and_takes_2_513():
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100, 90.0)],
                [(rect(20, 10, 180, 90), 0.8, 1)], t=1.6)
    assert lay.radius == pytest.approx([0.8], abs=1e-9)
    assert lay.used == pytest.approx([2.5133], abs=1e-4)
    assert lay.why == [("ride", 1, 0)]
    assert witnesses(lay, 13, share=SHARE) == []


def test_a_4_mm_patch_on_one_side_opens_a_fold_to_2_mm():
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                [(rect(20, 20, 80, 80), 4.0, 1)], t=1.6)
    assert lay.radius == pytest.approx([2.0], abs=1e-9)
    assert lay.why == [("meet", 0, 1)]
    assert witnesses(lay, 13, share=SHARE) == []


def test_a_liner_bigger_than_its_shell_bends_at_0_8():
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                [(rect(-5, -5, 205, 105), 0.8, 1)], t=1.6)
    assert lay.radius == pytest.approx([0.8], abs=1e-9)
    assert lay.used == pytest.approx([5.0265], abs=1e-4)
    assert witnesses(lay, 13, share=SHARE) == []


def _three():
    pieces = [(rect(0, 0, 260, 98), 0.8, 1)]
    for x0, x1 in ((2, 62), (100, 160), (198, 258)):
        pieces += [(rect(x0, 0, x1, 70), 0.8, 1),
                   (rect(x0, 0, x1, 45), 0.8, 1)]
    return stack(rect(0, 0, 260, 100),
                 [fold(80, 0, 80, 100, order=2),
                  fold(180, 0, 180, 100, order=1, label="B")],
                 pieces, t=1.6, base=1)


def test_a_strip_in_three_with_three_layers_a_panel_is_12_mm_thick():
    lay = _three()
    assert lay.radius == pytest.approx([4.4, 2.4], abs=1e-9)
    assert lay.used == pytest.approx([16.3363, 10.0531], abs=1e-4)
    assert lay.shift == [0.0, 0.0]
    assert _notes(lay, "shut")[0].a == pytest.approx(12.0, abs=1e-9)
    assert witnesses(lay, 17, share=SHARE) == []


@pytest.mark.parametrize("back, radius, bend", [
    (1.0, 3.0, 6.2832), (2.0, 3.0, 6.2832), (3.5, 1.3299, 3.6598),
    (6.0, 0.0, 1.5708), (10.0, 0.0, 1.5708)])
def test_piles_either_side_of_a_right_angle_open_it_as_far_as_they_stand_in(
        back, radius, bend):
    # 3 mm piles each side of a 90 degree fold in 2 mm leather, standing
    # back from the line by ``back``
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100, 90.0)],
                [(rect(40, 20, 100 - back, 80), 3.0, 1),
                 (rect(100 + back, 20, 160, 80), 3.0, 1)])
    assert lay.radius[0] == pytest.approx(radius, abs=1e-4)
    assert lay.used[0] == pytest.approx(bend, abs=1e-4)
    assert lay.why == [("corner", 1, 2) if radius else None]
    assert witnesses(lay, 9, share=SHARE) == []


# -- where a piece bends and where it stays flat -----------------------------

def test_a_patch_beside_a_flap_stays_flat():
    lay = stack(TEE, [fold(0, 65, 100, 65, radius=6.0)],
                [(rect(2, 40, 28, 59.5), 1.0, 1)])
    assert _bent(lay) == []
    assert not _notes(lay, "slit")
    assert witnesses(lay, 13) == []


def test_a_strap_across_the_end_of_a_hinge_is_said_to_be_slit():
    lay = stack(TEE, [fold(0, 65, 100, 65, radius=6.0)],
                [(rect(20, 50, 40, 80), 1.0, 1)])
    assert [(n.sheet, n.fold) for n in _notes(lay, "slit")] == [(1, 0)]
    assert _bent(lay) == [1]
    assert witnesses(lay, 13) == []


def test_a_patch_past_the_bodys_edge_beside_the_flap_stays_flat():
    lay = stack(TEE, [fold(0, 60, 100, 60)],
                [(rect(2, 40, 28, 70), 1.0, 1)])
    assert _bent(lay) == []
    assert {c.panel for c in lay.sheet_cells} == {lay.panels[0].index
                                                  if lay.panels[0].area
                                                  > lay.panels[1].area
                                                  else 1}
    assert witnesses(lay, 13) == []


def test_a_flap_with_a_piece_on_it_moves_a_later_fold_by_what_the_pile_needs():
    # the flap folds in on the middle panel first and reaches right to the
    # second fold. Alone, 2 mm leather, that fold forms pi mm further on;
    # with 1 mm of leather on the flap, 3 pi / 2
    folds = [fold(80, 0, 80, 60, order=1),
             fold(160, 0, 160, 60, order=2, label="B")]
    alone = fs.fold_piece(rect(0, 0, 240, 60), folds, base=1)
    assert alone.shift == pytest.approx([0.0, math.pi], abs=1e-9)
    lay = stack(rect(0, 0, 240, 60), folds,
                [(rect(0, 5, 78, 55), 1.0, 1)], base=1)
    assert lay.shift == pytest.approx([0.0, 1.5 * math.pi], abs=1e-9)
    moved, = _notes(lay, "moved")
    assert (moved.fold, moved.a, moved.other) == (
        1, pytest.approx(1.5 * math.pi, abs=1e-9), (3,))
    # nothing is said to shorten: the piece laid on it would not be
    assert lay.shift_trim == [[], []] and lay.remedy(1) == ""
    assert witnesses(lay, 17) == []


# -- past the end of a hinge, and where a piece is parted -------------------

def _wings(way):
    # a liner wider than the T's flap, folded 180 degrees 3 mm up its
    # stem, with a pocket on its left wing (beside the flap, over nothing
    # of the main piece)
    return stack(TEE, [fold(0, 63, 100, 63, 180.0, way)],
                 [(rect(10, 50, 90, 110), 1.0, 1),
                  (rect(12, 80, 28, 100), 1.0, 1)])


def _panels_of(lay, k):
    return sorted({(p, tuple(round(v, 6) for v in pc.bounds(q)))
                   for p, q, _f in lay._regions[k - 1]})


@pytest.mark.parametrize("way", [1, -1])
def test_a_liner_wider_than_its_flap_swings_with_it_wings_and_all(way):
    lay = _wings(way)
    flap = lay.panels[0].index if lay.panels[0].box[1] >= 63.0 - 1e-9 \
        else lay.panels[1].index
    body = 1 - flap
    # the liner is cut along the fold line only: the part above it, wings
    # and all, goes with the flap; the pocket on the wing goes with it
    assert _panels_of(lay, 1) == sorted([
        (body, (10.0, 50.0, 90.0, 63.0)), (flap, (10.0, 63.0, 90.0, 110.0))])
    assert _panels_of(lay, 2) == [(flap, (12.0, 80.0, 28.0, 100.0))]
    slits = _notes(lay, "slit")
    if way == 1:
        # to the front the bend is 6.28 mm wide and reaches 0.14 mm into
        # the body beside the flap: the liner glued there lies flat with
        # it, and is parted from what bends, along the body's edge (20 mm)
        # and the end of the hinge (0.14 mm), at each end
        assert [(n.sheet, n.fold, round(n.a, 4), n.at) for n in slits] == [
            (1, 0, 20.1416, (20.0, 60.0)), (1, 0, 20.1416, (80.0, 60.0))]
        assert all(n.other == (0,) and n.b == pytest.approx(0.1412, abs=1e-4)
                   for n in slits)
    else:
        # to the back the bend misses the body: nothing is parted
        assert slits == []
    assert not _notes(lay, "clash", "limit", "crossing")
    assert witnesses(lay, 21, 3) == []


TONGUE = [(0, 0), (100, 0), (100, 100), (60.5, 100), (60.5, 50), (60, 50),
          (60, 100), (40, 100), (40, 50), (39.5, 50), (39.5, 100), (0, 100)]


def test_a_patch_across_the_slit_beside_a_flap_is_slit_along_the_flaps_edge():
    # a flap cut into a body by two slits half a mm wide, folded over at
    # its foot; a patch lies across the right-hand slit
    lay = stack(TONGUE, [fold(40, 50, 60, 50)],
                [(rect(55, 70, 70, 85), 1.0, 1)])
    flap = next(p.index for p in lay.panels if p.box[0] >= 40.0 - 1e-9)
    body = 1 - flap
    # (the half mm of it over the slit itself goes with the flap)
    assert {(p, tuple(round(v, 6) for v in pc.bounds(q)))
            for p, q, _f in lay._regions[0]} == {
        (flap, (55.0, 70.0, 60.0, 85.0)), (flap, (60.0, 70.0, 60.5, 85.0)),
        (body, (60.5, 70.0, 70.0, 85.0))}
    slit, = _notes(lay, "slit")
    assert (slit.sheet, slit.fold, slit.a, slit.at, slit.other) == (
        1, 0, pytest.approx(15.0, abs=1e-9), (60.5, 77.5), ())
    assert witnesses(lay, 13, 3) == []


# a body with a flap cut into it by two slits half a mm wide, down to 52
# mm; the flap folded over a few mm up from the slits' ends
SLIT_FLAP = [(0, 0), (100, 0), (100, 60), (70.5, 60), (70.5, 52), (70, 52),
             (70, 120), (30, 120), (30, 52), (29.5, 52), (29.5, 60), (0, 60)]


@pytest.mark.parametrize("y, way, slit, deep", [
    (57.0, 1, 25.6416, 0.3394), (57.0, -1, 24.0708, 0.0),
    (59.0, 1, 23.6416, 0.4512), (59.0, -1, 22.0708, 0.0),
    (59.9, 1, 22.7416, 0.4783), (59.9, -1, 21.1708, 0.0)])
def test_a_liner_over_a_flap_cut_into_its_body_swings_with_it_wings_and_all(
        y, way, slit, deep):
    # a liner wider than the flap lies over its root and over the body's
    # leather beside the slits: what stands above the body's edge goes
    # with the flap, and only what is glued to the body beside the slits
    # is parted from it, along the body's edge (once given as 54 to 57
    # mm along the flap's edges, its wings left lying on the body)
    lay = stack(SLIT_FLAP, [fold(30, y, 70, y, 180.0, way)],
                [(rect(10, 40, 90, 110), 1.0, 1)])
    flap = next(p.index for p in lay.panels if p.box[1] >= y - 1e-9)
    assert {p for p, q, _f in lay._regions[0]
            if pc.bounds(q)[1] >= 60.0 - 1e-9} == {flap}
    slits = _notes(lay, "slit")
    assert [(round(n.a, 4), round(n.b, 4), n.at) for n in slits] == [
        (slit, deep, (19.75, 60.0)), (slit, deep, (80.25, 60.0))]
    assert witnesses(lay, 5, 3) == []


# seed 48 of the first check's stacks at the end of a T's hinge: piece 5
# rests on piece 4, which lies on the flap and stands past its edge
SEED48 = [(0, 0), (100, 0), (100, 60), (72.64, 60), (72.64, 101.06),
          (25.4, 101.06), (25.4, 60), (0, 60)]
SEED48_PIECES = [
    ([(70.89, 56.1), (80.1, 55.92), (80.96, 101.46), (71.75, 101.63)],
     0.6, -1),
    ([(71.72, 59.52), (75.19, 59.52), (75.19, 96.04), (71.72, 96.04)],
     0.8, -1),
    ([(73.06, 65.77), (74.53, 65.77), (74.53, 93.88), (73.06, 93.88)],
     2.0, -1),
    ([(89.8, 73.15), (90.3, 75.58), (56.4, 82.69), (55.89, 80.26)], 1.5,
     -1),
    ([(75.93, 69.42), (76.9, 69.42), (77.0, 69.93), (77.29, 70.36),
      (77.72, 70.65), (78.23, 70.75), (78.74, 70.65), (79.17, 70.36),
      (79.46, 69.93), (79.56, 69.42), (80.53, 69.42), (80.53, 89.46),
      (75.93, 89.46)], 1.5, -1)]


def test_a_piece_over_nothing_of_the_main_piece_goes_with_what_it_rests_on():
    lay = stack(SEED48, [fold(0, 65, 100, 65, 180.0, -1, radius=3.0)],
                SEED48_PIECES, t=1.0)
    assert lay.sheets[4].rests == [4]
    flap = next(p.index for p in lay.panels if p.box[1] >= 65.0 - 1e-9)
    assert {p for p, _q, _f in lay._regions[3]} == {flap}
    assert {p for p, _q, _f in lay._regions[4]} == {flap}
    # it rides round the inside of the bend with the piece it rests on
    assert lay.why == [("ride", 5, 0)]
    assert lay.radius == pytest.approx([6.4], abs=1e-9)
    for j in range(21):
        cells = lay.solid(lay.fractions(j / 20))
        assert not [d for d, ta, tb in overlaps(cells)
                    if {sh.sheet_of(ta), sh.sheet_of(tb)} == {4, 5}], j


def test_a_piece_over_nothing_resting_on_nothing_goes_with_its_side():
    # a patch on the back beside the flap, over nothing of the main piece
    # and resting on nothing, nearer the body's edge than the flap's but
    # on the flap's side of the fold line: it goes with the flap
    lay = stack(TEE, [fold(0, 65, 100, 65)], [(rect(72, 66, 98, 72), 1.0,
                                                -1)])
    flap = next(p.index for p in lay.panels if p.box[1] >= 65.0 - 1e-9)
    assert {p for p, _q, _f in lay._regions[0]} == {flap}
    assert witnesses(lay, 9, 3) == []



def test_a_patch_on_a_liner_past_the_stem_goes_on_round_with_the_liner():
    # a liner on the T's front standing out past the stem's edge, which
    # goes on round the bend past the hinge's end, and a patch on the
    # liner beside the stem, over no leather of the main piece: what lies
    # with what goes on goes on too, so the patch is not left flat to be
    # drawn 0.89 mm into the liner folded over it
    lay = stack(TEE, [fold(0, 65, 100, 65)], [(rect(20, 50, 80, 80), 1.0, 1),
                                              (rect(72, 56, 80, 64), 1.0, 1)])
    assert _notes(lay, "clash") == []
    assert {p for p, _q, _f in lay._regions[1]} == {1}
    assert witnesses(lay, 9, 3) == []


@pytest.mark.parametrize("way", [1, -1])
def test_a_piece_on_the_flap_past_the_stem_bends_on_along_the_fold_line(
        way):
    # a 15 by 19 mm piece past the T's stem, clear of it by 5 mm and of
    # the body by 1 mm, across the line the stem folds on: it goes with
    # the flap, which carries it round past the hinge's end, and so it is
    # cut along the fold line -- 15 by 15 mm with the flap, 15 by 4 with
    # the body -- not swung whole with the flap
    lay = stack(TEE, [fold(0, 65, 100, 65, 180.0, way)],
                [(rect(75, 61, 90, 80), 1.0, 1)])
    flap = next(p.index for p in lay.panels if p.box[1] >= 65.0 - 1e-9)
    body = next(p.index for p in lay.panels if p.index != flap)
    assert sorted((p, round(abs(pc.area(q)), 6))
                  for p, q, _f in lay._regions[0]) == sorted(
        [(flap, 225.0), (body, 60.0)])
    assert witnesses(lay, 9, 3) == []

L_SHAPE = [(0, 0), (100, 0), (100, 60), (40, 60), (40, 140), (0, 140)]



@pytest.mark.parametrize("side", [1, -1])
def test_a_patch_over_where_fold_lines_cross_cut_by_one_only_is_not_said(
        side):
    # the L-shaped piece folded across its upright arm and then across its
    # foot, the lines crossing past its inside corner at (70, 75), and a
    # 16 by 14 mm patch over that point and over nothing of the piece: it
    # is cut along the first line only (in two, 8 by 14 mm each side of
    # it), the second does not work it there, and nothing is parted
    lay = stack(L_SHAPE, [fold(0, 75, 100, 75, 180.0, 1, order=1),
                          fold(70, 0, 70, 60, 135.0, -1, label="B",
                               order=2)],
                [(rect(62, 68, 78, 82), 1.0, side)])
    assert sorted(round(abs(pc.area(q)), 6)
                  for _p, q, _f in lay._regions[0]) == [112.0, 112.0]
    assert _notes(lay, "crossing") == []
    assert witnesses(lay, 9, 3) == []

def test_a_piece_over_where_two_fold_lines_cross_past_the_main_piece_is_said():
    # an L-shaped piece folded across its upright arm and then across its
    # foot; the two lines cross past its inside corner, at (70, 75), under
    # a liner that stands out over the corner
    lay = stack(L_SHAPE, [fold(0, 75, 100, 75, 180.0, 1, order=1),
                          fold(70, 0, 70, 60, 135.0, -1, label="B",
                               order=2)],
                [(rect(20, 50, 100, 100), 1.0, 1)])
    crossing, = _notes(lay, "crossing")
    assert (crossing.sheet, crossing.fold, crossing.other, crossing.at) == (
        1, 0, (1,), (70.0, 75.0))
    # the liner goes three ways, and is parted round the crossing: said,
    # with how long and how deep
    assert len({p for p, _q, _f in lay._regions[0]}) == 3
    slit, = _notes(lay, "slit")
    assert (slit.sheet, round(slit.a, 4), round(slit.b, 4),
            slit.rest) == (1, 34.3197, 0.5369, 0.0)
    assert witnesses(lay, 17, 3) == []


def _tray(*pieces):
    return stack(rect(0, 0, 160, 160),
                 [fold(30, 0, 30, 160, 90.0),
                  fold(130, 0, 130, 160, 90.0, label="B"),
                  fold(0, 30, 160, 30, 90.0, label="C"),
                  fold(0, 130, 160, 130, 90.0, label="D")], list(pieces))


def test_a_trays_wall_liner_is_said_to_be_slit_where_its_corners_part():
    # the right wall's corners are hinged to the end walls, not to it: a
    # liner along the wall is parted from what lies on each corner
    lay = _tray((rect(2, 2, 28, 158), 1.0, 1), (rect(132, 2, 158, 158), 1.0,
                                                1))
    slits = _notes(lay, "slit")
    assert [(n.sheet, n.fold, n.a, n.at, n.other) for n in slits] == [
        (2, 2, pytest.approx(26.0, abs=1e-9), (145.0, 30.0), (0,)),
        (2, 3, pytest.approx(26.0, abs=1e-9), (145.0, 130.0), (0,))]
    # where the corner folds into the wall the liner is drawn through
    # itself, said under the slit; the left liner, whose corners are
    # hinged to its wall, is not parted at all
    # (0.57 mm of it at rest, the rest on the way)
    assert [(round(n.b, 4), round(n.rest, 4)) for n in slits] == [
        (6.9317, 0.5708), (4.8657, 0.5708)]
    assert witnesses(lay, 9, 3) == []


def test_two_piles_that_pass_through_each_other_inside_a_step_are_said():
    # a channel: both walls stand up, one after the other; the second
    # wall's pile sweeps through the first wall's on the way and is clear
    # of it when the wall stands
    lay = stack(rect(0, 0, 240, 60),
                [fold(80, 0, 80, 60, 90.0, order=1),
                 fold(160, 0, 160, 60, 90.0, order=2, label="B")],
                [(rect(25, 10, 45, 50), 60.0, 1),
                 (rect(165, 10, 185, 50), 40.0, 1)], base=1)
    # 6.82 mm into each other on the way, first seen half way through the
    # second step; nothing at rest
    clash, = _notes(lay, "clash", "limit")
    assert (clash.kind, clash.sheet, clash.other, clash.b, clash.rest) == (
        "clash", 1, (2,), 1.5, 0.0)
    assert clash.a == pytest.approx(6.8231, abs=1e-4)
    for t in (0.0, 1.0, 2.0):
        assert not overlaps(lay.solid(lay.fractions(t)))
    assert witnesses(lay, 9, 3) == []


def _three_77(trial):
    rng = random.Random(77)
    for _k in range(trial + 1):
        out, folds, sheets, t, base = random_stack(rng, "three")
    return fs.fold_piece(out, folds, thickness=t, sheets=sheets, base=base)


def test_a_later_fold_moves_clear_of_a_pile_folded_in_over_a_lower_rider():
    # random strip in three, trial 9 of seed 77: the first flap's pieces
    # are folded in over the middle panel and run on into the second
    # fold's bend, along a stretch where only a low piece rides round it.
    # Measured against the tallest piece riding round anywhere, the fold
    # moved 21.3 mm and left them 1.91 mm in its bend; it moves 25.08
    lay = _three_77(9)
    assert lay.shift == pytest.approx([0.0, 25.0796], abs=1e-4)
    assert not lay.stuck[1] and not lay.warnings
    # (the 0.036 mm left is what a fold moved to its place may keep: no
    # finding)
    assert not _notes(lay, "clash", "limit", "landing", "stuck")
    assert witnesses(lay, 9, 3) == []


def test_a_fold_stopped_for_want_of_room_closes_clean_further_on():
    # trial 23: where the pile first drove it, 26.07 mm off its line, the
    # second fold's bend -- wide for the pile riding in it -- would not
    # fit before the edge of the last panel. A little further on the pile
    # no longer rides in the bend, which bends tighter and fits: it
    # closes clean there
    lay = _three_77(23)
    assert lay.stuck == [False, False] and not lay.warnings
    assert lay.shift == pytest.approx([0.0, 27.7933], abs=1e-4)
    assert lay.radius == pytest.approx([6.1, 6.5], abs=1e-9)
    assert not _notes(lay, "stuck", "clash", "limit", "landing")
    assert witnesses(lay, 9, 3) == []


def test_a_fold_with_no_room_to_move_clear_says_so_and_what_is_in_the_way():
    # a strip in three whose first flap, with a patch on it, folds in
    # over the middle and reaches 10 mm past the second fold; the last
    # panel is 10 mm wide, too narrow for the bend wherever it forms
    lay = stack(rect(0, 0, 160, 60), [fold(80, 0, 80, 60, order=1),
                                      fold(150, 0, 150, 60, order=2,
                                           label="B")],
                [(rect(5, 5, 75, 55), 1.0, 1)], base=1)
    assert lay.stuck == [False, True]
    stuck, = _notes(lay, "stuck")
    assert (stuck.fold, round(stuck.a, 4), round(stuck.b, 4),
            stuck.other) == (1, 7.854, 13.927, (0, 1))
    assert lay.warnings == [
        "What is folded inside fold B reaches 13.93 mm into its bend, and "
        "the fold cannot move further out of its way: its bend would not "
        "fit before the edge of panel 3."]
    assert witnesses(lay, 9, 3) == []


def _trifold(pockets, t=0.8):
    # a 270 by 90 mm strip, 1.4 mm, its right flap folded in over the
    # middle and then the two over the left; a pocket on the front of
    # any of its three panels
    sheets = [sh.Sheet(rect(x - 40, -40, x + 40, 10), t, name=name)
              for name, x in (("L", -90), ("M", 0), ("R", 90))
              if name in pockets]
    return fs.fold_piece(rect(-135, -45, 135, 45),
                         [fold(-45, -45, -45, 45, order=2),
                          fold(45, -45, 45, 45, order=1, label="B")],
                         thickness=1.4, sheets=sheets)


@pytest.mark.parametrize("pockets, shift", [
    ("", 2.1991), ("L", 2.8274), ("M", 2.8274), ("R", 2.8274),
    ("LR", 3.4558), ("LMR", 4.0841)])
def test_a_fold_moves_on_while_each_move_brings_the_pile_clearer(pockets,
                                                                  shift):
    # the pocket on the left panel comes into the second fold's bend as
    # the fold moves and lines its round: each move clears the folded
    # flap by less than it is long. Given up after one, the fold stuck
    # 2.53 mm into the flap with all three pockets on
    lay = _trifold(pockets)
    assert lay.shift == pytest.approx([shift, 0.0], abs=1e-4)
    assert lay.stuck == [False, False] and not lay.warnings
    assert not _notes(lay, "clash", "limit", "landing", "stuck")
    assert witnesses(lay, 9, 3) == []


def test_a_hair_a_moved_fold_is_let_keep_is_no_finding():
    # with 0.2 mm pockets the strip is left 0.004 mm into itself where
    # its fold forms off its line: less than a moved fold may keep
    lay = _trifold("LMR", 0.2)
    assert lay.shift == pytest.approx([2.0704, 0.0], abs=1e-4)
    deep = max(d for d, _a, _b in overlaps(lay.solid([1.0, 1.0])))
    assert 0.0 < deep < 0.05
    assert not _notes(lay, "clash", "limit", "landing")


# -- one piece folds as before ------------------------------------------------

@pytest.mark.parametrize("piece", ["trifold", "tray", "bifold"])
def test_a_stack_whose_only_sheet_has_no_leather_folds_as_its_piece_alone(
        piece):
    if piece == "trifold":
        ring = rect(0, 0, 240, 100)
        folds = [fold(80, 0, 80, 100, order=1),
                 fold(160, 0, 160, 100, -1, order=2, label="B")]
    elif piece == "tray":
        ring = rect(0, 0, 160, 160)
        folds = [fold(30, 0, 30, 160, 90.0), fold(130, 0, 130, 160, 90.0,
                                                   label="B"),
                 fold(0, 30, 160, 30, 90.0, label="C"),
                 fold(0, 130, 160, 130, 90.0, label="D")]
    else:
        ring, folds, _sheets, _t, _holes = generated(shell_t=1.6,
                                                      inner_t=0.8)
    alone = fs.fold_piece(ring, folds, thickness=1.6)
    lay = fs.fold_piece(ring, folds, thickness=1.6, sheets=[
        sh.Sheet([(10, 10), (20, 10), (30, 10)], 0.8)])
    assert lay.radius == alone.radius and lay.used == alone.used
    assert lay.wrap == alone.wrap and lay.shift == alone.shift
    assert lay.sheet_cells == []
    for t in (0.0, 0.5 * alone.n_steps, float(alone.n_steps)):
        one = alone.solid(alone.fractions(t))
        two = lay.solid(lay.fractions(t))
        assert [c.tag for c in one] == [c.tag for c in two]
        assert [sorted(c.verts) for c in one] == \
            [sorted(c.verts) for c in two]


def test_a_crease_has_no_face_on_its_inside_and_the_cell_stays_convex():
    lay = fs.fold_piece(rect(0, 0, 160, 100), [fold(80, 0, 80, 100)])
    cells = lay.solid([1.0])
    strips = [c for c in cells if c.tag[0] == "bend"]
    # the inside face of every strip is the line the bend goes round:
    # left out, not a sliver of a plane made of rounding
    assert len(strips) == 16
    assert all(sum(f.kind == bsp.FRONT for f in c.faces) == 0
               for c in strips)
    assert inside_out(cells) <= FLAT_ENOUGH


# -- seeded random stacks -----------------------------------------------------

@pytest.mark.parametrize("family, seed", [
    ("flat", 1), ("shut", 2), ("shut", 12), ("zigzag", 3), ("open", 4),
    ("open", 14), ("tee", 5), ("tee", 15), ("three", 6), ("three", 16),
    ("ell", 7), ("ell", 17), ("wings", 8), ("wings", 18)])
def test_random_stacks_are_whole_at_five_moments_of_their_folding(family,
                                                                  seed):
    # 1 to 7 pieces each, on both faces, turned to any angle, rounded,
    # L-shaped, notched and standing past the edge; folded flat shut, in
    # a zigzag, at 30 to 160 degrees, on a T-shaped piece, as a strip in
    # three whose flaps reach the other bend, on an L-shaped piece across
    # both arms, or on a T-shaped piece under a liner wider than its flap
    # with pieces on its wings. Looked at at five moments, and at three
    # more inside each step: whatever is drawn into anything is said
    rng = random.Random(seed)
    for trial in range(8):
        out, folds, sheets, t, base = random_stack(rng, family)
        lay = fs.fold_piece(out, folds, thickness=t, sheets=sheets,
                            base=base)
        assert witnesses(lay, 5, 3) == [], (family, seed, trial)


@pytest.mark.parametrize("family", ["tee", "zigzag", "four", "three"])
def test_ordinary_stacks_are_said_to_be_drawn_through_on_the_way_too(family):
    # T-shaped pieces with pieces on the body and the flap; rectangles
    # with two and with three parallel folds; strips in three with their
    # pockets. Looked at at 41 moments a step: nothing is drawn into
    # anything deeper than 0.05 mm without a note naming it at least nine
    # tenths as deep -- on the way as at rest
    rng = random.Random(31)
    for trial in range(3):
        out, folds, sheets, t, base = random_stack(rng, family)
        lay = fs.fold_piece(out, folds, thickness=t, sheets=sheets,
                            base=base)
        assert witnesses(lay, 1 + 40 * max(1, lay.n_steps)) == [], (
            family, trial)


# -- controls: each rule switched off makes a witness fail --------------------

def _pocket_in_a_corner():
    # a 0.8 mm pocket reaching into the bend of a right angle from one
    # side: it is inside the bend, but has nothing across from it
    return stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100, 90.0)],
                 [(rect(20, 10, 99.8, 90), 0.8, 1)], t=1.6)


def test_without_ride_a_pocket_in_a_corner_is_turned_inside_out(
        monkeypatch):
    lay = _pocket_in_a_corner()
    assert lay.radius == pytest.approx([0.8], abs=1e-9)
    assert witnesses(lay, 5) == []
    monkeypatch.setattr(sh, "ride", lambda layout: False)
    lay = _pocket_in_a_corner()
    assert lay.radius == [0.0]
    assert inside_out(lay.solid([0.75])) > 1.0


def test_without_meet_the_bifolds_pockets_go_into_each_other(monkeypatch):
    monkeypatch.setattr(fs.Layout, "_clear_bodies",
                        lambda self, h, frames, need: need)
    lay = bifold(_narrower, shell_t=1.6, inner_t=0.8)
    # the liner alone asks for its own 0.8 mm
    assert lay.radius == pytest.approx([0.8], abs=1e-9)
    deep = max(d for d, _a, _b in overlaps(lay.solid([1.0])))
    assert deep == pytest.approx(0.8, abs=1e-6)


def test_without_corner_piles_either_side_of_a_right_angle_meet(
        monkeypatch):
    monkeypatch.setattr(sh, "corner_room",
                        lambda layout, hinges, theta, asked: (asked, ()))
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100, 90.0)],
                [(rect(40, 20, 99, 80), 3.0, 1),
                 (rect(101, 20, 160, 80), 3.0, 1)])
    assert lay.radius == [0.0]
    deep = max(d for d, _a, _b in overlaps(lay.solid([0.75])))
    assert deep == pytest.approx(2.167, abs=1e-3)


def _strap_at_60():
    ring = turned(rounded(60, 30, 140, 70, 3.0), 60.0, 100, 50)
    return stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100, radius=3.0)],
                 [(ring, 1.0, 1)], t=1.6)


def test_without_the_extra_cut_a_strap_aslant_a_bend_has_a_cell_inside_out(
        monkeypatch):
    assert witnesses(_strap_at_60(), 5) == []
    monkeypatch.setattr(fs, "_rider_parts",
                        lambda pts, flags, h: pc.convex_parts(pts, flags))
    assert inside_out(_strap_at_60().solid([0.75])) > 0.01


def _lining_far_off():
    # a piece drawn far from the drawing's origin, turned, with a lining
    # whose inside face is a crease when shut
    o, turn = (304.7078505604395, -1849.535376028203), 129.08664706831746
    c = (o[0] + 100, o[1] + 50)
    ring = turned(rect(o[0], o[1], o[0] + 200, o[1] + 100), turn, *c)
    a, b = turned([(o[0] + 100, o[1]), (o[0] + 100, o[1] + 100)], turn, *c)
    lining = turned(rect(o[0] + 20, o[1] + 10, o[0] + 180, o[1] + 90),
                    turn, *c)
    return stack(ring, [fs.FoldLine(a, b, 180.0, 1, label="A")],
                 [(lining, 1.2, 1)], t=1.6)


def test_without_the_crease_on_its_axis_a_lining_is_turned_inside_out(
        monkeypatch):
    assert inside_out(_lining_far_off().solid([1.0])) <= FLAT_ENOUGH
    monkeypatch.setattr(fs, "_CREASE", -1.0)
    assert inside_out(_lining_far_off().solid([1.0])) > 1.0


# -- stitch holes through the stack -----------------------------------------

def _row(x0, x1, y, step=5.0, dx=0.0):
    n = int(round((x1 - x0) / step))
    return [(x0 + k * step + dx, y, 1.0) for k in range(n + 1)]


def test_pieces_the_fold_brings_face_to_face_have_their_holes_compared():
    def patches(off):
        return [sh.Sheet(rect(10, 10, 90, 90), 1.0, holes=_row(20, 80, 20),
                         name="left"),
                sh.Sheet(rect(110, 10, 190, 90), 1.0,
                         holes=_row(120, 180, 20, dx=off), name="right")]
    lay = fs.fold_piece(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                        sheets=patches(0.0))
    faced = [f for f in lay.lineup().findings if not f.laid]
    assert [(f.a, f.b, f.names, f.panels) for f in faced] == [
        (1, 2, ("left", "right"), (1, 2))]
    assert (faced[0].holes, faced[0].met, faced[0].off) == (26, 26, 0)
    lay = fs.fold_piece(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                        sheets=patches(1.5))
    f, = [f for f in lay.lineup().findings if not f.laid]
    assert (f.off, f.worst) == (26, pytest.approx(1.5, abs=1e-9))


def test_the_bifolds_shell_halves_are_not_compared_through_its_pockets():
    lay = bifold(shell_t=1.6, inner_t=0.8)
    check = lay.lineup()
    assert not [f for f in check.findings if not f.laid]
    # (alone, the shell's two halves line up: 140 holes)
    alone = fs.fold_piece(lay.outline, lay.folds, thickness=1.6,
                          holes=lay.holes[:lay._own_holes])
    assert [(f.a, f.b, f.met) for f in alone.lineup().findings] == [
        (1, 2, 140)]
    # the shell's holes come through the liner and the pockets
    assert sum(len(v) for v in check.blanks.values()) == 438


def test_a_lining_punched_to_meet_when_shut_is_read_shut():
    # a lining round the inside of a fold in 2 mm leather, 1 mm thick:
    # pi times 1.5 mm shorter round it. Punched where the shell is, it
    # meets flat; punched half that nearer the fold each side, shut
    holes = [(x, 95.0, 1.0) for x in range(10, 91, 5)] + \
        [(x, 95.0, 1.0) for x in range(110, 191, 5)]
    over = math.pi * 1.5

    def lining(pull):
        return [sh.Sheet(rect(0, 0, 200, 98), 1.0, name="lining", holes=[
            (x + (pull if x < 100 else -pull), y, d) for x, y, d in holes])]

    # (punched where the shell is, its holes read shut where it is held on
    # one half are 4.71 mm off on the other -- and so within 0.29 mm of
    # the next hole of the shell, 5 mm on: 33 meet)
    for pull, want in ((0.0, (34, 33, False)), (0.5 * over, (0, 34, True))):
        lay = fs.fold_piece(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                            holes=holes, sheets=lining(pull))
        cross, = _notes(lay, "cross")
        assert cross.a == pytest.approx(over, abs=1e-9)
        assert lay.lineup().readings == {1: want}


def test_two_linings_punched_for_shut_are_read_shut_together():
    # two 1 mm linings, one on the other, inside a 2 mm piece folded flat:
    # pi x 1.5 and pi x 2.5 mm too long round the inside. Each is punched
    # half that nearer the fold each side; read shut together, every
    # hole of the three meets
    holes = [(x, 95.0, 1.0) for x in range(10, 91, 5)] + \
        [(x, 95.0, 1.0) for x in range(110, 191, 5)]

    def lining(name, over):
        return sh.Sheet(rect(0, 0, 200, 98), 1.0, name=name, holes=[
            (x + (0.5 * over if x < 100 else -0.5 * over), y, d)
            for x, y, d in holes])

    lay = fs.fold_piece(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                        holes=holes, sheets=[lining("one", math.pi * 1.5),
                                             lining("two", math.pi * 2.5)])
    check = lay.lineup()
    assert check.readings == {1: (0, 34, True), 2: (0, 34, True)}
    assert len(check.holes(fs.MEETS)) == 102


def _strip_lining(wa, wb, angle_a, punch):
    # a strip in three, 1.6 mm, the middle panel still; a 0.8 mm lining
    # on its front round both folds, its holes punched by ``punch``
    holes = [(x, 95.0, 1.0) for x in range(5, 256, 5)
             if abs(x - 80) > 8 and abs(x - 180) > 8]
    lining = sh.Sheet(rect(0, 0, 260, 98), 0.8, name="lining",
                      holes=[(punch(x), y, d) for x, y, d in holes])
    return fs.fold_piece(rect(0, 0, 260, 100),
                         [fold(80, 0, 80, 100, angle_a, wa, order=2),
                          fold(180, 0, 180, 100, 180.0, wb, order=1,
                               label="B")],
                         thickness=1.6, base=1, holes=holes, sheets=[lining])


OV = math.pi * 1.2      # what a bend flat takes of the lining, more or less


@pytest.mark.parametrize("name, wa, wb, angle_a, at_middle, at_still", [
    # inside both folds: held at the middle panel or so that its ends
    # come equally far, each flap lands OV further out
    ("C", 1, 1, 180.0, (OV, -OV), (OV, -OV)),
    # inside the first fold, outside the second: held so that its ends
    # come equally far, its middle lands OV towards the first fold; held
    # at the still middle panel, both flaps land OV away from it
    ("Z", 1, -1, 180.0, (0.0, 0.0, -OV), (OV, OV, 0.0)),
    # a right angle and a flat fold: the first takes half as much
    ("C at 90", 1, 1, 90.0, (0.5 * OV, -OV), (0.5 * OV, -OV))])
def test_a_lining_round_two_folds_is_read_shut_where_it_lands(
        name, wa, wb, angle_a, at_middle, at_still):
    for pull in (at_middle, at_still):
        left, right = pull[0], pull[1]
        mid = pull[2] if len(pull) > 2 else 0.0

        def punch(x):
            return x + (left if x < 80 else right if x > 180 else mid)

        lay = _strip_lining(wa, wb, angle_a, punch)
        check = lay.lineup()
        assert check.readings[1][1:] == (45, True), (name, pull)
        own = lay._own_holes
        assert all(check.state.get(own + i) == fs.MEETS
                   for i in range(45)), (name, pull)


def test_linings_are_read_flat_or_shut_whole_whatever_their_order():
    holes = [(x, 95.0, 1.0) for x in range(10, 91, 5)] + \
        [(x, 95.0, 1.0) for x in range(110, 191, 5)]

    def lining(name, pull):
        return sh.Sheet(rect(0, 0, 200, 98), 1.0, name=name, holes=[
            (x + (pull if x < 100 else -pull), y, d) for x, y, d in holes])

    def read(*pulls):
        lay = fs.fold_piece(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                            holes=holes, sheets=[
                                lining(f"l{k}", p)
                                for k, p in enumerate(pulls, 1)])
        return lay.lineup().readings

    # both punched as they lie: read flat, every hole of each meets
    assert read(0.0, 0.0) == {1: (34, 0, False), 2: (34, 0, False)}
    # both punched for shut: read shut
    assert read(0.75 * math.pi, 1.25 * math.pi) == {
        1: (0, 34, True), 2: (0, 34, True)}
    # (the third number is the reading shown, the same for every piece)
    for pulls in ((0.0, 1.25 * math.pi), (0.75 * math.pi, 0.0)):
        got = read(*pulls)
        assert len({shown for _f, _s, shown in got.values()}) == 1



@pytest.mark.parametrize("turn", [0.0, 30.0, 90.0])
def test_the_size_and_the_leather_shut_go_by_the_stack_not_the_drawing(turn):
    # a 200 by 90 mm shell, 1.6 mm, folded in the middle, with a square
    # cornered 0.8 mm liner stopping 2 mm short of its top edge, drawn
    # turned on the page
    ring = turned(rect(0, 0, 200, 90), turn, 100, 45)
    a, b = turned([(100, 0), (100, 90)], turn, 100, 45)
    liner = turned(rect(0, 0, 200, 88), turn, 100, 45)
    lay = fs.fold_piece(ring, [fs.FoldLine(a, b, 180.0, 1, label="A")],
                        thickness=1.6, sheets=[sh.Sheet(liner, 0.8)])
    size, = _notes(lay, "size")
    assert (size.a, size.b) == pytest.approx((99.8867, 90.0), abs=1e-4)
    # along the top edge a needle goes through the shell twice, only
    leather, = _notes(lay, "leather")
    assert (leather.a, leather.b) == pytest.approx((4.8, 3.2), abs=1e-9)



@pytest.mark.parametrize("angle, most", [
    (175.0, 4.8092), (177.0, 4.8033), (179.0, 4.8004)])
def test_a_needle_meets_a_fold_not_quite_shut_right_through_its_slant(
        angle, most):
    # a 200 by 90 mm shell, 1.6 mm, folded in the middle not quite shut,
    # with a 0.8 mm liner stopping 2 mm short of its top edge: the folded
    # half lies aslant over the still half, and a needle meets it, and
    # its liner, for more than their thickness where it is deepest --
    # 2.4 mm lying level and 2.4 / cos(180 - angle) aslant
    lay = stack(rect(0, 0, 200, 90), [fold(100, 0, 100, 90, angle)],
                [(rect(0, 0, 200, 88), 0.8, 1)], t=1.6)
    leather, = _notes(lay, "leather")
    assert (round(leather.a, 4), round(leather.b, 4)) == (most, 1.6)


@pytest.mark.parametrize("angle", [90.0, 120.0, 150.0, 170.0, 174.9])
def test_the_leather_a_needle_meets_is_told_only_of_a_stack_that_is_shut(
        angle):
    # the same shell and liner with the fold standing open: a needle
    # square to the still half says nothing of the stitching, and no
    # figure is given, nor looked for -- the size is told as ever
    lay = stack(rect(0, 0, 200, 90), [fold(100, 0, 100, 90, angle)],
                [(rect(0, 0, 200, 88), 0.8, 1)], t=1.6)
    assert _notes(lay, "leather") == []
    assert [n.kind for n in _notes(lay, "size", "shut")] == ["size", "shut"]


def test_one_fold_of_two_standing_open_and_no_needle_figure_is_given():
    # a strip in three, the first fold shut and the second at 90 degrees
    folds = [fold(140, 0, 140, 60, 180.0, 1, order=1),
             fold(220, 0, 220, 60, 90.0, -1, order=2, label="B")]
    lay = stack(rect(0, 0, 300, 60), folds, [(rect(0, 0, 300, 60), 0.8, 1)],
                t=1.6, base=0)
    assert _notes(lay, "leather") == []
    # ... and a fold that is not made at all does not count as open: the
    # 160 mm folded over cover all of the 140 mm lying still
    folds[1] = dataclasses.replace(folds[1], order=0, angle=0.0)
    lay = stack(rect(0, 0, 300, 60), folds, [(rect(0, 0, 300, 60), 0.8, 1)],
                t=1.6, base=0)
    leather, = _notes(lay, "leather")
    assert (leather.a, leather.b) == pytest.approx((4.8, 4.8), abs=1e-9)


def _needle(lay):
    """A witness of its own for the leather a needle meets in the stack
    shut: ``needle(x, y)`` adds up what the upright line through a point
    meets of every cell, between the cell's own faces, leaving out the
    windows of the cells that lie level -- or None off the still panel's
    flat leather. With it, the boxes round that leather."""
    at = lay.fractions(float(lay.n_steps))
    frames = lay.frames(at)
    cells, still = [], []
    for c, cell in lay._posed(at):
        xs = [v[0] for v in cell.verts]
        ys = [v[1] for v in cell.verts]
        planes = [f.plane for f in cell.faces]
        level = all(abs(pl[2]) < 1e-9 or abs(pl[2]) > 1 - 1e-9
                    for pl in planes)
        windows = []
        if level and c.hinge is None:
            windows = [[fs.place(frames[c.panel], q)[:2] for q in ring]
                       for ring in c.polys]
        cells.append((min(xs), min(ys), max(xs), max(ys), planes, windows))
        if c.sheet == 0 and c.panel == lay.base and c.hinge is None:
            still.append((min(xs), min(ys), max(xs), max(ys), planes))

    def inside(planes, x, y):
        return all(pl[0] * x + pl[1] * y <= pl[3] - 1e-6 for pl in planes
                   if abs(pl[2]) < 1e-9)

    def needle(x, y):
        if not any(inside(planes, x, y) for *_b, planes in still):
            return None
        total = 0.0
        for x0, y0, x1, y1, planes, windows in cells:
            if x < x0 or x > x1 or y < y0 or y > y1:
                continue
            lo, hi = -math.inf, math.inf
            for nx, ny, nz, d in planes:
                if abs(nz) < 1e-12:
                    if nx * x + ny * y > d + 1e-9:
                        hi = -math.inf
                elif nz > 0.0:
                    hi = min(hi, (d - nx * x - ny * y) / nz)
                else:
                    lo = max(lo, (d - nx * x - ny * y) / nz)
            if hi > lo and not any(pc.inside((x, y), w) for w in windows):
                total += hi - lo
        return total

    return needle, [b[:4] for b in still]


def _strip_in_a_z():
    # a strip 300 by 60 mm, 1.6 mm, with a 0.8 mm liner all over it,
    # folded shut at 140 mm and back again at 220 mm: the round of the
    # second fold lies over the still panel, 60 mm in from its edge
    return stack(rect(0, 0, 300, 60),
                 [fold(140, 0, 140, 60, 180.0, 1, order=1),
                  fold(220, 0, 220, 60, 180.0, -1, order=2, label="B")],
                 [(rect(0, 0, 300, 60), 0.8, 1)], t=1.6, base=0)


def _wallet_nearly_shut():
    outline, folds, sheets, t, holes = generated(shell_t=1.6, inner_t=0.8)
    return fs.fold_piece(outline, [dataclasses.replace(f, angle=177.0)
                                   for f in folds],
                         thickness=t, holes=holes, sheets=sheets)


def _tee_under_a_round():
    # the fourteenth random T-shaped piece of seed 11: 1 mm leather with
    # one piece on it, its flap folded shut on a wide round that comes
    # to lie over the still panel -- 3.8053 mm at the most, by the
    # corners of the pieces alone
    rng = random.Random(11)
    for _k in range(14):
        outline, folds, sheets, t, base = random_stack(rng, "tee")
    return fs.fold_piece(outline, folds, thickness=t, sheets=sheets,
                         base=base)


@pytest.mark.parametrize("build, most, least", [
    (_strip_in_a_z, 7.2, 2.4),
    (_wallet_nearly_shut, 11.2077, 1.6),
    (_tee_under_a_round, 3.8254, 1.0),
    (lambda: _windowed("liner"), 5.2, 4.2)])
def test_no_point_of_the_still_panel_has_more_or_less_leather_than_told(
        build, most, least):
    # the most and the least are of every place: no point of 4,000 taken
    # at random over the still panel's flat leather, and 1,600 more just
    # inside its edges, has more leather under a needle than the most
    # told, or less than the least, and the points come to within a
    # hundredth of a millimetre of both -- where leather lies aslant too
    # (a fold not quite shut, the round of a bend), which was once only
    # sampled at the corners of the pieces
    lay = build()
    leather, = _notes(lay, "leather")
    assert (leather.a, leather.b) == pytest.approx((most, least), abs=1e-4)
    needle, boxes = _needle(lay)
    rng = random.Random(3)
    found = []
    for x0, y0, x1, y1 in boxes:
        spots = [(rng.uniform(x0, x1), rng.uniform(y0, y1))
                 for _k in range(4000 // len(boxes))]
        for _k in range(400 // len(boxes)):
            x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
            near = rng.uniform(0.001, 0.05)
            spots += [(x0 + near, y), (x1 - near, y), (x, y0 + near),
                      (x, y1 - near)]
        for x, y in spots:
            mm = needle(x, y)
            if mm is not None:
                found.append(mm)
    assert len(found) > 4000
    assert max(found) <= leather.a + 1e-7
    assert min(found) >= leather.b - 1e-7
    assert max(found) == pytest.approx(leather.a, abs=0.01)
    assert min(found) == pytest.approx(leather.b, abs=0.01)


def test_a_slab_of_leather_tipped_up_is_met_as_a_plane_says_piece_by_piece():
    # a 20 by 10 mm slab, 2 mm thick, tipped 30 degrees about its 10 mm
    # edge: seen from above, a needle goes through 2 / cos 30 of it where
    # it meets both its faces, and runs out to nothing across the 1 mm
    # (2 sin 30) at each end, where it leaves through an end
    lay = fs.fold_piece(rect(0, 0, 40, 10), [fold(20, 0, 20, 10, 150.0)],
                        thickness=2.0, radius=0.0)
    cell, = [cell for c, cell in lay._posed(lay.fractions(1.0))
             if c.panel != lay.base and c.hinge is None]
    pieces = sh._aslant(cell)
    assert len(pieces) == 3
    full = max(pieces, key=lambda got: abs(pc.area(got[0])))
    upright = 2.0 / math.cos(math.radians(30))
    assert full[1] == pytest.approx((0.0, 0.0, upright), abs=1e-9)
    volume = 0.0
    for ring, plane in pieces:
        x0, y0, x1, y1 = pc.bounds(ring)
        assert y1 - y0 == pytest.approx(10.0, abs=1e-9)
        volume += abs(pc.area(ring)) * sh._at(plane, pc.centroid(ring))
        if ring is not full[0]:
            assert x1 - x0 == pytest.approx(1.0, abs=1e-9)
            ends = sorted(sh._at(plane, q) for q in ring)
            assert ends[0] == pytest.approx(0.0, abs=1e-9)
            assert ends[-1] == pytest.approx(upright, abs=1e-9)
    # ... and what the needle meets, added up over all of it, is the
    # slab: its length (what it covers, less the 1 mm, by cos 30), its
    # 10 mm and its 2 mm
    xs = [v[0] for v in cell.verts]
    long = (max(xs) - min(xs) - 1.0) / math.cos(math.radians(30))
    assert volume == pytest.approx(long * 10.0 * 2.0, abs=1e-6)


def test_leather_riding_round_a_bend_past_its_hinge_end_moves_its_fold():
    # an L-shaped piece folded 90 degrees across its upright arm (A) and
    # then shut across both arms (B), with three 1 mm pieces on its front
    # standing out past its edges, one of them turned: what rides round
    # B's bend past the end of its hinge, over nothing, is piled in its
    # round there too, and B moves 68.33 mm to fold clear
    lay = fs.fold_piece(
        [(0, 0), (203.8, 0), (203.8, 74.1), (87.7, 74.1), (87.7, 160.4),
         (0, 160.4)],
        [fs.FoldLine((0, 129.3), (203.8, 129.3), 90.0, -1, order=1,
                     label="A"),
         fs.FoldLine((129.5, 0), (129.5, 160.4), 180.0, 1, order=2,
                     label="B")],
        thickness=1.6, sheets=[
            sh.Sheet([(92.2, -5.6), (206.5, -5.6), (206.5, 46.4),
                      (154.9, 46.4), (154.9, 153.3), (92.2, 153.3)], 1.0),
            sh.Sheet([(12.1, 94.8), (137.0, 1.6), (196.2, 80.9),
                      (71.2, 174.1)], 1.0),
            sh.Sheet(rect(39.0, 27.8, 205.7, 144.9), 1.0)])
    assert lay.shift == pytest.approx([0.0, 68.33], abs=0.01)
    assert lay.stuck == [False, False]
    assert [n for n in lay.notes if n.kind in ("clash", "limit",
                                               "landing")] == []
    assert witnesses(lay, 9, 3) == []


def _seed_493():
    # the checker's generated stack 493: an L-shaped shell, 1.6 mm, shut
    # across its corner (A, 175 degrees) and then turned up across its arm
    # (B), with a 3 mm piece and a 0.6 mm liner on its back
    return stack(
        [(132.5, 204.7), (-29.1, 94.5), (31.9, 4.9), (130.5, 72.2),
         (161.7, 26.3), (224.8, 69.4)],
        [fold(196.5, 110.9, 34.8, 0.7, 175, -1, radius=1, order=0),
         fold(37.7, 140.1, 98.8, 50.6, 90, -1, radius=0, order=2,
              label="B")],
        [([(119.2, 105.1), (111.6, 99.9), (112, 99), (112, 98), (111.6, 97.1),
           (110.9, 96.4), (110, 96), (109, 96), (108.1, 96.4), (107.4, 97.1),
           (99.8, 91.9), (118.5, 64.5), (137.9, 77.7)], 3.0, -1),
         ([(8.7, 130.2), (67.8, -34.2), (166.4, 1.2), (107.3, 165.7)], 0.6,
          -1)], t=1.6)


def test_a_fold_whose_first_move_brings_nothing_clear_is_tried_further_on():
    # fold A's first move, 7.21 mm, leaves the 3 mm piece as far in its
    # bend as before (it rides on the panel that moves with the fold), and
    # A was put back on its line and said to be stuck. Further on the
    # piece no longer rides in the bend: A forms clean 38.31 mm along
    lay = _seed_493()
    assert lay.shift == pytest.approx([38.3125, 0.0], abs=1e-3)
    assert lay.stuck == [False, False] and not lay.warnings
    assert not _notes(lay, "stuck", "clash", "limit", "landing")
    assert witnesses(lay, 9, 3) == []


def _seed_392():
    # the checker's generated stack 392: a shell shut twice, B on A's
    # panel, with six pieces on it
    return stack(
        [(0, 0), (164.4, 0), (164.4, 98.3), (0, 98.3)],
        [fold(61.3, 0, 61.3, 98.3, 180, -1, radius=-1, order=2),
         fold(80.9, 0, 80.9, 98.3, 180, -1, radius=2.5, order=1,
              label="B")],
        [(rect(44.8, 46.2, 76.2, 65), 0.8, 1),
         (rect(88.9, 36, 132.4, 95.1), 0.8, 1),
         ([(98, 28.7), (97.8, 30), (97.3, 31.2), (96.5, 32.3), (95.5, 33.1),
           (94.3, 33.6), (93, 33.7), (78.8, 33.7), (77.5, 33.6), (76.3, 33.1),
           (75.3, 32.3), (74.5, 31.2), (74, 30), (73.8, 28.7), (73.8, 14.1),
           (74, 12.8), (74.5, 11.6), (75.3, 10.5), (76.3, 9.7), (77.5, 9.2),
           (78.8, 9), (93, 9), (94.3, 9.2), (95.5, 9.7), (96.5, 10.5),
           (97.3, 11.6), (97.8, 12.8), (98, 14.1)], 1.2, -1),
         (rect(47.6, 52.9, 58.8, 63.4), 0.8, 1),
         (rect(42.4, 43.6, 68.5, 103.3), 2.0, 1),
         ([(31.7, 88.5), (31.5, 90), (30.9, 91.4), (30, 92.6), (28.8, 93.5),
           (27.4, 94), (25.9, 94.2), (-8, 94.2), (-9.5, 94), (-10.9, 93.5),
           (-12.1, 92.6), (-13, 91.4), (-13.6, 90), (-13.8, 88.5),
           (-13.8, 81.4), (-13.6, 79.9), (-13, 78.5), (-12.1, 77.3),
           (-10.9, 76.4), (-9.5, 75.8), (-8, 75.6), (25.9, 75.6),
           (27.4, 75.8), (28.8, 76.4), (30, 77.3), (30.9, 78.5),
           (31.5, 79.9), (31.7, 81.4)], 1.6, -1)], t=1.6)


def _seed_476():
    # the checker's generated stack 476: a 2.5 mm strip shut twice, B on
    # the far panel, with six pieces on it
    return stack(
        [(196, 63.5), (195.9, 64.3), (195.6, 65), (195.1, 65.7),
         (194.5, 66.1), (193.8, 66.4), (193, 66.5), (3, 66.5), (2.2, 66.4),
         (1.5, 66.1), (0.9, 65.7), (0.4, 65), (0.1, 64.3), (0, 63.5), (0, 3),
         (0.1, 2.2), (0.4, 1.5), (0.9, 0.9), (1.5, 0.4), (2.2, 0.1), (3, 0),
         (193, 0), (193.8, 0.1), (194.5, 0.4), (195.1, 0.9), (195.6, 1.5),
         (195.9, 2.2), (196, 3)],
        [fold(111.1, 0, 111.1, 66.5, 180, -1, radius=-1, order=1),
         fold(145.8, 0, 145.8, 66.5, 180, -1, radius=-1, order=2,
              label="B")],
        [([(177.2, 30), (177, 31.4), (176.5, 32.7), (175.6, 33.9),
           (174.4, 34.8), (173.1, 35.3), (171.7, 35.5), (34.9, 35.5),
           (33.4, 35.3), (32.1, 34.8), (30.9, 33.9), (30, 32.7),
           (29.5, 31.4), (29.3, 30), (29.3, -0.2), (29.5, -1.7), (30, -3),
           (30.9, -4.2), (32.1, -5.1), (33.4, -5.6), (34.9, -5.8),
           (171.7, -5.8), (173.1, -5.6), (174.4, -5.1), (175.6, -4.2),
           (176.5, -3), (177, -1.7), (177.2, -0.2)], 0.8, 1),
         (rect(118.4, 9.8, 140.5, 69.3), 2.0, -1),
         ([(205.7, 63.6), (205.5, 65.4), (204.8, 67.1), (203.6, 68.6),
           (202.2, 69.7), (200.5, 70.4), (198.6, 70.7), (177.6, 70.7),
           (175.8, 70.4), (174.1, 69.7), (172.6, 68.6), (171.5, 67.1),
           (170.8, 65.4), (170.5, 63.6), (170.5, 11.6), (170.8, 9.8),
           (171.5, 8.1), (172.6, 6.6), (174.1, 5.5), (175.8, 4.8),
           (177.6, 4.6), (198.6, 4.6), (200.5, 4.8), (202.2, 5.5),
           (203.6, 6.6), (204.8, 8.1), (205.5, 9.8), (205.7, 11.6)], 2.5, 1),
         ([(150.9, 70), (150.8, 70.8), (150.5, 71.6), (149.9, 72.3),
           (149.3, 72.8), (148.5, 73.1), (147.7, 73.2), (142.4, 73.2),
           (141.5, 73.1), (140.7, 72.8), (140.1, 72.3), (139.6, 71.6),
           (139.2, 70.8), (139.1, 70), (139.1, 29.9), (139.2, 29.1),
           (139.6, 28.3), (140.1, 27.6), (140.7, 27.1), (141.5, 26.8),
           (142.4, 26.7), (147.7, 26.7), (148.5, 26.8), (149.3, 27.1),
           (149.9, 27.6), (150.5, 28.3), (150.8, 29.1), (150.9, 29.9)], 1.6,
          1),
         (rect(61.2, 28.6, 124.9, 71.5), 0.8, 1),
         ([(130.2, 3.2), (190.7, 3.2), (190.7, 71.5), (173.6, 71.5),
           (172.6, 66.5), (169.7, 62.2), (165.5, 59.4), (160.5, 58.4),
           (155.5, 59.4), (151.2, 62.2), (148.4, 66.5), (147.4, 71.5),
           (130.2, 71.5)], 1.0, 1)], t=2.5)


@pytest.mark.parametrize("seed, moved, reach", [
    (392, [53.6031, 0.0], (0, 17.9938, 71.5969, (0,))),
    (476, [0.0, 43.4456], (1, 37.0256, 80.4711, (0, 1)))])
def test_a_fold_that_closes_clean_nowhere_up_to_its_panels_edge_is_stuck(
        seed, moved, reach):
    # each stuck fold has been tried at every place on to the edge of the
    # panel it moves over: nowhere is its bend clear
    lay = _seed_392() if seed == 392 else _seed_476()
    assert lay.shift == pytest.approx(moved, abs=1e-3)
    assert lay.stuck == [i == reach[0] for i in range(2)]
    stuck, = _notes(lay, "stuck")
    assert (stuck.fold, round(stuck.a, 4), round(stuck.b, 4),
            stuck.other) == reach


def _seed_531():
    # the checker's generated stack 531: a 3 mm strip folded three times,
    # C shut last on the far panel, with six pieces on it
    return stack(
        [(237.1, 79.9), (236.9, 80.7), (236.6, 81.4), (236.1, 82),
         (235.4, 82.4), (234.7, 82.7), (233.9, 82.8), (1.1, 70.3),
         (0.4, 70.2), (-0.4, 69.8), (-1, 69.3), (-1.4, 68.7), (-1.7, 67.9),
         (-1.7, 67.2), (2.1, -3.3), (2.2, -4.1), (2.5, -4.8), (3, -5.4),
         (3.7, -5.8), (4.4, -6.1), (5.2, -6.2), (238, 6.3), (238.8, 6.4),
         (239.5, 6.8), (240.1, 7.3), (240.5, 7.9), (240.8, 8.7),
         (240.8, 9.4)],
        [fold(53, -3.6, 48.9, 72.9, 120, 1, radius=-1, order=3),
         fold(110.8, -0.5, 106.7, 76, 175, 1, radius=-1, order=1,
              label="B"),
         fold(182.5, 3.3, 178.4, 79.8, 180, 1, radius=-1, order=2,
              label="C")],
        [([(113, 53.8), (113.4, 54.2), (113.6, 54.6), (113.8, 55.1),
           (113.8, 55.6), (113.7, 56), (113.5, 56.5), (90, 91.2),
           (89.6, 91.6), (89.2, 91.8), (88.7, 92), (88.2, 92), (87.7, 91.9),
           (87.3, 91.7), (67, 77.9), (66.6, 77.6), (66.3, 77.2), (66.2, 76.7),
           (66.2, 76.2), (66.3, 75.7), (66.5, 75.3), (90, 40.6), (90.4, 40.2),
           (90.8, 39.9), (91.2, 39.8), (91.7, 39.7), (92.2, 39.8),
           (92.7, 40.1)], 0.4, 1),
         ([(87.1, 74.4), (86.8, 75.8), (86.2, 77.1), (85.3, 78.2), (84.1, 79),
           (82.7, 79.5), (81.3, 79.6), (4.2, 75.5), (2.8, 75.2), (1.5, 74.6),
           (0.4, 73.7), (-0.4, 72.5), (-0.9, 71.2), (-1, 69.7), (-0.2, 54.3),
           (0.1, 52.9), (0.7, 51.6), (1.6, 50.5), (2.8, 49.7), (4.1, 49.2),
           (5.6, 49.1), (82.7, 53.2), (84.1, 53.5), (85.4, 54.1), (86.5, 55),
           (87.3, 56.2), (87.8, 57.6), (87.9, 59)], 2.0, 1),
         ([(225.8, 50.7), (225.7, 51), (225.6, 51.3), (225.4, 51.5),
           (225.2, 51.7), (224.9, 51.8), (224.6, 51.8), (193.5, 50.2),
           (193.2, 50.1), (192.9, 50), (192.7, 49.8), (192.5, 49.5),
           (192.4, 49.3), (192.4, 48.9), (194.4, 12.5), (194.4, 12.2),
           (194.5, 11.9), (194.7, 11.7), (195, 11.5), (195.3, 11.4),
           (195.6, 11.4), (226.6, 13.1), (226.9, 13.1), (227.2, 13.3),
           (227.4, 13.5), (227.6, 13.7), (227.7, 14), (227.7, 14.3)], 2.5, 1),
         ([(65.9, 25.4), (88, 70.6), (73.7, 77.6), (51.5, 32.4)], 0.4, -1),
         ([(67.5, 38.5), (69.4, 39), (71.1, 40), (72.6, 41.3), (73.6, 43),
           (74.2, 44.9), (74.2, 46.9), (73.1, 57.4), (72.6, 59.3),
           (71.7, 61.1), (70.3, 62.5), (68.6, 63.5), (66.7, 64.1),
           (64.7, 64.2), (63.9, 64.1), (62, 63.6), (60.3, 62.7), (58.8, 61.3),
           (57.8, 59.6), (57.2, 57.7), (57.2, 55.7), (58.3, 45.2),
           (58.8, 43.3), (59.7, 41.5), (61.1, 40.1), (62.8, 39.1),
           (64.7, 38.5), (66.7, 38.5)], 2.0, -1),
         ([(229, 45.1), (229.7, 46), (230, 46.9), (230.1, 47.9), (230, 49),
           (229.5, 49.9), (228.9, 50.7), (166.1, 110.2), (165.2, 110.8),
           (164.3, 111.2), (163.3, 111.3), (162.2, 111.1), (161.3, 110.7),
           (160.5, 110), (142.4, 90.9), (141.8, 90.1), (141.4, 89.1),
           (141.3, 88.1), (141.5, 87.1), (141.9, 86.1), (142.5, 85.3),
           (205.3, 25.8), (206.2, 25.2), (207.1, 24.9), (208.2, 24.7),
           (209.2, 24.9), (210.1, 25.3), (210.9, 26)], 1.0, -1)], t=3.0)


def _at_rest(lay):
    # how deep each two pieces are drawn into each other at rest, as the
    # notes say: {(piece, piece): mm}, the main piece in itself (0, 0)
    out = {}
    for n in lay.notes:
        if n.kind in ("clash", "limit", "landing") and n.rest > 0.0:
            key = (n.sheet, n.other[0]) if n.kind == "clash" else (0, 0)
            out[key] = max(out.get(key, 0.0), round(n.rest, 3))
    return out


def test_a_fold_clear_further_on_is_kept_there_and_the_rest_is_told(
        monkeypatch):
    # "clear" is the fold's own bend: C, stopped 46.10 mm off its line
    # with the pile 2.81 mm in its bend (48.91 mm on its line), forms
    # clear 0.81 mm further on. The stack is drawn through at rest all the
    # same, two pieces by two no deeper than where C stopped, and each
    # pair is told: the landing with its depth with C on its line as well
    lay = _seed_531()
    assert lay.shift == pytest.approx([0.0, 0.0, 46.9107], abs=1e-3)
    assert lay.stuck == [False, False, False]
    assert sorted((n.kind, n.other, round(n.rest, 3)) for n in lay.notes
                  if n.kind == "landing") == [
        ("landing", (("panel", 0), ("panel", 2)), 6.689),
        ("landing", (("panel", 0), ("panel", 3)), 1.196)]
    assert lay.warnings == [
        "With fold C off its line, panel 1 and panel 3 are drawn 6.69 mm "
        "into each other (5.91 mm with the fold on its line) (and they "
        "are not the only ones)."]
    rest = {(0, 0): 6.689, (0, 2): 7.704, (0, 6): 7.689, (2, 6): 8.704}
    assert _at_rest(lay) == rest
    # (where it stopped, the pile in its bend: a limit of 1.93 at rest in
    # place of the landing of 1.20 between panels 1 and 4)
    monkeypatch.setattr(fs, "_further", lambda *args: None)
    stopped = _seed_531()
    assert stopped.stuck == [False, False, True]
    assert stopped.shift[2] == pytest.approx(46.0982, abs=1e-3)
    stuck, = _notes(stopped, "stuck")
    assert (round(stuck.a, 2), round(stuck.b, 2)) == (2.81, 48.91)
    limit, = _notes(stopped, "limit")
    assert round(limit.rest, 3) == 1.928
    assert _at_rest(stopped) == rest


def test_a_fold_that_closes_clean_nowhere_leaves_the_next_one_to_be_tried():
    # a cross: a 70 mm square with a flap on its left and one below, each
    # with a 1 mm patch on it, folded in over it, and a 10 mm tab on its
    # right (B) and on top (D) folded over them. Both tabs move and stop
    # for want of room. B closes clean nowhere; D, tried after it, closes
    # clean 7.51 mm off its line, its bend clear of the flaps' leather
    lay = stack(
        [(0, 0), (5, 0), (5, -80), (65, -80), (65, 0), (70, 0), (70, 5),
         (80, 5), (80, 65), (70, 65), (70, 70), (65, 70), (65, 80),
         (5, 80), (5, 70), (0, 70), (0, 65), (-80, 65), (-80, 5), (0, 5)],
        [fold(0, 5, 0, 65, order=1),
         fold(5, 0, 65, 0, order=2, label="C"),
         fold(70, 5, 70, 65, order=3, label="B"),
         fold(5, 70, 65, 70, order=4, label="D")],
        [(rect(-75, 10, -5, 60), 1.0, 1), (rect(10, -75, 60, -5), 1.0, 1)])
    assert lay.shift == pytest.approx([0.0, 0.0, 3.7168, 7.5137], abs=1e-4)
    assert lay.stuck == [False, False, True, False]
    stuck, = _notes(lay, "stuck")
    assert (stuck.fold, round(stuck.a, 4), round(stuck.b, 4)) == (
        2, 10.3303, 14.0471)
    assert lay.warnings == [
        "What is folded inside fold B reaches 14.05 mm into its bend, and "
        "the fold cannot move further out of its way: its bend would not "
        "fit before the edge of panel 5."]
    # (what B leaves in the patches is told; D brings nothing more)
    assert sorted((n.kind, n.other, round(n.rest, 3))
                  for n in _notes(lay, "clash", "limit", "landing")) == [
        ("clash", (1,), 1.333), ("limit", (0,), 1.415)]
    assert witnesses(lay, 9, 3) == []


def test_a_fold_on_its_line_with_no_way_to_go_is_tried_nowhere(
        monkeypatch):
    # fold A of 493 is searched on its line the way its first move went
    # (to its left); told no way, the search tries no place at all, not
    # the drawn line itself again
    real, seen = fs._further, []
    monkeypatch.setattr(fs, "_further",
                        lambda *args: seen.append(args) or real(*args))
    _seed_493()
    (ring, first, lay, fi, held, fixed, kw, folds, way), = seen
    assert (fi, lay.moved[fi], way) == (0, 0.0, 1.0)
    recut, cuts = fs._recut, []
    monkeypatch.setattr(fs, "_recut",
                        lambda *args: cuts.append(args) or recut(*args))
    assert real(ring, first, lay, fi, held, fixed, kw, folds, 0.0) is None
    assert cuts == []

def _windowed(where):
    # a 200 by 90 mm shell, 1.6 mm, folded in the middle, with a 20 mm
    # square window: in the shell's still half under a 1 mm liner over
    # all of it, in a 1 mm pocket over all the still half, or in the
    # still half of a 1 mm liner over all
    window = rect(30, 30, 50, 50)
    if where == "shell":
        return fs.fold_piece(rect(0, 0, 200, 90), [fold(100, 0, 100, 90)],
                             thickness=1.6, cutouts=[window],
                             sheets=[sh.Sheet(rect(0, 0, 200, 90), 1.0)])
    size = rect(0, 0, 100, 90) if where == "pocket" else rect(0, 0, 200, 90)
    return fs.fold_piece(rect(0, 0, 200, 90), [fold(100, 0, 100, 90)],
                         thickness=1.6,
                         sheets=[sh.Sheet(size, 1.0, cutouts=[window])])


@pytest.mark.parametrize("where, most, least", [
    ("shell", 5.2, 3.6), ("pocket", 4.2, 3.2), ("liner", 5.2, 4.2)])
def test_a_needle_through_a_window_goes_through_no_leather_of_it(
        where, most, least):
    # through the window the needle meets the shut shell and liner but
    # the window's own leather: 1.6 + 2.0 under the shell's window; 3.2
    # under the pocket's; 3.2 + 1.0 under the liner's, which only the
    # liner's other half covers
    leather, = _notes(_windowed(where), "leather")
    assert (leather.a, leather.b) == pytest.approx((most, least), abs=1e-9)


def _many(n):
    # a shell 240 by 90 mm, 1.6 mm, folded in three, with ``n`` pieces
    # laid on its three panels at random
    rng = random.Random(5)
    folds = [fold(80, 0, 80, 90, order=1),
             fold(160, 0, 160, 90, order=2, label="B")]
    sheets = []
    for k in range(n):
        p = rng.randrange(3)
        x0 = 80 * p + rng.uniform(3, 20)
        x1 = 80 * p + 80 - rng.uniform(3, 20)
        sheets.append(sh.Sheet(rect(x0, rng.uniform(3, 30), x1,
                                    rng.uniform(55, 87)),
                               rng.choice([0.4, 0.6, 0.8]),
                               rng.choice([1, 1, -1]), name=f"s{k}"))
    return rect(0, 0, 240, 90), folds, sheets


def _wallet_built(angle):
    """The generated bifold built with its spine at an angle: the least
    processor time of three builds (s), and the layout."""
    import time
    best = math.inf
    for _try in range(3):
        outline, folds, sheets, t, holes = generated(shell_t=1.6,
                                                     inner_t=0.8)
        folds = [dataclasses.replace(f, angle=angle) for f in folds]
        t0 = time.process_time()
        lay = fs.fold_piece(outline, folds, thickness=t, holes=holes,
                            sheets=sheets)
        best = min(best, time.process_time() - t0)
    return best, lay


def test_the_wallet_builds_as_fast_with_its_spine_standing_open():
    # the search for the leather a needle meets once ran on every stack,
    # and on leather lying aslant it never ended: the wallet with its
    # spine at 150 degrees took 11.5 s to build, at 179 degrees 13.5 s
    # (57 ms shut). Now 54 ms and 103 ms: never more than twice the
    # wallet shut -- or 150 ms, for a busy machine and a coarse clock
    shut, lay = _wallet_built(180.0)
    leather, = _notes(lay, "leather")
    assert (leather.a, leather.b) == pytest.approx((11.2, 3.2), abs=1e-9)
    for angle in (90.0, 120.0, 150.0, 179.0):
        took, lay = _wallet_built(angle)
        assert took <= max(2.0 * shut, 0.15), angle
        assert len(_notes(lay, "leather")) == (1 if angle >= 175.0 else 0)


def test_random_shut_stacks_get_their_needle_figure_and_quickly():
    # fifteen stacks of up to seven pieces of every shape on a shell
    # folded shut: the search cuts its boxes along the outlines' own
    # sides, so each gets its figure within the search's ceiling, and
    # none takes longer than 0.14 s to build (1.0 s, before)
    import time
    rng = random.Random(7)
    worst = 0.0
    for _k in range(15):
        outline, folds, sheets, t, base = random_stack(rng, "shut")
        t0 = time.process_time()
        lay = fs.fold_piece(outline, folds, thickness=t, sheets=sheets,
                            base=base)
        worst = max(worst, time.process_time() - t0)
        assert len(_notes(lay, "leather")) == 1
    assert worst < 0.5


def test_a_search_cut_short_gives_no_needle_figure_rather_than_a_wrong_one(
        monkeypatch):
    # the search stops at a number of boxes looked at (4,000; the wallet
    # takes 3): beyond it no figure is given, and all else is told
    monkeypatch.setattr(sh, "_BOXES", 2)
    lay = bifold(shell_t=1.6, inner_t=0.8)
    assert _notes(lay, "leather") == []
    size, = _notes(lay, "size")
    assert (size.a, size.b) == pytest.approx((97.66, 90.0), abs=0.01)
    monkeypatch.setattr(sh, "_BOXES", 3)
    leather, = _notes(bifold(shell_t=1.6, inner_t=0.8), "leather")
    assert (leather.a, leather.b) == pytest.approx((11.2, 3.2), abs=1e-9)


def test_forty_pieces_on_a_trifold_build_in_well_under_half_a_second():
    import time
    best = math.inf
    for _try in range(3):
        outline, folds, sheets = _many(40)
        t0 = time.process_time()
        lay = fs.fold_piece(outline, folds, thickness=1.6, sheets=sheets,
                            base=1)
        best = min(best, time.process_time() - t0)
    leather, = _notes(lay, "leather")
    assert (leather.a, leather.b) == pytest.approx((28.8, 3.2), abs=1e-9)
    # (about 0.22 s on the machine it was written on; 1 s before)
    assert best < 0.5


def _limited():
    # a piece whose fold B forms off its line (the fault left over from
    # the fold fix, at its smallest), with a small patch on its back far
    # from every fold
    outline = [(0, 0), (127, 0), (127, 30), (60, 30), (60, 50), (30, 50),
               (30, 30), (0, 30)]
    folds = [fs.FoldLine((30, 30), (60, 30), 180, 1, order=1, label="F"),
             fs.FoldLine((30, 0), (30, 30), 180, 1, order=2, label="B"),
             fs.FoldLine((70, 0), (70, 30), 180, -1, order=3, label="C"),
             fs.FoldLine((112, 0), (112, 30), 90, -1, order=4, label="D")]
    return outline, folds


def test_a_stack_whose_fold_moves_is_told_what_the_move_brings_down():
    outline, folds = _limited()
    alone = fs.fold_piece(outline, folds, thickness=2.0)
    lay = fs.fold_piece(outline, folds, thickness=2.0, sheets=[
        sh.Sheet([(40, 5), (55, 5), (55, 20), (40, 20)], 0.4, -1)])
    # the lone piece's warning and its remedy, which the patch does not
    # stand in the way of
    assert lay.shift == alone.shift == pytest.approx([0.0, math.pi, 0, 0],
                                                     abs=1e-9)
    assert lay.warnings == alone.warnings == [
        "With fold B off its line, panel 5 and fold B are drawn 1.79 mm "
        "into each other."]
    assert lay.remedy(1) == alone.remedy(1) == (
        "To have it fold on its line, shorten panel 2 by 3.14 mm where "
        "it reaches into the fold.")
    # ... and among the notes, what the move brings down: not a limit of
    # folding the piece, which it is not with the fold on its line
    landing, = _notes(lay, "landing")
    assert (landing.fold, landing.other, round(landing.a, 4), landing.b,
            landing.c) == (1, (("panel", 4), ("bend", 1)), 1.7854, 3.75,
                           0.0)
    assert not _notes(lay, "limit", "clash")


def test_a_piece_that_crosses_the_fold_line_only_past_the_hinge_reaches_in():
    # an L-shaped piece on the T's body reaching into the bend of the flap's
    # fold, its arm standing up beside the flap across the fold line,
    # where nothing bends: it reaches in, it does not go round
    ell_ = [(40, 50), (95, 50), (95, 80), (75, 80), (75, 58), (40, 58)]
    lay = stack(TEE, [fold(0, 65, 100, 65, radius=6.0)], [(ell_, 1.0, 1)])
    assert not _notes(lay, "cross", "level")
    reach, = _notes(lay, "reach")
    assert (reach.sheet, round(reach.a, 4), round(reach.b, 4)) == (
        1, 3.9956, 10.9956)


def test_a_piece_outside_a_fold_cut_long_enough_comes_level():
    # 0.8 mm on the outside of a 1.6 mm piece folded flat needs pi x 1.2
    # mm more leather; cut that much longer, it is told it comes level
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                [(rect(-1.885, 10, 201.885, 90), 0.8, -1)], t=1.6)
    assert not _notes(lay, "cross")
    level, = _notes(lay, "level")
    assert (level.a, level.b) == (pytest.approx(-3.7699, abs=1e-4),
                                  pytest.approx(-3.77, abs=1e-9))

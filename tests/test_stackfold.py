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
    return {n.sheet for n in lay.notes if n.kind == "slit"}


def witnesses(lay, moments=9, slit_pass=False):
    """Everything the four witnesses find wrong with a layout, at
    ``moments`` moments of its folding, as a list of words.

    A sheet said to be slit at the end of a hinge is drawn where leather
    cannot go there, and is said to be: what it runs into itself is not
    news. With ``slit_pass`` what any other piece runs into it is not
    either (the random stacks: a piece resting on a slit one is carried
    off it by the flap). Where a fold forms off its line, what the
    released engine lets a lone piece keep is let pass as well: 0.05 mm,
    as ``tests/test_foldsolid.py`` measures it."""
    out = [f"sheet {k} cells cover {got:.3f} of {want:.3f}, {twice:.2e} "
           f"twice" for k, got, want, twice in covered(lay)]
    slit = slit_sheets(lay)
    share = 0.05 if any(lay.shift) else SHARE

    def excuse(ta, tb):
        a, b = sh.sheet_of(ta), sh.sheet_of(tb)
        return a == b and a in slit or slit_pass and (a in slit
                                                      or b in slit)

    n = lay.n_steps
    for j in range(moments if n else 1):
        t = n * j / (moments - 1) if n else 0.0
        cells = lay.solid(lay.fractions(t))
        io = inside_out(cells)
        if io > FLAT_ENOUGH:
            out.append(f"t {t:.2f}: a cell is inside out by {io:.2e}")
        bad = [x for x in overlaps(cells, excuse) if x[0] > share]
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
    return [n for n in lay.notes if n.kind in kinds]


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
    assert witnesses(lay, 9) == []


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
    assert witnesses(lay, 9) == []


def test_the_generated_bifold_is_whole_at_21_moments_of_closing():
    assert witnesses(bifold(shell_t=1.6, inner_t=0.8), 21) == []


# -- the room a fold needs ----------------------------------------------------

def test_a_lining_inside_a_right_angle_rides_at_0_8_and_takes_2_513():
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100, 90.0)],
                [(rect(20, 10, 180, 90), 0.8, 1)], t=1.6)
    assert lay.radius == pytest.approx([0.8], abs=1e-9)
    assert lay.used == pytest.approx([2.5133], abs=1e-4)
    assert lay.why == [("ride", 1, 0)]
    assert witnesses(lay, 13) == []


def test_a_4_mm_patch_on_one_side_opens_a_fold_to_2_mm():
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                [(rect(20, 20, 80, 80), 4.0, 1)], t=1.6)
    assert lay.radius == pytest.approx([2.0], abs=1e-9)
    assert lay.why == [("meet", 0, 1)]
    assert witnesses(lay, 13) == []


def test_a_liner_bigger_than_its_shell_bends_at_0_8():
    lay = stack(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                [(rect(-5, -5, 205, 105), 0.8, 1)], t=1.6)
    assert lay.radius == pytest.approx([0.8], abs=1e-9)
    assert lay.used == pytest.approx([5.0265], abs=1e-4)
    assert witnesses(lay, 13) == []


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
    assert witnesses(lay, 17) == []


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
    assert witnesses(lay, 9) == []


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
    ("open", 14), ("tee", 5), ("tee", 15), ("three", 6), ("three", 16)])
def test_random_stacks_are_whole_at_five_moments_of_their_folding(family,
                                                                  seed):
    # 1 to 7 pieces each, on both faces, turned to any angle, rounded,
    # L-shaped, notched and standing past the edge; folded flat shut, in
    # a zigzag, at 30 to 160 degrees, on a T-shaped piece, or as a strip
    # in three whose flaps reach the other bend
    rng = random.Random(seed)
    for trial in range(8):
        out, folds, sheets, t, base = random_stack(rng, family)
        lay = fs.fold_piece(out, folds, thickness=t, sheets=sheets,
                            base=base)
        assert witnesses(lay, 5, slit_pass=True) == [], (family, seed,
                                                         trial)


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

    for pull, want in ((0.0, (34, 0, False)), (0.5 * over, (0, 34, True))):
        lay = fs.fold_piece(rect(0, 0, 200, 100), [fold(100, 0, 100, 100)],
                            holes=holes, sheets=lining(pull))
        cross, = _notes(lay, "cross")
        assert cross.a == pytest.approx(over, abs=1e-9)
        assert lay.lineup().readings == {1: want}

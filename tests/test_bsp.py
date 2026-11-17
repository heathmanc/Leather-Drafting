"""Back-to-front ordering of convex cells: whatever is painted last where a
line of sight meets the model must be what the eye actually sees there."""

import math
import random

from leathercad import bsp


def _box(o, ex, ey, ez, sx, sy, sz, decals=None):
    """A box at ``o`` with unit axes and sizes, as a cell."""
    def at(a, b, c):
        return (o[0] + ex[0] * a * sx + ey[0] * b * sy + ez[0] * c * sz,
                o[1] + ex[1] * a * sx + ey[1] * b * sy + ez[1] * c * sz,
                o[2] + ex[2] * a * sx + ey[2] * b * sy + ez[2] * c * sz)
    quads = [((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
             ((0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 0)),
             ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
             ((1, 1, 0), (0, 1, 0), (0, 1, 1), (1, 1, 1)),
             ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)),
             ((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1))]
    faces = []
    for k, q in enumerate(quads):
        pts = tuple(at(*c) for c in q)
        kind = bsp.FRONT if k == 0 else bsp.BACK if k == 1 else bsp.WALL
        faces.append(bsp.Face(pts, bsp.plane_of(pts), kind, (True,) * 4,
                              decals if k == 0 else None, None,
                              sx * sy if k < 2 else sz))
    return bsp.Cell(faces)


_AXES = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


def _turned(rng):
    yaw, pitch, roll = rng.uniform(0, 6), rng.uniform(-1, 1), rng.uniform(0, 6)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    return ((cy * cp, sy * cp, -sp),
            (cy * sp * sr - sy * cr, sy * sp * sr + cy * cr, cp * sr),
            (cy * sp * cr + sy * sr, sy * sp * cr - cy * sr, cp * cr))


def _hit(face, o, v):
    """How far along ``v`` from ``o`` the line meets the face, or None."""
    nx, ny, nz, d = face.plane
    den = nx * v[0] + ny * v[1] + nz * v[2]
    if abs(den) < 1e-9:
        return None
    s = (d - (nx * o[0] + ny * o[1] + nz * o[2])) / den
    p = (o[0] + s * v[0], o[1] + s * v[1], o[2] + s * v[2])
    pts = face.pts
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        e = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
        c = (e[1] * w[2] - e[2] * w[1], e[2] * w[0] - e[0] * w[2],
             e[0] * w[1] - e[1] * w[0])
        if c[0] * nx + c[1] * ny + c[2] * nz < -1e-9:
            return None
    return s


def wrong_rays(cells, rng, lo, hi, rays=120, views=4):
    """Lines of sight where the face painted last is not the nearest one.
    Returns ``(lines that met the model, how many were wrong)``."""
    tree = bsp.build(cells)
    met = wrong = 0
    for _ in range(views):
        th, ph = rng.uniform(0, 2 * math.pi), rng.uniform(-1.4, 1.4)
        v = (math.cos(th) * math.cos(ph), math.sin(th) * math.cos(ph),
             math.sin(ph))
        faces = [f for c in bsp.back_to_front(tree, *v) for f in c.faces
                 if f.kind != bsp.INNER and f.plane[0] * v[0]
                 + f.plane[1] * v[1] + f.plane[2] * v[2] > 1e-6]
        for _ in range(rays):
            o = tuple(rng.uniform(lo[k], hi[k]) for k in range(3))
            hits = [s for f in faces for s in [_hit(f, o, v)]
                    if s is not None]
            if hits:
                met += 1
                wrong += max(hits) - hits[-1] > 1e-5
    return met, wrong


def test_stacked_slabs_paint_bottom_to_top():
    cells = [_box((0, 0, k), *_AXES, 10, 6, 1.0) for k in range(8)]
    tree = bsp.build(cells)
    assert bsp.count(tree) == (7, 8)             # parted, nothing cut
    order = bsp.back_to_front(tree, 0.0, 0.0, 1.0)   # the eye above
    assert [round(c.cz, 3) for c in order] == [k + 0.5 for k in range(8)]
    below = bsp.back_to_front(tree, 0.2, 0.0, -1.0)
    assert [round(c.cz, 3) for c in below] == [k + 0.5 for k in range(7, -1,
                                                                      -1)]


def test_cells_side_by_side_are_parted_without_cutting():
    cells = [_box((i * 3.0, j * 3.0, 0), *_AXES, 3, 3, 1.0)
             for i in range(6) for j in range(6)]
    tree = bsp.build(cells)
    assert bsp.count(tree) == (35, 36)
    met, wrong = wrong_rays(cells, random.Random(1), (-1, -1, -3),
                            (19, 19, 4))
    assert met > 50 and wrong == 0


def test_order_is_right_even_for_boxes_run_through_each_other():
    rng = random.Random(7)
    cells = []
    for _ in range(20):
        o = (rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-10, 10))
        cells.append(_box(o, *_turned(rng), rng.uniform(1, 8),
                          rng.uniform(1, 8), rng.uniform(0.3, 2)))
    met, wrong = wrong_rays(cells, rng, (-12,) * 3, (12,) * 3)
    assert met > 100 and wrong == 0


def test_split_face_keeps_lines_off_the_new_edge_and_marks_on_their_side():
    pts = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 2.0, 0.0), (0.0, 2.0, 0.0))
    decals = [(bsp.DOTS, [(1.0, 1.0, 0.0), (3.0, 1.0, 0.0)], "hole"),
              (bsp.LINE, ((0.5, 0.5, 0.0), (3.5, 0.5, 0.0)), 0),
              (bsp.POLY, [(1.5, 1.2, 0.0), (2.5, 1.2, 0.0), (2.5, 1.8, 0.0),
                          (1.5, 1.8, 0.0)], "cut")]
    face = bsp.Face(pts, bsp.plane_of(pts), bsp.FRONT, (True,) * 4, decals,
                    "tag", 8.0)
    front, back = bsp.split_face(face, (1.0, 0.0, 0.0, 2.0))   # x = 2
    assert front is not None and back is not None
    for part, xs in ((front, (2.0, 4.0)), (back, (0.0, 2.0))):
        assert {p[0] for p in part.pts} == set(xs)
        assert part.kind == bsp.FRONT and part.tag == "tag"
        # three real edges keep their line; the cut along x = 2 has none
        assert sorted(part.edges) == [False, True, True, True]
        k = part.edges.index(False)
        a, b = part.pts[k], part.pts[(k + 1) % 4]
        assert a[0] == b[0] == 2.0
        dots = [d for d in part.decals if d[0] == bsp.DOTS]
        lines = [d for d in part.decals if d[0] == bsp.LINE]
        polys = [d for d in part.decals if d[0] == bsp.POLY]
        assert len(dots) == len(lines) == len(polys) == 1
        assert len(dots[0][1]) == 1 and xs[0] <= dots[0][1][0][0] <= xs[1]
        assert sorted(p[0] for p in lines[0][1]) in ([0.5, 2.0], [2.0, 3.5])
        assert all(xs[0] <= p[0] <= xs[1] for p in polys[0][1])
    # a face wholly on one side comes back as it is
    assert bsp.split_face(face, (1.0, 0.0, 0.0, 9.0)) == (None, face)
    assert bsp.split_face(face, (1.0, 0.0, 0.0, -1.0)) == (face, None)


def test_split_face_keeps_what_its_edges_are_and_where_its_texture_lies():
    pts = ((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 2.0, 0.0), (0.0, 2.0, 0.0))
    turn = (0.0, 0.6, 0.8)        # the way the face round the bend is turned
    uv = ((1.0, 2.0, 3.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    face = bsp.Face(pts, bsp.plane_of(pts), bsp.FRONT,
                    (True, False, turn, False), uv=uv)
    assert face.uv is uv
    for part in bsp.split_face(face, (1.0, 0.0, 0.0, 2.0)):    # x = 2
        assert part.uv is uv
        n = len(part.pts)
        assert n == 4
        for i in range(n):
            a, b = part.pts[i], part.pts[(i + 1) % n]
            if a[1] == b[1] == 0.0:
                assert part.edges[i] is True      # the edge of the leather
            elif a[1] == b[1] == 2.0:
                assert part.edges[i] == turn      # still goes on round
            else:
                assert part.edges[i] is False     # an end, or the cut
    # a face with nothing said about its edges or its texture stays so
    plain = bsp.Face(pts, bsp.plane_of(pts), bsp.WALL)
    for part in bsp.split_face(plain, (1.0, 0.0, 0.0, 2.0)):
        assert part.edges is None and part.uv is None


def test_split_cell_gives_two_solids_without_a_face_on_the_cut():
    cell = _box((0, 0, 0), *_AXES, 4, 2, 1)
    front, back = bsp.split_cell(cell, (1.0, 0.0, 0.0, 1.0))
    assert len(front.faces) == len(back.faces) == 5
    assert min(v[0] for v in front.verts) == 1.0
    assert max(v[0] for v in back.verts) == 1.0
    one, none = bsp.split_cell(cell, (1.0, 0.0, 0.0, -5.0))
    assert none is None and len(one.faces) == 6


def test_a_tangle_stops_at_the_limit_and_still_gives_every_cell_once():
    rng = random.Random(3)
    cells = [_box((rng.uniform(-2, 2), rng.uniform(-2, 2), rng.uniform(-2, 2)),
                  *_turned(rng), 8, 8, 1.0) for _ in range(40)]
    tree = bsp.build(cells, limit=200)
    planes, n = bsp.count(tree)
    assert 40 <= n < 400
    order = bsp.back_to_front(tree, 0.3, -0.5, 0.8)
    assert len(order) == n and len({id(c) for c in order}) == n


def test_plane_of_points_out_of_a_counter_clockwise_face():
    pl = bsp.plane_of([(0, 0, 3), (1, 0, 3), (1, 1, 3), (0, 1, 3)])
    assert pl == (0.0, 0.0, 1.0, 3.0)
    assert bsp.plane_of([(0, 0, 0), (1, 0, 0), (2, 0, 0)]) is None

"""Interactive per-corner modifications: fillet (round) and chamfer (bevel).

Both operate on an :class:`EditablePath` vertex. A chamfer needs two straight
edges and cuts the vertex back along both. A fillet BLENDS: it finds the circle
of the requested radius tangent to the two sides of the corner -- straight,
arc or bezier, and across as many edges as a smooth side runs (a flattened
ellipse is dozens of short lines) -- and replaces everything between the two
tangent points with that arc. Each side may give up at most half of its smooth
run, so the corner at the other end of the run can be rounded too (for two
straight edges that is the classic "never past an edge midpoint" rule).
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from .geometry import Vec2
from .shapes import EditablePath, Edge, arc_through


def _corner_frame(ep: EditablePath, i: int) -> Optional[Tuple]:
    """(prev_pt, cur, next_pt, prev_edge_idx, next_edge_idx) for vertex i,
    or None if the vertex isn't flanked by two straight edges."""
    n = len(ep.nodes)
    m = len(ep.edges)
    if n < 3:
        return None
    if ep.closed:
        pe = (i - 1) % m
        ne = i % m
        pv = ep.nodes[(i - 1) % n]
        nv = ep.nodes[(i + 1) % n]
    else:
        if i <= 0 or i >= n - 1:
            return None                     # endpoints have only one edge
        pe = i - 1
        ne = i
        pv = ep.nodes[i - 1]
        nv = ep.nodes[i + 1]
    if not (0 <= pe < m and 0 <= ne < m):
        return None
    if ep.edges[pe].kind != "line" or ep.edges[ne].kind != "line":
        return None                         # only line-line corners for now
    return pv, ep.nodes[i], nv, pe, ne


def _tangent_points(pv: Vec2, cur: Vec2, nv: Vec2, setback: float):
    """Points ``setback`` along each edge from the corner + the corner angle,
    or None for degenerate/straight corners."""
    v0 = pv - cur
    v1 = nv - cur
    l0, l1 = v0.length(), v1.length()
    if l0 < 1e-9 or l1 < 1e-9:
        return None
    u0, u1 = v0 * (1.0 / l0), v1 * (1.0 / l1)
    ang = math.acos(max(-1.0, min(1.0, u0.dot(u1))))
    if ang < 1e-6 or abs(ang - math.pi) < 1e-6:
        return None
    t = min(setback, 0.5 * l0, 0.5 * l1)    # never eat past an edge midpoint
    return cur + u0 * t, cur + u1 * t, u0, u1, ang, t


def fillet_vertex(ep: EditablePath, i: int, radius: float) -> float:
    """Round vertex ``i`` with an arc of ``radius`` mm that blends into both
    sides (see the module doc). If that radius doesn't fit, the largest one
    that does is used. Returns the radius applied (truthy), or 0.0 if the
    vertex can't be rounded: an open path's end, or no real corner there."""
    if radius <= 1e-9:
        return 0.0
    plan = _blend_plan(ep, i, radius)
    used = radius
    if plan is None:
        # too big for this corner: the largest radius that still fits
        lo, hi, best = 0.0, radius, None
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            cand = _blend_plan(ep, i, mid)
            if cand is None:
                hi = mid
            else:
                lo, best = mid, cand
            if hi - lo < 1e-4 * radius:
                break
        if best is None:
            return 0.0
        plan, used = best, lo
    _apply_blend(ep, plan)
    return used


# -- fillet internals ----------------------------------------------------------
_SMOOTH_TURN = math.radians(25.0)   # sharper than this ends a side's smooth run
_SAMPLE_MM = 0.25                   # curve sampling for the coarse search


def _edge_eval(a: Vec2, b: Vec2, e: Edge):
    """(kind, point(f)) for one edge, f in [0, 1] from ``a`` to ``b``."""
    if e.kind == "arc" and e.mid is not None:
        arc = arc_through(a, e.mid, b)
        if arc is not None:
            c, r, a0, a1, ccw = arc
            two_pi = 2.0 * math.pi
            sweep = (a1 - a0) % two_pi if ccw else -((a0 - a1) % two_pi)
            return "arc", lambda f: Vec2(c.x + r * math.cos(a0 + sweep * f),
                                         c.y + r * math.sin(a0 + sweep * f))
    if e.kind == "bezier" and e.c1 is not None and e.c2 is not None:
        c1, c2 = e.c1, e.c2

        def bez(f):
            u = 1.0 - f
            return (a * (u * u * u) + c1 * (3 * u * u * f)
                    + c2 * (3 * u * f * f) + b * (f * f * f))
        return "bezier", bez
    return "line", lambda f: a + (b - a) * f


def _split_edge(a: Vec2, b: Vec2, e: Edge, f: float, keep: str) -> Edge:
    """The part of edge a->b on one side of parameter ``f``: ``keep`` = "head"
    (a .. P(f)) or "tail" (P(f) .. b), as a new Edge of the same kind."""
    kind, pt = _edge_eval(a, b, e)
    if kind == "arc":
        return Edge("arc", mid=pt(0.5 * f) if keep == "head"
                    else pt(0.5 * (1.0 + f)))
    if kind == "bezier":
        p01 = a.lerp(e.c1, f)
        p12 = e.c1.lerp(e.c2, f)
        p23 = e.c2.lerp(b, f)
        p012 = p01.lerp(p12, f)
        p123 = p12.lerp(p23, f)
        if keep == "head":
            return Edge("bezier", c1=p01, c2=p012)
        return Edge("bezier", c1=p123, c2=p23)
    return Edge("line")


def _side_samples(ep: EditablePath, k: int, step: int, max_edges: int):
    """Walk away from node ``k`` (``step`` +1 forward, -1 backward) over the
    smooth run of edges. Returns ``(samples, run_len, open_end)``: samples are
    ``(point, edge_index, f, s)`` outward from the corner (f in the edge's own
    a->b direction, s = distance walked), ``run_len`` the whole run's length,
    and whether the run ended at an open path's end."""
    n, m = len(ep.nodes), len(ep.edges)
    out = []
    s = 0.0
    prev_dir = None
    walked = 0
    e_idx = k if step > 0 else k - 1
    while walked < max_edges:
        if ep.closed:
            e_idx %= m
        elif not (0 <= e_idx < m):
            return out, s, True
        a, b = ep.nodes[e_idx], ep.nodes[(e_idx + 1) % n]
        kind, pt = _edge_eval(a, b, ep.edges[e_idx])
        if kind == "line" and (b - a).length() < 1e-9:
            walked += 1                          # a duplicate point: step over
            e_idx += step
            continue
        if kind == "line":
            fs = [0.0, 1.0]
        else:
            approx = sum((pt((j + 1) / 16) - pt(j / 16)).length()
                         for j in range(16))
            cnt = max(8, min(4000, int(math.ceil(approx / _SAMPLE_MM))))
            fs = [j / cnt for j in range(cnt + 1)]
        if step < 0:
            fs = fs[::-1]
        pts = [pt(f) for f in fs]
        first_dir = pts[1] - pts[0]
        if prev_dir is not None and first_dir.length() > 1e-12:
            cosang = prev_dir.dot(first_dir) / (prev_dir.length()
                                                * first_dir.length())
            if math.acos(max(-1.0, min(1.0, cosang))) > _SMOOTH_TURN:
                return out, s, False            # a real corner ends the run
        for j, (q, f) in enumerate(zip(pts, fs)):
            if out and j == 0:
                continue                         # shared node with last edge
            if out:
                s += (q - out[-1][0]).length()
            out.append((q, e_idx, f, s))
        last = pts[-1] - pts[-2]
        if last.length() > 1e-12:
            prev_dir = last
        walked += 1
        e_idx += step
    return out, s, False


def _offset_side(samples, inside_sign: float, outward_is_travel: bool, r: float):
    """Vertex-normal offset of a side's sample polyline by ``r`` toward the
    corner's inside. Travel direction is the PATH direction (outward for the
    forward side, inward for the backward one)."""
    pts = [q for q, *_ in samples]
    nrm = []
    for j in range(len(pts)):
        d0 = pts[j] - pts[j - 1] if j > 0 else None
        d1 = pts[j + 1] - pts[j] if j + 1 < len(pts) else None
        acc = Vec2(0.0, 0.0)
        for d in (d0, d1):
            if d is not None and d.length() > 1e-12:
                t = d * (1.0 / d.length())
                if not outward_is_travel:
                    t = t * -1.0
                acc = acc + Vec2(-t.y, t.x)
        L = acc.length()
        nrm.append(acc * (inside_sign / L) if L > 1e-12 else Vec2(0.0, 0.0))
    return [p + q * r for p, q in zip(pts, nrm)]


def _seg_cross(p0, p1, q0, q1):
    d1 = p1 - p0
    d2 = q1 - q0
    den = d1.cross(d2)
    if abs(den) < 1e-15:
        return None
    w = q0 - p0
    t = w.cross(d2) / den
    u = w.cross(d1) / den
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return t, u
    return None


def _nearest_on(samples, c: Vec2, upto: float):
    """Closest point to ``c`` on the sample polyline within distance ``upto``
    of the corner: (dist, segment index j, t along samples[j] -> [j+1])."""
    best = None
    for j in range(len(samples) - 1):
        if samples[j][3] > upto:
            break
        a, b = samples[j][0], samples[j + 1][0]
        ab = b - a
        L2 = ab.dot(ab)
        t = 0.0 if L2 < 1e-24 else max(0.0, min(1.0, (c - a).dot(ab) / L2))
        d = (a + ab * t - c).length()
        if best is None or d < best[0]:
            best = (d, j, t)
    return best


def _refine_param(ep, e_idx, f0, f1, c):
    """Edge parameter in [f0, f1] whose point is closest to ``c`` (golden
    section; the tangent point of a circle centred at ``c``)."""
    n = len(ep.nodes)
    kind, pt = _edge_eval(ep.nodes[e_idx], ep.nodes[(e_idx + 1) % n],
                          ep.edges[e_idx])
    lo, hi = min(f0, f1), max(f0, f1)
    if kind == "line":
        a, b = pt(0.0), pt(1.0)
        ab = b - a
        L2 = ab.dot(ab)
        return 0.0 if L2 < 1e-24 else max(0.0, min(1.0, (c - a).dot(ab) / L2))
    g = (math.sqrt(5.0) - 1.0) / 2.0
    x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
    d1, d2 = (pt(x1) - c).length(), (pt(x2) - c).length()
    for _ in range(60):
        if d1 < d2:
            hi, x2, d2 = x2, x1, d1
            x1 = hi - g * (hi - lo)
            d1 = (pt(x1) - c).length()
        else:
            lo, x1, d1 = x1, x2, d2
            x2 = lo + g * (hi - lo)
            d2 = (pt(x2) - c).length()
    return 0.5 * (lo + hi)


def _blend_plan(ep: EditablePath, i: int, r: float):
    """Where a radius-``r`` fillet at node ``i`` touches each side, or None if
    it doesn't fit: ``(i, (eA, fA, TA), (eB, fB, TB), centre)``."""
    n, m = len(ep.nodes), len(ep.edges)
    if n < 3 or m < 2:
        return None
    if not ep.closed and (i <= 0 or i >= n - 1):
        return None                              # an open path's end
    cap_edges = m // 2 if ep.closed else m
    A, lenA, endA = _side_samples(ep, i, -1, max(1, cap_edges))
    B, lenB, endB = _side_samples(ep, i, +1, max(1, cap_edges))
    if len(A) < 2 or len(B) < 2:
        return None
    # true tangents at the node (not sample chords, which on a small arc lean
    # off the tangent by half a sample step and fake a corner)
    eIn, eOut = A[0][1], B[0][1]
    _k, pin = _edge_eval(ep.nodes[eIn], ep.nodes[(eIn + 1) % n], ep.edges[eIn])
    _k, pout = _edge_eval(ep.nodes[eOut], ep.nodes[(eOut + 1) % n],
                          ep.edges[eOut])
    tA = pin(1.0) - pin(1.0 - 1e-6)              # path direction INTO the corner
    tB = pout(1e-6) - pout(0.0)                  # ...and out of it
    if tA.length() < 1e-15 or tB.length() < 1e-15:
        tA, tB = A[0][0] - A[1][0], B[1][0] - B[0][0]
    if tA.length() < 1e-12 or tB.length() < 1e-12:
        return None
    turn = math.atan2(tA.cross(tB), tA.dot(tB))
    if abs(turn) < math.radians(1.0):
        return None                              # smooth: no corner to round
    inside = 1.0 if turn > 0 else -1.0
    # each side may give up at most half its run (all of it at a free end)
    capA = lenA if endA else 0.5 * lenA
    capB = lenB if endB else 0.5 * lenB
    offA = _offset_side(A, inside, False, r)
    offB = _offset_side(B, inside, True, r)
    best = None
    for ja in range(len(offA) - 1):
        if A[ja][3] > capA:
            break
        for jb in range(len(offB) - 1):
            if B[jb][3] > capB:
                break
            if best is not None and A[ja][3] + B[jb][3] > best[0]:
                break
            hit = _seg_cross(offA[ja], offA[ja + 1], offB[jb], offB[jb + 1])
            if hit is None:
                continue
            c = offA[ja] + (offA[ja + 1] - offA[ja]) * hit[0]
            na = _nearest_on(A, c, capA)
            nb = _nearest_on(B, c, capB)
            if na is None or nb is None:
                continue
            # the circle must touch each side, not cut through it
            if abs(na[0] - r) > 0.02 * r + 1e-3 or abs(nb[0] - r) > 0.02 * r + 1e-3:
                continue
            sA = A[na[1]][3] + na[2] * (A[na[1] + 1][3] - A[na[1]][3])
            sB = B[nb[1]][3] + nb[2] * (B[nb[1] + 1][3] - B[nb[1]][3])
            if sA > capA + 1e-9 or sB > capB + 1e-9:
                continue
            if best is None or sA + sB < best[0]:
                best = (sA + sB, c, na, nb)
    if best is None:
        return None
    _, c, na, nb = best

    def touch(samples, near):
        """(edge, f, point) where a circle centred at ``c`` touches a side:
        the closest point of the TRUE edge, not of its samples."""
        j, t = near[1], near[2]
        q0, e0, f0, _s0 = samples[j]
        q1, e1, f1, _s1 = samples[j + 1]
        if e0 != e1:                             # sits on a node: snap to it
            e, f = (e0, f0) if t < 0.5 else (e1, f1)
        else:
            e = e0
            # search a little either side: the true closest point of a curved
            # edge can sit just past the sample segment that was nearest
            span = abs(f1 - f0)
            f = _refine_param(ep, e, max(0.0, min(f0, f1) - span),
                              min(1.0, max(f0, f1) + span), c)
        pt = _edge_eval(ep.nodes[e], ep.nodes[(e + 1) % n], ep.edges[e])[1]
        return e, f, pt(f)

    # The search above works on offset sample polylines, which only
    # approximate the sides' true offsets (on the long chords of a flattened
    # ellipse, to a few degrees). Refine to exact tangency: with T the true
    # closest point of each side and u the unit normal from it to the centre,
    # the centre satisfies u . c = u . T + r for both sides -- a 2x2 solve.
    for _ in range(25):
        nA = _nearest_on(A, c, capA)
        nB = _nearest_on(B, c, capB)
        if nA is None or nB is None:
            return None
        eA, fA, TA = touch(A, nA)
        eB, fB, TB = touch(B, nB)
        uA, uB = c - TA, c - TB
        if uA.length() < 1e-12 or uB.length() < 1e-12:
            return None
        uA, uB = uA * (1.0 / uA.length()), uB * (1.0 / uB.length())
        det = uA.cross(uB)
        if abs(det) < 1e-12:
            break                                # sides parallel here
        ka, kb = uA.dot(TA) + r, uB.dot(TB) + r
        c_new = Vec2((ka * uB.y - kb * uA.y) / det, (uA.x * kb - uB.x * ka) / det)
        moved = (c_new - c).length()
        c = c_new
        if moved < 1e-10:
            break
    nA, nB = _nearest_on(A, c, capA), _nearest_on(B, c, capB)
    if nA is None or nB is None:
        return None
    eA, fA, TA = touch(A, nA)
    eB, fB, TB = touch(B, nB)
    if abs((TA - c).length() - r) > 1e-6 * max(1.0, r) or \
            abs((TB - c).length() - r) > 1e-6 * max(1.0, r):
        return None                              # didn't settle on a tangent
    return i, (eA, fA, TA), (eB, fB, TB), c


def _apply_blend(ep: EditablePath, plan) -> None:
    """Replace the outline from tangent point A, round the corner, to tangent
    point B with the fillet arc, trimming the two touched edges."""
    i, (eA, fA, TA), (eB, fB, TB), c = plan
    n, m = len(ep.nodes), len(ep.edges)
    first = ep.nodes[0]
    # work on a rotation where the edited stretch doesn't wrap: edge eA first
    rot = eA if ep.closed else 0
    nodes = ep.nodes[rot:] + ep.nodes[:rot] if ep.closed else list(ep.nodes)
    edges = ep.edges[rot:] + ep.edges[:rot] if ep.closed else list(ep.edges)
    a_i = (eA - rot) % m if ep.closed else eA
    b_i = (eB - rot) % m if ep.closed else eB
    nn = len(nodes)
    head = _split_edge(nodes[a_i], nodes[(a_i + 1) % nn], edges[a_i], fA, "head")
    tail = _split_edge(nodes[b_i], nodes[(b_i + 1) % nn], edges[b_i], fB, "tail")
    r = ((TA - c).length() + (TB - c).length()) / 2.0
    bis = (TA - c) + (TB - c)
    if bis.length() < 1e-12:
        bis = Vec2(-(TB - TA).y, (TB - TA).x)
    mid = c + bis * (r / bis.length())
    # nodes a_i .. (TA) [arc] (TB) .. b_i+1 ...
    new_nodes = nodes[:a_i + 1] + [TA, TB] + nodes[b_i + 1:]
    new_edges = edges[:a_i] + [head, Edge("arc", mid=mid), tail] + edges[b_i + 1:]
    if fA <= 1e-9:                    # tangent point sits on edge eA's start
        new_nodes.pop(a_i + 1)
        new_edges.pop(a_i)
    if fB >= 1.0 - 1e-9:              # ...or on edge eB's end
        idx = new_nodes.index(TB)
        new_nodes.pop(idx)
        new_edges.pop(idx)
    if ep.closed and first in new_nodes:     # keep the original start node
        k = new_nodes.index(first)
        new_nodes = new_nodes[k:] + new_nodes[:k]
        new_edges = new_edges[k:] + new_edges[:k]
    ep.nodes[:] = new_nodes
    ep.edges[:] = new_edges


def nearest_on_edge(ep: EditablePath, i: int, p: Vec2):
    """``(f, point, distance)``: the point of edge ``i`` (line, arc or bezier,
    on its TRUE geometry) closest to ``p``, as parameter f in [0, 1]."""
    n = len(ep.nodes)
    a, b = ep.nodes[i], ep.nodes[(i + 1) % n]
    kind, pt = _edge_eval(a, b, ep.edges[i])
    if kind == "line":
        ab = b - a
        L2 = ab.dot(ab)
        f = 0.0 if L2 < 1e-24 else max(0.0, min(1.0, (p - a).dot(ab) / L2))
    else:
        steps = 64
        f = min((j / steps for j in range(steps + 1)),
                key=lambda t: (pt(t) - p).length())
        f = _refine_param(ep, i, max(0.0, f - 1.0 / steps),
                          min(1.0, f + 1.0 / steps), p)
    q = pt(f)
    return f, q, (q - p).length()


def split_edge_at(ep: EditablePath, i: int, f: float) -> bool:
    """Add a node on edge ``i`` at parameter ``f``, splitting the edge into
    two of the SAME kind so the outline doesn't move: a line into two lines,
    an arc into two arcs on the same circle, a bezier into two (de
    Casteljau). False if that point is (practically) an existing node."""
    n = len(ep.nodes)
    a, b = ep.nodes[i], ep.nodes[(i + 1) % n]
    e = ep.edges[i]
    _kind, pt = _edge_eval(a, b, e)
    q = pt(f)
    if (q - a).length() < 1e-3 or (q - b).length() < 1e-3:
        return False
    head = _split_edge(a, b, e, f, "head")
    tail = _split_edge(a, b, e, f, "tail")
    ep.nodes.insert(i + 1, q)
    ep.edges[i:i + 1] = [head, tail]
    return True


def chamfer_vertex(ep: EditablePath, i: int, setback: float) -> bool:
    """Bevel vertex ``i``: cut it back ``setback`` mm along both edges."""
    frame = _corner_frame(ep, i)
    if frame is None or setback <= 1e-9:
        return False
    pv, cur, nv, _pe, ne = frame
    v = _tangent_points(pv, cur, nv, setback)
    if v is None:
        return False
    p_start, p_end, *_rest = v
    ep.nodes[i] = p_start
    ep.nodes.insert(i + 1, p_end)
    ep.edges.insert(ne, Edge("line"))
    return True


def path_to_nodes(path):
    """A ``Path`` (lines, arcs, cubic beziers) as EditablePath ``(nodes,
    edges, closed)`` in the same coordinates -- exact, nothing flattened."""
    from .path import Arc, CubicBezier, QuadraticBezier
    segs = list(path.segments)
    if not segs:
        return [], [], path.closed
    nodes, edges = [], []
    for sg in segs:
        nodes.append(sg.start())
        if isinstance(sg, Arc):
            sweep = sg._sweep()
            edges.append(Edge("arc", mid=sg._point(sg.a0 + sweep / 2.0)))
        elif isinstance(sg, (CubicBezier, QuadraticBezier)):
            cb = sg.to_cubic() if isinstance(sg, QuadraticBezier) else sg
            edges.append(Edge("bezier", c1=cb.p1, c2=cb.p2))
        else:
            edges.append(Edge("line"))
    end = segs[-1].end()
    if path.closed:
        if (end - nodes[0]).length() > 1e-9:     # an open-ended closed path
            nodes.append(end)
            edges.append(Edge("line"))
    else:
        nodes.append(end)
    return nodes, edges, path.closed


def to_editable(sh) -> Optional[EditablePath]:
    """A Polygon/PathShape as an equivalent EditablePath (same local coords),
    carrying over everything about the shape but its geometry. A polygon's
    rounded corners come across as real arcs. EditablePath passes through."""
    from .shapes import Polygon, PathShape, copy_shape_attrs
    if isinstance(sh, EditablePath):
        return sh
    if not isinstance(sh, (Polygon, PathShape)):
        return None
    pts = list(sh.points)
    closed = bool(getattr(sh, "close_path", False))
    if len(pts) < 2:
        return None
    if getattr(sh, "corner_radius", 0.0) > 1e-9:
        # a rounded polygon: its true outline, arcs and all (taking the bare
        # points dropped the rounding from every other corner)
        nodes, edges, closed = path_to_nodes(sh.local_path())
        ep = EditablePath(nodes=nodes, edges=edges, closed=closed)
    else:
        n_edges = len(pts) if closed else len(pts) - 1
        ep = EditablePath(nodes=[Vec2(p.x, p.y) for p in pts],
                          edges=[Edge("line") for _ in range(n_edges)],
                          closed=closed)
    ep.transform = sh.transform
    copy_shape_attrs(sh, ep, holes=True)
    ep.stitch = sh.stitch                     # shared, as before
    return ep

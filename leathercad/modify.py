"""Interactive per-corner modifications: fillet (round) and chamfer (bevel).

Both operate on an :class:`EditablePath` vertex joined by two straight edges:
the vertex is replaced by two tangent points connected by an arc (fillet) or a
straight cut (chamfer). Radius/setback is clamped so it never eats more than
half of either adjoining edge.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from .geometry import Vec2
from .shapes import EditablePath, Edge


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


def fillet_vertex(ep: EditablePath, i: int, radius: float) -> bool:
    """Round vertex ``i`` with an arc of (up to) ``radius`` mm. True on success."""
    frame = _corner_frame(ep, i)
    if frame is None or radius <= 1e-9:
        return False
    pv, cur, nv, _pe, ne = frame
    v = _tangent_points(pv, cur, nv, 1.0)   # probe with unit setback for angle
    if v is None:
        return False
    _ps, _pe2, u0, u1, ang, _t = v
    setback = radius / math.tan(ang / 2.0)
    v = _tangent_points(pv, cur, nv, setback)
    p_start, p_end, u0, u1, ang, t = v
    r_eff = t * math.tan(ang / 2.0)         # radius after clamping
    bis = (u0 + u1)
    if bis.length() < 1e-9:
        return False
    bis = bis * (1.0 / bis.length())
    center = cur + bis * (r_eff / math.sin(ang / 2.0))
    to_apex = cur - center
    apex = center + to_apex * (r_eff / to_apex.length())
    # replace the corner vertex with the two tangent points + an arc edge
    ep.nodes[i] = p_start
    ep.nodes.insert(i + 1, p_end)
    ep.edges.insert(ne, Edge("arc", mid=apex))
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


def to_editable(sh) -> Optional[EditablePath]:
    """A Polygon/PathShape as an equivalent EditablePath (same local coords),
    carrying over transform/layer/stitch. EditablePath passes through."""
    from .shapes import Polygon, PathShape
    if isinstance(sh, EditablePath):
        return sh
    if not isinstance(sh, (Polygon, PathShape)):
        return None
    pts = list(sh.points)
    closed = bool(getattr(sh, "close_path", False))
    if len(pts) < 2:
        return None
    n_edges = len(pts) if closed else len(pts) - 1
    ep = EditablePath(nodes=[Vec2(p.x, p.y) for p in pts],
                      edges=[Edge("line") for _ in range(n_edges)],
                      closed=closed)
    ep.transform = sh.transform
    ep.layer = sh.layer
    ep.opacity = sh.opacity
    ep.stitch = sh.stitch
    ep.baked_holes = sh.baked_holes
    ep.name = sh.name
    return ep

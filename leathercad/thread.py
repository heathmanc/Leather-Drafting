"""Thread length estimation for saddle stitching.

A saddle-stitched seam consumes thread on BOTH faces of the leather (the two
needles alternate), plus two passes through every hole, plus tail length at
each end to hold the needles. So for a single row:

    thread = 2 * seam_length  +  2 * holes * thickness  +  2 * tail

Double rows are two separate stitch runs (each with its own tails), and a
backstitch zone re-sews its holes once more. For long seams this converges to
the classic leatherworker rule of thumb of ~3.5-4x the seam length; for short
seams the needle tails dominate -- which is exactly why the rule of thumb runs
out of thread on card wallets.
"""

from __future__ import annotations

from typing import List

from .geometry import Vec2, distance


def _polyline_len(pts: List[Vec2]) -> float:
    return sum(distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def estimate_thread(res, settings, thickness_mm: float = 3.0,
                    tail_mm: float = 150.0) -> dict:
    """Thread needed to sew one stitched item.

    ``res`` is a StitchResult, ``settings`` its StitchSettings (or any object
    with rows/backstitch attributes). ``thickness_mm`` is the TOTAL leather
    stack at the seam (all layers). Returns a dict with seam_mm, holes, rows,
    thread_mm.
    """
    n = res.count
    if n == 0:
        return {"seam_mm": 0.0, "holes": 0, "rows": 1, "thread_mm": 0.0}
    rows = int(getattr(settings, "rows", 1) or 1)
    closed = bool(getattr(res, "closed", False))
    pts = res.points

    if rows == 2 and n >= 4:
        # holes come as aligned pairs across the two rows; walk the seam along
        # the pair midpoints, and sew each row as its own run
        mids = [Vec2((pts[i].x + pts[i + 1].x) / 2.0,
                     (pts[i].y + pts[i + 1].y) / 2.0)
                for i in range(0, n - 1, 2)]
        seam = _polyline_len(mids)
        row_holes = n // 2
        runs = 2
    else:
        seam = _polyline_len(pts)
        row_holes = n
        runs = 1
        rows = 1

    per_gap = seam / max(row_holes - 1, 1)
    bs = int(getattr(settings, "backstitch", 0) or 0)
    per_run = 2.0 * seam + 2.0 * row_holes * thickness_mm + 2.0 * tail_mm
    if bs:
        ends = 1 if closed else 2
        per_run += ends * bs * (2.0 * per_gap + 2.0 * thickness_mm)
    return {"seam_mm": seam * runs, "holes": n, "rows": rows,
            "thread_mm": per_run * runs}


def format_length(mm: float) -> str:
    if mm >= 1000.0:
        return f"{mm / 1000.0:.2f} m"
    return f"{mm / 10.0:.1f} cm"

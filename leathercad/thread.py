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


def _polyline_len(pts: List[Vec2], closed: bool = False) -> float:
    n = sum(distance(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    if closed and len(pts) >= 2:
        n += distance(pts[-1], pts[0])      # the last hole back to the first
    return n


def _looks_like_pairs(pts, spacing: float) -> bool:
    """True when a bare hole list is laid out as interleaved pairs -- hole 0
    beside hole 1 a row spacing apart, then the next station. The pair gaps
    must be the row spacing (within 10 %) AND each pair must sit ACROSS the
    seam: the step from hole 0 to hole 1 is at a steep angle (over 45
    degrees) to the step from hole 0 to hole 2, the next station. In a
    row-major list every step runs along the row, so a pitch near the row
    spacing (an ordinary 3.85 mm iron with 3 mm rows, fitted to 3.7 on a
    short edge) is no longer read as pairs; and fine stations (a 1.5 mm
    pitch with 3.5 mm rows) still are."""
    n = len(pts)
    if n < 4:
        return False
    stations = range(0, min(n - 2, 8), 2)
    for i in stations:
        if abs(distance(pts[i], pts[i + 1]) - spacing) >= 0.1 * spacing:
            return False
        u = pts[i + 1] - pts[i]                 # across the pair
        v = pts[i + 2] - pts[i]                 # on to the next station
        lu, lv = u.length(), v.length()
        if lu <= 1e-9 or lv <= 1e-9:
            return False
        if abs(u.cross(v)) <= 0.7 * lu * lv:    # sin < 0.7: along the row
            return False
    return True


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
    closed = bool(getattr(res, "closed", False))
    pts = res.points
    bs = int(getattr(settings, "backstitch", 0) or 0)

    # Every separately sewn run -- each row of a double row, and each stretch
    # of a piece sewn in pieces (an unsewn edge, a hardware keep-out) -- gets
    # its own tails and its own backstitch ends. The result says how the
    # holes are laid out; a bare list of holes is one run.
    want_rows = int(getattr(settings, "rows", 1) or 1)
    spacing = float(getattr(settings, "row_spacing", 3.0) or 3.0)
    extra_tails = 0.0
    bare_double = (getattr(res, "layout", None) is None
                   and getattr(res, "rows", 1) == 1 and want_rows == 2
                   and n >= 4 and not getattr(res, "fitted", False))
    if bare_double and _looks_like_pairs(pts, spacing):
        # a bare hole list stored as aligned pairs (row A, row B) per station,
        # the way rows used to be kept -- the pairs really are a row spacing
        # apart, so read them that way
        seqs = [list(range(0, n, 2)), list(range(1, n, 2))]
        rows = 2
    elif bare_double:
        # attached (baked) holes of a double row come row-major with no
        # pairing left: sew them as one run of thread, plus the tails of the
        # second row's own run (reading them as pairs doubled the seam;
        # costing them as one run shorted the tails by a third on a small
        # piece)
        seqs = [list(range(n))]
        rows = 2
        extra_tails = 2.0 * tail_mm
    elif hasattr(res, "sequences"):
        seqs = res.sequences()
        rows = max(1, int(getattr(res, "rows", 1) or 1))
    else:                                    # pragma: no cover - old callers
        seqs = [list(range(n))]
        rows = 1
    if not seqs:                             # a lone hole: tails only
        return {"seam_mm": 0.0, "holes": n, "rows": rows,
                "thread_mm": 2.0 * thickness_mm + 2.0 * tail_mm}

    seam_total = 0.0
    thread = 0.0
    for seq in seqs:
        run_pts = [pts[i] for i in seq]
        seam = _polyline_len(run_pts, closed)
        holes = len(seq)
        # a closed seam has as many gaps as holes; an open one, one fewer
        per_gap = seam / max(holes - (0 if closed else 1), 1)
        per_run = 2.0 * seam + 2.0 * holes * thickness_mm + 2.0 * tail_mm
        if bs:
            ends = 1 if closed else 2
            per_run += ends * bs * (2.0 * per_gap + 2.0 * thickness_mm)
        seam_total += seam
        thread += per_run
    return {"seam_mm": seam_total, "holes": n, "rows": rows,
            "thread_mm": thread + extra_tails}


def format_length(mm: float) -> str:
    if mm >= 1000.0:
        return f"{mm / 1000.0:.2f} m"
    return f"{mm / 10.0:.1f} cm"

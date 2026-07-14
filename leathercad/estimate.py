"""Whole-project job estimate.

Rolls a document up into the numbers a maker actually needs to quote and cut a
job: how many pieces and holes, how much thread, how far the laser travels
(cut / score / engrave), how much leather the parts use versus their footprint
(waste), a rough laser run-time, and -- when you feed it prices -- a cost.

Everything here is pure geometry + arithmetic so it is unit-testable without
the GUI. The canvas layer formats it for display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .geometry import Vec2
from .layers import CUT, SCORE, ENGRAVE
from .offset import signed_area
from .stitching import holes_for_shape
from .stitchsettings import StitchSettings
from .thread import estimate_thread

MM2_PER_SQFT = 92903.04
MM2_PER_DM2 = 10000.0


def _seg_len(pts: List[Vec2], closed: bool) -> float:
    if len(pts) < 2:
        return 0.0
    total = sum((pts[i + 1] - pts[i]).length() for i in range(len(pts) - 1))
    if closed and (pts[0] - pts[-1]).length() > 1e-9:
        total += (pts[0] - pts[-1]).length()
    return total


def _ring(pts: List[Vec2]) -> List[Vec2]:
    if len(pts) >= 2 and (pts[0] - pts[-1]).length() < 1e-9:
        return pts[:-1]
    return pts


@dataclass
class JobEstimate:
    pieces: int = 0                 # closed cut pieces
    holes: int = 0                  # every stitch hole in the job
    thread_mm: float = 0.0
    cut_mm: float = 0.0
    score_mm: float = 0.0
    engrave_mm: float = 0.0
    parts_area_mm2: float = 0.0     # summed area of the cut pieces
    footprint_mm2: float = 0.0      # bounding box of all laser geometry
    # optional, only when the caller supplies a laser feed / prices
    laser_seconds: Optional[float] = None
    cost: Dict[str, float] = field(default_factory=dict)

    @property
    def vector_mm(self) -> float:
        return self.cut_mm + self.score_mm

    @property
    def waste_pct(self) -> float:
        if self.footprint_mm2 <= 0.0:
            return 0.0
        return max(0.0, 1.0 - self.parts_area_mm2 / self.footprint_mm2) * 100.0


def estimate_project(doc, *, thickness_mm: float = 3.0, tail_mm: float = 150.0,
                     feed_mm_s: Optional[float] = None, pierce_s: float = 0.0,
                     usable_pct: float = 75.0,
                     price_per_sqft: float = 0.0, price_thread_per_m: float = 0.0,
                     price_laser_per_min: float = 0.0) -> JobEstimate:
    """Summarise ``doc``. Laser time is included only when ``feed_mm_s`` is set;
    a cost block is added only for the prices that are non-zero."""
    est = JobEstimate()
    xs: List[float] = []
    ys: List[float] = []

    def role_of(layer_name: str) -> str:
        lyr = doc.layer(layer_name)
        return getattr(lyr, "role", CUT) if lyr is not None else CUT

    for sh in doc.shapes:
        if getattr(sh, "construction", False):
            continue
        pts, _corners, closed = sh.world_polyline()
        if len(pts) < 2:
            continue
        xs += [p.x for p in pts]
        ys += [p.y for p in pts]
        role = role_of(sh.layer)
        length = _seg_len(pts, closed)
        if role == SCORE:
            est.score_mm += length
        elif role == ENGRAVE:
            est.engrave_mm += length
        elif role == CUT:
            est.cut_mm += length
            if closed and len(pts) >= 4:
                est.pieces += 1
                est.parts_area_mm2 += abs(signed_area(_ring(pts)))
        # holes carried by the shape's stitching
        res = holes_for_shape(sh)
        if res.count:
            est.holes += res.count
            st = sh.stitch or StitchSettings()
            est.thread_mm += estimate_thread(res, st, thickness_mm,
                                             tail_mm)["thread_mm"]

    for line in doc.stitch_lines:
        res = line.result()
        if not res.count:
            continue
        est.holes += res.count
        est.thread_mm += estimate_thread(res, line.settings, thickness_mm,
                                         tail_mm)["thread_mm"]
        for h in res.holes:
            xs.append(h.point.x)
            ys.append(h.point.y)

    # individually placed (ungrouped) holes still get sewn
    for h in doc.holes:
        est.holes += 1
        xs.append(h.point.x)
        ys.append(h.point.y)

    if xs and ys:
        est.footprint_mm2 = (max(xs) - min(xs)) * (max(ys) - min(ys))

    if feed_mm_s and feed_mm_s > 0.0:
        est.laser_seconds = est.vector_mm / feed_mm_s + est.holes * pierce_s

    usable = max(usable_pct, 1.0) / 100.0
    buy_mm2 = est.parts_area_mm2 / usable
    cost: Dict[str, float] = {}
    if price_per_sqft > 0.0:
        cost["leather"] = buy_mm2 / MM2_PER_SQFT * price_per_sqft
    if price_thread_per_m > 0.0:
        cost["thread"] = est.thread_mm / 1000.0 * price_thread_per_m
    if price_laser_per_min > 0.0 and est.laser_seconds is not None:
        cost["laser"] = est.laser_seconds / 60.0 * price_laser_per_min
    if cost:
        cost["total"] = sum(cost.values())
    est.cost = cost
    return est


def _fmt_len(mm: float) -> str:
    if mm >= 1000.0:
        return f"{mm / 1000.0:.2f} m"
    return f"{mm / 10.0:.1f} cm"


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def format_report(est: JobEstimate, *, usable_pct: float = 75.0) -> str:
    if est.pieces == 0 and est.holes == 0 and est.cut_mm == 0.0:
        return "Nothing to estimate yet — draw a cut piece or a seam first."
    cm2 = est.parts_area_mm2 / 100.0
    sqft = est.parts_area_mm2 / MM2_PER_SQFT
    buy = sqft / (max(usable_pct, 1.0) / 100.0)
    lines = [
        f"Pieces (closed cut):   {est.pieces}",
        f"Stitch holes:          {est.holes}",
        f"Thread needed:         ≈ {_fmt_len(est.thread_mm)}",
        "",
        f"Cut length:            {_fmt_len(est.cut_mm)}",
    ]
    if est.score_mm:
        lines.append(f"Score length:          {_fmt_len(est.score_mm)}")
    if est.engrave_mm:
        lines.append(f"Engrave outline:       {_fmt_len(est.engrave_mm)}")
    lines += [
        "",
        f"Leather in parts:      {cm2:.1f} cm²  =  {sqft:.2f} sq ft",
        f"Buy ≈ {buy:.2f} sq ft  (at {usable_pct:g}% hide yield)",
    ]
    if est.footprint_mm2 > 0.0:
        lines.append(f"Layout waste:          {est.waste_pct:.0f}% "
                     f"(parts vs. their footprint)")
    if est.laser_seconds is not None:
        lines += ["", f"Laser run-time:        ≈ {_fmt_time(est.laser_seconds)} "
                  f"(vector only)"]
    if est.cost:
        lines.append("")
        for key in ("leather", "thread", "laser"):
            if key in est.cost:
                lines.append(f"{key.capitalize() + ' cost:':22} "
                             f"${est.cost[key]:.2f}")
        lines.append(f"{'Estimated total:':22} ${est.cost['total']:.2f}")
    return "\n".join(lines)

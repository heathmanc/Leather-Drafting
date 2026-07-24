"""The Qt-free 3D fold engine: assembling flat panels along hinges."""

import math

from leathercad.geometry import Vec2
from leathercad.fold3d import (Vec3, Panel, Hinge, assemble, rotate_view,
                               project)


def _square(x0, y0, s):
    return [Vec2(x0, y0), Vec2(x0 + s, y0), Vec2(x0 + s, y0 + s), Vec2(x0, y0 + s)]


def _placed_by_id(placed):
    return {p.id: p for p in placed}


def test_flat_when_fraction_zero():
    """A net at fraction 0 is still flat: every point keeps z = 0."""
    panels = {
        "base": Panel("base", _square(0, 0, 10)),
        "wall": Panel("wall", _square(10, 0, 10)),
    }
    hinges = [Hinge("base", "wall",
                    (Vec2(10, 0), Vec2(10, 10)),
                    (Vec2(10, 0), Vec2(10, 10)), angle_deg=90)]
    placed = assemble(panels, hinges, root="base", fraction=0.0)
    for p in placed:
        for v in p.outline:
            assert abs(v.z) < 1e-9


def test_two_panel_right_angle_fold():
    """Fold a wall 90 deg off a base: the shared edge stays put and the wall's
    far edge ends up one wall-height off the base plane."""
    panels = {
        "base": Panel("base", _square(0, 0, 10)),
        "wall": Panel("wall", _square(10, 0, 10)),
    }
    hinges = [Hinge("base", "wall",
                    (Vec2(10, 0), Vec2(10, 10)),
                    (Vec2(10, 0), Vec2(10, 10)), angle_deg=90)]
    placed = _placed_by_id(assemble(panels, hinges, root="base", fraction=1.0))

    # base is untouched (z = 0 plane)
    for v in placed["base"].outline:
        assert abs(v.z) < 1e-9

    wall = placed["wall"].outline
    # the two hinge vertices coincide with the base edge, still at z = 0
    hinge_pts = [v for v in wall if abs(v.x - 10) < 1e-6 and abs(v.z) < 1e-6]
    assert len(hinge_pts) == 2
    # the far edge is 10 mm off the plane (|z| == wall height) and still at x = 10
    far = [v for v in wall if abs(abs(v.z) - 10) < 1e-6]
    assert len(far) == 2
    for v in far:
        assert abs(v.x - 10) < 1e-6            # folded straight up about the edge


def test_hinge_edge_is_shared_exactly():
    """The child's hinge-edge vertices map onto the parent's edge in 3D."""
    panels = {
        "a": Panel("a", _square(0, 0, 10)),
        "b": Panel("b", _square(10, 0, 10)),
    }
    hinges = [Hinge("a", "b",
                    (Vec2(10, 0), Vec2(10, 10)),
                    (Vec2(10, 0), Vec2(10, 10)), angle_deg=57)]
    placed = _placed_by_id(assemble(panels, hinges, root="a", fraction=1.0))
    a_edge = {(round(v.x, 6), round(v.y, 6), round(v.z, 6))
              for v in placed["a"].outline
              if abs(v.x - 10) < 1e-6}
    b_edge = {(round(v.x, 6), round(v.y, 6), round(v.z, 6))
              for v in placed["b"].outline
              if abs(v.z) < 1e-6 and abs(v.x - 10) < 1e-6}
    assert b_edge <= a_edge                      # child edge sits on parent edge


def test_holes_fold_with_their_panel():
    """A hole on the wall travels with the wall as it folds."""
    panels = {
        "base": Panel("base", _square(0, 0, 10)),
        "wall": Panel("wall", _square(10, 0, 10), holes=[Vec2(20, 5)]),
    }
    hinges = [Hinge("base", "wall",
                    (Vec2(10, 0), Vec2(10, 10)),
                    (Vec2(10, 0), Vec2(10, 10)), angle_deg=90)]
    placed = _placed_by_id(assemble(panels, hinges, root="base", fraction=1.0))
    h = placed["wall"].holes[0]
    # the hole was on the far edge of the wall -> now 10 mm off the plane
    assert abs(h.x - 10) < 1e-6 and abs(abs(h.z) - 10) < 1e-6


def test_chain_of_three_folds_each_relative_to_its_parent():
    """A -> B -> C chain: C folds relative to B, not the root, so two 90 deg
    folds double back (C's plane is anti-parallel to A's)."""
    panels = {
        "a": Panel("a", _square(0, 0, 10)),
        "b": Panel("b", _square(10, 0, 10)),
        "c": Panel("c", _square(20, 0, 10)),
    }
    hinges = [
        Hinge("a", "b", (Vec2(10, 0), Vec2(10, 10)),
              (Vec2(10, 0), Vec2(10, 10)), angle_deg=90),
        Hinge("b", "c", (Vec2(20, 0), Vec2(20, 10)),
              (Vec2(20, 0), Vec2(20, 10)), angle_deg=90),
    ]
    placed = _placed_by_id(assemble(panels, hinges, root="a", fraction=1.0))
    # B rises to a wall one height off the base (its plane is x = 10); C then
    # folds again about B's far edge into a "lid" parallel to the base, sitting a
    # full wall-height off it and doubling back over it in x.
    b_far = [v for v in placed["b"].outline if abs(v.x - 10) < 1e-6]
    assert len(b_far) == 4                       # B is vertical: all at x = 10
    for v in placed["c"].outline:
        assert abs(abs(v.z) - 10) < 1e-6         # C is a lid one height up
        assert -1e-6 <= v.x <= 10 + 1e-6         # doubled back over the base


def test_unreachable_panel_is_returned_flat():
    """A panel with no hinge path to the root is laid out flat, not dropped."""
    panels = {
        "base": Panel("base", _square(0, 0, 10)),
        "island": Panel("island", _square(50, 0, 10)),
    }
    placed = _placed_by_id(assemble(panels, [], root="base", fraction=1.0))
    assert set(placed) == {"base", "island"}
    for v in placed["island"].outline:
        assert abs(v.z) < 1e-9


def test_cycle_in_hinges_terminates():
    """A -> B -> A style cycle must not loop forever; each panel placed once."""
    panels = {"a": Panel("a", _square(0, 0, 10)),
              "b": Panel("b", _square(10, 0, 10))}
    hinges = [
        Hinge("a", "b", (Vec2(10, 0), Vec2(10, 10)),
              (Vec2(10, 0), Vec2(10, 10)), angle_deg=90),
        Hinge("b", "a", (Vec2(10, 0), Vec2(10, 10)),
              (Vec2(10, 0), Vec2(10, 10)), angle_deg=90),
    ]
    placed = _placed_by_id(assemble(panels, hinges, root="a", fraction=1.0))
    assert set(placed) == {"a", "b"}


def test_project_sorts_back_to_front():
    """project() returns painter-ordered panels (far depth first)."""
    near = Panel("near", [Vec2(0, 0), Vec2(1, 0), Vec2(1, 1)])
    far = Panel("far", [Vec2(0, 0), Vec2(1, 0), Vec2(1, 1)])
    placed = [
        # hand-place at different z by folding is overkill; use assemble flat then
        # shift via a fake hinge-free layout
    ]
    from leathercad.fold3d import PlacedPanel
    pn = PlacedPanel("near", [Vec3(0, 0, 5)], [])
    pf = PlacedPanel("far", [Vec3(0, 0, -5)], [])
    ordered = project([pn, pf], yaw=0.0, pitch=0.0)
    assert [o[0].id for o in ordered] == ["far", "near"]


def test_rotate_view_is_isometry():
    """Camera rotation preserves lengths (it's a rigid rotation)."""
    p = Vec3(3, 4, 5)
    r = rotate_view(p, yaw=0.7, pitch=-0.4)
    assert abs(r.length() - p.length()) < 1e-9


# -- single-piece scored folding --------------------------------------------

def _rect(w, h):
    return [Vec2(0, 0), Vec2(w, 0), Vec2(w, h), Vec2(0, h)]


def test_split_polygon_by_line_halves_a_rectangle():
    from leathercad.fold3d import split_polygon_by_line
    left, right = split_polygon_by_line(_rect(100, 40), Vec2(60, 0), Vec2(60, 40))
    # the cut is at x = 60: left area is 60x40, right is 40x40
    def area(poly):
        s = 0.0
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            s += a.x * b.y - b.x * a.y
        return abs(s) / 2.0
    assert abs(area(left) - 60 * 40) < 1e-6
    assert abs(area(right) - 40 * 40) < 1e-6


def test_scored_piece_numbers_panels_and_hinges_them():
    from leathercad.fold3d import Fold, panels_from_scored_piece
    outline = _rect(240, 100)
    folds = [Fold(Vec2(80, 0), Vec2(80, 100), 150, "front"),
             Fold(Vec2(160, 0), Vec2(160, 100), 150, "back")]
    panels, hinges, order = panels_from_scored_piece(outline, folds)
    assert [panels[o].name for o in order] == ["1", "2", "3"]
    assert len(hinges) == 2                       # 1-2 and 2-3, a connected chain
    # every panel is reachable from the middle -> one connected fold graph
    placed = {p.id: p for p in assemble(panels, hinges, root=order[1],
                                        fraction=1.0)}
    assert max(abs(v.z) for v in placed[order[1]].outline) < 1e-9   # root flat
    assert max(abs(v.z) for v in placed[order[0]].outline) > 1.0    # 1 folded
    assert max(abs(v.z) for v in placed[order[2]].outline) > 1.0    # 3 folded


def test_front_and_back_fold_opposite_ways():
    from leathercad.fold3d import Fold, panels_from_scored_piece
    outline = _rect(240, 100)
    folds = [Fold(Vec2(80, 0), Vec2(80, 100), 150, "front"),
             Fold(Vec2(160, 0), Vec2(160, 100), 150, "back")]
    panels, hinges, order = panels_from_scored_piece(outline, folds)
    placed = {p.id: p for p in assemble(panels, hinges, root=order[1],
                                        fraction=1.0)}
    z1 = sum(v.z for v in placed[order[0]].outline) / 4
    z3 = sum(v.z for v in placed[order[2]].outline) / 4
    assert z1 * z3 < 0                             # one up, one down (front vs back)


def test_thickness_stacks_folded_flat_layers():
    from leathercad.fold3d import Fold, panels_from_scored_piece
    outline = _rect(200, 90)
    folds = [Fold(Vec2(100, 0), Vec2(100, 90), 180, "back")]   # fold in half, flat
    panels, hinges, order = panels_from_scored_piece(outline, folds)
    flat = {p.id: p for p in assemble(panels, hinges, root=order[0],
                                      fraction=1.0, thickness=0.0)}
    stacked = {p.id: p for p in assemble(panels, hinges, root=order[0],
                                         fraction=1.0, thickness=3.0)}
    # with zero thickness the folded panel lands in the root plane (z ~ 0);
    # thickness lifts it one layer up so it doesn't z-fight
    child = order[1]
    z_flat = sum(v.z for v in flat[child].outline) / 4
    z_stack = sum(v.z for v in stacked[child].outline) / 4
    assert abs(z_flat) < 1e-6
    assert abs(z_stack - 3.0) < 1e-6


def test_bend_allowance_adds_to_the_folded_axis():
    from leathercad.fold3d import Fold, bend_allowance
    # two vertical scores fold in X -> they grow WIDTH, not height
    folds = [Fold(Vec2(80, 0), Vec2(80, 100), 90, "front"),
             Fold(Vec2(160, 0), Vec2(160, 100), 90, "back")]
    ba = bend_allowance(folds, thickness=3.0)
    assert ba["height"] == 0.0
    assert ba["width"] > 0.0
    # each 90 deg fold: arc = (pi/2)*(r + 0.5t) with r=t=3 -> ~7.07mm, x2
    import math
    one = math.radians(90) * (3.0 + 0.5 * 3.0)
    assert abs(ba["width"] - 2 * one) < 1e-6
    assert abs(ba["total"] - 2 * one) < 1e-6


def test_horizontal_score_grows_height():
    from leathercad.fold3d import Fold, bend_allowance
    folds = [Fold(Vec2(0, 50), Vec2(120, 50), 90, "front")]   # horizontal score
    ba = bend_allowance(folds, thickness=2.0)
    assert ba["width"] == 0.0 and ba["height"] > 0.0


# -- registration: do stacked layers line up? -------------------------------

def _wallet(total_w, fold_x, pitch=5.0):
    """Return placed panels for a single-piece wallet folded flat, with a
    stitched perimeter distributed onto the panels."""
    from leathercad.fold3d import Fold, panels_from_scored_piece
    from leathercad.stitchsettings import StitchSettings
    from leathercad.stitching import holes_for_shape
    from leathercad.shapes import Rectangle, Transform
    piece = Rectangle(width=total_w, height=100,
                      transform=Transform(x=total_w / 2, y=50),
                      stitch=StitchSettings(enabled=True, pitch_mm=pitch, inset=5.0))
    holes_world = [Vec2(h.point.x, h.point.y) for h in holes_for_shape(piece).holes]
    outline = [Vec2(0, 0), Vec2(total_w, 0), Vec2(total_w, 100), Vec2(0, 100)]
    folds = [Fold(Vec2(fold_x, 0), Vec2(fold_x, 100), 180, "back")]
    panels, hinges, order = panels_from_scored_piece(outline, folds)
    for hp in holes_world:
        for pid in order:
            from leathercad.fold3d import _pt_in_poly2d
            if _pt_in_poly2d(hp, panels[pid].outline):
                panels[pid].holes.append(hp)
                break
    root = max(order, key=lambda p: len(panels[p].outline))
    return assemble(panels, hinges, root=order[0], fraction=1.0, thickness=3.0)


def test_stacked_pairs_finds_the_folded_layers():
    from leathercad.fold3d import stacked_pairs
    placed = _wallet(180, 90)
    pairs = stacked_pairs(placed, thickness=3.0)
    assert len(pairs) == 1              # the two halves stack


def test_registration_symmetric_fold_mostly_aligns():
    from leathercad.fold3d import registration_report
    placed = _wallet(180, 90)
    lines, bad = registration_report(placed, thickness=3.0, tol=1.0)
    assert lines and "COUNT MISMATCH" not in lines[0]
    # a symmetric bifold: nearly every hole registers, few (if any) flagged red
    total_flagged = sum(len(s) for s in bad.values())
    assert total_flagged <= 2


def test_registration_short_flap_flags_unreached_holes():
    from leathercad.fold3d import registration_report
    placed = _wallet(180, 120)          # fold off-centre -> flap too short
    lines, bad = registration_report(placed, thickness=3.0, tol=1.0)
    assert "short of the edge" in lines[0] or "COUNT MISMATCH" in lines[0]
    assert sum(len(s) for s in bad.values()) > 10   # many holes flagged red


def test_registration_needs_a_fold_to_stack():
    from leathercad.fold3d import registration_report, Fold, panels_from_scored_piece
    outline = _rect(180, 100)
    folds = [Fold(Vec2(90, 0), Vec2(90, 100), 90, "back")]   # 90 deg: a wall, not stacked
    panels, hinges, order = panels_from_scored_piece(outline, folds)
    placed = assemble(panels, hinges, root=order[0], fraction=1.0)
    lines, bad = registration_report(placed, thickness=3.0)
    assert "No layers are stacked" in lines[0]
    assert not bad


def test_fold_bend_allowance_single():
    from leathercad.fold3d import Fold, fold_bend_allowance
    f = Fold(Vec2(0, 0), Vec2(0, 100), 90, "back")
    # (pi/2)*(r + 0.5t) with r=t=3
    assert abs(fold_bend_allowance(f, 3.0) - math.radians(90) * (3.0 + 1.5)) < 1e-9


def test_grow_polygon_at_fold_widens_by_allowance():
    from leathercad.fold3d import Fold, grow_polygon_at_fold
    outline = _rect(180, 100)
    f = Fold(Vec2(90, 0), Vec2(90, 100), 180, "back")
    grown = grow_polygon_at_fold(outline, f, 10.0)
    xs = [p.x for p in grown]
    assert abs((max(xs) - min(xs)) - 190.0) < 1e-6      # blank got 10mm wider
    ys = [p.y for p in grown]
    assert abs((max(ys) - min(ys)) - 100.0) < 1e-6      # height unchanged
    # only the far side moved: the near edge (x=90..180 side) stays put
    assert abs(min(xs) - (-10.0)) < 1e-6 or abs(max(xs) - 190.0) < 1e-6

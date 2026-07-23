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

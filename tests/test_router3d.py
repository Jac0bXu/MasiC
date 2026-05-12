"""Foundational tests for the 3D maze router."""

from __future__ import annotations

import pytest

from masic.router3d import (
    Router3DError,
    VoxelGrid,
    find_path,
)


def test_straight_line_path():
    grid = VoxelGrid()
    path = find_path(grid, (0, 1, 0), (5, 1, 0), net="n0")
    assert path[0] == (0, 1, 0)
    assert path[-1] == (5, 1, 0)
    assert len(path) == 6


def test_obstacle_forces_detour():
    grid = VoxelGrid()
    # Wall along x=3 blocking direct east-west motion at z=0.
    for y in range(0, 4):
        grid.obstacles.add((3, y, 0))
    path = find_path(grid, (0, 1, 0), (6, 1, 0), net="n0")
    assert path[0] == (0, 1, 0)
    assert path[-1] == (6, 1, 0)
    # Path must avoid (3, 1, 0).
    assert (3, 1, 0) not in path


def test_two_nets_cannot_be_adjacent_at_same_y():
    """A second net's path is forced off the row immediately south of n0's dust."""
    grid = VoxelGrid()
    # n0 owns a long horizontal run at z=0.
    for x in range(0, 6):
        grid.claim_dust((x, 1, 0), "n0")
    # n1 must go from (0, 1, 1) to (5, 1, 1). At z=1 it would be adjacent to
    # all of n0. Walls at y=2 and y=0 around z=1 force the search to either
    # detour to z=2 (away from n0) or change y; never sit at z=1.
    path = find_path(grid, (0, 1, 2), (5, 1, 2), net="n1")
    # No node in n1's path may be adjacent to any n0 dust at the same y.
    for x, y, z in path:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y, z + dz)
            assert grid.dust_owner.get(n) != "n0", f"{(x,y,z)} would auto-bridge with n0 at {n}"


def test_two_nets_can_share_a_corner_at_different_y():
    """The adjacency check is same-y only — different y is fine."""
    grid = VoxelGrid()
    for c in [(0, 1, 0), (1, 1, 0), (2, 1, 0)]:
        grid.claim_dust(c, "n0")
    # n1 routes above n0 — different y, no adjacency conflict.
    path = find_path(grid, (0, 3, 0), (2, 3, 0), net="n1")
    assert (1, 3, 0) in path

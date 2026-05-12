"""3D maze router for redstone synthesis.

This is the "real router" — A* search over a voxel grid that bakes in
redstone's geometry: dust must sit on a solid block, dust auto-connects to
orthogonal dust at the same y (so two different nets' dust can never be
adjacent), and crossing y levels requires a staircase pattern.

The router runs nets sequentially. Each net's path becomes an obstacle for
the next net's search, both at the dust coords themselves and at the
"adjacency halo" around each dust block. The router fails loudly when a net
can't be routed; future work is rip-up-and-reroute on failure.

Public entry: `route_module_3d(module, library) → (list[RouteBlock], dict[str, Coord])`
matching the interface of `route.route_module`.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable

from .cell_library import CellSpec
from .ir import Coord, Module
from .route import RouteBlock, _max_input_offset, _cell_port_world


class Router3DError(RuntimeError):
    pass


# A* node state. We carry the last move direction so the search can model
# straight-run cost separately from turn cost if we ever want to penalize bends.
@dataclass(frozen=True)
class _Node:
    coord: Coord
    last_dir: tuple[int, int, int] | None  # (dx, dy, dz) of the previous move


@dataclass
class VoxelGrid:
    """Sparse 3D grid tracking cell obstacles and per-net dust ownership.

    `obstacles[coord]` — coords occupied by placed cells. The router must not
        cross these (it can only enter at declared port coords).
    `dust_owner[coord]` — net name that has claimed `coord` for dust. Two
        different nets cannot occupy the same coord, AND cannot occupy coords
        that are orthogonally adjacent at the same y (would auto-bridge).
    `port_coord[coord]` — the cell port at this coord (only the port itself,
        not the rest of the cell). Used so the search can land on a port
        without it counting as an obstacle.
    """

    obstacles: set[Coord] = field(default_factory=set)
    dust_owner: dict[Coord, str] = field(default_factory=dict)
    port_coord: dict[Coord, tuple[str, str, str]] = field(default_factory=dict)
    # (cell_name, port_name, side) — side = "input"|"output"

    def mark_cell_volume(self, position: Coord, footprint: Coord) -> None:
        px, py, pz = position
        fx, fy, fz = footprint
        for x in range(px, px + fx):
            for y in range(py, py + fy):
                for z in range(pz, pz + fz):
                    self.obstacles.add((x, y, z))

    def unmark_port(self, port: Coord) -> None:
        """Cell ports are inside the cell's footprint but the router needs to
        enter/exit there, so they're removed from the obstacle set."""
        self.obstacles.discard(port)

    def is_passable(self, coord: Coord, net: str) -> bool:
        """Can the router place dust at `coord` for the given net?"""
        if coord in self.obstacles:
            return False
        existing = self.dust_owner.get(coord)
        if existing is not None and existing != net:
            return False
        # Check the four horizontal orthogonal neighbors at same y. If any of
        # them belong to a DIFFERENT net, dust at `coord` would auto-bridge.
        x, y, z = coord
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y, z + dz)
            owner = self.dust_owner.get(n)
            if owner is not None and owner != net:
                return False
        return True

    def claim_dust(self, coord: Coord, net: str) -> None:
        existing = self.dust_owner.get(coord)
        if existing is not None and existing != net:
            raise Router3DError(
                f"net {net!r} cannot claim {coord}: already owned by {existing!r}"
            )
        self.dust_owner[coord] = net


# Move primitives the A* search considers. Each is (delta_x, delta_y, delta_z).
# Horizontal: same-y orthogonal step. Vertical: 1y + 1 lateral (staircase).
_HORIZONTAL = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))
_STAIR_UP = ((1, 1, 0), (-1, 1, 0), (0, 1, 1), (0, 1, -1))
_STAIR_DOWN = ((1, -1, 0), (-1, -1, 0), (0, -1, 1), (0, -1, -1))


def _manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _moves(coord: Coord, src: Coord, dst: Coord) -> Iterable[tuple[Coord, tuple[int, int, int]]]:
    """Yield candidate next coords from `coord` along with the move delta.

    The dst is the FIRST endpoint we're heading toward — used to bias staircase
    direction so we step roughly toward the target rather than away.
    """
    x, y, z = coord
    for dx, dy, dz in _HORIZONTAL + _STAIR_UP + _STAIR_DOWN:
        nx, ny, nz = x + dx, y + dy, z + dz
        yield (nx, ny, nz), (dx, dy, dz)


def find_path(
    grid: VoxelGrid,
    src: Coord,
    dst: Coord,
    net: str,
    *,
    max_iters: int = 200_000,
) -> list[Coord]:
    """A* search from src to dst, both inclusive. Returns the list of dust
    coordinates the route should occupy. Raises Router3DError on failure."""
    if src == dst:
        return [src]

    # Open set keyed by f-score. (f, counter, coord, last_dir).
    counter = 0
    open_heap: list[tuple[int, int, Coord, tuple[int, int, int] | None]] = []
    heapq.heappush(open_heap, (_manhattan(src, dst), counter, src, None))

    g_score: dict[Coord, int] = {src: 0}
    came_from: dict[Coord, Coord] = {}

    iters = 0
    while open_heap:
        iters += 1
        if iters > max_iters:
            raise Router3DError(f"A* exhausted {max_iters} iterations for net {net!r} "
                                f"from {src} to {dst}")
        _, _, current, _ = heapq.heappop(open_heap)
        if current == dst:
            return _reconstruct(came_from, src, dst)

        for nxt, _delta in _moves(current, src, dst):
            # The destination is always allowed even if it would normally be an
            # obstacle/port — that's where we're heading.
            if nxt != dst and not grid.is_passable(nxt, net):
                continue
            # Bounds sanity: keep y in [0, 32] for now to bound the search.
            if not (0 <= nxt[1] <= 32):
                continue
            tentative = g_score[current] + 1
            if tentative >= g_score.get(nxt, 10**9):
                continue
            g_score[nxt] = tentative
            came_from[nxt] = current
            f = tentative + _manhattan(nxt, dst)
            counter += 1
            heapq.heappush(open_heap, (f, counter, nxt, None))

    raise Router3DError(f"no path found for net {net!r} from {src} to {dst}")


def _reconstruct(came_from: dict[Coord, Coord], src: Coord, dst: Coord) -> list[Coord]:
    path = [dst]
    cur = dst
    while cur != src:
        cur = came_from[cur]
        path.append(cur)
    path.reverse()
    return path


def build_grid(module: Module, library: dict[str, CellSpec]) -> VoxelGrid:
    grid = VoxelGrid()
    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            continue
        spec = library[cell.cell_spec]
        grid.mark_cell_volume(cell.position, spec.footprint)
        # Every cell port is a valid entry/exit for the router.
        for port_name, port in spec.inputs.items():
            world = _cell_port_world(cell, port_name, library, "input")
            grid.unmark_port(world)
            grid.port_coord[world] = (cell.name, port_name, "input")
        for port_name, port in spec.outputs.items():
            world = _cell_port_world(cell, port_name, library, "output")
            grid.unmark_port(world)
            grid.port_coord[world] = (cell.name, port_name, "output")
    return grid

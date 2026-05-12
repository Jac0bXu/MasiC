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
    `cell_xz` — the union of (x, z) footprints of all placed cells. Used to
        forbid any routing dust above cells: wall-torches inside a cell
        strong-power the block directly above, which (when used as a stair
        carrier) leaks power into routing dust at y+1.
    """

    obstacles: set[Coord] = field(default_factory=set)
    dust_owner: dict[Coord, str] = field(default_factory=dict)
    port_coord: dict[Coord, tuple[str, str, str]] = field(default_factory=dict)
    cell_xz: set[tuple[int, int]] = field(default_factory=set)

    def mark_cell_volume(self, position: Coord, footprint: Coord) -> None:
        px, py, pz = position
        fx, fy, fz = footprint
        for x in range(px, px + fx):
            for z in range(pz, pz + fz):
                self.cell_xz.add((x, z))
                for y in range(py, py + fy):
                    self.obstacles.add((x, y, z))

    def unmark_port(self, port: Coord) -> None:
        """Cell ports are inside the cell's footprint but the router needs to
        enter/exit there, so they're removed from the obstacle set."""
        self.obstacles.discard(port)

    def is_passable(self, coord: Coord, net: str) -> bool:
        """Can the router place dust at `coord` for the given net?

        Three checks:
          1. coord itself is not a cell obstacle (and not already owned by
             a different net).
          2. The carrier block one below (x, y-1, z) is not a cell-internal
             obstacle. (For y=1 dust the carrier is at y=0 which may be the
             cell's own floor — that's a solid block, fine. For y>=2 dust
             we require the position one below to be EITHER air, an
             obstacle that is part of a cell's floor row, OR already a
             carrier; cell-logic positions are rejected.)
          3. No same-y orthogonal neighbor belongs to a different net.
        """
        if coord in self.obstacles:
            return False
        existing = self.dust_owner.get(coord)
        if existing is not None and existing != net:
            return False

        x, y, z = coord
        if y >= 2:
            # Carrier validity (see class docstring).
            below = (x, y - 1, z)
            if below in self.obstacles:
                return False
            if below in self.port_coord:
                return False

        # Forbid ALL stacked dust (any net). Dust is not a solid carrier for
        # the dust above it, so a stack always produces a floating block.
        # The position above must be air; the position below must be solid
        # (we don't track solidity precisely — but if it's dust, that's wrong).
        above = (x, y + 1, z)
        if above in self.dust_owner:
            return False
        if y >= 2 and (x, y - 1, z) in self.dust_owner:
            return False

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
    max_iters: int = 2_000_000,
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


_STAIR_COST = 5  # heavily discourage y-changes vs horizontal


def _move_cost(delta: tuple[int, int, int]) -> int:
    """Edge cost: horizontal = 1, vertical stair = _STAIR_COST."""
    return _STAIR_COST if delta[1] != 0 else 1


def find_path_multi(
    grid: VoxelGrid,
    sources: Iterable[Coord],
    dst: Coord,
    net: str,
    *,
    max_iters: int = 2_000_000,
) -> list[Coord]:
    """A* from any of `sources` (all at g=0) to `dst`. Returns the path."""
    counter = 0
    open_heap: list[tuple[int, int, Coord]] = []
    g_score: dict[Coord, int] = {}
    came_from: dict[Coord, Coord] = {}
    sources_set = set(sources)
    for s in sources_set:
        g_score[s] = 0
        heapq.heappush(open_heap, (_manhattan(s, dst), counter, s))
        counter += 1

    iters = 0
    while open_heap:
        iters += 1
        if iters > max_iters:
            raise Router3DError(
                f"A* exhausted {max_iters} iterations for net {net!r} → {dst}")
        _, _, current = heapq.heappop(open_heap)
        if current == dst:
            return _reconstruct_multi(came_from, sources_set, dst)

        for nxt, delta in _moves(current, current, dst):
            if nxt != dst and not grid.is_passable(nxt, net):
                continue
            # Bound y: 1 is the lowest legal dust level (carrier at y=0 lives
            # under the cells or as world floor we add at emit time); below y=1
            # has no carrier and the dust drops on paste.
            if not (1 <= nxt[1] <= 32):
                # Special case: destination ports themselves may be at y=0
                # (e.g. NOT/DFF cells), so the dst exception still lets us land.
                continue
            # Disallow ascending into a coord whose space above is claimed.
            if delta[1] > 0:
                cur_above = (current[0], current[1] + 1, current[2])
                if grid.dust_owner.get(cur_above) is not None:
                    continue
                if cur_above in grid.obstacles:
                    continue
            # Also forbid the symmetric case for descending: stepping into a
            # coord whose space above is owned by another net (this means we'd
            # be stacking and blocking that net's stair).
            if delta[1] < 0:
                nxt_above = (nxt[0], nxt[1] + 1, nxt[2])
                if grid.dust_owner.get(nxt_above) is not None:
                    continue
            tentative = g_score[current] + _move_cost(delta)
            if tentative >= g_score.get(nxt, 10**9):
                continue
            g_score[nxt] = tentative
            came_from[nxt] = current
            heapq.heappush(open_heap, (tentative + _manhattan(nxt, dst), counter, nxt))
            counter += 1

    raise Router3DError(f"no path found for net {net!r} → {dst}")


def _reconstruct_multi(came_from: dict[Coord, Coord], sources: set[Coord], dst: Coord) -> list[Coord]:
    path = [dst]
    cur = dst
    while cur not in sources:
        cur = came_from[cur]
        path.append(cur)
    path.reverse()
    return path


def route_one_net(
    grid: VoxelGrid,
    driver: Coord,
    loads: list[Coord],
    net: str,
) -> tuple[set[Coord], list[tuple[Coord, Coord]]]:
    """Greedy Steiner-like routing: route driver to nearest load, then keep
    extending the spanning tree to the next-nearest load from any existing dust.

    Returns (set of all dust coords, list of (parent, child) edges).
    """
    claimed: set[Coord] = {driver}
    edges: list[tuple[Coord, Coord]] = []
    remaining = list(loads)

    while remaining:
        # Pick the load with the smallest distance to any currently-claimed coord.
        best_idx = min(
            range(len(remaining)),
            key=lambda i: min(_manhattan(c, remaining[i]) for c in claimed),
        )
        target = remaining.pop(best_idx)
        path = find_path_multi(grid, claimed, target, net)
        # Append every new coord (path[0] is in claimed; skip it for edges).
        for i in range(1, len(path)):
            parent, child = path[i - 1], path[i]
            edges.append((parent, child))
            if child not in claimed:
                claimed.add(child)
                grid.claim_dust(child, net)
    return claimed, edges


def build_grid(module: Module, library: dict[str, CellSpec]) -> VoxelGrid:
    """Construct the routing grid:
      - Cell volumes are obstacles.
      - Cell ports are punched out of obstacles (entry/exit points).
      - The "halo" — the 1-block ring around each cell at the port y level — is
        added to obstacles too, because routing dust placed in the halo ends up
        adjacent to cell-internal torches/dust and picks up spurious power.
        Port "approach" coords (one block west of each input port and one east
        of each output port) are explicitly KEPT clear so routes can still
        enter and leave at ports.
    """
    grid = VoxelGrid()
    port_approaches: set[Coord] = set()
    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            continue
        spec = library[cell.cell_spec]
        grid.mark_cell_volume(cell.position, spec.footprint)
        for port_name in spec.inputs:
            world = _cell_port_world(cell, port_name, library, "input")
            grid.unmark_port(world)
            grid.port_coord[world] = (cell.name, port_name, "input")
            port_approaches.add((world[0] - 1, world[1], world[2]))
        for port_name in spec.outputs:
            world = _cell_port_world(cell, port_name, library, "output")
            grid.unmark_port(world)
            grid.port_coord[world] = (cell.name, port_name, "output")
            port_approaches.add((world[0] + 1, world[1], world[2]))

    # Halo pass. For each cell, add the perimeter at port y to obstacles,
    # except where it overlaps a port approach.
    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            continue
        spec = library[cell.cell_spec]
        px, py, pz = cell.position
        fx, fy, fz = spec.footprint
        port_y = py + fy - 1
        cell_xz = {(x, z) for x in range(px, px + fx) for z in range(pz, pz + fz)}
        for cx, cz in cell_xz:
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, nz = cx + dx, cz + dz
                if (nx, nz) in cell_xz:
                    continue
                halo = (nx, port_y, nz)
                if halo in port_approaches:
                    continue
                grid.obstacles.add(halo)
    return grid


def _module_io_positions_3d(
    module: Module,
    library: dict[str, CellSpec],
) -> dict[str, Coord]:
    """External lever/lamp positions. Each port aligns z with the first cell
    port it connects to; module inputs sit west of all cells, outputs east.
    """
    if not module.cells:
        return {}
    xs = [c.position[0] for c in module.cells.values() if c.position]
    min_x, max_x = min(xs), max(xs)
    max_fx = max(library[c.cell_spec].footprint[0] for c in module.cells.values() if c.cell_spec)
    margin = _max_input_offset(library) + 2
    west_edge = min_x - margin
    east_edge = max_x + max_fx + margin
    route_y = max(library[c.cell_spec].footprint[1] - 1
                  for c in module.cells.values() if c.cell_spec)

    used: set[tuple[int, int]] = set()  # (edge_x, z) pairs
    pos: dict[str, Coord] = {}

    def _pick(edge_x: int, preferred_z: int) -> int:
        z = preferred_z
        while (edge_x, z) in used:
            z += 2
        used.add((edge_x, z))
        return z

    for port in module.ports:
        net = module.nets.get(port.name)
        if net is None:
            continue
        if port.direction == "input":
            if not net.loads:
                continue
            load_cell = module.cells[net.loads[0][0]]
            load_port = net.loads[0][1]
            spec = library[load_cell.cell_spec]
            preferred = load_cell.position[2] + spec.inputs[load_port].coord[2]
            z = _pick(west_edge, preferred)
            pos[port.name] = (west_edge, route_y, z)
        else:
            if net.driver is None:
                continue
            driver_cell = module.cells[net.driver[0]]
            driver_port = net.driver[1]
            spec = library[driver_cell.cell_spec]
            preferred = driver_cell.position[2] + spec.outputs[driver_port].coord[2]
            z = _pick(east_edge, preferred)
            pos[port.name] = (east_edge, route_y, z)
    return pos


def route_module_3d(
    module: Module,
    library: dict[str, CellSpec],
) -> tuple[list[RouteBlock], dict[str, Coord]]:
    """Route every net with the A* maze router. Returns the same (blocks, ports)
    tuple as `route.route_module` for drop-in use by the emitter."""
    grid = build_grid(module, library)
    ports = _module_io_positions_3d(module, library)

    blocks: list[RouteBlock] = []
    input_port_names = {p.name for p in module.ports if p.direction == "input"}
    output_port_names = {p.name for p in module.ports if p.direction == "output"}
    for port_name, coord in ports.items():
        if port_name in input_port_names:
            blocks.append(RouteBlock(coord=coord, kind="lever"))
        elif port_name in output_port_names:
            blocks.append(RouteBlock(coord=coord, kind="lamp"))
        blocks.append(RouteBlock(coord=(coord[0], coord[1] - 1, coord[2]), kind="floor"))
        grid.unmark_port(coord)
        grid.port_coord[coord] = ("__module__", port_name,
                                  "input" if port_name in input_port_names else "output")

    # Build the per-net work list: (net_name, driver_coord, [load_coords])
    work: list[tuple[str, Coord, list[Coord]]] = []
    for net in module.nets.values():
        if net.name in ports and net.driver is None:
            driver = ports[net.name]  # module input lever
        elif net.driver is not None:
            inst, port = net.driver
            cell = module.cells.get(inst)
            if cell is None:
                continue
            driver = _cell_port_world(cell, port, library, "output")
        else:
            continue

        loads: list[Coord] = []
        for inst, port in net.loads:
            cell = module.cells.get(inst)
            if cell is None:
                continue
            loads.append(_cell_port_world(cell, port, library, "input"))
        if net.name in output_port_names and net.name in ports:
            loads.append(ports[net.name])
        if not loads:
            continue
        work.append((net.name, driver, loads))

    # Net-ordering heuristic: route nets with the most loads first; tie-break
    # on total Manhattan span. Hard nets eat space before easy ones.
    def priority(w):
        name, driver, loads = w
        span = sum(_manhattan(driver, ld) for ld in loads)
        return (-len(loads), -span, name)
    work.sort(key=priority)

    # Pre-claim every driver and load coord so other nets cannot route through them.
    for net_name, driver, loads in work:
        grid.claim_dust(driver, net_name)
        for ld in loads:
            grid.claim_dust(ld, net_name)

    # Route each net. The driver is the seed; loads are visited greedily.
    port_coords = {c for c in (ports.values())}
    cell_port_coords = set(grid.port_coord) - port_coords
    for net_name, driver, loads in work:
        claimed, edges = route_one_net(grid, driver, loads, net_name)
        # Place dust + floor for every claimed coord.
        for coord in claimed:
            x, y, z = coord
            blocks.append(RouteBlock(coord=coord, kind="dust"))
            blocks.append(RouteBlock(coord=(x, y - 1, z), kind="floor"))
        # Insert repeaters every <= 14 dust-blocks along the path from
        # driver outward. Don't place repeaters at cell ports or stair steps.
        repeater_coords = _pick_repeater_coords(driver, edges, cell_port_coords)
        for coord, facing in repeater_coords:
            blocks.append(RouteBlock(coord=coord, kind="repeater", facing=facing))

    return blocks, ports


def _pick_repeater_coords(driver, edges, port_coords):
    """Walk the path tree from `driver` and pick coords every ~14 dust-steps
    along straight horizontal runs to convert into repeaters."""
    # Build adjacency from edges (undirected for traversal, directed for facing).
    children: dict = {}
    parent_of: dict = {}
    for parent, child in edges:
        children.setdefault(parent, []).append(child)
        parent_of[child] = parent

    repeaters: list[tuple[Coord, str]] = []
    visited: set = {driver}
    # BFS with cumulative distance reset on repeater placement.
    queue: list[tuple[Coord, int]] = [(driver, 0)]
    while queue:
        cur, dist = queue.pop(0)
        for nxt in children.get(cur, ()):
            if nxt in visited:
                continue
            visited.add(nxt)
            dx = nxt[0] - cur[0]
            dy = nxt[1] - cur[1]
            dz = nxt[2] - cur[2]
            is_stair = dy != 0
            new_dist = dist + 1
            place_here = (
                new_dist >= 14
                and not is_stair
                and nxt not in port_coords
            )
            if place_here:
                facing = _facing_from_delta(dx, dz)
                if facing is not None:
                    repeaters.append((nxt, facing))
                    new_dist = 0
            queue.append((nxt, new_dist))
    return repeaters


def _facing_from_delta(dx: int, dz: int) -> str | None:
    if dx == 1: return "east"
    if dx == -1: return "west"
    if dz == 1: return "south"
    if dz == -1: return "north"
    return None

"""Routing: wires every IR net between cell ports (and module I/O) as redstone dust.

Multi-lane router. Each net gets its own z-lane in a routing channel north of
the cell row, and each load at a cell picks a unique x-offset so that drops
from different nets at the same cell never share a column.

Routes are entirely at the cell-port y; no vertical staircases. The
"lane × offset" scheme is enough because:

  - Different nets sit at different z lanes (so their horizontal segments
    don't share dust).
  - Different loads at the same cell drop at different x columns (so the
    vertical south-drop segments don't bridge).
  - Each load's east-tail at the load's own z dust runs at a unique z per
    load (z = port.z), so eastward tails from different loads of the same
    cell are 2 z apart and don't auto-connect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .cell_library import CellSpec
from .ir import Coord, Module


@dataclass(frozen=True)
class RouteBlock:
    coord: Coord
    kind: str        # "dust" | "floor" | "repeater" | "lever" | "lamp"
    facing: str = "east"


class RoutingError(ValueError):
    pass


_LANE_SPACING = 2  # z-blocks between adjacent lanes (prevents dust bridging)


def _cell_port_world(cell, port_name: str, library: dict[str, CellSpec], side: str) -> Coord:
    if cell.position is None or cell.cell_spec is None:
        raise RoutingError(f"cell {cell.name!r} not placed/mapped")
    spec = library[cell.cell_spec]
    table = spec.inputs if side == "input" else spec.outputs
    if port_name not in table:
        raise RoutingError(f"{cell.name}: no {side} port {port_name!r}")
    lx, ly, lz = table[port_name].coord
    px, py, pz = cell.position
    return (px + lx, py + ly, pz + lz)


def _routing_y(module: Module, library: dict[str, CellSpec]) -> int:
    return max(library[c.cell_spec].footprint[1] - 1
               for c in module.cells.values() if c.cell_spec)


def _net_iter_for_lane_assignment(module: Module) -> Iterable[str]:
    for net in module.nets.values():
        if net.driver is not None or net.loads:
            yield net.name


def _assign_lanes(module: Module, library: dict[str, CellSpec]) -> dict[str, int]:
    """Map each net name to a z-lane.

    Nets that have exactly one endpoint (one load and no driver, or one driver
    and one external lamp) get aligned directly with that endpoint's z so the
    route is a straight east-west line with no drop. This avoids the
    drop-through-other-lanes problem that plagues multi-input cells when there
    are only a few nets to route. Other nets get their own staggered lanes.
    """
    base_z = min((c.position[2] for c in module.cells.values() if c.position), default=0)
    output_names = {p.name for p in module.ports if p.direction == "output"}

    # First pass: nets that can be aligned with their unique endpoint's z.
    aligned: dict[str, int] = {}
    other_names: list[str] = []
    for name in _net_iter_for_lane_assignment(module):
        net = module.nets[name]
        n_loads = len(net.loads)
        is_module_output = name in output_names
        if net.driver is None and n_loads == 1 and not is_module_output:
            # Module input feeding exactly one cell.
            load_cell = module.cells[net.loads[0][0]]
            load_port = net.loads[0][1]
            spec = library[load_cell.cell_spec]
            aligned[name] = load_cell.position[2] + spec.inputs[load_port].coord[2]
        elif net.driver is not None and n_loads == 0 and is_module_output:
            # Cell output feeding only the module output lamp.
            driver_cell = module.cells[net.driver[0]]
            driver_port = net.driver[1]
            spec = library[driver_cell.cell_spec]
            aligned[name] = driver_cell.position[2] + spec.outputs[driver_port].coord[2]
        else:
            other_names.append(name)

    # Second pass: remaining nets get unique staggered lanes that don't collide
    # with any already-aligned z.
    used_zs = set(aligned.values())
    next_lane = base_z - 1
    lanes = dict(aligned)
    for name in other_names:
        while next_lane in used_zs:
            next_lane -= 1
        lanes[name] = next_lane
        used_zs.add(next_lane)
        next_lane -= _LANE_SPACING
    return lanes


def _load_offset(spec: CellSpec, port_name: str) -> int:
    """How many blocks west of the cell to start this load's south drop.

    Each input port gets a unique offset based on its declaration order in the
    cell's manifest. Different loads of the same cell get different drop columns.
    """
    return list(spec.inputs).index(port_name) + 1


def _max_input_offset(library: dict[str, CellSpec]) -> int:
    """Largest load offset any library cell could produce."""
    return max((len(spec.inputs) for spec in library.values()), default=1)


def module_io_positions(
    module: Module,
    library: dict[str, CellSpec],
    *,
    margin: int | None = None,
) -> dict[str, Coord]:
    """Pick a world position for each module-level port.

    External levers (inputs) and lamps (outputs) sit on their net's lane so
    horizontal travel doesn't need a z-bend.
    """
    if not module.cells:
        return {}
    if margin is None:
        margin = _max_input_offset(library) + 1
    xs = [c.position[0] for c in module.cells.values() if c.position]
    min_x, max_x = min(xs), max(xs)
    max_fx = max(library[c.cell_spec].footprint[0] for c in module.cells.values() if c.cell_spec)
    east_edge = max_x + max_fx + margin
    west_edge = min_x - margin
    route_y = _routing_y(module, library)

    lanes = _assign_lanes(module, library)
    pos: dict[str, Coord] = {}
    for port in module.ports:
        net = module.nets.get(port.name)
        if net is None:
            continue
        lane_z = lanes.get(port.name)
        if lane_z is None:
            continue
        if port.direction == "input":
            pos[port.name] = (west_edge, route_y, lane_z)
        else:
            pos[port.name] = (east_edge, route_y, lane_z)
    return pos


def route_module(
    module: Module,
    library: dict[str, CellSpec],
    *,
    routing_y: int | None = None,
) -> tuple[list[RouteBlock], dict[str, Coord]]:
    if routing_y is None:
        routing_y = _routing_y(module, library)

    lanes = _assign_lanes(module, library)
    ports = module_io_positions(module, library)
    input_port_names = {p.name for p in module.ports if p.direction == "input"}
    output_port_names = {p.name for p in module.ports if p.direction == "output"}

    blocks: list[RouteBlock] = []
    for port_name, port_coord in ports.items():
        if port_name in input_port_names:
            blocks.append(RouteBlock(coord=port_coord, kind="lever"))
        elif port_name in output_port_names:
            blocks.append(RouteBlock(coord=port_coord, kind="lamp"))
        blocks.append(RouteBlock(coord=(port_coord[0], port_coord[1] - 1, port_coord[2]),
                                 kind="floor"))

    for net in module.nets.values():
        lane_z = lanes.get(net.name)
        if lane_z is None:
            continue
        src = _net_source(net, module, library, ports)
        if src is None:
            continue
        loads: list[tuple[Coord, int]] = []
        for load_inst, load_port in net.loads:
            load_cell = module.cells.get(load_inst)
            if load_cell is None:
                continue
            coord = _cell_port_world(load_cell, load_port, library, "input")
            offset = _load_offset(library[load_cell.cell_spec], load_port)
            loads.append((coord, offset))
        # Module output: lamp acts like a no-offset load at lane_z.
        if net.name in output_port_names:
            loads.append((ports[net.name], 0))
        if not loads:
            continue
        blocks.extend(_route_net(src, loads, lane_z, routing_y))
    return blocks, ports


def _route_net(src: Coord, loads: list[tuple[Coord, int]],
               lane_z: int, y: int) -> list[RouteBlock]:
    blocks: list[RouteBlock] = []
    sx, _, sz = src

    # Source drop to lane.
    if sz != lane_z:
        for z in _inclusive_range(sz, lane_z):
            blocks.append(RouteBlock(coord=(sx, y, z), kind="dust"))
            blocks.append(RouteBlock(coord=(sx, y - 1, z), kind="floor"))

    # Horizontal lane spans from min source/drop x to max drop/endpoint x.
    drop_xs = [lx - off for (lx, _, _), off in loads]
    min_x = min([sx] + drop_xs)
    max_x = max([sx] + [lx for (lx, _, _), _ in loads])
    for x in range(min_x, max_x + 1):
        blocks.append(RouteBlock(coord=(x, y, lane_z), kind="dust"))
        blocks.append(RouteBlock(coord=(x, y - 1, lane_z), kind="floor"))

    # Repeaters every 14 blocks along the lane.
    for i, x in enumerate(range(min_x, max_x + 1)):
        if i > 0 and i % 14 == 0:
            blocks.append(RouteBlock(coord=(x, y, lane_z), kind="repeater", facing="east"))

    # Per-load south drop + east tail.
    for (lx, _, lz), offset in loads:
        if offset == 0:
            # Endpoint sits on the lane; nothing more to do (lamp case).
            continue
        drop_x = lx - offset
        for z in _inclusive_range(lane_z, lz):
            blocks.append(RouteBlock(coord=(drop_x, y, z), kind="dust"))
            blocks.append(RouteBlock(coord=(drop_x, y - 1, z), kind="floor"))
        for x in range(drop_x + 1, lx + 1):
            blocks.append(RouteBlock(coord=(x, y, lz), kind="dust"))
            blocks.append(RouteBlock(coord=(x, y - 1, lz), kind="floor"))
    return blocks


def _inclusive_range(a: int, b: int) -> Iterable[int]:
    step = 1 if b >= a else -1
    return range(a, b + step, step)


def _net_source(net, module, library, ports) -> Coord | None:
    if net.driver is not None:
        inst, port = net.driver
        cell = module.cells.get(inst)
        if cell is None:
            return None
        return _cell_port_world(cell, port, library, "output")
    if net.name in ports and any(p.direction == "input" and p.name == net.name for p in module.ports):
        return ports[net.name]
    return None


def detect_collisions(module: Module, library: dict[str, CellSpec]) -> dict[Coord, set[str]]:
    from collections import defaultdict

    by_coord: dict[Coord, list[str]] = defaultdict(list)
    lanes = _assign_lanes(module, library)
    ports = module_io_positions(module, library)
    routing_y = _routing_y(module, library)
    output_port_names = {p.name for p in module.ports if p.direction == "output"}

    for net in module.nets.values():
        lane_z = lanes.get(net.name)
        if lane_z is None:
            continue
        src = _net_source(net, module, library, ports)
        if src is None:
            continue
        loads: list[tuple[Coord, int]] = []
        for load_inst, load_port in net.loads:
            load_cell = module.cells.get(load_inst)
            if load_cell is None:
                continue
            coord = _cell_port_world(load_cell, load_port, library, "input")
            offset = _load_offset(library[load_cell.cell_spec], load_port)
            loads.append((coord, offset))
        if net.name in output_port_names:
            loads.append((ports[net.name], 0))
        for rb in _route_net(src, loads, lane_z, routing_y):
            if rb.kind == "dust":
                by_coord[rb.coord].append(net.name)
    return {c: set(nets) for c, nets in by_coord.items() if len(set(nets)) > 1}

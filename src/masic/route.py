"""Routing: wires every IR net between cell ports (and module I/O) as redstone dust.

Phase 4b naive router. Produces `RouteBlock` descriptors that the emitter
stamps into the schematic.

What it does:
  - Resolves cell ports + module-level input/output port positions to world coords.
  - Lays an L-shaped Manhattan dust path on a single routing y level between
    each net's driver and each load.
  - Drops a stone floor block beneath every dust block.
  - Inserts a repeater every 14 blocks to stay under redstone's 15-block range.

What it does NOT do (yet):
  - Wire-over-wire crossings (paths may collide silently).
  - Vertical drops to ports at differing y levels.
  - Fan-out limits or buffer insertion.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cell_library import CellSpec
from .ir import Coord, Module


@dataclass(frozen=True)
class RouteBlock:
    coord: Coord
    kind: str        # "dust" | "floor" | "repeater" | "lever" | "lamp"
    facing: str = "east"


class RoutingError(ValueError):
    pass


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


def module_io_positions(
    module: Module,
    library: dict[str, CellSpec],
    *,
    margin: int = 2,
) -> dict[str, Coord]:
    """Pick a world position for each module-level port (external levers/lamps).

    Each external lever (input) is placed at the same z and y as the FIRST cell
    port it connects to, so the route is a straight east-west line — no z-bend.
    Same for output lamps. This avoids the dust-convergence bug where two L-
    shaped routes share a column and accidentally bridge through adjacent dust.
    """
    if not module.cells:
        return {}
    xs = [c.position[0] for c in module.cells.values() if c.position]
    min_x, max_x = min(xs), max(xs)
    max_fx = max(library[c.cell_spec].footprint[0] for c in module.cells.values() if c.cell_spec)
    east_edge = max_x + max_fx + margin
    west_edge = min_x - margin

    pos: dict[str, Coord] = {}
    for port in module.ports:
        if port.direction == "input":
            target = _first_load_port_coord(module, library, port.name)
            if target is None:
                continue
            pos[port.name] = (west_edge, target[1], target[2])
        else:
            target = _driver_port_coord(module, library, port.name)
            if target is None:
                continue
            pos[port.name] = (east_edge, target[1], target[2])
    return pos


def _first_load_port_coord(module: Module, library: dict[str, CellSpec], net_name: str) -> Coord | None:
    net = module.nets.get(net_name)
    if net is None or not net.loads:
        return None
    cell_name, port_name = net.loads[0]
    cell = module.cells.get(cell_name)
    if cell is None:
        return None
    return _cell_port_world(cell, port_name, library, "input")


def _driver_port_coord(module: Module, library: dict[str, CellSpec], net_name: str) -> Coord | None:
    net = module.nets.get(net_name)
    if net is None or net.driver is None:
        return None
    cell_name, port_name = net.driver
    cell = module.cells.get(cell_name)
    if cell is None:
        return None
    return _cell_port_world(cell, port_name, library, "output")


def detect_collisions(module: Module, library: dict[str, CellSpec]) -> dict[Coord, set[str]]:
    """Return {dust_coord: {net_names...}} for every coord where >1 net would share dust.

    The naive L-router runs every net on the same y level, so nets crossing or
    sharing a channel will collide. Use this to warn the user that the build
    is likely shorted before they load it.
    """
    from collections import defaultdict

    by_coord: dict[Coord, list[str]] = defaultdict(list)
    ports = module_io_positions(module, library)
    cell_outputs = {p.name for p in module.ports if p.direction == "output"}
    routing_y = max(library[c.cell_spec].footprint[1] - 1
                    for c in module.cells.values() if c.cell_spec)

    for net in module.nets.values():
        src = _net_source(net, module, library, ports)
        if src is None:
            continue
        for load_inst, load_port in net.loads:
            load_cell = module.cells.get(load_inst)
            if load_cell is None:
                continue
            dst = _cell_port_world(load_cell, load_port, library, "input")
            for rb in _route_one(src, dst, routing_y):
                if rb.kind == "dust":
                    by_coord[rb.coord].append(net.name)
        if net.name in cell_outputs:
            for rb in _route_one(src, ports[net.name], routing_y):
                if rb.kind == "dust":
                    by_coord[rb.coord].append(net.name)

    return {c: set(nets) for c, nets in by_coord.items() if len(set(nets)) > 1}


def route_module(
    module: Module,
    library: dict[str, CellSpec],
    *,
    routing_y: int | None = None,
) -> tuple[list[RouteBlock], dict[str, Coord]]:
    """Return (wire blocks, module-port → world coord) for a placed+mapped module."""
    if routing_y is None:
        routing_y = max(library[c.cell_spec].footprint[1] - 1
                        for c in module.cells.values() if c.cell_spec)

    ports = module_io_positions(module, library)
    cell_inputs = {p.name for p in module.ports if p.direction == "input"}
    cell_outputs = {p.name for p in module.ports if p.direction == "output"}

    blocks: list[RouteBlock] = []
    for port_name, port_coord in ports.items():
        if port_name in cell_inputs:
            blocks.append(RouteBlock(coord=port_coord, kind="lever"))
            blocks.append(RouteBlock(coord=(port_coord[0], port_coord[1] - 1, port_coord[2]), kind="floor"))
        else:
            blocks.append(RouteBlock(coord=port_coord, kind="lamp"))
            blocks.append(RouteBlock(coord=(port_coord[0], port_coord[1] - 1, port_coord[2]), kind="floor"))

    for net in module.nets.values():
        src = _net_source(net, module, library, ports)
        if src is None:
            continue
        for load_inst, load_port in net.loads:
            load_cell = module.cells.get(load_inst)
            if load_cell is None:
                continue
            dst = _cell_port_world(load_cell, load_port, library, "input")
            blocks.extend(_route_one(src, dst, routing_y))

        # Module output: net name matches an output port. The net's driver feeds
        # an external lamp; we still need a path from driver to that lamp.
        if net.name in cell_outputs:
            lamp_pos = ports[net.name]
            blocks.extend(_route_one(src, lamp_pos, routing_y))
    return blocks, ports


def _net_source(net, module, library, ports) -> Coord | None:
    """A net's signal source — a cell output, or a module input port."""
    if net.driver is not None:
        inst, port = net.driver
        cell = module.cells.get(inst)
        if cell is None:
            return None
        return _cell_port_world(cell, port, library, "output")
    if net.name in ports and any(p.direction == "input" and p.name == net.name for p in module.ports):
        return ports[net.name]
    return None


def _route_one(src: Coord, dst: Coord, y: int) -> list[RouteBlock]:
    sx, _, sz = src
    dx, _, dz = dst
    path: list[Coord] = []

    step_x = 1 if dx >= sx else -1
    for x in range(sx, dx + step_x, step_x):
        path.append((x, y, sz))

    step_z = 1 if dz >= sz else -1
    if sz != dz:
        for z in range(sz + step_z, dz + step_z, step_z):
            path.append((dx, y, z))

    blocks: list[RouteBlock] = []
    for i, c in enumerate(path):
        blocks.append(RouteBlock(coord=c, kind="dust"))
        blocks.append(RouteBlock(coord=(c[0], c[1] - 1, c[2]), kind="floor"))
        if i > 0 and i % 14 == 0:
            blocks.append(
                RouteBlock(coord=c, kind="repeater", facing="east" if step_x > 0 else "west")
            )
    return blocks

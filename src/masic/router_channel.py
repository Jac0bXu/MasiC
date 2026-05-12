"""Channel routing inspired by MinecraftHDL.

Strategy:
  1. Each cell exposes "pins" on its north face — a coord per input and per
     output, all at z = cell.z - 1, each at a unique x.
  2. A per-cell "redirector" wire is laid OUTSIDE the cell that ferries the
     signal between the cell's actual east/west port and its declared north-face
     pin. Redirectors stay at port y (no stairs).
  3. Each net is assigned a unique y "track" above the pin row. Different nets
     never share y, so their horizontal tracks can't bridge.
  4. For each net, signal goes: driver-pin → straight-up staircase to the net's
     track at (driver_pin.x, y_track, ...) → east along track → straight-down
     staircase at each load's pin x → load-pin.
  5. Repeater every <=14 blocks along the horizontal track.

Geometry guarantees by construction:
  - No two tracks share y, so no cross-net bridge at the track wire.
  - Pin columns at unique x per pin, so different nets' columns don't share x.
  - Staircase steps follow the dust-conduction rule (dy=±1, dz=±1, corner
    above lower dust stays air).
"""

from __future__ import annotations

from typing import Iterable

from .cell_library import CellSpec
from .ir import Coord, Module
from .route import RouteBlock, _max_input_offset


class ChannelRouterError(RuntimeError):
    pass


def _routing_y(module, library):
    return max(library[c.cell_spec].footprint[1] - 1
               for c in module.cells.values() if c.cell_spec)


def _pin_positions(module: Module, library: dict[str, CellSpec]) -> dict[tuple[str, str], Coord]:
    """Per-cell N-face pin coords. Map (cell_name, port_name) → pin coord."""
    pins: dict[tuple[str, str], Coord] = {}
    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            continue
        spec = library[cell.cell_spec]
        cx, cy, cz = cell.position
        port_y = cy + spec.footprint[1] - 1
        # Inputs: line up west of the cell, north face. Index by declaration order.
        for i, port_name in enumerate(spec.inputs):
            pin = (cx - 1 - i, port_y, cz - 1)
            pins[(cell.name, port_name)] = pin
        # Outputs: line up east of the cell, north face.
        for i, port_name in enumerate(spec.outputs):
            pin = (cx + spec.footprint[0] + i, port_y, cz - 1)
            pins[(cell.name, port_name)] = pin
    return pins


def _redirector(cell, port_name: str, side: str, pin: Coord,
                spec: CellSpec) -> list[RouteBlock]:
    """Wires that connect the cell's actual port to its N-face pin, staying
    outside the cell footprint. Inputs route west-then-north; outputs route
    east-then-north."""
    px, py, pz = cell.position
    if side == "input":
        port_local = spec.inputs[port_name].coord
    else:
        port_local = spec.outputs[port_name].coord
    port_world = (px + port_local[0], py + port_local[1], pz + port_local[2])
    blocks: list[RouteBlock] = []

    if side == "input":
        # From port_world, step west to pin's x, then step north to pin's z.
        first_step = (pin[0], port_world[1], port_world[2])  # west to pin.x at port.z
        for x in range(port_world[0] - 1, first_step[0] - 1, -1):
            blocks.append(RouteBlock(coord=(x, port_world[1], port_world[2]), kind="dust"))
            blocks.append(RouteBlock(coord=(x, port_world[1] - 1, port_world[2]), kind="floor"))
        # Then step north from port.z to pin.z.
        for z in range(port_world[2] - 1, pin[2] - 1, -1):
            blocks.append(RouteBlock(coord=(pin[0], port_world[1], z), kind="dust"))
            blocks.append(RouteBlock(coord=(pin[0], port_world[1] - 1, z), kind="floor"))
    else:
        # Output: east-then-north.
        for x in range(port_world[0] + 1, pin[0] + 1):
            blocks.append(RouteBlock(coord=(x, port_world[1], port_world[2]), kind="dust"))
            blocks.append(RouteBlock(coord=(x, port_world[1] - 1, port_world[2]), kind="floor"))
        for z in range(port_world[2] - 1, pin[2] - 1, -1):
            blocks.append(RouteBlock(coord=(pin[0], port_world[1], z), kind="dust"))
            blocks.append(RouteBlock(coord=(pin[0], port_world[1] - 1, z), kind="floor"))
    return blocks


def _channel_route(driver_pin: Coord, load_pins: list[Coord],
                   y_track: int) -> list[RouteBlock]:
    """Ascend from driver_pin to y=y_track (via straight-north staircase), run
    east along the track, descend at each load.x."""
    blocks: list[RouteBlock] = []
    dx, dy, dz = driver_pin
    ascend_steps = y_track - dy

    # Driver-side staircase: dust + floor at (dx, dy+i, dz-i) for i in 0..ascend_steps.
    for i in range(ascend_steps + 1):
        c = (dx, dy + i, dz - i)
        blocks.append(RouteBlock(coord=c, kind="dust"))
        blocks.append(RouteBlock(coord=(c[0], c[1] - 1, c[2]), kind="floor"))

    track_z = dz - ascend_steps
    # Horizontal track at (x, y_track, track_z) east to max load pin x.
    xs = [dx] + [lp[0] for lp in load_pins]
    min_x, max_x = min(xs), max(xs)
    for x in range(min_x, max_x + 1):
        c = (x, y_track, track_z)
        blocks.append(RouteBlock(coord=c, kind="dust"))
        blocks.append(RouteBlock(coord=(x, y_track - 1, track_z), kind="floor"))
    # Repeater every 14 blocks for signal restoration.
    for i, x in enumerate(range(min_x + 1, max_x), start=1):
        if i % 14 == 0:
            blocks.append(RouteBlock(coord=(x, y_track, track_z),
                                     kind="repeater",
                                     facing="east" if x > dx else "west"))

    # Load-side descents: ascend_steps stair-down from (lx, y_track, track_z)
    # to (lx, dy, track_z + ascend_steps). Then horizontal east at y=dy to load
    # pin's z if it differs.
    for lp in load_pins:
        lx, ly, lz = lp
        steps_down = y_track - ly
        for i in range(steps_down + 1):
            c = (lx, y_track - i, track_z + i)
            blocks.append(RouteBlock(coord=c, kind="dust"))
            blocks.append(RouteBlock(coord=(c[0], c[1] - 1, c[2]), kind="floor"))
        # If descent landing z != lp.z, walk south at y=ly to bridge.
        landing_z = track_z + steps_down
        if landing_z != lz:
            step = 1 if lz > landing_z else -1
            for z in range(landing_z + step, lz + step, step):
                c = (lx, ly, z)
                blocks.append(RouteBlock(coord=c, kind="dust"))
                blocks.append(RouteBlock(coord=(lx, ly - 1, z), kind="floor"))
    return blocks


def _module_io_positions(module: Module, library: dict[str, CellSpec],
                         pin_map: dict[tuple[str, str], Coord]) -> dict[str, Coord]:
    """Position external levers/lamps to the west/east of all cell pins, each
    sitting at a unique x at the same y/z as a pin row."""
    if not module.cells:
        return {}
    xs = [c.position[0] for c in module.cells.values() if c.position]
    min_x = min(xs)
    max_fx = max(library[c.cell_spec].footprint[0]
                 for c in module.cells.values() if c.cell_spec)
    pin_row_z = min(c.position[2] for c in module.cells.values() if c.position) - 1
    port_y = _routing_y(module, library)

    pos: dict[str, Coord] = {}
    # Each module port gets its own x in a strip far west or east of cells.
    west_strip = min_x - _max_input_offset(library) - 4
    east_strip = max(c.position[0] for c in module.cells.values() if c.position) + max_fx + 3
    next_west_x = west_strip
    next_east_x = east_strip
    for port in module.ports:
        if port.direction == "input":
            pos[port.name] = (next_west_x, port_y, pin_row_z)
            next_west_x -= 2
        else:
            pos[port.name] = (next_east_x, port_y, pin_row_z)
            next_east_x += 2
    return pos


def route_module_channel(module: Module,
                         library: dict[str, CellSpec]
                         ) -> tuple[list[RouteBlock], dict[str, Coord]]:
    """Channel-routing main entry. Returns (route_blocks, module-port positions).
    """
    pin_map = _pin_positions(module, library)
    io_pos = _module_io_positions(module, library, pin_map)

    blocks: list[RouteBlock] = []
    input_names = {p.name for p in module.ports if p.direction == "input"}
    output_names = {p.name for p in module.ports if p.direction == "output"}
    for port_name, c in io_pos.items():
        if port_name in input_names:
            blocks.append(RouteBlock(coord=c, kind="lever"))
        else:
            blocks.append(RouteBlock(coord=c, kind="lamp"))
        blocks.append(RouteBlock(coord=(c[0], c[1] - 1, c[2]), kind="floor"))

    # Lay all per-cell redirectors first.
    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            continue
        spec = library[cell.cell_spec]
        for port_name in spec.inputs:
            blocks.extend(_redirector(
                cell, port_name, "input", pin_map[(cell.name, port_name)], spec))
        for port_name in spec.outputs:
            blocks.extend(_redirector(
                cell, port_name, "output", pin_map[(cell.name, port_name)], spec))

    # Assign each net a unique y_track.
    nets_to_route: list[tuple[str, Coord, list[Coord]]] = []
    for net in module.nets.values():
        if net.driver is not None:
            drv_pin = pin_map[net.driver]
        elif net.name in io_pos and net.name in input_names:
            drv_pin = io_pos[net.name]
        else:
            continue
        load_pins: list[Coord] = []
        for ld in net.loads:
            if ld in pin_map:
                load_pins.append(pin_map[ld])
        if net.name in output_names and net.name in io_pos:
            load_pins.append(io_pos[net.name])
        if not load_pins:
            continue
        nets_to_route.append((net.name, drv_pin, load_pins))

    # Order: longest x-span first, so dense lanes go up first.
    def span(w):
        _, drv, lds = w
        xs = [drv[0]] + [lp[0] for lp in lds]
        return max(xs) - min(xs)
    nets_to_route.sort(key=lambda w: (-span(w), w[0]))

    y_base = _routing_y(module, library) + 2
    for i, (net_name, drv, lds) in enumerate(nets_to_route):
        y_track = y_base + i
        blocks.extend(_channel_route(drv, lds, y_track))

    return blocks, io_pos

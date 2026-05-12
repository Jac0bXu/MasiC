"""Schematic emitter: cells + routing + external I/O → one .schem or .litematic.

`.schem` (Sponge Schematic v3) is the default output format because it loads
directly with WorldEdit's `//schem load && //paste`. `.litematic` is still
supported for Litematica compatibility — pick by the output filename extension.
"""

from __future__ import annotations

from pathlib import Path

import mcschematic
from litemapy import BlockState, Region, Schematic

from .cell_library import CellSpec
from .ir import Module
from .place import cell_footprint
from .route import RouteBlock, module_io_positions, route_module
from .router3d import route_module_3d


class EmitError(ValueError):
    pass


_FLOOR = BlockState("minecraft:stone")
_DUST = BlockState("minecraft:redstone_wire")
_AIR = BlockState("minecraft:air")
_LEVER = BlockState("minecraft:lever", face="floor", facing="south")
_LAMP = BlockState("minecraft:redstone_lamp")
_REPEATERS = {
    "east": BlockState("minecraft:repeater", facing="east", delay="1"),
    "west": BlockState("minecraft:repeater", facing="west", delay="1"),
    "north": BlockState("minecraft:repeater", facing="north", delay="1"),
    "south": BlockState("minecraft:repeater", facing="south", delay="1"),
}


def emit(
    module: Module,
    library: dict[str, CellSpec],
    out_path: Path,
    *,
    name: str | None = None,
    with_routing: bool = True,
    router: str = "2d",
) -> Path:
    """Write the full circuit. Output format = filename extension.

    `.schem`     → Sponge v3, loadable via WorldEdit (`//schem load && //paste`)
    `.litematic` → Litematica's format
    `router`     → "2d" (default, lane-based) or "3d" (A* maze).
    """
    out_path = Path(out_path)
    suffix = out_path.suffix.lower()
    if suffix not in (".schem", ".litematic"):
        raise EmitError(f"unsupported output extension {suffix!r}; want .schem or .litematic")
    if not module.cells:
        raise EmitError(f"module {module.name!r} has no cells to emit")

    if with_routing:
        if router == "3d":
            route_blocks, ports = route_module_3d(module, library)
        elif router == "2d":
            route_blocks, ports = route_module(module, library)
        else:
            raise EmitError(f"unknown router {router!r}; want '2d' or '3d'")
    else:
        route_blocks, ports = [], {}

    cell_bbox = cell_footprint(module, library)
    bbox = _expand_bbox(cell_bbox, route_blocks, ports)

    # Pad each axis by 1 to leave room for the route layer's floor below y=0.
    # Origin is shifted into positive land before stamping.
    ox = -bbox[0]
    oy = -bbox[2]
    oz = -bbox[4]
    sx = bbox[1] - bbox[0] + 1
    sy = bbox[3] - bbox[2] + 1
    sz = bbox[5] - bbox[4] + 1
    region = Region(0, 0, 0, sx, sy, sz)

    # Stamp cells first (with internal port-block stripping so dust can take over).
    internal_port_coords = _internal_port_set(module, library)
    for cell in module.cells.values():
        spec = library[cell.cell_spec]
        _stamp_cell(region, spec, cell.position, (ox, oy, oz), internal_port_coords)

    # External I/O (levers, lamps) first, then dust/repeaters that respect them.
    reserved: set = set()
    for rb in route_blocks:
        if rb.kind in ("lever", "lamp"):
            _apply_route_block(region, rb, (ox, oy, oz))
            reserved.add((rb.coord[0] + ox, rb.coord[1] + oy, rb.coord[2] + oz))
    for rb in route_blocks:
        if rb.kind not in ("lever", "lamp"):
            _apply_route_block(region, rb, (ox, oy, oz), reserved=reserved)

    if suffix == ".litematic":
        schematic = region.as_schematic(name=name or module.name)
        schematic.save(str(out_path))
    else:  # .schem
        _save_as_schem(region, out_path)
    return out_path


# Backwards-compatibility alias for callers that still say emit_litematic.
emit_litematic = emit


def _save_as_schem(region: Region, out_path: Path) -> None:
    """Convert the in-memory litemapy region to a Sponge .schem via mcschematic."""
    schem = mcschematic.MCSchematic()
    for x in region.range_x():
        for y in region.range_y():
            for z in region.range_z():
                block = region[x, y, z]
                if block.id == "minecraft:air":
                    continue
                schem.setBlock((x, y, z), repr(block))
    folder = str(out_path.parent if str(out_path.parent) else ".")
    stem = out_path.stem
    schem.save(folder, stem, mcschematic.Version.JE_1_20_1)


def _expand_bbox(cell_bbox, route_blocks, ports):
    min_x = 0
    max_x = cell_bbox[0]
    min_y = -1  # routing floor sits at cell_y - 1
    max_y = cell_bbox[1]
    min_z = 0
    max_z = cell_bbox[2]
    for rb in route_blocks:
        x, y, z = rb.coord
        min_x = min(min_x, x); max_x = max(max_x, x + 1)
        min_y = min(min_y, y); max_y = max(max_y, y + 1)
        min_z = min(min_z, z); max_z = max(max_z, z + 1)
    for x, y, z in ports.values():
        min_x = min(min_x, x); max_x = max(max_x, x + 1)
        min_y = min(min_y, y); max_y = max(max_y, y + 1)
        min_z = min(min_z, z); max_z = max(max_z, z + 1)
    return (min_x, max_x, min_y, max_y, min_z, max_z)


def _internal_port_set(module: Module, library: dict[str, CellSpec]) -> set:
    """World coordinates of input/output ports that should be replaced by dust."""
    coords = set()
    for cell in module.cells.values():
        spec = library[cell.cell_spec]
        px, py, pz = cell.position
        for table in (spec.inputs, spec.outputs):
            for port in table.values():
                lx, ly, lz = port.coord
                coords.add((px + lx, py + ly, pz + lz))
    return coords


def _stamp_cell(region, spec, position, origin_shift, internal_port_coords):
    """Copy a cell's schematic blocks into `region`, replacing port lever/lamp with dust."""
    ox, oy, oz = origin_shift
    px, py, pz = position
    src_schem = Schematic.load(str(spec.schematic_path))
    if len(src_schem.regions) != 1:
        raise EmitError(f"{spec.name!r}: expected one schematic region")
    src = next(iter(src_schem.regions.values()))

    xs, ys, zs = list(src.range_x()), list(src.range_y()), list(src.range_z())
    sx, sy, sz = min(xs), min(ys), min(zs)

    for x in xs:
        for y in ys:
            for z in zs:
                block = src[x, y, z]
                if block.id == "minecraft:air":
                    continue
                world_x = px + (x - sx)
                world_y = py + (y - sy)
                world_z = pz + (z - sz)
                dest_coord = (world_x + ox, world_y + oy, world_z + oz)

                if (world_x, world_y, world_z) in internal_port_coords \
                        and block.id in ("minecraft:lever", "minecraft:stone_button", "minecraft:redstone_lamp"):
                    region[dest_coord] = _DUST
                    # Ensure a floor block exists beneath the new dust.
                    below = (dest_coord[0], dest_coord[1] - 1, dest_coord[2])
                    if region[below].id == "minecraft:air":
                        region[below] = _FLOOR
                else:
                    region[dest_coord] = block


def _apply_route_block(region, rb: RouteBlock, origin_shift, *, reserved: set | None = None):
    ox, oy, oz = origin_shift
    x, y, z = rb.coord[0] + ox, rb.coord[1] + oy, rb.coord[2] + oz
    if reserved is not None and (x, y, z) in reserved:
        return
    if rb.kind == "dust":
        region[(x, y, z)] = _DUST
    elif rb.kind == "floor":
        cur = region[(x, y, z)]
        if cur.id == "minecraft:air":
            region[(x, y, z)] = _FLOOR
    elif rb.kind == "repeater":
        region[(x, y, z)] = _REPEATERS.get(rb.facing, _REPEATERS["east"])
    elif rb.kind == "lever":
        region[(x, y, z)] = _LEVER
    elif rb.kind == "lamp":
        region[(x, y, z)] = _LAMP
    else:
        raise EmitError(f"unknown route block kind: {rb.kind}")

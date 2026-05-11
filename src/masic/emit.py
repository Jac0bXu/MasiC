"""Schematic emitter: stamps placed cells (and eventually routes) into a
single .litematic that Litematica can load into a Minecraft world.

For Phase 4a this only handles cell stamping — no routing. A single-cell
circuit (like `and2`) produces a valid, testable build because each cell's
hand-built schematic already contains its own input levers and output lamp.
Multi-cell circuits need Phase 4b routing to wire cells together.
"""

from __future__ import annotations

from pathlib import Path

from litemapy import Region, Schematic

from .cell_library import CellSpec
from .ir import Module
from .place import cell_footprint


class EmitError(ValueError):
    pass


def emit_litematic(
    module: Module,
    library: dict[str, CellSpec],
    out_path: Path,
    *,
    name: str | None = None,
) -> Path:
    """Write a `.litematic` containing every placed cell stamped at its position."""
    out_path = Path(out_path)
    if not module.cells:
        raise EmitError(f"module {module.name!r} has no cells to emit")

    bbox = cell_footprint(module, library)
    region = Region(0, 0, 0, bbox[0], bbox[1], bbox[2])

    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            raise EmitError(f"cell {cell.name!r} is not placed or not tech-mapped")
        spec = library[cell.cell_spec]
        if spec.schematic_path is None or not spec.schematic_path.exists():
            raise EmitError(f"cell {cell.name!r}: missing schematic at {spec.schematic_path}")
        _stamp(region, spec, cell.position)

    schematic = region.as_schematic(name=name or module.name)
    schematic.save(str(out_path))
    return out_path


def _stamp(dest: Region, spec: CellSpec, origin) -> None:
    """Copy the cell's schematic blocks into `dest` at `origin`."""
    ox, oy, oz = origin
    src_schem = Schematic.load(str(spec.schematic_path))
    if len(src_schem.regions) != 1:
        raise EmitError(
            f"cell {spec.name!r}: expected exactly one region in schematic, "
            f"got {list(src_schem.regions)}"
        )
    src = next(iter(src_schem.regions.values()))

    # Normalize the source region so its bounding box starts at (0, 0, 0).
    xs = list(src.range_x())
    ys = list(src.range_y())
    zs = list(src.range_z())
    sx, sy, sz = min(xs), min(ys), min(zs)

    for x in xs:
        for y in ys:
            for z in zs:
                block = src[x, y, z]
                if block.id == "minecraft:air":
                    continue
                dest[ox + (x - sx), oy + (y - sy), oz + (z - sz)] = block

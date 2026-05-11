"""Placement: assigns 3D coordinates to each tech-mapped cell.

Phase 4 starts with a dumb grid placer. Each cell is dropped into a column
of a coarse grid, with enough air around it for routing layers above and
between. Smarter algorithms (simulated annealing, analytical) replace this
later behind the same contract.
"""

from __future__ import annotations

import math

from .cell_library import CellSpec
from .ir import Coord, Module


class PlacementError(ValueError):
    pass


def place(
    module: Module,
    library: dict[str, CellSpec],
    *,
    routing_gap: int = 3,
) -> Module:
    """Assign positions to every cell in `module`. Mutates and returns it.

    Cells are laid out on a roughly square grid in the XZ plane. Each grid
    slot is wide enough to hold the largest cell in the library plus
    `routing_gap` blocks of space on every side for routing.
    """
    if not module.cells:
        return module

    specs = [library[c.cell_spec] for c in module.cells.values() if c.cell_spec]
    if len(specs) != len(module.cells):
        raise PlacementError("place() requires every cell to be tech-mapped first")

    slot_w = max(s.footprint[0] for s in specs) + routing_gap
    slot_d = max(s.footprint[2] for s in specs) + routing_gap

    n = len(module.cells)
    cols = max(1, math.ceil(math.sqrt(n)))

    for i, cell in enumerate(module.cells.values()):
        col, row = i % cols, i // cols
        cell.position = (col * slot_w, 0, row * slot_d)

    return module


def cell_footprint(module: Module, library: dict[str, CellSpec]) -> Coord:
    """Bounding box of all placed cells, in (x, y, z) blocks."""
    max_x = max_y = max_z = 0
    for cell in module.cells.values():
        if cell.position is None or cell.cell_spec is None:
            raise PlacementError(f"cell {cell.name!r} not placed/mapped")
        spec = library[cell.cell_spec]
        px, py, pz = cell.position
        fx, fy, fz = spec.footprint
        max_x = max(max_x, px + fx)
        max_y = max(max_y, py + fy)
        max_z = max(max_z, pz + fz)
    return (max_x, max_y, max_z)

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
    strategy: str = "row",
    routing_gap: int = 3,
    z_margin: int = 2,
) -> Module:
    """Assign positions to every cell in `module`. Mutates and returns it.

    strategy="grid": square grid in the XZ plane. Each slot fits the biggest cell + routing_gap.
    strategy="row":  one cell per column along +x, all sharing z=z_margin. Simpler for the
                    naive router which only does L-shaped paths at a single y-level.
    """
    if not module.cells:
        return module

    specs = [library[c.cell_spec] for c in module.cells.values() if c.cell_spec]
    if len(specs) != len(module.cells):
        raise PlacementError("place() requires every cell to be tech-mapped first")

    if strategy == "grid":
        slot_w = max(s.footprint[0] for s in specs) + routing_gap
        slot_d = max(s.footprint[2] for s in specs) + routing_gap
        cols = max(1, math.ceil(math.sqrt(len(module.cells))))
        for i, cell in enumerate(module.cells.values()):
            col, row = i % cols, i // cols
            cell.position = (col * slot_w, 0, row * slot_d)
    elif strategy == "row":
        order = _topo_order(module)
        ordered_cells = [module.cells[name] for name in order]
        ordered_specs = [library[c.cell_spec] for c in ordered_cells]
        x = routing_gap
        for cell, spec in zip(ordered_cells, ordered_specs, strict=True):
            cell.position = (x, 0, z_margin)
            x += spec.footprint[0] + routing_gap
    else:
        raise PlacementError(f"unknown placement strategy {strategy!r}")

    return module


def _topo_order(module: Module) -> list[str]:
    """Return cell names in dependency order (drivers before loads).

    Cells loading only module input ports come first; cells loading only
    other cells come later. Ties broken by insertion order so the result is
    stable.
    """
    # cell name → set of cell names whose output it depends on
    deps: dict[str, set[str]] = {name: set() for name in module.cells}
    for net in module.nets.values():
        if net.driver is None:
            continue
        driver_cell = net.driver[0]
        for load_cell, _ in net.loads:
            if load_cell in deps and driver_cell in module.cells:
                deps[load_cell].add(driver_cell)

    visited: set[str] = set()
    order: list[str] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for dep in deps.get(name, ()):
            visit(dep)
        order.append(name)

    for name in module.cells:
        visit(name)
    return order


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

"""Technology mapping: Yosys gate types → cells in our redstone library.

Walks an `ir.Module` and assigns each cell a `cell_spec` referencing a
CellSpec in the loaded library. The mapping is name-based: every cell's
manifest declares `yosys_types: [...]`, listing which Yosys cell types
(`$_AND_`, `$_NAND_`, etc.) it implements.

This is a 1:1 mapper — one Yosys gate becomes one redstone cell. Buffering,
fan-out limits, and gate fusion are not handled here. If the design has
fan-out higher than what a single redstone cell can drive, later stages
(routing, buffer insertion) need to deal with it.
"""

from __future__ import annotations

from .cell_library import CellSpec
from .ir import Module


class TechMapError(ValueError):
    pass


def build_type_index(library: dict[str, CellSpec]) -> dict[str, str]:
    """Index: Yosys cell type → name of the library cell that covers it."""
    index: dict[str, str] = {}
    for spec in library.values():
        for yt in spec.yosys_types:
            if yt in index:
                raise TechMapError(
                    f"two cells claim {yt!r}: {index[yt]!r} and {spec.name!r}"
                )
            index[yt] = spec.name
    return index


def tech_map(module: Module, library: dict[str, CellSpec]) -> Module:
    """Annotate each cell in `module` with a `cell_spec` from `library`.

    Mutates and returns the same module. Raises TechMapError on the first
    Yosys gate type not covered by any library cell.
    """
    index = build_type_index(library)
    unmapped: set[str] = set()
    for cell in module.cells.values():
        if cell.type in index:
            cell.cell_spec = index[cell.type]
        else:
            unmapped.add(cell.type)
    if unmapped:
        raise TechMapError(
            f"no cell library covers Yosys types: {sorted(unmapped)}; "
            f"library covers: {sorted(index)}"
        )
    return module

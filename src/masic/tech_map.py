"""Technology mapping: generic gates in IR → library cell instances."""

from __future__ import annotations

from .cell_library import CellSpec
from .ir import Module


def tech_map(module: Module, library: dict[str, CellSpec]) -> Module:
    raise NotImplementedError

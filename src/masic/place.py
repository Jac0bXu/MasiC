"""Placement: assigns 3D coordinates to each cell in the module.

Phase 4 starts with a dumb grid placer. Each cell is placed at
(i * pitch, 0, j * pitch) with generous spacing; smarter algorithms
(simulated annealing, analytical) replace this later behind the same
contract.
"""

from __future__ import annotations

from .ir import Module


def place(module: Module, pitch: int = 10) -> Module:
    raise NotImplementedError

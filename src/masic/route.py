"""Routing: wires nets between placed cells.

Constraints to respect:
    - Redstone dust attenuates over 15 blocks → insert a repeater every
      <= 14 blocks.
    - Repeaters and comparators are directional.
    - Use layer separation (cells at y=0, routing at y=3+) to avoid
      cell/wire collisions; bump up another layer for wire/wire crossings.
"""

from __future__ import annotations

from .ir import Module


def route(module: Module) -> Module:
    raise NotImplementedError

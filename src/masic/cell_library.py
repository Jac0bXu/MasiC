"""Redstone standard cell library.

Each cell is a hand-built .litematic in cells/<name>/cell.litematic plus a
YAML manifest in cells/<name>/manifest.yaml declaring:
    footprint: [dx, dy, dz]
    inputs: {A: [x, y, z], B: [x, y, z], ...}
    outputs: {Y: [x, y, z], ...}
    delay_ticks: <int>
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ir import Coord


@dataclass
class CellSpec:
    name: str
    footprint: Coord
    inputs: dict[str, Coord]
    outputs: dict[str, Coord]
    delay_ticks: int
    schematic_path: Path


def load_library(cells_dir: Path) -> dict[str, CellSpec]:
    raise NotImplementedError

"""Intermediate representation.

The IR is the contract between every pipeline stage. Stages read it, mutate
the relevant fields in place, and hand it on. Position/orientation/route are
filled in by later stages and are None before that stage runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Coord = tuple[int, int, int]


@dataclass
class Cell:
    name: str
    type: str
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, int | str] = field(default_factory=dict)
    position: Coord | None = None
    orientation: str | None = None
    cell_spec: str | None = None  # set by tech_map; names a CellSpec in the library


@dataclass
class Net:
    name: str
    driver: tuple[str, str] | None = None
    loads: list[tuple[str, str]] = field(default_factory=list)
    route: list[Coord] | None = None


@dataclass
class Port:
    name: str
    direction: str
    width: int


@dataclass
class Module:
    name: str
    ports: list[Port] = field(default_factory=list)
    cells: dict[str, Cell] = field(default_factory=dict)
    nets: dict[str, Net] = field(default_factory=dict)

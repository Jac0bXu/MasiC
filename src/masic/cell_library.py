"""Redstone standard cell library.

Each cell lives in its own directory under `cells/<name>/`:

    cells/<name>/manifest.yaml   — port coordinates, footprint, delay, truth table
    cells/<name>/cell.litematic  — the schematic, built by hand in Minecraft creative

A manifest looks like:

    name: NAND2
    yosys_types: ["$_NAND_"]      # which Yosys gate types this cell implements
    family: combinational         # combinational | sequential
    footprint: [3, 2, 5]          # width (x), height (y), depth (z) in blocks
    ports:
      inputs:
        A: {coord: [0, 0, 0], kind: lever}
        B: {coord: [0, 0, 4], kind: lever}
      outputs:
        Y: {coord: [2, 0, 2], kind: lamp}
    delay_ticks: 2
    truth_table:                  # required for combinational, omit for sequential
      - {A: 0, B: 0, Y: 1}
      - {A: 0, B: 1, Y: 1}
      - {A: 1, B: 0, Y: 1}
      - {A: 1, B: 1, Y: 0}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ir import Coord


class CellManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Port:
    name: str
    coord: Coord
    kind: str  # lever, lamp, dust, repeater, …


@dataclass
class CellSpec:
    name: str
    yosys_types: list[str]
    family: str  # "combinational" | "sequential"
    footprint: Coord
    inputs: dict[str, Port]
    outputs: dict[str, Port]
    delay_ticks: int
    truth_table: list[dict[str, int]] = field(default_factory=list)
    schematic_path: Path | None = None
    manifest_path: Path | None = None
    # Cell-internal coords (relative to the normalized footprint) of blocks
    # that emit redstone power outward: wall_torches, comparator outputs,
    # repeater outputs. The router uses these to forbid only the halo coords
    # adjacent to actual power emitters, not the whole cell perimeter.
    power_emitters: list[Coord] = field(default_factory=list)

    @property
    def has_schematic(self) -> bool:
        return self.schematic_path is not None and self.schematic_path.exists()

    def verify_combinational_truth_table(self) -> None:
        """Check that the truth table covers every input combination exactly once."""
        if self.family != "combinational":
            return
        n = len(self.inputs)
        if len(self.truth_table) != 2**n:
            raise CellManifestError(
                f"{self.name}: truth table has {len(self.truth_table)} rows, "
                f"expected {2**n} for {n} inputs"
            )
        seen: set[tuple[int, ...]] = set()
        in_names = sorted(self.inputs)
        out_names = sorted(self.outputs)
        for row in self.truth_table:
            key = tuple(row[k] for k in in_names)
            if key in seen:
                raise CellManifestError(f"{self.name}: duplicate truth-table row {row}")
            seen.add(key)
            for k in in_names + out_names:
                if k not in row:
                    raise CellManifestError(f"{self.name}: row missing {k!r}: {row}")
                if row[k] not in (0, 1):
                    raise CellManifestError(f"{self.name}: non-binary value in row {row}")


def load_cell(manifest_path: Path) -> CellSpec:
    manifest_path = Path(manifest_path)
    raw = yaml.safe_load(manifest_path.read_text())
    try:
        spec = _parse_manifest(raw, manifest_path)
    except (KeyError, TypeError) as e:
        raise CellManifestError(f"{manifest_path}: {e}") from e
    _validate_geometry(spec)
    spec.verify_combinational_truth_table()
    return spec


def load_library(cells_dir: Path) -> dict[str, CellSpec]:
    """Load every cells/*/manifest.yaml under cells_dir into a name → CellSpec map."""
    cells_dir = Path(cells_dir)
    library: dict[str, CellSpec] = {}
    for manifest in sorted(cells_dir.glob("*/manifest.yaml")):
        spec = load_cell(manifest)
        if spec.name in library:
            raise CellManifestError(f"duplicate cell name {spec.name!r}")
        library[spec.name] = spec
    return library


def _parse_manifest(raw: dict, manifest_path: Path) -> CellSpec:
    inputs = {n: _port(n, p) for n, p in raw["ports"]["inputs"].items()}
    outputs = {n: _port(n, p) for n, p in raw["ports"]["outputs"].items()}
    schematic = manifest_path.parent / raw.get("schematic", "cell.litematic")
    emitters = [tuple(c) for c in raw.get("power_emitters", [])]
    return CellSpec(
        name=raw["name"],
        yosys_types=list(raw.get("yosys_types", [])),
        family=raw["family"],
        footprint=tuple(raw["footprint"]),  # type: ignore[arg-type]
        inputs=inputs,
        outputs=outputs,
        delay_ticks=int(raw["delay_ticks"]),
        truth_table=list(raw.get("truth_table", [])),
        schematic_path=schematic,
        manifest_path=manifest_path,
        power_emitters=emitters,  # type: ignore[arg-type]
    )


def _port(name: str, p: dict) -> Port:
    return Port(name=name, coord=tuple(p["coord"]), kind=p.get("kind", "lever"))  # type: ignore[arg-type]


def _validate_geometry(spec: CellSpec) -> None:
    fx, fy, fz = spec.footprint
    if min(fx, fy, fz) <= 0:
        raise CellManifestError(f"{spec.name}: footprint must be positive, got {spec.footprint}")
    coords: dict[Coord, str] = {}
    for port in (*spec.inputs.values(), *spec.outputs.values()):
        x, y, z = port.coord
        if not (0 <= x < fx and 0 <= y < fy and 0 <= z < fz):
            raise CellManifestError(
                f"{spec.name}: port {port.name} at {port.coord} outside footprint {spec.footprint}"
            )
        if port.coord in coords:
            raise CellManifestError(
                f"{spec.name}: ports {coords[port.coord]} and {port.name} share {port.coord}"
            )
        coords[port.coord] = port.name
    if spec.family not in ("combinational", "sequential"):
        raise CellManifestError(f"{spec.name}: bad family {spec.family!r}")
    if spec.delay_ticks < 0:
        raise CellManifestError(f"{spec.name}: delay_ticks must be ≥ 0")

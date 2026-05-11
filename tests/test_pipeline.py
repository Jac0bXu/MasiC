"""End-to-end pipeline test: SV/V → IR → tech map → place → .litematic."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from litemapy import Schematic

from masic import cell_library, emit, frontend, place, tech_map

REPO = Path(__file__).parent.parent
FIXTURES = REPO / "tests" / "handwritten"
CELLS = REPO / "cells"


@pytest.fixture(autouse=True)
def _require_toolchain():
    if shutil.which("yosys") is None:
        pytest.skip("yosys not on PATH")


@pytest.fixture(scope="module")
def library():
    return cell_library.load_library(CELLS)


def test_tech_map_and2_to_AND2(library):
    module = frontend.synthesize(FIXTURES / "and2.v", top="and2", gate_set=["AND"])
    tech_map.tech_map(module, library)
    [cell] = module.cells.values()
    assert cell.cell_spec == "AND2"


def test_tech_map_full_adder_uses_library_cells(library):
    module = frontend.synthesize(FIXTURES / "full_adder.v", top="full_adder",
                                  gate_set=["AND", "NAND", "OR"])
    tech_map.tech_map(module, library)
    used = {c.cell_spec for c in module.cells.values()}
    assert used <= {"AND2", "NAND2", "OR2", "NOT"}


def test_tech_map_rejects_uncovered_type(library):
    """If Yosys produces a gate the library doesn't cover, fail clearly."""
    module = frontend.synthesize(FIXTURES / "full_adder.v", top="full_adder")
    with pytest.raises(tech_map.TechMapError, match="no cell library covers"):
        tech_map.tech_map(module, library)


def test_place_assigns_positions(library):
    module = frontend.synthesize(FIXTURES / "full_adder.v", top="full_adder",
                                  gate_set=["AND", "NAND", "OR"])
    tech_map.tech_map(module, library)
    place.place(module, library)
    positions = {c.position for c in module.cells.values()}
    assert None not in positions
    assert len(positions) == len(module.cells)  # no two cells overlap


def test_emit_and2_produces_loadable_litematic(library, tmp_path):
    module = frontend.synthesize(FIXTURES / "and2.v", top="and2", gate_set=["AND"])
    tech_map.tech_map(module, library)
    place.place(module, library)

    out = tmp_path / "and2.litematic"
    emit.emit_litematic(module, library, out, name="and2_test")
    assert out.exists()

    schem = Schematic.load(str(out))
    [region] = schem.regions.values()
    block_ids = {region[x, y, z].id
                 for x in region.range_x()
                 for y in region.range_y()
                 for z in region.range_z()}
    block_ids.discard("minecraft:air")
    # The emitter strips the AND cell's internal lever/lamp fixtures and adds
    # external module-port levers (inputs) + a lamp (output) connected by dust.
    assert "minecraft:lever" in block_ids
    assert "minecraft:redstone_lamp" in block_ids
    assert "minecraft:redstone_wire" in block_ids

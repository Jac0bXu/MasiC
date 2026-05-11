"""Frontend round-trip tests against the handwritten Phase 1 fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from masic.frontend import json_to_ir, synthesize, synthesize_to_json

FIXTURES = Path(__file__).parent / "handwritten"
DIGITAL_LIB = Path(__file__).parent / "golden" / "digital-lib"


@pytest.fixture(autouse=True)
def _require_yosys():
    if shutil.which("yosys") is None:
        pytest.skip("yosys not on PATH")


def test_and2_netlist_shape():
    netlist = synthesize_to_json(FIXTURES / "and2.v", top="and2")
    mod = netlist["modules"]["and2"]
    assert set(mod["ports"]) == {"a", "b", "y"}
    cells = list(mod["cells"].values())
    assert len(cells) == 1
    assert cells[0]["type"] == "$_AND_"


def test_and2_to_ir():
    module = synthesize(FIXTURES / "and2.v", top="and2")
    assert module.name == "and2"
    assert {p.name for p in module.ports} == {"a", "b", "y"}

    [cell] = module.cells.values()
    assert cell.type == "$_AND_"
    assert set(cell.inputs.values()) == {"a", "b"}
    assert set(cell.outputs.values()) == {"y"}

    assert module.nets["y"].driver == (cell.name, "Y")
    loads_a = module.nets["a"].loads
    loads_b = module.nets["b"].loads
    assert (cell.name, "A") in loads_a
    assert (cell.name, "B") in loads_b


def test_full_adder_has_five_gates():
    module = synthesize(FIXTURES / "full_adder.v", top="full_adder")
    assert module.name == "full_adder"
    assert len(module.cells) == 5
    cell_types = sorted(c.type for c in module.cells.values())
    assert cell_types == ["$_NAND_", "$_NAND_", "$_NAND_", "$_XOR_", "$_XOR_"]

    primary_outputs = {"sum", "cout"}
    for out_net in primary_outputs:
        assert module.nets[out_net].driver is not None


@pytest.mark.skipif(
    not (DIGITAL_LIB / "opt_pipe" / "src" / "opt_pipe.sv").exists(),
    reason="digital-lib submodule not checked out",
)
def test_opt_pipe_sv_round_trip():
    """T1 ground-truth module: sv2v + Yosys must accept it and produce gates."""
    sv_path = DIGITAL_LIB / "opt_pipe" / "src" / "opt_pipe.sv"
    module = synthesize(sv_path, top="opt_pipe")
    assert module.name == "opt_pipe"
    assert any(p.direction == "input" and p.name == "CLK" for p in module.ports)

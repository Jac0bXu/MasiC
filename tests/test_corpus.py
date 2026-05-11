"""Corpus walk: every T1–T3 digital-lib module synthesizes through to IR.

This test is the Phase 2 smoke check. If a module fails here, either the
frontend needs to grow to handle the SV construct, or the IR needs a new
field. Fail loudly — the contract should hold for the whole T1–T3 set.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from masic.frontend import synthesize

DIGITAL_LIB = Path(__file__).parent / "golden" / "digital-lib"

CORPUS = [
    pytest.param("opt_pipe/src/opt_pipe.sv", "opt_pipe",
                 {"WIDTH": 4, "NUM_STAGES": 2}, id="T1-opt_pipe"),
    pytest.param("synchronizer/src/socetlib_synchronizer.sv", "socetlib_synchronizer",
                 None, id="T1-synchronizer"),
    pytest.param("edge_detector/src/socetlib_edge_detector.sv", "socetlib_edge_detector",
                 None, id="T1-edge_detector"),
    pytest.param("shift_register/src/socetlib_shift_reg.sv", "socetlib_shift_reg",
                 None, id="T2-shift_register"),
    pytest.param("counter/src/socetlib_counter.sv", "socetlib_counter",
                 {"NBITS": 4}, id="T2-counter"),
    pytest.param("stack/src/socetlib_stack.sv", "socetlib_stack",
                 None, id="T3-stack"),
    pytest.param("fifo/src/socetlib_fifo.sv", "socetlib_fifo",
                 None, id="T3-fifo"),
]


@pytest.fixture(autouse=True)
def _require_toolchain():
    for tool in ("yosys", "sv2v"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on PATH")
    if not (DIGITAL_LIB / "opt_pipe").exists():
        pytest.skip("digital-lib submodule not checked out")


@pytest.mark.parametrize("src,top,params", CORPUS)
def test_module_synthesizes_to_ir(src, top, params):
    module = synthesize(DIGITAL_LIB / src, top=top, params=params)
    assert module.name == top
    assert module.cells, f"{top}: empty netlist (parameters may have trimmed everything)"
    for cell in module.cells.values():
        assert cell.type.startswith("$"), f"{top}: unmapped cell type {cell.type!r}"

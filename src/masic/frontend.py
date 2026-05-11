"""SystemVerilog frontend: sv2v + Yosys → parsed JSON netlist → IR.

Pipeline stage 1. Shells out to sv2v (SV → Verilog-2005) then Yosys
(Verilog → generic gate netlist as JSON). Parses the result into ir.Module.
"""

from __future__ import annotations

from pathlib import Path


def synthesize_to_json(sv_path: Path, top: str | None = None) -> dict:
    """Run sv2v + Yosys; return parsed JSON netlist."""
    raise NotImplementedError


def json_to_ir(netlist_json: dict):
    """Yosys JSON → ir.Module."""
    raise NotImplementedError

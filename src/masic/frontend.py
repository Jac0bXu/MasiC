"""SystemVerilog frontend: sv2v + Yosys → JSON netlist → IR.

Pipeline stage 1. Shells out to sv2v (SV → Verilog-2005, only for .sv inputs)
then Yosys (Verilog → generic gate netlist as JSON), and parses the result
into ir.Module.

Yosys JSON shape we depend on:
    modules.<name>.ports.<port>          = {direction, bits: [bit_id, ...]}
    modules.<name>.cells.<inst>          = {type, port_directions, connections}
    modules.<name>.cells.<inst>.connections.<port> = [bit_id, ...]
    modules.<name>.netnames.<name>       = {bits: [bit_id, ...]}

Bit IDs are integers; 0/1 are constants GND/VCC, "x"/"z" appear as strings.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .ir import Cell, Module, Net, Port

_CONST_BITS = {0: "$const0", 1: "$const1", "x": "$constx", "z": "$constz"}


class FrontendError(RuntimeError):
    pass


def synthesize_to_json(
    src: Path,
    top: str | None = None,
    params: dict[str, int] | None = None,
    gate_set: list[str] | None = None,
) -> dict:
    """Run (sv2v →) Yosys on `src`; return the parsed JSON netlist.

    `params` overrides module parameters via Yosys's `chparam` so a
    parameterized module like `opt_pipe(WIDTH=..., NUM_STAGES=...)` synthesizes
    to a concrete configuration rather than its (potentially degenerate)
    defaults.

    `gate_set` constrains Yosys's ABC pass to a list of valid gate types from
    `{AND, NAND, OR, NOR, XOR, XNOR, ANDNOT, ORNOT, MUX, NMUX, AOI3/4, OAI3/4}`.
    NOT is added automatically. Passing the cell library's coverage lets us
    guarantee every output cell has a redstone implementation.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(src)
    if params and top is None:
        raise FrontendError("params require an explicit top module")

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        verilog = td_path / "in.v"

        if src.suffix == ".sv":
            _run(["sv2v", str(src)], stdout=verilog)
        else:
            verilog.write_bytes(src.read_bytes())

        out_json = td_path / "out.json"
        steps = [f"read_verilog {verilog}"]
        if params:
            sets = " ".join(f"-set {k} {v}" for k, v in params.items())
            steps.append(f"chparam {sets} {top}")
        steps.append(f"synth{f' -top {top}' if top else ''}")
        if gate_set:
            steps.append(f"abc -g {','.join(gate_set)}")
            steps.append("opt_clean")
        steps.append(f"write_json {out_json}")
        _run(["yosys", "-q", "-p", "; ".join(steps)])

        return json.loads(out_json.read_text())


def json_to_ir(netlist: dict, top: str | None = None) -> Module:
    """Convert a Yosys JSON netlist to an ir.Module for the chosen top."""
    modules = netlist.get("modules", {})
    if not modules:
        raise FrontendError("netlist has no modules")

    name = top or _pick_top(modules)
    if name not in modules:
        raise FrontendError(f"top module {name!r} not found; have: {list(modules)}")
    m = modules[name]

    bit_to_net = _build_bit_map(m)
    module = Module(name=name)

    for port_name, port in m.get("ports", {}).items():
        width = len(port["bits"])
        module.ports.append(Port(name=port_name, direction=port["direction"], width=width))

    for inst, cell in m.get("cells", {}).items():
        ir_cell = Cell(name=inst, type=cell["type"], parameters=dict(cell.get("parameters", {})))
        for port, bits in cell["connections"].items():
            direction = cell["port_directions"][port]
            net_name = bit_to_net[bits[0]] if len(bits) == 1 else ",".join(bit_to_net[b] for b in bits)
            if direction == "input":
                ir_cell.inputs[port] = net_name
            else:
                ir_cell.outputs[port] = net_name
        module.cells[inst] = ir_cell

    _populate_nets(module, m, bit_to_net)
    return module


def _build_bit_map(yosys_module: dict) -> dict:
    """Map each bit ID to a net name (preferring user names from netnames)."""
    bit_to_net: dict = dict(_CONST_BITS)
    for net_name, net in yosys_module.get("netnames", {}).items():
        for bit_id in net["bits"]:
            if isinstance(bit_id, int) and bit_id not in _CONST_BITS:
                bit_to_net.setdefault(bit_id, net_name)
    for port_name, port in yosys_module.get("ports", {}).items():
        for bit_id in port["bits"]:
            if isinstance(bit_id, int) and bit_id not in _CONST_BITS:
                bit_to_net.setdefault(bit_id, port_name)
    return bit_to_net


def _populate_nets(module: Module, yosys_module: dict, bit_to_net: dict) -> None:
    for inst, cell in yosys_module.get("cells", {}).items():
        for port, bits in cell["connections"].items():
            direction = cell["port_directions"][port]
            for bit_id in bits:
                if bit_id not in bit_to_net or bit_to_net[bit_id].startswith("$const"):
                    continue
                net_name = bit_to_net[bit_id]
                net = module.nets.setdefault(net_name, Net(name=net_name))
                if direction == "output":
                    net.driver = (inst, port)
                else:
                    net.loads.append((inst, port))


def _pick_top(modules: dict) -> str:
    for name, m in modules.items():
        if m.get("attributes", {}).get("top") == "00000000000000000000000000000001":
            return name
    if len(modules) == 1:
        return next(iter(modules))
    raise FrontendError(f"no top module marked; specify one of {list(modules)}")


def _run(cmd: list[str], stdout: Path | None = None) -> None:
    if stdout:
        with stdout.open("wb") as fh:
            result = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE)
    else:
        result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise FrontendError(f"{cmd[0]} failed: {result.stderr.decode(errors='replace')[-500:]}")


def synthesize(
    src: Path,
    top: str | None = None,
    params: dict[str, int] | None = None,
    gate_set: list[str] | None = None,
) -> Module:
    """Convenience: synthesize + parse in one call."""
    return json_to_ir(
        synthesize_to_json(src, top=top, params=params, gate_set=gate_set), top=top
    )

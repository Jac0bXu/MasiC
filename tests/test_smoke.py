"""Smoke tests: confirm the package imports and module stubs are wired up."""

import masic
from masic import cell_library, cli, cosim, emit, frontend, ir, place, route, tech_map


def test_version():
    assert masic.__version__


def test_modules_importable():
    for mod in (ir, frontend, cell_library, tech_map, place, route, emit, cosim, cli):
        assert mod is not None


def test_ir_dataclasses():
    cell = ir.Cell(name="u1", type="NAND2", inputs={"A": "n1", "B": "n2"}, outputs={"Y": "n3"})
    assert cell.position is None
    net = ir.Net(name="n3", driver=("u1", "Y"), loads=[("u2", "A")])
    assert net.route is None
    mod = ir.Module(name="top")
    mod.cells[cell.name] = cell
    mod.nets[net.name] = net
    assert "u1" in mod.cells

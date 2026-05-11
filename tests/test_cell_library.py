"""Tests for the cell-library loader + manifest sanity checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from masic.cell_library import CellManifestError, load_cell, load_library

CELLS_DIR = Path(__file__).parent.parent / "cells"


def test_load_library_finds_all_cells(tmp_path):
    library = load_library(CELLS_DIR)
    assert set(library) == {"NOT", "NAND2", "AND2", "OR2", "DFF"}


def test_yosys_type_coverage():
    library = load_library(CELLS_DIR)
    declared = {t for cell in library.values() for t in cell.yosys_types}
    # The five canonical types Yosys produces from the default `synth` flow.
    expected = {"$_NOT_", "$_NAND_", "$_AND_", "$_OR_", "$_DFF_P_"}
    assert expected <= declared


def test_combinational_truth_tables_complete():
    library = load_library(CELLS_DIR)
    for name, cell in library.items():
        if cell.family == "combinational":
            n = len(cell.inputs)
            assert len(cell.truth_table) == 2**n, f"{name}: incomplete truth table"


def test_rejects_port_outside_footprint(tmp_path):
    manifest = {
        "name": "BAD",
        "family": "combinational",
        "footprint": [1, 1, 1],
        "ports": {
            "inputs": {"A": {"coord": [5, 0, 0], "kind": "lever"}},
            "outputs": {"Y": {"coord": [0, 0, 0], "kind": "lamp"}},
        },
        "delay_ticks": 1,
        "truth_table": [{"A": 0, "Y": 0}, {"A": 1, "Y": 1}],
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(CellManifestError, match="outside footprint"):
        load_cell(path)


def test_rejects_duplicate_port_coords(tmp_path):
    manifest = {
        "name": "BAD",
        "family": "combinational",
        "footprint": [2, 1, 1],
        "ports": {
            "inputs": {"A": {"coord": [0, 0, 0], "kind": "lever"}},
            "outputs": {"Y": {"coord": [0, 0, 0], "kind": "lamp"}},
        },
        "delay_ticks": 1,
        "truth_table": [{"A": 0, "Y": 0}, {"A": 1, "Y": 1}],
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(CellManifestError, match="share"):
        load_cell(path)


def test_rejects_incomplete_truth_table(tmp_path):
    manifest = {
        "name": "BAD",
        "family": "combinational",
        "footprint": [2, 1, 1],
        "ports": {
            "inputs": {
                "A": {"coord": [0, 0, 0], "kind": "lever"},
                "B": {"coord": [1, 0, 0], "kind": "lever"},
            },
            "outputs": {},
        },
        "delay_ticks": 1,
        "truth_table": [{"A": 0, "B": 0}],  # missing 3 rows
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(CellManifestError, match="expected 4"):
        load_cell(path)

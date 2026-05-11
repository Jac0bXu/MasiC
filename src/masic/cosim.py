"""Cosimulation harness: Verilator (golden) vs MCHPRS (generated redstone).

Runs the same testbench against both, diffs waveforms cycle-by-cycle.
This is what makes any correctness claim credible.
"""

from __future__ import annotations

from pathlib import Path


def cosim(sv_path: Path, litematic_path: Path, testbench: Path) -> bool:
    raise NotImplementedError

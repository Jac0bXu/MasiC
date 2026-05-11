"""Schematic emitter: stamps placed cells + routes into a master .litematic."""

from __future__ import annotations

from pathlib import Path

from .ir import Module


def emit_litematic(module: Module, out_path: Path) -> None:
    raise NotImplementedError

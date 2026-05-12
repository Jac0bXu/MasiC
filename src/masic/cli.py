"""masic CLI entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import cell_library, emit, frontend, place, route, tech_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="masic")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_synth = sub.add_parser("synth", help="Synthesize SV/V to .litematic")
    p_synth.add_argument("input")
    p_synth.add_argument("-o", "--output", required=True)
    p_synth.add_argument("--top")
    p_synth.add_argument(
        "--cells",
        default="cells",
        help="Path to the cell library (default: ./cells)",
    )
    p_synth.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Override a top-level parameter (repeatable)",
    )
    p_synth.add_argument(
        "--router",
        choices=("2d", "3d", "channel"),
        default="2d",
        help="Router backend: 2d lane (default), 3d A* maze, or channel "
             "(MinecraftHDL-inspired structured tracks).",
    )

    p_verify = sub.add_parser("verify", help="Cosim SV against generated redstone (TODO)")
    p_verify.add_argument("input")

    args = parser.parse_args(argv)

    if args.cmd == "synth":
        return _run_synth(args)
    raise NotImplementedError(f"command not yet wired: {args.cmd}")


def _run_synth(args: argparse.Namespace) -> int:
    library = cell_library.load_library(Path(args.cells))
    gate_set = sorted({gt for spec in library.values() for gt in spec.yosys_types
                       if gt in {"$_AND_", "$_NAND_", "$_OR_", "$_NOR_", "$_XOR_", "$_XNOR_"}})
    abc_gates = [gt.strip("$_").rstrip("_") for gt in gate_set]

    params = dict(_parse_kv(p) for p in args.param) if args.param else None

    module = frontend.synthesize(
        Path(args.input), top=args.top, params=params, gate_set=abc_gates or None
    )
    tech_map.tech_map(module, library)
    place.place(module, library)
    if args.router == "2d":
        collisions = route.detect_collisions(module, library)
        if collisions:
            print(f"WARNING: {len(collisions)} dust coords are routed by multiple nets "
                  f"(the naive single-layer router will short these together). "
                  f"The output schematic will be emitted anyway, but expect wrong behavior.")
    emit.emit(module, library, Path(args.output), router=args.router)
    print(f"wrote {args.output} ({len(module.cells)} cells)")
    return 0


def _parse_kv(s: str) -> tuple[str, int]:
    k, _, v = s.partition("=")
    return k, int(v)


if __name__ == "__main__":
    raise SystemExit(main())

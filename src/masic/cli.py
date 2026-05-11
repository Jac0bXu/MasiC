"""masic CLI entrypoint."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="masic")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_synth = sub.add_parser("synth", help="Synthesize SV to .litematic")
    p_synth.add_argument("input")
    p_synth.add_argument("-o", "--output", required=True)
    p_synth.add_argument("--top")

    p_verify = sub.add_parser("verify", help="Cosim SV against generated redstone")
    p_verify.add_argument("input")

    args = parser.parse_args(argv)
    raise NotImplementedError(f"command not yet wired: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())

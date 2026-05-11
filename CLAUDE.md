# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: MasiC

**MasiC is an EDA tool that synthesizes SystemVerilog into Minecraft redstone structures.** Source HDL is lowered through standard synthesis tooling, mapped onto a library of redstone "standard cells," placed and routed in 3D voxel space, and emitted as a `.litematic`/`.nbt` schematic that can be loaded into a Minecraft world.

The repo is at day-zero: only a stub README exists. Treat early work as bootstrapping the pipeline rather than modifying existing code.

## Pipeline (the architectural spine)

```
SystemVerilog → sv2v → Verilog → Yosys → JSON netlist
            → IR → tech map → placer → router → .litematic
```

Every stage reads and mutates a shared **IR** (intermediate representation) of `Cell` / `Net` / `Module` dataclasses. The IR is the contract between stages — design and stabilize it before building stages on top, because each stage becomes an independent module hanging off it.

External tools in the pipeline:
- **sv2v** — lowers SystemVerilog to Verilog-2005 (Yosys only handles a subset of SV).
- **Yosys** — synthesis frontend; invoke with `read_verilog; synth; abc -g NAND; opt; write_json` to get a generic-gate netlist as JSON. Understanding the `modules → cells → connections` shape of this JSON is foundational.
- **MCHPRS** (Minecraft High-Performance Redstone Server) — headless redstone simulator, ~10,000× faster than vanilla. This is the simulation backend for verification, *not* vanilla Minecraft.
- **Litematica** mod / **WorldEdit** — for loading generated builds into a world for visual inspection.
- **Verilator** — golden reference for cosimulation against the generated redstone.

**Do not use Mineflayer.** It controls a player avatar; this project generates world files directly. The right Python libraries are `litemapy` / `mcschematic` / `nbtlib`.

## Module layout (planned)

Each module sits behind a clear input/output contract over the IR:

- `yosys_runner.py` — shells out to sv2v + Yosys, returns parsed JSON.
- `ir.py` — `Cell`, `Net`, `Module` dataclasses. Stages add fields (`position`, `orientation`, `route`) as the IR flows through.
- `netlist_to_ir.py` — Yosys JSON → IR.
- `cell_library.py` — loads redstone standard cells (each cell = a small `.litematic` + YAML metadata: footprint, port coords, propagation delay in ticks).
- `tech_map.py` — replaces generic IR gates with library cells.
- `placer.py` — assigns 3D coordinates to cells.
- `router.py` — wires nets between cells with redstone dust + repeaters every ≤14 blocks (15-block signal limit).
- `emit_litematic.py` — stamps placed cell schematics + routing into a master `.litematic`.
- `cosim.py` — runs the same testbench through Verilator and MCHPRS, diffs waveforms.

Keep modules under ~500 lines.

## Redstone constraints that shape the design

These are physical constraints of the target "fabric" — every stage must respect them:
- **Signal range:** redstone dust attenuates over 15 blocks; repeaters are mandatory for longer runs.
- **Tick rate:** a redstone tick is 100 ms. Effective clock frequencies are single-digit Hz.
- **Directionality:** repeaters and comparators are directional; routing is anisotropic.
- **3D voxel routing:** layer crossings (e.g. cells at y=0, routing at y=3) avoid collisions in naive routers.
- **Signal-strength:** dust carries values 0–15 (4 bits). Existing projects ignore this; it is a known unexploited primitive.

Block-state IDs and comparator edge cases change between Minecraft versions in undocumented ways. Pin a version (1.20.1 is a safe default) and don't assume cells port across versions for free.

## Development workflow

The first milestone is a **hardcoded end-to-end "hello world"**: a single 2-input AND gate, Verilog → Yosys JSON → manual tech map → trivial placement → `.litematic` that lights an output lamp in Minecraft. Skip elegance; the goal is to round-trip *something* before generalizing.

**Golden test set:** the SoCET `digital-lib` corpus at `~/Documents/GitHub/digital-lib` is the authoritative ground-truth SV for this project. See [SCOPE.md](SCOPE.md) for the tiered ordering (T1 `opt_pipe`/`synchronizer`/`edge_detector` → T4 `wt_mult`/`cdc-fifo`). Each module has its own testbench under `<module>/tb/` — reuse those as cosim stimulus rather than writing new vectors. The library's SV style (parameterized, `always_ff @(posedge CLK, negedge nRST)`, packages for `wt_mult`) defines the supported SV subset.

Cosim through Verilator + MCHPRS is what makes any correctness claim credible — without it you can't tell whether your synthesizer is wrong or your hand-built cell is wrong. Wire it into `pytest` so the full pipeline runs end-to-end on the golden set.

## Working with agents / Claude on this codebase

- The cell library design step (building NAND/NOT/AND/OR/DFF in a creative-mode world, measuring footprints and port locations) is **hands-on Minecraft work**. It cannot be delegated; Claude cannot do it. Everything else is agent-friendly.
- For algorithm-heavy stages (placer, router): specify the algorithm and cost function in a design doc *before* implementation. Don't ask for "a good placer" — ask for "simulated annealing with this cost function."
- Spatial/visual debugging in Minecraft and version-specific block-state quirks are the other areas where human judgment is required.

## Tooling (not yet set up)

Once a Python project is initialized (`uv init` or `poetry new`, Python 3.11+), expected dependencies are: `litemapy`, `pytest`, dataclasses/`pydantic`. External binaries on PATH: `yosys`, `sv2v`, and a local `mchprs` build for cosim.

## Reference projects

Prior art to study (do not fork — the codebases are small and reimplementing teaches the interfaces):
- MinecraftHDL (https://github.com/itsfrank/MinecraftHDL) — closest existing reference.
- V2MC (https://github.com/Kenny2github/V2MC) — uses pyosys, emits `.nbt`.
- MCV (https://github.com/EngineersBox/MCV), mineroute (https://github.com/Gl237man/mineroute).
- MCHPRS (https://github.com/MCHPRS/MCHPRS) — the simulation backend.

## Novel contributions on the table

Once the base pipeline + cosim work, the project picks **one** novel direction (these are research-grade, not all-of-the-above):
1. **Signal-strength encoding** — pack multi-bit buses onto single wires using dust's 0–15 levels; potentially ~4× datapath shrink. Biggest unexploited primitive.
2. **Real 3D placer + router** — analytical/SA placement and a maze router that respects redstone anisotropy. Closer to a publishable result.
3. **Characterized cell library + STA** — `report_timing` for redstone; prerequisite for serious pipelining.
4. **SystemVerilog frontend polish** — sv2v integration that exposes interfaces, packages, structs, `always_ff`.
5. **Memory compiler** — parameterized BRAM generation.
6. **FPGA-on-redstone** — one reconfigurable fabric, bitstream-loaded designs. Only viable after everything else exists.

# MasiC — Scope & Todo

## Scope: what this project is (and isn't)

**One-line definition:** A compiler that takes SystemVerilog source and emits a Minecraft `.litematic` schematic of an equivalent redstone circuit, with a verification harness that proves the two behave the same.

### In scope (the "final product")

A working pipeline that, given a SystemVerilog module from a fixed feature subset, produces:
1. A loadable Minecraft schematic that implements the module.
2. A cosim report showing the schematic matches the RTL on a testbench.

**Supported HDL subset (target):** combinational logic, registers (`always_ff`), parameterized modules, buses, simple FSMs, small memories, **packages** (`*_pkg.sv`), active-low async reset (`negedge nRST`) — this is what the ground-truth library actually uses. **Not in scope:** floating point, tri-state buses, multi-clock domains.

**Target circuit size (target):** up to a small CPU core (think: 4-bit or 8-bit accumulator machine, ~hundreds of gates). Not a RISC-V core. Not anything that would melt MCHPRS.

### Ground-truth corpus: `digital-lib`

The repository at `~/Documents/GitHub/digital-lib` (SoCET Digital Library) is the authoritative source of test SV. All modules are parameterized, well-documented, and have UVM/SV testbenches we can crib for cosim stimulus. Treat this corpus as the **definitive golden set** — the final deliverable is: *every one of these modules synthesizes to a working `.litematic` and passes cosim.* Listed in synthesis-difficulty order:

| Tier | Module | LoC | Why it's at this tier |
|---|---|---|---|
| **T1 trivial** | `opt_pipe` | 34 | Single FF stage; smallest possible test of register handling. |
| T1 | `synchronizer` | 36 | Two-FF chain; CDC primitive but single-net. |
| T1 | `edge_detector` | 36 | Two FFs + combinational diff; Moore/Mealy parameter. |
| **T2 small** | `shift_register` | 47 | Parameterized width + depth. |
| T2 | `counter` | 59 | Parameterized counter with overflow comparator (introduces an adder + magnitude compare). |
| **T3 medium** | `stack` | 79 | Small memory (LIFO); first design that exercises the memory primitive. |
| T3 | `fifo` | 100 | Single-clock FIFO; memory + pointer logic. |
| **T4 hard (stretch)** | `cdc-fifo` | ~236 | **Two clock domains.** Outside the stated single-clock scope; gate-level synthesis is fine but cosim semantics around CDC are subtle. Stretch goal. |
| T4 | `wt_mult` | 444 | Wallace-tree multiplier; large combinational dataflow with a SV `package`. Stress test for the placer/router — this is where naive placement sprawl will hurt most. |

**Implication for scope tiers:**
- Weekend hack target: T1 modules synthesize and pass cosim.
- Semester target: T1 + T2 + T3 all pass.
- Year/thesis target: above + at least one T4. `wt_mult` is the natural benchmark for whatever placer/router novelty you pick.

**Pick exactly one novel contribution** on top of the base pipeline. The base + cosim + one novelty is the deliverable. Candidates (in CLAUDE.md): signal-strength encoding, 3D placer/router, characterized cells + STA, sv2v frontend, memory compiler. Do **not** attempt the FPGA-on-redstone idea — it depends on everything else being mature.

### Explicitly out of scope

- Optimizing redstone clock frequency beyond what falls out naturally.
- Targeting vanilla Minecraft performance (use MCHPRS).
- Multi-version Minecraft support — pin to 1.20.1.
- A GUI. CLI only.
- Designing redstone cells from scratch as a research effort — copy known-good designs from the redstone community where possible.
- A Mineflayer-based runtime (wrong abstraction).

### Time budget

- **Semester (12–14 weeks, ~10 hrs/week):** base pipeline + cosim + a modest novelty (sv2v integration *or* characterized cell library with basic STA).
- **Year-long / thesis-scale:** base + cosim + one heavy novelty (signal-strength encoding *or* real 3D placer/router).
- **Weekend hack:** stop at the "hardcoded AND gate round-trip" milestone (week 1 below). That alone is a satisfying artifact.

Reassess scope **after** the base pipeline runs end-to-end on the golden test set. Your sense of what's hard will change.

## The main parts of the final product

These are the components that must all exist for the final deliverable. Each is one module behind a clear contract over the IR.

| # | Component | Responsibility | Hardest part |
|---|---|---|---|
| 1 | **Frontend wrapper** | sv2v + Yosys invocation, JSON parsing | Understanding Yosys JSON shape |
| 2 | **IR** | `Cell` / `Net` / `Module` dataclasses; the contract between stages | Designing fields so later stages don't need to mutate the schema |
| 3 | **Cell library** | Hand-built redstone cells (NAND, NOT, AND, OR, MUX, DFF, full adder...) as `.litematic` + YAML metadata (footprint, port coords, delay) | Building & measuring cells in Minecraft creative mode |
| 4 | **Tech mapper** | Generic gates in IR → library cells | Choosing which library cells to expose; the tech-map cost function |
| 5 | **Placer** | Assigns 3D coordinates to each cell | Cost function for placement quality; avoiding sprawl |
| 6 | **Router** | Wires nets between cells with dust + repeaters; respects 15-block range, directionality, layer separation | 3D anisotropic routing is the genuinely hard part |
| 7 | **Schematic emitter** | Stamps placed cells + routes into a master `.litematic` | Block-state correctness |
| 8 | **Cell library editor / verifier** | Loads each cell into MCHPRS, drives inputs, confirms outputs match its declared truth table | None — but skipping it means silent cell bugs corrupt every design |
| 9 | **Verification harness (cosim)** | Runs the same testbench through Verilator (golden) and through MCHPRS (the generated schematic); diffs waveforms cycle-by-cycle | MCHPRS RPC plumbing |
| 10 | **Golden test set** | A growing collection of small Verilog modules with expected behavior; runs on every change | Discipline to keep it green |
| 11 | **CLI / orchestration** | `masic synth foo.sv -o foo.litematic`; `masic verify foo.sv` | Nothing |
| 12 | **The one novel contribution** | (Picked after base works) | Open research |
| 13 | **Docs & examples** | A few worked examples + a write-up of what the novel contribution achieves | Nothing |

## Todo list

Tracked as ordered phases. Don't skip ahead — each phase depends on the previous one being solid.

### Phase 0 — Bootstrap (1 day) ✅
- [x] Install `yosys` (verify `yosys -V`) — Yosys 0.64 via brew.
- [x] Install `sv2v` (verify `sv2v --version`) — sv2v 0.0.13 via brew.
- [x] Initialize Python project — `pyproject.toml` with hatchling backend, src layout.
- [x] Add deps: `litemapy`, `pyyaml`, `pytest`, `ruff`, `mypy` (via `uv sync --extra dev`).
- [ ] **Install Minecraft Java 1.20.1 + Fabric + Litematica mod** — manual, see [SETUP.md](SETUP.md). Can't be scripted (GUI + login).
- [x] Clone MCHPRS locally — at `tools/MCHPRS/`, building.
- [x] Sketch repo layout — `src/masic/{ir,frontend,cell_library,tech_map,place,route,emit,cosim,cli}.py` + `tests/test_smoke.py` + `cells/` + `docs/`. Smoke tests pass.

**Phase 0 verification (already passing):** `uv run pytest -q` → 3 passed. sv2v + Yosys end-to-end on `digital-lib/opt_pipe` produces a valid JSON netlist.

### Phase 1 — Yosys round-trip (1–2 days)
- [ ] Write `and2.v` (two-input AND, nothing else).
- [ ] Run `yosys -p "read_verilog and2.v; synth; write_json and2.json"` by hand. **Read the JSON.** Spend an hour understanding `modules → cells → connections`. This is the most important hour of the project.
- [ ] Write `frontend.py` that shells out to Yosys and returns parsed JSON.
- [ ] Repeat for `full_adder.v` to see what multi-gate netlists look like.

### Phase 2 — IR (2 days)
- [ ] Define `Cell`, `Net`, `Module` dataclasses in `ir.py`. Leave `position`/`route` fields as `None` initially — later stages fill them in.
- [ ] Write `netlist_to_ir.py`: Yosys JSON → IR.
- [ ] Write unit tests on `and2`, `full_adder`.
- [ ] Add the `digital-lib` corpus as a git submodule (or symlink) under `tests/golden/digital-lib/`. Wire pytest to discover each module's `<module>/src/*.sv` + `<module>/tb/` testbench.
- [ ] Smoke-test: feed `opt_pipe/src/opt_pipe.sv` and `synchronizer/src/socetlib_synchronizer.sv` through sv2v + Yosys end-to-end. Confirm both produce valid JSON netlists. (Skip placer/router for now — this just validates the frontend on real-world SV.)

### Phase 3 — Cell library (3–5 days, hands-on in Minecraft)
- [ ] In creative mode, build the smallest known-good NAND, NOT, AND, OR, DFF. Take screenshots. Record dimensions and exact input/output coordinates.
- [ ] Save each as a `.litematic` in `cells/`.
- [ ] Write a YAML manifest per cell: footprint (x, y, z), input ports `{name: (x,y,z)}`, output ports, delay in ticks.
- [ ] Write `cell_library.py` that loads cells + manifests into memory.
- [ ] For each cell, verify in MCHPRS: drive its declared inputs, confirm declared outputs. **Don't skip this** — a buggy cell silently corrupts every downstream design.

### Phase 4 — Naive tech map + placer + router + emit (4–5 days)
- [ ] Tell Yosys to emit only library primitives: `synth -top X; abc -g NAND; opt`.
- [ ] Write `tech_map.py`: walk the IR, replace each generic gate with a library cell instance.
- [ ] Write a dumb grid placer: `cell.position = (i * pitch, 0, j * pitch)`. Generous spacing.
- [ ] Write a dumb router: dust trail from driver to load; repeater every ≤14 blocks; use a separate y-layer for routing to avoid collisions; another layer for wire-over-wire crossings.
- [ ] Write `emit_litematic.py`: stamp each placed cell's `.litematic` at its position, then add routing blocks.
- [ ] Load the output in Minecraft, click input levers, confirm output lamp behaves correctly. **This is the "hello world" milestone.**

### Phase 5 — Verification harness (3–4 days)
- [ ] Learn MCHPRS's RPC/command interface for setting inputs and reading outputs.
- [ ] Write `cosim.py`: run a testbench against Verilator (golden) and against the generated MCHPRS world; diff waveforms.
- [ ] Wire into `pytest` so the full pipeline runs end-to-end on the golden test set.
- [ ] **Checkpoint:** all golden circuits pass cosim. This is the moment to pick the novel contribution.

### Phase 6 — Generalization across the `digital-lib` corpus (1–2 weeks)

Walk the tiers from SCOPE.md in order. Don't advance until the current tier passes cosim end-to-end on all of its modules.

- [ ] **T1 (sequential primitives):** `opt_pipe`, `synchronizer`, `edge_detector`. Smallest possible test of FF handling, parameter passing (`WIDTH`, `MOORE`, `RESET`), and async-low reset.
- [ ] **T2 (parameterized datapath):** `shift_register`, `counter`. Forces real width parameterization through the placer and exercises a comparator/adder in `counter`.
- [ ] **T3 (memories):** `stack`, `fifo`. First real test of the memory primitive — add MUX2, XOR, and a small register-file cell to the library as needed.
- [ ] **T4 (stress / stretch):** `wt_mult` for combinational sprawl; `cdc-fifo` only if multi-clock support is in scope.
- [ ] Adapt each module's existing `<module>/tb/` testbench as the cosim stimulus driver.
- [ ] CLI: `masic synth <file.sv> -o <out.litematic>`; `masic verify <file.sv>`.

### Phase 7 — One novel contribution (2–4 months)
- [ ] Pick one of the directions from CLAUDE.md.
- [ ] Write a design doc *before* coding. Specify the algorithm and the cost function / success metric.
- [ ] Implement behind the existing IR contract; old pipeline must keep working.
- [ ] Benchmark against the base pipeline on the golden test set (footprint, delay, or whatever metric the novelty optimizes).

### Phase 8 — Write-up
- [ ] Worked examples in `examples/`.
- [ ] README / report describing what the novel contribution improved and by how much.
- [ ] If aiming at a thesis or paper: include the cosim methodology and benchmark numbers.

## Decision points (revisit, don't pre-commit)

- **End of Phase 2:** Is the IR right? Easier to fix now than after five stages depend on it.
- **End of Phase 5:** Pick the novel contribution. Don't pick it earlier — your intuition for what's hard will be wrong.
- **End of Phase 7:** Is the result interesting enough to write up? If not, was the metric wrong, or the approach wrong?

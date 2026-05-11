# Cell library

This directory is the redstone standard-cell library — the bridge between the synthesizer's IR and the voxel world. Each subdirectory is one cell, with the same shape:

```
cells/<NAME>/
  manifest.yaml     ← port coordinates, footprint, delay, truth table
  cell.litematic    ← the schematic you build by hand in Minecraft (TODO)
```

The `manifest.yaml` files are committed with **placeholder coordinates** sized for the canonical redstone patterns. You must rebuild each cell in Minecraft and update the manifest with the actual coordinates of your build.

## Build order (matches Phase 3 of SCOPE.md)

Build in this order — each is simpler than the next, and later cells depend on patterns you'll learn from earlier ones.

| # | Cell | Canonical pattern | Yosys type(s) |
|---|---|---|---|
| 1 | [NOT](NOT/) | torch on side of a block | `$_NOT_` |
| 2 | [NAND2](NAND2/) | two-torch inverter feeding a torch (a NOR-of-NOTs) | `$_NAND_` |
| 3 | [AND2](AND2/) | NAND + inverter | `$_AND_` |
| 4 | [OR2](OR2/) | two dust trails meeting | `$_OR_` |
| 5 | [DFF](DFF/) | RS latch + edge detector | `$_DFF_P_`, `$_DFF_PP0_` |

## How to build a cell

For each cell:

1. Open a flat creative world in Minecraft 1.20.1 with the Fabric profile that has Litematica installed.
2. Place the cell at a known corner, with the **input side** on the local `x=0` plane and the **output side** on the local `x=fx-1` plane. This convention isn't enforced by code, but the placer assumes "inputs in, outputs out, signals flow along +x" — sticking to it keeps everything orthogonal.
3. Mark inputs with **levers** and outputs with **redstone lamps**. The placer wires inputs from outside levers and reads outputs from lamps during cell-library verification.
4. Test in the world: flip the input lever combinations, confirm the output lamp matches the truth table in `manifest.yaml`.
5. Use Litematica to select the cell volume (`Ctrl+M` → "Create selection") and save as `cells/<NAME>/cell.litematic`.
6. Edit `manifest.yaml`:
   - Replace `footprint` with `[width, height, depth]` of the selection.
   - Replace each port's `coord` with the exact `[x, y, z]` of the lever/lamp **relative to the selection origin** (the corner that becomes (0,0,0) when the schematic is stamped).
   - Replace `delay_ticks` with the propagation delay measured in redstone ticks (count repeater settings and torch flips; one redstone tick = 2 game ticks = 0.1 s).

## Verifying a cell

Until MCHPRS RIL is wired into the harness, verify each cell **by hand in the same world**:

1. For combinational cells: flip every combination of input levers and confirm the output lamp lights according to the manifest's `truth_table`.
2. For DFF: drive `D` to a value, pulse `CLK`, confirm `Q` captures `D` on the rising edge and holds it when `CLK` returns low.

Once all five cells pass, Phase 3 is done.

## What the loader checks (Python-side)

`masic.cell_library.load_library(cells_dir)` enforces, for each cell:

- Every port coordinate lies inside the declared footprint.
- No two ports occupy the same coordinate.
- Combinational cells have a complete truth table (one row per input combination, no duplicates, binary values).
- `family` is `combinational` or `sequential`; `delay_ticks` is non-negative.

This catches typos in the manifest. It does **not** prove the schematic matches the manifest — that's what hand-verification (and eventually MCHPRS cosim) is for.

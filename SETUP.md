# Setup

End-to-end setup for the MasiC pipeline. The automated steps are scripted; the Minecraft client steps require a logged-in account and the desktop launcher, so you do them by hand.

## Automated (already done in Phase 0)

These are installed; re-run if you're setting up on a new machine.

```bash
# Synthesis + build toolchains (via Homebrew on macOS)
brew install yosys sv2v uv rustup
rustup default stable          # installs cargo for the MCHPRS build

# Python project
uv sync --extra dev            # installs litemapy, pyyaml, pytest, ruff, mypy

# MCHPRS (Minecraft High-Performance Redstone Server) — simulation backend
git clone https://github.com/MCHPR/MCHPRS.git tools/MCHPRS
cd tools/MCHPRS && cargo build --release
# binary: tools/MCHPRS/target/release/mchprs
```

The `tools/` directory is gitignored — MCHPRS is vendored locally, not committed.

Verify:
```bash
yosys -V        # expect: Yosys 0.64 (or newer)
sv2v --version  # expect: 0.0.13
uv --version
tools/MCHPRS/target/release/mchprs --version
uv run pytest -q
```

## Manual — Minecraft client setup (you, not Claude)

These can't be scripted: they need your Mojang/Microsoft login and a GUI launcher. Do them once.

1. **Install Minecraft Java Edition 1.20.1.**
   - Open the official launcher, "Installations" → "New installation" → select "release 1.20.1" → launch once so the version's assets download.
   - Pin to 1.20.1 even if newer is available. Block-state IDs and comparator edge cases change between versions in undocumented ways; cells built for one version are not guaranteed to port forward.
2. **Install Fabric Loader for 1.20.1.** Download the installer from <https://fabricmc.net/use/>, run it, target 1.20.1, install.
3. **Install WorldEdit + Fabric API.** Drop these `.jar` files into `~/Library/Application Support/minecraft/mods/`:
   - Fabric API (for 1.20.1)
   - WorldEdit (Fabric build for 1.20.1) — <https://modrinth.com/plugin/worldedit>
4. **First-run smoke test.** Launch the Fabric 1.20.1 profile, create a flat creative world, place a redstone torch — if it lights up, the mod loader is fine. Then in chat run `//pos1`. If WorldEdit responds, you're done.

**Pasting a synthesized circuit:**
1. Drop the `.schem` file masic produced into `~/Library/Application Support/minecraft/config/worldedit/schematics/`.
2. In-game, stand where you want the build to start.
3. `//schem load <name>` (no extension).
4. `//paste -a` — paste with no air (skips empty blocks; safe for our outputs).

**Litematica is no longer required** — masic writes `.schem` by default and WorldEdit pastes instantly. The five cell `.litematic` files in `cells/` are still used as inputs but never opened in-game.

## What each tool does in the pipeline

| Tool | Role | When it runs |
|---|---|---|
| sv2v | SystemVerilog → Verilog-2005 | Frontend, before Yosys |
| Yosys | Synthesis → JSON netlist of generic gates | Frontend |
| MasiC (this repo) | IR, tech map, place, route, emit | Middle of the pipeline |
| litemapy / nbtlib (Python deps) | Read/write `.litematic` and `.nbt` | Output stage |
| MCHPRS | Fast headless redstone simulator | Cosim backend, ~10,000× faster than vanilla |
| Minecraft + Litematica | Visual inspection of generated builds | Manual review only |

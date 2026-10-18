# 28 — Distributable, cross-platform wheels for the native kernel (B8.3)

**Date:** 2026-10-18  ·  **Milestone:** M8 / M9 (packaging)  ·  **Status:** abi3 wheels building for Linux + Windows locally (no CI); Windows wheel installed and verified on the owner's host; macOS documented as build-on-Mac

## Question

The owner will share the app with colleagues (one on **macOS**), so the native kernel (B8.2)
must be distributable **cross-platform / cross-architecture**, and — explicitly — **without
paid CI runners**. Can we produce the wheels **locally on the WSL box**?

## Decisions

**1. Build abi3, not per-version.** `pyo3` gains `abi3-py310`, so one **stable-ABI** wheel per
platform runs on **CPython 3.10+**, including the newest. This answers the owner's "use the most
recent Python" directly: the kernel is forward-compatible by construction — no rebuild per Python
minor, and the thing that actually gates the host Python is mediapipe/opencv wheel availability,
not us. (The GIL is already released in the hot loop via `py.allow_threads` (B8.2), so a
free-threaded build buys this architecture little; keeping the abi3 floor at 3.10 also keeps
colleagues on older Pythons working.)

**2. Cross-compile locally, no CI.** A render kernel has no OS-specific code, so:

- **Linux x86_64** — native `maturin build` (manylinux abi3 wheel). Done here.
- **Windows x86_64** — cross-compiled from WSL with **`cargo-xwin`** (it fetches the MS CRT/SDK
  automatically; no Visual Studio, no Windows). Enabled `pyo3/generate-import-lib` so the Windows
  `python3.lib` is synthesised on Linux (no target Python needed). **Built and verified on the
  owner's host**: `pip install`ed the `cp310-abi3-win_amd64.whl` into the host Python 3.12,
  `import binaural_kernel` works, and the kernel runs at **~0.22 ms/block (≈25× RT at block 256)**
  on the host CPU. `rust/build-wheels.sh` captures the recipe.
- **macOS (x86_64 + arm64)** — **not** cross-buildable from Linux (needs Apple's licensed SDK and
  realistically a Mac; emulating macOS is not viable — QEMU only emulates CPU arch for *Linux*).
  The Mac colleague builds locally in one command (`pip install maturin && maturin build …`), or
  just uses the **pure-Python fallback** (the app already runs everywhere without the kernel,
  only slower). Documented, not automated.

## The WSL→Windows toolchain recipe (what it took)

Bring-up hit a chain of single, diagnosable causes (recorded so it's reproducible):
`cargo-xwin` needs rustc ≥ 1.89 (snap Rust was 1.85 → `rustup toolchain install stable` = 1.99);
the MSVC target (`rustup target add x86_64-pc-windows-msvc`); the LLVM CLI tools `cargo-xwin`
links with (`sudo apt-get install -y clang lld llvm` — only the shared libs were present);
Ubuntu ships no `clang-cl` symlink (`ln -sf $(command -v clang) ~/.cargo/bin/clang-cl`); and
`llvm-dlltool` fails with EACCES writing to a Windows **drive mount**, so `CARGO_TARGET_DIR` must
be a native-ext4 path. With those, `maturin build --target x86_64-pc-windows-msvc` produces the
abi3 win_amd64 wheel.

## Result / next

- Linux + Windows abi3 wheels build locally, no CI; Windows wheel verified on the owner's host.
- `rust/build-wheels.sh [linux|windows|all]` → `dist/`; prereqs + the macOS step in its header.
- `run_passthrough.cmd` now runs `--rust` (GPU-free) at a small block for low latency.
- Wheels are build artifacts (`dist/` gitignored), not committed. If per-release distribution
  grows (more arches, auto-publish), a local `cibuildwheel`/`maturin`-on-Mac pass is the next
  step — still no paid CI required. (→ L57)

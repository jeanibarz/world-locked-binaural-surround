# 27 — The native Rust render kernel: real-time on CPU, no GPU (B8.2)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency / native hot path)  ·  **Status:** built, bit-exact vs the Python renderer, ~5× real time on CPU at block 256 — the GPU is no longer required

## Question

Journal 26 showed the render math is already real-time on CPU; the GPU was covering Python
per-block overhead, not a compute wall. The owner chose to go straight for the recommended fix:
a **targeted native (Rust/PyO3) render kernel** for the hot loop, to drop the CuPy/CUDA
dependency (and its install pain, L51) and also unlock lower-latency small blocks.

## Method

The one heavy piece is the batched fixed-filter convolution the GPU does
(`gpu_render.GpuBusConvolver`): the 8 BRIR **buses** × 2 ears, each a uniformly-partitioned
overlap-save convolution with a fixed BRIR, summed to stereo. The routing/crossfade that
produces the buses (`live_render.BusMixer`, proven identical to the per-channel voice pool) and
the LFE handling are cheap, tested numpy. So **only the convolver was ported**, exactly
mirroring the GPU design:

- `rust/` — a separate PyO3/maturin crate (`binaural_kernel`), **not** folded into the
  setuptools Python package. It is an **optional native accelerator** like the `gpu` extra:
  built into the venv with `maturin develop --release -m rust/Cargo.toml`, never required on CI
  (tests `pytest.importorskip("binaural_kernel")`).
- `binaural_kernel.BusConvolver(left, right, block)` / `.process(bus_in (C,block)) -> (block,2)`
  reimplements the Python `PartitionedConvolver` (itself verified `== scipy.fftconvolve`) bin
  for bin: `realfft` real FFT of the `2B` `[prev, x]` window, `P` complex multiply-accumulates
  against the pre-transformed filter partitions, inverse real FFT, overlap-save second half.
  Allocation-free per block (all scratch preallocated per bus), and **multithreaded across the
  8 buses with rayon**. `num_complex`/`realfft` normalisation matched to numpy (divide the
  inverse by `nfft`; zero the DC/Nyquist imaginary bins).
- `rust_render.RustWorldLocked7p1` — a drop-in for `WorldLocked7p1`/`GpuWorldLocked7p1`:
  `BusMixer` + the native convolver + the same flat/brir LFE handling. The passthrough selects
  it with `--rust` (takes precedence over `--gpu`; falls back to pure-Python CPU if unbuilt).

## Result

Measured on the real BRIR (8ch × 500 ms/24000-tap, 48 kHz), **worst case** = continuous head
motion (every channel crossfading, two live buses):

| block | Python CPU | **Rust** | speedup |
|---|---|---|---|
| 256 (5.3 ms) | 0.4× RT | **5.0× RT** | 12.4× |
| 512 (10.7 ms) | 1.1× RT | **5.5× RT** | 5.2× |
| 1024 (21.3 ms) | 2.5× RT | **8.0× RT** | 3.2× |

- **Bit-exact parity** with the pure-Python `WorldLocked7p1` over a crossfading head sweep: max
  abs error **~1–2e-16** (both LFE modes, all block sizes) — floating round-off, not just the
  1e-9 target. 7 new tests (importorskip-gated); **292 total, ruff clean.**
- At **block 256** the Rust kernel sustains **5× real time** where pure-Python CPU could not keep
  up (0.4×) — so it is GPU-class (the GPU was ~6× at block 256) **without CUDA**, and it makes
  the low-latency small blocks viable, feeding straight into the B8.1 latency work.

## Interpretation

This confirms journal 26 directly: the GPU was masking interpreter overhead. Moving just the
convolution hot loop to native code — same algorithm, same bus reformulation — recovers all of
it and more, on the CPU, because the per-block work is now one FFI call with no Python
allocations and coarse-grained thread parallelism across the buses. The deliberately narrow
scope (convolver only; routing/crossfade/LFE stay tested-numpy; tracking stays Python/MediaPipe)
kept the port small and let it inherit the existing parity proof (`BusMixer == voice pool`).

## Decision / next

- **The GPU dependency is now optional.** `--rust` is the default fast path to recommend; CuPy
  stays for hosts that prefer it. The host launcher can drop `--gpu` for `--rust` and run a
  smaller block (lower latency) — do that once the owner confirms drop-free on the host.
- Native build is host/dev-only (needs a Rust toolchain + `maturin develop`); CI and the
  Python wheel are unchanged. Recorded in AGENTS.md.
- Natural follow-ups (not needed for real time now): pull `BusMixer` + `bus_targets` + LFE into
  the kernel too (one FFI call per block instead of a numpy einsum + copy), and an in-place
  output buffer to avoid the per-block stereo allocation. (→ L56)

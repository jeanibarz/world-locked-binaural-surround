# 37 — One shared interpolation method for the whole passthrough (B4.4)

**Date:** 2026-10-18  ·  **Milestone:** M4 (end-to-end on Windows)  ·  **Status:** done

## Question

Journal 35 ended on this: the owner judged **timealign** the best-sounding interpolation and
wanted it to render the **daily 7.1 passthrough**, not only the movable test source. Until now
`M` cycled the method for the *test source only*; the 7.1 world-lock was hard-wired to `nearest`
(the fast 8-bus path). So the owner could hear timealign on the probe but their actual music still
played through nearest, and the GUI never showed which method was in force unless a test was
running. The ask (chosen from a menu of UI variants): **one shared method used for both**, changed
in one place, **shown always**.

## Context

The 7.1 renderer (`WorldLocked7p1`, journal 24) is a **bus mix**: the 8 channels are cross-faded
into **8 fixed-direction buses** (one per speaker's nearest BRIR) and the buses are convolved in a
batch. That is exactly why `nearest` is cheap — 8 buses, no synthesis. A *synthesis* method has no
single nearest BRIR per channel; it steers each channel over a **dense ring** of synthesized BRIRs
(`interpolated_ring`, B3.3 / journal 33 — one BRIR every `grid_step`°). The test source already did
this with a per-direction voice pool. To make the full 7.1 do it, the bus mix has to fan **8 inputs
into N grid buses** (N = 360/step), not 8-into-8.

## Change

New renderer `live_render.InterpWorldLocked7p1`:

- Generalized `BusMixer` to **n inputs → `n_bus` buses** (gain matrix `(n_in, n_bus)`, backward
  compatible — existing callers get `n_bus = n_in`). Each frame, every directional channel targets
  its nearest grid bus; the mixer cross-fades into the dense bus set.
- The dense ring (`interpolated_ring(brir, method, step_deg)`) is convolved by either the Python
  `StereoConvolver` or, with `--rust`, the native `binaural_kernel.BusConvolver` — the kernel is
  already parameterized by bus count (journal 27), so no Rust change was needed.
- LFE keeps the B4.3 treatment (`brir` convolve-through or flat per-ear gains).

Passthrough wiring:

- `build_main_renderer(method)` — `nearest` → the fast 8-bus `_nearest_renderer()`; any synthesis
  method → `InterpWorldLocked7p1` over the dense grid.
- **M now cycles the one shared method and rebuilds BOTH** the 7.1 renderer and the test source,
  swapped in together under the audio lock (with a "switching… (building)" overlay).
- The method is drawn **always** as an amber **title-bar chip** (`_console` variant 1, the owner's
  pick from the mockup gallery); the test readout no longer repeats it.

Dropout fix (owner report: *"when I tried timealign in test I had a lot of dropout"*):

- Profiled it. The cost is **not** the synthesis (that is a one-off at build) — it is the **voice
  pool accumulating tails while the source moves**. `WorldLockedSource` keeps one persistent
  convolver *voice* per grid direction; a retired voice rings its BRIR's **full ~0.5 s tail**. When
  the source **sweeps** a fine grid, every grid point it passes holds a full-length convolution
  alive, so voices stack up. On the 24000-tap BRIR: a *static* timealign source is ~1 voice / 10×
  RT, but a *moving* one at 10° climbs to **13 concurrent voices → 0.81× RT (peak 13 ms vs a 5.3 ms
  block budget) → dropouts**. `nearest` (8 coarse dirs) only reaches ~4 voices, which is why it was
  fine and timealign was not — finer grid, more left-behind voices.
- Fix: a `max_tail_ms` cap on `WorldLockedSource`. A **retired** voice now rings for at most that
  window, its output linearly **faded to silence** across the window (so cutting the tail short is
  click-free) and then dropped; the **active** voice is exempt, so a stationary source is unchanged.
  `None` (the default) keeps the original full-tail path — used by the nearest 7.1 voice pool and
  the offline parity reference, so neither changes. The passthrough's test source sets it (default
  **150 ms**, `--max-tail-ms`). Result: moving timealign @10°/block 256 → **1.95× RT, 5 voices,
  4.7 ms peak** (was 0.81×/13), real-time even pure-Python; block 512 → 7.8×, 1024 → 23×.

## Method / Result

- **Correctness.** A new parity test drives `InterpWorldLocked7p1` and a reference per-direction
  voice pool over the *same* grid with the same azimuth stream: the dense bus-mix equals the voice
  pool (plus LFE) to **1e-9** — the N-bus reformulation is faithful, same as the original 8-bus
  proof (journal 24). Further tests cover shape + all four methods, and the tail cap (it bounds
  concurrent voices during a sweep, the output fade keeps it click-free, and `None` is bit-identical
  to the old full-tail path). 5 new tests → **313 total**, ruff clean. (3 pre-existing
  `h5py`-missing SOFA tests still skip in this env — unrelated.)
- **Real-time.** Pure-Python, synthetic 8×24000-tap BRIR (matches the owner's real length): the
  dense synthesis 7.1 runs **~1.35× RT at block 1024 / 15° grid** but is **sub-RT at block
  256/512** (the many-bus convolution + Python per-block overhead). The native `--rust` kernel is
  ~5× the Python throughput (journal 27), so block 1024 has comfortable headroom on it. `nearest`
  is untouched — it still uses the fast 8-bus path, not this renderer.

## Interpretation

The split the owner was confused by is gone: there is now a single method knob, it governs what
they actually hear, and it is visible at rest. The cost is honest — a synthesis method on the whole
surround is genuinely more work than `nearest` (N dense buses vs 8), so it wants the native kernel
and a larger block. That is a real trade the owner now controls, with the **S** diagnostics panel
reporting live whether the chosen combination keeps up, rather than a hidden default.

## Learnings

- The bus-mix reformulation generalizes cleanly from "one bus per speaker" to "one bus per grid
  direction" — the same batched-convolution machinery and the same 1e-9 parity argument carry over.
  The only new thing is the input→bus fan-out, which `BusMixer` now does for any `n_bus`.
- `nearest` and a synthesis method are different performance regimes, not a tweak: keep the fast
  8-bus path as its own branch rather than forcing nearest through the dense renderer.
- A steered voice pool's RT cost is driven by **motion**, not the method — a sweep holds a
  full-tail voice alive per grid point touched. Profile the *moving* case; cap retired-voice tails.

## Decision / next

- Shipped shared-method + always-on chip + the test-source tail cap. **Host launcher
  recommendation:** for daily use with timealign, run the passthrough with
  `--rust --method timealign --block 1024 --grid-step 15`; keep `--method nearest` (any block) if
  the lowest latency matters more, and cycle to timealign with **M** only while A/B-ing. The test
  source now stays real-time while dragging/rotating thanks to `--max-tail-ms` (default 150 ms).
- **Validate the rear positions first** on the host — synthesis methods flip front↔back in the
  owner's rear BRIR gap (journal 18, a data problem), so timealign on the *full* 7.1 may surface
  that behind the owner on real content; if it bites, `nearest` stays the safe default and the gap
  is a BRIR-data fix (B1.8 / M9), not a renderer one.
- Follow-up (perf): moving `BusMixer` itself into Rust would remove the Python per-block overhead
  that caps the small-block synthesis path — noted, not done.

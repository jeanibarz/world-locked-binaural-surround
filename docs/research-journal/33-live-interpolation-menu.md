# 33 — Live interpolation-method menu for A/B comparison (B3.3)

**Date:** 2026-10-18  ·  **Milestone:** M3 (world-lock) / M5 render-quality  ·  **Status:** done; a hotkey in the snare demo switches methods live

## Question

After the layout was corrected to the Dolby angles (journal 32), the owner asked to **compare
the interpolation methods live** — and whether they're worth re-evaluating now. The live render
uses `nearest` (snap to the nearest of 8 real BRIRs + crossfade); the synthesis methods
(`timealign`/`minphase`/`notchwarp`) are the offline B1.10 experiment (journal 18 picked
`nearest` because synthesis flips front/back). The corrected layout has a **wide 80° front→side
gap** where `nearest` quantisation is coarsest, so interpolation is most tempting there — a good
reason to let the owner judge by ear.

## Method

Reuse the proven, click-free voice pool instead of inventing a real-time synthesiser:

- **`live_render.interpolated_ring(brir, method, step_deg)`** builds a **dense** directional
  `BakedBRIRSet` — one BRIR synthesised every `step_deg` around the ring via
  `baked_interp.interpolate_baked_brir` — plus its azimuth map. `nearest` just samples the real
  ring denser (no synthesis); the others fill the in-between directions.
- **`WorldLockedSource` gained an `azimuths=` override**, so it steers a source over that dense
  grid exactly as it does the 8-speaker ring (lazy per-direction voices, crossfade unchanged).
- The **snare demo** (`live_tracker_audio.py`, the single-source A/B instrument — as journal 18
  used) builds a renderer per method, cached; **`M` cycles** nearest → timealign → minphase →
  notchwarp, synthesising the grid on first use and swapping under the lock while audio keeps
  playing the old method. `--method` / `--grid-step` flags; the HUD shows the active method. The
  7.1 passthrough stays on `nearest` (the GPU/Rust kernels are built around the fixed 8 buses).

## Result

On the real BRIR (8ch × 24000-tap), grid build (36 dirs @ 10°) is **0.11 s timealign / 0.14 s
minphase / 1.29 s notchwarp** — a barely-perceptible hitch on first switch, instant thereafter;
all render finite. 3 new tests (dense-grid shape + nearest-on-grid equals the real BRIR; the
azimuth override steers a held source to the exact grid voice to 1e-9; bad-step guard). 308
total, ruff clean.

## Is it worth re-evaluating? (the owner's second question)

- **The angle fix alone likely helped most**: the steering boundaries were misplaced up to ~20°
  before, so re-listen to `nearest` first.
- Journal 18's core finding — synthesis flips front/back — is a property of the BRIR **data**
  (B1.11), unchanged by relabeling, so the verdict may still be `nearest`. But the 80° front→side
  gap makes the azimuth cost of `nearest` highest there; this menu lets the owner hear the
  tradeoff directly (smooth-but-flippy synthesis vs coarse-but-clean nearest). The judge model was
  unreliable on this polar axis (journal 18), so the ear is the arbiter.

## Decision / next

Shipped as a comparison tool, not a default change — `nearest` stays the default. If an ear test
favours a synthesis method in the front→side region, the follow-up is a hybrid (nearest for the
rear where flips dominate, synthesis for the wide front gaps) and making the live passthrough use
the dense grid (needs the GPU/Rust bus set to be variable, currently fixed-8).

# 14 — Parametric EQ engine: EqualizerAPO biquads, live and per-channel (B5.1)

**Date:** 2026-10-16  ·  **Milestone:** M5  ·  **Status:** done

## Question

Journal 13 showed the comp+EQ post-stage is **load-bearing for the spatial image**
(it raises the direct-to-reverberant ratio so the image stops wandering), not
cosmetic timbre — so the renderer needs its own EQ stage. B5.1 is the filter engine
for it: per-channel and bus **parametric EQ (biquads)**, matching EqualizerAPO's
semantics, **changeable live without glitches** (FR-8). What should it be, and —
given the sibling project already has a faithful EqualizerAPO EQ — build vs reuse?

## Context & decision (depend vs port)

The sibling `binaural_localization.eqapo` has a validated EqualizerAPO engine
(journal 12/13 reused it offline). AGENTS.md says *depend, don't duplicate*. But:

1. The product needs a **live block-processor** (stream audio, retune on the fly).
   The sibling's `chain.py` is an **offline impulse reconstructor** (feeds deltas,
   reads the effective IR) — the wrong *shape* for real-time.
2. Importing `binaural_localization` pulls **TensorFlow + soundfile** at import
   (the judge stack) — an unacceptable runtime dependency for a low-latency audio
   path (requirements §7).
3. The backlog mandate is literally to **reuse EqualizerAPO *semantics*** — and the
   semantics is a small, standard, stable kernel (RBJ Audio EQ Cookbook, ~60 lines
   of coefficient math). Porting that is not "duplicating logic/research" in the
   sense AGENTS guards against.

**Decision: port the RBJ coefficient math + the `Filter:` parser into
`binaural_headtracker.eq`, as a dependency-light live block-processor; keep the
sibling for *offline verification only* (the B5 spike).** To make "reuse the
semantics" literal and checkable, the port is byte-for-byte the sibling's formulas.

## Method

`binaural_headtracker.eq` (pure numpy/scipy):

- `BiquadSpec` + `parse_filter_line` — EqualizerAPO `Filter:` lines (PK/LS/HS,
  Q / BW-oct / shelf-slope, numbered REW form, aliases, `OFF`→None).
- `design` / `design_sos` — RBJ coefficients (PK/LS/HS/LP/HP/NO/AP), `a0`-
  normalised SOS rows — ported line-for-line from the sibling's `biquad.design`.
- `BiquadCascade` — **stateful streaming** cascade: retains `sosfilt` `zi` across
  blocks, so block-by-block == one offline `sosfilt` (no inter-block clicks).
- `ParametricEQ` — one cascade per channel over an `(M, C)` block (used for the
  per-channel 7.1 input EQ *and* the stereo output-bus EQ). `set_specs` retunes
  live with a short **linear crossfade** between the old and new cascades run in
  parallel — click-free by construction, always stable (no coefficient ramping).

## Result

B5.1 **done**. 20 new tests (172 total, ruff clean):

- **Coefficient correctness** via the actual frequency response (not the formula):
  peaking gain == `gain_db` at `Fc` (Q and BW-oct forms), low/high-shelf asymptotes
  at DC/Nyquist, zero-gain PK is flat, `a0` normalised.
- **Parsing** on the owner's real lines (PK Q, PK BW-oct, bare LS, `HS 6 dB`,
  numbered REW `Filter 1:`, aliases, `OFF`/non-filter → None).
- **Streaming == offline**: block-by-block cascade equals `sosfilt` to 1e-10 over
  ragged blocks; empty cascade is pass-through; `reset` clears state.
- **Live retune**: a hard swap equals a fresh cascade; a crossfaded swap starts
  from the old output (no click), stays bounded, and converges to the new filter
  (steady-state tone boosted the expected 12 dB).

**Parity ground-truth** (not a committed test — the sibling isn't a product dep, so
the check runs under the sibling's env): `binaural_headtracker.eq.design` ==
`binaural_localization.eqapo.biquad.design` on **all 70 of the owner's real filters**
(baked subjective EQ + per-ear comp), max coefficient difference **0.00e+00**. So
our EQ is bit-identical to EqualizerAPO's and the sibling's — the port is faithful.

## Decision / next

- **B5.1 done**: the renderer now has an EqualizerAPO-faithful, live, per-channel/bus
  parametric-EQ engine with glitch-free retuning, no heavy deps.
- **Next (new integration block, B5.3): emit the full chain.** Wire a per-ear
  **comp FIR** (a convolution — reuse `convolve`/`surround`) + the per-channel and
  bus `ParametricEQ` into the render path, so the static renderer outputs the full
  comp+EQ-equivalent signal (not the bare bake). Verify offline against the
  sibling's full-chain effective BRIR (the B5 spike already reconstructs it). That
  is what makes the renderer both *sound* and *localise* right — closing the loop on
  the "moving snare" (journal 13): EQ raises DRR, B1.7 would separate the tail.
- **B5.2** (preset save/load) remains later.
(→ L35)

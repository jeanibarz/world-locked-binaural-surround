# 49 — Per-bus direct + one shared late reverb: energy/onset validation (B1.14)

**Date:** 2026-10-18  ·  **Milestone:** M1 (render engine) / M8 (efficiency)  ·  **Status:** validated — prototype built, gated on owner by-ear sign-off (issue #53, measure-first)

## Question

A ~24000-tap (500 ms) BRIR is convolved **per bus**, but journals 12/13 showed the
**tail is diffuse and direction-independent** (interaural coherence ~0). Every
serious spatial-audio engine (Steam Audio, Resonance, Oculus) exploits this with
**per-source short DIRECT + one SHARED late reverb**. Can we? This is APPROXIMATE,
so — measure first (issue #53): validate the direct/tail split against the **actual
energy** in the owner's real BRIR, decide how to handle the leading delay, and
quantify the approximation error offline *before* trusting it. No kernel rewrite.

## Context / hard requirements (owner, issue #53)

1. **Generous 20 ms direct window** (not 5 ms) as the margin.
2. Validate the split against the real `baked_7p1_test` BRIR energy-vs-time.
3. The direct is **not** guaranteed to be in the first samples (filters may carry a
   leading propagation delay) → pick and justify **(a)** subtract a uniform constant
   delay then a fixed 20 ms window, or **(b)** a per-filter onset-aware window.
4. Quantify the error offline like journal 41 (full per-direction render vs
   direct(20 ms)+shared-tail), spectral comparison; the `pooled5` judge is optional.

## Method

Pure numpy/scipy on the owner's real BRIR (`surround.load_baked_brir`), headless.

- **Onset / energy** (`spikes/b1_14_onset_energy.py`): per channel per ear,
  the onset via `baked_interp.estimate_delay` (Hilbert-envelope peak) *and* an
  energy-threshold onset (first sample past 0.5 % cumulative energy), the cumulative
  energy curve, the 20 ms direct-energy fraction, and the interaural onset (ITD).
- **Approximation error** (`spikes/b1_14_approx_error.py`): split each BRIR at
  `D + 20 ms` (`D` = uniform lead delay); render two representative scenes — the
  same pink noise to all directional channels (**correlated**), and independent
  pink noise per channel (**decorrelated**) — the **full** per-direction way vs
  **directs + one shared tail** (shared = per-ear mean of the channel tails), and
  compare per-ear broadband level + octave-band levels (dB). The 7 directional
  channels (LFE excluded).

## Result (real BRIR: 8 ch, 24000 taps, 48 kHz)

**There is a uniform ~10 ms leading propagation delay.** The energy-threshold
onset of all 16 channel-ears sits in a **0.9 ms band**: 482–524 samples
(10.042–10.917 ms), spread 42 samples. The direct is **not** in the first samples —
cumulative energy is ~0 % until ~10 ms. The Hilbert-envelope onset
(`estimate_delay`, even widened to 30 ms) is **not** reliable here: on **RR_L,
SL_R, SR_L** it latches a later reflection at ~16.5 ms, not the first arrival.

**20 ms captures the direction-bearing early response and the whole ITD span.**
Max interaural onset difference = **64 samples = 1.33 ms** (R channel) ≪ 20 ms.
Direct-energy fraction within a 20 ms window from the uniform onset:
**min 49 %, mean 61 %, max 73 %** (onset-aware per-ear estimate_delay is worse:
min 47 %, mean 56 %; fixed-from-sample-0 is worst: min 28 %, mean 53 %, because it
wastes ~10 ms of the window on the leading silence). The remaining ~40 % is the
diffuse tail beyond 20 ms — the part journals 12/13 showed is directionless.

**(a) vs (b) decision → (a) uniform constant delay.** The onsets are uniform
(a single ~10 ms / 482-sample leading delay common to every filter), not
direction-varying, so a uniform subtract is the right model. It also preserves all
interaural/inter-channel timing exactly (same constant off every filter) and is
simpler. An onset-aware window (b) built on `estimate_delay` would be **less**
robust: it misfires to ~16.5 ms on three channels and would start the window after
the real direct, clipping it on exactly those directions.

**Approximation error — full vs directs(20 ms)+shared-tail** (shared = per-ear mean
of the channel tails):

| scene | broadband ΔL (L / R) | tail-only ΔL | octave band err (mean / max\|Δ\|) |
|---|---|---|---|
| correlated (same signal → all ch) | **+0.00 / +0.00 dB** | +0.00 dB | **0.00 / 0.00 dB** |
| decorrelated (independent per ch) | **−1.14 / −0.93 dB** | −4.96 / −3.77 dB | 0.74–0.91 / 1.32–1.41 dB |

The **direct term is identical** in both renders by construction (same windowed
samples), so **localisation, ITD/ILD and early timbre are exact** — the only error
is the tail. A `√N`-scaled (geometric-mean) shared tail was worse (correlated
+4.4 dB, decorrelated +2 dB), so the plain **mean tail** is the right representative.

**Why it reconstructs correlated exactly:** `Σ_c tail_c = N · mean_tail`, and the
shared stage convolves the *summed* scene, so for equal inputs
`conv(Σ x_c, mean_tail) = conv(x, Σ tail_c) = Σ_c conv(x, tail_c)`. The tail-level
error only appears as channels decorrelate (the shared mono reverb can't reproduce
`Σ|H_c|²` for independent inputs); real 7.1 content lives between the two rows, so
the tail-level error is bounded by **≤ ~1 dB broadband / ≤ ~1.4 dB per octave**.

**pooled5 localization judge:** **unavailable** — it lives in the sibling
`binaural_localization`, which is not importable in this WSL env. Skipped per the
issue; the spectral comparison above is the minimum and it is the direct term
(which the judge would score) that is exact here, so the judge adds little.

**Convolution reduction** (tap-equivalents, from the real split: direct 960 taps,
shared tail 23518 taps): **7 buses → 5.6×**, **36 buses (the dense interp grid)
→ 14.9×** — the single shared tail dominates, so the order-of-magnitude win shows
up at the many-bus counts the issue targets; load is constant per block.

## Interpretation

The owner's premise holds on his real BRIR. A generous **20 ms** direct window,
taken after subtracting the measured **uniform ~10 ms leading delay (decision a)**,
captures the full ITD span and the direction-bearing early response; the ~40 %
diffuse remainder is well modelled by **one shared late reverb = the per-ear mean
of the direction tails**, which is *exact* for correlated content and only ~1 dB
low broadband at full decorrelation. The approximation is a **diffuse-reverb-level**
variation with content correlation, **not** a spectral-shape or localisation error.
Worth building; still approximate → owner by-ear sign-off is the final gate.

## Change (prototype)

`binaural_headtracker.shared_reverb` (pure numpy, 11 headless tests):
- `energy_onset` / `uniform_lead_delay` — robust onset + the decision-(a) delay.
- `split_direct_shared` → `DirectSharedSplit`: per-direction short directs (lead
  delay trimmed) + one shared tail per ear (mean of the channel tails, direct
  region zeroed).
- `SharedReverbRenderer` / `render_shared_reverb` — stream a scene to stereo as
  `Σ_c conv(x_c, direct_c) + conv(Σ_c x_c, shared_tail)`.

Tests pin: correlated input reconstructs the full (trimmed) render **exactly**
(≤1e-8, incl. on the real BRIR); zero-tail is bit-exact for *any* input (the direct
path is exact per direction); decorrelated stays within 2 dB broadband on a
direct-dominated set; streaming == offline; LFE ignored; lead delay ~482 samples.
The exact `surround`/`live_render` paths are untouched.

## Learnings

- **A baked BRIR can carry a large uniform leading delay — validate onset before
  windowing from sample 0.** These filters put the direct at ~10 ms; a fixed 20 ms
  window from sample 0 captures only ~28–53 % of the direct energy (half the window
  is dead air), vs ~49–73 % once the uniform delay is subtracted.
- **Prefer a uniform-delay subtract over a per-filter onset window when the onsets
  are uniform** — and they are here (0.9 ms spread). The envelope-peak onset
  (`estimate_delay`) latches a late reflection on some directions, so an onset-aware
  window would clip the direct exactly where it's least robust; a uniform subtract
  preserves ITD/ILD exactly and can't misfire.
- **A single shared mono reverb reconstructs correlated content exactly and is only
  ~1 dB low at full decorrelation — because the direct dominates the energy.** The
  tail-level error swings with inter-channel correlation (mean-tail is exact for
  correlated, ~−1 dB broadband for decorrelated); this is the architecture's one
  real approximation and it never touches the direct/localisation.
- **Measure the approximation on the real BRIR with correlated *and* decorrelated
  scenes** — the content correlation, not the spectrum, is what moves the error, so
  one scene would have hidden it (correlated reads 0 dB everywhere).

## Decision / next

- **Offline validation passes; prototype built and tested.** Do **not** merge:
  approximate → needs the owner's by-ear sign-off (issue #53), and the native-kernel
  port + the exact-NUPC companion are separate follow-ups. PR left open for review.
- **Next (not this task):** owner listens to directs(20 ms)+shared-tail vs the full
  render; if it carries, port to the native kernel (`binaural_kernel`) and wire a
  `--shared-reverb` render path; otherwise prefer the exact NUPC companion.

# 08 — Direction interpolation (B1.2)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

Given an arbitrary direction `(az, el)`, can we return an HRIR interpolated from
the nearest **measured** directions of a SOFA set — well enough that the render
engine can place a speaker *anywhere*, not only at a measured grid point — without
the colouration that naive IR averaging causes?

## Context

B1.1 (journal 06) loads a SOFA set and gives the **nearest** measured IR. The
head-tracked engine (FR-5) needs a *continuous* field: as the head turns, each
world-fixed speaker sweeps through directions that fall between measured points.
Snapping to the nearest measurement would quantise the image and click on every
grid crossing.

The naive fix — average the neighbouring IRs — **comb-filters**. Two IRs whose
main arrivals differ by an inter-direction (or interaural) delay, summed
sample-for-sample, give a magnitude spectrum with periodic nulls (the two-tap
comb), which audibly colours the result and smears localisation. The low-frequency
**ITD** is the dominant lateral cue, so it must be interpolated *as a delay*, not
destroyed by summation. This is B1.2; the continuous-field interpolation is
distinct from B1.7 (coarse rotation over the owner's 8 fixed BRIRs).

## Hypothesis

**Time-alignment interpolation** (the delay + magnitude model used by most SOFA
interpolators) solves it: estimate each neighbour's broadband arrival delay *per
ear*, advance each IR to a common origin, blend the aligned IRs with
inverse-distance weights (no comb, because arrivals coincide), then re-apply the
weighted-mean delay. The explicit delay carries and smoothly interpolates the ITD
→ "ITD-aware". A k-nearest + inverse-distance weighting avoids triangulating the
(irregular) measurement sphere, which a true barycentric/VBAP scheme would need.

## Method

- `src/binaural_headtracker/interpolate.py`:
  - `estimate_delay(ir)` — broadband arrival as the **analytic-signal (Hilbert)
    envelope** peak with parabolic sub-sample refinement.
  - `fractional_delay(x, d)` — FFT linear-phase fractional shift (exact for
    integer `d`; the caller zero-pads so the circular wrap only moves zeros).
  - `nearest_indices` / `interpolation_weights` — k-NN by great-circle angle,
    `w ∝ 1/dᵖ` (p=2), one-hot on an exact hit.
  - `interpolate_hrir(set, az, el, k=3)` and `HRIRSet.interpolated_irs(...)`.
- `tests/test_interpolate.py` (17): the delay/shift/weight math directly, and
  `interpolate_hrir` on synthetic delta-IR sets — pinning exact reproduction,
  ITD interpolation, and comb suppression. Hermetic (no SOFA, no device).
- Real-data validation on **MIT KEMAR** (fetched, `.gitignore`d): exact
  reproduction, ITD smoothness/bracketing on the horizontal plane, and a
  **leave-one-out** accuracy test (predict a held-out measured direction from its
  neighbours; log-spectral distortion vs the true IR, ITD-aware vs naive).

### Negative result that reshaped the estimator

The first `estimate_delay` used `argmax(|ir|)`. It passed every synthetic test
but, on KEMAR, the interaural delay **jumped 8 samples between az 30° and 35°** —
a lobe-swap artefact: a real HRIR has several comparable sample peaks (pinna
reflections) and `argmax` hops between them. Switching to the **Hilbert-envelope
peak** (one smooth broadband maximum) removed the jump (12.25 → 14.43 samples
across the same 5°) with no change to the synthetic tests. Lesson 21.

## Result

- **72/72 pytest green** (17 new), ruff + format clean, on WSL/CI (no device).
- Exact reproduction at a measured direction: `max|interp − measured| = 0` (the
  one-hot exact-hit path returns the measurement untouched — no fractional smear).
- Synthetic comb test: a 50/50 blend of two deltas 10 samples apart has a naive
  magnitude null at ~0 but an ITD-aware minimum **> 0.8** of peak (no comb), with
  the single arrival at the mean delay.
- KEMAR horizontal plane (72 az @ ~5° spacing, 48 kHz):

  | Check | Result |
  |---|---|
  | Exact reproduction `(0°,0°)` | `0.0e+00` |
  | ITD at interpolated midpoints bracketed by neighbours | **97%** within ±0.5 smp |
  | ITD sweep 0°→90° | smooth **0 → ~35 samples** (~0.73 ms), monotone |
  | Leave-one-out LSD vs true IR — **naive avg** | mean **6.81** / median 7.63 dB |
  | Leave-one-out LSD vs true IR — **ITD-aware** | mean **2.50** / median 1.39 dB |
  | Held-out ears where ITD-aware beats naive | **82%** |

## Interpretation

- **The hypothesis holds on real data.** Predicting a withheld measured direction
  from its neighbours, ITD-aware interpolation is ~**2.7× closer** to ground truth
  than naive averaging (2.50 vs 6.81 dB LSD) and wins on 82% of ears — direct
  evidence that time-alignment, not sample-averaging, is the right model.
- **ITD interpolates smoothly and physically** (0 at front → ~0.73 ms at the side,
  97% of midpoints bracketed by their neighbours). The rare >1-sample outliers sit
  where a single measurement's envelope peak still catches an early reflection;
  they do not dominate the LSD.
- **k-NN inverse-distance is enough for M1.** It needs no sphere triangulation and
  reproduces measured points exactly. A barycentric/Delaunay-VBAP weighting
  (smoother across the grid, no third-neighbour pull) is a possible refinement if
  artefacts show up under fast head motion (noted as a follow-up, not needed now).
- **The Hilbert envelope is the load-bearing choice.** Delay estimation quality —
  not the weighting — decides whether the ITD is smooth; the raw-peak estimator
  looked fine until real data.

## Dependency licences (§ 9 compliance)

No new dependency. `scipy.signal.hilbert` is already baseline (scipy, BSD-3).

## Decision / next

B1.2 done: continuous ITD-aware direction interpolation over a SOFA set,
validated on KEMAR by leave-one-out. Next eligible block: **B1.3** (partitioned-
FFT real-time convolution engine with overlap-save) — the piece that will convolve
each channel with its (now interpolatable) HRIR in the real-time loop. Added a
follow-up **B1.8** for barycentric/VBAP interpolation if grid artefacts appear.

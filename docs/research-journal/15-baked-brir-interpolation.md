# 15 — Head-rotation interpolation over the owner's 8 baked BRIRs (B1.7)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

The static renderer (B1.6) has only the owner's **8 fixed-direction** BRIRs. But
both the head-tracked engine (M3) and a source that *pans between* speakers (the
rotating-source test the owner asked for) need the response at an **arbitrary**
azimuth. With 8 sparse, reverberant points we cannot interpolate the room the way
B1.2 interpolates a dense anechoic SOFA grid. How do we synthesise an in-between
BRIR that localises cleanly?

## Context

The owner's own idea: "**vary the ITD and blend the signals**." Journal 12/13
gave the key structural fact that makes this safe: a BRIR is a short, direction-
bearing **direct wavefront** followed by a **diffuse tail** (interaural coherence
~0 — essentially directionless). So the direct and the tail must be interpolated
*differently*: the direct carries the ITD/ILD/spectral cues and should be time-
aligned and blended; the tail has no direction to interpolate and must **not** be
time-aligned (shifting two different room tails and summing them comb-filters).

This is distinct from B1.2 (continuous SOFA, dense grid, no direct/tail split) and
from the rotating demo's first version, which used **amplitude panning** between
two whole speaker BRIRs — that renders *two* directions and *two* room tails at
once, so the phantom is diffuse, worst across the 90° gap at the back.

## Method

`binaural_headtracker.baked_interp.interpolate_baked_brir(brir, az)`:

1. **Bracket** `az` between its two adjacent ring speakers (the 7 directional 7.1
   channels by azimuth, wrap-around across the front), with a pairwise fraction.
2. **Split** each BRIR at `onset + direct_ms` (~3 ms) into direct / tail.
3. **Direct:** estimate each neighbour's arrival delay, time-align to a common
   origin, magnitude-blend with the pairwise weights, then re-apply the
   *interpolated* delay — a single resynthesised ITD at the in-between angle
   (B1.2's time-alignment idea, restricted to the direct so the sharp wavefront
   isn't comb-filtered). Reuses `interpolate.estimate_delay` / `fractional_delay`.
4. **Tail:** blend the neighbours' tails **in place** (no shift) with **equal-power**
   weights (`sqrt(weight)`) — the tails are decorrelated, so a linear sum would dip
   ~3 dB at the midpoint (the room would audibly dim between speakers); equal-power
   holds the room level constant across the pan.

The direct-onset search is restricted to the first `ONSET_SEARCH_MS` (10 ms) so a
loud late reflection can't capture the (whole-signal) Hilbert argmax and drag the
direct/tail split into the reverb.

An exact measured azimuth returns the measured BRIR untouched.

## Result

B1.7 **done**. 14 tests (189 total, ruff clean), on a synthetic ring whose direct
deltas encode a toy ITD:

- Measured azimuths reproduce exactly.
- Between two speakers the arrival ITD interpolates (≈ the pairwise mean) and is
  **monotonic** sweeping front→left.
- The tail is the in-place **equal-power** blend (not time-aligned) — verified
  sample-wise, and its **power is held within 1 dB** across the whole L→SL pan (no
  ~3 dB midpoint dip, which a linear sum of decorrelated tails would give).
- A loud late reflection in the tail does **not** move the direct/tail split (the
  direct region is byte-identical with and without it) — the early-window onset.
- Across the wide **back gap** (RL 135° → RR 225°), the midpoint is a **single
  clean arrival** (one dominant direct peak ≤ 4 samples wide), not two — the whole
  point versus amplitude panning.
- Wrap-around (FR 330° → C 0°) interpolates without error; right-side azimuths give
  a right-leading ITD.

**Audible check.** Regenerated the owner's 360°-in-8 s rotating snare *through
B1.7* (`caisse_rotating_360_interp.wav`) by granular time-varying convolution
(2048-sample Hann grains, 50 % overlap → click-free) with the per-grain
interpolated BRIR, then the full comp+EQ post-stage, for A/B against the
amplitude-panned `caisse_rotating_360.wav`. Gross ILD traces track each other (ILD
is a weak proxy and these BRIRs have modest ILD); the intended difference is a
*single* phantom at the true angle rather than two speakers' worth, best heard
across the back. (The comp+EQ post-stage here was taken from the sibling's offline
chain; the in-product version is B5.3.)

## Decision / next

- **B1.7 done**: `baked_interp` is the direction engine for the owner's sparse set
  — the counterpart of B1.2 (generic SOFA). Coarse with 8 points but renders a
  clean single phantom at any azimuth.
- **Next candidates.** The rotating demo's granular, click-free time-varying
  convolution is a direct precursor to **B3.1** (couple the tracker to the render:
  per-frame `Rᵀ(pᵢ−p_h)` directions + click-free crossfade) — but that needs the
  tracker (**M2/B2.1**) first. Orthogonally, **B5.3** (wire comp FIR + EQ into our
  own render path) would let the demo run entirely in-engine instead of borrowing
  the sibling's post-stage. Either is well-motivated; B2.1 opens the path to the
  actual head-tracked effect.
(→ L36)

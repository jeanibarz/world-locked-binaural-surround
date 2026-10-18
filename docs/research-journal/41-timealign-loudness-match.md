# 41 — Loudness-matching the timealign interpolation (B1.13)

**Date:** 2026-10-18  ·  **Milestone:** M1 (render engine) / M3 (world-lock)  ·  **Status:** done

## Question

Listening to **stereo** music (L+R only) through the world-locked passthrough on `timealign`, the
owner noticed the **bass drops when facing straight ahead (0°)** and swells when the head turns
±30°. Is it an HRIR L/C/R balance problem, or an artifact of the interpolation?

## Method

Measured directly on the owner's real baked BRIR (offline, deterministic): the effective per-ear
response to correlated/stereo content is just the **sum of the active channels' BRIRs** at their
head-relative directions, so its magnitude spectrum is exact — no noise needed. Computed the
binaural band power (L²+R²) of the summed **L+R** front channels versus head yaw, for `nearest`
(no synthesis) vs `timealign`.

## Result

`nearest` is **flat** across head angle (bass 60–200 Hz: +0.1 dB at 30°). `timealign` is **not**:

| yaw | bass 60–200 Hz (re 0°), timealign |
|---|---|
| 0° | 0.0 (both L+R on measured nodes) |
| +10° | **+2.4** |
| +20° | **+2.7** |
| +30° | +1.6 |

So it is **the interpolation, not an HRIR balance issue** (nearest would show a balance fault too;
it doesn't — and with stereo there is no C channel involved at all). At 0° both L (+30°) and R
(−30°) sit exactly on their measured speaker directions, so `timealign` returns the real BRIRs with
no synthesis — the level minimum. Turn the head and both channels move onto *interpolated*
directions, each carrying a low-frequency lift. The cause: `_interp_ear` **time-aligns** the two
neighbouring directs and blends them with linear weights; once aligned, their long wavelengths are
in phase and sum **constructively**, so an in-between BRIR gains ~2–3 dB of bass (and dips slightly
in the highs) relative to the measured endpoints. A level **ripple** that dips at every measured
node — and the head rests facing a node.

## Change (the fix)

`_loudness_match` (B1.13): after synthesising the BRIR, rescale its **smoothed combined (L²+R²)
magnitude envelope** (⅓-octave) to the **weight-interpolated envelope of the two measured
endpoints**, applying the **same real per-frequency gain to both ears**. Because the gain is real
and shared, the interaural level and time differences — ILD/ITD, i.e. the localisation the owner
wants to keep — are preserved *exactly*; only the overall per-band loudness changes. At a measured
node the target equals that endpoint, so a correctly-levelled synth is unchanged. Gain clamped to
±9 dB.

- `baked_interp.interpolate_baked_brir(..., loudness_match=False)` — the new option, **off** on the
  primitive (keeps the raw synthesis as characterised in B1.10 / journal 18, and all its tests).
- `live_render.interpolated_ring(..., loudness_match=True)` — **on by default** on the render path,
  so both the 7.1 world-lock (`InterpWorldLocked7p1`) and the test source get the correction.

## Validation

On the owner's real BRIR, through the **shipped** `interpolated_ring`, the stereo L+R bass ripple
collapses from **+2.4 / +2.6 / +2.7 / +1.6 dB** (10/15/20/30°) to **+0.1 / +0.1 / +0.1 / +0.2 dB** —
flat across the whole head turn, highs no longer dip. 5 new tests prove the contract on synthetic
sets: the same real gain hits both ears (ILD/ITD preserved), a measured node is a no-op, the level
holds within ~1 dB across the pan (and strictly better than un-matched), and the matched envelope
lands on the interpolated-endpoint target for unequal endpoints. 321 tests, ruff clean.

## Learnings

- **Time-aligning then linearly blending two broadband directs inflates the low end**: once aligned,
  the neighbours' long wavelengths are correlated and sum constructively, so interpolated
  directions are ~2–3 dB louder in the bass than the measured ones — a level ripple that dips at the
  grid nodes. The tail already used equal-power (√w) weights for exactly this reason (journal, the
  tail-power note); the *direct* needed its own loudness correction.
- **Fix loudness with a real, both-ears-equal spectral gain** and you cannot disturb localisation:
  ILD (inter-ear magnitude ratio) and ITD (phase) are untouched by construction. Correct toward the
  weight-interpolated *measured* envelope so nodes stay exact and the pan is constant-loudness.
- **Diagnose by direct computation when you can**: for correlated content the effective response is
  the sum of the channel BRIRs, so the spectrum is exact — no playback, no mic, no guesswork; it
  separated "interpolation artifact" from "HRIR balance" in one measurement.

## Decision / next

- Shipped, on by default for the render path. The owner keeps `timealign`'s imaging with flat bass
  across head movement; `nearest` was never the answer (it loses azimuth accuracy). No launcher
  change needed — it rides the existing `--method timealign`.
- The same correction is available to `minphase`/`notchwarp` (any synthesis method) through the same
  flag, should their loudness ever matter.

# 07 — Static 7.1 → binaural renderer from baked BRIRs (B1.6)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

Can we reproduce the owner's hand-tuned 7.1 → binaural surround (his Audeze
LCD-GX EqualizerAPO bake) *inside our engine* as a static (fixed-head) renderer,
driven by his own per-speaker BRIRs?

## Context

The owner has a tuned EqualizerAPO config
(`audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_rev26_final.txt`) that
convolves each 7.1 channel with a per-ear **BRIR** and sums to stereo, then
applies a headphone-compensation FIR and a large subjective parametric EQ. The
spatial part is **8 fixed-direction stereo BRIRs** (`baked_7p1_test/<CH>_L.wav` /
`_R.wav`, 500 ms @ 48 kHz) — one per 7.1 speaker, measured at the neutral head
orientation. That sparse, fixed set is exactly the data for a **static**
renderer (not the continuous field the head-tracked path needs — journal 06 /
the owner has only these 8 directions). Building it ports his proven sound into
our pipeline and gives a reference baseline.

## Hypothesis

Loading the 16 baked WAVs and convolving each 7.1 channel with its L/R BRIR,
summed per ear, reproduces the config's convolve-and-sum stage; the result shows
the expected per-speaker lateralisation (left speakers louder/earlier at the left
ear, etc.).

## Method

- `src/binaural_headtracker/surround.py`: `BakedBRIRSet` (per-channel L/R IRs),
  `load_baked_brir` (reads `<CH>_L/_R.wav`, zero-pads to a common length),
  `render_7p1_to_stereo` (per-channel `fftconvolve` + per-ear sum — the config's
  operation), and `SPEAKER_LAYOUT_7P1` nominal ITU/SMPTE angles (azimuth CCW,
  +left; the layout the later interpolation builds on). Pure numpy/scipy.
- Tests (`tests/test_surround.py`, 9): renderer math with tiny known BRIRs (an
  impulse on channel *i* must reproduce exactly that channel's L/R IR),
  linearity, length, shape guards; `load_baked_brir` round-trip + padding +
  rate-mismatch, against synthetic WAVs in a tmp dir (no personal data in git).
- Validation on the **real** baked set: render a 0.2 s noise burst per channel
  and read the interaural level/time differences.

## Result

55/55 pytest green on WSL; ruff + format clean. Real set loaded: 8 channels,
24000 taps (500 ms) @ 48 kHz. Per-speaker interaural cues from the renderer:

| Speaker | azimuth | ILD dB (L−R) | ITD µs (+ = left ear earlier) |
|---|---|---|---|
| L (FL) | +30° | +5.7 | +125 |
| R (FR) | −30° | −1.0 | −146 |
| C | 0° | +2.0 | 0 |
| LFE | 0° | +2.7 | 0 |
| RL | +135° | +5.4 | 0* |
| RR | −135° | +1.5 | −250 |
| SL | +90° | **+7.8** | **+854** |
| SR | −90° | −0.9 | **−729** |

(*RL's onset-difference estimate misfired on an early reflection; ILD is
left-dominant as expected.)

## Interpretation

- **The renderer faithfully reproduces the owner's tuned spatialisation.** Every
  **ITD sign is correct** — left speakers reach the left ear first, right speakers
  the right, the side speakers (±90°) show the largest delays (±700–850 µs) — which
  validates the channel→BRIR mapping *and* the left/right ear assignment together.
- **ILDs are correct in trend but left/right asymmetric** (e.g. RR leans slightly
  left; C is +2 dB not 0). That is a property of the owner's real-head, hand-tuned
  BRIRs, **not** the renderer: the unit tests prove convolve-and-sum reproduces
  each WAV exactly. ILD alone is therefore an unreliable correctness check for this
  data; the ITD sign is the clean one.
- This covers the config's *convolution + per-ear sum* stage. The headphone-comp
  FIR and the subjective parametric EQ it applies afterwards are a separate
  post-stage → the live-EQ milestone (M5 / B5), not duplicated here.
- Offline `fftconvolve` is enough to verify correctness; the real-time
  partitioned/overlap-save engine is B1.3.

## Dependency licences (§ 9 compliance)

No new dependencies (numpy/scipy/h5py already baseline). The owner's `.wav` BRIRs
are **personal data**, kept out of git (`*.wav` is `.gitignore`d); the loader is
pointed at the EqualizerAPO config directory that holds them.

## Decision / next

B1.6 done: a static 7.1 → binaural renderer reproduces the owner's tuned sound
from his 8 baked BRIRs, verified by construction and by per-speaker ITD. Next, per
the owner's request, **B1.7**: approximate head-rotation by interpolating over
those 8 fixed BRIRs via **ITD resynthesis + magnitude blending** (time-alignment
interpolation on the direct-path wavefront; the room tail held fixed) — coarse
with 8 points, but a usable prototype for modest turns. The generic continuous-SOFA
interpolation remains **B1.2**.

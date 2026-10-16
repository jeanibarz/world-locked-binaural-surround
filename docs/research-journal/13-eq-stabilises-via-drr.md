# 13 — Correction: the EQ *does* steady the image — via direct/reverb ratio, not interaural geometry

**Date:** 2026-10-16  ·  **Milestone:** M1↔M5  ·  **Status:** done (correction to entry 12)

## Question

Entry 12 concluded that the comp+EQ post-stage does **not** stabilise the spatial
image (the ITD/coherence metric was no steadier — slightly worse — with the full
chain), and therefore the "moving snare" is the reverb tail, not the EQ. Then the
owner listened to the regenerated A/B WAVs and reported the opposite of what that
conclusion predicted: the **full-chain** renders (`cmp_full_*`) are *nice and
steady*, the **bake-only** renders (`cmp_bake_*`) *wander / feel unstable* —
spatially, not just tonally. So: was entry 12 wrong, and if so, how?

## Context

Entry 12's metric was **windowed interaural cross-correlation** (ITD lag + peak
coherence). That measures interaural *geometry*: the ITD/ILD the two ears present.
A filter applied equally to both ears (the subjective EQ, `Channel: L R`) cannot
change that geometry — which entry 12 proved (0.16 dB L/R asymmetry). The error
was treating "interaural geometry unchanged" as "perceived image unchanged." They
are **not** the same thing: perceived stability of a reverberant source is
dominated by the **direct-to-reverberant ratio (DRR)** — how loud the fixed-
direction direct wavefront is relative to the undirected diffuse tail — and a
common-ear EQ *can* change that, because the direct hit and the diffuse tail have
different spectra. The owner's own hypothesis named this exactly: "maybe it's the
reverb tail, except that with full EQ the tail isn't audible/dominant enough to
affect spatialisation."

## Hypothesis

The full chain raises DRR: its large HF boost (+22 dB @ 7 kHz, +22.7 dB HS) and
the comp lift the broadband, impulsive **direct** sound, while the HF-poor
**diffuse tail** gains little — so the direct image dominates and the wandering
tail is masked. The channels the owner found least stable (rear/side) should show
the largest DRR gain.

## Method

Extended `spikes/b5_eqapo_offline_compare.py` with a DRR step: for each channel's
effective stereo BRIR (bake vs full), energy in a ±2 ms window at the onset (the
direct wavefront) over energy in the diffuse tail (8–68 ms after onset), in dB.

## Result

DRR is higher with the full chain on 7 of 8 channels, and the **rear/side
channels — the ones the owner flagged as unstable — gain the most**:

| channel | DRR bake | DRR full | Δ |
|---|--:|--:|--:|
| RR | **−5.1 dB** | −0.0 dB | **+5.1** |
| SL | 3.3 | 5.9 | +2.6 |
| SR | −0.7 | 1.1 | +1.8 |
| RL | −1.7 | −0.0 | +1.6 |
| L | 3.2 | 4.9 | +1.6 |
| LFE | 1.6 | 3.1 | +1.5 |
| C | 1.3 | 2.4 | +1.2 |
| R | 0.1 | 0.2 | ~0 |
| **mean** | **0.3** | **2.2** | **+1.9** |

In the bake, **RR's diffuse tail is 5 dB *louder* than its direct sound** (DRR
−5.1 dB) — so the rear-right image has almost nothing fixed to hold onto and
wanders badly. The full chain pulls it to ~0 dB. The tail does **not** become
directional (its interaural coherence stays ~0.0–0.09 either way) — the EQ makes
it *quieter relative to the direct hit*, not *directional*. That is exactly the
mechanism the owner described, and it matches the listening test.

## Decision / next

- **Correction to entry 12.** Its *geometry* findings stand (our render == EQ-APO
  bake to 2.8e-17; the subjective EQ is interaurally symmetric and cannot change
  ITD/ILD). But its *conclusion* — "the comp+EQ does not stabilise the image, so
  the movement isn't the EQ" — was **wrong**, because the ITD/coherence metric was
  the wrong proxy. The comp+EQ **does** steady the perceived image, by raising the
  direct-to-reverberant ratio (+1.9 dB mean, +5.1 dB on the worst channel). Both
  the cause (the diffuse reverb tail) *and* why EqualizerAPO doesn't suffer it (its
  EQ buries that tail under the direct sound) are now accounted for.
- **This elevates B5 and couples it to spatialisation.** The comp+EQ chain is
  **not** a cosmetic timbre stage — it is load-bearing for the perceived image.
  The product's static renderer should produce the **full** chain's output, not
  the bare bake, to sound right *and* stable. Our bake-only renderer (B1.6/B1.5)
  was never meant to be auditioned raw (journal 07 said as much).
- **Open design fork for B5 (next block).** We have a faithful, reusable EQ engine
  in the sibling (`binaural_localization.eqapo`); the product needs a *live,
  per-channel, glitch-free, eventually head-tracked* EQ (FR-8). So: **depend on the
  sibling** (AGENTS "depend, don't duplicate"; fast, faithful, but adds a cross-
  repo dep and the sibling's engine is offline/LTI, not a live block-processor) vs
  **port a live biquad+FIR stage into our engine** (reusing the sibling's RBJ
  coefficient semantics; satisfies the live/head-tracked requirement; some
  duplication of formulas). This is a genuine product/architecture choice, flagged
  for the owner rather than decided unilaterally.
- **B1.7 remains complementary.** Separating the direct wavefront from the room
  tail attacks the wander at its source (and is needed anyway so the tail does not
  rotate with the head under tracking). DRR-via-EQ and tail-separation are two
  independent levers on the same problem.
(→ L34)

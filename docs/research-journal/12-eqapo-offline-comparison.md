# 12 — Does the comp+EQ post-stage explain the "moving" snare? (B5 offline comparison)

**Date:** 2026-10-16  ·  **Milestone:** M1↔M5  ·  **Status:** done (investigation)

## Question

The owner reported that a rendered 7.1 **snare** clip (`caisse claire`) was well
placed at first but its image "moved a lot toward the end," while the *same*
material through his **EqualizerAPO** chain did not. An earlier interactive
session (not journaled) *hypothesised* that the missing headphone-compensation
FIR + subjective parametric EQ were what stabilises EqualizerAPO's image (the big
HF boost favouring the coherent direct sound over the diffuse reverb). Two
questions needed a ground-truth answer, not a guess:

1. **Is our renderer faithful?** Does `surround.render_7p1_to_stereo` (B1.6)
   produce exactly what EqualizerAPO's binaural *bake* produces on the same BRIRs?
2. **Does the comp+EQ post-stage actually stabilise the spatial image**, or only
   change the timbre?

## Context

The owner's active config is
`audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_rev26_final.txt`. Its
chain, read in full, is: (a) **bake** — duplicate each 7.1 channel per ear,
convolve with its baked BRIR (`baked_7p1_test/*.wav`, 24000 taps / 500 ms), sum to
L/R; (b) **per-ear comp** (`Include audeze_comp.txt`) — a compensation FIR
(`left.wav`/`right.wav`) plus ~19 per-ear peaking filters, **different for L and
R**; (c) **subjective EQ** — `Preamp -4.5 dB` and 31 LS/HS/PK filters under
`Channel: L R`, i.e. applied **identically to both ears**.

The sibling repo `binaural-localization` already has a faithful, unit-tested
offline EqualizerAPO engine (`binaural_localization.eqapo`: RBJ-cookbook biquads +
a config interpreter that reconstructs the effective 8×2 binaural IR by feeding
unit impulses — the chain is LTI). AGENTS.md says *depend on it, don't duplicate
it*, so the spike reuses it rather than re-implementing EQ in this repo.

## Hypothesis

If the previous session was right, adding the comp+EQ stage should measurably
**steady** the interaural image (lower ITD variance, higher interaural coherence),
especially on the diffuse rear/side channels. If the subjective EQ is applied
equally to both ears, it is interaurally symmetric and *cannot* change the image
at all — only the per-ear comp could.

## Method

`spikes/b5_eqapo_offline_compare.py` (reuses the sibling engine):

1. Parse the config and cut it at three points — **bake** / **bake+comp** /
   **full** — and reconstruct each cut's effective 8×2 binaural IR via impulses.
2. **Faithfulness**: compare the engine's *bake* IR to our loaded `baked_7p1_test`
   BRIR set, per channel, per ear.
3. **Interaural symmetry** per stage transition: mean over channels of
   `|Δ(L-ear magnitude, dB) − Δ(R-ear magnitude, dB)|`. ≈0 ⇒ the stage re-timbres
   both ears equally and cannot move the image; large ⇒ it alters the L/R geometry.
4. **Image stability**: render each of the 7 snare clips with the bake and full
   IRs; over sliding 1024-sample windows measure the interaural cross-correlation
   lag (ITD) and its peak (coherence); report ITD std and mean coherence.
5. Write bake/full audition WAVs (0.5 s raised-cosine tail fade) for A/B listening.

## Result

**1. Our render is bit-exact to EqualizerAPO's bake.** `|engine bake − our BRIR|
= 2.78e-17` (machine epsilon). `render_7p1_to_stereo` *is* EqualizerAPO's binaural
bake stage — settled numerically, not just by the LTI argument. There is no
rendering bug.

**2. The subjective EQ is interaurally symmetric — timbre only.**
`bake+comp → full` interaural asymmetry = **0.16 dB** ≈ 0: the 31-filter EQ
(`Channel: L R`) multiplies both ears by the *same* transfer function, so it
changes tone, **not** the image. The per-ear comp *does* change the interaural
relationship (`bake → bake+comp` = **2.93 dB**).

**3. Nothing in the chain stabilises the image.** Mean over positions:

| stage | ITD std (↓ = steadier) | coherence (↑ = sharper) |
|---|---|---|
| bake | 32.0 | 0.404 |
| bake+comp | 31.3 | 0.395 |
| full | 34.4 | 0.391 |

The per-ear comp barely moves stability (32.0→31.3), and the full chain is
marginally **worse** (34.4, 0.391), not better. **The previous session's
"EQ stabilises the image" hypothesis is refuted.**

## Decision / next

- **The missing comp+EQ does not explain the "movement."** It cannot: the
  subjective EQ is interaurally symmetric (proven, 0.16 dB), and the per-ear comp
  does not improve interaural stability. So the earlier guess was wrong.
- **The real end-of-clip wander is the BRIR reverb tail**, independent of the EQ:
  while the snare plays, the direct sound localises it; when it stops, only the
  diffuse ~500 ms room tail remains, which has no fixed direction, so the image
  "wanders." Confirmed earlier by the ITD jumping only at clip-end; mitigated here
  by a 0.5 s tail fade. The proper fix is **B1.7** (separate the direct-path
  wavefront from the room tail), *not* B5.
- **Any A/B difference the owner perceives vs EqualizerAPO is timbral / salience**
  (the chain's large LF/HF boosts change tone and how prominent the diffuse tail
  is relative to the direct hit), **not a spatial-rendering difference** — our
  render matches EqualizerAPO's bake exactly, and the EQ provably cannot move the
  image. The `around_the_room` montage, separately, moves *by design*.
- **B5 (parametric EQ) remains a legitimate product feature** for timbre /
  headphone matching (FR-8), and the sibling provides a faithful reusable engine —
  but it is **not** the fix for the reported movement. The next block aimed at the
  snare complaint is **B1.7**; B5 proper (an in-engine, live, per-channel,
  head-tracked EQ — and the depend-on-sibling vs port-into-engine decision) stays
  on the backlog as a product-tone block, now de-coupled from the movement issue.

This entry is the first durable record of the snare investigation (the earlier
pass was interactive only); it supersedes that pass's spoken conclusion.
(→ L32, L33)

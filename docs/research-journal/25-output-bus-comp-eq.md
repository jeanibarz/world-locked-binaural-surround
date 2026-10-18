# 25 — The global output-bus comp + EQ: matching the tuned EqualizerAPO sound (B5.3)

**Date:** 2026-10-18  ·  **Milestone:** M5 (live filters) / M4 render-quality  ·  **Status:** implemented, proven bit-exact against the owner's EqualizerAPO config offline; host listening is the owner's confirm

## Question

The owner ran the live passthrough (B4.1) and reported it sounds **tonally very different**
from their daily EqualizerAPO setup (`baked_7p1_rev26_final.txt`): thin, bass-light, forward.
They asked for a loopback comparison of EqualizerAPO vs our 0°/neutral render per speaker, and
— the key constraint — a correction applied **globally**, "an equalization that doesn't disturb
each BRIR individually." This is the B5.3 / FR-8 gap flagged back in journal 24.

## Context

B5.1 already ported EqualizerAPO's biquad semantics into a live block-processor
(`eq.ParametricEQ`, byte-exact with the sibling on all 70 of the owner's real filters). What
was missing was *wiring a global stage into the render path*. The renderer convolves a **bare**
7.1→binaural bake (`baked_7p1_test`): room + directions, but **no headphone compensation and no
subjective EQ**. The owner's EqualizerAPO config carries those — and reading it settled where:

## Method & finding — the difference is one post-downmix per-ear chain, nothing else

`baked_7p1_rev26_final.txt` convolves the **exact same** `baked_7p1_test` BRIRs the app loads,
downmixes to L/R, and *then* applies a global per-ear chain to the stereo bus:

1. `Include audeze_comp.txt` — headphone comp: `-15 dB`, a per-ear FIR (`left.wav`/`right.wav`,
   14400 taps), and per-ear peaking filters (19 L / 18 R, plus `-1.2 dB` on R);
2. subjective EQ: `-4.5 dB` + ~31 shelves/peaks applied to **both** ears.

Because `test_7p1_neutral_equals_static_render` already pins the renderer's neutral output equal
to that EqualizerAPO downmix, the **only** thing that differs is this chain — so no acoustic
loopback is needed to *find* the correction: both paths are digital binaural signals to the same
headphone DAC, whose response cancels. The chain's own per-ear magnitude response **is** "what
differs," computed directly: a big **sub-bass shelf (+12–14 dB, 30–80 Hz)**, a **low-mid dip
(−2 to −6 dB, 120–500 Hz)**, a **presence scoop (−6 to −10 dB, 5–7 kHz)**, an **upper-treble lift
(+7–10 dB, 10–14 kHz)** then a sharp cut at 18 kHz, plus the per-ear headphone correction. That
smile-with-a-scoop is exactly why the bare bake sounds thin and forward.

The fix is the owner's own prescription: apply that **same chain, once, to the stereo output** —
never touching the 16 BRIRs, so the world-lock steering is untouched.

## Structure

`binaural_headtracker.output_eq` (new, headless, numpy/scipy):

- A small EqualizerAPO config walker (`Include` resolved; channel selection **reset to `all`
  when an include finishes** — the journal-18 rule — so the subjective block hits both ears).
- The global chain is isolated without hand-picking lines: accumulate every `Preamp`/`Filter`/
  `Convolution` landing on bus **L** or **R**, and **clear a bus's accumulation on any `Copy`
  that reassigns it** — because a copy overwrites the bus with the downmix the renderer itself
  produces. Only operations *after* the final downmix survive = comp + subjective EQ.
- `OutputEQ`: a streaming per-ear stage `y = biquads(fir(gain·x))`, reusing the tested
  `PartitionedConvolver` (comp FIR) and `eq.BiquadCascade` (comp + subjective biquads). One
  block of latency per ear (no extra algorithmic delay); the passthrough's FIFO absorbs it.

Wired into both host apps behind `--output-eq <eqapo-config>`: the passthrough applies it in the
render **worker** (never the audio callback, L52); the snare demo in its render step.

## Result

- **Proven bit-exact offline.** Feeding `OutputEQ` (built from `rev26_final.txt`) the app's bare
  neutral render reproduces the sibling's independently-validated full-chain EqualizerAPO
  reconstruction (`binaural_localization.eqapo.chain.reconstruct_brir`) to **max 1.5e-14** across
  all 8 input channels and both ears. Extracted gains L −19.5 dB / R −20.7 dB, 50/49 biquads,
  14400-tap comp FIR — all as the config specifies. So *app render + output EQ == EqualizerAPO*.
- 7 new unit tests (synthetic CI-safe config: extraction, streaming==offline to 1e-9, the
  include-resets-to-all rule, the pre-downmix-dropped rule, pass-through, shape guards).
- **285 tests pass (7 new), ruff clean.**

## Interpretation

The tonal gap was never a plumbing bug (channel order and routing were already proven in B4.1);
it was the missing comp+EQ, exactly as predicted. Keeping it as an **output-bus** stage — rather
than re-baking 16 BRIRs — is both what the owner wanted and the correct DSP: the chain is LTI and
acts on the final mix, so it is independent of the world-lock steering and retunable live later.

One coupling to remember: the EQ's +22/+28 dB bass shelves exist to **restore** the sub-bass the
LFE BRIR strips, so `--output-eq` must pair with **`--lfe-mode brir`** to match EqualizerAPO;
`flat` LFE (B4.3) already keeps that bass, so combining it with the EQ would double it. The host
launchers now set `--lfe-mode brir --output-eq …`.

## Decision / next

B5.3 closes the "sounds/localises right" loop (journal 13/14): the live renderer now emits the
full comp+EQ-equivalent output. Host listening to confirm by ear is the owner's (the maths is
proven). Residual render-quality items are unchanged: the rear front/back BRIR gap (B1.11) and
HRIR fine-tuning (M9). Remaining M5 work: **B5.2** preset save/load (layout + HRIR + EQ + calib),
which can now serialise the output-EQ config path alongside the rest.

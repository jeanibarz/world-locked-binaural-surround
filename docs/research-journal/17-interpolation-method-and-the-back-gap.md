# 17 — Direct-path interpolation: notch-warp wins, but the back gap is a data problem (B1.10)

**Date:** 2026-10-16  ·  **Milestone:** M1 (→ M7/M9)  ·  **Status:** done

## Question

B1.7 interpolates the owner's 8 baked BRIRs, but a rotating source was perceived as
**transitionally confused / unstable**, worst in the wide side↔rear gaps — while the
static speaker positions localise fine. The owner traced it to the *direct-path
interpolation*. Which interpolation avoids the artifact — and does any of them
actually fix it?

## Method — three methods, evaluated from every angle (per the contract's depth rule)

Three ways to interpolate the direct wavefront (`baked_interp.method`):

- **timealign** — time-align the two neighbours and blend in the time domain. Keeps
  real phase (externalisation) but the *complex* sum cancels where the two HRTFs'
  pinna notches differ → comb.
- **minphase** — interpolate log-magnitude, rebuild a plain minimum-phase impulse +
  ITD. Comb-free, but discards excess phase.
- **notchwarp** — slide the single N1 notch from each neighbour's frequency to the
  interpolated one (frequency-warped magnitude), reconstructed as *minimum-phase of
  the warped magnitude × the nearer neighbour's **excess** (all-pass) phase*.
  Comb-free **and** keeps real excess phase.

Evaluated by four independent lenses, not one average:

1. **Unit/analytic:** on synthetic combs, timealign dips to **0.44×** the parents'
   magnitude envelope (spurious null); minphase/notchwarp stay in the geometric
   interior (≥0.85) — mathematically can't invent a notch.
2. **Envelope on the owner's real BRIRs, 9 angles:** minphase/notchwarp eliminate
   the spurious comb on 9/9 (timealign dips to 0.03–0.27 at the exact trouble
   azimuths 15°, 112°, 247°).
3. **Synthetic-listener judge** (generic IRCAM template; built as a reusable
   harness): notchwarp is **best on every aggregate** — overall angular error 60.7°
   (timealign 70.0, nearest 71.0), median-plane QE **56%** (vs 83–87%), front/back
   flip **55%** (vs 79–85%). Interpolation beats the nearest-speaker snap on the
   sides (112°/247°).
4. **The owner's ears:** minphase sounded *less distant* (externalisation lost —
   excess phase matters); **notchwarp is better than minphase** and keeps the image
   out, but rotation is *"still a bit confusing."*

## Result

- **notchwarp is the best interpolation** — the only method that is both comb-free
  *and* keeps externalisation, confirmed by judge + ear + the comb metrics. Adopted
  as the default; timealign/minphase kept as switchable comparisons. 21 tests.
- **But interpolation is not the ceiling.** The single most actionable, method-
  *independent* finding: the **90° back gap (≈150–210°) is broken for every method**
  (judge angular error 115–151°, image collapses to the front). Why — the owner's
  own measured **rear BRIRs (RL/RR) read as front/back-ambiguous** to the judge
  (sibling entries 18/19: P(front) 0.85–0.94). No blend of two front/back-ambiguous
  endpoints can manufacture a correct back cue. That matches the owner's residual
  confusion and is why notchwarp improves the gaps but doesn't fully resolve them.

## Caveats (trust, per the depth rule)

- The judge here is a **generic IRCAM-template** listener, and the sibling's entry
  20 says it does **not** predict *this* owner's front/back perception on his
  reverberant/EQ'd render — so the absolute median-plane numbers are the least
  trustworthy column; the **lateral axis and relative ranking** are the safe read.
  "minphase wins the judge" was a trap (the model rewards clean min-phase magnitude,
  exactly what the owner rejected); notchwarp scoring well *while keeping excess
  phase* is the meaningful result.
- The definitive re-score awaits the owner's **mixed-rig judge** — now resolvable
  via the B7.0 registry (`judge_registry.best_judge()`) once the sibling publishes
  it (delegated; it is (re)training the pooled judge per entry 24).

## Decision / next

- **Ship notchwarp** as the interpolation (best available, evidence-converged).
- **The real frontier is a data problem, not interpolation:** the rear front/back
  ambiguity. Levers: better rear cues or an HRTF **set** that resolves the owner's
  rears (the sibling's HRTF-selection track found public sets that do). Gated on the
  mixed-rig judge for an objective re-score of methods *and* candidate rear fixes.
(→ L38, L39)

# 18 — Rotation: nearest real BRIR wins (judge + ear); flips beat accuracy as the worst error (B1.12)

**Date:** 2026-10-17  ·  **Milestone:** M1  ·  **Status:** done

## Question

Entry 17 adopted `notchwarp` as the default interpolation on a *generic* IRCAM-
template judge + a partial ear test (notchwarp vs minphase only). With the owner's
**mixed-rig judge (`pooled5`)** now published (B7.0 registry) and a **dense,
cue-decomposed horizon map** built against it, which rotation method actually wins?

## Method

Dense horizon map (every 3°, el 0) scored by `pooled5` via the full-distribution
readout (`explain_localization`/`diagnose_distribution`): per azimuth, decoded
angular error, the **trustworthy lateral (ITD/ILD)** error, and the low-confidence
front/back probability + bimodality. Three rotation methods compared on the identical
grid, plus an apples-to-apples ear A/B (same snare/orbit/tail/EQ-chain): `nearest`
(nearest measured real BRIR + render-layer crossfade), `timealign`, `notchwarp`.

## Result

**Both the judge and the ear converge on `nearest`.**

| method | mean ang | lateral err (trustworthy) | front/back ok | %>45° |
|---|--:|--:|--:|--:|
| **nearest** | **24.9°** | **16.2°** | **86%** | **13%** |
| timealign | 42.7° | 15.8° | 69% | 25% |
| notchwarp | 51.3° | 22.3° | 57% | 46% |

- `notchwarp` — the entry-17 default — is the **worst**, and not only on the shaky
  front/back axis: it is worst on the **trustworthy lateral axis** too (22.3° vs
  ~16°). Its specific failure: in the front-left gap (33–90°) it produces *confident
  rear flips* — plausibly because warping the N1 **pinna-notch frequency** moves the
  very cue that encodes front/back, pushing a front source's notch into "rear".
- The owner's ear, independently: **nearest is best.** On the synthesis methods he
  heard *"front→centre / back→centre / back→front rapid transitions… disturbing and
  wrong."* On nearest: only *"small azimuth-varying errors… much less confusing."*

## The principle (why nearest wins)

**A stable image with a small azimuth error beats an image that jumps hemispheres.**
Spectral interpolation buys a smooth-looking phantom at the cost of **front↔back
flips** — the most disturbing, most *wrong* error class. `nearest` never synthesises
a phantom spectrum, so it never flips; it only snaps to the closest real measured
direction (8 zones), and smooth motion comes from the **render layer's crossfade**
(the rotating demo's overlap-add; the live B3.1 click-free crossfade), not from
faking an in-between spectrum. Real measured BRIRs carry real cues; the judge rewards
that and the ear confirms it.

## Decision / next

- **`method="nearest"` is now the default** of `interpolate_baked_brir` (returns the
  nearest ring speaker's real BRIR, no synthesis). `timealign`/`minphase`/`notchwarp`
  are kept as switchable options for analysis. This **reverses the entry-17/PR-18
  default** — made on weaker evidence (a generic judge). The mixed-rig judge + dense
  cue-decomposed map + ear caught it before it shipped further: the evaluation
  methodology (AGENTS "Analysis depth" + the registry + the judge-readout contract)
  worked exactly as intended.
- **Render layer owes the crossfade.** B3.1 (tracker → render) must crossfade between
  successive `nearest` BRIRs click-free as the head/source moves (the demo proves the
  shape via Hann overlap-add).
- **The back gap stays a data problem** (B1.11): 165–210° is bimodal/diffuse for every
  method, including nearest — the owner's rear BRIRs lack a clean front/back cue. That
  is the next real frontier (rear cues / HRTF selection), not the rotation method.
(→ L40, L41)

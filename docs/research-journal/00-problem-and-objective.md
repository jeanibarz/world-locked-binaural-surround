# 00 — Problem & objective

**Date:** 2026-10-15  ·  **Milestone:** M0  ·  **Status:** done

## Question

What are we building, for whom, and why — stated once, so every later block can
be traced back to it.

## Context

The owner has a personal Windows binaural-surround setup (EqualizerAPO, 7.1 folded
to headphones, built from his own in-ear measurements and hand-tuned by ear; see
the sibling project `binaural-localization`). It is good but **static**: the sound
field rotates with the head, which weakens externalisation and front/back
perception, and tuning is subjective and unverifiable.

Real listening gives the brain a powerful cue that static binaural throws away:
when you move your head, real sources stay fixed in the world, and the *change* in
the binaural signal as you move is what resolves front/back and externalises the
source. We want to put that cue back.

## Objective

A Windows application that renders any app's surround audio to headphones as a
**world-locked, head-tracked binaural** stage:

- virtual loudspeakers at **fixed, screen-relative world positions** (configurable
  distance/scale);
- a **camera head-tracker** estimating head orientation (azimuth + elevation,
  ideally roll) and position in real time;
- per-frame **convolution with the HRIR/BRIR for each speaker's direction relative
  to the current head pose**, summed to stereo for any headphone;
- so **moving the head leaves the sound fixed in the room and changes only the
  convolution** — training the brain to externalise into real space;
- captured through a **virtual Windows 7.1 audio device** (VoiceMeeter-like) so
  any app can feed it, and output to any selectable headphone;
- with **live, adjustable filters** (parametric EQ, EqualizerAPO-style) and an
  **AI judge-in-the-loop** (the sibling project's synthetic listener) to calibrate
  HRIR/EQ **objectively** instead of by unreliable ear;
- at **minimal end-to-end latency**, the make-or-break property for realism.

The perceptual hypothesis — that world-locking the stage while head-tracking the
convolution measurably improves externalisation and front/back over static
binaural — is the thing the whole project exists to deliver and, eventually, test.

## Method (how we will build it)

Block by block, prototype-first: a Python render engine + tracker to prove the
world-lock loop end-to-end at relaxed latency (M1–M4), then live EQ + GUI + the
AI-judge calibration loop (M5–M7), then harden the hot audio path to native for
the latency target (M8), then HRIR fine-tuning and a donationware release (M9).
The autonomous chain records each block here and in the backlog.

## Interpretation

No existing product integrates all of this (individual/AI-tuned HRIR + camera
head-tracking + world-locked virtual 7.1 device on Windows + live EQ + AI-judge
calibration); the closest, the Smyth Realiser A16, is costly dedicated hardware.
That gap is both the technical motivation and the basis for a possible
donationware product with a paid AI-calibration tier.

## Learnings

- **The core mechanic is world-lock: fix the speakers in the world, move the
  convolution with the head.** Everything else (virtual device, EQ, GUI,
  calibration) serves that one perceptual idea. (→ LEARNINGS #1)

## Decision / next

Proceed with **M0 foundations** — set up the Python prototype stack (B0.1) and the
audio / virtual-device / head-tracker spikes (B0.2–B0.4) — before any render code,
so the latency and device realities are known early. Next block: **B0.1**.

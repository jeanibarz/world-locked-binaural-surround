# 40 — Measuring pose age at the ear (B8.5)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** done (software path)

## Question

The owner asked how to get end-to-end latency under 50 ms without a faster camera. Journal 36
reframed the target as **pose age at the ear** and named orientation prediction (B8.6) as the lever
— but every number so far was an *estimate* (the panel's `1.5 × frame interval` + buffer sum). You
cannot optimize 200 ms → 50 ms blind, and B8.6 needs the real pipeline delay to extrapolate over.
So before prediction: **measure the real pose-age-at-ear, as a distribution.**

## Change

`HeadPose` already carries a capture timestamp `t`. We now carry it through to the ear:

- New pure module `latency.LatencyProbe` / `LatencyStats`: a rolling window of millisecond samples
  reduced to **p50/p95/p99 + min/max/mean** — a distribution, because a rare 100 ms spike wrecks a
  world-locked scene far more than a good mean hides (requirements § 7.3). `add` is a cheap deque
  append (safe on the render worker's hot path); `stats` reduces only when the GUI asks.
- The passthrough carries `state["pose_t"]` beside the steered azimuths. When the render worker
  pushes a block to the output FIFO, it records **pose age at the ear** =
  `(now − pose_t) + (FIFO blocks ahead of it)·block_ms + device_buffer_ms` — i.e. everything from
  the pose timestamp through detect, staleness, render, the output queue and the device buffer. The
  only part it misses is the camera exposure/transport *before* `pose_t` (~1 frame), which is not
  software-measurable and stays a small labelled estimate.
- `diagnostics.classify` takes the measured distribution; when samples exist the **head→ear**
  headline becomes `camera-front(≈1 frame est.) + measured p50`, flagged **(measured p50)**, with a
  `p95 / p99 / n` tail line (the panel shows the distribution, not just a point). On exit the app
  prints the session's `p50/p95/p99/min/max`.

The pose timestamp now flows tracker → render → ear — the exact seam B8.6's predictor plugs into
(predict the pose forward by the measured delay).

## Method / Result

- 9 new tests (7 for the probe: percentiles, rolling window, the tail exposing a spike the median
  hides, invalid-sample rejection; 2 for `classify`: the measured value overrides the estimate and
  is flagged, and it falls back cleanly with no samples). 317 → **326 total** (incl. host rust
  suite), ruff clean. (3 pre-existing `h5py` SOFA failures in WSL — unrelated.)
- Display/panel verified by logic + tests; the live numbers are the owner's **S** panel + the
  on-exit print (cv2 + the audio/camera stack are host-only).

## Interpretation

The panel stops guessing: head→ear is now a measured p50 with its p95/p99 tail, sourced from the
real pipeline rather than a frame-rate heuristic. That turns "it feels like ~200 ms" into a split we
can act on — how much is the output buffer (tune it / WASAPI exclusive) versus pose staleness the
camera frame rate forces (only prediction removes it). And the timestamp seam means B8.6 can be
built straight on top.

## Learnings

- **Measure the latency you care about as a distribution before optimizing it.** A single mean (or a
  frame-rate estimate) hid both which stage dominates and the p99 spikes that actually break
  world-lock. The cheap version — timestamp the pose, subtract at the moment the block is queued for
  playout, keep p50/p95/p99 — needs no hardware and is enough to prioritize the real work.
- Carry the measurement seam (the pose timestamp) end-to-end even if you are "only measuring": it is
  the same seam the fix (prediction) needs, so measuring first costs nothing extra later.

## Decision / next

- Shipped the software measurement + the timestamp seam. **Next: read the owner's real
  distribution** from a live session, then do **B8.6 orientation prediction** (extrapolate the pose
  forward by the measured p50) and tighten the audio output path in parallel (lower `--latency-ms`,
  WASAPI exclusive — B0.5); each ms shaved off the raw delay is a ms less the predictor must cover.
- Still open (needs host hardware, not code): the **motion→sound** cross-check with a motorized /
  external rotation reference (requirements § 7.3c) to validate the software number against ground
  truth and capture the exposure the software stamp can't see.

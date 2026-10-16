# 19 — Head-pose tracker: smoothed (yaw,pitch,roll) on its own thread (B2.1)

**Date:** 2026-10-17  ·  **Milestone:** M2  ·  **Status:** done

## Question

M3 needs a **steady, low-latency** head pose, updated continuously **off the audio
thread**. B0.4 proved MediaPipe can produce a yaw/pitch/roll estimate (~12 ms
inference); B2.1 turns that into a *usable stream*: smoothed against jitter, on a
background thread, with the latency/jitter trade-off exposed — and testable without a
camera.

## Method

`binaural_headtracker.tracking` (portable, device-free):

- **`OneEuroFilter`** — the Casiez et al. 1€ filter. Its two knobs *are* the
  latency-vs-jitter trade-off: `min_cutoff` (baseline smoothing floor — lower =
  steadier at rest but laggier) and `beta` (how fast the cutoff opens with motion —
  higher = less lag on fast turns). `d_cutoff` low-passes the derivative.
- **`HeadPose`** (yaw/pitch/roll in deg + timestamp) and **`PoseFilter`** (one 1€
  filter per angle; within the ±45° operating range there is no wrap, so each angle
  filters directly).
- **`HeadTracker`** — pulls raw poses from an abstract **source** iterator on a
  daemon thread, smooths each, and publishes the latest pose (lock-guarded) plus
  update-rate / jitter `stats`. `start/stop/wait`, context-manager, clean stop on an
  endless source. The source abstraction is the key seam: a **recorded clip** drives
  it headless in tests; the live **MediaPipe webcam** source lives in the host spike.
  Stats are derived from the samples' own timestamps, so they're deterministic.
- Reuses `head_pose.head_pose_from_matrix` (B0.4) for the matrix→Euler step.

## Result

B2.1 **done**. 14 tests (222 total, ruff clean), all headless and with **no real
sleeps** (timing is timestamp-driven): 1€ first-sample passthrough, constant
convergence, step lags-then-settles, lower `min_cutoff` ⇒ more lag, jitter reduction,
reset; per-axis pose smoothing; and the threaded tracker on recorded clips — publishes
the tracked latest value, `latest()` is None before the first sample, `stats()` recovers
the clip's rate/jitter, output is steadier than a noisy source, clean stop on an endless
generator, double-start guard, context manager.

Host spike `spikes/b2_1_head_tracker.py` wires a webcam→MediaPipe generator into the
tracker and prints the live smoothed pose + measured rate + per-frame inference latency
(the <50 ms budget) — host-only (camera + `mediapipe` + the `.task` model), like B0.4.

## Decision / next

- B2.1 done: a smoothed pose stream on its own thread, tunable, camera-decoupled for
  test. The tracker's abstract source + `latest()` is exactly the seam **B3.1** plugs
  into: per frame, take `latest()` → head rotation `R` → `WorldLayout.head_relative_
  directions(R)` (B1.4) → per channel the **nearest** real BRIR (B1.12) → crossfade
  click-free (entry 18). 
- Next eligible: **B2.2** (calibration — neutral "looking at screen centre" pose,
  camera→world frame), then **B3.1** (the world-lock coupling). The Euler→`R`
  sign/axis mapping, deliberately deferred since B1.4, is B2.2's job.
(→ L42)

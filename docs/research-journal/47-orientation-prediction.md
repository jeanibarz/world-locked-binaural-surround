# 47 — Orientation prediction to the render instant (B8.6)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** done (first cut)

## Question

The live split (journal 46) measured head-move→ear as **stale 32–46 + queue 16–20 + device 22 ms**:
~40 ms of irreducible output buffer plus **~40 ms of camera pose staleness**. No buffer tweak
touches `stale` — the pose is simply that old by the time its sound is heard. The only lever without
a faster camera (journal 36) is to **predict** where the head will be at the render instant and
steer with that.

## Change

New `prediction.py`:

- `AlphaBetaPredictor` — a scalar α-β tracker (position + velocity) that smooths the `(angle, t)`
  stream and extrapolates `angle + velocity · horizon`, with the step clamped to a max so a stop
  doesn't overshoot. α-β (not the tracker's 1€ filter) because extrapolation needs an explicit
  velocity state; the 1€ smoothing stays, this runs on top for the look-ahead.
- `PosePredictor` — one tracker per Euler angle (head motion is yaw-dominated and the horizon is
  short, so per-angle linear extrapolation suffices — no quaternions), with a bounded horizon
  (`max_horizon_ms`) and a bounded step (`max_rate_dps × horizon`, the overshoot guard for the ~1°
  static MAA). `predict(pose, 0)` returns the smoothed current pose, so the same path covers
  predictor-off.

Passthrough wiring (`--predict`, `--predict-ms` 0 = auto): the camera loop feeds the smoothed pose
to the predictor and steers the **audio** on `predict(pose, horizon)`, where the **horizon is the
live-measured pose-age-at-ear p50** (the thing we just instrumented, journal 46) — so it predicts
forward by exactly the delay it's compensating, self-tuning as conditions change, with a 60 ms
default until enough samples land. The HUD still shows the real pose; the radar/audio follow the
prediction. A `predict NNms` readout shows the active horizon. Launcher enables it.

## Result

7 prediction tests (constant-velocity ramp lands ahead; a still head predicts no motion; a stop
settles without persistent overshoot; the step clamp bounds a fast move; the pose wrapper
extrapolates yaw and keeps the timestamp; horizon clamps and 0 disables). 334 tests, ruff clean. No
change to the render/DSP — prediction only chooses the pose value fed to the existing world-lock.

## Interpretation

Prediction doesn't shrink the pipeline delay; it cancels its *effect*. Steering on the pose
extrapolated by the measured ~40 ms of staleness means a head turn is reflected in the binaural cue
when the sound is heard, not a frame-and-a-buffer later — so the *effective* `stale` drops toward
the predictor's error (small during smooth motion), and the jittery p95 tail (which was the camera's
frame jitter) is smoothed by the velocity model. Expected felt latency: the ~40 ms buffer floor,
i.e. under 50 ms, during motion; the trade is a possible slight overshoot at an abrupt stop, bounded
by the step clamp and tunable via `max_rate_dps` / the α-β gains.

## Learnings

- **You can't remove camera staleness, but you can predict through it — and the horizon should be
  the *measured* delay, not a guess.** Feeding the live pose-age-at-ear p50 back as the extrapolation
  horizon makes the predictor self-tune to the actual pipeline, and it's stable because prediction
  changes the pose value, not the timing that the probe measures.
- **For a short horizon on yaw-dominated motion, per-Euler-angle α-β beats reaching for
  quaternions** — simpler, testable, no gimbal handling, and enough; keep the quaternion const-ω
  form as a refinement only if combined pitch/roll rotation at long horizons ever needs it.
- **Bound the extrapolation step, not just the horizon** — a velocity that hasn't decayed at a stop
  would fling the estimate past the real angle (the ~1° static resolution is unforgiving); clamping
  the per-block step is the cheap overshoot guard.

## Decision / next

- Shipped the first cut, launcher-enabled (`--predict`, auto horizon). **Validate by ear on the
  host**: does the world-lock feel tighter during turns, and is the settle at a stop clean? Tune
  `--predict-ms` / the α-β gains if it overshoots or feels nervous. The S-panel `stale` won't drop
  (it's the true pipeline age) — the win is perceptual; judge it by feel, and optionally by a
  motion→sound measurement (B8.5 §c, host hardware) later.
- If it holds up, the latency milestone is met (buffer floor ~40 ms + prediction). Remaining latency
  ideas (WASAPI-exclusive via the APO/VST single-clock path, B8.7) are for A-V sync, not head-lag.

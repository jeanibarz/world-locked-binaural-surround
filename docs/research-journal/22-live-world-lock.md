# 22 — Live world-lock: tracker → render, click-free, a source that stays put (B3.1)

**Date:** 2026-10-17  ·  **Milestone:** M3 (the core effect)  ·  **Status:** core + host app done; host listening confirmation pending

## Question

Everything upstream is in place — the tracker (B2.1), the camera→world calibration
(B2.2), the world layout (B1.4), and the decision that the smooth-rotation renderer is
**nearest real BRIR + a render-layer crossfade**, not spectral synthesis (journal 18).
B3.1 is the join: per frame turn the head pose into head-relative speaker directions
`Rᵀ(pᵢ−p_h)` and move the convolution **without clicks** as the nearest BRIR changes.

The owner's framing simplified the test target: instead of first building the Windows
virtual-device plumbing, **loop the caisse-claire sample as a single world-fixed source**
and drive it live from the camera — hear the snare stay locked in the room as the head
turns. The VoiceMeeter/system-audio path is then a separate, later concern (M4).

## Method

Split the usual way — a tested core and a thin host shell.

- **Core — `binaural_headtracker.live_render`** (pure numpy, unit-tested). A single
  world-fixed mono source is steered head-relative through the **nearest** of the owner's
  directional baked BRIRs (the 7 placed 7.1 channels; LFE is non-directional and excluded
  from selection). Each BRIR is a persistent `StereoConvolver` **voice**. Per block we pick
  the nearest BRIR to the current head-relative azimuth; when it changes we ramp the
  source's **input gain** from the old voice to the new over `fade_ms` (default 20 ms). The
  retired voice keeps convolving the trailing zeros, so its room tail rings out and is
  reaped once drained — never a hard filter swap. The first voice starts at full gain
  (a fade-in from silence would needlessly make startup differ from a plain convolution).
  `render_world_locked(mono, az_track, brir)` is the offline driver that renders a whole
  signal under a per-block azimuth track → stereo, for reproducible A/B.
- **World-lock geometry stays in the tested layer.** The renderer never sees a pose; the
  caller turns a pose into the source's head-relative azimuth with a one-"speaker"
  `source_layout(world_az)` fed to `calibrate.directions_for_pose` — i.e. the exact
  `Rᵀ(p_src−p_head)` speaker path, not a second copy of the math.
- **Host shell — `apps/live_tracker_audio.py`** (OpenCV + MediaPipe + `sounddevice`,
  host-only). The audio callback pulls from the looping snare and calls the renderer with
  the latest azimuth; the camera loop updates that azimuth from the tracked pose; **C**
  recenters, **Q** quits; `--record` writes the heard stereo `.wav` plus the `.npz`
  azimuth track that re-renders the run offline. Plays straight to the headphones — no
  virtual device.

## Result

Core done, **263 tests pass (16 new), ruff clean.** The 16 pin: LFE-excluded nearest
selection with 360° wrap; a source held at a ring azimuth renders **exactly** a plain
convolution with that BRIR (1e-9); after a sweep the output reaches the new BRIR's exact
steady state (past the fade **and** one IR length, once the retired voice has drained);
the pose→azimuth wiring gives the world-lock sign (+30° yaw ⇒ source −30° ⇒ nearest R).

Measured, not just asserted:
- **Click-free.** On a 440 Hz tone swung across the L/SL boundary, the worst
  sample-to-sample step at the swap is 0.1126 for a hard switch and **0.0294 with any fade
  ≥5 ms** — and 0.0294 is the tone's *own* max slope (0.5·2π·440/48000 ≈ 0.0288/sample).
  So the crossfade removes the switching discontinuity down to the signal itself (~3.8×),
  and it has already plateaued by 5 ms.
- **Cheap.** Over a continuous front→rear sweep across all 7 ring points, live voices are
  min 1 / max 2 / mean 1.34 — steady state is a single convolver, a transition briefly
  overlaps two, never more.

## Interpretation

The "moving snare" instability was the spectral interpolation flipping front/back
(journal 18); `nearest` avoids that but needs a smooth hand-off between discrete BRIRs.
Crossfading the **input gain into persistent voices** (rather than swapping the filter) is
that hand-off done right: each voice is an uninterrupted convolution with a correctly
decaying tail, so there is no click and no tail jump, and the cost stays at one or two
convolvers. This is the whole M3 mechanic in a form that already runs live.

## Decision / next

- **Host listening (owner).** Run `apps/live_tracker_audio.py` with the snare: confirm the
  snare stays world-locked when turning, recenter with **C**, and confirm the three camera
  axis **signs** by eye (turn left ⇒ source swings right), flipping a `--*-sign` if needed.
  `--record` captures a run for a reproducible A/B.
- **B3.2** — the head-locked-vs-world-locked A/B capture for review, straight from the
  `--record` output.
- **M4** — route a real Windows system stream in through VoiceMeeter so *any* audio (not
  just the test loop) is world-locked; the virtual-device spike (journals 02–04) is the
  hook.
- **B1.11** (issue #22) — the rear front/back **data** problem is still the real frontier
  once the live effect is confirmed. (→ L45)

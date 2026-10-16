# 20 — Calibration: camera (yaw,pitch,roll) → world head rotation (B2.2)

**Date:** 2026-10-17  ·  **Milestone:** M2  ·  **Status:** done

## Question

B1.4 built the world-lock projection `d_i = Rᵀ(p_i − p_h)` but deliberately left the
**Euler→R sign/axis mapping and the neutral pose** to calibration. B2.1 produces a
smoothed `(yaw, pitch, roll)` stream. B2.2 is the missing glue: turn that stream into
the **world←head rotation `R`** that `WorldLayout.head_relative_directions` consumes,
anchored on a neutral "looking at screen centre" pose, so the scene world-locks.

## Method

`binaural_headtracker.calibrate`:

- **Neutral pose** — `calibrate_neutral(poses)` averages a few steady "looking at
  centre" samples to `(yaw0, pitch0, roll0)`; every later pose is taken *relative* to
  it (so `R = I` at neutral and the layout sits at its nominal angles).
- **Axis mapping (camera → world frame `+x` front / `+y` left / `+z` up):** yaw →
  rotation about world up (`+z`), pitch → about world left (`+y`, elevation), roll →
  about world front (`+x`), composed `R = Rz(Δyaw)·Ry(Δpitch)·Rx(Δroll)` on the
  per-axis deltas from neutral. This is the relabelling from the camera-frame Euler
  (`head_pose`, yaw about the camera's Y) to the world frame.
- **Sign per axis** (`yaw_sign`/`pitch_sign`/`roll_sign`, default +1) — which way the
  camera calls "+yaw" is hardware-dependent; exposed to be confirmed once on the host
  against the live camera (as B0.4 characterised the range), since it can't be
  verified headless. Defaults give the correct world-lock *direction* assuming
  +yaw = turning left, +pitch = nodding up.

## Result

B2.2 **done**. 18 tests (242 total, ruff clean) pinning the world-lock contract
through a calibrated pose:

- rotation helpers orthonormal (det 1);
- `calibrate_neutral` averages noisy neutral samples to the true centre;
- **at the neutral pose `R = I` and the layout = its nominal 7.1 angles**;
- **a head yaw of +30° shifts every speaker azimuth by −30°** (elevation unchanged) —
  the exact world-lock mechanic B1.4 unit-tested, now driven by a calibrated head pose;
- a pitch Δ tilts the front-centre speaker in elevation (azimuth unchanged on the
  median plane);
- a flipped `yaw_sign` reverses the azimuth shift.

## Decision / next

B2.2 done — the camera→world seam is closed and unit-verified. **M2 is complete**
(B2.1 tracker + B2.2 calibration). Everything B3.1 needs is now in place:

    tracker.latest() → calibrate.head_rotation(cal, pose)
        → layout.head_relative_directions(R)     # per-channel (az, el), world-locked
        → nearest real BRIR per channel (B1.12)  # no flips
        → click-free crossfade render (entry 18) # smooth rotation

Only one thing remains host-side: confirm the three axis **signs** against the live
camera (one short host run), since the default ±1 are chosen but not camera-verified.
Next block: **B3.1** — wire the above into a live, world-locked render.
(→ L43)

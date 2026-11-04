# 89 — Full-range head tracking, Phase-0 0c: the ±90° yaw cap is real, and a full-circle azimuth fixes it

**Date:** 2026-11-04  ·  **Milestone:** full-range head tracking (RFC), Phase 0  ·  **Status:** done (spike)

## Question

The full-range head-tracking RFC (`docs/rfc/rfc-full-range-head-tracking.md`) claims, as its
load-bearing first finding, that the current pose pipeline **cannot represent a yaw past ±90°** —
not because the model loses the face, but because the *representation itself* caps it. Slice **C0**
depends on this being true and on a full-circle replacement existing. Before building any ground-
truth rig or model bake-off, the RFC's Phase-0 **0c** says to make this executable: *write the
end-to-end test first; it should fail against the current decomposition.* This is that spike —
pure rotation algebra, no camera, no model.

## Method

`rust-vision-client/src/solve.rs`, new `#[cfg(test)] mod full_circle_spike` plus one new function
`rotation_to_azimuth`. The tests build rotation matrices in the engine's own convention
`R = Rz(roll)·Ry(yaw)·Rx(pitch)` (the one `rotation_to_euler` documents) and feed known
yaw/pitch/roll through both the existing `rotation_to_euler` and the new `rotation_to_azimuth`.

`rotation_to_azimuth(R)` returns the azimuth of the head's **forward axis** (canonical `+Z`)
projected onto the horizontal plane: `atan2((R·ẑ)_x, (R·ẑ)_z)` — the third column of `R`. Unlike the
Euler yaw (the *middle* axis of `Rz·Ry·Rx`, so `cy = |cos(yaw)| ≥ 0` forces `yaw ∈ [−90°, +90°]`),
this never saturates.

## Result — 5 tests, all green (`cargo test --lib`, 15/15 in the crate; fmt clean)

- **`euler_yaw_caps_at_90_degrees`** — the problem, executable. A *pure* yaw of 120° decodes to
  **60°**, 150° → **30°**, 170° → **10°** under `rotation_to_euler`. The RFC claim is now a test,
  not a reading: the fold is real and sits exactly at the operating-range edge.
- **`azimuth_is_full_circle_for_pure_yaw`** — `rotation_to_azimuth` returns the true yaw across
  −170°…+180° to 1e-6°.
- **`azimuth_equals_euler_yaw_when_roll_is_zero`** — the new function is a **safe drop-in** for the
  front cone: it matches `rotation_to_euler`'s yaw to 1e-9° for every yaw∈[−85,85] **and every
  pitch∈[−30,30]** (roll = 0). The `cos(pitch)` factor cancels inside the `atan2`, so pitch does
  not perturb it at all.
- **`azimuth_is_monotonic_through_the_rear`** — a continuous 0°→180° sweep stays strictly
  increasing and accumulates exactly 180°; `rotation_to_euler` cannot (it folds back at 90°).
- **`azimuth_roll_pitch_coupling_is_bounded_and_the_only_divergence`** — honest limit: the azimuth
  diverges from the Euler yaw **only** when roll *and* pitch are both nonzero (second-order in
  roll×pitch), staying <10° even at an extreme corner (roll 20°, pitch 30°); it is identically zero
  whenever roll = 0.

## Interpretation

- **0c confirms the RFC premise and falsifies the v1 framing** (which thought the discontinuity was
  the ±180° wrap). The singularity is at **±90°**, intrinsic to the ZYX-middle-axis convention; the
  cure is a representation where yaw is not the middle axis. The forward-axis azimuth is that
  representation, and it coincides with the current yaw exactly where the current pipeline is valid,
  so adopting it regresses nothing in the front cone.
- This is the **primitive** for slice C0, not C0 itself. C0 also has to carry this through
  `PoseState`, `geometry.rs::published_yaw`/`recenter_neutral`, the audio azimuth (`speaker_az −
  yaw`), and the 1€/α-β smoothing — all made wrap-safe — and **pin the exact handedness against the
  live camera** (the spike only proves the math and in-range parity; it does not touch the live
  path, so nothing ships behaviourally yet).

## Decision / next

- Spike done; the representation risk is retired with evidence. `rotation_to_azimuth` lands behind
  no feature flag but is **not yet wired** into the live pose path, so engine behaviour is
  unchanged (no-op until C0 wires it).
- Remaining Phase-0 items need the host/hardware, so they are owner-run: **0a** (measure the
  incumbent pose latency + DirectML headroom on the Windows box), **0b** (log a real-session yaw
  histogram), **0d** (run 6DRepNet360 on a few real frames — runnable now per operator decision D3,
  personal rig), **0f** (marker-cube GT premise check once the cap/cube arrive).

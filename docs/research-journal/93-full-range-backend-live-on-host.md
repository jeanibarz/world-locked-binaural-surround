# 93 — Full-range head tracking: the 6DRepNet360 backend runs live on the host

**Date:** 2026-11-05  ·  **Milestone:** full-range head tracking (RFC), estimator slice  ·  **Status:** done — live-deploy validated; the head detector (Component 1) is the confirmed next wall

## What

The live-deploy step journal 92 named as "next": build the #263 engine for Windows, select the
full-range backend with **`WLBS_POSE_BACKEND=360`**, and run it against a real camera with audio
playing — so the pose path and the audio path are exercised together, on the operator's own head.

- Built `rust-engine` with `cargo xwin --release` from `origin/main` (`2fa2942`, PR #263) and
  deployed to the host install (`host/deploy-windows.sh`). The previously live exe was `055ed30`,
  which predates `headpose360.rs` — so the backend genuinely needed a rebuild, not just a flag.
- `sixdrepnet360.onnx` (94 MB) was already placed next to `facedet.onnx` in the engine's onnx-dir
  (`%USERPROFILE%\facemesh_extract`); the backend loads it from there.
- Added a non-destructive validation launcher `run-engine-360.bat` in the install dir (not in the
  repo): it sets `WLBS_POSE_BACKEND=360`, passes through `--yaw-sign` / `WLBS_POSE360_CROP`, and
  redirects the engine's `VISION …` stderr status stream to `traces\pose360-session.log` so the run
  can be scored from data rather than eyeballed. The default `run-engine.bat` (FaceMesh) is untouched.

## Verification (live session, host camera + audio)

Scored from the engine's `VISION` status stream (`traces\pose360-session.log`, ~18 k frames):

- **Backend loaded live:** `VISION backend: 6DRepNet360 (full-range) [WLBS_POSE_BACKEND=360]` — not a
  silent fallback to FaceMesh.
- **Full range confirmed live:** yaw reached **−170.5°** (and +82.3° the other way, the operator
  having turned further left than right). The shipped FaceMesh+Kabsch path dies at ~±50°. This
  exceeds the offline reproduction (−116°/+98°, journal 92) — the port keeps, and on a freer live
  sweep extends, the full-range capability on a real head.
- **Latency comfortable, with audio running:** `infer_ms` **mean ~6.8 ms, max ~15 ms** — inside the
  ~12–16 ms budget (G3). (It reads ~2 ms during the rear dropouts below, because no inference runs
  when the detector finds nothing.) No audio dropouts reported during the sweep.
- **Detection is the wall, exactly where predicted:** the detection **misses are essentially all at
  the deep rear** (the aggregate miss-rate is otherwise just a function of how long the head dwells
  facing forward — it fell from ~6 % to ~2 % as the session accumulated front-facing frames).
  Tracking holds through front and profile, then **BlazeFace loses the face past ~±140°**: `ok`
  freezes and `miss` climbs in ~90–120-frame bursts while the azimuth is pinned at its last value
  (froze at −141.7°, then −170.5°), re-acquiring only when the face swings back into view. A *face*
  detector cannot box the back of a head; this is the RFC's **Component 1 (head detector /
  detect-and-track)** becoming the binding constraint, now on live data, not just the offline 88 %.

## Caveats / next

- **Crop + sign need no change.** Default crop `1.4` held through front/profile; the rear dropouts
  are a detector limitation (no visible face to crop), not a crop-scale or handedness issue. Front
  sat near 0° and both hemispheres were reachable, so no `--yaw-sign` flip was needed.
- **The deep-rear freeze is the expected failure mode** the RFC specifies handling for (hold last
  sign at a confidence floor; re-establish sign only on a frontal/profile re-acquire). Here the
  azimuth simply held its last value and snapped on re-acquire — acceptable for a bare backend, and
  the motivation for the fusion/continuity slice (C1) and the head detector (C2).
- **Confidence still unthreaded.** `PoseState` carries yaw only; the rear freeze is currently
  indistinguishable downstream from a genuine hold. Threading confidence + regime fusion are later
  slices.

## Decision / next

Live validation **passes the tracker gate**: the full-range backend runs live, in budget, and tracks
far past ±90° where the incumbent dies. The evidence points to one next step, now confirmed twice
(offline journal 92, live here): **C2 — a head/profile-robust detector with detect-and-track**, so the
estimator keeps a target through the rear instead of freezing. Tracked on issue #264.

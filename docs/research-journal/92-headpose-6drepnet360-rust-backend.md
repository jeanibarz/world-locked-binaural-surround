# 92 — Full-range head tracking, estimator slice: the 6DRepNet360 backend in Rust (flag-selected)

**Date:** 2026-11-05  ·  **Milestone:** full-range head tracking (RFC), estimator slice  ·  **Status:** done (backend + parity); live deploy is next

## What

The RFC's estimator slice, first increment: a **full-range head-pose backend in the Rust engine**,
selectable at runtime, that replaces the FaceMesh+Kabsch landmark solve with **6DRepNet360**
(ResNet-50, 6-D rotation) read out as a **full-circle azimuth**.

- `rust-vision-client/src/headpose360.rs` — `Pose360Pipeline`: BlazeFace detect (reused) → an
  axis-aligned loose square crop → ImageNet-normalised **224² NCHW** tensor → `sixdrepnet360.onnx`
  (`ort`, DirectML like the FaceMesh path) → a 3×3 rotation → **`atan2(R02, R22)`** full-circle
  azimuth (`solve::rotation_to_azimuth`, journal 89). Pure parts (the 224 preprocessor, the
  matrix→azimuth) are native-unit-tested; the shared `ort` helpers were made `pub(crate)`.
- `rust-engine/src/vision.rs` — a `PoseBackend` enum (`Mesh` | `Full`) selected by
  **`WLBS_POSE_BACKEND=360`**, exposing one `estimate` → `CommonEstimate` so the capture loop is
  backend-agnostic. Default (unset) = the shipped FaceMesh path, unchanged.
- `pose360_dump` bin — runs the backend over an image directory, for the parity check below.

## Verification

- **Native unit tests** (5): 224-NCHW shape/normalisation, channel-first RGB order, matrix→azimuth
  (identity → 0°, a pure +150° yaw → **+150° not folded to +30°**), bad-length guard. vision-client
  23/23 pass; `cargo fmt` clean.
- **Windows build type-checks**: `cargo xwin check --target x86_64-pc-windows-msvc` green (the
  `#[cfg(windows)]` `vision.rs` + the new backend compile for the real target).
- **Rust↔Python parity** on the operator's 338-frame sweep (journal 91): the Rust backend reproduces
  the Python 6DRepNet360 trajectory to **median 1.6° / mean 2.9°** where both detect (mid-range
  |yaw|<60°: **1.5°**; extremes ≥60°: 6.4°, the detector/crop difference). Rust tracked to
  **−116°/+98°** (span 214°; 58 frames past ±70°) — i.e. the port keeps the full-range capability.
  Detection 88% vs Python's 93% (Rust uses the frontal-ish **BlazeFace**; Python used RetinaFace) —
  the detector is the next wall, as the RFC predicts.

## Caveats / next

- **Not yet live-deployed.** Running it in the app needs a `cargo xwin` release build, copying
  `sixdrepnet360.onnx` next to `facedet.onnx` in the engine's onnx-dir, `set WLBS_POSE_BACKEND=360`,
  and a live camera session — which also exercises the audio path. That is the next step and needs
  the operator in front of the camera.
- **Crop scale** (`DEFAULT_CROP_SCALE`, overridable live via `WLBS_POSE360_CROP`) is pinned against
  BlazeFace boxes offline; expect a small live tune. **Handedness** (azimuth sign vs the engine's
  +left convention) must be confirmed live and, if needed, corrected by the existing `invert`/flip
  controls — no code change.
- **Detector** is still BlazeFace (frontal) → range is detector-bound (~the 88% above). Swapping in a
  head/profile-robust detector is the follow-up that pushes toward the true rear.
- `PoseState` still carries yaw only; threading **confidence** + the regime fusion are later slices.

## Decision / next

Backend landed and parity-verified offline. Next: the **live deploy + camera validation** (handedness,
crop tune, does the stage stay locked past ±50° by ear/eye), then the **head detector** swap, then
`PoseState` confidence + fusion.

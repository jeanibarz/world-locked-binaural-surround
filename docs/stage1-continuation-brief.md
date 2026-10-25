# Stage-1 continuation brief — zero-Python Rust engine + HRTF studio

**Purpose.** Durable handoff for a fresh task to carry the engine port without rediscovery. Written
at the clean checkpoint: **R-S1 formally closed PASS**, the `vision-client` solve ported. Read the
RFC first: [`docs/rfc/rfc-standalone-distributable-and-rust-engine.md`](rfc/rfc-standalone-distributable-and-rust-engine.md).

## North star (non-negotiable, operator)
**The delivered product ships zero Python** — a single standalone native Rust binary: no `python`,
no `mediapipe`, no OpenCV, no interpreter. The ML nets become static ONNX assets the engine loads
(like the filter packs). Track D (bundled-Python hybrid) is a **de-risking intermediate**, not the
destination. Escalate only if a gate genuinely can't be met by any ownable component.

## Status

### Merged (feat/cockpit-ui-redesign is the integration branch; all PRs target it)
| PR | What | Gate (measured on host) |
|----|------|----|
| #125 | RFC (approved) | 5-critic panel + consensus attack |
| #126 | **M-D1** `filterpack` v1 + shared Rust/Python loader | byte-identical digest, diff=0 |
| #127 | **R0-s1** render-core: steering + crossfade in Rust | bit-exact vs live_render |
| #128 | **M-D2** SOFA/BRIR→pack baker (Python) | round-trips through loader (int16-exact) |
| #129 | **R0-s2** render-core: BusMixer | bit-exact (incl. einsum sum order) |
| #130 | docs: NFR-4b pose-perf budget + R-S1 dual-gate | — |
| #131 | **vision-client** wire protocol (versioned NDJSON) | 4/4 conformance |
| #132 | **vision-client** Rust Kabsch head-pose solve | bit-identical to Python (2.8e-14°) |

**Open, left for the operator to fold:** #122 (NSIS installer), #123 (engine HIGH priority), #124
(FEATURES.md). Do not merge these from the Rust thread.

### R-S1 — FORMALLY CLOSED **PASS** (the make-or-break gate)
Pose model = **candidate (3): MediaPipe's own FaceMesh landmarks → Kabsch solve** (not 6DRepNet —
it was accurate but ~32× too heavy). All four facets measured:
- **Accuracy vs GT** (AFLW2000-3D, ±45/30/30): candidate (3) GT-RMS **≤ MediaPipe + 0.65°/axis**
  (yaw 4.45 vs 4.56, pitch 5.77 vs 5.67, roll 4.44 vs 3.79). Bar was ±2°/axis. PASS.
- **Perf (NFR-4b):** ≤ MediaPipe by construction — MediaPipe's own nets (12.5 core-ms baseline;
  MobileNetV3-small-class) + a microsecond Kabsch. 6DRepNet 400 core-ms = OUT.
- **Export fidelity:** both TFLite nets → ONNX, max|err| ≤ 3.8e-4 (TFLite vs ONNX on identical input).
- **Rust solve:** Umeyama/Kabsch ported, bit-identical to the Python reference (2.8e-14°).

### Other spike gates
- **R-S2** (audio): Rust `wasapi` loopback robust (1.44M frames/30s, 0 timeouts), MMCSS "Pro Audio"
  from Rust works, render-clock drift +276 ppm (cushion-absorbable). Spike: `spikes/wasapi-probe/`.
- **M-D3** (hybrid size): ~540 MB — 1.7× over the 321 MB target → concrete evidence *for* zero-Python.

## The proven candidate-(3) recipe (so you don't rediscover it)
1. **Detector** `face_detector.tflite` → `facedet.onnx` (418 KB, 128² in). BlazeFace short-range:
   896 anchors × (box + 6 keypoints) + score → decode + NMS → face box + eye keypoints.
2. **ROI/align:** from the box+keypoints, MediaPipe computes a rotated 256² crop (align on the eye
   line). Reproduce this similarity transform.
3. **Landmark net** `face_landmarks_detector.tflite` → `facemesh.onnx` (4.9 MB, 256² in → 478×3
   landmarks + presence). Un-rotate/un-crop landmarks back to image coords.
4. **Solve** (DONE in Rust, `rust-vision-client/src/solve.rs`): rigid Umeyama/Kabsch aligning the
   **canonical face model** (468 metric verts, MediaPipe Apache-2.0, **flip Y and Z: OpenGL→OpenCV**)
   to the detected landmark xyz (z scaled by image width), on a **20-point rigid subset** (indices in
   `spikes/r_s1_landmark_pnp.py` `RIGID_IDX`), then `rotation_to_euler` (engine's Rz·Ry·Rx). Kabsch
   on landmark xyz matches MediaPipe without SQPNP (one SVD).
- **ONNX assets** currently generated on the host at `C:\Users\ibarz\facemesh_extract\{facemesh,facedet}.onnx`
  (via `tf2onnx --tflite`, TensorFlow installed for conversion only — **not** a runtime dep). These
  become bundled engine assets. Canonical model: `C:\Users\ibarz\canonical_face_model.obj`.
- Spikes (on branch `spike/r-s1-onnx-pose`): `r_s1_pose_parity.py` (6DRepNet + harness),
  `r_s1_landmark_pnp.py` (candidate-3 SQPNP spike), `r_s1_gt_gate.py` (formal GT gate).

## Remaining engine port (the work for the fresh task)
Add each crate's body **as its code lands** (YAGNI — do NOT stub empty crates):
1. **`vision-client` body** (finish it): `ort` inference of both ONNX nets in Rust; BlazeFace
   detector decode + NMS; the ROI/align transform; wire the Kabsch solve. **Guard-1** (Rust ONNX
   landmarks ≈ MediaPipe, already proven at the net level ≤3.8e-4) and **guard-2** (end-to-end Rust
   pose parity ≈ the ~0.65° GT-parity in the shipping pipeline) are the validation gates.
2. **`render-core`** (grow from R0): R0 landed `wrapped_deg`/`nearest_brir_index`/`gain_ramp`/
   `BusMixer` bit-exact. Next: the voice-pool block loop, LFE handling, the full `WorldLocked7p1` /
   `InterpWorldLocked7p1` render, feeding the existing Rust convolution kernel (`binaural_kernel`).
3. **`audio-io`:** `wasapi` loopback capture + output, the cushioned drift-FIFO (journal 81 re-prime),
   MMCSS elevation (proven in R-S2), behind an `AudioSource`/output trait so Track-B (SYSVAD) is a
   drop-in. Replaces `sounddevice`/PyAudioWPatch.
4. **`control-plane`:** `axum` serving the existing web-cockpit assets (`apps/web_cockpit/*`) +
   `/events` SSE + `/camera` MJPEG + `/api` + the crash-safe settings store. **Freeze the wire
   protocol with a conformance test** run against both the Python and Rust servers.
5. **Track S (studio):** M-D2's Python baker is the start; OQ1 = grow the core baker in the sibling
   `binaural-localization`; a Rust-native baker makes the *product* zero-Python.
6. **Track D (distributable):** M-D1/M-D2 done; M-D3 bundle + asset bundling + user pack dir
   (`%APPDATA%\WLBS\packs`) + import flow. Ships first as the hybrid de-risker.

## Standing decisions (binding)
- **Incumbent-parity bar:** MediaPipe is "good enough" (N3 forbids a perceptual redesign). A pose
  model passes if its in-range GT-RMS is ≤ MediaPipe + ~1.5–2°/axis. Being better is a bonus.
- **NFR-4b perf budget:** 60 fps-capable (≤~12–16 ms/frame OR decouple inference rate + predict via
  the engine's One-Euro/α-β) AND no heavier than MediaPipe on measured CPU%/GPU%; CPU-light FIRST,
  ONNX EPs (DirectML/NPU) are a *measured optional accelerator*, not the strategy (portability + the
  game wants the GPU).
- **YAGNI crate ordering:** add `audio-io`/`control-plane`/etc. only when their implementation lands.
- **Dataset/GT fetch = the agent's job** (browser/gdown/Kaggle); a flaky mirror is not a wall;
  escalate only if gated behind the owner's *personal* credentials. (AFLW2000-3D GT: Kaggle
  `mohamedadlyi/aflw2000-3d`, extracted at `C:\Users\ibarz\aflw_gt\AFLW2000`.)
- **Delivery/merge:** work in a fresh git worktree off `origin/feat/cockpit-ui-redesign`; commit+PR+
  merge each slice with its measured gate; Conventional Commits + `Co-Authored-By: Claude Opus 4.8`;
  self-merge after local verification (operator's own repo); do NOT commit in the main checkout.
- **Host testing discipline:** SSH/WSL-interop + Claude-in-Chrome; run everything yourself, report
  measured numbers never "should work"; the engine runs live on the host — **protect its audio**:
  run heavy host work (installs/builds/inference) at **Idle** priority (demote only your own
  processes by command-line match, never the engine's `py.exe`); loopback-only probes never play
  into the owner's output. The web cockpit is on `:8731` (`/events` for live dropout/telemetry).

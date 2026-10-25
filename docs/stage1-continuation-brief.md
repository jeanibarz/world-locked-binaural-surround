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
1. **`vision-client` body** — **DONE** (slice 1). `ort` inference of both ONNX nets in Rust;
   BlazeFace short-range anchors + decode + weighted NMS; the MediaPipe ROI/align transform
   (DetectionsToRects eye-line rotation + RectTransformation 1.5×/square_long → 256² rotated crop);
   FaceMesh landmark un-projection; wired to the Kabsch solve. Behind an `inference` feature so the
   protocol/solve crate stays CI-light. **Both guards measured on the host over AFLW2000:**
   - **Guard-1 (landmark parity):** Rust pipeline 478 landmarks vs MediaPipe's own — **median 0.92 px,
     mean 1.22 px, p95 1.46 px** (27,246 landmark pairs, 57 imgs). Closes net-level ≤3.8e-4 + ROI.
   - **Guard-2 (GT pose parity, operating range ±45/30/30):** Rust GT-RMS **yaw 4.19° / pitch 5.72° /
     roll 3.99°** vs MediaPipe **4.08 / 5.61 / 3.42** → worst delta **+0.57°/axis** (bar ±2°). **PASS.**
     Direct Rust↔MediaPipe parity 1.51° combined. The zero-Python pose source is proven end-to-end.
2. **`render-core`** — **DONE** (slice 2, the full per-block render in pure Rust). R0 landed
   `wrapped_deg`/`nearest_brir_index`/`gain_ramp`/`BusMixer` bit-exact. This slice added:
   - **`binaural_conv`** (new pure-Rust `rlib`): the batched partitioned-convolution compute extracted
     from `binaural_kernel` with a slice API (no PyO3), so the convolver composes **with no
     interpreter in the loop**. `binaural_kernel` is now a thin PyO3 shim over it — wheel math
     unchanged, the kernel parity test still passes (7/7, `<1e-9`).
   - **`render_core::GridRender`** (+ PyO3 `RenderGrid`): the full render — targets (nearest grid bus
     per directional channel) → crossfade `BusMixer` → native grid convolution → LFE (`brir`/`flat`) →
     stereo, in one Rust call. The Rust twin of `InterpWorldLocked7p1.process_block` (the daily-driver
     `--method timealign` path). `BusMixer` now delegates to the shared `BusMixerState` (one impl).
   - Also fixed the broken `maturin develop -m rust-render-core/Cargo.toml` (added the missing
     per-crate `pyproject.toml`, an oversight from #127 — the kernel had one).

   **Gate — bit-exact parity + measured perf (WSL, maturin wheels):** `RenderGrid` matches Python
   `InterpWorldLocked7p1` (`use_rust=True`) over a crossfade sweep to **`<1e-9`** across
   method∈{nearest,timealign} × lfe∈{brir,flat} × block∈{128,256} (8 cases); 23/23 Rust parity tests
   green. **Perf (36-bus/24000-tap grid, block 256 @ 48 kHz, budget 5.33 ms):** all-Rust **3.58
   ms/block (1.5× realtime)** vs Python-orchestrated **6.13 ms/block (0.9× — sub-realtime)** → a
   **1.7× speedup that crosses into realtime** (removes the numpy BusMixer + marshaling + GIL glue).
   Next (follow-up): wire `RenderGrid`/`binaural_conv` into the live engine + a pure-Rust binary
   (no PyO3 shim) as `audio-io`/`control-plane` land.
3. **`audio-io`** — **DONE** (slice 3). Native `audio_io` crate replacing `sounddevice`/PyAudioWPatch:
   - **Traits** `AudioSource`/`AudioOutput` (device-agnostic, so a Track-B SYSVAD capture drops in).
   - **`CushionFifo`** — pure, cross-platform: the cushioned output drift-FIFO with the journal-81
     maintained-setpoint **re-prime** (prefill `cushion`; `max_backlog = 2·cushion` drops oldest on
     drift; on underrun hold silent until rebuilt to `cushion`, then resume). 5 unit tests cover
     prefill / steady-state / backlog-cap / underrun→reprime (WSL/CI, no audio).
   - **WASAPI backend** (`cfg(windows)`): `WasapiLoopback` capture + `WasapiRender` event-driven
     shared-mode output + MMCSS "Pro Audio" elevation. Cross-compiled from WSL via `cargo-xwin`.
   - **Gate — measured on host (loopback-only, Idle, 30 s)** via the `audio_probe` binary: capture
     **1,440,960 frames/30 s** (== R-S2's 1.44M), **0 event-timeouts**, 0 silent, drift **+306 ppm**
     (R-S2 +276; cushion-absorbable), **MMCSS Pro Audio = true from Rust**. The live-driven FIFO:
     **0 underruns / 0 re-primes / 0 overflow-drops**, mean fill 6.49 blocks (above the 4-block
     cushion) — stable. (`WasapiRender` compiles + is behind the trait; live output validation is
     deferred to when the engine drives it, to keep this slice strictly loopback-only.)
4. **`control-plane`** — **DONE** (slice 4). The `control_plane` axum crate serving the existing
   web-cockpit assets (`apps/web_cockpit/*`) + `/events` SSE (`data: <json>\n\n`, `: ping` idle) +
   `/camera` MJPEG (`multipart/x-mixed-replace; boundary=frame`) + `GET /api/get_config` +
   `POST /api/<name>` + a **crash-safe settings store** (atomic temp+fsync+rename; corrupt→defaults,
   2 unit tests). The engine drives it via a `Cockpit` handle (`push_state`/`set_frame`/`set_config`,
   control events in `take_dispatched`); byte-exact framing to the Python server (`_send` headers +
   `Cache-Control: no-store`). **Gate — wire-protocol conformance run against BOTH servers**
   (`tests/test_control_plane_conformance.py`: starts the Python `WebCockpit` in-process + the Rust
   `cockpit_server` subprocess on identical fixtures): **9/9 pass** — `get_config` JSON equal, static
   (4 assets) status/Content-Type/body equal, 404 equal, `POST /api` 200 `{}` equal, SSE `data:`
   framing + JSON equal, MJPEG multipart framing equal. The wire protocol is frozen. clippy clean.
   (Serves stubbed state until the Rust engine is wired in to produce real `Cockpit` state.)
**Engine integration — Stage A DONE** (the four crates → one running zero-Python binary). New
`wlbs_engine` binary + a pure-Rust `render_core_lib` (the render logic extracted from the PyO3
`render_core`, now its shim — parity preserved 16/16) + `control_plane::serve_blocking`. It loads the
baked 7.1 BRIRs (own hound reader), loopback-captures a source, world-lock renders on the native
kernel (pose-steered, nearest-over-ring + LFE), outputs to a spare endpoint, and serves the cockpit.
**Verified end-to-end on the host** (loopback source, spare Bigscreen output, Idle): live render
**7501 blocks / 40 s, 0 underruns**; cockpit `GET /` 200; **live SSE pose yaw 39.9°→33.7°**,
`engine:"rust"`; and **`wlbs_engine.exe` has no child processes / no `py.exe`** — zero interpreter.
Stage B (next): the `vision-client` camera→pose wire for live head tracking (Stage A uses a demo
yaw rotator; the owner's camera is held by their running Python engine, so Stage B feeds a face
image / MJPEG to prove the vision wire without a device conflict).

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

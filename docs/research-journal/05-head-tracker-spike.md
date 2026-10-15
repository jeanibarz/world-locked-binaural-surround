# 05 — Head-tracker spike: MediaPipe → yaw/pitch/roll + latency (B0.4)

**Date:** 2026-10-16  ·  **Milestone:** M0  ·  **Status:** done

## Question

Can we get a head **yaw/pitch/roll** estimate from a webcam frame on this box
(via WSL interop), and what is the **inference latency** — does it fit the
<~50 ms head-tracking budget (requirements § 2 / NFR-1)?

## Context

M2 will build the real tracker thread; this spike only de-risks the path and the
latency. The dev box is WSL2, but the webcam lives on the Windows host and is
reachable through interop exactly like the audio stack (journal 02/05 pattern:
`py.exe -3.12`). MediaPipe's **FaceLandmarker** (Tasks API) can emit a 4x4
**facial-transformation matrix** per face, whose rotation block is the head
orientation — so no manual solvePnP/landmark-to-pose step is needed.

## Hypothesis

FaceLandmarker produces a usable per-frame head transform; converting its
rotation to Euler angles yields a yaw/pitch/roll that moves coherently with the
head; and CPU inference is well under 50 ms.

## Method

- Portable, hardware-free helpers in `src/binaural_headtracker/head_pose.py`:
  `rotation_from_matrix` (scale-normalise the 3x3 block), `rotation_matrix_to_euler`
  (singularity-guarded `R = Rz(roll)·Ry(yaw)·Rx(pitch)` decomposition),
  `head_pose_from_matrix`, and `summarize_latencies`. Unit-tested on WSL
  (`tests/test_head_pose.py`, 12 tests) against independently-built rotation
  matrices and hand-computed latency stats — no camera/MediaPipe/OpenCV.
- Windows spike `spikes/b0_4_head_tracker.py`: open the webcam (DirectShow),
  **warm it up** (`--settle`, drain frames while auto-exposure converges), run
  FaceLandmarker in IMAGE mode with `output_facial_transformation_matrixes`, and
  per frame time the `detect()` call (inference) and the full grab→pose cost,
  excluding a warm-up, then summarise. `mediapipe` + `opencv-python` recorded as
  the `vision` optional-extra; model kept out of git.
- One variable per observation; full numbers below.

## Result

Live run, 200 frames, camera #0 (Logi C270) @ 640x480, 2 s settle, operator
doing a deliberate **~±90° yaw sweep** (left, hold, right, hold):

| Metric | Value |
|---|---|
| Faces detected | **116 / 200 (0.58)** |
| Inference latency (face present) | mean **11.9 ms**, median 11.8, **p95 15.0**, max 16.5 |
| Inference latency (no face) | mean ~2.8 ms (model bails early) |
| Warm-up first inference | ~20 ms |
| Grab→pose (full) | mean **66.6 ms → ~15 fps** |
| Yaw range tracked | **−52° … +57°** (span 108°), smooth and monotonic through 0 |
| Pitch / roll range | pitch −26…−18 (≈−22° camera-below offset) ; roll ±19° (tilt coupling) |
| Steady frames with **no face** | **84 / 195** — clustered at the yaw extremes |

The trace makes the dropout explicit: continuous detection on the way out to
~+52°, then a **gap (frames 64–93)** while held near the +90° extreme, detection
resumes ~+55° and tracks down through 0 to ~−48°, then a second **gap (frames
131–187)** at the −90° extreme. The misses are not random — they sit exactly
where yaw exceeds ~±50°.

An earlier, under-instrumented run logged only the first 8 detected frames
(yaw 9→24°) and wrongly looked like a tiny range; the fix was to log the full
series. Portable helpers: 34/34 pytest green on WSL; ruff + format clean.

## Interpretation

- **Path works, across a wide range.** Yaw tracks a full −52°→+57° swing smoothly
  and monotonically — FR-4's camera→orientation link is proven, not just near
  centre. The steady pitch ≈ −22° is a fixed offset from the camera sitting
  *below* the face (it looks up); that is exactly what neutral-pose calibration
  (B2.2) will zero out, not an error.
- **The reliable range is ~±50° yaw; the model loses the face beyond it.** The
  detection gaps sit precisely at the held ±90° extremes — a known property of a
  near-frontal face-mesh model. This directly set the **product operating range:
  ±45° yaw / ±45° pitch** (owner's decision, 2026-10-16), which sits safely inside
  the reliable zone with margin; it is now in requirements § 3 (FR-4). Beyond ±45°
  is graceful-loss territory (NFR-5 / B4.2) or a wider-range/multi-camera option
  (FR-13).
- **Inference is a non-issue:** ~12 ms with a face (p95 14.6), ~3 ms idle — tiny
  against the <50 ms budget. **The bottleneck is the camera**, not the model: the
  C270 at 640×480 yields ~15 fps (~66 ms between pose updates), which alone eats
  most of the tracking-latency ceiling. Raising the *update rate* (MJPG fourcc,
  lower resolution, or a faster camera) matters far more than optimising
  inference → follow-up **B0.7**.
- **Two real gotchas the live run exposed** (why the spike runs on hardware):
  1. **Virtual cameras masquerade as the webcam.** Index #0 = the C270, but
     index #2 is the **OBS Virtual Camera** (its logo, no face). The only reliable
     way to find the real camera was to *save a frame and look*; indices and
     brightness alone misled. Resolve the camera by inspection, not by index.
  2. **The sensor needs warm-up.** The first run read 0 faces because the C270
     returned dark frames for ~1–2 s after opening (and the room was unlit).
     Adding a timed `--settle` drain (and light) fixed it. Detection is also
     sensitive to framing/exposure — hence 69 %, not 100 %.
- The 58 % overall detection rate is dragged down by the *deliberate* out-of-range
  extremes (the two gaps); **within ±45° detection was continuous.** The M2 tracker
  can still lift robustness with VIDEO mode (temporal tracking), steadier framing,
  and a confidence/one-euro filter, and must handle re-acquisition when the head
  leaves the operating range (B2.1 / B4.2).

## Dependency licences (§ 9 compliance — all permissive)

| Package | Licence |
|---|---|
| mediapipe | Apache-2.0 |
| opencv-python (OpenCV) | Apache-2.0 |
| FaceLandmarker model (`.task`) | Google MediaPipe model, redistribution per Google's model terms — **fetched by the user, not bundled** (kept out of git) |

No GPL. The model is downloaded at setup, not committed.

## Decision / next

B0.4 done: webcam → yaw/pitch/roll proven, inference latency measured (~12 ms,
well within budget). The camera update rate (15 fps) is the real constraint →
new follow-up **B0.7**. M0 spikes are now complete (B0.1–B0.4 + B0.6; B0.5 and
B0.7 are latency follow-ups). Next eligible block: **B1.1** (HRIR/SOFA loader) —
the start of the static render engine (M1), and pure Python/numpy, so no host
interop needed.

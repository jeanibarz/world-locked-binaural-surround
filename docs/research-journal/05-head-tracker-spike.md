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

Live run, 150 frames, camera #0 (Logi C270) @ 640x480, 2 s settle, operator
slowly turning:

| Metric | Value |
|---|---|
| Faces detected | **103 / 150 (0.69)** |
| Inference latency (face present) | mean **11.9 ms**, median 11.9, **p95 14.6**, max 16.2 |
| Inference latency (no face) | mean ~2.8 ms (model bails early) |
| Warm-up first inference | 20.4 ms |
| Grab→pose (full) | mean **66.6 ms → ~15 fps** |
| Sample yaw (deg) | 9.4 → 12.3 → 14.2 → 16.4 → 17.8 → 20.2 → 22.0 → 23.9 |
| Sample pitch / roll (deg) | pitch ≈ −25 to −29 (steady) ; roll −2.7 → −8.5 |

Portable helpers: 34/34 pytest green on WSL; ruff + format clean.

## Interpretation

- **Path works.** Yaw climbs monotonically (9°→24°) as the head turns — the pose
  tracks real motion, FR-4's camera→orientation link is proven. The steady
  pitch ≈ −26° is a fixed offset from the camera sitting *below* the face (it
  looks up); that is exactly what neutral-pose calibration (B2.2) will zero out,
  not an error.
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
- Detection rate 69 % is fine for a spike; the M2 tracker can lift it with VIDEO
  mode (temporal tracking), steadier framing, and a confidence/one-euro filter.

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

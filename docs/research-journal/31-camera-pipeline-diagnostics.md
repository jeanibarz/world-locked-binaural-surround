# 31 — Camera pipeline, measured: it's the C270 (30 fps) + low light, not USB (B6.4)

**Date:** 2026-10-18  ·  **Milestone:** M6 (GUI) / diagnostics  ·  **Status:** done; camera/detect/loop surfaced in the panel, owner's "why 30 Hz / 15 fps?" answered from measurement

## Question

The diagnostics panel (B6.3) showed tracker **30 Hz**; the owner asked what that means vs the
camera's refresh rate, where the delays are, why they'd seen **15 fps**, whether it's resolution,
the **driver** or the **USB** — and whether settings can change without a restart.

## Method — measure the pipeline on the host

Probed the real camera (OpenCV + MediaPipe) at several resolutions, then MJPG-vs-YUY2 at one
resolution, and read the device name. **A first probe was mis-designed** (it requested 30 fps for
480p/720p and 60 only for 240p, so it merely echoed the requests — a false "lower res → 60 fps").
Corrected with explicit per-mode fps requests.

## Result — the facts

- The camera is a **Logitech C270** — a budget **720p @ 30 fps** webcam with **no 60 fps mode**;
  the reported "60 fps" when requested are duplicated frames, not real captures.
- **MediaPipe FaceLandmarker detect is only ~2.5–3.3 ms** per frame and **independent of camera
  resolution** (it downsamples internally). So detection is *not* the tracker bottleneck — the
  tracker loop rate equals the camera's delivered frame rate.
- So the **30 Hz is the C270's native 30 fps**, i.e. the camera sensor/firmware — **not** USB
  (the C270 is USB-2 and 720p30 is a tiny fraction of that bandwidth) and not a driver bug.
- The **15 fps was low light**: the C270's auto-exposure lengthens exposure in dim conditions and
  halves the frame rate. The lever is **light / fixed exposure**, not resolution or the USB port.
  Going above 30 Hz needs a **faster (60 fps) camera**.

## What shipped

- `diagnostics.classify` now takes `detect_ms`, `camera_fps`, `cam_w/cam_h` and returns a
  `loop_ms` + a plain **`tracking_note`** that distinguishes: detection-bound (resolution won't
  help), a **low-light frame-rate drop** (`rate < 0.6 × camera fps` → "add light / fix exposure"),
  at the camera's hardware fps ("a faster webcam is the only way to more"), or loop overhead.
  4 new tests.
- The stats panel shows a **tracker section**: loop Hz (+ ms/frame), detect ms/frame, camera
  `WxH @ fps`, and the note. Both apps time `detect()` (EWMA) and read the camera's actual
  resolution/fps; `apps/_console.apply_camera_settings` applies optional `--cam-width/--cam-height/
  --cam-fps` (0 = leave the driver default — **default behaviour unchanged**) and reports what the
  driver negotiated. Verified by host PNG renders (30 Hz and a 15 Hz low-light case). 305 tests.

## Real-time configurability (the owner's other question)

Honest state: **audio** settings (`--block`, `--latency-ms`, `--output-eq`, `--lfe-mode`, device)
are baked into the renderer + the duplex streams at startup → a **restart** is needed to change
them. **Tracker-side**: azimuth flip is already live (**F**); camera resolution/fps are flags
(restart) but could be made live by reopening the capture; the 1€ smoothing could be live-bound to
keys. None of these are wired as live controls yet — a live-settings panel is a clean follow-up
(audio reconfig is the hard part: it means rebuilding the renderer/FIFO/streams without a gap).

## Decision / next

Measurement settled the camera question; the panel now explains it in situ. For >30 Hz tracking
the owner needs a 60 fps webcam; for the 15 fps, light. Live audio reconfiguration is the open
item if real-time settings are wanted.

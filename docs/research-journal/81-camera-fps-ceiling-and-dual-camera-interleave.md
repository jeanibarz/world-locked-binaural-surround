# 81 — Camera fps is a hardware ceiling (30 on all three); dual-camera interleave gives ~1.66x

**Context:** chasing head-tracker pose latency ("camera stale ~47 ms"). The cockpit showed ~30 fps
for every camera; the owner doubted that and asked whether a bottleneck was capping us at 30, and
whether two cameras could be fused to raise the effective rate. Measured on the host (Win10, cv2 5.0.0,
three physical webcams + two virtual cameras).

## Finding 1 — the cockpit was right: 30 fps is a true hardware ceiling, not a software bottleneck

Per each camera's own DirectShow capability list (`ffmpeg -list_options -f dshow`), the max fps in
**any** resolution or pixel format:

| physical camera | max fps (any format) | notes |
|---|---|---|
| Logi C270 HD WebCam | **30** (≤640x480, YUYV or MJPG) | 720p only 7.5 fps YUYV / 30 MJPG |
| Logitech HD Webcam C525 | **30** (every resolution) | bgr24 / yuv420p |
| "USB Camera" (generic) | **30** (640x480 / 800x600) | 720p 10 fps YUYV / 30 MJPG |

No mode above 30 fps exists on any of them. (Two more devices enumerate — `NVIDIA Broadcast` and
`OBS Virtual Camera` — both virtual, not real capture sources.)

Backend/format behaviour that explains the confusing readings:
- **MSMF** (OpenCV's default on Windows, and what the live app uses) hard-caps these cameras at 30 and
  silently ignores both the MJPG FOURCC and the 60-fps requests.
- **DirectShow** *accepts* a 60-fps request (CAP_PROP_FPS reads back 60) but the hardware still tops
  out at 30; in raw YUYV it was even bandwidth-starved to **15–24 fps**. The MJPG FOURCC never actually
  engaged through OpenCV's `set()` (fourcc stayed YUYV) — but it is moot, since the capability list
  shows MJPG is also 30-capped at usable resolutions.

**Conclusion:** lowering resolution does NOT unlock a higher fps on this hardware; 30 fps / ~33 ms is
the floor. A single-camera >30 fps path needs different hardware (e.g. C922/Brio or a global-shutter
tracking cam). Prediction (`--predict`) stays the main single-camera latency mitigation.

## Finding 2 — the three cameras CAN stream simultaneously

Pairwise coexistence test (0+1, 0+2, 1+2) at 640x480 and 320x240: every pair opened and delivered
20/20 frames. So dual-camera capture is feasible on the current USB setup (no bandwidth wall at these
resolutions). Gotcha: opening two MSMF cameras at the *same instant* fails — open them **sequentially**
(a ~0.5 s stagger) then read concurrently.

## Finding 3 — interleaving two 30-fps cameras gives a real but partial latency win (~1.66x)

`cam_phase_probe.py` on cams 0+1 @640x480/30 for 25 s:

```
camera 0: 30.0 fps   camera 1: 30.0 fps   combined 59.9 fps
interleave quality 48%  (stable over the run: 48/47/47/51/50%)
mean pose age:  single 26.0 ms  ->  combined 15.7 ms   (latency gain x1.66)
combined gaps (ms): p50 9.1 | p95 51.9 | p99 59.9 | max 71.7
```

- The **average** pose age drops 26 → 16 ms — a genuine ~1.66x improvement.
- NOT a clean 2x: USB webcams free-run with no sync, so they sit at a semi-random relative phase
  (~48% interleave here). It can be better or worse in other samples; one 25 s window only.
- **Variance is the catch:** the combined stream is *less* regular than a single camera — p95/p99 gaps
  (52–60 ms) are *worse* than a single camera's steady 33 ms at the unlucky moments. The average
  improves but the worst-case staleness occasionally spikes above single-cam, so a fusion layer must
  handle that jitter (and the per-camera pose-estimate mismatch) or it trades mean latency for wobble.

## Decision / next

- **30 fps is settled as a hardware fact.** No format/backend change beats it.
- **Fusion is the only >30-fps route with the current cameras**, and it's a measured ~1.66x average
  win — real but moderate, and complex (two capture threads + two MediaPipe detectors incl. the GIL
  concern from journals 74–76, per-camera neutral calibration, timestamp interleaving, jitter
  handling). This is RFC-worthy, not a quick patch.
- **Simpler alternative:** a single genuine 60-fps camera gives a clean, jitter-free 16.5 ms mean age
  with none of the fusion complexity — likely the better engineering trade if buying hardware is OK.
- Dual-camera's stronger standalone benefit is **coverage/robustness** (wider yaw range, occlusion
  resilience), independent of the rate bump.

Probes committed: `spikes/cam_fps_probe.py` (per-camera throughput vs claimed), `spikes/cam_phase_probe.py`
(two-camera interleave + mean-pose-age gain). Also note: camera index 2 ("USB Camera") is currently
**mounted upside-down** — MediaPipe won't track an inverted face without a software rotation.

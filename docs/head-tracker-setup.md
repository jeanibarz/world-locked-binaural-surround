# Head tracker — setup (Windows)

The tracker turns a **webcam frame** into a **head orientation** (yaw/pitch/roll)
that the renderer uses to keep the virtual speakers world-locked (FR-4). The
prototype uses **MediaPipe FaceLandmarker**, whose facial-transformation matrix
gives the head pose directly (no manual solvePnP).

Engineering evidence: [`research-journal/05-head-tracker-spike.md`](research-journal/05-head-tracker-spike.md).

## Dependencies

On the Windows interpreter that runs the spike (both Apache-2.0, permissive):

```bash
py -3.12 -m pip install mediapipe opencv-python
```

Recorded as the `vision` optional-extra in `pyproject.toml`. They are kept out of
the baseline so `uv sync` stays green on headless WSL/CI; the portable pose/latency
math in `binaural_headtracker.head_pose` needs neither and is unit-tested without
them.

## Model file (not in git)

FaceLandmarker needs a ~3.7 MB `.task` model, kept out of git (`.gitignore`):

```bash
mkdir -p spikes/models
curl -L -o spikes/models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

Override the location with `--model` or `$BHT_FACE_MODEL`.

## Running the spike

From the WSL repo, via interop:

```bash
WINPATH=$(wslpath -w spikes/b0_4_head_tracker.py)
py.exe -3.12 "$WINPATH" --frames 150 --camera 0 --settle 2.0
```

It opens the webcam, warms it up (see below), runs FaceLandmarker per frame,
prints a pose stream and a latency summary, and emits a `RESULT_JSON:` line.

## Camera notes (learned the hard way — journal 05)

- **Resolve the camera by looking, not by index.** On this host, index **#0** is
  the physical **Logi C270**; index **#2** is the **OBS Virtual Camera** (shows the
  OBS logo, no face). Indices drift and virtual cameras masquerade as real ones,
  so if you get 0 detections, save a frame and *look* at it rather than trusting
  the index:
  ```bash
  py.exe -3.12 -c "import cv2;c=cv2.VideoCapture(0,cv2.CAP_DSHOW);[c.read() for _ in range(10)];import cv2 as x;ok,f=c.read();x.imwrite(r'C:\\Users\\you\\frame.jpg',f)"
  ```
- **Give the sensor time to wake up.** A USB webcam returns dark/garbage frames
  for the first ~1–2 s while it powers on and auto-exposes; `--settle` (default
  1.5 s) drains those first. Too little light or no settle ⇒ zero detections.
- **The camera frame rate is the latency bottleneck, not the model.** The C270 at
  640×480 delivers ~15 fps (~66 ms between pose updates), while inference is
  ~12 ms. Raising the tracking update rate (MJPG fourcc, lower resolution, or a
  faster camera) matters more than optimising the model — tracked as a follow-up.

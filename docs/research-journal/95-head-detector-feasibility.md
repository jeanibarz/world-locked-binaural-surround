# 95 — Head-detector feasibility: a CrowdHuman head net closes the rear gap (C2 spike)

**Date:** 2026-11-05  ·  **Milestone:** full-range head tracking (RFC), slice C2 (detector half)  ·  **Status:** spike done — model selected and measured; wiring is next

## What

Journals 93–94 established that the full-range backend tracks past ±90° and that detect-and-track
bridges short detector dropouts, but the binding limit stands: **BlazeFace is a *face* detector and is
blind when the back of the head faces the camera.** This spike answers the gating question *before any
Rust*: **is there a head/profile-robust detector that boxes the head from behind, fast enough to use?**

Offline, in the phase-0 workspace (`dmlvenv`: onnxruntime-directml + opencv, no installs), on the
operator's real **338-frame sweep** (`sweep/`), with the RetinaFace baseline (`poses360.csv`) as the
ground-truth gap: RetinaFace detects **313/338 (93 %)** and **misses 25** frames — the deep rear.

**Model:** `Sharath33/Person` (Hugging Face) — a **YOLOv11-small** net trained on **CrowdHuman**,
exported to ONNX, dual-class (**0 = person, 1 = head**), 640² input, BGR/255. License `openrail++`
(not a gate per D3, personal rig). Export has three outputs: `boxes (1,8400,4)` as **`cx,cy,w,h` in
640-letterbox space**, `scores (1,8400,1)`, `classes (1,8400,1)` — *not* the `(1,6,8400)` its own
`inference.py` assumes (that mismatch read 0 detections until corrected).

## Verification (measured, offline)

- **Detection:** the head detector fires on **338/338 frames (100 %)** and **recovers all 25/25**
  rear frames RetinaFace missed (head-score min 0.67, mean 0.73). Box geometry confirmed `cx,cy,w,h`
  on a frontal and a rear frame (the `xyxy` reading gives inverted boxes).
- **Latency (DirectML, with the GPU shared by a concurrent LLM):** **p50 11.7 ms / p95 13.2 / p99
  14.5 ms**. **CPU: ~104 ms p50 — unusable**, so this path requires DirectML.
- The 94 MB 6DRepNet360 pose net is separate (~1.65 ms free, journal 90); ~12 ms for the detector is
  the new cost to place.

## Decision — a cost-aware 3-layer cascade (not detect-every-frame)

~12 ms is too heavy to run the head detector *every* frame on top of the pose net (G3). But it only
needs to fire where BlazeFace fails. So the design (reusing the just-merged detect-and-track):

1. **BlazeFace every frame** — cheap (418 KB net), accurate for front/profile. Unchanged.
2. **Head detector only when BlazeFace misses** — pay the ~12 ms just in the rear (~7 % of frames),
   so p50 frame cost stays near the front path and only rear frames carry the detector.
3. **`BoxTracker`** (journal 94) — bridges the rare frame where both miss.

Feed whichever box wins to the existing crop → 6DRepNet360 → full-circle azimuth.

## Next

Wire the cascade into `Pose360Pipeline` behind a flag (e.g. `WLBS_POSE360_DETECTOR=head`), with a new
640 letterbox + the 3-output YOLO decode (`cx,cy,w,h` + head-class filter + NMS) in Rust (`ort`), place
`yolov11_phd_s.onnx` next to the other assets on the host, then live-validate a full 360° and confirm
the rear `miss=` count collapses (vs journal 94's `miss=149`). Tracked on #264.

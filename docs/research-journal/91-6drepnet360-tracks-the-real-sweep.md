# 91 — Full-range head tracking, Phase-0 0d (behaviour): 6DRepNet360 tracks a real head past ±90°, and the full-circle azimuth is what reads it

**Date:** 2026-11-05  ·  **Milestone:** full-range head tracking (RFC), Phase 0  ·  **Status:** done (0d behaviour + the real-model latency); positive

## Question

0a/0d-latency (journal 90) showed the budget is workable. The open behavioural question: does an
**off-the-shelf full-range model** actually track the operator's head **past the ±50° wall** where
the current BlazeFace→FaceMesh→Kabsch pipeline drops out (journal 05) — on *this* head, *this*
camera, *this* (dim) room? And does the **full-circle azimuth** from journal 89 matter in practice?

## Method

- **Capture:** 338 frames at 1280×720, ~11 fps over 31 s (`C:\Users\ibarz\wlbs-phase0\sweep\`), the
  operator doing a slow continuous yaw sweep (left to ~profile, back, right past profile), in a
  dimly-lit room (the real C270 at index 0; index 2 is the OBS virtual camera).
- **Model:** the real **6DRepNet360 "Full-Rotation"** weights (`300W_LP+Panoptic`, OvGU cloud), a
  **ResNet-50** backbone + a 6-D rotation head, output as a **rotation matrix** via
  `compute_rotation_matrix_from_ortho6d` (the repo's own `sixdrepnet360/` code). ImageNet
  normalisation, 256→centre-crop-224.
- **Detector:** RetinaFace (`batch_face`) per frame → a **square, loosely-padded** crop (the crop
  shape matters: a tight/offset box cost ~12° on a profile frame in testing; square + 20 % pad
  recovered it).
- **Two yaw read-outs from the same predicted matrix R:** the repo's own
  `euler_yaw = atan2(−R20, √(R00²+R10²))` (the capped extraction), and the journal-89
  **`fc_yaw = atan2(R02, R22)`** (full-circle azimuth of the head's forward axis).

## Result

![6DRepNet360 on a real head sweep: the full-circle azimuth tracks past ±90° while the Euler
extraction folds at the cap.](../figures/phase0-6drepnet360-sweep.png)

- **Detection 313/338 (93 %)** — RetinaFace found the face through deep profile; the ~7 % misses
  cluster at the **extremes** (the yellow bands, ~+100° and one later).
- **Euler yaw capped at −85° / +89°** — exactly the ±90° ceiling, by construction.
- **Full-circle yaw reached −93° / +113°** (span **206°**): **115** frames beyond ±50°, **77** beyond
  ±70°, **53** beyond ±85°. The sweep lived mostly *outside* the current pipeline's ±50° range (green
  band).
- **The two read-outs diverge by up to 50°** — and only where real yaw exceeds 90°: the red (Euler)
  curve *folds back* under the blue (full-circle) at the +100° peak, live confirmation of journal 89.
- **Latency of the actual model** (ResNet-50, ONNX, measured like journal 90): **1.65 ms p50 /
  2.2 ms p99 on DirectML** (CPU ~20 ms) — *cheaper* than FaceMesh (10.9 ms) and than the RepVGG proxy
  journal 90 used (4.3 ms). The earlier 4.3 ms figure was a proxy; **this is the real winning model.**

## Interpretation

- **The RFC's core premise holds on real data.** A permissive-to-run full-range model + a
  profile-capable detector + the full-circle azimuth tracks the operator **continuously and smoothly
  through ±90° and past it**, where the shipped pipeline hard-fails at ±50°. The upgrade is real, not
  hypothetical.
- **The full-circle azimuth (journal 89) is load-bearing, not academic.** 6DRepNet360's *own* code
  reads yaw through the same ±90°-capping `atan2(−R20, ·)`; its full-range performance is only
  visible via the rotation matrix. Reading `fc_yaw = atan2(R02, R22)` off that matrix is precisely
  what turns the net's full-range capability into a usable yaw. Slice C0 is validated end-to-end.
- **Detection is the next wall, exactly as the RFC predicts.** The misses sit at the angle extremes —
  a *face* detector (even RetinaFace) thins out toward the rear. Pushing past ~±110° toward the true
  rear needs a **head** detector (RFC Component 1), not a better face detector.
- **Budget: comfortable.** The real 360 net is ~1.65 ms; replacing FaceMesh frees ~9 ms for a
  detect-rarely head detector + temporal fusion. G3 is not close to tight.

## Caveats (honest)

- **No ground truth.** `fc_yaw` is the *model's* estimate; the operator felt ~90° at the held
  extremes and the model read ~92° (left) / ~113° (right) — right ballpark, but the sign/scale error
  is **unquantified**. That is exactly what the marker-cube rig (0f, D5) will pin down.
- **Dim lighting + CPU offline inference** here; production is DirectML (the 1.65 ms above).
- **Crop/convention are approximate** (ad-hoc square pad; handedness not yet pinned to the engine) —
  fine for a qualitative range/continuity result, to be made exact in C0/M-C.
- **One subject, one session.** This is a feasibility datapoint on the operator's own rig (the
  personal-rig target, D3), not a benchmark.

## Decision / next

- **0d behaviour: PASS.** Off-the-shelf 6DRepNet360 solves the operator's actual complaint (tracking
  dies by ±45°) on the operator's own head/camera, within budget. Combined with 0a/0d-latency
  (journal 90) and 0c (journal 89), the **Phase-0 tracker gate is green**.
- Next, in order: (1) swap RetinaFace → a **head** detector to push past the ±110° detection wall
  toward the rear; (2) **slice C0** — wire `rotation_to_azimuth` + 6DRepNet360 through
  `PoseState`/`geometry` wrap-safe, pin handedness against the live camera; (3) **0f** — the
  marker-cube GT to turn "right ballpark" into measured per-regime error, and **per-user fine-tuning**
  (D3) on those labels.
- Host-side throwaway artefacts in `C:\Users\ibarz\wlbs-phase0\` (venv, weights, the 338 sweep
  frames, scripts); safe to delete, but the **sweep frames are worth keeping** for re-runs against the
  head detector and the fine-tuned model.

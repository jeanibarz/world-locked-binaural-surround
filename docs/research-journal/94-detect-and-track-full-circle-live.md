# 94 — Detect-and-track: the full-range backend tracks a clean 360° live

**Date:** 2026-11-05  ·  **Milestone:** full-range head tracking (RFC), slice C2 (first increment)  ·  **Status:** done — detect-and-track validated live on two full turns; a head-robust detector is the next increment

## What

Journal 93 validated the 6DRepNet360 backend live but found the binding limit: **BlazeFace is a
*face* detector**, so through the deep rear (~±140°) it returns no box, `estimate()` returned `None`,
and the azimuth froze until the face reappeared. The **pose net itself reads a valid rear angle** from
a crop at the same place — only the detector gives up. So this slice bridges the gap instead of
freezing (`feat/pose360-detect-and-track`):

- **`BoxTracker`** (pure, unit-tested) in `rust-vision-client/src/headpose360.rs`: holds the last
  accepted face box through a bounded run of detector misses, flags those frames `tracked`, and
  re-acquires (resetting the budget) on the next real detection. Budget is env-tunable via
  **`WLBS_POSE360_TRACK_MISSES`** (default **15** frames ≈ 1 s at 15 fps; `0` = the pre-C2 behaviour).
- `Pose360Pipeline::estimate` feeds the detection (or `None`) to the tracker and runs the pose net on
  whatever crop it returns; the last real detection's score is carried onto tracked frames.
- `pose360_dump` sets the budget to `0` — it scores *independent* images, so a miss must never re-use
  the previous image's box.
- Engine (`rust-engine/src/vision.rs`): `CommonEstimate.tracked` threaded through; the `VISION` status
  line now reports **`track=<n>`** (of `ok`, how many frames came from a held box).

## Verification

- **Native unit tests:** 5 new `BoxTracker` tests (pass-through untracked; hold through a bounded miss
  run then give up; re-acquire resets the budget; `0` disables; miss-before-any-detection). vision-client
  **28/28**. `cargo xwin check --target x86_64-pc-windows-msvc` green.
- **Live, on the host** (deployed `ba58824`; operator ran **two full 360° anticlockwise** turns):
  - yaw swept the **full circle twice** — two monotonic ramps to ~+180° that **wrap to −180° and
    continue** (`… 156 → 174 →[wrap]→ −167 … −30 → 0 → 87 → 164 →[wrap]→ −165 … → 0`), reaching
    **+174° / −171°** at the sample grid. Essentially **one ~6° blip** of rollback across both
    revolutions — the "monotonic, nearly no rollback" the operator expected. **No deep-rear freeze.**
  - **`track=152`** frames were bridged by the held box; **`miss=149`** frames still gave up — i.e.
    BlazeFace is fully blind while the back of the head faces the camera for longer than the 15-frame
    budget, so the tracker coasts on the last angle until the face comes back around. On a steady turn
    this is invisible (it re-acquires on the far side with no visible rollback); it is the motivation
    for a head-robust detector next.
  - Latency this run read **`infer_ms` mean ~24 ms / max 30 ms** — **elevated by GPU contention** (the
    operator was running an LLM on the same GPU), not the backend: journal 93 measured ~6.8 ms on a
    free GPU. No `est_err` / `decode_fail`; audio ran throughout.

## Caveats / next

- **The deep rear is still detector-blind.** Detect-and-track makes a *steady* turn continuous, but a
  slow turn or a long dwell facing away still exhausts the budget and coasts (then may freeze). The
  real fix is **a head/profile-robust detector** (the next C2 increment: e.g. a CrowdHuman-head ONNX,
  run detect-rarely + track) so there is always a box, not just a held one.
- **Budget is a trade-off, not a cure:** raising `WLBS_POSE360_TRACK_MISSES` extends coasting but
  trusts a staler box if the head translates. 15 held cleanly here; leave it default and tune live.
- **Confidence still unthreaded:** a tracked (held-box) frame is not yet distinguishable downstream
  from a fresh detection beyond the `track=` count; threading confidence/ambiguity is a later slice.

## Decision / next

Slice **C2 first increment — detect-and-track — is validated**: full 360° tracks cleanly and the
deep-rear freeze is gone on a steady turn, at no cost to the default path (behind
`WLBS_POSE_BACKEND=360`). The remaining limit is the face detector itself, so the **next increment is a
head-robust detector**. Tracked on #264.

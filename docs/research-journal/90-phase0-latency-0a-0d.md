# 90 — Full-range head tracking, Phase-0 0a + 0d: the latency budget, measured on the host

**Date:** 2026-11-05  ·  **Milestone:** full-range head tracking (RFC), Phase 0  ·  **Status:** done (0a, 0d); 0b blocked on real usage

## Question

The full-range head-tracking RFC's Phase-0 gate asks two latency questions before committing to a
model programme:

- **0a** — what does the *current* pose pipeline cost on this box, and is DirectML actually engaged?
  (If the incumbent already spends the NFR-4b budget, there is no GPU headroom to add anything.)
- **0d** — does a full-range, landmark-free regressor (6DRepNet-class) fit the budget?

Measured directly on the Windows host via WSL interop. No camera needed (the nets' inference time is
content-independent, so zero-filled input tensors give the right latency), which is just as well —
the real webcam was unavailable this session (see 0b below).

## Method

- The production pipeline's two ONNX nets are on disk at `C:\Users\ibarz\facemesh_extract\`:
  `facedet.onnx` (BlazeFace short-range, input `input` `[1,128,128,3]`) and `facemesh.onnx`
  (FaceMesh, input `input_12` `[1,256,256,3]`). The engine runs them via `ort` with the
  `gpu-directml` feature (`rust-engine/Cargo.toml`: `vision_client` features
  `["inference","gpu-directml"]`; `DirectML.dll` is bundled in the deployed `WLBS-Engine/`).
- **0a**: timed each net with `onnxruntime` (120 runs, 20 warm-up, p50/p95/p99) under
  `CPUExecutionProvider` and `DmlExecutionProvider`. The system Python's `onnxruntime` 1.31 is
  CPU-only (providers: Azure, CPU), so the DirectML numbers were taken in an **isolated venv** with
  `onnxruntime-directml` — the system interpreter was not modified.
- **0d**: `6DRepNet` (pip `sixdrepnet`, the same **RepVGG** backbone + 6D-rotation head as
  6DRepNet360 — the 360 variant differs in *training data*, not architecture, so it is a faithful
  **latency** proxy), exported to ONNX (`torch.onnx.export`, 224×224×3) and timed the same way.
- Caveats: `onnxruntime-directml` (Python) is representative of, but not bit-identical to, the Rust
  `ort` DirectML path; numbers exclude first-inference GPU warm-up and assume no GPU contention; the
  full pipeline adds cheap pre/post (letterbox, crop, Kabsch — sub-ms) on top of the net times.

## Result (this host; GPU = whatever DX12 adapter the box has)

**0a — current pipeline:**

| Net | DirectML p50 | DirectML p95 | CPU p50 (all threads) | CPU p95 |
|---|---|---|---|---|
| BlazeFace (facedet, 128²) | **0.54 ms** | 0.86 ms | 3.3 ms | 6.0 ms |
| FaceMesh (facemesh, 256²) | **10.87 ms** | 12.73 ms | 20.4 ms | 31.8 ms |
| **Face-present pipeline (sum)** | **11.41 ms** | **13.59 ms** | 23.4 ms | 36.8 ms |

DirectML **is** engaged and dominant-net FaceMesh is the cost. Note the Rust engine caps ONNX
intra-op threads to 1–2 (BUG-12, so inference doesn't starve the audio thread), so its **CPU
fallback is ~100 ms** (the code comment in `vision.rs:388` — "~10-20 ms = DirectML, ~100 ms+ = CPU");
the ~23 ms CPU above used the full thread pool and is not the engine's fallback number.

**0d — full-range regressor:**

| Net | DirectML p50 | DirectML p95 | CPU p50 |
|---|---|---|---|
| 6DRepNet (RepVGG, 224²) | **4.34 ms** | 5.42 ms | 32.6 ms |

## Interpretation

- **DirectML is on, and the incumbent uses ~11.4 ms p50 / 13.6 ms p95** of the NFR-4b ~12–16 ms
  budget. Read alone, 0a says *there is almost no headroom to add anything on the GPU* — the naive
  "bolt a head detector + a full-range net onto the existing stack" would bust the budget.
- **But 0d reframes it:** a full-range regressor is **~4.3 ms — ~2.5× cheaper than FaceMesh**, because
  RepVGG at 224² is lighter than the 256² mesh. The full-range estimator is a **replacement** for
  FaceMesh, not an addition. Swapping FaceMesh (10.9 ms) → regressor (4.3 ms) **frees ~6.5 ms**,
  which comfortably absorbs a **detect-rarely head detector** (BlazeFace-class ≈ 0.5–2 ms, amortised
  further by running at 2–5 Hz) and the temporal-fusion layer, keeping the full-range pipeline **under
  the budget and lighter than today**.
- **So G3 is satisfiable** on this hardware, and the architecture consequence is sharp: the regime
  state machine must run **one net per frame** (landmark solve *or* regressor, not both every frame).
  The hybrid's front-cone FaceMesh and the rear regressor should be **mutually exclusive per frame**
  with a hysteretic handover; running both simultaneously (10.9 + 4.3 ≈ 15 ms + detector) is the one
  combination that approaches the ceiling and must be avoided except briefly at the blend boundary.

## 0b — not done (needs real usage; camera unavailable this session)

0b (log a real-session yaw histogram to decide how much the rear hemisphere is actually used) is
**blocked**: it needs the operator's genuine listening sessions, which cannot be synthesised. The
real webcam was also unavailable this session — index 0 (Logi C270) opened but delivered **no
frames** (in use / privacy shutter / driver), and index 2 is the **OBS Virtual Camera** (logo frame,
confirmed by capture), matching journal 05's index map. Recommendation: a small yaw-logging build the
operator runs during normal use; defer until the camera is free and the marker-cube rig (D5) arrives,
at which point 0b and 0f (GT premise) can be captured together.

## Reproduce

Host-side throwaway scripts live in `C:\Users\ibarz\wlbs-phase0\` (`measure_latency.py`,
`measure_6drepnet.py`) with an isolated `dmlvenv` (onnxruntime-directml + torch CPU). They are not in
the repo (host-specific paths, user interpreter); safe to delete. The nets they time are the
deployed `facemesh_extract` ONNX assets.

## Decision / next

- **Phase-0 latency gate: PASS.** DirectML engaged; full-range replacement is lighter than the
  incumbent; G3 has headroom provided the regime runs one net per frame. This retires the single
  biggest G3 risk with numbers on the real box.
- Feeds the RFC: the hybrid's "one net per frame" constraint is now a hard design rule, and the head
  detector should be detect-rarely + tracked.
- Remaining host items are operator-gated: **0b** (real yaw histogram) and **0f** (marker-cube GT
  premise) once the camera is free and the cap/cube arrive; **0d behaviour** (rear-quarter accuracy,
  not just latency) needs real turned-head frames, i.e. the recording set.

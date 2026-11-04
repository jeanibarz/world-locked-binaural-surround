# RFC: Full-range (toward 360°) head tracking — from a face-landmark solve to a landmark-free head-orientation estimator

**Status:** Draft (v3 — operator decisions folded in, 2026-11-04). Scope narrowed to the
**tracker only**; perception/BRIR work explicitly deferred. Ready to start at the Phase-0 gate.
**Date:** 2026-11-04
**Author:** Jean Ibarz (with Claude)

> **v3 note (operator decisions, 2026-11-04).** The operator resolved every open question and
> set a firm sequencing rule: **get head tracking accurate and reliable first; solve the
> psychoacoustic/BRIR localization problem later — one thing at a time.** Concretely:
> **(D1) No IMU** — the head-mounted IMU track is out of scope; this is a single-camera effort.
> **(D2) Perception deferred** — the tracker is judged on **pose accuracy vs. ground truth**,
> *not* on whether the renderer can yet use the angle; the render back-gap (journal 17) stays
> documented as a known downstream dependency but is **not** on this effort's critical path.
> **(D3) Personal rig** — local use only, so the §9 license gate is **dropped**: we can run a
> non-commercial model (e.g. 6DRepNet360) locally now, and **per-user personalization becomes
> the main accuracy play**, not a non-shippable probe. **(D4) Continuity-based rear** — single
> camera; the pure-rear ±180° ambiguity is handled by trajectory continuity, and a second
> camera (FR-13) is deferred, not committed. **(D5) Marker cap confirmed** — the operator will
> wear a printed-marker cap for ground truth (rig specified below).

> **v2 note (post-review).** v1 claimed the fix was "a new estimator behind the wire; the
> wire, world geometry, and `rotation_to_euler` stay unchanged; the only downstream edit is
> a wrap-aware filter." A 5-critic panel **falsified that framing against the code** on three
> points, each verified in-source: (1) `solve.rs::rotation_to_euler` **mathematically caps
> yaw at ±90°** (yaw is the middle gimbal axis), so the singularity is at **±90°**, not the
> ±180° wrap v1 described, and the decomposition **must change**; (2) there are **two** pose
> producers and the **live** one (`rust-engine/src/vision.rs`) calls the `Pipeline` directly,
> **bypassing the NDJSON wire** where v1 placed the front/back fusion; (3) the engine
> **consumes only `yaw`** — `confidence`, pitch and roll are not threaded into `PoseState`
> and `confidence` appears nowhere in `rust-engine`, so "graceful degradation keys on
> confidence" was describing work that does not exist. v2 retracts "minimal downstream
> change," re-scopes the pose **representation** as the first and load-bearing slice, adopts a
> **hybrid** front-landmark / rear-regressor architecture to protect front accuracy, makes the
> milestone gates **numeric**, front-loads a cheap **Phase-0** measurement gate, and promotes
> the **head-mounted IMU** from an afterthought to a recommended track because it is the only
> listed path to *literal* 360°.

---

## TL;DR

Tracking is reliable to ~±20° and soft by ±45° because the whole pipeline is
**face-landmark-based** (BlazeFace detect → FaceMesh 478 landmarks → Kabsch rigid solve):
turn away and the landmarks degrade then vanish, so the solve has nothing to stand on — no
information remains past profile, it is not merely noisier. Extending the range needs a
**landmark-free, direct head-orientation regressor** on a **head** crop, plus a **head**
detector, plus **temporal front/back disambiguation**, plus a **full-circle pose
representation** that the current code does **not** have (the Euler decomposer caps yaw at
±90°, and the engine carries yaw as a bare scalar).

This RFC proposes that as a **measurement-first, gated** programme. A cheap **Phase 0**
answers the three questions that decide whether the expensive work is even worth starting —
*does the rear hemisphere get used, is there latency headroom, and can a full-circle yaw even
travel end-to-end through this codebase* — and ships two low-risk wins immediately (a
wrap-safe pose path, and a cheap in-range jitter fix that may remove most of the felt pain).
Only if Phase 0 passes do we bake off models, and only if no permissive model clears the bar
do we train. Per the operator's decisions (below), this is a **single-camera, tracker-first**
effort: **no IMU** and **perception/BRIR work deferred**; it's a **personal rig**, so licensing
is not a constraint and **per-user personalisation is the main accuracy play**; the pure-rear
±180° ambiguity is handled by **trajectory continuity** on the one camera.

---

## Problem

### Symptom (operator report)

> Head tracking is good for ~±20° (or slightly more), but degrades very quickly approaching
> ±45°: it becomes unstable or disables entirely. I want to rotate my head up to 360° and
> still have the stage stay world-locked.

Note the report names **two** distinct failures, which have different causes and different
costs to fix:

- **±20°→±45° "unstable":** the face is still visible, but the landmarks on the turning-away
  cheek get noisier and the rigid solve (validated only to ±45°) degrades → **jitter in the
  range people actually use**. This is cheap to attack (temporal smoothing / VIDEO-mode
  tracking) and may be most of the felt pain.
- **≥±45° "disabled":** BlazeFace stops firing and/or the FaceMesh **presence gate**
  (`Identity_1`, sigmoid ≥ 0.5, added for issue #211 so a garbage yaw can't jump the
  world-lock onto a non-face) **rejects** the frame → `Ok(None)` → the tracker goes stale →
  the world-lock **freezes** at its last pose. This is the architectural wall.

### Why it happens — the pipeline runs out of signal (architectural, not tuning)

`rust-vision-client` (`inference` feature) is a faithful Rust/ONNX re-implementation of
MediaPipe's face stack: `detector.rs` (BlazeFace short-range, **near-frontal face**
detector), `pipeline.rs` (FaceMesh 478 landmarks + the presence gate), `solve.rs`
(Kabsch/Umeyama rigid alignment of a 20-point subset → yaw/pitch/roll, validated vs
MediaPipe to ~3.9°/1.4°/0.8° RMS **only within ±45°/30°/30°**). Journal 05 measured the wall
directly: a ±90° sweep tracked smoothly to ~+52°/−48°, then **detection gaps exactly at the
held extremes** — "the misses sit exactly where yaw exceeds ~±50°." That set the product
range to **±45° yaw / ±45° pitch** (FR-4), "wider range a later option (FR-13)." No threshold
change, re-tune, or faster camera moves the wall: past profile there are no face landmarks to
solve from.

### The three things the code already does that v1 got wrong (all verified in-source)

These reshape the whole design, so they lead the Problem section rather than hiding in a
footnote:

1. **The Euler decomposer caps yaw at ±90°.** `solve.rs:48-58`:
   `cy = sqrt(r00² + r10²) ≥ 0`, `yaw = atan2(-r20, cy)` → **yaw ∈ [−90°, +90°]**, with gimbal
   lock at ±90° (yaw is the *middle* axis of `Rz(roll)·Ry(yaw)·Rx(pitch)`, documented at
   `solve.rs:43`). A true 150° head turn decodes as yaw ≈ 30° with pitch/roll flipped ~180°.
   **The discontinuity to design around is ±90°, not the ±180° wrap v1 assumed.** Full range
   requires yaw to be an **outer-gimbal axis** (a different Euler order) **or** a non-Euler
   scalar **azimuth emitted directly from the rotation matrix** (or a quaternion carried
   through). `rotation_to_euler` **must change**, and that changes the compose order / meaning
   of pitch & roll downstream.
2. **The live engine bypasses the wire.** `rust-engine/src/vision.rs:22` does
   `use vision_client::pipeline::{decode_image, Pipeline}` and reads yaw **in-process**; the
   NDJSON `VisionMsg` wire is used by the MJPEG tap / R-S1 spike, **not** the product's default
   camera path. So a fusion layer placed "between the net and the wire" (v1) **does not run in
   the shipped product.** Any front/back fusion must sit **below the `Pipeline` API**, on the
   path **both** producers traverse, and `vision.rs` is a named file-to-change.
3. **The engine consumes `yaw` only.** `PoseState` (`vision.rs:32`) is `yaw: Mutex<f64>`
   (plus liveness/flip bookkeeping) — **no pitch, roll, or confidence**; `grep confidence
   rust-engine/src` is **empty**. `published_yaw` is `sign·(raw − neutral)` and the audio
   azimuth is `speaker_az − yaw` (`geometry.rs`), both **plain arithmetic that breaks at the
   wrap** (raw 170 − neutral −20 = 190). So "graceful degradation keys on confidence" is
   **new engine work**, and wrap-safety spans `published_yaw` + `recenter_neutral` + the
   azimuth, not just a filter.

### The renderer is a co-bottleneck: the BRIR back gap (the review's own blind spot)

A full-range **tracker** is necessary but **not sufficient**, and a consensus attack on the
review panel caught that the whole panel — and v1 — treated everything downstream of
`speaker_az − yaw` as a faithful consumer. It is not. The owner's directional ring is **7
fixed anchors** at `{0, ±30, ±110, ±150}` (`pack.rs::ring_indices`, `geometry.rs`), and
**journal 17** records, judge- **and ear**-verified, that the **90° back gap (≈150–210°) is
"broken for every method"** (image collapses to the front) because the owner's **measured
rear BRIRs (RL/RR) are front/back-ambiguous** — *"no blend of two front/back-ambiguous
endpoints can manufacture a correct back cue."* This is a **data** ceiling, independent of
interpolation and of tracking.

Why it bites **inside** the extended range, not just behind you: at neutral the 7 world
speakers sit on the measured anchors (zero interpolation, best fidelity); as the head turns,
**all 7 head-relative azimuths rotate rigidly by −yaw** into the sparse/broken regions. At
**yaw = 90°** (inside the "±90° solid" deliverable) speakers migrate into the 80° side gaps
and the 150–210° back gap. At **yaw = 180°** the **world CENTRE channel (dialogue)** lands at
head-relative **180°**, dead centre of the broken gap, and "collapses to the front" — i.e.
the dialogue that is physically *behind* you is rendered as *in front of* you: the stage
**un-locks**, the exact failure the feature exists to prevent. So a perfect rear yaw can feed
an **anti-world-locked** render. **Consequence:** the render **rear-cue / BRIR-coverage**
problem is a real downstream dependency for the *audio illusion* at large head angles.

**Operator decision D2 — deferred (tracker first).** The operator has chosen to make head
tracking accurate and reliable **first**, and to solve this psychoacoustic/BRIR problem
**later** ("one thing at a time"). So this section is **documented context, not a blocker**:
the tracker is judged on **pose accuracy vs. ground truth**, independent of whether the
renderer can yet express the angle. Phase-0 **0g** is **retained but demoted to an advisory
one-off** — it confirms the known back-gap and sizes the eventual rear-cue work; it does
**not** gate the tracker programme. Once the tracker is reliable, the rear-cue/BRIR upgrade
becomes the next effort.

### The fundamental limit this RFC does and does not remove

A **single screen-mounted camera cannot see the back of the head.** Facing fully away, the
pixels are hair/skull/neck — low-information and **left/right symmetric near ±180°**, so one
frame cannot tell "160° left" from "160° right." This RFC does **not** claim a monocular
frame-wise oracle at the back. The **vision track** claims the achievable thing: lock
accurately through the **front/side hemisphere (±90°)**, track **continuously into the rear
quarters** by temporal continuity (you *rotated* there through 90°/120°/150°), and **degrade
honestly** (low confidence, held sign, never a flip) where even that fails. The only paths to
**literal, drift-bounded 360° with no blind cone** are a **head-mounted IMU** or
**multi-camera** (FR-13) — both of which the operator has, for now, **ruled out / deferred**
(D1/D4). So the agreed deliverable is explicit: **front/side hemisphere (±90°) solid +
continuity-based rear on a single camera**, with literal full-circle left to a later effort.
This is a conscious operator choice ("tracker first, one thing at a time"), not a silent
retreat from the 360° ambition.

---

## Goals

- **G1 — Reliable range to at least ±90° yaw** with accuracy comparable to today's front
  accuracy, **continuous tracking into the rear quarters** with honest confidence decay, and
  **zero front/back flips** inside the covered range. (Numeric targets: *Gates*, below.)
- **G2 — No regression in the front cone (±30°).** Where the ~1° static minimum-audible-angle
  bites (requirements §2), the new path must match or beat today's accuracy and jitter. This
  is why v2 adopts a **hybrid** that keeps the validated landmark solve in front (*Design*).
- **G3 — Stay inside the performance budget**, measured as **p99 / worst-frame** over the
  **composed** pipeline (detector + regressor + fusion) **with the audio thread running**, on
  the target machine via DirectML — not a p50 of the net alone. Baseline is the **measured**
  incumbent cost (Phase 0a), not the NFR-4b number in the abstract.
- **G4 — Zero new Python in the delivered product.** Shipped estimator is a Rust-ownable ONNX
  model via `ort` (as today). Python is allowed only as **offline** training/eval tooling.
- **G5 — Licensing: relaxed (operator decision D3 — personal rig).** This is a local-only
  personal rig, so the §9 permissive-shipping gate is **dropped**: a non-commercial /
  research-only model (e.g. **6DRepNet360**) and its research-licensed training data
  (300W-LP, CMU Panoptic) **may be run locally now**. Licensing stops being a milestone
  blocker. (If the project is ever productized, the shippable-weights constraint returns and a
  permissive or own-trained model would be needed — noted, not in scope here.)
- **G6 — Measurement before model.** Phase 0 gates the programme; every model choice is made
  against numbers on real recordings.

### Non-goals

Multi-person pose; precise 6-DoF translation (orientation is the product-critical quantity);
an IMU or second camera (both ruled out/deferred — D1/D4); the render/BRIR back-gap and all
perception work (deferred — D2); productization and shippable-weight licensing (personal rig —
D3); **sub-degree *tracker* accuracy at world-rear directions** (MAA is coarse there) — **but note
this is not the same as the render back-gap**, which afflicts *world-front* content (dialogue,
front L/R) once a turned head swings it to *head-relative* rear, and *is* in scope as the 0g /
rear-cue workstream; and — **now explicitly in scope, retracted from v1** — the pose
**representation**, `published_yaw`,
`recenter_neutral`, azimuth math, `PoseState`, and `vision.rs` **do change**.

---

## Design

### Phase 0 — the cheap gate that decides whether to start (days, not weeks)

Every critic independently asked for this. Build **none** of the rig/harness/models until
these pass; two of them ship value regardless.

- **0a — Measure the incumbent.** Current end-to-end pose latency and whether DirectML is
  actually engaged on this box, with the audio thread running. *Decides* whether G3 has any
  headroom to spend before we add a detector + heavier net. If the incumbent already spends
  the budget on the GPU, "DirectML is the headroom lever" is empty and the plan changes.
- **0b — Measure real usage.** Log yaw over a few real listening sessions; histogram it. *If*
  ~all real listening sits inside ±45°, the rear-cone programme serves a fraction of a percent
  of usage and the cheap fixes (0e, and the hybrid's front half) may be the whole answer. This
  is the single cheapest way to right-size the entire effort.
- **0c — Representation spike (half a day).** Write the end-to-end test **first**: feed a
  synthetic rotation sequence +60°→+120°→+170° through `solve`/representation → `PoseState` →
  `geometry` world-lock and assert the azimuth advances monotonically. It **fails today**
  against `rotation_to_euler`. Making it pass (full-circle azimuth or quaternion, wrap-safe
  `published_yaw`/`recenter`/azimuth) is **slice C0** and ships now as a **no-op inside ±45°**.
- **0d — Model feasibility spike (~1 day).** Export one full-range model (e.g. 6DRepNet360)
  to ONNX, run it on a handful of real frames incl. profile/rear, eyeball behaviour and
  measure latency via `ort`+DirectML. *Decides* whether off-the-shelf full-range HPE is real
  and fast enough here before any rig is built.
- **0e — In-range jitter quick-fix, shipped.** Add temporal smoothing / VIDEO-mode continuity
  to the **existing** solve for the ±20–45° band and ship it. *Decides* how much of the felt
  pain is in-range jitter vs. the hard wall — and delivers a win immediately either way.
- **0f — GT-1 premise check.** One cap-with-marker clip: verify ArUco/AprilTag PnP accuracy
  and flip-rate at **60°/90°/120°** yaw and measure **cap slip**, **before** building the rig.
  *Decides* whether the ground-truth plan survives contact (see *Ground truth* for why this is
  in doubt exactly at the rear).
- **0g — Render-fidelity check (advisory, not a gate — operator decision D2).** Using the
  **existing** rotating-source harness + judge from journals 15/17, measure whether the
  world-locked stage survives a 90–180° head turn at the *render* level. Journal 17 indicates
  it **cannot** today (back gap broken, rears front/back-ambiguous). Per D2 this is **deferred
  context**: run it once (cheap, the harness exists) to size the eventual rear-cue/BRIR work,
  but it does **not** gate the tracker. The tracker's own success is pose accuracy vs. ground
  truth, measured regardless of render fidelity.

**Phase-0 gate (tracker-only, per D2):** proceed to the model programme if 0a shows latency
headroom (or a path to it), 0c/C0 lands (full-circle yaw travels end-to-end), 0d shows a viable
in-budget model (6DRepNet360 is runnable now — license no longer a blocker, D3), and 0f shows
usable ground truth. 0b (real-usage histogram) and 0g (render-fidelity) are **advisory** — they
inform scope and the *next* (perception) effort but do not block the tracker. Otherwise the
deliverable may be just C0 + 0e + the hybrid's front half, a legitimate cheaper outcome.

> **Phase-0 measured results (2026-11-05 — journals 89, 90; done on the host):**
> **0c PASS** — `rotation_to_azimuth` lands (full-circle, exact front-cone parity, monotonic through
> the rear); the ±90° Euler cap is proven in a test (journal 89, merged).
> **0a** — on the production **DirectML** path the current pipeline is **~11.4 ms p50 / 13.6 ms p95**
> (BlazeFace 0.5 + FaceMesh 10.9); CPU ~23 ms (Rust 1–2-thread fallback ~100 ms). It already uses most
> of the ~12–16 ms budget.
> **0d** — a full-range **6DRepNet / RepVGG (224²)** regressor is **~4.3 ms p50** on DirectML — **~2.5×
> cheaper than FaceMesh**. So the full-range net is a **replacement** for FaceMesh that *frees* ~6.5 ms,
> and **G3 is satisfiable** — provided the regime runs **one net per frame** (landmark solve *or*
> regressor, never both except briefly at the blend boundary). This is now a hard design rule for the
> hybrid below. **0b** deferred (needs real sessions; webcam was busy — index 0 no frames, index 2 is
> OBS virtual).

### The estimator: a hybrid, not a wholesale swap

v1 replaced the landmark stack entirely, which puts G2 (front ~1° precision) at risk for a
problem that is only acute **outside** ±45°. v2 keeps what works and adds what's missing:

```
 frame ─▶ [ HEAD DETECTOR ]─▶┬─ FRONTAL (±~50°): existing BlazeFace+FaceMesh+Kabsch solve  ─┐
          (head box, not     │                    (keeps validated ~1° front precision, G2)  │
           a face box)       └─ PROFILE/REAR:    landmark-free full-range rotation regressor ┤
                                                                                              ▼
                                            [ REGIME STATE MACHINE + SIGN CONTINUITY + CONF ]
                                            (hysteresis FRONTAL↔PROFILE↔REAR; blends the two
                                             estimators; carries unwrapped sign + confidence)
                                                                                              ▼
                              full-circle azimuth (+ pitch/roll + confidence + ambiguity)  ──▶
                              PoseState (extended) ─▶ wrap-safe geometry world-lock ─▶ render
```

- **Head detector (Component 1).** A face detector cannot box the back of a head. Prefer
  **detect-rarely + track** (run a head detector at ~2–5 Hz, track the box between detections)
  to keep per-frame cost near the net alone (G3). Needs a **re-acquisition rule** after box
  loss (fast whip), not just the blur note. A permissively-licensed lightweight head detector
  (e.g. CrowdHuman-head-trained) or the full-range net's own one-stage localisation are
  alternatives, decided on 0d/M-B numbers.
- **Full-range regressor (Component 2).** Direct, **landmark-free** rotation regression in a
  **continuous, singularity-free** representation (6D rotation / quaternion / rotation matrix),
  **not** Euler (Euler is unstable through the gimbal regions full range must visit). Decompose
  to the engine's representation **once, at the edge**, in the **new** full-circle convention.
- **Regime state machine + sign continuity (Component 3).** `FRONTAL` (landmark solve, high
  conf) ↔ `PROFILE` (blend) ↔ `REAR` (regressor, sign carried by trajectory, low conf), with
  **hysteresis** so a boundary doesn't chatter the stage. **Placed below the `Pipeline` API**
  so the in-engine `vision.rs` path and the wire path both traverse it. Tracks **unwrapped**
  yaw; in the sign-ambiguous rear, prefers the hypothesis continuous with recent angular
  velocity. **Sign-loss recovery is specified** (below), because a single multi-frame gap
  mid-turn otherwise loses the hemisphere permanently.

### Full-circle representation + wrap-safe engine (Component 4 — the load-bearing slice C0)

This is **not** "make the 1€ filter wrap-aware"; it is the pose **representation** end to end:

- Emit a **full-circle azimuth** directly from the rotation matrix (atan2 over the appropriate
  entries, continuous through ±90°) **or** carry a quaternion and derive azimuth downstream —
  replacing the ±90°-capped `rotation_to_euler` for yaw. Pitch/roll follow the new convention.
- Make **`published_yaw`**, **`recenter_neutral`**, and the **azimuth** (`speaker_az − yaw`)
  **wrap-safe** (operate on the circle: subtract then wrap to (−180°, 180°], or on unit
  vectors / SO(3)). Property-test the ±180° and ±90° seams.
- Extend **`PoseState`** to carry **confidence** (+ an **ambiguity flag / two-hypothesis**
  channel for the bimodal rear, since one scalar cannot express "θ or −θ"), and **consume** it
  in the world-lock: under low confidence / ambiguity, **hold** the last sign and **slow** or
  freeze stage sign-changes — the concrete mechanism behind "degrade honestly, never flip."
- Make the downstream **1€ filter / α-β predictor wrap-aware** (journals 19/47 state they
  assume no wrap within ±45°) as part of the same representation change.

**C0 is behaviour-identical inside ±45°, so it ships now independently** and is the one slice
that de-risks everything after it. The +60→+120→+170 test (0c) is its acceptance gate.

### Ground truth without labelled angles — honestly bounded

"I can't provide real angles." Three strategies, each with the limits the panel exposed:

- **GT-1 — marker rig, with documented dead-bands (not "true 6-DoF at all yaws").** An
  AprilTag/ArUco **cube/multi-face board** on a cap gives machine labels via `solvePnP`, but:
  planar markers at **grazing incidence near ±90°** are near-degenerate and suffer the **planar
  180° pose-flip ambiguity** — the front/back problem **reappears inside the ground truth** at
  the profile band we care most about; `solvePnP` gives the **marker** pose, needing a
  **marker→head extrinsic** calibration; and a cap **slips**, injecting a drifting offset
  right during fast sweeps. So GT-1 ships **with**: a marker→head calibration step, a
  **per-session slip check** (below), and a documented dead-band near ±90°. Validated in 0f
  before the rig is built.
  This is why GT-1 uses a **cube** (several marker faces), not a flat tag: a face is always at
  a workable angle, and because the cube's back and side faces turn **toward** the front camera
  as the head turns away, **GT-1 gives true angles directly in the rear** — the single front
  camera reads the rear-facing markers at 90–180°. The cube's **multi-face redundancy is also
  the slip check**: when two faces are visible at once, their poses must agree up to the known
  cube geometry; a disagreement flags cap slip with no extra sensor. Rig specified below
  (ArUco DICT_4X4_50, 60 mm, IDs 0–5 on the six faces).
- **GT-2 — head-mounted IMU: out of scope (operator decision D1).** An IMU reference would have
  cross-checked slip and labelled bare-head clips, but the operator has ruled IMU out. Its two
  jobs are covered without it: slip by the cube's multi-face redundancy (above), and bare-head
  evaluation by GT-3 self-consistency plus a short "same motion, cap on then cap off" paired
  clip to measure the cap-domain gap.
- **GT-3 — model-ensemble pseudo-GT, with a correlated-bias caveat.** A geodesic (rotation)
  median over several full-range models, **but** those models share the real datasets' rear
  blind spot, so their errors are **correlated** and agreement can be **confidently wrong** at
  the rear. Use GT-3 for front/profile ranking and for **label-free self-consistency**
  (continuity/monotonicity on smooth sweeps; angular-velocity integration), **not** as a rear
  ruler. The rear ruler is **GT-1's rear-facing cube markers**, full stop.

**Recording protocol (the operator's offer):** short clips, raw frames + capture timestamps,
marker cap (no IMU, per D1): slow continuous yaw sweeps CW and CCW through a full turn; **plus a "turn-and-hold
facing away" dwell** (the still-rear posture that breaks velocity-continuity); pitch nods;
roll tilts; combined look-around; at 2–3 distances and lightings; with confounders (hat
on/off, glasses, hair up/down, hand near face). **Keep cap and bare-head clips as separate
distributions** so cap-domain shift is measurable, not hidden.

### Train / personalise (M-D) — personalization is the main accuracy play (operator decision D3)

With the personal-rig decision (D3), **per-user personalisation is promoted from a
non-shippable probe to the primary accuracy strategy**, and licensing no longer constrains it.
Two levers, personalisation first:

- **Per-user personalisation (primary).** Fine-tune on the operator's **own GT-1 marker-cap
  labels** — true angles of the actual head, hair, glasses, room and camera, across the full
  yaw circle (the cube gives rear labels directly). A model specialised to one head in one
  setup can be both **smaller and more accurate** than any general one, and since nothing
  ships, this is the single highest-leverage use of the recordings. Start from a strong
  full-range base (e.g. **6DRepNet360**, runnable now) and fine-tune.
- **Synthetic full-range data (supporting).** FLAME/3DMM + hair/scalp with domain
  randomisation, to backfill **labelled rear/extreme** views the real set is thin on and to
  harden against hats/hair/light. The **synth→real gap is worst in the front cone** that G2
  cares most about — which is why the **hybrid keeps the real landmark solve in front**, so the
  regressor only has to win **outside** ±45°.

M-D still carries a **capped budget** and a **kill rule**: if after the budget the
personalised net still misses M-B's numeric gate, fall back to C0 + 0e + the hybrid front half.

### Integration & rollout — independently shippable slices (not one gated leap)

v1's "M-C" bundled five changes behind one gate that couldn't bisect a front regression. v2
ships slices, each merge-after-local-verification, each leaving the ±45° tracker's behaviour
unchanged until a slice explicitly flips it:

- **C0 — wrap-safe full-circle pose path** (representation + `published_yaw`/`recenter`/azimuth
  + `PoseState` confidence/ambiguity + wrap-aware filter). No-op inside ±45°. **Ships now.**
- **C1 — regime/fusion module** as a pure function, tested offline on the M-A recordings, not
  wired in.
- **C2 — head detector / detect-and-track**, behind a **runtime** flag (settings), single
  camera owner, **one** pipeline instantiated at a time (lazy load), legacy path untouched.
- **C3 — full-range regressor** behind the same flag, hybrid-blended with the kept landmark
  solve.
- **C4 — live A/B + promotion.** A surfaced **"active estimator"** telemetry field; neutral is
  **cleared and recaptured** on a flag flip (as `set_flips` already does). Promotion to default
  is its **own** slice, gated numerically (below) **on live, not offline, latency**.
- **C5 — retire legacy**, only after a **named soak period** (≥1 release) with the legacy path
  kept as the rollback.

### Files to change (anticipated, corrected for the two-producer reality)

- `rust-vision-client/src/`: `solve.rs` (`rotation_to_euler` → full-circle representation),
  `pipeline.rs` (hybrid dispatch + regressor net, keep ONNX/`ort`+DirectML + error-not-panic),
  `detector.rs` (head detector / detect-and-track), **new** regime/fusion module **below the
  `Pipeline` API**.
- `rust-engine/src/`: **`vision.rs`** (the live in-process producer — extend `PoseState` with
  pitch/roll/confidence/ambiguity; traverse fusion), **`geometry.rs`**
  (`published_yaw`/`recenter_neutral`/azimuth wrap-safe + consume confidence), and the
  engine's One-Euro/α-β smoothing (wrap-aware; `lib.rs` says the engine owns it — confirmed by
  grep, resolved in Phase 0, not left as an M-C "TODO").
- Offline (non-shipping Python): marker-cube GT extraction (ArUco solvePnP, multi-face), ensemble pseudo-GT,
  eval harness + **numeric** metrics, and (if reached) the M-D synthesis/training/distill
  pipeline.
- Docs: `head-tracker-setup.md`, requirements FR-4/FR-13, a journal entry per milestone. Model
  assets fetched-not-committed **with a version pin + hash** so a flag rollback can't leave a
  mismatched asset cache.

---

## Edge cases & failure modes

- **±90° gimbal (not ±180° wrap):** the real discontinuity under the current convention;
  C0/0c fix it; without C0, enabling any new estimator swings the stage **back toward front**
  as the head passes 90° — a silent geometric inversion.
- **Second producer bypasses fusion:** addressed by placing fusion below `Pipeline`; guarded
  by a test that the in-engine path produces the fused value.
- **Confidence unconsumed:** addressed by extending `PoseState` and the world-lock; without
  it, the stage locks onto a low-confidence rear guess.
- **Still dwell facing away:** turn to 160°, hold 30 s → velocity integrates to 0, the
  trajectory anchor decays, the net is sign-ambiguous. Specified handling: hold last sign at a
  **confidence floor**, do not re-estimate the sign from a single ambiguous frame, re-establish
  sign only on a frontal/profile re-acquire (no IMU fallback, per D1). In the recording set.
- **Sign lost on a multi-frame gap mid-turn (15 fps is the real bottleneck, journal 05):** a
  180° turn in ~0.5 s is ~6 frames; one dropout can lose the hemisphere **permanently** until
  frontal re-acquire, and rear magnitude can drift unbounded. Specified: bound rear-cone drift,
  define the confidence floor during held-sign rear tracking, and treat **camera fps as a
  first-class constraint** (raising it is a real mitigation, not an assumption).
- **Latency regression:** worst-frame (detector+net+fusion, larger head crop) starves audio
  even if p50 looks fine → G3 is a **p99/worst-frame** gate with the audio thread running.
- **GT ill-conditioned at ±90°:** flat markers flip near profile → the **cube** keeps a
  non-grazing face there; the rear ruler is **GT-1's rear-facing cube markers**, and slip is
  caught by multi-face agreement (D1 removed the IMU cross-check; the cube replaces it).
- **Cap-domain shift:** evaluating/training on always-cap imagery vs bare-head use → keep
  distributions separate; measure the gap with a paired "same motion, cap on then cap off" clip
  (no IMU).
- **License:** not a blocker (D3, personal rig) — 6DRepNet360 and research-licensed data may be
  used locally. (Only re-enters if the project is ever productized.)

## Milestones & numeric gates (each a go/no-go)

- **Phase 0 (gate above).** Cheap; ships C0 + 0e. **Prerequisite for everything.**
- **M-A — measurement rig & GT.** Build GT-1 (marker cube, post-0f), GT-3 self-consistency, the
  harness + metrics; capture the operator's set. *Gate:* can we score a model **numerically**
  per regime on real recordings, with the cube's rear-facing markers as the rear ruler?
- **M-B — model bake-off.** Score candidates (incl. 6DRepNet360, runnable now) on M-A. *Numeric
  gate (examples, to be fixed with the operator):* front (±30°) RMS **≤ incumbent + 1°** per
  axis; ±90° yaw RMS **≤ ~8°**; rear (90–180°) RMS **≤ ~20°** vs the cube markers; **front/back
  flip rate = 0** over N full CW+CCW sweeps; **p99 composed latency ≤ measured-headroom** (0a)
  with audio running. (License is **not** a gate — D3.) **Pass → C1–C4. Miss the accuracy gate
  → M-D (personalise/fine-tune), which D3 makes the expected primary path anyway.**
- **M-C — integration (slices C1–C4).** Hybrid + fusion + wrap path live behind the flag; A/B
  on M-A. *Gate:* G2 (no front regression, numeric) **and** G1 (measured range extension + zero
  flips) on **live** latency → promote (C4), then C5 after soak.
- **M-D — train/personalise (primary path, D3).** Per-user fine-tune on GT-1 cube labels
  (+ synthetic backfill); distil to an in-budget ONNX net; **kill rule:** if after the capped
  budget the net still misses M-B's numeric gate, fall back to C0 + 0e + hybrid-front. Return to
  M-C on success.

Spikes/proofs first: 0c (representation, half day) **before** 0d (model, 1 day) **before** any
rig — the cheapest falsifications lead.

## Alternatives considered

- **Head-mounted IMU (headphone-strapped), Smyth Realiser / Waves Nx style — considered and
  ruled out by the operator (D1).** An IMU would give camera-independent, occlusion-proof,
  full-circle orientation with no rear blind cone (the only path to *literal* 360°), and
  camera+IMU fusion would be strictly better than either alone. The operator has declined it for
  now (no head hardware). Recorded as the natural escalation if, later, the single-camera rear
  proves inadequate and literal full-circle is wanted.
- **Multiple cameras (FR-13) — deferred (D4).** A second camera genuinely sees the rear,
  removing the blind cone, at the cost of calibration and setup. Not committed now; it is the
  escalation for true rear coverage if continuity-based single-camera tracking proves
  insufficient.
- **Keep face-only; just add VIDEO-mode temporal smoothing.** Cannot pass profile — but is
  **0e**, shipped first, and may remove most of the *in-range* pain cheaply. Orthogonal, not a
  range fix.
- **Wholesale landmark→regressor swap (v1).** Rejected for G2 risk (synth→real gap worst in
  front); the **hybrid** keeps the validated front solve and asks the regressor to win only
  outside ±45°.
- **Status quo (±45° cap).** The honest baseline; rejected — the operator wants full range and
  pre-approved the effort.

## Operator decisions (resolved 2026-11-04 — formerly open questions)

All five open questions were resolved by the operator; the plan above reflects them.

1. **Product vs personal rig → D3: personal rig.** Local-only, so the §9 license gate is
   dropped: **6DRepNet360 (non-commercial) and research-licensed data may run locally now**, and
   **per-user personalisation is the primary accuracy play** (M-D), not a non-shippable probe.
2. **Head-mounted IMU → D1: out of scope.** Single-camera effort. IMU kept only as a recorded
   future escalation (Alternatives).
3. **Rear priority → D2 + D4: tracker-first, single-camera.** Make tracking accurate and
   reliable first (judged on pose accuracy vs. ground truth); the vision-only deliverable is
   **front/side hemisphere (±90°) solid + continuity-based rear**, with the pure-rear ±180°
   ambiguity handled by trajectory continuity. Literal full-circle (second camera) is deferred.
4. **Marker-cap recording → D5: yes.** The operator will wear a printed-marker **cube cap**
   (ArUco DICT_4X4_50, 60 mm, IDs 0–5 on six faces; see *Ground truth* and the rig note). This
   gives true angles across the full circle (rear markers face the camera when turned away) and
   doubles as per-user fine-tuning labels. No IMU (D1).
5. **Rear-cue / BRIR upgrade → D2: deferred.** Journal 17's back-gap is a real downstream
   dependency for the *audio illusion*, but "one thing at a time": the tracker comes first and
   is measured independently of render fidelity; the rear-cue/BRIR upgrade is the **next** effort
   once the tracker is reliable. 0g is advisory, not a gate.

### Rig note (procurement — for M-A)

The ground-truth rig is a DIY marker cube on a plain cap (total ≈ €20, no IMU): a plain
adjustable cap + printable **matte** A4 sticker sheets + a small rigid cube (≥65 mm/face) with
one ArUco marker per face. Markers generated (DICT_4X4_50, 60 mm, IDs 0–5) and print-ready.
**Print A4 at 100% ("actual size"), verify 60 mm with a ruler** (the physical side length sets
the pose scale). Still-open minor items for M-A, decidable from measurements not opinion:
the target machine's GPU/DirectML headroom (measured in 0a) and the exact numeric gate
thresholds in M-B (fixed with the operator once the harness produces baseline numbers).

---

## Critic feedback incorporated

v2 is a near-total restructure after a 5-critic panel (`failure-mode-analyst`,
`design-minimalist`, `ambition-amplifier`, `socratic-challenger`, `delivery-pragmatist`) plus
a consensus attack. Code-grounded findings adopted:

- **[failure-mode, ambition — CRITICAL, code-verified] Euler yaw cap at ±90°.** v1's
  "`rotation_to_euler` stays / wire unchanged" retracted; representation change is now the
  load-bearing slice C0, and the singularity re-located from ±180° to ±90°.
- **[failure-mode — CRITICAL, code-verified] Two producers; live path bypasses the wire.**
  Fusion moved **below `Pipeline`**; `rust-engine/src/vision.rs` named in Files to change.
- **[failure-mode — HIGH, code-verified] `confidence` unconsumed; `PoseState` is yaw-only.**
  `PoseState` extended (pitch/roll/confidence/ambiguity) and the world-lock made to consume it;
  "already keys on confidence" retracted as new work.
- **[delivery-pragmatist] M-C un-bisectable; no rollback; non-numeric gates; filter ownership
  unresolved.** Split into C0–C5 independently shippable slices; C0 ships now; numeric gates;
  runtime-flag semantics (single camera owner, lazy load, telemetry, neutral recapture); legacy
  kept ≥1 release; filter ownership resolved in Phase 0.
- **[socratic, design-minimalist] Measure before building; ship the cheap in-range fix first.**
  Phase 0 added (incumbent latency 0a, real-usage histogram 0b, representation spike 0c,
  model spike 0d, in-range quick-fix 0e, GT premise 0f), gating the programme.
- **[failure-mode, delivery, socratic] Latency must be p99/worst-frame over the composed
  pipeline with audio running** — G3 re-stated accordingly; measured incumbent baseline (0a).
- **[failure-mode, socratic, delivery] Ground truth weakest exactly at the rear** (planar flip
  at ±90°, cap slip, marker→head extrinsic, correlated ensemble). GT-1 downgraded with a slip
  budget + dead-bands + calibration; GT-2 (IMU) is the rear ruler and bare-head cross-check;
  GT-3 restricted to front/self-consistency; 0f validates before the rig.
- **[failure-mode — F6] Front-precision risk of a wholesale swap.** Adopted the **hybrid**
  (keep landmark solve in front, regressor past profile), protecting G2.
- **[ambition, failure-mode, delivery] Vacuous "beats a pipeline that outputs nothing" gate.**
  Replaced with numeric per-regime targets + zero flip-rate + live-latency gate.
- **[socratic — Q7, failure-mode — F5] Sign loss on still-dwell / multi-frame gap.** Added the
  turn-and-hold-away recording case, a specified sign-loss recovery + confidence floor + bounded
  rear drift, and camera-fps as a first-class constraint.
- **[ambition — F7] Bimodal rear needs more than a scalar.** Added an ambiguity / two-hypothesis
  channel to `PoseState`.

**Rejected / deferred:** v1's wholesale replacement (rejected for G2); per-frame head detection
(deferred — detect-rarely+track is right for a single slow-moving head, G3); promoting a second
camera ahead of the IMU (deferred — IMU covers the same gap more cheaply; second camera is the
IMU-refused fallback).

**Adversarial-pair resolution (`ambition-amplifier` vs `design-minimalist`).** They conflicted
on scope sequencing: amplify (run M-D concurrently, commit IMU + a full training corpus up
front) vs minimise (smallest thing first, spike before any rig). **I sided with
design-minimalist on *sequencing*** — Phase 0's cheap spikes gate the programme and the rig/
training corpus are not built until the premise is proven — **while adopting ambition-
amplifier's *substance*** where it was a defect or an intent-retreat, not scope inflation: the
±90° Euler defect, numeric full-circle criteria, M-D planned+budgeted as the *probable* path,
and the IMU promoted from alternative to a recommended track. The two reconcile at the
**Phase-0 spike**, which both demanded.

**Invocation log.** `ambition-amplifier` 2026-11-04: novel finding (±90° Euler cap,
code-verified; IMU-promotion and numeric-gate arguments). `assumption-archaeologist` not
invoked (no ADR-justified behavior is being changed; the RFC adds a capability). `design-
experimenter` not separately invoked for the post-round-1 empirical check: the round's
load-bearing empirical claims were **code claims** already falsified/confirmed directly
in-source during the round (Euler cap, two producers, confidence-unconsumed — re-verified by
the author against `solve.rs`, `vision.rs`, `geometry.rs`); the remaining empirical claims
(a permissive in-budget full-range model exists; ArUco GT holds at the rear) are **hardware/
asset-dependent** and are deferred to Phase-0 spikes 0d/0f by design (per the workflow's
"platforms unavailable" skip) — recorded here and in Open questions.

**Consensus attack.** `general-purpose` 2026-11-04: consensus-attack — **found a real shared
blind spot** (not consensus-survives). The whole panel and v1/v2 debated the *tracker*
end-to-end and inherited "if the tracker reports a correct head-relative direction, the
renderer reproduces it with fidelity" as given. It is false in the owner's **own** evidence:
journal 17 (judge + ear verified) documents the **90° back gap (≈150–210°) broken for every
interpolation method** because the measured rear BRIRs are front/back-ambiguous — a **data**
ceiling no tracker or interpolation fix touches — and it bites **inside** the ±90° band
(at yaw 180° the world-centre dialogue maps to head-relative 180° and "collapses to the
front," i.e. anti-world-locked). **Incorporated** (one revision, per workflow): added the
*renderer co-bottleneck* subsection to **Problem**, Phase-0 gate **0g** (render-fidelity check
on the existing journal-15/17 harness), corrected the non-goal mislabel (world-front content
swung to head-relative rear ≠ world-rear content), and a sequencing rule that the rear-cue/
BRIR upgrade **leads** if 0g fails. No further round triggered; the finding was already
empirically established in-repo (journal 17), so no `design-experimenter` probe was needed.
*(v3 update: the operator's decision D2 supersedes the "leads" sequencing — the rear-cue/BRIR
work is **deferred** behind the tracker, and 0g is demoted to advisory. The co-bottleneck
finding stands as documented context; only its priority changed.)*

## Empirical-validation note

Load-bearing claims and where each is falsifiable, cheapest first: (0c) a full-circle yaw can
travel end-to-end through this codebase — **falsifiable today** by the +60→+120→+170 test, and
**expected to fail** against current `rotation_to_euler` (that failure is the point); (0a)
there is latency headroom — measured on this box; (0b) the rear hemisphere is actually used —
measured over real sessions; (0d) a permissive, in-budget full-range model exists with clean
training-data lineage — model spike; (0f) marker PnP yields usable rear GT despite grazing-
incidence flip and cap slip — one clip. These gate the expensive M-D training work; none
should be taken on faith.

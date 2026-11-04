# RFC: Full-range (toward 360°) head tracking — from a face-landmark solve to a landmark-free head-orientation estimator

**Status:** Draft (v2 — post 5-critic panel + consensus attack). Proposal awaiting operator
go/no-go on the Phase-0 gate and the three load-bearing decisions in *Open questions*.
**Date:** 2026-11-04
**Author:** Jean Ibarz (with Claude)

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
do we train. Throughout, a **head-mounted IMU** (the headphones are already on the head) is
flagged as the one path that reaches *literal* 360° with no rear blind cone, and is
recommended as a parallel track for the operator to weigh.

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
an **anti-world-locked** render. **Consequence for this RFC:** the render **rear-cue /
BRIR-coverage** problem is a **co-equal workstream on the critical path**, not a downstream
detail — and it gates *every* 360° path, vision **and** IMU (a perfect IMU orientation still
can't fix the back gap). Phase-0 **0g** measures this cheaply before the tracker programme
commits; if 0g fails (journal 17 says it already does), the rear-cue/BRIR upgrade is the
**true gating item** for literal world-locked 360°.

### The fundamental limit this RFC does and does not remove

A **single screen-mounted camera cannot see the back of the head.** Facing fully away, the
pixels are hair/skull/neck — low-information and **left/right symmetric near ±180°**, so one
frame cannot tell "160° left" from "160° right." This RFC does **not** claim a monocular
frame-wise oracle at the back. The **vision track** claims the achievable thing: lock
accurately through the **front/side hemisphere (±90°)**, track **continuously into the rear
quarters** by temporal continuity (you *rotated* there through 90°/120°/150°), and **degrade
honestly** (low confidence, held sign, never a flip) where even that fails. The only paths to
**literal, drift-bounded 360° with no blind cone** are the **head-mounted IMU** and
**multi-camera** (FR-13); both are promoted below from "alternative" to tracks the operator
should actively choose between, because the operator asked for *360°*, not *±90°*.

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
- **G5 — Licensing (requirements §9): conditional on the product/personal decision.** §9 wants
  GPL avoided and compatibility with a possible **paid tier**, so for a *shipped* model the
  architecture, **weights**, and **every training dataset** must be permissive (no GPL, no
  non-commercial/research-only). **If this is the operator's personal rig** (Open Q1), G5
  relaxes for local-only use and a non-commercial model may run privately. This fork is
  load-bearing and decided before M-B.
- **G6 — Measurement before model.** Phase 0 gates the programme; every model choice is made
  against numbers on real recordings.

### Non-goals

Multi-person pose; precise 6-DoF translation (orientation is the product-critical quantity);
eliminating the camera on the vision track (the IMU track does that separately);
**sub-degree *tracker* accuracy at world-rear directions** (MAA is coarse there) — **but note
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
- **0g — Render-fidelity check (the consensus-attack gate).** Using the **existing**
  rotating-source harness + judge from journals 15/17, measure whether the **world-locked
  stage survives a 90–180° head turn at the *render* level** — i.e. can the 7-anchor BRIR set
  reproduce world-front content (centre/dialogue, front L/R) when it maps to head-relative
  150–210°? Journal 17 indicates it **cannot** today (back gap broken, rears front/back-
  ambiguous). *Decides the sequencing of the whole programme:* if 0g fails, a **rear-cue /
  BRIR-coverage upgrade** (re-measured rears, an HRTF set that resolves the owner's back, or a
  synthesised rear cue — journal 17's own "next") is on the critical path **ahead of or beside**
  the tracker work, because an accurate rear yaw is unusable without it. Cheapest high-leverage
  check in the RFC — the harness already exists.

**Phase-0 gate:** proceed to the model programme only if 0a shows headroom (or a path to it),
0b shows the rear is worth it (or the operator wants it regardless), 0c/C0 lands, 0d shows a
viable net, 0f shows usable rear GT (or we fall back to IMU GT), **and 0g shows the renderer
can express rear-turned directions (or the rear-cue/BRIR upgrade is scheduled alongside)**.
Otherwise the deliverable may be just C0 + 0e + the hybrid's front half, which is a legitimate,
much cheaper outcome — and if 0g fails, the **rear-cue/BRIR upgrade leads**, with the tracker
programme gated behind it rather than the reverse.

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
  **per-session slip check against an independent reference**, a documented dead-band near ±90°,
  and a cube (not a flat tag) to narrow the dead-bands. Validated in 0f before the rig is built.
- **GT-2 — head-mounted IMU (orientation).** Phone/cheap IMU on a headband, time-synced to
  video. Drifts in absolute yaw but is **occlusion-proof and bare-head-capable**, so it is the
  **independent reference** that cross-checks GT-1's slip and covers bare-head clips GT-1
  cannot. (It is also the sensor the IMU track below would ship — the work is shared.)
- **GT-3 — model-ensemble pseudo-GT, with a correlated-bias caveat.** A geodesic (rotation)
  median over several full-range models, **but** those models share the real datasets' rear
  blind spot, so their errors are **correlated** and agreement can be **confidently wrong** at
  the rear. Use GT-3 for front/profile ranking and for **label-free self-consistency**
  (continuity/monotonicity on smooth sweeps; angular-velocity integration), **not** as a rear
  ruler. The rear ruler is GT-2 (IMU), full stop.

**Recording protocol (the operator's offer):** short clips, raw frames + capture timestamps,
cap+IMU: slow continuous yaw sweeps CW and CCW through a full turn; **plus a "turn-and-hold
facing away" dwell** (the still-rear posture that breaks velocity-continuity); pitch nods;
roll tilts; combined look-around; at 2–3 distances and lightings; with confounders (hat
on/off, glasses, hair up/down, hand near face). **Keep cap and bare-head clips as separate
distributions** so cap-domain shift is measurable, not hidden.

### Train / personalise (M-D) — likely the real path, budgeted as such

The panel's licence analysis says M-D is **probable, not contingent**: every named candidate
has tainted lineage (300W-LP derives from research-licensed 300-W; CMU Panoptic/AGORA are
restricted), so a **permissive shippable** weight set may not exist. v2 therefore **plans and
budgets** M-D up front (with a **kill rule** and a **capped budget**), while still **gating**
its start on Phase 0 / M-B so we don't train blind:

- **Synthetic full-range data** (FLAME/3DMM + hair/scalp, domain randomisation over hair,
  hats, glasses, light, background, skin): the only source of **labelled rear** views at scale,
  **license-clean by construction**. Risk: the **synth→real gap is worst in the front cone**
  that G2 cares most about — which is exactly why the **hybrid keeps the real landmark solve in
  front**, so the regressor only has to win **outside** ±45°.
- **Per-user personalisation** — the single biggest accuracy win **for this listener**, on
  GT-1/GT-2 labels of the actual head/hair/room/camera. **But it does not ship to others**, so
  whether it is the main play or only a research probe is decided by Open Q1 (product vs
  personal rig). If personal rig: personalisation is the main play and G5 relaxes.

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
- Offline (non-shipping Python): marker+IMU GT extraction & time-sync, ensemble pseudo-GT,
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
  sign only on a frontal/profile re-acquire or from the IMU if present. In the recording set.
- **Sign lost on a multi-frame gap mid-turn (15 fps is the real bottleneck, journal 05):** a
  180° turn in ~0.5 s is ~6 frames; one dropout can lose the hemisphere **permanently** until
  frontal re-acquire, and rear magnitude can drift unbounded. Specified: bound rear-cone drift,
  define the confidence floor during held-sign rear tracking, and treat **camera fps as a
  first-class constraint** (raising it is a real mitigation, not an assumption).
- **Latency regression:** worst-frame (detector+net+fusion, larger head crop) starves audio
  even if p50 looks fine → G3 is a **p99/worst-frame** gate with the audio thread running.
- **GT ill-conditioned exactly at the rear:** GT-1 dead-bands/flip at ±90°, cap slip; GT-3
  correlated-wrong at the rear → the rear ruler is GT-2 (IMU); GT-1 numbers carry a slip budget.
- **Cap-domain shift:** training/evaluating on always-cap imagery vs bare-head deployment →
  keep distributions separate; bare-head GT via IMU.
- **License surprise:** scored in M-B **before** accuracy; a GPL/non-commercial model is
  quarantined to **offline pseudo-GT only** (unless Open Q1 = personal rig).

## Milestones & numeric gates (each a go/no-go)

- **Phase 0 (gate above).** Cheap; ships C0 + 0e. **Prerequisite for everything.**
- **M-A — measurement rig & GT.** Build GT-1 (post-0f), GT-2 cross-check, GT-3 self-
  consistency, the harness + metrics; capture the operator's set. *Gate:* can we score a model
  **numerically** per regime on real recordings, with a trusted rear ruler?
- **M-B — off-the-shelf bake-off.** Score candidates on M-A. *Numeric gate (examples, to be
  fixed with the operator):* front (±30°) RMS **≤ incumbent + 1°** per axis; ±90° yaw RMS **≤
  ~8°**; rear (90–180°) RMS **≤ ~20°** vs GT-2; **front/back flip rate = 0** over N full CW+CCW
  sweeps; **p99 composed latency ≤ measured-headroom** (0a) with audio running; **license =
  permissive** (or Q1 = personal). **All pass → C1–C4. Any fail on a non-permissive winner →
  M-D.**
- **M-C — integration (slices C1–C4).** Hybrid + fusion + wrap path live behind the flag; A/B
  on M-A. *Gate:* G2 (no front regression, numeric) **and** G1 (measured range extension + zero
  flips) on **live** latency → promote (C4), then C5 after soak.
- **M-D — (planned, gated) train/personalise.** Synthetic + per-user; distil to an in-budget
  ONNX net; **kill rule:** if after the capped budget the net still misses M-B's numeric gate,
  stop and fall back to C0 + 0e + hybrid-front + (recommended) the IMU track. Return to M-C on
  success.

Spikes/proofs first: 0c (representation, half day) **before** 0d (model, 1 day) **before** any
rig — the cheapest falsifications lead.

## Alternatives considered

- **Head-mounted IMU (headphone-strapped), Smyth Realiser / Waves Nx style — promoted to a
  recommended parallel track.** The headphones are **already on the head**, so the one cited
  cost ("hardware on the head") is **already paid**. An IMU gives **camera-independent,
  occlusion-proof, low-latency, full-circle** orientation with **no rear blind cone** — the
  only listed path to *literal* 360°, which is what the operator asked for. Its cost is **yaw
  drift**, which the **camera cancels every time the user faces the screen** (most of the
  time), and a rear excursion lasts seconds where gyro drift is small. **Camera+IMU fusion** is
  therefore strictly better than either alone: camera = absolute reference, IMU = rear cone +
  lower latency (helps the ~1° MAA and G3). GT-2 already puts an IMU on the head for M-A, so the
  hardware/time-sync work is shared. **Recommendation: pursue the vision hybrid as the
  no-extra-hardware default *and* scope the IMU-fusion track in parallel; if the operator
  values literal 360°, IMU-fusion is the surer route** (Open Q2).
- **Multiple cameras (FR-13).** A second camera genuinely sees the rear, removing the blind
  cone — but adds calibration, cost, and setup. Worth promoting **only if the IMU is refused**;
  make it the explicit fallback with a trigger written into the M-C rear gate (Open Q4).
- **Keep face-only; just add VIDEO-mode temporal smoothing.** Cannot pass profile — but is
  **0e**, shipped first, and may remove most of the *in-range* pain cheaply. Orthogonal, not a
  range fix.
- **Wholesale landmark→regressor swap (v1).** Rejected for G2 risk (synth→real gap worst in
  front); the **hybrid** keeps the validated front solve and asks the regressor to win only
  outside ±45°.
- **Status quo (±45° cap).** The honest baseline; rejected — the operator wants full range and
  pre-approved the effort.

## Open questions for the operator (load-bearing — these change the plan)

1. **Product or your personal rig?** This decides **G5**: a shipped product needs permissive
   weights (likely forcing M-D/training); a personal rig can run a non-commercial model (e.g.
   6DRepNet360) **locally this week**, and makes **per-user personalisation the main play**
   rather than a non-shippable probe. **Most load-bearing question in the RFC.**
2. **Head-mounted IMU: in scope?** If yes, camera+IMU fusion is likely the **fastest, surest**
   route to literal, drift-bounded 360° (and the headphones already carry it). Pursue in
   parallel with — or instead of — the harder rear-cone vision work?
3. **Rear-hemisphere priority vs front:** given 0b may show the rear is rarely used, do you
   want M-C to **ship at ±90° solid + graceful rear** (sooner), or **hold for full-circle**
   (IMU/multi-cam)? Are you aware the **vision-only** deliverable is "±90° solid + honest rear
   decay," not a literal circle?
4. **Marker-cap recording:** OK to wear a printed-tag cap + a headband IMU for a few sessions,
   so we can auto-label and cross-check? This is the single thing that most de-risks evaluation
   and personalisation (and is needed for 0f).
5. **Rear-cue / BRIR upgrade priority (from the consensus attack):** journal 17 says your
   current 7-anchor BRIR set cannot render world-front content at head-relative 150–210°
   (the back gap is "broken for every method"; your rears read front/back-ambiguous). So
   literal world-locked 360° needs a **render-side** fix — re-measured/added rear BRIRs, an
   HRTF/HRIR set that resolves your back, or a synthesised rear cue — **as well as** the
   tracker. If **0g** confirms this, do you want the rear-cue workstream to **lead** (tracker
   gated behind it), run **in parallel**, or do you accept "±90° solid, honest rear" and defer
   literal 360° until the BRIR set is upgraded? This gates how much of the tracker programme is
   worth starting now.

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

## Empirical-validation note

Load-bearing claims and where each is falsifiable, cheapest first: (0c) a full-circle yaw can
travel end-to-end through this codebase — **falsifiable today** by the +60→+120→+170 test, and
**expected to fail** against current `rotation_to_euler` (that failure is the point); (0a)
there is latency headroom — measured on this box; (0b) the rear hemisphere is actually used —
measured over real sessions; (0d) a permissive, in-budget full-range model exists with clean
training-data lineage — model spike; (0f) marker PnP yields usable rear GT despite grazing-
incidence flip and cap slip — one clip. These gate the expensive M-D training work; none
should be taken on faith.

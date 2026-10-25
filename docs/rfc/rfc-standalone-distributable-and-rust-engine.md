# RFC: Standalone distributable + a Rust engine & HRTF studio, via a SOFA→filter-pack split

**Status:** **Approved** (operator, 2026-10-25) — north star: **zero Python in the delivered
product**; Track D is interim de-risking, not the destination. All open questions resolved (see
*Operator decisions*). v2 = restructured after a 5-critic panel + consensus attack.
**Date:** 2026-10-25
**Author:** Jean Ibarz (with Claude)

> **v2 note (post-review).** v1 proposed one phased arc (distributable → Rust engine → gated
> MediaPipe spike) and claimed the engine/toolkit split "removes the hard parts from the engine." A
> 5-critic panel + a source check corrected three load-bearing things: (1) the split removes the
> *filter* hard parts but leaves the **highest-risk** item (head pose) untouched; (2) the spike as
> written measured the **wrong hypothesis** — the engine consumes MediaPipe's *FaceGeometry* 4×4
> matrix, not a TFLite+PnP output, so "convert TFLite + PnP" would fail algorithmically; (3) the
> **expected** outcome of the Rust arc is a *permanent* Python vision sidecar + bundled interpreter,
> i.e. Python is not actually eliminated — v1 priced the hoped outcome, not the likely one. v2
> splits the work into an independent **Distributable track** (ships now, no rewrite) and an
> honestly-priced, committed **Rust + Studio track** (the zero-Python north star, with one escalation
> gate), fixes the filter-pack contract to be
> direction-indexed, and reframes the pose spike around the real target.

---

## Operator decisions (2026-10-25 — all six OQs closed)

The operator (delegated authority) **approved this RFC** and resolved every open question, so none
remain open. The north star is **explicit and non-negotiable: the application fully standalone in
Rust, zero Python in the delivered product.** Track D is the de-risking intermediate.

- **OQ6 (payoff — load-bearing):** the win is a **single self-contained native binary** — no embedded
  Python interpreter, no WSL dependency or hardcoded-path fragility (the real thing that made it
  non-distributable), smaller install, no interpreter CVE/supply-chain surface, one codebase, and a
  cross-platform studio essentially for free. Track R is approved **as a goal**, not time-permitting.
- **OQ3 (vision sidecar):** a Python vision sidecar is acceptable **only as an interim migration
  scaffold, never the delivered product.** Therefore R-S1 prioritizes **route (b): a Rust-ownable
  ONNX 6-DoF pose model** (e.g. 6DRepNet via the `ort` crate); route (a) (reproduce FaceGeometry) is
  the fallback. **Escalation condition (the one stop):** if R-S1 cannot clear its numeric pose-parity
  gate on *either* route across the multi-condition set — i.e. zero-Python is genuinely blocked —
  STOP and report to the operator. Everything short of that: keep driving.
- **OQ1 (studio repo):** the core **baker library lives in `binaural-localization`** (it owns
  densification + emits the grids); the interactive studio ships as its own repo later. Do not block
  Stage 0 — M-D2's bake path is an in-repo library.
- **OQ2 (licensing):** ship the owner's own redistributable packs + import/bake tooling only; import
  fetches third-party DBs from source or a user-supplied file. No third-party redistribution.
- **OQ4 (pack payload):** `.wav`-per-IR + JSON manifest first; a binary variant only if size bites.
- **OQ5 (44.1k):** bounded load-time resample (not per-block, not a hard refusal); multi-rate bake
  later only if measurement demands it.

## Problem

The engine is a Python app with a Rust convolution kernel. To run it needs a WSL Python 3.12 stack
(MediaPipe, OpenCV, `sounddevice`, PyAudioWPatch, scipy, h5py) plus personal assets outside the repo
(EqualizerAPO BRIRs; a 321 MB `hrir_sets/` of raw 96 kHz SOFA). The Tauri installer (PR #122)
packages the *window* but still launches that WSL Python via a hardcoded-path `run.cmd`. So it is
**not** a clean-machine distributable. The owner wants: (1) a self-contained installer that ships or
imports HRIR filters; and (2) ideally to drop Python for a pure-Rust engine, plus a second product —
a head-filter **studio** — and cross-platform support, accepting "a lot of work" if functionality is
kept or improved.

## What changed since journal 26 — stated honestly

Journal 26/27 scoped Rust to the convolver only: *"MediaPipe/OpenCV have no good Rust path and are
not the bottleneck."* Two things improved since: the render kernel is a mature Rust crate, and this
branch replaced Dear PyGui with a **web cockpit** a Rust server can host unchanged. **But the one
blocker journal 26 named — head pose — is unchanged.** No new evidence says MediaPipe's Rust path
improved; v1 merely deferred it to a spike. Honest framing: the remaining blocker is the *same* one
previously judged intractable. v2 treats porting the engine as **conditional** on that blocker, not
as a foregone conclusion, and makes distributability independent of it.

## Goals

- **G1.** Self-contained Windows installer: fresh machine → working head-tracked audio, no WSL/Python
  setup. *(Does not require any Rust port.)*
- **G2.** Ship the owner's HRIR packs + an **import/bake** path for more, without a 321 MB installer.
- **G3.** A **filter-pack contract** so the engine loads packs without parsing SOFA/HDF5 or doing
  CTF/DF **decomposition**/resampling at runtime. It keeps the cheap per-direction *synthesis* +
  live method cycling (that is the render method, not decomposition — preserved, see the pack design).
- **G4 (north star, committed).** **Zero Python in the delivered product** — a single standalone
  native Rust binary. This is non-negotiable (operator). The *only* stop is the R-S1 escalation
  condition (pose gate fails both routes); a Python vision sidecar is an interim scaffold, never
  shipped. Payoff is OQ6 (resolved).
- **G5.** A first-class **HRTF studio**: import, CTF/DF decomposition, analysis/comparison plots, bake
  5.1/7.1/N-channel packs. The owner's second product; where filter quality (= audio quality) is set.

## Non-goals

- **N1.** Redistributing third-party HRTF databases (licensing; see OQ2).
- **N2.** Porting the Track-B kernel driver (separate RFC; C/SYSVAD).
- **N3.** A DSP/perceptual redesign — this is packaging + a language port, so bit-exactness stays a
  checkable invariant.
- **N4.** macOS *loopback* parity as a near-term gate (no system loopback without a virtual driver);
  macOS is a goal for the **studio** (pure file processing, cheap) and a later engine item.

## Design

Two tracks. **Track D ships independently of Track R** — distributability is not held hostage to the
Rust outcome.

### The filter-pack contract (baked set primary + optional grid overlay)

v1's pack was speaker-layout-centric; a first correction over-rotated to "the pack is a direction
*grid*" — but that was verified against the **test** path (`load_dense_grid_sofa` → `dense_override`,
the RFC-0003 comparison harness), not the owner's **daily** render. **Verified in source**
(`live_render.py:341-440,512-643`, `voicemeeter_passthrough.py:400-418,475-477`): the production path
(`--brir-dir baked_7p1_test --method timealign`, no `--hrir-sets-dir`) consumes **three things at
once**, and a frozen grid carries none of them cleanly: (1) the **8-speaker baked layout** for 7.1
**input-channel routing** (`SPEAKER_LAYOUT_7P1`); (2) a **non-directional LFE** convolved through its
own baked BRIR (`--lfe-mode brir`) to match the owner's EqualizerAPO bass tuning — LFE sits at no
ring azimuth; (3) a **runtime-synthesized directional ring** (`interpolate_baked_brir` + loudness
match), with the **method cycled live (M key)**. Baking to a grid would drop LFE, kill live method
A/B, and contradict G3/N3 (runtime synthesis *is* the daily behavior). The corrected contract:

- **Primary payload = the measured baked set:** per-speaker stereo IRs for the input layout
  (channel labels, incl. LFE + its `brir`/`flat` handling) — the owner's hand-tuned 7.1 is one of
  these. The engine routes the 7.1 input over this and **synthesizes the directional ring at
  runtime** with the chosen method (live-cyclable) — unchanged from today.
- **Optional pre-baked `grid` overlay:** an ordered direction list + coordinate convention +
  per-direction IRs + the interpolation method it assumes — the dense-SOFA *comparison/test* sets
  (tens of MB). Supplied when a user wants to A/B a fixed grid; not required for daily render.
- **The toolkit bakes the former from SOFA** (CTF/DF decomposition, resample — all offline); the
  engine keeps the cheap per-direction *synthesis* (that is the render method, not decomposition).
- **Required manifest fields** (round-1 panel found these missing and biting): pack-format version;
  **coordinate / azimuth-sign & yaw/pitch/roll convention** (else packs localize *backwards* across
  toolkits); **per-pack gain / integrated loudness** (else live set-switch jumps level); samplerate(s)
  present; IR length; comp variants (`ctf`/`df`) and how they relate to the output-EQ; provenance +
  licence (metadata, not engine logic); **payload integrity hash**.
- **One loader, shared.** Implement the loader once in a small Rust `filterpack` lib and expose it to
  Python via PyO3, so Track D's Python engine and any Track R Rust engine read **byte-identically**
  (no two drifting loaders). Ship a **JSON schema + golden fixtures** both repos test against in CI.
- **No runtime shell-out.** The engine only *scans a pack directory*. Baking lives entirely in the
  studio/toolkit (a separate optional tool), so the engine never depends on the toolkit's runtime —
  the dependency direction that makes G3/G4 reachable.

### Track D — self-contained distributable (ships first, no Rust engine needed)

- **M-D1** `filterpack` format + shared loader + JSON schema + golden fixtures; engine reads packs
  (still Python). *Must express the dense direction grid + convention + gain before coding* (gate).
- **M-D2** Studio bake path: SOFA/dataset → `grid` pack (CTF/DF), writing into the user pack dir.
- **M-D3** Self-contained install. **Go/No-Go proof first:** assemble the full stack
  (mediapipe/opencv/scipy/h5py/pyaudiowpatch) under an **embeddable host-Windows Python** (today it
  runs under WSL — never under embedded host Python) and **measure the installed size**. If it won't
  assemble or blows past ~321 MB, Phase-0's premise is void and we rethink before committing. User
  pack dir (`%APPDATA%\WLBS\packs`) scanned at startup; bundled-default fallback; paths resolved
  relative to the install (fixes the hardcoded `run.cmd` path).

Track D delivers G1–G3 with Python still in the box — which is the honest statement that **Python was
never what blocked distributability**; the hardcoded paths, WSL assumption, and unbundled SOFA were.

### Track R — Rust engine + studio (the owner's ambition, de-risked and honestly priced)

**Spikes run FIRST** (cheapest-to-disprove first), because they gate everything after.

- **R-S1 (pose spike — the make-or-break gate; run early).** Target = a **Rust-ownable head-pose
  source** the engine owns outright (so zero-Python is real), *not* "reproduce MediaPipe via PnP."
  Verified: the engine uses MediaPipe **FaceGeometry**'s 4×4 transform matrix
  (`output_facial_transformation_matrixes=True` → `head_pose_from_matrix`), a Procrustes solve over
  ~478 metric landmarks — a *subsystem*, not a PnP one-liner. **Route (b) is primary (operator):** a
  Rust-deployable **ONNX 6-DoF regressor** (e.g. 6DRepNet via the `ort` crate) — a model the engine
  owns/quantizes, which makes zero-Python achievable. **Route (a)** (reproduce FaceGeometry in Rust)
  is the fallback. Both measured against MediaPipe's **own matrix on identical frames**.
  **Gate:** pose RMS error (deg) and p95 detect latency within a stated tolerance across a
  **multi-condition validation set** (faces, skin tones, glasses, low light) — not just the owner's
  face. **Escalation:** if *neither* route clears the gate, zero-Python is blocked → STOP and report
  to the operator (the one stop condition); do not silently settle for a permanent sidecar.
- **R-S2 (audio spike, parallel).** `wasapi`-crate loopback **with** the dual-clock cushion/backlog/
  drift accounting and **MMCSS "Pro Audio"** elevation (`AvSetMmThreadCharacteristicsW`) replicated,
  vs PyAudioWPatch under sustained load — "frames arrive" is not parity. (SYSVAD/Track-B capture is N2.)
- **R0 (strangler, low-risk, valuable either way).** Move the world-lock/interp DSP from
  `live_render.py` into a Rust `render-core` lib behind PyO3. This removes the largest Python DSP
  *incrementally* (no big-bang parallel engine, no dual-maintenance window) and is useful even if the
  host never changes. It is the better *first* Rust step than the HTTP server.
- **R-engine (only if R-S1/R-S2 pass their gates).** Crate-split, not a god-binary: `render-core`
  (pack + world-lock + kernel, pure, no IO) · `audio-io` (a `AudioSource`/output trait so Track-B is a
  drop-in) · `control-plane` (`axum` serving the existing cockpit assets; the `/events`/`/api`/
  `get_config` **wire protocol frozen with a conformance test** run against both servers) ·
  `vision-client` (a **versioned** protocol: raw pose + confidence + **capture timestamp** + frames +
  control + heartbeat — so the predictor compensates the right latency and R-S1's result drops in
  either way).
- **Track S (studio), first-class and ideally Rust-native.** Import, CTF/DF decomposition,
  analysis/comparison plots (std-dev across subjects, filter A/B), N-channel bake. A Rust-native
  toolkit (an HDF5/SOFA reader + `rustfft` + `rubato` + webview plots) means the installed product has
  **no Python at all**; a Python baker is an acceptable *interim*. Cross-platform at near-zero cost
  (pure file processing). Repo choice = OQ1.

### Outcome & the single stop condition (operator-set)

The target end-state is **committed, not best-effort**: a standalone zero-Python Rust binary. Track D
ships first as de-risking (real win on its own). The Python vision sidecar exists **only as an
interim scaffold** during migration — it is never the delivered product. The **one** thing that can
stop the Rust goal is the R-S1 escalation condition: if a Rust-ownable pose source cannot clear the
numeric parity gate on *either* route (b ONNX, then a FaceGeometry-repro fallback) across the
multi-condition set, then zero-Python is genuinely blocked and the effort **stops and reports to the
operator** for a call with the owner — rather than quietly shipping a permanent sidecar. Short of
that gate failing, the effort keeps driving to zero Python.

## Edge cases

- **Samplerate:** today the engine *resamples at load* (`resample_poly`); a 44.1k-only DAC exists.
  "One pipeline-rate pack + refuse mismatch" would make the app refuse to run. Resolution: packs
  carry rate; the engine keeps a **bounded load-time resample** (not per-block), or the studio bakes
  the needed rate — not a hard refusal.
- **Pack convention mismatch** → localizes backwards: the convention field is **required**; loader
  rejects a pack missing it.
- **Sidecar crash** (base case): degrade to last-known pose / freeze-fade (reuse B4.2), not a hard stop.
- **Bundle assembly/size** (M-D3 gate): if embeddable-Python + mediapipe won't assemble or exceeds
  the SOFA size we set out to avoid, Track D stops and we reconsider (e.g. a slimmer vision runtime).
- **Pack format evolution:** refuse unknown *major*; define unknown-*minor* as ignore-with-warning.

## Milestones (each a go/no-go; spikes and proofs first)

| ID | What | Gate |
|----|------|------|
| M-D1 | `filterpack` schema+loader+fixtures (grid + convention + gain) | schema expresses the dense grid |
| M-D2 | Studio: SOFA → grid pack (CTF/DF) | round-trips through the loader |
| M-D3 | Self-contained install | **bundle assembles & size ≤ target** |
| R-S1 | Pose spike (FaceGeometry-repro **or** ONNX pose model) | RMS/latency across the validation set |
| R-S2 | Audio spike (loopback + drift cushion + MMCSS) | parity under sustained load |
| R0 | `render-core` DSP behind PyO3 | bit-exact vs Python |
| R-engine | Crate-split Rust engine (only if R-S1 & R-S2 pass) | wire-protocol conformance; no DSP change |
| S1.. | Studio slices (import → decomp view → compare → N-ch bake) | cross-platform from first slice |

## Alternatives considered

- **Ship raw SOFA / decompose at runtime.** Rejected: 321 MB, keeps h5py/scipy in the engine.
- **Stop at Track D forever (Python hybrid).** A *viable product*, not a strawman. The honest
  question for the owner (OQ6): what specific defect of the shipping hybrid does the Rust port repair?
  If none can be named, Track D is the RFC and Track R is a preference to pursue as time allows.
- **Big-bang pure-Rust rewrite.** Rejected: bets on the falsified premise; dual-maintenance trap.
- **Rust `main` embedding Python via PyO3 for vision only.** The in-process form of the sidecar;
  noted as a Track-R option.
- **Reproduce MediaPipe exactly vs. adopt a different pose model.** R-S1 keeps both routes open;
  (b) may be *easier* and gives the engine a model it can own/quantize, at the cost of re-validation.

## Decisions (formerly open questions — all RESOLVED, see *Operator decisions*)

> All six are closed by the operator (2026-10-25). The entries below keep the rationale that led to
> each resolution; the binding answers are in *Operator decisions* at the top.


- **OQ1 — Studio repo:** extend `binaural-localization` (owns densification + emits the grids) vs a
  new standalone product. *Lean:* core baker in `binaural-localization`; the shipped interactive
  studio likely wants its own repo/release cycle (ambition-amplifier's point). **Owner picks.**
- **OQ2 — Licensing:** owner's own HRIRs are redistributable; third-party DBs (IRCAM Listen, RIEC…)
  often are not for a donationware product. *Lean:* ship owner packs + tooling; import **fetches from
  source** or a user file.
- **OQ3 — Accept the base case?** Is "Rust engine + permanent small Python vision sidecar + bundled
  interpreter" acceptable, given it may be *more* surface than today? Decide **before** R-engine.
- **OQ4 — Pack payload:** `.wav`-per-IR+manifest first; define the binary variant's bit-depth/
  interleave/endianness only if size bites.
- **OQ5 — 44.1k policy:** bounded load-time resample vs multi-rate bake.
- **OQ6 — The payoff (the one the panel kept asking):** name the concrete, falsifiable win pure-Rust
  gives the *user* (install size ↓, cold-start ↓, no interpreter CVE surface, single binary,
  maintainability). Track R's justification rests on this line.

## Empirical-validation note (per the review workflow)

The round-1 **empirical checkpoint falsified v1's load-bearing premise** by source read: the engine
consumes MediaPipe FaceGeometry's 4×4 matrix (`voicemeeter_passthrough.py:806,1351-1353`,
`head_pose.py:70`, `spikes/b0_4_head_tracker.py:19-22`), so "TFLite+PnP" was the wrong target — R-S1
is re-scoped accordingly. The remaining load-bearing claims are explicit, **gated** spikes to run
during implementation, not assertions: R-S1 (pose parity), R-S2 (loopback+drift+MMCSS parity), and
the M-D3 embeddable-Python bundle size/assembly. All are runnable on the Windows host.

## Critic feedback incorporated

Round 1 — 5 critics (boundary-critic, failure-mode-analyst, design-minimalist, socratic-challenger,
ambition-amplifier) + the mandatory post-round-1 empirical checkpoint.

- **failure-mode-analyst (critical, source-verified):** the pose spike tested the wrong hypothesis
  (FaceGeometry ≠ TFLite+PnP) → **R-S1 re-scoped**. Expected outcome is a permanent sidecar +
  bundled Python → **base case priced for OQ3; operator then set G4 = committed zero-Python with one
  escalation gate (sidecar interim-only)**. Missing manifest fields
  (convention, gain, integrity, grid) → **added**. MMCSS + dual-clock cushion absent from feasibility
  → **R-S2 now includes them**. Embeddable-Python bundle unproven → **M-D3 go/no-go proof**.
  Samplerate refusal breaks 44.1k → **bounded resample / multi-rate (OQ5)**.
- **boundary-critic (needs-revision):** pack must be direction-indexed, not fixed channels (critical)
  → **rewritten**; runtime shell-out re-couples engine→toolkit → **removed (dir-scan only)**; god-
  binary → **crate split**; sidecar/wire/vision protocols under-specified → **versioned + conformance
  tests**; share one loader via PyO3 → **adopted**; strangler `render-core`-first → **R0**.
- **design-minimalist vs ambition-amplifier (adversarial pair — resolution):** I sided with
  **ambition-amplifier on keeping** the pure-Rust engine and a first-class studio as goals — the
  owner explicitly asked for both and accepted large effort, so cutting them (minimalist) would pivot
  away from stated intent — **but adopted design-minimalist's discipline**: Track D ships
  independently, Track R must carry an explicit payoff (OQ6) and a stopping rule, and speculative
  manifest scope (N-channel, versioning beyond major) is trimmed to what a consumer needs now. Net:
  ambition on *what* to pursue, minimalism on *gating and sequencing*.
- **socratic-challenger:** "what is Rust *for*?" unanswered → **OQ6 made load-bearing**; Phase 0
  proves Python doesn't block distributability → **stated outright**; dual-maintenance window →
  **R0 strangler instead of a parallel engine**; spike validation across faces → **multi-condition
  set in R-S1's gate**.
- **ambition-amplifier (invocation log, 2026-10-25: novel finding):** studio as a second product →
  **G5 + Track S + S-milestones**; Rust-native toolkit so "no Python" is real → **adopted as the
  goal, Python baker as interim**; run the pose gate first → **spikes-first ordering**; cross-platform
  asymmetry (studio cheap, engine hard) → **N4 reflects it**.
- **Empirical checkpoint (2026-10-25):** v1's M-R2 premise falsified against source (see note) →
  restructured rather than iterated (early convergence per the workflow).

**Consensus attack (general-purpose, 2026-10-25: finding — shared blind spot found, incorporated):**
the whole panel validated "pack = direction-indexed grid" against the RFC-0003 *test* harness
(`dense_override`), not the owner's *daily* render — which actually routes 7.1 input over the baked
layout, convolves a non-directional LFE through its own BRIR, and **synthesizes** the ring at runtime
with a live-cycled method. A grid-only pack would break the bass tuning, kill live method A/B, and
contradict G3/N3. **Incorporated:** the pack's primary payload is now the measured baked set (layout
+ LFE + per-speaker IRs); the dense grid is an optional pre-baked overlay for comparison; G3 is
refined to decomposition/resampling-offline while directional synthesis stays in the engine. The
split, the R-S1 pose re-scope, and the R-S2 audio risk were explicitly re-examined and **survive**.

Convergence: one critic round + mandatory empirical checkpoint (falsified v1's pose premise) + one
consensus attack (found + fixed the pack-model blind spot). Early convergence per the workflow —
the round-1 restructure was decisive and the attack's finding was source-verified, not speculative.

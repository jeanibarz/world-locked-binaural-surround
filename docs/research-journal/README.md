# Research journal — a head-tracked, world-locked binaural renderer

The linear, chronological record of this project: the problem, the objective,
every block we build, what each one taught us, where we were wrong, and what we
do next. Read top to bottom for the whole story; jump to any entry for one step.

Written as a genuine engineering/lab notebook: state intentions before results,
record negative results, tie conclusions to evidence, write for a cold reader.
It is maintained by an autonomous build chain (see [`../../AGENTS.md`](../../AGENTS.md)).

## The problem & objective

See [`00-problem-and-objective.md`](00-problem-and-objective.md). In short: static
headphone surround rotates with the head and fails to externalise; we keep the
virtual speakers **world-locked** and move the convolution with the head, to train
real-space perception — as a low-latency Windows app with a virtual 7.1 device,
individual/AI-tuned HRIRs, live EQ, and an AI judge for calibration. Full
requirements: [`../requirements.md`](../requirements.md).

## Status at a glance

| Milestone | Goal | Status |
|---|---|---|
| M0 | Foundations: repo, docs, dev env, spikes | ● done (B0.5/B0.7 are latency follow-ups) |
| M1 | Static binaural render engine (7.1 SOFA → stereo) | ● core done (B1.7 head-rot interp remains) |
| M2 | Head-pose tracker (camera → smoothed az/el/roll) | ◑ in progress (B2.1 tracker+1€ done; B2.2 calibration remains) |
| M3 | World-lock: tracker ↔ render, world-fixed speaker | ☐ |
| M4 | End-to-end on Windows (virtual device → headphone) | ☐ |
| M5 | Live EQ filters & presets | ◑ in progress (B5.1 EQ engine done; comp+EQ render integration + presets remain) |
| M6 | GUI | ☐ |
| M7 | AI-judge calibration loop | ☐ |
| M8 | Latency hardening (native hot path) | ☐ |
| M9 | HRIR fine-tuning, packaging, donationware release | ☐ |

The ordered work blocks live in [`../backlog.md`](../backlog.md).

## The journal, in order

| # | Entry | One line |
|---|---|---|
| 00 | [Problem & objective](00-problem-and-objective.md) | Why world-locked head-tracked binaural, and what we are building. |
| 01 | [Python project setup](01-python-project-setup.md) | Committed prototype stack (uv + src/ + pytest + ruff); green scaffold. |
| 02 | [Audio-I/O spike](02-audio-io-spike.md) | Real WASAPI from WSL via interop: enumerate + ~22 ms reported latency; VoiceMeeter masks the hardware path. |
| 03 | [Virtual-device spike](03-virtual-device-spike.md) | VoiceMeeter VAIO = the 7.1 sink; captured its stream (22 ms in); loopback harness exposed, measured round-trip gated on VAIO→B1 routing. |
| 04 | [Measured round-trip latency](04-measured-roundtrip-latency.md) | Virtual loopback ≈ **130 ms** via AUX→B1 — essentially one 6144-sample (128 ms) driver buffer. Fixed a concurrent-capture bug in the spike. |
| 05 | [Head-tracker spike](05-head-tracker-spike.md) | MediaPipe FaceLandmarker → yaw/pitch/roll from the webcam; inference ~**12 ms** (p95 14.6), but the C270's ~15 fps capture is the real bottleneck. |
| 06 | [HRIR/SOFA loader](06-hrir-sofa-loader.md) | Read SOFA via h5py → directions + per-ear IRs + engine-rate resample; validated on MIT KEMAR (ipsi/contra 4:1 head shadow confirms the convention). |
| 07 | [Static binaural renderer](07-static-binaural-renderer.md) | 7.1 → stereo from the owner's 8 baked Audeze BRIRs (convolve + per-ear sum); per-speaker ITD signs all correct; the EQ chain is deferred to M5. |
| 08 | [Direction interpolation](08-direction-interpolation.md) | ITD-aware time-alignment interpolation over a SOFA set (Hilbert-envelope delay + fractional shift + k-NN blend); KEMAR leave-one-out LSD 2.50 vs 6.81 dB naive. |
| 09 | [Partitioned convolution](09-partitioned-convolution.md) | Real-time uniformly-partitioned overlap-save FFT convolver (single-channel + 7.1→stereo); matches `fftconvolve` to 1e-14, latency = one block; 7.1→stereo 3.2×RT at block 256 (5.3 ms). |
| 10 | [World layout model](10-world-layout-model.md) | Screen-relative 3-D speaker positions → per-channel (az,el); the full `Rᵀ(pᵢ−p_h)` projection defaulting to the fixed head; reproduces the nominal 7.1 angles exactly, yaw shifts every azimuth by −yaw. |
| 11 | [Static SOFA renderer](11-static-sofa-renderer.md) | Wired B1.1–B1.4 into `binaural_headtracker.render` (generic-SOFA 7.1→stereo, fixed head): layout → per-speaker HRIR lookup → convolve-and-sum; offline render == the B1.3 streaming engine to 1e-9; world-lock seam (head_rotation) ready for M3. |
| 12 | [EqualizerAPO offline comparison](12-eqapo-offline-comparison.md) | Our render == EQ-APO's bake to 2.8e-17; the subjective EQ is interaurally symmetric (0.16 dB) so it can't change ITD/ILD. **Its stability conclusion is corrected by entry 13** (ITD/coherence was the wrong proxy). |
| 13 | [EQ stabilises via DRR](13-eq-stabilises-via-drr.md) | Correction to 12: listening showed bake wanders, full is steady. The EQ *does* steady the image — not via geometry but by raising the direct-to-reverberant ratio (+1.9 dB mean, +5.1 dB on the worst rear channel), burying the diffuse tail under the direct hit. Elevates B5 (load-bearing, not cosmetic). |
| 14 | [Parametric EQ engine](14-parametric-eq-engine.md) | B5.1: `binaural_headtracker.eq` — EqualizerAPO-faithful RBJ biquads (PK/LS/HS…), stateful streaming cascade (block==offline), per-channel/bus `ParametricEQ` with glitch-free live retune. Ported the semantics (not the heavy sibling pkg); byte-exact parity (0.0) with the sibling on all 70 of the owner's real filters. |
| 15 | [Baked-BRIR interpolation](15-baked-brir-interpolation.md) | B1.7: `baked_interp` — synthesise a BRIR at any azimuth over the owner's 8 fixed speakers by bracketing + direct/tail split (ITD-resynth & blend the direct, hold the diffuse tail). Single clean phantom across the back gap, unlike amplitude panning. Rotating snare demo regenerated through it. |
| 16 | [Judge-model registry](16-judge-model-registry.md) | B7.0: a shared registry contract so we always pick the sibling's *best validated* judge (no guessing which `saved_model/*.keras`). `judge_registry` (pure-python) reads `../binaural-localization/models/registry/` → `best_judge()` with metadata/metrics. Sibling owns publishing (delegated); this is the consumer. |
| 17 | [Interpolation method & the back gap](17-interpolation-method-and-the-back-gap.md) | B1.10: evaluated timealign/minphase/notchwarp 4 ways; picked notchwarp on a *generic* judge + partial ear test. **Superseded by entry 18.** Standing finding: the 90° back gap is broken for all methods (owner's rear BRIRs front/back-ambiguous) — a data problem. |
| 18 | [Nearest default, no flips](18-nearest-default-no-flips.md) | B1.12: the mixed-rig judge `pooled5` (dense cue-decomposed map) **and** the ear agree — **`nearest` real BRIR wins** (25° vs 43/51°, best on the trustworthy lateral axis too). Synthesis methods flip front↔back (the worst error); nearest only drifts in azimuth. Default → `nearest`; smooth rotation is the render-layer crossfade. Reverses entry 17's default. |
| 19 | [Head-pose tracker](19-head-tracker.md) | B2.1: `binaural_headtracker.tracking` — 1€ filter (latency/jitter knobs), per-axis `PoseFilter`, threaded `HeadTracker` over an abstract pose source (recorded-clip in tests, MediaPipe webcam in the host spike), publishing a smoothed latest pose + rate/jitter stats. 14 headless tests, no real sleeps. The `latest()` seam is where B3.1 plugs in. |

See also [`LEARNINGS.md`](LEARNINGS.md) — the cumulative, numbered lessons.

## Maintenance protocol (the contract)

After every block or decision: extend the linear record with a new numbered entry
(never rewrite history to hide a wrong turn), update this index's status table and
"journal, in order", keep the frontier honest, and add durable lessons to
`LEARNINGS.md`. Entry template: **Question · Context · Hypothesis · Method ·
Result · Interpretation · Learnings · Decision/next.** One variable at a time;
hypothesis before result; record negative results; tie conclusions to evidence.

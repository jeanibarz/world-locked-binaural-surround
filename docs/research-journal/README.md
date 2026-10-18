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
| M2 | Head-pose tracker (camera → smoothed az/el/roll) | ● done (B2.1 tracker+1€, B2.2 calibration; axis signs host-confirmed) |
| M3 | World-lock: tracker ↔ render, world-fixed speaker | ◑ in progress (B3.1 live core + host app done, click-free; B3.2 A/B capture + host listening remain) |
| M4 | End-to-end on Windows (virtual device → headphone) | ◑ in progress (B4.1 passthrough **validated live** — drop-free 7.1, correct routing; render on a worker thread. B4.2 camera-loss done, device-change remains; B4.3 non-directional LFE + B5.3 comp/EQ render-quality follow-ups done) |
| M5 | Live EQ filters & presets | ◑ in progress (B5.1 EQ engine + B5.3 output-bus comp+EQ done; B5.2 presets remain) |
| M6 | GUI | ◑ in progress (B6.1 calibration console; B6.2 device pickers + host bring-up, validated live) |
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
| 20 | [Calibration: camera→world](20-calibration-camera-to-world.md) | B2.2: `calibrate` — neutral "screen-centre" pose + the deferred Euler→world-rotation mapping (yaw→+z, pitch→+y, roll→+x), so `head_rotation(cal,pose)` feeds `world.head_relative_directions`. Verified: neutral→identity→nominal layout; +30° yaw ⇒ every az −30° (world-lock) through a calibrated pose. Camera signs host-confirmed. M2 complete. |
| 21 | [Calibration console GUI](21-calibration-gui.md) | B6.1 (first GUI slice): a VR-style OpenCV console — live camera + face mesh + pose HUD beside a top-down world-locked azimuth radar (nearest speaker highlighted), **C** to recenter. Portable `radar` geometry (5 tests) + a host-only cv2 shell composed from the tracker/calibrate/world. The live proof of world-lock before audio. |
| 22 | [Live world-lock](22-live-world-lock.md) | B3.1: tracker→render. A single world-fixed source (the snare loop) steered head-relative through the **nearest** BRIR (journal 18), each BRIR a persistent convolver *voice*; on a flip the source crossfades **into** the new voice while the old rings out — click-free (worst sample-step down to the signal's own slew, ~3.8× below a hard switch; 1 voice steady, 2 in transition). `live_render` core (16 tests) + a host `sounddevice`+camera demo with `--record`. World-lock geometry reused from calibrate/world. M4 does the Windows routing. |
| 23 | [Device pickers + host bring-up](23-device-pickers-and-host-bringup.md) | B6.2: camera + audio-output pickers (tested `devices` logic + a Tk pre-launch menu) for both host GUIs, and the **first live host run** of the world-lock — validated by the owner. Host testing caught two real bugs headless tests missed: the audio callback aborted on WASAPI's variable buffer (fixed: render into a pending buffer, surface errors), and the mirror-before-detect **inverted yaw** (fixed: estimate pose on the un-mirrored frame; plus a live **F** flip toggle). AGENTS.md now documents the host-launch recipe. Next: M4. |
| 24 | [7.1 passthrough](24-voicemeeter-passthrough.md) | B4.1: world-lock **any** system audio — capture the virtual 7.1 device → render → headphones. Two discoveries: capture via **WASAPI loopback** (`pyaudiowpatch`), not VoiceMeeter's output bus (it downmixes to stereo, losing centre/LFE/surrounds); and the full 8ch × 500 ms-BRIR render is ~0.97× RT on the CPU, so it needs the **GPU**. The world-lock (`WorldLocked7p1`, generalising B3.1) is reformulated as a **bus mix** into fixed-filter buses (`BusMixer`/`bus_targets`), **proven identical to the voice pool offline to 1e-9** — so only a batched partitioned convolution is host-only (`gpu_render`, CuPy, ~6× RT). Two clocks bridged by a primed bounded FIFO. 6 new tests (278 total). Camera-loss degradation landed too (B4.2 partial). **Validated live on the host**: moving the render off the audio callback onto a **worker thread** (it was collapsing throughput to ~31 kHz and dropping) gives drop-free 48 kHz; loopback diagnostics (`spikes/b4_1_loopback_diag.py` + the `audio-loopback-diagnostics` skill) confirmed correct channel order and traced "no bass" to sub-bass-dead BRIRs (→ B4.3 LFE, B5.3 comp/EQ). |
| 25 | [Output-bus comp + EQ](25-output-bus-comp-eq.md) | B5.3: the bare bake sounds thin because it omits the owner's headphone comp + subjective EQ — which EqualizerAPO applies as a **global per-ear chain on the stereo mix**, not per-BRIR. New `output_eq` reads an EqualizerAPO config, isolates that post-downmix L/R chain (gain + comp FIR + biquads, reusing `convolve`/`eq`), and applies it to the output via `--output-eq`. The renderer's neutral output already equals the EqualizerAPO downmix, so *render + output EQ* reproduces the full config — **proven bit-exact (1.5e-14) against the sibling's reconstruction** across all 8 channels/2 ears. Pairs with `--lfe-mode brir`. 7 new tests (285 total). |

See also [`LEARNINGS.md`](LEARNINGS.md) — the cumulative, numbered lessons.

## Maintenance protocol (the contract)

After every block or decision: extend the linear record with a new numbered entry
(never rewrite history to hide a wrong turn), update this index's status table and
"journal, in order", keep the frontier honest, and add durable lessons to
`LEARNINGS.md`. Entry template: **Question · Context · Hypothesis · Method ·
Result · Interpretation · Learnings · Decision/next.** One variable at a time;
hypothesis before result; record negative results; tie conclusions to evidence.

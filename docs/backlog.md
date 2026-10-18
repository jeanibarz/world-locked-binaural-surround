# Backlog — ordered work blocks

This is the **queue** the autonomous chain works from. The *next eligible block*
is the first unchecked `[ ]` whose dependencies are checked. Keep it honest:
check a block only when it is done, recorded in the journal, and committed. Add
follow-ups and newly-discovered blocks as you go. Split any block too big for one
sitting and leave the remainder unchecked.

Legend: `[ ]` todo · `[~]` in progress (slice done, more remains) · `[x]` done.

## M0 — Foundations

- [x] Repo, README, requirements, AGENTS contract, journal skeleton, backlog
- [x] **B0.1** Choose the dev language/runtime for the Python prototype; set up
  the project (pyproject/venv), linting, and a trivial passing test. Record the
  stack decision in the journal. _(uv + src/ + pytest + ruff; see journal 01.)_
- [x] **B0.2** Audio I/O spike: enumerate Windows audio devices and open a
  low-latency stereo output + a multichannel input via WASAPI (`sounddevice` or
  equivalent). Measure round-trip latency. Record findings. _(Real WASAPI from
  WSL via interop; enumerate ✓, stereo out ✓, multichannel in ✓, ~22 ms reported
  latency. A measured round-trip is deferred to B0.3 (needs a virtual loopback);
  see journal 02.)_
- [x] **B0.3** Virtual-device spike: confirm a path to a virtual 7.1 sink
  (VB-Audio / loopback) and capture its stream. Document setup steps for users.
  **Also expose a clean render→capture loopback so B0.2's measured round-trip
  latency can be completed** (VoiceMeeter VAIO→B-bus, or a dedicated VB-CABLE).
  _(VoiceMeeter VAIO = 8-ch 7.1 sink ✓; captured its `VoiceMeeter Output` stream
  (22 ms in) ✓; name/channel role-resolution helpers + Windows spike + user setup
  doc ✓; loopback harness auto-pairs sink→capture ✓. A **measured** round-trip is
  gated on a one-time user routing step (VAIO→B1) or a VB-CABLE install → B0.6.
  See journal 03.)_
- [x] **B0.4** Head-tracker spike: get MediaPipe (or chosen model) producing a
  head yaw/pitch/roll estimate from a webcam frame; measure inference latency.
  _(MediaPipe FaceLandmarker → yaw/pitch/roll via its facial-transformation
  matrix; yaw tracked a smooth −52°→+57° sweep; inference ~12 ms (p95 15), ~3 ms
  idle — well under the <50 ms budget. Reliable to ~±50° yaw, loses the face past
  it → **operating range set to ±45°** (requirements FR-4). The webcam (C270,
  ~15 fps) is the real latency bottleneck → B0.7. See journal 05.)_

## M1 — Static binaural render engine

- [x] **B1.1** HRIR loader: read a SOFA set, expose directions + ipsi/contra
  IRs, resample to the engine rate. Unit-tested on a sample SOFA.
  _(`binaural_headtracker.hrir`: h5py SOFA read → `HRIRSet` (directions, L/R IRs,
  nearest lookup, ipsi/contra, polyphase resample). 12 tests on a synthetic SOFA +
  pure math; validated on MIT KEMAR (710 dirs, ipsi/contra 4:1 head shadow). h5py
  added to baseline. See journal 06.)_
- [x] **B1.2** Direction interpolation: given (az,el), return an interpolated
  HRIR (nearest → bilinear/VBAP, ITD-aware). Tested against known directions.
  _(`binaural_headtracker.interpolate`: ITD-aware **time-alignment** interpolation
  — Hilbert-envelope delay estimate + FFT fractional shift + k-NN inverse-distance
  blend (`interpolate_hrir`, `HRIRSet.interpolated_irs`). 17 tests; exact at
  measured dirs; KEMAR leave-one-out LSD **2.50 vs 6.81 dB** naive, ITD smooth
  0→~35 smp. See journal 08.)_
- [x] **B1.3** Partitioned-FFT convolution engine: per-channel real-time convolve
  with overlap-save; correctness + latency test vs. `scipy.fftconvolve`.
  _(`binaural_headtracker.convolve`: `PartitionedConvolver` (uniformly-partitioned
  overlap-save, single channel) + `StereoConvolver` (C-in→stereo, live counterpart
  of B1.6's `render_7p1_to_stereo`) + `convolve_stream` helper. 38 tests; streamed
  output == `scipy.fftconvolve` to **5.7e-14**; latency = one block, no extra
  algorithmic delay; 7.1→stereo (200 ms BRIRs) **3.2×RT at block 256 (5.3 ms)**,
  1.0×RT at 128. Default prototype block = 256 @ 48 kHz. See journal 09.)_
- [x] **B1.4** 7.1 world layout model (screen-relative positions, configurable
  distance/scale) → per-channel (az,el) for a *fixed* head. Tested.
  _(`binaural_headtracker.world`: right-handed world frame (`+x`=front, `+y`=left,
  `+z`=up) so `az=atan2(y,x)`/`el=asin(z/r)` match the HRIR convention;
  `WorldLayout` holds 3-D positions, `head_relative_directions` is the full
  `Rᵀ(pᵢ−p_h)` projection defaulting to the fixed neutral head; `layout_7p1`
  builds the nominal ring (configurable distance/scale per FR-3). 25 tests;
  reproduces `SPEAKER_LAYOUT_7P1` exactly, a head yaw of +θ shifts every azimuth
  by −θ (the world-lock mechanic, unit-tested). Euler→R mapping left to B2.2/B3.1.
  See journal 10.)_
- [x] **B1.5** Wire B1.1–B1.4 into a static renderer: 7.1 PCM in → stereo out,
  offline-verified on a test signal (e.g. a known pan). No tracking yet.
  _(`binaural_headtracker.render`: `build_speaker_hrirs` projects each speaker via
  `layout.head_relative_directions()` and looks its direction up in the SOFA set
  (`HRIRSet.interpolated_irs`) → a `SpeakerHRIRBank` `(C,N)` IR bank;
  `.render`/`render_layout_to_stereo` convolve-and-sum offline (shared
  `surround.convolve_and_sum_stereo` kernel), `.streaming_convolver` hands the
  bank to the B1.3 engine. 15 tests; offline render **== the B1.3 `StereoConvolver`
  stream to 1e-9**, impulse-per-channel reproduces that speaker's HRIR, ITD/ILD
  signs correct, +30° head yaw swings the front speaker to −30° (the world-lock
  seam, ready for M3). With B1.6 this makes M1's static engine complete for both
  the generic-SOFA and baked-BRIR paths. See journal 11.)_
- [x] **B1.6** Static 7.1 → binaural renderer from the owner's **baked BRIR set**
  (8 fixed-direction Audeze LCD-GX BRIRs): per-channel convolve + per-ear sum,
  offline. `binaural_headtracker.surround` (`BakedBRIRSet`, `load_baked_brir`,
  `render_7p1_to_stereo`, `SPEAKER_LAYOUT_7P1`); 9 tests; validated on the real
  set (per-speaker ITD signs all correct). The comp FIR + parametric EQ are the
  M5/B5 post-stage. _(owner request; see journal 07)_
- [x] **B1.7** Head-rotation interpolation over the owner's **8 fixed BRIRs**:
  approximate a rotated head by **ITD resynthesis + magnitude blending** between
  the nearest speaker BRIRs (time-alignment interpolation on the direct-path
  wavefront; room tail held fixed). Coarse with 8 points, but usable for modest
  turns — the owner's "vary ITD + blend signals" idea. Distinct from **B1.2** (the
  generic continuous-SOFA interpolation). _(owner request; see journal 07)_
  _(`binaural_headtracker.baked_interp.interpolate_baked_brir`: bracket az between
  the 2 adjacent ring speakers → direct/tail split → ITD-resynth + magnitude-blend
  the direct, hold the diffuse tail in place. 12 tests (exact at measured az, ITD
  interpolates + monotonic, tail held, single clean arrival across the back gap,
  wrap-around). Rotating-snare demo regenerated through it. See journal 15.)_
  **One of two levers on the "moving snare"** the owner heard — the wander is the
  undirected BRIR reverb tail; separating the direct-path wavefront from the held
  room tail attacks it at the source (and is needed anyway so the tail doesn't
  rotate with the head). The *other* lever is the comp+EQ chain (B5), which the
  owner's listening + a DRR measurement show also steadies the image by burying
  the diffuse tail under the direct sound. _(journal 12→13)_

## M2 — Head-pose tracker

- [x] **B2.1** Tracker interface + MediaPipe implementation: webcam → smoothed
  (yaw,pitch,roll) stream on its own thread. One-euro filter; expose latency/
  jitter knobs. Tested with a recorded clip.
  _(`binaural_headtracker.tracking`: `OneEuroFilter` (min_cutoff/beta knobs),
  per-axis `PoseFilter`, threaded `HeadTracker` over an abstract pose source
  (recorded-clip in tests, MediaPipe webcam in `spikes/b2_1_head_tracker.py`),
  publishing a smoothed `latest()` + rate/jitter `stats`. 14 headless tests, no
  real sleeps. The `latest()` seam is where B3.1 couples. See journal 19.)_
- [x] **B2.2** Calibration: define the neutral "looking at screen centre" pose;
  map camera frame → world/screen frame.
  _(`binaural_headtracker.calibrate`: `calibrate_neutral` (average "screen-centre"
  samples) + `head_rotation(cal, pose)` = `Rz(Δyaw)·Ry(Δpitch)·Rx(Δroll)` on
  per-axis deltas from neutral (yaw→world +z, pitch→+y, roll→+x) → feeds
  `world.head_relative_directions`. 18 tests: neutral→identity→nominal, +30° yaw ⇒
  az −30° (world-lock), pitch→elevation, sign flip. Per-axis signs host-confirmed.
  See journal 20.)_

## M3 — World-lock (the core effect)

- [x] **B3.1** Couple tracker → render: per frame compute head-frame speaker
  directions `Rᵀ(p_i−p_h)` and update the convolution click-free (crossfade).
  _(`binaural_headtracker.live_render`: a single **world-fixed** mono source steered
  head-relative through the **nearest** baked BRIR (journal 18), each BRIR a persistent
  `StereoConvolver` *voice*; on a nearest-BRIR flip the source is crossfaded **into** the
  new voice over `fade_ms` while the retired voice rings out its room tail — click-free
  with no spectral synthesis. World-lock geometry stays in the tested layer via a
  one-"speaker" `source_layout` + `calibrate.directions_for_pose`. 16 tests: LFE-excluded
  nearest selection + wrap, static source == plain BRIR convolution to 1e-9, transition
  reaches the new BRIR's exact steady state, crossfade cuts the worst sample-step ~3.8×
  (down to the signal's own slew = fully artifact-free), pose→azimuth world-lock sign.
  Host demo `apps/live_tracker_audio.py` (camera → tracker → renderer → `sounddevice`,
  loops the owner's snare, `--record` dumps the heard wav + az track for a reproducible
  A/B). Steady state = 1 voice, transitions peak at 2 (mean 1.34 over a full sweep).
  Windows/VoiceMeeter routing deferred to M4. See journal 22.)_
- [ ] **B3.2** Perceptual demo: a world-fixed virtual speaker that stays put when
  the head turns; capture a short A/B (head-locked vs world-locked) for review.
- [x] **B3.3** Live interpolation-method A/B in the snare demo.
  _(`live_render.interpolated_ring` builds a dense directional BRIR set (one BRIR every
  `--grid-step`° synthesized via `baked_interp`), and `WorldLockedSource` gained an `azimuths=`
  override so the voice pool steers over it. The snare demo's **M** key cycles nearest →
  timealign → minphase → notchwarp (synthesized on first use, 0.1–1.3 s, cached). Lets the owner
  re-evaluate the methods by ear with the corrected Dolby layout — esp. the wide 80° front→side
  gap; `nearest` stays the default. 3 tests (308 total). See journal 33.)_

## M4 — End-to-end on Windows

- [x] **B4.1** Live capture from the virtual 7.1 device → engine → chosen
  headphone output, running continuously without dropouts.
  _(`apps/voicemeeter_passthrough.py`: world-locks **any** system audio — point apps at a
  virtual 7.1 output (VoiceMeeter), capture that stream, world-lock every channel with the
  head pose, play stereo to the headphone card. Two discoveries drove the design (journal
  24): (a) **capture via WASAPI loopback** (`pyaudiowpatch`), not VoiceMeeter's output bus —
  the bus downmixes to stereo and loses centre/LFE/surrounds, so the loopback taps the raw
  pre-mix 7.1 on the render endpoint; (b) the full 8ch × 500 ms-BRIR render is **~0.97× RT on
  the CPU** in pure Python, so it needed the GPU _(superseded: the native Rust kernel does it on
  CPU at ~5× RT — the GPU is now optional; see B8.2)_. The world-lock itself is
  `live_render.WorldLocked7p1` —
  generalises B3.1's `WorldLockedSource` to 8 world-fixed speakers (each steered through its
  nearest BRIR, click-free crossfade; LFE non-directional, convolved through its own fixed
  BRIR). For the GPU it is reformulated as a **bus mix**: a time-varying crossfade of the 8
  inputs into 8 **fixed-filter buses** (`BusMixer` + `bus_targets`), **proven identical to the
  voice pool offline to 1e-9** over a head sweep — so only a batched partitioned convolution
  is host-only (`gpu_render.GpuBusConvolver`/`GpuWorldLocked7p1`, CuPy, ~6× RT at block 256,
  full tail). Capture and output run on independent clocks, bridged by a primed silence-cushion
  FIFO with bounded backlog. The render runs on a **worker thread**, not in the audio callback —
  live testing showed rendering inside the WASAPI capture callback collapses throughput to
  ~31 kHz and drops constantly (GIL + GPU sync starve the callbacks); the worker keeps the
  callbacks microsecond-light and sustains 48 kHz drop-free. 6 new tests (neutral == static
  render, LFE not steered, yaw steers a channel to the rotated BRIR, bus-mix == voice pool);
  278 total, ruff clean. **Validated live on the host**: continuous, drop-free 7.1 passthrough
  with correct channel routing (confirmed via the loopback diagnostics — `spikes/b4_1_loopback_diag.py`
  / the `audio-loopback-diagnostics` skill). Remaining "sounds wrong" is render quality, not B4.1
  plumbing: LFE loses its bass through a sub-bass-dead BRIR (→ B4.3) and the bare bake lacks the
  headphone comp+EQ chain (→ B5.3). See journal 24.)_
- [~] **B4.2** Graceful degradation: camera loss → freeze/fade; device change
  handling.
  _(Partial, landed with B4.1: the passthrough app already handles **camera loss** — hold the
  last head pose (audio keeps world-locking), keep the window + keys alive, and reopen the
  camera periodically rather than spin blind. Remaining: audio **device-change** handling
  (headphone/virtual-device hot-swap) and a deliberate fade. See journal 24.)_
- [x] **B4.3** Non-directional LFE: route the .1 channel flat to both ears, not convolved
  through the directional BRIR (the baked BRIRs roll off sub-bass ~−20 dB — journal 24 loopback
  diagnostics — so the pure-sub-bass LFE disappears when steered).
  _(`lfe_mode` on `WorldLocked7p1` + `GpuWorldLocked7p1`: `"flat"` sums LFE to both ears at a
  per-ear gain = that ear's LFE-IR L2 norm (loudness-matched to convolution, no spectral
  rolloff, no tail); `"brir"` keeps the old convolve-through-BRIR path (default in the renderer,
  == the static reference, so the neutral==static / lfe-not-steered tests still hold). The
  passthrough app defaults to `--lfe-mode flat` (it wants the bass) with `brir` to A/B. Measured
  +14 dB at 40 Hz (flat vs brir) on CPU and GPU; GPU flat == CPU flat to 1.7e-16. 1 new test
  (278 total). The offline static render is deliberately left as the EqualizerAPO reference.)_

## M5 — Live filters & presets

- [x] **B5.1** Per-channel + bus parametric EQ (biquads), changeable live without
  glitches (reuse EqualizerAPO semantics from the sibling project).
  _(`binaural_headtracker.eq`: RBJ-cookbook biquads (PK/LS/HS/LP/HP/NO/AP) +
  EqualizerAPO `Filter:` parser + stateful streaming `BiquadCascade` (block==offline)
  + per-channel/bus `ParametricEQ` with a glitch-free crossfade retune. **Ported** the
  sibling's semantics into a dep-light live block-processor rather than importing its
  offline/heavy package (the depend-vs-port fork, resolved; see journal 14). 20 tests;
  **byte-exact parity (0.0) with the sibling on all 70 of the owner's real filters**,
  so our EQ == EqualizerAPO. Load-bearing for the image, not cosmetic (journal 13).)_
- [x] **B5.3** **Full-chain render integration:** wire a per-ear comp FIR (a
  convolution — reuse `convolve`/`surround`) + the per-channel and bus
  `ParametricEQ` (B5.1) into the render path, so the static renderer emits the full
  comp+EQ-equivalent output (not the bare bake). Verify offline against the
  sibling's full-chain effective BRIR (the B5 spike already reconstructs it). This is
  what makes the renderer both *sound* and *localise* right — closes the "moving
  snare" loop (journal 13/14). _(discovered from journal 14)_
  _(`binaural_headtracker.output_eq`: reads an EqualizerAPO config, isolates the
  **global per-ear chain applied to the stereo mix after the downmix** (gain + comp
  FIR + comp/subjective biquads) and applies it as an output-bus stage — the BRIRs,
  and so the world-lock, are untouched. Wired into both host apps via `--output-eq`
  (pair with `--lfe-mode brir`). The renderer's neutral output == the EqualizerAPO
  downmix (existing test), so render+EQ reproduces the whole config: **bit-exact
  (1.5e-14) vs the sibling's `reconstruct_brir`** on the owner's `rev26_final` across
  all 8 channels/2 ears. 7 tests; 285 total. See journal 25.)_
- [ ] **B5.2** Preset save/load (layout + HRIR + EQ + tracker calib).

## M6 — GUI

- [~] **B6.1** Minimal control GUI: device pickers, layout editor, HRIR chooser,
  live EQ, tracker calibration.
  _(First slice done — the **tracker-calibration console**: `apps/calibration_gui.py`
  (host OpenCV), layout A chosen from a mockup — live camera + face mesh + pose HUD
  beside a world-locked azimuth radar (nearest speaker highlighted), `C` to recenter,
  `Q` to quit. Portable `binaural_headtracker.radar` geometry (5 tests). See journal 21.
  Remaining for B6.1: layout editor, HRIR chooser, live EQ controls.)_
- [x] **B6.2** Device pickers (camera + audio output) for the host GUIs, plus the
  live bring-up fixes found testing on the Windows host.
  _(`binaural_headtracker.devices`: tested pure selection logic — `output_device_choices`
  (filter to output-capable, label with host-API + channels, mark the default),
  `camera_choices`, `resolve_output_device` (index or name-substring → index, loud on a
  typo), `default_choice_label` — 8 tests; plus host-only enumeration (`probe_cameras`,
  `query_output_devices`) and a Tk pre-launch `settings_dialog` (lazy imports, CI-safe).
  Wired into both apps: `live_tracker_audio.py` (camera + audio), `calibration_gui.py`
  (camera). **Validated live on the host** — the owner ran the live world-lock end-to-end
  (snare stays fixed when turning). Host fixes in the same block: (a) audio callback made
  robust to WASAPI's variable buffer size (render fixed blocks into a pending buffer; surface
  callback errors) — "picture but no sound" was the stream aborting; (b) estimate the head
  pose from the **un-mirrored** frame (mirror only the display) so yaw keeps its real sign —
  the mirror had inverted it, making the source track the head instead of the world. AGENTS.md
  now documents how to launch the host GUIs (interop, `sys.path` injection, local-`C:` model
  copy). See journal 23.)_
- [x] **B6.3** VR-console refresh to the calibration mockup (layout A) + a toggleable real-time
  **diagnostics panel**.
  _(Shared `apps/_console.py` OpenCV renderer (titlebar + pulsing live pill, camera HUD + face
  box, azimuth radar with FOV cone/halo/dashed pointer/legend, controls bar, stats overlay) used
  by both `calibration_gui.py` and `voicemeeter_passthrough.py`. New tested
  `binaural_headtracker.diagnostics.classify` turns live numbers (render ms, FIFO fill, device
  latencies, tracker rate, recent underruns) into render-load, an end-to-end **head-move→ear
  latency estimate**, and a plain verdict (healthy/tight/at risk/failing) with reasons. The
  passthrough is instrumented (per-block render time EWMA+peak; underrun timestamps) with the
  audio callbacks kept light; **S** toggles the panel. Verified via host PNG renders. 9 tests
  (301 total). See journal 30.)_
- [x] **B6.4** Camera-pipeline diagnostics + radar fixes from live use.
  _(Panel now shows camera `WxH @ fps`, detect ms, loop Hz and a plain `tracking_note`
  (`diagnostics.classify` gained `detect_ms`/`camera_fps`/`loop_ms`); `--cam-width/-height/-fps`
  flags (default no-op). Measured the owner's C270: 30 Hz is its native fps, detect ~3 ms
  (resolution-independent), the 15 fps was low-light auto-exposure — not USB (journal 31). Radar
  "active"→**"facing"** (7.1 plays all channels; no single active one), and `SPEAKER_LAYOUT_7P1`
  corrected to the **Dolby 7.1 angles** (side ±110°, rear ±150°) which drive the world-lock
  steering + radar and must match the baked BRIR directions (journal 32). 305 tests.)_
- [x] **B6.5** In-passthrough experiment layer + world-frame radar.
  _(Radar is world-frame: speakers fixed at their world azimuths, the head arrow + gaze cone
  rotate; `_console.radar_az_at` inverts the geometry for click-to-place. The passthrough gains a
  movable test source mixed into the live duplex (the 7.1 path is untouched when off): **T**
  toggle, **O** solo, **R** auto-rotate, **[ ]** level, **click** the radar to place, **M** cycle
  the interpolation method (dense grid, B3.3). Source = `--source` WAV or a built-in noise tick.
  Verified by PNG + offline mix; 308 tests. See journal 34.)_

## M7 — AI-judge calibration

- [x] **B7.0** Judge-model **registry** consumer: resolve the *best validated*
  trained judge the sibling publishes, without guessing which `saved_model/*.keras`
  is current. `binaural_headtracker.judge_registry` (pure-python, no TF) reads the
  shared registry (`../binaural-localization/models/registry/`, override via
  `$BHT_JUDGE_REGISTRY`): `best_judge()` → model path + metadata/metrics. Registry
  contract v1 (layout + metadata schema) is in the module docstring; the sibling
  publishes validated models there (delegated to its task to implement + train the
  pooled mixed-rig judge). 7 tests on a mock registry. _(owner request)_
- [ ] **B7.1** Integrate the `binaural-localization` synthetic-listener judge to
  score a candidate HRIR/EQ configuration. _(uses B7.0's `judge_registry.best_judge()`
  to pick the judge; the offline scoring harness runs in the sibling env.)_
- [ ] **B7.2** Guided blind A/B / staircase calibration harness; validate on the
  synthetic listener before the human.

## M8 — Latency hardening

- [x] **B8.1** Profile the Python hot path; decide the native stack (JUCE /
  PortAudio / Rust) via a small benchmark; port the convolution path.
  _(Profiled (journal 26): the convolution math is already 1.6–4.3× RT on CPU; the GPU was
  masking Python per-block overhead, not a compute wall. Also tightened the head-move→ear
  reaction latency — it's the output FIFO cushion, not the tracker: default `--latency-ms`
  120→60, `max_backlog` 4×→2× cushion, output `latency="low"`. Native stack chosen: **Rust +
  PyO3** (B8.2).)_
- [x] **B8.2** Native render kernel (Rust/PyO3) to drop the GPU dependency.
  _(`rust/` maturin crate `binaural_kernel`: the batched partitioned bus convolution
  (`GpuBusConvolver`'s CPU twin) in Rust — rustfft + rayon, allocation-free, multithreaded over
  the 8 buses — as an **optional accelerator** (importorskip on CI, not in the Python wheel).
  `rust_render.RustWorldLocked7p1` drops in (BusMixer + native convolver + LFE); app flag
  `--rust`. **Bit-exact (~1e-16)** vs the Python renderer; **~5× real time on CPU at block 256**
  (Python 0.4×), so the GPU is now optional and low-latency small blocks are viable. 7 tests
  (292 total). See journal 27. Follow-ups: pull BusMixer/LFE into the kernel; native low-latency
  duplex (replace the Python audio bridge). → issue #31._
- [x] **B8.3** Distributable cross-platform wheels for the kernel, **no paid CI**.
  _(abi3 (`abi3-py310`) → one wheel per platform for CPython 3.10+; cross-compiled locally from
  WSL: Linux native + **Windows via `cargo-xwin`** (`generate-import-lib` synthesises
  `python3.lib`). Windows `win_amd64` wheel installed + verified on the owner's host (~25× RT).
  macOS = build-on-Mac or the pure-Python fallback (not cross-buildable from Linux).
  `rust/build-wheels.sh`; launcher uses `--rust`. See journal 28.)_
- [ ] **B8.5** **Pose-age / motion→sound latency measurement harness.** Measure the
  true *pose age at the ear* as a **distribution (p50/p95/p99 + extremes, not a mean)**,
  via three measurements (requirements § 7.3): (a) **camera→app** — a known temporal
  signal (LED / on-screen timecode) in view vs the frame's software-availability
  timestamp, accounting for exposure window + rolling-shutter skew; (b) **per-stage
  compute** time (extend the existing `detect_ms` / per-block render EWMA); (c)
  **motion→sound** — an external (ideally motorised) rotation reference recorded with the
  audio output, effective applied orientation recovered by cross-correlation / system
  identification. Carries a pose **timestamp** end-to-end (the seam B8.6 needs). Decides
  whether the current C270 is already good enough before any camera spend. _(from journal
  36; feeds NFR-1 and B8.6.)_
- [ ] **B8.6** **Orientation prediction to the audio-render instant.** Estimate head
  orientation at the *future* instant the DSP renders, so the pose baked into a block
  matches the head when it is heard — a prediction stage between tracker and render. First
  cut: constant-angular-velocity quaternion extrapolation `q̂(t+Δt) = q(t) ⊗
  exp((Δt/2)·ω(t))` with a lightweight **α-β** / orientation+angular-velocity state filter
  (not a heavy model). Tracker stays 60 Hz; DSP re-evaluates the predicted pose per block,
  decoupling reactivity from the camera fps (our bottleneck). **Precautions:** limit the
  horizon + damp velocity so an abrupt stop doesn't overshoot (the ~1° static MAA,
  requirements § 7.1); filter `ω` jitter without re-adding latency. Depends on B8.5 for the
  real Δt and the pose timestamp, and on a validation that it measurably lowers effective
  lag without hurting the at-rest image. _(from journal 36; the main M8 lever after the
  cushion fix.)_

## M9 — Fine-tuning & release

- [ ] **B9.1** HRIR selection/fine-tuning toward morphology + headphone EQ.
- [ ] **B9.2** Packaging, installer, user docs, donationware release.

---

### Discovered blocks / follow-ups
_(the chain appends here as it learns)_

- [ ] **B0.5** Exclusive-mode low-latency audit: once the virtual chain can be
  bypassed (B0.3/B4), measure WASAPI exclusive-mode latency against a *hardware*
  endpoint (e.g. the FiiO K11 DAC) and confirm it beats the ~22–24 ms the
  VoiceMeeter virtual sink reports. Feeds the <30 ms budget (requirements § 2).
  _(from journal 02)_
- [x] **B0.6** Complete the **measured** virtual round-trip latency: once a
  loopback route exists — VoiceMeeter VAIO strip → bus **B1**, or a dedicated
  **VB-CABLE** installed — run `spikes/b0_3_virtual_device.py` and record the real
  cross-correlated round-trip (the harness is already in place; the number was
  gated on owner-side routing/install, not on code). _(from journal 03)_
  _(Measured **~130 ms** via an isolated AUX→B1 tap — essentially one 6144-sample
  (128 ms) driver buffer. Fixed a concurrent-capture bug in the spike (it recorded
  only after a blocking full-buffer write) and added `--out-device/--in-device`
  overrides. Owner declined to lower the live buffer, so a single over-buffered
  point stands; see journal 04.)_
- [ ] **B1.8** Barycentric / Delaunay-VBAP direction interpolation: replace the
  k-NN inverse-distance weighting (B1.2) with a triangulated-sphere barycentric
  blend (smoother across the grid, no third-neighbour pull past the two bracketing
  measurements). Only if grid artefacts show up under fast head motion in M3;
  B1.2's k-NN is sufficient for the MVP. _(from journal 08)_
- [ ] **B1.9** Vectorise the partitioned convolver: replace the per-partition and
  per-channel Python loops in `convolve.py` with stacked `H`/FDL tensordot MACs and
  FFTs batched across partitions and channels, to reclaim real-time headroom at a
  128-sample (2.67 ms) block in pure Python (currently only 1.0×RT for 8-ch
  7.1→stereo; block 256 already clears the § 7 budget). Only if M4 needs a sub-5 ms
  block before the M8 native port. _(from journal 09)_
- [x] **B1.10 / B1.12** Rotation over the baked set: `baked_interp` offers
  `nearest` (**default**) / `timealign` / `minphase` / `notchwarp`. B1.10 built the
  three synthesis methods; **B1.12 made `nearest` the default** — the mixed-rig judge
  `pooled5` (dense cue-decomposed map) *and* the owner's ear agree it localises best
  (25° vs 43/51°, best on the trustworthy lateral axis too), because synthesis methods
  flip front↔back (the worst error) while nearest only drifts in azimuth. Smooth
  rotation is the **render layer's** crossfade, not a synthesised in-between spectrum.
  _(journals 17→18; reverses the notchwarp default.)_
- [x] **B3.1 note** When the tracker→render coupling lands, it **must crossfade**
  between successive `nearest` BRIRs click-free as the head/source moves (the rotating
  demo proves the shape via Hann overlap-add). _(journal 18)_ **Done in B3.1** — the
  crossfade is on the input **gain** into persistent per-BRIR voices (retired voices
  drain their tail), measured click-free down to the signal's own slew. See journal 22.
- [ ] **B1.11** **Rear front/back is a data problem, not interpolation.** Journals
  17/18 (judge + sibling 18/19) showed the owner's measured RL/RR BRIRs read as
  front/back-ambiguous, so *no* rotation method (incl. nearest) fixes the back gap.
  Resolve via better rear cues or an HRTF **set** that resolves the owner's rears
  (sibling HRTF-selection track). Re-score with the owner's **mixed-rig judge**
  (`judge_registry.best_judge()`, B7.0). **This is the next real frontier.** _(journal 18)_
- [ ] **B0.7** Raise the head-tracking update rate / lower its frame age — **measure
  first, buy last** (requirements § 7.4). Detect is only ~3 ms and resolution-independent;
  the camera is the bottleneck (journals 05/31). Cheapest-first: (1) **optimise the C270 we
  have** — fix lighting / set **manual exposure + fixed focus** (journal 31 showed the
  15 fps was low-light auto-exposure, not USB; native is 30 fps), keep acquisition
  **decoupled + drop-latest** (newest frame, discard the stale queue); verify the achieved
  frame age with B8.5 rather than trusting `CAP_PROP_BUFFERSIZE=1` (not guaranteed under
  MSMF/DSHOW). (2) **Only if B8.5 shows the sensor dominates, a 60 fps *uncompressed*
  webcam** (manual exposure, no autofocus) for a *mastered* low-latency path — not a nominal
  90–120 fps one (period ≠ pipeline latency). (3) **A second camera only for angular
  coverage/occlusion** across the ±45° turns (FR-13), fused asynchronously by timestamp;
  hardware sync only if async fusion is insufficient. Also worth a bench: a minimal
  `solvePnP` orientation estimator vs MediaPipe if only yaw/pitch/roll matters. _(from
  journals 05/31/36)_

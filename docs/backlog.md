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
- [ ] **B1.3** Partitioned-FFT convolution engine: per-channel real-time convolve
  with overlap-save; correctness + latency test vs. `scipy.fftconvolve`.
- [ ] **B1.4** 7.1 world layout model (screen-relative positions, configurable
  distance/scale) → per-channel (az,el) for a *fixed* head. Tested.
- [ ] **B1.5** Wire B1.1–B1.4 into a static renderer: 7.1 PCM in → stereo out,
  offline-verified on a test signal (e.g. a known pan). No tracking yet.
  _(Note: a static renderer already exists for the **owner's baked BRIR set** via
  B1.6; B1.5 remains for the **generic SOFA** path through B1.1–B1.4.)_
- [x] **B1.6** Static 7.1 → binaural renderer from the owner's **baked BRIR set**
  (8 fixed-direction Audeze LCD-GX BRIRs): per-channel convolve + per-ear sum,
  offline. `binaural_headtracker.surround` (`BakedBRIRSet`, `load_baked_brir`,
  `render_7p1_to_stereo`, `SPEAKER_LAYOUT_7P1`); 9 tests; validated on the real
  set (per-speaker ITD signs all correct). The comp FIR + parametric EQ are the
  M5/B5 post-stage. _(owner request; see journal 07)_
- [ ] **B1.7** Head-rotation interpolation over the owner's **8 fixed BRIRs**:
  approximate a rotated head by **ITD resynthesis + magnitude blending** between
  the nearest speaker BRIRs (time-alignment interpolation on the direct-path
  wavefront; room tail held fixed). Coarse with 8 points, but usable for modest
  turns — the owner's "vary ITD + blend signals" idea. Distinct from **B1.2** (the
  generic continuous-SOFA interpolation). _(owner request; see journal 07)_

## M2 — Head-pose tracker

- [ ] **B2.1** Tracker interface + MediaPipe implementation: webcam → smoothed
  (yaw,pitch,roll) stream on its own thread. One-euro filter; expose latency/
  jitter knobs. Tested with a recorded clip.
- [ ] **B2.2** Calibration: define the neutral "looking at screen centre" pose;
  map camera frame → world/screen frame.

## M3 — World-lock (the core effect)

- [ ] **B3.1** Couple tracker → render: per frame compute head-frame speaker
  directions `Rᵀ(p_i−p_h)` and update the convolution click-free (crossfade).
- [ ] **B3.2** Perceptual demo: a world-fixed virtual speaker that stays put when
  the head turns; capture a short A/B (head-locked vs world-locked) for review.

## M4 — End-to-end on Windows

- [ ] **B4.1** Live capture from the virtual 7.1 device → engine → chosen
  headphone output, running continuously without dropouts.
- [ ] **B4.2** Graceful degradation: camera loss → freeze/fade; device change
  handling.

## M5 — Live filters & presets

- [ ] **B5.1** Per-channel + bus parametric EQ (biquads), changeable live without
  glitches (reuse EqualizerAPO semantics from the sibling project).
- [ ] **B5.2** Preset save/load (layout + HRIR + EQ + tracker calib).

## M6 — GUI

- [ ] **B6.1** Minimal control GUI: device pickers, layout editor, HRIR chooser,
  live EQ, tracker calibration.

## M7 — AI-judge calibration

- [ ] **B7.1** Integrate the `binaural-localization` synthetic-listener judge to
  score a candidate HRIR/EQ configuration.
- [ ] **B7.2** Guided blind A/B / staircase calibration harness; validate on the
  synthetic listener before the human.

## M8 — Latency hardening

- [ ] **B8.1** Profile the Python hot path; decide the native stack (JUCE /
  PortAudio / Rust) via a small benchmark; port the convolution path.

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
- [ ] **B0.7** Raise the head-tracking update rate: the C270 gives ~15 fps @
  640×480 (~66 ms/frame), which dominates the <50 ms tracking budget while
  inference is only ~12 ms (journal 05). Evaluate MJPG fourcc / lower resolution /
  a faster camera, and measure the achievable pose update rate. _(from journal 05)_

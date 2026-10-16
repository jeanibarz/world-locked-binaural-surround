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
- [ ] **B1.7** Head-rotation interpolation over the owner's **8 fixed BRIRs**:
  approximate a rotated head by **ITD resynthesis + magnitude blending** between
  the nearest speaker BRIRs (time-alignment interpolation on the direct-path
  wavefront; room tail held fixed). Coarse with 8 points, but usable for modest
  turns — the owner's "vary ITD + blend signals" idea. Distinct from **B1.2** (the
  generic continuous-SOFA interpolation). _(owner request; see journal 07)_
  **One of two levers on the "moving snare"** the owner heard — the wander is the
  undirected BRIR reverb tail; separating the direct-path wavefront from the held
  room tail attacks it at the source (and is needed anyway so the tail doesn't
  rotate with the head). The *other* lever is the comp+EQ chain (B5), which the
  owner's listening + a DRR measurement show also steadies the image by burying
  the diffuse tail under the direct sound. _(journal 12→13)_

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

- [x] **B5.1** Per-channel + bus parametric EQ (biquads), changeable live without
  glitches (reuse EqualizerAPO semantics from the sibling project).
  _(`binaural_headtracker.eq`: RBJ-cookbook biquads (PK/LS/HS/LP/HP/NO/AP) +
  EqualizerAPO `Filter:` parser + stateful streaming `BiquadCascade` (block==offline)
  + per-channel/bus `ParametricEQ` with a glitch-free crossfade retune. **Ported** the
  sibling's semantics into a dep-light live block-processor rather than importing its
  offline/heavy package (the depend-vs-port fork, resolved; see journal 14). 20 tests;
  **byte-exact parity (0.0) with the sibling on all 70 of the owner's real filters**,
  so our EQ == EqualizerAPO. Load-bearing for the image, not cosmetic (journal 13).)_
- [ ] **B5.3** **Full-chain render integration:** wire a per-ear comp FIR (a
  convolution — reuse `convolve`/`surround`) + the per-channel and bus
  `ParametricEQ` (B5.1) into the render path, so the static renderer emits the full
  comp+EQ-equivalent output (not the bare bake). Verify offline against the
  sibling's full-chain effective BRIR (the B5 spike already reconstructs it). This is
  what makes the renderer both *sound* and *localise* right — closes the "moving
  snare" loop (journal 13/14). _(discovered from journal 14)_
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
- [ ] **B1.9** Vectorise the partitioned convolver: replace the per-partition and
  per-channel Python loops in `convolve.py` with stacked `H`/FDL tensordot MACs and
  FFTs batched across partitions and channels, to reclaim real-time headroom at a
  128-sample (2.67 ms) block in pure Python (currently only 1.0×RT for 8-ch
  7.1→stereo; block 256 already clears the § 7 budget). Only if M4 needs a sub-5 ms
  block before the M8 native port. _(from journal 09)_
- [ ] **B0.7** Raise the head-tracking update rate: the C270 gives ~15 fps @
  640×480 (~66 ms/frame), which dominates the <50 ms tracking budget while
  inference is only ~12 ms (journal 05). Evaluate MJPG fourcc / lower resolution /
  a faster camera, and measure the achievable pose update rate. _(from journal 05)_

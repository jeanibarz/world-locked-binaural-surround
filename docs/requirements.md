# Requirements & architecture

Living design document. The autonomous build chain keeps this current; when a
decision changes, update the relevant section and record why in the research
journal.

---

## 1. Vision & goals

Build a Windows application that renders any app's surround audio to headphones
as a **world-locked, head-tracked binaural** stage, using an individual (or
AI-fine-tuned) HRIR/BRIR set, with **minimal end-to-end latency** so the illusion
is stable and convincing.

**Primary goal (the perceptual hypothesis).** Keeping the virtual speakers fixed
in the world while the head moves — so head rotation changes the convolution, not
the scene — gives the brain the dynamic cue it uses in real listening, improving
**externalisation** and **front/back / elevation** perception beyond static
binaural.

**Secondary goal (the product).** A tunable, calibratable chain (parametric EQ,
HRIR selection/fine-tuning) that a non-expert can dial in — ideally with an
**AI judge** (sibling project `binaural-localization`) scoring changes
objectively instead of by unreliable ear.

## 2. Users & use cases

- A headphone listener who wants convincing surround for games, films, music on
  Windows, without rotating-with-the-head artefacts.
- An enthusiast (the owner) who has his own measured in-ear BRIRs and hand-tuned
  EqualizerAPO chain, and wants that chain to become head-tracked and
  objectively calibratable.
- (Later) users who do not have measured HRTFs and rely on **AI selection /
  fine-tuning** of a public HRIR to fit their morphology and their headphone EQ.

## 3. Functional requirements

Numbered `FR-n`. MoSCoW priority in brackets.

- **FR-1 [Must]** Capture a multichannel (5.1 / 7.1, configurable) audio stream
  produced by other Windows applications.
- **FR-2 [Must]** Provide the capture source as a **virtual Windows audio
  output device** that any application can select as its speaker output (the
  "VoiceMeeter-like" sink). The device advertises a configurable channel count
  (stereo / 5.1 / 7.1).
- **FR-3 [Must]** Define a **virtual speaker layout** in a screen-relative world
  frame: each channel maps to a fixed 3-D position relative to the screen
  (e.g. FC = centre of screen, 1 m behind; FL/FR at screen edges at 1 m; SL/SR,
  RL/RR arranged around the listener). Per-speaker distance and the overall
  scale are **user-configurable**.
- **FR-4 [Must]** Track the listener's **head pose** — orientation (yaw/azimuth,
  pitch/elevation, ideally roll) and, if feasible, translation — from one
  (optionally several) camera(s) facing the listener, in real time.
- **FR-5 [Must]** For each virtual speaker, compute its direction **relative to
  the current head pose** and render it binaurally by convolving its signal with
  the HRIR/BRIR for that relative direction (interpolated between measured
  directions). Sum all speakers per ear → stereo.
- **FR-6 [Must]** Output the stereo mix to **any user-selectable headphone
  output device** (any sound card), at low latency.
- **FR-7 [Must]** Load HRIR/BRIR sets from **SOFA** files; support the owner's
  own measured BRIRs. Allow **choosing** among sets.
- **FR-8 [Should]** Apply **on-the-fly adjustable filters** — parametric EQ,
  shelves, gain, delay per channel and on the stereo bus — mirroring an
  EqualizerAPO-style chain, changeable live without audio dropouts.
- **FR-9 [Should]** **GUI** to: pick devices, position/scale virtual speakers,
  choose/swap HRIR sets, edit the EQ chain live, calibrate head-tracker, and
  run guided calibration sessions.
- **FR-10 [Should]** **AI judge-in-the-loop calibration**: score a candidate
  HRIR/EQ configuration with the synthetic-listener model from
  `binaural-localization`, and iterate (manual A/B or guided blind test) toward a
  better-localising configuration. Validate the test harness on the synthetic
  listener before asking the human.
- **FR-11 [Could]** **AI fine-tuning of the HRIR** toward the user's morphology
  and/or to match their current headphone EQ calibration (DTF-style
  normalisation, per-direction correction).
- **FR-12 [Could]** Save/load named presets (layout + HRIR + EQ + tracker calib).
- **FR-13 [Could]** Support **multiple cameras** for more robust/large-range head
  tracking.
- **FR-14 [Won't, v1]** Room acoustics simulation beyond the chosen BRIR; 6-DoF
  positional audio for VR. Out of scope for the first product.

## 4. Non-functional requirements

- **NFR-1 Latency [Must].** End-to-end audio latency (virtual-device input →
  headphone output) target **< 20–30 ms**; head-tracking-to-audio update latency
  target **< ~50 ms** and smooth (no zipper noise on pose change). These are the
  make-or-break numbers for the illusion.
- **NFR-2 Platform [Must].** Windows 10/11, x64. Real-time audio via WASAPI
  (shared or exclusive) / ASIO; consider low-latency paths.
- **NFR-3 Audio quality [Must].** No dropouts/glitches under normal CPU load;
  click-free HRIR switching (crossfade / commutation); 48 kHz baseline.
- **NFR-4 CPU/GPU budget [Should].** Convolution + tracking must run comfortably
  on a typical gaming PC alongside the game. Partitioned FFT convolution; tracker
  on a separate thread/GPU.
- **NFR-5 Reliability [Should].** Degrade gracefully if the camera is lost
  (freeze last pose / fade to static binaural), and if a device disappears.
- **NFR-6 Configurability [Should].** Everything perceptually relevant is
  user-adjustable and persisted.
- **NFR-7 Privacy [Must].** The camera feed is processed locally; no video leaves
  the machine. State this clearly to users.

## 5. Architecture

Data flow (one-way, real-time audio path kept lock-free and allocation-free):

```
 Apps (game/film) ──select output──▶  [Virtual 7.1 Audio Device]  (FR-2)
                                               │  multichannel PCM
                                               ▼
                                    ┌──────────────────────┐
  Camera(s) ──▶ [Head-Pose Tracker]│   Render Engine        │
     (FR-4)        │ yaw/pitch/roll │  - per-speaker dir vs  │
                   │  + position    │    head pose (FR-5)    │
                   └──── pose ──────▶  - HRIR interp + part.  │
                                     │    FFT convolution     │
                     HRIR/BRIR sets ─▶  - per-channel EQ (FR-8)│
                        (SOFA, FR-7) │  - sum → stereo        │
                                     └─────────┬──────────────┘
                                               ▼
                                    [Headphone Output Device] (FR-6)
                                               ▲
                            [GUI / Config / Presets] (FR-9) ── controls all blocks
                            [AI Judge / Calibration] (FR-10/11) ── offline + guided
```

**Components**

1. **Virtual audio device (FR-2).** The hardest-to-build native piece. Options,
   cheapest-integration first:
   - *Reuse an existing virtual device* (VB-Audio VoiceMeeter / VB-CABLE / Hi-Fi
     Cable, or an EqualizerAPO-driven device) as the 7.1 sink, and **WASAPI
     loopback-capture** it. Fastest path to an MVP; ship instructions + bundling.
   - *Our own virtual driver.* A user-mode **APO** is not a full virtual endpoint;
     a true virtual sound card needs a driver. Candidates: a **Virtual Audio
     Device driver** based on Microsoft's SysVAD sample / `VirtualAudioDriver`,
     or a Windows **Audio Processing Object** chained to a loopback. This is a
     stretch goal; the MVP must not block on it.
2. **Head-pose tracker (FR-4).** Camera → 6-DoF (or 3-DoF orientation) head pose.
   - MVP: a dedicated, low-latency vision model — **MediaPipe Face
     Landmarker / Face Mesh** (head pose from landmarks, runs real-time on
     CPU/GPU), or a light face-pose net (e.g. SixDRepNet/WHENet-class). A
     **large multimodal "LLM" is the wrong tool for the real-time loop** (too
     slow/expensive); reserve any LLM for setup/calibration UX, not per-frame
     tracking. Keep the tracker pluggable behind an interface.
   - Smoothing (one-euro filter) to trade latency vs. jitter; expose the knob.
3. **Render engine (FR-5, FR-8).** Real-time core:
   - World model: virtual speaker positions (screen-relative), listener pose.
   - Per frame: speaker direction in head frame = `R(pose)^{-1} · (p_speaker −
     p_head)`; look up/interpolate HRIR for that (az,el); partitioned-FFT
     convolve the channel; apply per-channel EQ; sum L/R.
   - Click-free HRIR update (crossfade overlapping convolutions, or
     time-domain commutation of partitions).
4. **DSP / filters (FR-8).** Biquad parametric EQ, shelves, gains, delays —
   reuse the EqualizerAPO semantics (the sibling project already models this).
5. **HRIR management (FR-7, FR-11).** SOFA load, resample, DTF normalisation,
   per-direction interpolation, selection/fine-tuning.
6. **GUI (FR-9)** and **AI judge/calibration (FR-10/11)** — control + offline.

**Tech choice (sequenced).** Prototype the **render engine + tracker + world-lock
math in Python** (fast iteration: `sounddevice`/WASAPI, `numpy`/`scipy` or a
partitioned-convolution lib, MediaPipe, `python-sofa`/`sofar`). Prove the
perceptual loop end-to-end at relaxed latency. **Then** move the hot audio path to
**native (C++ with JUCE or PortAudio/WASAPI, or Rust)** for NFR-1. Keep a clean
boundary so the Python prototype and native engine share config, HRIR handling,
and the world model. This matches the sibling project's "prototype then harden"
discipline and de-risks latency without blocking early perceptual validation.

## 6. Core rendering math (the world-lock)

Let the virtual speaker `i` sit at world position `p_i` (from the screen-relative
layout, FR-3). The head has position `p_h` and orientation `R` (rotation from head
frame to world) from the tracker. The speaker's direction **in the head frame** is

```
d_i = normalize( Rᵀ · (p_i − p_h) )     →  (azimuth, elevation)
```

Render speaker `i` by convolving its channel with `HRIR(az_i, el_i)` (bilinear /
VBAP-style interpolation between measured directions, magnitude+ITD aware), per
ear, and sum over `i`. Because `p_i` is fixed and `R` tracks the head, **turning
the head rotates `d_i`**, so the convolution follows the head while the scene
stays world-fixed — the core effect. Optionally model distance (gain ∝ 1/r,
air-absorption, near-field) from `|p_i − p_h|`.

## 7. Latency budget (target, MVP → native)

| stage | MVP (Python) | target (native) |
|---|---|---|
| virtual-device capture buffer | 10–20 ms | 3–10 ms |
| convolution block | 5–10 ms | 1–5 ms |
| output buffer | 10–20 ms | 3–10 ms |
| **audio end-to-end** | ~30–50 ms | **< 20–30 ms** |
| camera + pose inference | 20–40 ms | 10–25 ms |
| pose → audio apply (smoothed) | — | < ~50 ms total |

## 8. Prior art / competitive landscape (FR context)

- **Smyth Realiser A16** — closest: measured personalised BRIR, head-tracked,
  virtual speakers. Dedicated **hardware**, ~thousands of €. We are software +
  AI calibration.
- **Waves Nx** — head-tracked binaural (camera/Bluetooth tracker), generic HRTF,
  no individual-BRIR capture, no virtual 7.1 device, no AI calibration.
- **Sony 360 Reality Audio, Dolby Atmos for Headphones, Apple Spatial Audio** —
  head-tracked spatial, but closed, generic HRTF, content/ecosystem-bound.
- **Embody Immerse, Genelec Aural ID** — personalised HRTF (photo/scan), no
  world-locked virtual-device pipeline on Windows.
- **Steam Audio, Sound Particles, SPARTA/SADIE, Mysphere** — SDKs / tools for
  developers or research, not an end-user Windows virtual-device product.
- **EqualizerAPO + HeSuVi** — static binaural surround on Windows (what the owner
  uses today). No head tracking, no world-lock, subjective tuning only.

**Gap we fill:** individual/AI-tuned HRIR **+** camera head-tracking **+**
world-locked virtual 7.1 device on Windows **+** live EQ **+** AI-judge
calibration — as one integrated, low-cost product. No listed system does all of
these together.

## 9. Licensing & business

Intended **donationware**, with a possible paid tier around **AI HRIR/filter
calibration**. Therefore **no open-source licence is applied yet** (all rights
reserved pending a decision). Keep third-party dependencies licence-compatible
with a future closed/donationware release (watch GPL components; prefer
MIT/BSD/Apache and permissively-licensed HRTF datasets). Record every dependency
and its licence in the journal when added.

## 10. Risks & open questions

- **Virtual audio device** is the biggest native risk. Mitigation: MVP on an
  existing virtual device (VB-Audio) + loopback; custom driver later.
- **Latency** of a Python prototype may be too high to *feel* the effect.
  Mitigation: validate the world-lock perceptually even at relaxed latency, then
  port the hot path.
- **Head-tracker robustness** (lighting, glasses, range, multi-camera fusion).
- **HRIR interpolation artefacts** and **click-free switching** under fast head
  motion.
- **Calibration without the owner's lost raw HRTFs** — leans on the sibling
  project's selection/fine-tuning findings.
- Open: exact virtual-device strategy; native stack (JUCE vs PortAudio vs Rust);
  tracker model; how tightly to couple with `binaural-localization`'s judge.

## 11. MVP definition & milestones

**MVP (perceptual proof):** one HRIR set (SOFA), a fixed 7.1 world layout,
MediaPipe head-orientation tracking, Python partitioned-convolution render engine,
audio in from an existing virtual 7.1 device (loopback) and out to a chosen
headphone, no GUI (config file). Success = turning the head audibly keeps a
virtual speaker fixed in space, at a latency low enough to be convincing.

Milestones (see [`backlog.md`](backlog.md) for the ordered, small work blocks):

- **M0** Repo, docs, journal, dev env, audio/tracker spikes.
- **M1** Static binaural render engine (no tracking): 7.1 SOFA → stereo, verified.
- **M2** Head-pose tracker producing a smooth (az,el[,roll]) stream.
- **M3** World-lock: couple tracker → render; demonstrate a world-fixed speaker.
- **M4** Virtual-device capture + headphone output, end-to-end on Windows.
- **M5** Live EQ chain (FR-8) + presets.
- **M6** GUI (FR-9).
- **M7** AI-judge calibration loop (FR-10) with `binaural-localization`.
- **M8** Latency hardening (native hot path) toward NFR-1.
- **M9** HRIR fine-tuning (FR-11), packaging, donationware release.

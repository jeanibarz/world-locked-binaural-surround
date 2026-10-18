# 36 — The pose-age latency budget, the perceptual targets, and orientation prediction (M8 planning / RFC)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** planning entry — reframes the latency budget, grounds it in the psychoacoustics, and opens **B8.5** (pose-age measurement) and **B8.6** (orientation prediction); expands **B0.7** (camera). No code shipped here.

## Question

We have shipped the cheap latency win (journal 26: the head-move→ear lag was the output
cushion, now ~70–110 ms) and a CPU-realtime render kernel (journal 27, ~5× RT). The owner
asked, in effect: *what is the realistic latency budget for a world-locked renderer, which
stages dominate, what is physically achievable without spending money (my current camera
first, a better one only if the measurements demand it), and what is the right next lever?*
This entry answers that durably and sets the M8 direction.

## The reframing: the metric is **pose age at the moment the sound is heard**, not a sum of worst cases

For world-locked audio the quantity that matters is not "how fast does the tracker run" or
"how big is the audio buffer" in isolation. It is: **when the listener hears a given block of
audio, how old is the head pose that was baked into it?** That is the delay that makes the
scene lag the head (or, worse, momentarily *follow* it). Four distinct delays feed it:

1. **Image delay** — exposure, sensor read-out, USB transfer, driver/capture buffering. This
   is the part people underestimate: it is *not* `1/fps`.
2. **Compute delay** — landmark detection + 6-DoF/orientation estimation + filtering.
3. **Audio-render delay** — convolution (direct/partitioned/overlap-save), block size, the
   output device buffer. Note the FIR *length* does not set the algorithmic latency; the
   *block/partition* scheme and the device buffer do (journal 09: our partitioned convolver
   adds exactly one block of latency regardless of the 500 ms BRIR).
4. **Residual angular error during motion** — even with everything above small, while the head
   is actually turning the pose is stale by the above sum, so the rendered direction trails the
   true one by `ω · Δt`.

**These do not simply add.** They only sum when defined between successive, non-overlapping
time boundaries. In practice capture and DSP are pipelined, and a pose's *age* at the ear
depends on the phase alignment between frame arrivals and audio blocks — so the honest figure
is a **distribution**, not a single number (see "Measurement", below, and the diagnostics model
in `diagnostics.classify`, which already reports `tracking_ms ≈ 1.5 × pose_interval` + audio
stages as an *estimate*, explicitly not a claim of exact physical latency).

## An illustrative budget for a well-optimised 60 fps path (~33 ms)

Not a measurement of any specific camera — a target to design against, for a **60 fps,
uncompressed, manual-exposure** acquisition on this CPU with the Rust kernel at a small block:

| stage | budget |
|---|---|
| capture + transfer + buffers | ~16 ms |
| landmarks + 6-DoF estimation | ~7 ms (we measure ~3 ms detect on the C270, journal 31) |
| pose → DSP hand-off | ~2 ms |
| DSP + audio output | ~8 ms |
| **total (pose age at the ear)** | **~33 ms** |

At a **90°/s** head turn (a brisk but ordinary rotation; peak voluntary yaw is ~200°/s, see
below), 33 ms of pose age is an instantaneous angular error of `90 × 0.033 ≈ 3.0°`. The owner's
headline scenario — glance from one screen to another ~45° away — is exactly this regime.

## The perceptual targets (why ~33 ms is good, and where the real risk is)

Two *different* perceptual thresholds bound this problem, and conflating them is the usual
mistake. **These figures are from the published psychoacoustics literature (recalled, with the
caveats below), not from this project's knowledge bases** — a `kb-scout` survey of the local
shelves (`sound-source-localization`, `hrtf-individualization`,
`spatial-audio-psychoacoustics`) confirmed they do **not** index latency-threshold, MAMA, or
head-velocity material, so treat the specific numbers as literature values to verify against the
primary sources before any load-bearing use, not as measured-here facts.

1. **Detection of the latency itself (the scene "swims"/lags).** For dynamic binaural synthesis,
   just-noticeable total system latency clusters around **~50–75 ms**, signal- and
   paradigm-dependent. Lindau (2009, adaptive 3AFC) reports the *lowest* observed threshold at
   **~52 ms** (below earlier literature); other reports give ~50 ms (simple scene) to ~60 ms
   (complex), and >~73 ms raising localization error for brief sounds. **Implication: an
   end-to-end pose age comfortably under ~50 ms is below most listeners' detection threshold;
   under ~30 ms is generous headroom.** Our ~33 ms illustrative budget lands in the good band;
   the current ~70–110 ms (journal 26) is *above* threshold — hence "it reacts too slowly."

2. **Resolution of the angular error *as an angle*.** The minimum audible *movement* angle
   (MAMA) grows with source velocity: Perrott & Musicant (1977, real motion) measured **~8.3° at
   90°/s** rising to ~21° at 360°/s; Saberi & Perrott (1990, simulated) ~1.7° at 1.8°/s up to
   ~10° at 320°/s, U-shaped with best resolution near 2°/s. **Implication: the ~3° of
   latency-lag during a 90°/s turn is *below* the ~8° MAMA at that speed — so the angular error
   is not itself what a listener resolves during the turn.** What they detect is the *temporal*
   lag (threshold 1), and — critically — the behaviour at the **end** of the turn: once the head
   stops, the source is static and the much finer static minimum-audible-angle (~1°) applies, so
   any pose that is still stale (or that a predictor has overshot) is now plainly wrong.

This reconciles the two and sets the design: during motion the ear is forgiving of a few
degrees but not of tens of milliseconds of lag; at rest it is forgiving of lag but not of
degrees. That is exactly the signature a good predictor targets (reduce effective lag during
motion, settle cleanly at stop).

Why bother at all (the world-lock premise, this one KB-backed): dynamic, head-coupled binaural
cues materially improve front/back disambiguation and externalisation over static binaural —
the KB's `sound-source-localization/2610.01388` records head-related cues lifting front/back
accuracy from **51.0% → 69.3%**, and `hrtf-individualization` warns these cue-driven errors are
exactly what spatially/spectrally averaged metrics wash out (so evaluate per-direction). The
whole latency budget exists to protect that dynamic cue.

## The lever: predict the orientation at the audio-render instant (B8.6)

The most valuable improvement after the cushion fix is **not** detecting more landmarks or
producing poses more often. It is estimating the head orientation at the *future* instant the
DSP will actually render, so the pose baked into a block matches the head when that block is
heard. Pipeline:

```
camera 60 fps ─▶ timestamped frame ─▶ landmarks + 3-D pose  →  q(t), tracking confidence
            └▶ filter + angular-velocity estimate           →  filtered q, ω(t)
            └▶ predict to the audio output instant           →  q̂(t+Δt)
            └▶ DSP re-evaluates q̂ every audio block (or sample) ─▶ world-lock convolution
```

A constant-angular-velocity extrapolation is enough for a first cut:

```
q̂(t + Δt) = q(t) ⊗ exp( (Δt/2) · ω(t) )
```

(with the frame/representation conventions matched to `calibrate`/`world`). Prefer a **lightweight
α-β filter**, or a small orientation+angular-velocity state filter, over a heavy predictive
model. The big payoff: the tracker can stay at 60 Hz while the DSP re-evaluates the *predicted*
pose at block rate — decoupling the perceived reactivity from the camera's frame rate, which is
our actual bottleneck (journal 05/31: the C270 is capture-bound, detect is only ~3 ms).

Two precautions, both flowing from threshold (2) above:

- **The predictor is wrong when the head stops abruptly** (constant-ω overshoots into a now-static
  source where the ~1° static MAA bites). Mitigate: **limit the prediction horizon**, damp the
  estimated velocity, and detect regime changes (decelerating → shrink Δt toward 0).
- **Landmark jitter makes ω noisy.** Filter it well *without* adding back the latency the
  prediction just removed (the classic one-euro trade-off we already expose in `tracking.py`).

This is a genuine architecture addition (a prediction stage between tracker and render, plus a
pose timestamp carried end-to-end), so it is recorded as its own block (B8.6) rather than
smuggled into an implementation task. It depends on B8.5 to know Δt (the real pose age) and to
prove it helps.

## How we will validate it (B8.5) — measure, don't assume

Three measurements, reported as **distributions (p50/p95/p99 + extremes), never a mean** — a
rare 100 ms spike is far more damaging to a world-locked scene than a slightly higher average:

1. **Camera → application.** A known temporal signal (an LED or a screen flashing a timecode)
   in view of the camera, compared to the software-availability timestamp of the frame that
   shows it. Account for exposure window + rolling-shutter skew; `t_arrival` is software arrival,
   **not** mid-exposure.
2. **Compute time.** Instrument each stage — capture, landmarks, pose, filtering, DSP publish —
   which the passthrough already partly does (`detect_ms`, per-block render EWMA/peak).
3. **Motion → sound.** An external motion reference (ideally a motorised/instrumented rotation)
   recorded synchronously with the audio output; recover the effective applied orientation by
   cross-correlation / system identification and read off the end-to-end delay.

This harness is also how we find out whether the **current C270 is already good enough**: if the
dominant term turns out to be software/buffering rather than the sensor, no camera purchase helps
(journal 26 already showed the audio cushion, not the camera, was the felt lag).

## The camera path, cheapest-first (expands B0.7)

Ordered by cost, measure before buying:

1. **Optimise and instrument the C270 we have.** We already know (journal 31) it runs 30 fps
   native, detect ~3 ms, and that the earlier 15 fps was **low-light auto-exposure**, not USB.
   So: fix lighting / set **manual exposure** and **fixed focus** to hold 30 fps and cut motion
   blur; keep acquisition **decoupled** from compute and **drop-latest** (process the newest
   frame, discard the stale queue — our tracker thread already publishes latest-only; extend the
   same to capture). `cv2.CAP_PROP_BUFFERSIZE=1` is *not* guaranteed under MSMF/DSHOW, so verify
   the achieved frame age with B8.5 rather than trusting the flag.
2. **Only if the measurements demand it, a 60 fps *uncompressed* webcam** with manual exposure and
   disabled autofocus (e.g. an Elgato-Facecam-class STARVIS sensor with YUY2/NV12 out) — chosen
   for a *mastered* low-latency path, not for a bigger headline fps. A nominal 90–120 fps camera
   whose internal latency is unknown is **not** obviously better: 90 fps shaves the frame
   *period* from 16.7 ms to 11.1 ms, which says nothing about the full capture→application delay,
   and autofocus/auto-exposure add variance. Period ≠ pipeline latency.
3. **A second camera only for angular coverage or occlusion robustness** (keeping the face in
   view across the ±45° turns), not for speed. Do **not** start with hardware sync; build an
   **asynchronous temporal-state estimator** — each camera emits (pose, timestamp, confidence),
   fused by acquisition time, late measurements handled — and only reach for hardware
   synchronisation if the async fusion is measurably insufficient. Treat USB arrival timestamps
   as arrival, not exposure.

A lighter orientation estimator is also worth a bench against MediaPipe if only yaw/pitch/roll
matters: a few stable landmarks → `solvePnP` with a camera calibration → temporal filtering,
which can beat a full face-geometry solve for pure head orientation. (MediaPipe's `LIVE_STREAM`
mode already drops frames under load and reuses tracking between detections — consistent with
the drop-latest strategy.)

## Decision / next

- **Reframed the latency budget around pose age** and wrote it into `requirements.md` §7
  (§7.1 perceptual targets, §7.2 prediction, §7.3 measurement, §7.4 camera path), with the
  illustrative 60 fps budget and the two perceptual thresholds.
- **Opened B8.5** (pose-age / motion→sound measurement harness) and **B8.6** (orientation
  prediction to the audio-render instant); **expanded B0.7** with the acquisition-optimisation
  and measure-first-buy-second camera guidance.
- **No code shipped** — this is the planning/RFC entry the M8 implementation blocks read from.
  The accurate frontier remains this journal's status table + `docs/backlog.md`.
- **Honesty note:** the latency-threshold / MAMA / head-velocity figures are literature recall
  (sources below), *not* KB-verified or measured here; B8.5 replaces the budget's estimates with
  our own measured distribution.

### Sources (verify against the primaries before load-bearing use)

- Lindau, A. (2009), *The Perception of System Latency in Dynamic Binaural Synthesis* (DAGA) —
  adaptive 3AFC, lowest detection threshold ~52 ms. Also Lindau's dissertation *Binaural
  Resynthesis of Acoustical Environments* (TU Berlin).
- Perrott, D. R. & Musicant, A. D. (1977), minimum audible movement angle vs velocity (~8.3° at
  90°/s → ~21° at 360°/s); Saberi, K. & Perrott, D. R. (1990), simulated-motion MAMA, U-shaped.
- Head-rotation velocity: VR/XR head-motion traces — yaw dominant, peak ~200°/s, mostly <100°/s
  (e.g. arXiv:2007.14084, arXiv:2407.02636).
- Dynamic/binaural cue benefit for front/back: KB `sound-source-localization/2610.01388`
  (69.3% vs 51.0%); `hrtf-individualization` (averaged metrics hide the cue-driven errors).
</content>
</invoke>

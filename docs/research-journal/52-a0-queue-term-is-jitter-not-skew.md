# 52 — A0: the queue term is jitter/burst, not clock skew (RFC Track A, B8.7a)

**Block:** B8.7a (RFC Track A, A0) · **Milestone:** M8 (latency hardening) · **Status:** done
(measured on the host with the real VoiceMeeter loopback + FiiO K11; two 90 s runs).

## Question

Track A's A0 gate (RFC): is the ~31 ms live `queue` term — the standing output-FIFO depth —
driven by **clock skew** (the VoiceMeeter capture clock and the K11 output clock running at
slightly different rates, so the FIFO ramps) or by **jitter / burst pacing** (bursty loopback
delivery, so the FIFO bounces around a level)? The answer decides A1: skew → an async
drift-compensating resampler in our user-mode bridge fixes it with no kernel; jitter/burst → a
resampler can't (it keeps VoiceMeeter's burst delivery), which is the Track-B case.

## Method

`spikes/a0_fifo_probe.py`, driven from WSL via interop on the owner's host. It reproduces the
passthrough's `queue`-term pipeline where it matters — PyAudio WASAPI **loopback** capture of the
VoiceMeeter 7.1 render endpoint → a burst-drain worker (MMCSS 'Pro Audio' + `gc.freeze`, like the
app) → a cushioned output FIFO (512@20, cushion 2 blocks, max_backlog 4) → sounddevice output to
the K11 — with a **synthetic pose** and no camera/window (per the host runbook: the camera does
not touch FIFO timing). A player stream loops a WAV into all 8 VoiceMeeter channels so the capture
is never silent.

**Key instrumentation choice.** The FIFO is drop-capped at `max_backlog`, so clock skew cannot
show up as an unbounded fill *slope* — it saturates into overflow drops. So the probe logs the
**cumulative (captured − played) frame divergence**: its slope is the true skew (ppm) and is *not*
hidden by the cap (dropped frames still count as captured-but-not-played); the detrended variance
is the jitter. `binaural_headtracker.fifo_drift` (B8.7a) classifies the series.

## Result

Two 90 s runs at 512@20 WASAPI shared, 22 ms device buffer:

| run | skew | drift over run | jitter RMS | trend frac | verdict | mean standing | net |
|-----|------|----------------|-----------|-----------|---------|---------------|-----|
| plain | +4.6 ppm | 0.4 ms | 3.3 ms | 0.00 | **JITTER** | 19.8 ms | −0.7 ms |
| MMCSS + gc.freeze | +26.9 ppm | 2.4 ms | 5.0 ms | 0.02 | **JITTER** | 16.1 ms | −0.7 ms |

Both: 0 overflow drops, 5–7 underruns (startup). **Skew is negligible** — the VoiceMeeter and K11
clocks stay within ~27 ppm, i.e. ≤2.4 ms of net divergence over 90 s. The FIFO wander is
**jitter/burst-dominated** (trend fraction ≈ 0).

## Interpretation

**A1 (the async drift-compensating resampler) will not cut the queue term.** A resampler corrects
*drift*, and there is essentially none to correct. The queue floor is the cushion needed to absorb
VoiceMeeter's **bursty loopback delivery** — which is why journal 48 found block 256 glitches 3×
worse (a smaller cushion can't absorb the bursts) and 512@40 runs clean (more cushion). This is the
RFC's explicit "jitter → Track B" branch: the lever is the **capture path**, not a resampler. So
**Track A's A1 is deprioritized**, and the remaining latency levers for the queue term are:

1. **Cheap first (Alternatives §5):** lower the VoiceMeeter / VB-CABLE buffering (the burst size),
   which the owner previously declined — worth retrying now that A0 pins the burst as the cause.
2. **Track B (custom virtual 7.1 device):** removes the VoiceMeeter loopback (and its burst
   pacing + the ~128 ms content buffer) entirely — gated on the B-pre VM `audiodg` feasibility test.

This also sharpens journal 48: the queue floor is *burst-jitter*-bound, not *drift*-bound — a
distinction journal 48 couldn't make without separating the cumulative divergence from the
cap-bounded depth.

**Honest limits.** (1) The probe uses a cheap 8→2 downmix, not the full BRIR render, so its
*standing depth* (16–20 ms) is lighter than the app's measured 31 ms (journal 48) — the render
cost deepens the real backlog. The **skew≈0 conclusion is render-independent** (skew is a device-
clock property), so it stands; the jitter *magnitude* (3–5 ms, noisy run-to-run) is approximate.
(2) Head-move→ear is unaffected either way (it was already a non-goal — journal 51).

## Learnings

- **Measure the cause, not the symptom.** A drop-capped FIFO converts skew into overflow drops, so
  reading the bounded depth would have mislabeled skew as jitter. Logging cumulative captured−played
  divergence recovers the true slope regardless of the cap.
- **Rule out your own instrument.** The jitter persisted (even rose) with MMCSS 'Pro Audio' +
  `gc.freeze`, so it is VoiceMeeter's burst delivery, not the probe's scheduling. Worth the second
  run before concluding.

## Decision / next

- **A0 done; A1 (resampler) deprioritized** — A0 shows no skew for it to fix. Recorded in B8.7a.
- Next latency lever for the queue term is owner's call: the **cheap VoiceMeeter-buffer experiment**
  first, else **Track B** (gated on the B-pre VM test). Pending owner steer; not started.
- Head-move→ear remains a non-goal (structural, journal 51).

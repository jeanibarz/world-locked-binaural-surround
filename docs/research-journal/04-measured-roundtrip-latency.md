# 04 — Measured virtual-loopback round-trip latency (B0.6)

**Date:** 2026-10-16  ·  **Milestone:** M0  ·  **Status:** done

## Question

What is the *measured* (not reported) round-trip latency of the virtual loopback
— the number B0.2 and B0.3 deferred because no render→capture route was wired?

## Context

B0.3 exposed the loopback harness and confirmed the capture path, but every run
read silence: the VoiceMeeter VAIO strip was not routed to a B bus. The owner
then wired an **isolated tap**: the normally-unused **AUX input strip → bus B1**,
leaving the VAIO strip on **A1** (the real headphone path) untouched — so test
sweeps never reach the ears. Setup confirmed from a screenshot of VoiceMeeter
Banana: HW-out A1 = Casque (FiiO K11) @ 48 kHz/1024; VAIO→A1; AUX→B1 @ 0 dB;
VAIO/AUX driver buffer **6144**.

A first attempt still read silence on the *working* route — which exposed a bug
in the spike itself (see Result).

## Hypothesis

A render→capture round-trip is measurable once a bus route exists, and its value
is **dominated by the VAIO/AUX driver buffer** (6144 samples = 128 ms), not by
the engine or the hardware output buffer (1024).

## Method

- Route: feed `VoiceMeeter Aux Input` (#27) → AUX strip → B1 → capture
  `VoiceMeeter Output (VAIO)` (#35). Added `--out-device`/`--in-device` overrides
  to `spikes/b0_3_virtual_device.py` so an explicit cross-strip pair can be
  measured without disturbing auto-resolution.
- **Bug fix (the reason B0.3 saw silence on a live route):** `measure_loopback`
  wrote the *entire* output buffer in one blocking `play.write()` and only then
  began reading. During that write the input ring overflowed, so a 50 ms sweep
  buried in a multi-second buffer was discarded before the first `read()`. A
  *continuous* tone survived this (hence a loud 1 kHz probe read back at peak
  ~1.0 while the sweep read 0.0). Fixed to **record concurrently**: write the
  output in 1024-frame blocks and drain the input on every pass.
- Sanity: a 2 s continuous tone on #27→#35 (expect the route to carry it), then
  three sweep round-trips. One variable held: only the buffer/route in play.

## Result

Continuous-tone sanity on #27→#35: peak **0.3** (unity-gain path — the 0.3 sent
came back at 0.3), confirming the route carries signal.

Sweep round-trip (3 runs, cross-correlated delay):

| Run | roundtrip_ms | peak |
|---|---|---|
| 1 | 149.2 | 0.27 |
| 2 | 137.6 | 0.27 |
| 3 | 104.9 | 0.27 |

Mean ≈ **130 ms**, spread ±~20 ms. Portable helpers unchanged: 22/22 pytest
green; ruff + format clean.

## Interpretation

- **The round-trip is essentially one driver buffer.** 6144 / 48000 = **128 ms**,
  versus a measured ~130 ms — the VAIO/AUX buffer *is* the latency here; the
  K11's 1024 output buffer, the VoiceMeeter engine, and shared-mode WASAPI are in
  the noise beside it. Latency on this path scales directly with that setting.
- **This closes B0.2/B0.3's deferred measurement** and quantifies the gap: ~130 ms
  is ~4× over the <30 ms target (requirements § 2), entirely because of the large
  buffer the owner set to stop dropouts. A low-buffer datapoint was offered to
  trace the buffer→latency curve but the owner declined to change the live buffer;
  the single point plus the near-exact 6144↔128 ms match already establish the
  relationship.
- **The measurement is a ballpark, not sample-accurate.** Two independent WASAPI
  streams (render + capture) start with an unknown sub-buffer skew and are polled,
  so the ±20 ms spread is method noise, not route instability. Good enough to
  attribute latency to the buffer; a sample-accurate figure would need a single
  full-duplex stream (one clock) — not worth it for a spike.
- Confirms the standing conclusion: the <30 ms budget needs the buffer driven
  down and/or the virtual mixer bypassed via exclusive-mode hardware (B0.5) or a
  native path (B8) — not a different virtual cable.

## Learnings

- **Loopback latency probes must record *concurrently* with playback.** Writing
  the whole buffer before reading drops a short probe to input-ring overflow; a
  continuous tone hides the bug. Interleave block-writes with reads (or use one
  full-duplex stream). (→ this entry)
- **Measured virtual round-trip ≈ the VAIO/AUX driver buffer** (6144 = 128 ms →
  ~130 ms measured). Latency tracks that one setting; the engine and a 1024-sample
  hardware output are negligible against it. (→ this entry)
- **Use an unused VoiceMeeter input strip (AUX) → B-bus as an isolated test tap.**
  It keeps measurement sweeps off the listening strip (VAIO→A1) so nothing reaches
  the headphones, and needs no change to the normal routing. (→ this entry)
- Dual independent streams make the delay a ballpark (±~20 ms from start skew +
  polling); sample-accuracy needs a single-clock full-duplex stream. (→ this entry)

## Decision / next

B0.6 done — the measured round-trip is recorded (~130 ms, ≈ one 6144 buffer, via
AUX→B1). Spike gains concurrent capture + device overrides. No change to the
owner's buffer. Next eligible block remains **B0.4** (head-tracker spike:
MediaPipe yaw/pitch/roll + inference latency), runnable here via interop.

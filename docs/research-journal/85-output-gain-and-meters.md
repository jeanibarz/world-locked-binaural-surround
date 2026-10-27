# 85 — Adjustable output gain with pre-clamp meters

## Question and hypothesis

The owner reports low loudness despite maximizing available volume controls. The existing
native engine applies a fixed −20 dB preamp inside the headphone-EQ chain, inherited from
the owner's EqualizerAPO setup. Installed BRIR sample peaks range from −39.43 to −19.24 dBFS,
but individual frequency-response maxima range from −4.28 to +2.04 dB. An impulse's sample
peak does not establish playback headroom after convolution, channel summation, and EQ.

Hypothesis: shared stereo gain after the complete EQ chain, paired with measured peak/RMS
and clipping counts, lets the owner set usable level without changing spatial balance.
Keep 0 dB additional gain as the default and retain the original EQ headroom policy.

## Method and implementation

`GainStage` applies a finite −24…+24 dB trim after EQ, including the bypass path. A linear
amplitude ramp lasts 10 ms and advances once per stereo frame. Both ears receive the same
factor. It replaces the previous final conversion/clamp; the FIFO still owns each output
block. Render-load timing now includes this stage. No limiter, BRIR normalization, or new
third-party dependency was added.

Meters accumulate after gain and before the existing clamp. Peak and RMS cover samples
since the previous telemetry heartbeat (about 50 ms); a scaled sum of squares avoids
numeric overflow. Silence/empty intervals use JSON null rather than a fabricated dBFS
floor. Values above full scale remain visible numerically. Individual over-range samples
are counted before clipping; cumulative clipping/invalid-input counters survive telemetry
updates. They reset on process restart. These are sample-peak measurements before the FIFO
and output resampler, not endpoint true peaks or acoustic loudness.

The additive persisted setting defaults to zero in old files. API and saved values share
finite/range validation. Gain changes use the existing settings preview/commit/cancel flow
and do not rebuild the renderer or FIFO. The browser submits on slider release, waits for
engine confirmation, resists stale snapshots while editing, and restores the last confirmed
value on failure. Its fallback read has a bounded timeout. A separate compact cockpit row
keeps existing system telemetry readable.

## Verification

- Engine suite: 81 passed, three intentionally ignored. Six gain-stage tests cover bounds,
  finite handling, silence, clipping, interval/lifetime counters, shared ramp and block
  partition invariance. Three additional telemetry contracts pin null/positive levels and
  counters. Two synthetic-room tests use real multichannel convolution and per-ear FIR /
  biquad EQ at several head positions and blocks 256/512, comparing samples and meters with
  independently calculated references. They verify chain behavior, not the owner's room or
  perceived loudness.
- Windows release build passed without warnings. Eight Playwright browser cases passed,
  including failed requests, delayed/stale state, hanging fallback reads and reconnects.
- An isolated real Windows engine check passed finite/range request handling, preview/cancel,
  SSE output fields, and persisted −6 dB on a second startup. Both ten-second runs used an
  idle BenQ capture and unused VoiceMeeter Aux output, with camera disabled. They rendered
  zero audio blocks, so this proves API/persistence wiring and silence telemetry, not audible
  gain or signal headroom. Device ownership ended through normal `--secs` shutdown.
- Screenshot inspection at 1660×960 and 900×820 shows the added panel fitting the real cockpit. Browser
  tests also measure text containment at 900×820; this does not establish native Windows DPI.

## Negative results and limits

The first host-check launch directly from the shared build directory failed before Rust
startup (Windows loader status `0xc0000279`). Adding the install directory to DLL search did
not fix it. The build directory contains a Linux-symlinked DirectML DLL; the harness instead
copies the executable to an isolated Windows-local temporary directory, with explicit asset
paths. It also isolates APPDATA and uses finite `--secs` runs on unused endpoints so it cannot
write the owner's settings or need a forced device-owning process kill.

The gain meter cannot guarantee a clipping-free Windows mixer/DAC path. Source content,
head pose, summation and EQ all affect usable gain. No automatic level increase is part of
deployment. User-facing instructions and API details are in `docs/output-gain.md`.

## Delivery and live postcondition

[PR #222](https://github.com/jeanibarz/world-locked-binaural-surround/pull/222).
The reviewed executable and cockpit assets were deployed with backups. A graceful restart
preserved block 512, cushion 32 ms, VoiceMeeter Input/K11, method, EQ and camera settings.
Additional output gain was confirmed at 0 dB in the startup config. During the first live
readback, gain changed from 0 to +6 dB; a subsequent five-second observation returned
87 states at +24 dB. The observer did not send these gain requests or revert them.
The latter sample's maximum peak was -16.19 dBFS, with zero cumulative clipped
or non-finite samples. This verifies active rendered telemetry and control changes, not
representative program loudness or guaranteed headroom for other material.

Deployed executable SHA-256: `1ca93bc07d7134695a637ca1c7db3d44cf7e6e400af953b2c141162ec9d41d6b` (code commit `2f4ffb7`).
Subsequent delivery changes update documentation only. The recorder remains enabled with a
new live file in the install's `traces` directory. Browser screenshots/DOM checks above are
local Chromium evidence, not a native host-DPI assertion.

## Decision

Deliver adjustable gain with measured headroom and explicit clipping visibility. Leave the
owner's initial gain at zero; they can raise it gradually while observing representative
material. A limiter would need a separate evaluation of waveform changes and latency.

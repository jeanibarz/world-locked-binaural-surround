# 02 — Windows audio-I/O spike: WASAPI enumeration + latency (B0.2)

**Date:** 2026-10-15  ·  **Milestone:** M0  ·  **Status:** done

## Question

Can we enumerate Windows audio devices and open a low-latency WASAPI stereo
output and a multichannel input from this project, and what round-trip latency
does the real hardware report — given the dev box is WSL2 (Linux), not Windows?

## Context

The prototype is developed on a WSL2 box. WSL exposes only WSLg's PulseAudio
(an RDP sink/source), no ALSA soundcard and **no WASAPI** — so a naïve read is
"B0.2 is blocked until we're on native Windows" (journal 01 flagged exactly
this). But WSL **interop** reaches the Windows host directly: `py.exe -3.12`
launches a native Windows Python, and that interpreter already had
`sounddevice` 0.5.6 (PortAudio V19.7) + numpy 2.5.4. So the spike is runnable
*now*, from the WSL repo, by driving the Windows Python against the real audio
stack. (The owner confirmed this is the intended workflow and asked us to record
it durably — see AGENTS.md "Runtime environment".)

## Hypothesis

Through interop we can (a) enumerate the host's WASAPI endpoints, (b) open a
low-latency stereo WASAPI output and a multichannel WASAPI input and read
PortAudio's reported latency, and (c) measure a true round-trip via a loopback.
Exclusive mode should beat shared mode on latency.

## Method

- Portable, hardware-free helpers in `src/binaural_headtracker/audio_spike.py`
  (probe-sweep generator, FFT cross-correlation delay estimator,
  samples→ms, device-table formatter), unit-tested on WSL
  (`tests/test_audio_spike.py`, 5 tests) — no device, no `sounddevice` needed.
- `spikes/b0_2_audio_io.py` runs on the Windows host:
  `WINPATH=$(wslpath -w spikes/b0_2_audio_io.py); py.exe -3.12 "$WINPATH"`.
  It enumerates host APIs/devices, opens short-lived streams on the WASAPI
  host-API's default output (stereo) and a multichannel input, reads
  `stream.latency` in shared (`latency='low'`/`'high'`) and exclusive mode, and
  attempts a round-trip by playing a 0.3-amplitude 50 ms sweep to a virtual
  cable and capturing the far end, recovering the delay by cross-correlation.
- `sounddevice` recorded as the `audio` optional-extra in `pyproject.toml`
  (kept out of the baseline/dev extras so headless `uv sync` stays green);
  `uv.lock` updated. One variable per open: host-API default vs hardware DAC,
  shared vs exclusive.

## Result

Enumeration works: **56 endpoints** across MME, DirectSound, **WASAPI** (host
index 2), and WDM-KS. Reported WASAPI latencies (real hardware, this box):

| Stream | Mode | Latency | Rate |
|---|---|---|---|
| Default output #31 (VoiceMeeter Input, 8-ch virtual sink) | shared low | **22.0 ms** | 48 kHz |
| …same | shared high | 22.0 ms | 48 kHz |
| …same | exclusive low | **24.0 ms** | 48 kHz |
| Multichannel input #34 (2-ch) | shared low | **21.995 ms** | 44.1 kHz |
| Hardware DAC #26 (FiiO K11, stereo) | shared/exclusive | **fails, `-9996 Invalid device`** | — |
| Loopback #31→#35 (VoiceMeeter VAIO→Output) | — | **no signal (peak 0.0)** | — |

Portable helpers: 7/7 pytest green on WSL; ruff + format clean; baseline
`uv sync` confirmed free of `sounddevice`.

## Interpretation

- **B0.2's goal is met from WSL via interop** — WASAPI is fully reachable;
  "blocked on WSL" was wrong. This unblocks B0.4 (camera) the same way.
- The host routes everything through **VoiceMeeter**, so the WASAPI *default
  output* is a virtual 8-ch sink, not bare hardware. Its ~22 ms (shared) /
  ~24 ms (exclusive) is dominated by the virtual mixer — which is why exclusive
  is **not** lower here, contradicting the hypothesis. The bare hardware DAC
  (FiiO K11) can't be opened directly (`-9996`): VoiceMeeter owns it.
- The virtual-cable loopback captured silence because the VAIO strip isn't
  assigned to the B (virtual-out) bus — that routing is **B0.3's** job, not
  something to change in the owner's mixer here. So a *measured* round-trip is
  deferred; the reported latencies bound it: out+in ≈ **44 ms** on the current
  virtual chain — above the <30 ms target (requirements § 2), confirming that
  low latency will need exclusive-mode hardware access and/or a leaner virtual
  device (B4/B8), not the default VoiceMeeter path.

## Learnings

- WSL interop (`py.exe -3.12`) gives the project the real Windows audio/vision
  stack; Windows-only spikes are runnable from this box — don't mark them
  "blocked on WSL". Run scripts by their `wslpath -w` path; keep portable logic
  in the package (unit-tested on WSL), hardware I/O in `spikes/`.
- `sounddevice.WasapiSettings` in this build exposes only
  `exclusive`/`auto_convert`/`explicit_sample_format` — **no `loopback`** flag;
  a render-loopback capture needs a different path (virtual cable, or a newer
  binding), so round-trip measurement depends on B0.3's virtual device.
- The default output here is a VoiceMeeter virtual sink (~22 ms, exclusive no
  better); the real low-latency budget requires bypassing it. Record device
  indices as *roles* resolved at runtime (they drift); never hard-code them.

## Dependency licences (§ 9 compliance — all permissive)

| Package | Licence |
|---|---|
| sounddevice | MIT |
| PortAudio (bundled) | MIT |
| cffi (transitive) | MIT |
| pycparser (transitive) | BSD-3-Clause |

No GPL.

## Decision / next

B0.2 done (enumerate ✓, low-latency WASAPI stereo out ✓, multichannel in ✓,
reported latency ✓). A *measured* round-trip is deferred to **B0.3**, which owns
the virtual-device routing (and should expose a clean loopback for latency
tests). Added follow-up **B0.5** to revisit exclusive-mode low-latency against a
hardware endpoint once the virtual chain can be bypassed. Next eligible block
after this: **B0.3** (virtual 7.1 sink + loopback capture) — also runnable here
via interop.

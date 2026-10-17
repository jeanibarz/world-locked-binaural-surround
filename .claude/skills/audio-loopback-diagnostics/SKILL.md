---
name: audio-loopback-diagnostics
description: >-
  Diagnose real audio problems — dropouts/glitches, wrong or swapped channels, missing bass,
  spectral imbalance, L/R reversal — by driving a KNOWN signal through the real
  capture→render→output path and measuring what comes back, instead of trusting headless
  tests or your ears. Use whenever the live passthrough or any audio path "sounds wrong",
  stutters, or when you suspect channel order, LFE/bass, surround routing, or sample-rate
  issues. Device/timing/spectral bugs only appear against real hardware; measure them.
---

# Audio loopback diagnostics

**Core principle:** headless unit tests prove the *math* (e.g. `test_7p1_neutral_equals_static_render`),
but channel order, real-time timing, sample rates, and spectral/headphone coloration are
properties of the *hardware path* and are invisible to them. To debug them, inject a **known**
signal into the real device, capture/render it, and **measure** the result. Never diagnose these
by ear alone, and never assume the capture layout matches your code's assumed layout — prove it.

The toolkit is `spikes/b4_1_loopback_diag.py` (four subcommands). It runs on the **Windows host**
via WSL interop (except `spectral`, which is offline). See AGENTS.md "Running the host GUI apps".

```bash
WINPATH=$(wslpath -w spikes/b4_1_loopback_diag.py)
py.exe -3.12 "$WINPATH" devices      # list output + WASAPI-loopback devices with rates + host APIs
py.exe -3.12 "$WINPATH" order        # feed a distinct tone per channel; read the captured column order
py.exe -3.12 "$WINPATH" duplex --arch worker --gpu --wav C:\...\out.wav   # dropouts/throughput/underruns
py.exe -3.12 "$WINPATH" spectral     # per-channel render level + sub-bass retention + BRIR low-freq response
```
Offline (CI/WSL): `python spikes/b4_1_loopback_diag.py spectral --brir-dir <dir>`.

## Method (symptom → probe → fix)

1. **Pick devices by measurement, not assumption** — `devices`. The capture (WASAPI loopback of
   the virtual 7.1 render endpoint) and the headphone output are separate clock domains; note
   their **sample rates** and **host APIs**. A name like `"FiiO"` can resolve to an MME 44.1 kHz
   endpoint when a WASAPI 48 kHz one exists — prefer WASAPI and match the BRIR rate.

2. **Dropouts / stutter / throughput collapse** — `duplex`. It reports effective capture/output
   sample rates, PortAudio under/overflow flags, FIFO occupancy, callback gaps, render timing, and
   records the output WAV. **If the effective rate collapses (e.g. ~31 kHz vs 48 kHz) with huge
   render-time spikes, the render is running inside the audio callback** — it holds the GIL and
   blocks on GPU sync, starving the callbacks. Fix: move the render to a **worker thread**; keep
   the capture callback to an enqueue and the output callback to a drain (compare `--arch inline`
   vs `--arch worker` to prove it). The FIFO cushion then absorbs render jitter.

3. **Wrong / swapped / silent channels** — `order`. Feeds a unique tone into each virtual-device
   channel and reports which captured column each lands on, vs the code's assumed order
   (`surround.CHANNELS_7P1`). A **MISMATCH** means you must remap the captured columns into
   `brir.channels` order before rendering (symptoms: LFE steered as a directional channel, center
   missing, surround/rear swapped). A channel reading `(silent)` that should carry signal points
   upstream (the source isn't really 7.1, or Windows isn't up-mixing).

4. **No bass / hollow / dead channel / imbalance** — `spectral`. Prints each BRIR's low-frequency
   magnitude (30/50/80/120 Hz re mid-band) and each channel's rendered output level + sub-bass
   retention. **Heavy sub-bass rolloff (e.g. −20 to −25 dB at 30–80 Hz) means the BRIR strips
   bass** — catastrophic for the **LFE** channel, which is *only* sub-bass, so it vanishes.
   Consider routing LFE **non-directionally** (summed flat to both ears, optionally low-passed)
   rather than through a bass-deficient HRIR. Gross L/R RMS imbalance on a channel that should be
   centered points to a BRIR or azimuth-sign error. A uniformly "hollow/wrong" tone across all
   front channels is usually the **missing per-ear headphone-compensation FIR + EQ** (the bare
   bake vs the full comp+EQ chain — B5.3), not a routing bug.

## Reusable beyond this project

The pattern generalizes to any capture/playback stack: (a) enumerate devices and their real
rates/APIs; (b) feed per-channel known tones to recover the true channel map; (c) run the live
duplex with instrumentation (effective rates, underruns, FIFO, callback timing) to separate
I/O-bridge problems from render-cost problems; (d) feed broadband per channel and compare input
vs output spectra to find filtering/coloration/bass loss. Always keep the heavy DSP **off** the
real-time audio callbacks.

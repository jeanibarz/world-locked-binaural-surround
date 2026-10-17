# 24 — World-lock *any* system audio: the 7.1 passthrough (B4.1)

**Date:** 2026-10-17  ·  **Milestone:** M4 (end-to-end on Windows)  ·  **Status:** implemented + offline-verified; owner's live no-dropout run pending

## Question

B3.1 proved the head-tracking chain world-locks a single test loop. The owner wants the
*real* thing: **any** sound the computer makes — a game, a film, music — world-locked to the
room. That means capturing the system's 7.1 stream, running every channel through the same
world-lock render, and playing stereo to the headphones, continuously, without dropouts.

    any app (7.1) → virtual 7.1 device (VoiceMeeter) → [this engine] → headphones

Two things had to be found out, and both changed the design.

## Method & findings

### 1. Capture the *raw pre-mix* 7.1 via WASAPI loopback — not VoiceMeeter's output bus

The obvious capture point — read VoiceMeeter's 8-channel output bus as a `sounddevice`
input — is **wrong**. VoiceMeeter downmixes that bus to stereo before it is exposed, so
centre, LFE and the surrounds are already gone (confirmed by measuring per-channel RMS: only
L/R carried signal). The first cut of the device layer chased this dead end (an
`input_device_choices` / `query_input_devices` 7.1-input picker); it was removed once the
measurement came back stereo.

The fix is **WASAPI loopback** (`pyaudiowpatch`, a Windows-only PyAudio fork): loopback taps
the stream apps send *into* the render endpoint, before any mixer downmix — the true 8-channel
signal. So the app points VoiceMeeter's Input (VAIO) at 7.1, sets it as the default playback
device, and loopback-captures it. (→ L48)

### 2. The full 8ch × 500 ms-BRIR render needs the GPU

Convolving 8 channels of a ~500 ms baked BRIR per block is **~0.97× real time on the CPU** —
under the line, so it drops out. Dropping the tail to fit would throw away the room sound that
makes it localise. So the render moves to the GPU.

To make that a *batched fixed-filter* convolution (what a GPU runs in one shot), the
world-lock is **reformulated as a bus mix**. B3.1's per-channel voice pool
(`WorldLockedSource`: steer each source through its nearest BRIR, crossfading the input gain
between persistent per-BRIR voices, L45) is generalised to 8 world-fixed speakers in
`WorldLocked7p1`. Because convolution is linear, that pool is *identical* to:

1. crossfade the 8 inputs into 8 **fixed-BRIR buses** (`BusMixer`: `bus_in[b] = Σ_c g_{c,b}·pcm_c`,
   each channel's gain ramping toward a one-hot on its current nearest-BRIR bus), then
2. convolve each bus with its fixed BRIR and sum.

The gains move; the *filters* never do — so step 2 is a batched partitioned overlap-save over
(bus, ear) that the GPU batches. The equivalence is **proven on CI to 1e-9** over a head sweep
that makes channels change buses (`test_bus_mix_matches_voice_pool_7p1`), so the only host-only
piece is the batched convolution itself (`gpu_render.GpuBusConvolver`, CuPy, mirrors the
CPU `PartitionedConvolver` already verified `== fftconvolve`). Measured ~6× RT at block 256
with the full tail on the RTX 3090. (→ L49)

### 3. Two clocks, one FIFO

The loopback capture clock and the headphone output clock are independent and both deliver
audio in bursts. The input callback renders freshly (it owns the newest head pose) and pushes
stereo blocks into a FIFO primed with a `--latency-ms` silence cushion; the output callback
drains it, zero-filling on underrun. The backlog is capped at a few cushions and drops the
oldest only on real drift, so latency stays bounded. This is the safe first duplex; a
native low-latency hot path is M8. (→ L50)

### Structure & tests

The world-lock geometry stays in the tested layer (`calibrate.directions_for_pose` on a
BRIR-ordered `layout_7p1`); `WorldLocked7p1` / `BusMixer` / `bus_targets` own only the voice
pools and the bus mix and are unit-tested (neutral == static render, LFE not steered, yaw
steers a channel to the rotated BRIR, bus-mix == voice pool). The app is a thin host shell
(camera + MediaPipe + the two audio streams). 6 new tests; 278 total, ruff clean.

### Host operational notes (also in AGENTS.md)

- CuPy 13 (`cupy-cuda12x`) needs a consistent CUDA-12 runtime in site-packages because the
  host's system toolkits are CUDA 11.x; install the full `nvidia-*-cu12` wheel set or cuFFT/
  nvrtc break.
- Launch the host Python with the cwd on a **real `C:` drive**, never the `\\wsl.localhost`
  worktree: CuPy's `_get_cuda_path` falls back to `os.path.exists('/usr/local/cuda')`, which
  resolves into the WSL mount from a `\\wsl.localhost` cwd and loads the wrong CUDA. (→ L51)

## Decision / next

The passthrough is implemented and everything CI can see is green: the bus reformulation is
proven identical to the validated voice pool, so the render maths is trustworthy. What remains
is inherently host-bound and is the owner's to run — loopback capture, the CuPy path, the
camera, and the continuous duplex can only be exercised on the Windows+RTX machine. The
headline acceptance ("running continuously without dropouts") is that live run.

Camera-loss graceful degradation already landed here (hold last pose, audio keeps locking,
periodic reopen), so **B4.2** is partly done; its remainder is audio device-change handling and
a deliberate fade.

# 79 — Ring bridge implemented (correct approach); the real blocker is audiodg not filling our render DMA

**Block:** B2 / Track B data plane (RFC `rfc-sysvad-real-loopback.md`, #112, PR #114) ·
**Milestone:** M8 / Track B · **Status:** the web-confirmed mechanism (render→capture ring) is built
and installed, but a persistent, now-isolated blocker remains: `audiodg` does not write the rendered
mix into our build's render DMA. Next = compare against a known-working SYSVAD/OpenAudioCable baseline.

## What was done

Per journal 78's web-informed redirect, implemented the **render→capture ring bridge** from the MIT
SYSVAD base (not the GPL OpenAudioCable): `make_ring_bridge.py` adds a device-wide byte ring to
`minwavertstream.cpp` — the render stream's `ReadBytes` calls `RingWrite(m_pDmaBuffer+off, run)`
(producer, next to the stock `SaveData`), and the loopback/capture `WriteBytes` calls
`RingRead(...)` in place of `m_ToneGenerator.GenerateSine` (consumer, silence on underflow). Applied
on a **stock, non-pull** `minwavertstream` (git-restored) plus the kept 7.1 + Win10-compat edits.

Build + install clean: `MSBUILD EXIT 0`, Win11-signed, installed in `kookr-bpre` (loaded hash
`85CADE60`), device `Status=OK`, no bugcheck.

## Test result — ring is empty

`planet_ring_test.py` played 500 Hz into the SYSVAD render endpoint and captured every SYSVAD input
endpoint. All of them (the mic-array capture endpoints, which now `RingRead` instead of the tone)
read **silent** (RMS ~1e-6). Since the capture side now drains the ring, silence = **the ring is
empty** = the render `ReadBytes`→`RingWrite` never received audio = **the render DMA is empty**.

(Caveat: the *actual* loopback pin is reached via WASAPI render-loopback — `pyaudiowpatch`
`get_loopback_device_info_generator` — which `kookr-bpre` doesn't have installed; the mic endpoints
are the only capture endpoints `sounddevice` enumerates. But the mic result already proves the ring
is unfed, which is upstream of any capture path.)

## The real, isolated blocker

`audiodg` does **not** deposit the rendered mix into our SYSVAD build's render DMA. This has now
survived **three** independent data-plane configs — pull-driven (journal 77), non-pull timer
(journal 77), and the ring bridge (here) — and the whole-DMA scan (journal 77) showed zero across the
entire buffer. So it is not the clock model, not the read position, not host-vs-offload (both render
pins hit `ReadBytes`), and not the ring. It is upstream of all of them: the engine isn't rendering
into this device's buffer.

OpenAudioCable (same CVCable/ring mechanism) **works**, so the difference is in **our build**, not the
mechanism. The remaining suspects, to isolate next:

1. **Our 7.1 endpoint edit (`make_speaker_71`)** or **Win10-compat edit** changed the render endpoint
   so `audiodg` won't render into it (format/topology mismatch — a render stream is created and
   advances, but no PCM is written). Isolate by building **stock SYSVAD + ring only** (no 7.1, no
   Win10 edits) in the VM and re-testing.
2. **Test setup**: the SYSVAD Speakers endpoint may not be the engine's actual render target for the
   DirectSound playback (default-device / routing). Set it as the default playback and retest.
3. **Install a known-working baseline** (stock SYSVAD, or build OpenAudioCable) on `kookr-bpre` and
   confirm *its* render DMA fills, to prove the host/VM + test harness are sound before blaming our
   edits.
4. **Proper loopback test tooling**: `pip install PyAudioWPatch` in `kookr-bpre` so the real
   render-endpoint WASAPI loopback (not the mic endpoints) can be measured.

## Banked / state

- `make_ring_bridge.py` + `planet_ring_test.py` committed — the correct mechanism, ready to run the
  moment the render feed is fixed. Plane T's section/MDL machinery (journals 77) remains banked.
- Host build tree: `minwavertstream` git-restored to stock + ring applied; 7.1/compat/dump kept; the
  **host's installed/running driver is untouched**. `kookr-bpre` VM has the ring build installed.
- Host otherwise clean; relaunch `run_pull.cmd` for audio (VoiceMeeter path).

## Decision / next

The mechanism question is **settled** (ring bridge, per MS + OpenAudioCable). The open question is
narrowed to a single, testable blocker — **why `audiodg` won't render into our endpoint's DMA** —
with a clear isolation plan (stock-SYSVAD-+-ring control, default-device check, working-baseline
install, pyaudiowpatch loopback test). That is the focused next session.

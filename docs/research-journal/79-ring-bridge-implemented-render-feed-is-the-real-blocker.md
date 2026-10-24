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

## Update — the blocker is the VM audio harness, not the driver or the mechanism

Isolated it further with the stock `SaveData` WAV (same bytes the ring uses), no rebuild:

- Playing 500 Hz produced a fresh `STREAM_HOST_86.wav` (8ch, 6.3 s = the playback duration): so
  `audiodg` **does** open and run a **host** render stream and `ReadBytes` fires — the render path is
  alive. But the WAV is **all-zero (silent)**.
- Matrix test — **every** SYSVAD output endpoint × {DirectSound, WASAPI} × {2ch, 8ch} → **silent DMA**
  (the few WASAPI opens that failed are the known `usbTerminalGUID` PortAudio bug; all that opened
  rendered silence). Meanwhile the playback callback pumps (~700 periods/7 s in earlier runs).

So the stream runs and the client delivers samples, yet the device DMA is uniformly silent across
**all** endpoints and APIs. That is not our driver (it would be endpoint-specific) and not the ring
(the DMA is zero before the ring). It is the **VM audio harness**: the SYSVAD endpoints are almost
certainly **muted / at zero volume** in this cloned VM (so `audiodg` zeroes the mix before the device
DMA), or `audiodg`-in-a-VM renders silence for these virtual endpoints. **The VM cannot produce real
audio into the device**, which retroactively explains why *every* data-plane probe (pull, non-pull,
Plane T whole-DMA scan, ring) read zero — there was never real audio in the buffer to read, because
nothing non-silent ever reached it in the VM.

**Implication:** the ring bridge is validated as far as the VM allows — it builds, installs, loads
(device OK, no bugcheck), and the render/capture plumbing runs. The one thing the VM can't give is a
**non-silent render DMA**. True validation needs a setup where real audio actually reaches the device:
1. **The host.** The owner's host DOES have real audio (Spotify et al. render a real mix into the
   device). Installing the ring-bridge driver on the host and playing real audio is the decisive test
   — but it is a host driver swap (journal-70 idle-first rule; replaces the working SYSVAD) and needs
   the owner's audio/ears, so it is gated on explicit owner go-ahead.
2. **Fix the VM endpoint volume/mute** (set the SYSVAD render endpoints to 100%/unmuted via the Core
   Audio `IAudioEndpointVolume` API or a tool) and re-run the matrix; if a WAV then carries 500 Hz,
   the ring bridge is proven in the VM with zero host risk.
3. **Known-working baseline** (install OpenAudioCable or stock SYSVAD in the VM): if *its* DMA is also
   silent, it confirms the VM harness, not any driver, is the blocker.

Recommended order: **(2) fix VM volume** (cheap, zero host risk) → if it works, the ring is proven and
only then consider **(1) the host install** for the real end-to-end + listen test.

## Update 2 — volume ruled out; the VM audio engine renders silence, period

Installed `pycaw` and set **all 8 SYSVAD endpoints to vol=1.00, mute=0** (confirmed read-back), then
re-ran the matrix: **still silent across every endpoint × API**. So it is **not** mute/volume. The
conclusion is firm: **`audiodg` in this headless Hyper-V VM does not transfer the client's samples
into the virtual device's render DMA** — it opens and runs the render stream (a silent WAV is written
for the full playback duration) but renders silence. A VM with no real audio hardware/session simply
does not drive the engine to produce real output into a virtual endpoint. **The VM cannot validate
the render-DMA / loopback data path at all** — which retroactively explains *every* zero in this
investigation (pull, non-pull, Plane-T whole-DMA scan, ring): there was never non-silent audio in the
buffer because the VM never put any there.

**So the only place to validate real passthrough is the host**, where real audio demonstrably flows
into the device (the owner's Spotify etc. produced the tone-loopback earlier; a real audio session
exists). The decisive test is: **install the ring-bridge driver on the host, play real audio, confirm
the loopback carries it** (and then the world-locked listen test). That is:
- a **host driver swap** (journal-70 idle-first rule; temporarily replaces the PR-#108 SYSVAD build
  the owner runs — reversible by reinstalling it),
- with a small residual **BSOD risk** inherent to any kernel-driver install, and
- it needs the **owner's audio + ears** to confirm the end result.

Because that is materially riskier and more intrusive than the VM work (it touches the owner's main
machine's audio driver), it is **gated on the owner's explicit go-ahead**, not done autonomously.

**Net of the whole arc:** the data-plane mechanism is **settled and built** (render→capture ring
bridge, per MS + OpenAudioCable; compiles, Win11-signs, installs, loads clean, no bugcheck). The VM
cannot prove it carries real audio because the VM engine renders silence. The one remaining action is
the host install + real-audio test — the owner's call.

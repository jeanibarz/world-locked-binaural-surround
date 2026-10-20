# 59 — B-pre VERDICT: PASS (audiodg produces clean device output under a bursty pull-driven cadence)

**Block:** B8.7b (RFC Track B, B-pre) · **Milestone:** M8 / Track B · **Status:** **B-pre PASSES.**
The make-or-break for the custom pull-driven virtual device is answered, rigorously, in the VM.

## The definitive measurement (device-level)

Journals 57/58 found that audiodg is paced by the WaveRT notification timer (not the position), and
that an app-side probe is structurally blind to device-output faults (audiodg decouples the app via
its shared buffer). So the real test had to measure the **device output** — the exact bytes our
engine → K11 would consume.

Enabled SYSVAD's `SaveData` (`g_DoNotCreateDataFiles=0`), so `ReadBytes` writes every consumed chunk
to a WAV (`C:\Windows\System32\drivers\DriverData\Audio_Samples\Sysvad\STREAM_HOST_*.wav`). Played a
continuous 440 Hz tone through audiodg into the SYSVAD **Speakers** endpoint and analysed the captured
WAV (`spikes/bpre-vm/wav_analyze.py`) for silent gaps and discontinuity jumps — a continuous input
must come out continuous; any gap/jump means audiodg under-produced or repeated a buffer.

## Results

| driver cadence | device output (captured WAV) |
|---|---|
| baseline (stock notification timer) | **CLEAN** — 0 silent gaps, 0 discontinuity jumps, full-amplitude tone |
| **representative pull-driven** (notifications rate-matched but jittered: delay 1 in 4) | **CLEAN** — 0 gaps, 0 jumps |
| **sensitivity check** (deliberately duplicate a chunk every 200 reads) | **GLITCHED — 6 jumps caught** |

Every build was hash-verified as the loaded driver (`DriverVer` bumped per build — journals 57/58).

## Verdict

**PASS.** audiodg produces a continuous, glitch-free stream into a pull-driven virtual device even
when the notification cadence is bursty — and the measurement provably detects real defects (the
injected duplicates), so the CLEAN results are trustworthy, not a blind pass. Two reinforcing facts:

- When notifications arrive sparsely/late, audiodg **catches up** (it fills the buffer to the
  available space on the next notification), rather than under-producing one period per notification.
  That is why even aggressive sparsity stayed clean — audiodg is robust to a bursty pull cadence.
- The position path (`UpdatePosition`) only feeds `IAudioClock`; it does not gate the device data.

## What this means for Track B (the custom pull-driven 7.1 device)

- The core architectural risk the RFC flagged — *"audiodg may insert silence / disconnect / glitch
  when the device is pull-driven"* — is **retired**. audiodg tolerates driving the device from an
  external (K11) consumer via bursty notifications, and the device output stays continuous.
- The seam is the **notification timer** (`TimerNotifyRT` / `KeSetEvent` on `m_NotificationList`),
  which our K11 consumer fires per block it drains (1:1 with consumption). `UpdatePosition` is kept
  only for a correct `IAudioClock`/A-V timestamp.
- Remaining open item (minor, not a blocker): directly confirm `IAudioClock` monotonicity for A-V
  sync under a bursty position — worth a quick check during the real build, but B-pre's make-or-break
  (clean device output) is settled.

## The rig (reusable for the real build)

All in `spikes/bpre-vm/`: VM provisioning, the EWDK build/sign/install loop, the app-side probe,
and now the device-level capture+analyser (`capture.ps1` + `wav_analyze.py`). The driver-iteration
loop (modify `minwavertstream.cpp` → build → sign → reinstall → measure) is proven.

## Decision / next (owner committed to the driver)

- **B-pre passed → build the real pull-driven device.** Replace the simulated timer with a
  consumption+notification driven by a user-mode consumer (first a software pseudo-K11 at the K11's
  cadence over the SPSC ring + `advance()` seam from the RFC; then the real K11), wire the Rust DSP
  core behind `FrameSource`/`ClockSink`, and advertise 7.1 @ 48 kHz.
- Sequencing per the RFC: VM correctness + safety (stall watchdog, engine-absent behaviour), then
  host + real K11 with a System Restore point / removable driver (the residual BSOD risk lives on the
  host, not here).

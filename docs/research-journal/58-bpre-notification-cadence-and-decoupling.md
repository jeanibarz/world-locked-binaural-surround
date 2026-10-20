# 58 — B-pre: audiodg tolerates a bursty notification cadence, and decouples the app from the device

**Block:** B8.7b (RFC Track B, B-pre) · **Milestone:** M8 / Track B · **Status:** B-pre's core
question answered **favorably** for the representative case; a device-level measurement would make
it definitive. Owner committed to the custom driver (so this continues toward it).

## Setup

Continues journal 57 (which found audiodg is paced by the WaveRT **notification** events, not
`GetPosition`). Here I drove the *notification cadence* itself — the real pacing seam — and measured
with the same in-VM Python/WASAPI probe. Each run was hash-verified as the loaded driver and the
`DriverVer` was bumped per build (journal 57's caching trap).

## Experiments and results

| change to the driver | what it simulates | app-side probe |
|---|---|---|
| position bursty / frozen (journal 57) | pull-driven *position* | CLEAN (position doesn't pace audiodg) |
| notification **delayed 1 in 4** (rate-matched, jittered) | **the representative pull-driven cadence** | **CLEAN** |
| notification **half-rate** (a permanent deficit) | an *artificial* starvation, not the real design | CLEAN app-side |

## Two findings

1. **The representative pull-driven cadence is tolerated (app-side).** A K11-driven consumer fires
   one notification per block it consumes — rate-matched to audiodg, but with timing jitter/bursts.
   Delaying 1 in 4 notifications (a ~2x-interval gap, then catch-up; still 1:1 on average) produced
   **no underflows, monotonic clock, correct rate**. That is the make-or-break cadence, and audiodg
   absorbed it.

2. **audiodg decouples the app from the virtual device** (why even half-rate looked clean app-side).
   The app writes into audiodg's shared buffer at its own pace; audiodg's write *to the device* is a
   separate, buffered stage. So **an app-side probe cannot observe device-level pacing faults** — a
   device under-production lands on the *device output* (what our engine → K11 consumes), which a
   virtual device discards. My half-rate test created an artificial consume-full/notify-half deficit
   whose glitch would hit the device output, invisible to the app — and it is also *not* the real
   design (there, notifications == consumptions, 1:1).

## What this means for the pull-driven design

- In the real design our engine consumes a block and fires a notification per block (**1:1**), so
  audiodg produces exactly as fast as we consume — just in bursts matching our consumption. The only
  failure mode is audiodg **under-producing within a single notification gap**; the representative
  (1-in-4 jittered, rate-matched) test says it does not. **Encouraging for Track B.**
- The seam to drive from the K11 is the **notification timer** (`TimerNotifyRT` / `m_pNotificationTimer`
  / the `KeSetEvent` on `m_NotificationList`), **not** `UpdatePosition` — correcting journal 53's
  pre-M1 analysis. `UpdatePosition` is still needed for a correct `IAudioClock` (A-V sync), but it
  does not pace audiodg.

## To make B-pre definitive (the remaining measurement)

The app-side probe is structurally blind to the device output. A definitive verdict needs a
**device-level** measurement under the representative (1:1 jittered) cadence, one of:
- enable SYSVAD's `SaveData` (ReadBytes → WAV) and check a known tone played through audiodg for
  gaps/discontinuities at the device output; or
- count SYSVAD's own `eMINIPORT_GLITCH_REPORT` ETW underrun events; or
- a real user-mode consumer reading the device and checking for stale/short reads.
- Separately, read **`IAudioClock`** directly to confirm A-V-sync monotonicity under a bursty position.

## Methodology (reinforces journal 57)

- Every "clean" was hash-verified as the modified driver, and `DriverVer` bumped per build.
- **Know what your probe can and cannot see.** The app-side probe measures the app↔audiodg stage,
  not audiodg↔device — so it is the right tool for "does playback survive" but the wrong tool for
  "does the device output stay continuous." Match the measurement to the stage under test.

## Decision / next (owner committed to the driver)

- Build the **device-level** measurement (SaveData output analysis is the most direct) and re-run
  the representative 1:1-jittered cadence for a definitive B-pre verdict; add a direct `IAudioClock`
  read for A-V sync.
- If definitive-clean: B-pre passes → proceed to the real pull-driven device (drive the notification
  timer + consumption from a pseudo-K11, then the K11). Rig + driver-iteration loop are built
  (`spikes/bpre-vm/`).

# 60 — B1 slice 1: the virtual device is driven externally (pull-driven) by a user-mode consumer

**Block:** B8.7b (RFC Track B, B1) · **Milestone:** M8 / Track B · **Status:** B1 slice 1 done in the
VM — the SYSVAD device is now **pull-driven from user mode** (no internal timer), ~real-time,
near-clean output, VM stable. Two real BSOD-class bugs found + fixed (the whole reason for the VM).

## What B1 slice 1 does

Replaced SYSVAD's internal `ExTimer` (which paced audiodg) with an **external drive**: on
`KSSTATE_RUN` the driver opens a named sync event and starts a system thread that waits on it; each
signal calls `PullAdvance()` (advance one notification period: consume via `ReadBytes` + fire the
buffer-complete notification). A user-mode **pseudo-K11** (`spikes/bpre-vm/pseudo_k11.py`) creates
the event and signals it at the K11 cadence (~10 ms), standing in for the real K11 playback
callback. A 100 ms free-run watchdog keeps the device from wedging if no one ticks. `GetPosition`
no longer wall-clock-interpolates in pull mode (the position is advanced only by ticks).

## Result

With the pseudo-K11 ticking at 10 ms: the captured device-output WAV was **13.5 s of audio in ~14 s
wall-clock** (≈real-time) with **0 silent gaps and 13 discontinuities** — vs 909 in free-run and the
slow 100 ms pacing. The ~1/s discontinuities are almost certainly the Python ticker's timing jitter
(an occasional late tick → a stale read); the real K11's hardware-timed callback should be tighter.
The mechanism — user-mode drives the kernel device, audiodg produces to match, device output stays
continuous — is proven.

## The two bugs the VM caught (host-risk, now fixed)

1. **BSOD `0x18 REFERENCE_BY_POINTER`** (param4 = `0xffff...ffff`, an over-dereference). Cause:
   `IoCreateSynchronizationEvent` returns a pointer **owned by the handle** — I wrongly
   `ObDereferenceObject`'d it on cleanup, over-decrementing the ref count. Fix: close the handle
   only, never dereference that pointer. (The VM rebooted from the bugcheck; `MEMORY.DMP` +
   `Get-WinEvent Id=1001` gave the code.)
2. **`ACCESS_DENIED` (err 5)** opening the event from user mode: a kernel-created named event's
   default DACL denies the interactive user. Fix: the **user-mode consumer creates** the event
   (with a NULL DACL it controls) *before* the stream starts, and the driver `IoCreateSynchronization
   Event` **opens** the existing one. Also sidesteps `ZwCreateEvent`/`SECURITY_DESCRIPTOR` not being
   declared in this translation unit's header set.

## Lessons (durable)

- **Object ref-count discipline is the #1 BSOD source here.** `IoCreate*Event` → pointer owned by
  the handle (no deref); `ObReferenceObjectByHandle` → you own a ref (must deref). Never mix them up.
- **Driver-created named objects aren't user-openable by default** (DACL). Easiest: have user mode
  create the shared object, the driver opens it.
- The VM contained a real bugcheck with zero host impact. This is exactly why B2/B3 (the real K11 on
  the host) must wait until the driver survives extended stress here.

## Decision / next (toward the real K11, safely)

- **Harden before any host attempt:** many start/stop cycles, stop-the-consumer mid-stream (watchdog
  must keep it safe, not crash), engine-absent/teardown races — the driver must survive all of it in
  the VM with zero bugcheck before it touches the host.
- Tighten the cadence (match the notification interval; a C/precise ticker) to drive the
  discontinuities to 0 — or confirm they vanish with the real hardware-timed K11.
- Then B2: the real K11 consumer (read 7.1 from the device → Rust binaural DSP → WASAPI exclusive to
  the K11, firing the advance 1:1 with the K11 callback) on the host, with Secure Boot off +
  System Restore + removable driver + a tested recovery path.
- Driver diff lives in the cloned `audio/sysvad` (MS sample, not vendored); the reproducible rig +
  consumer are in `spikes/bpre-vm/`.

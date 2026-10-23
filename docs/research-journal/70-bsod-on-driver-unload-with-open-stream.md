# 70 — The BSOD: never unload the virtual driver to clear a stuck stream (reboot instead)

**Block:** B8.7b (RFC Track B, B2) · **Milestone:** M8 / Track B · **Status:** done — root cause
recorded; the B2 consumer harness now tears down safely and a NON-NEGOTIABLE guard is in AGENTS.md.

## What happened

A Track-B session bringing up the B2 consumer (journal 69's 7.1 SYSVAD sink → `RustWorldLocked7p1`
→ WASAPI-exclusive K11, pull-driven by `Global\KookrPullTick`) **bugchecked the host** — a real
BSOD that lost the whole session. The driver install itself was clean (journal 68/69: loads
`CM_PROB_NONE`, zero bugcheck, PR #100/#101 merged). The crash came **after**, during the first live
run of `b2_consumer.py`.

## Root cause (reconstructed from the session transcript + the owner)

The first draft of `b2_consumer.py` (`wip(b2)… host run hangs, NOT working yet`) **hung** on the
host. The hang left a **half-open stream on the SYSVAD sink**: the loopback capture and/or the
pull-driven output never closed, so the device kept a stream pinned and the named kernel pull-event
`Global\KookrPullTick` stayed held (a fresh `CreateEvent` returned `err=5`, `ACCESS_DENIED` — stale),
with `audiodg` still attached to the endpoint.

Trying to clear that stuck stream, the session escalated: `devcon restart Root\Sysvad_ComponentizedAudioSample`
returned *"Requires reboot"* (a root-enumerated device can't be hot-restarted), the event stayed
stale — and then an attempt to **uninstall / force-unload the driver** to drop the stream faulted the
driver's teardown path in kernel mode → **bugcheck**. SYSVAD is a kernel-mode PortCls/WDM driver;
unloading it while a stream and its pull-event are still live dereferences objects the open stream
still owns.

The transcript ends mid-diagnosis at the "who holds the handle" probe (no lingering python, `audiodg`
alive, no active render session, no `handle.exe`) — i.e. exactly when the force-unload was attempted.

## What the reboot proved

After the owner rebooted, this session verified the host over the SSH admin channel, read-only:

- Device `ROOT\MEDIA\0004` — `Status OK`, **`CM_PROB_NONE`** (the driver is fine; the install held).
- `Global\KookrPullTick` — fresh `CreateEvent` → **`err=0`** (clear; no stream pins it).
- `audiodg` — **not running** (no active session).

So the leak was **stream state, not driver state**. A reboot clears a stuck stream completely and
safely; unloading the driver to do the same is what crashes.

## The fix

1. **`b2_consumer.py` — guaranteed, driver-safe teardown.** The whole run (open → wait → close) is
   wrapped in `try/finally`; every exit path (normal, exception, `KeyboardInterrupt`, or a
   **watchdog** deadline at `seconds + 15 s`) routes through a single idempotent `teardown()` that
   closes the three audio streams and the pull-event handle — and **never touches the driver**. After
   teardown it probes `Global\KookrPullTick` and, if still pinned (or if the run crashed), prints a
   **SAFE RECOVERY banner**: *reboot to clear; do NOT uninstall / remove / disable / `devcon restart`
   the driver.* First validate on a fresh boot with `--mode shared --seconds 6` (the exclusive-open
   leg is what can wedge), then raise duration / switch to exclusive.

2. **AGENTS.md safety contract** gains a NON-NEGOTIABLE rule for the (now host-installed, test-signed)
   virtual audio driver: a stuck stream is cleared **only by a reboot**, never by unloading the driver.

## Learnings

- **Unloading a kernel audio driver to force a stuck stream shut bugchecks the host.** The stream, not
  the driver, is stuck; the safe lever is a **reboot**. (Added to AGENTS.md + LEARNINGS.)
- **A half-open stream pins `Global\KookrPullTick`.** It is the canonical liveness probe: `err=0/183`
  = clear, `err=5` = a stream still holds the device. Any host audio harness must guarantee stream
  teardown on *every* exit path, and must never hang unbounded — a wedged exclusive open is what
  created the leak.
- **Root-enumerated SYSVAD can't be hot-restarted** (`devcon restart` → "Requires reboot"), so there
  is no mid-session "just restart the device" escape hatch; design the harness so one is never needed.

## Decision / next

Harness teardown hardened and the guard recorded (this PR). **Next (unchanged from journal 69's
handoff):** run the now-safe B2 consumer live — `--mode shared --seconds 6` smoke on a fresh boot,
then the real exclusive latency sweep against the journal-48 baseline — and only ever reboot (never
unload the driver) if a run leaves the endpoint stuck.

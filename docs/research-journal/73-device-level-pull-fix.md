# 73 — The fix for the beeps: one device-level pull thread advances every stream

**Block:** B8.7b (RFC Track B, B2, issue #104) · **Milestone:** M8 / Track B · **Status:** fix written
and **VM-validated** (compiles, loads `CM_PROB_NONE`, survives register/unregister stress with zero
bugcheck). **Host rollout is pending** a DriverStore cleanup (a French-localisation tooling bug, below)
+ a reboot; the balance/no-beeps measurement happens after that.

## The change

Journal 72 traced the variable-pitch beeps to tick-stealing: every pull-mode stream opened the same
auto-reset `\BaseNamedObjects\KookrPullTick` and ran its own pull thread, so one `SetEvent` advanced
only one stream (balance = 1/N streams). The fix (`make_device_pull.py`, patching
`minwavertstream.cpp/.h`) moves the pull from per-stream to **one device-wide pull thread**:

- A file-scope registry: `g_PullList` (active streams), a `KMUTEX` `g_PullMutex` serialising
  register/unregister at **PASSIVE_LEVEL** (required — `PsCreateSystemThread` /
  `IoCreateSynchronizationEvent` / `KeWaitForSingleObject(thread)` are PASSIVE-only; a guarded/fast
  mutex would raise to APC_LEVEL and bugcheck), and a `KSPIN_LOCK` `g_PullSpinLock` guarding the list
  against the pull thread's iteration.
- Each stream on RUN calls `PullRegister(this)` (adds to the list; opens `KookrPullTick` + starts the
  single pull thread if it is the first); on PAUSE and in the dtor it calls `PullUnregister(this)`
  (removes from the list; stops the thread + closes the event if it is the last). Both idempotent via
  a `m_PullListEntry.Flink == NULL` guard.
- `PullThreadRoutine` now waits on the one `KookrPullTick` and, per tick, takes `g_PullSpinLock` and
  `PullAdvance()`s **every** registered stream — so one K11 tick advances the whole device one period,
  for any number of streams. The 100 ms free-run watchdog is preserved.

So `balance → 1.0` regardless of how many apps feed the sink, restoring the single-master-clock intent
of journal 62/63 without the two-clock drift of a free-running thread.

## VM validation (Win11 `kookr-bpre`, checkpoint `pre-b2-devicepull`)

- **Build:** EWDK (EndpointsCommon + TabletAudioSample), `MSBUILD EXIT 0`; all imports resolve on
  Win10 19045 (portcls 9 / ntoskrnl 66 / wdfldr 5 / hal 1). `.sys` sha256 `6A7D2A2B…`.
- **Load:** force-reinstalled pkg10; device `CM_PROB_NONE`, all 8 endpoints OK, **no bugcheck**.
- **Stress** (`stress_b1.ps1`): 15 rapid start/stop cycles (register/unregister churn) + a watchdog
  test (kill the ticker mid-stream) → `cycles done`, `watchdog test done`, device still
  `CM_PROB_NONE`, VM alive. The device-level pull thread create/destroy path is crash-safe.

## Host rollout — blocked on a tooling bug (not the driver)

Installing on the Win10 host via `host_install_and_test.ps1` bound the device to an **old** DriverStore
package (`DA3A02FE…`), not the rebuilt `6A7D2A2B…`. Cause: the install scripts parse `pnputil
/enum-drivers` output for the English `Published Name:` / `Original Name:` lines, but the host is
**French** (`Nom publié :` / `Nom d'origine :`), so the stale-package delete loop silently matched
nothing and five `componentizedaudiosample.inf` packages accumulated. (Journal 54 already hit host
French-localisation — the authorized-key ACL needed SIDs. The driver-store scripts regressed on it.)

**Fix direction:** make the cleanup language-agnostic — enumerate with `pnputil /enum-drivers
/format:xml` (stable element names) or delete by the provider/class match rather than the localised
label, remove all stale `componentizedaudiosample` packages, reinstall pkg10, then **reboot** to bind
our build cleanly. The reboot is gated on the owner's explicit go.

## A historical host bugcheck to note

The host logged a **0x3B** (SYSTEM_SERVICE_EXCEPTION, `c0000005`) at ~20:36 during this session's
earlier `b2_consumer` exclusive testing **on the old per-stream-pull driver**; it rebooted itself and
has been stable since (~80 min). Not caused by the new build (which never ran on the host) nor by the
install. Plausibly a latent fault in the old per-stream pull under multi-stream exclusive load — which
this device-level fix removes — but the `MEMORY.DMP` (4.8 GB) was not analysed. Flagged for follow-up.

## Decision / next

1. This PR preserves the VM-validated fix (`make_device_pull.py` + the refreshed vendored patch).
2. Host rollout (owner-gated reboot): fix the French-safe DriverStore cleanup, reinstall pkg10, reboot,
   then run `b2_consumer` (producer + loopback) and confirm `balance → 1.0`, `out_starve → 0`, and the
   owner hears clean audio (no beeps). Safety (journal 70): never unload the driver to clear a stuck
   stream — reboot.

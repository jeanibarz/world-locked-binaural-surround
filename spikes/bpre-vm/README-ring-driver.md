# Ring-bridge SYSVAD driver — build & install (operator guide)

This is the VoiceMeeter-free data plane: the SYSVAD virtual 7.1 device's loopback pin carries the
**real rendered system mix** (render→capture ring bridge), so `--pull-sysvad` world-locks live audio
with no VoiceMeeter. Design + proof: [`docs/rfc/rfc-sysvad-real-loopback.md`](../../docs/rfc/rfc-sysvad-real-loopback.md)
(v5) and research journals 77–80.

## TL;DR — three commands

Run on the **Windows host**, elevated. `build_*` is non-elevated-OK; `install_*` and the test-mode
scripts need elevation.

```powershell
# 1. ONE-TIME: trust the test cert + enable test-signing, then REBOOT (a full Windows restart).
powershell -ExecutionPolicy Bypass -File host_arm_testmode.ps1
#    ...reboot the host...

# 2. BUILD the signed package reproducibly (applies all 3 source edits, builds, Win10-ports, signs).
powershell -ExecutionPolicy Bypass -File build_ring_driver.ps1          # => D:\kookr-vm\pkg10

# 3. INSTALL / reinstall robustly (no second reboot; idle-first reload, no bugcheck).
powershell -ExecutionPolicy Bypass -File install_ring_driver.ps1
```

Then listen:

```
C:\Users\ibarz\BinauralHeadtracker\run_pull.cmd listen --pull-sysvad --ui cockpit --block 512 --latency-ms 40
```

## What `build_ring_driver.ps1` guarantees (the reproducibility fix)

The shipped driver is **stock WDK SYSVAD + exactly three idempotent source edits**. Before, two of
them were applied by hand and wired into no script, so a clean build silently dropped the ring. The
orchestrator applies all three deterministically and verifies the ring landed:

| # | Edit | Applied by | Files |
|---|------|-----------|-------|
| 1 | stereo → 7.1 render endpoint | `make_speaker_71.py` | `EndpointsCommon/speakerwavtable.h`, `speakertoptable.h` |
| 2 | Win10 down-level lookaside compat | `apply_win10_compat.ps1` | `common.cpp` |
| 3 | render→capture ring bridge (real loopback) | `make_ring_bridge.py` | `EndpointsCommon/minwavertstream.cpp` |

**Not applied:** `make_device_pull.py`. The ring build is **non-pull on purpose** — pull-driving the
render stream stops `audiodg` filling its DMA (RFC v5 / journal 79). Do not add the pull edit.

Prerequisites (one-time, via the `stage1..6` scripts): the EWDK image `D:\kookr-vm\ewdk.iso`, the
staged package `D:\kookr-vm\pkg`, and the `CN=KookrTestDriver` signing cert in `CurrentUser\My`.
`build_ring_driver.ps1` checks these and fails early with the fix if any is missing.

## What `install_ring_driver.ps1` does (easier + robust)

- **No bugcheck, no reboot for reinstalls.** It stops the audio service first so every stream closes
  and the device goes **idle** (journal-70 rule: replacing a driver under an open stream bugchecks),
  swaps the package, cycles the PnP device, then restarts audio.
- **Idempotent.** Removes any prior `ComponentizedAudioSample` package from the store, then adds the
  fresh `pkg10`. Safe to re-run after every rebuild.
- **Bounded blast radius.** The SYSVAD endpoint is **not** made the default playback device; the
  script records the default before and asserts it is unchanged after.
- **Verified.** Confirms the loaded `.sys` hash matches the package, the endpoint is `OK`, the
  default device is unchanged, and no bugcheck fired. Writes `D:\kookr-vm\ring_install_result.txt`.
- **Gated.** Refuses to run unless test-signing is **live in the running kernel** (not merely
  configured) — proven via `LastBootUpTime` vs the `host_arm_testmode.ps1` armed-at marker.

## Rollback

```powershell
powershell -ExecutionPolicy Bypass -File host_revert.ps1   # remove device+package, test-signing off
#    ...reboot to leave test mode...
```

Manual rollback to the prior build: `pnputil /delete-driver <oemNN.inf> /uninstall /force` then cycle
the device (the previously staged package reactivates). Find `<oemNN.inf>` with `pnputil /enum-drivers`.

## Known follow-on (not an install concern)

The ring is a raw byte FIFO with no rate reconciliation or fill-level telemetry; under long sessions,
clock drift between the render and loopback DPCs could let the fill creep (→ silence on underflow, or
a click when `RingWrite` drops oldest on overflow). A fill-level log + a few-hour soak is the way to
confirm/deny this; if it drifts, add light fill-centering at the ring boundaries. Tracked in RFC v5.

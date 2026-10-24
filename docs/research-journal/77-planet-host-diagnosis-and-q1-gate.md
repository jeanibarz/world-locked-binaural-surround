# 77 — Plane T groundwork: host diagnosis confirms a driver change is required; Q1 gated on a VM build

**Block:** B2 / Track B data plane (RFC `rfc-sysvad-real-loopback.md`, issue #112, PR #113) ·
**Milestone:** M8 / Track B · **Status:** host Phase-0 done (decisive); Q1 (render DMA holds the
real mix?) blocked on the installed builds → needs a controlled VM driver build. No kernel code
written yet.

## What this entry records

An autonomous Phase-0 diagnosis run directly against the owner's host (WSL→Windows interop +
elevated SSH `ibarz@172.17.96.1`, `kookr_host_admin` key, session `elevated=True`), to decide —
before any kernel work — *which plane is broken* and whether the chosen **Plane T (tick-return)**
is viable (RFC v4, owner's choice).

## Decisive finding (host): the capture/loopback plane is a hardcoded tone — a driver change IS required

Rendered a controlled **500 Hz** signal into the SYSVAD 7.1 render endpoint (`Haut-parleurs
(Virtual Audio Device (WDM) - Tablet Sample)`, WASAPI dev 47) and simultaneously captured its
**WASAPI render-loopback** (the exact `get_loopback_device_info_generator` path the app uses).

Result: the loopback returned a **constant 3000 Hz, 0.5-amplitude tone on all 8 channels**
(uniform RMS 0.3534 = a 0.5-amplitude sine; the driver's `LoopbackCaptureToneFrequency=3000`,
`LoopbackCaptureToneAmplitude=50`), **invariant to every lever tried**:

- what is rendered (the 500 Hz never appeared);
- registry overrides set via elevated SSH under the service key
  `HKLM\SYSTEM\CurrentControlSet\Services\sysvad_componentizedaudiosample`
  (`DisableToneGenerator=1`, `LoopbackCaptureToneFrequency=1234`, `DoNotCreateDataFiles=0`) —
  **all ignored**, so the installed build does not read these from the service key;
- a full device disable/enable restart (done safely with the device idle after stopping the
  passthrough app; **no bugcheck**, device returned `Status=OK`, problemcode 0).

**Conclusion:** the loopback carries the driver's synthesized tone regardless of rendered content
or config. The cheap fixes (registry/config, a different endpoint) are **ruled out** — a driver
change is genuinely required, exactly as the RFC concluded. This confirms the RFC's premise with
host evidence and closes #112's "is it routing?" question: it is not.

Also observed: WASAPI **render into** the SYSVAD endpoint is intermittently flaky on the host
(`-9999 Unanticipated host error, GetNameFromCategory usbTerminalGUID=9324`); it rendered once,
failed on later attempts regardless of app contention. The `Haut-parleurs` endpoint also reported
PnP `Status=Unknown` at one point. The installed render endpoint is in a fragile state.

## The gate that remains: Q1 — does the real mix reach the render DMA?

Plane T returns the render DMA's just-consumed window, so its go/no-go is **Q1: does `audiodg`
deposit the real, per-app-volume mix into this pull-driven, DAC-less device's render DMA?** The
loopback test does **not** answer this (the loopback pin is tone-fed independently of the render
DMA). The intended oracle — the driver's `ReadBytes`/`SaveData` render-DMA WAV dump
(`g_DoNotCreateDataFiles=0`, path `…\DriverData\Audio_Samples\Sysvad\*.wav`) — **produced no WAV
on either the host or the `kookr-bpre` VM** (`capture.ps1 -Dur 8` → "NO WAV CREATED"). So Q1 is
unmeasured: the installed builds either don't hit `ReadBytes` on the render stream (pull-mode
render path), write elsewhere, or have the dump effectively off.

**Therefore Q1 itself now needs a controlled driver build** — a minimal, robust render-window
dump (e.g. a direct `ZwWriteFile` of the consumed window in the render stream's `PullAdvance`,
not the finicky `SaveData`), built with the EWDK and installed in the VM. That is the first unit
of the implementation, and it answers Q1 before the full Plane-T IPC is built.

## Dev path confirmed ready (so the next session starts oriented)

- Elevated host access: `ssh -i ~/.ssh/kookr_host_admin ibarz@172.17.96.1` (full admin token).
- Full SYSVAD source on the host build tree: `D:\kookr-vm\wds\audio\sysvad\` (`EndpointsCommon`,
  `TabletAudioSample`, …).
- Build + test tooling on `D:\kookr-vm\` (`apply_win10_compat.ps1`, `build_sysvad.ps1`,
  `build_ewdk.ps1`, `install_driver.ps1`, `forceinstall.ps1`, `capture.ps1`, `bpre_probe.py`,
  `wav_analyze.py`, `pseudo_k11.py`, `b2_consumer.py`).
- Dev VMs running: `kookr-bpre` and `kookr-bpre-w10` (Win10, matches the host); PowerShell-Direct
  reachable (`kookr` / `Kookr-VM-2026!`), Python present, SYSVAD MEDIA device `Status=OK`.
- Pull architecture understood (`make_device_pull.py`): a single device-wide pull thread waits on
  `\BaseNamedObjects\KookrPullTick` (consumer-created, driver opens via `ZwOpenEvent` +
  `ObReferenceObjectByHandle(*ExEventObjectType)`), and per tick iterates `g_PullList` under
  `g_PullSpinLock`, calling `PullAdvance()` on each stream (render → `ReadBytes`; capture →
  `WriteBytes`/tone). 100 ms watchdog free-runs when no consumer.

## Plane T design (settled enough to implement)

`KookrPullTick` is a bare auto-reset event — **it cannot return data**. So Plane T = the existing
event for timing **plus a named shared-memory section** (`KookrPullData`) for the payload, same
ownership pattern as the event (consumer creates with a permissive DACL; driver opens + maps):

1. In the render stream's `PullAdvance`, after computing the consumed window, memcpy it into the
   section's ring at a write index + sequence, then **zero the source window** (take-and-clear →
   D3 silence on under-run/absence). Runs inside the existing `g_PullSpinLock` critical section
   (no new lock; render-before-capture already serialized by the single pull thread).
2. Consumer maps the section, sets `KookrPullTick` per K11 period (as today), and its render
   worker reads the newest sequence from the ring instead of the WASAPI loopback.

This removes the loopback pin, the `audiodg` capture hop, and the H1/H2/H3 ambiguity; it is
phase-locked by construction. **First build the Q1 dump (above); only on Q1=YES build the section.**

## Decision / next

1. **Build the Q1 render-dump** (`ZwWriteFile` of the consumed render window) in the VM; play a
   distinctive signal into the VM's SYSVAD, read the file, FFT it:
   - contains the signal → **Q1=YES** → implement the `KookrPullData` section (Plane T).
   - silence → **Q1=NO** → the gap is upstream (parent RFC B-pre: `audiodg` isn't rendering a real
     mix into this pull-driven device) — pivot there, Plane T can't help.
2. Then implement Plane T, validate in the VM (B1-vm: dump==rendered bit-correct; silence on
   absence; repeated start/stop no bugcheck), and only then the host listen test (owner's ears).

No kernel code committed here — this entry is the groundwork + decision record so the build phase
starts from evidence, not a wrong premise. The host was left clean (registry overrides reverted,
device `Status=OK`, probe scripts removed); the passthrough app was stopped during testing and
should be relaunched with `run_pull.cmd`.

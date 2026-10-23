# 68 — The Win10 driver-load fix: two NI-gated lookaside imports from unused sideband code

**Block:** B8.7b (RFC Track B, B1→B2) · **Milestone:** M8 / Track B · **Status:** **RESOLVED** — the
test-signed SYSVAD virtual device now **loads** (`CM_PROB_NONE`) on Windows 10 19045, both in a
purpose-built Win10 VM and on the owner's host. This closes the blocker journals 65/66/67 left open
("installs but will not load on Win10").

## In plain terms

The custom virtual sound-card driver was built with the Windows 11 toolkit. It called two tiny memory
helpers that Windows 11 provides as real functions but Windows 10 only has as "copy-paste" inline code
— so on Win10 the driver asked for two functions that do not exist, and Windows refused to start it
(no crash, just "driver failed to load"). Those two calls live only in the Bluetooth/USB/A2DP headset
code, which our virtual speaker never uses. The fix swaps just those two calls for memory functions
Windows 10 *does* export, so the driver loads on both Win10 and Win11. Verified by rebuilding and
checking every function the driver imports against the live Win10 kernel, then loading it for real in
a throwaway Win10 virtual machine and on the host.

**Glossary.** *Import* — a function a driver borrows from the kernel by name; if the running kernel
does not export that name, the driver cannot load. *NTDDI gate* — a `#if` in the Windows headers that
switches an API between "real exported function" and "inline code" depending on the Windows version
you build for. *Lookaside list* — a fast fixed-size allocator. *PortCls / WaveRT* — the kernel audio
miniport framework. *CM_PROB_NONE (0)* — Device Manager code meaning the device started with no
problem.

## What happened when we retried on the host (after the real reboot)

Journal 67 left one action: a genuine reboot into test mode, then run the install. The host did reboot
(`LastBootUpTime 2026-10-23 14:58`, after the `testmode_armed_at` marker; Fast Startup off), so the
hardened gate passed and the **Win10-gated INF finally bound a service** (`sysvad_componentizedaudiosample`,
`oem161.inf`) — further than journals 65/66 ever got. But the node came up:

```
Status=Error  Problem=CM_PROB_DRIVER_FAILED_LOAD (39)  ProblemStatusNT=0xC0000263  (STATUS_DRIVER_ENTRY_NOT_FOUND)
System event 219: driver load failed for ROOT\MEDIA\0004
```

**No bugcheck, no CodeIntegrity 3004** — exactly the *safe* downlevel-load failure journal 65 predicted
("if it imports a KS/PortCls DDI absent on Win10 it simply fails to load"). So test-signing was fine;
the binary itself referenced something the Win10 kernel does not have.

## Root cause (pinned by import/export diff against the live kernel)

`dumpbin /imports` on `TabletAudioSample.sys`, diffed against the live `C:\Windows\System32\ntoskrnl.exe`
export table, gave exactly two unresolved symbols:

```
ExAllocateFromNPagedLookasideList
ExFreeToNPagedLookasideList
```

All 9 PortCls, the other 66 ntoskrnl, HAL and WDFLDR imports resolve on 19045. The two culprits are
gated in `wdm.h` (26100):

```c
#if (NTDDI_VERSION >= NTDDI_WIN10_NI)     // Win11 22H2 (0x0A00000C)+  -> NTKERNELAPI (import)
    ... ExAllocateFromNPagedLookasideList / ExFreeToNPagedLookasideList ...
#else                                      // Win10 -> __inline (no import)
```

Built at NI (the 26100 default), they become imports; Win10 19045 (NTDDI_WIN10_VB, `0x0A000008`) does
not export them. They are called **only** from the `#ifdef SYSVAD_BTH_BYPASS` / `SYSVAD_USB_SIDEBAND` /
`SYSVAD_A2DP_SIDEBAND` work-task pools in `common.cpp` (Bluetooth-HFP / USB / A2DP sideband) — paths a
Root-enumerated, pull-driven virtual **render** device never instantiates.

## Why the obvious levers did not work

- **Lower `NTDDI_VERSION` so the header inlines them.** Breaks compilation: the sample's topology
  (`mintopo.cpp`) uses `KSJACK_DESCRIPTION3` and audio-posture (`AUDIOPOSTURE_ORIENTATION_NOTROTATED`),
  both gated at the **same** `NTDDI_WIN10_NI`. Even one notch below NI (CO) loses them. NTDDI cannot
  separate the two.
- **Drop the `SYSVAD_BTH_BYPASS` / `SYSVAD_USB_SIDEBAND` defines.** Breaks compilation too:
  `MiniportAudioEngineNode.cpp` references `m_pSidebandDevice` / `IsSidebandDevice()` outside the
  guards.

## The fix (surgical, statically verifiable)

Keep `NTDDI` at NI (everything compiles; those two were the *only* missing imports), and redirect just
the two calls to always-exported pool APIs via a compat block inserted after the include block of
`common.cpp` (reproducible: `spikes/bpre-vm/apply_win10_compat.ps1`, idempotent):

```c
#if (NTDDI_VERSION >= NTDDI_WIN10_NI)
#undef  ExAllocateFromNPagedLookasideList
#undef  ExFreeToNPagedLookasideList
#define ExAllocateFromNPagedLookasideList(Lookaside) \
            ExAllocatePool2(POOL_FLAG_NON_PAGED, (Lookaside)->L.Size, (Lookaside)->L.Tag)
#define ExFreeToNPagedLookasideList(Lookaside, Entry) ExFreePool(Entry)
#endif
```

`ExAllocatePool2` / `ExFreePool` are exported on 19045 (confirmed). The pools are never exercised by a
render-only device, so the allocation semantics are irrelevant. Rebuilt via the EWDK (only
`EndpointsCommon` + `TabletAudioSample`, skipping the `wil`-dependent APO demos), re-ported the INF to
build 19041 and re-signed (`pkg10`, DriverVer `10.0.19041.101`).

**Static proof** (rebuilt `.sys`, imports diffed against the live 19045 kernel): the two lookaside
imports are gone; **every** import (PortCls 9, ntoskrnl 66, WDFLDR 5, HAL 1) resolves on Win10 19045.

## Validation — loads clean on Win10, both VM and host

A new **Win10 22H2 VM** `kookr-bpre-w10` (Gen2, Secure Boot off so test-signing needs no toggle; built
beside the Win11 `kookr-bpre`, scripts `dl_w10.ps1` + `make_w10_vm.ps1` + `autounattend_w10.xml`) is
**build 19045 — identical to the host**, which the Win11 VM never was (that is why the Win11 VM could
not catch this). Results:

| | node | service | endpoints | bugcheck | CI 3004 |
|---|---|---|---|---|---|
| **Win10 VM 19045** | `CM_PROB_NONE` | bound | 8 × [OK] | none | none |
| **Host 19045** | `CM_PROB_NONE` | `sysvad_componentizedaudiosample` | 8 × [OK] (Haut-parleurs, Casque, SPDIF, mic arrays…) | none | none |

The host's default playback device is unchanged; the virtual device is present, demand-start and
**not** default. Audio endpoints appear only when the miniport actually starts, so this is a true
functional load, not just a created node.

## State left behind

- **Host:** the fixed `pkg10` is installed and loaded (`ROOT\MEDIA\0004`, `CM_PROB_NONE`). Reversible
  with `spikes/bpre-vm/host_revert.ps1` + reboot. Test-signing remains on.
- **VM:** `kookr-bpre-w10` with checkpoints `clean-testsigning` and `pre-pkg10-install`; a durable
  Win10 19045 test rig matching the host for all future driver work.
- `common.cpp` on the host carries the compat block (`.preCompat.bak` kept); the source tree is not in
  the repo, so `apply_win10_compat.ps1` is the committed, reproducible representation of the fix.

## Handoff — step 3 (unchanged): build the B2 consumer and measure latency

The driver now loads on the host, which has the real **FiiO K11** audio clock the VM lacks. The next
block is the journal-63 B2 consumer: loopback-capture the SYSVAD endpoint → binaural DSP →
WASAPI-exclusive to the K11 → tick `KookrPullTick` from the K11 render callback (no cushion FIFO) →
measure end-to-end latency vs the journal-48 baseline (confirm the ≈16–20 ms `queue`-term win).

## Note — stale safety-contract wording in AGENTS.md

`AGENTS.md` "Elevated host control" still reads "Kernel drivers load ONLY inside the Hyper-V VM, never
on the host" / "Never touch host boot config." The owner superseded that for Track B in journals 61–67
(host arm + test-signing + bounded demand-start load), and did so again here. The wording should be
updated to reflect the owner's standing authorization for the bounded, reversible Track-B host load;
left as a follow-up to keep this change focused on the driver fix.

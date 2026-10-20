# 53 — Track B kickoff: environment assessment + SYSVAD position seam (pre-M1)

**Block:** B8.7b (RFC Track B) · **Milestone:** M8 / Track B · **Status:** pre-M1 study done;
**B-pre blocked on owner-side setup** (WDK + a Hyper-V VM — both admin-gated).

## Context

A0 (journal 52) showed the live `queue` term is jitter/burst-bound, not clock skew, so the A1
resampler can't cut it — the lever is the capture path. The owner chose to **commit to Track B**
(the custom virtual 7.1 device that removes the VoiceMeeter loopback). Track B's first milestone is
**B-pre**: prove in a VM that `audiodg` tolerates a **pull-driven** WaveRT position before investing
in the full driver. This entry records what the host has, what's missing, and the SYSVAD position
study the RFC mandated "before committing to M1".

## Host environment (assessed via WSL interop)

| component | state |
|-----------|-------|
| OS | **Windows 10 Pro 22H2, build 19045.7807** |
| Driver model | **SYSVAD / PortCls / WaveRT** (ACX is Win11-only → resolves an RFC Open Question) |
| Visual Studio | 2022, 2019, 2017 present |
| Windows SDK | 10.0.19041 present; `signtool.exe` present |
| Hyper-V | **enabled** (hypervisor detected; virtual NICs present) — **no VM created yet** |
| **WDK (kernel kit)** | **ABSENT** — no `km/` headers (`wdm.h`, `portcls.h`), no km libs |

**So the two hard prerequisites for B-pre are missing and both need admin** (which the WSL interop
shell does not have): the **WDK** (to build any driver) and a **Hyper-V VM** (to load/test it
without risking a host BSOD; test-signing must stay off the host so it doesn't break anti-cheat).

## Pre-M1 study — SYSVAD's position/clock path (the RFC's gate before M1)

Read `audio/sysvad/EndpointsCommon/minwavertstream.cpp` (microsoft/Windows-driver-samples, main).
Findings:

- **`UpdatePosition(qpc)` (≈line 1397) is the single seam.** Stock SYSVAD advances the stream by a
  pure wall-clock timer simulation: `ByteDisplacement = m_ulDmaMovementRate × TimeElapsedInMS / 1000`,
  then `m_ullLinearPosition += ByteDisplacement`, updates `m_ullWritePosition` /
  `m_ullPresentationPosition`, moves data via `WriteBytes`/`ReadBytes`, and stamps
  `m_ullDmaTimeStamp`. This free-running timer is exactly the "throwaway part" the RFC named.
- **The pull-driven change is surgical, not a rewrite.** Replace the *source* of `ByteDisplacement`:
  instead of elapsed-time × nominal rate, feed the **actual frames our K11 consumer reports consumed**
  (the RFC's `advance(frames, qpc)`), keeping the rest of `UpdatePosition` and the KS/position
  handlers intact. The position fields are the seam the RFC hoped for.
- **`GetPosition` already calls `UpdatePosition(KeQueryPerformanceCounter())` on demand** when running.
  So *stock* SYSVAD already presents a position that steps at `audiodg`'s poll cadence — pull-driven
  only changes the step *size source* (K11 consumption vs elapsed×rate), both monotonic and
  real-time-tracking. **This makes `audiodg` intolerance less likely than the RFC feared** — but it
  is still the make-or-break B-pre question and must be proven empirically in the VM.
- **Second piece to rework:** the buffer-notification timer (`m_pNotificationTimer` / `TimerNotifyRT`
  / the DPC) fires packet-complete events on a schedule; pull-driven must fire those on real K11
  consumption instead. Isolated from the position seam but on the B-pre checklist.

Conclusion: the pull-driven position is **feasible and well-localized**; the design maps onto one
function plus the notification path. The open empirical question (audiodg tolerance of bursty
external advance) is narrowed and ready for the VM test.

## B-pre setup runbook (owner — the blocker)

**A. Hyper-V VM (safe kernel sandbox):**
1. Confirm Hyper-V on (admin PowerShell): `Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All` (reboot if it flips). It appears already enabled.
2. Hyper-V Manager → **New → Virtual Machine**: Generation 2, 8 GB+ RAM (dynamic ok), 60 GB+ disk, attach a Windows 10/11 ISO, install Windows. (Or **Quick Create**.)
3. In the VM (admin cmd): `bcdedit /set testsigning on`, then reboot. (Kept inside the VM so the host keeps Secure Boot on and anti-cheat games keep working.)
4. Give the VM network + a way to move files in (Enhanced Session / a shared folder).

**B. WDK (to build the driver):** install on the **host** (lets the build be driven from WSL interop) — Windows SDK **10.0.22621** (or 26100) + the matching **WDK** + the **WDK Visual Studio extension** (offered at the end of the WDK installer); VS 2022 is already present. (Alternatively install VS2022+WDK inside the VM and do everything there — simpler but hands-on.)

**C. Then B-pre** (once A+B exist): clone `microsoft/Windows-driver-samples`, build `audio/sysvad`,
apply the pull-driven `UpdatePosition` change + a software **pseudo-K11** consumer, test-sign, install
in the VM (`pnputil`), render a 48 kHz app's 7.1 into it, and observe `audiodg`: no
silence-insert/disconnect, monotonic `IAudioClock`.

## Division of labour (what's automatable vs owner's)

- **Owner, one-time, admin:** install the WDK; create the VM + test-signing (A, B above).
- **Claude (once WDK exists):** author the SYSVAD fork (the `UpdatePosition` pull-driven change,
  the shared-ring + `advance()` seam), drive the build via interop, write the pseudo-K11 consumer +
  the 48 kHz 7.1 test app for the audiodg probe.
- **Owner (or a reachable VM):** install the signed driver in the VM and run the probe; kernel-debug
  from the host if it BSODs. (A Hyper-V guest is not drivable from the WSL interop shell.)

## Decision / next

B-pre cannot start until the WDK + VM exist (A + B). The pull-driven seam is confirmed feasible, so
the moment the WDK is installed, the fork + test harness can be authored and built. Not started.
Head-move→ear remains a non-goal (journal 51).

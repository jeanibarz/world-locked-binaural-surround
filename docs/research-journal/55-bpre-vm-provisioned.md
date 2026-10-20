# 55 — B-pre environment provisioned autonomously (WDK + Hyper-V VM)

**Block:** B8.7b (RFC Track B) · **Milestone:** M8 / Track B · **Status:** toolchain + VM
provisioned over the elevated SSH channel; Windows installing/booting when this was written. Next
is the SYSVAD fork + the B-pre audiodg test.

## What's now set up on the host (all via the elevated SSH channel, journal 54)

**Driver toolchain (host) — can build drivers now:**
- Visual Studio 2022 (already present) + **Windows SDK 10.0.26100** + **WDK 10.0.26100.6584**
  (the VS2022-matched pair, journal 53). Verified: `C:\Program Files (x86)\Windows Kits\10\
  Include\10.0.26100.0\km\portcls.h` and `wdm.h` present.
- Installed silently by `spikes/bpre-vm/stage1.ps1` (run as a detached SYSTEM scheduled task,
  logged to `D:\kookr-vm\build_stage1.log`). The SDK/WDK web bootstrappers fork child installers
  and return immediately, so verify by the km headers, not the bootstrapper exit code.

**Windows 11 ISO:** build **26300** (en-US, 8.43 GB) at `D:\kookr-vm\win11.iso`, fetched via
Fido (`-Win 11 -Lang English -Arch x64 -GetUrl` → official MS link) + BITS. *(Microsoft retired
the prebuilt dev-environment VHDX — the old download page now 301-redirects to a generic page —
so the ISO + unattended install is the route.)*

**The VM (`kookr-bpre`):** created by `spikes/bpre-vm/stage2_vm.ps1`.
- Gen2, 8 GB RAM (static), 4 vCPU, 64 GB dynamic OS disk, at `D:\kookr-vm\vm\`.
- **vTPM (local key protector) + Secure Boot** (MicrosoftWindows template) → Win11 installs with
  no hardware-check bypass.
- **Unattended install** via `spikes/bpre-vm/autounattend.xml` on a tiny FAT32 VHDX
  (`D:\kookr-vm\unattend.vhdx`) attached as a second disk; OS disk stays DiskID 0 (the answer file
  wipes/targets DiskID 0). Local admin **`kookr`** / **`Kookr-VM-2026!`**, auto-logon, all OOBE
  skipped, `BypassNRO` set so a local account works without network.

## How to reach the VM (reconnect recipe)

From the elevated SSH channel on the host, **PowerShell Direct** (no guest network needed):

```powershell
$cred = New-Object System.Management.Automation.PSCredential('kookr',
         (ConvertTo-SecureString 'Kookr-VM-2026!' -AsPlainText -Force))
Invoke-Command -VMName kookr-bpre -Credential $cred -ScriptBlock { ... }
```

`spikes/bpre-vm/check_psdirect.ps1` prints `READY <name>` once the guest is up; `guest_info.ps1`
dumps OS/user. *(Credentials recorded here because the repo is private and the VM is a disposable
local sandbox — it holds no real data and is reachable only from this host over the VM bus.)*

## Lesson (so I don't repeat it)

My first readiness waiter grepped `READY`, which **also matches `NOTREADY`** → it reported the VM
ready on the first poll while Windows was still installing. Match the whole token (`^READY `).
General rule: a readiness probe's success pattern must not be a substring of its not-ready pattern.

## Safety posture (unchanged, journal 54 contract)

Test-signing + driver loading happen **only inside this VM**; the host keeps Secure Boot on. A
Hyper-V **checkpoint** is taken before each driver load (rollback). Teardown: `Remove-VM
kookr-bpre` + delete `D:\kookr-vm` (plus `spikes/host_admin_sshd_revert.ps1` for the channel).

## Decision / next

- Environment ready. Next, once Windows finishes booting (PowerShell Direct confirmed with the
  `^READY ` fix): enable test-signing in the guest, checkpoint, then author/build the SYSVAD
  pull-driven fork (journal 53 seam) on the host and run the **B-pre** audiodg-tolerance test.
- Provisioning scripts live in `spikes/bpre-vm/` so the whole environment is reproducible.

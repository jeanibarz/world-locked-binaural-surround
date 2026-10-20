# 56 — B-pre VM: unattended install, test-signing, and the toolchain snag (EWDK)

**Block:** B8.7b (RFC Track B) · **Milestone:** M8 / Track B · **Status:** VM installed + test-signing
on + clean checkpoint; driver toolchain being assembled via the EWDK (host VS is too old). Next is
the SYSVAD pull-driven fork + B-pre. All driven autonomously over the SSH channel (journal 54).

## Progress since journal 55

1. **Unattended Win11 install — succeeded after two fixes** (diagnosed by capturing the VM console
   via the Hyper-V thumbnail WMI, `spikes/bpre-vm/vm_shot.ps1`):
   - **Gen2 "press any key to boot from CD"**: headless, nothing pressed → DVD skipped → "no OS".
     Fixed by sending ENTER via the synthetic keyboard (`Msvm_Keyboard.TypeKey`).
   - **New Win11 (build 26300) setup ignores autounattend on a secondary disk.** Fixed by
     **remastering the ISO** with `autounattend.xml` at the **root** + the **no-prompt** UEFI boot
     image (`efisys_noprompt.bin`) via `oscdimg` (ADK Deployment Tools) — `spikes/bpre-vm/
     stage3_remaster.ps1` + `stage4_vm.ps1`. From the remastered ISO the install is fully silent.
   - Result: Windows 11 Pro build 26300, admin `kookr`, reachable by PowerShell Direct.
2. **Test-signing enabled** (`spikes/bpre-vm/stage5_testsign.ps1`): `bcdedit /set testsigning on`
   first **failed — blocked by Secure Boot** (exactly the RFC's conceded limitation; the two are
   mutually exclusive). So: verified BitLocker is **off** (toggling Secure Boot with it on would
   force a recovery-key lockout), then **disabled Secure Boot on the VM only** (host untouched),
   enabled test-signing, verified it ON, and took a **`clean-testsigning` checkpoint** (the
   rollback point before any driver load, per the safety contract).
3. **SYSVAD sample cloned** to the host (`audio/sysvad`, 188 files, buildable sln).

## The toolchain snag — host VS is too old → EWDK

Baseline-building `sysvad.sln` failed with **MSB8020**: the `WindowsKernelModeDriver10.0` platform
toolset isn't registered. Cause: the host's **Visual Studio 2022 is version 17.0.6** (the original
2021 release) — far older than the **17.11+** that WDK 26100 needs, and for which the WDK is an
individual VS component (the silent `wdksetup /quiet` skips it). The WDK's km headers are installed
(so the kit is there), but there is no VS driver build integration.

**Decision: use the EWDK, not update the owner's VS.** Updating VS 17.0.6 → current is a large,
invasive change to the owner's working dev environment (could disrupt their other work). The
**EWDK** (Enterprise WDK) is a self-contained, mountable ISO with its own VS Build Tools 17.14 +
SDK + WDK and the driver toolsets — it ignores the installed VS entirely, so it builds drivers from
the command line without touching the host's VS. Downloading the **Win11 25H2 EWDK** (fwlink
2335681, matches WDK 26100.6584) to `D:\kookr-vm\ewdk.iso` (`spikes/bpre-vm/stage6_ewdk_dl.ps1`).

## Lessons (so a future run skips the pain)

- **Headless Hyper-V Gen2 Win11 install:** put `autounattend.xml` at the **ISO root** and use the
  **no-prompt** boot image; a secondary unattend disk is ignored by the new setup, and the keypress
  prompt stalls a headless boot. Capture the console with the thumbnail WMI to debug blind installs.
- **Secure Boot vs test-signing are mutually exclusive:** install Win11 with Secure Boot **on** (its
  hardware check wants it), then **disable Secure Boot in the VM** for driver work — after checking
  BitLocker is off.
- **Check the VS version before assuming a WDK build works.** For CLI driver builds that must not
  perturb the host's VS, the **EWDK** is the clean, isolated toolchain.

## Decision / next

- VM ready for drivers (test-signing on, checkpoint taken). Toolchain: EWDK downloading.
- Next: mount the EWDK, baseline-build `sysvad.sln`, then apply the **pull-driven `UpdatePosition`**
  change (journal 53 seam), sign, load in the VM, and run the **B-pre audiodg-tolerance test**.
- Reproducible scripts in `spikes/bpre-vm/`.

# 65 — B2 is blocked on the host: the driver targets Win11 22621+, the host runs Win10 19045

**Block:** B8.7b (RFC Track B, B1→B2) · **Milestone:** M8 / Track B · **Status:** **BLOCKED** — the
test-signed virtual device cannot bind on the host because its INF targets a Windows build the host
does not run. This corrects journal 64's implied "install works": the node was created but **no driver
ever bound** (the crash-safety "PASS" was real but trivial — an unconfigured node loads nothing).

## What happened when I went to build B2

Step 4 (build the real B2 consumer, journal 63) needs the installed SYSVAD device to expose a render
endpoint so the consumer can loopback-capture it. It does not:

- `py.exe -3.12` (host Python via WSL interop — the journal-48 way) enumerated **every** WASAPI
  endpoint: K11, VoiceMeeter, Bigscreen, NVIDIA HDMI… **no SYSVAD/Tablet/Speaker endpoint at all.**
- `devcon status` showed the node `ROOT\MEDIA\0004` "currently stopped"; a `devcon restart` flipped it
  to **Status: Error, `CM_PROB_NOT_CONFIGURED` (code 1)**, with **`DEVPKEY_Device_Service` and
  `DEVPKEY_Device_DriverInfPath` both empty** → no driver/service bound to the node.

## Root cause (certain)

The package INF (`ComponentizedAudioSample.inf`, `DriverVer 10.0.26100.12`) gates its models section on
the OS build:

```
[Manufacturer]
%MfgName%=SYSVAD,NTamd64.10.0...22621
[SYSVAD.NTamd64.10.0...22621]
%SYSVAD_SA.DeviceDesc%=SYSVAD_SA, Root\sysvad_ComponentizedAudioSample
```

`NTamd64.10.0...22621` = **applies only to build ≥ 22621 (Windows 11 22H2+)**. The host is:

```
Microsoft Windows 10 Professionnel   Version 10.0.19045 (22H2)   UBR 7807
```

Build **19045 < 22621**, so `devcon install` matches the hardware id and creates the node, but PnP
finds **no applicable install section** → unconfigured node, no driver. The VM validated everything
because it was provisioned as Windows 11 (≥ 22621); the identical `devcon install` bound there. The
whole of Track B was built and stress-tested on Win11; the host is Win10.

I removed the dead `ROOT\MEDIA\0004` node afterwards (no driver bound → harmless, just a Device-Manager
yellow-bang). Test-signing is left on (reversible, and saves re-arming for a future attempt).

## Is the driver portable to Win10? (INF-level: yes; runtime: unverified)

I read the full INF. Nothing in it is Windows-11-exclusive:

- the DDInstall `[SYSVAD_SA.NT]` uses only standard audio-class directives —
  `Include=ks.inf,wdmaudio.inf`, `Needs=KS.Registration, WDMAUDIO.Registration`, `CopyFiles`,
  `AddReg` — all present on Win10. There is no custom `AddService`; the PortCls service comes from
  `WDMAUDIO.Registration`.
- the only version-specific bits are `DriverVer … 26100` and the `...22621` target gate.

So **lowering the gate is low-effort** (edit the Manufacturer + model-section decorations to e.g.
`NTamd64.10.0...19041`, regenerate `sysvad.cat` with stampinf/inf2cat, re-sign with the existing test
cert; the `.sys` stays byte-identical). **The real unknown is runtime:** `tabletaudiosample.sys` is
built with the 26100 WDK. Downlevel-loading a 26100 PortCls miniport on 19045 is the riskier
direction — if it imports a KS/PortCls DDI absent on Win10 it simply fails to load (safe); if it calls
a newer DDI at runtime it could bugcheck (bounded by demand-start + not-default → no bootloop, and the
crash harness catches it, but it is still a kernel load on the owner's daily-driver machine).

## Decision — this is the operator's call (safety contract + daily-driver OS)

The journal-54 safety contract is explicit: **confirm before anything irreversible or outward-facing
on the host.** Deliberately loading a Windows-11-built kernel driver, downlevel, on the owner's primary
Windows-10 workstation is exactly that. So I am **not** self-authorizing it. The resolution options,
with recommendation:

1. **(Recommended) Upgrade the host to Windows 11, then resume step 4 unchanged.** The driver binds
   natively (the VM already proves it), no INF surgery, no downlevel risk, and Win10 reached
   end-of-support in Oct 2025 so the upgrade is independently due. Cleanest path to the real B2
   end-to-end latency number.
2. **Port the INF to Win10 and attempt a bounded downlevel load.** Add a `...19041` model section,
   re-sign, install demand-start/not-default, run the crash harness. Low effort, bounded blast radius,
   but a real (if small) BSOD risk on the daily driver, and no guarantee the 26100 miniport runs clean
   on 19045.
3. **Stand up a Windows 11 test host with real audio hardware elsewhere** (second machine / dual-boot)
   and run B2 there, leaving the daily driver untouched.

What stays true regardless: the VM has **proven the mechanism** (pull-driven device, lifecycle +
recovery stress, zero bugcheck, clean output under both a software ticker and a WASAPI-callback
ticker). The only thing still unmeasured is the **real end-to-end latency on real hardware**, and that
needs a Win11 box with an audio device — which no environment we have today provides at once (VM =
Win11 but no audio clock; host = audio clock but Win10).

## Handoff

- If option 1/3: on a Win11 box with the K11, re-run `host_arm_testmode.ps1` → reboot →
  `host_install_and_test.ps1` (now it actually binds) → build the B2 consumer (loopback-capture the
  SYSVAD device → binaural DSP → WASAPI-exclusive to the K11 → tick `KookrPullTick` from the K11 render
  callback, no cushion FIFO) → measure end-to-end latency vs the journal-48 baseline (confirm the
  ≈16–20 ms `queue`-term win).
- If option 2: INF port + re-sign first, then the same install + build + measure, with the crash
  harness and `host_revert.ps1` on standby.

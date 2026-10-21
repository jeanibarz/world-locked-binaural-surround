# 66 — The Win10 INF port binds (no bugcheck); loading it now only needs the reboot into test mode

**Block:** B8.7b (RFC Track B, B1→B2) · **Milestone:** M8 / Track B · **Status:** option 2 (journal 65)
executed. The driver now **matches Windows 10** and installs with **"Drivers installed successfully"
and zero bugcheck** — the Win11-gate blocker is solved. It does not yet *load* for one remaining
reason: the host has not actually rebooted into test-signing mode (last boot predates test-signing).

## What was done (the port)

`spikes/bpre-vm/host_port_win10.ps1` builds a Win10 variant of the package in `D:\kookr-vm\pkg10`:

- lowers the INF OS gate `NTamd64.10.0...22621` → `...19041` (2 replacements: the `[Manufacturer]`
  line and the model-section header) — now applies to build ≥ 19041, covering the host's 19045 **and**
  Win11;
- bumps `DriverVer` (so the store replaces any prior package);
- regenerates the catalog with `Inf2Cat /os:10_X64` (exit 0);
- re-signs `.sys` / `.dll` / `.cat` with the `KookrTestDriver` test cert.

The `TabletAudioSample.sys` hash is unchanged (`A021A1DE…B29D`) — a byte-identical binary, only the
INF/catalog changed.

## Install result — the gate fix works

`devcon install D:\kookr-vm\pkg10\ComponentizedAudioSample.inf Root\Sysvad_ComponentizedAudioSample`:

```
Device node created. Install is complete when drivers are installed...
Updating drivers for Root\Sysvad_ComponentizedAudioSample from D:\kookr-vm\pkg10\...
Drivers installed successfully.
```

No "devcon failed", **no bugcheck**. Contrast journal 65, where the Win11-gated INF produced an
unconfigured node with no driver. The port made PnP find the install section and stage the driver.

## Why it still doesn't load — and it is not a port problem

The node came up `CM_PROB_UNSIGNED_DRIVER` (code 52), and the CodeIntegrity operational log recorded,
at the install instant:

```
Event 3004: Windows is unable to verify the image integrity of …\tabletaudiosample.sys
            because file hash could not be found on the system.
```

That is the running kernel **enforcing signatures** — i.e. it is *not* in test-signing mode. The
evidence:

- `bcdedit … testsigning = Yes` is only the **configured** value; it takes effect on the **next boot**.
- `Win32_OperatingSystem.LastBootUpTime = 2026-10-19 18:32` — the running kernel booted **before**
  test-signing was set, so at runtime it still rejects the test-cert-signed driver.
- The identical `.sys`, signed the identical way, loads in the Win11 VM (which *did* boot into test
  mode) — so the signing method is correct; the variable is the host's live boot state.

This is step 2 of the handoff (journal 63) — the reboot — which has not actually happened. The
`host_install_and_test.ps1` "test-signing: active" line only checks the bcdedit *config*, not the
live kernel, so it did not catch this; a live check (CI enforcement / last-boot vs. test-signing) is
the honest gate and is noted for the script.

## State left on the host

- `pkg10` remains staged in the DriverStore; the error node was removed (clean Device Manager).
- Test-signing stays configured `on`; cert still trusted. So the **only** action needed is a reboot.

## Handoff — after a real reboot

1. Reboot the host (operator). Confirm live test mode (desktop "Test Mode" watermark; or the install
   below simply loads without a CI 3004).
2. `devcon install D:\kookr-vm\pkg10\ComponentizedAudioSample.inf Root\Sysvad_ComponentizedAudioSample`
   → expect `CM_PROB_NONE` + a bound `tabletaudiosample` service + a new render endpoint, no bugcheck.
3. Build the B2 consumer: loopback-capture the SYSVAD endpoint → binaural DSP → WASAPI-exclusive to
   the K11 → tick `KookrPullTick` from the K11 render callback (no cushion FIFO) → measure end-to-end
   latency vs the journal-48 baseline (confirm the ≈16–20 ms `queue`-term win).
4. Revert path unchanged: `host_revert.ps1` + reboot → host back to stock.

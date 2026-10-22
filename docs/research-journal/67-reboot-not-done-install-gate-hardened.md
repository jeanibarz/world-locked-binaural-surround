# 67 — The host never rebooted into test mode; hardened the install gate so this can't mislead again

**Block:** B8.7b (RFC Track B, B1→B2) · **Milestone:** M8 / Track B · **Status:** still blocked on **one
real host reboot** — everything else (Win10 INF port #98, test-signing armed, cert trusted, `pkg10`
staged) is in place. The operator believed the host had rebooted; it had not.

## What I checked

The owner asked to proceed with the Win10 driver, saying they thought they had rebooted and the driver
should now install. Journal 66 left exactly one gate: the running kernel must have booted *after*
test-signing was armed, or a test-cert-signed driver still fails CodeIntegrity. The host says it has
**not** rebooted at all since test-signing was set:

- `Win32_OperatingSystem.LastBootUpTime = 2026-10-19 18:32:55` (checked 2026-10-22 21:54).
- System event-log boot events (id 12): the most recent is **2026-10-19 18:33** — no boot since.
- Fast Startup is **off** (`HiberbootEnabled = 0`), so a hybrid shutdown did not silently swallow a
  boot — there simply was none on this machine.
- `bcdedit … testsigning = Yes` (configured), cert still trusted in `LocalMachine\Root`, `pkg10`
  present in `D:\kookr-vm`. No SYSVAD node currently installed.

So installing now would reproduce journal 66's CI 3004 / `CM_PROB_UNSIGNED_DRIVER` exactly. I did
**not** install.

## Why journal 66 (and this) was confusing — and the fix

`host_install_and_test.ps1`'s gate checked only `bcdedit … testsigning = Yes`, which is the
*configured* value, not the *live* kernel state. It printed "test-signing: active" while the kernel was
stale — the trap journal 66 flagged (its §"Why it still doesn't load") but had not yet closed. Fixed:

- **`host_arm_testmode.ps1`** now drops an armed-at marker `D:\kookr-vm\testmode_armed_at.txt` (ISO
  timestamp) when it enables test-signing, and its final message says to do a full **Restart** (not
  shutdown+power-on).
- **`host_install_and_test.ps1`** now gates on `LastBootUpTime` vs that marker: if the kernel booted
  *before* test-signing was armed it **ABORTs** with "you have NOT rebooted since arming it … do a full
  Windows RESTART", instead of proceeding into a guaranteed CI failure. If the marker is absent it
  warns rather than claiming live test mode.
- It now installs **`pkg10`** (the Win10-ported, re-signed package from journal 66), not the old
  Win11-gated `pkg`.

I also wrote the marker on the host now (test-signing was armed in a prior session with no marker), so
the gate is armed for the pending reboot. Verified live: the gate **correctly ABORTs** against the
2026-10-19 kernel.

## The one remaining action (operator-only)

A reboot kills this WSL/SSH session (it runs on the host — journal 62), so it cannot be done in-session.
After a full **Restart** of the host, a session runs:

```
powershell -ExecutionPolicy Bypass -File D:\kookr-vm\host_install_and_test.ps1
```

Expect: gate passes (kernel now booted after the marker) → `devcon install` of `pkg10` →
`CM_PROB_NONE` + a bound `tabletaudiosample` service + a new SYSVAD render endpoint, no bugcheck. Then
build the **B2** consumer (loopback-capture the SYSVAD endpoint → binaural DSP → WASAPI-exclusive to the
K11 → tick `KookrPullTick` from the K11 render callback, no cushion FIFO) and measure end-to-end latency
vs the journal-48 baseline (confirm the ≈16–20 ms `queue`-term win). Revert path unchanged:
`host_revert.ps1` + reboot.

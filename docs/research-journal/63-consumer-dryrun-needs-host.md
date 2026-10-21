# 63 — The render-callback-driven consumer is a host-only test; VM validation is complete

**Block:** B8.7b (RFC Track B, B1→B2) · **Milestone:** M8 / Track B · **Status:** the tick-from-render-
callback *plumbing* is validated; the end-to-end consumer dry-run is confirmed to **require the host**
(a real output device as the master clock). Everything the VM can validate for Track B is now done.

## What was tried

`consumer_dryrun.py` replaces `pseudo_k11.py`'s software busy-wait ticker with the **real B2
mechanism**: open a WASAPI output stream and signal `KookrPullTick` once per **render callback**, so
the pull-driven SYSVAD device advances 1:1 with a hardware audio clock (the stand-in for the K11's
render callback).

## Result + diagnosis

- The tick plumbing works: the render callback fired **1659 times in 16 s (~104 Hz), 0 output
  underflows** — ticking the kernel device from a WASAPI callback is clean and stable.
- But the captured device-output WAV was **silent (rms 0.000)**, and a re-run of the single-stream
  `pseudo_k11` path was immediately **CLEAN again (rms 0.141, 0 gaps, 0 jumps)** — so the silence was
  **not** a driver regression.
- **Root cause (a VM limitation, not a bug):** a Hyper-V VM reached over PowerShell Direct has **no
  real audio hardware** — the only output endpoints are the SYSVAD ones we installed. So
  `consumer_dryrun`'s output stream opened on a SYSVAD endpoint and became a **second pull-driven
  stream competing for the same auto-reset `KookrPullTick` event** (each `SetEvent` wakes only one
  waiter), wrecking the pacing for both. There is no independent audio clock in the VM to drive the
  consumer.

## B2 design consequence (important)

The real consumer must **not** open a competing SYSVAD render stream. The correct shape:

- apps play into the SYSVAD virtual device in shared mode → **the only pull-driven stream**;
- the consumer **loopback-captures** that device (a *capture* client, not a second render stream);
- the consumer outputs to the **K11 as a normal WASAPI-exclusive stream** (not pull-driven);
- the consumer ticks `KookrPullTick` once per **K11 render callback** → one master clock, no
  contention.

The VM cannot host this (no real output device), so the render-callback-driven end-to-end test — and
the real end-to-end latency number — are **host-only (B2)**.

## Status: VM validation of Track B is complete

In the VM we have proven, with zero host risk: the pull-driven device works, survives lifecycle +
consumer-drop + recovery stress with **zero bugcheck**, produces clean device output under a software
ticker, and the tick-from-WASAPI-callback plumbing is clean. Two BSOD-class bugs were caught and
fixed here. The binary staged for the host is byte-identical to the one validated.

## Handoff — the next session (post-reboot) does the host run, in order

1. `spikes/bpre-vm/host_arm_testmode.ps1` (elevated) → trust cert + `testsigning on`.
2. **Reboot** the host (operator's call; it restarts WSL + any Claude session on the host).
3. `spikes/bpre-vm/host_install_and_test.ps1` (elevated) → install the device (not default), confirm
   no bugcheck → `D:\kookr-vm\host_test_result.txt`.
4. Build **B2** per the design above (loopback-read + DSP + WASAPI-exclusive to the K11 + tick from
   the K11 callback, **no cushion FIFO**) and measure end-to-end audio latency vs the journal-48
   baseline → confirm the ≈16–20 ms `queue`-term win.
5. If anything misbehaves: `spikes/bpre-vm/host_revert.ps1` + reboot returns the host to stock.

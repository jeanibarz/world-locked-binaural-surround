# 80 — Breakthrough: the ring bridge carries REAL system audio on the host

**Block:** B2 / Track B data plane (RFC `rfc-sysvad-real-loopback.md`, #112, PR #114) ·
**Milestone:** M8 / Track B · **Status:** **real passthrough proven at the driver level** on the host
(VoiceMeeter-free); one fidelity refinement (ring rate-match) remains before the owner listen test.

## Result

Installed the Win10 ring-bridge driver (journal 79's `make_ring_bridge.py`) on the **host**
(`JEAN-PC1`, Win10 19045), reloaded it cleanly (audio-service bounce + device cycle — **no
bugcheck**, status OK, `DriverVer 10.0.19041.109`, `oem169.inf`), then played a known **500 Hz** into
the SYSVAD render endpoint and captured its WASAPI render-loopback:

```
per-ch RMS [0.2078, 0.2078, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]   dominant ~463 Hz
```

- **Signal only in front L/R** (channels 0–1, exactly where the test put the tone), zeros on 2–7 —
  **not** the stock loopback tone, which was a **uniform 0.3535 on all 8 channels** at 3000 Hz.
- Dominant frequency **~463 Hz** (the played 500 Hz), **not 3000 Hz**.

So the loopback now carries the **real rendered mix**, routed render→ring→capture inside the driver.
This is exactly the MS-documented fix and the OpenAudioCable mechanism, implemented from the MIT base.
**VoiceMeeter-free real passthrough works at the driver level.**

## Why the VM never showed this, and the host did

The VM (journal 79) rendered **silence** into the device DMA regardless of config — a headless-VM
`audiodg` property, not a driver bug. The host has a **real audio session**, so `audiodg` deposits the
real mix into the render DMA; the ring then delivers it to the loopback. The entire earlier
"render DMA empty" saga was the VM harness, confirmed by the host carrying real audio the moment the
ring build actually loaded.

## Open refinement — ~7% pitch drop (463 vs 500 Hz)

The captured tone is ~7 % low, a steady pitch shift ⇒ a **rate mismatch** between the ring producer
(render fill) and consumer (loopback drain): the raw byte FIFO does no rate reconciliation, so if the
capture pin drains at a slightly different effective rate than the render fills (or the ring
periodically under/overflows), the pitch shifts. Refinements to try:
- Confirm the loopback capture pin's format/rate exactly equals the render's (both 48 kHz 7.1); a
  rate difference would pitch-shift directly.
- Pace the ring: track fill level and drop/duplicate at the boundaries only, or (better) make the
  capture drain exactly match the render fill per notification period.
- Rule out periodic under/overflow (the FIFO drops oldest on overflow, silence on underflow) by
  sizing/centering the ring and logging fill.

This is a fidelity tune, not a mechanism failure — real audio demonstrably flows end to end.

## State / rollback

- Host now runs the **ring build** (`oem169.inf`, hash `85CADE60`). The prior **PR-#108 tone build**
  (`8B6FCAAF`, `db299bfc…`) is **kept in the DriverStore** — rollback = `pnputil /delete-driver
  oem169.inf /uninstall /force` then cycle the device, and the old build reactivates.
- Reload technique that worked (no bugcheck, no reboot): stop `Audiosrv` (closes all streams → device
  idle, journal-70 rule), `Disable`/`Enable-PnpDevice`, restart `Audiosrv`.

## Decision / next

1. **Fix the ~7 % pitch** (ring rate-match) — the one thing between here and clean real passthrough.
2. **Owner listen test:** with the ring build installed, play real music to the SYSVAD device and run
   the cockpit — it should now world-lock **real audio** (not the tone). Final UX confirmation = the
   owner's ears.
3. Fold this into the RFC: the data plane is the **render→capture ring bridge** (settled + host-proven)
   — supersede Plane T; the pitch refinement is the remaining implementation detail.

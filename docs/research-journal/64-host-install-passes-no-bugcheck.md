# 64 — The test-signed virtual device installs on the HOST with zero bugcheck

**Block:** B8.7b (RFC Track B, B1→B2) · **Milestone:** M8 / Track B · **Status:** host crash-safety
verdict = **PASS**. The byte-identical VM-validated driver loads on the real host, no BSOD, no change
to the default playback device. The host now has what the VM could not offer: a **real audio clock**
(the FiiO K11) to drive the B2 consumer.

## What was run

With the host already armed (journal 62/63 → `host_arm_testmode.ps1`: test cert trusted in
`LocalMachine\Root` + `TrustedPublisher`, `bcdedit testsigning on`), the handoff's step 3 ran over the
elevated SSH admin channel (journal 54):

```
powershell -ExecutionPolicy Bypass -File D:\kookr-vm\host_install_and_test.ps1
```

The script is blast-radius-bounded by design: the device is **demand-start, Root-enumerated, and NOT
set as the default playback endpoint**, so nothing streams to it at boot and it cannot bootloop even if
it were buggy. It records the sound devices before/after and checks for any bugcheck since boot.

## Result + verdict

- **test-signing: active** (the current boot came up with `testsigning Yes`; last boot 2026-10-19).
- The device installed as instance **`ROOT\MEDIA\0004`** — `Get-PnpDevice` reports **Status OK,
  Problem CM_PROB_NONE**. It sits "stopped" because it is demand-start and nothing is opening it yet;
  that is the healthy resting state, not an error.
- The loaded `TabletAudioSample.sys` SHA256 is **A021A1DE…B29D** — byte-identical to the binary stress-
  validated in the VM (journal 61).
- **No bugcheck since boot** → crash-safety verdict **PASS**.
- The default playback device and all real endpoints are untouched; the real **FiiO K11**
  ("Casque (FiiO K11)") is present and OK alongside VoiceMeeter / NVIDIA HDMI / Bigscreen / etc.
- Benign wrinkle: `devcon install` prints "devcon.exe failed" while also printing "Device node
  created" — devcon returns a nonzero exit on this path even though PnP shows the node healthy
  (CM_PROB_NONE). Not a real failure.

Result file on the host: `D:\kookr-vm\host_test_result.txt`.

## Why this matters

Every prior Track B validation was in a Hyper-V VM with **no real audio hardware** — which is exactly
what blocked the end-to-end consumer dry-run (journal 63: the consumer's output stream had no
independent clock and contended for the pull-tick event). The host install clears that: the device
loads safely on real hardware, and the **K11 provides the independent master clock** the real B2
consumer needs.

## Handoff — step 4: build the real B2 consumer and measure end-to-end latency

The next session builds B2 per the journal-63 design, now that the host device is proven:

1. apps play into the SYSVAD virtual device in shared mode → **the only pull-driven stream**;
2. the consumer **loopback-captures** that device (a *capture* client, not a second render stream);
3. the consumer outputs to the **K11 as a normal WASAPI-exclusive stream** (not pull-driven);
4. the consumer ticks `KookrPullTick` once per **K11 render callback** → one master clock, no
   contention, **no cushion FIFO**;
5. measure end-to-end audio latency vs the journal-48 baseline → confirm the ≈16–20 ms `queue`-term win.

If anything misbehaves: `spikes/bpre-vm/host_revert.ps1` + reboot returns the host to stock
(test-signing off, cert untrusted, device removed).

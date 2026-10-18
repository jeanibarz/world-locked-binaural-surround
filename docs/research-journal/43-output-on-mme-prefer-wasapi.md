# 43 — The output was on MME; prefer WASAPI (B8.5c)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** done

## Question

The host latency sweep (journal 42) failed on every row with:

```
ValueError: Multiple output devices found for 'FiiO':
  [8] Casque (FiiO K11), MME
  [21] Casque (FiiO K11), Windows DirectSound
  [26] Casque (FiiO K11), Windows WASAPI
  [47] Headphones (FiiO K11), Windows WDM-KS
```

`sounddevice` refuses an ambiguous name. But the important realisation is *why it was ambiguous*
and what it implies for the **running app**: the FiiO is exposed under four host APIs, and
`resolve_output_device` returned the **first label match** — and the device list orders **MME
first**. So `--audio-device "FiiO"` in the passthrough had been resolving to **MME**, whose shared
buffer is ~90–100 ms. That is almost exactly the fixed device term the component split (journal 42)
said was dominating the pose-age-at-ear (real p50 143 ms vs the sim's 68 ms with a nominal buffer).

## Change

- `devices.resolve_output_device`: when a name matches several devices (same hardware under
  different host APIs), the **lowest-latency host API wins** — WASAPI, then WDM-KS, DirectSound,
  MME. A bare `"FiiO"` now lands on WASAPI (shared, and the only API that supports exclusive mode),
  not MME. A single or unambiguous match is unchanged. This fixes the **running passthrough**
  (its output silently moves off MME) *and* unblocks the benchmark.
- `latency_bench` resolves the device through the same helper instead of handing the raw string to
  `sounddevice` (so the sweep no longer dies on the ambiguous name), and reports the resolved name.
- The passthrough now prints the chosen output's **host API and buffer** on startup
  (`output: Casque (FiiO K11) [Windows WASAPI] (buffer ~X ms)`), so the device term is visible, not
  a guess.

## Result

Headless tests cover the preference (ambiguous `"FiiO"` → the WASAPI index; WDM-KS wins when WASAPI
is absent; an unambiguous name is unchanged). 325 tests, ruff clean. The real effect — how much the
device buffer drops MME → WASAPI shared → WASAPI exclusive — is what the owner's host sweep now
measures; the startup log also states it directly for a normal run.

## Interpretation

The component split predicted "a ~90–100 ms fixed device buffer dominates, and block size barely
moves the total"; the ambiguous-name failure then revealed the mechanism — the output was on MME
the whole time. This is the single most likely big latency win, and it needed no DSP change, just
correct device selection. WASAPI shared should roughly halve the device term; exclusive mode should
cut it to single digits (the sweep ranks both).

## Learnings

- **On Windows, the *host API* a device is opened under is a first-class latency parameter.** The
  same DAC under MME vs WASAPI differs by ~80–100 ms of output buffer. "First substring match wins"
  silently picked the worst one; name-resolution for audio must prefer WASAPI (and expose which API
  it chose). Measuring the pipeline (journal 40/42) is what surfaced a term that was pure
  configuration, not code.

## Decision / next

- Shipped the WASAPI-preferring resolver + visible output-API logging. **Re-run
  `run_latency_sweep.cmd`** (now works) to quantify shared vs exclusive, and reload the passthrough
  to pick up the WASAPI output (watch the new startup line / the S-panel device term fall).
- If exclusive mode wins cleanly, add an opt-in `--exclusive` to the passthrough output and set it in
  the launcher. Then **B8.6 prediction** for the remaining camera-staleness term.

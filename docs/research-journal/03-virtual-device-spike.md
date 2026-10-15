# 03 — Windows virtual-device spike: 7.1 sink + loopback capture (B0.3)

**Date:** 2026-10-15  ·  **Milestone:** M0  ·  **Status:** done

## Question

Is there a working path to a **virtual 7.1 Windows output device** (FR-2) whose
stream we can **capture** from the renderer, and can we expose a clean
**render→capture loopback** so the round-trip latency deferred in B0.2 can be
measured — all driven from the WSL box via interop?

## Context

B0.2 established WASAPI enumeration and reported (not measured) stream latencies,
and left the *measured* round-trip blocked on routing it declined to change in
the owner's live mixer: the installed `sounddevice` 0.5.6 / PortAudio V19.7
`WasapiSettings` exposes **no `loopback` flag** (re-confirmed this run), so a
WASAPI render-loopback capture is unavailable; a round-trip needs a **virtual
cable** whose render half feeds its capture half. This host already runs
VoiceMeeter, so the virtual device almost certainly exists — the open questions
were which endpoints to bind (indices drift) and whether the loopback carries
signal as configured.

## Hypothesis

(a) A virtual multichannel sink (≥ 8-ch) and a matching capture endpoint exist
and can be resolved by name/channel-count at runtime; (b) we can open the capture
endpoint and read frames (capture the virtual stream); (c) a sweep rendered to
the sink is recovered at the capture endpoint, yielding a measured round-trip —
*if* the cable's render strip is routed to its capture bus.

## Method

- Portable role-resolution helpers in
  `src/binaural_headtracker/virtual_device.py` (pure, no `sounddevice`):
  `is_virtual_device`, `virtual_family` (pairs a cable's render/capture halves by
  their shared trailing parenthetical), `pick_virtual_sink`, `pair_loopbacks`,
  `loopback_for_sink`, each accepting an `allow` index set to constrain
  resolution to one host API. Unit-tested on WSL (`tests/test_virtual_device.py`,
  17 tests) — no device needed.
- Windows host spike `spikes/b0_3_virtual_device.py` (run via
  `WINPATH=$(wslpath -w …); py.exe -3.12 "$WINPATH"`): enumerate → resolve the
  WASAPI virtual sink + its capture endpoint → open the capture endpoint and read
  frames (reporting input latency + peak) → render a quiet 50 ms log-sweep to the
  sink and recover the delay by cross-correlation, flagging silence as a routing
  gap rather than a spurious 0 ms. It never changes the mixer and never installs
  software.
- User setup doc `docs/virtual-device-setup.md` (VoiceMeeter for a true 7.1 sink;
  VB-CABLE for a zero-config stereo loopback; how to run the spike).
- One variable per observation; the full machine output is quoted below.

## Result

Resolved on the WASAPI host API (indices are this run's; they drift):

| Role | Endpoint | Channels | Reported latency |
|---|---|---|---|
| Virtual 7.1 **sink** (FR-2) | #31 `VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)` | 8 out | — |
| Loopback **capture** | #35 `VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)` | 2 in | **22.0 ms** (input) |
| (second cable) | #27→#36 VoiceMeeter **AUX** VAIO | 2→2 | — |

- **Capture works:** opened #35, read **23040 frames @ 48 kHz**, reported input
  latency **22.0 ms**, peak **0.0** (`ok: true, carrying_signal: false`).
- **Loopback round-trip:** rendered sweep to #31, captured #35 → **peak 0.0, no
  signal** (`status: loopback_silent`). The VAIO strip is not assigned to bus B1
  in the owner's live mixer, so no measured round-trip yet; the spike flagged the
  config gap (not 0 ms) and pointed at the setup doc.
- Portable helpers: **22/22** pytest green on WSL; ruff + format clean. The first
  host run also surfaced two real bugs, now fixed (see Interpretation).

`RESULT_JSON` (verbatim, 2026-10-15):

```json
{"virtual_sink": {"device": 31, "name": "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)", "max_output_channels": 8},
 "loopback_capture": {"device": 35, "name": "VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)", "max_input_channels": 2},
 "capture_probe": {"ok": true, "channels": 2, "samplerate": 48000.0, "reported_latency_ms": 22.0, "frames_read": 23040, "peak": 0.0, "carrying_signal": false},
 "loopback_roundtrip": {"ok": false, "signal_present": false, "out_device": 31, "in_device": 35, "roundtrip_ms": null, "peak": 0.0,
   "note": "no signal captured; cable render->capture routing not configured (VoiceMeeter: assign the VAIO strip to bus B1 ...)"},
 "status": "loopback_silent"}
```

## Interpretation

- **FR-2's path exists and the stream is capturable from WSL via interop.** The
  7.1 sink (8-ch VAIO) and its capture endpoint are present and resolve cleanly by
  name; we can open and read the capture stream. B0.3's "confirm a path + capture
  its stream" is met.
- **The clean render→capture loopback is now *exposed* programmatically** (the
  harness auto-pairs sink→capture and measures), but **completing the measured
  round-trip needs one user action** that is not mine to take on the owner's live
  mixer: route the VAIO strip to **B1** (or install a dedicated VB-CABLE whose
  internal loopback needs no routing). Both are documented. Until then the
  reported figures bound it: out 22 ms (B0.2) + in 22 ms ≈ **44 ms** on the
  VoiceMeeter chain — above the <30 ms target (requirements § 2), reinforcing that
  low latency needs the virtual mixer bypassed (B4/B8) or exclusive-mode hardware
  (B0.5).
- **Two real bugs the first host run caught** (why spikes run on real hardware):
  1. Feeding the *full* multi-host-API device list to the resolvers picked an
     **MME** VoiceMeeter duplicate, not the WASAPI one. Role resolution must be
     constrained to the WASAPI host API (`allow=` the WASAPI subset).
  2. That MME endpoint's name was **truncated to 31 chars** (`MAXPNAMELEN`),
     chopping the closing paren and breaking parenthetical pairing. WASAPI names
     are full, so constraining to WASAPI sidesteps it; the helper stays
     conservative (a truncated name forms its own family, never cross-pairs).
  A third, cosmetic: the Windows console is **cp1252** and aborted on a `→` in an
  output string — the spike now forces UTF-8 stdout and keeps notes ASCII.

## Learnings

- **A virtual 7.1 sink on Windows = VoiceMeeter VAIO (8-ch); VB-CABLE is stereo
  only.** Apps select `VoiceMeeter Input (VAIO)` as 7.1 speakers; the renderer
  captures `VoiceMeeter Output (VAIO)`. Pair a cable's halves by their shared
  trailing parenthetical, and resolve on the **WASAPI** host API only — the same
  cable appears under MME/DirectSound/WDM-KS with different latency, and **MME
  truncates names to 31 chars**, breaking name matching.
- **A VoiceMeeter loopback needs the strip routed to a B bus**; the capture
  endpoint opens and reads frames regardless (silent until routed). Report silence
  as a config gap, never as 0 ms. A dedicated **VB-CABLE** gives a zero-config
  internal loopback for a clean latency reference.
- **Windows console spikes: force UTF-8 stdout and keep output ASCII** — the
  default cp1252 codec aborts the whole run on a stray non-ASCII character.
- VoiceMeeter / VB-CABLE are **proprietary donationware, not redistributable** —
  the user installs them; we depend at runtime only, bundle nothing, add no
  Python dependency, introduce no GPL (requirements § 9 safe).

## Decision / next

B0.3 done: virtual 7.1 sink confirmed ✓, stream capture confirmed ✓, loopback
harness exposed ✓, user setup documented ✓. The **measured** round-trip is gated
on a one-time user routing step (VAIO→B1) or a VB-CABLE install — captured as
follow-up **B0.6**, not a blocker. Next eligible block: **B0.4** (head-tracker
spike — MediaPipe yaw/pitch/roll + inference latency), also runnable here via
interop against the webcam (journal 02).

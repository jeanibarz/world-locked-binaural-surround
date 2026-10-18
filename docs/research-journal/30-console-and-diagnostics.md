# 30 — The VR console refresh + a real-time diagnostics panel (B6.3)

**Date:** 2026-10-18  ·  **Milestone:** M6 (GUI)  ·  **Status:** done; both host GUIs reskinned to the mockup, diagnostics panel verified by rendered frames

## Question

Two owner asks after the app ran well: (1) finish the GUI so it matches the **mockup** we drew
for the calibration console (`scratchpad/calib_gui_mockup.html`, layout **A — side-by-side**),
and (2) add a **toggleable diagnostics panel** that shows audio dropouts, latency, buffer fill,
render headroom and an **end-to-end (head-move → ear) delay estimate**, readable by a
non-technical *and* a technical user — "is real-time fast enough, close to failing, or failing?"

## Method

- **A tested diagnosis core** — `binaural_headtracker.diagnostics` (pure, headless): `classify(...)`
  turns the live numbers the apps already hold (block size, sample rate, measured render ms,
  FIFO fill, device latencies, tracker rate, recent underruns) into a `Diagnosis` — render load
  vs the per-block budget, a stage-by-stage **end-to-end latency estimate** (tracking age +
  input + one block + FIFO drain + output buffer, with the unmeasurable camera exposure folded
  into a labelled estimate), and a plain verdict `healthy / tight / at risk / failing` with
  reasons. 9 tests pin the verdict bands and the latency arithmetic.
- **A shared OpenCV renderer** — `apps/_console.py` (host-only drawing): the mockup's palette
  and components — titlebar with a pulsing live pill, camera panel (subtle scanlines, face box +
  lock tag, yaw/pitch/roll HUD), the azimuth radar (rings, FRONT/LEFT/BACK/RIGHT, forward-FOV
  cone, nearest speaker with halo + dashed pointer, listener arrow, legend), a controls bar
  (readout chips + key-hint buttons), and the `stats_panel` overlay driven by the diagnosis.
  `compose()` lays out layout A so both apps share one view.
- **Wired into both host apps**: `calibration_gui.py` (tracker stats; audio panel reads "not
  running"), and `voicemeeter_passthrough.py` — now **instrumented**: the render worker records
  per-block render time (EWMA mean + a decaying peak), the output callback records underrun
  timestamps (one cheap append — the callback stays microsecond-light, L52), and the main loop
  reads FIFO fill + device latency to build the live `Diagnosis`. **S** toggles the panel; the
  radar stays visible (the overlay sits over the camera, not the radar).

## Result

- Both GUIs match the mockup (verified by rendering representative frames to PNG on the host and
  inspecting them — healthy, failing, no-face, and calibration states all render correctly).
- The diagnostics panel reads for both audiences: a big **HEALTHY/TIGHT/AT RISK/FAILING** pill +
  plain reasons ("the render missed the deadline") for a non-technical user, and the numbers
  (head→ear ms, render load % with peak marker, buffer blocks, dropouts, budget ms) for a
  technical one. On the owner's host the passthrough reads ~96 ms head→ear, render ~30% of budget.
- 9 new tests (301 total), ruff clean. Camera `CAP_PROP_BUFFERSIZE=1` set on both (freshest frame).

## Interpretation

The audio metrics only exist where the audio pipeline runs, so the diagnostics live in the
passthrough; the calibration console shows the tracker subset. Keeping the look in one
`_console` module (and the maths in a tested `diagnostics` module) meant the host-only cv2 code
stayed a thin view and the judgment logic is unit-tested. cv2 has only Hershey fonts, so the
type differs from the HTML mock; palette, layout and components match.

## Decision / next

GUI milestone is in good shape. The head→ear figure is a labelled *estimate* (camera exposure
is unmeasurable from software); a true measured end-to-end would need the loopback rig (B3.2).
The azimuth *accuracy* the owner noted is the 8-point BRIR discretisation (B1.8 better
interpolation / B1.11 rear data), unrelated to this view.

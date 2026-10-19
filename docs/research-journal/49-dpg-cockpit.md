# 49 — Dear PyGui cockpit (B6.12, slice 1: portable core + preview)

**Block:** B6.12 · **Milestone:** M6 (GUI) · **Status:** slice 1 done (foundation + preview);
slices 2–3 (live engine wiring, camera/controls) tracked in the backlog.

## Question

The owner asked for a GUI overhaul and, across two rounds of HTML mockups, converged on a
**cockpit**: a prominent camera feed, the head-move→ear **latency distribution + its
stale/queue/device components**, and **realtime engine controls** (interpolation method + a
CPU/Rust/GPU backend switch). The open question was the *runtime* — "Qt or not". Which stack, and
what is the smallest first slice that de-risks it?

## Context

Today's host console (`apps/_console.py`) is a single fixed-size OpenCV window shared by the
calibration GUI and the passthrough. B6.9 (journal 48) sharpened its **type** (TrueType over
Hershey) but not its **shape**: it stays one non-resizable window, keyboard-only, with the rich
latency data (measured p50/p95/p99 and the stale/queue/device split from B8.5e, journal 46) hidden
behind an **S** toggle instead of being the star. The whole recent arc (B8.5–B8.6) has been a
latency hunt, so the data deserves a front-and-centre home.

## Hypothesis

A native cockpit organised around the latency number — with live controls to switch method/backend
without a relaunch — is worth building, and the stack choice turns on **licensing**, because the
project targets a donationware / possibly-closed release (requirements §9; `pyproject.toml` says
"prefer MIT/BSD/Apache; flag GPL").

## Method

- **Mockups, not code, first.** Two rounds of self-contained HTML galleries (built from the real
  `_console.py` palette + the mockup's fonts) let the owner pick direction from viewable variants:
  round 1 (five directions) → they liked the native docked-panel look + the lag distribution + the
  realtime backend switch; round 2 (four *runtimes* for the same cockpit content + a licence/weight
  tradeoff matrix) → they asked about Qt's licence and chose **Dear PyGui**.
- **Licence finding (the deciding factor):** Qt's Python binding **PySide6 is LGPL-3** — legal to
  sell closed-source, but it obliges you to keep Qt a *replaceable* dynamically-linked library
  (relink provision) and ship licence notices on every release, or buy a commercial seat. **Dear
  PyGui is MIT** — no copyleft, no relink clause, static-link/close/modify freely. For "open-source
  now, maybe sell later" MIT is the clean fit; LGPL is a standing compliance tax.
- **Slice 1 scope** = the half that is testable on headless WSL + de-risks the look, with **zero
  risk to the working cv2 console** (purely additive):
  - `binaural_headtracker.cockpit` — a **pure view-model**: `latency_histogram` /
    `histogram_bin_edges` (binning with out-of-range clamping into the end bins), the console
    palette as RGBA tuples, and `build_view(diag, latency_samples, …) → CockpitView` turning a
    `Diagnosis` + raw `LatencyProbe` samples into ready-to-draw primitives (headline, verdict pill,
    component waterfall, histogram, status bars, readout chips). No Dear PyGui, no display.
  - `LatencyProbe.samples()` — a cheap atomic snapshot so the histogram has a real source (the
    existing `stats()` only exposed percentiles).
  - `apps/cockpit_view.py` — the Dear PyGui shell, **importing dpg lazily** exactly as `_console`
    imports `cv2`, drawn as **one full-redraw drawlist** (the whole canvas repainted each frame from
    the `CockpitView`, the same discipline as cv2 `compose`), with interactive controls as real
    widgets beneath + hooks (`on_method`/`on_backend`/…) for the live app. Ships a synthetic
    **`run_preview()`** so the native look is judgeable on the host before the live engine exists.
  - `pyproject.toml` optional `gui` extra (`dearpygui>=1.11`, MIT), host-only, out of the baseline.

## Result

- 18 new tests for the view-model (histogram edges/clamping/empty, verdict→colour, measured vs
  estimate paths, axis floor+round-up, probe snapshot); **full suite 363 passed, 1 skipped**, ruff
  clean. The view-model and `samples()` run on WSL with no display.
- `apps/cockpit_view.py` imports cleanly on WSL with Dear PyGui absent (`available()` → False), so
  CI stays green; it is never imported by the test suite.
- The cv2 console is untouched and remains the default front-end.

## Interpretation

The licence question was the real fork, and it resolved cleanly to MIT (Dear PyGui) — the two
HTML rounds earned their cost by turning a vague "overhaul the GUI" into a specific, licence-aware
stack decision before a line of GUI code. Splitting the portable view-model out from the dpg shell
(mirroring `radar`/`diagnostics`/`_type`) keeps the testable brain on CI and the untestable drawing
thin — the only part that needs the host.

**Honest limitation:** the Dear PyGui drawing itself is **not** verified — WSL has no GPU/display
and dpg isn't installed here, so `run_preview()` has not been run. That host visual check is the
first thing slice 2 should do (owner can run it now via `py -3.12 apps/cockpit_view.py`).

## Learnings

- A permissive licence (MIT/BSD) vs LGPL is a *first-class* GUI-stack criterion for a
  donationware/closed target: LGPL (PySide6) is sellable but carries a permanent relink+notice
  obligation; MIT (Dear PyGui) / BSD (pywebview) carry none. Decide licence before ergonomics.
- The established split — pure, tested model in `src/binaural_headtracker/`, host-only drawing in
  `apps/` with a lazy toolkit import — extends cleanly from cv2 to a second GUI toolkit. A
  full-redraw drawlist makes the dpg view a faithful function of the model, with no widget-state
  drift to debug.

## Decision / next

Build the cockpit in **Dear PyGui (MIT)**. Shipped slice 1 (foundation + preview). Next:

- **B6.12 slice 2** — host visual check of `run_preview()`, then wire the cockpit to the live
  passthrough engine (refactor the engine out of `voicemeeter_passthrough.py` so both the cv2
  console and the cockpit drive it; feed the real `Diagnosis` + `probe.samples()` + camera frame;
  make the control hooks mutate the running method/backend/cushion/predict).
- **B6.12 slice 3** — camera texture polish, radar drag-to-place parity with the cv2 console,
  session presets, and a `--ui {console,cockpit}` selector on the host app.

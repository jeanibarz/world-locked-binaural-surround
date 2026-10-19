# 50 — Cockpit on the live engine (B6.12 slice 2)

**Block:** B6.12 slice 2 · **Milestone:** M6 (GUI) · **Status:** done (code + GUI host-verified;
the live audio path is the owner's to confirm on the host).

## Question

Slice 1 shipped the Dear PyGui cockpit as a **preview** (synthetic data, no camera/audio). The
owner's reaction on seeing it run was the right one — *"is it working or a mockup? I don't hear
anything"*. Can the cockpit drive the **real** world-lock passthrough engine, so it actually
head-tracks the system audio, while the proven OpenCV console stays the default?

## Context

`apps/voicemeeter_passthrough.py` is the working app: WASAPI loopback captures the virtual 7.1,
a render worker world-locks it to stereo with the freshest head pose, an output callback drains a
cushioned FIFO to the headphones; an OpenCV window (`_console.compose`) shows camera + radar +
diagnostics and reads keys. Slice 1's `apps/cockpit_view.py` is a drawing shell over the pure
`binaural_headtracker.cockpit` view-model, with control **hooks** left unwired.

## Hypothesis

The display is the only part that needs to change: the per-frame compute (camera read → detect →
pose filter → optional prediction → `state["az"]` → test-source update → `classify`) is identical
for both front-ends, and the cockpit's hooks can mutate the same live engine state the console's
keys do. So a `--ui {console,cockpit}` switch can run either front-end on one engine.

## Method

- Added `--ui` (default `console`, so the working app is byte-for-byte unchanged). The cv2 window
  creation + mouse callback are guarded to console-only; the cv2 loop guard became
  `while args.ui == "console"`.
- Added `_run_cockpit()` (nested in `main`, closing over the engine handles): it builds a
  `CockpitWindow`, wires every hook to the live state under the same `lock` the audio threads use —
  `on_method`/`on_backend` rebuild the 7.1 renderer (via a `backend`-parameterised
  `build_main_renderer`, so CPU/Rust/GPU switch **live**), `on_predict` toggles orientation
  prediction, `on_test`/`on_solo`/`on_rotate` and `on_place_test` (radar click/drag) drive the test
  source, `on_recenter` recalibrates — then runs the same compute as the console loop and feeds the
  result to `cockpit.build_view` + `CockpitWindow.update`, with `set_test_state` mirroring the
  engine's test state (and the `source` checkbox) back onto the radar.
- The cockpit view's test source now defaults **off** (matching the engine) and exposes
  `set_test_state`; the preview sets it on so its radar marker still shows.
- Host launcher: `run_cockpit.cmd` = `run_passthrough.cmd` + `--ui cockpit`.

## Result

Lint clean; full suite **364 passed, 1 skipped** (the engine/ view changes are host-only; the pure
`cockpit`/`latency` logic stays covered). The cockpit **preview** (now from this branch) renders
correctly on the host — the view changes (`set_test_state`, checkbox tag, off-by-default source)
don't regress it. The live `--ui cockpit` path compiles and mirrors the console compute exactly.

## Interpretation

The slice-1 model/view split paid off: wiring to the engine touched only the display layer, and the
hooks map cleanly onto the existing live-mutation points (the `test`/`main_rend`/`state` dicts under
`lock`). Backend switching needed one additive parameter on the renderer builder; nothing else in
the audio path moved, so the console's behaviour is untouched.

**Honest limitation:** the **live audio path is not verified from here** — WSL has no VoiceMeeter
loopback or headphone device, so I can confirm the GUI/camera render but not that the sound is
world-locked. That check is the owner's: run `run_cockpit.cmd` with audio playing. Because the
change is additive and `--ui` defaults to console, a bug in the cockpit path cannot affect the
working console.

## Learnings

- A second front-end for a real-time engine is a *display swap*, not a rewrite, when the per-frame
  compute is separated from draw+input: both loops call the same compute and only differ in render
  + control source (cv2 keys vs cockpit hooks). Keeping `--ui` defaulted to the proven path makes
  the addition strictly additive and un-break-able for existing users.
- Live backend/method switching costs almost nothing once the renderer builder takes the choice as
  a parameter and the swap happens under the audio lock.

## Decision / next

Shipped slice 2. The owner verifies the live audio. Remaining **B6.12 slice 3**: live cushion
change (resize the FIFO safely), session presets, a keyboard-shortcut parity pass (flip/reload),
and camera-texture polish.

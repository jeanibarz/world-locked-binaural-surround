# 64 — One host entrypoint: a single start script with modes

**Block:** housekeeping / host DX · **Milestone:** M4 (host runtime) · **Status:** done —
one `apps/launch.py` dispatcher + one tracked `host/run.cmd`; the legacy
`live_tracker_audio.py` app retired; 423 tests pass.

## Why

The owner's host folder `C:\Users\ibarz\BinauralHeadtracker\` had grown **nine** launcher
files — `launch_live.py`, `launch_cockpit.py`, `launch_sweep.py`, `launch_passthrough.py`
and five `run*.cmd` wrappers — and it was "becoming a mess." They were not nine programs:
`run_passthrough.cmd` and `run_cockpit.cmd` launched the **same** app
(`voicemeeter_passthrough.py`), differing only by `--ui cockpit`; the rest were flag presets
over the same handful of apps, plus a stale slice-1 cockpit *preview* launcher. The owner
asked for **one** start script, and for the RFC-0003 blind listening test to be reachable
from it rather than from its own launcher.

## What was built

- **`apps/launch.py`** — the single entrypoint. First argument is a **mode**:
  - `listen` (default) — the daily world-lock passthrough, **cockpit** UI;
  - `test` — the same engine plus `--hrir-sets-dir`, so the cockpit exposes the RFC-0003
    **blind A/B + localization test** over the owner's set and the dense-grid candidates;
  - `calibrate` — the calibration GUI;
  - `latency` — the headless latency sweep.

  It computes its repo root from `__file__`, prepends `src`+`apps` to `sys.path` (so it runs
  the WSL checkout straight off a `\\wsl.localhost\...` UNC path, nothing installed), injects
  the per-mode defaults (`--ui cockpit` for listen/test; requires `--hrir-sets-dir` for test),
  and forwards everything else verbatim to the target app's `main()`. The app imports are
  deferred to dispatch time, so the launcher imports and unit-tests on headless WSL/CI without
  the host-only `cv2`/`sounddevice`/Dear PyGui stack.

- **`host/run.cmd`** (tracked template) + **`host/README.md`** — the one host wrapper. It
  carries the owner's device flags (EqualizerAPO baked-BRIR dir + output-EQ, FiiO out,
  `timealign`, `--rust`, block 2048, predict), dispatches on the mode, keeps the exit-97
  in-app-reload loop for listen/test, and supplies `--hrir-sets-dir %~dp0hrir_sets` for `test`.
  Deploy = copy this one file to the host folder; the nine old launchers are replaced by it.

- **Retired `apps/live_tracker_audio.py`** — the older single-source snare demo, superseded by
  the passthrough (which already mixes a movable test source). References in `AGENTS.md`,
  `devices.py`/`live_render.py` docstrings, and backlog **B6.10** (now obsolete) updated.

## Decision

The single entrypoint stays a **sys.path-injecting dispatcher launcher**, not a pip
`console_script`: AGENTS.md's host workflow depends on running the WSL checkout's code live
(edit Python, no redeploy; in-app reload on exit 97), which an installed console script would
break. New capability becomes a **mode** on `launch.py`, never another `run_*.cmd`.

## Verification

`tests/test_launch.py` (11 tests) covers mode parsing, the `--ui cockpit` default and its
override (`--ui console`, `--ui=console`), the `test`-mode `--hrir-sets-dir` requirement, and
verbatim forwarding for every mode — all with stubbed app modules, no host stack. Full suite
**423 passed, 1 skipped**; ruff clean.

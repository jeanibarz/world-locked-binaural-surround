# 39 — In-app reload (restart to pick up code changes) (B4.5)

**Date:** 2026-10-18  ·  **Milestone:** M4 (end-to-end on Windows)  ·  **Status:** done

## Question

The owner iterates on the code often, and because the host launchers import live from the WSL
checkout a **restart** is all it takes to run the latest version — but restarting meant *closing the
window, opening Explorer, running `run.cmd`, choosing 2, hitting Enter*. They asked for a one-key
in-app restart instead.

## Options

- **Re-exec in-process** (`os.execv`) — reloads the module, but re-opening WASAPI loopback + the
  output device from inside a process that still half-holds them is fragile; a half-released device
  can fail to re-acquire.
- **Exit-code + launcher loop** (chosen) — the app exits *cleanly* (its `finally` already stops and
  closes both audio streams, terminates PyAudio, releases the camera), then a loop in the `.cmd`
  relaunches a brand-new `py` process. Fresh import = latest code, and the devices are fully free
  before re-acquire. Robust, and it matches how the owner already thinks about "restart".

## Change

- `voicemeeter_passthrough.main()` returns **`RESTART_EXIT_CODE` (97)** when the owner presses
  **Enter** (a new *Reload* button in the controls bar); it sets a flag and breaks the loop so the
  existing clean teardown runs, then returns 97 (plain quit — **Q**/**Esc** — still returns 0). A
  short "restarting…" overlay gives immediate feedback before the window closes.
- Host `run_passthrough.cmd` wraps the launch in `:loop` … `if %errorlevel%==97 goto loop`, so 97
  relaunches and 0 falls through to `pause`. `launch_passthrough.py` already does
  `raise SystemExit(app.main())`, so the code propagates out through `py.exe` unchanged.

## Method / Result

- Logic verified by reading (flag + return; teardown unchanged) and ruff; 307 tests pass in WSL
  (the 3 `h5py` SOFA failures are pre-existing/environmental). No unit test: `main()` needs the full
  host audio/camera stack and the behaviour is an interactive keypress + a batch-file loop, neither
  headless-testable. The live check is the owner pressing **Enter** on the host.
- The only reused machinery is the exit code; the clean-shutdown path that makes a device re-acquire
  safe already existed (journal 23/24).

## Interpretation

The restart is now a single keypress and reuses the one mechanism already proven to release the
devices cleanly, so it is as reliable as a normal quit — just followed by a relaunch. Picking the
launcher-loop over `os.execv` trades one line of batch for not having to reason about partially-held
audio devices across an in-process re-exec.

## Learnings

- For "restart to pick up changes" on a host app that holds exclusive devices, prefer **clean exit +
  an external relaunch loop** over in-process re-exec: you reuse the shutdown path that already frees
  the devices, and a fresh process is an unambiguous reload. A distinct exit code (not 0/1) is the
  whole contract between app and launcher.

## Decision / next

- Shipped for the passthrough + its launcher. `run_binaural.cmd` (the snare demo) could get the same
  loop if the owner wants it there too — deferred until asked.

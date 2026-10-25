# Host launcher

The owner runs the apps on the **Windows host** (native `py -3.12`), not under WSL
(camera + WASAPI + a display). There is **one** start script — `run.cmd` in this folder —
and it is the only host launcher you need. It replaces the old
`launch_live.py` / `launch_cockpit.py` / `launch_sweep.py` / `launch_passthrough.py`
plus `run.cmd` / `run_binaural.cmd` / `run_cockpit.cmd` / `run_latency_sweep.cmd` /
`run_passthrough.cmd` — all of which were the same few apps with different flag presets.

## Deploy

Copy `run.cmd` into the host install folder next to the owner's assets:

```
C:\Users\ibarz\BinauralHeadtracker\
  run.cmd                 <- this file (double-click or `run.cmd <mode>`)
  face_landmarker.task    <- local C: copy (MediaPipe can't open a UNC path)
  dry_snare.wav
  hrir_sets\              <- dense_*.sofa candidates + report.json (for `test`)
```

`run.cmd` points at the WSL **main** checkout over `\\wsl.localhost\...` and injects
`src`+`apps` onto `sys.path` via `apps/launch.py`, so editing the Python needs no redeploy;
dev worktrees are deleted after merge, so the launcher deliberately does not chase them.
When the `\\wsl.localhost` paths or the EqualizerAPO config paths in `run.cmd` change,
edit the `set` lines at the top of the copied `run.cmd` — the rest is mode dispatch.

## Modes

| command | what it does |
| --- | --- |
| `run.cmd` / `run.cmd listen` | daily world-lock passthrough, **cockpit** UI |
| `run.cmd test` | passthrough + loads `hrir_sets\` for the RFC-0003 **blind A/B & localization test** |
| `run.cmd calibrate` | calibration GUI — recenter / axis-sign check |
| `run.cmd latency` | headless latency sweep against the real FiiO device |

Flags after the mode are forwarded, e.g. `run.cmd listen --method nearest --block 1024`,
`run.cmd listen --ui console`. `listen`/`test` relaunch automatically on the in-app Reload
(Enter → exit 97). See `AGENTS.md` → "Running the host GUI apps" for the host-interop gotchas.

## Native app (Tauri shell + installer)

`src-tauri/` is a tiny Tauri shell (`wlbs-shell.exe`) that opens one native WebView2 window on
the cockpit and spawns the engine via `run.cmd` (hidden, fixed port 8731, `--no-browser`); closing
the window stops the engine. `host/build-tauri.sh` builds it **and** bundles a Windows installer:

```
bash host/build-tauri.sh
# -> C:\Users\ibarz\wlbs-build\src-tauri\target\release\wlbs-shell.exe            (the shell)
# -> ...\target\release\bundle\nsis\World-Locked Binaural Surround_<ver>_x64-setup.exe  (installer)
```

Windows `cargo` can't build on the WSL 9P filesystem, so the script syncs `src-tauri/` to a Windows
build dir and runs `cargo.exe tauri build` there (`cargo tauri` comes from the `tauri-cli` crate,
which the script installs once if missing). The first bundle run downloads NSIS + the WebView2
bootstrapper; later runs reuse them and the kept `target/` cache.

**Scope:** the installer packages the *window* (shell + WebView2). The engine it launches still runs
the WSL Python checkout over `\\wsl.localhost\...` via the installed `run.cmd`, so the app is not yet
a standalone distributable for a clean machine — it needs the owner's WSL + Python stack at runtime.

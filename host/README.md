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

## Native window + live HTTP inspection — both at once

The nominal UI is a **native OS window** (the Tauri shell in `src-tauri/`). But the window and
browser-based inspection are **not** an either/or — they run **simultaneously, off the one engine**:

- The engine always serves the cockpit over HTTP on a **fixed port (8731)** and accepts **multiple
  concurrent clients** (the SSE subscriber list + per-connection MJPEG in `apps/cockpit_web.py`).
- The native window is simply **one** client — a WebView pointed at `http://127.0.0.1:8731`.
- **Any browser opening that same URL is another client, at the same time, on the same live session.**
  So you can watch the app in its native window *and* inspect/debug it in Chrome concurrently.
- `--no-browser` (what the shell passes) only means "don't *auto-launch* a browser" — it does **not**
  disable the server. The HTTP surface is always there to attach to.
- The engine shuts down only when **every** client disconnects (autoquit grace), so a browser can
  attach and detach freely while the native window stays open. Closing the native window stops the app.

**Cross-platform.** The window runs on Windows (WebView2), macOS (WKWebView) and Linux (WebKitGTK) from
the same source. Build it:

```
host/build-tauri.sh windows    # the Windows .exe, built from WSL (syncs to a Windows FS)
host/build-tauri.sh            # a native build for this host (Linux / macOS)
```

Linux needs the WebKitGTK dev packages (see the header of `build-tauri.sh`); macOS needs the Xcode
command-line tools.

**How the shell finds the engine** (no hardcoded path — resolved from the environment, so one binary
works on any host):

| var | platform | meaning |
| --- | --- | --- |
| `WLBS_ENGINE_CMD` | all | full command line for the engine, run via the platform shell. Must serve the cockpit on `$WLBS_PORT` (exported) and not open a browser. Wins when set. |
| `WLBS_RUN_CMD` | Windows | path to this `run.cmd`. Default: the owner's install location (so the existing Windows setup is unchanged). |
| `WLBS_LAUNCH` | Unix | launcher prefix. Default: `python3 apps/launch.py`. |
| `WLBS_HOME` | Unix | working directory the launcher resolves against (e.g. the repo root). |

**Inspecting while the native window is open:** the cockpit stays on `http://127.0.0.1:8731/index.html`
for as long as the shell runs — just open it in any browser; it attaches to the same live engine
alongside the window. (The legacy `run.cmd` path, with no shell, instead auto-opens the cockpit in
Chrome `--app` on a random port — fine for a quick look, but the shell's fixed 8731 is what makes
"window + browser, simultaneously" reliable.)

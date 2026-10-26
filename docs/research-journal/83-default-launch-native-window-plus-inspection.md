# 83 — Default launch is the native window, with HTTP inspection always concurrent

**Block:** B9.2b (native shell, feat/cockpit-ui-redesign) · **Milestone:** M9 (toward G1) · **Status:**
launcher wiring done; Windows headless-spawn fix applied; full per-OS run is a host step (no Tauri build
here).

## Question

The operator's requirement, clarified: **native window + HTTP inspection must be possible
simultaneously**, baked into the *default* launch — not an either/or mode. Today the default host launch
(`run.cmd` / `start-hidden.vbs`) opens the cockpit in **Chrome `--app` on a random port** — one client,
no native window, no stable URL to also attach a browser to. How do we make "window + browser, at once"
the thing you get by default?

## Context

The engine already serves an always-on HTTP cockpit that accepts **multiple concurrent clients** (the
SSE subscriber list + per-connection MJPEG in `apps/cockpit_web.py`), and autoquit fires only when
*every* client disconnects (`_autoquit_due`). The Tauri shell (`src-tauri/`) already starts the engine
headless on the **fixed** port 8731 (`--no-browser`) and shows a native window that is just one WebView
client of that server. So the capability existed — the native shell is a client, any browser is another
— it simply was not the *default* launcher, and the no-shell path used a random port.

## Hypothesis

Pointing the default host launchers at the native shell (instead of the Chrome path) delivers both
simultaneously with no engine change: the shell's fixed-port, headless, multi-client server already
supports a browser attaching alongside the window.

## Method

- `host/start-hidden.vbs` (Windows) repurposed to launch the native shell exe (overridable via
  `WLBS_SHELL_EXE`); new `host/run-app.sh` (macOS/Linux) launches the shell binary (overridable via
  `WLBS_SHELL_BIN`). Both are the *default* way to start the app.
- `src-tauri/src/main.rs`: the Windows engine spawn now sets `WLBS_HIDDEN=1` so `run.cmd` skips its
  trailing `pause` when driven headless by the shell (previously it could block the hidden process).
- `host/run.cmd` reframed as the explicit **console/Chrome dev path** (random port, in-app Reload loop),
  not the nominal launcher. README documents the one-launch "window + browser at `127.0.0.1:8731`" flow.

## Result

Default launch now routes through the native shell on every OS, and because that shell serves the fixed
8731 port with a multi-client server, a browser can inspect the same live session concurrently with the
window — the operator's "both at once," by default. Verified: `rustfmt` clean; the `cfg(unix)` launch
path compiles in isolation (journal 82). **Not** run here: a full Tauri build + an actual window + a
second browser client on the host — WSL has no WebView dev libs. That end-to-end confirmation is an
operator step (`host/build-tauri.sh`, then `start-hidden.vbs` / `run-app.sh`, then open 8731).

## Interpretation

"Both at once" was never an architecture problem — the server was already multi-client on a fixed port
under the shell. The gap was purely that the *default* launcher was the single-client Chrome path. The
fix is launcher routing + one headless-spawn nicety (`WLBS_HIDDEN`), not engine surgery.

## Learnings

- When a capability "should be possible," check whether it is merely not *wired into the default path*
  before designing anything — here the multi-client server already did the hard part.
- A GUI-subsystem shell exe is its own launcher; the `.vbs`/`.sh` wrappers exist only to fix the path
  and avoid a console flash, not to add behaviour.

## Decision / next

Merge once the operator confirms a host build. The hardcoded build-dir exe path in the launchers is
interim; the self-contained install location is the installer's job (feat/tauri-nsis-installer, G1).

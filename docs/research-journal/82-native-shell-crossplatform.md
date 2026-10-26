# 82 — The native shell builds cross-platform (Windows / macOS / Linux)

**Block:** native shell portability (`src-tauri/`, feat/cockpit-ui-redesign) · **Milestone:** M9 (toward
G1 self-contained installer) · **Status:** code done; Unix launch path compile-verified in isolation;
full Tauri build is host-gated (needs the platform WebView dev libs).

## Question

The native app window (the Tauri shell that wraps the local cockpit so the nominal UI is a real OS
window, not a borrowed Chrome `--app`) was written Windows-only. Can it be made to build and run on
Windows, macOS **and** Linux from one source without regressing the existing Windows launch? (Operator
request: *"make the native shell cross-platform first"*, before wiring it as the default launcher.)

## Context

`src-tauri/src/main.rs` had three Windows-only couplings: `#![windows_subsystem]` unconditionally, a
top-level `use std::os::windows::process::CommandExt` + `CREATE_NO_WINDOW`, and a hardcoded
`RUN_CMD = r"C:\Users\ibarz\BinauralHeadtracker\run.cmd"` launched via `cmd /c`. Tauri itself is already
cross-platform (WebView2 / WKWebView / WebKitGTK); only the **engine launch** and those cfg-less
Windows symbols were not. The rest (TCP-wait-then-navigate, kill-on-Destroyed) is portable.

## Hypothesis

Gating the Windows-only symbols behind `cfg(windows)` and resolving the engine command per-OS from the
environment (instead of a hardcoded path) is sufficient to build on all three OSes with no Windows
behavioural change.

## Method

- Split `spawn_engine(port)` into `#[cfg(windows)]` and `#[cfg(unix)]` impls; moved `CommandExt` +
  `CREATE_NO_WINDOW` inside the Windows one; gated `windows_subsystem` on `target_os = "windows"`.
- Replaced the hardcoded path with env resolution: `WLBS_ENGINE_CMD` (full command, any OS, wins when
  set), `WLBS_RUN_CMD` (Windows default = the owner's install path — unchanged), `WLBS_LAUNCH` +
  `WLBS_HOME` (Unix default = `python3 apps/launch.py` in the repo root). The chosen port is exported as
  `WLBS_PORT` for a custom command.
- Generalised `host/build-tauri.sh`: `windows` mode = the existing WSL→Windows-FS cargo.exe sync; no-arg
  = native `cargo build` in place. Documented the Linux WebKitGTK dev deps.
- Verified: `rustfmt` clean; the `#[cfg(unix)]` `spawn_engine` compiled in isolation with `rustc`
  (`UNIX_PATH_OK`) to catch std-API/type errors independent of Tauri.

## Result

Code compiles on the Unix path and is cfg-correct. The **full** Tauri build was not run here: WSL has no
`webkit2gtk` dev libs and no passwordless sudo to install them, and the Windows `.exe` still needs the
cargo.exe-on-C: path (the 9P-FS limitation this script already existed for). So "builds on macOS/Windows"
is verified by construction (cfg-gating + isolated compile), not by a green build on each OS — that is a
host step for the owner.

## Interpretation

The Windows-only-ness was incidental, not fundamental: three small couplings, all removable without
touching the window logic. The engine-launch abstraction also removes the hardcoded-path fragility the
standalone-distributable RFC (`rfc-standalone-distributable-and-rust-engine.md`, G1) names as the real
reason the app was non-distributable — a small step toward that north star, independent of the Rust
engine port.

## Learnings

- Tauri portability is free; the work is isolating the OS-specific *process launch* behind `cfg` + a
  clean, env-resolved command (no hardcoded paths).
- Keep the Windows default literal (overridable) so a portability change is a strict superset, not a
  behavioural change for the existing host.

## Decision / next

Merge the cross-platform shell. Next (separately, per the operator's "cross-platform first" ordering):
wire the native window as the **default** launcher and demote Chrome to an explicit inspection path;
owner to run `host/build-tauri.sh` on a real macOS/Linux host to confirm the green build.

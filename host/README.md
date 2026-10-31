# Host build & launch

The product is the **standalone Rust engine** `wlbs_engine.exe` — zero Python, zero
interpreter. It resolves all its assets (ONNX models, BRIR packs, `hp_eq/`, `web_cockpit/`)
relative to its own exe, so the bundle runs wherever it is dropped. It needs a **real webcam,
a real audio output, and a display**, so it runs on the **Windows host**, never under WSL.

## Build the bundle (from WSL)

`bundle-engine.sh` cross-compiles the engine with `cargo xwin` and assembles a self-contained
folder:

```
host/bundle-engine.sh <out_dir> [onnx_dir] [canonical_obj] [default_pack_dir]
```

```
<out_dir>/
  wlbs_engine.exe        the engine (onnxruntime static-linked)
  DirectML.dll           onnxruntime's DirectML EP (only runtime DLL dep)
  assets/                facedet.onnx, facemesh.onnx, canonical_face_model.obj
  packs/default/         the default BRIR pack (<ch>_L.wav / <ch>_R.wav)
  hp_eq/                 the bundled headphone-comp / output-EQ profile (on by default)
  web_cockpit/           the cockpit UI assets (shared, served by the control plane)
```

User-imported BRIR packs live under `%APPDATA%\WLBS\packs` (`wlbs_engine --import-pack <dir>`);
imported headphone-EQ profiles under `%APPDATA%\WLBS\hp_eq`.

## Run on the host

Copy the bundle to a per-user folder (e.g. `C:\Users\ibarz\WLBS-Engine\`) and double-click
`wlbs_engine.exe`. It prints `COCKPIT http://127.0.0.1:8732` and serves the cockpit UI there —
open that URL in Chrome for inspection, or use the native window (below).

Common flags (all optional — the engine picks sensible defaults and persists overrides to
`%APPDATA%\WLBS`):

| flag | meaning |
| --- | --- |
| `--source <name>` | input device name substring (default `VoiceMeeter`) |
| `--output <name>` | headphone output device name substring |
| `--pack <name>` / `--brir-dir <dir>` | pick a BRIR pack |
| `--port <n>` | cockpit HTTP port (default 8732) |
| `--block <n>` / `--latency-ms <ms>` | render block size / output cushion |
| `--method nearest\|timealign\|minphase\|notchwarp` | ring interpolation |
| `--list-devices` / `--list-packs` / `--list-hp-eq` | enumerate and exit |

Everything else (camera, flip, HRIR set, headphone comp, **per-position ITD**) is adjustable
live from the cockpit's Settings panel; changes reconfigure the engine without a restart.

## Native window

The Tauri shell (`src-tauri/`) is the optional native-app wrapper: it launches
`wlbs_engine.exe` (resolved next to its own exe) hidden on port 8732 and shows the cockpit in a
single native window; closing the window stops the engine. See `host/build-tauri.sh`.

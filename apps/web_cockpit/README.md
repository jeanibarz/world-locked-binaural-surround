# Web cockpit (pixel-faithful UI redesign)

A web front-end for the cockpit that reproduces the owner's redesign
(`docs/design/ui-redesign/`) near-pixel-perfect, intended to run in a desktop
webview (pywebview / Edge WebView2) driven by the existing engine.

## Status
- **Static UI — done & verified** (screenshot-compared against the mockups):
  - `index.html` + `style.css` + `cockpit.js` — main window (1280×820).
  - `settings.html` + `settings.css` — settings dialog (560×1000).
- **Live wiring — next:** a pywebview bridge (`WebCockpit`) that reuses the
  `binaural_headtracker.cockpit` view-model, pushes engine state + camera frames
  to `window.cockpit.applyState()` / `setCameraFrame()`, and routes the controls'
  `window.pywebview.api.*` calls back to the engine hooks; integrated as a new
  `--ui web` mode in `apps/voicemeeter_passthrough.py`.

## Preview standalone
`python3 -m http.server` here, then open `index.html` / `settings.html`
(they ship the mockup's sample values so they render identically offline).

## JS entry points (for the bridge)
- `window.cockpit.applyState(state)` — one engine tick's view-model (JSON).
- `window.cockpit.setCameraFrame(dataURL)` — a JPEG data URL for the camera panel.
- Controls call `window.pywebview.api.<name>(...)`: `set_predict`, `recenter`,
  `open_settings`, `window_min/max/close`, `ready` (+ the settings controls, wired
  with settings.js). The experiments drawer (`set_drawer_tab`, `blind_play`,
  `blind_shuffle_start`) is hidden until the engine implements it (#212).

## Native engine output controls

The Rust engine serves this cockpit over HTTP with SSE state updates. The output-gain
slider and peak/RMS/clipping meters use that live connection, and stay unavailable until
the engine confirms the setting. See [Output gain](../../docs/output-gain.md) for levels,
persistence, limits, and verification.

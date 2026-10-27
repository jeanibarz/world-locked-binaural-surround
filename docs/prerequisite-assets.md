# Prerequisite assets — what to download and where to put it

Before the engine can track your head and render audio it needs a few **data
files** that are deliberately **not** shipped in the source tree (they are large,
or personal, or have their own licences): the **head-tracking model**, a **BRIR
pack** (the per-speaker impulse responses it convolves), and — for neutral timbre
— a **headphone-compensation profile**. This page lists each one, says in a
sentence what it is for, and tells you exactly where it goes.

Which files you need depends on **how you run the engine**:

- **The standalone engine** (`wlbs_engine.exe`) — a zero-Python bundle. If you
  have this bundle, **the assets are already inside it**; this page is then just a
  map of what is where and how to override it.
- **The developer / owner launcher** (`host/run.cmd`, native Windows `py -3.12`
  driving the WSL checkout) — here you place the assets yourself next to
  `run.cmd`.

> **Scope note.** This guide covers only *obtaining and placing* assets. For the
> virtual audio device the renderer captures from, see
> [`virtual-device-setup.md`](virtual-device-setup.md). For the BRIR/HRIR data
> itself, see [`hrir-setup.md`](hrir-setup.md) and
> [`baked-brir-setup.md`](baked-brir-setup.md).

---

## 1. The standalone engine bundle (`wlbs_engine.exe`)

The standalone engine resolves every asset **relative to the executable**, so the
bundle is self-contained — drop it anywhere and run it (see
`rust-engine/src/assets.rs` and `host/bundle-engine.sh`). The bundle layout is:

```
<install>/
  wlbs_engine.exe        the engine
  DirectML.dll           the GPU/compute runtime the ONNX models need
  assets/
    facedet.onnx         finds your face in the webcam frame
    facemesh.onnx        turns that face into a head orientation (yaw/pitch/roll)
    canonical_face_model.obj   the reference head the pose is solved against
  packs/default/         the default BRIR pack: <ch>_L.wav / <ch>_R.wav for L R C LFE RL RR SL SR
  web_cockpit/           the cockpit UI the engine serves in your browser
```

**If you received a bundle, you do not need to download any of these** — they ship
with it. There is nothing to place.

> **One asset the bundle script does *not* place: the headphone-comp config.** The
> engine also looks for a headphone-compensation config at `<install>/hp_eq/`
> install-relative (resolution order below), and defaults it **on**. But the repo's
> bundle script (`host/bundle-engine.sh`) does **not** copy an `hp_eq/` directory
> into the bundle — the config lives in the source tree at
> `rust-engine/hp_eq/baked_7p1_rev26_final.txt` and no build script ships it. So on
> a script-built bundle, headphone EQ is absent and the engine logs
> `HP_EQ_WARN … headphone EQ disabled` and renders without it until you supply a
> config (put one under `<install>/hp_eq/` or `%APPDATA%\WLBS\hp_eq\`, or point
> `--hp-eq-dir` / `WLBS_HP_EQ_DIR` at it). See
> [`headphone-compensation.md`](headphone-compensation.md) for how to obtain and
> load a profile.

### What each asset is for (one line each)

| Asset | What it does |
|---|---|
| `assets/facedet.onnx` | Detects the face region in each webcam frame. |
| `assets/facemesh.onnx` | Estimates head orientation (the world-lock cue) from that face. |
| `assets/canonical_face_model.obj` | The reference 3-D head the pose solve is measured against. |
| `packs/default/` | The baked 7.1 → binaural impulse responses the renderer convolves. |
| `hp_eq/` (not bundled — see note above) | Corrects your headphone's own frequency response so the render sounds neutral. |

### Where the engine looks for each asset (resolution order)

For every asset the engine takes the **first** location that resolves (from
`assets.rs`):

1. an explicit **command-line flag**, then
2. an **environment variable**, then
3. **next to the executable** (the bundle layout above), then
4. **`%APPDATA%\WLBS\`** (your per-user data folder).

| Asset | CLI flag | Env var | Bundle path | User-data fallback |
|---|---|---|---|---|
| Head-detect / mesh ONNX nets | `--onnx-dir <dir>` | `WLBS_ONNX_DIR` | `assets/` | `%APPDATA%\WLBS\assets\` |
| Canonical face model | `--canonical <file>` | `WLBS_CANONICAL` | `assets/canonical_face_model.obj` | `%APPDATA%\WLBS\assets\canonical_face_model.obj` |
| BRIR pack | `--pack <name>` or `--brir-dir <dir>` | — | `packs/default/` | `%APPDATA%\WLBS\packs\<name>\` |
| Headphone-comp config | `--hp-eq-dir <dir>` | `WLBS_HP_EQ_DIR` | `hp_eq/` (looked for, but not placed by the bundle script) | `%APPDATA%\WLBS\hp_eq\` |
| Cockpit UI | `--web-dir <dir>` | `WLBS_WEB_DIR` | `web_cockpit/` | `%APPDATA%\WLBS\web_cockpit\` |

So you only ever place a file when you want to **override** a bundled asset or
**add** one (for example a BRIR pack of your own). The natural home for your own
additions is **`%APPDATA%\WLBS\`**, which never gets overwritten when you replace
the bundle.

If one of the *optional* assets is missing the engine does not crash — it prints an
`ASSET_WARN` line (e.g. head tracking unavailable, or the cockpit page will 404),
or the `HP_EQ_WARN` above, and carries on with what it has. The **BRIR pack is the
one hard requirement**, though: with no loadable pack the engine exits with an
error rather than warning.

### Adding your own BRIR pack

A **pack** is a directory holding the full baked 7.1 WAV set (`<ch>_L.wav` and
`<ch>_R.wav` for channels `L R C LFE RL RR SL SR`). Import one with:

```
wlbs_engine.exe --import-pack <path-to-pack-dir> [--name my-pack]
```

This copies it into `%APPDATA%\WLBS\packs\<name>\`, after which it appears in the
cockpit's HRIR picker. List what is installed with `wlbs_engine.exe --list-packs`.
See [`baked-brir-setup.md`](baked-brir-setup.md) for the pack layout, and
[`choosing-an-hrir-set.md`](choosing-an-hrir-set.md) for which set to pick.

> **TODO (needs a shipped installer to verify).** Whether an installer
> pre-populates `%APPDATA%\WLBS\` on first run, and how the ONNX nets
> (`facedet.onnx` / `facemesh.onnx`) are obtained for a *source* build, is not yet
> documented here — in `host/bundle-engine.sh` they are copied from the owner's
> private `facemesh_extract` directory, which is not part of this repo. For a
> bundled install they are already present and this step does not arise.

---

## 2. The developer / owner launcher (`host/run.cmd`)

`host/run.cmd` runs the WSL checkout's app natively on Windows. Its head tracking
uses **MediaPipe (Python)**, which needs a different head-tracking model from the
standalone engine (rendering itself still runs on the Rust backend via `--rust`).
It expects its assets in the **same folder as `run.cmd`** — on the
owner's host that folder is `C:\Users\ibarz\BinauralHeadtracker\` (see
[`../host/README.md`](../host/README.md)). The `set` lines at the top of `run.cmd`
point at them:

```
C:\Users\ibarz\BinauralHeadtracker\
  run.cmd                 the single host start script
  face_landmarker.task    MediaPipe FaceLandmarker head-tracking model
  dry_snare.wav           a dry source clip to render
  hrir_sets\              SOFA HRIR candidates + report.json (for `run.cmd test`)
```

The BRIR directory and the headphone-EQ config are referenced by `run.cmd` at
their EqualizerAPO install paths (`--brir-dir` and `--output-eq`), not copied next
to `run.cmd`.

### The FaceLandmarker `.task` model

The Python head tracker uses MediaPipe's **FaceLandmarker**, which needs a ~3.7 MB
`.task` model. It is **not** in git (see
[`head-tracker-setup.md`](head-tracker-setup.md)). Download it:

```bash
curl -L -o face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

Keep the file on a **local `C:` path** (next to `run.cmd` is ideal): MediaPipe's
loader cannot open a `\\wsl.localhost\...` UNC path (see
[`../AGENTS.md`](../AGENTS.md) → "Running the host GUI apps"). Override its
location with `--model` or `$BHT_FACE_MODEL`.

> This `.task` model is **only** for the Python launcher. The standalone engine
> (§1) does head tracking with the `facedet.onnx` / `facemesh.onnx` nets instead;
> the two are not interchangeable.

### The BRIR / HRIR audio data

The impulse-response audio is **not** committed (`.gitignore`: `*.wav`, `*.sofa`,
`data/`) because it is large and, for the baked set, personal data:

- **Baked 7.1 BRIR set** (what the owner's launcher renders) — the per-speaker
  WAVs under the EqualizerAPO config directory. See
  [`baked-brir-setup.md`](baked-brir-setup.md) for the layout and how the loader
  reads it.
- **Generic SOFA HRIR sets** (for the Python API and the `run.cmd test` blind A/B
  localization test) — fetch one yourself, e.g. MIT KEMAR. See
  [`hrir-setup.md`](hrir-setup.md) for the fetch command and the set to choose.

### The headphone-compensation config

`run.cmd` passes `--output-eq <config.txt>` pointing at the owner's EqualizerAPO
config — an EqualizerAPO-format file that downmixes to L/R and carries the per-ear
compensation + subjective EQ. See
[`headphone-compensation.md`](headphone-compensation.md) for how to obtain and load
your own, and [`baked-brir-setup.md`](baked-brir-setup.md) for how that config
relates to the baked bake.

---

## Summary — the shortest path

- **Using the standalone bundle?** The model, the default pack, and the cockpit are
  already inside it — nothing to download. The headphone-comp config is **not**
  placed by the bundle script, so supply one if you want EQ (or leave it disabled).
  Optionally import your own BRIR pack (`--import-pack`).
- **Using the Python launcher?** Download `face_landmarker.task` to a local `C:`
  path next to `run.cmd`, provide a baked BRIR directory, and (optionally) a dry
  source WAV and `hrir_sets\` for the test mode.

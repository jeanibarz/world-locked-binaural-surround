# wlbs_engine — Bug Tracker (overhaul audit, 2026-10-25)

## Status (kept in sync as batches land)

| Bug | Sev | Status | PR / branch |
|---|---|---|---|
| BUG-01 | P0 | Fixed | PR1 `fix/engine-bug-backlog` |
| BUG-02 | P0 | Fixed | PR1 |
| BUG-05 | P1 | Fixed | PR1 |
| BUG-06 | P1 | Fixed | PR1 |
| BUG-07 | P1 | Fixed | PR1 |
| BUG-08 | P1 | Fixed | PR1 |
| BUG-09 | P1 | Fixed | PR1 (recenter wired with the control drain) |
| BUG-17 | P2 | Fixed | PR1 |
| BUG-03 | P1 | Fixed | PR2 `fix/audio-robustness` (hard-error on source/output/pack rate mismatch) |
| BUG-04 | P1 | Fixed | PR2 (output exit flips `running` + surfaces the error) |
| BUG-14 | P1 | Fixed | PR2 (retry transient capture reads; give up only after a sustained run) |
| BUG-15 | P1 | Fixed | PR2 (Ctrl-C flips `running`; join audio threads; release devices) |
| BUG-18 | P2 | Fixed | PR2 (exact match wins; ambiguous substring errors) |
| BUG-19 | P2 | Fixed | PR2 (5.1 source; output endpoints with >2 channels) |
| BUG-20 | P2 | Fixed | PR2 (Int24; reject unsupported formats at open) |
| BUG-23 | P2 | Fixed | PR2 (poison-tolerant FIFO lock; lock-free ring noted as follow-up) |
| BUG-33 | P3 | Fixed | PR7 `fix/p3-buffer-strcam` (shared buffer = engine default `0`; discontinuity + silent flags surfaced) |
| BUG-10 | P1 | Fixed | PR3 `fix/vision-robustness` (`--no-camera` opt-out + "camera unavailable" telemetry) |
| BUG-11 | P1 | Fixed | PR3 (`load_canonical_rigid`/`Pipeline::new` → `Result`; pose liveness already in PR1) |
| BUG-12 | P1 | Fixed | PR3 (ONNX intra-threads capped; DirectML EP noted as follow-up) |
| BUG-28 | P2 | Fixed | PR3 (explicit resolution/fps; `--resolution`/`--framerate`; controlled default) |
| BUG-29 | P2 | Fixed | PR3 (reuse the frame f32 buffer; zero-copy crop noted as follow-up) |
| BUG-30 | P2 | Fixed | PR3 (`stop_stream` on exit; reopen on unplug; blocked-`frame()` interrupt noted) |
| BUG-31 | P2 | Fixed | PR3 (clamp `--pose-stride` ≥ 1) |
| BUG-32 | P2 | Fixed | PR3 (count + log camera decode failures) |
| BUG-34 | P3 | Partial | PR3 doc + PR7 string-indexed cameras now listed & selectable; live cockpit camera-switch + nokhwa COM reconfirm remain host-only follow-ups |
| BUG-13 | P1 | Fixed | PR4 `fix/dsp-perf` (resident RT scratch in render_core_lib + engine loop; single-thread conv benchmark noted) |
| BUG-21 | P2 | Fixed | PR4 (output clamp + non-finite drop; reject non-finite IR taps at load) |
| BUG-27 | P2 | Fixed | PR4 (stereo world-lock over the full ring grid) |
| BUG-16 | P2 | Fixed | PR5 `fix/lifecycle-packs` (`is_pack_dir` requires the baked set `BrirPack::load` reads) |
| BUG-22 | P2 | Fixed | PR5 (bad numeric flags error loudly; `arg()` precedence documented) |
| BUG-24 | P2 | Fixed | PR5 (startup existence check for web/onnx/canonical assets) |
| BUG-25 | P2 | Fixed | PR1 (held-yaw no-camera case now logs `POSE none (held yaw 0)`) |
| BUG-26 | P2 | Fixed | PR5 (`import_pack`: clear dest, reject `..`/separators, recursive copy) |
| BUG-35 | P0 | Fixed | PR6 `fix/com-mta-regression` (COM apartment regression: enumerate off main thread + tolerant `init_com`) |

> Baseline: the uncommitted "direct webcam capture (nokhwa) + `--list-devices`" edits were applied
> as the first commit on this branch.



Source: 5-way read-only code audit of `world-locked-binaural-surround` @ `feat/cockpit-ui-redesign`
(engine crates + cockpit UI), plus the in-progress camera-capture edits. Paths are `file:line`.
Severity: **P0** breaks a core promise · **P1** major (audio loss / silent failure / unusable feature) ·
**P2** should-fix correctness/robustness · **P3** minor/polish.

Legend of components: UI (web_cockpit + control-plane), AUD (audio-io), DSP (render/conv/pack/geometry),
VIS (vision/pose/camera), LIFE (main.rs orchestration/CLI/assets/packs).

---

## P0 — core features silently non-functional

- **BUG-01 · UI · Settings do nothing.** Every `POST /api/<name>` is stored in `Cockpit.dispatched`, but
  `take_dispatched()` is **never called** — dead code. The engine loop only ever `push_state`s. So camera,
  HRIR, method, block, cushion, comp, hp_eq, exclusive, predict, recenter, reset, begin/commit/cancel — all no-ops.
  `rust-control-plane/src/lib.rs:88,131-145` · `rust-engine/src/main.rs:220-253`. *(Your headline complaint.)*
  **Fix:** drain `take_dispatched()` in the loop and apply each event to live state; persist via `SettingsStore`.

- **BUG-02 · UI · Cockpit telemetry is dead (frozen on mockup).** Engine pushes `{"type":"state","pose":…,…}`
  with fields at top level; UI does `applyState(m.state)` → `m.state` is `undefined` → UI keeps hardcoded sample data.
  `apps/web_cockpit/cockpit.js:231` vs `rust-engine/src/main.rs:242-250`.
  **Fix:** wrap as `{"type":"state","state":{…}}` (the legacy wire format).

---

## P1 — major (audio loss, silent failure, or an unusable feature)

- **BUG-03 · AUD/DSP · No sample-rate reconciliation (dropouts + wrong spatialization).** Source rate, output rate,
  and `pack.samplerate` are never compared; no resampler; both streams use `autoconvert:false`. Rate mismatch
  (e.g. 44.1k device vs 48k pack) → continuous FIFO underrun/overflow = dropouts; also convolves the BRIR at the
  wrong rate (wrong ITD/pitch) and miscomputes cushion + fade. `main.rs:139,208-213,263,296-301`,
  `rust-audio-io/src/wasapi_backend.rs:83-91,158`. **Fix:** require `source==output`; resample or hard-error vs pack.
- **BUG-04 · AUD/LIFE · Output thread dies silently → "running but no sound."** On any output error it prints
  `OUTPUT_ERROR` and returns **without** setting `running=false`; render loop keeps filling a FIFO nobody drains.
  `main.rs:197-202,288-318`. **Fix:** set `running=false` + surface to telemetry on output error.
- **BUG-05 · AUD/UI · Idle source freezes the cockpit.** Capture timeout → `continue` skips the telemetry push, so
  an idle loopback endpoint produces zero UI updates. `main.rs:225-252`. **Fix:** time-driven telemetry heartbeat with
  FIFO stats + a `source_idle` flag, before the `continue`.
- **BUG-06 · UI · Telemetry payload omits almost everything the UI renders** (meters, buffer, dropouts, latency,
  health, `pose.yaw_deg`/facing). Only raw pose floats + fifo stats are sent. `main.rs:243-249` vs `cockpit.js:87-170`.
- **BUG-07 · UI/VIS · Camera preview always blank.** `img.src="/camera"` works but the real engine never calls
  `Cockpit::set_frame` (only the test bin does); vision consumes frames for pose only. `cockpit.js:238-244`.
- **BUG-08 · UI/LIFE · No settings persistence / round-trip.** `SettingsStore` never instantiated; `set_config`
  never called; begin/commit/cancel has no backend. `main.rs:145-152`, `rust-control-plane/src/settings.rs`.
- **BUG-09 · VIS · No recenter.** `yaw0` latches on first pose; no `PoseState::recenter()`; `Control::Recenter`
  unconsumed → off-axis bias is permanent. `vision.rs:31-37`, `rust-vision-client/src/lib.rs:51`.
- **BUG-10 · VIS · Auto-opens webcam 0 by default** (privacy surprise; contradicts the module's own doc note; silent
  tracking loss if the camera is held elsewhere). `main.rs:160-170`, `vision.rs:9-11`. **Fix:** make camera opt-in or
  telemeter "camera unavailable".
- **BUG-11 · VIS · Pose-thread death leaves a frozen yaw with no signal;** `load_canonical_rigid` uses
  `expect/unwrap/index` → a missing/malformed canonical OBJ **panics** the pose thread (asymmetric: missing ONNX
  returns a clean error). `main.rs:229,242-250`, `rust-vision-client/src/pipeline.rs:98-111`.
- **BUG-12 · VIS · ONNX inference unbounded + contends with audio.** No `with_intra_threads`/EP selection → uses all
  cores; two nets per estimate; competes with the pro-audio render thread → underrun risk; fights the "60fps without
  hogging CPU" goal. `pipeline.rs:47-52`. **Fix:** cap intra threads, consider DirectML EP, lower pose rate.
- **BUG-13 · DSP/AUD · Realtime budget risk: per-block heap allocations on the audio thread** (render-core-lib
  `targets`/`l`/`r`/LFE + bus mixer `desired`/`new_gain` + loop `pcm`/`pcm64`/`out`/`stereo`), plus rayon workers not
  priority-boosted, driving a 500 ms/24000-tap BRIR. `rust-render-core-lib/src/lib.rs:46,58,159,168-179`,
  `main.rs:232-236`, `rust-conv-core/src/lib.rs:157`. **Fix:** reuse scratch buffers; benchmark single-thread conv.
- **BUG-14 · AUD · Transient capture read error kills the whole engine** (`src.read(…)?`). `main.rs:225`.
- **BUG-15 · LIFE/VIS · No Ctrl-C/graceful shutdown; threads never joined; device/camera leaked on console close.**
  `main.rs:155,183,197,254-257`, `vision.rs:97-105`.

---

## P2 — correctness / robustness

- **BUG-16 · LIFE · `is_pack_dir` accepts packs `BrirPack::load` can't read** (filterpack.json-only / partial WAV set)
  → lists & resolves then fails with a confusing `L_L.wav` error; the manifest format is advertised but unimplemented.
  `packs.rs:23-28` vs `pack.rs:48-63`.
- **BUG-17 · UI/LIFE · Dead/placeholder config + wrong samplerate.** `methods/hrir/resolutions/framerates` are
  single-element stubs; `samplerate` hardcoded 48000 (wrong for non-48k packs); current-values (hrir_name, comp,
  exclusive, hp_eq, cushion, hp_models) never sent → stale/misleading Settings. `main.rs:145-152`.
- **BUG-18 · AUD · Ambiguous device match** (first case-insensitive substring) — `"VoiceMeeter"` can grab the wrong
  (2-ch) endpoint. `wasapi_backend.rs:38-45`.
- **BUG-19 · AUD · Rigid channel counts** — non-8/non-2 source is a hard error (no 5.1); output must be exactly 2-ch.
  `main.rs:264-284,299-301`.
- **BUG-20 · AUD · Unsupported bit depths silently corrupt** — 24-bit source → silence; unknown output format → short
  buffer/garbage. `wasapi_backend.rs:56-63,175-188`.
- **BUG-21 · DSP · No output clamp/limiter (clipping); NaN/Inf IR tap poisons output forever** (persistent FFT state);
  no finite check at load. `main.rs:236`, `pack.rs:35`, `rust-render-core-lib/src/lib.rs:194-197`.
- **BUG-22 · LIFE · Silent flag-parse fallbacks** — bad `--camera` → camera 0; numeric flags default on typo; `arg()`
  first-match precedence is surprising; a value-flag with no value swallows the next token. `main.rs:49-51,98-101,123-124`.
- **BUG-23 · AUD/DSP · `Mutex` on the realtime path + `.unwrap()` poison cascade** (priority inversion; a pose-thread
  panic poisons the FIFO lock → audio thread panics). `main.rs:229,237,241,309`.
- **BUG-24 · LIFE · Missing asset dirs degrade silently** — missing `web_dir` → 404s; missing onnx/canonical → failure
  buried in the vision thread; no startup existence check. `assets.rs:28-55`, `main.rs:104-120`.
- **BUG-25 · LIFE · No pose source + no camera → held yaw 0 with no log line** (silent "tracking off"). `main.rs:160-190`.
- **BUG-26 · LIFE · `import_pack`**: doesn't clear the dest (stale WAVs on re-import), copies top-level only, and the
  `--name`/`--pack` value is unvalidated (`../..` path traversal). `packs.rs:58-64,69-87`.
- **BUG-27 · DSP · Stereo world-lock collapses to a 2-point grid** (no movement until the ±30° bisector). `main.rs:275-282`.
- **BUG-28 · VIS · `AbsoluteHighestFrameRate` → uncontrolled resolution** (tiny→poor detection, or 1080p→heavy); ignores
  the cockpit resolution/fps options; no `--resolution`/`--framerate`. `vision.rs:81`.
- **BUG-29 · VIS · Per-frame full-frame `u8→f32` alloc** (~11 MB/frame @720p) though only 128²/256² crops are used.
  `vision.rs:114-118`.
- **BUG-30 · VIS · Camera not promptly released on shutdown**; blocked `frame()` not interruptible; no reopen on unplug.
  `vision.rs:97-105,129`.
- **BUG-31 · VIS · `--pose-stride 0` disables all pose updates** (`is_multiple_of(0)`). `vision.rs:107`.
- **BUG-32 · VIS · Camera decode failures swallowed** (no log, no miss count) → silent frozen tracker. `vision.rs:110-113`.

---

## P3 — minor / polish

- **BUG-33 · AUD · WASAPI buffer = device `min_time`** (aggressive); capture discontinuity/silent flags discarded.
  `wasapi_backend.rs:88-89,127,157-158`, `main.rs:225`.
- **BUG-34 · VIS · Stale module doc** (claims webcam path "left out"); `enumerate_cameras` drops `String`-indexed
  cameras and `--camera` only takes `u32`; `Control::SelectCamera` unimplemented (no runtime camera switch); confirm
  nokhwa `input-native` does its own COM/MFStartup on the capture thread (`init_com` not called there). `vision.rs:9-11,47-50,97`.

---

## Notes — audited and found CORRECT (don't re-chase)
- `CushionFifo` + `cushion_blocks` logic (well unit-tested); only the engine wiring around them is buggy.
- Partitioned-convolution kernel (overlap-save, DC/Nyquist handling) — correct, unit-validated.
- Pack channel order / ear mapping / zero-padding / int scaling; LFE correctly NOT world-locked.
- Yaw sign/azimuth math; pose-source priority ordering; nokhwa RGB8→RgbImage mapping; stereo path has no out-of-range.
- control-plane static-file serving has a correct traversal guard.
</content>

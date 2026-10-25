# FEATURES — what the app does today (capability inventory)

The **capability view** of the project, beside the *spec* view (`requirements.md`) and the
*work* view (`backlog.md`). It answers one question those two don't: **"what can the app
actually do right now?"** — the list you'd give a new user, a colleague, or a reviewer.

**This file reuses the `agent-notebook` pattern** (see `kookr` `docs/reference/agent-notebook.md`),
generalized from agent *memory* to project *tracking*:

- **Bounded, read whole.** One short entry per capability; no retrieval, no ranking. If the file
  grows past ~300 lines, entries are too verbose or the app has sprawled — consolidate.
- **Atomic entries.** Each line is one user-facing capability, ≤ ~2 lines. A capability that needs
  a paragraph is a *design* — it lives in `requirements.md`, an RFC, or a journal entry, and is
  only *referenced* here.
- **Status, not votes.** Features aren't evicted; their status changes. Legend:
  `✅ shipped` · `🟡 partial / in progress` · `⬜ planned (backlog)` · `🔮 vision (RFC/future)`.
- **Maintained by a ritual, so it can't rot.** The one-block loop updates this file whenever a
  block changes a user-facing capability (see `AGENTS.md` → "The one-block loop").

Refs point at the authority: `B#.#` = backlog block, `FR-#` = requirement, `J#` = journal entry.
Status reflects the `feat/cockpit-ui-redesign` line (the current app), not stale `main`.

---

## Core effect — world-locked binaural surround

- ✅ **World-locked 7.1 → binaural.** Any 7.1 source is rendered to headphones so the virtual
  speakers stay fixed in the room as you turn your head. The product's whole point. (B3.1, FR-3)
- ✅ **Any app's audio becomes head-tracked.** Captures a system 7.1 stream (game, film, music)
  and world-locks it live, not just a built-in demo. (B4.1, B4.4)
- ✅ **Non-directional LFE.** The `.1` channel is routed flat to both ears, not convolved. (B4.3)
- ✅ **Click-free steering.** Channels crossfade to their nearest BRIR as the head moves — no
  zipper noise on retarget. (B3.1, J-crossfade)
- 🟡 **Perceptual "fixed virtual speaker" demo** — a standalone show-off demo is not built. (B3.2)

## Head tracking

- ✅ **Webcam head-pose tracking.** MediaPipe FaceLandmarker → smoothed yaw/pitch/roll at ~30 Hz.
  (B2.1, FR-2)
- ✅ **Neutral calibration / recenter.** Define "looking at screen centre"; recenter live. (B2.2)
- ✅ **Orientation prediction.** An α-β predictor extrapolates the pose to the audio-render instant,
  self-tuning to the measured pose-age-at-ear, cutting felt lag. (B8.6, J47)
- 🟡 **Graceful camera loss** — freeze/fade on camera drop is partial. (B4.2)
- ⬜ **Higher tracker rate / lower frame age.** (B0.7)

## HRIR / BRIR filters

- ✅ **Owner's measured BRIR set.** Loads the owner's hand-tuned 7.1 baked BRIRs (EqualizerAPO
  `.wav`), convolve-and-sum. (B1.6)
- ✅ **SOFA HRIR loading + interpolation.** Reads a SOFA set; interpolates an IR for any (az, el)
  with ITD-aware time-alignment. (B1.1, B1.2)
- ✅ **Dense-grid HRIR sets (RFC-0003).** Loads dense horizontal grids emitted by the sibling
  `binaural-localization` project; nearest-on-dense is flip-free, no synthesis. (hrir_sets.py)
- ✅ **Live HRIR-set switching.** Switch between the owner's set and candidate dense grids live in
  the cockpit. (feat/live-hrir-switcher)
- ✅ **CTF + diffuse-field compensation variants.** Each set carries a tonally-faithful (CTF) and a
  diffuse-field (DF) variant; selectable (`comp = ctf | df | both`). (recent)
- ✅ **Interpolation methods, A/B-able live.** nearest / timealign / minphase / notchwarp, switched
  live. (B3.3, B1.7)
- ✅ **Per-channel + bus parametric EQ (biquads), live.** (B5.1)
- ✅ **Per-ear compensation FIR on the stereo bus.** (B5.3)
- ⬜ **Preset save/load** (layout + HRIR + EQ + calibration). (B5.2)
- 🔮 **SOFA → filter-pack toolkit** — import from a dataset, CTF/DF decomposition, analysis/plots,
  bake 5.1/7.1/N-channel packs the engine loads. Separate companion project. (RFC pending)

## Cockpit UI

- ✅ **Web cockpit (the only UI).** One HTML/JS interface served over HTTP, shown in a native
  WebView2 window; replaced the old Dear PyGui / cv2 consoles. (feat/cockpit-ui-redesign, B6.12)
- ✅ **Live engine controls.** Interpolation method, CPU/Rust backend, HRIR set, comp, predict
  toggle, recenter — all live, under the audio lock. (B6.3, B6.5)
- ✅ **Camera preview + world-frame radar** with head/speaker positions. (B6.4, B6.5)
- ✅ **Crash-safe live-preview settings.** Settings persist atomically; a bad setting is prevented
  at apply time, with a DEFAULTS backstop if a start never completes. (rfc-crash-safe-settings)
- 🟡 **Device pickers** (camera + audio) — present; some host-GUI polish pending. (B6.2, B6.1)
- ⬜ **HiDPI / resizable legacy console** — moot now the web cockpit is the UI. (B6.11)

## Host app & packaging

- ✅ **Native desktop app (Tauri shell).** One native window titled "World-Locked Binaural
  Surround", app icon, hidden launcher (no console); closing it stops the engine. (PR #121)
- ✅ **Windows NSIS installer.** `host/build-tauri.sh` → `cargo tauri build` bundles a
  `*-setup.exe` (shell + WebView2 bootstrapper). (PR #122)
- ✅ **Single host entrypoint.** `run.cmd {listen|test}` forwards to one launcher; in-app reload
  (exit 97) picks up code changes with no restart. (B4.5, host/run.cmd)
- 🟡 **Installer is not yet a clean-machine distributable** — the engine still runs the WSL Python
  checkout via `run.cmd` at runtime. (PR #122 scope note)
- ⬜ **Packaging / user docs / donationware release.** (B9.2, FR-9)
- 🔮 **Pure-Rust engine + bundled assets** — eliminate the Python runtime dependency; MediaPipe is
  the one hard blocker. (RFC pending)

## Diagnostics, latency & performance

- ✅ **Native Rust render kernel.** Partitioned-FFT convolution via PyO3; bit-exact vs Python,
  ~5× real-time on CPU, dropped the GPU dependency. Optional accelerator (`--rust`). (B8.2, J27)
- ✅ **Output-FIFO cushion with re-prime.** A cushion absorbs render jitter; after an underrun it
  rebuilds to full depth so the margin persists. (J81)
- ✅ **Process runs at HIGH priority** so background CPU load can't starve the engine. (PR #123, J82)
- ✅ **Live diagnostics panel.** Render load (7.1 + test split), block size, out-buffer fill vs
  cushion, dropout strip (underrun vs overflow). (B6.7, cockpit)
- ✅ **Pose-age-at-ear latency split** (stale / queue / device), measured live. (B8.5, J46)
- 🟡 **Automated latency sweep / component split harness** — partial; needs host hardware. (B8.5b/c)

## Calibration & listening tests

- ✅ **Blind A/B + localization test (RFC-0003).** Shuffle HRIR sets blind, play A/B, score
  localization accuracy and front/back confusions. (`run.cmd test`, hrir_sets.py)
- ✅ **Judge-model registry consumer.** Resolves the best validated judge model from the sibling
  project. (B7.0)
- ⬜ **Synthetic-listener judge integration** for calibration. (B7.1)
- ⬜ **Guided blind-A/B / staircase calibration harness.** (B7.2)
- ⬜ **HRIR selection/fine-tuning toward morphology + headphone EQ.** (B9.1)

## Platform support

- ✅ **Windows host** (camera + WASAPI + the native window) — the primary target.
- ✅ **Linux/WSL for development & headless tests** — the DSP/pose math unit-tests without a
  camera/audio device; the Rust kernel cross-compiles to Windows from Linux. (B8.3, J28)
- 🔮 **macOS** — intended cross-platform, not yet built/tested (no Mac to hand). (FR, email)

---

### Maintenance

When a block ships, changes, or retires a **user-facing capability**, add or flip its entry here in
the same commit that updates `backlog.md` — one atomic line, with a ref. Keep the file read-whole:
if an entry needs more than ~2 lines, the detail belongs in `requirements.md`, an RFC, or a journal,
and this file only links to it.

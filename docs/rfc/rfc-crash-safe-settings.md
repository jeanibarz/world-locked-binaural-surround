# RFC: Safe settings — persistence + apply-time validation (not crash recovery)

**Status:** Draft (v2 — restructured after a 5-critic panel + a consensus attack falsified v1's premise
from the source code)
**Date:** 2026-10-24
**Author:** Jean Ibarz (with Claude)

> **v2 note (post-review).** v1 proposed a BIOS-style three-tier config (DEFAULTS / known_good /
> pending) with a crash marker and a fail-count escalation ladder, on the premise that **a bad setting
> manifests as a process crash**, detectable at the next startup. A 5-critic panel shredded the state
> machine (the launcher doesn't auto-relaunch on a crash; `promote` cleared the marker so
> post-promotion faults were invisible; `pending` was never loaded at startup; auto-revert re-poisoned
> known_good; a crash usually means a *device vanished*, not a bad setting). Then a **consensus attack
> read the engine source and falsified the premise itself**: for almost every risky setting, a bad
> value does **not** crash the process — it is caught in-process, self-heals, degrades-but-stays-alive,
> or exits cleanly with a message. So "detect the crash and recover at next boot" was solving a problem
> that mostly doesn't exist, while being **structurally blind** to the one that does (a live setting
> that degrades without crashing, e.g. a too-small block size → permanent xrun storm, where the process
> never exits so a startup-marker never fires). Worse, v1's fix (add a crash-relaunch loop to
> `run.cmd`) would have *created* a crash-loop hazard that doesn't exist today (exit-1 → `pause` can't
> loop). **v2 reframes around apply-time validation + prevention**: make it structurally impossible for
> a setting to take down the app, so there is nothing to "recover" from. Persistence then becomes a
> simple atomic save/load. The full review is logged at the end.

---

## Problem

The cockpit exposes live settings — camera (device / resolution / fps), engine (method, backend),
audio output (block size, cushion, exclusive device), HRIR filter + compensation, headphone EQ. They
are ephemeral today (launch flags; tweakable live; not saved). The owner wants **save / load /
reset-to-defaults**, and — the real requirement — **a saved setting must never be able to render the
app unusable** such that recovery requires knowing a config file exists and deleting it.

**What is the actual failure surface?** v1 assumed "bad setting ⇒ process crash ⇒ recover next boot."
Reading `apps/voicemeeter_passthrough.py` and `host/run.cmd`, that is false for nearly every setting:

| Setting | What a bad value actually does | Source |
|---|---|---|
| backend (rust/gpu) | **Caught in-process**, falls back to CPU | `build_main_renderer` try/except (≈ lines 291, 302) |
| block size | **Applies live, no restart**; too-small = missed deadlines = **xrun storm, process stays alive** | `_set_block` (≈ 864–876); callbacks swallow `BaseException` (≈ 587, 656) |
| camera device / resolution / fps | OpenCV **clamps** to a supported mode; a dead read triggers release+reopen — **self-healing** | `apply_camera_settings`; camera-lost reopen (≈ 1120–1125, 1254–1261) |
| exclusive device / loopback / HRIR / event handle | **`raise SystemExit(msg)` at init → exit 1**, a clean, catchable, synchronous failure with a message | ≈ lines 441, 447, 510 |
| (any of the above) at runtime | render/output callbacks **catch `BaseException`** → "never crash the app" | ≈ 587, 656 |

`host/run.cmd` loops **only** on exit code 97 (a user-pressed Reload); every other exit falls to
`pause` with **no relaunch**. So the *current* behaviour on a bad init setting — exit 1, pause — is
already safe (it cannot crash-loop; nothing relaunches it).

Two true failure classes remain, and neither is a process crash:
- **C-LIVE (degrade-but-alive):** a live setting that makes the running app bad without exiting — the
  canonical case is a too-small block size → permanent xrun storm. v1's crash-marker is blind to this
  (no exit ⇒ no marker ⇒ no revert; the user is stuck until they manually kill the app).
- **C-INIT (clean fatal at startup):** a persisted setting that fails `SystemExit` on the next launch
  (e.g. the exclusive device is now busy, the SOFA path is gone) → exit 1 → `pause`. Catchable and
  synchronous, but today it *does* drop the user at a dead console with no automatic fallback.

The only genuinely-uncatchable residual is **C-NATIVE**: a native driver **hang** on, e.g., WASAPI
exclusive open or a wedged device (the code itself flags driver bugchecks as the real danger, ≈ line
488). That is a tiny, specific surface — the one place isolation (a subprocess probe) earns its keep.

## Goals

- **G1** — Persist settings; load on startup; **reset to built-in defaults** from inside the app
  (one click), never by editing/deleting a file.
- **G2 — The app can never be made unusable by its own settings**, achieved **by construction**: no
  setting-apply path and no settings-load path may be able to take the main process down or leave it
  wedged. Worst case, a bad persisted value is ignored and the app runs on the default for that key.
- **G3** — A setting that would fault is caught **at apply time** (or at load), rejected with a clear
  message, and the previous value kept — **before** it affects the running app, not after a crash.
- **G4** — No new crash-loop / relaunch machinery is introduced. The launcher's current
  exit-1 → `pause` (no relaunch) stays; we do **not** add an auto-relaunch loop.

## Non-goals

- Named presets / profiles (future).
- Cloud sync.
- Surviving a true kernel bugcheck (a userspace design can't; the subprocess probe reduces the chance
  of *triggering* one, nothing more).
- A general crash-recovery framework. We are deliberately **not** building one — see Alternatives.

## Design

Three layers, in priority order. The first two deliver G2/G3 by construction; the third is a minimal
honest backstop for the narrow residual.

### A. Apply-time validation + prevention (the primary mechanism)

Classify each setting key once, in a **key-descriptor table** (owned by the config schema), by how a
bad value fails — `catchable` / `live-reversible` / `native-probe` — and route apply accordingly:

- **`catchable`** (backend, HRIR, comp, exclusive-open, output/loopback selection): wrap the apply in
  `try/except`. On failure, **keep the current value**, do not mutate live state, and toast the reason
  (the existing `backend_switch_note` / toast machinery). Crucially, **make engine *init* resilient
  too**: the `SystemExit(msg)` paths (exclusive device busy, missing SOFA, loopback mismatch) become
  *catch → fall back to the default for that key → toast*, instead of killing the process. This alone
  converts the entire C-INIT class into a non-event (G2 for startup).
- **`live-reversible`** (block size, cushion, method): these already apply live and are reversible in
  process. Apply, then **watch health for a short window** (reuse the existing xrun/fifo/`cb_error`
  signals): if the live change degrades the running app (sustained xrun storm, starved fifo) beyond a
  threshold within the window, **auto-revert to the previous value in-process** and toast — no exit,
  no restart. This is the ONLY correct way to catch C-LIVE (block-size xrun storm), which the v1
  crash-marker could never see.
- **`native-probe`** (WASAPI exclusive open, a camera format that can hang a driver): before committing
  the value, **open it in a short-lived child process**; adopt the setting only if the child reports
  success within a timeout. If the child hangs or dies, the parent **never adopts** the value and the
  main process is never put at risk. This isolates the C-NATIVE residual without any persistent
  machinery. (A true kernel bugcheck is out of scope; the probe only contains userspace hangs.)

Because every apply path is now catch / revert / probe, **a setting can't take the app down**, so
there is nothing to recover from — the hard problems v1 fought (stability definition across process
boundaries, escalation ladders, device-gone-vs-config-faulted, re-poisoning known_good) **do not
arise**.

### B. Persistence (save / load / reset) — simple and atomic

- **One file**, `cockpit_config/settings.json`, holding only the **user-tweakable** keys (NOT host
  identity: model/SOFA/BRIR/source paths and device *names* come from the launch profile / `run.cmd`
  flags and are not persisted here — see precedence below). Schema-less beyond a flat key→value map.
- **Write** atomically: `*.tmp`, `f.flush()`, `os.fsync()`, `os.replace()` (durable against power loss,
  not just process crash). Writes happen on an explicit **Save** and/or a debounced auto-save after a
  change settles (TBD; see open questions) — but persistence is decoupled from any "proof" ceremony.
- **Load on startup**, then **validate each key through the same key-descriptor table** used at apply
  time: an invalid / unsupported / now-failing value is dropped and the **default for that key** is
  used (per-key fallback, with a toast listing what was dropped). A corrupt/unparseable file ⇒ treated
  as absent ⇒ all defaults. This is exactly layer A applied at load time, so loading is as safe as
  applying.
- **Reset to defaults** = overwrite `settings.json` with DEFAULTS (atomic) + re-apply. **Scope is
  explicit** in the UI: it resets audio/camera/engine/HRIR settings and does **not** touch the
  head-tracking **calibration** (which is separate state) — the button/confirmation says so.

**Config precedence** (resolves the boundary critic's finding): `DEFAULTS (in code)` < `launch profile
(run.cmd flags: host assets + device names)` < `settings.json (user-tweakable keys)` < `--safe`. The
launch profile supplies host identity; `settings.json` supplies only the user knobs; `--safe` /
`COCKPIT_SAFE=1` forces DEFAULTS for the user knobs (keeping host identity) as a deliberate escape
hatch that needs no file knowledge.

### C. Minimal last-resort backstop (honest about the residual)

Layers A+B make bricking essentially impossible, but to be safe-by-construction against **any**
uncatchable startup fault we didn't foresee, keep **one** tiny mechanism:

- A **`starting` marker** written (fsync) immediately before the risky part of init and cleared as
  soon as the cockpit server is serving. If, at the next startup, the marker is **still set**, the last
  start never reached a usable UI ⇒ **start with DEFAULTS for the user knobs this once** and surface a
  one-line "started with default settings after the previous start didn't complete" banner. This
  bottoms out at DEFAULTS in **one** step; it never escalates, never relaunches, never deletes the
  user's `settings.json` (it's renamed to `settings.bad.json` so the values are recoverable).
- **Marker semantics are fail-safe**: an unreadable/corrupt marker is treated as "still set" (suspect),
  not clean. The clear happens on reaching the serving state, **not** from a `finally`/`atexit`/signal
  handler (which would mask a real failure). A single-instance **lock file** in `cockpit_config/`
  prevents a second launch from racing the marker.
- **Honest launcher note:** because `run.cmd` does not relaunch non-97 exits, this backstop takes
  effect on the user's **next launch**, not automatically mid-session. We do **not** add a relaunch
  loop (G4). In practice layer A makes this backstop almost never fire, since init is resilient.

## Files to change

- **`config_store.py`** (new, small): atomic `load()/save()/reset()`; the **key-descriptor table**
  (key → default, type/range validator, failure-class `catchable|live-reversible|native-probe`);
  per-key validation. Pure, unit-testable, no engine/audio dependency.
- **`probe.py`** (new, tiny): `probe_native(key, value) -> ok` running the open in a child process.
- **`voicemeeter_passthrough.py`**: make init resilient (catch the `SystemExit` config-failure paths →
  per-key fallback + toast instead of exit); route each settings hook through the descriptor table
  (catch / live-watch-revert / probe); write/clear the `starting` marker; resolve effective config via
  `ConfigStore` + precedence.
- **Web/UI**: Save, Reset-to-defaults (scoped), the two banners (per-key-dropped on load; default-fallback
  backstop). Settings changes call **engine commands**, not the store directly.

## Edge cases

- **Too-small block → xrun storm**: caught by the `live-reversible` health-watch auto-revert (A). ✓ The
  case v1 was blind to.
- **Exclusive device now busy on next launch**: resilient init catches the `SystemExit` → falls back to
  shared for that key + toast; app starts normally. ✓
- **Missing SOFA / renamed HRIR path**: per-key validation at load drops it → default HRIR + toast. ✓
- **Device unplugged at startup** (the socratic/failure-mode misattribution): a `catchable` open failure
  ⇒ fall back + toast; it does **not** wipe `settings.json` (no crash-attribution logic exists to
  misfire). ✓
- **Native hang on exclusive open**: the subprocess probe times out → value not adopted. Kernel
  bugcheck: out of scope (documented). 
- **Corrupt settings.json / marker**: settings.json → absent → defaults; marker → suspect → defaults
  this once. ✓
- **Two instances**: lock file; second instance refuses rather than racing. ✓
- **Power loss mid-write**: fsync + atomic rename → either old or new file, never garbage. ✓

## Alternatives considered

1. **v1's three-tier crash-recovery state machine (DEFAULTS / known_good / pending + marker +
   fail_count escalation).** Rejected: the consensus attack showed it solves a problem that mostly
   doesn't exist (settings don't crash the process; they're caught/self-heal/exit-cleanly) while being
   **blind to the one that does** (live degrade-but-alive). It also required a launcher crash-relaunch
   loop that would *introduce* the crash-loop hazard it set out to bound. Its only sound parts (atomic
   writes, fsync, single-instance, per-key validation, "reset doesn't touch calibration", host-profile
   vs user-settings precedence) are kept here in a far smaller form.
2. **Modal "Keep / Revert?" dialog (display-resolution style).** Rejected as primary: a live-degrade
   doesn't block the UI, and a native hang can't answer a dialog. The `live-reversible` health-watch is
   the non-modal equivalent; a toast explains risky applies.
3. **No persistence, flags only (status quo).** Fails G1.
4. **Validate only, no probe, no live-watch.** Fails C-LIVE (block xrun) and C-NATIVE (exclusive hang).

## Open questions

- Health-watch window + thresholds for `live-reversible` auto-revert — reuse the exact xrun/fifo/
  `cb_error` signals already in the loop; pick a window (a few seconds) and an xrun-rate threshold.
- Auto-save vs explicit Save (debounce interval), and whether a live change persists immediately or
  only after the health-watch window passes (lean: persist after it passes, so a reverted change isn't
  saved).
- Exact key-descriptor failure-class for each setting (draft table above; confirm `camera format`
  really needs `native-probe` or is fully covered by OpenCV clamping — if clamping always succeeds, it
  drops to `catchable`, shrinking the probe surface further).

---

## Critic feedback incorporated

Round 1 — 5 critics (panel N=5, within cap): `failure-mode-analyst`, `boundary-critic`,
`design-minimalist`, `socratic-challenger`, `state-machine-verifier`. **Converged after round 1: the
consensus attack falsified v1's core premise from the source**, so the correct response was to
restructure, not iterate (per the workflow).

- **Launcher premise false** (failure-mode, verified in source; boundary; state-machine): `run.cmd`
  relaunches only on exit 97; config faults exit 1 → `pause`. → v1's "automatic recovery via
  auto-relaunch" deleted; v2 adds **no** relaunch loop (G4).
- **`promote` cleared the marker → post-promotion faults invisible → unbounded** (state-machine C1):
  dissolved — v2 has no promote/marker-at-startup recovery machine.
- **Apply/Save promoted unproven configs; `pending` never loaded; auto-revert re-poisoned known_good**
  (state-machine C2/C3, failure-mode P0-3/P0-4, boundary): dissolved by dropping the tier machine.
- **"Crash" usually means a *device vanished*, not a bad setting** (socratic Q1, failure-mode P0-2):
  v2 never attributes a fault to the config; device-open failures fall back per-key + toast and leave
  `settings.json` intact.
- **"Stable" must mean "audio actually rendered", and must cover camera** (socratic Q3/Q4,
  state-machine m6): folded into the `live-reversible` health-watch; camera handled by clamping/probe
  rather than an audio-only stability gate.
- **Over-built machinery; cut persisted `pending`, the fail_count ladder, version/applied_at/safe_mode
  record, two-instance note-as-assumption** (design-minimalist): adopted — v2 is one `settings.json` +
  one `starting` marker + a descriptor table.
- **Config precedence (host-profile vs user settings); reset scope (not calibration); fsync;
  single-instance lock; banner on the common revert** (boundary, socratic Q2/Q10, state-machine
  C5/P1-6): all incorporated.
- **Quarantine oversold (no memory of the bad value)** (socratic Q7): dissolved — v2 *prevents* the bad
  value from applying, so it can't loop; a rejected value is simply not adopted and is toasted.

_Invocation-log entries:_
- `design-experimenter` (post-round-1 empirical check): **satisfied by the consensus attack**, which
  read `voicemeeter_passthrough.py` + `host/run.cmd` and classified each setting's actual failure mode
  against source — the load-bearing empirical claim ("bad setting ⇒ crash") was falsified there, so no
  separate probe was needed.
- `general-purpose 2026-10-24: consensus-attack — shared blind spot found` (did NOT survive). The whole
  panel inherited from the BIOS analogy the premise that a bad setting crashes the process and must be
  recovered at next boot. The source shows otherwise (backend→CPU fallback; live block→xrun-not-crash;
  camera clamps/self-heals; init faults exit cleanly; callbacks swallow BaseException). The converged
  "add a crash-relaunch loop" fix would have manufactured the crash-loop hazard the RFC exists to
  prevent. → **Incorporated (one revision):** v2 reframes to **apply-time validation + prevention**
  (catch / live-watch-revert / subprocess-probe) with simple atomic persistence and a one-step DEFAULTS
  backstop; the three-tier crash-recovery machine is moved to rejected-alternative #1. Triaged once; no
  further rounds (the attack is a final input, not a gate).

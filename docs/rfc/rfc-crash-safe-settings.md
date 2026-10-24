# RFC: Crash-safe settings persistence (save / load / reset, BIOS-style fail-safe recovery)

**Status:** Draft
**Date:** 2026-10-24
**Author:** Jean Ibarz (with Claude)

---

## Problem

The cockpit now exposes live settings — camera (device / resolution / fps), engine (method,
backend), audio output (block size, cushion, exclusive device), HRIR filter + compensation,
headphone EQ. Today they are ephemeral: set from launch flags, tweakable live, but **not saved**.
The owner wants **save / load / reset-to-defaults**.

But the real requirement is stronger and is the crux of this RFC: **a bad saved setting must never
be able to brick the app.** Several settings can crash or hang the engine on apply or on the next
launch — e.g. WASAPI **exclusive device** that the hardware refuses, a **block size** that overloads
the render worker into a permanent xrun storm, a **GPU backend** that faults, a camera format the
driver can't deliver. If such a setting is persisted and reloaded on startup, the app enters a
**crash loop**, and the only escape today would be for the user to know the settings file exists,
find it, and delete it. That is unacceptable.

This is exactly the problem a **PC BIOS** solves: persistent configuration **plus** a guarantee that
no configuration a user enters can permanently prevent the machine from booting — via **fail-safe
defaults**, a **boot-failure counter**, and **last-known-good** fallback. The same shape appears in
**display mode-setting** ("keep these settings? reverting in 15 s") where the *inability to confirm*
is the signal to revert ([game display-settings PR with 15 s auto-rollback + atomic versioned
writes](https://github.com/12Mosch/factory/pull/290); [Windows custom-resolution Test
countdown](https://us.ktcplay.com/blogs/support-tips/safe-mode-fix-monitor-unsupported-resolution)),
and in network gear ([FortiGate config-confirmation revert
timeout](https://infosecmonkey.com/configuration-confirmation-on-fortigate/)). BIOS vendors reportedly
revert after a few failed boots into a stripped-down safe config
([overview](https://www.artificialignorance.net/2026/08/system-posted-in-safe-mode.html)); Windows
has the OS-level [Last Known Good
Configuration](https://www.diskinternals.com/partition-recovery/last-known-good-configuration-on-windows10/).

## Goals

- **G1** — Persist settings; **load** them on startup; **reset to built-in defaults** from inside
  the app (one click), never by editing/deleting a file.
- **G2 — Secure by design: the app can never be bricked by its own settings.** Worst case it starts
  on built-in defaults. No file knowledge is ever required to recover.
- **G3** — A setting that crashes the app on apply or startup is **auto-quarantined** after a single
  failed run; recovery is automatic (the user does nothing).
- **G4** — **Crash loops are bounded:** repeated failures escalate the fallback (pending → last-known
  -good → built-in defaults) so the app is always usable within a small, fixed number of restarts.
- **G5** — Persistence is **atomic and corruption-resistant**; a crash mid-write or a mangled file
  never wedges startup.

## Non-goals

- Multiple named presets / profiles (future; this RFC is the single active config + its safety net).
- Cloud sync or sharing config between machines.
- Recovering from **code** bugs unrelated to settings. If the built-in defaults crash, that is a bug
  to surface loudly, not a config to recover from (the safety net bottoms out at defaults and stops).

## Design — three config tiers + a commit-on-stability protocol

Model the BIOS directly. **Three tiers**, in increasing trustworthiness:

1. **DEFAULTS** — built into the code (today's launch defaults). Immutable, always available, cannot
   be "bad". The ultimate fallback = BIOS *Load Fail-Safe Defaults*.
2. **`known_good`** — the last config **proven to run stably**. Persisted at
   `cockpit_config/known_good.json`. = BIOS *last-known-good*.
3. **`pending`** — a config **being tried**, not yet proven. Persisted at
   `cockpit_config/pending.json`, guarded by a **`boot_state`** marker holding `{trying: bool,
   applied_at, fail_count, safe_mode}`.

### The state machine (crash = the revert trigger)

**On startup** (`ConfigStore.on_startup() -> effective_config`):
1. Read `boot_state`.
2. **If `trying` is still set from the previous run** — the app applied `pending` and never reached
   "stable" before it exited ⇒ `pending` is **suspect**. `fail_count += 1` and:
   - `fail_count == 1` → load **`known_good`** (revert the suspect change). *One bad setting, reverted.*
   - `fail_count >= 2` → **`known_good` itself is implicated** → load **DEFAULTS**, set `safe_mode`
     (the UI shows a "started in safe mode — your settings were reset to defaults after repeated
     failures" banner). *Crash loop stopped.*
3. **If `trying` is clear** (last run exited cleanly) → load **`known_good`** (or DEFAULTS if none).
4. **Write `trying = true` + the chosen config + `applied_at` to `boot_state` BEFORE applying it**, so
   a crash *during* apply/startup is caught on the next launch.

**At runtime:**
- **A settings change** updates `pending`, persists it (atomic), and **resets the stability timer**
  (the config is "unproven" again). `trying` stays set.
- **Promotion** (`mark_stable`): once the app has run **stably for `T` seconds** (default 10 s) since
  the last change — audio flowing, no fatal error, no sustained xrun storm — **promote `pending` →
  `known_good`** (atomic), **clear `trying`**, reset `fail_count`. This is "boot succeeded ⇒ commit
  last-known-good". After this, the current settings are safe even across a later hard crash.
- The user may also **Save** explicitly (immediate promotion) and **Reset to defaults** (load
  DEFAULTS, persist as the new `pending`, let it promote). Auto-promotion means **Save is rarely
  needed** — the user just tweaks and it sticks once it's proven.

**On clean shutdown / reload** (`mark_clean_exit`): clear `trying`. Crucially, the launcher's
**exit-97 Reload is a clean exit** — clear `trying` before returning 97 so a reload is never counted
as a crash.

### Why this is secure by design (maps to the goals)

- **G2/G3** — a setting that crashes on apply/startup leaves `trying` set ⇒ next launch reverts it
  with zero user action.
- **G4** — the fallback **strictly escalates** (pending → known_good → DEFAULTS) and DEFAULTS are
  in-code and cannot be bad, so the app is usable within **≤ 2 restarts**, always. The launcher's
  auto-relaunch (exit 97) becomes the recovery mechanism instead of a crash-loop amplifier.
- **G1** — reset is an in-app button; recovery is automatic. A `--safe` launch flag / `COCKPIT_SAFE=1`
  env var forces DEFAULTS as a deliberate escape hatch (the "hold a key at boot" equivalent), for the
  rare case the user wants to force-reset without reaching the UI.

### Crash detection mechanics

- **"Crash" ≡ the `trying` marker is still present at the next startup.** This catches hard crashes,
  **hangs** (the user force-kills), power loss, and a kill during apply — everything that matters,
  without needing signal handlers to be reliable.
- Distinguish **"crashed within the stability window"** (suspect the pending change) from **"ran fine,
  then exited"** (clean, `trying` already cleared). The stability window uses a **monotonic** clock,
  not wall-clock, so clock skew can't mis-promote.
- A setting that is fine on apply but faults **later** (after promotion) → it is now in `known_good`,
  so the next startup escalates known_good → DEFAULTS (one extra restart). `T` trades confidence
  before promotion against how long good settings take to stick; 10 s covers apply-time + first-audio
  faults, which is where almost all setting-induced crashes land.

### Atomic + corruption-resistant persistence (G5)

- Every write is **`*.tmp` + `os.replace`** (atomic rename) — a crash mid-write never corrupts a tier.
- Each file carries a schema **`version`** and is **validated on load**; unparseable or invalid ⇒
  treated as **absent** ⇒ fall through to the next tier. Unknown keys are ignored (forward-compat);
  missing keys fall back **per-key** to DEFAULTS (so adding a setting later doesn't invalidate a saved
  config).
- `known_good` and `pending` are **separate files** so a bad `pending` can never overwrite the proven
  `known_good`.

### Optional affordance for known-risky settings

For a setting that is *expected* to sometimes fail hard (exclusive device), show a transient toast
on apply — "trying exclusive output… will revert automatically if it fails" — so the auto-revert is
*explained* rather than surprising. This is the display-resolution UX, but the **crash itself is the
revert trigger**, so no blocking "Keep/Revert?" modal is needed (you can't answer a modal if the
setting already froze the app).

## Files to change

- **`config_store.py`** (new): `ConfigStore` owning the three tiers + the marker. API:
  `on_startup() -> dict`, `save_pending(cfg)`, `promote()`, `reset_to_defaults()`,
  `mark_stable()`, `mark_clean_exit()`, plus atomic read/write + per-key validation against a
  `DEFAULTS` schema.
- **`voicemeeter_passthrough.py`**: on startup, derive the effective config via `ConfigStore` (instead
  of raw flags); route each settings-hook change through `save_pending`; a **stability timer** in the
  capture loop calls `mark_stable` after `T` s of no change + healthy audio; clean exit / exit-97 call
  `mark_clean_exit`.
- **Web API (`cockpit_web.py`)**: wire `settings_apply` → `promote`; `reset_defaults` →
  `reset_to_defaults` + reload; broadcast a `safe_mode` banner when set. (`save`/`reset` already exist
  as inert dispatch names — this gives them behavior.)
- **Settings dialog**: "Apply" = save/promote now, "Reset to defaults" works, optional "running in
  safe mode" banner.
- Config dir: `cockpit_config/` next to the host assets (discoverable, not buried).

## Edge cases

- **First run** (no files): DEFAULTS; write `known_good` after the first stable window.
- **Corrupt `known_good`**: fall to DEFAULTS (and overwrite once stable).
- **Hang (not crash)**: user force-kills → `trying` still set → treated as a failed boot. ✓
- **Exit-97 reload**: clear `trying` first → not a failure. ✓ (Load-bearing: miss this and every
  reload looks like a crash and resets the user's settings.)
- **Power loss during the stability window**: counted as one failure → revert to known_good. Mildly
  annoying (loses an unproven tweak), never unsafe.
- **Two instances**: single-instance assumption; lock the config dir, or last-writer-wins on promote.
- **A setting that only crashes intermittently**: may promote then crash later → escalates on the
  next boot. Bounded, still recovers; documented as the known residual.

## Alternatives considered

1. **Plain settings file + "delete it to reset."** The exact failure mode the owner wants gone.
   Rejected.
2. **Modal "Keep / Revert?" dialog as the primary mechanism** (display-resolution style). Rejected as
   primary: a setting that freezes the app also freezes the dialog, so no one can answer it. Kept only
   as an *optional explanatory toast* for known-risky settings.
3. **Dual full copies ("Dual BIOS").** Our three-tier scheme is the generalization: DEFAULTS live in
   code, so we need only two persisted files (`known_good`, `pending`) instead of two full backups.
4. **Trust a clean SIGTERM/atexit handler to mark success.** Rejected as the sole signal: hard
   crashes and hangs skip handlers. "Marker still set at next startup = failure" is robust to both.

## Open questions

- `T` (stability window) and `N` (fail_count → defaults): proposed 10 s and 2. Tune against how fast
  real setting-induced faults surface (mostly < 2 s at apply / first audio).
- Should per-setting "risk" be modeled (e.g. only exclusive-device gets the explanatory toast), or
  treat all changes uniformly? Start uniform; add risk tags if needed.
- Where exactly to detect "healthy audio" for `mark_stable` (no fatal callback error + fifo not
  permanently starved) — reuse the existing xrun/health signals.

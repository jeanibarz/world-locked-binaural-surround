# 88 — Engine disables console QuickEdit Mode at startup

## Question and context

A live debugging session reported the cockpit as "broken since the latest PR": the output-level
meter stuck on "Waiting for audio levels…", no sound, and the output-gain slider refusing to
confirm a change ("check the connection"). Inspection via Claude-in-Chrome showed the engine was
actually *alive* — `/api/get_config` returned 200 and `/events` carried live pose frames — and the
operator then realised the real cause: a stray **click in the engine's terminal** had frozen it.

That is Windows console **QuickEdit Mode**. When the engine runs directly in a console (the dev /
"run the exe in a terminal" workflow, as opposed to the hidden `CREATE_NO_WINDOW` launch the Tauri
app and `start-hidden.vbs` use), clicking or selecting anywhere in the window silently *suspends the
whole process* until a key is pressed. A suspended engine stops servicing audio and telemetry, so
every symptom above follows — and the set is indistinguishable from a code regression, which sent
the diagnosis chasing the most recent PR diff (which the running build did not even contain).

## Hypothesis

The engine can remove the foot-gun for every direct run by clearing the QuickEdit bit on its own
console at startup. It should be best-effort and invisible when there is no console (the hidden
launch, or stdin redirected to a pipe), so it never changes the Tauri path or CI.

## Method and implementation

New Windows-only module `rust-engine/src/win_console.rs` with `disable_quick_edit()`:

- Raw `#[link(name = "kernel32")] extern "system"` FFI (`GetStdHandle` / `GetConsoleMode` /
  `SetConsoleMode`, `isize` handles) — the same minimal-FFI style the crate already uses for
  `SetPriorityClass` in `rust-audio-io` (no new dependency, no `windows` crate).
- Reads the current console input mode, clears `ENABLE_QUICK_EDIT_MODE` and sets
  `ENABLE_EXTENDED_FLAGS` (required for the QuickEdit change to take effect) while preserving every
  other bit, and writes it back. `GetConsoleMode` fails when stdin is not a console, so the hidden
  launch / a pipe returns `false` and nothing is touched.

Wired in `main.rs`: `#[cfg(windows)] mod win_console;`, called as the first line of `engine::run()`
(before any thread is spawned, so the window is protected from the first moment), printing
`CONSOLE_QUICK_EDIT disabled|unavailable` alongside the existing `PROCESS_PRIORITY` / `ENGINE_BUILD`
startup lines for diagnosability.

## Result and verification

- Native suite green: `cargo test --manifest-path rust-engine/Cargo.toml` → **103 passed**.
- Windows engine cross-compiles clean: `cargo xwin check --target x86_64-pc-windows-msvc` (the FFI
  module and its call site type-check on the real target).
- No behavioural change on the hidden Tauri launch (no console → `unavailable`, no-op).

## Interpretation

This closes a genuine operability gap: the single most confusing failure mode of running the engine
in a terminal was a self-inflicted freeze that mimicked a build regression. Disabling QuickEdit at
the source protects every current and future direct run, independent of how the user launches it,
with zero cost to the packaged (hidden) path.

## Learnings

- Before bisecting a "broken since PR X" report, confirm the backend is live and *which build is
  running* (`/api/get_config` `version.short`, re-probe `/events`): a suspended process looks exactly
  like a code regression, and the running build may predate the suspect PR entirely.
- A console that owns the process can suspend it (QuickEdit); a long-running foreground service with
  a console should clear that bit at startup.

## Decision / next

Shipped as an operability fix. No follow-up required. (Separately noted during diagnosis: the
operator's running engine was build `ad7def0`, which predates the configurable-max-gain PR #254 —
a rebuild is needed to pick up both that feature and this fix.)

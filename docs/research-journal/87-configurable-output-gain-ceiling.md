# 87 — Configurable output-gain ceiling with clip-on-lower

## Question and context

Journal [85](85-output-gain-and-meters.md) added a shared stereo trim after the EQ chain,
fixed at −24…+24 dB. For an unusually quiet source, +24 dB of make-up gain is not always
enough headroom to reach a usable level. The question: let the user raise the boost ceiling
without weakening the safety story that keeps a careless setting from destroying hearing or
the waveform.

## Hypothesis

Only the positive headroom needs to be configurable — attenuation is always safe, so the
−24 dB floor can stay fixed. A single user setting ("Max output gain") that raises the
ceiling, bounded by a hard upper limit, covers the quiet-source case. When the ceiling is
lowered below the live gain, the gain should follow it down (so the setting can't leave an
inaudibly-loud value stranded above a new, lower ceiling), while a gain already in range is
untouched.

## Method and implementation

Core, in `rust-engine/src/output_gain.rs` (the portable, natively-tested module; the engine
is `cfg(windows)`):

- `GAIN_FLOOR_DB = -24`, `DEFAULT_MAX_GAIN_DB = +24` (the historical fixed max, kept as the
  default so existing installs are unchanged), and the configurable ceiling itself is bounded
  to `[MIN_MAX_GAIN_DB = 0, MAX_MAX_GAIN_DB = 60]` dB (1000× — a hearing-safety / finite-overflow
  guard).
- `sanitize_max_gain_db` clamps a ceiling request to that span and maps non-finite → default
  (a NaN ceiling would otherwise reject every gain and silence the trim).
- `valid_gain_db(gain, max)` now takes the ceiling; `clamp_gain_db(gain, max)` clips a gain
  into `[floor, max]` (used when the ceiling moves).
- `GainStage` holds a live `max_gain_db`; `set_max_gain_db` sanitises the request, and when the
  current gain exceeds the new ceiling clips it down **through the existing 10 ms ramp** (a
  smooth drop, not a step), returning the resulting gain so a caller can follow it.

Engine wiring (`main.rs`): `max_gain_db` is a persisted setting (backward-compatible overlay —
an absent key keeps the +24 default; a persisted gain is re-clamped to the persisted ceiling for
consistency), a new `set_max_gain` control sanitises the ceiling and clips the gain in one move,
`set_output_gain` validates against the live ceiling, and the ceiling is applied to the live
`GainStage` before the gain. The ceiling and its bounds ship in `/api/get_config`
(`max_gain_db`, `max_gain_min_db`, `max_gain_limit_db`) and the live ceiling in SSE
(`output_level.max_gain_db`), so the cockpit slider retunes its own `max` live. The settings
dialog gains a 6 dB stepper over the engine-reported span.

## Result

9 new `output_gain` unit tests (sanitise/validate/clamp helpers; default parity with the old
fixed range; raising admits a previously-rejected boost; lowering clips a too-loud gain, spares
a lower one, and the boundary gain==max case; clip-down ramps to the new linear factor;
out-of-span & non-finite requests; re-widening does not restore a clipped gain) plus telemetry
wire-frame coverage for a non-default ceiling. Full native suite **103 passed**; the Windows
engine type-checks clean via `cargo xwin check --target x86_64-pc-windows-msvc`. PR #254.

## Interpretation

Making only the ceiling configurable (not the floor) keeps the change small and the safety
argument intact: attenuation is unrestricted, boost has a hard cap, and a lowered cap can never
strand a dangerously-loud gain. Reusing the existing ramp for the clip-down means the louder-than-
intended case resolves smoothly rather than as an audible jump.

## Learnings

- A one-sided, hard-capped, sanitised ceiling is enough to open up make-up gain without a new
  limiter or any change to the floor/attenuation path.
- Persist the ceiling *and* re-clamp the persisted gain to it on load — the two values must stay
  consistent even if a settings file is hand-edited.

## Decision / next

Shipped as an enhancement to the output-gain control (#221 / journal 85). No follow-up required;
the +60 dB hard cap can be revisited if a real source ever needs more.

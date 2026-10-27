# 86 — Stale-doc audit: journal index reconciled, runbooks + latency claims corrected

**Date:** 2026-10-27 · **Block:** maintenance (cf. journal 29, B8.4) · **Milestone:** cross-cutting ·
**Status:** done — docs-only; no code or behaviour changed.

## Question

Parallel feature branches each append a journal entry and merge, but nothing had reconciled the
index afterward. How far had the canonical docs drifted from the shipped reality, and what else was
stale?

## Method

A read-only fan-out audit (three scoped scans + spot verification against the journal and the
working tree): `docs/backlog.md`, `README.md` / `docs/requirements.md` / `AGENTS.md`, and
`docs/rfc/` + `docs/design/` + repo-root clutter. Every finding was re-checked against quoted
source before any edit; contested latency figures were left to the figures the project's own later
corrections endorse rather than re-asserted.

## Result — what was stale, and the fix

- **Journal index (`research-journal/README.md`).** The "journal, in order" table listed 00–50 then
  jumped to 82–85 — entries **48 (queue-term), 51–81** (~32 entries, almost all M8 / Track B) were
  present as files but unlisted, and the table was fragmented into broken one-row tables by stray
  blank lines. Rebuilt into one continuous table covering all 89 files, with one-liners drawn from
  each entry's own title + status.
- **Duplicate entry numbers.** Entries **48, 64, 81** each exist twice (parallel-branch collisions:
  e.g. `64-host-install-passes-no-bugcheck` vs `64-single-host-entrypoint`). Not renumbered — that
  would rewrite the append-only record and break inbound links — but both are now listed, each with
  a note. **Open for the owner:** decide whether to renumber or leave as-is.
- **Milestone "Status at a glance".** It stopped at ~journal 47. Added the shipped-but-missing work:
  M6 — the Dear PyGui **cockpit** (B6.12, journals 49–50) + output-gain control (journal 85); M8 —
  the **Track B** custom pull-driven SYSVAD driver (B-pre PASS → driver loads on the host → ring
  bridge carries real audio VoiceMeeter-free; journals 59/68–69/80). No existing markers re-judged.
- **AGENTS.md / host/README.md runbooks.** Referenced a launcher **zoo** (`run_binaural.cmd`,
  `run_passthrough.cmd`, `launch_live.py`, `launch_passthrough.py`) and `latency_sweep.py` /
  `latency_bench.py` plus `calibrate` / `latency` modes — all removed when the host was consolidated
  to one `run.cmd` → `apps/launch.py` (journal 64) and the standalone latency harness was retired
  (`refactor: remove the calibrate and latency modes`). Rewrote to the single entrypoint + the live
  latency probe. Also fixed stale `apps/calibration_gui.py` references in `devices.py` / `radar.py`
  docstrings and a `run_passthrough.cmd` comment in `voicemeeter_passthrough.py`.
- **Latency claims (README, requirements).** Both still cited journal 26's "~70–110 ms, above
  threshold" as current, pre-dating the device-buffer fix and prediction. Updated to the measured
  path (MME→WASAPI device-buffer cut, journals 42–44; prediction cancelling camera staleness,
  journal 47) without re-asserting a single contested number.
- **backlog.md.** Marked **B8.5b / B8.5c** `[x]` (their "Remaining" work landed via B0.5 / journal
  44); corrected **B0.5**'s "the launcher uses it" (the launcher was reverted to shared in journal
  45); rejoined B8.5's description sentence, which had been split across the B8.5b/B8.5c blocks.
- **RFC status.** `rfc-single-clock-virtual-driver.md` still said "Track B gated on B-pre"; B-pre has
  passed and Track B is validated end-to-end on the host (journals 59/80) — status updated.
- **Repo root.** Removed six orphaned scratch mockups (`output-*-mockups.html`, zero references;
  design mockups live under `docs/design/ui-redesign/`).

## Learnings / decision

The index drifts whenever parallel branches merge without a reconciliation pass; the duplicate
numbers are the sharper symptom. A periodic stale-audit (this entry, like 29) catches it, but a
lighter guard — a merge-time check that every `NN-*.md` is listed exactly once — would stop the
drift at the source. Left as a suggestion for the owner; no code added here.

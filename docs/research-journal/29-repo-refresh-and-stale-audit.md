# 29 — Repo refresh: stale-knowledge audit, prune, and the Windows deploy runbook (B8.4)

**Date:** 2026-10-18  ·  **Milestone:** maintenance  ·  **Status:** done

## Question

After the Rust kernel (B8.2) and cross-platform wheels (B8.3), the owner asked to (1) capture
the current **build-and-deploy procedure for the Windows host** durably, and (2) **audit the
whole repo for stale knowledge** — docs, comments, scripts, dead code — and prune what no longer
helps.

## Method

Two read-only audit passes (one over `src`/`apps`/`tests`/`rust`, one over `docs`/spikes/the
host launcher dir), each required to grep for real references before calling anything dead.

## Result — what changed

- **Corrected the pervasive "the CPU can't keep up → the GPU is required" claim**, now false
  since B8.2 (Rust kernel, ~5× RT on CPU). Reworded in `gpu_render.py`, `voicemeeter_passthrough`
  docstring + runnable example (now shows `--rust`), `__init__.py`, `pyproject.toml` `gpu` extra,
  `AGENTS.md` "GPU compute" lead, and a "superseded by B8.2" aside on the backlog's B4.1 note.
  The GPU is presented everywhere as an **optional** alternative to `--rust`.
- **`README.md` status** "Early scaffolding" → an honest summary (working end-to-end on Windows;
  CPU real-time via the Rust kernel; what's still open).
- **`docs/virtual-device-setup.md`**: noted the live passthrough captures via **WASAPI loopback**
  on the render endpoint, not the VoiceMeeter output bus (that section is only the latency spike).
- **Added a Windows build-and-deploy runbook** to AGENTS.md (launcher chain, build/install the
  abi3 wheel, run on `--rust`; code changes need no redeploy, only kernel changes rebuild).
- **Pruned dead host-debug artifacts** from `C:\…\BinauralHeadtracker\`: `duplex_diag.py` (pointed
  at a deleted `-b4.1` worktree, broken), `chan_probe.py`, `diag_out.wav` — all one-off B4.1
  diagnostics, superseded. Host launcher `run_passthrough.cmd` switched `--gpu`→`--rust --block
  256`; `run.cmd` menu label fixed.

## Decision — what was deliberately KEPT (so it is not re-flagged as "dead")

A code-only view flags the **generic-SOFA / HRIR / direction-interpolation cluster**
(`hrir.py` B1.1, `interpolate.py` B1.2, `render.py` B1.6, `baked_interp.py` B1.7/B1.10) as
orphaned — the *live* apps use the baked-BRIR path (`surround` + `live_render`) and don't import
it. **It is retained on purpose:** it is completed, tested M1 foundation for the project's
headline differentiator — an *individual / AI-fine-tuned HRIR* set (`docs/hrir-setup.md` documents
`hrir.load_sofa`; the open **B1.8** builds on `interpolate`; **M9/B9.1** is HRIR fine-tuning). Do
not delete it as "dead code" without retiring those roadmap items first. Likewise
`judge_registry.py` is the intentional **M7** (AI-judge) cross-project seam, and `audio_spike.py`
backs the B0.2/B0.3 spikes — both kept. The research journal and all six spikes stay untouched
(append-only record; every spike is doc/skill/module-referenced).

## Next

Nothing opened; this was maintenance. The accurate frontier remains the journal README status
table and `docs/backlog.md`.

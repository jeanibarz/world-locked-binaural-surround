# 01 — Python prototype: stack, project layout, toolchain (B0.1)

**Date:** 2026-10-15  ·  **Milestone:** M0  ·  **Status:** done

## Question

What language/runtime, project layout, and tooling do we commit to for the
prototype, and can we stand up a reproducible, lint-clean, green test harness that
every later block extends?

## Context

Requirements § 5 mandates "prototype the render engine + tracker + world-lock math
in **Python** (fast iteration), then move the hot audio path to native." The
sibling project `binaural-localization` already runs a Python stack managed by
`uv` with a `src/` layout and `pytest`. Reusing its conventions keeps the two
repos ergonomically identical for a single maintainer and lets calibration blocks
(M7) share tooling without friction. This block only lays the foundation — no
audio, vision, or DSP yet.

## Hypothesis

A `uv`-managed project (setuptools backend, `src/` layout, `pytest`, `ruff`) with
a minimal `numpy`/`scipy` baseline will sync, lint, and test green on this machine
and on CI, and mirrors the sibling closely enough to be maintenance-free.

## Method

- `git worktree add ../binaural-headtracker-b0.1-pyproject -b b0.1-python-project-setup origin/main`.
- Wrote `pyproject.toml`: `requires-python = ">=3.10,<3.13"`, deps `numpy`,
  `scipy`; dev extras `pytest`, `ruff`; `[tool.ruff.lint] select = E,W,F,I,UP,B`;
  pytest `pythonpath=["src"]`, `testpaths=["tests"]`.
- Created `src/binaural_headtracker/__init__.py` (`__version__`) and
  `tests/test_smoke.py` (import + version shape; a one-line `scipy.fftconvolve`
  sanity check — the operation at the heart of M1).
- Added `.ruff_cache/` to `.gitignore`; kept `uv.lock` **tracked** (reproducible
  app builds, as the sibling does).
- `uv sync --extra dev` → `uv run ruff check .` → `uv run ruff format --check .`
  → `uv run pytest -q`.

## Result

All green. `uv` resolved 17 packages and selected **CPython 3.12.8** for the venv.
Installed: `numpy 2.5.4`, `scipy 1.18.1`, `pytest 9.1.1`, `ruff 0.17.1` (+ trans:
iniconfig, packaging, pluggy, pygments). `ruff check` → "All checks passed!";
`ruff format --check` → 11 files already formatted; `pytest` → **2 passed**.

## Interpretation

The sibling's conventions transfer cleanly; nothing fought the `src/` + `uv`
setup. Keeping heavy platform deps (sounddevice, mediapipe, sofar) out of the
baseline means `uv sync` stays fast and portable on headless CI/WSL where no
audio device or camera exists — those deps join at the spike block that first
needs them. The `<3.13` cap is inherited from the sibling (TensorFlow ceiling);
not strictly required here, but harmless and keeps the two environments aligned.

## Learnings

- `uv` + setuptools + `src/` layout + `pytest` + `ruff` is the committed prototype
  stack; `uv.lock` is tracked for reproducibility.
- Add platform-specific audio/vision deps per-spike, not in the baseline, to keep
  CI/WSL green.

## Dependency licences (§ 9 compliance — all permissive)

| Package | Licence |
|---|---|
| numpy | BSD-3-Clause |
| scipy | BSD-3-Clause |
| pytest | MIT |
| ruff | MIT |
| uv (tool) | MIT / Apache-2.0 |

No GPL. No `LICENSE` file / `license` field added — the repo stays all-rights-
reserved pending the donationware decision (requirements § 9); a comment in
`pyproject.toml` records this so a later block does not "helpfully" add one.

## Decision / next

B0.1 done. Next eligible block is **B0.2** (Windows audio-I/O spike: enumerate
devices, open low-latency WASAPI stereo out + multichannel in, measure round-trip
latency) — note it needs a real Windows audio environment, not this WSL box, so it
may record a platform blocker. B1.1 (SOFA HRIR loader) is a camera/audio-free
alternative that can proceed anywhere if B0.2 is blocked here.

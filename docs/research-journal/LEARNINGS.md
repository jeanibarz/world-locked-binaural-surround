# Cumulative learnings

Durable, compounding lessons, newest appended at the end, each tagged with the
journal entry it came from. This is the quick reference; the entries carry the
full story.

1. **The core mechanic is world-lock: fix the virtual speakers in the world, move
   the convolution with the head.** Head rotation must change the per-speaker
   direction (and thus the HRIR), not the scene. Every component (virtual device,
   EQ, GUI, AI calibration) serves that single perceptual idea.
   (→ [00](00-problem-and-objective.md))
2. **Committed prototype stack:** `uv` (lockfile tracked) + setuptools + `src/`
   layout + `pytest` + `ruff` (lint `E,W,F,I,UP,B`), Python `>=3.10,<3.13`,
   baseline deps `numpy`/`scipy` only. Mirrors the sibling `binaural-localization`
   for zero-friction maintenance across both repos. (→ [01](01-python-project-setup.md))
3. **Keep platform-specific deps out of the baseline.** Audio (sounddevice) and
   vision (mediapipe) deps join at the spike block that first needs them, each
   logged with its licence, so `uv sync`/CI stay fast and green on headless WSL.
   (→ [01](01-python-project-setup.md))
4. **No open-source licence yet** (donationware decision pending, requirements
   § 9): no `LICENSE`/`license` field; a `pyproject.toml` comment guards against a
   later block adding one. All deps so far are permissive (BSD/MIT/Apache), no GPL.
   (→ [01](01-python-project-setup.md))

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
5. **The dev box is WSL2, but the Windows host is reachable via interop.**
   `py.exe -3.12` runs a native Windows Python (with `sounddevice`/WASAPI and the
   webcam); run repo scripts by their `wslpath -w` path. So Windows-only spikes
   (audio, camera) are runnable from this box — never record them "blocked on WSL".
   Keep portable logic in the package (unit-tested on WSL), hardware I/O in
   `spikes/`. (→ [02](02-audio-io-spike.md))
6. **WASAPI reality on this host:** ~22 ms reported latency (shared) on the
   default output, which is a **VoiceMeeter virtual sink** — exclusive mode is no
   better and the bare hardware DAC can't be opened directly (VoiceMeeter owns
   it). The <30 ms target needs the virtual chain bypassed (B4/B8). A *measured*
   round-trip needs a loopback the virtual device must expose (B0.3). Resolve
   device indices as runtime *roles*, never hard-code them. (→ [02](02-audio-io-spike.md))
7. **Language is not locked to Python.** Python-first for iteration, but a block
   may prototype/benchmark a hot path in C/C++/Rust as soon as performance is the
   deciding question — don't defer all native work to M8. Record the language
   choice + its licence in the journal; keep config/HRIR/world-model shared across
   languages. (→ AGENTS.md "Working discipline")
8. **A virtual 7.1 sink on Windows = VoiceMeeter VAIO (8-ch); VB-CABLE is stereo
   only.** Apps select `VoiceMeeter Input (VAIO)` as their 7.1 speakers; the
   renderer captures `VoiceMeeter Output (VAIO)`. Resolve both by name/channels at
   runtime on the **WASAPI** host API only — the same cable also appears under
   MME/DirectSound/WDM-KS with different latency, and **MME truncates device names
   to 31 chars** (`MAXPNAMELEN`), breaking name matching. Pair a cable's two halves
   by their shared trailing parenthetical. (→ [03](03-virtual-device-spike.md))
9. **A VoiceMeeter loopback needs the virtual strip routed to a B bus.** The
   capture endpoint opens and reads frames regardless, but stays silent until the
   strip is assigned to B1 — report that as a config gap, never a spurious 0 ms. A
   dedicated **VB-CABLE** gives a zero-config internal loopback for a clean latency
   reference. The reported bound on the VoiceMeeter chain is ~44 ms (22 out + 22
   in), above the <30 ms target. (→ [03](03-virtual-device-spike.md))
10. **Windows-console spikes: force UTF-8 stdout and keep output ASCII.** The
    default cp1252 codec aborts the whole run on a stray non-ASCII char (`→`); a
    `sys.stdout.reconfigure(encoding="utf-8")` guard plus ASCII-only notes keeps
    the RESULT_JSON line from being lost. (→ [03](03-virtual-device-spike.md))
11. **VoiceMeeter / VB-CABLE are proprietary donationware, not redistributable.**
    Depend on them at runtime only; the user installs them. Bundle nothing, add no
    Python dependency, introduce no GPL — § 9 safe. (→ [03](03-virtual-device-spike.md))

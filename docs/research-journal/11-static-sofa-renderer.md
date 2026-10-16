# 11 — Static SOFA renderer: wiring B1.1–B1.4 together (B1.5)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

Can the four M1 building blocks — the SOFA HRIR loader (B1.1), ITD-aware
direction interpolation (B1.2), the partitioned convolution engine (B1.3), and
the world layout model (B1.4) — be composed into one **static** renderer that
takes a 7.1 PCM stream and produces a binaural stereo downmix for a *fixed*
head, verifiable offline on a known pan? This is the integration block that
turns four tested-in-isolation modules into the first end-to-end render path for
the **generic SOFA** HRIR set.

## Context

B1.6 already renders 7.1 → stereo, but only from the owner's eight **baked
BRIRs** (`surround.render_7p1_to_stereo` + `BakedBRIRSet`): a sparse, fixed set
of room responses with no notion of a continuous direction field. B1.5 is the
parallel path for a **continuous SOFA HRIR set**, which is what the head-tracked
engine (M2/M3) actually needs — the tracker will ask for an HRIR at an arbitrary
`(az, el)` every frame, not one of eight fixed directions.

All four pieces were green in isolation but had never been joined. The join is
small by design: each module already speaks the right vocabulary —
`layout_7p1().head_relative_directions()` returns per-channel `(az, el)` (B1.4),
`HRIRSet.interpolated_irs(az, el)` returns a left/right IR for any direction
(B1.1/B1.2), and the per-channel convolve-and-sum already exists for the baked
path (B1.6) and its streaming twin `StereoConvolver` (B1.3). The only genuinely
new logic is the **direction lookup** that bridges geometry to HRIRs: project
each speaker, fetch its HRIR, stack into a per-channel IR bank.

## Hypothesis

The integration needs no new DSP — only a loop that maps each of the 8 layout
directions to an HRIR and assembles a `(C, N)` left/right bank, after which the
*existing* convolve-and-sum renders it. The offline result should equal the
B1.3 streaming engine's output (they are the same convolution, batch vs
block). Because the join forwards `head_rotation`/`head_position` straight to the
B1.4 projection, the same code becomes the world-locked live renderer at M3 with
no structural change.

## Method

- **Extracted the shared kernel.** Pulled the offline per-channel convolve-and-sum
  out of `surround.render_7p1_to_stereo` into a bank-level free function
  `surround.convolve_and_sum_stereo(pcm, left, right)` taking raw `(C, N)` IR
  banks. `render_7p1_to_stereo` is now a thin wrapper over it (backward
  compatible — the B1.6 tests are untouched and still pass). This keeps the
  baked and SOFA paths from duplicating the convolution loop.
- **New module `binaural_headtracker.render`** (the SOFA path):
  - `SpeakerHRIRBank` — a frozen dataclass holding one left/right IR per output
    channel plus the `(az, el)` each was looked up at (provenance). It is the
    HRIR-path analogue of `BakedBRIRSet`, deliberately the same `(C, N)` shape so
    both feed the same kernels. `.render(pcm)` renders offline; `.streaming_convolver(block)`
    hands the bank to a B1.3 `StereoConvolver` for the live path.
  - `build_speaker_hrirs(hrir_set, layout, *, interpolate=True, k, power,
    head_position, head_rotation)` — the one new piece: project every speaker
    (`layout.head_relative_directions(...)`), look each direction up in the HRIR
    set (ITD-aware interpolated, or nearest when `interpolate=False`), stack into
    a bank. `head_position`/`head_rotation` pass straight through to the B1.4
    projection — the M3 seam.
  - `render_layout_to_stereo(pcm, hrir_set, layout, ...)` — build-then-render
    convenience for a single block.
- **Tests** (`tests/test_render.py`, hermetic): a synthetic `HRIRSet` placed at
  the nominal 7.1 azimuths, with a toy ITD/ILD (left source → left ear earlier &
  louder). Pins: per-speaker IRs reproduce the measured HRIR at each grid point;
  bank directions equal the nominal layout for a fixed head; the ITD/ILD *sign*
  is correct per speaker (L/SL lead left, R/SR/RR lead right, C symmetric); a
  lone impulse on one channel reproduces exactly that speaker's HRIR; offline
  render **equals** the B1.3 `StereoConvolver` stream to 1e-9; nearest and
  interpolated paths agree on grid points; a +30° head yaw swings the front
  speaker to −30° and changes the rendered IRs (the world-lock seam, exercised
  without a tracker).

## Result

B1.5 **done**. `binaural_headtracker.render` renders a generic SOFA set's 7.1 →
binaural stereo for a fixed head, and the full suite is green (**152 passed**,
ruff clean). The headline cross-check held: the offline batch render matches the
block-by-block B1.3 engine to floating error (`atol=1e-9`), so the static path
and the future live path are the same computation — the offline renderer is a
faithful oracle for the streaming one.

The integration confirmed the hypothesis that no new DSP was needed: the whole
block is one direction-lookup loop plus an extract-function refactor. The world
layout's "build the full `Rᵀ(pᵢ−pₕ)` projection now" decision (B1.4, L27) paid
off exactly as intended — the fixed-head renderer became world-lock-ready by
*forwarding two optional arguments*, and the yaw test renders a genuinely
rotated scene with zero tracker code.

One deliberate simplification: **LFE is convolved through its HRIR like any other
channel** (its nominal direction is front, az 0). For a baked set that is what
the owner measured; for a generic HRIR set the LFE is band-limited bass where
direction barely matters, so a per-channel HRIR is harmless and keeps the path
uniform. A dedicated low-pass/diffuse LFE handling is a tuning concern for M5/M9,
not this block.

No negative result: the join behaved as predicted on the first pass. The only
friction was environmental — a fresh `git worktree` venv must be set up with the
documented `uv sync --extra dev` (journal 01); `uv run pytest` *without* it
composes an overlay environment that silently drops the `h5py` baseline dep, so
the three SOFA loader tests fail with a misleading `ModuleNotFoundError`. Running
the documented setup first makes all 152 green. (→ L31)

## Learnings

- Integration blocks that join well-factored modules need almost no new code when
  the modules already share a vocabulary: B1.5 is one direction-lookup loop over
  `layout.head_relative_directions()` → `HRIRSet.interpolated_irs()` → an IR
  bank, because B1.1–B1.4 were each built to the same `(az, el)` / `(C, N)`
  contract. Design the seams and the wiring is a formality. (→ L29)
- Make the offline renderer and the real-time engine the *same* computation and
  assert it in a test (offline batch == B1.3 `StereoConvolver` stream to 1e-9):
  the simple offline path then serves as a ground-truth oracle for the harder-to-
  inspect streaming path, and any future divergence is caught immediately. (→ L30)
- A fresh `git worktree` starts with **no** `.venv`; always run `uv sync --extra
  dev` before `uv run pytest`, or `uv run pytest` builds a transient env that
  drops baseline deps like `h5py` and fails with a misleading
  `ModuleNotFoundError` — a toolchain artifact, not a code regression. Verify
  against a known-good checkout before suspecting the diff. (→ L31)

## Decision / next

B1.5 **done**: the generic-SOFA static renderer exists and is verified offline
against both a known pan and the B1.3 streaming engine. Together with B1.6 (the
baked-BRIR path), M1's "static binaural render engine" is functionally complete —
both HRIR sources now render 7.1 → stereo for a fixed head.

Next eligible block is **B1.7** — head-rotation interpolation over the owner's 8
fixed BRIRs (ITD resynthesis + magnitude blending between the nearest speaker
BRIRs; the owner's "vary ITD + blend" idea), distinct from B1.2's continuous-SOFA
interpolation. After that, M2 (B2.1 tracker interface) opens. No new follow-up
blocks discovered.

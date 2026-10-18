# 34 — In-passthrough experiment layer + a world-frame radar (B6.5)

**Date:** 2026-10-18  ·  **Milestone:** M6 (GUI) / M3  ·  **Status:** done; the daily passthrough can now run A/B experiments without a restart

## Question

The owner runs the **passthrough** daily and wanted to run experiments *inside it* without
restarting: inject a test sound (snare/tick) at any position, rotate it, A/B the interpolation
methods, click the radar to place it. And a radar fix: **keep the speakers fixed and rotate the
head** — more faithful to "world-locked".

## What shipped

**World-frame radar.** `_console.radar` now draws the speakers at their **fixed world azimuths**
and rotates the **head** (centre arrow + gaze cone) to where it faces (`head_deg = −C's
head-relative azimuth`); the cardinals are world directions. The faced speaker is still
highlighted, and an optional **test-source marker** (green, with a dashed pointer) shows where a
placed sound sits in the world. Both GUIs pass `world_az` (the neutral `head_relative_directions`)
+ `head_deg` instead of per-frame head-relative directions. `radar_az_at(x, y)` inverts the
geometry for click-to-place.

**Experiment layer in the passthrough.** A single **movable test source** mixed into the running
duplex (the 7.1 render path is **unchanged when it's off** — everything is behind an `if on`):
- **T** toggles it; **O** solos it (hear the test alone, 7.1 muted); **R** auto-rotates it around
  you (`--rotate-speed`); **[ ]** adjust its level; **click the radar** to place it at a world
  azimuth; **M** cycles its interpolation method (`nearest`/`timealign`/`minphase`/`notchwarp`),
  synthesising the dense grid on first use (B3.3, cached).
- The source is the owner's `--source` WAV or a built-in 1 s **noise tick** (a repeating transient
  that is easy to localise). It is rendered as a single `WorldLockedSource` (so it *can* use the
  dense-grid interpolation even though the 7.1 passthrough stays on the fixed-8 GPU/Rust kernels),
  at the head-relative azimuth `world_az + head_offset`, through the same output-EQ.
- The render worker reads the test state once per batch under the lock and mixes per block; the
  audio callbacks are untouched (L52 preserved). The radar marker + a `test` readout show method /
  position / solo / rotating live.

## Result

308 tests pass, ruff clean. World-frame radar verified by host PNG render (speakers fixed, head
arrow + cone rotated to the gaze, test marker + dashed pointer). The mix/solo path validated
offline on the real BRIR (finite, safe levels). Mouse/keys are host-only (the owner tries live).

## Decision / next

This makes the passthrough the single daily tool that also runs A/B experiments. The 7.1 render
itself still uses `nearest` (the dense-grid A/B is on the *test source*); if an ear test favours a
synthesis method, folding it into the 7.1 path needs a variable bus set in the GPU/Rust kernels.
A natural follow-up: snap-to-speaker placement and recording an A/B clip of a chosen motion.

# 32 — Radar semantics ("facing", not "active") + the Dolby 7.1 layout angles (B6.4)

**Date:** 2026-10-18  ·  **Milestone:** M6 (GUI) / world model  ·  **Status:** done; both are owner-reported and confirmed

## Two owner reports on the radar

### 1. "The active speaker looks inverted"

Turning the head right, the owner heard the **centre** channel move to the **front-left** (correct
world-lock) while the radar highlighted the **front-right** speaker — read as an inversion. It is
not: the two are different quantities, both correct.

- With the layout (`L +30`, `R −30`, `+az = left`), turning right makes world-C's head-relative
  azimuth **+~25° (the listener's left)**, so the renderer steers it through the nearest BRIR —
  **L (front-left)** — and the owner hears centre on the left. ✓ world-lock.
- The speaker **nearest straight-ahead** is then **R (front-right)**, which is what the radar
  highlighted. ✓

The bug was the **label**: "active" reads as "where the sound is", but in the full 7.1 passthrough
**all eight channels play at once** — there is no single active speaker. Renamed the highlight and
its readout to **"facing"** (the speaker you are pointed at), which is unambiguous. No geometry
changed.

### 2. "The surround/rear angles are wrong"

The owner's speakers are at the **Dolby 7.1 reference angles** — side surrounds **±110°**, rear
surrounds **±150°** — not the older ITU minimums (**±90° / ±135°**) the code shipped with.
Corrected `surround.SPEAKER_LAYOUT_7P1` to `SL/SR ±110`, `RL/RR ±150` (front ±30, centre 0
unchanged).

This is **not cosmetic**: `SPEAKER_LAYOUT_7P1` is the single source of the ring directions, so it
drives **both** the radar *and* the world-lock **steering** — `ring_azimuths` decides which BRIR
each world-fixed channel routes to as the head turns, and where the nearest-BRIR crossfade
boundaries sit. The angles must match the directions the baked BRIRs were actually measured at;
the owner confirms their set is at ±110/±150, so this aligns the model with the hardware (a
correctness fix, not a preference). A different BRIR set would set its own angles here (a natural
follow-up is to make them configurable per set rather than a constant).

## Tests

The nearest-BRIR selection cases were re-verified against the new angles (the expected channels
are unchanged; a source at 100° still picks SL, 170°/179° still RL, etc.). The kept interpolation
cluster (`baked_interp`) hardcoded "60° = midway L↔SL" — updated to **70°** (midway 30↔110) and
the L→SL sweep to span 110°. 305 tests pass, ruff clean.

## Decision / next

Both shipped. The steering-angle change alters which BRIR a channel uses near the surround/rear
boundaries, so it is worth an ear check, but it is the correct geometry for the owner's set.
Making the layout angles a per-set config (not a module constant) is the clean follow-up if more
BRIR layouts are added.

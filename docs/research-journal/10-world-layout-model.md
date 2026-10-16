# 10 — 7.1 world layout model (B1.4)

**Date:** 2026-10-16  ·  **Milestone:** M1  ·  **Status:** done

## Question

Where do the virtual speakers live, and how do we turn their fixed world
positions into the `(azimuth, elevation)` each one presents to a *fixed* head —
the input the HRIR lookup (B1.1/B1.2) and the convolution engine (B1.3) consume?
This is the geometry half of the render engine (requirements § 5–§ 6, FR-3).

## Context

Everything downstream speaks `(az, el)`: `hrir.nearest_index` /
`interpolate.interpolate_hrir` look HRIRs up by direction, and `SPEAKER_LAYOUT_7P1`
(in `surround.py`) already lists the nominal 7.1 angles. But a *layout* is not a
list of angles — the whole point of the project is that the head **moves**, and
when it does the per-speaker direction changes. The core math (requirements § 6)
is `d_i = Rᵀ · (p_i − p_h)`: fixed world positions `p_i`, a head position `p_h`
and orientation `R`, projected into the head frame. B1.4 is the fixed-head case
(`R = I`, `p_h = 0`), but it must be built as the full projection so M3/B3.1 only
has to supply `R` from the tracker — otherwise the fixed-head code is a dead end.

FR-3 also requires per-speaker **distance** and an overall **scale** to be
user-configurable. For a head at the ring centre those don't change the angles,
but they are load-bearing the instant the head translates (a nearer speaker swings
through a larger angle), so the model must keep full 3-D positions, not just
angles.

## Hypothesis

A right-handed world frame anchored on the neutral head — `+x` forward (toward
the screen / FC), `+y` left, `+z` up — makes `azimuth = atan2(y, x)` and
`elevation = asin(z/r)` match the azimuth convention already used by `hrir.py`
and `SPEAKER_LAYOUT_7P1` (CCW from front, +90° = left ear). Placing the 7.1
speakers at their nominal azimuths on that sphere and projecting with `R = I`,
`p_h = 0` must reproduce `SPEAKER_LAYOUT_7P1` **exactly**; a yaw of the head must
shift every speaker's azimuth rigidly by the opposite angle; a forward step must
swing the side/back speakers behind the listener. All of this is pure geometry,
so the tests can be exact, not approximate.

## Method

- New module `binaural_headtracker.world` (pure numpy, headless):
  - `direction_to_unit(az, el)` / `unit_to_direction(vec)` — the convention, as an
    exact round-trip; `unit_to_direction` is vectorised over `(…, 3)` and returns
    **signed** azimuth in `(-180, 180]` to round-trip with the signed
    `SPEAKER_LAYOUT_7P1` (the HRIR lookup normalises to `[0, 360)` itself).
  - `WorldLayout(channels, positions)` — frozen dataclass holding `(C, 3)` metre
    positions; `distances`, `index`, `scaled(factor)`, and
    `head_relative_directions(head_position=None, head_rotation=None)` — the
    § 6 projection `d_i = Rᵀ(p_i − p_h)`, defaulting to the neutral head. With
    positions stacked as rows, `(Rᵀ · v)` for all rows is `v @ R`.
  - `layout_7p1(distance=2.0, scale=1.0, distances=None, elevations=None)` — the
    nominal ITU/SMPTE ring from `SPEAKER_LAYOUT_7P1`, with per-channel distance
    and elevation overrides.
- 25 tests (`tests/test_world.py`): direction/unit round-trip across the 7.1
  angles + off-horizon; axis sanity (front=+x, left=+y, up=+z); the fixed head
  reproducing every `SPEAKER_LAYOUT_7P1` angle exactly; distance/scale
  direction-invariance for a centred head; per-channel distance & elevation
  overrides; and the world-lock projection under a hand-built yaw matrix (single
  speaker and the full ring shifting rigidly by `−yaw`) and under head
  translation; plus validation (bad shapes, non-positive scale/distance, a
  speaker coincident with the head).

One variable at a time: the convention is pinned by the round-trip tests; the
fixed-head angles are pinned against the independently-authored
`SPEAKER_LAYOUT_7P1`; the moving-head behaviour is checked separately with
explicit rotation/translation.

**Scope boundary.** B1.4 deliberately does **not** define the yaw/pitch/roll →
`R` mapping. `head_pose.py` (B0.4) is explicit that the sign/axis mapping and the
neutral pose are a *calibration* concern (B2.2), and B3.1 is where the tracker's
Euler stream is bound to this frame. Fixing a matrix convention now would pre-empt
that with no tracker to validate it against. `head_relative_directions` takes the
matrix as data, so B3.1 supplies it without touching this module.

## Result

All **25** new tests pass; full suite **135 passed**; ruff clean.

- Fixed head at the origin reproduces every `SPEAKER_LAYOUT_7P1` angle to `1e-9`
  (L 30°, R −30°, C/LFE 0°, SL/SR ±90°, RL/RR ±135°, all el 0).
- A centred head's directions are identical at distance 1 m vs 5 m and after
  `scaled(10)` — distance/scale move the geometry, not a centred head's angles.
- Head yaw of +20° shifts every speaker azimuth by exactly −20° (mod 360) with
  elevations unchanged — a rigid world-locked rotation, the effect itself, in
  miniature.
- A 1 m forward step keeps C dead ahead and swings SL from 90° to behind the
  listener (`|az| > 90°`).

## Interpretation

The frame choice pays off: because `+x/+y/+z = front/left/up`, the layout's
`(az, el)` drop straight into the existing HRIR lookup with no conversion, and the
fixed-head angles match the hand-written nominal layout exactly — two
independently-derived routes to the same numbers, which is the real correctness
signal here (a geometry bug would desynchronise them).

Building the full `Rᵀ(p − p_h)` projection rather than a fixed-head special case
cost nothing extra and turned the M3 coupling into "supply a 3×3 matrix": the yaw
test *is* a world-lock demo (speakers rotate opposite the head), so the core
perceptual mechanic is already exercised in a unit test, well before any audio or
tracker exists.

No negative result to report — the geometry behaved as predicted on the first
pass. The one judgement call was leaving the Euler→matrix convention undefined;
the alternative (guessing a mapping) would have been untestable against a tracker
that does not exist yet and risked baking in a wrong sign that B2.2 would have to
unpick.

## Learnings

- A single right-handed world frame `+x=front, +y=left, +z=up` makes
  `az=atan2(y,x)`, `el=asin(z/r)` agree with the SOFA/HRIR azimuth convention, so
  world geometry and HRIR lookup share one coordinate system with no conversion
  layer. (→ L26)
- Build the fixed-head layout as the full `d_i = Rᵀ(p_i − p_h)` projection with
  `R`, `p_h` as defaulted arguments, not a special case: the fixed head is
  `R=I, p_h=0`, and M3 plugs the tracker in by passing a matrix — no rewrite, and
  the world-lock effect is unit-testable now (yaw shifts every azimuth by −yaw).
  Keep the Euler→`R` mapping out of it; that binds to the tracker and belongs to
  calibration (B2.2/B3.1). (→ L27)
- Verify a geometry module by two independent routes to the same numbers: the
  projected fixed-head angles must equal the hand-written nominal layout, and
  `direction_to_unit`/`unit_to_direction` must round-trip. Distance/scale being
  direction-invariant for a centred head is a third, cheap invariant. (→ L28)

## Decision / next

B1.4 **done**: `binaural_headtracker.world` gives screen-relative 3-D speaker
positions (configurable distance/scale per FR-3) and the § 6 head-relative
`(az, el)` projection, defaulting to the fixed neutral head and ready for the
tracker matrix at M3. Default ring radius for the prototype: **2.0 m**.

Next eligible M1 block is **B1.5** — wire B1.1 (SOFA load) + B1.2 (interpolation)
+ B1.3 (convolution) + B1.4 (this layout) into the **generic-SOFA** static
renderer: `layout_7p1().head_relative_directions()` → `interpolate_hrir` per
channel → `StereoConvolver`, offline-verified on a known pan. (B1.6 already does
the owner's *baked-BRIR* path; B1.5 is the SOFA path.) No new follow-up blocks.

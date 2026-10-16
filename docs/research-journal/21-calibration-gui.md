# 21 — Calibration console GUI: live camera + world-lock radar + VR recenter (B6.1)

**Date:** 2026-10-17  ·  **Milestone:** M6 (first slice) / M2 front-end  ·  **Status:** done

## Question

The owner wanted a **VR-headset-style calibration**: see the live camera with the
head-pose estimate, a top-down **azimuthal plane** of the speakers, and a one-key
**recenter**. It's also the visual proof that the M2 calibration world-locks before
any audio (M3), and the place to confirm the camera axis signs by eye.

## Method

Chose layout **A (camera | radar, side-by-side)** from a high-fidelity mockup the
owner picked among three. Split the work the project's way:

- **Portable geometry — `binaural_headtracker.radar`** (unit-tested, no OpenCV):
  `azimuth_to_radar_xy` (front → up, +az → screen-left, matching the world az
  convention) and `nearest_front_channel` (the speaker closest to straight ahead —
  the real BRIR the `nearest` renderer, B1.12, would pick — highlighted on the radar).
- **Host shell — `apps/calibration_gui.py`** (OpenCV, host-only): mirrored webcam with
  the MediaPipe face mesh + a yaw/pitch/roll HUD on the left; the world-locked azimuth
  radar on the right (rings, FRONT/LEFT/BACK/RIGHT, the head arrow facing up, the 7.1
  ring, the active speaker highlighted amber with a line to it); a status line with
  the update rate; **C** recenters (→ `calibrate.calibrate_neutral` on the recent pose
  buffer), **Q** quits. Composed from tested parts — `tracking.PoseFilter` (1€
  smoothing), `calibrate.directions_for_pose` (B2.2), `world.layout_7p1` (B1.4),
  `radar` — so the GUI is just drawing.

## Result

B6.1 (calibration slice) **done**. 5 radar tests (247 total, ruff clean) pin the
geometry: front→up, +90°→left, −90°→right, 180°→down, radius scaling, and
nearest-front with 360° wrap. The OpenCV app itself is host-only (camera + MediaPipe +
a window), so — like the spikes — it is not run on CI; its logic is the tested `radar`
+ the already-tested tracker/calibrate/world.

## Decision / next

- The console is the calibration front-end **and** the live world-lock demo: turning
  the head swings the ring the opposite way, and recenter zeroes it — on the host this
  is where the three axis **signs** get confirmed by eye (turn left ⇒ ring right; nod
  up ⇒ ring tilts down; flip a `--*-sign` flag if not).
- **Next: B3.1.** The GUI already runs the exact world-lock chain
  (`directions_for_pose` → `nearest_front_channel`); B3.1 reuses it to drive the
  **audio** — per frame pick each channel's nearest real BRIR and crossfade the
  convolution click-free (entry 18) — turning the radar's motion into sound.
(→ L44)

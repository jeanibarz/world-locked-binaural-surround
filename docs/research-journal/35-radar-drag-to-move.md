# 35 — Drag to move the test source live (B6.6)

**Date:** 2026-10-18  ·  **Milestone:** M6 (GUI)  ·  **Status:** done

## Question

The owner found **timealign** best by ear and wanted to compare the methods on a **moving**
source, not just at clicked points — i.e. click-and-drag the radar to sweep the virtual sound in
real time, at whatever speed the cursor moves.

## Change

The passthrough's radar mouse handler now tracks a **drag**: left-press starts it (places the
source, turns it on, stops any auto-rotation), moving with the button held sweeps the source
live, release ends it. `_console.radar_az_at` gained `allow_outside=True` so a drag that strays a
little past the ring still tracks the cursor's angle (only the dead-centre is ignored); a plain
click still has to land on the ring. The source position updates at mouse-event rate; the camera
loop reads it each frame to steer the voice pool, so the move is smooth and `M` still A/Bs the
interpolation method mid-sweep. 308 tests, ruff clean; host launcher defaults the test source to
`--method timealign`.

## Next

If timealign is confirmed the keeper, folding it into the **7.1** render (not just the test
source) needs the GPU/Rust kernels to accept a variable/dense bus set — the larger follow-up.

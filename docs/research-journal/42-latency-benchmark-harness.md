# 42 — Automated latency benchmark + the device-buffer hypothesis (B8.5b)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** harness done

## Question

Hand-tuning latency by editing the launcher, restarting, moving the head and reading the on-exit
number does not scale — and it misled us: dropping the block **1024 → 512** barely moved the owner's
measured pose-age-at-ear (**p50 138 → 143 ms**). A summed number can't say *which stage* the latency
is in. Build something that sweeps settings unattended and **splits the latency by stage**.

## Change

`apps/latency_bench.py` — runs the real render pipeline with a **synthetic head pose** (a
deterministic yaw sweep, no camera/MediaPipe) and a **synthetic 7.1 sine** (no VoiceMeeter), so one
invocation measures one configuration, unattended, after discarding a warm-up. It reports each
stage as a **distribution** (p50/p95/p99 + extremes):

- **stale** — pose age when the block is rendered (camera interval + worker pickup),
- **queue** — time the rendered block then waits in the output FIFO,
- **device** — the output device buffer (real `out.latency` in `--output device`; a nominal in `sim`),
- **total** = stale + queue + device — the pose-age-at-ear the GUI shows.

It also checks the audio is **processed correctly** (output finite, not silent, energy still at the
input tone → `tone_snr_db`) and counts **dropouts** (consumer finding the FIFO empty). `--output sim`
runs anywhere (a real-time-clocked consumer thread, no device); `--output device` uses the real
sounddevice output, and `--exclusive` switches it to **WASAPI exclusive mode**. `apps/latency_sweep.py`
drives a grid as fresh subprocesses (clean device re-acquire each, like restarting) and prints a
ranked, component-split table; the host `run_latency_sweep.cmd` runs it on the FiiO comparing shared
vs exclusive across block sizes.

## Result (sim, so far) + hypothesis

The headless sim (nearest, which keeps up in pure Python) already localises the latency:

| block | cushion | queue p50 | stale p50 | device (nominal) | total p50 |
|---|---|---|---|---|---|
| 512 | 2 blk @ 20 ms | 17 | 21 | 30 | **68** |
| 512 | 4 blk @ 40 ms | 38 | 21 | 30 | 89 |
| 1024 | 2 blk | 39 | 20 | 30 | 89 |

**queue** tracks `cushion_blocks × block_ms` and **stale** is ~20 ms (one 30 fps interval) — both
small and both move with the knobs. With a *nominal* 30 ms device buffer the total is **68 ms**; the
owner's *real* p50 is **143 ms**. That ~75 ms gap is the **real output device buffer** — almost
certainly WASAPI **shared mode** on the FiiO sitting around ~90–100 ms, which dwarfs the block/cushion
terms. That is exactly why 1024 → 512 did nothing: it only touches *queue* (~20 ms), while the
dominant term is the device buffer. **Lever: WASAPI exclusive mode** (B0.5), which the host sweep now
measures directly.

## Learnings

- **When a tuning knob "does nothing", a component-resolved measurement finds the term that
  dominates.** Block size barely moved the total because the total was ~⅔ a fixed device buffer the
  summed number hid. Split latency by stage (stale / queue / device) and the knob-to-stage map is
  obvious: block→queue, camera→stale, WASAPI mode→device.
- **A synthetic pose + synthetic tone turns an interactive, device-bound measurement into an
  unattended sweep.** No human, no camera, no music; deterministic; the one thing the sim can't model
  (the real device buffer) is exactly what the host `device` mode run adds back, so the two together
  fully attribute the latency.
- Report **distributions and dropouts**, and verify the tone actually passes — a latency harness that
  doesn't check integrity can "win" by silently dropping audio.

## Decision / next

- Harness shipped (`latency_bench` + `latency_sweep`, 3 headless tests). **Run `run_latency_sweep.cmd`
  on the host** to confirm the device-buffer hypothesis and read shared-vs-exclusive directly.
- If exclusive mode cuts the device term as expected, wire exclusive (opt-in) into the passthrough
  output and set it in the launcher. Then **B8.6 orientation prediction** takes on the remaining
  ~20–40 ms of pose *stale*ness (the camera term), which no buffer change can touch.

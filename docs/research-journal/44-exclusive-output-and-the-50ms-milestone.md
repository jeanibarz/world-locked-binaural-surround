# 44 — WASAPI exclusive output, and under 50 ms (B0.5 / B8.5d)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** done

## Result (the milestone)

The owner's host sweep, after the MME→WASAPI fix (journal 43), with the per-stage split:

| block | mode | dev (device buffer) | stale | queue | **total p50** | p95 |
|---|---|---|---|---|---|---|
| 512 | WASAPI **exclusive** | **13** | 20 | 1 | **35** | 51 |
| 1024 | exclusive | 13 | 32 | 1 | 46 | 46 |
| 512 | WASAPI shared | 22 | 21 | 1 | 43 | 58 |

Head-move→ear is now **p50 35–46 ms — under the ~50 ms "scene swims" threshold** (journal 36),
from the ~140–200 ms we started at. The journey was all **device buffer**: MME ~90–100 ms →
WASAPI shared **22 ms** → exclusive **13 ms**. `queue` (1 ms) and `stale` (20 ms, the camera) are
now the whole story.

## Changes

- **Passthrough `--exclusive`** (B0.5): open the output in WASAPI exclusive mode (smallest buffer),
  falling back to shared with a printed note if the device refuses it (format/rate/in-use). The host
  launcher now passes it. Opt-in, safe.
- **Benchmark pacing fix** (B8.5d): the sweep's device-mode runs reported 400–1000 phantom
  "dropouts" at 32–68 % load — a harness artifact. The producer was a Python thread paced by a
  `sleep` clock, but Windows' default ~15 ms timer granularity can't feed a 2-block (21 ms) cushion,
  while the real app is fed by hardware-clocked audio callbacks. Fixed two ways: the producer now
  **tops the FIFO up to the cushion** (consumer-paced, refilling in a burst after a coarse wake,
  like the real worker draining a capture backlog), and the bench **raises the Windows timer
  resolution to ~1 ms** for the run. Sim now shows 0 dropouts where load is under 100 %, and genuine
  overload is still reported — so the integrity column is trustworthy.

## The capture buffer question (why it is *not* the head-tracking latency)

The owner asked about the VoiceMeeter/VB-Cable ~128 ms buffer (6144 samples) on the **capture**
side and whether to drop VoiceMeeter entirely. Key distinction, now measured:

- **Head-move→ear** (the number above) = pose age + render + **output** buffer. The capture buffer
  sits *before* our render; the pose is stamped at camera time and applied to whatever audio block
  is current, so a head turn is reflected on the next rendered block regardless of how old that
  audio is. The capture buffer does **not** add to head-tracking responsiveness — our probe starts
  at the pose timestamp and proves it.
- The capture buffer only delays the **audio content** (music late vs a video source — A/V sync).
  For head-tracked externalisation it is irrelevant; for lip-sync with video it matters, and it is a
  **setting** (VoiceMeeter's WDM buffering / the VB-Cable control panel Max Latency), lowerable from
  6144 toward ~1024 at a stability cost — not a fixed 128 ms.

Dropping VoiceMeeter is possible but is an architecture change, not a quick win: the lowest-latency
endgame is to run the binaural render **inside EqualizerAPO as a VST** on a 7.1-configured FiiO
endpoint (single engine clock, no virtual cable, no loopback, no second app) with the Python/camera
tracker feeding pose over shared memory — our Rust kernel wrapped as the plugin. Recorded as a future
RFC (B8.7); not needed to hit the current target.

## Learnings

- **The whole latency was the output device buffer, and most of it was the host API.** MME→WASAPI
  (90→22 ms) + exclusive (→13 ms) took head-move→ear under 50 ms with **no DSP change** — block size
  and cushion were never the lever. Measure by stage before tuning.
- **A sleep-paced producer can't simulate a hardware audio clock on Windows** (~15 ms timer); pace
  the synthetic producer off the *consumer* (top-up-to-cushion) and raise the timer resolution, or
  the harness invents dropouts the real callback-driven app never has.
- **Capture latency ≠ reaction latency.** In a world-locked renderer the pose is applied fresh at
  render, so input buffering delays the audio, not the head-tracking — don't spend the virtual-cable
  rework budget on a term that doesn't move the felt number.

## Decision / next

- Shipped `--exclusive` + the bench fix; launcher updated. **Re-run the sweep** (dropouts should be
  ~0 now) and reload the passthrough (watch the startup `WASAPI exclusive (~13 ms)` line and the
  S-panel total).
- Only ~20 ms of `stale` (camera pose age) remains — **B8.6 orientation prediction** is the last
  lever, and it is now the dominant term. The VoiceMeeter→APO/VST rearchitecture (B8.7) is optional,
  for A/V sync, not head-tracking.

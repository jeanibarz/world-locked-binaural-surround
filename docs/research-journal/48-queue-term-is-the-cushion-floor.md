# 48 — The queue term is the cushion floor; block size is the lever (B8.8)

**Date:** 2026-10-18  ·  **Milestone:** M8 (latency hardening)  ·  **Status:** measured (host, via interop)

## Question

The live split (journal 46/47) put `queue` at 16–20 ms — ~15× the bench's ~1 ms (B8.5b). B8.8
recorded it as the next lever but guessed the fix was *"sweep `--latency-ms` 60→20"*. Two things
were wrong with that guess, and a direct measurement settles both: (a) `run_passthrough.cmd`
already runs at **`--latency-ms 20`**, not 60, and (b) `cushion = max(2, round(latency_ms·sr/block))`
has a **2-block floor** — so at block 512 the cushion can't drop below ~21 ms no matter how low
`--latency-ms` goes. So: is `queue` the cushion, and if so what actually moves it?

## Measurement

Why the bench couldn't answer this: its producer is **consumer-paced** (`if fill < cushion:
produce_one()`) so the FIFO stays near-empty (queue ≈ 1 ms). The live worker is **capture-burst-paced**
(drains the whole VoiceMeeter backlog, `extend`s the FIFO, drops oldest past `max_backlog`), which
holds the FIFO near the cushion. So a faithful probe needs the real loopback pacing, not the bench.

`scratchpad/queue_probe.py` (run on the host from WSL via interop, `py.exe`): replicates the
passthrough's exact wiring — pyaudiowpatch WASAPI **loopback capture** of VoiceMeeter Input →
burst-drain render worker (real Rust kernel) → output FIFO (cushion + `max_backlog`, drop-oldest) →
sounddevice WASAPI out to the FiiO — but with a **synthetic pose** (`pose_t` set at 30 Hz) instead
of the camera, so `queue`/`device` are measured unattended, no face. A low 8-ch tone is fed into
VoiceMeeter Input for the whole run to keep the loopback active (an idle WASAPI render endpoint
emits no loopback callbacks — the first run starved, 807 underruns, until this was added).

## Result

| block | `--latency-ms` | cushion | queue p50/p95 | ear p50 | drops u/o (8 s) | note |
|---|---|---|---|---|---|---|
| 512 | 20 | 2 | **31 / 32** | 69 | 3 / 2 | owner's live config |
| 512 | 10 | 2 | **31 / 32** | 69 | 3 / 2 | **identical — already at the 2-block floor** |
| 512 | 40 | 4 | 63 / 78 | 100 | 0 / 0 | queue tracks cushion (doubles) |
| 256 | 20 | 4 | 31 / 47 | 69 | 3 / 6 | block↓ alone doesn't help (lat 20 keeps cushion 4) |
| 256 | 10 | 2 | **15 / 16** | 53 | 4 / 7 | **the win: −16 ms, needs block↓ AND lat↓** |
| 128 | 6 | 2 | 0 / 16 | 53 | **186 / 480** | too shallow — drift overruns the cushion |

`device` = **22 ms** (WASAPI shared) throughout. (`stale` read a flat ~16 ms = half the 30 Hz
synthetic-pose period, an artifact of the probe, not the real ~40 ms camera stale — ignore it here;
and the absolute `queue` sits a little above the live 16–20 ms because the probe's loopback feeder
is a third clock that deepens the FIFO. The robust signal is the **cross-config deltas**, which are
mechanism, not offset.)

## Interpretation

- **`queue` ≈ the output-FIFO depth, which the burst-drain worker holds at ~cushion blocks.** It
  doubles when the cushion doubles (512: 20→40 ms ⇒ queue 31→63), so it is cushion-driven, full stop.
- **`--latency-ms` is already at its floor for block 512** (`max(2, …)` = 2 blocks ≈ 21 ms). 512@20
  and 512@10 are bit-identical — lowering it does nothing. The merged B8.8 "sweep `--latency-ms`"
  plan was wrong.
- **Block size is the only real knob, and only paired with a matching `--latency-ms`** so the cushion
  actually hits the 2-block floor (256@20 stays at cushion 4 = no win; 256@10 drops to cushion 2 =
  the win). Block 256 @ 10 ms: queue 31→15 ms, **ear-latency 69→53 ms (−16 ms, ~−23 %)**, at a
  modest cost (a few more overflow drops: 4/7 vs 3/2 over 8 s — borderline, wants a by-ear check).
- **Block 128 is past the limit:** the 2-block cushion (~10.7 ms) can't absorb the two-clock
  loopback drift (journal 45), so it glitches hard (186 underruns + 480 overflow drops). Render load
  was fine (14 %) — the floor here is **drift, not CPU.** So block 256 is the practical floor.

## Learnings

- **"Needs host hardware" = run it on the host via interop, not "blocked".** The whole audio
  pipeline (loopback → render → device) is measurable from WSL with `py.exe` + a synthetic pose;
  only a human moving their head / judging by ear truly needs a person. (Recorded in AGENTS.md so the
  next session doesn't re-derive it — it cost a wrong "I can't" here first.)
- **To measure a FIFO's latency you must reproduce its *producer pacing*.** The consumer-paced bench
  and the capture-burst live app give `queue` values 20× apart from the same code; the pacing, not
  the block maths, is the variable.
- **A `max(2, …)` floor is a floor you can't tune past** — the cushion can't follow `--latency-ms`
  below 2 blocks, so block size is the only way down, and it's bounded below by clock drift, not CPU.

## Decision / next

- **B8.8's fix is block 256 + `--latency-ms` ~10** (not a `--latency-ms` sweep), for ~−16 ms ear
  latency, gated on a host **by-ear** check that the few extra overflow drops are inaudible during
  real head motion + `--predict`. Not shipped as a blind default — it trades cushion headroom.
- The 2-block cushion floor and the single-source-vs-feeder offset are the remaining uncertainties; a
  live S-panel read at block 256/`--latency-ms` 10 on the owner's real source confirms the absolute
  number. Backlog B8.8 updated to replace the latency-ms guess with this.

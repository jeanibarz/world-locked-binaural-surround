# 81 — The output-FIFO cushion is a one-shot prefill, so it rides the empty floor

**Block:** B8.7c follow-up (web cockpit, feat/cockpit-ui-redesign) · **Milestone:** M8 / Track B ·
**Status:** mechanism isolated from the code + the owner's live cockpit reading; a re-prime fix applied,
pending the owner's A/B listen test on the host.

## Question

On the `--ui web` cockpit the buffer meter sits at **0–1 of 2 blocks**, "very rarely reaches 2," and the
owner still hears the sound "vanish a bit / crack" — small and rare. Journal 76 already fixed the render
worker's GIL starvation (`sys.setswitchinterval(0.0005)`, #109 closed) and the web canvas draws in the
*browser*, not under the Python GIL — so the worker is not being starved here. Why does the FIFO never
fill to its cushion, and is that the cause of the rare cracks?

## Context

The output cushion is set up once (`voicemeeter_passthrough.py`): `cushion = max(2, round(latency_ms·sr/
block))` and `out_fifo` is **prefilled** with that many silent blocks. The render worker then only ever
*reacts* to input — it renders one `--block` of output per `--block` of captured input and `extend`s the
FIFO; the output callback `popleft`s blocks to feed the device.

In the single-master-clock pull path (`--pull-sysvad`) the worker is **rate-locked** to the output: each
output callback fires one `KookrPullTick`, which advances SYSVAD exactly one capture period, which is the
only input the worker gets. So the worker consumes and produces at *identical* average rates and **can
never get ahead of real time** — it has no spare input with which to rebuild depth.

## Hypothesis

The cushion is a **prefill, not a setpoint.** Because the worker can't out-run the clock, the moment any
transient (a scheduling hiccup, a capture/worker phase slip, a batch-timing jitter) lets the output lead
the worker by a block, that block is gone from the FIFO *for good* — nothing ever puts it back. The queue
therefore ratchets down from the prefilled 2 to the 0–1-block floor and stays there, so the "2-block
cushion" delivers ~1 block of real margin, and the next jitter tips it to an underrun = the rare crack.
(Overflow/drift is structurally absent in single-clock mode — journal 75 — so a crack here is only ever
an underrun.)

## Evidence (from the code + the live meter)

- `fifo_fill_blocks` the cockpit shows is an **instantaneous** `len(out_fifo)` sampled ~1/s, read against
  `cushion_blocks` (default 2). `diagnostics.py` already flags `fifo_fill_blocks < 1.0` as **TIGHT**
  ("output buffer is nearly empty — close to a dropout"), which is exactly the 0–1 the owner sees.
- The only places the FIFO is reduced are the output `popleft` and the `max_backlog` overflow cap; the
  only place it grows is the worker's reactive `extend`. **There is no path that restores the cushion
  after it drains** — no re-prime, no setpoint, no silence top-up.
- The owner reports cracks are *rare* and (per the journal-76 pattern) load is low, so the worker is
  keeping up on average — consistent with a thin-margin floor punctured by occasional jitter, not a
  compute shortfall.

A second, smaller finding fell out of the same read: the cushion is **whole render blocks**
(`round(ms·sr/block)`), so `--latency-ms` only buys depth in ~`--block`-duration steps. At block 2048 the
2→3-block jump needs ~107 ms and 3→4 ~150 ms; any smaller bump reads back as the *same* depth — which is
why nudging the cushion up a little appears to "do nothing."

## Fix

Make the cushion a **maintained setpoint**: on an output underrun, hold the output silent (the pull clock
keeps ticking via `kset`, so the worker keeps filling) until `out_fifo` is rebuilt to the full `cushion`
depth, then resume draining with the whole margin restored. The rebuild silence is **bounded by the
cushion's own latency** — which the owner already accepts as head-move-to-ear delay — and fires only on an
underrun, which is already a glitch, so it never adds silence to a clean stream. Universal: in the
two-clock VoiceMeeter path the worker *can* burst ahead, so the rebuild is even cheaper there; the flag is
touched only by the audio-callback thread and the `out_fifo` read stays under `fifo_lock`.

This replenishes the cushion; it does not deepen it. If the owner's listen test shows the restored 2-block
margin is still too thin for their jitter, the depth lever is `--latency-ms ≥ ~107 ms` (→ 3 blocks) or a
smaller `--block` — now that the margin actually *persists*, a given cushion setting finally means what it
says. The `--log-fifo-fill` A0 diagnostic (→ `binaural_headtracker.fifo_drift`) can confirm the floor
rises off 0–1 after the change.

## Learnings

- A prefill is not a cushion. In a rate-locked passthrough the producer can't rebuild drained margin, so a
  one-shot prefill decays to the sawtooth floor and the configured depth quietly becomes ~1 block. If a
  buffer must *hold* a depth, something has to restore it — a setpoint/re-prime, not just an initial fill.
- A buffer meter pinned near empty with the worker keeping up (low load, rare drops) is a
  **replenishment** problem, not a throughput one — the mirror image of journal 76's "load < 100 % but
  dropping = a blocking problem." Read *which* side (fill vs. compute) is short before reaching for a knob.
- Whole-unit granularity hides in "milliseconds" knobs: surface the step size, or a sub-step adjustment
  silently no-ops and reads as a broken control.

## Decision / next

- **Applied:** underrun re-prime to the full `cushion` in the output callback, default on; `--latency-ms`
  help now states the whole-block step and the setpoint behavior.
- **Owner A/B (the gate):** run `--ui web --pull-sysvad` before/after — the buffer should now recover to
  the full cushion and hold instead of riding 0–1, and the rare cracks should drop. Audio behavior is only
  truly judged by ear on the host, so this is not self-merged; the listen test is the merge gate.
- If still marginal, raise `--latency-ms` to ~110–150 ms (3–4 blocks) or lower `--block`, and/or capture
  `--log-fifo-fill` to measure the new floor.

# 75 — The pull-SYSVAD listen test is clean at a 200 ms cushion (and that depth is itself a finding)

**Block:** B8.7c (RFC Track B, B2, issue #104, PR #108) · **Milestone:** M8 / Track B · **Status:**
host-validated by ear; `--pull-sysvad` cushion default raised to 200 ms; a render-worker stall opened
as a follow-up.

## Question

Journal 74's Approach A (pin the SYSVAD output stage to exactly 480 frames, Option B driver fix for
`err 5`) made `--pull-sysvad listen` start cleanly — no crash, no beeps, a world-locked sine. But the
owner still heard the sound "vanish a bit / crack" occasionally — small and rare. Is the FIFO cushion the
right lever, and what depth does this path need?

## Context

`--pull-sysvad` makes the K11 (FiiO) output callback the **single master clock**: one `KookrPullTick`
per callback advances the SYSVAD device one 480-frame period, and the renderer keeps its larger
`--block` (2048) behind the existing output FIFO cushion (`--latency-ms`). One clock means **no
two-clock drift**, so overflow/drift drops (journal 45) are structurally absent here — a crack can only
be an **output-FIFO underrun**: the render worker missing the drain deadline and the callback emitting
silence. The owner's launcher (`run.cmd` lineage) bakes `--latency-ms 20` — the thinnest cushion in the
range — which is exactly where render jitter shows through.

## Hypothesis

The rare cracks are FIFO underruns, not drift. Raising the FIFO cushion (`--latency-ms`) should remove
them; the minimum clean depth measures how large the worker's worst-case lateness is.

## Method

Owner re-ran `run_pull.cmd listen --pull-sysvad --latency-ms <N>` (an appended flag overrides the
launcher's baked 20 — argparse takes the last value), listening ~1–2 min with normal head movement at
each N, counting the buffer-underrun events.

## Result

| cushion (ms) | underruns |
|-------------:|-----------|
|           80 | some |
|          120 | some |
|          150 | very rare |
|          200 | **none** |

Monotonic: clean only at **200 ms**. 150 ms was close but not clean.

## Interpretation

The hypothesis holds — these are FIFO underruns, and the cushion is the correct lever. But **needing
200 ms is itself the finding.** At block 2048 @ 48 kHz a cushion of 200 ms is ≈ 4.7 render blocks
(≈ 213 ms of audio), so the worker occasionally stalls up to ~150 ms before a block is ready. That is far
too large for ordinary render jitter or GC — a healthy real-time worker keeps up in a couple of blocks.
The prime suspect is **running the Python live over the `\\wsl.localhost` 9P share** (`run.cmd` sets
`ROOT=\\wsl.localhost\...`): 9P can stall 100+ ms on a file touch, and journal 74 already flagged the 9P
share as per-session and fragile. So 200 ms *masks* a stall rather than being an intrinsic cost of the
pull path. The owner's `--predict` (journal 47) extrapolates pose to playout, so the added world-locking
latency of a deeper cushion is largely compensated during motion — which is why 200 ms stays usable.

## Learnings

- In single-clock pull mode, a crack is always an underrun, never drift — so the cushion is the only
  audio-domain lever, and the cockpit's amber (underrun) vs red (overflow) tick coding (journal 45) reads
  the diagnosis directly.
- An abnormally deep cushion requirement is a worker-stall signal, not a tuning endpoint. Record the depth
  *and* chase the stall, rather than normalising a large latency.

## Decision / next

- **`--pull-sysvad` default cushion → 200 ms** (measured clean), with a mode-aware default in
  `voicemeeter_passthrough.py` (VoiceMeeter path still defaults to 60 ms; an explicit `--latency-ms`
  always wins, and a pull-mode value below the ~150 ms underrun floor now prints a warning). The cockpit
  `±` stepper ceiling was raised (120 → 260 ms) so it can represent the new default without clamping it
  down into underrun. The owner's host `run_pull.cmd` should drop its baked `--latency-ms 20` (to inherit
  200) or set it to 200.
- **Follow-up (opened):** investigate the ~150 ms render-worker stall — confirm the 9P-share hypothesis
  (e.g. stage the checkout on the host's local disk vs. run over UNC and compare the minimum clean
  cushion) so the pull path's cushion can come back down toward the VoiceMeeter path's ~60 ms.
- PR #108's acceptance gate (clean world-locked sine, no `err 5`, no beeps) is **met** at the 200 ms
  cushion; the stall is a performance follow-up, not a blocker on the driver fix.

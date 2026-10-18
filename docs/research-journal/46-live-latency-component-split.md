# 46 — Live latency measured at playout, split by stage (B8.5e)

**Date:** 2026-10-18  ·  **Milestone:** M6 / M8  ·  **Status:** done

## Question

Live, the owner saw p95 106 / p99 121 ms — higher than the bench's idealised 43 — and asked to push
toward 50 ms. To tune the right term instead of guessing, the live app needs the same per-stage
split the bench has (journal 42): **stale** (pose→render) vs **queue** (FIFO wait) vs **device**.

## Change

The live pose-age-at-ear probe now measures **at playout**, not at push. Each FIFO entry carries
`(block, pose_t, render_t)`; when the output callback hands a block to the device it records:

- **stale** = `render_t − pose_t` (camera interval + detect + worker pickup),
- **queue** = `now − render_t` (time the block waited in the FIFO),
- **device** = the output buffer (`out.latency`),
- **total** = `now − pose_t + device` = the three summed.

This replaces the old push-time *estimate* (`ahead × block_ms + out.latency`) with a direct
measurement of when the audio actually leaves for the ear. The **S** panel's sub-line now reads
`stale X + queue Y + dev Z   p95 … p99 …`, and the on-exit print adds the split — so the breakdown
is read off the running app, not inferred.

## Result

21 diagnostics tests (the split fields are carried through `classify`). 326 total, ruff clean. No
behaviour change to the audio path beyond tagging FIFO blocks with their timestamps; the overflow
counter (B6.8) and the WASAPI-shared output (journal 45) are unchanged.

## Interpretation

This closes the "why is live higher than the bench" question with data instead of my estimate: the
panel/exit line now attributes the ~100 ms directly. Expectation from the numbers so far — device
~22 ms (WASAPI shared), queue a few blocks, and **stale ~40–50 ms from the 30 fps camera** as the
dominant, and only prediction-addressable, term. Whichever it is, we now tune the stage the split
names.

## Learnings

- **Measure the latency at the point it's incurred.** The push-time estimate (blocks-ahead ×
  block_ms) approximated the FIFO wait; tagging each block with its pose/ render time and reading the
  clock when the *device* takes it measures stale/queue/device exactly, and the split immediately
  says which knob to turn. Same lesson as the bench, now in the live app so they agree.

## Decision / next

- Shipped the live split. Read it on the host: if **stale** dominates (expected, the camera), the
  only lever left is **B8.6 orientation prediction** — predict the pose forward by the measured
  total so the sound matches the head when heard, pulling the effective stale toward ~0 and taking
  the felt latency under 50 ms. If **queue** is unexpectedly large, tighten the FIFO drift first
  (cheap). The split decides.

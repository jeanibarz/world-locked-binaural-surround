# 38 — Diagnostics panel: render config + per-source load split (B6.7)

**Date:** 2026-10-18  ·  **Milestone:** M6 (GUI)  ·  **Status:** done

## Question

Live on the host, the owner hit **render load 100%+ with ~130 dropouts and a ~200 ms head→ear
estimate** — but *only* with the **test source (T) on while music played**; with the test off the
7.1 settled to ~90% load and 0 dropouts. Two gaps: (1) they **couldn't tell what they were
running** ("I don't know which block size I'm using"), and (2) the panel reported one combined
render-load number, so "the test source is what tips it over" was invisible.

## Diagnosis (what was actually happening)

Read the host launcher (`run_passthrough.cmd`): `--method timealign --rust --block 256`. The trap
is a consequence of B4.4 (journal 37): making timealign the **shared** method meant the **full 7.1**
now renders with timealign on a 10° grid — and at **block 256** that dense convolution is ~87% of
the block budget even on the native kernel (matches the measured ~90%). The test source (a
pure-Python voice pool) mixed in over music then pushes it past 100% → dropouts. The fix for the
owner is just a bigger block (block 512 ≈ 38%, block 1024 ≈ 15%); the launcher was bumped to 512.
But the deeper fix is to make the panel *show* this so it is self-diagnosing next time.

## Change

- **Render config, on screen.** `classify` carries `block_size`, `samplerate` and a `render_label`;
  the **S** panel prints `engine · method · grid (+bus count)` and `block N smp (X.X ms) @ kHz`. The
  label is read **off the live renderer** — `InterpWorldLocked7p1` grew `engine` (`rust`/`cpu`,
  whichever it actually built), `method`, `grid_step`, `n_bus`; the nearest path reports its class
  (`rust`/`gpu`/`cpu`, 8-bus). So the config is observed, never assumed, and it updates when **M**
  swaps the method.
- **Per-source load split.** The render worker times the test-source render separately
  (`meter["test_ms"]`), passed as `render_ms_test` → `render_load_test`. The load line now reads
  `7.1 AA% + test BB%` whenever the test is adding load, so "test over music is what tips it over"
  is explicit. When the budget is tight **and** the test source is a real share, `classify` adds a
  reason: turn it off (T), or raise `--block` / use `--rust` for A/B-while-playing headroom.

## Method / Result

- 4 new tests: the config fields surface; the test share is split out and **clamped** to the total;
  the test source is blamed only when the budget is tight and its share is non-zero. 317 total
  (incl. the host rust suite), ruff clean. (3 pre-existing `h5py` SOFA failures in WSL — unrelated.)
- Panel verified by logic + the taller box (356 px) fits the two added rows; the on-host PNG/live
  check is the owner's **S** toggle (cv2 is host-only, so it can't render in WSL).

## Interpretation

The panel was already the right place for this — it estimates latency and load, it just didn't
state the *inputs*. Now a tight session answers its own "why": you can see you are on timealign at
block 256, see the 7.1 vs test split, and read the one-line advice. The earlier confusion ("rust?
1024?") is gone because the number comes from the renderer object, not a memory of the launcher.

## Learnings

- A real-time health panel should show the **configuration that produced the numbers** (engine,
  method, block), not only the numbers — otherwise the user cannot connect a bad reading to the
  knob that fixes it. Read it from the live object so it cannot drift from reality.
- When several sources share a budget, **attribute the cost per source**; a single combined load
  hides the one the user can act on (here, the test source they can drop with a keypress).
- A shared-quality setting (B4.4) silently raised the *baseline* cost of the daily path, because an
  old launcher pinned a small block chosen back when that path was cheap `nearest`. Shipping a
  cost-changing default should carry a launcher/runbook update in the same breath (done now).

## Decision / next

- Shipped the config readout + load split; host launcher at `--block 512`. If the owner still wants
  to A/B heavily while music plays, `--block 1024` has the most headroom (at ~+10 ms latency), and
  `--method nearest` is the lowest-latency daily option (cycle to timealign with **M** to compare).
- The ~200 ms head→ear estimate is the separate latency track (B8.5 measurement / B8.6 prediction,
  journal 36); a bigger block trades into it, which the panel now makes visible.

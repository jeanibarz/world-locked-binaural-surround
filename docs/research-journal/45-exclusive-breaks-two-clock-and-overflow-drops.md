# 45 — Exclusive output breaks the two-clock sync; count overflow drops (B6.8)

**Date:** 2026-10-18  ·  **Milestone:** M6 / M8  ·  **Status:** done

## Question

With `--exclusive` (journal 44) the owner heard **"huge dropouts" — but the diagnostics panel said
0 dropouts**. Why the discrepancy, and why does exclusive glitch?

## Diagnosis

Two bugs, one mine:

1. **Exclusive mode is wrong for this loopback architecture.** The passthrough captures VoiceMeeter
   (one clock) and plays the FiiO (another). In WASAPI **shared** mode the Windows audio engine
   resamples between them, hiding the drift. **Exclusive** mode removes that engine resampling for a
   smaller buffer — so our FIFO alone must absorb the drift, and the ~2-block cushion can't. The
   queue repeatedly overflows (and/or starves) → audible glitches. Exclusive saved ~9 ms (22→13)
   but broke sync; shared was clean and already under 50 ms. Recommending it without accounting for
   the two clocks was my error (the journal-02/24 history already said VoiceMeeter bridges two
   clocks).
2. **The dropout counter only counted *underruns*.** When the FIFO grows past `max_backlog` the
   worker discards the oldest block — an audible glitch — but that **overflow drop was never
   counted**. So under exclusive-mode drift the FIFO was *overflowing*, not starving, and the panel
   honestly-but-misleadingly showed "0 dropouts" while the ear heard every discard.

## Change

- Reverted the launcher to WASAPI **shared** (clean, ~22 ms device buffer, under 50 ms). `--exclusive`
  stays available but is documented as incompatible with the loopback two-clock setup.
- The worker now **timestamps every overflow drop** (`meter["over_ts"]`), `classify` takes
  `recent_overflows`, it **fails health** with a plain reason ("capture/output clocks drifting faster
  than the cushion absorbs — raise --latency-ms or use shared mode"), and the panel's dropout line
  shows `N recent +M overflow`. So the two glitch classes — *starved* (underrun) and *discarded*
  (overflow) — are both visible and distinguishable.

## Result

1 new test (overflow drops counted + failing even with 0 underruns). 326 tests, ruff clean. The
panel will no longer report "0 dropouts" while audio glitches; the overflow line names the clock
drift and points at the fix.

## Learnings

- **A low-latency device mode can trade away *sync*, not just buffer.** WASAPI exclusive removes the
  engine's resampling between two unsynchronised clocks; in a capture-and-play (loopback) design that
  pushes all the drift onto a small application FIFO and it glitches. Exclusive mode suits a
  single-clock render-to-hardware path, not a two-clock bridge. Shared mode's "wasted" ~10 ms is
  buying the resampling that keeps it glitch-free.
- **Count *every* way audio is lost, not just the obvious one.** The FIFO starving (underrun) and the
  FIFO overflowing (drop-oldest) are both audible; instrumenting only the first made the panel lie
  exactly when it mattered. A dropped sample is a dropped sample however it was dropped.

## Decision / next

- Shared mode is the default; the latency target is already met there. Exclusive remains an opt-in
  for a future single-clock (APO/VST, B8.7) path. Next latency lever is still **B8.6 prediction** for
  the ~20 ms camera staleness — now the only term left.

# 76 — The deep pull-cushion was the cockpit starving the audio worker on the GIL

**Block:** B8.7c follow-up (issue #109) · **Milestone:** M8 / Track B · **Status:** root cause isolated
by a host A/B; one-line fix applied, pending the owner's low-cushion cockpit re-test.

## Question

Journal 75 shipped a 200 ms `--pull-sysvad` cushion because the path underran below it, and flagged a
~150 ms render-worker stall as abnormal (filed #109, prime suspect the 9P share). What actually stalls
the worker?

## Context

The render worker is a Python thread. The heavy convolution runs **GIL-free** already
(`rust/src/lib.rs:185`, `py.allow_threads`), so the worker holds the GIL only for its per-block Python
glue (slice, bus-mix setup, FIFO extend under a lock). In `--ui cockpit` the **main thread** repaints the
entire Dear PyGui canvas every frame: `cockpit_view.py` `update()` does
`dpg.delete_item(canvas, children_only=True)` then re-issues **hundreds of `dpg.draw_*` calls**
(`voicemeeter_passthrough.py` loop, `win_ck.update()` + `render_frame()` once per camera frame). The
console UI does none of that.

## Hypothesis

The cockpit's per-frame full redraw holds the GIL enough to starve the worker; the console path, with no
such redraw, should run clean at a small cushion. If so, the lever is GIL hand-off, not audio buffering —
and certainly not the DSP (already native).

## Method

Owner ran the same `--pull-sysvad` listen, one variable changed — the UI — at a deliberately small
cushion:

| UI | cushion | underruns |
|---|---:|---|
| cockpit (`--ui cockpit`) | 40 ms | some |
| console (`--ui console`) | 60 ms | **none** |
| console (`--ui console`) | 20 ms | **none** |

## Result

Console is **completely clean at the 20 ms minimum cushion**; the cockpit alone forces the ~200 ms
cushion. The render-time load meter sat **under ~40–50 %** while underrunning — so the worker was never
compute-bound; it was being **blocked**, i.e. waiting for the GIL.

## Interpretation

The 200 ms cushion in journal 75 was masking **GIL starvation by the cockpit's per-frame full redraw**,
not a 9P-share stall. At CPython's default `sys.getswitchinterval()` of **5 ms**, the worker waits up to
5 ms for the GIL every time it needs it — several times per block while the main thread is mid-redraw —
and that compounds into the ~150 ms worst-case lateness. GC was already ruled out (`gc.freeze()` runs at
startup). The 9P share affects source loading at import, not the steady-state hot loop, so it was the
wrong suspect.

## Learnings

- A dropout with the render load well under 100 % is a **scheduling/blocking** problem, not a compute
  one — look at what else holds the GIL (here, a Python GUI repainting every frame), not at the DSP.
- "Rewrite the hot path in Rust" is the wrong instinct when the hot path is already native and already
  releases the GIL. The cheap, correct lever for a starved real-time Python thread is the **GIL switch
  interval** (and, failing that, moving the GUI off the critical thread/process) — not a port.
- One-variable A/B (cockpit vs console, same cushion) localised a week-sized suspicion to a one-line fix.

## Decision / next

- **Fix:** `sys.setswitchinterval(0.0005)` at the entry to the real-time section (next to `gc.freeze()`),
  so the GIL returns to the worker ~10× sooner. Process-global; harmless to the console/bench paths
  (no contention there).
- **Pending:** owner re-tests `--ui cockpit --pull-sysvad` at a low cushion. If the cockpit now runs clean
  near ~20–40 ms, lower the `--pull-sysvad` default (journal 75's 200 ms) back toward the console path and
  close #109. If a residual periodic stall remains, the next lever is `render_dearpygui_frame` holding the
  GIL across vsync — throttle the cockpit redraw rate (decouple it from the camera-paced loop), still no
  rewrite.

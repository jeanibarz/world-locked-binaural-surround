# 72 — The real cause of the beeps: one auto-reset pull event, split across streams

**Block:** B8.7b (RFC Track B, B2) · **Milestone:** M8 / Track B · **Status:** diagnosis done and
corrected; the driver fix is scoped (issue #104) and gated on vendoring the driver source. Journal 71
named a plausible-but-secondary cause; this entry corrects it with a decisive experiment (we record
the wrong turn rather than rewrite it).

## Journal 71 said "notification interval"; a one-variable test says otherwise

Journal 71 attributed the `balance = 0.469` capture deficit to the sink's per-tick advance being one
**notification interval** (~240 frames) vs the K11's 512-frame block. That is at most a small secondary
effect. The decisive test — same run, the **only** change being whether the producer is present:

| run | streams on the sink | capture/output **balance** | out_starve / 6 s |
|---|---|---|---|
| `--mode shared --seconds 6` **(producer on)** | 2 (producer render + loopback capture) | **0.469** | 297 |
| `--mode shared --seconds 6 --no-producer` | 1 (loopback capture only) | **0.940** | 35 |

Balance tracks **1 / (number of RUN streams on the device)**, not the block/interval ratio. With the
producer's render stream present the capture gets ~half the ticks; alone it gets ~all of them.

## Root cause — a shared auto-reset event, one waiter woken per tick

In `minwavertstream.cpp`, **every** stream that enters `KSSTATE_RUN` in pull mode (L1381-1402):

- opens the **same** named event `\BaseNamedObjects\KookrPullTick` via `IoCreateSynchronizationEvent`
  — a **synchronization (auto-reset)** event, and
- starts its **own** `PullThreadRoutine`, which waits on that shared event and calls `PullAdvance()`
  on each signal.

An auto-reset event released from a wait wakes **exactly one** waiter and resets. So one user-mode
`SetEvent(KookrPullTick)` advances **one** stream's DMA position, chosen by the kernel. With K streams
in RUN (render + loopback, and more if several apps play), each advances at ~1/K the tick rate — the
loopback capture starves, and the K11 replays/zero-fills → the variable-pitch beeps the owner heard.

The single-master-clock intent (journal 62/63) was "one K11 tick = the device advances one period."
The implementation makes it "one tick = **one stream** advances," which only holds when exactly one
stream is running. It is our driver, not the FiiO (a correct 48 kHz output that only reveals the
deficit), and not a sample-rate mismatch (sink + loopback are both 48 kHz / 8 ch).

## The fix (issue #104): one device-level pull, advancing every active stream per tick

Move the pull from per-stream to the **miniport (device)**: a single pull thread waits on
`KookrPullTick`, and on each tick iterates the device's active streams and `PullAdvance()`s each once —
so one K11 tick advances the whole device one period, for any number of streams. This restores the
single-clock semantics and makes `balance → 1.0` regardless of how many apps feed the sink.

Rejected alternatives (journal 71): ticking N times per callback (auto-reset coalesces); a free-running
pull thread (two clocks → drift). A manual-reset (notification) event alone doesn't work either — it
stays signalled and the threads would run away without a reset handshake.

## Blocker to doing it through the normal reviewed flow

The B1/B2 pull-mode C++ (`m_bPullMode`, `PullAdvance`, `PullThreadRoutine`, the `KookrPullTick` open)
was hand-edited **directly into the host build tree** `D:\kookr-vm\wds\audio\sysvad\` and is **not in
the repo** — only the header patch `make_speaker_71.py` and the build/sign scripts are tracked. So the
actual kernel change can't be PR-reviewed or rebuilt from the repo, and a disk loss would lose it.
**Before the #104 refactor, vendor the patched SYSVAD source (or a tracked patch) into the repo**, so
the pull-architecture change is reviewable, buildable from source control, and VM-validated with a
checkpoint (safety contract) before touching the host.

## Decision / next

1. Vendor the driver source / patch into the repo (prereq for a safe, reviewable fix).
2. Implement #104 (device-level single pull → advance all streams), rebuild (EWDK), checkpoint + load
   in the Win10 VM, validate `balance → 1.0` / `out_starve → 0` with `pseudo_k11.py` + a producer +
   the loopback (no K11 needed in the VM), then host reinstall and re-run the exclusive 60 s sweep.
3. Safety (journal 70) stands: if a run leaves the endpoint stuck, reboot — never unload the driver.

# 57 — B-pre: audiodg is paced by the WaveRT notification timer, not the device position

**Block:** B8.7b (RFC Track B, B-pre) · **Milestone:** M8 / Track B · **Status:** B-pre partially
answered — a real finding that **refines** the pull-driven design. Full verdict needs one more
experiment (the notification path). All run autonomously in the VM over the SSH channel.

## What got built and validated (the whole B-pre rig works)

End-to-end, autonomously: the elevated SSH channel (journal 54) → Win11 VM with test-signing +
`clean-testsigning` checkpoint (journals 55/56) → the **EWDK** toolchain (host VS2022 17.0.6 is too
old; the EWDK is isolated) → **build → catalog → test-sign → install the SYSVAD driver in the VM**,
which loads and exposes working render endpoints → **Python+sounddevice measurement harness inside
the VM** (installed offline — the VM had no DNS; wheels copied over the VM bus). The full
**modify → rebuild → re-sign → reinstall → measure** loop works and is reproducible
(`spikes/bpre-vm/`).

## The experiment

Modified `minwavertstream.cpp::UpdatePosition` to make the reported position **bursty / pull-driven**
(release the position only in whole ~10 ms chunks, holding the remainder — what a K11 consumer
draining the device in bursts looks like). Measured with `bpre_probe.py`: play a tone into the
SYSVAD **Speakers** render endpoint (WASAPI shared) and record output underflows, DAC-clock
monotonicity, and stream continuity. Baseline (stock smooth position): **CLEAN**. Bursty 10 ms,
bursty 100 ms: **CLEAN**. Rate halved: **CLEAN**. Position **frozen** (`ByteDisplacement = 0`):
**still CLEAN**.

## The rigor checks that made this trustworthy (and caught a near-false-verdict)

A stepped-but-monotonic position is indistinguishable from smooth in the probe, so a clean result
was ambiguous. Two checks resolved it:

1. **No-op check:** replace the change with something unmissable (halve, then freeze the position).
   A frozen position *should* break playback if it drove pacing. It didn't — the first signal that
   either the driver wasn't really modified or the probe was blind to position.
2. **Hash verification:** the running driver's `.sys` (in the DriverStore) vs the freshly built
   `.sys`. **They differed** → the earlier "PASS" results had been running the *cached baseline*
   driver, not my modification. Root cause: a pinned `DriverVer`, so Windows saw "same version,
   already installed" and never replaced the store copy. Fix: **bump the `DriverVer` version every
   build** (+ `devcon remove` / `pnputil /delete-driver /uninstall /force` / `pnputil /add-driver
   /install`). After the fix, the running `.sys` hash matched the frozen build — and freezing the
   position *still* played clean.

So the modification is provably loaded and active, yet has no effect on shared-mode playback.

## The finding

**In WASAPI shared mode, audiodg's render pacing is driven by the WaveRT buffer *notification*
events (SYSVAD's `m_pNotificationTimer` / `TimerNotifyRT`), not by the device's `GetPosition`.**
`GetPosition` only feeds `IAudioClock` (the A/V-sync timestamp an app reads); PortAudio's
`outputBufferDacTime` is its own estimate, so the probe is correctly insensitive to it.

**Consequence for the RFC / the pull-driven design (refines journal 53's pre-M1 seam analysis):**

- A pull-driven **position** alone does **not** perturb audiodg — that risk the RFC worried about is
  a non-issue for *pacing*. Good news: one less failure mode.
- The thing that actually paces audiodg, and whose "burstiness" could stress it, is the
  **notification timer**. The pull-driven design must drive **that** from the K11 consumer
  (fire a buffer-complete notification per real burst), not just advance the position.
- The remaining B-pre risks are therefore: (a) does audiodg tolerate a **bursty notification
  cadence**? and (b) does a bursty **position** corrupt **`IAudioClock` / A-V sync** (which the
  shared-mode tone probe does not measure)?

## Methodology lessons (durable)

- **A clean result can be a false pass.** Always include a no-op/inversion check that *must* fail
  if the mechanism works, and verify the *loaded binary by hash*, not just the build output.
- **Windows caches drivers by `DriverVer`.** Bump the version every iteration or the running driver
  never changes — a silent, very misleading trap.
- Shared-mode app-level probes see audiodg's engine clock, not the device `IAudioClock`; to test a
  position/clock change you must read `IAudioClock` directly (or use exclusive mode).

## Decision / next

- B-pre's original "position tolerance" question: **answered — not a pacing factor** (favorable).
- To finish B-pre: (1) make the **notification timer** bursty/pull-driven and re-measure audiodg
  tolerance; (2) read **`IAudioClock`** directly to check A-V-sync monotonicity under a bursty
  position. Both reuse the validated rig in `spikes/bpre-vm/`.
- Owner check-in at this finding (it redirects the experiment from the position to the notification
  path) before investing in phase (1)/(2).

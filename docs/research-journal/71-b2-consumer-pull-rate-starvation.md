# 71 — The B2 consumer beeps: our SYSVAD sink is under-pulled (240 frames/tick vs a 512 block)

**Block:** B8.7b (RFC Track B, B2) · **Milestone:** M8 / Track B · **Status:** done for the harness
(the drop gate now tells the truth); the real fix is a driver/stream period-alignment, tracked as an
issue. The B2 consumer does **not** yet produce clean audio.

## What the owner heard

Running the B2 consumer live (journal 69/70), the owner heard **beeps with a varying pitch**, not the
continuous 8-sine test signal the producer emits. Yet the harness printed **`ZERO-DROP PASS`**. Both
were true — and the gate was lying by omission.

## Two harness bugs, then the real one

The first-draft `b2_consumer.py` had a 1-deep `latest_stereo` hand-off:

1. **Capture discarded surplus frames.** The loopback callback delivers frames in bursts (often
   > `blk` per callback); `cap_cb` rendered only the first `blk` and dropped the rest.
2. **Output replayed a stale block.** When no fresh render had arrived, `out_cb` re-emitted the last
   stereo block and **did not** count it — so `no_data` stayed 0 and the run read `ZERO-DROP PASS`
   while actually emitting a repeated/fragmented signal (a ~93.75 Hz buzz warbling against the real
   tone = the "variable-pitch beeps").

Fixing both (this PR) — keep every captured frame, render every complete block into a bounded FIFO,
pop one per output callback, and **count a FIFO-empty starve or FIFO-full overflow as a real drop** —
turned the verdict honest:

```
block=512 mode=shared seconds=6
frames: cap=134656 out=287232 balance=0.469  q_depth_max=1/8
DROPS: cap_over=0 cap_under=0 out_under=0 out_starve=297 q_over=0  TOTAL=297
VERDICT: FAIL (297 drops)
```

**`balance=0.469`** is the whole story: the capture side delivers only ~47 % of the frames the K11
consumes. A bounded FIFO cannot paper over a *sustained* rate deficit — it drains and starves forever.

## Root cause — our virtual driver, not the FiiO

The FiiO K11 is a normal WASAPI output clocking correctly at 48 kHz (512 frames per callback); it only
*reveals* the deficit by consuming at the right rate. The deficit is in **our** SYSVAD sink's
pull-driven advance (`minwavertstream.cpp`):

- `PullAdvance()` advances **one notification interval per `KookrPullTick`**:
  `bytes = m_ulDmaMovementRate * m_ulNotificationIntervalMs / 1000` (L1922).
- `m_ulNotificationIntervalMs = ulBufferDurationMs / NotificationCount_` (L616–617). With
  `NotificationCount_ = 2` on a ~10 ms engine buffer that is **~5 ms ≈ 240 frames** at 48 kHz.
- The consumer fires **one** tick per K11 callback (one per 512-frame block). So the sink advances
  ~240 frames while the K11 takes 512 → a **2.13× under-pull**. Measured: cap ≈ 22 443 frames/s
  (≈ 239 frames/tick) vs the 48 000 frames/s the K11 drains. `240 / 512 = 0.469` — exactly the balance.

So it is not a code bug in the driver (it advances exactly one notification period per tick, as
designed) nor anything to do with the FiiO. It is a **period-alignment mismatch**: the sink's
notification interval (set from the WASAPI client's buffer request / `NotificationCount_`) is ~240
frames, but the single-master-clock design (journal 62/63) assumes **one tick = one consumer block**.

## Why the obvious "quick fixes" are wrong

- **Tick more often per callback** — `KookrPullTick` is an **auto-reset** event, so several `SetEvent`s
  before the driver re-arms coalesce into one advance. Unreliable.
- **Free-run a pull thread** at the sink's own rate — reintroduces **two clocks** (sink vs K11) and the
  drift the pull-driven design exists to eliminate (journal 45/62); the FIFO would slowly drift to
  overflow or underflow over a long run.

The only correct fix keeps **one tick = one consumer block**, by making the sink's advance-per-tick
equal the K11 block (512): align the notification interval (e.g. `NotificationCount_`/buffer so one
notification period = the consumer block), or have `PullAdvance` consume a consumer-block worth per
tick. That is a driver/stream-init change needing its own investigation and VM validation — tracked as
a GitHub issue, not blind-patched on the owner's live audio.

## What this PR delivers

The harness now **measures honestly**: every captured frame is kept and rendered through a bounded
FIFO, and real starves/overflows are counted, so `balance` and `out_starve` expose exactly this class
of rate mismatch instead of hiding it behind a stale-block replay. It does **not** fix the beeps — it
proves what causes them and gives the fix an objective target (`balance → 1.0`, `out_starve → 0`).

## Decision / next

- Merge the honest-gate harness (this PR).
- Open an issue: align the SYSVAD pull advance to the consumer block so one `KookrPullTick` = one K11
  block (balance 1.0, zero starve), then re-run the shared smoke + the exclusive 60 s latency sweep.
- Safety rule from journal 70 still stands for every live run: if a run leaves the endpoint stuck,
  reboot — never unload the driver.

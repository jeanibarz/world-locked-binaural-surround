# 51 — The EqualizerAPO dead-end, the real baseline, and the resampler-first RFC (B8.7 superseded)

**Block:** B8.7 (RFC) · **Milestone:** M8 (latency hardening) · **Status:** RFC approved
(owner, 2026-10-20); Track A start authorized. No audio-path code shipped yet — all physical
validation is host + K11, which this box (Linux/WSL) does not have.

## Question

B8.7 stood as *"eliminate VoiceMeeter by running the binaural render as a VST inside
EqualizerAPO on a 7.1-configured FiiO endpoint"* — recorded as the lowest-latency endgame and,
after B8.8 (journal 48), as *the* clean way to cut the `queue` term below the ~70 ms live floor.
The owner asked whether we could instead do a **custom virtual driver in a sandbox** to minimize
latency on their own machine. Drafting that RFC forced the prior plan to be checked, and it did
not survive.

## What the review falsified (three corrections)

1. **EqualizerAPO-on-a-7.1-FiiO-endpoint is physically impossible.** The owner's own channel-count
   argument is right and decisive: the **K11 is a stereo USB DAC**, so Windows can expose only 2
   channels on it. Surround software needs an **8-channel render endpoint**, which the K11 can
   never be — a *virtual* 7.1 endpoint is mandatory. And an APO (EqualizerAPO included) is
   in-pipeline DSP bound to **one** endpoint; it cannot route audio from a virtual device to the
   K11. So "EqualizerAPO on the FiiO" could neither expose 7.1 nor carry the result to the
   headphones. The phrase in B8.7/journal 44 was an aspiration, never a tested config. **B8.7 is
   superseded.**

2. **The baseline was wrong.** B8.7/B0.5 leaned on "35–46 ms". That is the **synthetic
   component-split bench** (journal 44); the 35 ms specifically was **WASAPI exclusive**, which
   journal 45 then **reverted as glitchy** on this two-clock architecture. The real **live** floor
   is **~70 ms** (journal 48: *"structural until B8.7"*), dominated by the **31 ms `queue`** term
   (capture-burst pacing) plus ~20 ms `stale` (camera) and 13–22 ms `device`. Any design must be
   measured against the live ~70 ms, not a config nobody runs.

3. **A custom device does *not* give "one clock end-to-end".** Slaving a virtual device to the K11
   makes only the **output FIFO** drift-free. Games/films/music each carry their **own content
   clock**, so the rate reconciliation is **relocated** into `audiodg` (shared mode) or the app
   (exclusive) — not removed. Consequently a driver does **not** improve head-move→ear (that is
   `stale` + `device`, both untouched). Its genuine latency win is narrower: removing VoiceMeeter's
   **~128 ms capture buffer + burst pacing** (content / A-V latency).

## Method

Followed `rfc-iterative-review`: drafted `docs/rfc/rfc-single-clock-virtual-driver.md` in a
worktree; one round of 5 critics (failure-mode, boundary, design-minimalist, socratic,
ambition); the mandatory post-round-1 empirical checkpoint (the one locally-checkable claim —
the baseline — was verified against journals 44/45/48 and corrected; every *physical* claim was
flagged host-only and scheduled as the earliest gates, since no Windows/K11/VM exists here); and
one consensus attack. The review **converged after round 1** because the headline premise was
falsified — the correct response per the workflow is to restructure around reality, not iterate.

## Result — the RFC (two latency levers, Track A first)

The premise flip changed the goal from "one clock" to **two complementary latency levers that are
not substitutes**:

- **Track A (do first, zero kernel risk):** async **drift-compensating resampler/servo** in the
  existing user-mode bridge, behind a `FrameSource`/`ClockSink` seam. Targets the live **`queue`**
  term. (This is the alternative B8.8 already named; now the primary first step.) A0 = diagnose
  skew-vs-jitter from FIFO fill-vs-time on the host; A1 = servo, measure on the host with the K11.
- **Track B (gated):** a **custom virtual 7.1 capture surface** replacing the VoiceMeeter loopback.
  The only lever that removes the **~128 ms content/A-V buffer** (Track A runs *inside* VoiceMeeter
  and structurally cannot). Dev/most functional test in a **Hyper-V VM** (BSOD contained);
  make-or-break gate **B-pre** is testable in the VM *without* the K11 — does `audiodg` tolerate a
  **pull-driven** WaveRT position (no silence-insert/disconnect, monotonic `IAudioClock`)? Fork
  SYSVAD for plumbing only; its timer-driven clocking is the throwaway part, the pull-driven
  position is the new, risky part.

**Consensus attack** caught the whole panel collapsing a two-axis decision (latency vs owning the
device / FR-2·§9·§10) onto a single latency delta, nearly recommending "just ship the resampler"
— which would cement VoiceMeeter *deeper*. Surfaced as an explicit tradeoff.

## Owner decision (2026-10-20)

Presented the fork rather than deciding it silently. Owner: **latency is the goal; removing
VoiceMeeter is a means, not an end**, and playback is **mixed (video/games/music)** so audio-vs-
video sync counts. This grounds the two-axis split onto **two latency terms**: Track A for
`queue`, Track B for content/A-V. Both justified; **Track A first**, Track B gated on **B-pre
alone** (the intent half is answered). VoiceMeeter removal (FR-2/§9/§10) demotes to a recorded
strategic bonus. Owner then said **"proceed with Track A."**

## Learnings

- **Check the physical layer before the software plan.** A year-old backlog line ("7.1 FiiO
  endpoint") encoded an impossibility that only the owner's channel-count question exposed. The
  DAC being *stereo* makes the virtual 8-channel endpoint non-optional — no amount of APO/VST
  cleverness changes that.
- **Guard the baseline.** A synthetic bench number (35 ms, exclusive) silently became the plan's
  reference even though the live config (shared, ~70 ms) and journal 45's revert said otherwise.
  Measure against what the owner actually runs.
- **"Single clock" is only true for the output FIFO.** Any claim of end-to-end clock unification
  is wrong the moment the source app has its own clock, which all real media apps do.

## Decision / next

- **B8.7 superseded** in the backlog; replaced by RFC Track A (**B8.7a**) + Track B (**B8.7b**).
- **Track A next.** A0 (FIFO fill-vs-time, skew-vs-jitter) and A1 (servo) are **host + K11**
  operations — the owner runs/verifies them, exactly as the live audio path has always been the
  owner's to confirm (journal 50). The code that *can* be written and unit-tested here is the
  servo logic + the `FrameSource`/`ClockSink` seam; its host tuning and glitch/latency numbers are
  the owner's.
- Track B stays gated on the VM `audiodg` pre-test; not started.

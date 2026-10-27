# RFC: Cut the live latency floor & remove VoiceMeeter — resampler first, driver gated

**Status:** Approved (owner, 2026-10-20). **Track A:** A0 done — the `queue` term is jitter, not
clock skew, so A1's drift servo was deprioritized (journal 52). **Track B:** the B-pre gate
**PASSED** (journal 59) and the custom pull-driven SYSVAD driver is validated end-to-end on the
host — real audio world-locks through it, VoiceMeeter-free (journals 68–80; see
[`rfc-sysvad-real-loopback.md`](rfc-sysvad-real-loopback.md)). Integration into the daily app ongoing.
**Date:** 2026-10-20
**Author:** Jean Ibarz (with Claude)

> **v2 note.** v1 proposed a custom kernel "single-clock" driver as the primary path.
> A 5-critic review falsified its headline premise (see *Critic feedback
> incorporated*): slaving a virtual device to the K11 makes only the **output FIFO**
> drift-free — it does **not** remove the app's content clock, which is merely
> relocated into `audiodg`/the app. v2 restructures around what is actually true,
> inverts the milestone order (cheap zero-risk work first), and re-scopes the driver
> as a **gated** second phase whose real value is removing VoiceMeeter's capture-burst
> buffer, not "one clock end-to-end".
>
> **v3 note (owner decision, 2026-10-20).** Asked to resolve the one fork the review
> left open, the owner stated: **latency is the goal; removing VoiceMeeter is a *means*
> to it, not an end in itself**, and playback is **mixed (video, games, music)** so
> audio-vs-video sync counts. This collapses v2's two co-equal axes into **one goal
> (latency)** with two complementary levers that are *not* substitutes: Track A removes
> the live **`queue`** term in user mode (cheap, now); Track B removes the **content /
> A-V** latency (VoiceMeeter's ~128 ms capture buffer) that Track A structurally cannot
> touch. Because the owner feels A-V latency on video/games, Track B keeps a genuine
> *latency* job — so the plan is **Track A first, then Track B**, both justified by
> latency alone. Removing the VoiceMeeter dependency (FR-2/§9/§10) stays a real
> **strategic bonus** that falls out of Track B, but it is no longer the justification.

---

## Problem

The world-lock passthrough works. Its **live** end-to-end latency is floored at
**~70 ms** on the owner's shipped config (block 512, `--latency-ms 20`, WASAPI
**shared**), measured on the host (journal 48). Journal 48's verdict: *"the ~70 ms
live floor is structural until B8.7."* That is comfortably above the ~50 ms "scene
swims" threshold, so there **is** a real gap worth attacking.

Where the 70 ms lives (live component split, journals 44/46/48):

| term | live value | what it is | moved by this RFC? |
|------|-----------|------------|--------------------|
| `stale` | ~20 ms | camera pose age (~1 frame @ 30 fps) | **No** — camera/tracker, out of scope (B0.7/B8.6) |
| `device` | 22 ms (shared) / 13 ms (exclusive) | K11 output buffer | Only indirectly (exclusive needs the drift gone first) |
| `queue` | **31 ms** | output FIFO depth — **the dominant, movable term** | **Yes** — this is the target |

Two facts a correct design must respect (both verified against the journals; v1 got
the first one wrong):

1. **The baseline is the live ~70 ms, not the 35–46 ms bench.** The 35–46 ms figures
   are the *synthetic* component-split sweep (journal 44); the 35 ms specifically was
   **WASAPI exclusive**, which journal 45 then **reverted as glitchy** on this
   architecture. Comparing a new design against 35 ms would be comparing against a
   config nobody runs. (journals 44, 45, 48)
2. **Head-move→ear will not improve here.** It is `stale` + render + `device`; the
   `queue`/capture path sits *before* render and the pose is applied fresh, so no
   change to the capture/clock path moves it (journal 44). Any design that claims a
   head-tracking-latency win is overclaiming. The movable prize is the **`queue` term
   and audio-content / A-V latency**, both driven by the capture side.

### Why the `queue` term is 31 ms live (and the capture side matters)

The 31 ms live `queue` (vs ~1 ms in the bench) is **capture-burst pacing**: the
VoiceMeeter WASAPI loopback delivers audio in bursts, so the render worker drains a
backlog and the output FIFO sits deep (journal 48). On top of it, VoiceMeeter/VB adds a
**~128 ms capture-side buffer** (6144 samples) that delays audio *content* / A-V sync
(not head-tracking). Both the burst pacing and the 128 ms buffer are **properties of the
VoiceMeeter loopback bridge.**

### The two-clock drift, stated correctly

VoiceMeeter's capture clock ≠ the K11's output clock. In WASAPI **shared** the Windows
engine resamples between them and hides the drift (clean, but the FIFO cushion is both
the latency and the drift-absorber); **exclusive** removes that resampling and the small
FIFO then glitches (journal 45). Shrinking block/cushion hits the same wall (block 128:
186 underruns + 480 overflow drops / 60 s at 14 % CPU — drift/jitter, **not** CPU;
journal 48).

**What v1 got wrong about the fix.** v1 claimed a custom virtual device *slaved to the
K11* gives "a single clock domain end-to-end", removing the drift entirely. That is only
half true and it is the half that doesn't matter for the stated apps:

- **True:** our *output FIFO* (virtual device → K11) can be made drift-free by draining
  exactly the frames the K11 consumed each callback.
- **False:** the *whole chain* app → virtual device → K11 is not one clock domain
  whenever the app has its own content clock — which games, film players and music apps
  all do. That audio still has to be rate-reconciled to the K11 somewhere; slaving the
  virtual device merely **relocates the resampler** from VoiceMeeter into `audiodg`
  (shared-mode apps) or the app's own renderer (exclusive-mode apps). It does not delete
  it.

So "remove the second clock" is not a real differentiator for this workload. The honest
differentiators of a custom device over the status quo are narrower and must be stated as
such (below).

### Dead end, for the record

The old B8.7 **EqualizerAPO-VST on a "7.1 FiiO endpoint"** is invalid: the K11 is a
**stereo** USB DAC, so Windows can only expose 2 channels on it — an 8-channel virtual
render endpoint is mandatory to receive 7.1 — and an APO cannot route between devices, so
it can't carry stereo to the K11 regardless. (The journals' "7.1-configured FiiO
endpoint" phrasing was an aspiration that was never physically possible, not a tested
config.) This RFC supersedes B8.7.

## Goals / Non-goals

**The goal is latency** (owner decision, v3 note). The review's consensus attack showed
the two work items are **not substitutes** — but, per the owner, that is because they
attack **different latency terms**, not because one is latency and the other a separate
dependency requirement:

- **Lever A — the live `queue` term:** cut the 31 ms output-FIFO depth below the ~70 ms
  floor, glitch-free on real content. Fixable in user mode (Track A).
- **Lever B — content / A-V latency:** remove VoiceMeeter's ~128 ms capture buffer + burst
  pacing, the audio-vs-video delay the owner feels on video/games. Track A **structurally
  cannot** touch this (it runs *inside* VoiceMeeter); only the owned virtual device
  (Track B) removes it. This is the driver's genuine *latency* reason to exist.

**Removing the VoiceMeeter dependency is a means, not an end** (owner decision). It is the
*mechanism* by which Lever B cuts content/A-V latency — and it carries a real **strategic
bonus**: it also advances **FR-2 [Must]** (own the 7.1 output device), **§9** (a
closed-source/donationware-compatible stack — VoiceMeeter is proprietary and unbundleable),
and **§10** (the virtual audio device, *"the biggest native risk … custom driver later"*).
That bonus is worth recording, but it is **not** what justifies Track B; the content/A-V
latency win is. A resampler-over-VoiceMeeter (Track A) cannot deliver Lever B at all.

**Goal (one sentence):** drive the felt latency as low as it goes — take the cheap
`queue` win now (Track A), then remove the content/A-V buffer (Track B) — at the least
risk that achieves each.

**In scope:**

- The owner's machine, FiiO **K11**, 48 kHz, 7.1-in / 2-out.
- A measured reduction of the **`queue` term** and the **audio-content / A-V** latency.
- Reducing or removing the VoiceMeeter loopback (its burst pacing + ~128 ms buffer are
  the movable cost — and removing the dependency is itself a stated owner motivation,
  the original B8.7 framing).

**Explicit non-goals:**

- **Head-move→ear latency** — already under target off this path and structurally
  unaffected by it (see Problem). Not a success metric here.
- "Single clock end-to-end" as a *claim* — we claim only a drift-free output FIFO.
- Any device but the K11; any rate but 48 kHz; WHQL/installer/Secure-Boot distribution;
  in-kernel DSP; generalization; front/back localization (B1.11).

## Requirements

- **R1** Apps must see a selectable Windows **7.1 output device** (the mandatory
  8-channel surface; FR-2). *(True for the driver track; the resampler track keeps
  VoiceMeeter's VAIO as that surface.)*
- **R2** The **capture→output rate** must be reconciled with **bounded, non-accumulating
  error** (no unbounded drift) at a cushion small enough to beat the 31 ms live `queue`.
- **R3** The chosen design must be **glitch-free on real content** — measured at the
  **app-facing** boundary with a real 48 kHz media file and a real game, not only on a
  device-slaved synthetic generator. Reuse journal 45's overflow+underrun counters.
- **R4** Development of any kernel component must be possible **without risking a host
  bluescreen** (VM + kernel debugger).
- **R5** Any kernel component must be **cleanly removable** on the host and must **not be
  able to wedge third-party apps or the default audio device** if the user-mode engine
  stalls, crashes, or is not running.
- **R6** DSP output stays **bit-correct** vs the current offline renderer (regression
  guard; not a risk gate).

## Design — two tracks, gated

### Track A (do first; zero kernel risk): diagnose, then servo the existing bridge

The current bridge is user-mode and already ours. Before any driver:

1. **A0 — Diagnose the term.** Log output-FIFO fill vs time at block 256/512 on the host
   (synthetic pose + the existing bench, then live). The **slope** is clock skew (ppm);
   the **variance/spikes** are jitter/burst pacing. This decides the architecture:
   - *If skew dominates:* an async **drift-compensating resampler** (a PI servo on the
     fill level adjusting the resample ratio, trimming a few samples/s) removes it with
     ~0–2 samples of added latency — inaudible — and no kernel. This is essentially what
     WASAPI shared already does internally, done explicitly in our bridge so the cushion
     can shrink.
   - *If jitter/burst pacing dominates:* the lever is the **capture delivery** (the
     VoiceMeeter loopback burst), which a resampler cannot fix because it keeps
     VoiceMeeter — this is the evidence that justifies Track B.
2. **A1 — Servo.** Implement the resampler/servo behind a `FrameSource` abstraction (see
   seam, below), run it with exclusive output at block 256, measure `queue`/ear-latency
   and glitches/min over ≥10 min on real content.

Track A delivers a **real latency number cheaply and now** — but only on **Lever A (the
`queue` term)**. It does **not** cancel Track B, because it runs *inside* VoiceMeeter and
so **structurally cannot touch Lever B (content / A-V latency)** — the ~128 ms capture
buffer stays. Since the owner feels A-V latency on video/games (v3 note), that term is
real felt latency that only Track B removes. So A0/A1 answer *"how much of the felt latency
is the `queue` term, and did we clear it?"*, **not** *"do we build the driver?"*. The
driver's go/no-go is the **B-pre feasibility test** (Open Questions) — the content/A-V
latency it targets is already confirmed to matter. Even if A1 fully clears the `queue`
term, Track B is still warranted to cut the content/A-V buffer (and, as a bonus, remove
the VoiceMeeter dependency per FR-2/§9/§10).

### Track B (gated on a VM feasibility pre-test): a custom virtual 7.1 capture surface

Scoped honestly: Track B's value is **not** "one clock end-to-end". It is **replacing
the VoiceMeeter loopback with an in-process 7.1 capture surface**, which removes the
burst pacing and the ~128 ms buffer (the content/A-V latency win — Lever B) and, as a
bonus, the VoiceMeeter dependency. The output FIFO is
drift-free with the K11 (the true half of v1's claim); the app→device boundary still has
`audiodg`/the app reconciling the content clock, and **that boundary is where Track B can
fail** — so the design and its gates are built around it.

**Components (corrected seam, per boundary review):**

```
 apps ──render 7.1 (mostly SHARED mode)──▶ audiodg ──▶ ┌───────────────────────┐
                                                        │ virtual 7.1 endpoint  │
                                                        │ (kernel miniport)     │
                                                        │  DATA plane: SPSC ring│
                                                        │  CLOCK plane: advance()│
                                                        └──────────┬────────────┘
                                                      data ▲       │ advance(frames, qpc)
                                                           │       ▼
        FiiO K11 ◀──WASAPI exclusive──┬──────────  Rust RT core (FrameSource/ClockSink)
         (hardware clock)             │              • on K11 cb: read N consumed, run DSP,
                                      │                write stereo, advance() the ring
                                 Python control shell (pose, config, mode/fallback)
```

- **Kernel miniport:** advertises 7.1 @ 48 kHz. **Two explicit planes** (not the vague
  "shared-mem / IOCTL / loopback pin" of v1): a **data plane** = an SPSC shared ring with
  monotonic frame counters (kernel memory, app writes via the normal WaveRT path, engine
  reads); a **clock plane** = a single narrow `advance(frames_consumed, qpc_timestamp)`
  command. The miniport owns overflow/starvation policy (bounded fill target) and a
  **stall watchdog** (R5): if no `advance` arrives within N ms the device goes silent or
  free-runs, explicitly as a *safety net*, not the clock.
- **Rust RT core:** owns the K11 exclusive callback, ring consumption, DSP, and the
  `advance` call; frame accounting uses the **actual** frames the K11 consumed per
  callback (the K11 is a **USB-async** DAC — per-callback frame counts vary with the
  feedback endpoint, so a nominal constant would let drift creep back).
- **Python control shell:** pose, config, mode/fallback — off the RT path.
- Both tracks' engines sit behind the same `FrameSource`/`ClockSink` trait, so Track A's
  VoiceMeeter-loopback source and Track B's virtual-device source are swappable and the
  M3-failure pivot is not a rewrite.

### Driver technology

No user-mode audio endpoint exists on Windows (unlike macOS `AudioServerPlugin`), so the
endpoint is a kernel miniport. **Fork SYSVAD** (microsoft/Windows-driver-samples,
PortCls/WaveRT) **for enumeration/format/pin plumbing only** — its own clocking is a
DPC/timer-driven simulation (exactly the free-run R2 forbids), so the position/clock
subsystem is **new code and the primary risk**, not a tweak. Before committing to M1,
read SYSVAD's `CMiniportWaveRTStream` position-advance path and confirm it can be driven
externally without breaking its KS clock/position handlers. ACX is a fallback only if
SYSVAD's model proves too rigid; the owner's exact Windows build decides availability
(Open Questions). DSP stays in the user-mode Rust core — never in kernel.

### Safe dev & validation (answers the owner's sandbox question; R4/R5)

- **Dev + most functional test → a Hyper-V VM** with test-signing + WinDbg kernel
  debugging attached: a driver fault bluescreens the **VM**, not the host.
- **Critically — the make-or-break question is testable in the VM *without* the K11.**
  The real risk is not hardware timing; it is whether **`audiodg` tolerates a bursty,
  externally-driven WaveRT position** (it may insert silence, disconnect the stream, or
  emit non-monotonic `IAudioClock` timestamps that corrupt A/V sync). Test this in the VM
  with a **software pseudo-K11** (a user-mode consumer draining at a steady ~13 ms
  cadence standing in for the real callback). This front-loads the real risk off the host.
- **Honest limit:** the VM cannot validate *real* K11 latency or USB-async feedback
  behavior — that still needs a test-signed driver on the host (residual BSOD risk).
  Mitigations: System Restore point / full image before each install; `pnputil`-removable;
  documented Safe Mode / F7 "disable signature enforcement" recovery *before* first
  install; a spare boot/SSD if available. **Note:** test-signing requires Secure Boot off,
  which breaks many **anti-cheat games** — so Track B is validated on films, music, and
  non-anti-cheat games; anti-cheat titles are a known limitation of a test-signed PoC.

## Milestones (each a go/no-go; riskiest cheap test first)

| # | Where | What | Gate |
|---|-------|------|------|
| **A0** | Host | FIFO fill-vs-time at block 256/512, live + bench | Classify dominant term: **skew vs jitter/burst**. Decides the Lever-A fix (resampler vs capture-path) — **not** whether Track B is built (Track B targets Lever B, the content/A-V buffer A0 doesn't measure) |
| **A1** | Host | Async resampler/servo in the bridge, exclusive out, block 256, real content ≥10 min | Measure ear-latency + glitches/min. **The cheap `queue`-term (Lever A) win, taken now.** It does **not** gate Track B — Track B removes the content/A-V buffer A1 can't touch |
| **B-pre** | **VM** | Pseudo-K11 (steady user-mode consumer) drives a SYSVAD-fork's **pull-driven** position; a real 48 kHz app renders 7.1 into it (shared mode) | **`audiodg` tolerates the bursty external position**: no silence-insert/disconnect, monotonic `IAudioClock`. **This is the make-or-break feasibility gate for Track B** — if it fails, the content/A-V win needs a different clocking model and VoiceMeeter stays until one exists |
| **B1** | VM | Engine reads all 8 channels; DSP bit-correct vs offline (R6); stall-watchdog + engine-absent behavior exercised | Correctness + R5 safety proven in the sandbox |
| **B2** | **Host + K11** | Slave to the real K11 exclusive callback with exact USB-async frame accounting; **real media file + real game** ≥10 min | **0 overflow + 0 underrun at the app-facing boundary** (not just our FIFO) + worst-case per-callback DSP timing fits under GC/scheduling jitter |
| **B3** | **Host + K11** | Measure vs the A1 baseline | Numeric target below |

**Numeric success target (pre-committed, per review).** Both targets are **latency** (the
single goal):
- **Track A — the `queue` term (Lever A):** live ear-latency p50 **≤ 50 ms** at **≤ the
  current ~5 glitches/min** on real content.
- **Track B — content / A-V latency (Lever B):** cuts **content/A-V latency by ≥ 50 ms**
  (removing most of the ~128 ms capture buffer), holding ear-latency ≤ 50 ms and glitches
  ≤ 5/min on real content. *Bonus (not the pass/fail):* the 7.1 device is owned with no
  VoiceMeeter in the path (FR-2/§9/§10).

**Decision rule.** Ship Track A once it hits its target — that is the `queue`-term answer,
taken now. Then build Track B: its go/no-go is **B-pre passing** (the owner already feels
the content/A-V latency it removes, so no further intent gate is needed). Track B's success
is the **content/A-V cut**, *not* re-winning head-move→ear (structurally out of reach — a
non-goal). The one outcome that kills Track B is **B-pre failing** (audiodg rejects the
pull-driven position): then the content/A-V win needs a different clocking model and
VoiceMeeter stays until one exists.

## Files to change / add

- `docs/rfc/rfc-single-clock-virtual-driver.md` — this RFC.
- `docs/backlog.md` — supersede the invalid B8.7 (EqualizerAPO premise); add Track A
  (resampler/servo) and the gated Track B (driver) as blocks with these milestones.
- `docs/research-journal/` — an entry recording the architecture decision, the baseline
  correction (bench 35–46 vs live ~70), and the EqualizerAPO-routing correction (credit
  the owner's channel-count argument).
- *(Build phase, post-approval — not in this RFC's commit):* Track A resampler in
  `apps/`; Track B `driver/` subtree + the Rust RT core behind `FrameSource`/`ClockSink`.

## Edge cases

- **App content clock ≠ K11:** the reconciliation lives in `audiodg` (shared) or the app
  (exclusive) — **not removed**; B-pre/B2 must prove it doesn't glitch on real content.
- **Engine stalls / crashes / not running (R5):** stall watchdog → silent/free-run so the
  device never wedges apps; the virtual device is hidden when the engine is absent.
- **DSP overrun:** decouple `advance` from DSP completion (advance on measured K11 delta;
  run DSP as a separate stage) so one deadline miss doesn't corrupt the clock.
- **USB-async feedback:** account the *actual* frames consumed per callback, never a
  nominal constant, or slow drift returns.
- **Exclusive-open fails:** do **not** "fall back to shared and still call it single
  clock" (v1's error — shared reintroduces the engine's reconciliation). Fall back and
  say so.
- **44.1 kHz content:** shared-mode apps are resampled to 48 kHz by `audiodg` before the
  endpoint, so a 48 kHz-only device still plays them; only exclusive-mode 44.1 clients
  fail to open. Pin 48 kHz, document, test one 44.1 file through the shared path.

## Alternatives considered

1. **Ship Track A only (resampler over VoiceMeeter).** Clears the live `queue` term
   cheaply. Cannot touch the ~128 ms content/A-V buffer or remove VoiceMeeter, so for the
   owner's mixed video/game use it leaves real felt latency on the table. **Kept as the
   primary first step and the Track-B latency baseline — but not the whole answer.**
2. **EqualizerAPO-VST (old B8.7).** Invalid (stereo K11; APO can't route). Superseded.
3. **In-kernel DSP.** Rejected — far more dangerous, no benefit over the user-mode Rust
   core (~5–25× RT average).
4. **ASIO output to the K11** (FlexASIO). Shrinks only the output buffer; already
   approximated by exclusive mode (B0.5); not a capture-path fix.
5. **Lower the VoiceMeeter/VB capture buffer** (6144 → ~1024). Cheap partial content-
   latency win with no code; worth trying in A0 before Track B (owner previously declined
   to lower it — revisit).
6. **Do nothing.** Shared mode is tolerable (~5 glitches/min, mostly masked). Legitimate;
   rejected only because the owner wants the floor explored.

## Open questions (inputs needed from the owner)

- **Exact Windows build** (10 vs 11 + version) — decides ACX availability / WDK baseline.
- **Is a Hyper-V VM available** for the dev loop + the B-pre `audiodg` pre-test, and a
  **spare boot/SSD** for host B2/B3?
- **Acceptance of residual test-signed-driver risk** on the host (B2/B3 only), and that
  **anti-cheat games won't run** with Secure Boot off.
- **K11 specifics:** stock USB Audio Class 2 (no vendor ASIO)? minimum exclusive-mode
  buffer? async-feedback behavior?
- ~~**What does the owner most want** — lowest millisecond, or VoiceMeeter gone?~~
  **Resolved (2026-10-20):** latency is the goal; VoiceMeeter removal is a means. Playback
  is mixed (video/games/music), so content/A-V latency counts → Track B justified on
  latency (Lever B). Folded into Goals + the decision rule.

### Empirical-validation note (per the review workflow)

The load-bearing *physical* claims — `audiodg`'s tolerance of a bursty external WaveRT
position, the live drift-vs-jitter split, USB-async frame behavior, worst-case DSP
callback timing — **require the Windows host + K11 (and a VM for the pre-test)**. This
review ran on Linux/WSL with no such hardware, so none could be probed here; they are
scheduled as the earliest gates (A0, B-pre, B2) precisely because they are unproven.

---

## Critic feedback incorporated

Round 1 — 5 critics (panel N=5, within cap): `failure-mode-analyst`, `boundary-critic`,
`design-minimalist`, `socratic-challenger`, `ambition-amplifier`.

**Premise falsified → converged after round 1 (no round 2), restructured around reality**
(per the workflow's "converge early if the load-bearing claim is falsified"):

- **"Single clock end-to-end" is false** (failure-mode-analyst, corroborated by
  socratic-challenger & boundary-critic): slaving the virtual device makes only the
  *output FIFO* drift-free; the app content clock is relocated into `audiodg`/the app, not
  removed. → Headline claim deleted; v2 claims only a drift-free output FIFO and re-scopes
  the driver's value to removing the VoiceMeeter capture buffer/burst-pacing.
- **Baseline was wrong** (socratic-challenger; I verified in journals 44/45/48): live
  floor is **~70 ms** (not the 35–46 ms bench; 35 ms was the reverted exclusive mode). →
  Problem section + numeric targets rewritten around the live ~70 ms and its 31 ms
  `queue` term; head-move→ear demoted to an explicit **non-goal** (it is structurally
  unaffected — failure-mode-analyst & ambition-amplifier both concur).
- **M3 was vacuous** (failure-mode-analyst): the old gate measured our FIFO, clean by
  construction. → New gates measure the **app-facing boundary on real content**
  (B2/B3), with an `audiodg`-tolerance **VM pre-test (B-pre)** that front-loads the real
  risk off the host.
- **Invert the order — cheap first** (design-minimalist): → Track A (diagnose + resampler
  servo) now precedes the driver as the cheap Axis-1 win; the driver is **not** gated on
  beating it (consensus-attack correction — see below).
- **Seam under-specified / engine overloaded** (boundary-critic): → two explicit planes
  (SPSC data ring + narrow `advance()`), a Rust RT core / Python control-shell split, a
  `FrameSource`/`ClockSink` trait shared with the fallback, kernel-owned overflow policy +
  stall watchdog.
- **Blast radius / engine-as-clock** (failure-mode-analyst & boundary-critic): → R5 +
  engine-absent/crash/overrun edge cases; advance decoupled from DSP; watchdog.
- **USB-async frame accounting** (failure-mode-analyst): → exact per-callback frames, not
  a constant.
- **SYSVAD clocking is the throwaway part** (failure-mode-analyst): → scoped as plumbing
  only; the position/clock subsystem is new and the primary risk; read it before M1.
- **No pre-committed success threshold** (socratic-challenger & design-minimalist): →
  numeric target added (ear p50 ≤ 50 ms AND content cut ≥ 50 ms AND ≤5 glitches/min).
- **44.1 kHz / test-signing & anti-cheat** (socratic-challenger, design-minimalist): →
  edge case (audiodg resamples 44.1 in shared) + Secure-Boot/anti-cheat limitation noted.

**Adversarial-pair resolution (`ambition-amplifier` vs `design-minimalist`).** They
conflicted on whether to build the driver at all. I sided with **design-minimalist on
sequencing** (resampler-first, driver gated on a measured residual) because the live
baseline shows head-move→ear is already near target and the driver's unique value is
unproven — but with **ambition-amplifier on the driver's *reason to exist*** (if built,
its payoff is content/A-V latency + VoiceMeeter removal, made the headline, not
head→ear) and on **moving the make-or-break proof into the VM** (the B-pre pseudo-clock
test). On ambition's "make the format/device generic (2/6/8 ch) for FR-2": **partially
accepted** — code the source behind the `FrameSource` trait so it isn't K11-hardcoded
(cheap). I first dropped the FR-2 framing (per boundary-critic & design-minimalist, since a
no-free-run engine-coupled PoC device is not the *product* device) — but the
**consensus attack reversed that**: the PoC device need not be the product device to
**de-risk FR-2's "custom driver later" (§10)**; that strategic value is real and was
wrongly deleted. v2-final restores FR-2/§9/§10 as Axis 2 while keeping the honest caveat
that this PoC device is a stepping stone, not the shippable one.

**Consensus attack (`general-purpose`) — did NOT survive; shared blind spot found and
incorporated.** All five critics reasoned on a single axis (latency delta), collapsing a
two-axis decision. They conceded the driver's unique win is content/A-V latency — which is
*VoiceMeeter's* buffer — then recommended the path (resampler) that keeps VoiceMeeter and
forecloses it; and they ignored FR-2/§9/§10, under which the owned driver is justified
independent of the millisecond delta (verified: `requirements.md` §9 licensing, §10
"biggest native risk … custom driver later"). → Goals split into Axis 1 / Axis 2; Track B
is no longer gated on a latency residual but on **B-pre feasibility + the owner's Axis-2
intent**; the decision rule and A1 gate were rewritten so "ship the resampler" no longer
silently cancels the driver. The panel's *technical* corrections (claim overstated, M3
vacuous, blast radius) stand and shaped the rest of v2.

**v3 — owner resolved the open fork (2026-10-20).** The consensus attack correctly forced
the two-axis split to be *surfaced* rather than decided silently; presented as an explicit
tradeoff (per the workflow's intent-preservation step), the owner chose **latency as the
goal, VoiceMeeter-removal as a means**, with **mixed video/game/music playback**. This did
not reverse the attack — it *grounded* it: the driver and resampler remain non-substitutes,
but now because they attack **different latency terms** (`queue` vs content/A-V), not
because one serves a separate dependency axis. Track B stays justified (its content/A-V
cut is felt latency for this owner) and its gate simplifies to **B-pre feasibility alone**
(the intent half is answered). FR-2/§9/§10 demote from co-equal justification to a recorded
**strategic bonus**. Goals, Track A/B framing, milestones, decision rule, and the final
Open Question were updated accordingly (v3 note at top).

_Invocation-log entries:_
- `ambition-amplifier 2026-10-20: novel finding` — content/A-V latency is the driver's
  only unique win; VM synthetic-clock pre-test; generic format table.
- `assumption-archaeologist` — not invoked (no ADR-justified behavior change; this
  reverses a backlog item, not an accepted ADR).
- `general-purpose 2026-10-20: consensus-attack — shared blind spot found` (panel
  collapsed a two-axis decision onto latency; FR-2/§9/§10 dependency axis restored — see
  the paragraph above).

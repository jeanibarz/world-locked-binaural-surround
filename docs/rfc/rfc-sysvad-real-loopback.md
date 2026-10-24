# RFC: Make SYSVAD carry real rendered audio to the head-tracker (Track B data plane)

**Status:** **Validated end-to-end on host (v5)** — data plane is the **render→capture ring
bridge** (a corrected Plane M); Plane T superseded. Real music world-locks through SYSVAD,
VoiceMeeter-free.
**Date:** 2026-10-24
**Author:** Jean Ibarz (with Claude)

> Child of [`rfc-single-clock-virtual-driver.md`](rfc-single-clock-virtual-driver.md)
> (Track-B parent). The parent settled *why* we own a virtual 7.1 device (remove
> VoiceMeeter's ~128 ms capture buffer + burst pacing; FR-2/§9/§10 bonus), R1–R6, the
> VM-first safety model, and "SYSVAD is plumbing; its clocking is the risk." Its
> milestone **B-pre** (pull-driven position tolerated by `audiodg`) is **done** (journal
> 74). Its milestone **B1 — "engine reads all 8 channels"** is **not**, and that is this
> RFC's subject: the *data plane*.
>
> **v2 note (post-review).** v1 asserted the "continuous sine" was SYSVAD's driver
> *capture-pin* tone and proposed replacing it with a copy of the render DMA. A 5-critic
> round **falsified that premise from the code**: the app captures via **OS WASAPI
> render-loopback** (`get_loopback_device_info_generator()` →
> `AUDCLNT_STREAMFLAGS_LOOPBACK`, `apps/voicemeeter_passthrough.py:434`,
> `spikes/bpre-vm/b2_consumer.py:262`), which taps `audiodg`'s render mix and **never
> reads a driver capture pin**. So editing the capture pin could change nothing the app
> receives. v2 is restructured so the **first deliverable is a zero-code host diagnosis**
> that decides *which plane is broken*; the kernel work is gated behind it and, if it is
> still needed, hardened against the correctness/safety defects the panel found.
>
> **v3 note (consensus attack).** The attack caught a blind spot the whole panel shared:
> "OS WASAPI render-loopback" vs "the driver's KS loopback pin" is a **false dichotomy** on
> a WaveRT device that ships its own loopback pin — Windows services the loopback flag
> *from that driver pin*, so they are the **same pin**, and the driver's per-tick fill
> (`m_bCapture → WriteBytes`, patch line 117) **is** what the app receives. So v1's plane
> was not wrong; the panel's "it's OS loopback → fix app-side" exit was. The genuinely
> load-bearing, never-tested claim is one level deeper — **does the real per-app-volume
> mix reach this pull-driven device's render DMA at all?** B-pre ("done") proved only the
> **clock** plane with a **synthetic sine**; the **data** plane was never exercised with
> real content. v3 re-centers Phase 0 on that question (Q1 below) and corrects the table.
>
> **v4 note (owner decision, 2026-10-24).** Asked to choose the data plane, the owner picked
> **Plane T (tick-return)**. So Plane T is the chosen design; Plane M (capture-pin mirror)
> is retained only as a fallback / cheap VM fidelity gate. This does **not** skip Phase 0:
> Plane T returns the render DMA's just-consumed window, so Q1 (does the real mix reach that
> DMA?) is still the gate — the Phase-0 render-DMA dump **is** Plane T's go/no-go.
>
> **v5 note (implemented + validated end-to-end, 2026-10-24).** Phase 0 and both planes were
> built and run on the host and VM (journals 77–80). The outcome rewrote the plane choice:
>
> - **Plane T was built and loads cleanly, but its premise does not hold.** Driving the
>   render stream from the pull tick to return its consumed window breaks `audiodg`'s render
>   path — the render stream must stay a normal (non-pull) timer stream for the engine to
>   fill it. Plane T is therefore **superseded**, not the data plane. Its section/MDL
>   machinery (`make_plane_t.py`) is **banked**.
> - **Q1 resolved — direction-dependent.** In the **headless VM** the render DMA is silent
>   regardless of config: `audiodg` with no real audio session renders silence into a
>   DAC-less virtual endpoint (journal 79, ruled out mute/volume with `pycaw`). On the
>   **host**, which has a real audio session, `audiodg` **does** deposit the real per-app mix
>   into the device render DMA — **Q1 = yes on the host.** This retroactively explains every
>   "empty DMA" reading in the investigation: the VM never put real audio there to read.
> - **The data plane is the render→capture *ring bridge* (a corrected Plane M).** Per web
>   research (journal 78: the MS-documented WaveRT loopback fix + the GPL OpenAudioCable
>   `CVCable` pattern), the render stream's `WriteData` publishes its PCM into a device-wide
>   byte ring, and the loopback pin's fill reads that ring **instead of** `GenerateSine`
>   (silence on under-run). This is Plane M's *intent* — feed the loopback pin from the
>   render stream — realized through the stream's **own writes**, not a cross-stream DMA
>   scrape, so it carries none of Plane M's lifetime/lock hazards. Built from the MIT SYSVAD
>   base by `make_ring_bridge.py` (idempotent patcher; `RingWrite`/`RingRead`/`RingEnsure`).
> - **End-to-end validated on the host (journal 80).** Installed the ring build (no bugcheck,
>   device OK), played real audio: the WASAPI render-loopback carries the **real rendered
>   mix** (signal only where the content is, tracks content + per-app volume), at **correct
>   pitch** — the transient "~7 % pitch" was a measurement artifact (the owner's concurrent
>   music mixing into the probe; with music paused, three runs read a steady 500.0 Hz).
>   **Owner listen test passed:** real music world-locks through SYSVAD with `--pull-sysvad`,
>   **VoiceMeeter-free**; very few dropouts at 512-block / 20 ms cushion, fewer still at
>   40 ms (consistent with the GIL-fix + cushion findings, journals 74–76).
> - **D5 latency gate — informal pass, formal measurement is the one follow-on.** The owner
>   confirms clean world-locked audio by ear; a QPC-stamped p50/p99 render→consumer
>   measurement (B1-lat) was not run and remains the single open validation item.
>
> So the settled data plane is the **render→capture ring bridge**; Plane T and the SPSC ring
> stay banked. The sections below are preserved as the design record that led here; read the
> plane discussion through this note.

---

## Problem

`--pull-sysvad` is wired and clean (single clock, block 512, 40 ms cushion, GIL fix —
journals 74–76) but carries **the wrong signal**. Host test (2026-10-24, issue #112):
capturing the 8-ch `Haut-parleurs (Virtual Audio Device (WDM) - Tablet Sample) [Loopback]`
endpoint while Chrome/Spotify renders into it, the captured signal is a **constant
mid-level that does not track the music and does not change with the app's volume**, and
is silent until playback starts.

**What v1 got wrong.** v1 read that as SYSVAD's stock capture-pin tone generator
(`LoopbackCaptureToneFrequency = 3000`) and proposed mirroring the render DMA into the
capture pin's fill. But the app does **not** read a capture pin. It opens an OS **WASAPI
render-loopback** on the render endpoint (`AUDCLNT_STREAMFLAGS_LOOPBACK`), which the
Windows audio engine (`audiodg`) synthesizes by copying the **render mix** — the same
bytes `audiodg` sends to the device. Two independent critics confirmed this against
`b2_consumer.py:262` and `voicemeeter_passthrough.py:434`.

**So the symptom is a paradox that must be explained before building anything.** A true
render-loopback returns the engine's post-mix render buffer *or* silence — it should carry
the music, never a driver capture-pin tone. A constant, volume-independent signal coming
through render-loopback means one of:

- **(H1) It is not render-loopback.** PyAudioWPatch resolved to a *distinct* SYSVAD KS
  **capture** endpoint (a recording device), not the render-endpoint loopback shadow. Then
  v1's capture-pin plane is right and a (hardened) kernel mirror is the fix.
- **(H2) The app's audio never reaches this device's render mix.** `audiodg` isn't
  rendering Chrome into the SYSVAD endpoint (wrong default device, topology, or the
  pull-driven endpoint never enters a normal mixed RUN state), and something else supplies
  the constant signal. Then the fix is render-topology / device-selection — **no kernel
  change** — or a fundamentally different capture path.
- **(H3) Render-loopback does not function on this pull-driven virtual device.** The engine
  may not produce a loopback mix for an endpoint with no real DAC whose only consumer is
  our own pull clock. Then neither "fix the capture pin" nor "fix routing" is the answer; a
  driver-supplied data plane (capture-pin mirror **or** tick-return, below) is mandatory.

v1 committed 100 % to H1 while listing the deciding question as a trailing "open
question." v2 made deciding among H1/H2/H3 the first gate — but the consensus attack
showed **H1-vs-H2 is a false split**: on this WaveRT device the OS loopback flag is
serviced by the driver's own loopback pin, so "app reads the driver pin" and "app reads
WASAPI render-loopback" are the **same thing**, and the driver's fill is what the app
gets. That collapses the real decision to one never-tested question:

- **(Q1 — the load-bearing claim) Does the real, per-app-volume-scaled mixed PCM reach
  this device's render DMA at all while real music plays?** B-pre ("done") proved only the
  **clock** plane (position advances, `audiodg` doesn't disconnect) using a **synthetic
  sine** (`pseudo_k11.py`; journal 74 "plays a clean sine"). The **data** plane — that
  `audiodg` deposits a real mix into this *pull-driven, DAC-less* host-pin DMA — has
  **never** been exercised with real content. The host symptom (a signal blind to both the
  music *and* the per-app volume) is consistent with the DMA **not** holding a real mix.
- **(Q2) If the mix is in the DMA**, route it to the loopback fill (Plane M) or return it
  from the tick (Plane T). **If it is not**, no capture tap can help — the problem is
  upstream: the parent RFC's **B-pre data-plane** is actually unproven (only its clock
  half passed), and the fix is "make `audiodg` render a real mix into this device," not a
  data-plane patch.

So Q1 is the **first gate**, and the old H2 "fix app-side, no kernel" exit is deleted — it
rested on the false split.

## Goals / Non-goals

**Goal:** the head-tracker receives the **real rendered 7.1 stream** apps play into the
SYSVAD device, at a **measured** content latency at or below VoiceMeeter's, so
`--pull-sysvad` world-locks live audio (music/film/games) with **no VoiceMeeter**. This
realizes the parent's **Lever B** and **B1**.

**Non-goals (inherited):** head-move→ear latency; removing the app content clock (`audiodg`
reconciles it); any device but K11; any rate but 48 kHz; WHQL/Secure-Boot distribution;
in-kernel DSP; generalization beyond 7.1.

## Requirements

Inherits R1–R6. Data-plane specifics:

- **D0 — Diagnose first.** No kernel code until the host diagnosis (Phase 0) decides
  H1/H2/H3. The design below is contingent on that result.
- **D1 — Fidelity.** If a driver data path is built, it delivers the device's render-mix
  frames bit-for-bit, verified against the **render-DMA WAV dump as the oracle** (not "vs
  what the app played" — `audiodg` upmixes/volumes before the DMA, so the app's source is
  not a valid reference).
- **D2 — Alignment.** Mirrored frames correspond to the render frames consumed in the same
  tick, with an **explicit render-before-capture ordering** and **re-anchoring** to the
  render stream's own consume position each time it enters RUN (the pins are **not**
  co-resident — the capture side advances every tick, the render side only while RUN).
- **D3 — Clean fallback.** No render stream, or a render stream that under-ran (stale ring
  PCM): deliver **silence**, never a tone and never a repeated stale lap. Use a
  **take-and-clear** scratch (copy, then zero the source window).
- **D4 — Safety (R4/R5).** The real hazard is the pull thread's copy vs. the **PASSIVE**
  teardown path (`PullUnregister` on stop/close) freeing `m_pDmaBuffer`. Guard **buffer
  validity**, not just a pointer; publish the **lock hierarchy**; and do the clear inside
  the pull thread's own critical section so no third lock is introduced. Engine stall must
  not wedge apps rendering into the device (carry over the parent's **stall watchdog**).
- **D5 — Latency gate.** A pre-committed, **measured** content-latency target (below), or
  the RFC cannot fail its own premise. Fidelity alone does not justify the driver.
- **D6 — Reversible.** A named source selector (`tone | mirror | silence`), tone as a
  compile-time A/B, expressed as a clean diff in the vendored patch.

## Phase 0 — zero-code host diagnosis (the first and decisive deliverable)

Run on the host, **no driver change, no kernel code**. Decides H1/H2/H3 and therefore
whether any kernel work happens at all. (All three need the Windows host + SYSVAD; none can
run on WSL — see the empirical-validation note.)

1. **Render-DMA oracle dump — the gate (answers Q1).** With music playing into SYSVAD,
   trigger the **existing** `ReadBytes`/`SaveData` render-DMA WAV dump (already present;
   `g_DoNotCreateDataFiles=0`) and inspect the WAV.
   - Contains the real music (tracks content *and* per-app volume) ⇒ **Q1 = yes**, the mix
     reaches the device DMA → route it (Plane M or T).
   - Silent / tone / constant ⇒ **Q1 = no**, the mix never reaches this pull-driven device
     → a parent-RFC **B-pre data-plane gap**; fix that first (table above). *Has anyone ever
     looked at this WAV with real content? B-pre only ever dumped the synthetic sine.*
2. **Silence probe (sanity check, not a discriminator).** On the current build set
   `g_DisableToneGenerator = 1` and capture while playing music. Expected: the capture goes
   **silent**, confirming the app's `[Loopback]` is **driver-fed** (the loopback pin's fill
   is what the app receives — the false-dichotomy correction). This confirms Plane M would
   reach the app *if* Q1 is yes; it does **not** imply an app-side fix.
3. **Endpoint identity (informational).** Enumerate what
   `get_loopback_device_info_generator()` resolves to for the SYSVAD speakers (its
   `DataFlow`/endpoint GUID) — useful detail, but per the v3 correction it is the driver
   loopback pin either way, so this does not change the plane.

**Phase-0 decision table (keyed on Q1 — the render-DMA dump is the gate; the silence probe
is only a sanity check that the app's loopback is driver-fed, not a driver-vs-app-side
discriminator — that split was false):**

| render-DMA dump, real music playing | ⇒ | next |
|---|---|---|
| **has the real music** (tracks content *and* per-app volume) | Q1 = yes, the mix reaches the device DMA | **route it** — Plane M (fill the loopback pin from the DMA) or Plane T (tick-return); choose by the **D5 latency** measurement |
| **silent / tone / constant** (ignores content & volume) | Q1 = no, the mix never reaches the DMA | **stop — this is a parent-RFC B-pre *data-plane* gap**, not a capture tap. First make `audiodg` render a real mix into this pull-driven device (endpoint role/topology — the thing B-pre's synthetic sine never tested), then re-run Q1. No loopback/tick work can precede this. |

## Design — contingent on Phase 0

Two candidate data planes; Phase 0 + the D5 latency measurement choose between them. The
parent's SPSC shared ring stays **deferred** (heaviest; no scaffolding carried into this
patch).

### Plane M — capture-pin mirror (only if Phase 0 = H1)

Keep the app-side WASAPI-loopback read unchanged; change where the capture fill gets its
bytes — **owned by the existing pull orchestrator**, not a new miniport pointer:

- The pull thread already walks `g_PullList` once per tick under `g_PullSpinLock` and calls
  `PullAdvance` per stream (patch lines ~107–274). Within one tick, **advance the render
  stream first**, snapshot the exact `[old, new)` byte window it consumed into a
  **device-level static scratch** (~480 fr × 8 ch × bytes ≈ 15 KB; never freed → no pointer
  lifetime, no third lock), then the capture fill **memcpy**s from the scratch (replacing
  `GenerateSine`) and **zeros the consumed source window** (take-and-clear → D3 silence on
  under-run/absence, no stale lap).
- **Preconditions, asserted in-driver (degrade to silence if violated):** render and capture
  pins share identical format, `m_ulNotificationIntervalMs`, and `m_ulDmaMovementRate`
  (else the per-tick advances and wrap moduli diverge — a silent tear/skew, not a crash).
- **Ordering:** render-before-capture made explicit (list head or a direct call), not left to
  registration order (today a 10 ms phase error depending on insert order).
- **Lifetime/locks:** clear the scratch's source linkage in the same critical section that
  precedes freeing the render `m_pDmaBuffer`; publish `g_PullSpinLock → m_PositionSpinLock`
  as the only nesting; no new lock. Snapshot under lock, bulk-copy outside if the lifetime
  model allows (bound the DISPATCH dwell — the copy *is* the work, ~15 KB/tick/stream).
- **Watchdog drift:** under a K11 stall the pull thread free-runs on its 100 ms timeout
  (anti-wedge, R5); document that render+capture then advance together but drift from the K11
  cadence — acceptable during xruns.

### Plane T — tick-return (likely preferred; decided by D5)

The engine already calls `KookrPullTick` once per period and the driver advances by exactly
the consumed frames. Have that call **return the just-consumed 480-frame render window** in
the same round-trip (the parent's "narrow `advance()` clock plane" **with a data return**).
This **removes** the loopback pin, the `audiodg` capture hop and its period quantization, the
Python/PortAudio capture client (a candidate source of the current 40 ms cushion), the
cross-stream `DISPATCH` copy, and the H1/H2/H3 ambiguity entirely (it never depends on what
render-loopback resolves to). It is **smaller kernel-side than Plane M** and structurally
phase-locked (the returned data *is* the advance). Cost: an app-side change (read from the
tick round-trip instead of a WASAPI loopback device) and confirming the tick transport can
carry a payload.

**Chosen (owner, v4): Plane T is the data plane.** It is phase-locked by construction (the
returned data *is* the advance), avoids the loopback-pin/capture-pin question entirely, and
is the lowest-latency path (no `audiodg` capture hop, no Python/PortAudio capture client).
Plane M is kept only as the fallback / cheap VM fidelity gate (and it keeps a working
loopback endpoint for tools like OBS). **Both still require Q1** — Plane T returns the
render DMA's consumed window, so if Phase 0 shows the real mix never reaches that DMA
(Q1 = no), Plane T has nothing to return and the problem is the parent's upstream B-pre
data-plane gap, not a tick-return patch.

**Plane T's one unknown to confirm before coding:** whether the `KookrPullTick` transport
can carry a return payload (today it is a bare auto-reset event). If not, Plane T needs a
small synchronous IOCTL (tick-with-data) alongside/replacing the event — a bounded addition,
still far smaller than the SPSC ring. This is the first implementation question once Q1 passes.

### Complementary lever — report true latency (A-V sync)

Independent of which plane: the driver owns the position/QPC pair and
`KSPROPERTY_RTAUDIO_GETHWLATENCY`. Folding the engine FIFO + cushion + K11 time into the
reported latency lets players (mpv/MPC) delay video to match — fixes A-V sync at the source
rather than only by shrinking buffers. Note as a follow-on, not a gate.

## Validation (VM first, host last — R4/R5)

| # | Where | What | Gate |
|---|-------|------|------|
| **Phase 0** | Host | silence probe + render-DMA dump + endpoint identity | Decide H1/H2/H3 (table above). **Blocks all kernel work.** |
| **B1-vm** | VM | chosen plane delivers the rendered content; **oracle = loopback/return WAV vs the render-DMA WAV** (not vs the app's source) | Bit-correct vs the render-DMA oracle; silence on absence/under-run (deliberately starve `audiodg`); **different-period** render/capture sub-case degrades to silence, not tears |
| **B1-lat (D5)** | VM + Host | QPC-stamp a submitted impulse/ramp; stamp arrival at the consumer; p50/p99 render→engine | Content latency **≥ 50 ms below VoiceMeeter's ~128 ms** (parent B3), else Plane T is required / the ring reconsidered |
| **B1-safety** | VM + WinDbg | repeated render start/stop **while capture keeps running**; close render app mid-copy; stop consumer mid-stream | No bugcheck/hang; re-anchors (no carried offset); silence when render gone; clean unload with device idle (journal 70) |
| **B2** | Host + K11 | real media + real game, `--pull-sysvad`, ≥10 min | Hear real audio world-locked; 0 under/overflow at the app boundary; B1-lat target holds; no VoiceMeeter |

## Edge cases

- **Which plane the `[Loopback]` device is** — Phase 0 resolves; the design branches on it.
- **Render under-run (stream RUN, not freshly written):** stale ring PCM → take-and-clear
  yields silence, not a repeated lap (D3).
- **Render/capture non-co-residence:** capture advances every tick, render only while RUN →
  re-anchor on render RUN; never assume capture's counter tracks render's (D2).
- **Different period/format between pins:** assert equality; degrade to silence (D1/D2).
- **Teardown vs copy:** PASSIVE free vs DISPATCH copy → guard buffer validity in the
  pull-thread critical section (D4); non-null pointer ≠ valid buffer.
- **Stream replacement** (audiodg opens the new render stream before closing the old):
  compare-and-clear (a stream clears the link only if it still owns it) → no silent drop.
- **Multiple render pins:** `audiodg` mixes before the device in shared mode → the render DMA
  already holds the mix; last-RUN-wins into the scratch otherwise. Not pre-built.
- **Watchdog free-run under stall:** documented drift from the K11 cadence (D4).
- **44.1 kHz:** `audiodg` resamples to 48 kHz before the device; DMA is already 48 kHz.
- **Engine crash/stall:** stall watchdog → device doesn't wedge apps (R5).

## Alternatives considered

1. **SPSC shared-ring data plane** (parent's original). Heaviest; the mirror/tick-return
   reuse what the render DMA + existing planes already provide. **Deferred, no scaffolding**
   — revisit only if B1-lat fails on both M and T. (Honest delta vs a ring ≈ one capture
   period + wake jitter; it does not remove `audiodg`'s render-side write-ahead.)
2. **Disable the tone and assume render-loopback "just works."** This is exactly Phase 0's
   silence probe — the *decisive experiment*, not a half-fix. Promoted into Phase 0.
3. **Keep VoiceMeeter.** The thing being removed. Rejected.
4. **Re-read `audiodg`'s mix in kernel.** No supported hook; the render DMA *is* that mix's
   device copy. Rejected.
5. **Mirror into the capture pin via a miniport `activeRenderStream` pointer + new
   `KSPIN_LOCK`** (v1's mechanism). Rejected: duplicates the existing `g_PullList` lifetime
   registry (drift + journal-70 teardown class), a cross-stream private-DMA read with an
   unstated lock order (deadlock surface). Replaced by pull-orchestrator ownership + scratch.

## Open questions (host inputs)

- Phase 0's three results (H1/H2/H3) — everything branches on them.
- If a driver plane is built: can the `KookrPullTick` transport carry a return payload
  (Plane T), or is it a bare auto-reset event only (then M, or a new IOCTL for T)?
- Exact SYSVAD position/advance API and the render-pin lifetime vs the capture pin (for M's
  re-anchor + lifetime guard) — read from the host source before coding.
- Capture-pin format table: the patch widens only the *render* `wavtable` to 8 ch; if H1,
  the capture pin needs the matching 7.1 format.

### Empirical-validation note (per the review workflow)

The load-bearing claims — **which plane the app captures (H1/H2/H3)**, whether the render
DMA holds the real mix, whether render-loopback functions on a pull-driven virtual device,
and all alignment/lifetime behavior — require the **Windows host + SYSVAD source/VM**. This
review ran on Linux/WSL with only the vendored patch; the mandatory post-round-1
`design-experimenter` probe **could not run here** (no Windows/SYSVAD/audiodg). That is
exactly why Phase 0 is the first gate and blocks all kernel work: the premise must be
settled on the host before code.

---

## Critic feedback incorporated

Round 1 — 5 critics (panel N=5, within cap): `failure-mode-analyst`, `boundary-critic`,
`design-minimalist`, `socratic-challenger`, `ambition-amplifier`. **Converged after round 1
(no round 2): the load-bearing premise was falsified from the code**, so the correct
response was to restructure around reality, not iterate (per the workflow).

- **Premise falsified — wrong plane** (socratic & failure-mode, independently, from
  `b2_consumer.py:262`/`voicemeeter_passthrough.py:434`): the app captures via OS WASAPI
  render-loopback, not a driver capture pin. → v2 reframed around H1/H2/H3 and a **Phase-0
  host diagnosis** (silence probe + render-DMA oracle + endpoint identity) that gates all
  kernel work; v1's capture-pin mirror demoted to the contingent "Plane M (only if H1)".
- **Ownership / cross-stream coupling** (boundary & minimalist & failure-mode): v1's miniport
  `activeRenderStream` pointer + new `KSPIN_LOCK` + cross-stream private-DMA read duplicated
  the existing `g_PullList` registry and created an unstated lock order. → Plane M is owned
  by the **pull orchestrator** with a **device-level take-and-clear scratch**; no new lock;
  moved to rejected-alternative #5.
- **Intra-tick ordering** (failure-mode, boundary, minimalist): advance order was list-insert
  order → silent 10 ms skew. → explicit render-before-capture + snapshot the consumed window.
- **Format/period equality** (failure-mode): "480 fr both pins" assumes equal period/format/
  movement-rate and one wrap modulus. → asserted precondition, degrade to silence, B1-vm
  mismatched-period sub-case.
- **Teardown vs buffer lifetime + lock hierarchy** (failure-mode): real race is DISPATCH copy
  vs PASSIVE free; pointer≠buffer-valid. → guard buffer validity in the pull-thread section,
  published nesting, no third lock.
- **Non-co-residence** (failure-mode): capture runs without render → re-anchor on render RUN.
- **Under-run stale data** (failure-mode & ambition): → take-and-clear.
- **Missing latency gate** (ambition): fidelity-only RFC can't fail its own premise. → **D5 +
  B1-lat** QPC-stamped, p50/p99, parent's ≥50 ms cut; Alt-1 trigger made numeric.
- **Tick-return data plane** (ambition): a lighter option that removes the loopback hop + the
  H1/H2/H3 ambiguity. → added as **Plane T**, recommended when the app can't use
  render-loopback; the complementary **report-true-latency** (`GETHWLATENCY`) lever added.
- **Oracle for D1** (minimalist & socratic): can't compare to the app's source (`audiodg`
  upmix/volume). → oracle = loopback/return WAV vs the **render-DMA WAV**.
- **R5 stall watchdog** (boundary & ambition): carried over; engine stall must not wedge apps.
- **Ring deferral reasoning** (all): kept deferred, no scaffolding; delta stated honestly.

**Adversarial pair (`ambition-amplifier` vs `design-minimalist`).** Both agreed: drop the
SPSC ring, keep the data path minimal, no app change *if avoidable*. They conflicted on
scope: minimalist wanted fidelity-only with policy left to implementation; ambition insisted
on the latency gate and deciding plane/policy now. **I sided with ambition on the latency
gate and plane decision** (a driver that can't measure its content-latency win does not
justify itself — the parent's whole reason for Track B) **and with minimalist on the kernel
mechanism** (scratch + take-and-clear + no new lock, not v1's pointer/lock machinery). Both
independently favored tick-return as smaller — reflected in Plane T.

_Invocation-log entries:_
- `ambition-amplifier 2026-10-24: novel finding` — dropped latency gate; tick-return data
  plane; report-true-latency for A-V sync.
- `assumption-archaeologist` — not invoked (implements the parent RFC's B1 milestone; no
  ADR-justified behavior reversed).
- `design-experimenter` (post-round-1 empirical check) — **could not run**: the load-bearing
  claims need Windows/SYSVAD/audiodg, unavailable on this WSL host. Gap recorded; Phase 0 is
  the host gate that substitutes for it. (Converged on a falsified premise → restructure, not
  further rounds.)
- `general-purpose 2026-10-24: consensus-attack — shared blind spot found` (did NOT
  survive). The panel split "OS WASAPI render-loopback" vs "driver KS loopback pin" into a
  false XOR and exited on "it's OS loopback → fix app-side"; on a WaveRT device that ships
  its own loopback pin they are the **same pin** (the RFC's own text puts `GenerateSine` on
  the loopback pin; patch line 117 fills it per tick), and a signal blind to both content
  and per-app volume is driver-sourced, not an engine mix. Deeper: everyone assumed a real
  mix already exists on some tap and only the *tap* was in question — but B-pre validated
  only the **clock** plane with a synthetic sine, so whether the real mix reaches the DMA
  at all (**Q1**) was never tested. → **Incorporated (one revision):** v3 note added; the
  false H1/H2 split and the "fix app-side" exit deleted; Phase 0 re-centered on the
  **render-DMA oracle dump with real music (Q1)** as the gate, with the silence probe
  demoted to a sanity check; decision table rebuilt; the Q1=no branch routes to the
  parent's unproven B-pre data plane, not a capture-tap patch. Triaged once; no further
  rounds (the attack is a final input, not a gate).

# 78 — Web-informed redirect: real passthrough is an in-driver render→capture ring, not a render-DMA read

**Block:** B2 / Track B data plane (RFC `rfc-sysvad-real-loopback.md`, #112, PR #114) ·
**Milestone:** M8 / Track B · **Status:** approach corrected from authoritative sources; Plane T
superseded; new plan = the CVCable-style loopback bridge.

## Why gather from the web

Journal 77 proved Plane T's export mechanism works but Q1=NO (the render DMA we read was empty,
pull or non-pull). Rather than keep bashing kernel cycles, checked Microsoft's docs + the SYSVAD
sample notes + a working community driver. That settled the whole thing.

## What the sources say (authoritative)

1. **WASAPI loopback (MS Core Audio docs):** loopback normally taps the **audio-engine mix** —
   "the WASAPI system module implements loopback mode in software, copying the output stream from
   the audio engine into the capture buffer." **But** — "**if the hardware exposes a loopback pin on
   the render endpoint, WASAPI uses that pin's audio**; otherwise WASAPI copies the engine's output."
2. **SYSVAD fakes loopback (MS sample test notes):** "the driver **simulates loopback by returning a
   sine tone** instead of performing real mixing of streams in **host and/or offload pins**. You
   would need to **implement real mixing of the render stream into the loopback capture path
   yourself**."
3. **OpenAudioCable** (github.com/amanison/OpenAudioCable — GPL-3.0, SysVAD-derived, a working
   "Virtual Audio Loopback Cable") shows the exact mechanism, in `EndpointsCommon/minwavertstream.cpp`:
   - `m_pVCable = CVCable::getCable(Capture_)` — a **singleton ring shared by the render and capture
     streams** (both get the same instance).
   - Render stream: `m_pVCable->WriteData(m_pDmaBuffer + off, run)` — writes its real DMA audio in.
   - Capture stream: `m_pVCable->LoopData(m_pDmaBuffer + off, run)` — reads it out **in place of the
     tone**. (`SetLoopbackProtection → m_pVCable->SetMute`.)

## What this means for us

- **Our 3 kHz tone is SYSVAD's *stock* fake-loopback pin**, which pre-empts the OS software loopback.
  Not a bug we introduced; the documented behaviour.
- **Plane T looked at the wrong end.** It read the render DMA and exported it to a user-space shared
  section. The correct, MS-documented, field-proven mechanism is an **in-driver render→capture ring
  bridge** (CVCable): the render stream feeds the ring; the **capture/loopback pin** drains it; the
  app then uses its **existing WASAPI loopback** and gets real audio — no user-space section, no
  Plane-T tick-return.
- **The empty render DMA (journal 77) is explained.** OpenAudioCable writes the ring from
  `m_pDmaBuffer` in the render processing path, so on a working build the render DMA *does* hold the
  real mix. Our scan read zero because of (a) the **host-vs-offload pin** split (the MS note's "host
  and/or offload" — `audiodg` likely rendered to the offload pin while we read the host pin), and/or
  (b) our **pull/7.1/compat modifications** disturbing the stock render data flow. The fix captures
  from the render stream(s) the stock way, not by reading one pin's DMA externally.

## Redirected plan (supersedes Plane T)

1. **Adopt the CVCable render→capture ring bridge** on a stock-ish SYSVAD render/capture model:
   render `WriteData` → shared ring; loopback capture `LoopData` ← ring (replace `GenerateSine`),
   covering **both host and offload** render pins (the MS note's requirement). This is the whole fix
   for "loopback carries real audio."
2. **Drop the pull-driven *render* path for the data plane** — it disturbs `audiodg`'s render (journal
   77) and is unnecessary here: let `audiodg` fill the render stream normally (stock WaveRT), bridge
   it to the capture pin, and pace the **K11 output + world-lock in user space** (the FIFO cushion,
   exactly as the proven VoiceMeeter path already does). The single-master-clock pull was only ever
   needed for output pacing, which is user-mode.
3. The app side is **unchanged** — it already WASAPI-loopback-captures the endpoint; once the capture
   pin carries the ring (real audio) instead of the tone, world-locking just works.

## Licensing

OpenAudioCable is **GPL-3.0** → not directly usable for the donationware target (requirements §9).
But the CVCable pattern is literally Microsoft's documented guidance, and `vcable.*` is derived from
SYSVAD's MIT/MS-PL `savedata.*`. So implement the ring bridge **from the MIT SYSVAD `CSaveData`
base + the MS docs**, using OpenAudioCable only as a read-only conceptual reference — no GPL code
copied.

## Decision / next

- **Supersede Plane T** (the render-DMA/tick-return data plane) in the RFC with the **render→capture
  ring bridge**. Plane T's kernel work (section map/lock, seqlock, build/sign/install pipeline) is
  banked but not the chosen mechanism.
- Implement the ring bridge (host+offload render → ring → loopback capture) from the MIT base;
  validate in the VM that WASAPI loopback of the endpoint carries a played signal (the planet_consumer
  FFT, but capturing the loopback instead of the section); then the host listen test.
- This resolves option 3 (the earlier fork): the virtual-device model **can** carry real audio — via
  the loopback bridge the OS/MS intend, not via reading the render DMA. VoiceMeeter's VAIO works for
  the same reason (its render endpoint's loopback carries the mix); ours didn't because SYSVAD ships a
  tone instead of a bridge.

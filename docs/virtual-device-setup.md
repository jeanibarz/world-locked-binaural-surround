# Virtual audio device — user setup (Windows)

The renderer takes a **multichannel (5.1 / 7.1) stream produced by other Windows
apps** (FR-1) through a **virtual Windows audio output device** that any app can
select as its speakers (FR-2), processes it, and plays stereo to your headphones.
Windows has no such virtual multichannel device built in, so you install one. This
page covers what to install, how to route it, and how to run the latency test.

It is written for a cold reader; the engineering evidence behind it is in
[`research-journal/03-virtual-device-spike.md`](research-journal/03-virtual-device-spike.md).

## What you need

| Role | What it is | Example endpoint on this host |
|---|---|---|
| **Virtual 7.1 sink** | a virtual *playback* device your game/film/music app selects as its 7.1 speakers | `VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)` — 8-ch |
| **Capture endpoint** | the matching *recording* device the renderer reads that stream from | `VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)` |
| **Loopback route** | sink → capture must actually carry audio (for the latency self-test) | VoiceMeeter strip → bus **B1** |

The app resolves these by **name and channel count at runtime**, never by a fixed
index — PortAudio indices drift between boots and configs (journal 02). Run the
spike (below) and it prints exactly which endpoints it picked.

## Option A — VoiceMeeter (gives a true 7.1 sink) · recommended

VoiceMeeter (VB-Audio, **donationware**) exposes `VoiceMeeter Input (VAIO)` as an
8-channel virtual sink — the only no-cost way to get a real **7.1** virtual output
on this box.

1. Install **VoiceMeeter Banana or Potato** from <https://vb-audio.com/Voicemeeter/>
   and reboot (the virtual driver loads at boot).
2. Point the app you want spatialised at the sink: Windows **Settings → System →
   Sound**, set its output to **VoiceMeeter Input (VAIO)**. Set the sink to 7.1 in
   **Sound → VoiceMeeter Input → Configure → 7.1 Surround**.
3. The renderer reads **VoiceMeeter Output (VAIO)** (a WASAPI input endpoint).
4. **For the loopback / latency self-test only:** in the VoiceMeeter window, on the
   **VAIO virtual input strip**, enable the **B1** bus button. That routes the
   strip to `VoiceMeeter Output (VAIO)` so a sweep played to the sink is captured
   back. Without B1 the capture endpoint opens fine but stays silent — which is
   exactly the state the spike reports on an unconfigured box (journal 03).

> Keep your normal listening bus (usually **A1** → your headphones/DAC) as it is;
> **B1** is an *additional* tap, it does not replace A1. The renderer itself does
> not need B1 — only the latency self-test does.

## Option B — VB-CABLE (clean stereo loopback, no routing) · for latency tests

If you only want a **clean, reproducible round-trip measurement** (not 7.1),
install **VB-CABLE** from <https://vb-audio.com/Cable/> (donationware). Its
`CABLE Input` → `CABLE Output` is a **hardwired internal loopback** — no routing
to configure. Run the spike with `--min-channels 2` so it accepts the stereo
cable. VB-CABLE is stereo, so it is **not** a substitute for the 7.1 sink; use it
alongside Option A when you want a zero-config latency reference.

## Running the virtual-device spike

From the WSL repo, driving the Windows Python via interop (see `AGENTS.md` →
"Runtime environment"):

```bash
WINPATH=$(wslpath -w spikes/b0_3_virtual_device.py)
py.exe -3.12 "$WINPATH" --seconds 1.0         # enumerate + capture + loopback
py.exe -3.12 "$WINPATH" --no-loopback         # enumerate + capture only (no sweep)
py.exe -3.12 "$WINPATH" --min-channels 2      # accept a stereo virtual cable (VB-CABLE)
```

It prints the resolved sink/capture endpoints, confirms it can **capture** the
virtual stream, and — if a loopback route exists — reports a **measured
round-trip latency**. If the route is not configured it says so and points back
here, rather than reporting a misleading `0 ms`.

### Measuring latency on an isolated tap (recommended)

To measure a round-trip without sending sweeps to your headphones, route a
*normally-unused* input strip to a B bus and target it explicitly. On this host
(journal 04): the **AUX** input strip → bus **B1**, with the **VAIO** strip left
on **A1** (your listening path). Then point the spike at that exact pair:

```bash
# feed AUX input (#27) -> AUX strip -> B1 -> capture VoiceMeeter Output / B1 (#35)
py.exe -3.12 "$WINPATH" --out-device 27 --in-device 35 --seconds 2
```

`--out-device` / `--in-device` override auto-resolution so you can measure any
render→capture pair. Indices drift — confirm them from the printed device table.
The measured round-trip is roughly **one VAIO/AUX driver buffer** (e.g. 6144
samples ≈ 128 ms), so lowering that buffer in the VB-Audio control panel lowers
latency almost one-for-one — at the cost of dropout headroom on a busy host.

## Licences (donationware-release compliance, requirements § 9)

VoiceMeeter and VB-CABLE are **proprietary donationware** from VB-Audio, **not**
open source and **not redistributable**. This project therefore **does not bundle
them** — the user installs them separately and the app depends on them only at
runtime, which is compatible with our own donationware release. They are **not**
Python package dependencies (nothing is added to `pyproject.toml`). No GPL is
introduced. If a fully redistributable virtual device is needed later, evaluate an
open-source driver (e.g. a signed WDM/APO virtual audio device) in a dedicated
block.

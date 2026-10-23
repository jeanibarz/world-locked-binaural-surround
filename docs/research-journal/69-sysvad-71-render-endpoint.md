# 69 — The SYSVAD device gets a 7.1 render endpoint (the surround sink the world-lock needs)

**Block:** B8.7b (RFC Track B, B2) · **Milestone:** M8 / Track B · **Status:** **done** — the
Win10-loadable SYSVAD device (journal 68) now exposes an **8-channel (7.1) LPCM** render endpoint, so
apps can render surround into it exactly as they do into VoiceMeeter Input today. Validated loading
clean in the Win10 VM and presenting 8ch on the host.

## In plain terms

The driver loaded (journal 68), but its "Speakers" only accepted **stereo** — while the head-tracker's
world-lock needs a **7.1 (8-channel)** sink to render surround into (that is what VoiceMeeter Input
gives us at 8ch). So before the virtual device can replace VoiceMeeter in the real app, its speaker
endpoint had to be made 7.1. Done: the endpoint now reports 8 channels and accepts a 7.1 stream.

**Glossary.** *Endpoint* — what Windows shows as a playback/record device. *LPCM* — ordinary
uncompressed audio (as opposed to a compressed bitstream). *KSDATARANGE / WAVEFORMATEXTENSIBLE* — the
kernel-streaming structures that advertise a pin's accepted formats (channels, rate, bits, channel
mask). *Channel mask* — which speakers the channels map to; `KSAUDIO_SPEAKER_7POINT1_SURROUND` is 7.1.

## What was wrong

PortAudio `check_output_settings(channels=8)` on the SYSVAD "Haut-parleurs" endpoint failed
(`Invalid number of channels`); the endpoint was stereo. The HDMI endpoint's 7.1 entries are only
compressed-passthrough (Dolby MAT / DTS-HD IEC-61937), not LPCM, so there was no usable 7.1 LPCM sink.

## The change (`spikes/bpre-vm/make_speaker_71.py`, idempotent)

Convert the Speaker endpoint in `EndpointsCommon/speakerwavtable.h` + `speakertoptable.h` to 7.1 LPCM:

- `SPEAKER_DEVICE_MAX_CHANNELS`, `SPEAKER_HOST_MAX_CHANNELS`, `SPEAKER_OFFLOAD_MAX_CHANNELS`: 2 → 8
  (the host/offload/loopback `KSDATARANGE_AUDIO` MaxChannels follow these).
- Every `WAVEFORMATEXTENSIBLE` in the audio-engine / host-pin / offload format tables:
  `nChannels` 2 → 8, `nBlockAlign` 4 → 16, `nAvgBytesPerSec` → `SampleRate × 16`,
  `dwChannelMask` `KSAUDIO_SPEAKER_STEREO` → `KSAUDIO_SPEAKER_7POINT1_SURROUND`.
- The topology jack description channel mask (`speakertoptable.h`) → `7POINT1_SURROUND`.

Everything else (the Win10 lookaside compat of journal 68) is unchanged; the `.sys` is rebuilt via the
EWDK (`EndpointsCommon` + `TabletAudioSample`) and re-signed into `pkg10` (DriverVer `10.0.19041.102`).

Also fixed a latent bug in `apply_win10_compat.ps1`'s import verifier: it parsed past dumpbin's
trailing `Summary` block and mis-counted the `INIT` / `PAGE` **section names** as unresolved
`wdfldr.sys` imports, producing a false "STILL MISSING" verdict that then skipped the pkg refresh.
The loop now stops at `Summary`.

## Validation

- **Build:** MSBUILD exit 0 — the 7.1 topology compiles. The rebuilt `.sys` still imports nothing
  absent on Win10 19045 (PortCls 9 / ntoskrnl 66 / WDFLDR 5 / HAL 1 all resolve; the two NI-gated
  lookaside imports stay gone).
- **Win10 VM (19045):** force-reinstalled `pkg10`; the **new** binary loads (DriverStore hash matches
  the rebuilt sys), node `CM_PROB_NONE`, **no bugcheck**.
- **Host (19045):** the "Haut-parleurs (… Tablet Sample)" endpoint now reports **`max_output_channels = 8`**
  and `check_output_settings(channels=8, 48 kHz)` → **OK**; all eight Tablet-Sample endpoints `[OK]`,
  default playback device untouched.

## Handoff — step 3 (now unblocked): the B2 consumer + end-to-end latency

With a real 7.1 LPCM SYSVAD sink on the host, build the B2 consumer per journal 62/63: loopback-capture
the 8-ch SYSVAD render endpoint → `RustWorldLocked7p1` binaural DSP → WASAPI-**exclusive** to the FiiO
K11 → fire `KookrPullTick` once per K11 render callback (single master clock, **no cushion FIFO**).
Then measure the per-stage split (device / queue / render), under/overflow counts, and sweep block
size + shared-vs-exclusive, against the journal-48 baseline to confirm the ≈16–20 ms `queue`-term win.
Shared mode is unavoidable on the capture leg (apps open the sink in shared mode → audiodg's ~22 ms);
exclusive-to-K11 is now safe because the pull-driven design is single-clock (journal 45's two-clock
objection no longer applies).

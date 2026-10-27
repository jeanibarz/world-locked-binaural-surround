# World-Locked Binaural Surround

**A real-time, head-tracked binaural renderer for Windows that keeps a virtual
surround sound stage fixed in the world while your head moves — so your brain
learns to externalise the sound into real space.**

![The binaural-headtracker cockpit in motion: as the listener's head turns left and right, the six virtual speakers of the world-locked ring stay fixed in place while the head icon and its facing cone rotate to follow the pose. The surrounding panels update live — camera face-lock, tracker and render health, head pose (yaw / pitch / roll), and head-move-to-ear latency.](docs/figures/cockpit-demo.gif)

*The cockpit, live. Turn your head and the **world-locked ring** holds its speakers in place while the head, pose read-outs, load meters, and latency update in real time — the on-screen half of what the renderer is doing to the sound.*

## The idea in one paragraph

Headphone surround normally rotates with your head: turn left, and the whole
sound field turns with you, which kills the illusion of sound coming from a fixed
place in the room. This project inverts that. We place a set of **virtual
loudspeakers at fixed positions relative to your screen** (e.g. front-centre 1 m
behind the middle of the screen, front-left/right at its edges, sides and rears
arranged around you). A camera watches your head and estimates its **orientation
(azimuth + elevation)** and position in real time. For every audio frame we
convolve each virtual speaker's signal with the **HRIR/BRIR for that speaker's
direction *relative to your current head pose*** and mix down to stereo for any
headphone. When you turn your head, the sound stays put in the room; only the
convolution changes. Over time this **world-locked** cue trains your brain to
place the virtual speakers in real space — stronger externalisation, better
front/back, a more convincing 3-D image.

## What makes it different

No single existing product does all of this together (see
[`docs/requirements.md`](docs/requirements.md) § *Prior art*): an **individual or
AI-fine-tuned HRIR** set, a **camera head-tracker**, a **world-locked virtual
7.1/5.1 stage** you can position and scale, a **virtual Windows audio device** so
*any* app's surround output is captured and rendered, **on-the-fly parametric EQ**
you can calibrate, and an **AI judge-in-the-loop** that scores and tunes your
filters objectively (the synthetic-listener work in the sibling project
[`binaural-localization`](../binaural-localization)). The closest integrated
system, the Smyth Realiser A16, is dedicated hardware costing thousands.

## Status

Working end-to-end on Windows. The static + live render engine, the camera head
tracker, and the **world-locked 7.1 passthrough** (any system audio → head-tracked →
headphones) are built and live-validated, with the owner's headphone comp + EQ applied on
the output. The render hot loop runs in real time on the **CPU** via a native Rust kernel
(no GPU required). Still open: a custom virtual-audio driver to
drop VoiceMeeter (further latency + a cleaner install), HRIR fine-tuning, presets, and the
AI-judge calibration loop.

**Where the latency went.** The felt "it reacts too slowly" was the audio output path, not
the camera. Measuring **pose age at the moment the sound is heard** as a distribution
(journal 40) traced the lag to the output **device buffer**: the output had silently been on
**MME** (~90–100 ms), and moving it to **WASAPI** shared cut that to ~22 ms (journals 42–44).
The remaining ~40 ms of **camera staleness** is cancelled by **orientation prediction** to the
audio-render instant (an α-β extrapolation over the live-measured pose age; journal 47).
Together those took the live head-move→ear latency from ~140–200 ms down to roughly **~70 ms**
(sub-50 ms on the synthetic component-split bench, but the live floor is dominated by the FIFO
`queue` term; journals 42–48). Closing the rest of the gap to the `< ~50 ms` target is the
custom **pull-driven virtual audio driver** that removes VoiceMeeter (Track B, journals 51–80). The psychoacoustics
that set the target (detection threshold ≈ 50–75 ms; a few degrees of lag is sub-threshold
*during* a turn but not at the stop) are in
[`docs/requirements.md`](docs/requirements.md) § 7 and journal entry 36.

The project is built **block by block** by an autonomous Kookr agent chain that records its
progress in a living research journal; read
[`docs/research-journal/`](docs/research-journal/README.md) for the current frontier (and the
accurate milestone status table) and [`docs/backlog.md`](docs/backlog.md) for the ordered
work blocks. Requirements and architecture live in
[`docs/requirements.md`](docs/requirements.md).

## Intended distribution

Donationware, with a possible paid service around **AI-assisted HRIR/filter
calibration**. Licensing is therefore **deliberately undecided** (not yet
open-source licensed); see [`docs/requirements.md`](docs/requirements.md)
§ *Licensing & business*.

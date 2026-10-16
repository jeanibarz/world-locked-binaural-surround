# 23 — Device pickers + the first live host bring-up (B6.2)

**Date:** 2026-10-17  ·  **Milestone:** M6 (GUI) / M3 host validation  ·  **Status:** done, validated live

## Question

The owner asked for a **device-selection menu** in the GUI — pick which webcam and which
audio output to use (on Windows there are several: laptop cam vs USB cam; speakers,
headphones, a VoiceMeeter endpoint). And then: **run it on the Windows host**. So this
block is both the picker *and* the first real end-to-end bring-up of the live world-lock
(B3.1) on actual hardware — which, as usual, is where the truth came out.

## Method

- **Pure selection logic — `binaural_headtracker.devices`** (unit-tested, no cv2/sd/tk):
  `output_device_choices` (keep output-capable devices, label `name (host-API, Nch)`, mark
  the system default), `camera_choices`, `resolve_output_device` (a `--audio-device` spec
  = index or case-insensitive name substring → device index, raising on a typo rather than
  silently playing the wrong device), `default_choice_label`. 8 tests.
- **Host-only shell** (lazy imports): `probe_cameras` (cv2), `query_output_devices` (sd),
  and a small Tk **pre-launch `settings_dialog`** with two dropdowns (OpenCV windows have
  no menu widget). Wired into both apps — `live_tracker_audio.py` (camera + audio),
  `calibration_gui.py` (camera). The dialog can be previewed under WSLg but the camera +
  audio only run on the host.
- **Ran it on the Windows host via interop** (`py.exe -3.12`), which surfaced the real
  gotchas and two genuine bugs (below).

## Result

B6.2 done; **271 tests pass (8 new), ruff clean.** The device picker enumerated the real
devices on the host and drove the app. The owner ran the **live world-lock end-to-end**
and confirmed it: with the fixes below, turning the head leaves the snare fixed in the
room ("working… very usable").

Host bring-up findings, each fixed in this block:

1. **Launching the host GUI from the WSL worktree.** The host Python has cv2/mediapipe/
   sounddevice but **not** our package, and `PYTHONPATH` is overridden on this box. A
   `\\wsl.localhost\…\src` **UNC path imports fine**, so a tiny host-local launcher does
   `sys.path[:0] = [apps, src]` and calls `main()`. **MediaPipe's model loader, though,
   cannot open a UNC path** (`FileNotFoundError`) — the `.task` must be copied to a local
   `C:` path. Both recorded in AGENTS.md.
2. **"Picture but no sound."** The audio callback assumed WASAPI hands it fixed
   `block_size` buffers; it doesn't, so `process_block` raised *inside the callback*,
   which silently **aborts** the PortAudio stream (video keeps running). Fix: decouple the
   renderer's fixed block from the callback's `frames` — render fixed blocks into a
   `pending` buffer, serve whatever is asked, and **surface callback errors** to the main
   loop instead of swallowing them.
3. **The source tracked the head instead of the world** (world-lock reversed). The frame
   was **mirrored before** being fed to MediaPipe; a horizontal flip negates the estimated
   yaw, so the pose sign was inverted. Fix: estimate the pose from the **un-mirrored**
   frame and mirror only the *display* (flipping the landmark x in the calibration GUI so
   its overlay still lines up). This is the owner's "reverse the azimuth relation" — done
   at the root, keeping the B2.2 `yaw_sign=+1` convention consistent everywhere. As a belt
   -and-braces for *other* cameras/drivers that may pre-flip, the live app also has a
   runtime **F** toggle (and `--flip-az`) that mirrors the azimuth without a rebuild.

## Interpretation

Two of the three are the lesson restated: **hardware is the only real test.** The callback
buffer-size assumption and the mirror-vs-yaw sign both passed every headless test and only
failed against a real WASAPI device and a real head turn. The mirror bug in particular was
*predicted* by the B6.1 review (the un-tested cv2 shell) and still had to be seen live to
pin the sign — which is why the axis signs were always deferred to a host flag.

## Decision / next

- The live world-lock is validated on the host; the residual "not perfect" is the known
  set (8-point BRIR discretisation, tracker jitter/latency, the rear front/back data gap
  **B1.11**), not the coupling.
- **Next: M4 / B4.1** — the owner wants *any* system sound world-locked, not just the test
  loop: capture the **7.1 stream from VoiceMeeter**, run every channel through the same
  world-lock render (8 fixed world speakers → nearest BRIR + crossfade, generalising
  `live_render`), and play stereo to the headphone card. The capture spike (journals
  02–04) + the 7.1 renderer + this validated head-tracking chain are the pieces; B4.1 wires
  them into a continuous duplex. (→ L46, L47)

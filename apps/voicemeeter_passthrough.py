"""World-lock *any* system audio: capture the virtual 7.1 device → render → headphones (B4.1).

The end-to-end Windows path (M4). Point your apps at a virtual 7.1 output (VoiceMeeter
Input / VAIO, set as the Windows default playback); this app **captures** that 7.1 stream
from the virtual device's output bus, world-locks every channel with your head pose
(`WorldLocked7p1`: each of the 8 speakers steered through its nearest BRIR, click-free,
LFE non-steered), and plays the stereo result to your **headphone soundcard**. So anything
on the computer — a game, a film, music — becomes head-tracked and world-locked.

    any app (7.1) → VoiceMeeter Input (VAIO) → [this engine] → FiiO K11 (headphones)

Set VoiceMeeter so it does **not** also route to the headphones (disable the A1 that
points at them); this engine is the only thing feeding them, so there's no double audio.

Three decoupled stages so the render never blocks a real-time audio callback: the capture
callback only *enqueues* the raw 8ch frames; a **render worker thread** world-locks them to
stereo (the heavy GPU convolution lives here, with the freshest head pose) and fills a FIFO;
the output callback drains the FIFO to the headphones. Rendering in the callback itself
starves the audio threads (GIL + GPU sync) and drops audio — the worker keeps the callbacks
microsecond-light and the cushion absorbs the render's jitter. Composed from tested parts
(`live_render.WorldLocked7p1`,
`calibrate`, `world`, `tracking`, `devices`); only the camera, MediaPipe and the two
`sounddevice` streams are host-side, so this runs on the Windows host, not CI/WSL.

Run on the host (see AGENTS.md "Running the host GUI apps"), e.g.::

    py -3.12 apps/voicemeeter_passthrough.py --model face_landmarker.task --brir-dir <baked> --gpu
    # a pre-launch menu picks camera + 7.1 input + headphone output; --no-dialog uses flags
    # --input-device "VoiceMeeter Output"  --audio-device "FiiO"  (name substrings or indices)
    # --gpu renders on the GPU (CuPy); the full 8ch 500ms-BRIR path needs it to keep up.

Keys: **C** recenter, **F** flip L/R (if the feed is mirrored), **Q** quit.
"""

from __future__ import annotations

import argparse
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from binaural_headtracker.calibrate import HeadCalibration, calibrate_neutral, directions_for_pose
from binaural_headtracker.devices import (
    camera_choices,
    probe_cameras,
    query_output_devices,
    resolve_output_device,
    settings_dialog,
)
from binaural_headtracker.head_pose import head_pose_from_matrix
from binaural_headtracker.live_render import WorldLocked7p1
from binaural_headtracker.surround import load_baked_brir
from binaural_headtracker.tracking import HeadPose, PoseFilter
from binaural_headtracker.world import layout_7p1

FG = (245, 237, 232)
MUTED = (171, 151, 139)
ACCENT = (214, 214, 56)


def main(argv=None) -> int:
    import cv2
    import mediapipe as mp
    import sounddevice as sd
    from mediapipe.tasks.python import vision

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="FaceLandmarker .task path")
    p.add_argument("--brir-dir", type=Path, required=True, help="baked 7.1 BRIR WAV directory")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--input-device", default=None,
                   help="7.1 capture device index or name substring (e.g. 'VoiceMeeter Output')")
    p.add_argument("--audio-device", default=None,
                   help="headphone output device index or name substring (e.g. 'FiiO')")
    p.add_argument("--no-dialog", action="store_true", help="skip the picker, use the flags")
    p.add_argument("--block", type=int, default=512, help="render block size (bigger = steadier)")
    p.add_argument("--fade-ms", type=float, default=20.0)
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--latency-ms", type=float, default=120.0,
                   help="output buffer cushion that absorbs MME capture/output jitter")
    p.add_argument("--min-cutoff", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=0.3)
    p.add_argument("--yaw-sign", type=float, default=1.0)
    p.add_argument("--pitch-sign", type=float, default=-1.0)
    p.add_argument("--roll-sign", type=float, default=1.0)
    p.add_argument("--flip-az", action="store_true", help="mirror azimuth L/R (also F live)")
    p.add_argument("--gpu", action="store_true",
                   help="render on the GPU (CuPy) — needed for the full-BRIR 8ch path in real time")
    p.add_argument("--monitor", action="store_true",
                   help="print per-channel captured input RMS ~1/s (diagnose channel layout)")
    args = p.parse_args(argv)

    brir = load_baked_brir(args.brir_dir)
    renderer = WorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms)
    if args.gpu:
        try:
            from binaural_headtracker.gpu_render import GpuWorldLocked7p1
            renderer = GpuWorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms)
            print("render: GPU (CuPy)")
        except Exception as exc:  # noqa: BLE001  — fall back to CPU, which may drop out
            print(f"GPU render unavailable ({exc!r}); falling back to CPU "
                  "(may not keep up on the full 8ch BRIR)")
    else:
        print("render: CPU (pass --gpu for the real-time full-BRIR path)")
    layout = layout_7p1(channels=brir.channels)  # aligned to BRIR channel order

    out_devs, default_out = query_output_devices()
    cam_index = args.camera
    out_index = resolve_output_device(args.audio_device, out_devs)
    if not args.no_dialog:
        picked = settings_dialog(
            camera_choices(probe_cameras()), out_devs,
            title="World-lock passthrough — devices", camera_default=args.camera,
            audio_default=out_index if out_index is not None else default_out)
        if picked is None:
            return 0
        cam_index = picked["camera"] if picked["camera"] is not None else args.camera
        out_index = picked["audio_device"]

    # Capture the RAW 7.1 as apps send it INTO the virtual device, via WASAPI **loopback**
    # (PyAudioWPatch). Reading VoiceMeeter's *output* bus is wrong — VoiceMeeter downmixes
    # it to stereo, losing centre/LFE/surrounds (confirmed by measurement). The loopback
    # taps the pre-mix stream on the render endpoint apps target.
    import pyaudiowpatch as pawp

    pya = pawp.PyAudio()
    in_spec = (args.input_device or "VoiceMeeter Input").lower()
    lb = next((d for d in pya.get_loopback_device_info_generator()
               if in_spec in d["name"].lower()), None)
    if lb is None:
        raise SystemExit(f"no WASAPI loopback device matching {in_spec!r} — is VoiceMeeter "
                         "running and set as the default 7.1 playback device?")
    in_ch = int(lb["maxInputChannels"])
    if in_ch != brir.n_channels:
        raise SystemExit(f"loopback '{lb['name']}' is {in_ch}ch, need {brir.n_channels} — set "
                         f"that endpoint to 7.1 in Windows Sound.")
    out_name = sd.query_devices(out_index)["name"] if out_index is not None else "default"
    print(f"capture (loopback): {lb['name']}  {in_ch}ch  ->  output: {out_name}  "
          f"@ {brir.samplerate} Hz")
    print(f"block {args.block}  latency cushion ~{args.latency_ms:.0f} ms")

    # Shared state: per-channel head-relative azimuth (written by the camera thread, read by
    # the render worker). Start at the neutral (fixed-head) directions.
    state = {
        "az": layout.head_relative_directions()[:, 0].copy(),
        "cal": HeadCalibration(0.0, 0.0, 0.0, args.yaw_sign, args.pitch_sign, args.roll_sign),
    }
    lock = threading.Lock()
    # The loopback capture clock ≠ the output clock, and both deliver audio in bursts, so
    # the output FIFO is primed with a silence cushion (`--latency-ms`) that absorbs the
    # jitter; the backlog is capped at a few cushions and only drops oldest on real drift.
    cushion = max(2, round(args.latency_ms * 1e-3 * brir.samplerate / args.block))
    out_fifo: deque[np.ndarray] = deque(np.zeros((args.block, 2)) for _ in range(cushion))
    fifo_lock = threading.Lock()
    in_tail = {"buf": np.zeros((0, brir.n_channels))}  # leftover capture < one block
    cb_error: list[BaseException] = []
    max_backlog = cushion * 4

    # The render must NOT run in the audio callback: the full 8-ch BRIR convolution (tens of
    # ms, GPU sync) would hold the GIL and starve the capture/output callbacks, collapsing
    # throughput and dropping audio (measured: ~31 kHz effective, constant drops). Instead the
    # capture callback only *enqueues* the raw frames and returns in microseconds; a dedicated
    # worker thread drains the queue, renders, and fills the output FIFO. The callbacks stay
    # light, and the render's jitter is absorbed by the cushion — click-free, no dropouts.
    in_q: deque[np.ndarray] = deque()
    in_q_lock = threading.Lock()
    stop_worker = threading.Event()
    mon = {"t": 0.0}

    def pa_in_cb(in_data, frame_count, _ti, _status):  # noqa: ANN001  (PyAudio loopback)
        try:
            # astype(copy) is essential: PyAudio reuses in_data after we return.
            cap = np.frombuffer(in_data, dtype=np.float32).reshape(-1, in_ch).astype(np.float64)
            with in_q_lock:
                in_q.append(cap)
        except BaseException as exc:  # noqa: BLE001
            cb_error.append(exc)
        return (None, pawp.paContinue)

    def render_worker() -> None:
        """Drain captured 7.1 → world-lock render → output FIFO, off the audio threads."""
        while not stop_worker.is_set():
            with in_q_lock:
                chunks = list(in_q)
                in_q.clear()
            if not chunks:
                stop_worker.wait(0.001)  # idle: brief, interruptible pause
                continue
            try:
                with lock:
                    az = state["az"]
                cap = np.concatenate(chunks, axis=0)
                if args.monitor:
                    tnow = time.monotonic()
                    if tnow - mon["t"] > 1.0:
                        mon["t"] = tnow
                        rms = np.sqrt((cap ** 2).mean(axis=0) + 1e-20)
                        labels = " ".join(f"{c}={v:.3f}" for c, v in zip(brir.channels, rms,
                                                                          strict=True))
                        print("in RMS  " + labels, flush=True)
                buf = np.concatenate([in_tail["buf"], cap], axis=0)
                i = 0
                rendered = []
                while buf.shape[0] - i >= args.block:
                    rendered.append(renderer.process_block(buf[i : i + args.block], az) * args.gain)
                    i += args.block
                in_tail["buf"] = buf[i:]
                if rendered:
                    with fifo_lock:
                        out_fifo.extend(rendered)
                        while len(out_fifo) > max_backlog:  # bound latency: drop oldest
                            out_fifo.popleft()
            except BaseException as exc:  # noqa: BLE001
                cb_error.append(exc)

    out_carry = {"buf": np.zeros((0, 2))}  # leftover rendered samples < one output request

    def out_cb(outdata, frames, _t, status):  # noqa: ANN001  (FIFO → headphones)
        try:
            buf = out_carry["buf"]
            with fifo_lock:
                while buf.shape[0] < frames and out_fifo:
                    buf = np.concatenate([buf, out_fifo.popleft()], axis=0)
            n = min(frames, buf.shape[0])
            outdata[:n] = buf[:n].astype(np.float32)
            if n < frames:
                outdata[n:] = 0  # underrun → silence
            out_carry["buf"] = buf[n:]
        except BaseException as exc:  # noqa: BLE001
            cb_error.append(exc)
            outdata.fill(0)

    detector = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))
    cap = cv2.VideoCapture(cam_index)
    recent: deque[HeadPose] = deque(maxlen=20)
    pose_filter = PoseFilter(args.min_cutoff, args.beta)
    flip_az = args.flip_az
    miss = 0  # consecutive failed camera reads (graceful degradation, B4.2)
    win = "Binaural head-tracker — world-lock passthrough"

    # Capture = PyAudio WASAPI loopback (raw 7.1); output = sounddevice to the headphones
    # (blocksize=0 lets it pick the host-native buffer; the callbacks handle any frame count).
    in_stream = pya.open(format=pawp.paFloat32, channels=in_ch, rate=brir.samplerate,
                         input=True, input_device_index=lb["index"],
                         frames_per_buffer=args.block, stream_callback=pa_in_cb)
    worker = threading.Thread(target=render_worker, name="render", daemon=True)
    worker.start()
    in_stream.start_stream()
    out_ctx = sd.OutputStream(samplerate=brir.samplerate, channels=2, device=out_index,
                              blocksize=0, dtype="float32", callback=out_cb)
    try:
        out_ctx.start()
        while True:
            if cb_error:
                print(f"audio callback error: {cb_error[0]!r}")
                break
            ok, frame = cap.read()
            if not ok:
                # Camera lost: hold the last pose (audio keeps world-locking), keep the
                # window + keys alive, and try to reopen periodically — don't spin blind.
                miss += 1
                if miss % 90 == 0:
                    cap.release()
                    cap = cv2.VideoCapture(cam_index)
                lost = np.full((240, 520, 3), (28, 20, 16), np.uint8)
                cv2.putText(lost, "camera lost - holding last pose (audio live)", (14, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, MUTED, 1, cv2.LINE_AA)
                cv2.putText(lost, "[Q] quit", (14, 150),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, FG, 1, cv2.LINE_AA)
                cv2.imshow(win, lost)
                if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                    break
                continue
            miss = 0
            now = time.monotonic()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # un-mirrored → real yaw sign
            result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            frame = cv2.flip(frame, 1)  # mirror for display only
            if result.facial_transformation_matrixes:
                yaw, pitch, roll = head_pose_from_matrix(
                    np.asarray(result.facial_transformation_matrixes[0]))
                pose = pose_filter.update(HeadPose(yaw, pitch, roll, now))
                recent.append(pose)
                with lock:
                    cal = state["cal"]
                az = directions_for_pose(cal, pose, layout)[:, 0]
                if flip_az:
                    az = -az
                with lock:
                    state["az"] = az
                cv2.putText(frame, f"yaw {pose.yaw:+5.1f}{'  [flip]' if flip_az else ''}",
                            (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ACCENT, 1, cv2.LINE_AA)
            else:
                cv2.putText(frame, "no face", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, MUTED, 1, cv2.LINE_AA)
            cv2.putText(frame, "[C] recenter   [F] flip L/R   [Q] quit",
                        (12, frame.shape[0] - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, FG, 1, cv2.LINE_AA)
            cv2.imshow(win, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("f"):
                flip_az = not flip_az
            if key == ord("c") and recent:
                new_cal = calibrate_neutral(list(recent), yaw_sign=args.yaw_sign,
                                            pitch_sign=args.pitch_sign, roll_sign=args.roll_sign)
                with lock:
                    state["cal"] = new_cal
    finally:
        stop_worker.set()
        worker.join(timeout=1.0)
        out_ctx.stop()
        out_ctx.close()
        in_stream.stop_stream()
        in_stream.close()
        pya.terminate()
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

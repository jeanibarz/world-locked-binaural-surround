"""Live world-locked head-tracked demo: a looped source you can hear stay put (B3.1).

The payoff of the whole project, on the host: play the owner's **caisse-claire loop**
as a single **world-fixed** source, track the head with the camera, and render it
through the nearest baked BRIR, crossfaded, so **turning your head swings the snare the
opposite way and it stays locked in world space**. No Windows virtual device yet — this
plays straight out your headphones via ``sounddevice``; routing a real system stream in
through VoiceMeeter is the later M4 step. This app only *composes* tested parts:

    tracking.PoseFilter → calibrate.directions_for_pose(source_layout) → source azimuth
    → live_render.WorldLockedSource.process_block → stereo out

Run on the Windows host (camera + audio), e.g.::

    py -3.12 apps/live_tracker_audio.py --model face_landmarker.task \
        --brir-dir path/to/baked --source caisse_claire.wav
    # --world-az 0   (place the source straight ahead; try 90 for a side source)
    # --record out/run  -> writes out/run.wav (what you heard) + out/run.npz (az track)
    # sign flags as in the calibration GUI: --yaw-sign ±1 --pitch-sign ±1 --roll-sign ±1

Recenter with **C** (sets the current pose as "looking at screen centre", like a VR
headset); quit with **Q**. The ``.npz`` azimuth track re-renders the exact run offline
via ``live_render.render_world_locked`` for a reproducible A/B (B3.2).
"""

from __future__ import annotations

import argparse
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from binaural_headtracker.calibrate import HeadCalibration, calibrate_neutral, directions_for_pose
from binaural_headtracker.head_pose import head_pose_from_matrix
from binaural_headtracker.live_render import WorldLockedSource, source_layout
from binaural_headtracker.surround import load_baked_brir
from binaural_headtracker.tracking import HeadPose, PoseFilter

FG = (245, 237, 232)
MUTED = (171, 151, 139)
ACCENT = (214, 214, 56)
ACTIVE = (75, 178, 246)


def _load_mono_loop(path: Path, target_sr: int):
    """Read a WAV as mono float64 in [-1, 1], resampled to ``target_sr``."""
    import warnings

    from scipy.io import wavfile
    from scipy.signal import resample_poly

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sr, data = wavfile.read(path)
    data = np.asarray(data)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float64) / float(np.iinfo(data.dtype).max)
    else:
        data = data.astype(np.float64)
    if sr != target_sr:
        from math import gcd

        g = gcd(int(sr), int(target_sr))
        data = resample_poly(data, target_sr // g, sr // g)
    return data


def main(argv=None) -> int:
    import cv2
    import mediapipe as mp
    import sounddevice as sd
    from mediapipe.tasks.python import vision

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="FaceLandmarker .task path")
    p.add_argument("--brir-dir", type=Path, required=True, help="baked 7.1 BRIR WAV directory")
    p.add_argument("--source", type=Path, required=True, help="dry mono loop WAV (e.g. snare)")
    p.add_argument("--world-az", type=float, default=0.0, help="world azimuth of the source")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--block", type=int, default=256)
    p.add_argument("--fade-ms", type=float, default=20.0)
    p.add_argument("--gain", type=float, default=1.0, help="output gain (headroom)")
    p.add_argument("--min-cutoff", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=0.3)
    p.add_argument("--yaw-sign", type=float, default=1.0)
    p.add_argument("--pitch-sign", type=float, default=-1.0)
    p.add_argument("--roll-sign", type=float, default=1.0)
    p.add_argument("--record", type=Path, default=None, help="write <path>.wav + <path>.npz")
    args = p.parse_args(argv)

    brir = load_baked_brir(args.brir_dir)
    loop = _load_mono_loop(args.source, brir.samplerate)
    if loop.size == 0:
        raise SystemExit("source loop is empty")
    renderer = WorldLockedSource(brir, block_size=args.block, fade_ms=args.fade_ms)
    layout = source_layout(world_az_deg=args.world_az)

    # Shared head-relative source azimuth: written by the camera thread, read by audio.
    state = {"az": args.world_az, "active": "--", "cal": HeadCalibration(
        0.0, 0.0, 0.0, args.yaw_sign, args.pitch_sign, args.roll_sign)}
    lock = threading.Lock()
    recorded: list[np.ndarray] = []
    az_track: list[float] = []
    pos = 0  # read cursor into the looping source

    def audio_cb(outdata, frames, _time, status):  # noqa: ANN001
        nonlocal pos
        if status:
            pass  # xruns are expected under load; keep going
        # Pull `frames` samples from the looping source.
        idx = (pos + np.arange(frames)) % loop.size
        block = loop[idx]
        pos = int((pos + frames) % loop.size)
        with lock:
            az = state["az"]
        stereo = renderer.process_block(block, az) * args.gain
        outdata[:] = stereo.astype(np.float32)
        if args.record is not None:
            recorded.append(stereo.copy())
            az_track.append(az)

    detector = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))
    cap = cv2.VideoCapture(args.camera)
    recent: deque[HeadPose] = deque(maxlen=20)
    pose_filter = PoseFilter(args.min_cutoff, args.beta)
    win = "Binaural head-tracker — live world-lock"

    with sd.OutputStream(samplerate=brir.samplerate, channels=2, blocksize=args.block,
                         dtype="float32", callback=audio_cb):
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            now = time.monotonic()
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            if result.facial_transformation_matrixes:
                yaw, pitch, roll = head_pose_from_matrix(
                    np.asarray(result.facial_transformation_matrixes[0]))
                pose = pose_filter.update(HeadPose(yaw, pitch, roll, now))
                recent.append(pose)
                with lock:
                    cal = state["cal"]
                src_az = float(directions_for_pose(cal, pose, layout)[0][0])
                with lock:
                    state["az"] = src_az
                cv2.putText(frame, f"yaw {pose.yaw:+5.1f}  src az {src_az:+6.1f}",
                            (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ACCENT, 1, cv2.LINE_AA)
            else:
                cv2.putText(frame, "no face", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, MUTED, 1, cv2.LINE_AA)
            cv2.putText(frame, "[C] recenter   [Q] quit", (12, frame.shape[0] - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, FG, 1, cv2.LINE_AA)
            cv2.imshow(win, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c") and recent:
                new_cal = calibrate_neutral(list(recent), yaw_sign=args.yaw_sign,
                                            pitch_sign=args.pitch_sign, roll_sign=args.roll_sign)
                with lock:
                    state["cal"] = new_cal

    cap.release()
    cv2.destroyAllWindows()

    if args.record is not None and recorded:
        from scipy.io import wavfile

        args.record.parent.mkdir(parents=True, exist_ok=True)
        stereo = np.concatenate(recorded, axis=0)
        wavfile.write(str(args.record.with_suffix(".wav")), brir.samplerate,
                      np.clip(stereo, -1.0, 1.0).astype(np.float32))
        np.savez(str(args.record.with_suffix(".npz")), az_track=np.asarray(az_track),
                 samplerate=brir.samplerate, block=args.block, world_az=args.world_az)
        print(f"recorded {stereo.shape[0] / brir.samplerate:.1f}s -> "
              f"{args.record.with_suffix('.wav')} (+ .npz az track)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

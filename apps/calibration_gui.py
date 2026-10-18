"""Calibration console (GUI) — VR-style recenter + live world-lock radar (B6.1 / B6.3, layout A).

A single OpenCV window, side-by-side: LEFT the webcam with the MediaPipe face mesh, a face
box and a yaw/pitch/roll HUD; RIGHT a top-down azimuth radar — you at the centre facing up,
the **world-locked 7.1 ring** around you, the forward field-of-view cone, and the nearest
(active) speaker highlighted. Turn your head and the ring swings the opposite way; press **C**
to recenter (set the current pose as "looking at screen centre", like a VR headset), **S** to
toggle the diagnostics panel (tracker rate here — audio health lives in the passthrough), **Q**
to quit. The look is shared with the passthrough via :mod:`_console`.

Composed from tested parts: the 1€ smoothing (`tracking.PoseFilter`), the camera→world
calibration (`calibrate`, B2.2), the world layout (`world`, B1.4), the radar geometry
(`radar`) and the real-time diagnosis (`diagnostics`). Only the camera, MediaPipe and the
OpenCV drawing are host-side, so this runs on the Windows host, not CI/WSL:

    py -3.12 apps/calibration_gui.py --model face_landmarker.task
    # a pre-launch menu picks the camera; --no-dialog uses --camera N instead.
    # flags: --camera 0  --min-cutoff 1.0 --beta 0.3  --yaw-sign ±1 --pitch-sign ±1 --roll-sign ±1

The sign flags confirm the camera's axis directions against the live feed (B2.2): turn
left — the ring should swing right; nod up — it should tilt down. Flip a sign if not.
"""

from __future__ import annotations

import argparse
import time
from collections import deque
from pathlib import Path

import _console as C
import numpy as np

from binaural_headtracker.calibrate import HeadCalibration, calibrate_neutral, directions_for_pose
from binaural_headtracker.devices import camera_choices, probe_cameras, settings_dialog
from binaural_headtracker.diagnostics import classify
from binaural_headtracker.head_pose import head_pose_from_matrix
from binaural_headtracker.radar import nearest_front_channel
from binaural_headtracker.tracking import HeadPose, PoseFilter
from binaural_headtracker.world import layout_7p1


def main(argv=None) -> int:
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="FaceLandmarker .task path")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--no-dialog", action="store_true",
                   help="skip the camera-picker menu and use --camera")
    p.add_argument("--min-cutoff", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=0.3)
    p.add_argument("--yaw-sign", type=float, default=1.0)
    p.add_argument("--pitch-sign", type=float, default=-1.0)
    p.add_argument("--roll-sign", type=float, default=1.0)
    args = p.parse_args(argv)

    cam_index = args.camera
    if not args.no_dialog:
        picked = settings_dialog(camera_choices(probe_cameras()), [],
                                 title="Calibration — camera", camera_default=args.camera)
        if picked is None:
            return 0  # cancelled
        if picked["camera"] is not None:
            cam_index = picked["camera"]

    cap = cv2.VideoCapture(cam_index)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # keep the freshest frame, don't trail buffered ones
    detector = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))

    layout = layout_7p1()
    channels = layout.channels
    pose_filter = PoseFilter(args.min_cutoff, args.beta)
    recent: deque[HeadPose] = deque(maxlen=20)
    cal = HeadCalibration(0.0, 0.0, 0.0, args.yaw_sign, args.pitch_sign, args.roll_sign)
    times: deque[float] = deque(maxlen=60)
    neutral_set = False
    show_stats = False
    phase = 0.0
    win = "Binaural head-tracker — calibration"

    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.monotonic()
        times.append(now)
        phase = (phase + 0.03) % 1.0
        # Estimate the pose from the TRUE (un-mirrored) frame so yaw keeps its real sign;
        # mirror only for display and flip the landmark x so the overlay still lines up.
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        frame = cv2.flip(frame, 1)  # mirror, so moving left looks left
        pose = None
        landmarks = None
        if result.facial_transformation_matrixes:
            mat = np.asarray(result.facial_transformation_matrixes[0])
            yaw, pitch, roll = head_pose_from_matrix(mat)
            pose = pose_filter.update(HeadPose(yaw, pitch, roll, now))
            recent.append(pose)
            if result.face_landmarks:
                landmarks = result.face_landmarks[0]

        span = times[-1] - times[0]
        rate = (len(times) - 1) / span if len(times) > 1 and span > 0 else 0.0
        dirs = directions_for_pose(cal, pose, layout) if pose else layout.head_relative_directions()
        nearest_i = nearest_front_channel(dirs)
        active = channels[nearest_i] if pose else "--"

        live = "TRACKING" if pose is not None else "NO FACE"
        live_col = C.GOOD if pose is not None else C.MUTED
        readout = [
            ("rate", f"{rate:3.0f}Hz", C.GOOD if rate >= 20 else C.ACTIVE),
            ("neutral", "set" if neutral_set else "--", C.GOOD if neutral_set else C.MUTED),
            ("active", active, C.ACTIVE),
        ]
        buttons = [("Recenter", "C", True), ("Stats", "S", False), ("Quit", "Q", False)]
        diag = classify(block_size=256, samplerate=48000, render_ms=0.0, render_ms_peak=0.0,
                        fifo_fill_blocks=0.0, cushion_blocks=0, pose_rate_hz=rate) if show_stats \
            else None

        canvas = C.compose(
            frame, title=f"calibration — webcam {cam_index}", live=live, live_color=live_col,
            pose=pose, landmarks=landmarks, lock=None, dirs=dirs, channels=channels,
            nearest_i=nearest_i, readout=readout, buttons=buttons, diagnosis=diag,
            show_stats=show_stats, audio_stats=False, rate_hz=rate, phase=phase)
        cv2.imshow(win, canvas)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            show_stats = not show_stats
        if key == ord("c") and recent:
            cal = calibrate_neutral(list(recent), yaw_sign=args.yaw_sign,
                                    pitch_sign=args.pitch_sign, roll_sign=args.roll_sign)
            neutral_set = True

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

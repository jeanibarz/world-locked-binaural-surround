"""Calibration console (GUI) — VR-style recenter + live world-lock radar (B6.1, layout A).

A single OpenCV window, side-by-side: LEFT the webcam with the MediaPipe face mesh and
a yaw/pitch/roll HUD; RIGHT a top-down azimuth radar — you at the centre facing up, the
**world-locked 7.1 ring** around you, and the nearest (active) speaker highlighted. Turn
your head and the ring swings the opposite way; press **C** to recenter (set the current
pose as "looking at screen centre", like a VR headset), **Q** to quit.

Composed entirely from tested parts: the 1€ smoothing (`tracking.PoseFilter`), the
camera→world calibration (`calibrate`, B2.2), the world layout (`world`, B1.4) and the
radar geometry (`radar`, unit-tested). Only the camera, MediaPipe and the OpenCV drawing
are host-side, so this runs on the Windows host, not CI/WSL:

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

import numpy as np

from binaural_headtracker.calibrate import HeadCalibration, calibrate_neutral, directions_for_pose
from binaural_headtracker.devices import camera_choices, probe_cameras, settings_dialog
from binaural_headtracker.head_pose import head_pose_from_matrix
from binaural_headtracker.radar import azimuth_to_radar_xy, nearest_front_channel
from binaural_headtracker.tracking import HeadPose, PoseFilter
from binaural_headtracker.world import layout_7p1

# Dark-console palette in OpenCV BGR.
BG = (23, 16, 13)
PANEL = (36, 26, 21)
LINE = (68, 51, 42)
FG = (245, 237, 232)
MUTED = (171, 151, 139)
ACCENT = (214, 214, 56)   # cyan
ACTIVE = (75, 178, 246)   # amber
GOOD = (140, 217, 84)


def _text(img, s, x, y, color, scale=0.42) -> None:
    import cv2

    cv2.putText(img, s, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def _draw_hud(frame, pose: HeadPose) -> None:
    import cv2

    h = frame.shape[0]
    for i, (lbl, val) in enumerate([("yaw", pose.yaw), ("pitch", pose.pitch), ("roll", pose.roll)]):
        y = h - 58 + i * 20
        _text(frame, f"{lbl:<5}", 12, y, MUTED)
        x0 = 70
        cv2.rectangle(frame, (x0, y - 9), (x0 + 96, y - 3), (40, 40, 40), -1)
        cv2.line(frame, (x0 + 48, y - 10), (x0 + 48, y - 2), (120, 120, 120), 1)
        px = int(x0 + 48 + np.clip(val, -45, 45) / 45 * 48)
        cv2.line(frame, (px, y - 11), (px, y - 1), ACTIVE, 2)
        _text(frame, f"{val:+5.1f}", x0 + 104, y, ACCENT)


def _draw_landmarks(frame, landmarks, *, mirror=False) -> None:
    import cv2

    h, w = frame.shape[:2]
    for lm in landmarks:
        x = (1.0 - lm.x) if mirror else lm.x  # landmarks come from the un-mirrored frame
        cv2.circle(frame, (int(x * w), int(lm.y * h)), 1, ACCENT, -1, cv2.LINE_AA)


def _draw_radar(canvas, cx, cy, R, dirs, channels, nearest_i) -> None:
    import cv2

    for f in (1.0, 0.66, 0.33):
        cv2.circle(canvas, (cx, cy), int(R * f), LINE, 1, cv2.LINE_AA)
    for lbl, az in (("FRONT", 0), ("LEFT", 90), ("BACK", 180), ("RIGHT", -90)):
        x, y = azimuth_to_radar_xy(az, R + 16, cx, cy)
        _text(canvas, lbl, x - 18, y + 4, MUTED, 0.38)
    ax, ay = azimuth_to_radar_xy(float(dirs[nearest_i][0]), R, cx, cy)
    cv2.line(canvas, (cx, cy), (int(ax), int(ay)), ACTIVE, 1, cv2.LINE_AA)
    for i, ch in enumerate(channels):
        x, y = azimuth_to_radar_xy(float(dirs[i][0]), R, cx, cy)
        on = i == nearest_i
        col = ACTIVE if on else ACCENT
        cv2.circle(canvas, (int(x), int(y)), 8 if on else 5, col, -1, cv2.LINE_AA)
        if on:
            cv2.circle(canvas, (int(x), int(y)), 13, ACTIVE, 1, cv2.LINE_AA)
        tx, ty = azimuth_to_radar_xy(float(dirs[i][0]), R - 20, cx, cy)
        _text(canvas, ch, tx - 8, ty + 4, ACTIVE if on else MUTED, 0.36)
    # head arrow (listener), pointing up = front
    arrow = np.array([[cx, cy - 13], [cx + 8, cy + 8], [cx, cy + 3], [cx - 8, cy + 8]])
    cv2.fillPoly(canvas, [arrow], FG)


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
    detector = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))

    layout = layout_7p1()
    channels = layout.channels
    pose_filter = PoseFilter(args.min_cutoff, args.beta)
    recent: deque[HeadPose] = deque(maxlen=20)
    cal = HeadCalibration(0.0, 0.0, 0.0, args.yaw_sign, args.pitch_sign, args.roll_sign)
    times: deque[float] = deque(maxlen=60)
    CAM_W, CAM_H, RAD_W, BAR = 600, 540, 400, 60
    win = "Binaural head-tracker — calibration"

    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.monotonic()
        times.append(now)
        # Estimate the pose from the TRUE (un-mirrored) frame so yaw keeps its real sign
        # (mirroring would invert it → the radar swings the wrong way); mirror only for
        # display and flip the landmark x so the overlay still lines up.
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
        frame = cv2.flip(frame, 1)  # mirror, so moving left looks left
        pose = None
        if result.facial_transformation_matrixes:
            mat = np.asarray(result.facial_transformation_matrixes[0])
            yaw, pitch, roll = head_pose_from_matrix(mat)
            pose = pose_filter.update(HeadPose(yaw, pitch, roll, now))
            recent.append(pose)
            if result.face_landmarks:
                _draw_landmarks(frame, result.face_landmarks[0], mirror=True)
            _draw_hud(frame, pose)

        canvas = np.full((CAM_H + BAR, CAM_W + RAD_W, 3), BG, np.uint8)
        canvas[0:CAM_H, 0:CAM_W] = cv2.resize(frame, (CAM_W, CAM_H))
        cv2.rectangle(canvas, (CAM_W, 0), (CAM_W + RAD_W, CAM_H), PANEL, -1)
        cv2.line(canvas, (CAM_W, 0), (CAM_W, CAM_H), LINE, 1)

        dirs = directions_for_pose(cal, pose, layout) if pose else layout.head_relative_directions()
        nearest_i = nearest_front_channel(dirs)
        _draw_radar(canvas, CAM_W + RAD_W // 2, CAM_H // 2, 150, dirs, channels, nearest_i)

        span = times[-1] - times[0]
        rate = (len(times) - 1) / span if len(times) > 1 and span > 0 else 0.0
        active = channels[nearest_i] if pose else "--"
        status = f"rate {rate:4.1f} Hz   active {active}   [C] recenter   [Q] quit"
        if pose is None:
            status = "no face detected — look at the camera     [Q] quit"
        _text(canvas, status, 14, CAM_H + 38, FG, 0.5)

        cv2.imshow(win, canvas)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("c") and recent:
            cal = calibrate_neutral(list(recent), yaw_sign=args.yaw_sign,
                                    pitch_sign=args.pitch_sign, roll_sign=args.roll_sign)

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

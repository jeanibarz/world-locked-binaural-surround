"""B2.1 spike — live head tracker: webcam → MediaPipe → smoothed pose on a thread.

Wires the portable core (`binaural_headtracker.tracking.HeadTracker` + the 1€ filter)
to a real camera: a generator reads frames, MediaPipe FaceLandmarker yields the 4x4
facial-transformation matrix, `head_pose_from_matrix` turns it into (yaw, pitch, roll),
and the tracker smooths it on its own thread. Prints the live smoothed pose plus the
measured update rate / jitter, and the per-frame inference latency (the <50 ms budget,
requirements §2 / NFR-1).

Host-only (needs a camera, `mediapipe`, `opencv-python`, and the FaceLandmarker
`.task` model) — run on the Windows host, not on CI/WSL:

    py -3.12 spikes/b2_1_head_tracker.py --model face_landmarker.task [--seconds 20]
    # tune smoothing: --min-cutoff 1.0 --beta 0.3   (lower cutoff = steadier/laggier)

The portable logic it exercises is unit-tested headless in tests/test_tracking.py; this
spike only adds the camera + model, which cannot run without hardware.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Iterator
from pathlib import Path

from binaural_headtracker.head_pose import head_pose_from_matrix
from binaural_headtracker.tracking import HeadPose, HeadTracker


def pose_source(cap, detector, stop_at: float, latencies: list[float]) -> Iterator[HeadPose]:
    """Yield raw HeadPose per frame until ``stop_at`` (monotonic seconds)."""
    import cv2  # noqa: PLC0415 (host-only dep)
    import numpy as np

    while time.monotonic() < stop_at:
        ok, frame = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        import mediapipe as mp  # noqa: PLC0415

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        t0 = time.perf_counter()
        result = detector.detect(mp_image)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        mats = getattr(result, "facial_transformation_matrixes", None)
        if not mats:
            continue
        yaw, pitch, roll = head_pose_from_matrix(np.asarray(mats[0]))
        yield HeadPose(yaw=yaw, pitch=pitch, roll=roll, t=time.monotonic())


def main(argv=None) -> int:
    from binaural_headtracker.head_pose import summarize_latencies

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="FaceLandmarker .task path")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--min-cutoff", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=0.3)
    args = p.parse_args(argv)

    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision

    cap = cv2.VideoCapture(args.camera)
    opts = vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True,
        num_faces=1,
    )
    detector = vision.FaceLandmarker.create_from_options(opts)

    latencies: list[float] = []
    stop_at = time.monotonic() + args.seconds
    src = pose_source(cap, detector, stop_at, latencies)
    tracker = HeadTracker(src, min_cutoff=args.min_cutoff, beta=args.beta)
    tracker.start()
    try:
        while not tracker.wait(0.25):
            pose = tracker.latest()
            if pose is not None:
                print(f"\ryaw {pose.yaw:+6.1f}  pitch {pose.pitch:+6.1f}  roll {pose.roll:+6.1f}"
                      f"   {tracker.stats()['rate_hz']:5.1f} Hz", end="", flush=True)
    finally:
        tracker.stop()
        cap.release()

    print("\n--- summary ---")
    print("tracker stats:", tracker.stats())
    if latencies:
        print("inference latency (ms):", summarize_latencies(latencies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

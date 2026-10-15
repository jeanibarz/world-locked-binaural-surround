"""B0.4 — Windows head-tracker spike (MediaPipe FaceLandmarker → yaw/pitch/roll).

Runs on the **Windows host** (the webcam lives there), driven from the WSL repo
via interop::

    WINPATH=$(wslpath -w spikes/b0_4_head_tracker.py)
    py.exe -3.12 "$WINPATH" --frames 120

Needs ``mediapipe`` + ``opencv-python`` + ``numpy`` on the Windows interpreter
(``py -3.12 -m pip install mediapipe opencv-python``; recorded as the ``vision``
optional-extra in ``pyproject.toml``). Both are Apache-2.0.

It also needs the FaceLandmarker model (a ~3.7 MB ``.task`` file, kept out of git;
see ``docs/head-tracker-setup.md``). Default location
``spikes/models/face_landmarker.task``; override with ``--model`` or ``$BHT_FACE_MODEL``.

What it does, and why:

1. Open the webcam and run **FaceLandmarker in IMAGE mode** with
   ``output_facial_transformation_matrixes`` — that gives, per frame, a 4x4
   camera-space head transform whose rotation block is the head orientation
   (FR-4), so no manual solvePnP is needed.
2. Convert each transform to **(yaw, pitch, roll)** via the portable
   ``head_pose`` helpers (unit-tested on WSL).
3. **Measure inference latency** per frame (the ``detect()`` call) and the full
   grab→pose cost, excluding a warm-up, and summarise against the <~50 ms
   tracking-latency budget (requirements § 2 / NFR-1).

Prints a human summary and a ``RESULT_JSON:`` line for the journal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cv2  # noqa: E402
import mediapipe as mp  # noqa: E402
from mediapipe.tasks.python import BaseOptions  # noqa: E402
from mediapipe.tasks.python.vision import (  # noqa: E402
    FaceLandmarker,
    FaceLandmarkerOptions,
    RunningMode,
)

from binaural_headtracker.head_pose import head_pose_from_matrix, summarize_latencies  # noqa: E402

DEFAULT_MODEL = Path(__file__).resolve().parent / "models" / "face_landmarker.task"


def open_camera(index: int):
    """Open a webcam, trying the given index first then a few fallbacks.

    Uses DirectShow on Windows (faster, more reliable open than MSMF here) and
    verifies a frame actually reads before returning.
    """
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else 0
    for idx in [index, *[i for i in range(0, 4) if i != index]]:
        cap = cv2.VideoCapture(idx, backend)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                return cap, idx
            cap.release()
    return None, None


def settle_camera(cap, seconds: float) -> None:
    """Read and discard frames for ``seconds`` so the sensor's auto-exposure and
    white-balance converge before measuring.

    A USB webcam (e.g. the Logi C270) hands back dark/garbage frames for the
    first ~1-2 s after opening while it powers up and auto-exposes; running
    detection on those just yields spurious misses, so we drain them first.
    """
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        cap.read()


def build_detector(model_path: Path) -> FaceLandmarker:
    options = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=RunningMode.IMAGE,
        num_faces=1,
        output_facial_transformation_matrixes=True,
    )
    return FaceLandmarker.create_from_options(options)


def main() -> int:
    ap = argparse.ArgumentParser(description="B0.4 Windows head-tracker spike")
    ap.add_argument("--frames", type=int, default=120, help="frames to process")
    ap.add_argument("--warmup", type=int, default=5, help="warm-up frames excluded from stats")
    ap.add_argument("--camera", type=int, default=0, help="preferred camera index")
    ap.add_argument("--settle", type=float, default=1.5, help="seconds to warm up the camera")
    ap.add_argument(
        "--model", type=str, default=os.environ.get("BHT_FACE_MODEL", str(DEFAULT_MODEL))
    )
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    model_path = Path(args.model)
    print(f"mediapipe {mp.__version__}  |  cv2 {cv2.__version__}  |  platform {sys.platform}")
    if not model_path.exists():
        print(f"\nMODEL MISSING: {model_path}")
        print("Fetch it (see docs/head-tracker-setup.md):")
        print("  curl -L -o spikes/models/face_landmarker.task \\")
        print(
            "    https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task"
        )
        print("\nRESULT_JSON:" + json.dumps({"status": "model_missing", "model": str(model_path)}))
        return 1

    cap, cam_idx = open_camera(args.camera)
    if cap is None:
        print("NO CAMERA: could not open a webcam that yields frames.")
        print("\nRESULT_JSON:" + json.dumps({"status": "no_camera"}))
        return 1
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"camera #{cam_idx}: {width}x{height}  (settling {args.settle:.1f}s)")
    settle_camera(cap, args.settle)

    detector = build_detector(model_path)

    infer_ms: list[float] = []
    frame_ms: list[float] = []
    warmup_infer_ms: list[float] = []
    poses: list[tuple[float, float, float]] = []
    detected = 0
    processed = 0

    try:
        for i in range(args.frames):
            t0 = time.perf_counter()
            ok, bgr = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            t1 = time.perf_counter()
            result = detector.detect(mp_image)
            t2 = time.perf_counter()

            processed += 1
            infer = 1000.0 * (t2 - t1)
            frame = 1000.0 * (t2 - t0)
            if i < args.warmup:
                warmup_infer_ms.append(infer)
            else:
                infer_ms.append(infer)
                frame_ms.append(frame)

            mats = result.facial_transformation_matrixes
            if mats:
                detected += 1
                m = np.asarray(mats[0], dtype=np.float64).reshape(4, 4)
                yaw, pitch, roll = head_pose_from_matrix(m)
                if i >= args.warmup and len(poses) < 8:
                    poses.append((round(yaw, 2), round(pitch, 2), round(roll, 2)))
    finally:
        cap.release()
        detector.close()

    result_json: dict = {
        "status": "ok" if infer_ms else "no_steady_frames",
        "mediapipe": mp.__version__,
        "camera_index": cam_idx,
        "resolution": [width, height],
        "frames_processed": processed,
        "faces_detected": detected,
        "detection_rate": round(detected / processed, 3) if processed else 0.0,
        "warmup_first_infer_ms": round(warmup_infer_ms[0], 2) if warmup_infer_ms else None,
        "inference_ms": summarize_latencies(infer_ms) if infer_ms else None,
        "frame_grab_to_pose_ms": summarize_latencies(frame_ms) if frame_ms else None,
        "sample_poses_yaw_pitch_roll_deg": poses,
    }

    print(
        f"\nframes: {processed}  faces: {detected}  detection rate: {result_json['detection_rate']}"
    )
    print(f"warm-up first inference: {result_json['warmup_first_infer_ms']} ms")
    print(f"inference latency      : {result_json['inference_ms']}")
    print(f"grab->pose latency     : {result_json['frame_grab_to_pose_ms']}")
    print(f"sample poses (y,p,r)   : {poses}")
    print("\nRESULT_JSON:" + json.dumps(result_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Portable helpers for the B0.4 head-tracker spike.

The hardware/model code (webcam + MediaPipe FaceLandmarker) lives in
``spikes/b0_4_head_tracker.py`` and runs on the Windows host via interop;
everything here is pure and portable so the **pose math** and the **latency
statistics** are unit-tested on WSL/CI with no camera, no MediaPipe, no OpenCV.

Two jobs:

* turn MediaPipe's 4x4 facial-transformation matrix into a ``(yaw, pitch, roll)``
  head-pose estimate (FR-4: camera → head orientation); and
* summarise per-frame inference latencies into the numbers the <~50 ms
  head-tracking budget (requirements § 2 / NFR-1) is judged against.

Angle convention: the rotation is decomposed as ``R = Rz(roll) @ Ry(yaw) @
Rx(pitch)`` — pitch about X (nod up/down), yaw about Y (turn left/right), roll
about Z (tilt). The *sign/axis mapping* to "looking left = +yaw" and the neutral
"facing the screen" pose are deliberately left to calibration (B2.2); this spike
only has to produce a stable estimate and measure its cost.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def rotation_from_matrix(matrix) -> np.ndarray:
    """Return the scale-normalised 3x3 rotation from a 3x3 or 4x4 transform.

    MediaPipe's facial-transformation matrix carries rotation + translation and
    may include a small scale; we take the 3x3 linear block and normalise each
    column to unit length, so Euler extraction sees a pure rotation.
    """
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape not in {(3, 3), (4, 4)}:
        raise ValueError("expected a 3x3 or 4x4 matrix")
    r = m[:3, :3].astype(np.float64, copy=True)
    norms = np.linalg.norm(r, axis=0)
    if np.any(norms < 1e-12):
        raise ValueError("degenerate rotation block (zero-length column)")
    return r / norms


def rotation_matrix_to_euler(rotation) -> tuple[float, float, float]:
    """Return ``(yaw, pitch, roll)`` in degrees from a 3x3 rotation matrix.

    Uses the standard singularity-guarded decomposition for
    ``R = Rz(roll) @ Ry(yaw) @ Rx(pitch)``. Near gimbal lock (yaw ≈ ±90°) roll is
    pinned to 0 and folded into yaw, which is the conventional degenerate choice.
    """
    r = np.asarray(rotation, dtype=np.float64)
    if r.shape != (3, 3):
        raise ValueError("expected a 3x3 matrix")
    # cos(yaw): length of the X-axis projection remaining in the XY image plane.
    cy = math.hypot(r[0, 0], r[1, 0])
    if cy > 1e-6:
        pitch = math.atan2(r[2, 1], r[2, 2])
        yaw = math.atan2(-r[2, 0], cy)
        roll = math.atan2(r[1, 0], r[0, 0])
    else:  # gimbal lock: yaw ≈ ±90°, roll is indeterminate → fold into pitch
        pitch = math.atan2(-r[1, 2], r[1, 1])
        yaw = math.atan2(-r[2, 0], cy)
        roll = 0.0
    return (math.degrees(yaw), math.degrees(pitch), math.degrees(roll))


def head_pose_from_matrix(matrix) -> tuple[float, float, float]:
    """Convenience: ``(yaw, pitch, roll)`` degrees straight from a 4x4 transform."""
    return rotation_matrix_to_euler(rotation_from_matrix(matrix))


def summarize_latencies(samples_ms: Sequence[float]) -> dict:
    """Summarise per-frame latencies (ms): count/mean/median/p95/min/max + fps.

    ``fps`` is ``1000/mean`` — the sustained frame rate implied by the mean
    inference cost, the figure to compare against the tracking-latency budget.
    """
    a = np.asarray(list(samples_ms), dtype=np.float64)
    if a.size == 0:
        raise ValueError("no latency samples")
    if np.any(a < 0):
        raise ValueError("latencies must be non-negative")
    mean = float(a.mean())
    return {
        "count": int(a.size),
        "mean_ms": round(mean, 3),
        "median_ms": round(float(np.median(a)), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
        "min_ms": round(float(a.min()), 3),
        "max_ms": round(float(a.max()), 3),
        "fps": round(1000.0 / mean, 2) if mean > 0 else None,
    }

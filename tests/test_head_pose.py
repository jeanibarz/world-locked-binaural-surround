"""Unit tests for the portable B0.4 head-pose + latency helpers.

These run anywhere (no camera, no MediaPipe, no OpenCV): they check the
rotation→Euler extraction against independently-built rotation matrices and the
latency summary against hand-computed values.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from binaural_headtracker.head_pose import (
    head_pose_from_matrix,
    rotation_from_matrix,
    rotation_matrix_to_euler,
    summarize_latencies,
)


def _rx(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def _ry(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def _rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def test_identity_is_zero_pose():
    yaw, pitch, roll = rotation_matrix_to_euler(np.eye(3))
    assert (abs(yaw), abs(pitch), abs(roll)) == pytest.approx((0, 0, 0), abs=1e-9)


@pytest.mark.parametrize("deg", [-40, -15, 10, 30])
def test_pure_single_axis_rotations_recovered(deg):
    # Convention R = Rz(roll) @ Ry(yaw) @ Rx(pitch); a single-axis rotation must
    # come back on that axis only.
    yaw, pitch, roll = rotation_matrix_to_euler(_ry(deg))
    assert (yaw, pitch, roll) == pytest.approx((deg, 0, 0), abs=1e-6)
    yaw, pitch, roll = rotation_matrix_to_euler(_rx(deg))
    assert (yaw, pitch, roll) == pytest.approx((0, deg, 0), abs=1e-6)
    yaw, pitch, roll = rotation_matrix_to_euler(_rz(deg))
    assert (yaw, pitch, roll) == pytest.approx((0, 0, deg), abs=1e-6)


def test_composed_rotation_recovered():
    yaw_t, pitch_t, roll_t = 25.0, -12.0, 8.0
    r = _rz(roll_t) @ _ry(yaw_t) @ _rx(pitch_t)
    yaw, pitch, roll = rotation_matrix_to_euler(r)
    assert (yaw, pitch, roll) == pytest.approx((yaw_t, pitch_t, roll_t), abs=1e-6)


def test_gimbal_lock_yaw_90_is_stable():
    yaw, pitch, roll = rotation_matrix_to_euler(_ry(90))
    assert yaw == pytest.approx(90, abs=1e-6)
    assert roll == 0.0  # folded, by convention


def test_rotation_from_matrix_strips_scale_and_translation():
    r = _ry(30)
    m = np.eye(4)
    m[:3, :3] = r * 1.7  # uniform scale
    m[:3, 3] = [10.0, -5.0, 2.0]  # translation (ignored)
    out = rotation_from_matrix(m)
    assert np.allclose(out, r, atol=1e-9)
    # columns are unit length again
    assert np.allclose(np.linalg.norm(out, axis=0), 1.0, atol=1e-9)


def test_head_pose_from_matrix_end_to_end():
    r = _rz(8) @ _ry(25) @ _rx(-12)
    m = np.eye(4)
    m[:3, :3] = r
    assert head_pose_from_matrix(m) == pytest.approx((25, -12, 8), abs=1e-6)


def test_matrix_shape_errors():
    with pytest.raises(ValueError):
        rotation_matrix_to_euler(np.eye(4))
    with pytest.raises(ValueError):
        rotation_from_matrix(np.zeros((2, 2)))
    with pytest.raises(ValueError):
        rotation_from_matrix(np.zeros((4, 4)))  # degenerate (zero columns)


def test_summarize_latencies_values():
    s = summarize_latencies([10.0, 20.0, 30.0, 40.0])
    assert s["count"] == 4
    assert s["mean_ms"] == 25.0
    assert s["median_ms"] == 25.0
    assert s["min_ms"] == 10.0 and s["max_ms"] == 40.0
    assert s["fps"] == round(1000.0 / 25.0, 2)


def test_summarize_latencies_rejects_empty_and_negative():
    with pytest.raises(ValueError):
        summarize_latencies([])
    with pytest.raises(ValueError):
        summarize_latencies([5.0, -1.0])

"""Head-pose calibration: neutral pose + camera-angles → world head rotation (B2.2).

This closes the seam B1.4 deliberately left open: turning the tracker's
``(yaw, pitch, roll)`` (degrees, from `head_pose`, relative to *some* camera-defined
zero) into the **world←head rotation** ``R`` that
:meth:`world.WorldLayout.head_relative_directions` consumes, so the rendered scene
world-locks as the head moves.

Two jobs:

1. **Neutral pose.** The user looks at screen centre; we average a few samples to a
   neutral ``(yaw0, pitch0, roll0)``. Every later pose is taken *relative* to it, so
   at the neutral pose ``R = I`` and the layout sits at its nominal angles.

2. **Axis mapping (camera → world).** The head angles are mapped to the world frame
   (``+x`` front, ``+y`` left, ``+z`` up): **yaw → rotation about world up (+z)**,
   **pitch → about world left (+y)** (elevation), **roll → about world front (+x)**,
   composed ``Rz(Δyaw)·Ry(Δpitch)·Rx(Δroll)`` from the per-axis deltas. With this,
   a head yaw of +θ makes the whole scene azimuth shift by −θ — the world-lock
   mechanic B1.4 unit-tests.

The per-axis **sign** (which way the camera calls "+yaw") is hardware/convention
dependent and is exposed as ``yaw_sign``/``pitch_sign``/``roll_sign``, to be confirmed
once on the host against the live camera (as B0.4 characterised the range). The
defaults — ``yaw_sign=+1``, ``pitch_sign=-1``, ``roll_sign=+1`` — give the correct
world-lock *direction* under the assumption that **+yaw = turning left** and
**+pitch = nodding up**: turning left sends a world-fixed front speaker to the right
(az −Δyaw), and nodding up sends it *below* the gaze (el < 0, hence ``pitch_sign=-1``).

Pure numpy; unit-tested headless.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from binaural_headtracker.tracking import HeadPose
from binaural_headtracker.world import WorldLayout


def _rot_x(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def _rot_y(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def _rot_z(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


@dataclass(frozen=True)
class HeadCalibration:
    """The neutral "looking at screen centre" pose + per-axis sign convention."""

    yaw0: float
    pitch0: float
    roll0: float
    yaw_sign: float = 1.0
    pitch_sign: float = -1.0  # +pitch = nodding up ⇒ world-fixed scene tilts down (el<0)
    roll_sign: float = 1.0


def calibrate_neutral(
    poses,
    *,
    yaw_sign: float = 1.0,
    pitch_sign: float = -1.0,
    roll_sign: float = 1.0,
) -> HeadCalibration:
    """Average a set of neutral samples (looking at screen centre) → a calibration.

    ``poses`` is a non-empty iterable of :class:`~binaural_headtracker.tracking.HeadPose`.
    Averaging the Euler angles directly is fine for the small spread of a steady gaze.
    """
    ys, ps, rs = [], [], []
    for p in poses:
        ys.append(p.yaw)
        ps.append(p.pitch)
        rs.append(p.roll)
    if not ys:
        raise ValueError("need at least one neutral pose to calibrate")
    return HeadCalibration(
        yaw0=float(np.mean(ys)),
        pitch0=float(np.mean(ps)),
        roll0=float(np.mean(rs)),
        yaw_sign=float(yaw_sign),
        pitch_sign=float(pitch_sign),
        roll_sign=float(roll_sign),
    )


def head_rotation(cal: HeadCalibration, pose: HeadPose) -> np.ndarray:
    """World←head rotation ``R`` for ``pose`` relative to the neutral calibration.

    ``R = Rz(Δyaw)·Ry(Δpitch)·Rx(Δroll)`` on the sign-applied per-axis deltas from
    neutral (see module docstring). ``R = I`` at the neutral pose. Feed it to
    :meth:`world.WorldLayout.head_relative_directions`.
    """
    dyaw = cal.yaw_sign * (pose.yaw - cal.yaw0)
    dpitch = cal.pitch_sign * (pose.pitch - cal.pitch0)
    droll = cal.roll_sign * (pose.roll - cal.roll0)
    return _rot_z(dyaw) @ _rot_y(dpitch) @ _rot_x(droll)


def directions_for_pose(
    cal: HeadCalibration, pose: HeadPose, layout: WorldLayout
) -> np.ndarray:
    """Per-channel ``(az, el)`` (deg) for the current head pose — the world-lock output.

    Convenience: ``layout.head_relative_directions(head_rotation=head_rotation(cal, pose))``.
    Named distinctly from `WorldLayout.head_relative_directions` (which it wraps) to
    keep the B3.1 pipeline readable.
    """
    return layout.head_relative_directions(head_rotation=head_rotation(cal, pose))

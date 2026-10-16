"""Unit tests for B2.2 calibration — the camera→world head-rotation mapping.

The headline tests pin the world-lock contract: at the neutral pose the layout sits
at its nominal angles, and a head yaw of +θ shifts every speaker azimuth by −θ (the
mechanic B1.4 verified), now driven through a calibrated head pose.
"""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.calibrate import (
    _rot_x,
    _rot_y,
    _rot_z,
    calibrate_neutral,
    directions_for_pose,
    head_rotation,
)
from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1
from binaural_headtracker.tracking import HeadPose
from binaural_headtracker.world import layout_7p1


def _pose(yaw=0.0, pitch=0.0, roll=0.0, t=0.0):
    return HeadPose(yaw=yaw, pitch=pitch, roll=roll, t=t)


# --- rotation helpers -------------------------------------------------------


@pytest.mark.parametrize("rot", [_rot_x, _rot_y, _rot_z])
@pytest.mark.parametrize("deg", [0.0, 30.0, -45.0, 90.0])
def test_rotation_helpers_are_orthonormal(rot, deg):
    r = rot(deg)
    assert np.allclose(r @ r.T, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(r), 1.0)


# --- calibration ------------------------------------------------------------


def test_calibrate_averages_neutral_samples():
    rng = np.random.default_rng(0)
    poses = [_pose(2.0 + rng.normal(0, 0.5), -1.0 + rng.normal(0, 0.5), rng.normal(0, 0.5))
             for _ in range(200)]
    cal = calibrate_neutral(poses)
    assert cal.yaw0 == pytest.approx(2.0, abs=0.2)
    assert cal.pitch0 == pytest.approx(-1.0, abs=0.2)
    assert cal.roll0 == pytest.approx(0.0, abs=0.2)


def test_calibrate_empty_raises():
    with pytest.raises(ValueError):
        calibrate_neutral([])


# --- the world-lock contract ------------------------------------------------


def test_neutral_pose_gives_identity_and_nominal_layout():
    cal = calibrate_neutral([_pose(5.0, -3.0, 1.0)])  # neutral anywhere
    R = head_rotation(cal, _pose(5.0, -3.0, 1.0))  # same pose → no delta
    assert np.allclose(R, np.eye(3), atol=1e-12)
    layout = layout_7p1()
    dirs = directions_for_pose(cal, _pose(5.0, -3.0, 1.0), layout)
    for i, ch in enumerate(layout.channels):
        az_nom, el_nom = SPEAKER_LAYOUT_7P1[ch]
        assert dirs[i, 0] == pytest.approx(az_nom, abs=1e-6)
        assert dirs[i, 1] == pytest.approx(el_nom, abs=1e-6)


def test_yaw_turn_shifts_every_azimuth_by_minus_yaw():
    # The world-lock mechanic: head yaw +theta ⇒ every speaker azimuth −theta.
    cal = calibrate_neutral([_pose(0.0, 0.0, 0.0)])
    layout = layout_7p1()
    nominal = directions_for_pose(cal, _pose(0.0, 0.0, 0.0), layout)
    turned = directions_for_pose(cal, _pose(30.0, 0.0, 0.0), layout)  # +30° yaw
    for i in range(layout.n_channels):
        diff = (turned[i, 0] - nominal[i, 0] + 180.0) % 360.0 - 180.0
        assert diff == pytest.approx(-30.0, abs=1e-6)
        assert turned[i, 1] == pytest.approx(nominal[i, 1], abs=1e-6)  # elevation unchanged


def test_pitch_up_drops_the_front_speaker_below_the_gaze():
    # World-lock DIRECTION (not just magnitude): nodding up (+pitch) must send the
    # world-fixed front speaker BELOW the gaze → negative elevation (default
    # pitch_sign=-1). A sign error here would tilt the scene the wrong way.
    cal = calibrate_neutral([_pose(0.0, 0.0, 0.0)])  # default signs
    layout = layout_7p1()
    c = layout.channels.index("C")  # front-centre, az 0 / el 0 at neutral
    up = directions_for_pose(cal, _pose(0.0, 20.0, 0.0), layout)  # nod up 20°
    assert up[c, 1] == pytest.approx(-20.0, abs=1e-6)  # scene tilts DOWN
    assert up[c, 0] == pytest.approx(0.0, abs=1e-6)    # azimuth unchanged on median plane
    # pitch_sign=+1 would (wrongly) tilt it up:
    cal_up = calibrate_neutral([_pose(0.0, 0.0, 0.0)], pitch_sign=+1.0)
    el_up = directions_for_pose(cal_up, _pose(0.0, 20.0, 0.0), layout)[c, 1]
    assert el_up == pytest.approx(20.0, abs=1e-6)


def test_yaw_sign_flips_the_direction():
    layout = layout_7p1()
    pos = calibrate_neutral([_pose(0.0, 0.0, 0.0)], yaw_sign=+1.0)
    neg = calibrate_neutral([_pose(0.0, 0.0, 0.0)], yaw_sign=-1.0)
    c = layout.channels.index("L")
    d_pos = directions_for_pose(pos, _pose(30.0, 0.0, 0.0), layout)[c, 0]
    d_neg = directions_for_pose(neg, _pose(30.0, 0.0, 0.0), layout)[c, 0]
    # opposite azimuth shift relative to the nominal 30°
    assert (d_pos - 30.0) == pytest.approx(-(d_neg - 30.0), abs=1e-6)

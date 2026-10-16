"""Unit tests for the 7.1 world layout model (B1.4).

The geometry is pure math, so everything here is exact: the fixed head at the
world origin must reproduce the nominal SPEAKER_LAYOUT_7P1 angles, scale/distance
must leave a centred head's directions untouched, and the Rᵀ(p−p_h) projection
must behave correctly under a translated / rotated head (the M3 seam, exercised
here with hand-built rotation matrices).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from binaural_headtracker.surround import CHANNELS_7P1, SPEAKER_LAYOUT_7P1
from binaural_headtracker.world import (
    DEFAULT_DISTANCE_M,
    WorldLayout,
    direction_to_unit,
    layout_7p1,
    unit_to_direction,
)

# --- direction <-> unit vector round-trip ------------------------------------


@pytest.mark.parametrize(
    "az,el",
    [(0, 0), (30, 0), (-30, 0), (90, 0), (-90, 0), (135, 0), (-135, 0), (0, 45), (60, -20)],
)
def test_direction_unit_roundtrip(az, el):
    vec = direction_to_unit(az, el)
    assert np.isclose(np.linalg.norm(vec), 1.0)
    back = unit_to_direction(vec)
    assert np.isclose(back[0], az, atol=1e-9)
    assert np.isclose(back[1], el, atol=1e-9)


def test_front_is_plus_x_left_is_plus_y_up_is_plus_z():
    assert np.allclose(direction_to_unit(0, 0), [1, 0, 0])  # front
    assert np.allclose(direction_to_unit(90, 0), [0, 1, 0], atol=1e-9)  # left ear
    assert np.allclose(direction_to_unit(0, 90), [0, 0, 1], atol=1e-9)  # up


def test_unit_to_direction_vectorised():
    vecs = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0]], dtype=float)
    out = unit_to_direction(vecs)
    assert out.shape == (4, 2)
    assert np.allclose(out[:, 0], [0, 90, 0, 180])  # azimuth
    assert np.allclose(out[:, 1], [0, 0, 90, 0])  # elevation


def test_unit_to_direction_rejects_zero_vector():
    with pytest.raises(ValueError):
        unit_to_direction(np.zeros(3))
    with pytest.raises(ValueError):
        unit_to_direction(np.array([3, 4]))  # wrong last axis


# --- the nominal 7.1 layout ---------------------------------------------------


def test_layout_7p1_reproduces_nominal_angles_for_fixed_head():
    layout = layout_7p1()
    assert layout.channels == CHANNELS_7P1
    dirs = layout.head_relative_directions()  # neutral head at origin
    assert dirs.shape == (8, 2)
    for ch, (az, el) in zip(CHANNELS_7P1, dirs, strict=True):
        assert np.isclose(az, SPEAKER_LAYOUT_7P1[ch][0], atol=1e-9), ch
        assert np.isclose(el, SPEAKER_LAYOUT_7P1[ch][1], atol=1e-9), ch


def test_default_distance_and_scale_are_direction_invariant():
    near = layout_7p1(distance=1.0)
    far = layout_7p1(distance=5.0)
    assert np.allclose(near.distances, 1.0)
    assert np.allclose(far.distances, 5.0)
    # a centred head sees identical directions regardless of distance / scale
    assert np.allclose(
        near.head_relative_directions(), far.head_relative_directions(), atol=1e-9
    )
    assert np.allclose(
        near.head_relative_directions(),
        near.scaled(10.0).head_relative_directions(),
        atol=1e-9,
    )


def test_per_channel_distance_override():
    layout = layout_7p1(distance=2.0, distances={"C": 0.5}, scale=2.0)
    d = layout.distances
    assert np.isclose(d[layout.index("C")], 1.0)  # 0.5 * scale 2
    assert np.isclose(d[layout.index("L")], 4.0)  # 2.0 * scale 2


def test_elevation_override():
    layout = layout_7p1(elevations={"C": 15.0})
    dirs = layout.head_relative_directions()
    assert np.isclose(dirs[layout.index("C")][1], 15.0, atol=1e-9)


def test_front_speaker_is_straight_ahead_on_plus_x():
    layout = layout_7p1(distance=3.0)
    c = layout.positions[layout.index("C")]
    assert np.allclose(c, [3.0, 0.0, 0.0], atol=1e-9)


# --- the world-lock projection (M3 seam) -------------------------------------


def _yaw_matrix(deg: float) -> np.ndarray:
    """World<-head rotation for a head turned `deg` toward +azimuth, about +z (up)."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def test_head_turn_rotates_directions_opposite_to_the_turn():
    # Head yaws +30deg (turns left). A speaker dead ahead in the world should now
    # sit at -30deg azimuth in the head frame (it moved to the head's right).
    layout = WorldLayout(channels=("C",), positions=np.array([[2.0, 0.0, 0.0]]))
    dirs = layout.head_relative_directions(head_rotation=_yaw_matrix(30.0))
    assert np.isclose(dirs[0][0], -30.0, atol=1e-9)
    assert np.isclose(dirs[0][1], 0.0, atol=1e-9)


def test_full_ring_shifts_rigidly_under_head_yaw():
    layout = layout_7p1()
    base = layout.head_relative_directions()
    turned = layout.head_relative_directions(head_rotation=_yaw_matrix(20.0))
    # every azimuth shifts by -20deg (mod 360), elevations unchanged
    daz = ((turned[:, 0] - base[:, 0] + 180) % 360) - 180
    assert np.allclose(daz, -20.0, atol=1e-9)
    assert np.allclose(turned[:, 1], base[:, 1], atol=1e-9)


def test_identity_rotation_matches_default():
    layout = layout_7p1()
    assert np.allclose(
        layout.head_relative_directions(head_rotation=np.eye(3)),
        layout.head_relative_directions(),
    )


def test_head_translation_changes_angles():
    # Head steps forward (+x) 1 m toward a front speaker at 2 m: the side speakers
    # swing further back; the centre stays dead ahead.
    layout = layout_7p1(distance=2.0)
    moved = layout.head_relative_directions(head_position=np.array([1.0, 0.0, 0.0]))
    base = layout.head_relative_directions()
    assert np.isclose(moved[layout.index("C")][0], 0.0, atol=1e-9)  # still ahead
    # side-left (90deg at origin) moves behind the listener -> |az| > 90
    assert abs(moved[layout.index("SL")][0]) > abs(base[layout.index("SL")][0])


def test_speaker_on_head_raises():
    layout = WorldLayout(channels=("C",), positions=np.array([[2.0, 0.0, 0.0]]))
    with pytest.raises(ValueError):
        layout.head_relative_directions(head_position=np.array([2.0, 0.0, 0.0]))


# --- validation ---------------------------------------------------------------


def test_bad_positions_shape_rejected():
    with pytest.raises(ValueError):
        WorldLayout(channels=("C",), positions=np.zeros((2, 3)))  # row count mismatch
    with pytest.raises(ValueError):
        WorldLayout(channels=("C",), positions=np.zeros((1, 2)))  # not 3 cols


def test_non_positive_scale_and_distance_rejected():
    with pytest.raises(ValueError):
        layout_7p1(distance=0.0)
    with pytest.raises(ValueError):
        layout_7p1().scaled(-1.0)
    with pytest.raises(ValueError):
        layout_7p1(distances={"C": -1.0})


def test_default_distance_constant():
    assert layout_7p1().distances[0] == DEFAULT_DISTANCE_M

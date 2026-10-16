"""Unit tests for the radar geometry (portable; no camera/OpenCV)."""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.radar import azimuth_to_radar_xy, nearest_front_channel


def test_front_is_up():
    x, y = azimuth_to_radar_xy(0.0, 100.0, cx=200.0, cy=200.0)
    assert x == pytest.approx(200.0)
    assert y == pytest.approx(100.0)  # up = smaller y


def test_left_right_back():
    cx = cy = 200.0
    xl, yl = azimuth_to_radar_xy(90.0, 100.0, cx, cy)   # +az → left
    assert (xl, yl) == pytest.approx((100.0, 200.0))
    xr, yr = azimuth_to_radar_xy(-90.0, 100.0, cx, cy)  # -az → right
    assert (xr, yr) == pytest.approx((300.0, 200.0))
    xb, yb = azimuth_to_radar_xy(180.0, 100.0, cx, cy)  # back → down
    assert (xb, yb) == pytest.approx((200.0, 300.0))


def test_radius_scales_distance_from_centre():
    for az in (0.0, 37.0, 150.0, -120.0):
        x, y = azimuth_to_radar_xy(az, 80.0, cx=0.0, cy=0.0)
        assert np.hypot(x, y) == pytest.approx(80.0)


def test_nearest_front_picks_smallest_absolute_azimuth():
    dirs = np.array([[30.0, 0.0], [-25.0, 0.0], [120.0, 0.0]])
    assert nearest_front_channel(dirs) == 1  # -25° is closest to front


def test_nearest_front_wraps_around_360():
    dirs = np.array([[350.0, 0.0], [20.0, 0.0]])  # 350° == 10° from front < 20°
    assert nearest_front_channel(dirs) == 0

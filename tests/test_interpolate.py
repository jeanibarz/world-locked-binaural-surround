"""Unit tests for the B1.2 direction interpolation (ITD-aware blending).

All hermetic: the delay/shift/weight math is tested directly, and
``interpolate_hrir`` is tested on small synthetic HRIR sets built in-memory —
no SOFA file, no audio device. The headline tests pin the two properties the
block exists for: (1) measured directions reproduce exactly, and (2) the result
is ITD-aware — the arrival delay interpolates between neighbours and the blend
does *not* comb-filter the way a naive average does.
"""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.hrir import HRIRSet
from binaural_headtracker.interpolate import (
    estimate_delay,
    fractional_delay,
    interpolate_hrir,
    interpolation_weights,
    nearest_indices,
)

# --- estimate_delay -------------------------------------------------------


def test_estimate_delay_on_delta_is_exact():
    x = np.zeros(64)
    x[10] = 1.0
    assert estimate_delay(x) == pytest.approx(10.0, abs=1e-9)


def test_estimate_delay_subsample_between_two_samples():
    # Equal energy at 10 and 11 → the parabolic peak sits halfway.
    x = np.zeros(64)
    x[10] = x[11] = 1.0
    assert estimate_delay(x) == pytest.approx(10.5, abs=1e-6)


def test_estimate_delay_zero_ir():
    assert estimate_delay(np.zeros(16)) == 0.0


# --- fractional_delay -----------------------------------------------------


def test_fractional_delay_integer_shift_is_exact():
    x = np.zeros(64)
    x[5] = 1.0
    y = fractional_delay(x, 3.0)
    assert np.argmax(y) == 8
    assert y[8] == pytest.approx(1.0, abs=1e-9)


def test_fractional_delay_subsample_moves_peak_and_keeps_length():
    x = np.zeros(64)
    x[20] = 1.0
    y = fractional_delay(x, 2.5)
    assert y.size == x.size
    assert estimate_delay(y) == pytest.approx(22.5, abs=1e-3)
    # Energy (DC-free band-limited interpolation) is preserved.
    assert np.sum(y) == pytest.approx(np.sum(x), abs=1e-9)


def test_fractional_delay_roundtrip_recovers_signal():
    # A smooth (band-limited) pulse: shift forward then back recovers it. A
    # fractional shift cannot be exactly inverted for a signal with Nyquist
    # energy (a real signal has no sin-at-Nyquist basis), so use a Gaussian bump
    # whose high-frequency content is negligible.
    t = np.arange(128)
    x = np.exp(-((t - 50.0) ** 2) / (2 * 3.0**2))
    y = fractional_delay(fractional_delay(x, 4.25), -4.25)
    assert np.allclose(y, x, atol=1e-6)


# --- neighbour search & weights -------------------------------------------


def test_nearest_indices_sorted_and_wrap_aware():
    dirs = np.array([[0, 0], [90, 0], [180, 0], [270, 0]], dtype=float)
    idx, dist = nearest_indices(dirs, 358, 0, k=2)
    assert idx[0] == 0  # 358° wraps to the front, not to 270°
    assert dist[0] == pytest.approx(2.0, abs=1e-6)
    assert list(dist) == sorted(dist)


def test_nearest_indices_clamps_k():
    dirs = np.array([[0, 0], [90, 0]], dtype=float)
    idx, _ = nearest_indices(dirs, 10, 0, k=5)
    assert idx.size == 2


def test_interpolation_weights_equidistant_is_even():
    w = interpolation_weights(np.array([10.0, 10.0]))
    assert np.allclose(w, [0.5, 0.5])


def test_interpolation_weights_exact_hit_is_one_hot():
    w = interpolation_weights(np.array([5.0, 1e-9, 3.0]))
    assert np.array_equal(w, [0.0, 1.0, 0.0])


def test_interpolation_weights_closer_neighbour_dominates():
    w = interpolation_weights(np.array([1.0, 10.0]), power=2.0)
    # Inverse-distance^power: 1/1^2 and 1/10^2 → [100/101, 1/101]. Pin the exact split so a
    # wrong exponent is caught (power=1 would give [10/11, 1/11]); `w[0] > w[1]` alone is blind.
    assert w == pytest.approx([100 / 101, 1 / 101])
    assert w.sum() == pytest.approx(1.0)


# --- interpolate_hrir: the two properties that matter ---------------------


def _delta_set(azimuths, left_delays, right_delays, *, n=128, sr=48000):
    """Synthetic HRIRSet whose IRs are unit deltas at the given per-ear delays."""
    azimuths = np.asarray(azimuths, dtype=float)
    m = azimuths.size
    left = np.zeros((m, n))
    right = np.zeros((m, n))
    for i in range(m):
        left[i, left_delays[i]] = 1.0
        right[i, right_delays[i]] = 1.0
    dirs = np.column_stack([azimuths, np.zeros(m)])
    return HRIRSet(directions=dirs, left=left, right=right, samplerate=sr)


def test_interpolate_returns_exact_at_measured_direction():
    hs = _delta_set([0, 90], left_delays=[20, 40], right_delays=[25, 30])
    left, right = interpolate_hrir(hs, 90, 0)
    assert np.array_equal(left, hs.left[1])  # untouched, not re-delayed
    assert np.array_equal(right, hs.right[1])


def test_interpolate_itd_is_between_neighbours():
    # Left-ear arrival depends on azimuth; so does the right. Interpolating at the
    # midpoint azimuth must yield per-ear delays at the midpoints — the ITD, which
    # differs between ears, is carried through, not averaged away.
    hs = _delta_set([0.0, 40.0], left_delays=[20, 40], right_delays=[40, 30])
    left, right = interpolate_hrir(hs, 20.0, 0.0, k=2)  # equidistant → 50/50
    assert estimate_delay(left) == pytest.approx(30.0, abs=0.05)  # mean(20, 40)
    assert estimate_delay(right) == pytest.approx(35.0, abs=0.05)  # mean(40, 30)
    # The interaural delay is preserved (left and right are not identical).
    assert abs(estimate_delay(left) - estimate_delay(right)) == pytest.approx(5.0, abs=0.1)


def test_itd_aware_blend_avoids_comb_filtering():
    # Two neighbours are deltas 10 samples apart on the same ear. A naive average
    # combs (periodic magnitude nulls); the ITD-aware blend is a single delayed
    # delta with an essentially flat magnitude spectrum.
    hs = _delta_set([0.0, 40.0], left_delays=[20, 30], right_delays=[20, 30])
    left, _ = interpolate_hrir(hs, 20.0, 0.0, k=2)

    naive = 0.5 * hs.left[0] + 0.5 * hs.left[1]
    mag_naive = np.abs(np.fft.rfft(naive))
    mag_itd = np.abs(np.fft.rfft(left))

    # Naive average has a deep null (comb); ITD-aware stays near flat.
    assert mag_naive.min() < 0.05  # a near-zero null exists
    assert mag_itd.min() > 0.8  # no comb null
    assert mag_itd.max() == pytest.approx(1.0, abs=1e-2)
    # And the single arrival sits at the mean delay.
    assert estimate_delay(left) == pytest.approx(25.0, abs=0.05)


def test_interpolate_k1_reduces_to_nearest():
    hs = _delta_set([0, 90], left_delays=[20, 40], right_delays=[25, 30])
    left, right = interpolate_hrir(hs, 80, 0, k=1)  # nearest is the 90° measurement
    # k=1 realigns and re-delays by the same delay → recovers the nearest IR.
    assert estimate_delay(left) == pytest.approx(40.0, abs=1e-3)
    assert np.allclose(left, hs.left[1], atol=1e-6)
    assert np.allclose(right, hs.right[1], atol=1e-6)


def test_hrirset_interpolated_irs_method_matches_function():
    hs = _delta_set([0, 90], left_delays=[20, 40], right_delays=[25, 30])
    a = hs.interpolated_irs(45, 0)
    b = interpolate_hrir(hs, 45, 0)
    assert np.allclose(a[0], b[0]) and np.allclose(a[1], b[1])


def test_interpolate_rejects_empty_set_via_nearest():
    with pytest.raises(ValueError):
        nearest_indices(np.zeros((0, 2)), 0, 0, k=1)

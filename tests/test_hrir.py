"""Unit tests for the B1.1 HRIR/SOFA loader.

The pure math (great-circle nearest, resampling) is tested directly; ``load_sofa``
is tested against a synthetic SimpleFreeFieldHRIR SOFA generated in a tmp dir, so
no large binary lives in git and the tests stay hermetic.
"""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.hrir import (
    HRIRSet,
    angular_distance,
    load_sofa,
    nearest_index,
    resample_irs,
)


def test_angular_distance_basics():
    assert angular_distance(0, 0, 0, 0) == pytest.approx(0.0, abs=1e-9)
    assert angular_distance(0, 0, 90, 0) == pytest.approx(90.0, abs=1e-6)
    # Wrap: 359° is 2° from 1°, not 358°.
    assert angular_distance(1, 0, 359, 0) == pytest.approx(2.0, abs=1e-6)
    # Elevation only.
    assert angular_distance(0, -30, 0, 30) == pytest.approx(60.0, abs=1e-6)


def test_nearest_index_respects_wrap_and_elevation():
    dirs = np.array([[0, 0], [90, 0], [180, 0], [270, 0], [0, 45]], dtype=float)
    assert nearest_index(dirs, 5, 0) == 0  # near front
    assert nearest_index(dirs, 358, 0) == 0  # wraps to 0, not 270
    assert nearest_index(dirs, 100, 0) == 1  # near 90
    assert nearest_index(dirs, 10, 40) == 4  # elevation dominates


def test_nearest_index_rejects_bad_shape():
    with pytest.raises(ValueError):
        nearest_index(np.zeros((0, 2)), 0, 0)
    with pytest.raises(ValueError):
        nearest_index(np.zeros((3, 3)), 0, 0)


def test_resample_noop_when_rates_equal():
    x = np.random.default_rng(0).normal(size=(4, 128))
    out = resample_irs(x, 48000, 48000)
    assert np.array_equal(out, x)


def test_resample_changes_length_by_ratio():
    x = np.zeros((2, 441))
    out = resample_irs(x, 44100, 48000)
    # 44100 -> 48000 is 160/147; length scales by that ratio.
    assert out.shape[0] == 2
    assert out.shape[1] == round(441 * 160 / 147)


def test_resample_preserves_a_tone_frequency():
    # A 1 kHz tone at 16 kHz, resampled to 48 kHz, should still peak at 1 kHz.
    sr_in, sr_out, f = 16000, 48000, 1000.0
    t = np.arange(sr_in) / sr_in
    x = np.sin(2 * np.pi * f * t)[None, :]
    y = resample_irs(x, sr_in, sr_out)[0]
    spec = np.abs(np.fft.rfft(y * np.hanning(y.size)))
    peak_hz = np.fft.rfftfreq(y.size, 1 / sr_out)[np.argmax(spec)]
    assert peak_hz == pytest.approx(f, abs=5.0)


def test_resample_rejects_nonpositive_rate():
    with pytest.raises(ValueError):
        resample_irs(np.zeros((1, 8)), 0, 48000)


def test_hrirset_shape_validation_and_resampled():
    dirs = np.array([[0, 0], [90, 0]], dtype=float)
    left = np.ones((2, 64))
    right = np.ones((2, 64)) * 2
    hs = HRIRSet(directions=dirs, left=left, right=right, samplerate=44100)
    assert hs.n_measurements == 2 and hs.n_taps == 64
    assert hs.resampled(44100) is hs  # no-op returns self
    up = hs.resampled(48000)
    assert up.samplerate == 48000 and up.n_taps == round(64 * 160 / 147)
    with pytest.raises(ValueError):
        HRIRSet(directions=dirs, left=left, right=np.ones((2, 32)), samplerate=44100)


def test_ipsi_contra_side_mapping():
    dirs = np.array([[90, 0], [270, 0]], dtype=float)  # left, right sources
    left = np.array([[1.0, 0, 0], [1.0, 0, 0]])
    right = np.array([[2.0, 0, 0], [2.0, 0, 0]])
    hs = HRIRSet(directions=dirs, left=left, right=right, samplerate=48000)
    ipsi, contra = hs.ipsi_contra(90, 0)  # source on the left -> left ear ipsi
    assert ipsi[0] == 1.0 and contra[0] == 2.0
    ipsi, contra = hs.ipsi_contra(270, 0)  # source on the right -> right ear ipsi
    assert ipsi[0] == 2.0 and contra[0] == 1.0


def _write_synthetic_sofa(path, *, sr=44100, n_taps=16):
    """Write a minimal SimpleFreeFieldHRIR SOFA: 4 azimuths at elevation 0."""
    import h5py

    az = np.array([0.0, 90.0, 180.0, 270.0])
    m = az.size
    ir = np.zeros((m, 2, n_taps), dtype=np.float64)
    for i in range(m):
        ir[i, 0, i % n_taps] = 1.0  # a unique left-ear delta per measurement
        ir[i, 1, (i + 1) % n_taps] = 0.5  # right ear
    source = np.column_stack([az, np.zeros(m), np.ones(m)])  # (az, el, radius)
    with h5py.File(path, "w") as f:
        f.create_dataset("Data.IR", data=ir)
        f.create_dataset("Data.SamplingRate", data=np.array([sr], dtype=np.float64))
        sp = f.create_dataset("SourcePosition", data=source)
        sp.attrs["Type"] = "spherical"
        sp.attrs["Units"] = "degree, degree, metre"
    return az, ir, sr


def test_load_sofa_roundtrip(tmp_path):
    p = tmp_path / "synthetic.sofa"
    az, ir, sr = _write_synthetic_sofa(p)
    hs = load_sofa(p)
    assert hs.samplerate == sr
    assert hs.n_measurements == 4 and hs.n_taps == 16
    assert np.allclose(hs.directions[:, 0], az)
    assert np.allclose(hs.directions[:, 1], 0.0)
    # Left/right match what we wrote (receiver 0 = left, 1 = right).
    assert np.allclose(hs.left, ir[:, 0, :])
    assert np.allclose(hs.right, ir[:, 1, :])
    # Nearest lookup: 85° -> the 90° measurement (index 1).
    li, ri = hs.nearest_irs(85, 0)
    assert np.argmax(li) == 1 and np.argmax(ri) == 2


def test_load_sofa_resamples_to_target(tmp_path):
    p = tmp_path / "synthetic.sofa"
    _write_synthetic_sofa(p, sr=44100, n_taps=147)
    hs = load_sofa(p, target_rate=48000)
    assert hs.samplerate == 48000
    assert hs.n_taps == round(147 * 160 / 147)  # == 160


def test_load_sofa_rejects_non_hrir(tmp_path):
    import h5py

    p = tmp_path / "bad.sofa"
    with h5py.File(p, "w") as f:
        f.create_dataset("Data.IR", data=np.zeros((3, 4, 8)))  # 4 receivers, not 2
        f.create_dataset("Data.SamplingRate", data=np.array([48000.0]))
        f.create_dataset("SourcePosition", data=np.zeros((3, 3)))
    with pytest.raises(ValueError):
        load_sofa(p)

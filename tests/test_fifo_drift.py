"""Unit tests for the A0 FIFO-drift analyzer (RFC Track A).

Synthetic fill-vs-time series with a *known* skew/jitter composition pin down the classifier:
a clean ramp must read as skew, zero-mean noise as jitter, a ramp+noise mix as mixed, and a
flat line as clean. Headless — no audio, no host, no K11.
"""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.fifo_drift import (
    _MIN_SAMPLES,
    FifoDriftReport,
    _read_csv,
    analyze_fill,
)

SR = 48000
BLOCK = 512


def _times(n: int, dt: float = BLOCK / SR) -> np.ndarray:
    return np.arange(n, dtype=np.float64) * dt


def test_pure_ramp_is_skew() -> None:
    # Fill grows a steady 48 frames/s = +1000 ppm, no noise → unambiguous skew.
    t = _times(600)  # ~6.4 s
    slope = 48.0  # frames/s
    fill = 1024.0 + slope * t
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "skew"
    assert r.skew_ppm == pytest.approx(slope / SR * 1e6, rel=1e-3)  # +1000 ppm
    assert r.skew_ppm > 0  # growing fill = positive skew
    assert r.trend_fraction > 0.99
    assert r.jitter_rms_ms == pytest.approx(0.0, abs=1e-6)


def test_negative_skew_is_signed() -> None:
    # Draining fill → negative skew (heading toward underrun).
    t = _times(600)
    fill = 4096.0 - 48.0 * t
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "skew"
    assert r.skew_ppm < 0
    assert r.skew_ppm == pytest.approx(-1000.0, rel=1e-6)  # exact magnitude, not just sign


def test_pure_jitter_is_jitter() -> None:
    # Zero-trend noise around a constant level → jitter, no real drift. A long window keeps the
    # regression from reading a spurious slope into the noise.
    rng = np.random.default_rng(0)
    t = _times(1200)  # ~12.8 s
    fill = 1536.0 + rng.normal(0.0, 120.0, t.size)  # ~2.5 ms RMS bounce, no drift
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "jitter"
    assert r.trend_fraction < 0.30  # almost no variance explained by a trend
    assert r.skew_drift_ms < r.jitter_rms_ms  # any apparent drift is below the jitter band
    assert r.jitter_rms_ms > 1.0


def test_ramp_plus_noise_is_mixed() -> None:
    # Skew and jitter of comparable weight → the classifier must not force a single label.
    # slope 30 frames/s over ~12.8 s vs 120-frame noise puts trend_fraction in the mixed band.
    rng = np.random.default_rng(1)
    t = _times(1200)
    fill = 1024.0 + 30.0 * t + rng.normal(0.0, 120.0, t.size)
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "mixed"
    assert 0.30 < r.trend_fraction < 0.60
    assert r.skew_ppm > 0
    assert r.jitter_rms_ms > 1.0


def test_flat_is_clean() -> None:
    # A perfectly steady FIFO → neither term is relevant.
    t = _times(600)
    fill = np.full(t.size, 1536.0)
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "clean"
    assert r.skew_drift_ms < 1.0
    assert r.jitter_rms_ms < 1.0


def test_tiny_skew_below_floor_is_clean() -> None:
    # A real but inaudible drift (<1 ms over the window) is reported clean, not skew.
    t = _times(600)  # ~6.4 s
    fill = 1536.0 + 1.0 * t  # 1 frame/s → ~6.4 frames ≈ 0.13 ms over the window
    r = analyze_fill(t, fill, SR)
    assert r.skew_drift_ms < 1.0
    assert r.dominant == "clean"


def test_insufficient_samples() -> None:
    t = _times(_MIN_SAMPLES - 1)
    fill = 1024.0 + 48.0 * t
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "insufficient"
    assert "Insufficient" in r.recommendation


def test_zero_span_is_insufficient() -> None:
    # All timestamps identical → no span, a slope is meaningless.
    t = np.zeros(32)
    fill = np.linspace(1000, 2000, 32)
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "insufficient"


def test_non_finite_rows_dropped() -> None:
    t = _times(600)
    fill = 1024.0 + 48.0 * t
    fill[10] = np.nan
    t[20] = np.inf
    r = analyze_fill(t, fill, SR)
    assert r.n == 598  # two bad rows removed
    assert r.dominant == "skew"


def test_uneven_sampling_ok() -> None:
    # Bursty (uneven) timestamps still yield the right slope via least squares.
    rng = np.random.default_rng(2)
    t = np.cumsum(rng.exponential(BLOCK / SR, 600))
    fill = 1024.0 + 48.0 * t
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "skew"
    # Data is noise-free, so least squares recovers the slope to ~1e-12. Uneven spacing is
    # the only reason this test exists; a tight bound catches a spacing-biased slope estimate
    # that a loose rel=5e-2 (±50 ppm) would wave through.
    assert r.skew_ppm == pytest.approx(1000.0, rel=1e-6)


def test_mismatched_lengths_raise() -> None:
    with pytest.raises(ValueError, match="same length"):
        analyze_fill([0.0, 1.0, 2.0], [1.0, 2.0], SR)


def test_bad_samplerate_raises() -> None:
    with pytest.raises(ValueError, match="samplerate"):
        analyze_fill(_times(32), np.ones(32), 0)


def test_recommendation_mentions_track_b_for_jitter() -> None:
    rng = np.random.default_rng(3)
    t = _times(1200)
    fill = 1536.0 + rng.normal(0.0, 120.0, t.size)
    r = analyze_fill(t, fill, SR)
    assert r.dominant == "jitter"
    assert "Track B" in r.recommendation


def test_report_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    r = analyze_fill(_times(32), np.ones(32) * 1536.0, SR)
    assert isinstance(r, FifoDriftReport)
    with pytest.raises(FrozenInstanceError):
        r.n = 5  # type: ignore[misc]


def test_read_csv_tolerates_header_and_commas(tmp_path) -> None:
    p = tmp_path / "fill.csv"
    p.write_text("time_s,fill_frames\n0.0,1024\n0.01,1072\n# a comment\n0.02,1120\n")
    t, f = _read_csv(str(p))
    assert t.tolist() == [0.0, 0.01, 0.02]
    assert f.tolist() == [1024.0, 1072.0, 1120.0]


def test_read_csv_empty_raises(tmp_path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("time_s,fill_frames\n# nothing numeric\n")
    with pytest.raises(ValueError, match="no numeric"):
        _read_csv(str(p))

"""Tests for the pose-age latency probe (B8.5)."""

from __future__ import annotations

import pytest

from binaural_headtracker.latency import LatencyProbe, LatencyStats


def test_empty_probe_reports_no_data():
    s = LatencyProbe().stats()
    assert s.n == 0 and s.empty
    assert (s.p50, s.p95, s.p99, s.min, s.max, s.mean) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_percentiles_and_extremes():
    p = LatencyProbe()
    for ms in range(1, 101):  # 1..100 ms uniform
        p.add(float(ms))
    s = p.stats()
    assert s.n == 100
    assert s.min == 1.0 and s.max == 100.0
    assert s.p50 == pytest.approx(50.5, abs=0.5)
    assert s.p95 == pytest.approx(95.05, abs=0.5)
    assert s.p99 == pytest.approx(99.01, abs=0.5)
    assert s.mean == pytest.approx(50.5)


def test_window_keeps_only_recent_samples():
    p = LatencyProbe(window=10)
    for ms in range(100):  # last 10 are 90..99
        p.add(float(ms))
    s = p.stats()
    assert s.n == 10 and s.min == 90.0 and s.max == 99.0


def test_rare_spike_shows_in_tail_not_median():
    # 99 good samples + 1 big spike: p50 stays low, the max/p99 expose the spike.
    p = LatencyProbe()
    for _ in range(99):
        p.add(20.0)
    p.add(200.0)
    s = p.stats()
    assert s.p50 == pytest.approx(20.0)
    assert s.max == 200.0
    assert s.p99 >= 20.0 and s.mean > 20.0


def test_invalid_samples_are_ignored():
    p = LatencyProbe()
    p.add(float("nan"))
    p.add(float("inf"))
    p.add(-5.0)
    assert len(p) == 0 and p.stats().n == 0
    p.add(12.0)
    assert len(p) == 1 and p.stats().p50 == pytest.approx(12.0)


def test_reset_clears():
    p = LatencyProbe()
    p.add(5.0)
    p.reset()
    assert len(p) == 0 and p.stats().empty


def test_stats_is_a_frozen_dataclass():
    s = LatencyStats(1, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    with pytest.raises(AttributeError):  # frozen
        s.n = 2  # type: ignore[misc]

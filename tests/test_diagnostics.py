"""Tests for the real-time health + latency diagnosis (B6.3)."""

from __future__ import annotations

import pytest

from binaural_headtracker.diagnostics import Health, classify

# A healthy baseline: block 512 @ 48k = 10.67 ms budget, render well under, buffer at cushion.
BASE = dict(
    block_size=512,
    samplerate=48000,
    render_ms=2.0,
    render_ms_peak=3.0,
    fifo_fill_blocks=5.0,
    cushion_blocks=5,
    output_latency_ms=10.0,
    input_latency_ms=10.0,
    pose_rate_hz=58.0,
    recent_underruns=0,
)


def test_block_budget_and_loads():
    d = classify(**BASE)
    assert d.block_ms == pytest.approx(512 / 48000 * 1000)   # ~10.67 ms
    assert d.render_load == pytest.approx(2.0 / d.block_ms)
    assert d.render_load_peak == pytest.approx(3.0 / d.block_ms)
    assert d.health is Health.OK


def test_end_to_end_is_sum_of_stages():
    d = classify(**BASE)
    pose_interval = 1000.0 / 58.0
    expected_audio_out = 5.0 * d.block_ms + 10.0
    assert d.audio_out_latency_ms == pytest.approx(expected_audio_out)
    assert d.tracking_ms == pytest.approx(1.5 * pose_interval)
    assert d.end_to_end_ms == pytest.approx(
        d.tracking_ms + 10.0 + d.block_ms + expected_audio_out
    )


def test_underruns_force_failing():
    d = classify(**{**BASE, "recent_underruns": 2})
    assert d.health is Health.FAILING
    assert any("dropout" in r for r in d.reasons)


def test_render_over_budget_is_failing():
    d = classify(**{**BASE, "render_ms": 8.0, "render_ms_peak": 12.0})  # peak > 10.67 ms budget
    assert d.health is Health.FAILING
    assert any("over 100%" in r for r in d.reasons)


def test_render_spike_near_budget_is_at_risk():
    d = classify(**{**BASE, "render_ms": 5.0, "render_ms_peak": 9.8})  # ~92% peak, mean ~47%
    assert d.health is Health.RISK


def test_high_mean_load_is_tight():
    d = classify(**{**BASE, "render_ms": 7.0, "render_ms_peak": 8.0})  # ~66% mean, ~75% peak
    assert d.health is Health.TIGHT
    assert any("averages" in r for r in d.reasons)


def test_nearly_empty_buffer_is_tight():
    d = classify(**{**BASE, "fifo_fill_blocks": 0.0})
    assert d.health is Health.TIGHT
    assert any("buffer" in r for r in d.reasons)


def test_cold_start_zeros_are_safe():
    d = classify(
        block_size=512, samplerate=48000, render_ms=0.0, render_ms_peak=0.0,
        fifo_fill_blocks=0.0, cushion_blocks=0, pose_rate_hz=0.0, recent_underruns=0,
    )
    assert d.end_to_end_ms >= 0.0 and d.render_load == 0.0
    assert isinstance(d.health, Health)


def test_health_rank_orders_worst_last():
    assert Health.OK.rank < Health.TIGHT.rank < Health.RISK.rank < Health.FAILING.rank

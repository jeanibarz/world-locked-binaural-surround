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


def test_overflow_drops_are_counted_and_failing():
    # Clock-drift overflow drops are audible too — they must fail health and be reported, even
    # when the output-underrun counter is 0 (the "0 dropouts but I hear glitches" bug).
    d = classify(**{**BASE, "recent_underruns": 0, "recent_overflows": 5})
    assert d.recent_overflows == 5
    assert d.health is Health.FAILING
    assert any("overflow" in r for r in d.reasons)


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


def test_measured_pose_age_split_is_carried():
    d = classify(**{**BASE, "measured_pose_age_ms": 70.0, "measured_pose_age_n": 300,
                    "measured_stale_ms": 40.0, "measured_queue_ms": 8.0,
                    "measured_device_ms": 22.0})
    assert d.pose_age_stale_ms == 40.0
    assert d.pose_age_queue_ms == 8.0
    assert d.pose_age_device_ms == 22.0


def test_measured_pose_age_overrides_the_estimate():
    # With a live measurement, end_to_end = camera-front (one frame) + measured p50, and it is
    # flagged measured; the distribution tail is carried through.
    d = classify(**{**BASE, "measured_pose_age_ms": 70.0, "measured_pose_age_p95_ms": 95.0,
                    "measured_pose_age_p99_ms": 110.0, "measured_pose_age_n": 300})
    assert d.end_to_end_measured
    assert d.pose_age_p50_ms == 70.0 and d.pose_age_p95_ms == 95.0 and d.pose_age_p99_ms == 110.0
    assert d.pose_age_n == 300
    frame_ms = 1000.0 / BASE["pose_rate_hz"]
    assert d.end_to_end_ms == pytest.approx(frame_ms + 70.0)


def test_falls_back_to_estimate_without_samples():
    d = classify(**{**BASE, "measured_pose_age_ms": None, "measured_pose_age_n": 0})
    assert not d.end_to_end_measured and d.pose_age_n == 0
    # the all-estimate stage sum (as before the probe existed)
    assert d.end_to_end_ms == pytest.approx(
        d.tracking_ms + BASE["input_latency_ms"] + d.block_ms + d.audio_out_latency_ms)


def test_cold_start_zeros_are_safe():
    d = classify(
        block_size=512, samplerate=48000, render_ms=0.0, render_ms_peak=0.0,
        fifo_fill_blocks=0.0, cushion_blocks=0, pose_rate_hz=0.0, recent_underruns=0,
    )
    assert d.end_to_end_ms >= 0.0 and d.render_load == 0.0
    assert isinstance(d.health, Health)


def test_health_rank_orders_worst_last():
    assert Health.OK.rank < Health.TIGHT.rank < Health.RISK.rank < Health.FAILING.rank


def test_tracking_note_camera_limited():
    # 30 Hz loop, 3 ms detect, camera says 30 fps → at the camera's hardware rate.
    d = classify(**{**BASE, "pose_rate_hz": 30.0, "detect_ms": 3.0, "camera_fps": 30.0})
    assert d.loop_ms == pytest.approx(1000.0 / 30.0)
    assert "camera" in d.tracking_note and "hardware rate" in d.tracking_note
    assert d.detect_ms == 3.0 and d.camera_fps == 30.0


def test_tracking_note_low_light_drop():
    # Camera reports 30 fps but only 14 Hz delivered with fast detect → low-light auto-exposure.
    d = classify(**{**BASE, "pose_rate_hz": 14.0, "detect_ms": 3.0, "camera_fps": 30.0})
    assert "low light" in d.tracking_note


def test_tracking_note_detection_limited():
    # Slow detect dominating the loop → flagged as detection-bound (resolution won't help).
    d = classify(**{**BASE, "pose_rate_hz": 12.0, "detect_ms": 70.0, "camera_fps": 30.0})
    assert "detection is the limit" in d.tracking_note


def test_tracking_note_empty_without_rate():
    d = classify(**{**BASE, "pose_rate_hz": 0.0})
    assert d.tracking_note == "" and d.loop_ms == 0.0


def test_config_fields_are_surfaced():
    d = classify(**{**BASE, "render_label": "rust · timealign · 15° grid"})
    assert d.block_size == 512 and d.samplerate == 48000
    assert d.render_label == "rust · timealign · 15° grid"


def test_test_source_load_is_split_out_and_clamped():
    # test source takes 3 ms of a 5 ms total; its share of the ~10.67 ms budget is reported,
    # and render_ms_test can never exceed the whole render_ms.
    d = classify(**{**BASE, "render_ms": 5.0, "render_ms_peak": 6.0, "render_ms_test": 3.0})
    assert d.render_ms_test == 3.0
    assert d.render_load_test == pytest.approx(3.0 / d.block_ms)
    over = classify(**{**BASE, "render_ms": 4.0, "render_ms_test": 99.0})
    assert over.render_ms_test == 4.0  # clamped to the total


def test_test_source_is_blamed_when_budget_is_tight():
    # 7.1 fine alone, but the test source pushes a peak near/over budget → advise T / block / rust.
    d = classify(**{**BASE, "render_ms": 7.0, "render_ms_peak": 10.4, "render_ms_test": 3.0})
    assert any("test source adds" in r for r in d.reasons)


def test_no_test_blame_when_test_share_is_zero():
    d = classify(**{**BASE, "render_ms": 7.0, "render_ms_peak": 10.4, "render_ms_test": 0.0})
    assert not any("test source adds" in r for r in d.reasons)

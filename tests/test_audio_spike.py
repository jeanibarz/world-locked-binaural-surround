"""Unit tests for the portable B0.2 audio-spike helpers.

These run anywhere (no audio device, no ``sounddevice``) — they exercise the
probe/cross-correlation delay recovery and the device-table formatting that the
Windows WASAPI spike relies on.
"""

from __future__ import annotations

import numpy as np

from binaural_headtracker.audio_spike import (
    estimate_lag_samples,
    format_device_table,
    log_sweep,
    samples_to_ms,
)

SR = 48_000.0


def test_log_sweep_shape_and_level():
    probe = log_sweep(SR, duration_s=0.05)
    assert probe.dtype == np.float32
    assert probe.size == int(round(SR * 0.05))
    assert np.max(np.abs(probe)) <= 1.0


def test_estimate_lag_recovers_known_delay_under_noise():
    probe = log_sweep(SR, duration_s=0.05)
    rng = np.random.default_rng(0)
    for delay in (0, 137, 2048, 9001):
        captured = np.zeros(delay + probe.size + 5000, dtype=np.float64)
        captured[delay : delay + probe.size] += 0.4 * probe  # attenuated copy
        captured += rng.normal(0.0, 0.01, captured.size)  # measurement noise
        assert abs(estimate_lag_samples(probe, captured) - delay) <= 1


def test_estimate_lag_rejects_empty():
    import pytest

    with pytest.raises(ValueError):
        estimate_lag_samples([], [1.0, 2.0])


def test_samples_to_ms():
    assert samples_to_ms(480, SR) == 10.0
    assert abs(samples_to_ms(240, SR) - 5.0) < 1e-9


def test_format_device_table_marks_host_and_channels():
    hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    devices = [
        {
            "name": "Speakers",
            "hostapi": 1,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": "Microphone",
            "hostapi": 0,
            "max_input_channels": 1,
            "max_output_channels": 0,
            "default_samplerate": 44100.0,
        },
    ]
    table = format_device_table(devices, hostapis)
    assert "Windows WASAPI" in table
    assert "Speakers" in table and "Microphone" in table
    # Two data rows + header + rule.
    assert len(table.splitlines()) == 4

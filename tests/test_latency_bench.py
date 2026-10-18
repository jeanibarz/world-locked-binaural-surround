"""Tests for the latency benchmark harness (B8.5b) — headless sim mode, synthetic BRIR."""

from __future__ import annotations

import argparse

import numpy as np
import pytest

from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet

latency_bench = pytest.importorskip("latency_bench")  # app module (apps/ on pythonpath)


def _brir(n: int = 256) -> BakedBRIRSet:
    rng = np.random.default_rng(0)
    c = len(CHANNELS_7P1)
    left = np.zeros((c, n))
    right = np.zeros((c, n))
    for i in range(c):
        left[i, 3 + i] = 1.0           # a clean direct so convolution passes the tone
        right[i, 5 + i] = 0.9
        left[i, 30:60] = 0.02 * rng.standard_normal(30)
        right[i, 30:60] = 0.02 * rng.standard_normal(30)
    return BakedBRIRSet(channels=CHANNELS_7P1, left=left, right=right, samplerate=48000)


def _args(**over):
    base = dict(method="nearest", block=256, latency_ms=40.0, grid_step=10.0, fade_ms=20.0,
                lfe_mode="brir", output_eq=None, rust=False, output="sim", audio_device=None,
                exclusive=False, device_ms=25.0, fps=60.0, sweep_deg=20.0, sweep_hz=0.25,
                tone_hz=220.0, tone_level=0.2, warmup=0.3, seconds=0.7, json=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_sim_run_produces_a_clean_result():
    r = latency_bench.run_bench(_brir(), _args())
    # distributions populated, no dropouts for the cheap nearest path, audio intact
    assert r["total_ms"]["n"] > 0 and r["stale_ms"]["n"] > 0
    assert r["dropouts"] == 0
    assert r["render_load"] < 1.0
    assert r["integrity"]["finite"] and r["integrity"]["ok"]
    assert r["integrity"]["tone_snr_db"] > 15.0   # the pipeline passed the sine


def test_device_buffer_term_is_added_to_total():
    r = latency_bench.run_bench(_brir(), _args(device_ms=50.0))
    assert r["device_ms"] == 50.0
    # total = stale + queue + device, so every sample is at least the device buffer
    assert r["total_ms"]["min"] >= 49.0
    # and the components are in a sane order (stale is the pose age, > 0)
    assert r["stale_ms"]["p50"] > 0.0


def test_overload_is_reported_as_dropouts_not_hidden():
    # A tiny block with a heavy per-block cost (long BRIR) can't keep up in pure Python → the
    # harness must *report* dropouts and flag integrity bad, not silently pass.
    r = latency_bench.run_bench(_brir(n=20000), _args(method="timealign", block=64, seconds=0.6))
    assert r["dropouts"] > 0
    assert not r["integrity"]["ok"]

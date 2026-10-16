"""Unit tests for the B2.1 head tracker — all headless (no camera/MediaPipe, no
real sleeps; timing is driven by each sample's timestamp)."""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.tracking import HeadPose, HeadTracker, OneEuroFilter, PoseFilter

FS = 60.0  # sample rate of the synthetic clips (Hz)


def _clip(values, fs=FS, jitter=0.0, seed=0):
    """A recorded clip: HeadPose stream of `values` (yaw) at `fs`, pitch/roll=0."""
    rng = np.random.default_rng(seed)
    out = []
    for i, v in enumerate(values):
        noise = rng.normal(0, jitter) if jitter else 0.0
        out.append(HeadPose(yaw=float(v) + noise, pitch=0.0, roll=0.0, t=i / fs))
    return out


# --- OneEuroFilter ----------------------------------------------------------


def test_first_sample_passes_through():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.1)
    assert f.filter(5.0, 0.0) == 5.0


def test_constant_input_converges_to_constant():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.0)
    y = [f.filter(10.0, i / FS) for i in range(120)]
    assert y[-1] == pytest.approx(10.0, abs=1e-6)


def test_step_response_lags_then_settles():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.0)
    f.filter(0.0, 0.0)
    y1 = f.filter(1.0, 1 / FS)  # first step sample
    assert 0.0 < y1 < 1.0  # lags (does not jump straight to 1)
    for i in range(2, 200):
        y = f.filter(1.0, i / FS)
    assert y == pytest.approx(1.0, abs=1e-3)  # eventually settles


def test_lower_min_cutoff_means_more_lag():
    steps = [0.0] + [1.0] * 10
    def settle(mc):
        f = OneEuroFilter(min_cutoff=mc, beta=0.0)
        return [f.filter(v, i / FS) for i, v in enumerate(steps)][-1]
    laggy = settle(0.3)   # heavy smoothing
    snappy = settle(5.0)  # light smoothing
    assert laggy < snappy  # lower cutoff ⇒ further behind the step after N samples


def test_reduces_jitter_on_a_noisy_constant():
    rng = np.random.default_rng(1)
    noise = 10.0 + rng.normal(0, 1.0, 400)
    f = OneEuroFilter(min_cutoff=0.5, beta=0.0)
    y = np.array([f.filter(v, i / FS) for i, v in enumerate(noise)])
    assert np.std(y[50:]) < 0.5 * np.std(noise)  # output much steadier than input


def test_reset_clears_state():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.2)
    for i in range(10):
        f.filter(i, i / FS)
    f.reset()
    assert f.filter(42.0, 0.0) == 42.0  # behaves like a first sample again


# --- PoseFilter -------------------------------------------------------------


def test_pose_filter_smooths_each_axis_independently():
    pf = PoseFilter(min_cutoff=1.0, beta=0.0)
    out = [pf.update(HeadPose(yaw=30.0, pitch=-10.0, roll=5.0, t=i / FS)) for i in range(100)]
    last = out[-1]
    assert last.yaw == pytest.approx(30.0, abs=1e-3)
    assert last.pitch == pytest.approx(-10.0, abs=1e-3)
    assert last.roll == pytest.approx(5.0, abs=1e-3)
    assert out[0].yaw == 30.0  # first sample passes through per axis


# --- HeadTracker (threaded, recorded clip) ----------------------------------


def test_tracker_processes_a_recorded_clip_and_publishes_latest():
    clip = _clip([0, 10, 20, 30, 30, 30, 30], fs=FS)
    tr = HeadTracker(clip, min_cutoff=2.0, beta=0.1)
    tr.start()
    assert tr.wait(2.0), "worker should finish a finite clip"
    latest = tr.latest()
    assert latest is not None
    assert latest.yaw == pytest.approx(30.0, abs=1.0)  # tracked to the held value
    tr.stop()


def test_tracker_latest_is_none_before_first_sample():
    tr = HeadTracker([])
    assert tr.latest() is None
    tr.start()
    assert tr.wait(2.0)
    assert tr.latest() is None  # empty source → never published


def test_tracker_stats_from_sample_timestamps():
    clip = _clip(range(60), fs=FS)  # 60 samples at 60 Hz = 1 s
    tr = HeadTracker(clip)
    tr.start()
    assert tr.wait(2.0)
    s = tr.stats()
    assert s["n"] == 60
    assert s["rate_hz"] == pytest.approx(FS, rel=1e-6)
    assert s["jitter_ms"] == pytest.approx(0.0, abs=1e-6)  # perfectly regular clip


def test_tracker_output_is_steadier_than_a_noisy_source():
    raw = _clip([20.0] * 300, fs=FS, jitter=1.5, seed=3)
    tr = HeadTracker(raw, min_cutoff=0.5, beta=0.0)
    tr.start()
    assert tr.wait(2.0)
    # Re-filter offline to compare variance (the tracker keeps only the latest);
    # assert the filter the tracker uses would reduce the jitter.
    pf = PoseFilter(min_cutoff=0.5, beta=0.0)
    out = np.array([pf.update(p).yaw for p in raw])
    raw_yaw = np.array([p.yaw for p in raw])
    assert np.std(out[50:]) < 0.5 * np.std(raw_yaw)
    assert tr.latest().yaw == pytest.approx(20.0, abs=1.0)


def test_tracker_stop_is_clean_on_an_endless_source():
    def endless():
        i = 0
        while True:
            yield HeadPose(yaw=0.0, pitch=0.0, roll=0.0, t=i / FS)
            i += 1
    tr = HeadTracker(endless())
    tr.start()
    tr.stop(timeout=2.0)  # must not hang
    assert tr.wait(0.1)  # thread has ended


def test_double_start_raises():
    tr = HeadTracker(_clip([0, 1, 2]))
    tr.start()
    tr.wait(2.0)
    with pytest.raises(RuntimeError):
        tr.start()
    tr.stop()


def test_context_manager_starts_and_stops():
    with HeadTracker(_clip(range(30))) as tr:
        tr.wait(2.0)
        assert tr.latest() is not None


def test_negative_beta_rejected():
    with pytest.raises(ValueError):
        OneEuroFilter(min_cutoff=1.0, beta=-0.1)


def test_stats_window_is_bounded():
    from binaural_headtracker.tracking import STATS_WINDOW
    clip = _clip(range(STATS_WINDOW + 200), fs=FS)  # more samples than the window
    tr = HeadTracker(clip)
    tr.start()
    assert tr.wait(5.0)
    assert tr.stats()["n"] == STATS_WINDOW  # only the recent window is retained

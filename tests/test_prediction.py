"""Tests for orientation prediction (B8.6) — headless, deterministic."""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.prediction import AlphaBetaPredictor, PosePredictor, PredictorConfig
from binaural_headtracker.tracking import HeadPose

FS = 60.0  # pose rate for the synthetic streams
DT = 1.0 / FS


def _feed_constant_velocity(pred: AlphaBetaPredictor, rate_dps: float, n: int = 120):
    """Feed n samples of a pure constant-velocity ramp; return the last timestamp + true value."""
    for i in range(n):
        t = i * DT
        pred.update(rate_dps * t, t)
    return (n - 1) * DT, rate_dps * ((n - 1) * DT)


def test_predicts_a_constant_velocity_ramp_ahead():
    # Moving at 90 deg/s, a 100 ms look-ahead should land ~9 deg beyond the current true angle.
    pred = AlphaBetaPredictor()
    t_last, true_now = _feed_constant_velocity(pred, 90.0)
    horizon = 0.1
    predicted = pred.predict(horizon)
    # On a perfect ramp the alpha-beta filter converges to the exact velocity/position, so the
    # tolerance can be tight. The old abs=5.0/3.0 accepted a ±5 deg/s velocity error and a ±3 deg
    # (33%) landing error — nearly useless on a 90 deg/s, 9 deg-lead case.
    assert pred.velocity == pytest.approx(90.0, abs=1e-2)       # velocity learned
    assert predicted == pytest.approx(true_now + 90.0 * horizon, abs=1e-2)  # lands ahead


def test_still_head_predicts_no_motion():
    pred = AlphaBetaPredictor()
    for i in range(60):
        pred.update(12.0, i * DT)  # perfectly still at 12 deg
    assert pred.velocity == pytest.approx(0.0, abs=1e-6)
    assert pred.predict(0.1) == pytest.approx(12.0, abs=1e-6)


def test_stop_does_not_overshoot_persistently():
    # Ramp up, then hold still. A frame or two after the stop the estimate may lead slightly, but it
    # must settle to the held value — not keep flinging ahead at the old velocity.
    pred = AlphaBetaPredictor()
    t = 0.0
    for i in range(80):          # ramp at 120 deg/s
        pred.update(120.0 * (i * DT), t)
        t += DT
    hold = pred.predict(0.0)     # current smoothed angle at the stop
    for _ in range(40):          # now hold still at that angle
        pred.update(hold, t)
        t += DT
    # Settled: velocity has decayed to ~0 and the 100 ms prediction sits on the held angle.
    # A regression that kept extrapolating at the old 120 deg/s would show velocity ~120 and a
    # ~12 deg lead, so a tight bound (vs the old abs=5.0/2.0) is what makes this discriminating.
    assert pred.velocity == pytest.approx(0.0, abs=0.1)
    assert pred.predict(0.1) == pytest.approx(hold, abs=0.1)   # settled, not overshooting


def test_max_delta_bounds_the_step():
    pred = AlphaBetaPredictor()
    _feed_constant_velocity(pred, 300.0)      # fast
    far = pred.predict(0.2)                    # 300*0.2 = 60 deg step, unclamped
    near = pred.predict(0.2, max_delta=5.0)    # clamp the step to 5 deg
    assert abs(far - near) > 10.0
    assert near == pytest.approx(pred.predict(0.0) + 5.0, abs=1e-6)


def test_pose_predictor_extrapolates_yaw_and_keeps_timestamp():
    pp = PosePredictor(PredictorConfig(max_rate_dps=1000.0))  # don't clamp this moderate motion
    last = None
    for i in range(90):
        last = HeadPose(yaw=60.0 * (i * DT), pitch=0.0, roll=0.0, t=i * DT)
        pp.update(last)
    out = pp.predict(last, horizon_ms=100.0)
    assert out.t == last.t                                  # timestamp preserved
    assert out.yaw > last.yaw                               # yaw looks ahead
    assert out.yaw == pytest.approx(last.yaw + 60.0 * 0.1, abs=3.0)
    assert out.pitch == pytest.approx(0.0, abs=1e-6)        # still axes untouched


def test_horizon_clamped_and_zero_disables():
    pp = PosePredictor(PredictorConfig(max_horizon_ms=120.0))
    last = None
    for i in range(90):
        last = HeadPose(yaw=90.0 * (i * DT), pitch=0.0, roll=0.0, t=i * DT)
        pp.update(last)
    at_zero = pp.predict(last, horizon_ms=0.0)
    # a 0 ms horizon is just the smoothed current angle (prediction disabled path)
    assert at_zero.yaw < pp.predict(last, horizon_ms=100.0).yaw
    # An absurd horizon is clamped to max_horizon_ms (120 ms), not taken literally. Comparing
    # against the 120 ms prediction (exactly at the cap, so not itself clamped) both confirms
    # the clamp fires AND pins the cap value: if the clamp used a different ceiling (e.g. a
    # hardcoded 80 ms) the two would diverge. (The old `at_zero == predict(0)` self-compare,
    # which could never fail, has been removed.)
    huge = pp.predict(last, horizon_ms=10000.0).yaw
    clamped = pp.predict(last, horizon_ms=120.0).yaw
    assert huge == pytest.approx(clamped, abs=1e-6)
    assert clamped > at_zero.yaw  # the 120 ms cap still extrapolates ahead (not collapsed to 0)


def test_reset_clears_state():
    pp = PosePredictor()
    pp.update(HeadPose(10.0, 0.0, 0.0, 0.0))
    pp.update(HeadPose(20.0, 0.0, 0.0, DT))
    pp.reset()
    out = pp.predict(HeadPose(0.0, 0.0, 0.0, 0.0), horizon_ms=100.0)
    assert out.yaw == 0.0 and np.isfinite(out.yaw)

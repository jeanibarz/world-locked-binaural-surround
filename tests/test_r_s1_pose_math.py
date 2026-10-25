"""Unit tests for the R-S1 spike's pure pose-parity math (host-free, runs on CI/WSL).

The spike itself (``spikes/r_s1_pose_parity.py``) needs mediapipe/sixdrepnet and runs on the host;
its alignment/RMS math is pure numpy, so it is tested here without those deps. Loaded via importlib
because ``spikes/`` is not an importable package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

_SPIKE = Path(__file__).resolve().parent.parent / "spikes" / "r_s1_pose_parity.py"
_spec = importlib.util.spec_from_file_location("r_s1_pose_parity", _SPIKE)
rs1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs1)


def test_align_axis_recovers_sign_and_offset():
    rng = np.random.default_rng(0)
    ref = rng.uniform(-40, 40, 200)
    model = -ref + 5.0 + rng.normal(0, 2.0, 200)  # sign -1, offset such that ref = -model + 5
    sign, offset, aligned = rs1.align_axis(ref, model)
    assert sign == -1.0
    assert abs(offset - 5.0) < 0.6
    assert np.sqrt(np.mean((ref - aligned) ** 2)) < 2.6  # ~ injected noise


def test_parity_rms_per_axis():
    rng = np.random.default_rng(1)
    ref = {"yaw": rng.uniform(-40, 40, 150), "pitch": rng.uniform(-30, 30, 150),
           "roll": rng.uniform(-20, 20, 150)}
    model = {"yaw": -ref["yaw"] + 5 + rng.normal(0, 2.0, 150),
             "pitch": ref["pitch"] - 3 + rng.normal(0, 1.0, 150),
             "roll": -ref["roll"] + rng.normal(0, 0.5, 150)}
    r = rs1.parity_rms(ref, model)
    assert r["yaw"]["sign"] == -1 and r["pitch"]["sign"] == 1 and r["roll"]["sign"] == -1
    assert r["yaw"]["rms_deg"] < 2.6 and r["roll"]["rms_deg"] < 1.0


def test_parity_rms_rejects_shape_mismatch():
    import pytest
    with pytest.raises(ValueError, match="shape mismatch or empty"):
        rs1.parity_rms({"yaw": np.zeros(3), "pitch": np.zeros(3), "roll": np.zeros(3)},
                       {"yaw": np.zeros(2), "pitch": np.zeros(2), "roll": np.zeros(2)})


def test_latency_stats():
    s = rs1.latency_stats([5.0, 6.0, 7.0, 8.0, 20.0])
    assert s["n"] == 5 and s["p50_ms"] == 7.0 and s["max_ms"] == 20.0

"""Tests for the portable cockpit view-model (B6.12) — no Dear PyGui, no display."""

from __future__ import annotations

import math

import pytest

from binaural_headtracker import cockpit as C
from binaural_headtracker.diagnostics import Health, classify
from binaural_headtracker.latency import LatencyProbe

# ------------------------------------------------------------------ histogram

def test_histogram_empty_is_zeros():
    h = C.latency_histogram([], bins=10)
    assert h == [0] * 10


def test_histogram_counts_sum_to_n_in_range():
    s = [5, 15, 25, 35, 45]
    h = C.latency_histogram(s, lo_ms=0.0, hi_ms=100.0, bins=10)
    assert sum(h) == len(s)
    # 0–10, 10–20, 20–30, 30–40, 40–50 → one each in bins 0..4
    assert h[:5] == [1, 1, 1, 1, 1]
    assert h[5:] == [0] * 5


def test_histogram_clamps_out_of_range_into_end_bins():
    h = C.latency_histogram([-10, 0, 999], lo_ms=0.0, hi_ms=100.0, bins=4)
    assert h[0] == 2          # -10 and 0 both land in the first bin
    assert h[-1] == 1         # 999 clamps into the last bin
    assert sum(h) == 3


def test_histogram_drops_non_finite():
    h = C.latency_histogram([float("nan"), float("inf"), 20.0], bins=8, hi_ms=80.0)
    assert sum(h) == 1


def test_histogram_upper_edge_goes_in_last_bin():
    # a value exactly at hi_ms must not overflow the list
    h = C.latency_histogram([100.0], lo_ms=0.0, hi_ms=100.0, bins=5)
    assert h[-1] == 1
    assert sum(h) == 1


@pytest.mark.parametrize("bins,hi", [(0, 100.0), (-3, 100.0)])
def test_histogram_rejects_bad_bins(bins, hi):
    with pytest.raises(ValueError):
        C.latency_histogram([1, 2], bins=bins, hi_ms=hi)


def test_histogram_rejects_bad_range():
    with pytest.raises(ValueError):
        C.latency_histogram([1], lo_ms=50.0, hi_ms=50.0, bins=4)


def test_bin_edges_length_and_monotonic():
    edges = C.histogram_bin_edges(0.0, 100.0, 10)
    assert len(edges) == 11
    assert edges[0] == 0.0 and edges[-1] == 100.0
    assert all(b > a for a, b in zip(edges, edges[1:], strict=False))


# ------------------------------------------------------------------ palette

def test_health_color_covers_every_verdict():
    for h in Health:
        c = C.health_color(h)
        assert len(c) == 4 and all(0 <= v <= 255 for v in c)
    assert C.health_color(Health.OK) == C.GOOD
    assert C.health_color(Health.FAILING) == C.BAD


# ------------------------------------------------------------------ build_view

def _diag(**over):
    base = dict(
        block_size=512, samplerate=48000, render_ms=4.0, render_ms_peak=7.0,
        fifo_fill_blocks=6.0, cushion_blocks=6, output_latency_ms=13.0,
        input_latency_ms=10.7, pose_rate_hz=58.0, detect_ms=11.0,
        camera_fps=30.0, cam_w=640, cam_h=480, render_label="rust · nearest (8-bus)",
    )
    base.update(over)
    return classify(**base)


def test_build_view_measured_has_three_components():
    diag = _diag(measured_pose_age_ms=35.0, measured_pose_age_p95_ms=50.0,
                 measured_pose_age_p99_ms=62.0, measured_pose_age_n=200,
                 measured_stale_ms=22.0, measured_queue_ms=13.0, measured_device_ms=13.0)
    v = C.build_view(diag, latency_samples=[30, 35, 40, 48, 60], rate_hz=58.0,
                     method="nearest", backend="rust", facing_label="FL")
    assert v.measured is True
    assert len(v.components) == 3
    assert [c.label for c in v.components] == ["stale", "queue", "device"]
    assert v.components[0].ms == pytest.approx(22.0)
    assert "n=200" in v.detail


def test_build_view_estimate_has_no_components():
    v = C.build_view(_diag(), rate_hz=58.0)
    assert v.measured is False
    assert v.components == ()
    assert "estimate" in v.detail


def test_build_view_histogram_length_matches_bins():
    v = C.build_view(_diag(), latency_samples=[20, 40, 60], hist_bins=24)
    assert len(v.histogram) == 24
    assert sum(v.histogram) == 3


def test_build_view_axis_has_80ms_floor_and_rounds_up():
    # worst value small → floor at 80; a big p99 rounds up to the next 20 ms
    small = C.build_view(
        _diag(measured_pose_age_ms=35.0, measured_pose_age_p95_ms=50.0,
              measured_pose_age_p99_ms=62.0, measured_pose_age_n=100),
        latency_samples=[30, 40])
    assert small.hist_hi_ms == 80.0
    big = C.build_view(
        _diag(measured_pose_age_ms=70.0, measured_pose_age_p95_ms=95.0,
              measured_pose_age_p99_ms=111.0, measured_pose_age_n=50),
    )
    assert big.hist_hi_ms == 120.0  # ceil(111/20)*20


def test_build_view_readouts_and_verdict():
    diag = _diag()
    v = C.build_view(diag, rate_hz=58.0, method="timealign", backend="gpu",
                     facing_label="FL", predict_ms=48.0, flipped=True)
    labels = {r.label: r.value for r in v.readouts}
    assert labels["rate"] == "58 Hz"
    assert "FL" in labels["facing"] and "[flip]" in labels["facing"]
    assert labels["predict"] == "48 ms"
    assert v.method == "timealign" and v.backend == "gpu"
    assert v.verdict == C._VERDICT_LABEL[diag.health]
    assert v.verdict_color == C.health_color(diag.health)


def test_verdict_labels_are_the_expected_literals():
    # pin the literal verdict text so a renamed label is caught (not re-derived via the map)
    assert C._VERDICT_LABEL[Health.OK] == "GOOD"
    assert C._VERDICT_LABEL[Health.TIGHT] == "TIGHT"
    assert C._VERDICT_LABEL[Health.RISK] == "AT RISK"
    assert C._VERDICT_LABEL[Health.FAILING] == "FAILING"
    # and that a known-healthy diagnosis surfaces the "GOOD" pill end to end
    assert C.build_view(_diag(), rate_hz=58.0).verdict == "GOOD"


def test_build_view_bars_and_dropouts():
    healthy = C.build_view(_diag(), rate_hz=58.0)
    assert [b.label for b in healthy.bars] == ["render load", "out buffer"]
    assert healthy.dropouts_text == "none" and healthy.dropouts_color == C.GOOD
    bad = C.build_view(_diag(recent_underruns=2, recent_overflows=1), rate_hz=58.0)
    assert "underrun" in bad.dropouts_text and "overflow" in bad.dropouts_text
    assert bad.dropouts_color == C.BAD


def test_build_view_consumes_probe_samples():
    probe = LatencyProbe(window=100)
    for ms in (30.0, 35.0, 40.0, 45.0):
        probe.add(ms)
    v = C.build_view(_diag(), latency_samples=probe.samples(), hist_bins=16)
    assert sum(v.histogram) == 4


def test_probe_samples_snapshot():
    probe = LatencyProbe(window=3)
    for ms in (1.0, 2.0, 3.0, 4.0):   # window rolls → keeps last 3
        probe.add(ms)
    assert probe.samples() == (2.0, 3.0, 4.0)
    assert all(math.isfinite(x) for x in probe.samples())

"""B3.1 — world-locked single-source streaming renderer: selection, parity, click-free."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import fftconvolve

from binaural_headtracker.calibrate import HeadCalibration, directions_for_pose
from binaural_headtracker.live_render import (
    RING_CHANNELS,
    WorldLockedSource,
    nearest_brir_index,
    render_world_locked,
    ring_azimuths,
    source_layout,
)
from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet
from binaural_headtracker.tracking import HeadPose

SR = 48_000


def _synthetic_brir(n: int = 192) -> BakedBRIRSet:
    """A BRIR set with a distinct, well-separated IR per channel (delta + short tail).

    Each channel gets its delta at a different tap and a tiny decaying tail, so a
    convolution through one channel is unmistakably different from another and the
    per-channel parity checks are meaningful. LFE is present but non-directional.
    """
    rng = np.random.default_rng(0)
    c = len(CHANNELS_7P1)
    left = np.zeros((c, n))
    right = np.zeros((c, n))
    for i in range(c):
        left[i, 3 + i] = 1.0
        right[i, 5 + i] = 0.8
        left[i, 20:40] = rng.normal(0, 0.02, 20)
        right[i, 20:40] = rng.normal(0, 0.02, 20)
    return BakedBRIRSet(channels=CHANNELS_7P1, left=left, right=right, samplerate=SR)


def test_ring_excludes_lfe_and_maps_azimuths() -> None:
    brir = _synthetic_brir()
    az = ring_azimuths(brir)
    chans = {brir.channels[i] for i in az}
    assert chans == set(RING_CHANNELS)
    assert "LFE" not in chans
    assert az[brir.index("C")] == 0.0
    assert az[brir.index("L")] == 30.0
    assert az[brir.index("SR")] == -90.0


@pytest.mark.parametrize(
    "src_az, expect",
    [
        (0.0, "C"),
        (5.0, "C"),
        (30.0, "L"),
        (-30.0, "R"),
        (90.0, "SL"),
        (-90.0, "SR"),
        (100.0, "SL"),   # 90 is 10 away, 135 is 35 away
        (170.0, "RL"),   # 135 is 35 away, -135 is 55 away (wrapped)
        (-170.0, "RR"),  # symmetric
        (179.0, "RL"),   # 135 (44) vs -135 (46, wrapped) -> RL
    ],
)
def test_nearest_brir_selection(src_az: float, expect: str) -> None:
    brir = _synthetic_brir()
    assert nearest_brir_index(ring_azimuths(brir), src_az) == brir.index(expect)


def test_static_source_equals_plain_convolution() -> None:
    """A source held at exactly a ring azimuth == convolving the dry signal with that BRIR."""
    brir = _synthetic_brir()
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.3, 4000)
    block = 256
    n_blocks = int(np.ceil(x.size / block))
    track = np.full(n_blocks, 30.0)  # straight at L for the whole signal

    out = render_world_locked(x, track, brir, block_size=block, fade_ms=20.0)

    li = brir.index("L")
    ref_l = fftconvolve(x, brir.left[li])
    ref_r = fftconvolve(x, brir.right[li])
    m = min(out.shape[0], ref_l.size)
    assert np.allclose(out[:m, 0], ref_l[:m], atol=1e-9)
    assert np.allclose(out[:m, 1], ref_r[:m], atol=1e-9)
    # Nothing beyond the reference length (padding tail is silent).
    assert np.max(np.abs(out[m:])) < 1e-9


def test_transition_reaches_the_new_brir_steady_state() -> None:
    """After sweeping the azimuth to a new ring point, the steady output is that BRIR."""
    brir = _synthetic_brir()
    rng = np.random.default_rng(2)
    x = rng.normal(0, 0.3, 8000)
    block = 256
    n_blocks = int(np.ceil(x.size / block))
    # First half pointed at L (30), second half swung to SL (90).
    track = np.where(np.arange(n_blocks) < n_blocks // 2, 30.0, 90.0)

    out = render_world_locked(x, track, brir, block_size=block, fade_ms=20.0)

    # In true steady state — past the fade *and* one IR length, so the retired L voice
    # has drained and SL's whole window is full-gain x — the output is exactly a plain
    # SL convolution of the same input, at the same absolute samples.
    si = brir.index("SL")
    ref_l = fftconvolve(x, brir.left[si])
    swap = (n_blocks // 2) * block
    fade_samples = int(round(0.020 * SR))
    start = swap + fade_samples + brir.n_taps + 8
    end = x.size
    assert start < end
    assert np.allclose(out[start:end, 0], ref_l[start:end], atol=1e-9)


def test_crossfade_is_click_free_vs_hard_switch() -> None:
    """A steady tone swung across a BRIR boundary: the fade removes the switching click."""
    brir = _synthetic_brir()
    block = 256
    t = np.arange(6000) / SR
    x = 0.5 * np.sin(2 * np.pi * 440 * t)  # steady tone, so any jump is the switch
    n_blocks = int(np.ceil(x.size / block))
    # Hold at L, then at SL — the boundary flip happens at one block.
    track = np.where(np.arange(n_blocks) < n_blocks // 2, 30.0, 90.0)

    faded = render_world_locked(x, track, brir, block_size=block, fade_ms=20.0)
    hard = render_world_locked(x, track, brir, block_size=block, fade_ms=0.0)  # ~1 sample

    region = slice((n_blocks // 2) * block - block, (n_blocks // 2) * block + block)
    jump_faded = np.max(np.abs(np.diff(faded[region, 0])))
    jump_hard = np.max(np.abs(np.diff(hard[region, 0])))
    # The crossfade must make the worst sample-to-sample step materially smaller.
    assert jump_faded < 0.5 * jump_hard


def test_block_api_shapes_and_flush() -> None:
    brir = _synthetic_brir()
    src = WorldLockedSource(brir, block_size=128, fade_ms=10.0)
    out = src.process_block(np.zeros(128), 0.0)
    assert out.shape == (128, 2)
    with pytest.raises(ValueError):
        src.process_block(np.zeros(100), 0.0)
    tail = src.flush()
    assert tail.shape == (src.tail_blocks * 128, 2)
    assert np.all(np.isfinite(out)) and np.all(np.isfinite(tail))


def test_world_lock_chain_from_pose_selects_opposite_brir() -> None:
    """The pose→azimuth wiring the host app uses: +yaw ⇒ source swings the other way.

    Neutral pose with the source at world-front selects C; a +30° yaw (turning left)
    world-locks the source to −30° head-relative, selecting R — the world-lock sign.
    """
    brir = _synthetic_brir()
    az = ring_azimuths(brir)
    layout = source_layout(world_az_deg=0.0)
    cal = HeadCalibration(0.0, 0.0, 0.0, 1.0, -1.0, 1.0)

    neutral = HeadPose(0.0, 0.0, 0.0, 0.0)
    az_neutral = float(directions_for_pose(cal, neutral, layout)[0][0])
    assert nearest_brir_index(az, az_neutral) == brir.index("C")

    turned_left = HeadPose(30.0, 0.0, 0.0, 1.0)
    az_turned = float(directions_for_pose(cal, turned_left, layout)[0][0])
    assert az_turned == pytest.approx(-30.0, abs=1e-6)
    assert nearest_brir_index(az, az_turned) == brir.index("R")

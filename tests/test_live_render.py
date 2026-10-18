"""B3.1 — world-locked single-source streaming renderer: selection, parity, click-free."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import fftconvolve

from binaural_headtracker.calibrate import HeadCalibration, directions_for_pose
from binaural_headtracker.live_render import (
    RING_CHANNELS,
    WorldLocked7p1,
    WorldLockedSource,
    nearest_brir_index,
    render_world_locked,
    ring_azimuths,
    source_layout,
)
from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet, render_7p1_to_stereo
from binaural_headtracker.tracking import HeadPose
from binaural_headtracker.world import layout_7p1

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
    assert az[brir.index("SR")] == -110.0


@pytest.mark.parametrize(
    "src_az, expect",
    [
        (0.0, "C"),
        (5.0, "C"),
        (30.0, "L"),
        (-30.0, "R"),
        (90.0, "SL"),
        (-90.0, "SR"),
        (100.0, "SL"),   # SL 110 is 10 away, L 30 is 70 away
        (170.0, "RL"),   # RL 150 is 20 away, -150 is 40 away (wrapped)
        (-170.0, "RR"),  # symmetric
        (179.0, "RL"),   # 150 (29) vs -150 (31, wrapped) -> RL
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
    # First half pointed at L (30), second half swung to SL (110).
    track = np.where(np.arange(n_blocks) < n_blocks // 2, 30.0, 110.0)

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
    track = np.where(np.arange(n_blocks) < n_blocks // 2, 30.0, 110.0)

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


# --- B4.1: full 7.1 world-locked passthrough ---------------------------------------

def _run_7p1(w, pcm, az, block):
    n_blocks = int(np.ceil(pcm.shape[0] / block))
    chunks = []
    for b in range(n_blocks):
        blk = pcm[b * block : (b + 1) * block]
        if blk.shape[0] < block:
            blk = np.vstack([blk, np.zeros((block - blk.shape[0], pcm.shape[1]))])
        chunks.append(w.process_block(blk, az))
    chunks.append(w.flush())
    return np.concatenate(chunks, axis=0)


def _neutral_azimuths(brir):
    cal = HeadCalibration(0.0, 0.0, 0.0, 1.0, -1.0, 1.0)
    pose = HeadPose(0.0, 0.0, 0.0, 0.0)
    return directions_for_pose(cal, pose, layout_7p1(channels=brir.channels))[:, 0]


def test_7p1_neutral_equals_static_render() -> None:
    """At the neutral pose every channel steers to its own BRIR, so the sum == the static
    fixed-head 7.1 render (each channel through its own BRIR)."""
    brir = _synthetic_brir()
    rng = np.random.default_rng(3)
    pcm = rng.normal(0, 0.2, (4000, brir.n_channels))
    block = 256
    az = _neutral_azimuths(brir)

    out = _run_7p1(WorldLocked7p1(brir, block_size=block, fade_ms=20.0), pcm, az, block)
    ref = render_7p1_to_stereo(pcm, brir)
    m = min(out.shape[0], ref.shape[0])
    assert np.allclose(out[:m], ref[:m], atol=1e-9)
    assert np.max(np.abs(out[m:])) < 1e-9


def test_7p1_process_block_validation() -> None:
    brir = _synthetic_brir()
    w = WorldLocked7p1(brir, block_size=128)
    good_az = np.zeros(brir.n_channels)
    assert w.process_block(np.zeros((128, brir.n_channels)), good_az).shape == (128, 2)
    with pytest.raises(ValueError):
        w.process_block(np.zeros((100, brir.n_channels)), good_az)  # wrong block
    with pytest.raises(ValueError):
        w.process_block(np.zeros((128, brir.n_channels)), np.zeros(3))  # wrong az count


def test_7p1_yaw_steers_a_channel_to_the_rotated_brir() -> None:
    """A +90° yaw world-locks the front-centre channel to −90° head-relative, so a signal
    in C alone comes out through the SR BRIR (steady state)."""
    brir = _synthetic_brir()
    rng = np.random.default_rng(4)
    pcm = np.zeros((6000, brir.n_channels))
    pcm[:, brir.index("C")] = rng.normal(0, 0.3, 6000)

    cal = HeadCalibration(0.0, 0.0, 0.0, 1.0, -1.0, 1.0)
    az = directions_for_pose(cal, HeadPose(90.0, 0.0, 0.0, 0.0),
                             layout_7p1(channels=brir.channels))[:, 0]
    assert az[brir.index("C")] == pytest.approx(-90.0, abs=1e-6)  # world-lock

    block = 256
    out = _run_7p1(WorldLocked7p1(brir, block_size=block, fade_ms=20.0), pcm, az, block)
    si = brir.index("SR")
    ref_l = fftconvolve(pcm[:, brir.index("C")], brir.left[si])
    m = min(out.shape[0], ref_l.size)
    assert np.allclose(out[:m, 0], ref_l[:m], atol=1e-9)


def test_7p1_lfe_is_not_steered() -> None:
    """LFE goes through its own fixed BRIR regardless of head azimuths (non-directional)."""
    brir = _synthetic_brir()
    rng = np.random.default_rng(5)
    pcm = np.zeros((4000, brir.n_channels))
    pcm[:, brir.index("LFE")] = rng.normal(0, 0.3, 4000)
    block = 256
    wild_az = np.full(brir.n_channels, 123.0)  # any head azimuths

    out = _run_7p1(WorldLocked7p1(brir, block_size=block, fade_ms=20.0), pcm, wild_az, block)
    li = brir.index("LFE")
    ref_l = fftconvolve(pcm[:, li], brir.left[li])
    ref_r = fftconvolve(pcm[:, li], brir.right[li])
    m = min(out.shape[0], ref_l.size)
    assert np.allclose(out[:m, 0], ref_l[:m], atol=1e-9)
    assert np.allclose(out[:m, 1], ref_r[:m], atol=1e-9)


def test_7p1_lfe_flat_mode_sums_non_directional() -> None:
    """lfe_mode='flat' (B4.3) adds LFE straight to both ears at the per-ear IR L2 norm — no
    convolution — so it keeps the sub-bass the bass-light BRIR would strip, with no tail."""
    brir = _synthetic_brir()
    rng = np.random.default_rng(9)
    pcm = np.zeros((2000, brir.n_channels))
    sig = rng.normal(0, 0.3, 2000)
    pcm[:, brir.index("LFE")] = sig  # LFE-only; directional channels silent
    block = 256
    w = WorldLocked7p1(brir, block_size=block, fade_ms=20.0, lfe_mode="flat")
    out = _run_7p1(w, pcm, _neutral_azimuths(brir), block)

    li = brir.index("LFE")
    g_l, g_r = np.linalg.norm(brir.left[li]), np.linalg.norm(brir.right[li])
    n = pcm.shape[0]
    assert np.allclose(out[:n, 0], sig * g_l, atol=1e-9)   # flat, block-aligned, no filtering
    assert np.allclose(out[:n, 1], sig * g_r, atol=1e-9)
    assert np.max(np.abs(out[n:])) < 1e-9                  # flat LFE adds no convolution tail
    with pytest.raises(ValueError):
        WorldLocked7p1(brir, block_size=block, lfe_mode="bogus")


# --- B4.2: bus-mix reformulation (what the GPU batches) == the voice pool --------------

def test_bus_mixer_steady_state_routes_full_gain() -> None:
    from binaural_headtracker.live_render import BusMixer
    mx = BusMixer(4, fade_samples=100)
    pcm = np.ones((64, 4))
    targets = np.array([2, 2, 0, 3])  # ch0,1 -> bus2; ch2 -> bus0; ch3 -> bus3
    bus = mx.mix(pcm, targets)  # (4, 64); first block starts at full gain
    assert bus.shape == (4, 64)
    assert np.allclose(bus[2], 2.0)   # two channels of ones summed into bus 2
    assert np.allclose(bus[0], 1.0)
    assert np.allclose(bus[3], 1.0)
    assert np.allclose(bus[1], 0.0)   # bus 1 unused


def test_bus_mix_matches_voice_pool_7p1() -> None:
    """Convolving the crossfaded buses with fixed BRIRs == the per-channel voice pool."""
    from binaural_headtracker.live_render import BusMixer, bus_targets
    brir = _synthetic_brir()
    c = brir.n_channels
    block = 256
    rng = np.random.default_rng(7)
    pcm = rng.normal(0, 0.2, (5000, c))
    n_blocks = int(np.ceil(pcm.shape[0] / block))
    cal = HeadCalibration(0.0, 0.0, 0.0, 1.0, -1.0, 1.0)
    lay = layout_7p1(channels=brir.channels)
    yaws = np.linspace(0.0, 60.0, n_blocks)  # a head sweep so channels change buses
    azs = [directions_for_pose(cal, HeadPose(float(y), 0.0, 0.0, 0.0), lay)[:, 0] for y in yaws]

    def padded(b):
        blk = pcm[b * block : (b + 1) * block]
        if blk.shape[0] < block:
            blk = np.vstack([blk, np.zeros((block - blk.shape[0], c))])
        return blk

    # voice pool, per-block azimuths
    w = WorldLocked7p1(brir, block_size=block, fade_ms=20.0)
    vp_chunks = [w.process_block(padded(b), azs[b]) for b in range(n_blocks)]
    vp_chunks.append(w.flush())
    vp = np.concatenate(vp_chunks, axis=0)

    # bus mix, same per-block azimuths, then convolve each bus with its fixed BRIR
    mx = BusMixer(c, fade_samples=int(round(0.020 * SR)))
    buses = [mx.mix(padded(b), bus_targets(brir, azs[b])) for b in range(n_blocks)]
    bus_sig = np.concatenate(buses, axis=1)  # (C, total)
    total = bus_sig.shape[1]
    stereo = np.zeros((total + brir.n_taps - 1, 2))
    for bch in range(c):
        stereo[:, 0] += fftconvolve(bus_sig[bch], brir.left[bch])
        stereo[:, 1] += fftconvolve(bus_sig[bch], brir.right[bch])
    m = min(vp.shape[0], stereo.shape[0])
    assert np.allclose(vp[:m], stereo[:m], atol=1e-9)

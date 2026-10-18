"""Tests for the direct + shared-reverb prototype (B1.14 / issue #53).

All headless on synthetic BRIR sets (no device, no real BRIR). The real-BRIR
parity check is opt-in and skipped when the owner's files are absent.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import fftconvolve

from binaural_headtracker.shared_reverb import (
    DEFAULT_DIRECT_MS,
    DIRECTIONAL_7P1,
    SharedReverbRenderer,
    energy_onset,
    render_shared_reverb,
    split_direct_shared,
    uniform_lead_delay,
)
from binaural_headtracker.surround import BakedBRIRSet

SR = 48000
RNG = np.random.default_rng(7)


def _synth_brir(
    channels=DIRECTIONAL_7P1,
    *,
    n=4000,
    lead=480,
    direct_len=960,
    tail_scale=0.1,
    zero_tail=False,
) -> BakedBRIRSet:
    """A synthetic BakedBRIRSet: uniform `lead` delay, a distinct direct per
    channel/ear inside [lead, lead+direct_len), then a decorrelated random tail."""
    c = len(channels)
    left = np.zeros((c, n))
    right = np.zeros((c, n))
    for k in range(c):
        # distinct direct: a couple of spikes at channel/ear-dependent offsets
        left[k, lead + 5 + k] = 1.0 - 0.03 * k
        left[k, lead + 40 + 2 * k] = 0.4
        right[k, lead + 7 + k] = 0.9 - 0.03 * k
        right[k, lead + 44 + 2 * k] = 0.35
        if not zero_tail:
            left[k, lead + direct_len :] = tail_scale * RNG.standard_normal(n - lead - direct_len)
            right[k, lead + direct_len :] = tail_scale * RNG.standard_normal(n - lead - direct_len)
    return BakedBRIRSet(channels=tuple(channels), left=left, right=right, samplerate=SR)


def test_energy_onset_and_uniform_lead_delay():
    ir = np.zeros(2000)
    ir[500] = 1.0
    assert energy_onset(ir) == 500
    assert energy_onset(np.zeros(100)) == 0
    brir = _synth_brir(lead=480)
    d = uniform_lead_delay(brir)
    # onset lands at the first direct spike (lead+5..), never before `lead`
    assert 480 <= d <= 480 + 10


def test_split_shapes_and_lead_delay():
    brir = _synth_brir(lead=480, direct_len=960, n=5000)
    s = split_direct_shared(brir, direct_ms=20.0)
    assert s.channels == DIRECTIONAL_7P1
    assert s.direct_len == int(round(20.0 * SR / 1000))  # 960
    assert s.direct_left.shape == (len(DIRECTIONAL_7P1), 960)
    assert s.shared_left.shape == (5000 - s.lead_delay,)
    # shared tail's leading direct region is zeroed (no double count)
    assert np.allclose(s.shared_left[: s.direct_len], 0.0)
    assert np.allclose(s.shared_right[: s.direct_len], 0.0)


def test_shared_tail_is_mean_of_channel_tails():
    brir = _synth_brir()
    s = split_direct_shared(brir)
    d = s.lead_delay
    idx = [brir.index(c) for c in s.channels]
    expected_l = np.mean(
        [np.where(np.arange(brir.n_taps - d) < s.direct_len, 0.0, brir.left[i][d:])
         for i in idx], axis=0)
    assert np.allclose(s.shared_left, expected_l)


def _full_trimmed_ref(brir, pcm, channels, d, length):
    """Exact full render over `channels` with the lead delay `d` trimmed off."""
    out = np.zeros((length, 2))
    for c in channels:
        i = brir.index(c)
        x = pcm[:, brir.channels.index(c)]
        yl = fftconvolve(x, brir.left[i][d:])
        yr = fftconvolve(x, brir.right[i][d:])
        out[: min(length, yl.size), 0] += yl[:length]
        out[: min(length, yr.size), 1] += yr[:length]
    return out


def test_correlated_matches_full_trimmed_exactly():
    """Decomposition identity: correlated input → directs+shared == full (trimmed)."""
    brir = _synth_brir(n=4000)
    m = 1500
    sig = RNG.standard_normal(m)
    pcm = np.zeros((m, brir.n_channels))
    for c in DIRECTIONAL_7P1:
        pcm[:, brir.channels.index(c)] = sig  # same signal to all directional channels
    got = render_shared_reverb(pcm, brir, block_size=256, direct_ms=20.0)
    d = uniform_lead_delay(brir)
    ref = _full_trimmed_ref(brir, pcm, DIRECTIONAL_7P1, d, got.shape[0])
    assert got.shape == ref.shape
    assert np.max(np.abs(got - ref)) < 1e-8


def test_direct_path_exact_per_direction_zero_tail():
    """With zero tails, the shared-reverb render is bit-exact for ANY (decorrelated)
    input — proves the per-direction direct (ITD/ILD/early timbre) is exact."""
    brir = _synth_brir(n=3000, zero_tail=True)
    m = 1200
    pcm = np.zeros((m, brir.n_channels))
    for c in DIRECTIONAL_7P1:  # independent signal per channel (decorrelated)
        pcm[:, brir.channels.index(c)] = RNG.standard_normal(m)
    got = render_shared_reverb(pcm, brir, block_size=256, direct_ms=20.0)
    d = uniform_lead_delay(brir)
    ref = _full_trimmed_ref(brir, pcm, DIRECTIONAL_7P1, d, got.shape[0])
    assert np.max(np.abs(got - ref)) < 1e-8


def test_decorrelated_level_within_budget():
    """Decorrelated scene: broadband error stays small on a DIRECT-dominated BRIR.

    The real BRIR carries ~50-75 % of its energy in the 20 ms direct window
    (journal 49), so the worst-case decorrelated tail penalty (~-8.5 dB on 7
    independent tails) is diluted to ~1 dB broadband. ``tail_scale=0.01`` puts
    this synthetic set in that same direct-dominated regime."""
    brir = _synth_brir(n=4000, tail_scale=0.01)
    m = SR  # 1 s
    pcm = np.zeros((m, brir.n_channels))
    for c in DIRECTIONAL_7P1:
        pcm[:, brir.channels.index(c)] = RNG.standard_normal(m)
    got = render_shared_reverb(pcm, brir, block_size=512, direct_ms=20.0)
    d = uniform_lead_delay(brir)
    ref = _full_trimmed_ref(brir, pcm, DIRECTIONAL_7P1, d, got.shape[0])
    for ear in (0, 1):
        lg = 10 * np.log10(np.mean(got[:, ear] ** 2) + 1e-20)
        lr = 10 * np.log10(np.mean(ref[:, ear] ** 2) + 1e-20)
        assert abs(lg - lr) < 2.0  # within 2 dB broadband


def test_streaming_equals_offline():
    brir = _synth_brir(n=3000)
    m = 1000
    pcm = RNG.standard_normal((m, brir.n_channels))
    offline = render_shared_reverb(pcm, brir, block_size=256, flush=True)
    r = SharedReverbRenderer(brir, block_size=256)
    chunks = []
    for b in range(int(np.ceil(m / 256))):
        blk = pcm[b * 256 : (b + 1) * 256]
        if blk.shape[0] < 256:
            blk = np.vstack([blk, np.zeros((256 - blk.shape[0], brir.n_channels))])
        chunks.append(r.process_block(blk))
    chunks.append(r.flush())
    streamed = np.concatenate(chunks, axis=0)
    assert np.allclose(streamed, offline)


def test_ignores_nondirectional_columns():
    """An LFE column present in the pcm but not in the directional set is ignored."""
    brir = _synth_brir(channels=("L", "R", "C", "LFE", "SL", "SR", "RL", "RR"), n=3000)
    s = split_direct_shared(brir, channels=DIRECTIONAL_7P1)
    assert "LFE" not in s.channels
    m = 600
    pcm = RNG.standard_normal((m, brir.n_channels))
    pcm2 = pcm.copy()
    pcm2[:, brir.channels.index("LFE")] += 5.0 * RNG.standard_normal(m)  # perturb LFE only
    got1 = render_shared_reverb(pcm, brir, block_size=256, channels=DIRECTIONAL_7P1)
    got2 = render_shared_reverb(pcm2, brir, block_size=256, channels=DIRECTIONAL_7P1)
    assert np.allclose(got1, got2)


def test_tail_blocks_and_flush_length():
    brir = _synth_brir(n=4000)
    r = SharedReverbRenderer(brir, block_size=256)
    assert r.tail_blocks > 0
    flushed = r.flush()
    assert flushed.shape == (r.tail_blocks * 256, 2)


def test_errors():
    brir = _synth_brir(n=2000)
    with pytest.raises(ValueError):
        split_direct_shared(brir, channels=("ZZZ",))
    with pytest.raises(ValueError):
        split_direct_shared(brir, direct_ms=0.0)
    with pytest.raises(ValueError):
        SharedReverbRenderer(brir, block_size=0)
    r = SharedReverbRenderer(brir, block_size=256)
    with pytest.raises(ValueError):
        r.process_block(np.zeros((128, brir.n_channels)))


_REAL_BRIR = (
    "/mnt/c/Program Files/EqualizerAPO/config/"
    "audeze-lcd-gx-binaural-surround/eqapo_config/baked_7p1_test"
)


@pytest.mark.skipif(not Path(_REAL_BRIR).is_dir(), reason="owner's real BRIR not present")
def test_real_brir_correlated_parity_and_lead_delay():
    from binaural_headtracker.surround import load_baked_brir

    brir = load_baked_brir(_REAL_BRIR)
    d = uniform_lead_delay(brir)
    assert 400 <= d <= 600  # ~482 samples (~10 ms) leading delay measured in journal 49
    m = 4000
    sig = RNG.standard_normal(m)
    pcm = np.zeros((m, brir.n_channels))
    for c in DIRECTIONAL_7P1:
        pcm[:, brir.channels.index(c)] = sig
    got = render_shared_reverb(pcm, brir, block_size=512, direct_ms=DEFAULT_DIRECT_MS)
    ref = _full_trimmed_ref(brir, pcm, DIRECTIONAL_7P1, d, got.shape[0])
    assert np.max(np.abs(got - ref)) < 1e-7  # correlated scene reconstructs exactly


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))

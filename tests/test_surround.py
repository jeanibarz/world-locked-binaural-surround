"""Unit tests for the static 7.1 → binaural renderer (B1.6).

The renderer math is tested with tiny known BRIRs (impulse responses that make
the convolution output verifiable by hand); `load_baked_brir` is tested against
synthetic WAVs written to a tmp dir, so no personal `.wav` data lives in git.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.io import wavfile

from binaural_headtracker.surround import (
    CHANNELS_7P1,
    SPEAKER_LAYOUT_7P1,
    BakedBRIRSet,
    load_baked_brir,
    render_7p1_to_stereo,
)


def _toy_set(channels=("L", "R"), n=4):
    # Each channel gets a distinct, easily-recognised L/R impulse response.
    c = len(channels)
    left = np.zeros((c, n))
    right = np.zeros((c, n))
    for i in range(c):
        left[i, i % n] = 1.0  # delta at a channel-specific tap
        right[i, (i + 1) % n] = 0.5
    return BakedBRIRSet(channels=tuple(channels), left=left, right=right, samplerate=48000)


def test_layout_covers_all_channels():
    assert set(SPEAKER_LAYOUT_7P1) == set(CHANNELS_7P1)
    assert len(CHANNELS_7P1) == 8


def test_impulse_on_one_channel_reproduces_its_brir():
    brir = _toy_set()
    pcm = np.zeros((1, 2))
    pcm[0, 0] = 1.0  # unit impulse on channel 0 only
    out = render_7p1_to_stereo(pcm, brir)
    assert out.shape == (brir.n_taps, 2)  # M=1 -> out_len = N
    assert np.allclose(out[:, 0], brir.left[0])
    assert np.allclose(out[:, 1], brir.right[0])


def test_second_channel_routes_to_its_own_brir():
    brir = _toy_set()
    pcm = np.zeros((1, 2))
    pcm[0, 1] = 1.0
    out = render_7p1_to_stereo(pcm, brir)
    assert np.allclose(out[:, 0], brir.left[1])
    assert np.allclose(out[:, 1], brir.right[1])


def test_linearity_sum_of_channels():
    brir = _toy_set()
    a = np.zeros((1, 2))
    a[0, 0] = 1.0
    b = np.zeros((1, 2))
    b[0, 1] = 1.0
    out_a = render_7p1_to_stereo(a, brir)
    out_b = render_7p1_to_stereo(b, brir)
    out_ab = render_7p1_to_stereo(a + b, brir)
    assert np.allclose(out_ab, out_a + out_b)


def test_scaling_and_convolution_length():
    brir = _toy_set(n=8)
    m = 16
    rng = np.random.default_rng(0)
    pcm = rng.normal(size=(m, 2))
    out = render_7p1_to_stereo(pcm, brir)
    assert out.shape == (m + brir.n_taps - 1, 2)
    # Doubling the input doubles the output (linear system).
    assert np.allclose(render_7p1_to_stereo(2 * pcm, brir), 2 * out)


def test_render_rejects_wrong_channel_count():
    brir = _toy_set(channels=CHANNELS_7P1)
    with pytest.raises(ValueError):
        render_7p1_to_stereo(np.zeros((10, 2)), brir)  # 2 cols, expects 8


def test_brirset_shape_validation():
    with pytest.raises(ValueError):  # 2 channels but 1 row
        BakedBRIRSet(
            channels=("L", "R"), left=np.zeros((1, 4)), right=np.zeros((1, 4)), samplerate=48000
        )
    with pytest.raises(ValueError):  # left/right lengths differ
        BakedBRIRSet(
            channels=("L",), left=np.zeros((1, 4)), right=np.zeros((1, 8)), samplerate=48000
        )


def _write_wav(path, data, sr=48000):
    wavfile.write(path, sr, np.asarray(data, dtype=np.float32))


def test_load_baked_brir_roundtrip_and_padding(tmp_path):
    channels = ("L", "R", "C")
    expected_left = {}
    expected_right = {}
    for i, ch in enumerate(channels):
        # Varying lengths on purpose -> loader must zero-pad to a common N.
        left = np.zeros(4 + i)
        left[i] = 1.0
        right = np.zeros(4 + i)
        right[0] = 0.25 * (i + 1)
        _write_wav(tmp_path / f"{ch}_L.wav", left)
        _write_wav(tmp_path / f"{ch}_R.wav", right)
        expected_left[ch] = left
        expected_right[ch] = right

    brir = load_baked_brir(tmp_path, channels=channels)
    assert brir.channels == channels
    assert brir.samplerate == 48000
    assert brir.n_taps == 6  # max length (4 + 2)
    for ch in channels:
        i = brir.index(ch)
        assert np.allclose(brir.left[i, : expected_left[ch].size], expected_left[ch], atol=1e-6)
        assert np.allclose(brir.right[i, : expected_right[ch].size], expected_right[ch], atol=1e-6)
        # zero-padded region
        assert np.allclose(brir.left[i, expected_left[ch].size :], 0.0)


def test_load_baked_brir_detects_rate_mismatch(tmp_path):
    _write_wav(tmp_path / "L_L.wav", np.zeros(4), sr=48000)
    _write_wav(tmp_path / "L_R.wav", np.zeros(4), sr=44100)
    with pytest.raises(ValueError):
        load_baked_brir(tmp_path, channels=("L",))

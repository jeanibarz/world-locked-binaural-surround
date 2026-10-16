"""Unit tests for the partitioned-FFT streaming convolution engine (B1.3).

The engine's job is to reproduce, block by block in real time, what
``scipy.signal.fftconvolve`` computes offline. So the central tests feed random
signals and random impulse responses through the streaming convolver and assert
the output matches ``fftconvolve`` to floating error — across block sizes that do
and don't divide the filter length (P=1 and P>1 partitions), and against the
real :func:`~binaural_headtracker.surround.render_7p1_to_stereo` for the stereo
path.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import fftconvolve

from binaural_headtracker.convolve import (
    PartitionedConvolver,
    StereoConvolver,
    convolve_stream,
)
from binaural_headtracker.surround import BakedBRIRSet, render_7p1_to_stereo


# --- single-channel correctness vs scipy ------------------------------------
@pytest.mark.parametrize("block", [1, 4, 16, 64, 100])
@pytest.mark.parametrize("ir_len", [1, 5, 16, 33, 200])
def test_stream_matches_fftconvolve(block, ir_len):
    rng = np.random.default_rng(block * 1000 + ir_len)
    x = rng.standard_normal(257)  # deliberately not a multiple of any block
    ir = rng.standard_normal(ir_len)
    got = convolve_stream(x, ir, block)
    ref = fftconvolve(x, ir)
    assert got.shape == ref.shape
    assert np.allclose(got, ref, atol=1e-9)


def test_block_by_block_region_has_no_extra_delay():
    # The m-th output block must carry the convolution of the m-th input block
    # (plus history) — i.e. no algorithmic delay beyond the one-block buffering.
    rng = np.random.default_rng(7)
    block = 32
    x = rng.standard_normal(block * 5)
    ir = rng.standard_normal(20)
    conv = PartitionedConvolver(ir, block)
    streamed = np.concatenate(
        [conv.process_block(x[i * block : (i + 1) * block]) for i in range(5)]
    )
    ref = fftconvolve(x, ir)[: x.size]  # real-time region, tail dropped
    assert np.allclose(streamed, ref, atol=1e-9)


def test_impulse_response_is_recovered():
    # Feeding a unit impulse must emit the IR itself (classic sanity check).
    block = 8
    ir = np.array([0.0, 1.0, -0.5, 0.25, 0.1, 0.0, 0.0, 0.3, -0.2])
    x = np.zeros(1)
    x[0] = 1.0
    got = convolve_stream(x, ir, block)
    assert np.allclose(got, ir, atol=1e-12)


def test_streaming_is_linear():
    rng = np.random.default_rng(3)
    block = 16
    ir = rng.standard_normal(40)
    a = rng.standard_normal(80)
    b = rng.standard_normal(80)
    ca = convolve_stream(a, ir, block, flush=False)
    cb = convolve_stream(b, ir, block, flush=False)
    cab = convolve_stream(a + b, ir, block, flush=False)
    assert np.allclose(cab, ca + cb, atol=1e-9)
    # Scaling the input scales the output.
    assert np.allclose(convolve_stream(2 * a, ir, block, flush=False), 2 * ca, atol=1e-9)


def test_reset_restores_initial_state():
    rng = np.random.default_rng(11)
    block = 16
    ir = rng.standard_normal(50)
    conv = PartitionedConvolver(ir, block)
    x0 = rng.standard_normal(block)
    first = conv.process_block(x0).copy()
    # Run some more blocks to dirty the delay line, then reset and repeat x0.
    for _ in range(3):
        conv.process_block(rng.standard_normal(block))
    conv.reset()
    assert np.allclose(conv.process_block(x0), first, atol=1e-12)


def test_no_flush_returns_only_realtime_region():
    rng = np.random.default_rng(5)
    block = 16
    x = rng.standard_normal(40)
    ir = rng.standard_normal(30)
    body = convolve_stream(x, ir, block, flush=False)
    # Padded up to a block multiple (40 -> 48), tail NOT appended.
    assert body.size == 48
    assert np.allclose(body[: x.size], fftconvolve(x, ir)[: x.size], atol=1e-9)


# --- introspection / framing ------------------------------------------------
def test_partition_count_and_latency():
    conv = PartitionedConvolver(np.ones(100), block_size=32)
    assert conv.block_size == 32
    assert conv.latency == 32  # one block of buffering, no extra algorithmic delay
    assert conv.n_parts == 4  # ceil(100 / 32)
    assert conv.tail_blocks == 4  # ceil((100 - 1) / 32)
    # A filter shorter than a block is a single partition with no tail overflow.
    short = PartitionedConvolver(np.ones(10), block_size=32)
    assert short.n_parts == 1
    assert short.tail_blocks == 1


def test_single_sample_filter_has_no_tail():
    conv = PartitionedConvolver(np.array([2.0]), block_size=8)
    assert conv.tail_blocks == 0
    assert conv.flush().size == 0
    assert np.allclose(conv.process_block(np.arange(8.0)), 2.0 * np.arange(8.0))


# --- error handling ---------------------------------------------------------
def test_rejects_bad_block_size():
    with pytest.raises(ValueError):
        PartitionedConvolver(np.ones(4), block_size=0)


def test_rejects_empty_ir():
    with pytest.raises(ValueError):
        PartitionedConvolver(np.zeros(0), block_size=4)


def test_rejects_wrong_block_length():
    conv = PartitionedConvolver(np.ones(4), block_size=8)
    with pytest.raises(ValueError):
        conv.process_block(np.zeros(7))


# --- stereo / multichannel path ---------------------------------------------
def _toy_brir(channels=("L", "R", "C"), n=37, seed=0):
    rng = np.random.default_rng(seed)
    c = len(channels)
    left = rng.standard_normal((c, n))
    right = rng.standard_normal((c, n))
    return BakedBRIRSet(channels=tuple(channels), left=left, right=right, samplerate=48000)


def test_stereo_stream_matches_offline_renderer():
    brir = _toy_brir(seed=1)
    block = 16
    rng = np.random.default_rng(2)
    total = block * 6
    pcm = rng.standard_normal((total, brir.n_channels))

    conv = StereoConvolver(brir.left, brir.right, block)
    blocks = [conv.process_block(pcm[i * block : (i + 1) * block]) for i in range(6)]
    streamed = np.concatenate([*blocks, conv.flush()])

    ref = render_7p1_to_stereo(pcm, brir)
    # Streamed output covers the real-time region + flushed tail; it may be a few
    # blocks longer than `ref` (whole-block flush), so compare the overlap.
    assert streamed.shape[0] >= ref.shape[0]
    assert np.allclose(streamed[: ref.shape[0]], ref, atol=1e-9)
    assert np.allclose(streamed[ref.shape[0] :], 0.0, atol=1e-9)


def test_stereo_latency_and_shape():
    brir = _toy_brir()
    conv = StereoConvolver(brir.left, brir.right, 64)
    assert conv.latency == 64
    assert conv.n_channels == 3
    out = conv.process_block(np.zeros((64, 3)))
    assert out.shape == (64, 2)


def test_stereo_rejects_shape_mismatches():
    brir = _toy_brir()
    conv = StereoConvolver(brir.left, brir.right, 16)
    with pytest.raises(ValueError):
        conv.process_block(np.zeros((16, 2)))  # wrong channel count
    with pytest.raises(ValueError):
        conv.process_block(np.zeros((8, 3)))  # wrong block length
    with pytest.raises(ValueError):
        StereoConvolver(np.zeros((2, 10)), np.zeros((3, 10)), 16)  # L/R channel mismatch

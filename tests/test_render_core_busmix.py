"""R0 slice 2 parity: Rust `BusMixer` == Python `live_render.BusMixer`, bit-exact across blocks.

Exercises the stateful crossfade: a sequence of blocks with changing target buses, so the gain
ramps and the per-(c,b) state carry forward — the same state machine on both sides. The mix sum
over channels must match numpy `einsum("cbn,nc->bn")` (optimize=False) exactly.
"""
from __future__ import annotations

import numpy as np
import pytest

render_core = pytest.importorskip("render_core")
from binaural_headtracker.live_render import BusMixer as PyBusMixer  # noqa: E402


def _run_sequence(n_in, n_bus, fade, blocks, seed):
    rng = np.random.default_rng(seed)
    py = PyBusMixer(n_in, n_bus, fade_samples=fade)
    rs = render_core.BusMixer(n_in, n_bus, fade_samples=fade)
    for _ in range(blocks):
        block = int(rng.integers(32, 512))
        pcm = rng.standard_normal((block, n_in))
        targets = rng.integers(0, n_bus, size=n_in).astype(np.intp)
        out_py = np.asarray(py.mix(pcm, targets), dtype=np.float64)
        out_rs = np.asarray(rs.mix(pcm, targets), dtype=np.float64)
        assert out_py.shape == out_rs.shape == (n_bus, block)
        assert np.array_equal(out_py, out_rs), (
            f"max|diff|={np.max(np.abs(out_py - out_rs)):.3e}")


def test_busmix_square_bit_exact():
    _run_sequence(n_in=8, n_bus=8, fade=256, blocks=12, seed=0)


def test_busmix_dense_grid_bit_exact():
    # n_bus > n_in: the B4.4 dense-grid case (7 inputs -> many interpolated buses)
    _run_sequence(n_in=7, n_bus=36, fade=512, blocks=12, seed=1)


def test_busmix_fast_retarget_bit_exact():
    # short fade + many blocks: targets change faster than the ramp completes
    _run_sequence(n_in=6, n_bus=6, fade=64, blocks=20, seed=2)


def test_busmix_first_block_full_gain():
    # first block starts at full gain (no fade-in from silence) on both sides
    rng = np.random.default_rng(3)
    py = PyBusMixer(4, 4, fade_samples=128)
    rs = render_core.BusMixer(4, 4, fade_samples=128)
    pcm = rng.standard_normal((100, 4))
    t = np.array([0, 1, 2, 3], dtype=np.intp)
    assert np.array_equal(np.asarray(py.mix(pcm, t)), np.asarray(rs.mix(pcm, t)))

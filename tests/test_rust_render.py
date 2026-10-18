"""B8.2 — the native Rust render kernel matches the pure-Python renderer.

Skipped unless the optional native accelerator is built into the venv
(``maturin develop --release -m rust/Cargo.toml``); CI stays pure-Python.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("binaural_kernel")

from binaural_headtracker.live_render import WorldLocked7p1  # noqa: E402
from binaural_headtracker.rust_render import RustWorldLocked7p1  # noqa: E402
from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet  # noqa: E402
from binaural_headtracker.world import layout_7p1  # noqa: E402

SR = 48_000


def _synthetic_brir(n: int = 600) -> BakedBRIRSet:
    """A BRIR set with a distinct, well-separated IR per channel (delta + decaying tail).

    ``n`` spans several blocks so the partitioned convolution runs with P > 1 partitions.
    """
    rng = np.random.default_rng(0)
    c = len(CHANNELS_7P1)
    left = np.zeros((c, n))
    right = np.zeros((c, n))
    for i in range(c):
        left[i, 3 + i] = 1.0
        right[i, 5 + i] = 0.8
        left[i, 20:400] = rng.normal(0, 0.02, 380)
        right[i, 20:400] = rng.normal(0, 0.02, 380)
    return BakedBRIRSet(channels=CHANNELS_7P1, left=left, right=right, samplerate=SR)


@pytest.mark.parametrize("lfe_mode", ["brir", "flat"])
@pytest.mark.parametrize("block", [128, 256, 512])
def test_rust_matches_python_world_locked(lfe_mode: str, block: int) -> None:
    brir = _synthetic_brir()
    c = brir.n_channels
    az0 = layout_7p1(channels=brir.channels).head_relative_directions()[:, 0].copy()
    cpu = WorldLocked7p1(brir, block_size=block, fade_ms=20.0, lfe_mode=lfe_mode)
    rust = RustWorldLocked7p1(brir, block_size=block, fade_ms=20.0, lfe_mode=lfe_mode)

    rng = np.random.default_rng(1)
    max_err = 0.0
    for i in range(40):
        pcm = rng.standard_normal((block, c)) * 0.1
        # A continuous sweep + alternation forces every channel to change nearest BRIR, so
        # the crossfade (two live voices / two active buses) is exercised, not just steady state.
        az = az0 + 50.0 * np.sin(i * 0.3) + (i % 2) * 20.0
        a = cpu.process_block(pcm, az)
        b = rust.process_block(pcm, az)
        assert b.shape == (block, 2)
        max_err = max(max_err, float(np.abs(a - b).max()))
    assert max_err < 1e-9, f"max|cpu-rust| = {max_err:.2e}"


def test_rust_block_shape_validated() -> None:
    brir = _synthetic_brir()
    rust = RustWorldLocked7p1(brir, block_size=128)
    with pytest.raises(ValueError):
        rust.process_block(np.zeros((64, brir.n_channels)), np.zeros(brir.n_channels))

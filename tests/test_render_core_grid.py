"""Parity: the Rust `render_core.RenderGrid` matches Python `InterpWorldLocked7p1.process_block`.

`RenderGrid` is the full per-block world-lock render in Rust (BusMixer + the shared native
convolution core + LFE). It must reproduce the Python `InterpWorldLocked7p1` (built with
`use_rust=True`, so its grid convolution is the same native kernel) to floating error over a head
sweep that forces every directional channel to change its nearest grid bus — exercising the
crossfade, not just steady state. The LFE path (brir) uses the numpy `StereoConvolver` in Python vs
the native core in Rust, so parity is to FFT floating error, not bit-exact; the mixer + flat-LFE are
bit-exact.

Both extensions are optional-native: skipped unless built into the venv
(`maturin build` + pip install of `render_core` and `binaural_kernel`).
"""
from __future__ import annotations

import numpy as np
import pytest

render_core = pytest.importorskip("render_core")
pytest.importorskip("binaural_kernel")

from binaural_headtracker.live_render import InterpWorldLocked7p1  # noqa: E402
from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet  # noqa: E402
from binaural_headtracker.world import layout_7p1  # noqa: E402

SR = 48_000


def _synthetic_brir(n: int = 600) -> BakedBRIRSet:
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


def _build_rust(py: InterpWorldLocked7p1, brir: BakedBRIRSet, block: int, lfe_mode: str):
    dense = py._dense  # noqa: SLF001 — parity test deliberately mirrors the Python internals
    fade_samples = max(1, round(20.0 * 1e-3 * brir.samplerate))
    kw = dict(lfe_mode=lfe_mode)
    if py._lfe_idx is not None:  # noqa: SLF001
        li = py._lfe_idx  # noqa: SLF001
        kw["lfe_idx"] = int(li)
        if lfe_mode == "brir":
            kw["lfe_left"] = np.ascontiguousarray(brir.left[li])
            kw["lfe_right"] = np.ascontiguousarray(brir.right[li])
        else:
            kw["lfe_gain_l"], kw["lfe_gain_r"] = py._lfe_gain  # noqa: SLF001
    return render_core.RenderGrid(
        np.ascontiguousarray(dense.left),
        np.ascontiguousarray(dense.right),
        [float(a) for a in py._grid_az],  # noqa: SLF001
        [int(i) for i in py._dir_idx],  # noqa: SLF001
        brir.n_channels,
        fade_samples,
        block,
        **kw,
    )


@pytest.mark.parametrize("method", ["nearest", "timealign"])
@pytest.mark.parametrize("lfe_mode", ["brir", "flat"])
@pytest.mark.parametrize("block", [128, 256])
def test_rust_grid_matches_python_interp(method: str, lfe_mode: str, block: int) -> None:
    brir = _synthetic_brir()
    c = brir.n_channels
    az0 = layout_7p1(channels=brir.channels).head_relative_directions()[:, 0].copy()
    py = InterpWorldLocked7p1(brir, method=method, block_size=block, fade_ms=20.0,
                              grid_step=10.0, lfe_mode=lfe_mode, use_rust=True)
    assert py.engine == "rust"
    rust = _build_rust(py, brir, block, lfe_mode)

    rng = np.random.default_rng(1)
    max_err = 0.0
    for i in range(40):
        pcm = rng.standard_normal((block, c)) * 0.1
        az = az0 + 70.0 * np.sin(i * 0.3) + (i % 2) * 15.0  # sweep: forces grid-bus changes
        a = py.process_block(np.ascontiguousarray(pcm), np.ascontiguousarray(az))
        b = np.asarray(rust.process_block(np.ascontiguousarray(pcm), np.ascontiguousarray(az)))
        assert b.shape == (block, 2)
        max_err = max(max_err, float(np.abs(a - b).max()))
    assert max_err < 1e-9, f"max|python-rust| = {max_err:.2e}"

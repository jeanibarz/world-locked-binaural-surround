"""R0 parity: the Rust `render_core` must match `live_render.py`'s steering/crossfade BIT-EXACTLY.

The reference formulas below are copied verbatim from `src/binaural_headtracker/live_render.py`
(`_wrapped_deg` :59, `nearest_brir_index` :79, `WorldLockedSource._gain_ramp` :217) so the test runs
in a minimal venv (numpy only). A second check imports the *real* functions when their deps are
present, guarding against copy-drift. `render_core` is `importorskip`ed (built via
`maturin develop -m rust-render-core/Cargo.toml`), the kernel's optional-native pattern.
"""
from __future__ import annotations

import numpy as np
import pytest

render_core = pytest.importorskip("render_core")


# --- faithful references (verbatim from live_render.py) ---
def ref_wrapped_deg(delta: float) -> float:
    return (delta + 180.0) % 360.0 - 180.0


def ref_nearest(azimuths: dict[int, float], src: float) -> int:
    best_i, best_d = None, None
    for i, az in azimuths.items():
        d = abs(ref_wrapped_deg(src - az))
        if best_d is None or d < best_d - 1e-9:
            best_i, best_d = i, d
    assert best_i is not None
    return best_i


def ref_gain_ramp(start: float, end: float, fade_samples: float, block: int) -> np.ndarray:
    step = (1.0 if end >= start else -1.0) / fade_samples
    ramp = start + step * np.arange(1, block + 1, dtype=np.float64)
    lo, hi = (start, end) if end >= start else (end, start)
    return np.clip(ramp, lo, hi)


def test_wrapped_deg_bit_exact():
    rng = np.random.default_rng(0)
    for delta in np.concatenate([rng.uniform(-720, 720, 500), [180.0, -180.0, 0.0, 360.0, -360.0]]):
        assert render_core.wrapped_deg_py(float(delta)) == ref_wrapped_deg(float(delta))


def test_nearest_brir_index_bit_exact():
    rng = np.random.default_rng(1)
    # a realistic 7-point ring (indices in RING order), many random source angles
    ring = {3: 0.0, 0: 30.0, 1: -30.0, 5: 90.0, 6: -90.0, 7: 135.0, 8: -135.0}
    pairs = list(ring.items())
    for src in rng.uniform(-180, 180, 2000):
        assert render_core.nearest_brir_index(pairs, float(src)) == ref_nearest(ring, float(src))


def test_gain_ramp_bit_exact():
    rng = np.random.default_rng(2)
    for _ in range(300):
        start = float(rng.uniform(0, 1)); end = float(rng.uniform(0, 1))
        fade = float(rng.uniform(1, 4096)); block = int(rng.integers(1, 4096))
        got = np.asarray(render_core.gain_ramp(start, end, fade, block), dtype=np.float64)
        want = ref_gain_ramp(start, end, fade, block)
        assert got.shape == want.shape
        assert np.array_equal(got, want)  # bit-exact, not approximate


def test_reference_matches_live_render_when_available():
    """Guard against copy-drift: the real live_render functions == the references used above."""
    try:
        from binaural_headtracker.live_render import _wrapped_deg, nearest_brir_index
    except Exception:  # noqa: BLE001 — deps not present in a minimal venv; skip
        pytest.skip("live_render not importable in this env")
    rng = np.random.default_rng(3)
    ring = {3: 0.0, 0: 30.0, 1: -30.0, 5: 90.0, 6: -90.0}
    for src in rng.uniform(-180, 180, 500):
        assert _wrapped_deg(float(src)) == ref_wrapped_deg(float(src))
        assert nearest_brir_index(ring, float(src)) == ref_nearest(ring, float(src))

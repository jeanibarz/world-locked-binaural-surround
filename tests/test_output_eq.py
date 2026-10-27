"""Tests for the global output-bus comp + EQ stage (B5.3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile
from scipy.signal import fftconvolve, sosfilt

from binaural_headtracker.eq import design_sos
from binaural_headtracker.output_eq import EarChain, OutputEQ, load_output_eq

FS = 48000


def _write_ir(path: Path, ir: np.ndarray) -> None:
    wavfile.write(str(path), FS, ir.astype(np.float32))


def _make_config(tmp_path: Path) -> Path:
    """A minimal EqualizerAPO binaural config with the owner's chain shape.

    Trivial per-channel convolutions + a downmix to L/R, then a per-ear comp Include
    (FIR + biquads, different per ear) and a shared subjective-EQ block.
    """
    rng = np.random.default_rng(0)
    _write_ir(tmp_path / "left.wav", rng.standard_normal(64) * 0.1)
    _write_ir(tmp_path / "right.wav", rng.standard_normal(64) * 0.1)

    (tmp_path / "comp.txt").write_text(
        "Channel: L R\n"
        "Preamp: -6 dB\n"
        "Channel: L\n"
        "Convolution: left.wav\n"
        "Filter: ON PK Fc 3000 Hz Gain -4 dB Q 2\n"
        "Channel: R\n"
        "Convolution: right.wav\n"
        "Preamp: -1 dB\n"
        "Filter: ON PK Fc 5000 Hz Gain -3 dB Q 2\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "main.txt"
    cfg.write_text(
        "Channel: L R\n"
        "Filter: ON PK Fc 500 Hz Gain 99 dB Q 1\n"   # pre-downmix: MUST be dropped
        "Copy: L=0*L+L R=0*R+R\n"                     # the (trivial) downmix
        "Channel: L R\n"
        "Include: comp.txt\n"
        "# subjective EQ (both ears, after the include resets Channel to all)\n"
        "Preamp: -4.5 dB\n"
        "Filter: ON PK Fc 1000 Hz Gain 6 dB Q 1\n"
        "Filter: ON LS Fc 100 Hz Gain 8 dB\n",
        encoding="utf-8",
    )
    return cfg


def _reference(block: np.ndarray, chain: EarChain, specs_fs: int = FS) -> np.ndarray:
    """One-shot offline application of an EarChain: gain -> FIR -> biquads."""
    n = block.shape[0]
    y = block * chain.gain
    if chain.fir is not None:
        y = fftconvolve(y, chain.fir)[:n]
    sos = design_sos(chain.specs, specs_fs)
    if sos.shape[0]:
        y = sosfilt(sos, y)
    return y


def test_extracts_post_downmix_chain(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    eq = load_output_eq(cfg, samplerate=FS, block_size=32)
    # The 99 dB pre-downmix filter must be gone; a stray 99 dB peak would be unmissable.
    imp = np.zeros((32, 2))
    imp[0] = 1.0
    out = eq.process_block(imp)
    # With the +99 dB pre-downmix filter correctly dropped the impulse response peaks
    # at ~0.07; a live +99 dB peaking filter would push this to ~10^4. A tight bound is
    # what makes the test discriminating (a loose `< 10` passed even with the filter live).
    assert np.abs(out).max() < 0.5


def test_gains_firs_and_specs(tmp_path: Path) -> None:
    from binaural_headtracker.output_eq import _extract_ear_chains, _read_commands

    cfg = _make_config(tmp_path)
    chains = _extract_ear_chains(_read_commands(cfg), FS)
    left, right = chains["L"], chains["R"]
    # L: comp -6 + subjective -4.5 ; R also the per-ear -1
    assert left.gain == pytest.approx(10 ** ((-6 - 4.5) / 20))
    assert right.gain == pytest.approx(10 ** ((-6 - 1 - 4.5) / 20))
    # The comp FIR must be the per-ear IR from the matching WAV (not the other ear's, not
    # None): a loader that swapped L/R or dropped the IR passed when only `is not None` was
    # checked. Compare against the source WAVs read back (same samplerate ⇒ no resample).
    left_ir = wavfile.read(str(cfg.parent / "left.wav"))[1].astype(np.float64)
    right_ir = wavfile.read(str(cfg.parent / "right.wav"))[1].astype(np.float64)
    assert left.fir is not None and np.array_equal(left.fir, left_ir)
    assert right.fir is not None and np.array_equal(right.fir, right_ir)
    assert not np.array_equal(left.fir, right.fir)  # the two ears are distinct IRs
    # comp filter (per ear) + 2 subjective filters, and the +99 dB one excluded.
    assert [s.fc for s in left.specs] == [3000, 1000, 100]
    assert [s.fc for s in right.specs] == [5000, 1000, 100]
    assert all(s.gain_db != 99 for s in left.specs + right.specs)


def test_streaming_equals_offline(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    from binaural_headtracker.output_eq import _extract_ear_chains, _read_commands

    chains = _extract_ear_chains(_read_commands(cfg), FS)
    block_size = 64
    eq = load_output_eq(cfg, samplerate=FS, block_size=block_size)

    rng = np.random.default_rng(1)
    x = rng.standard_normal((block_size * 20, 2))
    streamed = np.concatenate(
        [eq.process_block(x[i : i + block_size]) for i in range(0, x.shape[0], block_size)]
    )
    ref = np.column_stack([_reference(x[:, 0], chains["L"]), _reference(x[:, 1], chains["R"])])
    assert np.allclose(streamed, ref, atol=1e-9)


def test_include_resets_channel_to_all(tmp_path: Path) -> None:
    # The subjective block comes after an Include that ends on `Channel: R`. If the reset
    # to "all" did not happen, those filters would hit R only and L would lack them.
    cfg = _make_config(tmp_path)
    from binaural_headtracker.output_eq import _extract_ear_chains, _read_commands

    chains = _extract_ear_chains(_read_commands(cfg), FS)
    assert 1000 in [s.fc for s in chains["L"].specs]  # subjective filter reached L
    assert 100 in [s.fc for s in chains["L"].specs]


def test_no_lr_chain_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bare.txt"
    cfg.write_text("Channel: C\nFilter: ON PK Fc 1000 Hz Gain 3 dB Q 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no output-bus"):
        load_output_eq(cfg, samplerate=FS, block_size=32)


def test_passthrough_when_identity() -> None:
    eq = OutputEQ(EarChain(), EarChain(), block_size=16, samplerate=FS)
    rng = np.random.default_rng(2)
    block = rng.standard_normal((16, 2))
    assert np.allclose(eq.process_block(block), block)


def test_block_shape_validated() -> None:
    eq = OutputEQ(EarChain(gain=0.5), EarChain(gain=0.5), block_size=16, samplerate=FS)
    with pytest.raises(ValueError, match="must be"):
        eq.process_block(np.zeros((8, 2)))
    with pytest.raises(ValueError, match="must be"):
        eq.process_block(np.zeros((16, 1)))

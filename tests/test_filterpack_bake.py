"""M-D2 baker tests: a baked pack round-trips through the M-D1 loader, exact at int16 precision."""
from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker import filterpack as fp
from binaural_headtracker import filterpack_bake as bake


def _synthetic_speakers(n_dir=4, ir_len=96, seed=0):
    rng = np.random.default_rng(seed)
    decay = np.exp(-np.arange(ir_len) / 20.0)
    spk = []
    azis = [0.0, 30.0, -30.0, 110.0][:n_dir]
    for i, az in enumerate(azis):
        left = (rng.standard_normal(ir_len) * decay)
        right = (rng.standard_normal(ir_len) * decay)
        m = max(np.max(np.abs(left)), np.max(np.abs(right))) + 1e-9
        spk.append(bake.BakeSpeaker(f"CH{i}", az, left / m, right / m))
    # a non-directional LFE (azimuth None)
    lfe = rng.standard_normal(ir_len) * decay
    lfe /= np.max(np.abs(lfe)) + 1e-9
    spk.append(bake.BakeSpeaker("LFE", None, lfe, lfe))
    return spk


def _q(x):  # int16 round-trip a float64 IR, exactly as write/read do
    q = np.clip(np.round(np.asarray(x) * 32767.0), -32768, 32767).astype("<i2")
    return q.astype(np.float64) / 32768.0


def test_bake_roundtrips_through_loader(tmp_path):
    spk = _synthetic_speakers()
    out = bake.write_pack(tmp_path / "pack", name="synthetic", samplerate=48000, comp="ctf",
                          speakers=spk, lfe_channel="LFE", lfe_mode="brir")
    pack = fp.load(out)  # M-D1 loader: validates convention, integrity, reads wavs
    assert pack.name == "synthetic" and pack.samplerate == 48000 and pack.comp == "ctf"
    assert [s.channel for s in pack.speakers] == ["CH0", "CH1", "CH2", "CH3", "LFE"]
    assert pack.lfe_channel == "LFE"
    assert next(s for s in pack.speakers if s.channel == "LFE").azimuth_deg is None
    assert next(s for s in pack.speakers if s.channel == "CH1").azimuth_deg == 30.0
    # exact at int16 precision: loaded IR == source quantized exactly as write/read do
    for src, loaded in zip(spk, pack.speakers):
        assert np.array_equal(loaded.left, _q(src.left))
        assert np.array_equal(loaded.right, _q(src.right))


def test_baked_pack_digest_is_stable(tmp_path):
    out = bake.write_pack(tmp_path / "pack", name="s", samplerate=48000, comp="df",
                          speakers=_synthetic_speakers(seed=7), lfe_channel="LFE")
    assert fp.canonical_digest(fp.load(out)) == fp.canonical_digest(fp.load(out))


def test_bake_rejects_mismatched_ir_lengths(tmp_path):
    spk = _synthetic_speakers()
    spk[1] = bake.BakeSpeaker("CH1", 30.0, np.zeros(10), np.zeros(10))  # wrong length
    with pytest.raises(ValueError, match="share one length"):
        bake.write_pack(tmp_path / "p", name="x", samplerate=48000, comp="ctf", speakers=spk)


def test_bake_baked_brir_when_surround_importable(tmp_path):
    """End-to-end: synthetic <ch>_L/<ch>_R wavs -> load_baked_brir -> bake -> load. Needs scipy."""
    try:
        import scipy.io.wavfile as wav  # noqa: F401
        from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("surround/scipy not importable in this env")
    import scipy.io.wavfile as wav
    src = tmp_path / "brir"
    src.mkdir()
    rng = np.random.default_rng(1)
    for ch in ("L", "R", "C", "LFE", "RL", "RR", "SL", "SR"):  # full 7.1 set (CHANNELS_7P1)
        for ear in ("_L", "_R"):
            ir = (rng.standard_normal(128) * 0.1).astype(np.float32)
            wav.write(src / f"{ch}{ear}.wav", 48000, ir)
    out = bake.bake_baked_brir(src, tmp_path / "pack", name="owner-synth")
    pack = fp.load(out)
    assert "LFE" in [s.channel for s in pack.speakers]
    assert next(s for s in pack.speakers if s.channel == "LFE").azimuth_deg is None

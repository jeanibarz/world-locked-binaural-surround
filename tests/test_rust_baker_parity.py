"""Track-S gate: the Rust `filterpack_bake` baker produces the same pack as the Python baker.

Bakes a synthetic 7.1 baked-BRIR set with BOTH the Rust binary and the Python
`filterpack_bake.bake_baked_brir`, then asserts int16-exactness + manifest-content equality — which
means the shared loader computes the identical `canonical_digest` for both (the M-D2 round-trip
gate), so owner-pack baking is zero-Python-equivalent.

Skips unless the Rust binary is built; its path comes from `$WLBS_FILTERPACK_BAKE` (default: the
shared cargo target). Needs numpy + the repo `src` on the path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent
BAKE_BIN = Path(os.environ.get("WLBS_FILTERPACK_BAKE", "/mnt/d/cargo-target/release/filterpack_bake"))
CHANNELS = ["L", "R", "C", "LFE", "RL", "RR", "SL", "SR"]

pytestmark = pytest.mark.skipif(not BAKE_BIN.exists(), reason=f"build {BAKE_BIN} first")


def _write_mono_f32(path: Path, data: np.ndarray, sr: int = 48000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # write int16 mono (both bakers read int/float; int keeps it simple+exact)
        w.setframerate(sr)
        pcm = np.clip(np.round(data * 32767.0), -32768, 32767).astype("<i2")
        w.writeframes(pcm.tobytes())


def _read_i16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 2 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def test_rust_baker_matches_python(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "src"))
    from binaural_headtracker.filterpack_bake import bake_baked_brir

    # synthetic baked BRIRs: a distinct decaying IR per channel/ear
    src = tmp_path / "brir"
    src.mkdir()
    rng = np.random.default_rng(0)
    for i, ch in enumerate(CHANNELS):
        n = 500 + 7 * i  # varying lengths -> baker pads to the longest
        _write_mono_f32(src / f"{ch}_L.wav", rng.normal(0, 0.1, n) * np.exp(-np.arange(n) / 200))
        _write_mono_f32(src / f"{ch}_R.wav", rng.normal(0, 0.1, n) * np.exp(-np.arange(n) / 180))

    out_rust = tmp_path / "pack_rust"
    out_py = tmp_path / "pack_py"
    subprocess.run([str(BAKE_BIN), str(src), str(out_rust), "--name", "mine", "--comp", "ctf"], check=True)
    bake_baked_brir(str(src), str(out_py), name="mine", comp="ctf")

    mr = json.loads((out_rust / "manifest.json").read_text())
    mp = json.loads((out_py / "manifest.json").read_text())
    for k in ("filterpack_version", "samplerate", "ir_length", "comp"):
        assert mr[k] == mp[k], k
    assert mr["layout"]["channels"] == mp["layout"]["channels"]
    assert mr["convention"] == mp["convention"]
    for ch in mr["layout"]["channels"]:
        assert mr["layout"]["speakers"][ch]["azimuth_deg"] == mp["layout"]["speakers"][ch]["azimuth_deg"], ch

    max_err = 0
    for ch in mr["layout"]["channels"]:
        a = _read_i16(out_rust / f"{ch}.wav").astype(np.int64)
        b = _read_i16(out_py / f"{ch}.wav").astype(np.int64)
        assert a.shape == b.shape, ch
        max_err = max(max_err, int(np.abs(a - b).max()))
    assert max_err == 0, f"int16 differ by {max_err}"

"""Generate a deterministic golden filter pack for M-D1 tests.

Deterministic (fixed seed) so the canonical digest is stable across machines and across the Python
and Rust loaders — that stability is the whole point of the M-D1 byte-identical gate. Stdlib + numpy
only (no SOFA/scipy). Run: ``python tests/fixtures/filterpack/make_fixture.py``.
"""
from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import numpy as np

OUT = Path(__file__).parent / "owner_min"
SR = 48000
IR_LEN = 64          # tiny IRs keep the fixture small; the format is length-agnostic
# a minimal but representative input layout: a few directional speakers + a non-directional LFE
SPEAKERS = [
    ("L",   30.0),
    ("R",  -30.0),
    ("C",    0.0),
    ("LFE", None),   # non-directional
    ("RL", 110.0),
    ("RR", -110.0),
]


def _ir(seed: int) -> np.ndarray:
    """A deterministic decaying-noise stereo IR, shape (IR_LEN, 2), in [-1, 1]."""
    rng = np.random.default_rng(seed)
    decay = np.exp(-np.arange(IR_LEN) / 12.0)
    stereo = rng.standard_normal((IR_LEN, 2)) * decay[:, None]
    stereo /= np.max(np.abs(stereo)) + 1e-9
    return stereo.astype(np.float64)


def _write_wav_int16(path: Path, stereo: np.ndarray) -> None:
    pcm = np.clip(np.round(stereo * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files_hash: dict[str, str] = {}
    speakers_manifest: dict[str, dict] = {}
    for i, (ch, az) in enumerate(SPEAKERS):
        fname = f"{ch}.wav"
        _write_wav_int16(OUT / fname, _ir(seed=1000 + i))
        files_hash[fname] = hashlib.sha256((OUT / fname).read_bytes()).hexdigest()
        speakers_manifest[ch] = {"azimuth_deg": az, "ir": fname}

    manifest = {
        "filterpack_version": 1,
        "name": "owner-min-fixture",
        "provenance": {"source": "synthetic-fixture", "subject": "none",
                       "license": "owner-redistributable"},
        "samplerate": SR,
        "ir_length": IR_LEN,
        "comp": "ctf",
        "convention": {"azimuth_zero": "front", "azimuth_positive": "counterclockwise",
                       "angle_units": "degrees", "yaw_sign": 1, "pitch_sign": 1, "roll_sign": 1},
        "gain": {"integrated_lufs": -23.0, "peak_dbfs": -1.0},
        "layout": {
            "channels": [ch for ch, _ in SPEAKERS],
            "lfe": {"channel": "LFE", "mode": "brir"},
            "speakers": speakers_manifest,
        },
        "grid": None,
        "integrity": {"algo": "sha256", "files": files_hash},
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote fixture: {OUT}  ({len(SPEAKERS)} speakers, ir_length={IR_LEN})")


if __name__ == "__main__":
    main()

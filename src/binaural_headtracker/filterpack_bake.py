"""filterpack baker (M-D2) — the offline producer that emits packs the engine loads.

Turns a measured set (the owner's baked 7.1 BRIRs, or a dense-grid SOFA) into a **filter pack** the
M-D1 loader (``filterpack.py``) reads without SOFA/HDF5 at runtime. This is the in-repo core of the
studio's bake path (OQ1: the full studio lives in ``binaural-localization`` later); it stays Python
because it is offline and latency-irrelevant, and it is where CTF/DF-compensated variants become
engine-ready packs.

IR payload is int16 ``.wav`` — the same format ``filterpack.py`` reads with the stdlib ``wave``
module (no scipy at load time, per G3). A lossless float/binary payload is OQ4 future work.
"""
from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from binaural_headtracker.filterpack import FILTERPACK_VERSION

DEFAULT_CONVENTION = {
    "azimuth_zero": "front", "azimuth_positive": "counterclockwise", "angle_units": "degrees",
    "yaw_sign": 1, "pitch_sign": 1, "roll_sign": 1,
}


@dataclass(frozen=True)
class BakeSpeaker:
    """One input channel's BRIR to bake. ``azimuth_deg`` is ``None`` for the non-directional LFE."""
    channel: str
    azimuth_deg: float | None
    left: np.ndarray   # float64 in [-1, 1]
    right: np.ndarray


def _write_int16_wav(path: Path, left: np.ndarray, right: np.ndarray, samplerate: int) -> str:
    """Write a 2-channel int16 WAV (ch0=left ear, ch1=right ear); return its sha256."""
    stereo = np.stack([np.asarray(left, np.float64), np.asarray(right, np.float64)], axis=1)
    pcm = np.clip(np.round(stereo * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(int(samplerate))
        w.writeframes(pcm.tobytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_pack(
    out_dir: str | Path,
    *,
    name: str,
    samplerate: int,
    comp: str,
    speakers: list[BakeSpeaker],
    gain: dict | None = None,
    convention: dict | None = None,
    lfe_channel: str | None = None,
    lfe_mode: str = "brir",
    provenance: dict | None = None,
) -> Path:
    """Write a baked-set filter pack (manifest + per-speaker int16 BRIR wavs). Returns ``out_dir``.

    All IRs must share one length. The manifest matches ``filterpack.schema.v1.json`` so the pack
    round-trips through :func:`filterpack.load`.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    if not speakers:
        raise ValueError("no speakers to bake")
    ir_len = len(speakers[0].left)
    if any(len(s.left) != ir_len or len(s.right) != ir_len for s in speakers):
        raise ValueError("all speaker IRs must share one length")

    files_hash: dict[str, str] = {}
    chan_order: list[str] = []
    speakers_manifest: dict[str, dict] = {}
    for s in speakers:
        fname = f"{s.channel}.wav"
        files_hash[fname] = _write_int16_wav(d / fname, s.left, s.right, samplerate)
        chan_order.append(s.channel)
        speakers_manifest[s.channel] = {"azimuth_deg": s.azimuth_deg, "ir": fname}

    manifest = {
        "filterpack_version": FILTERPACK_VERSION,
        "name": name,
        "provenance": provenance or {"source": "baker", "subject": "", "license": "owner"},
        "samplerate": int(samplerate),
        "ir_length": int(ir_len),
        "comp": comp,
        "convention": convention or dict(DEFAULT_CONVENTION),
        "gain": gain or {"integrated_lufs": None, "peak_dbfs": None},
        "layout": {
            "channels": chan_order,
            "lfe": ({"channel": lfe_channel, "mode": lfe_mode} if lfe_channel else None),
            "speakers": speakers_manifest,
        },
        "grid": None,
        "integrity": {"algo": "sha256", "files": files_hash},
    }
    (d / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return d


def bake_baked_brir(brir_dir: str | Path, out_dir: str | Path, *, name: str, comp: str = "ctf",
                    **kw) -> Path:
    """Bake the owner's measured 7.1 baked-BRIR set (``<ch>_L.wav``/``<ch>_R.wav``) into a pack.

    Uses the engine's own loader + layout so the baked pack reproduces today's routing + LFE.
    """
    from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1, load_baked_brir

    brir = load_baked_brir(brir_dir)
    speakers = []
    for i, ch in enumerate(brir.channels):
        directional = ch != "LFE" and ch in SPEAKER_LAYOUT_7P1
        az = float(SPEAKER_LAYOUT_7P1[ch][0]) if directional else None
        speakers.append(BakeSpeaker(channel=ch, azimuth_deg=az,
                                    left=brir.left[i], right=brir.right[i]))
    lfe = "LFE" if "LFE" in brir.channels else None
    return write_pack(out_dir, name=name, samplerate=brir.samplerate, comp=comp,
                      speakers=speakers, lfe_channel=lfe, **kw)

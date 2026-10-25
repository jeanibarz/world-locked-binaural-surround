"""filterpack v1 — the engine↔toolkit contract (RFC: standalone-distributable-and-rust-engine).

A **filter pack** is a directory: a ``manifest.json`` + per-speaker stereo-BRIR ``.wav`` files
(ch0 = left ear, ch1 = right ear), plus an optional pre-baked ``grid`` overlay. The real-time
engine consumes a pack **without parsing SOFA/HDF5 or doing CTF/DF decomposition** — that is all
offline in the toolkit (the baker, which lives in ``binaural-localization``). The pack's *primary*
payload is the measured baked set: per-input-channel BRIRs + the 7.1 input layout + non-directional
LFE handling — i.e. what the owner's daily render actually uses (see the RFC's pack-contract
section, corrected by the consensus attack).

This module is the **reference loader**. The shipped engine will use a single Rust loader exposed to
Python via PyO3; :func:`canonical_digest` defines the exact byte layout both implementations must
reproduce, so "byte-identical reads from both sides" (M-D1's gate) is a `sha256` equality check.

Dependency-light on purpose: stdlib ``wave`` + ``numpy`` (already a core dep). No SOFA, no scipy.
"""
from __future__ import annotations

import hashlib
import json
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FILTERPACK_VERSION = 1


@dataclass(frozen=True)
class Speaker:
    """One input channel's BRIR. ``azimuth_deg`` is ``None`` for the non-directional LFE."""
    channel: str
    azimuth_deg: float | None
    left: np.ndarray   # left-ear IR,  float64, shape (ir_length,)
    right: np.ndarray  # right-ear IR, float64, shape (ir_length,)


@dataclass(frozen=True)
class FilterPack:
    name: str
    samplerate: int
    ir_length: int
    comp: str                      # "ctf" | "df"
    convention: dict               # azimuth zero/sign, euler signs — else packs localize backwards
    gain: dict                     # integrated loudness / peak — for level-matched live switching
    lfe_channel: str | None
    lfe_mode: str                  # "brir" | "flat"
    speakers: tuple[Speaker, ...]  # in manifest channel order (input-routing order)
    provenance: dict


class FilterPackError(ValueError):
    """A pack is malformed, fails validation, or fails its integrity hash."""


_REQUIRED = ("filterpack_version", "name", "samplerate", "ir_length", "comp",
             "convention", "gain", "layout", "integrity")
_REQUIRED_CONVENTION = ("azimuth_zero", "azimuth_positive", "angle_units",
                        "yaw_sign", "pitch_sign", "roll_sign")


def _read_stereo_wav(path: Path, *, expect_len: int) -> tuple[np.ndarray, np.ndarray]:
    """Read a 2-channel WAV as (left_ear, right_ear) float64 in [-1, 1]. int16 or float32 PCM."""
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 2:
            raise FilterPackError(
                f"{path.name}: expected 2 channels (L-ear/R-ear), got {w.getnchannels()}")
        n = w.getnframes()
        sw = w.getsampwidth()
        raw = w.readframes(n)
    if sw == 2:          # int16
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sw == 4:        # float32
        a = np.frombuffer(raw, dtype="<f4").astype(np.float64)
    else:
        raise FilterPackError(f"{path.name}: unsupported sample width {sw*8} bits")
    a = a.reshape(-1, 2)
    if a.shape[0] != expect_len:
        raise FilterPackError(f"{path.name}: ir_length {a.shape[0]} != manifest {expect_len}")
    return np.ascontiguousarray(a[:, 0]), np.ascontiguousarray(a[:, 1])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load(pack_dir: str | Path) -> FilterPack:
    """Load + validate a filter pack. Raises :class:`FilterPackError` on any contract violation."""
    d = Path(pack_dir)
    man_path = d / "manifest.json"
    if not man_path.is_file():
        raise FilterPackError(f"no manifest.json in {d}")
    try:
        m = json.loads(man_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise FilterPackError(f"manifest.json is not valid JSON: {e}") from e

    for k in _REQUIRED:
        if k not in m:
            raise FilterPackError(f"manifest missing required key: {k!r}")
    if m["filterpack_version"] != FILTERPACK_VERSION:
        raise FilterPackError(
            f"unsupported filterpack_version {m['filterpack_version']} "
            f"(this loader is v{FILTERPACK_VERSION})")
    for k in _REQUIRED_CONVENTION:
        if k not in m["convention"]:
            raise FilterPackError(
                f"convention missing required key: {k!r} (packs must be unambiguous)")

    sr = int(m["samplerate"])
    ir_len = int(m["ir_length"])
    layout = m["layout"]
    lfe = layout.get("lfe") or {}
    lfe_channel = lfe.get("channel")
    lfe_mode = lfe.get("mode", "brir")

    # integrity first: every referenced IR file must match its recorded hash
    integ = m["integrity"]
    if integ.get("algo") != "sha256":
        raise FilterPackError(f"unsupported integrity algo {integ.get('algo')!r}")
    files = integ.get("files", {})

    speakers: list[Speaker] = []
    for ch in layout["channels"]:                 # manifest order == input-routing order
        spec = layout["speakers"].get(ch)
        if spec is None:
            raise FilterPackError(f"layout.channels lists {ch!r} but layout.speakers has no entry")
        ir_file = spec["ir"]
        if ir_file not in files:
            raise FilterPackError(f"{ir_file}: no integrity hash recorded")
        fpath = d / ir_file
        if not fpath.is_file():
            raise FilterPackError(f"{ir_file}: file missing")
        got = _sha256(fpath)
        if got != files[ir_file]:
            raise FilterPackError(f"{ir_file}: sha256 mismatch (pack corrupt or tampered)")
        left, right = _read_stereo_wav(fpath, expect_len=ir_len)
        az = spec.get("azimuth_deg")
        speakers.append(Speaker(channel=ch, azimuth_deg=(None if az is None else float(az)),
                                left=left, right=right))

    return FilterPack(
        name=str(m["name"]), samplerate=sr, ir_length=ir_len, comp=str(m["comp"]),
        convention=m["convention"], gain=m["gain"], lfe_channel=lfe_channel, lfe_mode=lfe_mode,
        speakers=tuple(speakers), provenance=m.get("provenance", {}))


def canonical_digest(pack: FilterPack) -> str:
    """A sha256 over a **fixed, cross-language byte layout** of the loaded pack.

    This is the M-D1 gate: the Rust loader (via PyO3) must produce the *same* digest for the same
    pack, proving byte-identical reads. Layout (integers big-endian, floats IEEE-754 float64 BE):

        b"FPAK\\x01"                      magic + version(1 byte)
        u32 samplerate · u32 ir_length · u32 n_speakers
        comp (len-prefixed u16 + utf8)
        per speaker, in order:
            channel (len-prefixed u16 + utf8)
            u8 has_azimuth · if 1: f64 azimuth_deg
            ir_length × f64 left · ir_length × f64 right
    """
    h = hashlib.sha256()
    h.update(b"FPAK")
    h.update(struct.pack(">B", FILTERPACK_VERSION))
    h.update(struct.pack(">III", pack.samplerate, pack.ir_length, len(pack.speakers)))

    def _s(x: str) -> None:
        b = x.encode("utf-8")
        h.update(struct.pack(">H", len(b)))
        h.update(b)

    _s(pack.comp)
    for sp in pack.speakers:
        _s(sp.channel)
        if sp.azimuth_deg is None:
            h.update(struct.pack(">B", 0))
        else:
            h.update(struct.pack(">B", 1))
            h.update(struct.pack(">d", sp.azimuth_deg))
        # float64 big-endian, exactly ir_length each
        h.update(sp.left.astype(">f8", copy=False).tobytes())
        h.update(sp.right.astype(">f8", copy=False).tobytes())
    return h.hexdigest()

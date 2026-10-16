"""Static 7.1 → binaural stereo renderer from per-speaker BRIRs (B1.6).

This renders a 7.1 PCM stream to stereo by convolving each speaker channel with
its own **binaural room impulse response** (one IR per ear) and summing — the
*fixed-head* case, reproducing a baked EqualizerAPO-style binaural surround
(e.g. the owner's hand-tuned Audeze LCD-GX set) inside our engine. No head
tracking yet: each channel uses a single BRIR at the neutral head orientation.

It is the counterpart of the SOFA path (:mod:`binaural_headtracker.hrir`): the
SOFA loader gives a *continuous* anechoic field for the head-tracked engine,
while this consumes a *sparse, fixed* set of 8 baked BRIRs — all the owner has
measured. Both feed the same convolution idea (FR-5).

Everything here is pure numpy/scipy, so it runs and is unit-tested headless. The
owner's `.wav` BRIRs are personal data kept out of git; point `load_baked_brir`
at the EqualizerAPO config directory that holds them (see
``docs/baked-brir-setup.md``).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve

# EqualizerAPO 7.1 channel labels, in Microsoft/WASAPI 7.1 interleave order
# (FL, FR, FC, LFE, back L/R, side L/R). A 7.1 PCM frame's columns are in this
# order, and the owner's baked WAVs are named "<label>_L.wav" / "<label>_R.wav".
CHANNELS_7P1: tuple[str, ...] = ("L", "R", "C", "LFE", "RL", "RR", "SL", "SR")

# Nominal world directions — azimuth counter-clockwise from front (+ = left),
# elevation 0 — per ITU-R BS.775 / SMPTE 7.1. LFE is non-directional (angle
# nominal only). Not needed to render a fixed head, but it is the layout the
# later ITD/magnitude interpolation (the head-tracked step) builds on, so it
# lives with the renderer. Matches the azimuth convention of hrir.py.
SPEAKER_LAYOUT_7P1: dict[str, tuple[float, float]] = {
    "L": (30.0, 0.0),
    "R": (-30.0, 0.0),
    "C": (0.0, 0.0),
    "LFE": (0.0, 0.0),
    "SL": (90.0, 0.0),
    "SR": (-90.0, 0.0),
    "RL": (135.0, 0.0),
    "RR": (-135.0, 0.0),
}


@dataclass(frozen=True)
class BakedBRIRSet:
    """Per-speaker binaural room impulse responses for a fixed head.

    ``left``/``right`` are ``(C, N)`` — one IR per channel per ear, in the order
    of ``channels``. All IRs share a length (shorter ones are zero-padded at
    load) and sample rate.
    """

    channels: tuple[str, ...]
    left: np.ndarray  # (C, N)
    right: np.ndarray  # (C, N)
    samplerate: int

    def __post_init__(self) -> None:
        c = len(self.channels)
        if self.left.shape[0] != c or self.right.shape[0] != c:
            raise ValueError("left/right must have one row per channel")
        if self.left.shape != self.right.shape:
            raise ValueError("left/right must have the same shape")

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def n_taps(self) -> int:
        return int(self.left.shape[1])

    def index(self, channel: str) -> int:
        return self.channels.index(channel)


def _read_ir(path: Path) -> tuple[int, np.ndarray]:
    """Read a mono IR WAV as float64 in [-1, 1]; take channel 0 if multichannel."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # these WAVs carry extra chunks scipy warns on
        sr, data = wavfile.read(path)
    data = np.asarray(data)
    if data.ndim > 1:
        data = data[:, 0]
    if np.issubdtype(data.dtype, np.integer):
        data = data.astype(np.float64) / float(np.iinfo(data.dtype).max)
    else:
        data = data.astype(np.float64)
    return int(sr), data


def load_baked_brir(
    directory,
    channels: tuple[str, ...] = CHANNELS_7P1,
    left_suffix: str = "_L",
    right_suffix: str = "_R",
) -> BakedBRIRSet:
    """Load a baked per-speaker BRIR set from a directory of mono WAVs.

    Expects ``<channel><left_suffix>.wav`` and ``<channel><right_suffix>.wav``
    for each channel (default EqualizerAPO naming, e.g. ``L_L.wav``/``L_R.wav``).
    All WAVs must share a sample rate; IRs are zero-padded to a common length.
    """
    directory = Path(directory)
    rate: int | None = None
    lefts: list[np.ndarray] = []
    rights: list[np.ndarray] = []
    for ch in channels:
        sr_l, ir_l = _read_ir(directory / f"{ch}{left_suffix}.wav")
        sr_r, ir_r = _read_ir(directory / f"{ch}{right_suffix}.wav")
        if rate is None:
            rate = sr_l
        if {sr_l, sr_r} != {rate}:
            raise ValueError(f"sample-rate mismatch for channel {ch}: {sr_l},{sr_r} vs {rate}")
        lefts.append(ir_l)
        rights.append(ir_r)

    n = max(ir.size for ir in (*lefts, *rights))
    left = np.zeros((len(channels), n))
    right = np.zeros((len(channels), n))
    for i, (il, ir) in enumerate(zip(lefts, rights, strict=True)):
        left[i, : il.size] = il
        right[i, : ir.size] = ir
    return BakedBRIRSet(channels=tuple(channels), left=left, right=right, samplerate=int(rate))


def convolve_and_sum_stereo(
    pcm: np.ndarray, left: np.ndarray, right: np.ndarray
) -> np.ndarray:
    """Offline per-channel convolve-and-sum → stereo, from raw ``(C, N)`` IR banks.

    ``pcm`` is ``(M, C)`` with columns aligned to the ``C`` rows of ``left`` and
    ``right`` (one left-ear and one right-ear IR per channel). Returns
    ``(M + N - 1, 2)`` — the left output is the sum of every channel convolved
    with its left-ear IR, likewise the right.

    This is the shared offline kernel of the static renderer. Both the baked-BRIR
    path (:func:`render_7p1_to_stereo`, B1.6) and the generic-SOFA path
    (:mod:`binaural_headtracker.render`, B1.5) feed it their own IR banks; the
    block-by-block real-time counterpart is
    :class:`binaural_headtracker.convolve.StereoConvolver` (B1.3).
    """
    pcm = np.asarray(pcm, dtype=np.float64)
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    if left.shape != right.shape or left.ndim != 2:
        raise ValueError("left/right IR banks must share shape (C, N)")
    n_channels, n_taps = left.shape
    if pcm.ndim != 2 or pcm.shape[1] != n_channels:
        raise ValueError(f"pcm must be (M, {n_channels}); got {pcm.shape}")
    out_len = pcm.shape[0] + n_taps - 1
    out = np.zeros((out_len, 2))
    for c in range(n_channels):
        x = pcm[:, c]
        if not np.any(x):
            continue
        out[:, 0] += fftconvolve(x, left[c])[:out_len]
        out[:, 1] += fftconvolve(x, right[c])[:out_len]
    return out


def render_7p1_to_stereo(pcm: np.ndarray, brir: BakedBRIRSet) -> np.ndarray:
    """Convolve a 7.1 PCM block with the per-speaker BRIRs → stereo (fixed head).

    ``pcm`` is ``(M, C)`` with columns in ``brir.channels`` order. Returns
    ``(M + N - 1, 2)`` (full linear convolution) — exactly the EqualizerAPO baked
    config's per-ear convolve-and-sum. Thin wrapper over
    :func:`convolve_and_sum_stereo`.
    """
    return convolve_and_sum_stereo(pcm, brir.left, brir.right)

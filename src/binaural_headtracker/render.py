"""Static 7.1 → binaural renderer over a generic SOFA HRIR set (B1.5).

This wires the four M1 building blocks into one offline renderer for the
**generic SOFA path**:

* the world layout (:mod:`binaural_headtracker.world`, B1.4) places the 7.1
  speakers and projects each to a head-relative ``(az, el)`` for a *fixed* head;
* the SOFA HRIR set (:mod:`binaural_headtracker.hrir`, B1.1) supplies a
  continuous anechoic field;
* ITD-aware interpolation (:mod:`binaural_headtracker.interpolate`, B1.2) looks up
  one left/right IR per speaker direction;
* the convolve-and-sum kernel (:func:`binaural_headtracker.surround.convolve_and_sum_stereo`,
  the offline sibling of the B1.3 streaming engine) renders PCM → stereo.

It is the SOFA counterpart of :func:`binaural_headtracker.surround.render_7p1_to_stereo`
(B1.6), which renders from the owner's 8 *baked* BRIRs instead. Both produce a
fixed-head binaural downmix — **no head tracking yet**. The seam that makes the
scene world-locked is already present: :meth:`build_speaker_hrirs` forwards
``head_position``/``head_rotation`` to the layout projection, so once the tracker
(M2) yields a head pose, re-running the lookup per frame and crossfading the
convolution (M3/B3.1) turns this static renderer into the live one. The IR bank
it builds also drops straight into :class:`binaural_headtracker.convolve.StereoConvolver`
for block-by-block real-time rendering (:meth:`SpeakerHRIRBank.streaming_convolver`).

Everything here is pure numpy/scipy and unit-tested headless on a synthetic SOFA
set — no device, no SOFA file.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from binaural_headtracker.convolve import StereoConvolver
from binaural_headtracker.hrir import HRIRSet
from binaural_headtracker.surround import convolve_and_sum_stereo
from binaural_headtracker.world import WorldLayout


@dataclass(frozen=True)
class SpeakerHRIRBank:
    """Per-speaker HRIRs for one fixed head pose, looked up from a SOFA set.

    Unlike the continuous :class:`~binaural_headtracker.hrir.HRIRSet` (measured at
    arbitrary grid directions), this holds exactly one left/right IR per output
    channel — the HRIR at each speaker's current head-relative direction. It is
    the HRIR-path analogue of :class:`~binaural_headtracker.surround.BakedBRIRSet`
    and the same shape that feeds the convolve-and-sum kernels.

    ``left``/``right`` are ``(C, N)`` in ``channels`` order; ``directions`` is
    ``(C, 2)`` — the ``(az, el)`` each IR was looked up at, kept for provenance
    and debugging (e.g. to confirm a head rotation swung the azimuths).
    """

    channels: tuple[str, ...]
    left: np.ndarray  # (C, N)
    right: np.ndarray  # (C, N)
    samplerate: int
    directions: np.ndarray  # (C, 2) az, el (deg)

    def __post_init__(self) -> None:
        c = len(self.channels)
        if self.left.shape[0] != c or self.right.shape[0] != c:
            raise ValueError("left/right must have one row per channel")
        if self.left.shape != self.right.shape:
            raise ValueError("left/right must have the same shape")
        if self.directions.shape != (c, 2):
            raise ValueError("directions must be (C, 2)")

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def n_taps(self) -> int:
        return int(self.left.shape[1])

    def render(self, pcm: np.ndarray) -> np.ndarray:
        """Offline render ``(M, C)`` PCM → ``(M + N - 1, 2)`` stereo (fixed head).

        PCM columns are in ``channels`` order. Full linear convolution; the
        real-time counterpart is :meth:`streaming_convolver`.
        """
        return convolve_and_sum_stereo(pcm, self.left, self.right)

    def streaming_convolver(self, block_size: int) -> StereoConvolver:
        """A block-by-block :class:`~binaural_headtracker.convolve.StereoConvolver`
        primed with this bank (the live-render path, B1.3)."""
        return StereoConvolver(self.left, self.right, block_size)


def build_speaker_hrirs(
    hrir_set: HRIRSet,
    layout: WorldLayout,
    *,
    interpolate: bool = True,
    k: int = 3,
    power: float = 2.0,
    head_position: np.ndarray | None = None,
    head_rotation: np.ndarray | None = None,
) -> SpeakerHRIRBank:
    """Look up one HRIR per speaker for a fixed head → a :class:`SpeakerHRIRBank`.

    Projects each speaker in ``layout`` to its head-relative ``(az, el)`` (the
    § 6 ``Rᵀ(pᵢ − p_h)`` projection, defaulting to the neutral head) and fetches
    that direction from ``hrir_set`` — ITD-aware interpolated across the ``k``
    nearest measurements when ``interpolate`` (default), else the single nearest
    measured IR. ``head_position``/``head_rotation`` are forwarded to the layout
    projection: the neutral defaults give the static scene; a tracker-supplied
    pose (M3) gives the world-locked one.

    The HRIR set should already be at the engine rate (call
    :meth:`HRIRSet.resampled` first); the bank inherits ``hrir_set.samplerate``.
    """
    directions = layout.head_relative_directions(
        head_position=head_position, head_rotation=head_rotation
    )  # (C, 2)
    n_taps = hrir_set.n_taps
    c = layout.n_channels
    left = np.zeros((c, n_taps))
    right = np.zeros((c, n_taps))
    for i in range(c):
        az, el = float(directions[i, 0]), float(directions[i, 1])
        if interpolate:
            lir, rir = hrir_set.interpolated_irs(az, el, k=k, power=power)
        else:
            lir, rir = hrir_set.nearest_irs(az, el)
        left[i] = lir
        right[i] = rir
    return SpeakerHRIRBank(
        channels=layout.channels,
        left=left,
        right=right,
        samplerate=hrir_set.samplerate,
        directions=directions,
    )


def render_layout_to_stereo(
    pcm: np.ndarray,
    hrir_set: HRIRSet,
    layout: WorldLayout,
    *,
    interpolate: bool = True,
    k: int = 3,
    power: float = 2.0,
    head_position: np.ndarray | None = None,
    head_rotation: np.ndarray | None = None,
) -> np.ndarray:
    """One-shot offline render: ``(M, C)`` PCM → ``(M + N - 1, 2)`` stereo.

    Convenience over :func:`build_speaker_hrirs` + :meth:`SpeakerHRIRBank.render`
    for a single block. Build the bank once and reuse it when rendering many
    blocks at the same (fixed) head pose.
    """
    bank = build_speaker_hrirs(
        hrir_set,
        layout,
        interpolate=interpolate,
        k=k,
        power=power,
        head_position=head_position,
        head_rotation=head_rotation,
    )
    return bank.render(pcm)

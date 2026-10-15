"""HRIR loading from SOFA files (B1.1) — the front of the static render engine.

A SOFA file (Spatially Oriented Format for Acoustics) is an HDF5 container; the
**SimpleFreeFieldHRIR** convention stores, per measurement, a left/right impulse
response and the source direction. This module reads one with ``h5py`` (mirroring
the sibling ``binaural-localization`` project, which parses SOFA the same way),
exposes the directions + per-ear IRs, and resamples them to the engine rate.

Only numpy/scipy/h5py are used, so this runs headless on WSL/CI with no audio
device. The angular-nearest and resampling math are pure functions, unit-tested
without any SOFA file; ``load_sofa`` is exercised against a synthetic SOFA the
tests generate (no large binary in git; real sets are fetched per
``docs/hrir-setup.md`` and are ``.gitignore``d).

Conventions (SimpleFreeFieldHRIR):

* ``Data.IR`` has shape ``(M, 2, N)`` — M measurements, 2 receivers (0 = left
  ear, 1 = right ear), N taps.
* ``SourcePosition`` is ``(M, 3)`` spherical ``(azimuth, elevation, radius)`` in
  degrees/degrees/metres; azimuth is counter-clockwise from front (0° = front,
  90° = left), which fixes the ipsi/contra mapping below. We normalise azimuth to
  ``[0, 360)``. A ``cartesian`` ``SourcePosition.Type`` is converted to spherical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import resample_poly


def angular_distance(az1: float, el1: float, az2: float, el2: float) -> float:
    """Great-circle angle (degrees) between two (azimuth, elevation) directions.

    Elevation is latitude-like; azimuth is longitude-like. Using the spherical
    law of cosines on unit vectors keeps the "nearest direction" lookup correct
    across the azimuth wrap (359° is 2° from 1°, not 358°).
    """
    a1, e1, a2, e2 = map(math.radians, (az1, el1, az2, el2))
    cos_d = math.sin(e1) * math.sin(e2) + math.cos(e1) * math.cos(e2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_d))))


def nearest_index(directions: np.ndarray, az: float, el: float) -> int:
    """Index of the row in ``directions`` (M×2 az/el deg) nearest to ``(az, el)``.

    Vectorised great-circle distance; ties resolve to the lowest index.
    """
    d = np.asarray(directions, dtype=np.float64)
    if d.ndim != 2 or d.shape[1] != 2 or d.shape[0] == 0:
        raise ValueError("directions must be a non-empty (M, 2) array")
    a, e = np.radians(d[:, 0]), np.radians(d[:, 1])
    a0, e0 = math.radians(az), math.radians(el)
    cos_d = np.sin(e) * math.sin(e0) + np.cos(e) * math.cos(e0) * np.cos(a - a0)
    return int(np.argmax(np.clip(cos_d, -1.0, 1.0)))


def resample_irs(irs: np.ndarray, src_rate: float, dst_rate: float) -> np.ndarray:
    """Resample impulse responses along the last axis from ``src`` to ``dst`` rate.

    Uses polyphase filtering (``scipy.signal.resample_poly``) with the up/down
    factors reduced by their GCD — high quality and exact-ratio, unlike FFT
    resampling which assumes periodicity. A no-op when the rates already match.
    """
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("sample rates must be positive")
    if src_rate == dst_rate:
        return np.asarray(irs, dtype=np.float64)
    g = math.gcd(int(round(src_rate)), int(round(dst_rate)))
    up = int(round(dst_rate)) // g
    down = int(round(src_rate)) // g
    return resample_poly(np.asarray(irs, dtype=np.float64), up, down, axis=-1)


def _to_spherical(pos: np.ndarray, pos_type: str) -> np.ndarray:
    """Return (M, 3) spherical (az deg [0,360), el deg, radius) from SourcePosition."""
    pos = np.asarray(pos, dtype=np.float64)
    if pos_type.lower().startswith("cart"):
        x, y, z = pos[:, 0], pos[:, 1], pos[:, 2]
        radius = np.sqrt(x**2 + y**2 + z**2)
        az = np.degrees(np.arctan2(y, x))
        el = np.degrees(np.arcsin(np.divide(z, radius, out=np.zeros_like(z), where=radius > 0)))
        out = np.column_stack([az, el, radius])
    else:
        out = pos.copy()
    out[:, 0] = np.mod(out[:, 0], 360.0)
    return out


@dataclass(frozen=True)
class HRIRSet:
    """An HRIR set: per-measurement directions and left/right impulse responses."""

    directions: np.ndarray  # (M, 2) azimuth [0,360), elevation (deg)
    left: np.ndarray  # (M, N) left-ear IRs
    right: np.ndarray  # (M, N) right-ear IRs
    samplerate: int

    def __post_init__(self) -> None:
        m, n = self.left.shape
        if self.right.shape != (m, n) or self.directions.shape != (m, 2):
            raise ValueError("directions/left/right shapes are inconsistent")

    @property
    def n_measurements(self) -> int:
        return int(self.left.shape[0])

    @property
    def n_taps(self) -> int:
        return int(self.left.shape[1])

    def nearest(self, az: float, el: float) -> int:
        return nearest_index(self.directions, az, el)

    def nearest_irs(self, az: float, el: float) -> tuple[np.ndarray, np.ndarray]:
        """Nearest-direction ``(left_ir, right_ir)`` (B1.2 adds interpolation)."""
        i = self.nearest(az, el)
        return self.left[i], self.right[i]

    def ipsi_contra(self, az: float, el: float) -> tuple[np.ndarray, np.ndarray]:
        """Nearest ``(ipsilateral, contralateral)`` IRs for a source at ``(az, el)``.

        With azimuth counter-clockwise from front, a source at 0–180° is on the
        **left**, so the left ear is ipsilateral; 180–360° is on the right. On the
        median plane (0/180°) the set is symmetric and we return (left, right).
        """
        left_ir, right_ir = self.nearest_irs(az, el)
        # Source on the right half (180–360°, exclusive) → right ear ipsilateral;
        # otherwise (left half or median plane) → left ear ipsilateral.
        on_right = 180.0 < (az % 360.0) < 360.0
        return (right_ir, left_ir) if on_right else (left_ir, right_ir)

    def resampled(self, target_rate: int) -> HRIRSet:
        """Return a copy with IRs resampled to ``target_rate`` (no-op if equal)."""
        if target_rate == self.samplerate:
            return self
        return HRIRSet(
            directions=self.directions,
            left=resample_irs(self.left, self.samplerate, target_rate),
            right=resample_irs(self.right, self.samplerate, target_rate),
            samplerate=int(target_rate),
        )


def load_sofa(path, target_rate: int | None = None) -> HRIRSet:
    """Load a SimpleFreeFieldHRIR SOFA file into an :class:`HRIRSet`.

    ``target_rate`` resamples to the engine rate (e.g. 48000) when given. Raises
    ``ValueError`` if the file is not a 2-receiver HRIR set.
    """
    import h5py  # imported here so the pure helpers need no h5py on CI

    with h5py.File(path, "r") as f:
        ir = np.asarray(f["Data.IR"], dtype=np.float64)  # (M, 2, N)
        source = np.asarray(f["SourcePosition"], dtype=np.float64)  # (M, 3)
        sr = float(np.asarray(f["Data.SamplingRate"]).ravel()[0])
        pos_type = ""
        if "SourcePosition" in f:
            pos_type = str(f["SourcePosition"].attrs.get("Type", b"spherical"))

    if ir.ndim != 3 or ir.shape[1] != 2:
        raise ValueError(f"expected Data.IR of shape (M, 2, N), got {ir.shape}")
    if source.shape != (ir.shape[0], 3):
        raise ValueError(f"SourcePosition {source.shape} does not match {ir.shape[0]} measurements")

    directions = _to_spherical(source, pos_type)[:, :2]
    hrir = HRIRSet(
        directions=directions,
        left=np.ascontiguousarray(ir[:, 0, :]),
        right=np.ascontiguousarray(ir[:, 1, :]),
        samplerate=int(round(sr)),
    )
    return hrir.resampled(target_rate) if target_rate is not None else hrir

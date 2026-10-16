"""World layout model (B1.4) — screen-relative speaker positions → per-channel (az, el).

This is the geometry half of the render engine (requirements § 5–§ 6): it places
the virtual speakers at fixed 3-D positions in a **world frame anchored on the
listener's neutral head pose**, then projects each speaker into the head frame to
get the ``(azimuth, elevation)`` the HRIR lookup needs. For B1.4 the head is
*fixed* at the neutral pose, so the projection uses an identity rotation and a
zero head position — but the full world-lock formula

    d_i = Rᵀ · (p_i − p_h)        (requirements § 6)

is implemented here, with ``R`` (world←head rotation) and ``p_h`` (head position)
as optional arguments defaulting to the neutral head. That is the seam M3/B3.1
plugs the tracker into: once the tracker yields a head rotation matrix, the same
call yields the head-relative directions that make the scene world-locked. B1.4
deliberately does **not** fix the yaw/pitch/roll→matrix mapping (sign/axis and the
neutral pose are a calibration concern, left to B2.2/B3.1, see ``head_pose.py``).

Coordinate convention — a right-handed world frame on the neutral head:

* ``+x`` = forward, toward the screen (the FC / "front" direction);
* ``+y`` = left;
* ``+z`` = up.

Then ``azimuth = atan2(y, x)`` degrees, counter-clockwise from front so ``+90°``
is the left ear, and ``elevation = asin(z / r)`` degrees, positive up. This is the
same azimuth convention as :mod:`binaural_headtracker.hrir` and the nominal
``SPEAKER_LAYOUT_7P1`` in :mod:`binaural_headtracker.surround` (signed azimuth,
front = 0°). Azimuth is returned signed in ``(-180, 180]`` to round-trip with that
layout; the HRIR lookup normalises to ``[0, 360)`` itself.

Distance and overall scale are user-configurable (FR-3). For a head at the world
origin the returned ``(az, el)`` are distance-invariant (direction only), but the
distances are load-bearing the moment the head **translates** (closer speakers
swing through a larger angle): the model keeps full 3-D positions, not just
angles, so translation and rotation both work without a rewrite.

Everything here is pure numpy, unit-tested headless with no device.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from binaural_headtracker.surround import CHANNELS_7P1, SPEAKER_LAYOUT_7P1

# Default nominal radius (metres) of the 7.1 ring around the neutral head.
DEFAULT_DISTANCE_M = 2.0


def direction_to_unit(az: float, el: float) -> np.ndarray:
    """Unit vector in the world frame for an ``(azimuth, elevation)`` in degrees.

    Inverse of :func:`unit_to_direction`. ``az`` is CCW from front (+90° = left),
    ``el`` positive up; the returned vector is ``(x=front, y=left, z=up)``.
    """
    a, e = math.radians(az), math.radians(el)
    ce = math.cos(e)
    return np.array([ce * math.cos(a), ce * math.sin(a), math.sin(e)], dtype=np.float64)


def unit_to_direction(vec: np.ndarray) -> np.ndarray:
    """Convert world-frame vector(s) to ``(azimuth, elevation)`` in degrees.

    Accepts a single ``(3,)`` vector (returns ``(2,)``) or a stack ``(..., 3)``
    (returns ``(..., 2)``). Azimuth is signed in ``(-180, 180]``, CCW from front;
    elevation in ``[-90, 90]``. Raises on a zero-length vector (direction
    undefined — e.g. a speaker coincident with the head).
    """
    v = np.asarray(vec, dtype=np.float64)
    if v.shape[-1] != 3:
        raise ValueError("expected vector(s) with last axis of length 3")
    x, y, z = v[..., 0], v[..., 1], v[..., 2]
    r = np.sqrt(x**2 + y**2 + z**2)
    if np.any(r < 1e-12):
        raise ValueError("zero-length direction vector (undefined azimuth/elevation)")
    az = np.degrees(np.arctan2(y, x))
    el = np.degrees(np.arcsin(np.clip(z / r, -1.0, 1.0)))
    return np.stack([az, el], axis=-1)


@dataclass(frozen=True)
class WorldLayout:
    """Fixed virtual-speaker positions in the neutral-head world frame.

    ``positions`` is ``(C, 3)`` metres in the ``(x=front, y=left, z=up)`` frame,
    one row per channel in ``channels`` order. Build the nominal 7.1 ring with
    :func:`layout_7p1`, or pass arbitrary positions for a custom layout (e.g. a
    GUI placing speakers at literal screen-edge coordinates).
    """

    channels: tuple[str, ...]
    positions: np.ndarray  # (C, 3) metres

    def __post_init__(self) -> None:
        pos = np.asarray(self.positions, dtype=np.float64)
        if pos.ndim != 2 or pos.shape[1] != 3:
            raise ValueError(f"positions must be (C, 3); got {pos.shape}")
        if pos.shape[0] != len(self.channels):
            raise ValueError("positions must have one row per channel")
        object.__setattr__(self, "positions", pos)

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def distances(self) -> np.ndarray:
        """Per-channel distance (metres) from the world origin (neutral head)."""
        return np.linalg.norm(self.positions, axis=1)

    def index(self, channel: str) -> int:
        return self.channels.index(channel)

    def scaled(self, factor: float) -> WorldLayout:
        """Return a copy with every speaker position scaled about the origin.

        Scales distances without touching directions, so a fixed head's ``(az,
        el)`` are unchanged — the overall-scale knob of FR-3.
        """
        if factor <= 0:
            raise ValueError("scale factor must be positive")
        return WorldLayout(channels=self.channels, positions=self.positions * float(factor))

    def head_relative_directions(
        self,
        head_position: np.ndarray | None = None,
        head_rotation: np.ndarray | None = None,
    ) -> np.ndarray:
        """Per-channel ``(az, el)`` (degrees) in the head frame — the § 6 projection.

        ``head_position`` is the head's world position (default origin);
        ``head_rotation`` is the ``3×3`` world←head rotation ``R`` (default
        identity = neutral pose). Returns ``(C, 2)`` in ``channels`` order, with
        the same az/el convention as :func:`unit_to_direction`.

        Implements ``d_i = Rᵀ · (p_i − p_h)``: with the speaker positions stacked
        as rows, ``(Rᵀ · v)`` for all rows is ``(p − p_h) @ R``.
        """
        rel = self.positions.copy()
        if head_position is not None:
            hp = np.asarray(head_position, dtype=np.float64)
            if hp.shape != (3,):
                raise ValueError("head_position must be a (3,) vector")
            rel = rel - hp
        if head_rotation is not None:
            rot = np.asarray(head_rotation, dtype=np.float64)
            if rot.shape != (3, 3):
                raise ValueError("head_rotation must be a (3, 3) matrix")
            rel = rel @ rot  # rows of (Rᵀ · v) == v @ R
        return unit_to_direction(rel)


def layout_7p1(
    distance: float = DEFAULT_DISTANCE_M,
    scale: float = 1.0,
    channels: tuple[str, ...] = CHANNELS_7P1,
    distances: dict[str, float] | None = None,
    elevations: dict[str, float] | None = None,
) -> WorldLayout:
    """Build the nominal ITU/SMPTE 7.1 ring as a :class:`WorldLayout`.

    Speakers sit at the nominal azimuths of
    :data:`binaural_headtracker.surround.SPEAKER_LAYOUT_7P1` (L 30°, R −30°, C/LFE
    0°, SL/SR ±90°, RL/RR ±135°), on the horizon by default, at a common
    ``distance`` metres scaled by ``scale``. ``distances`` overrides the radius of
    named channels (metres, pre-scale); ``elevations`` overrides their elevation
    (degrees). For a head at the origin this reproduces exactly those nominal
    angles — the fixed-head case B1.4 verifies — while the per-channel distances
    make head translation (M3) behave correctly.
    """
    if distance <= 0:
        raise ValueError("distance must be positive")
    distances = distances or {}
    elevations = elevations or {}
    rows = []
    for ch in channels:
        az = SPEAKER_LAYOUT_7P1[ch][0]
        el = elevations.get(ch, SPEAKER_LAYOUT_7P1[ch][1])
        r = distances.get(ch, distance) * scale
        if r <= 0:
            raise ValueError(f"distance for channel {ch} must be positive")
        rows.append(direction_to_unit(az, el) * r)
    return WorldLayout(channels=tuple(channels), positions=np.array(rows, dtype=np.float64))

"""World-locked streaming renderer: a single source, head-tracked, click-free (B3.1).

This is the core of the world-lock effect. A single dry mono source (the owner's
caisse-claire loop, in the live test) is placed at a **world-fixed** direction; as
the head turns, the source's *head-relative* azimuth changes, and we render it
through the **nearest of the owner's 8 baked BRIRs** (the ``nearest`` decision from
journal 18 — no spectral synthesis, which flips front/back). When the nearest BRIR
changes we do **not** hard-swap the filter (that clicks and the long room tail jumps);
instead each BRIR is a continuous convolution *voice* and we crossfade the source
**into** the new voice over a few ms. The retired voice keeps running on its decaying
room tail until drained, so the transition is seamless.

The world-lock geometry stays in the already-tested layer: the caller turns a head
pose into the source's head-relative azimuth via
:func:`binaural_headtracker.calibrate.directions_for_pose` on a one-speaker layout
(see :func:`source_layout`) and passes just that azimuth here. This module owns only
the voice pool and the crossfade, so it is pure numpy and unit-tested headless; the
camera + ``sounddevice`` live path lives in ``apps/live_tracker_audio.py`` (host-only).

The same voice pool generalises to the full 7.1 world-locked passthrough later (one
source per input channel sharing the ring); B3.1 only needs the single source that
reproduces the rotating-snare demo as a *live* effect.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from binaural_headtracker.convolve import StereoConvolver
from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1, BakedBRIRSet
from binaural_headtracker.world import WorldLayout

# The directional ring we can steer a source across: the 7 placed speakers of the
# 7.1 layout (LFE is non-directional — same nominal azimuth as C — so it is never a
# steering target and is excluded from nearest-BRIR selection).
RING_CHANNELS: tuple[str, ...] = ("L", "R", "C", "SL", "SR", "RL", "RR")


def source_layout(world_az_deg: float = 0.0, distance: float = 2.0) -> WorldLayout:
    """A one-"speaker" :class:`WorldLayout` holding a single world-fixed source.

    The source sits at azimuth ``world_az_deg`` (0° = straight ahead of the screen),
    elevation 0, at ``distance`` metres. Feed it to
    :func:`binaural_headtracker.calibrate.directions_for_pose` and read element 0's
    azimuth to get the source's head-relative azimuth for :meth:`WorldLockedSource.
    process_block`. Keeping the source as a one-row layout means the world-lock math
    is exactly the tested speaker path (``Rᵀ(p_src − p_head)``), not a second copy.
    """
    import math

    a = math.radians(world_az_deg)
    pos = np.array([[distance * math.cos(a), distance * math.sin(a), 0.0]], dtype=np.float64)
    return WorldLayout(channels=("SRC",), positions=pos)


def _wrapped_deg(delta: float) -> float:
    """Signed smallest angle (deg) for ``delta`` wrapped to ``(-180, 180]``."""
    return (delta + 180.0) % 360.0 - 180.0


def ring_azimuths(brir: BakedBRIRSet) -> dict[int, float]:
    """Map each steerable BRIR's channel index → its nominal azimuth (deg).

    Only the directional ring channels present in ``brir`` (see :data:`RING_CHANNELS`)
    are included, so LFE never becomes a steering target.
    """
    out: dict[int, float] = {}
    for ch in RING_CHANNELS:
        if ch in brir.channels:
            out[brir.channels.index(ch)] = SPEAKER_LAYOUT_7P1[ch][0]
    if not out:
        raise ValueError("BRIR set has none of the directional ring channels")
    return out


def nearest_brir_index(azimuths: dict[int, float], source_az_deg: float) -> int:
    """Channel index of the ring BRIR whose azimuth is closest to ``source_az_deg``.

    Distance is the wrapped angular distance, so e.g. a source at −170° is nearer the
    135°-side rear (RR at −135°, 35° away) than the 135° rear (55° away) only if that
    is in fact closer — the wrap is handled. An exact tie resolves to whichever channel
    comes first in :data:`RING_CHANNELS` order (the order ``ring_azimuths`` built the
    dict), e.g. SL over RL at 112.5°; exact float ties don't arise from live tracking.
    """
    best_i, best_d = None, None
    for i, az in azimuths.items():
        d = abs(_wrapped_deg(source_az_deg - az))
        if best_d is None or d < best_d - 1e-9:
            best_i, best_d = i, d
    assert best_i is not None
    return best_i


@dataclass
class _Voice:
    conv: StereoConvolver
    gain: float            # current input gain at the end of the last block
    silent_blocks: int     # consecutive fully-drained (zero-in, zero-gain) blocks


class WorldLockedSource:
    """Render one world-fixed mono source to stereo, steered head-relative, click-free.

    Each directional BRIR is a persistent :class:`StereoConvolver` voice. Per block the
    source is routed (via a per-sample gain ramp) into the voice for the *nearest* BRIR
    to the current head-relative azimuth; when the nearest changes, the gain crossfades
    from the old voice to the new one over ``fade_ms``. Voices whose gain has reached 0
    keep convolving the trailing zeros so their room tail rings out naturally, and are
    reset once drained (``tail_blocks`` of silence), so steady state runs a single voice.
    """

    def __init__(
        self,
        brir: BakedBRIRSet,
        *,
        block_size: int = 256,
        fade_ms: float = 20.0,
    ) -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        self._brir = brir
        self._block = int(block_size)
        self._azimuths = ring_azimuths(brir)
        self._fade_samples = max(1, int(round(fade_ms * 1e-3 * brir.samplerate)))
        # One voice per directional channel (built lazily to keep steady state cheap).
        self._voices: dict[int, _Voice] = {}
        self._active: int | None = None  # the index currently being faded toward gain 1

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def samplerate(self) -> int:
        return self._brir.samplerate

    @property
    def tail_blocks(self) -> int:
        return self._voice_for(next(iter(self._azimuths))).conv.tail_blocks

    def _voice_for(self, idx: int) -> _Voice:
        v = self._voices.get(idx)
        if v is None:
            left = self._brir.left[idx : idx + 1]
            right = self._brir.right[idx : idx + 1]
            v = _Voice(StereoConvolver(left, right, self._block), gain=0.0, silent_blocks=0)
            self._voices[idx] = v
        return v

    def _gain_ramp(self, start: float, end: float) -> np.ndarray:
        """Per-sample gain across one block ramping ``start``→``end`` over ``fade_samples``.

        The ramp is linear and clamped to ``[0, 1]``; a ramp shorter than a block
        reaches ``end`` partway and holds it, so a fast head turn still crossfades over
        a fixed, click-free time rather than switching within a single sample.
        """
        step = (1.0 if end >= start else -1.0) / self._fade_samples
        ramp = start + step * np.arange(1, self._block + 1, dtype=np.float64)
        lo, hi = (start, end) if end >= start else (end, start)
        return np.clip(ramp, lo, hi)

    def process_block(self, mono_block, source_az_deg: float) -> np.ndarray:
        """Render one mono block at the given head-relative source azimuth → stereo.

        ``mono_block`` is ``(block_size,)``. Returns ``(block_size, 2)``. Call once per
        audio block with the source azimuth for that block (from the head pose via
        :func:`source_layout` + ``directions_for_pose``).
        """
        x = np.asarray(mono_block, dtype=np.float64).reshape(-1)
        if x.size != self._block:
            raise ValueError(f"mono_block must be ({self._block},); got {x.shape}")

        target = nearest_brir_index(self._azimuths, source_az_deg)
        if self._active is None:
            # First block: the source is already at its position, so start the voice at
            # full gain (a fade-in from silence here would make startup differ from a
            # plain convolution for no perceptual gain — the onset is the source itself).
            self._active = target
            self._voice_for(target).gain = 1.0
        elif target != self._active:
            self._active = target  # retarget; gains ramp toward the new active voice

        out = np.zeros((self._block, 2))
        # Every voice with any input or residual tail must run this block.
        for idx in list(self._voices.keys()) + ([target] if target not in self._voices else []):
            v = self._voice_for(idx)
            end_gain = 1.0 if idx == self._active else 0.0
            g = self._gain_ramp(v.gain, end_gain)
            fed = x * g
            v.gain = float(g[-1])
            if np.any(fed) or v.silent_blocks < v.conv.tail_blocks:
                out += v.conv.process_block(fed.reshape(-1, 1))
                v.silent_blocks = 0 if np.any(fed) else v.silent_blocks + 1
            # else: fully drained and silent — skip, leave at rest.
        self._reap()
        return out

    def _reap(self) -> None:
        """Drop voices that are at zero gain and have fully drained their tail."""
        for idx in list(self._voices):
            if idx == self._active:
                continue
            v = self._voices[idx]
            if v.gain <= 0.0 and v.silent_blocks >= v.conv.tail_blocks:
                del self._voices[idx]

    def flush(self) -> np.ndarray:
        """Flush every live voice's remaining tail → ``(tail_blocks * block_size, 2)``."""
        n = self.tail_blocks * self._block
        out = np.zeros((n, 2))
        for v in self._voices.values():
            t = v.conv.flush()
            out[: t.shape[0]] += t
        return out


def render_world_locked(
    mono,
    source_az_track,
    brir: BakedBRIRSet,
    *,
    block_size: int = 256,
    fade_ms: float = 20.0,
    flush: bool = True,
) -> np.ndarray:
    """Offline driver: stream a whole mono signal under a per-block azimuth track → stereo.

    ``mono`` is ``(M,)``; ``source_az_track`` is one head-relative azimuth (deg) **per
    block** (length ``ceil(M / block_size)``) — the pose track a live run would produce,
    resampled to blocks. Returns the world-locked stereo, with the trailing room tail
    appended when ``flush`` is set, so a recorded head motion renders to a reproducible
    ``.wav`` for A/B review (head-locked vs world-locked, B3.2).
    """
    x = np.asarray(mono, dtype=np.float64).reshape(-1)
    track = np.asarray(source_az_track, dtype=np.float64).reshape(-1)
    n_blocks = int(np.ceil(x.size / block_size)) if x.size else 0
    if track.size != n_blocks:
        raise ValueError(f"source_az_track must have {n_blocks} entries (one per block); "
                         f"got {track.size}")
    src = WorldLockedSource(brir, block_size=block_size, fade_ms=fade_ms)
    chunks: list[np.ndarray] = []
    for b in range(n_blocks):
        block = x[b * block_size : (b + 1) * block_size]
        if block.size < block_size:
            block = np.concatenate([block, np.zeros(block_size - block.size)])
        chunks.append(src.process_block(block, float(track[b])))
    if flush and n_blocks:
        chunks.append(src.flush())
    return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 2))

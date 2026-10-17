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


class WorldLocked7p1:
    """World-locked 7.1 → stereo: steer every input channel through its nearest BRIR (B4.1).

    The full-passthrough generalisation of :class:`WorldLockedSource`: the 8 input
    channels are the 8 **world-fixed** 7.1 speakers, and each directional channel gets its
    own steered voice pool (nearest BRIR + click-free crossfade) so that, as the head
    turns, the whole 7.1 field stays locked in the room. LFE is non-directional bass, so it
    is **not** steered — it is convolved through its own fixed BRIR and summed.

    This is what makes *any* 7.1 system audio (captured from the virtual device) head-locked
    to the world. The world-lock geometry stays in the tested layer: the caller passes the
    per-channel head-relative azimuths (``directions_for_pose`` on ``layout_7p1`` built in
    the BRIR's channel order), one per channel aligned to ``brir.channels``; this class owns
    only the per-channel voice pools and the sum.
    """

    def __init__(self, brir: BakedBRIRSet, *, block_size: int = 256, fade_ms: float = 20.0) -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        self._brir = brir
        self._block = int(block_size)
        self._dir_idx = [brir.channels.index(c) for c in RING_CHANNELS if c in brir.channels]
        if not self._dir_idx:
            raise ValueError("BRIR set has none of the directional ring channels")
        self._sources = {
            i: WorldLockedSource(brir, block_size=block_size, fade_ms=fade_ms)
            for i in self._dir_idx
        }
        self._lfe_idx = brir.channels.index("LFE") if "LFE" in brir.channels else None
        if self._lfe_idx is not None:
            li = self._lfe_idx
            self._lfe = StereoConvolver(brir.left[li : li + 1], brir.right[li : li + 1], block_size)

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def samplerate(self) -> int:
        return self._brir.samplerate

    def process_block(self, pcm, azimuths) -> np.ndarray:
        """Render one ``(block_size, C)`` 7.1 block → ``(block_size, 2)`` stereo.

        ``pcm`` columns are in ``brir.channels`` order; ``azimuths`` is one head-relative
        azimuth (deg) per channel, same order (the LFE entry is ignored). Call per block
        with the azimuths for the current head pose.
        """
        pcm = np.asarray(pcm, dtype=np.float64)
        c = self._brir.n_channels
        if pcm.ndim != 2 or pcm.shape != (self._block, c):
            raise ValueError(f"pcm must be ({self._block}, {c}); got {pcm.shape}")
        az = np.asarray(azimuths, dtype=np.float64).reshape(-1)
        if az.size != c:
            raise ValueError(f"azimuths must have {c} entries (one per channel); got {az.size}")
        out = np.zeros((self._block, 2))
        for i, src in self._sources.items():
            out += src.process_block(pcm[:, i], float(az[i]))
        if self._lfe_idx is not None:
            out += self._lfe.process_block(pcm[:, self._lfe_idx : self._lfe_idx + 1])
        return out

    def flush(self) -> np.ndarray:
        """Flush every channel's remaining tail → stereo (longest component's length)."""
        tails = [src.flush() for src in self._sources.values()]
        if self._lfe_idx is not None:
            tails.append(self._lfe.flush())
        n = max((t.shape[0] for t in tails), default=0)
        out = np.zeros((n, 2))
        for t in tails:
            out[: t.shape[0]] += t
        return out


def bus_targets(brir: BakedBRIRSet, azimuths) -> np.ndarray:
    """Per-input-channel target **bus** (= the BRIR index its signal routes through).

    For B4.2's bus formulation of the 7.1 world-lock: directional channels route to the
    nearest ring BRIR for their current head-relative azimuth (``nearest_brir_index``);
    LFE routes to its own fixed BRIR (non-directional). ``azimuths`` is one per channel,
    in ``brir.channels`` order (the LFE entry is ignored). Returns an int array of length
    ``brir.n_channels`` whose entries are BRIR channel indices.
    """
    az = np.asarray(azimuths, dtype=np.float64).reshape(-1)
    ring = ring_azimuths(brir)
    lfe = brir.channels.index("LFE") if "LFE" in brir.channels else None
    out = np.empty(brir.n_channels, dtype=np.intp)
    for i in range(brir.n_channels):
        out[i] = i if i == lfe else nearest_brir_index(ring, float(az[i]))
    return out


class BusMixer:
    """Route + click-free crossfade the ``C`` input channels into ``C`` BRIR **buses**.

    The world-lock as a time-varying mix: ``bus_in[b,n] = Σ_c g_{c,b}[n] · pcm[n,c]``,
    where each channel ``c``'s gain ramps toward a one-hot on its current target bus
    (``bus_targets``) over ``fade_samples`` when the target changes. Because convolution is
    linear, convolving each bus with its fixed BRIR and summing is **identical** to the
    per-channel :class:`WorldLockedSource` voice pool — but it is only ``C`` fixed-filter
    convolutions, which is what the GPU batches. Pure numpy; the first block starts at full
    gain (so startup equals the static render). Unit-tested; the GPU conv is the only
    host-only piece (see :mod:`binaural_headtracker.gpu_render`).
    """

    def __init__(self, n_channels: int, *, fade_samples: int) -> None:
        self._c = int(n_channels)
        self._fade = max(1, int(fade_samples))
        self._gain = np.zeros((self._c, self._c))  # gain[c, b]: channel c -> bus b
        self._started = False

    @property
    def n_bus(self) -> int:
        return self._c

    def mix(self, pcm, targets) -> np.ndarray:
        """``pcm`` ``(block, C)`` + target bus per channel → ``bus_in`` ``(C, block)``."""
        pcm = np.asarray(pcm, dtype=np.float64)
        block = pcm.shape[0]
        targets = np.asarray(targets, dtype=np.intp).reshape(-1)
        desired = np.zeros((self._c, self._c))
        desired[np.arange(self._c), targets] = 1.0
        if not self._started:
            self._gain = desired.copy()  # start at full gain, no fade-in from silence
            self._started = True
        start = self._gain  # (C, C)
        step = np.where(desired >= start, 1.0, -1.0) / self._fade
        ramp = start[:, :, None] + step[:, :, None] * np.arange(1, block + 1)  # (C,C,block)
        lo = np.minimum(start, desired)[:, :, None]
        hi = np.maximum(start, desired)[:, :, None]
        ramp = np.clip(ramp, lo, hi)
        self._gain = ramp[:, :, -1]
        # bus_in[b,n] = Σ_c ramp[c,b,n] * pcm[n,c]
        return np.einsum("cbn,nc->bn", ramp, pcm)

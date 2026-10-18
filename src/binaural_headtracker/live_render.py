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

import math
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


def interpolated_ring(
    brir: BakedBRIRSet, method: str = "nearest", *, step_deg: float = 10.0,
    loudness_match: bool = True,
) -> tuple[BakedBRIRSet, dict[int, float]]:
    """A **dense** directional BRIR set + its azimuths, one BRIR every ``step_deg`` around the
    ring, synthesized with ``baked_interp.interpolate_baked_brir`` under ``method``.

    Feed the pair to :class:`WorldLockedSource` (``azimuths=`` override) to steer a source over
    a fine grid instead of the 8 measured speakers — the basis for comparing interpolation
    methods live (B3.3). ``"nearest"`` snaps each grid point to its nearest real BRIR (so it is
    the measured ring, just sampled denser, with no synthesis); ``"timealign"`` / ``"minphase"``
    / ``"notchwarp"`` synthesize the in-between directions (journal 18's methods). Synthesizing
    the whole grid is the one-off cost paid when a method is selected; the voice pool then builds
    convolvers lazily and reuses them.

    ``loudness_match`` (default on, B1.13) corrects the synthesis methods' low-frequency level
    ripple — without it, timealign's in-between directions carry ~2–3 dB more bass than the
    measured speaker directions, so the surround sounds bass-light when the head faces a speaker
    and swells when it turns off-axis. It rescales each synthesized BRIR's smoothed envelope to
    the measured endpoints equally on both ears (ITD/ILD, so localisation, unchanged). No effect
    on ``"nearest"`` (returns measured BRIRs).
    """
    from binaural_headtracker.baked_interp import interpolate_baked_brir

    if step_deg <= 0:
        raise ValueError("step_deg must be positive")
    azs = np.arange(0.0, 360.0, float(step_deg))
    n = brir.n_taps
    left = np.zeros((azs.size, n))
    right = np.zeros((azs.size, n))
    for i, az in enumerate(azs):
        lft, rgt = interpolate_baked_brir(brir, float(az), method=method,
                                          loudness_match=loudness_match)
        lft, rgt = np.asarray(lft), np.asarray(rgt)
        left[i, : min(lft.size, n)] = lft[:n]
        right[i, : min(rgt.size, n)] = rgt[:n]
    channels = tuple(f"A{int(round(a)) % 360:03d}" for a in azs)
    dense = BakedBRIRSet(channels=channels, left=left, right=right, samplerate=brir.samplerate)
    azimuths = {i: _wrapped_deg(float(a)) for i, a in enumerate(azs)}
    return dense, azimuths


@dataclass
class _Voice:
    conv: StereoConvolver
    gain: float            # current input gain at the end of the last block
    silent_blocks: int     # consecutive zero-input blocks since this voice went silent


class WorldLockedSource:
    """Render one world-fixed mono source to stereo, steered head-relative, click-free.

    Each directional BRIR is a persistent :class:`StereoConvolver` voice. Per block the
    source is routed (via a per-sample gain ramp) into the voice for the *nearest* BRIR
    to the current head-relative azimuth; when the nearest changes, the gain crossfades
    from the old voice to the new one over ``fade_ms``. Voices whose gain has reached 0
    keep convolving the trailing zeros so their room tail rings out, and are dropped once
    drained, so steady state runs a single voice.

    ``max_tail_ms`` caps how long a *retired* (left-behind) voice keeps ringing: its output
    is faded to silence over that window and the voice is then dropped, instead of ringing
    the BRIR's full tail (often ~0.5 s). This bounds the number of concurrent voices when
    the source **sweeps** across a fine interpolation grid — otherwise every grid point
    touched holds a full-length convolution alive for the whole tail, which stacks up into
    dropouts during motion. The active voice always rings its full tail (it is never
    released while active), so a *stationary* source sounds identical to before.
    """

    def __init__(
        self,
        brir: BakedBRIRSet,
        *,
        block_size: int = 256,
        fade_ms: float = 20.0,
        max_tail_ms: float | None = None,
        azimuths: dict[int, float] | None = None,
    ) -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        self._brir = brir
        self._block = int(block_size)
        # ``azimuths`` overrides the default 7.1 ring: pass a channel-index → azimuth map to
        # steer over an arbitrary direction set, e.g. a dense interpolated grid for comparing
        # interpolation methods (``interpolated_ring``). Defaults to the measured 7.1 ring.
        self._azimuths = azimuths if azimuths is not None else ring_azimuths(brir)
        self._fade_samples = max(1, int(round(fade_ms * 1e-3 * brir.samplerate)))
        # A retired voice rings for at most this many blocks, with an output fade-out over
        # the window, before it is dropped (see the class docstring). Bounds concurrent voices
        # during a sweep; the active voice is exempt so a still source is unchanged. ``None``
        # (the default) rings the full natural tail — the original behaviour, kept for the
        # nearest 7.1 voice pool and the offline parity reference; a dense grid steered by a
        # moving source caps it to stay real-time (set by the passthrough's test source).
        self._release_blocks = (
            None if max_tail_ms is None
            else max(1, math.ceil(max_tail_ms * 1e-3 * brir.samplerate / self._block)))
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
        # Every voice with any input or still-ringing tail must run this block.
        for idx in list(self._voices.keys()) + ([target] if target not in self._voices else []):
            v = self._voice_for(idx)
            end_gain = 1.0 if idx == self._active else 0.0
            g = self._gain_ramp(v.gain, end_gain)
            fed = x * g
            v.gain = float(g[-1])
            has_input = bool(np.any(fed))
            if has_input:
                v.silent_blocks = 0
            else:
                v.silent_blocks += 1
            # The active voice rings its full natural tail; a retired (silent, inactive) voice
            # rings for at most ``_release_blocks`` and is faded out over that window so cutting
            # the tail short stays click-free. This caps concurrent voices during a sweep.
            # ``_release_blocks is None`` ⇒ no cap: every voice rings its full tail (old path).
            capped = (idx != self._active and self._release_blocks is not None
                      and self._release_blocks < v.conv.tail_blocks)
            cap = self._release_blocks if capped else v.conv.tail_blocks
            if has_input or v.silent_blocks <= cap:
                block_out = v.conv.process_block(fed.reshape(-1, 1))
                if capped and not has_input:
                    # linear output fade across the whole release window: amplitude
                    # (1 - (k-1)/R) → (1 - k/R) over block k (1-based), reaching 0 at k=R.
                    k, R = v.silent_blocks, self._release_blocks
                    env = np.linspace(1.0 - (k - 1) / R, 1.0 - k / R, self._block,
                                      dtype=np.float64)
                    block_out = block_out * env[:, None]
                out += block_out
            # else: past the release window (or fully drained) — skip, reap below.
        self._reap()
        return out

    def _reap(self) -> None:
        """Drop retired voices that have finished ringing (full tail, or capped release)."""
        for idx in list(self._voices):
            if idx == self._active:
                continue
            v = self._voices[idx]
            cap = v.conv.tail_blocks
            if self._release_blocks is not None:
                cap = min(self._release_blocks, cap)
            if v.gain <= 0.0 and v.silent_blocks >= cap:
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
    turns, the whole 7.1 field stays locked in the room. LFE is non-directional bass, so it is
    **not** steered: ``lfe_mode="brir"`` convolves it through its own fixed BRIR and sums (the
    default, == the static reference); ``lfe_mode="flat"`` sums it flat to both ears at a
    level-matched gain, preserving the sub-bass the bass-light BRIRs would otherwise strip (B4.3).

    This is what makes *any* 7.1 system audio (captured from the virtual device) head-locked
    to the world. The world-lock geometry stays in the tested layer: the caller passes the
    per-channel head-relative azimuths (``directions_for_pose`` on ``layout_7p1`` built in
    the BRIR's channel order), one per channel aligned to ``brir.channels``; this class owns
    only the per-channel voice pools and the sum.
    """

    def __init__(self, brir: BakedBRIRSet, *, block_size: int = 256, fade_ms: float = 20.0,
                 lfe_mode: str = "brir") -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        if lfe_mode not in ("brir", "flat"):
            raise ValueError("lfe_mode must be 'brir' or 'flat'")
        self._brir = brir
        self._block = int(block_size)
        self._lfe_mode = lfe_mode
        self._dir_idx = [brir.channels.index(c) for c in RING_CHANNELS if c in brir.channels]
        if not self._dir_idx:
            raise ValueError("BRIR set has none of the directional ring channels")
        self._sources = {
            i: WorldLockedSource(brir, block_size=block_size, fade_ms=fade_ms)
            for i in self._dir_idx
        }
        self._lfe_idx = brir.channels.index("LFE") if "LFE" in brir.channels else None
        self._lfe = None
        self._lfe_gain = (0.0, 0.0)
        if self._lfe_idx is not None:
            li = self._lfe_idx
            if lfe_mode == "brir":
                # Steer-free: LFE through its own fixed BRIR (matches the static reference).
                self._lfe = StereoConvolver(
                    brir.left[li : li + 1], brir.right[li : li + 1], block_size)
            else:
                # Non-directional flat mix to both ears (B4.3): the baked BRIRs roll off the
                # sub-bass ~-20 dB, so convolving the pure-sub-bass LFE kills the bass. Sum it
                # flat instead, at a per-ear gain = that ear's LFE-IR L2 norm, which preserves
                # the loudness convolution would give (RMS_out ≈ RMS_in·‖ir‖) without the
                # spectral rolloff. (journal 24)
                self._lfe_gain = (float(np.linalg.norm(brir.left[li])),
                                  float(np.linalg.norm(brir.right[li])))

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
            lfe = pcm[:, self._lfe_idx]
            if self._lfe_mode == "brir":
                out += self._lfe.process_block(lfe[:, None])
            else:  # flat: non-directional sum to both ears, no convolution (no tail)
                out[:, 0] += lfe * self._lfe_gain[0]
                out[:, 1] += lfe * self._lfe_gain[1]
        return out

    def flush(self) -> np.ndarray:
        """Flush every channel's remaining tail → stereo (longest component's length).

        Flat-LFE adds no tail (it is a gain, not a convolution), so only the directional
        voices (and, in ``brir`` mode, the LFE convolver) ring out here.
        """
        tails = [src.flush() for src in self._sources.values()]
        if self._lfe is not None:
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

    def __init__(self, n_channels: int, n_buses: int | None = None, *,
                 fade_samples: int) -> None:
        self._n_in = int(n_channels)
        self._n_bus = int(n_buses) if n_buses is not None else self._n_in
        self._fade = max(1, int(fade_samples))
        self._gain = np.zeros((self._n_in, self._n_bus))  # gain[c, b]: channel c -> bus b
        self._started = False

    @property
    def n_bus(self) -> int:
        return self._n_bus

    def mix(self, pcm, targets) -> np.ndarray:
        """``pcm`` ``(block, n_in)`` + target bus per channel → ``bus_in`` ``(n_bus, block)``.

        ``n_bus`` may exceed ``n_in`` — e.g. the 7 directional channels mixed into a dense
        interpolated grid of buses (B4.4), so every channel steers through a synthesized
        in-between BRIR, not just the 8 measured ones.
        """
        pcm = np.asarray(pcm, dtype=np.float64)
        block = pcm.shape[0]
        targets = np.asarray(targets, dtype=np.intp).reshape(-1)
        desired = np.zeros((self._n_in, self._n_bus))
        desired[np.arange(self._n_in), targets] = 1.0
        if not self._started:
            self._gain = desired.copy()  # start at full gain, no fade-in from silence
            self._started = True
        start = self._gain  # (n_in, n_bus)
        step = np.where(desired >= start, 1.0, -1.0) / self._fade
        ramp = start[:, :, None] + step[:, :, None] * np.arange(1, block + 1)  # (n_in,n_bus,block)
        lo = np.minimum(start, desired)[:, :, None]
        hi = np.maximum(start, desired)[:, :, None]
        ramp = np.clip(ramp, lo, hi)
        self._gain = ramp[:, :, -1]
        # bus_in[b,n] = Σ_c ramp[c,b,n] * pcm[n,c]
        return np.einsum("cbn,nc->bn", ramp, pcm)


class InterpWorldLocked7p1:
    """World-locked 7.1 → stereo using an interpolation **method** for every channel (B4.4).

    Where :class:`WorldLocked7p1` snaps each channel to the nearest of the 8 measured BRIRs,
    this steers the 7 directional channels through a **dense interpolated grid** — one BRIR
    every ``grid_step`` degrees, synthesized from the measured speakers via
    ``baked_interp.interpolate_baked_brir`` under ``method`` — so the *whole* surround field,
    not just a single test source, gets the chosen method. It is a bus mix (7 inputs → the grid
    buses) + one batched fixed-filter convolution, identical to a per-channel voice pool over
    the grid (proven), so it runs on the native kernel. LFE is non-directional and handled as in
    :class:`WorldLocked7p1` (``brir`` convolves its own fixed BRIR, ``flat`` sums it level-matched).

    ``method='nearest'`` works but the 8-bus ``WorldLocked7p1`` / ``RustWorldLocked7p1`` is cheaper
    for it; use this for the synthesis methods. ``use_rust`` runs the grid convolution on the
    native kernel (``binaural_kernel``); otherwise a numpy :class:`StereoConvolver` (for tests/CPU).
    """

    def __init__(self, brir: BakedBRIRSet, method: str = "timealign", *, block_size: int = 256,
                 fade_ms: float = 20.0, grid_step: float = 10.0, lfe_mode: str = "brir",
                 use_rust: bool = False) -> None:
        if lfe_mode not in ("brir", "flat"):
            raise ValueError("lfe_mode must be 'brir' or 'flat'")
        self._brir = brir
        self._block = int(block_size)
        self._lfe_mode = lfe_mode
        self._method = str(method)
        self._grid_step = float(grid_step)
        self._dense, az = interpolated_ring(brir, method, step_deg=grid_step)
        self._grid_az = np.array([az[i] for i in range(self._dense.n_channels)], dtype=np.float64)
        self._dir_idx = [brir.channels.index(c) for c in RING_CHANNELS if c in brir.channels]
        if not self._dir_idx:
            raise ValueError("BRIR set has none of the directional ring channels")
        fade_samples = max(1, round(fade_ms * 1e-3 * brir.samplerate))
        self._mixer = BusMixer(len(self._dir_idx), self._dense.n_channels,
                               fade_samples=fade_samples)
        if use_rust:
            import binaural_kernel
            self._conv = binaural_kernel.BusConvolver(
                np.ascontiguousarray(self._dense.left, dtype=np.float64),
                np.ascontiguousarray(self._dense.right, dtype=np.float64), self._block)
            self._rust = True
        else:
            self._conv = StereoConvolver(self._dense.left, self._dense.right, self._block)
            self._rust = False
        self._lfe_idx = brir.channels.index("LFE") if "LFE" in brir.channels else None
        self._lfe = None
        self._lfe_gain = (0.0, 0.0)
        if self._lfe_idx is not None:
            li = self._lfe_idx
            if lfe_mode == "brir":
                self._lfe = StereoConvolver(
                    brir.left[li : li + 1], brir.right[li : li + 1], self._block)
            else:
                self._lfe_gain = (float(np.linalg.norm(brir.left[li])),
                                  float(np.linalg.norm(brir.right[li])))

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def samplerate(self) -> int:
        return self._brir.samplerate

    @property
    def engine(self) -> str:
        """Which convolution backend this renderer actually built — ``'rust'`` or ``'cpu'``."""
        return "rust" if self._rust else "cpu"

    @property
    def method(self) -> str:
        return self._method

    @property
    def grid_step(self) -> float:
        return self._grid_step

    @property
    def n_bus(self) -> int:
        """Number of dense grid buses (one synthesized BRIR per grid direction)."""
        return self._dense.n_channels

    def _nearest_grid(self, az_deg: float) -> int:
        d = np.abs(((self._grid_az - az_deg + 180.0) % 360.0) - 180.0)
        return int(np.argmin(d))

    def process_block(self, pcm, azimuths) -> np.ndarray:
        """Render one ``(block_size, C)`` 7.1 block → ``(block_size, 2)`` stereo."""
        pcm = np.asarray(pcm, dtype=np.float64)
        c = self._brir.n_channels
        if pcm.ndim != 2 or pcm.shape != (self._block, c):
            raise ValueError(f"pcm must be ({self._block}, {c}); got {pcm.shape}")
        az = np.asarray(azimuths, dtype=np.float64).reshape(-1)
        targets = np.array([self._nearest_grid(float(az[i])) for i in self._dir_idx], dtype=np.intp)
        bus_in = self._mixer.mix(pcm[:, self._dir_idx], targets)  # (n_grid, block)
        if self._rust:
            out = np.asarray(self._conv.process(np.ascontiguousarray(bus_in)))
        else:
            out = self._conv.process_block(np.ascontiguousarray(bus_in.T))
        if self._lfe_idx is not None:
            lfe = pcm[:, self._lfe_idx]
            if self._lfe_mode == "brir":
                out = out + self._lfe.process_block(lfe[:, None])
            else:
                out = out.copy()
                out[:, 0] += lfe * self._lfe_gain[0]
                out[:, 1] += lfe * self._lfe_gain[1]
        return out

"""Per-bus short DIRECT + one SHARED late reverb render (B1.14, issue #53, approximate).

Every serious spatial-audio engine (Steam Audio, Resonance, Oculus) exploits the
fact that a BRIR's **late tail is diffuse and direction-independent** (journals
12/13: interaural coherence ~0): render each source's **short direct** per
direction, but run **one shared late reverb** on the summed scene instead of a
full per-direction tail. For 36 buses that is ~36 × direct + 1 × tail rather than
36 × full-filter — about an order of magnitude less convolution, at a *constant*
per-block load.

This module is the **offline-validated prototype** of that idea on the owner's
real baked BRIR. It is APPROXIMATE and gated on a by-ear sign-off (issue #53); it
does not replace the exact per-direction paths (``surround``/``live_render``),
which stay available.

What the validation (journal 49) established on the real ``baked_7p1_test``:

* The filters carry a **uniform ~10 ms (482-sample) leading propagation delay**:
  the energy-threshold onset of all 16 channel-ears sits in a 0.9 ms band around
  10 ms. The direct is *not* in the first samples. So we subtract a **uniform
  constant delay** (decision (a) of the issue) and take the fixed direct window
  from there — a per-filter ``estimate_delay`` window (decision (b)) is *worse*
  here, because the envelope peak latches a later reflection on three channels.
* A **generous 20 ms direct window** (not 5 ms) captures the direction-bearing
  early response and the whole ITD span (max |ITD| ≈ 1.3 ms ≪ 20 ms).
* The approximation error is confined to the tail: with the shared tail = the
  per-ear **mean of the channel tails**, a correlated scene reconstructs the full
  render **exactly**, and a fully-decorrelated scene is only ~1 dB low broadband
  (≤1.4 dB per octave band). The direct term — localisation, ITD/ILD, early
  timbre — is **identical** to the full render by construction.

Everything here is pure numpy, so it runs and is unit-tested headless. The native
kernel port is deliberately out of scope for this task (issue #53).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from binaural_headtracker.convolve import PartitionedConvolver, StereoConvolver
from binaural_headtracker.surround import BakedBRIRSet

# The directional 7.1 ring (LFE is non-directional and excluded from steering).
DIRECTIONAL_7P1: tuple[str, ...] = ("L", "R", "C", "SL", "SR", "RL", "RR")

# Generous direct window after the onset (ms). The owner's hard requirement: ~20 ms,
# not 5 ms, so the direction-bearing early response + full ITD span sit inside with
# margin even if an onset wanders a little (journal 49).
DEFAULT_DIRECT_MS = 20.0

# Fraction of cumulative energy that marks the onset. The real BRIR's onset is a
# ~10 ms leading delay followed by a sharp rise; 0.5 % of total energy is past the
# flat leading region but before the direct peak, giving a tight (<1 ms) onset
# spread across all channel-ears (journal 49) — far more robust than the Hilbert
# envelope peak, which latches late reflections on some directions.
ONSET_ENERGY_FRAC = 0.005


def energy_onset(ir: np.ndarray, frac: float = ONSET_ENERGY_FRAC) -> int:
    """First sample where cumulative energy crosses ``frac`` of the IR's total.

    Robust onset for a BRIR that carries a flat leading propagation delay: unlike
    an envelope-peak estimate it cannot be captured by a strong late reflection.
    Returns 0 for an all-zero IR.
    """
    ir = np.asarray(ir, dtype=np.float64)
    ce = np.cumsum(ir ** 2)
    total = ce[-1]
    if total <= 0.0:
        return 0
    return int(np.searchsorted(ce, frac * total))


def uniform_lead_delay(brir: BakedBRIRSet, channels: tuple[str, ...] = DIRECTIONAL_7P1) -> int:
    """The common leading delay to subtract from all filters (decision (a)).

    Taken as the **minimum** energy-threshold onset over the given channels and
    both ears, so trimming it clips no channel's direct. On the owner's real BRIR
    this is ~482 samples (~10 ms); the per-channel onsets lie within ~1 ms of it,
    which is exactly why a single uniform subtract is the right handling here.
    """
    present = [c for c in channels if c in brir.channels]
    if not present:
        raise ValueError("BRIR set has none of the requested channels")
    onsets = []
    for c in present:
        i = brir.index(c)
        onsets.append(energy_onset(brir.left[i]))
        onsets.append(energy_onset(brir.right[i]))
    return int(min(onsets))


@dataclass(frozen=True)
class DirectSharedSplit:
    """A BRIR set factored into per-direction short directs + one shared tail.

    ``direct_left``/``direct_right`` are ``(C, direct_len)`` — the short direct
    window per directional channel per ear, with the uniform lead delay trimmed
    off (so the whole render comes out ``lead_delay`` samples earlier, which only
    removes dead latency; all *relative* timing — ITD/ILD — is preserved). The
    directs reconstruct their directions' early response **exactly**.

    ``shared_left``/``shared_right`` are ``(tail_len,)`` — the single shared late
    reverb per ear, the mean of the directional channels' tails (the diffuse-field
    average). Its leading ``direct_len`` samples are zero so it never double-counts
    the direct region.

    ``channels`` is the directional order the ``direct_*`` rows follow.
    """

    channels: tuple[str, ...]
    direct_left: np.ndarray
    direct_right: np.ndarray
    shared_left: np.ndarray
    shared_right: np.ndarray
    lead_delay: int
    split: int
    samplerate: int

    @property
    def direct_len(self) -> int:
        return int(self.direct_left.shape[1])

    @property
    def tail_len(self) -> int:
        return int(self.shared_left.size)


def split_direct_shared(
    brir: BakedBRIRSet,
    *,
    direct_ms: float = DEFAULT_DIRECT_MS,
    channels: tuple[str, ...] = DIRECTIONAL_7P1,
    lead_delay: int | None = None,
) -> DirectSharedSplit:
    """Factor ``brir`` into per-direction short directs + one shared late reverb.

    ``lead_delay`` overrides the computed uniform leading delay (decision (a));
    ``direct_ms`` is the generous direct window after it. See
    :class:`DirectSharedSplit`. Pure numpy; works on any :class:`BakedBRIRSet`.
    """
    present = tuple(c for c in channels if c in brir.channels)
    if not present:
        raise ValueError("BRIR set has none of the requested channels")
    sr = brir.samplerate
    d = uniform_lead_delay(brir, present) if lead_delay is None else int(lead_delay)
    if d < 0:
        raise ValueError("lead_delay must be >= 0")
    direct_len = int(round(direct_ms * sr / 1000.0))
    if direct_len < 1:
        raise ValueError("direct_ms too small (direct window < 1 sample)")
    split = d + direct_len
    n = brir.n_taps
    split = min(split, n)
    direct_len = split - d

    idx = [brir.index(c) for c in present]
    dl = np.zeros((len(idx), direct_len))
    dr = np.zeros((len(idx), direct_len))
    tails_l = np.zeros((len(idx), n - d))
    tails_r = np.zeros((len(idx), n - d))
    for k, i in enumerate(idx):
        # Trim the uniform lead delay: everything shifts earlier by `d`.
        hl = brir.left[i][d:]
        hr = brir.right[i][d:]
        dl[k] = hl[:direct_len]
        dr[k] = hr[:direct_len]
        # Tail = the part beyond the direct window; leading direct region zeroed.
        tl = hl.copy()
        tr = hr.copy()
        tl[:direct_len] = 0.0
        tr[:direct_len] = 0.0
        tails_l[k] = tl
        tails_r[k] = tr
    shared_l = tails_l.mean(axis=0)
    shared_r = tails_r.mean(axis=0)
    return DirectSharedSplit(
        channels=present,
        direct_left=dl, direct_right=dr,
        shared_left=shared_l, shared_right=shared_r,
        lead_delay=d, split=split, samplerate=sr,
    )


class SharedReverbRenderer:
    """Stream a multichannel scene → stereo via per-bus directs + one shared reverb.

    Per block::

        direct_ear = Sum_c conv(x_c, direct_c_ear)      # exact, per direction
        tail_ear   = conv(Sum_c x_c, shared_ear)        # ONE shared late reverb
        out_ear    = direct_ear + tail_ear

    The directs run on a :class:`StereoConvolver` over the (short) direct bank; the
    shared tail is one mono→stereo pair of :class:`PartitionedConvolver`. Work per
    block is ``C`` short-direct convolutions + **one** long tail convolution,
    versus ``C`` full-length BRIR convolutions for the exact render — the order-of-
    magnitude reduction of issue #53, at a constant per-block cost.

    The output is advanced by ``split.lead_delay`` samples relative to the exact
    :func:`surround.render_7p1_to_stereo` (the trimmed dead latency); align by that
    offset to compare. This is the approximate path — gated on a by-ear sign-off.
    """

    def __init__(
        self,
        brir: BakedBRIRSet,
        *,
        block_size: int = 256,
        direct_ms: float = DEFAULT_DIRECT_MS,
        channels: tuple[str, ...] = DIRECTIONAL_7P1,
        split: DirectSharedSplit | None = None,
    ) -> None:
        if block_size <= 0:
            raise ValueError("block_size must be positive")
        self._block = int(block_size)
        self._split = split or split_direct_shared(brir, direct_ms=direct_ms, channels=channels)
        s = self._split
        self._ch_index = [brir.index(c) for c in s.channels]
        self._direct = StereoConvolver(s.direct_left, s.direct_right, self._block)
        self._tail_l = PartitionedConvolver(s.shared_left, self._block)
        self._tail_r = PartitionedConvolver(s.shared_right, self._block)

    @property
    def block_size(self) -> int:
        return self._block

    @property
    def samplerate(self) -> int:
        return self._split.samplerate

    @property
    def split(self) -> DirectSharedSplit:
        return self._split

    @property
    def tail_blocks(self) -> int:
        """Zero blocks needed to flush the longest (shared-tail) component."""
        return max(self._direct.tail_blocks, self._tail_l.tail_blocks)

    def process_block(self, pcm) -> np.ndarray:
        """Render one ``(block_size, C)`` scene block → ``(block_size, 2)`` stereo.

        ``pcm`` columns are in the BRIR's channel order (the directional columns
        this renderer was built for are used; any others, e.g. LFE, are ignored).
        """
        pcm = np.asarray(pcm, dtype=np.float64)
        if pcm.ndim != 2 or pcm.shape[0] != self._block:
            raise ValueError(f"pcm must be ({self._block}, C); got {pcm.shape}")
        dir_pcm = pcm[:, self._ch_index]
        out = self._direct.process_block(dir_pcm)
        mix = dir_pcm.sum(axis=1)
        out[:, 0] += self._tail_l.process_block(mix)
        out[:, 1] += self._tail_r.process_block(mix)
        return out

    def flush(self) -> np.ndarray:
        """Flush the direct + shared-tail tails → ``(tail_blocks * block_size, 2)``."""
        n = self.tail_blocks * self._block
        out = np.zeros((n, 2))
        dt = self._direct.flush()
        out[: dt.shape[0]] += dt
        lt = self._tail_l.flush()
        rt = self._tail_r.flush()
        out[: lt.size, 0] += lt
        out[: rt.size, 1] += rt
        return out


def render_shared_reverb(
    pcm,
    brir: BakedBRIRSet,
    *,
    block_size: int = 256,
    direct_ms: float = DEFAULT_DIRECT_MS,
    channels: tuple[str, ...] = DIRECTIONAL_7P1,
    flush: bool = True,
) -> np.ndarray:
    """Offline driver: stream a whole ``(M, C)`` scene through :class:`SharedReverbRenderer`.

    Returns the stereo output (trailing tail appended when ``flush`` is set), for
    offline A/B against the exact per-direction render. The output is advanced by
    the split's ``lead_delay`` samples (trimmed dead latency).
    """
    pcm = np.asarray(pcm, dtype=np.float64)
    if pcm.ndim != 2:
        raise ValueError("pcm must be 2-D (M, C)")
    m = pcm.shape[0]
    n_blocks = int(np.ceil(m / block_size)) if m else 0
    r = SharedReverbRenderer(brir, block_size=block_size, direct_ms=direct_ms, channels=channels)
    chunks: list[np.ndarray] = []
    for b in range(n_blocks):
        block = pcm[b * block_size : (b + 1) * block_size]
        if block.shape[0] < block_size:
            block = np.vstack([block, np.zeros((block_size - block.shape[0], pcm.shape[1]))])
        chunks.append(r.process_block(block))
    if flush and n_blocks:
        chunks.append(r.flush())
    return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 2))

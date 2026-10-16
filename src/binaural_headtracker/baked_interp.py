"""Head-rotation interpolation over the owner's 8 fixed baked BRIRs (B1.7).

B1.6 renders the owner's hand-tuned Audeze set as a *static* downmix: 8 fixed-
direction BRIRs, one per 7.1 speaker. But the head-tracked engine (M3) — and even a
source that pans *between* speakers (the rotating-source demo) — needs the response
at an *arbitrary* azimuth, not just the 8 measured ones. With only 8 sparse,
reverberant points we cannot interpolate the room the way B1.2 interpolates a dense
anechoic SOFA grid; instead we use the owner's own idea — **"vary the ITD and blend
the signals"** — applied to the **direct-path wavefront only**:

1. Bracket the target azimuth between its two adjacent ring speakers.
2. Split each BRIR into a short **direct** window (the first few ms — the
   direction-bearing wavefront) and the **tail** (the diffuse ~500 ms room, which
   journal 12/13 showed is essentially directionless, interaural coherence ~0).
3. **Direct:** time-align each neighbour to a common origin (remove its arrival
   delay), magnitude-blend with pairwise weights, then re-apply the *interpolated*
   delay — resynthesising a single, clean ITD at the in-between angle (the
   time-alignment interpolation of B1.2, here on just the direct part so the sharp
   wavefront doesn't comb-filter).
4. **Tail:** blend the neighbours' tails **in place** (no time-alignment) — the
   diffuse tail has no direction to interpolate and must not be smeared by the
   direct delay. Holding it fixed keeps the room intact.

Coarse with 8 points, but usable for modest angles — and, unlike amplitude panning
between two whole BRIRs (two room tails, two directions at once), it renders a
*single* phantom at the true in-between direction, which localises far better
across the wide gaps (notably the 90° hole across the back).

Distinct from B1.2 (continuous SOFA). Pure numpy/scipy, unit-tested headless.
"""

from __future__ import annotations

import numpy as np

from binaural_headtracker.interpolate import estimate_delay, fractional_delay
from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1, BakedBRIRSet

# The directional 7.1 speakers (LFE is non-directional, excluded from the ring).
DIRECTIONAL_7P1: tuple[str, ...] = ("L", "R", "C", "SL", "SR", "RL", "RR")

# Default direct-window length after the arrival (ms). ~3 ms covers the direct
# wavefront + earliest head response without reaching into the dense room tail.
DEFAULT_DIRECT_MS = 3.0

# A target within this many degrees of a measured speaker is treated as exact.
EXACT_TOL_DEG = 1e-6


def ring_azimuths(
    brir: BakedBRIRSet, directional: tuple[str, ...] = DIRECTIONAL_7P1
) -> list[tuple[int, float]]:
    """``(channel_index, azimuth_in_[0,360))`` for the directional speakers, az-sorted."""
    ring = []
    for ch in directional:
        if ch not in brir.channels:
            continue
        ring.append((brir.index(ch), SPEAKER_LAYOUT_7P1[ch][0] % 360.0))
    ring.sort(key=lambda t: t[1])
    if len(ring) < 2:
        raise ValueError("need at least two directional speakers to interpolate")
    return ring


def _bracket(ring: list[tuple[int, float]], az: float) -> tuple[int, int, float]:
    """Indices (into ``brir``) of the two speakers bracketing ``az`` + the fraction.

    Returns ``(ch_lo, ch_hi, frac)`` where ``frac`` is 0 at ``ch_lo`` and 1 at
    ``ch_hi`` — a pairwise position between the two adjacent ring speakers, with
    wrap-around across the front (…330° → 0°).
    """
    az = az % 360.0
    azs = [a for _, a in ring]
    for i in range(len(ring)):
        lo_i, hi_i = i, (i + 1) % len(ring)
        a_lo, a_hi = azs[lo_i], azs[hi_i]
        span = (a_hi - a_lo) % 360.0
        d = (az - a_lo) % 360.0
        if d <= span or np.isclose(d, span):
            frac = 0.0 if span == 0 else d / span
            return ring[lo_i][0], ring[hi_i][0], frac
    # Fallback (shouldn't happen): nearest speaker.
    return ring[0][0], ring[0][0], 0.0


def _interp_ear(
    ir_lo: np.ndarray, ir_hi: np.ndarray, w_lo: float, w_hi: float, split: int
) -> np.ndarray:
    """One ear: ITD-resynthesised direct (weighted) + in-place blended tail."""
    n = ir_lo.size
    split = min(split, n)
    # --- direct: time-align, blend, re-delay to the interpolated arrival --------
    pad = split  # generous zero pad so the fractional shifts wrap only zeros
    dlo = np.zeros(split + pad)
    dhi = np.zeros(split + pad)
    dlo[:split] = ir_lo[:split]
    dhi[:split] = ir_hi[:split]
    t_lo, t_hi = estimate_delay(dlo), estimate_delay(dhi)
    aligned = w_lo * fractional_delay(dlo, -t_lo) + w_hi * fractional_delay(dhi, -t_hi)
    direct = fractional_delay(aligned, w_lo * t_lo + w_hi * t_hi)[:split]
    # --- tail: blend in place (diffuse, no direction to align) ------------------
    out = np.zeros(n)
    out[:split] = direct
    out[split:] = w_lo * ir_lo[split:] + w_hi * ir_hi[split:]
    return out


def interpolate_baked_brir(
    brir: BakedBRIRSet,
    az: float,
    *,
    direct_ms: float = DEFAULT_DIRECT_MS,
    directional: tuple[str, ...] = DIRECTIONAL_7P1,
) -> tuple[np.ndarray, np.ndarray]:
    """Synthesise a ``(left, right)`` BRIR for azimuth ``az`` (deg, horizontal).

    Brackets ``az`` between the two adjacent ring speakers and blends them by the
    direct/tail scheme above. At (within :data:`EXACT_TOL_DEG` of) a measured speaker
    the exact measured BRIR is returned unchanged. ``direct_ms`` sets the direct
    window length; the room tail beyond it is held (blended in place), not aligned.
    """
    ring = ring_azimuths(brir, directional)
    az_n = az % 360.0

    # Exact hit → return the measured BRIR untouched.
    for idx, a in ring:
        if min((az_n - a) % 360.0, (a - az_n) % 360.0) <= EXACT_TOL_DEG:
            return brir.left[idx].copy(), brir.right[idx].copy()

    ch_lo, ch_hi, frac = _bracket(ring, az_n)
    w_lo, w_hi = 1.0 - frac, frac

    # Direct window = the arrival region plus `direct_ms`, measured from the earliest
    # onset across the two bracketing BRIRs (so both share one tail boundary).
    onset = min(
        estimate_delay(brir.left[ch_lo]), estimate_delay(brir.right[ch_lo]),
        estimate_delay(brir.left[ch_hi]), estimate_delay(brir.right[ch_hi]),
    )
    split = int(round(onset + direct_ms * brir.samplerate / 1000.0))

    left = _interp_ear(brir.left[ch_lo], brir.left[ch_hi], w_lo, w_hi, split)
    right = _interp_ear(brir.right[ch_lo], brir.right[ch_hi], w_lo, w_hi, split)
    return left, right

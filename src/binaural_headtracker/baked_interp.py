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
4. **Tail:** blend the neighbours' tails **in place** (no time-alignment), with
   **equal-power** weights — the diffuse tail has no direction to interpolate and
   must not be smeared by the direct delay. The tails are decorrelated, so a linear
   sum dips ~3 dB at the midpoint; ``sqrt(weight)`` holds the room level constant.

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

# Window (ms from the IR start) in which to find the direct-path onset. The direct
# arrival is the loudest thing in the first few ms; restricting the search here
# keeps a strong late reflection or the dense tail from capturing the global peak
# (``estimate_delay`` is a whole-signal argmax, tuned for anechoic HRIRs).
ONSET_SEARCH_MS = 10.0

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


def _min_phase_from_magnitude(mag_half: np.ndarray, nfft: int) -> np.ndarray:
    """Minimum-phase impulse (length ``nfft``) with the given rfft magnitude.

    Real-cepstrum method: fold the cepstrum onto the causal side so the phase
    becomes the Hilbert transform of the log-magnitude. Used to rebuild a single
    clean impulse from an *interpolated magnitude* — unlike a complex/time-domain
    blend, a magnitude blend cannot cancel, so no comb-filtering.
    """
    full = np.concatenate([mag_half, mag_half[-2:0:-1]])  # mirror to length nfft
    log_mag = np.log(np.maximum(full, 1e-12))
    cep = np.fft.ifft(log_mag).real
    win = np.zeros(nfft)
    win[0] = 1.0
    half = nfft // 2
    win[1:half] = 2.0
    win[half] = 1.0  # (win[half+1:] stays 0)
    mp_spectrum = np.exp(np.fft.fft(cep * win))
    return np.fft.ifft(mp_spectrum).real


def _interp_ear_minphase(
    ir_lo: np.ndarray, ir_hi: np.ndarray, w_lo: float, w_hi: float, split: int
) -> np.ndarray:
    """One ear, comb-free: interpolate the direct's **log-magnitude**, rebuild a
    minimum-phase impulse, and apply the separately-interpolated **ITD**; tail
    equal-power in place.

    Blending magnitudes (not complex IRs) avoids the phase cancellation that the
    time-domain blend produces where two HRTFs' pinna notches differ — the source
    of the rotation smear across wide speaker gaps.
    """
    n = ir_lo.size
    split = min(split, n)
    pad = split
    dlo = np.zeros(split + pad)
    dhi = np.zeros(split + pad)
    dlo[:split] = ir_lo[:split]
    dhi[:split] = ir_hi[:split]
    t_lo, t_hi = estimate_delay(dlo), estimate_delay(dhi)
    # Align each direct to a common origin so magnitudes are of the wavefront itself.
    alo = fractional_delay(dlo, -t_lo)
    ahi = fractional_delay(dhi, -t_hi)
    nfft = 1 << int(np.ceil(np.log2(max(256, 2 * split))))
    mlo = np.abs(np.fft.rfft(alo, n=nfft))
    mhi = np.abs(np.fft.rfft(ahi, n=nfft))
    log_blend = w_lo * np.log(np.maximum(mlo, 1e-12)) + w_hi * np.log(np.maximum(mhi, 1e-12))
    mp = _min_phase_from_magnitude(np.exp(log_blend), nfft)
    # Place the (causal) min-phase direct at the interpolated arrival delay.
    tstar = w_lo * t_lo + w_hi * t_hi
    placed = fractional_delay(np.concatenate([mp[:split], np.zeros(pad)]), tstar)
    out = np.zeros(n)
    out[:split] = placed[:split]
    out[split:] = np.sqrt(w_lo) * ir_lo[split:] + np.sqrt(w_hi) * ir_hi[split:]
    return out


# Pinna-notch search band (Hz) — the first pinna notch N1 lives here.
NOTCH_BAND_HZ = (5000.0, 11000.0)


def _notch_bin(mag: np.ndarray, fs: float, nfft: int, band=NOTCH_BAND_HZ) -> int | None:
    """Bin of the deepest pinna notch in ``band`` after removing the broad envelope.

    The envelope is a ~1/2-octave constant-Q smoothing of the log-magnitude; the
    detail (log-mag minus envelope) exposes the narrow notch. Returns the rfft bin
    of the deepest dip in the band, or ``None`` if there is no real notch there.
    """
    freqs = np.fft.rfftfreq(nfft, d=1.0 / fs)
    logmag = 20.0 * np.log10(np.maximum(mag, 1e-9))
    env = np.empty_like(logmag)
    for k, f in enumerate(freqs):
        lo = np.searchsorted(freqs, f / 2**0.25)
        hi = np.searchsorted(freqs, f * 2**0.25)
        env[k] = np.mean(logmag[lo : hi + 1]) if hi >= lo else logmag[k]
    detail = logmag - env
    in_band = (freqs >= band[0]) & (freqs <= band[1])
    if not np.any(in_band):
        return None
    idx = np.where(in_band)[0]
    j = idx[int(np.argmin(detail[idx]))]
    return int(j) if detail[j] < -3.0 else None  # require a real (>3 dB) dip


def _interp_ear_notchwarp(
    ir_lo: np.ndarray, ir_hi: np.ndarray, w_lo: float, w_hi: float, split: int, fs: float
) -> np.ndarray:
    """One ear: slide the N1 notch from each neighbour to the interpolated frequency
    (frequency-warped magnitude), reconstruct with the NEARER neighbour's **real
    phase** (keeps externalisation), apply the interpolated ITD; tail equal-power.
    """
    n = ir_lo.size
    split = min(split, n)
    pad = split
    dlo = np.zeros(split + pad)
    dhi = np.zeros(split + pad)
    dlo[:split] = ir_lo[:split]
    dhi[:split] = ir_hi[:split]
    t_lo, t_hi = estimate_delay(dlo), estimate_delay(dhi)
    alo = fractional_delay(dlo, -t_lo)
    ahi = fractional_delay(dhi, -t_hi)
    nfft = 1 << int(np.ceil(np.log2(max(256, 2 * split))))
    Alo = np.fft.rfft(alo, n=nfft)
    Ahi = np.fft.rfft(ahi, n=nfft)
    mlo = np.abs(Alo)
    mhi = np.abs(Ahi)
    nbins = mlo.size
    n_lo = _notch_bin(mlo, fs, nfft)
    n_hi = _notch_bin(mhi, fs, nfft)

    def warp_mag(mag, n_src, n_star):
        # Resample `mag` so its notch bin n_src lands at n_star (piecewise-linear
        # bin warp anchored at 0 and the last bin). No notch → return unchanged.
        if n_src is None or n_star is None:
            return mag
        last = nbins - 1
        xp = [0, n_star, last]
        fp = [0, n_src, last]  # new bin k came from original bin invwarp(k)
        orig = np.interp(np.arange(nbins), xp, fp)
        return np.interp(orig, np.arange(nbins), mag)

    log_lo = np.log(np.maximum(mlo, 1e-12))
    log_hi = np.log(np.maximum(mhi, 1e-12))
    if n_lo is not None and n_hi is not None:
        n_star = w_lo * n_lo + w_hi * n_hi
        mag = np.exp(
            w_lo * np.log(np.maximum(warp_mag(mlo, n_lo, n_star), 1e-12))
            + w_hi * np.log(np.maximum(warp_mag(mhi, n_hi, n_star), 1e-12))
        )
    else:
        mag = np.exp(w_lo * log_lo + w_hi * log_hi)  # no clear notch → plain mag blend
    # Reconstruct = minimum-phase of the (warped) magnitude × a blended EXCESS
    # (all-pass) phase. Using the full phase would re-impose a neighbour's own
    # magnitude notch; the excess phase carries the externalisation cue without the
    # magnitude coupling, so the warped notch survives AND the image stays out.
    # Blend BOTH neighbours' excess by weight (not a nearer-neighbour switch), so the
    # phase is continuous across frac=0.5 — a hard switch stepped the group delay at
    # every inter-speaker midpoint, a plausible source of rotation instability.
    mp_spec = np.fft.rfft(_min_phase_from_magnitude(mag, nfft), n=nfft)

    def _excess(spec):
        amp = np.fft.rfft(_min_phase_from_magnitude(np.abs(spec), nfft), n=nfft)
        e = spec / (amp + 1e-12)
        return e / (np.abs(e) + 1e-12)  # force unit magnitude (pure all-pass)

    excess = w_lo * _excess(Alo) + w_hi * _excess(Ahi)
    excess /= np.abs(excess) + 1e-12  # renormalise the blend to unit all-pass
    direct = np.fft.irfft(mp_spec * excess, n=nfft)
    placed = fractional_delay(
        np.concatenate([direct[:split], np.zeros(pad)]), w_lo * t_lo + w_hi * t_hi
    )
    out = np.zeros(n)
    out[:split] = placed[:split]
    out[split:] = np.sqrt(w_lo) * ir_lo[split:] + np.sqrt(w_hi) * ir_hi[split:]
    return out


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
    # --- tail: equal-power blend in place (diffuse, decorrelated, no direction) --
    # The two tails are decorrelated, so a linear w_lo+w_hi=1 sum is a *power* sum
    # that dips ~3 dB at the midpoint (the reverb would audibly dim between
    # speakers). Equal-power weights g=sqrt(w) keep g_lo^2+g_hi^2 = w_lo+w_hi = 1,
    # so the room level stays constant across the pan. No time-alignment (shifting
    # two different tails and summing would comb-filter).
    out = np.zeros(n)
    out[:split] = direct
    out[split:] = np.sqrt(w_lo) * ir_lo[split:] + np.sqrt(w_hi) * ir_hi[split:]
    return out


def interpolate_baked_brir(
    brir: BakedBRIRSet,
    az: float,
    *,
    method: str = "nearest",
    direct_ms: float = DEFAULT_DIRECT_MS,
    directional: tuple[str, ...] = DIRECTIONAL_7P1,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a ``(left, right)`` BRIR for azimuth ``az`` (deg, horizontal).

    ``method`` selects how a non-measured azimuth is produced:

    * ``"nearest"`` (default) — return the **nearest measured ring speaker's real
      BRIR**, unmodified. No spectral synthesis: this localises best by both the
      synthetic-listener judge (pooled5: 25° vs 43–51° for the synthesis methods, on
      the *trustworthy lateral axis* too) and in listening. The synthesis methods
      below buy a smooth phantom at the cost of **front↔back hemisphere flips** — the
      most disturbing error class (journal 18); nearest only drifts slightly in
      azimuth, which is far less wrong perceptually. **Smooth rotation is the render
      layer's job** (crossfade between successive BRIRs as the source moves, e.g. the
      overlap-add in the rotating demo / B3.1), not this per-azimuth lookup.
    * ``"notchwarp"`` — slide the single N1 pinna notch (frequency-warped magnitude)
      + nearer-neighbour excess phase. Comb-free, keeps externalisation, but the
      notch-warp can push the front/back cue → confident rear flips in wide gaps.
    * ``"timealign"`` — time-align the two bracketing directs and blend in time; real
      phase, but the complex sum combs between dissimilar directions.
    * ``"minphase"`` — plain minimum-phase of the magnitude blend + ITD; comb-free
      but flattens externalisation.

    For the synthesis methods: bracket ``az`` between the two adjacent speakers and
    blend their directs (per ``direct_ms``); the room tail is held (equal-power blend
    in place). An exact measured azimuth returns that speaker unchanged.
    """
    if method not in ("nearest", "timealign", "minphase", "notchwarp"):
        raise ValueError(
            f"method must be 'nearest', 'timealign', 'minphase' or 'notchwarp'; got {method!r}"
        )
    ring = ring_azimuths(brir, directional)
    if method == "nearest":
        az_n = az % 360.0
        idx = min(ring, key=lambda t: min((az_n - t[1]) % 360.0, (t[1] - az_n) % 360.0))[0]
        return brir.left[idx].copy(), brir.right[idx].copy()
    if method == "minphase":
        def ear(lo, hi, wl, wh, sp):
            return _interp_ear_minphase(lo, hi, wl, wh, sp)
    elif method == "notchwarp":
        def ear(lo, hi, wl, wh, sp):
            return _interp_ear_notchwarp(lo, hi, wl, wh, sp, brir.samplerate)
    else:
        def ear(lo, hi, wl, wh, sp):
            return _interp_ear(lo, hi, wl, wh, sp)
    az_n = az % 360.0

    # Exact hit → return the measured BRIR untouched.
    for idx, a in ring:
        if min((az_n - a) % 360.0, (a - az_n) % 360.0) <= EXACT_TOL_DEG:
            return brir.left[idx].copy(), brir.right[idx].copy()

    ch_lo, ch_hi, frac = _bracket(ring, az_n)
    w_lo, w_hi = 1.0 - frac, frac

    # Direct window = the arrival region plus `direct_ms`, measured from the earliest
    # onset across the two bracketing BRIRs (so both share one tail boundary). The
    # onset is searched only in the first `ONSET_SEARCH_MS` so a loud late reflection
    # or the dense tail cannot capture the (whole-signal) argmax.
    early = max(1, int(round(ONSET_SEARCH_MS * brir.samplerate / 1000.0)))
    onset = min(
        estimate_delay(brir.left[ch_lo][:early]), estimate_delay(brir.right[ch_lo][:early]),
        estimate_delay(brir.left[ch_hi][:early]), estimate_delay(brir.right[ch_hi][:early]),
    )
    split = int(round(onset + direct_ms * brir.samplerate / 1000.0))

    left = ear(brir.left[ch_lo], brir.left[ch_hi], w_lo, w_hi, split)
    right = ear(brir.right[ch_lo], brir.right[ch_hi], w_lo, w_hi, split)
    return left, right

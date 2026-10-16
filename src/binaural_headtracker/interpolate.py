"""Direction interpolation for HRIR sets (B1.2) — ITD-aware blending.

Given an arbitrary ``(az, el)``, return a left/right impulse response
interpolated from the nearest **measured** directions of an
:class:`~binaural_headtracker.hrir.HRIRSet`, so the render engine (FR-5) can
place a speaker at *any* direction, not only at a measured grid point.

Why not just average the neighbouring IRs? Because that **comb-filters** them.
Two IRs whose main arrivals differ by an inter-direction (or interaural) delay,
summed sample-for-sample, produce a magnitude spectrum with periodic nulls — the
classic two-tap comb — which audibly colours the result and smears the image. The
standard fix is **time-alignment interpolation** (a.k.a. the delay + minimum-phase
/ magnitude model used by most SOFA interpolators):

1. estimate each neighbour's broadband arrival delay (per ear),
2. advance each IR to a common time origin (remove its own delay),
3. blend the aligned IRs with the interpolation weights — now their main
   arrivals coincide, so there is no comb,
4. re-apply the weighted-mean delay to the blend.

The spectral/magnitude content is blended with onsets aligned, while the
delay — which carries the **ITD** and the inter-direction time-of-arrival — is
interpolated *explicitly* and therefore varies smoothly. Estimating the delay
**per ear independently** preserves and interpolates the interaural time
difference, which is the dominant low-frequency localisation cue — hence
"ITD-aware".

Everything here is pure numpy, so it runs and is unit-tested headless (no SOFA
file, no audio device). The neighbour search and weights are the VBAP-style step;
we use k-nearest with inverse-distance weights over the great-circle angle, which
needs no triangulation of the (possibly irregular) measurement sphere. A true
barycentric/Delaunay-VBAP weighting is a later refinement (noted in journal 08).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import hilbert

from .hrir import HRIRSet, angular_distance  # noqa: F401  (re-exported for callers)

# A query within this angle (degrees) of a measured direction is treated as an
# exact hit: we return that measurement untouched rather than fractionally
# re-delaying it (which would needlessly smear an otherwise exact IR).
EXACT_TOL_DEG = 1e-6


def estimate_delay(ir: np.ndarray) -> float:
    """Broadband arrival delay of an impulse response, in (sub-sample) samples.

    Estimated as the peak of the **analytic-signal (Hilbert) envelope** with
    parabolic sub-sample refinement. The Hilbert envelope has a single smooth
    broadband peak at the time of arrival, which is far more stable than the peak
    of the raw ``|ir|``: a real HRIR has several comparable sample peaks (pinna
    lobes), so ``argmax(|ir|)`` jumps between them from one direction to the next
    and the resulting ITD is noisy, whereas the envelope peak moves smoothly. The
    per-ear difference of two directions' delays is their ITD.

    A pure delta at index ``k`` returns exactly ``k`` (its envelope peaks there);
    a symmetric two-sample ramp returns the midpoint. Returns ``0.0`` for an
    all-zero IR.
    """
    ir = np.asarray(ir, dtype=np.float64)
    if not np.any(ir):
        return 0.0
    env = np.abs(hilbert(ir))
    peak = int(np.argmax(env))
    # Parabolic interpolation through (peak-1, peak, peak+1) of the envelope.
    if 0 < peak < env.size - 1:
        a, b, c = env[peak - 1], env[peak], env[peak + 1]
        denom = a - 2.0 * b + c
        if denom != 0.0:
            offset = 0.5 * (a - c) / denom
            # Guard against a degenerate fit driving the estimate off the peak.
            if -1.0 < offset < 1.0:
                return float(peak) + float(offset)
    return float(peak)


def fractional_delay(x: np.ndarray, delay: float) -> np.ndarray:
    """Delay ``x`` by ``delay`` samples (fractional, may be negative) via FFT.

    A positive ``delay`` shifts later (right); negative shifts earlier (left).
    Implemented as a linear-phase ramp in the frequency domain, which gives an
    exact band-limited (sinc) interpolation and is exact for an integer shift.

    The transform is circular, so content shifted past either end **wraps**. The
    caller must pad the signal with enough zeros that only (near-)zero samples
    wrap — see :func:`interpolate_hrir`, which pads before aligning/re-delaying.
    The output has the same length as ``x``.
    """
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n == 0 or delay == 0.0:
        return x.copy()
    freqs = np.fft.rfftfreq(n, d=1.0)  # cycles/sample in [0, 0.5]
    spectrum = np.fft.rfft(x) * np.exp(-2j * np.pi * freqs * delay)
    if n % 2 == 0:
        # Drop the Nyquist bin's phase (it cannot represent a fractional shift
        # for a real signal) to keep the result real and symmetric.
        spectrum[-1] = spectrum[-1].real
    return np.fft.irfft(spectrum, n=n)


def nearest_indices(
    directions: np.ndarray, az: float, el: float, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """The ``k`` measured directions nearest ``(az, el)`` by great-circle angle.

    Returns ``(indices, distances_deg)`` sorted by increasing distance. ``k`` is
    clamped to the number of available measurements.
    """
    d = np.asarray(directions, dtype=np.float64)
    if d.ndim != 2 or d.shape[1] != 2 or d.shape[0] == 0:
        raise ValueError("directions must be a non-empty (M, 2) array")
    if k < 1:
        raise ValueError("k must be >= 1")
    k = min(k, d.shape[0])
    a, e = np.radians(d[:, 0]), np.radians(d[:, 1])
    a0, e0 = np.radians(az), np.radians(el)
    cos_d = np.sin(e) * np.sin(e0) + np.cos(e) * np.cos(e0) * np.cos(a - a0)
    dist = np.degrees(np.arccos(np.clip(cos_d, -1.0, 1.0)))
    # argpartition for the k smallest, then sort those k by distance.
    part = np.argpartition(dist, k - 1)[:k]
    order = part[np.argsort(dist[part], kind="stable")]
    return order, dist[order]


def interpolation_weights(distances: np.ndarray, power: float = 2.0) -> np.ndarray:
    """Normalised inverse-distance weights for neighbour distances (degrees).

    ``w_j ∝ 1 / d_j**power``. If any distance is ~0 (an exact measured direction)
    the weight collapses onto that neighbour (a one-hot vector), so interpolation
    reproduces measured directions exactly. Weights sum to 1.
    """
    distances = np.asarray(distances, dtype=np.float64)
    exact = distances <= EXACT_TOL_DEG
    if np.any(exact):
        w = np.zeros_like(distances)
        w[np.argmax(exact)] = 1.0  # lowest-index exact hit
        return w
    w = 1.0 / np.power(distances, power)
    return w / w.sum()


def _blend_ear(irs: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """ITD-aware weighted blend of one ear's neighbour IRs (each a row of ``irs``).

    Align each IR to a common origin by its estimated delay, blend, then re-apply
    the weighted-mean delay. ``irs`` is ``(k, L)`` and already zero-padded enough
    that the circular shifts only wrap zeros. Returns a length-``L`` IR.
    """
    delays = np.array([estimate_delay(ir) for ir in irs])
    aligned = np.stack([fractional_delay(ir, -d) for ir, d in zip(irs, delays, strict=True)])
    blend = np.tensordot(weights, aligned, axes=(0, 0))
    mean_delay = float(np.dot(weights, delays))
    return fractional_delay(blend, mean_delay)


def interpolate_hrir(
    hrir_set: HRIRSet,
    az: float,
    el: float,
    k: int = 3,
    power: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolated ``(left_ir, right_ir)`` for direction ``(az, el)``.

    Finds the ``k`` nearest measured directions, weights them by inverse
    great-circle distance, and ITD-aware-blends them per ear (see the module
    docstring). If ``(az, el)`` coincides with a measured direction (within
    :data:`EXACT_TOL_DEG`), that measurement is returned unchanged.

    The returned IRs have the same length as the set's IRs.
    """
    idx, dist = nearest_indices(hrir_set.directions, az, el, k)
    weights = interpolation_weights(dist, power=power)

    # Exact hit: return the measured IR untouched (no fractional re-delay).
    if dist[0] <= EXACT_TOL_DEG:
        i = int(idx[0])
        return hrir_set.left[i].copy(), hrir_set.right[i].copy()

    n = hrir_set.n_taps
    # Pad so advancing by a neighbour's delay and re-delaying by the mean only
    # ever wraps zeros. Headroom = ceil(max delay) covers both shifts.
    left_rows = hrir_set.left[idx]
    right_rows = hrir_set.right[idx]
    max_delay = max(estimate_delay(ir) for ir in (*left_rows, *right_rows))
    pad = int(np.ceil(max_delay)) + 2
    work = n + pad
    left_pad = np.zeros((len(idx), work))
    right_pad = np.zeros((len(idx), work))
    left_pad[:, :n] = left_rows
    right_pad[:, :n] = right_rows

    left = _blend_ear(left_pad, weights)[:n]
    right = _blend_ear(right_pad, weights)[:n]
    return left, right

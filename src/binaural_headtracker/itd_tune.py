"""Per-position ITD retuning for the baked BRIR ring (in-app, no re-bake).

The owner's 7.1→binaural bake carries a hand-set interaural time difference (ITD)
per position — in the source EqualizerAPO config these are the *contralateral*
``Delay:`` lines (e.g. 0.14 ms front/rear, 0.70 ms sides). Baked into the BRIRs,
they can only be changed by re-baking. But the live renderer synthesises its
steering ring from the baked set, so retuning the ITD *before* that synthesis
takes effect live — the cockpit rebuilds the ring on the change, exactly as it
does for a rendering-method change.

This module does that retune. :func:`apply_itd_trims` returns a copy of a
:class:`~binaural_headtracker.surround.BakedBRIRSet` with each directional
channel's **contralateral** ear time-shifted so the group's ITD moves from a
labelled baseline to a target (both in ms). Front/rear (L, R, RL, RR) and the
sides (SL, SR) are two groups, matching the config's two ``Delay`` lines and the
fact that a front speaker (±30°) and a rear one (±150°) sit at the same *lateral*
angle, so share one natural ITD.

Larger ITD ⇒ the far (contralateral) ear arrives later ⇒ a wider image. The shift
is applied with the project's own band-limited :func:`fractional_delay`, so it is
exact for any sub-sample amount and introduces no clicks.

Honest scope: the shift is **relative** — ``target − baseline`` samples on the
contralateral ear. It does not re-measure the baked ITD (that is unreliable on a
reverberant blocked-ear BRIR); it assumes the baked baseline equals the labelled
one, so the *absolute* ms labels are only as good as that assumption, while the
*relative* retune the owner dials by ear is exact.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from binaural_headtracker.interpolate import fractional_delay
from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1, BakedBRIRSet

__all__ = [
    "ITD_GROUPS",
    "ITD_BASELINE_MS",
    "GROUP_AZIMUTH_DEG",
    "contralateral_ear",
    "apply_itd_trims",
    "population_itd_distribution",
]

#: Representative azimuth (deg) of each group, for plotting against the population
#: curve. Front/rear fold to the same lateral angle, so one value stands for both.
GROUP_AZIMUTH_DEG: dict[str, float] = {"front_rear": 30.0, "sides": 110.0}

#: The two retunable groups → their member directional channels. Front and rear
#: share one group because ±30° and ±150° are the same lateral angle (same ITD).
ITD_GROUPS: dict[str, tuple[str, ...]] = {
    "front_rear": ("L", "R", "RL", "RR"),
    "sides": ("SL", "SR"),
}

#: Labelled baseline ITD (ms) each group is baked with — the owner's configured
#: contralateral ``Delay`` values. Used as the zero point for a retune.
ITD_BASELINE_MS: dict[str, float] = {"front_rear": 0.14, "sides": 0.70}


def contralateral_ear(channel: str) -> str | None:
    """``"left"``/``"right"`` — the far ear for a directional ``channel``.

    A left-hemisphere speaker (positive azimuth) is heard first at the left ear, so
    its *contralateral* (delayed) ear is the right one, and vice versa. Returns
    ``None`` for a channel with no lateral offset (C/LFE at azimuth 0), which has no
    ITD to retune.
    """
    az = SPEAKER_LAYOUT_7P1.get(channel, (0.0, 0.0))[0]
    if az > 0:
        return "right"
    if az < 0:
        return "left"
    return None


def _lateral_angle(az_deg: float) -> float:
    """Fold an azimuth to its lateral angle in [0, 90] (front/back symmetry of ITD)."""
    a = abs(az_deg) % 360.0
    if a > 180.0:
        a = 360.0 - a
    return 90.0 - abs(90.0 - a)


def _woodworth_ms(az_deg: float, r_m: float, c: float = 343.0) -> float:
    """Woodworth spherical-head low-frequency ITD (ms) for a head of radius ``r_m``."""
    import math
    a = math.radians(_lateral_angle(az_deg))
    return r_m / c * (a + math.sin(a)) * 1000.0


def population_itd_distribution(
    *, az_step: float = 5.0, r_mean_m: float = 0.0875, r_sd_m: float = 0.005,
    r_clip: tuple[float, float] = (0.072, 0.104), n: int = 40000, seed: int = 0,
) -> dict:
    """ITD (ms) distribution vs azimuth across a modelled human population.

    Woodworth spherical-head ITD with the effective acoustic head radius drawn from a
    normal distribution (mean/sd in metres, clipped) — an anthropometric estimate that
    reproduces the commonly-quoted ~0.6–0.75 ms maximum ITD. Returns ``{"az": [...],
    "p05","q1","med","q3","p95": [...]}`` over azimuths 0..180°, for the in-app curve.

    It is a MODEL (labelled as such in the UI), not a measurement of any individual.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    r = np.clip(rng.normal(r_mean_m, r_sd_m, n), *r_clip)
    az = np.arange(0.0, 180.0 + az_step, az_step)
    out: dict = {"az": [round(float(a), 1) for a in az]}
    pct = {"p05": 5, "q1": 25, "med": 50, "q3": 75, "p95": 95}
    for name, q in pct.items():
        out[name] = [round(float(np.percentile(
            [_woodworth_ms(a, rr) for rr in r], q)), 3) for a in az]
    return out


def apply_itd_trims(
    brir: BakedBRIRSet,
    targets_ms: dict[str, float],
    *,
    baseline_ms: dict[str, float] | None = None,
) -> BakedBRIRSet:
    """Return a copy of ``brir`` with per-group ITD retuned to ``targets_ms``.

    ``targets_ms`` maps a group name in :data:`ITD_GROUPS` to its desired ITD in ms.
    A missing group, or a target equal to its baseline, leaves that group untouched.
    Each member channel's contralateral ear is shifted by ``target − baseline``
    samples (positive = later = wider image). ``C``/``LFE`` and any channel not in a
    group are copied unchanged, so the returned set is the same shape and channel
    order as the input.

    Raises :class:`ValueError` for an unknown group name or a non-finite target.
    """
    base = {**ITD_BASELINE_MS, **(baseline_ms or {})}
    sr = brir.samplerate
    left = np.array(brir.left, dtype=np.float64, copy=True)
    right = np.array(brir.right, dtype=np.float64, copy=True)

    for group, target in targets_ms.items():
        if group not in ITD_GROUPS:
            raise ValueError(f"unknown ITD group {group!r}; expected one of {tuple(ITD_GROUPS)}")
        if not np.isfinite(target):
            raise ValueError(f"ITD target for {group!r} must be finite, got {target!r}")
        shift = (float(target) - base[group]) * sr / 1000.0  # samples (may be fractional/neg)
        if shift == 0.0:
            continue
        for ch in ITD_GROUPS[group]:
            if ch not in brir.channels:
                continue
            ear = contralateral_ear(ch)
            if ear is None:
                continue
            i = brir.index(ch)
            buf = right if ear == "right" else left
            buf[i] = fractional_delay(buf[i], shift)

    return replace(brir, left=left, right=right)

"""Unit tests for B1.7 — interpolation over the owner's 8 fixed baked BRIRs.

A synthetic BakedBRIRSet places a direct-path delta at a per-ear, per-direction
delay that encodes a toy ITD (left-leading for left sources), plus a decorrelated
room "tail" after the direct window. That makes the two properties the block exists
for checkable: (1) measured azimuths reproduce exactly, and (2) between two speakers
the arrival delay (ITD) interpolates while the diffuse tail is held, not smeared.
"""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.baked_interp import (
    DIRECTIONAL_7P1,
    _min_phase_from_magnitude,
    interpolate_baked_brir,
    ring_azimuths,
)
from binaural_headtracker.interpolate import estimate_delay, fractional_delay
from binaural_headtracker.surround import SPEAKER_LAYOUT_7P1, BakedBRIRSet

FS = 48000
N = 2048
SPLIT = 200  # direct window in the synthetic set (well beyond the deltas)


def _synthetic_set(seed=0):
    """Baked set: per-speaker direct delta (ITD ∝ sin(az)) + a decorrelated tail."""
    rng = np.random.default_rng(seed)
    chans = DIRECTIONAL_7P1
    left = np.zeros((len(chans), N))
    right = np.zeros((len(chans), N))
    base = 60  # nominal direct arrival sample
    for i, ch in enumerate(chans):
        az = SPEAKER_LAYOUT_7P1[ch][0]
        lat = int(round(20 * np.sin(np.radians(az))))  # +left → left ear earlier
        left[i, base - lat] = 1.0
        right[i, base + lat] = 1.0
        # a quiet decorrelated tail after the direct window (diffuse room)
        left[i, SPLIT:] = 0.05 * rng.standard_normal(N - SPLIT)
        right[i, SPLIT:] = 0.05 * rng.standard_normal(N - SPLIT)
    return BakedBRIRSet(channels=chans, left=left, right=right, samplerate=FS)


def test_ring_is_sorted_and_covers_directional_speakers():
    ring = ring_azimuths(_synthetic_set())
    azs = [a for _, a in ring]
    assert azs == sorted(azs)
    assert len(ring) == len(DIRECTIONAL_7P1)
    assert azs[0] == pytest.approx(0.0)  # C at 0°


@pytest.mark.parametrize("ch", ["L", "C", "SL", "SR", "RR"])
def test_exact_at_measured_azimuth_returns_that_brir(ch):
    brir = _synthetic_set()
    i = brir.index(ch)
    az = SPEAKER_LAYOUT_7P1[ch][0]
    left, right = interpolate_baked_brir(brir, az)
    assert np.array_equal(left, brir.left[i])
    assert np.array_equal(right, brir.right[i])


def test_itd_interpolates_between_two_speakers():
    # 60° sits midway between L (30°) and SL (90°). Its ITD should land between the
    # two neighbours' ITDs, ~their mean (equal weights at the midpoint).
    brir = _synthetic_set()
    left, right = interpolate_baked_brir(brir, 60.0)
    itd = estimate_delay(right) - estimate_delay(left)  # >0 ⇒ left ear earlier
    lo, hi = brir.index("L"), brir.index("SL")
    itd_lo = estimate_delay(brir.right[lo]) - estimate_delay(brir.left[lo])
    itd_hi = estimate_delay(brir.right[hi]) - estimate_delay(brir.left[hi])
    assert itd_lo < itd < itd_hi
    assert itd == pytest.approx(0.5 * (itd_lo + itd_hi), abs=2.0)


def test_itd_is_monotonic_sweeping_front_to_left():
    brir = _synthetic_set()
    itds = []
    for az in range(0, 91, 15):
        left, right = interpolate_baked_brir(brir, float(az))
        itds.append(estimate_delay(right) - estimate_delay(left))
    assert np.all(np.diff(itds) > -1e-6)  # non-decreasing left-ear lead toward 90°


def test_tail_is_blended_in_place_equal_power():
    # The tail of a 60° interp (midway L↔SL) must be the two speakers' tails summed
    # in place (no time-alignment) with EQUAL-POWER weights sqrt(0.5), not linear 0.5.
    brir = _synthetic_set()
    left, _ = interpolate_baked_brir(brir, 60.0)
    lo, sl = brir.index("L"), brir.index("SL")
    g = np.sqrt(0.5)
    expected = g * brir.left[lo, SPLIT + 50 :] + g * brir.left[sl, SPLIT + 50 :]
    assert np.allclose(left[SPLIT + 50 :], expected, atol=1e-12)


def test_tail_power_is_preserved_across_the_pan():
    # Decorrelated tails: the blended tail's power at any pan position must stay ~the
    # endpoint power (no ~3 dB dip at midpoints). Check RMS across L→SL in dB.
    brir = _synthetic_set()
    lo = brir.index("L")
    ref = np.sqrt(np.mean(brir.left[lo, SPLIT:] ** 2))  # an endpoint tail RMS
    for az in (30.0, 45.0, 60.0, 75.0, 90.0):  # L(30) → SL(90)
        left, _ = interpolate_baked_brir(brir, az)
        rms = np.sqrt(np.mean(left[SPLIT:] ** 2))
        assert abs(20 * np.log10(rms / ref)) < 1.0  # within 1 dB everywhere


def test_onset_ignores_a_loud_late_reflection():
    # A reflection in the tail LOUDER than the direct must NOT drag the onset/split
    # (early-window search). Proof: the interpolated DIRECT region is identical with
    # and without the reflection — the reflection stays in the (held) tail. The old
    # whole-BRIR argmax onset would lock onto the reflection and corrupt the direct.
    clean = _synthetic_set()
    doctored = _synthetic_set()
    # Inject into BOTH bracketing speakers (L and SL), both ears — onset is a min()
    # across all four, so a reflection in one channel alone would be masked.
    for ch in ("L", "SL"):
        i = doctored.index(ch)
        doctored.left[i, 1500] = 5.0  # tail reflection 5x the direct delta
        doctored.right[i, 1500] = 5.0
    left_c, _ = interpolate_baked_brir(clean, 60.0)
    left_d, _ = interpolate_baked_brir(doctored, 60.0)
    assert np.allclose(left_d[:180], left_c[:180], atol=1e-12)  # direct unaffected
    assert abs(left_d[1500]) > 3.0  # the reflection is held in place in the tail


def test_back_gap_interpolation_is_a_single_arrival():
    # Across the wide back gap RL(135°)→RR(225°), the midpoint (180°) must still be a
    # single clean arrival (one dominant direct peak), not two — the whole point vs
    # amplitude-panning two whole BRIRs.
    brir = _synthetic_set()
    left, right = interpolate_baked_brir(brir, 180.0)
    direct = np.abs(left[:SPLIT]) + np.abs(right[:SPLIT])
    peak = direct.max()
    # Only one sample region should be near the peak (a single wavefront).
    assert np.sum(direct > 0.5 * peak) <= 4


def test_wraparound_bracket_front_right_to_center():
    # 345° sits between FR (330°) and C (360°≡0°); should interpolate, not error.
    brir = _synthetic_set()
    left, right = interpolate_baked_brir(brir, 345.0)
    assert left.shape == (N,) and right.shape == (N,)
    itd = estimate_delay(right) - estimate_delay(left)
    assert itd < 0  # right-ear leads on the right side


def test_rejects_set_without_two_directional_speakers():
    one = BakedBRIRSet(channels=("C",), left=np.zeros((1, N)), right=np.zeros((1, N)),
                       samplerate=FS)
    with pytest.raises(ValueError):
        interpolate_baked_brir(one, 10.0)


# --- minimum-phase magnitude-domain interpolation (comb-free, B1.10) --------


def test_min_phase_reconstruction_preserves_magnitude_and_is_causal():
    # A known FIR's magnitude, rebuilt as minimum-phase, must keep that magnitude
    # and be causal/front-loaded (min-phase packs energy earliest).
    nfft = 512
    h = np.array([1.0, -0.8, 0.5, 0.2, -0.3])
    mag = np.abs(np.fft.rfft(h, n=nfft))
    mp = _min_phase_from_magnitude(mag, nfft)
    assert np.allclose(np.abs(np.fft.rfft(mp, n=nfft)), mag, atol=1e-6)
    # min-phase ⇒ most energy in the first few samples
    cum = np.cumsum(mp**2) / np.sum(mp**2)
    assert cum[8] > 0.9


def _comb_pair_set(delay_lo=6, delay_hi=11):
    # Two adjacent ring speakers (L @30°, SL @90°) whose direct paths are comb
    # filters (delta + reflection) with nulls at DIFFERENT frequencies.
    base = 60
    left = np.zeros((2, N))
    right = np.zeros((2, N))
    for row, dly in ((0, delay_lo), (1, delay_hi)):
        for ch in (left, right):
            ch[row, base] = 1.0
            ch[row, base + dly] = 0.9  # reflection → spectral comb at fs/(2*dly)
    return BakedBRIRSet(channels=("L", "SL"), left=left, right=right, samplerate=FS)


def _direct_mag(ir, split=240, nfft=512):
    """Onset-aligned magnitude spectrum of a direct window (placement delay removed)."""
    seg = np.pad(ir[:split], (0, split))
    t = estimate_delay(seg)
    return np.abs(np.fft.rfft(fractional_delay(seg, -t)[:split], n=nfft))


def test_minphase_stays_within_parent_envelope_timealign_combs_below_it():
    # At 60° (midway L↔SL), the two directs are combs with nulls at different freqs.
    # minphase blends LOG-MAGNITUDE → weighted geometric mean, which stays pointwise
    # within [min,max] of the parents (keeps real notches, invents none). timealign
    # is a COMPLEX sum that cancels where phases oppose → dips BELOW both parents,
    # i.e. a spurious comb. That below-the-envelope dip is the rotation smear.
    brir = _comb_pair_set()
    mlo, mhi = _direct_mag(brir.left[0]), _direct_mag(brir.left[1])
    p_min, p_max = np.minimum(mlo, mhi), np.maximum(mlo, mhi)
    lt, _ = interpolate_baked_brir(brir, 60.0, method="timealign")
    lm, _ = interpolate_baked_brir(brir, 60.0, method="minphase")
    band = slice(5, 512 // 4)  # skip DC / very low bins
    eps = 1e-9
    mt, mm = _direct_mag(lt)[band], _direct_mag(lm)[band]
    pmn, pmx = p_min[band] + eps, p_max[band] + eps
    # minphase: never drops meaningfully below the parents' lower envelope (geometric interior).
    assert np.min(mm / pmn) > 0.85
    assert np.max(mm / pmx) < 1.15
    # timealign: dips well below both parents somewhere → spurious cancellation comb.
    assert np.min(mt / pmn) < 0.7


def test_minphase_itd_still_interpolates():
    brir = _synthetic_set()
    left, right = interpolate_baked_brir(brir, 60.0, method="minphase")
    itd = estimate_delay(right) - estimate_delay(left)
    lo, hi = brir.index("L"), brir.index("SL")
    itd_lo = estimate_delay(brir.right[lo]) - estimate_delay(brir.left[lo])
    itd_hi = estimate_delay(brir.right[hi]) - estimate_delay(brir.left[hi])
    assert itd_lo < itd < itd_hi


def test_minphase_exact_at_measured_direction():
    brir = _synthetic_set()
    i = brir.index("SL")
    left, right = interpolate_baked_brir(brir, SPEAKER_LAYOUT_7P1["SL"][0], method="minphase")
    assert np.array_equal(left, brir.left[i]) and np.array_equal(right, brir.right[i])


def test_invalid_method_raises():
    brir = _synthetic_set()
    with pytest.raises(ValueError):
        interpolate_baked_brir(brir, 60.0, method="bogus")

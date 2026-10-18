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
    # 70° sits midway between L (30°) and SL (110°). Its ITD should land between the
    # two neighbours' ITDs, ~their mean (equal weights at the midpoint).
    brir = _synthetic_set()
    left, right = interpolate_baked_brir(brir, 70.0, method="timealign")  # synthesis
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
        left, right = interpolate_baked_brir(brir, float(az), method="timealign")
        itds.append(estimate_delay(right) - estimate_delay(left))
    assert np.all(np.diff(itds) > -1e-6)  # non-decreasing left-ear lead toward the side speaker


def test_tail_is_blended_in_place_equal_power():
    # The tail of a 70° interp (midway L↔SL) must be the two speakers' tails summed
    # in place (no time-alignment) with EQUAL-POWER weights sqrt(0.5), not linear 0.5.
    brir = _synthetic_set()
    left, _ = interpolate_baked_brir(brir, 70.0, method="timealign")  # synthesis
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
    for az in (30.0, 50.0, 70.0, 90.0, 110.0):  # L(30) → SL(110)
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
    # Across the wide back gap RL(150°)→RR(210°), the midpoint (180°) must still be a
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
    # Two adjacent ring speakers (L @30°, SL @110°) whose direct paths are comb
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
    # At 70° (midway L↔SL), the two directs are combs with nulls at different freqs.
    # minphase blends LOG-MAGNITUDE → weighted geometric mean, which stays pointwise
    # within [min,max] of the parents (keeps real notches, invents none). timealign
    # is a COMPLEX sum that cancels where phases oppose → dips BELOW both parents,
    # i.e. a spurious comb. That below-the-envelope dip is the rotation smear.
    brir = _comb_pair_set()
    mlo, mhi = _direct_mag(brir.left[0]), _direct_mag(brir.left[1])
    p_min, p_max = np.minimum(mlo, mhi), np.maximum(mlo, mhi)
    lt, _ = interpolate_baked_brir(brir, 70.0, method="timealign")
    lm, _ = interpolate_baked_brir(brir, 70.0, method="minphase")
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


# --- notch-frequency-warping interpolation (B1.10) --------------------------


def _notched_pair_set(f_lo_hz=6000.0, f_hi_hz=9000.0):
    # Two adjacent ring speakers (L@30°, SL@110°) whose directs each have ONE clean
    # pinna-like notch, at different frequencies: a Gaussian magnitude dip rebuilt
    # as a minimum-phase impulse placed at `base`.
    base = 60
    nfft = 512
    freqs = np.fft.rfftfreq(nfft, 1 / FS)
    left = np.zeros((2, N))
    right = np.zeros((2, N))
    for row, f in ((0, f_lo_hz), (1, f_hi_hz)):
        mag = 1.0 - 0.95 * np.exp(-(((freqs - f) / 800.0) ** 2))
        mp = _min_phase_from_magnitude(mag, nfft)
        left[row, base : base + nfft] = mp[: min(nfft, N - base)]
        right[row] = left[row]
    return BakedBRIRSet(channels=("L", "SL"), left=left, right=right, samplerate=FS)


def _deepest_notch_hz(ir, split=240, nfft=1024):
    seg = np.pad(ir[:split], (0, split))
    t = estimate_delay(seg)
    mag = np.abs(np.fft.rfft(fractional_delay(seg, -t)[:split], n=nfft))
    freqs = np.fft.rfftfreq(nfft, 1 / FS)
    band = (freqs >= 5000) & (freqs <= 11000)
    logm = 20 * np.log10(np.maximum(mag, 1e-9))
    idx = np.where(band)[0]
    return float(freqs[idx[int(np.argmin(logm[idx]))]])


def test_notchwarp_slides_notch_to_interpolated_frequency():
    # Neighbours notch at 6 k and 9 k; the 60°(=midway L↔SL) interp should put a
    # SINGLE notch near 7.5 k, not keep both at 6 k and 9 k (what a plain mag blend
    # does). Check the deepest notch is near the interpolated frequency.
    brir = _notched_pair_set(6000.0, 9000.0)
    left, _ = interpolate_baked_brir(brir, 60.0, method="notchwarp")
    f = _deepest_notch_hz(left)
    assert 6800.0 < f < 8200.0  # ≈ 7.5 kHz, the interpolated notch


def test_notchwarp_matches_measured_notch_at_the_endpoints():
    # Near a speaker the warped notch coincides with that speaker's own notch.
    brir = _notched_pair_set(6000.0, 9000.0)
    left, _ = interpolate_baked_brir(brir, 40.0, method="notchwarp")  # close to L(30°)
    assert _deepest_notch_hz(left) < 7000.0  # pulled toward L's 6 kHz notch


def test_notchwarp_continuous_across_midpoint():
    # The excess-phase blend must be continuous across frac=0.5 (no nearer-neighbour
    # switch): the interpolated BRIR just below vs just above the L↔SL midpoint (60°)
    # should differ only slightly, not jump. Pins the group-delay-step fix.
    brir = _notched_pair_set(6000.0, 9000.0)
    lo_a, _ = interpolate_baked_brir(brir, 59.0, method="notchwarp")
    lo_b, _ = interpolate_baked_brir(brir, 61.0, method="notchwarp")
    direct_a, direct_b = lo_a[:240], lo_b[:240]
    step = np.max(np.abs(direct_a - direct_b))
    scale = np.max(np.abs(direct_a))
    assert step < 0.15 * scale  # smooth across the midpoint, no ~discontinuous jump


def test_notchwarp_applies_excess_phase_not_plain_minphase():
    # notchwarp must keep real excess phase → its direct differs from a plain
    # minimum-phase reconstruction of the same (warped) magnitude.
    brir = _notched_pair_set(6000.0, 9000.0)
    nw, _ = interpolate_baked_brir(brir, 60.0, method="notchwarp")
    mp, _ = interpolate_baked_brir(brir, 60.0, method="minphase")
    assert not np.allclose(nw[:240], mp[:240], atol=1e-6)


# --- nearest (the default): no synthesis, return the nearest real BRIR ----------


def test_nearest_is_the_default_method():
    # Default must be 'nearest' now: at an in-between azimuth it returns a *measured*
    # speaker's BRIR verbatim (not a synthesised blend).
    brir = _synthetic_set()
    default_l, default_r = interpolate_baked_brir(brir, 55.0)
    near_l, near_r = interpolate_baked_brir(brir, 55.0, method="nearest")
    assert np.array_equal(default_l, near_l) and np.array_equal(default_r, near_r)


def test_nearest_returns_the_nearest_measured_speaker():
    brir = _synthetic_set()
    # 55° is closer to L(30°) gap... actually nearest ring speaker to 55° is SL(90)? no:
    # |55-30|=25 vs |55-90|=35 → L. Check a few unambiguous ones.
    for az, ch in [(28.0, "L"), (88.0, "SL"), (10.0, "C"), (272.0, "SR"), (133.0, "RL")]:
        left, right = interpolate_baked_brir(brir, az, method="nearest")
        i = brir.index(ch)
        assert np.array_equal(left, brir.left[i]), f"az {az} should snap to {ch}"
        assert np.array_equal(right, brir.right[i])


def test_nearest_never_synthesises_only_eight_distinct_outputs():
    # Across the whole circle, nearest yields only the measured BRIRs — so it can't
    # introduce a phantom spectrum (the source of the front/back flips). Every output
    # equals one of the measured left-ear IRs exactly.
    brir = _synthetic_set()
    measured = [brir.left[i] for i in range(brir.n_channels)]
    for az in range(0, 360, 11):
        left, _ = interpolate_baked_brir(brir, float(az), method="nearest")
        assert any(np.array_equal(left, m) for m in measured)


# --- loudness-match: fix the synthesis LF level ripple (B1.13) ---------------

def _broadband_set(seed=1):
    """Like the synthetic set but each direct is a short *decorrelated* broadband burst
    (not a delta), so the time-aligned blend changes the combined level across the pan —
    the thing the loudness match exists to correct."""
    rng = np.random.default_rng(seed)
    chans = DIRECTIONAL_7P1
    left = np.zeros((len(chans), N))
    right = np.zeros((len(chans), N))
    base = 60
    for i, ch in enumerate(chans):
        az = SPEAKER_LAYOUT_7P1[ch][0]
        lat = int(round(20 * np.sin(np.radians(az))))
        burst_l = rng.standard_normal(24)
        burst_r = rng.standard_normal(24)
        left[i, base - lat : base - lat + 24] = burst_l
        right[i, base + lat : base + lat + 24] = burst_r
        left[i, SPLIT:] = 0.02 * rng.standard_normal(N - SPLIT)
        right[i, SPLIT:] = 0.02 * rng.standard_normal(N - SPLIT)
    return BakedBRIRSet(channels=chans, left=left, right=right, samplerate=FS)


def _combined_power(left, right, lo, hi):
    nf = 1 << int(left.size - 1).bit_length()
    f = np.fft.rfftfreq(nf, 1.0 / FS)
    p = np.abs(np.fft.rfft(left, nf)) ** 2 + np.abs(np.fft.rfft(right, nf)) ** 2
    m = (f >= lo) & (f < hi)
    return p[m].sum()


def test_loudness_match_preserves_ild_and_itd():
    # The same real per-frequency gain is applied to both ears, so the complex L/R ratio
    # (hence ILD and ITD — the localisation) is identical with and without the match.
    brir = _broadband_set()
    l0, r0 = interpolate_baked_brir(brir, 70.0, method="timealign", loudness_match=False)
    l1, r1 = interpolate_baked_brir(brir, 70.0, method="timealign", loudness_match=True)
    L0, R0 = np.fft.rfft(l0), np.fft.rfft(r0)
    L1, R1 = np.fft.rfft(l1), np.fft.rfft(r1)
    keep = (np.abs(L0) > 1e-6) & (np.abs(R0) > 1e-6)
    gl, gr = L1[keep] / L0[keep], R1[keep] / R0[keep]
    assert np.allclose(gl, gr, atol=1e-6)          # same gain both ears ⇒ ILD/ITD preserved
    assert np.allclose(gl.imag, 0.0, atol=1e-6)    # the gain is real ⇒ phase (ITD) untouched


def test_loudness_match_is_noop_at_a_measured_node():
    brir = _broadband_set()
    i = brir.index("SL")
    az = SPEAKER_LAYOUT_7P1["SL"][0]
    left, right = interpolate_baked_brir(brir, az, method="timealign", loudness_match=True)
    assert np.array_equal(left, brir.left[i]) and np.array_equal(right, brir.right[i])


def test_loudness_match_holds_level_constant_across_the_pan():
    # Equal-level endpoints ⇒ a constant perceived level across the pan. Without the match
    # the decorrelated direct blend makes the broadband level sag between speakers; with it
    # the level stays within ~1 dB of the endpoints at every angle.
    brir = _broadband_set()
    lo, _ = interpolate_baked_brir(brir, 30.0, method="nearest")   # L endpoint
    ro = interpolate_baked_brir(brir, 30.0, method="nearest")[1]
    ref = 10 * np.log10(_combined_power(lo, ro, 60, 4000))
    worst_off, worst_on = 0.0, 0.0
    for az in (45.0, 60.0, 70.0, 85.0, 100.0):  # across the L→SL gap
        for lm, acc in ((False, "off"), (True, "on")):
            lft, rgt = interpolate_baked_brir(brir, az, method="timealign", loudness_match=lm)
            dev = abs(10 * np.log10(_combined_power(lft, rgt, 60, 4000)) - ref)
            if acc == "off":
                worst_off = max(worst_off, dev)
            else:
                worst_on = max(worst_on, dev)
    assert worst_on < 1.0            # matched: within ~1 dB of the endpoints everywhere
    assert worst_on < worst_off      # and strictly better than the un-matched synthesis


def test_loudness_match_targets_the_interpolated_endpoint_envelope():
    # Unequal-level endpoints: the matched combined envelope must land on the weight-
    # interpolated endpoint envelope (here L scaled +6 dB, SL normal, midpoint ⇒ +2.5x power).
    brir = _broadband_set()
    li = brir.index("L")
    brir.left[li] *= 2.0
    brir.right[li] *= 2.0
    el = _combined_power(brir.left[li], brir.right[li], 60, 4000)
    si = brir.index("SL")
    es = _combined_power(brir.left[si], brir.right[si], 60, 4000)
    target = 0.5 * el + 0.5 * es  # 70° is the L↔SL midpoint (frac 0.5)
    lft, rgt = interpolate_baked_brir(brir, 70.0, method="timealign", loudness_match=True)
    got = _combined_power(lft, rgt, 60, 4000)
    assert abs(10 * np.log10(got / target)) < 1.0

"""Per-position ITD retuning (itd_tune.apply_itd_trims).

Clean synthetic BRIRs (single diracs) so the shift is exact and readable — the
reverberant real BRIRs are deliberately NOT used here (their ITD is not reliably
measurable; this unit pins the *operation*, not a field measurement)."""
from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.itd_tune import (
    ITD_BASELINE_MS,
    apply_itd_trims,
    contralateral_ear,
    population_itd_distribution,
)
from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet

SR = 48000
N = 256


def _dirac_set(peaks: dict[str, tuple[int, int]]) -> BakedBRIRSet:
    """Baked set over CHANNELS_7P1; peaks maps channel -> (left_idx, right_idx)."""
    c = len(CHANNELS_7P1)
    left = np.zeros((c, N))
    right = np.zeros((c, N))
    order = {ch: i for i, ch in enumerate(CHANNELS_7P1)}
    for ch, (li, ri) in peaks.items():
        left[order[ch], li] = 1.0
        right[order[ch], ri] = 1.0
    return BakedBRIRSet(channels=CHANNELS_7P1, left=left, right=right, samplerate=SR)


def _peak(x: np.ndarray) -> int:
    return int(np.argmax(np.abs(x)))


def test_contralateral_ear_by_azimuth():
    assert contralateral_ear("L") == "right"   # left speaker -> far ear is right
    assert contralateral_ear("SL") == "right"
    assert contralateral_ear("R") == "left"
    assert contralateral_ear("SR") == "left"
    assert contralateral_ear("C") is None       # no lateral offset, no ITD


def test_widening_delays_contralateral_ear_by_exact_amount():
    # L: ipsi (left) at 20, contra (right) at 20 + baseline. A target 10 samples
    # above baseline must move ONLY the right ear, by exactly 10 samples.
    base_s = round(ITD_BASELINE_MS["front_rear"] * SR / 1000)
    brir = _dirac_set({"L": (20, 20 + base_s)})
    extra = 10
    target = ITD_BASELINE_MS["front_rear"] + extra * 1000 / SR
    out = apply_itd_trims(brir, {"front_rear": target})
    i = CHANNELS_7P1.index("L")
    assert _peak(out.left[i]) == 20                       # ipsi untouched
    assert _peak(out.right[i]) == 20 + base_s + extra     # contra delayed by +10


def test_narrowing_advances_contralateral_ear():
    base_s = round(ITD_BASELINE_MS["sides"] * SR / 1000)
    brir = _dirac_set({"SL": (20, 20 + base_s)})
    extra = -8
    target = ITD_BASELINE_MS["sides"] + extra * 1000 / SR
    out = apply_itd_trims(brir, {"sides": target})
    i = CHANNELS_7P1.index("SL")
    assert _peak(out.left[i]) == 20
    assert _peak(out.right[i]) == 20 + base_s + extra      # contra advanced by 8


def test_right_speaker_shifts_the_left_ear():
    base_s = round(ITD_BASELINE_MS["front_rear"] * SR / 1000)
    brir = _dirac_set({"R": (20 + base_s, 20)})  # R: contra is LEFT ear
    out = apply_itd_trims(brir, {"front_rear": ITD_BASELINE_MS["front_rear"] + 12 * 1000 / SR})
    i = CHANNELS_7P1.index("R")
    assert _peak(out.right[i]) == 20                        # ipsi untouched
    assert _peak(out.left[i]) == 20 + base_s + 12           # contra delayed


def test_target_equal_baseline_is_identity():
    brir = _dirac_set({"L": (20, 27), "SL": (20, 54)})
    out = apply_itd_trims(brir, {"front_rear": ITD_BASELINE_MS["front_rear"],
                                 "sides": ITD_BASELINE_MS["sides"]})
    assert np.array_equal(out.left, brir.left)
    assert np.array_equal(out.right, brir.right)


def test_center_and_omitted_group_untouched():
    brir = _dirac_set({"C": (20, 20), "SL": (20, 54)})
    out = apply_itd_trims(brir, {"front_rear": 0.30})  # sides omitted
    ic, isl = CHANNELS_7P1.index("C"), CHANNELS_7P1.index("SL")
    assert np.array_equal(out.left[ic], brir.left[ic])   # C has no contra ear
    assert np.array_equal(out.right[ic], brir.right[ic])
    assert np.array_equal(out.right[isl], brir.right[isl])  # sides untouched


def test_shape_and_channels_preserved():
    brir = _dirac_set({"L": (20, 27)})
    out = apply_itd_trims(brir, {"front_rear": 0.25})
    assert out.channels == brir.channels
    assert out.left.shape == brir.left.shape and out.right.shape == brir.right.shape
    assert out.samplerate == brir.samplerate


def test_population_distribution_shape_and_physics():
    pop = population_itd_distribution(az_step=5.0)
    az = pop["az"]
    assert az[0] == 0.0 and az[-1] == 180.0
    for k in ("p05", "q1", "med", "q3", "p95"):
        assert len(pop[k]) == len(az)
    # percentiles ordered at every azimuth
    for i in range(len(az)):
        assert pop["p05"][i] <= pop["q1"][i] <= pop["med"][i] <= pop["q3"][i] <= pop["p95"][i]
    i0, i30, i90, i150, i180 = (az.index(x) for x in (0.0, 30.0, 90.0, 150.0, 180.0))
    assert pop["med"][i0] == 0.0 and pop["med"][i180] == 0.0      # front/back poles: no ITD
    assert pop["med"][i90] > pop["med"][i30]                       # peaks at the side
    # front/back fold: 150° shares the lateral angle (30°) of the front, so equal ITD
    assert pop["med"][i150] == pytest.approx(pop["med"][i30], abs=1e-6)
    assert 0.6 <= pop["med"][i90] <= 0.72                          # human-plausible max


def test_bad_inputs_raise():
    brir = _dirac_set({"L": (20, 27)})
    with pytest.raises(ValueError):
        apply_itd_trims(brir, {"nope": 0.3})
    with pytest.raises(ValueError):
        apply_itd_trims(brir, {"sides": float("nan")})

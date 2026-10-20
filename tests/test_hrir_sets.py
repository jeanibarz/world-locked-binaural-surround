"""Tests for dense-grid loading + the InterpWorldLocked7p1 dense_override path."""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.hrir_sets import (
    BlindAB,
    LocalizationTest,
    _ang_err_deg,
    _is_fb_confusion,
    discover_sets,
    load_dense_grid_sofa,
)
from binaural_headtracker.live_render import InterpWorldLocked7p1
from binaural_headtracker.surround import CHANNELS_7P1, BakedBRIRSet


def _write_sofa(path, az_deg, n_taps=128, fs=48000):
    """Minimal SimpleFreeFieldHRIR SOFA, horizontal ring."""
    import h5py
    az = np.asarray(az_deg, float)
    M = az.size
    rng = np.random.default_rng(0)
    ir = rng.normal(0, 1, (M, 2, n_taps))
    pos = np.column_stack([az % 360.0, np.zeros(M), np.ones(M)])
    with h5py.File(path, "w") as h:
        h.attrs["SOFAConventions"] = "SimpleFreeFieldHRIR"
        h.create_dataset("Data.IR", data=ir)
        h.create_dataset("Data.SamplingRate", data=np.array([float(fs)]))
        sp = h.create_dataset("SourcePosition", data=pos)
        sp.attrs["Type"] = "spherical"
    return ir


def test_load_dense_grid_sofa_shape_and_azimuths(tmp_path):
    az = np.array([0, 90, 45, 270, 180])     # deliberately unsorted
    p = tmp_path / "dense_x.sofa"
    _write_sofa(p, az, n_taps=64)
    brir, azimuths = load_dense_grid_sofa(p)
    assert isinstance(brir, BakedBRIRSet)
    assert brir.n_channels == 5
    assert brir.left.shape == (5, 64) and brir.right.shape == (5, 64)
    assert brir.samplerate == 48000
    # channels ordered by ascending [0,360) azimuth; values wrapped to [-180, 180]
    assert list(azimuths.keys()) == list(range(5))
    assert [v % 360 for v in azimuths.values()] == sorted(v % 360 for v in azimuths.values())
    assert all(-180.0 <= v <= 180.0 for v in azimuths.values())
    assert {round(v) % 360 for v in azimuths.values()} == {0, 45, 90, 180, 270}


def test_load_dense_grid_sofa_resamples(tmp_path):
    p = tmp_path / "dense_r.sofa"
    _write_sofa(p, [0, 120, 240], n_taps=100, fs=44100)
    brir, _ = load_dense_grid_sofa(p, target_rate=48000)
    assert brir.samplerate == 48000
    assert brir.left.shape[0] == 3


def test_discover_sets_owner_first(tmp_path):
    _write_sofa(tmp_path / "dense_ARI_nh102.sofa", [0, 180])
    _write_sofa(tmp_path / "dense_RIEC_001.sofa", [0, 180])
    sets = discover_sets(tmp_path)
    assert sets[0].name == "mine" and sets[0].is_owner
    names = [s.name for s in sets]
    assert names == ["mine", "ARI_nh102", "RIEC_001"]
    assert all(not s.is_owner for s in sets[1:])


def test_discover_sets_missing_dir(tmp_path):
    sets = discover_sets(tmp_path / "nope")
    assert [s.name for s in sets] == ["mine"]


def _owner_brir(n_taps=128, fs=48000):
    rng = np.random.default_rng(1)
    left = rng.normal(0, 1, (len(CHANNELS_7P1), n_taps))
    right = rng.normal(0, 1, (len(CHANNELS_7P1), n_taps))
    return BakedBRIRSet(CHANNELS_7P1, left, right, fs)


def test_interp_dense_override_renders_and_uses_grid(tmp_path):
    """InterpWorldLocked7p1 with a dense_override steers over the supplied real grid
    (nearest), using the owner brir only for 7.1 input routing + LFE."""
    owner = _owner_brir()
    p = tmp_path / "dense_cand.sofa"
    _write_sofa(p, np.arange(0, 360, 10), n_taps=96)   # 36-point dense ring
    dense, az = load_dense_grid_sofa(p)
    block = 64
    rend = InterpWorldLocked7p1(owner, dense_override=(dense, az), block_size=block)
    assert rend.method == "nearest"      # a real dense grid uses nearest lookup
    assert rend.n_bus == 36              # directional field is the 36-point grid, not 8
    pcm = np.random.default_rng(2).normal(0, 0.1, (block, len(CHANNELS_7P1)))
    az = dict(zip(CHANNELS_7P1, [30, -30, 0, 0, 150, -150, 110, -110], strict=True))
    out = rend.process_block(pcm, [az[c] for c in CHANNELS_7P1])
    assert out.shape == (block, 2)
    assert np.isfinite(out).all()


def test_interp_default_still_builds_from_sparse():
    """Without dense_override the behaviour is unchanged (grid from the sparse 8)."""
    owner = _owner_brir()
    rend = InterpWorldLocked7p1(owner, method="nearest", block_size=64, grid_step=30.0)
    assert rend.method == "nearest"
    assert rend.n_bus == len(np.arange(0, 360, 30))   # 12-point synth grid from the 8


# ------------------------------------------------------------------ BlindAB
def test_blind_play_returns_sealed_set_and_hides_label():
    import random
    ab = BlindAB("mine", "ARI_nh102")
    ab.shuffle(random.Random(0))
    # while sealed, the button label is the slot letter, not the set
    assert ab.label("A") == "A" and ab.label("B") == "B"
    name = ab.play("A")
    assert name in ("mine", "ARI_nh102")
    assert ab.current == "A"


def test_blind_tally_is_per_set_and_survives_shuffle():
    import random
    rng = random.Random(1)
    ab = BlindAB("mine", "ARI_nh102")
    ab.shuffle(rng)
    a_set = ab.set_name_for("A")
    ab.play("A")
    ab.prefer_current()      # +1 to whatever set A is
    assert ab.tally[a_set] == 1
    assert ab.trials == 1
    ab.shuffle(rng)                        # re-hide/reassign; tally persists per set
    assert ab.tally[a_set] == 1
    assert ab.current is None and ab.revealed is False


def test_blind_shuffle_can_swap_assignment():
    import random
    ab = BlindAB("x", "y")
    seen = set()
    for s in range(12):
        ab.shuffle(random.Random(s))
        seen.add(ab.set_name_for("A"))
    assert seen == {"x", "y"}              # both assignments occur across seeds


def test_blind_reveal_exposes_mapping():
    ab = BlindAB("mine", "cand")
    assert ab.revealed is False
    mp = ab.reveal()
    assert ab.revealed is True
    assert set(mp.keys()) == {"A", "B"} and set(mp.values()) == {"mine", "cand"}
    assert ab.label("A") == mp["A"]        # revealed → label is the set name


def test_blind_prefer_current_none_before_play():
    ab = BlindAB("a", "b")
    assert ab.prefer_current() is None
    assert ab.trials == 0


# ------------------------------------------------------------------ LocalizationTest
def test_ang_err_wraps():
    assert _ang_err_deg(350, 10) == pytest.approx(20.0)
    assert _ang_err_deg(10, 350) == pytest.approx(20.0)
    assert _ang_err_deg(0, 180) == pytest.approx(180.0)
    assert _ang_err_deg(30, 30) == pytest.approx(0.0)


def test_fb_confusion_detection():
    # 150° (rear-ish) mirrors to 30° (front, same side): guessing 30 for a 150 source is a flip
    assert _is_fb_confusion(150, 30) is True
    assert _is_fb_confusion(150, 150) is False      # correct → not a confusion
    assert _is_fb_confusion(30, 150) is True         # front heard as rear


def test_build_trials_count_and_pool():
    import random
    t = LocalizationTest.build(["mine", "cand"], n_per_set=5,
                               rng=random.Random(0), azimuths=[0, 90, 180, 270])
    assert t.total == 10
    assert {tr.set_name for tr in t.trials} == {"mine", "cand"}
    assert all(tr.truth_az in (0, 90, 180, 270) for tr in t.trials)
    # interleaved (shuffle) — not all of one set grouped first
    first_half = {tr.set_name for tr in t.trials[:5]}
    assert len(first_half) == 2


def test_answer_advances_and_completes():
    import random
    t = LocalizationTest.build(["a"], n_per_set=3, rng=random.Random(1))
    assert not t.done and t.current is not None
    for _ in range(3):
        cur = t.current
        t.answer(cur.truth_az, rt_s=1.0)   # perfect guesses
    assert t.done and t.current is None
    t.answer(0.0)   # no-op when done
    assert t.i == 3


def test_scoreboard_and_ranking():
    import random
    t = LocalizationTest.build(["good", "bad"], n_per_set=4, rng=random.Random(2),
                               azimuths=[45])
    while not t.done:
        cur = t.current
        if cur.set_name == "good":
            t.answer(cur.truth_az, rt_s=1.0)
        else:
            t.answer((180.0 - cur.truth_az) % 360.0, rt_s=3.0)   # front/back flip
    sb = t.scoreboard()
    assert sb["good"]["n"] == 4 and sb["bad"]["n"] == 4
    assert sb["good"]["mae"] == pytest.approx(0.0, abs=1e-6)
    assert sb["good"]["fbc"] == pytest.approx(0.0)
    assert sb["bad"]["fbc"] == pytest.approx(1.0)      # every bad trial flipped
    assert sb["good"]["rt"] == pytest.approx(1.0)
    assert t.ranking()[0] == "good"                    # lower error ranks first


def test_discover_groups_ctf_and_df(tmp_path):
    """ctf + df SOFAs for one subject group into a single HrirSet with both paths."""
    _write_sofa(tmp_path / "dense_SubjX.sofa", [0, 180])
    _write_sofa(tmp_path / "dense_SubjX_df.sofa", [0, 180])
    _write_sofa(tmp_path / "dense_OnlyCtf.sofa", [0, 180])
    sets = discover_sets(tmp_path)
    assert [s.name for s in sets] == ["mine", "OnlyCtf", "SubjX"]
    by = {s.name: s for s in sets}
    assert by["SubjX"].has_df
    assert by["SubjX"].path_for("df").stem.endswith("_df")
    assert by["SubjX"].path_for("ctf").stem == "dense_SubjX"
    assert not by["OnlyCtf"].has_df
    assert by["OnlyCtf"].path_for("df") == by["OnlyCtf"].path   # falls back to ctf

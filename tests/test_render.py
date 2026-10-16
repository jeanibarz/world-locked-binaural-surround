"""Unit tests for the generic-SOFA static renderer (B1.5).

Hermetic: a small synthetic :class:`HRIRSet` is built in-memory at the nominal
7.1 azimuths, so every speaker's layout direction lands on a *measured* grid
point and the interpolator returns it unchanged — which makes the per-speaker
HRIR lookup, the convolve-and-sum, and the world-lock seam all verifiable by
hand. No SOFA file, no audio device.

The IRs encode a toy ITD/ILD: a source on the left half (az in (0, 180)) has its
left-ear delta *earlier* and *louder* than its right-ear one, and vice-versa — so
the renderer's lateralisation is checkable, not just its plumbing.
"""

from __future__ import annotations

import numpy as np
import pytest

from binaural_headtracker.hrir import HRIRSet
from binaural_headtracker.render import (
    SpeakerHRIRBank,
    build_speaker_hrirs,
    render_layout_to_stereo,
)
from binaural_headtracker.surround import CHANNELS_7P1, SPEAKER_LAYOUT_7P1
from binaural_headtracker.world import layout_7p1

N_TAPS = 64
BASE_TAP = 24  # mid-IR, leaving room for the ITD shift either side
SR = 48000


def _lateralised_hrir_set() -> HRIRSet:
    """Synthetic HRIR grid at the nominal 7.1 azimuths with a toy ITD/ILD.

    For each measured azimuth ``az`` the left ear leads/leans by ``lat =
    round(8*sin(az))`` samples (positive = left source): left delta at
    ``BASE_TAP - lat`` with gain ``1 + 0.25*sin``, right at ``BASE_TAP + lat``
    with gain ``1 - 0.25*sin``. So a left source (az 90) → left earlier & louder.
    """
    azimuths = sorted({SPEAKER_LAYOUT_7P1[ch][0] % 360.0 for ch in CHANNELS_7P1})
    m = len(azimuths)
    left = np.zeros((m, N_TAPS))
    right = np.zeros((m, N_TAPS))
    for i, az in enumerate(azimuths):
        s = np.sin(np.radians(az))
        lat = int(round(8 * s))
        left[i, BASE_TAP - lat] = 1.0 + 0.25 * s
        right[i, BASE_TAP + lat] = 1.0 - 0.25 * s
    dirs = np.column_stack([np.array(azimuths), np.zeros(m)])
    return HRIRSet(directions=dirs, left=left, right=right, samplerate=SR)


# --- bank construction -----------------------------------------------------


def test_bank_has_one_ir_per_channel_in_order():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    assert bank.channels == CHANNELS_7P1
    assert bank.n_channels == 8
    assert bank.left.shape == (8, N_TAPS)
    assert bank.right.shape == (8, N_TAPS)
    assert bank.directions.shape == (8, 2)
    assert bank.samplerate == SR


def test_bank_directions_match_the_nominal_layout_for_a_fixed_head():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    for ch in CHANNELS_7P1:
        i = bank.channels.index(ch)
        az_nom, el_nom = SPEAKER_LAYOUT_7P1[ch]
        assert bank.directions[i, 0] == pytest.approx(az_nom, abs=1e-9)
        assert bank.directions[i, 1] == pytest.approx(el_nom, abs=1e-9)


def test_speaker_irs_reproduce_the_measured_hrir_at_the_grid_point():
    # Every nominal azimuth is a measured direction, so the (exact) interpolation
    # returns it unchanged: the bank IR equals the set's IR for that direction.
    hs = _lateralised_hrir_set()
    bank = build_speaker_hrirs(hs, layout_7p1())
    grid_az = list(hs.directions[:, 0])
    for ch in ("L", "SL", "SR", "C"):
        i = bank.channels.index(ch)
        az = SPEAKER_LAYOUT_7P1[ch][0] % 360.0
        j = grid_az.index(az)
        assert np.array_equal(bank.left[i], hs.left[j])
        assert np.array_equal(bank.right[i], hs.right[j])


# --- lateralisation (the renderer actually spatialises) --------------------


@pytest.mark.parametrize(
    "ch, left_leads",
    [("L", True), ("SL", True), ("R", False), ("SR", False), ("RR", False)],
)
def test_itd_and_ild_sign_per_speaker(ch, left_leads):
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    i = bank.channels.index(ch)
    left_peak = int(np.argmax(np.abs(bank.left[i])))
    right_peak = int(np.argmax(np.abs(bank.right[i])))
    left_gain = bank.left[i].max()
    right_gain = bank.right[i].max()
    if left_leads:  # source on the left half → left ear earlier and louder
        assert left_peak < right_peak
        assert left_gain > right_gain
    else:  # source on the right half → right ear earlier and louder
        assert right_peak < left_peak
        assert right_gain > left_gain


def test_center_channel_is_symmetric():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    i = bank.channels.index("C")  # az 0 → sin 0 → no ITD/ILD
    assert np.array_equal(bank.left[i], bank.right[i])


# --- offline render --------------------------------------------------------


def test_impulse_on_one_channel_reproduces_that_speakers_hrir():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    sl = bank.channels.index("SL")
    pcm = np.zeros((1, 8))
    pcm[0, sl] = 1.0  # a lone impulse panned hard to the side-left speaker
    out = bank.render(pcm)
    assert out.shape == (N_TAPS, 2)  # M=1 → out_len = N
    assert np.allclose(out[:, 0], bank.left[sl])
    assert np.allclose(out[:, 1], bank.right[sl])


def test_render_length_and_linearity():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    rng = np.random.default_rng(0)
    m = 200
    pcm = rng.standard_normal((m, 8))
    out = bank.render(pcm)
    assert out.shape == (m + N_TAPS - 1, 2)
    # Rendering channel-by-channel and summing equals rendering all at once.
    acc = np.zeros_like(out)
    for c in range(8):
        one = np.zeros_like(pcm)
        one[:, c] = pcm[:, c]
        acc += bank.render(one)
    assert np.allclose(acc, out)


def test_render_layout_to_stereo_matches_build_then_render():
    hs = _lateralised_hrir_set()
    layout = layout_7p1()
    pcm = np.random.default_rng(1).standard_normal((128, 8))
    one_shot = render_layout_to_stereo(pcm, hs, layout)
    staged = build_speaker_hrirs(hs, layout).render(pcm)
    assert np.allclose(one_shot, staged)


# --- offline == the B1.3 streaming engine ----------------------------------


def test_offline_render_equals_streaming_convolver():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    block = 32
    rng = np.random.default_rng(2)
    m = 5 * block + 7  # not a block multiple, to exercise zero-padding
    pcm = rng.standard_normal((m, 8))

    conv = bank.streaming_convolver(block)
    n_blocks = -(-m // block)  # ceil
    padded = np.zeros((n_blocks * block, 8))
    padded[:m] = pcm
    streamed = np.concatenate(
        [conv.process_block(padded[b * block : (b + 1) * block]) for b in range(n_blocks)]
        + [conv.flush()]
    )

    offline = bank.render(pcm)
    n = offline.shape[0]
    assert streamed.shape[0] >= n
    assert np.allclose(streamed[:n], offline, atol=1e-9)


# --- nearest vs interpolate ------------------------------------------------


def test_nearest_mode_also_reproduces_grid_hrirs():
    hs = _lateralised_hrir_set()
    interp = build_speaker_hrirs(hs, layout_7p1(), interpolate=True)
    nearest = build_speaker_hrirs(hs, layout_7p1(), interpolate=False)
    # Each speaker sits on a measured grid point, so both paths agree there.
    assert np.allclose(interp.left, nearest.left)
    assert np.allclose(interp.right, nearest.right)


# --- the world-lock seam (no tracker yet, just the projection) -------------


def test_head_yaw_swings_every_azimuth_by_minus_yaw():
    # A head yaw of +theta (CCW, about +z=up) must shift every speaker azimuth by
    # -theta in the head frame — the world-lock mechanic. Here we only exercise
    # the renderer's plumbing of head_rotation; the yaw->matrix mapping is M2/M3.
    hs = _lateralised_hrir_set()
    theta = np.radians(30.0)
    c, s = np.cos(theta), np.sin(theta)
    R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])  # world<-head, +z

    static = build_speaker_hrirs(hs, layout_7p1())
    turned = build_speaker_hrirs(hs, layout_7p1(), head_rotation=R)

    ci = static.channels.index("C")  # front speaker, az 0 at neutral
    assert static.directions[ci, 0] == pytest.approx(0.0, abs=1e-9)
    assert turned.directions[ci, 0] == pytest.approx(-30.0, abs=1e-6)
    # A real rotation changes the lookup, so the rendered IRs differ.
    assert not np.allclose(static.left, turned.left)


# --- validation ------------------------------------------------------------


def test_bank_rejects_wrong_pcm_width():
    bank = build_speaker_hrirs(_lateralised_hrir_set(), layout_7p1())
    with pytest.raises(ValueError):
        bank.render(np.zeros((10, 7)))  # 7 != 8 channels


def test_bank_rejects_inconsistent_shapes():
    with pytest.raises(ValueError):
        SpeakerHRIRBank(
            channels=("L", "R"),
            left=np.zeros((2, 8)),
            right=np.zeros((1, 8)),
            samplerate=SR,
            directions=np.zeros((2, 2)),
        )

"""Unit tests for the parametric-EQ engine (B5.1).

Correctness is checked by the properties that define each filter — peaking gain at
its centre frequency, shelf asymptotes at DC/Nyquist — via the actual frequency
response, so the RBJ coefficient port is validated independently of how it's
written. Parsing is checked on the owner's real EqualizerAPO lines; the streaming
cascade is checked to equal an offline `sosfilt`; and the live retune is checked to
be click-free.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import sosfilt, sosfreqz

from binaural_headtracker.eq import (
    BiquadCascade,
    BiquadSpec,
    ParametricEQ,
    design,
    design_sos,
    parse_filter_line,
)

FS = 48000.0


def _gain_db_at(sos, freq_hz):
    _, h = sosfreqz(sos.reshape(-1, 6), worN=np.atleast_1d(freq_hz), fs=FS)
    return 20.0 * np.log10(np.abs(h))


# --- parsing ---------------------------------------------------------------


def test_parse_peaking_bw_and_q():
    s = parse_filter_line("Filter: ON PK Fc 8200 Hz Gain -5 dB BW Oct 1.1")
    assert (s.ftype, s.fc, s.gain_db, s.bw_oct, s.q) == ("PK", 8200.0, -5.0, 1.1, None)
    s2 = parse_filter_line("Filter: ON PK Fc 8100 Hz Gain 4 dB Q 10")
    assert (s2.ftype, s2.q, s2.bw_oct) == ("PK", 10.0, None)


def test_parse_shelves_and_slope():
    ls = parse_filter_line("Filter: ON LS Fc 40 Hz Gain 22 dB")
    assert (ls.ftype, ls.fc, ls.gain_db, ls.q, ls.bw_oct, ls.slope_db) == (
        "LS", 40.0, 22.0, None, None, None,
    )
    hs = parse_filter_line("Filter: ON HS 6 dB Fc 3000 Hz Gain 0 dB")
    assert (hs.ftype, hs.slope_db, hs.fc, hs.gain_db) == ("HS", 6.0, 3000.0, 0.0)


def test_parse_numbered_rew_form_and_aliases():
    s = parse_filter_line("Filter  1: ON  PK       Fc   185.5 Hz  Gain  -1.10 dB  Q  1.060")
    assert (s.ftype, s.fc, s.gain_db, s.q) == ("PK", 185.5, -1.1, 1.06)
    assert parse_filter_line("Filter: ON PEQ Fc 1000 Hz Gain 3 dB Q 1").ftype == "PK"
    assert parse_filter_line("Filter: ON LSC 6 dB Fc 100 Hz Gain -4 dB").ftype == "LS"


def test_parse_off_and_non_filter_return_none():
    assert parse_filter_line("Filter: OFF PK Fc 1000 Hz Gain 3 dB Q 1") is None
    assert parse_filter_line("Preamp: -4.5 dB") is None
    assert parse_filter_line("Channel: L R") is None


# --- coefficient correctness (via the frequency response) ------------------


@pytest.mark.parametrize("gain", [-6.0, 3.0, 12.0])
def test_peaking_gain_at_center_equals_gain_db(gain):
    sos = design(BiquadSpec("PK", fc=1000.0, gain_db=gain, q=1.0), FS)
    assert _gain_db_at(sos, 1000.0)[0] == pytest.approx(gain, abs=1e-6)


def test_peaking_bw_octaves_also_hits_gain_at_center():
    sos = design(BiquadSpec("PK", fc=2000.0, gain_db=6.0, bw_oct=1.0), FS)
    assert _gain_db_at(sos, 2000.0)[0] == pytest.approx(6.0, abs=1e-6)


def test_zero_gain_peaking_is_flat():
    sos = design(BiquadSpec("PK", fc=1000.0, gain_db=0.0, q=1.0), FS)
    g = _gain_db_at(sos, np.array([50.0, 1000.0, 10000.0]))
    assert np.allclose(g, 0.0, atol=1e-9)


def test_low_shelf_asymptotes():
    sos = design(BiquadSpec("LS", fc=200.0, gain_db=10.0), FS)
    assert _gain_db_at(sos, 1.0)[0] == pytest.approx(10.0, abs=0.1)  # DC → full gain
    assert _gain_db_at(sos, 23000.0)[0] == pytest.approx(0.0, abs=0.1)  # Nyquist → 0


def test_high_shelf_asymptotes():
    sos = design(BiquadSpec("HS", fc=5000.0, gain_db=10.0), FS)
    assert _gain_db_at(sos, 23000.0)[0] == pytest.approx(10.0, abs=0.2)  # Nyquist → gain
    assert _gain_db_at(sos, 1.0)[0] == pytest.approx(0.0, abs=0.1)  # DC → 0


def test_design_normalises_a0_to_one():
    sos = design(BiquadSpec("PK", fc=1000.0, gain_db=6.0, q=2.0), FS)
    assert sos[3] == pytest.approx(1.0, abs=1e-12)


def test_design_sos_empty_and_stack_shape():
    assert design_sos([], FS).shape == (0, 6)
    specs = [BiquadSpec("PK", 500, 3, q=1), BiquadSpec("HS", 8000, 4, q=0.7)]
    assert design_sos(specs, FS).shape == (2, 6)


# --- streaming cascade == offline ------------------------------------------


def test_streaming_cascade_equals_offline_sosfilt():
    specs = [BiquadSpec("LS", 80, 12, None), BiquadSpec("PK", 3000, -5, q=2.0),
             BiquadSpec("HS", 9000, 6, None)]
    sos = design_sos(specs, FS)
    rng = np.random.default_rng(0)
    x = rng.standard_normal(5000)
    offline = sosfilt(sos, x)

    cascade = BiquadCascade(sos)
    block = 137  # not a divisor of len(x), to exercise ragged blocks
    chunks = [cascade.process_block(x[i : i + block]) for i in range(0, len(x), block)]
    streamed = np.concatenate(chunks)
    assert np.allclose(streamed, offline, atol=1e-10)


def test_empty_cascade_is_passthrough():
    c = BiquadCascade(np.empty((0, 6)))
    x = np.arange(10.0)
    assert np.array_equal(c.process_block(x), x)
    assert c.n_sections == 0


def test_reset_clears_state():
    c = BiquadCascade.from_specs([BiquadSpec("PK", 1000, 6, q=1)], FS)
    x = np.ones(64)
    first = c.process_block(x)
    c.reset()
    assert np.allclose(c.process_block(x), first)


# --- ParametricEQ: per-channel + glitch-free live retune -------------------


def test_parametric_eq_filters_each_channel_independently():
    specs = [[BiquadSpec("PK", 1000, 6, q=1)], [BiquadSpec("HS", 8000, -4, None)]]
    eq = ParametricEQ(specs, FS)
    rng = np.random.default_rng(1)
    x = rng.standard_normal((2000, 2))
    out = eq.process_block(x)
    # Each column equals that channel's own offline cascade.
    for ch in range(2):
        ref = sosfilt(design_sos(specs[ch], FS), x[:, ch])
        assert np.allclose(out[:, ch], ref, atol=1e-10)


def test_parametric_eq_rejects_wrong_width():
    eq = ParametricEQ([[BiquadSpec("PK", 1000, 3, q=1)]], FS)
    with pytest.raises(ValueError):
        eq.process_block(np.zeros((10, 2)))


def test_hard_swap_matches_fresh_cascade():
    eq = ParametricEQ([[BiquadSpec("PK", 1000, 6, q=1)]], FS)
    new = [[BiquadSpec("HS", 5000, 8, None)]]
    eq.set_specs(new, crossfade=0)
    x = np.random.default_rng(2).standard_normal((500, 1))
    # After a hard swap the output is the new filter fed from a cleared state.
    ref = BiquadCascade.from_specs(new[0], FS).process_block(x[:, 0])
    assert np.allclose(eq.process_block(x)[:, 0], ref, atol=1e-10)


def test_crossfade_is_click_free_and_converges():
    # A steady tone through an all-flat EQ, retuned mid-stream with a crossfade.
    fs = FS
    t = np.arange(8000) / fs
    tone = np.sin(2 * np.pi * 440 * t).reshape(-1, 1)
    eq = ParametricEQ([[]], fs)  # start flat (pass-through)
    before = eq.process_block(tone[:1000])
    assert np.allclose(before[:, 0], tone[:1000, 0], atol=1e-9)  # flat = identity

    cf = 2048
    eq.set_specs([[BiquadSpec("PK", 440, 12, q=1)]], crossfade=cf)
    faded = eq.process_block(tone[1000:5000])
    after = eq.process_block(tone[5000:])

    # (a) No click: the first faded sample starts from the OLD (flat) output, i.e.
    # equals the input tone there — g_new=0 at the fade start.
    assert faded[0, 0] == pytest.approx(tone[1000, 0], abs=1e-9)
    # (b) Bounded everywhere (the +12 dB peak can at most ~4x the tone amplitude).
    assert np.max(np.abs(np.concatenate([faded[:, 0], after[:, 0]]))) < 6.0
    # (c) Converged: well past the crossfade the output matches the NEW filter
    # driven by the same ongoing tone (compare steady-state RMS, boosted ~12 dB).
    steady = after[-1000:, 0]
    rms_out = np.sqrt(np.mean(steady**2))
    rms_in = np.sqrt(np.mean(tone[-1000:, 0] ** 2))
    assert 20 * np.log10(rms_out / rms_in) == pytest.approx(12.0, abs=0.5)

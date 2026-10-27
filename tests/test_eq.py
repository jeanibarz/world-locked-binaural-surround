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


def test_reference_coefficients_pin_alpha():
    # Independently computed RBJ SOS (NOT via eq.py) — pins alpha(Q) and the
    # default-S shelf alpha, which the gain-at-Fc / asymptote tests do not constrain.
    pk = design(BiquadSpec("PK", fc=1000.0, gain_db=6.0, q=2.0), FS)
    assert np.allclose(
        pk, [1.0224727682, -1.9381165806, 0.9323677439, 1.0, -1.9381165806, 0.9548405121],
        atol=1e-9,
    )
    ls = design(BiquadSpec("LS", fc=200.0, gain_db=10.0), FS)
    assert np.allclose(
        ls, [1.0108612709, -1.9718259538, 0.9621668305, 1.0, -1.9722369514, 0.9726171038],
        atol=1e-9,
    )


def test_bw_octaves_equals_equivalent_q():
    # BW-oct and its equivalent Q must give the identical filter (pins the BW→alpha
    # branch against the Q branch). Equivalence: Q = 1/(2*sinh(ln2/2*BW*w0/sin w0)).
    fc, bw = 2000.0, 1.0
    w0 = 2 * np.pi * fc / FS
    q_eq = 1.0 / (2 * np.sinh(np.log(2) / 2 * bw * w0 / np.sin(w0)))
    by_bw = design(BiquadSpec("PK", fc=fc, gain_db=6.0, bw_oct=bw), FS)
    by_q = design(BiquadSpec("PK", fc=fc, gain_db=6.0, q=q_eq), FS)
    assert np.allclose(by_bw, by_q, atol=1e-12)


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
    spec = [BiquadSpec("PK", 440, 12, q=1)]
    eq.set_specs([spec], crossfade=cf)
    faded = eq.process_block(tone[1000:5000])
    after = eq.process_block(tone[5000:])

    # Pin the exact blend: outgoing is the flat (identity) path = the input itself; incoming is
    # the new cascade from a cleared state; the two mix under a LINEAR gain ramp g = clip(n/cf).
    # Reconstructing the whole curve (BiquadCascade is coefficient-tested independently) catches
    # a non-linear ramp (e.g. g**0.2) and a wrong blend weight — the old first-sample + "< 6.0"
    # + steady-RMS trio passed both of those mutations.
    x = tone[1000:5000, 0]
    y_new = BiquadCascade.from_specs(spec, fs).process_block(x)
    g = np.clip(np.arange(x.size) / cf, 0.0, 1.0)
    assert np.allclose(faded[:, 0], (1.0 - g) * x + g * y_new, atol=1e-9)

    # Converged: well past the crossfade the output is the steady NEW filter, ~+12 dB.
    steady = after[-1000:, 0]
    rms_out = np.sqrt(np.mean(steady**2))
    rms_in = np.sqrt(np.mean(tone[-1000:, 0] ** 2))
    assert 20 * np.log10(rms_out / rms_in) == pytest.approx(12.0, abs=0.5)


def test_second_swap_mid_crossfade_is_click_free():
    # Retune twice in quick succession (second swap lands inside the first fade).
    # The current blend must be frozen as the new fade's source → no discontinuity.
    fs = FS
    tone = np.sin(2 * np.pi * 300 * np.arange(6000) / fs).reshape(-1, 1)
    eq = ParametricEQ([[]], fs)
    eq.process_block(tone[:500])
    spec1 = [BiquadSpec("LS", 120, 12, None)]
    eq.set_specs([spec1], crossfade=2048)
    mid = eq.process_block(tone[500:1200])  # 700 samples into a 2048 fade
    spec2 = [BiquadSpec("HS", 6000, -9, None)]
    eq.set_specs([spec2], crossfade=2048)  # overlap: 2nd swap lands inside the 1st fade
    after = eq.process_block(tone[1200:])

    # Reconstruct `after` exactly. When the 2nd swap fires 700 samples into the 1st fade, the
    # current blend is frozen as the outgoing source with weights [(flat, 1-gf), (LS, gf)],
    # gf = 700/2048, and the HS cascade fades in over 2048 samples on top of it. Dropping the
    # (1-gf) on the frozen flat weight — the regression this test exists to catch — shifts
    # every sample of `after`; the old median-seam-step bound let that mutation through.
    x1 = tone[500:1200, 0]
    x2 = tone[1200:, 0]
    ls = BiquadCascade.from_specs(spec1, fs)
    ls.process_block(x1)                      # advance LS state exactly as the live cascade did
    ls_out2 = ls.process_block(x2)
    hs_out2 = BiquadCascade.from_specs(spec2, fs).process_block(x2)  # incoming, cleared state
    gf = 700 / 2048                           # frozen gain of the 1st fade at the 2nd swap
    y_out2 = (1.0 - gf) * x2 + gf * ls_out2   # frozen outgoing blend (flat is identity ⇒ x2)
    g2 = np.clip(np.arange(x2.size) / 2048, 0.0, 1.0)
    expected_after = (1.0 - g2) * y_out2 + g2 * hs_out2
    assert np.allclose(after[:, 0], expected_after, atol=1e-9)

    # And the seam is still click-free and the whole signal bounded.
    joined = np.concatenate([mid[:, 0], after[:, 0]])
    steps = np.abs(np.diff(joined))
    boundary_step = abs(after[0, 0] - mid[-1, 0])
    assert boundary_step <= 5 * np.median(steps) + 1e-9  # no click spike at the seam
    assert np.max(np.abs(joined)) < 6.0
    assert np.max(np.abs(joined)) < 6.0  # bounded

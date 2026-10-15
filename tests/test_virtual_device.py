"""Unit tests for the portable B0.3 virtual-device helpers.

These run anywhere (no audio device, no ``sounddevice``): they exercise the
name-based virtual-endpoint detection and the render→capture pairing that the
Windows WASAPI spike relies on to find a virtual 7.1 sink and its loopback.

The device dicts below mirror this host's real WASAPI inventory (journal 02/03):
a VoiceMeeter VAIO 8-ch sink (#31) with a 2-ch capture half (#35), a VoiceMeeter
AUX VAIO pair, and physical cards that must never be mistaken for virtual ones.
"""

from __future__ import annotations

import pytest

from binaural_headtracker.virtual_device import (
    is_virtual_device,
    loopback_for_sink,
    pair_loopbacks,
    pick_virtual_sink,
    virtual_family,
)


def _dev(name, in_ch, out_ch, rate=48000.0):
    return {
        "name": name,
        "max_input_channels": in_ch,
        "max_output_channels": out_ch,
        "default_samplerate": rate,
    }


# Indices chosen to match this host (journal 02): 0 = VAIO 8-ch sink, ... .
HOST = [
    _dev("Casque (FiiO K11)", 0, 2),
    _dev("VoiceMeeter Aux Input (VB-Audio VoiceMeeter AUX VAIO)", 0, 2),
    _dev("VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)", 0, 8),
    _dev("Microphone (Logi C270 HD WebCam)", 1, 0),
    _dev("VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)", 2, 0),
    _dev("VoiceMeeter Aux Output (VB-Audio VoiceMeeter AUX VAIO)", 2, 0),
    _dev("BenQ EW3270U (NVIDIA High Definition Audio)", 0, 2),
]
VAIO_SINK = 2
VAIO_CAPTURE = 4


def test_is_virtual_device_detects_vendors_and_rejects_hardware():
    assert is_virtual_device("VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)")
    assert is_virtual_device("CABLE Output (VB-Audio Virtual Cable)")
    assert is_virtual_device("BlackHole 2ch")
    assert not is_virtual_device("Casque (FiiO K11)")
    assert not is_virtual_device("BenQ EW3270U (NVIDIA High Definition Audio)")


def test_virtual_family_pairs_render_and_capture_halves():
    render = "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)"
    capture = "VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)"
    assert virtual_family(render) == virtual_family(capture)
    # The AUX cable is a *different* family and must not collapse into the VAIO.
    aux = "VoiceMeeter Aux Output (VB-Audio VoiceMeeter AUX VAIO)"
    assert virtual_family(aux) != virtual_family(capture)
    # VB-CABLE halves share their parenthetical identity too.
    assert virtual_family("CABLE Input (VB-Audio Virtual Cable)") == virtual_family(
        "CABLE Output (VB-Audio Virtual Cable)"
    )


def test_virtual_family_none_for_hardware():
    assert virtual_family("Casque (FiiO K11)") is None


def test_pick_virtual_sink_selects_the_eight_channel_vaio():
    assert pick_virtual_sink(HOST, min_output_channels=8) == VAIO_SINK


def test_pick_virtual_sink_none_when_no_multichannel_virtual_output():
    stereo_only = [
        _dev("Casque (FiiO K11)", 0, 2),
        _dev("CABLE Input (VB-Audio Virtual Cable)", 0, 2),
    ]
    assert pick_virtual_sink(stereo_only, min_output_channels=8) is None
    # Lowering the bar accepts the stereo virtual cable.
    assert pick_virtual_sink(stereo_only, min_output_channels=2) == 1


def test_pair_loopbacks_matches_vaio_render_to_its_capture():
    pairs = pair_loopbacks(HOST)
    fams = {p.family: p for p in pairs}
    vaio_fam = virtual_family("VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)")
    assert vaio_fam in fams
    p = fams[vaio_fam]
    assert (p.render, p.capture) == (VAIO_SINK, VAIO_CAPTURE)
    assert p.render_channels == 8 and p.capture_channels == 2
    # Richest render leads the sorted list.
    assert pairs[0].render_channels == 8


def test_pair_loopbacks_ignores_hardware_only_families():
    hw = [_dev("Casque (FiiO K11)", 0, 2), _dev("Microphone (Trust)", 1, 0)]
    assert pair_loopbacks(hw) == []


def test_loopback_for_sink_returns_matching_capture():
    assert loopback_for_sink(HOST, VAIO_SINK) == VAIO_CAPTURE


def test_loopback_for_sink_none_without_capture_half():
    # A render-only virtual sink (no capture endpoint in its family).
    only_render = [_dev("CABLE Input (VB-Audio Virtual Cable)", 0, 8)]
    assert loopback_for_sink(only_render, 0) is None


def test_loopback_for_sink_out_of_range():
    assert loopback_for_sink(HOST, 999) is None
    assert loopback_for_sink(HOST, -1) is None


@pytest.mark.parametrize("bad", ["", "Realtek(R) Audio"])
def test_virtual_family_none_for_empty_or_plain(bad):
    assert virtual_family(bad) is None


# --- host-API restriction (the real bug the spike surfaced: journal 03) ------
#
# The same virtual cable appears under several host APIs. MME truncates device
# names to 31 chars ("MAXPNAMELEN"), chopping the closing paren so pairing breaks
# and latency differs from WASAPI. Role resolution must be constrained to one
# host API via ``allow``.

_MME_TRUNC = "VoiceMeeter Input (VB-Audio Voi"  # MME 31-char truncation, no ")"
MIXED = [
    _dev(_MME_TRUNC, 0, 8),  # 0: MME duplicate, truncated
    _dev("VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)", 0, 8),  # 1: WASAPI sink
    _dev("VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)", 2, 0),  # 2: WASAPI capture
]
WASAPI_ONLY = {1, 2}


def test_allow_restricts_sink_to_the_wasapi_endpoint():
    # Without a restriction the truncated MME duplicate (index 0) wins on tie.
    assert pick_virtual_sink(MIXED, min_output_channels=8) == 0
    # Restricted to WASAPI, the full-named endpoint is chosen.
    assert pick_virtual_sink(MIXED, min_output_channels=8, allow=WASAPI_ONLY) == 1


def test_allow_restricts_pairing_and_loopback():
    assert loopback_for_sink(MIXED, 1, allow=WASAPI_ONLY) == 2
    pairs = pair_loopbacks(MIXED, allow=WASAPI_ONLY)
    assert len(pairs) == 1 and (pairs[0].render, pairs[0].capture) == (1, 2)


def test_mme_truncated_name_does_not_pair_with_full_name():
    # The truncated MME name yields a different family from the full WASAPI name,
    # so it never cross-pairs — the reason we constrain to WASAPI rather than
    # rely on names alone.
    assert virtual_family(_MME_TRUNC) != virtual_family(
        "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)"
    )

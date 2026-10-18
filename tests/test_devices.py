"""B6.2 — device-selection pure logic: labelling, defaults, CLI spec resolution."""

from __future__ import annotations

import pytest

from binaural_headtracker.devices import (
    DeviceChoice,
    camera_choices,
    default_choice_label,
    output_device_choices,
    resolve_output_device,
)

# A query_devices()-shaped list: mics (input-only), speakers, headphones, a loopback.
RAW = [
    {"name": "Microphone", "max_output_channels": 0, "hostapi": 0},
    {"name": "Speakers (Realtek)", "max_output_channels": 2, "hostapi": 0},
    {"name": "Headphones (Audeze)", "max_output_channels": 2, "hostapi": 1},
    {"name": "VoiceMeeter Input", "max_output_channels": 8, "hostapi": 1},
]
HOSTAPIS = ["MME", "Windows WASAPI"]


def test_output_choices_filter_label_and_index() -> None:
    choices = output_device_choices(RAW, HOSTAPIS, default_index=2)
    # The input-only mic (index 0) is excluded; the three output devices keep their
    # original query_devices() indices (what sounddevice's device= expects).
    assert [c.index for c in choices] == [1, 2, 3]
    assert choices[0].label == "Speakers (Realtek) (MME, 2ch)"
    assert choices[2].label == "VoiceMeeter Input (Windows WASAPI, 8ch)"
    # The default device (index 2) is marked.
    assert choices[1].label.startswith("* ")
    assert "Headphones (Audeze)" in choices[1].label


def test_output_choices_without_hostapi_names() -> None:
    choices = output_device_choices(RAW)  # no hostapi list, no default
    assert choices[0].label == "Speakers (Realtek) (2ch)"
    assert not any(c.label.startswith("*") for c in choices)


def test_camera_choices() -> None:
    assert camera_choices([0, 2]) == [
        DeviceChoice(0, "Camera 0"),
        DeviceChoice(2, "Camera 2"),
    ]


def test_resolve_default_and_empty_are_none() -> None:
    choices = output_device_choices(RAW, HOSTAPIS)
    assert resolve_output_device(None, choices) is None
    assert resolve_output_device("", choices) is None


def test_resolve_index_passthrough() -> None:
    choices = output_device_choices(RAW, HOSTAPIS)
    assert resolve_output_device(3, choices) == 3
    assert resolve_output_device("1", choices) == 1


def test_resolve_name_substring_case_insensitive() -> None:
    choices = output_device_choices(RAW, HOSTAPIS)
    assert resolve_output_device("audeze", choices) == 2
    assert resolve_output_device("VoiceMeeter", choices) == 3


def test_resolve_unknown_name_raises() -> None:
    choices = output_device_choices(RAW, HOSTAPIS)
    with pytest.raises(ValueError, match="no output device matching"):
        resolve_output_device("nonexistent-device", choices)


def test_resolve_ambiguous_name_prefers_wasapi_over_mme() -> None:
    # The same hardware under several host APIs (as Windows exposes a DAC): a bare name must
    # resolve to the WASAPI entry (low latency, exclusive-capable), not MME's big buffer.
    raw = [
        {"name": "FiiO K11", "max_output_channels": 2, "hostapi": 0},  # MME
        {"name": "FiiO K11", "max_output_channels": 2, "hostapi": 1},  # DirectSound
        {"name": "FiiO K11", "max_output_channels": 2, "hostapi": 2},  # WASAPI
        {"name": "FiiO K11", "max_output_channels": 2, "hostapi": 3},  # WDM-KS
    ]
    apis = ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"]
    choices = output_device_choices(raw, apis)
    assert resolve_output_device("FiiO", choices) == 2          # the WASAPI index
    # WDM-KS beats DirectSound/MME when WASAPI is absent (list → MME, DirectSound, WDM-KS at 0,1,2)
    no_wasapi = output_device_choices(raw[:2] + raw[3:], apis)
    assert resolve_output_device("FiiO", no_wasapi) == 2        # the WDM-KS index here
    # an unambiguous name still resolves to its single match
    assert resolve_output_device("WDM-KS", choices) == 3


def test_default_choice_label() -> None:
    choices = output_device_choices(RAW, HOSTAPIS, default_index=2)
    assert default_choice_label(choices, 2) == choices[1].label
    assert default_choice_label(choices, None) is None
    assert default_choice_label(choices, 999) is None

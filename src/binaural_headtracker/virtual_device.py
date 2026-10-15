"""Portable helpers for the B0.3 virtual-device spike.

The hardware-touching code lives in ``spikes/b0_3_virtual_device.py`` and runs on
the Windows host (real WASAPI) driven through WSL interop; everything in *this*
module is pure and portable so it is unit-tested on any platform — including the
headless WSL/CI box that has no audio device and no ``sounddevice`` installed.

The job is **device-role resolution**: from a PortAudio device inventory (plain
dicts shaped like ``sounddevice.query_devices()``), identify the virtual-audio
endpoints, pick the virtual multichannel sink an application would select as its
7.1 output (FR-2), and pair each virtual render endpoint with the capture
endpoint that carries its stream back — the render→capture loopback B0.3 must
expose so the deferred round-trip latency (B0.2) can be measured.

Why name heuristics rather than indices: journal 02 established that PortAudio
device indices drift between boots and configs, so roles must be resolved at
runtime from names/channel counts, never hard-coded. VB-Audio endpoints encode
the cable identity in a trailing parenthetical that is *identical* for a cable's
render and capture halves (e.g. "VoiceMeeter Input (VB-Audio VoiceMeeter VAIO)"
and "VoiceMeeter Output (VB-Audio VoiceMeeter VAIO)"), which is what lets us pair
them without touching the mixer.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

# Vendor/driver tokens that mark a device as a software (virtual) audio endpoint
# rather than a physical card. Lower-cased substring match against the name.
_VIRTUAL_TOKENS: tuple[str, ...] = (
    "vb-audio",
    "voicemeeter",
    "virtual cable",
    "vb-cable",
    "virtual audio cable",
    "blackhole",  # macOS loopback driver
    "soundflower",  # legacy macOS loopback driver
    "loopback",
)

# Role words that distinguish the two halves of one cable; stripped when deriving
# a family key so a render endpoint and its capture endpoint collapse together.
_ROLE_WORDS: tuple[str, ...] = ("input", "output", "capture", "render", "playback")

_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")


def is_virtual_device(name: str) -> bool:
    """True when ``name`` looks like a software/virtual audio endpoint."""
    low = name.lower()
    return any(tok in low for tok in _VIRTUAL_TOKENS)


def _normalize(text: str) -> str:
    """Lower-case, drop role words, and collapse whitespace."""
    low = text.lower()
    for word in _ROLE_WORDS:
        low = re.sub(rf"\b{word}\b", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def virtual_family(name: str) -> str | None:
    """A stable key shared by the two halves of one virtual cable, or ``None``.

    Prefers the trailing parenthetical (VB-Audio puts the cable identity there,
    identical for both halves); otherwise falls back to the role-stripped name.
    Returns ``None`` for non-virtual devices so physical cards never pair.
    """
    if not is_virtual_device(name):
        return None
    m = _PAREN_RE.search(name)
    if m and is_virtual_device(m.group(1)):
        return _normalize(m.group(1))
    return _normalize(name)


def _out_ch(d: Mapping) -> int:
    return int(d.get("max_output_channels", 0) or 0)


def _in_ch(d: Mapping) -> int:
    return int(d.get("max_input_channels", 0) or 0)


def pick_virtual_sink(
    devices: Sequence[Mapping],
    min_output_channels: int = 8,
    allow: Collection[int] | None = None,
) -> int | None:
    """Index of the virtual *render* endpoint with the most output channels.

    This is the sink an application selects as its surround speaker output
    (FR-2). ``min_output_channels=8`` targets a 7.1 device; lower it to accept a
    stereo virtual cable. ``allow`` restricts consideration to those device
    indices — pass the WASAPI host-API subset, since the same virtual cable also
    appears under MME/DirectSound/WDM-KS with different latency (and MME
    truncates names to 31 chars, breaking pairing). Ties resolve to the lowest
    index for determinism.
    """
    best: int | None = None
    best_ch = min_output_channels - 1
    for i, d in enumerate(devices):
        if allow is not None and i not in allow:
            continue
        if not is_virtual_device(str(d.get("name", ""))):
            continue
        ch = _out_ch(d)
        if ch >= min_output_channels and ch > best_ch:
            best, best_ch = i, ch
    return best


@dataclass(frozen=True)
class LoopbackPair:
    """A virtual cable's render endpoint and the capture endpoint carrying it."""

    family: str
    render: int
    capture: int
    render_channels: int
    capture_channels: int


def pair_loopbacks(
    devices: Sequence[Mapping],
    allow: Collection[int] | None = None,
) -> list[LoopbackPair]:
    """All render→capture virtual-cable pairs found in ``devices``.

    Groups virtual endpoints by :func:`virtual_family`; a family yields a pair
    when it has at least one render endpoint (outputs) and one capture endpoint
    (inputs). Within a family the widest render and widest capture are chosen.
    ``allow`` restricts consideration to those device indices (e.g. the WASAPI
    subset) so a cable's MME/WDM-KS duplicates do not form cross-host-API pairs.
    Sorted by descending render channel count so the richest sink leads.
    """
    renders: dict[str, tuple[int, int]] = {}
    captures: dict[str, tuple[int, int]] = {}
    for i, d in enumerate(devices):
        if allow is not None and i not in allow:
            continue
        fam = virtual_family(str(d.get("name", "")))
        if fam is None:
            continue
        oc, ic = _out_ch(d), _in_ch(d)
        if oc > 0 and oc > renders.get(fam, (-1, -1))[1]:
            renders[fam] = (i, oc)
        if ic > 0 and ic > captures.get(fam, (-1, -1))[1]:
            captures[fam] = (i, ic)

    pairs = [
        LoopbackPair(
            family=fam,
            render=renders[fam][0],
            capture=captures[fam][0],
            render_channels=renders[fam][1],
            capture_channels=captures[fam][1],
        )
        for fam in renders
        if fam in captures
    ]
    pairs.sort(key=lambda p: p.render_channels, reverse=True)
    return pairs


def loopback_for_sink(
    devices: Sequence[Mapping],
    sink_index: int,
    allow: Collection[int] | None = None,
) -> int | None:
    """Capture endpoint in the same virtual family as the sink at ``sink_index``.

    Given the chosen 7.1 sink (a render endpoint), return the input endpoint that
    carries its stream back for a loopback measurement, or ``None`` if the cable
    exposes no capture half (then an external route/cable is required — see the
    user setup doc). ``allow`` restricts the pairing search (e.g. to WASAPI).
    """
    if not 0 <= sink_index < len(devices):
        return None
    fam = virtual_family(str(devices[sink_index].get("name", "")))
    if fam is None:
        return None
    for pair in pair_loopbacks(devices, allow=allow):
        if pair.family == fam:
            return pair.capture
    return None

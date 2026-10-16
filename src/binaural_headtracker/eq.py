"""Parametric EQ — EqualizerAPO-semantics biquads, live and per-channel (B5.1).

The owner's binaural chain is not just a bake: after the per-speaker convolution it
applies a headphone-compensation FIR and a large parametric EQ, and journal 13
showed that EQ is **load-bearing for the spatial image** (it lifts the direct sound
over the diffuse reverb tail, so the image stops wandering), not merely cosmetic
timbre. So the renderer needs its own EQ stage. This module is that stage's filter
engine: second-order-section (biquad) parametric filters matching the **RBJ Audio
EQ Cookbook** exactly as EqualizerAPO implements it — the *semantics* the sibling
project (`binaural_localization.eqapo`) already validated offline, ported here as a
small, dependency-light, **live block-processor** (the sibling's engine is an
offline impulse reconstructor and pulls heavy deps, so we reuse the math, not the
package — see journal 13's depend-vs-port note).

What it provides:

* `BiquadSpec` + `parse_filter_line` — read EqualizerAPO ``Filter:`` lines.
* `design` / `design_sos` — RBJ coefficients (PK/LS/HS/LP/HP/NO/AP), ``a0``-
  normalised, ready for `scipy.signal.sosfilt`. Byte-for-byte the sibling's formulas
  so our EQ == EqualizerAPO == the sibling's offline reconstruction.
* `BiquadCascade` — a **stateful streaming** cascade: feeding it block by block
  equals filtering the whole signal at once (continuous ``zi`` state, no
  inter-block clicks), the real-time counterpart of an offline `sosfilt`.
* `ParametricEQ` — one cascade per channel over a multichannel block (use it for
  the per-channel 7.1 input EQ *and* for the stereo output-bus EQ, FR-8), with a
  **glitch-free live update** (`set_specs`): a short linear crossfade between the
  old and new filters so the EQ can be retuned mid-stream without a click.

Pure numpy/scipy, unit-tested headless.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
from scipy.signal import sosfilt

#: Shelf slope used for a bare LS/HS with no Q/BW/slope given (the one case the
#: EqualizerAPO wiki leaves unpinned; the sibling uses the same S=1 convention).
DEFAULT_SHELF_S = 1.0


@dataclass
class BiquadSpec:
    """A parsed EqualizerAPO filter line."""

    ftype: str  # normalised type: PK, LS, HS, LP, HP, NO, AP
    fc: float  # centre / corner frequency in Hz
    gain_db: float = 0.0
    q: float | None = None
    bw_oct: float | None = None
    slope_db: float | None = None  # for LSC/HSC "x dB"


_FILTER_RE = re.compile(
    r"""Filter \s* \d* \s* : \s* (?P<state>ON|OFF) \s+ (?P<rest>.*)""",
    re.IGNORECASE | re.VERBOSE,
)


def parse_filter_line(line: str) -> BiquadSpec | None:
    """Parse one ``Filter: ...`` line into a :class:`BiquadSpec` (or ``None``).

    Returns ``None`` for ``OFF`` filters and for lines that are not filters. Matches
    EqualizerAPO's ``Filter: ON PK Fc 1000 Hz Gain 3 dB Q 1`` / ``BW Oct`` /
    ``LS 6 dB`` syntax, including the numbered ``Filter  1:`` REW form.
    """
    m = _FILTER_RE.match(line.strip())
    if not m or m.group("state").upper() != "ON":
        return None
    rest = m.group("rest")

    type_m = re.match(r"\s*(?P<t>[A-Za-z]+)\s*(?P<slope>[-\d.]+\s*dB)?", rest)
    if type_m is None:
        return None
    ftype = type_m.group("t").upper()
    slope_db = None
    if type_m.group("slope"):
        slope_db = float(re.sub(r"\s*dB", "", type_m.group("slope")))

    def grab(key: str) -> float | None:
        mm = re.search(key + r"\s+(-?\d+(?:\.\d+)?)", rest, re.IGNORECASE)
        return float(mm.group(1)) if mm else None

    fc = grab(r"Fc")
    gain = grab(r"Gain") or 0.0
    q = grab(r"Q")
    bw = grab(r"BW\s*Oct")
    if fc is None:
        return None

    aliases = {"PEQ": "PK", "MODAL": "PK", "LSC": "LS", "HSC": "HS", "LPQ": "LP", "HPQ": "HP"}
    ftype = aliases.get(ftype, ftype)
    return BiquadSpec(ftype=ftype, fc=fc, gain_db=gain, q=q, bw_oct=bw, slope_db=slope_db)


def _alpha(w0: float, spec: BiquadSpec, a_amp: float) -> float:
    """Cookbook ``alpha`` from Q, BW-octaves, or (for shelves) slope/S."""
    sin_w0 = math.sin(w0)
    if spec.q is not None:
        return sin_w0 / (2.0 * spec.q)
    if spec.bw_oct is not None:
        return sin_w0 * math.sinh(math.log(2.0) / 2.0 * spec.bw_oct * w0 / sin_w0)
    if spec.ftype in ("LS", "HS"):
        if spec.slope_db is not None and spec.slope_db > 0:
            s = min(1.0, spec.slope_db / 12.0)
        else:
            s = DEFAULT_SHELF_S
        return sin_w0 / 2.0 * math.sqrt((a_amp + 1.0 / a_amp) * (1.0 / s - 1.0) + 2.0)
    # Non-shelf type with no Q/BW given → Butterworth Q = 1/sqrt(2).
    return sin_w0 / (2.0 * (1.0 / math.sqrt(2.0)))


def design(spec: BiquadSpec, fs: float) -> np.ndarray:
    """Return one SOS row ``[b0, b1, b2, a0, a1, a2]`` (normalised so ``a0 = 1``).

    RBJ Audio EQ Cookbook, as EqualizerAPO implements it — identical to the sibling
    `binaural_localization.eqapo.biquad.design`.
    """
    w0 = 2.0 * math.pi * spec.fc / fs
    cos_w0 = math.cos(w0)
    a_amp = 10.0 ** (spec.gain_db / 40.0)
    alpha = _alpha(w0, spec, a_amp)
    t = spec.ftype

    if t == "PK":
        b0, b1, b2 = 1 + alpha * a_amp, -2 * cos_w0, 1 - alpha * a_amp
        a0, a1, a2 = 1 + alpha / a_amp, -2 * cos_w0, 1 - alpha / a_amp
    elif t == "LS":
        tsa = 2 * math.sqrt(a_amp) * alpha
        b0 = a_amp * ((a_amp + 1) - (a_amp - 1) * cos_w0 + tsa)
        b1 = 2 * a_amp * ((a_amp - 1) - (a_amp + 1) * cos_w0)
        b2 = a_amp * ((a_amp + 1) - (a_amp - 1) * cos_w0 - tsa)
        a0 = (a_amp + 1) + (a_amp - 1) * cos_w0 + tsa
        a1 = -2 * ((a_amp - 1) + (a_amp + 1) * cos_w0)
        a2 = (a_amp + 1) + (a_amp - 1) * cos_w0 - tsa
    elif t == "HS":
        tsa = 2 * math.sqrt(a_amp) * alpha
        b0 = a_amp * ((a_amp + 1) + (a_amp - 1) * cos_w0 + tsa)
        b1 = -2 * a_amp * ((a_amp - 1) + (a_amp + 1) * cos_w0)
        b2 = a_amp * ((a_amp + 1) + (a_amp - 1) * cos_w0 - tsa)
        a0 = (a_amp + 1) - (a_amp - 1) * cos_w0 + tsa
        a1 = 2 * ((a_amp - 1) - (a_amp + 1) * cos_w0)
        a2 = (a_amp + 1) - (a_amp - 1) * cos_w0 - tsa
    elif t == "LP":
        b0, b1, b2 = (1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2
        a0, a1, a2 = 1 + alpha, -2 * cos_w0, 1 - alpha
    elif t == "HP":
        b0, b1, b2 = (1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2
        a0, a1, a2 = 1 + alpha, -2 * cos_w0, 1 - alpha
    elif t == "NO":
        b0, b1, b2 = 1, -2 * cos_w0, 1
        a0, a1, a2 = 1 + alpha, -2 * cos_w0, 1 - alpha
    elif t == "AP":
        b0, b1, b2 = 1 - alpha, -2 * cos_w0, 1 + alpha
        a0, a1, a2 = 1 + alpha, -2 * cos_w0, 1 - alpha
    else:
        raise ValueError(f"Unsupported filter type: {t}")

    sos = np.array([b0, b1, b2, a0, a1, a2], dtype=float)
    return sos / a0


def design_sos(specs: list[BiquadSpec], fs: float) -> np.ndarray:
    """Stack several biquads into an ``(n, 6)`` SOS array for ``scipy.signal.sosfilt``."""
    if not specs:
        return np.empty((0, 6))
    return np.stack([design(s, fs) for s in specs], axis=0)


class BiquadCascade:
    """A stateful streaming biquad cascade over one signal.

    Feeding it block by block yields exactly the same output as filtering the whole
    signal at once (the filter state ``zi`` carries across blocks), so there are no
    clicks at block boundaries — the real-time counterpart of a single
    `scipy.signal.sosfilt`. An empty cascade is a pass-through.
    """

    def __init__(self, sos: np.ndarray) -> None:
        sos = np.asarray(sos, dtype=float).reshape(-1, 6)
        self._sos = sos
        self._zi = self._fresh_zi()

    def _fresh_zi(self) -> np.ndarray | None:
        if self._sos.shape[0] == 0:
            return None
        return np.zeros((self._sos.shape[0], 2))

    @property
    def n_sections(self) -> int:
        return int(self._sos.shape[0])

    def reset(self) -> None:
        """Clear the filter memory (next block starts from silence)."""
        self._zi = self._fresh_zi()

    def process_block(self, x: np.ndarray) -> np.ndarray:
        """Filter one 1-D block, advancing the retained state."""
        x = np.asarray(x, dtype=float)
        if self._sos.shape[0] == 0:
            return x.copy()
        y, self._zi = sosfilt(self._sos, x, zi=self._zi)
        return y

    @classmethod
    def from_specs(cls, specs: list[BiquadSpec], fs: float) -> BiquadCascade:
        return cls(design_sos(specs, fs))


class ParametricEQ:
    """Per-channel biquad EQ over a multichannel block, with glitch-free retuning.

    One :class:`BiquadCascade` per channel. Use a single instance per channel group:
    an 8-channel instance for the per-channel 7.1 input EQ, a 2-channel instance for
    the stereo output-bus EQ (FR-8). ``process_block`` takes ``(M, C)`` and returns
    ``(M, C)``.

    :meth:`set_specs` swaps the filters live. To avoid a click from the abrupt
    coefficient/state change it runs the outgoing and new cascades in parallel for
    ``crossfade`` samples and fades (linear amplitude ramp) outgoing→new; after the
    window only the new cascade remains. A second ``set_specs`` *during* a fade is
    also click-free: the current blend is frozen as the new fade's outgoing group
    (so the output is continuous across the second swap). ``crossfade=0`` swaps hard
    (for tests / offline use).
    """

    # A fade (per channel) is (outgoing, remaining, total): `outgoing` is a list of
    # (cascade, weight) whose weighted sum reproduces the signal that was being
    # output when the fade began; it fades out as (1-g) while the new cascade fades
    # in as g, over `total` samples.

    def __init__(self, channel_specs: list[list[BiquadSpec]], fs: float) -> None:
        self.fs = float(fs)
        self._n = len(channel_specs)
        self._cascades = [BiquadCascade.from_specs(s, fs) for s in channel_specs]
        self._fade: list[tuple[list[tuple[BiquadCascade, float]], int, int] | None]
        self._fade = [None] * self._n

    @property
    def n_channels(self) -> int:
        return self._n

    def reset(self) -> None:
        for c in self._cascades:
            c.reset()
        self._fade = [None] * self._n

    def set_specs(
        self, channel_specs: list[list[BiquadSpec]], crossfade: int = 2048
    ) -> None:
        """Replace every channel's filters, crossfading over ``crossfade`` samples."""
        if len(channel_specs) != self._n:
            raise ValueError(f"expected {self._n} channels, got {len(channel_specs)}")
        new = [BiquadCascade.from_specs(s, self.fs) for s in channel_specs]
        if crossfade <= 0:
            self._cascades = new
            self._fade = [None] * self._n
            return
        for ch in range(self._n):
            prev = self._fade[ch]
            if prev is None:
                outgoing = [(self._cascades[ch], 1.0)]
            else:
                out_list, remaining, total = prev
                # Gain the previous fade would apply to its *new* cascade at the next
                # sample; freeze the current blend = (1-g)*out_list + g*current.
                g = min(1.0, (total - remaining) / total)
                outgoing = [(c, w * (1.0 - g)) for c, w in out_list]
                outgoing.append((self._cascades[ch], g))
            self._fade[ch] = (outgoing, crossfade, crossfade)
        self._cascades = new

    def process_block(self, block: np.ndarray) -> np.ndarray:
        """Filter one ``(M, C)`` block → ``(M, C)``, advancing state and any fades."""
        block = np.asarray(block, dtype=float)
        if block.ndim != 2 or block.shape[1] != self._n:
            raise ValueError(f"block must be (M, {self._n}); got {block.shape}")
        out = np.empty_like(block)
        for ch in range(self._n):
            x = block[:, ch]
            y_new = self._cascades[ch].process_block(x)
            fade = self._fade[ch]
            if fade is None:
                out[:, ch] = y_new
                continue
            outgoing, remaining, total = fade
            # Advance every outgoing cascade (keep its state in sync with x) and sum
            # with the frozen weights → the signal that was playing when the fade began.
            y_out = sum(w * c.process_block(x) for c, w in outgoing)
            m = x.shape[0]
            # Linear new-gain ramp 0→1; this block covers samples [total-remaining, +m).
            start = total - remaining
            g_new = np.clip(np.arange(start, start + m, dtype=float) / total, 0.0, 1.0)
            out[:, ch] = (1.0 - g_new) * y_out + g_new * y_new
            remaining -= m
            self._fade[ch] = None if remaining <= 0 else (outgoing, remaining, total)
        return out

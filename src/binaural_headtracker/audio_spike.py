"""Portable helpers for the B0.2 audio-I/O spike.

The hardware-touching code lives in ``spikes/b0_2_audio_io.py`` and runs on the
Windows host (real WASAPI) driven through WSL interop; everything in *this*
module is pure and portable so it is unit-tested on any platform — including the
headless WSL/CI box that has no audio device and no ``sounddevice`` installed.

Two jobs:

* generate a deterministic probe signal and recover its delay from a captured
  recording (cross-correlation) — the measured round-trip latency; and
* render the device/host-API inventory as a readable table from plain dicts
  (so the formatting is testable without PortAudio).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def log_sweep(
    samplerate: float,
    duration_s: float = 0.05,
    f0: float = 500.0,
    f1: float = 8000.0,
) -> np.ndarray:
    """Return a mono float32 exponential sine sweep (great autocorrelation peak).

    A short swept sine is used as the latency probe: its sharp, unique
    autocorrelation peak makes the delay unambiguous under noise, unlike a pure
    tone (periodic) or a single click (fragile to any band-limiting).
    """
    n = int(round(samplerate * duration_s))
    if n <= 1:
        raise ValueError("duration too short for the sample rate")
    t = np.arange(n) / float(samplerate)
    k = (f1 / f0) ** (1.0 / t[-1])
    phase = 2.0 * np.pi * f0 * (k**t - 1.0) / np.log(k)
    return (0.9 * np.sin(phase)).astype(np.float32)


def estimate_lag_samples(reference: Sequence[float], captured: Sequence[float]) -> int:
    """Delay (in samples) of ``captured`` relative to ``reference`` via FFT xcorr.

    Assumes ``captured`` contains a (possibly noisy, scaled) copy of
    ``reference`` starting ``lag`` samples in. Returns that ``lag`` (>= 0).
    """
    r = np.asarray(reference, dtype=np.float64)
    c = np.asarray(captured, dtype=np.float64)
    if r.size == 0 or c.size == 0:
        raise ValueError("reference and captured must be non-empty")
    nfft = 1 << int(np.ceil(np.log2(r.size + c.size - 1)))
    corr = np.fft.irfft(np.fft.rfft(c, nfft) * np.conj(np.fft.rfft(r, nfft)), nfft)
    # Only non-negative lags where the whole reference still fits in the capture.
    search = corr[: max(c.size - r.size + 1, 1)]
    return int(np.argmax(search))


def samples_to_ms(n_samples: int, samplerate: float) -> float:
    """Convert a sample count to milliseconds at ``samplerate``."""
    if samplerate <= 0:
        raise ValueError("samplerate must be positive")
    return 1000.0 * n_samples / float(samplerate)


def format_device_table(
    devices: Sequence[Mapping],
    hostapis: Sequence[Mapping],
) -> str:
    """Render a PortAudio device inventory as a fixed-width table.

    ``devices`` and ``hostapis`` are plain dicts shaped like the output of
    ``sounddevice.query_devices()`` / ``query_hostapis()`` so the Windows spike
    can hand its findings here and the layout is unit-tested without PortAudio.
    """
    header = f"{'idx':>3}  {'in':>2} {'out':>3}  {'rate':>7}  {'host':<22} name"
    lines = [header, "-" * len(header)]
    for i, d in enumerate(devices):
        host_idx = d.get("hostapi", -1)
        host = hostapis[host_idx]["name"] if 0 <= host_idx < len(hostapis) else "?"
        lines.append(
            f"{i:>3}  {d.get('max_input_channels', 0):>2} "
            f"{d.get('max_output_channels', 0):>3}  "
            f"{float(d.get('default_samplerate', 0)):>7.0f}  "
            f"{host:<22} {d.get('name', '?')}"
        )
    return "\n".join(lines)

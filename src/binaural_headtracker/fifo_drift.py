"""A0 diagnostic: is the output-FIFO wander **clock skew** or **jitter/burst**? (RFC Track A)

The live ~70 ms floor is dominated by the 31 ms ``queue`` term — the standing depth of the
output FIFO (journal 48). The RFC's Track A asks the question that decides *how* to attack it:

- **Clock skew** — the VoiceMeeter capture clock and the K11 output clock run at slightly
  different rates, so the FIFO fill **ramps** (one side out-produces the other). A skew shows up
  as a sustained *slope* in fill-vs-time. The fix is an async drift-compensating resampler/servo
  in our own user-mode bridge (RFC A1): hold the fill at a small target by trimming a few
  samples/s. This is cheap and needs **no kernel**.
- **Jitter / burst pacing** — the capture delivers audio in bursts, so the fill *bounces* around
  a level without a consistent trend. A resampler cannot remove this (it keeps VoiceMeeter and
  its burst delivery); the lever is the capture path itself, which is what justifies the custom
  virtual device (RFC Track B).

So the ratio of *trend* to *bounce* in the FIFO fill series is the make-or-no-decision for A1.

This module is the **pure, portable analysis** — it takes a fill-vs-time series (captured on the
host by ``voicemeeter_passthrough --log-fifo-fill``) and returns a :class:`FifoDriftReport`
classifying the dominant term, with the signed skew in ppm and the detrended jitter in ms. The
maths is unit-tested headless with synthetic series (no audio, no camera, no clock); the *data*
it analyses must be captured on the Windows host with the real K11 (the skew only exists between
real hardware clocks — see the RFC's empirical-validation note).

CLI::

    python -m binaural_headtracker.fifo_drift fifo_fill.csv --samplerate 48000

reads a two-column ``time_s,fill_frames`` CSV (header optional) and prints the report.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

# Classification thresholds (overridable). Chosen so a term below ~1 ms of audio is "clean"
# (inaudible, below the cushion's own resolution), and the skew/jitter split follows how much of
# the fill's variance the linear trend explains.
DEFAULT_RELEVANCE_FLOOR_MS = 1.0  # below this, a term is negligible
DEFAULT_SKEW_FRACTION = 0.60      # trend explains ≥60 % of variance ⇒ skew dominates
DEFAULT_JITTER_FRACTION = 0.30    # trend explains ≤30 % ⇒ jitter dominates; between ⇒ mixed
_MIN_SAMPLES = 8                  # fewer than this ⇒ "insufficient" (a slope is meaningless)


@dataclass(frozen=True)
class FifoDriftReport:
    """Classification of an output-FIFO fill-vs-time series. ``n < _MIN_SAMPLES`` ⇒ insufficient.

    All millisecond fields are in audio-time (frames ÷ samplerate). ``skew_ppm`` is **signed**:
    positive = fill growing (capture out-produces drain), negative = fill draining (the FIFO
    heads toward underrun). ``dominant`` is one of ``skew`` / ``jitter`` / ``mixed`` / ``clean``
    / ``insufficient``.
    """

    n: int
    span_s: float
    samplerate: int
    skew_ppm: float          # signed rate mismatch, parts per million
    skew_drift_ms: float     # |skew| displacement accumulated over the window, ms
    jitter_rms_ms: float     # detrended fill RMS, ms
    trend_fraction: float    # 0..1: fraction of fill variance explained by the linear trend
    mean_fill_ms: float      # average standing FIFO depth over the window, ms
    dominant: str

    @property
    def recommendation(self) -> str:
        """The RFC Track-A reading of this report, in one line."""
        if self.dominant == "insufficient":
            return "Insufficient data — capture a longer run (≥ a few seconds of steady playback)."
        if self.dominant == "clean":
            return ("Fill is already steady — the queue term is cushion-sizing, not drift; a "
                    "resampler buys little. Re-check the cushion/block choice (journal 48).")
        if self.dominant == "skew":
            return (f"Skew dominates ({self.skew_ppm:+.0f} ppm). An async drift-compensating "
                    "resampler/servo (RFC A1) removes it with ~inaudible added latency, no kernel.")
        if self.dominant == "jitter":
            return ("Jitter/burst dominates — a resampler cannot fix it (it keeps VoiceMeeter's "
                    "burst delivery). This is the evidence that justifies the custom device "
                    "(Track B).")
        return ("Mixed skew + jitter. Start with the A1 resampler for the skew part; the residual "
                "jitter is the Track-B case. Re-measure after A1 to size the residual.")


def analyze_fill(
    times_s: np.ndarray | list[float],
    fill_frames: np.ndarray | list[float],
    samplerate: int,
    *,
    relevance_floor_ms: float = DEFAULT_RELEVANCE_FLOOR_MS,
    skew_fraction: float = DEFAULT_SKEW_FRACTION,
    jitter_fraction: float = DEFAULT_JITTER_FRACTION,
) -> FifoDriftReport:
    """Classify an output-FIFO fill-vs-time series as skew- or jitter-dominated.

    ``times_s`` are monotonic timestamps (seconds); ``fill_frames`` is the FIFO depth in frames
    at each timestamp; ``samplerate`` converts frames to audio-time. The series need not be evenly
    sampled (a least-squares line handles uneven spacing). Non-finite rows are dropped.
    """
    if samplerate <= 0:
        raise ValueError("samplerate must be positive")
    t = np.asarray(times_s, dtype=np.float64).ravel()
    f = np.asarray(fill_frames, dtype=np.float64).ravel()
    if t.shape != f.shape:
        raise ValueError("times_s and fill_frames must be the same length")
    good = np.isfinite(t) & np.isfinite(f)
    t, f = t[good], f[good]

    sr = int(samplerate)
    n = int(t.size)
    span = float(t[-1] - t[0]) if n >= 2 else 0.0
    mean_fill_ms = float(f.mean()) / sr * 1000.0 if n else 0.0

    if n < _MIN_SAMPLES or span <= 0.0:
        return FifoDriftReport(n, span, sr, 0.0, 0.0, 0.0, 0.0, mean_fill_ms, "insufficient")

    # Least-squares line fill ≈ slope·t + intercept. Shift time to t0 for conditioning.
    t0 = t - t[0]
    slope, intercept = np.polyfit(t0, f, 1)  # frames/s, frames
    residual = f - (slope * t0 + intercept)

    skew_ppm = float(slope) / sr * 1e6               # frames/s ÷ (frames/s full-rate) · 1e6
    skew_drift_ms = abs(float(slope)) * span / sr * 1000.0
    jitter_rms_ms = float(np.sqrt(np.mean(residual**2))) / sr * 1000.0

    total_var = float(np.var(f))
    resid_var = float(np.var(residual))
    # Fraction of the fill's wander explained by the linear trend (R²-like, clamped to [0, 1]).
    trend_fraction = 0.0 if total_var <= 1e-12 else max(0.0, min(1.0, 1.0 - resid_var / total_var))

    if skew_drift_ms < relevance_floor_ms and jitter_rms_ms < relevance_floor_ms:
        dominant = "clean"
    elif trend_fraction >= skew_fraction:
        dominant = "skew"
    elif trend_fraction <= jitter_fraction:
        dominant = "jitter"
    else:
        dominant = "mixed"

    return FifoDriftReport(
        n, span, sr, skew_ppm, skew_drift_ms, jitter_rms_ms, trend_fraction, mean_fill_ms, dominant
    )


def _read_csv(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Read a two-column ``time_s,fill_frames`` CSV, tolerating one header row."""
    rows: list[tuple[float, float]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) < 2:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue  # header or junk row
    if not rows:
        raise ValueError(f"no numeric time,fill rows found in {path}")
    arr = np.array(rows, dtype=np.float64)
    return arr[:, 0], arr[:, 1]


def _format(report: FifoDriftReport) -> str:
    r = report
    return (
        f"A0 FIFO-drift report  ({r.n} samples over {r.span_s:.1f} s @ {r.samplerate} Hz)\n"
        f"  mean fill      : {r.mean_fill_ms:6.1f} ms (standing queue depth)\n"
        f"  skew           : {r.skew_ppm:+8.1f} ppm  ->  {r.skew_drift_ms:6.1f} ms drift / window\n"
        f"  jitter (RMS)   : {r.jitter_rms_ms:6.1f} ms\n"
        f"  trend fraction : {r.trend_fraction:6.2f}  (1 = pure skew, 0 = pure jitter)\n"
        f"  DOMINANT       : {r.dominant.upper()}\n"
        f"  → {r.recommendation}"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="A0: classify output-FIFO fill as skew vs jitter.")
    p.add_argument("csv", help="two-column time_s,fill_frames CSV from --log-fifo-fill")
    p.add_argument("--samplerate", type=int, default=48000, help="stream rate (Hz), default 48000")
    args = p.parse_args(argv)
    times, fill = _read_csv(args.csv)
    print(_format(analyze_fill(times, fill, args.samplerate)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

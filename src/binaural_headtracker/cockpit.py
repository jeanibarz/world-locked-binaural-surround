"""Portable view-model for the Dear PyGui **cockpit** console (B6.12).

A second host front-end, chosen from the round-2 GUI mockups: a native
`Dear PyGui <https://github.com/hoffstadt/DearPyGui>`_ window (MIT) organised around the one
number every recent block chased — the **head-move-to-ear latency** — with its live
distribution, its stale/queue/device component split, a world-locked radar, a camera feed, and
*realtime* engine controls (interpolation method + CPU/Rust/GPU backend). The OpenCV console
(:mod:`apps._console`) stays the default and is untouched; this is additive.

This module is the **pure, headless** half: it turns the numbers the live apps already hold — a
:class:`~binaural_headtracker.diagnostics.Diagnosis` plus the raw latency samples behind it —
into a :class:`CockpitView` of ready-to-draw primitives (a latency headline, a verdict pill, the
component waterfall, a binned histogram, status bars, readout chips). No Dear PyGui, no camera,
no audio, no clock here, so it is unit-tested with no display; the drawing shell that consumes a
:class:`CockpitView` lives in ``apps/cockpit_view.py`` and imports Dear PyGui lazily, exactly as
:mod:`apps._console` imports ``cv2``.

Colours are the project's canonical console palette (the same hexes as ``apps/_console.py`` and
the HTML mockup), expressed here as RGBA 0–255 tuples because that is what Dear PyGui draws with.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from binaural_headtracker.diagnostics import Diagnosis, Health

# ---------------------------------------------------------------- palette (RGBA 0–255)

RGBA = tuple[int, int, int, int]


def _hex(s: str, a: int = 255) -> RGBA:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), a)


BG = _hex("0d1017")
PANEL = _hex("151a24")
PANEL2 = _hex("1b2230")
LINE = _hex("2a3344")
FG = _hex("e8edf5")
MUTED = _hex("8b97ab")
FAINT = _hex("64708a")
ACCENT = _hex("38d6d6")   # tracking cyan
ACTIVE = _hex("f6b24b")   # active speaker / recenter amber
GOOD = _hex("54d98c")
RISK = _hex("e8820e")     # orange
BAD = _hex("f2545b")      # failing red

HEALTH_COLOR: dict[Health, RGBA] = {
    Health.OK: GOOD,
    Health.TIGHT: ACTIVE,
    Health.RISK: RISK,
    Health.FAILING: BAD,
}

# A short verdict label for the headline pill (the Diagnosis carries the long form in reasons).
_VERDICT_LABEL: dict[Health, str] = {
    Health.OK: "GOOD",
    Health.TIGHT: "TIGHT",
    Health.RISK: "AT RISK",
    Health.FAILING: "FAILING",
}


def health_color(h: Health) -> RGBA:
    """RGBA for a real-time :class:`Health` verdict."""
    return HEALTH_COLOR[h]


# ---------------------------------------------------------------- latency histogram

def histogram_bin_edges(lo_ms: float, hi_ms: float, bins: int) -> list[float]:
    """The ``bins + 1`` evenly-spaced edges spanning ``[lo_ms, hi_ms]`` (ms)."""
    if bins <= 0:
        raise ValueError("bins must be positive")
    if not hi_ms > lo_ms:
        raise ValueError("hi_ms must exceed lo_ms")
    step = (hi_ms - lo_ms) / bins
    return [lo_ms + i * step for i in range(bins + 1)]


def latency_histogram(
    samples: Iterable[float], *, lo_ms: float = 0.0, hi_ms: float = 100.0, bins: int = 24
) -> list[int]:
    """Bin latency ``samples`` (ms) into ``bins`` counts over ``[lo_ms, hi_ms]``.

    Everything below ``lo_ms`` falls in the first bin and everything at or above ``hi_ms`` in the
    last, so a stray spike shows as a tail bar instead of vanishing. Non-finite samples are
    dropped. An empty input yields all-zero counts (a flat, drawable histogram).
    """
    if bins <= 0:
        raise ValueError("bins must be positive")
    if not hi_ms > lo_ms:
        raise ValueError("hi_ms must exceed lo_ms")
    counts = [0] * bins
    step = (hi_ms - lo_ms) / bins
    for raw in samples:
        v = float(raw)
        if not math.isfinite(v):
            continue
        idx = int((v - lo_ms) / step)
        idx = 0 if idx < 0 else bins - 1 if idx >= bins else idx
        counts[idx] += 1
    return counts


def _nice_upper_ms(diag: Diagnosis, samples: tuple[float, ...]) -> float:
    """A tidy upper bound for the histogram x-axis: round the worst value we expect to see up to
    the next 20 ms, with an 80 ms floor so the common sub-50 ms case keeps a stable, readable
    axis instead of rescaling every frame."""
    worst = max(
        [diag.pose_age_p99_ms, diag.end_to_end_ms, (max(samples) if samples else 0.0)]
    )
    return max(80.0, math.ceil(worst / 20.0) * 20.0)


# ---------------------------------------------------------------- view primitives

@dataclass(frozen=True)
class LatencyComponent:
    """One slice of the stale/queue/device latency waterfall."""

    label: str
    ms: float
    color: RGBA


@dataclass(frozen=True)
class StatBar:
    """A labelled meter: ``frac`` fills the bar, optional ``peak`` draws a tick."""

    label: str
    frac: float
    color: RGBA
    text: str
    peak: float | None = None


@dataclass(frozen=True)
class Readout:
    """A bottom-bar chip: ``label`` in muted, ``value`` in ``color``."""

    label: str
    value: str
    color: RGBA


@dataclass(frozen=True)
class CockpitView:
    """Everything the Dear PyGui cockpit draws for one frame (pure data)."""

    # latency headline
    headline_ms: str
    measured: bool
    verdict: str
    verdict_color: RGBA
    detail: str                      # "p95 … · p99 … · n=…"  or  "estimate"
    components: tuple[LatencyComponent, ...]
    # distribution
    histogram: tuple[int, ...]
    hist_lo_ms: float
    hist_hi_ms: float
    # engine / health
    bars: tuple[StatBar, ...]
    render_label: str
    dropouts_text: str
    dropouts_color: RGBA
    tracker_text: str
    # realtime engine controls (current state the view reflects)
    method: str
    backend: str                     # "cpu" | "rust" | "gpu"
    # bottom readout chips
    readouts: tuple[Readout, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)


def build_view(
    diag: Diagnosis,
    *,
    latency_samples: Iterable[float] = (),
    rate_hz: float = 0.0,
    method: str = "nearest",
    backend: str = "rust",
    predict_ms: float | None = None,
    facing_label: str = "",
    flipped: bool = False,
    hist_bins: int = 24,
) -> CockpitView:
    """Compose a :class:`CockpitView` from a live :class:`Diagnosis` and its latency samples.

    ``latency_samples`` are the raw pose-age-at-ear measurements (ms) from the app's
    :class:`~binaural_headtracker.latency.LatencyProbe` (``probe.samples()``); they drive the
    histogram. ``backend`` is the active render engine (``cpu``/``rust``/``gpu``) and ``method``
    the interpolation method — both shown so the realtime switches reflect the running config.
    """
    samples = tuple(float(s) for s in latency_samples)
    hi = _nice_upper_ms(diag, samples)
    histogram = tuple(latency_histogram(samples, lo_ms=0.0, hi_ms=hi, bins=hist_bins))

    measured = diag.end_to_end_measured and diag.pose_age_n > 0
    if measured:
        detail = (f"p95 {diag.pose_age_p95_ms:.0f} · p99 {diag.pose_age_p99_ms:.0f} ms"
                  f" · n={diag.pose_age_n}")
        components = (
            LatencyComponent("stale", diag.pose_age_stale_ms, BAD),
            LatencyComponent("queue", diag.pose_age_queue_ms, ACTIVE),
            LatencyComponent("device", diag.pose_age_device_ms, ACCENT),
        )
    else:
        detail = "estimate (no measured samples yet)"
        components = ()

    load = diag.render_load
    load_peak = diag.render_load_peak
    buf_frac = (diag.fifo_fill_blocks / (2 * diag.cushion_blocks)) if diag.cushion_blocks else 0.0
    buf_ok = diag.fifo_fill_blocks >= 1.0
    bars = (
        StatBar("render load", load, health_color(diag.health),
                f"{load * 100:.0f}% (pk {load_peak * 100:.0f}%) · {diag.block_ms:.1f} ms/blk",
                peak=load_peak),
        StatBar("out buffer", buf_frac, GOOD if buf_ok else BAD,
                f"{diag.fifo_fill_blocks:.0f}/{diag.cushion_blocks} blk",
                peak=None),
    )

    dropped = diag.recent_underruns + diag.recent_overflows
    if dropped:
        parts = []
        if diag.recent_underruns:
            parts.append(f"{diag.recent_underruns} underrun")
        if diag.recent_overflows:
            parts.append(f"{diag.recent_overflows} overflow")
        dropouts_text = " + ".join(parts)
        dropouts_color = BAD
    else:
        dropouts_text = "none"
        dropouts_color = GOOD

    if diag.detect_ms > 0:
        tracker_text = f"{rate_hz:.0f} Hz · detect {diag.detect_ms:.0f} ms"
    else:
        tracker_text = f"{rate_hz:.0f} Hz"
    if diag.cam_w and diag.cam_h:
        tracker_text += f" · {diag.cam_w}×{diag.cam_h}"
        if diag.camera_fps:
            tracker_text += f"@{diag.camera_fps:.0f}"

    readouts = [
        Readout("rate", f"{rate_hz:.0f} Hz", GOOD if rate_hz >= 20 else ACTIVE),
        Readout("lat", f"~{diag.end_to_end_ms:.0f} ms", health_color(diag.health)),
    ]
    if facing_label:
        readouts.append(
            Readout("facing", facing_label + ("  [flip]" if flipped else ""), ACTIVE))
    if predict_ms is not None:
        readouts.append(Readout("predict", f"{predict_ms:.0f} ms", GOOD))

    return CockpitView(
        headline_ms=f"{diag.end_to_end_ms:.0f}",
        measured=measured,
        verdict=_VERDICT_LABEL[diag.health],
        verdict_color=health_color(diag.health),
        detail=detail,
        components=components,
        histogram=histogram,
        hist_lo_ms=0.0,
        hist_hi_ms=hi,
        bars=bars,
        render_label=diag.render_label,
        dropouts_text=dropouts_text,
        dropouts_color=dropouts_color,
        tracker_text=tracker_text,
        method=method,
        backend=backend,
        readouts=tuple(readouts),
        reasons=diag.reasons[:2],
    )

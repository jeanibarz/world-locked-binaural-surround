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


def backend_switch_note(
    requested: str, actual: str, reason: str | None = None
) -> tuple[str, str] | None:
    """Decide the toast for a backend switch, or ``None`` when the request was honored cleanly.

    ``requested`` is what the user picked (cpu/rust/gpu); ``actual`` is the engine the renderer the
    app actually built runs on. ``reason`` is the failure detail when a requested accelerator was
    unavailable (else ``None``). Returns ``(message, kind)`` where kind is ``"error"`` for a genuine
    unavailability fallback and ``"info"`` for an intentional remap (e.g. a ``gpu`` request maps to
    rust for synthesis methods, which have no GPU kernel). The caller snaps the selector to
    ``actual`` regardless; this only governs whether — and how — to surface a banner.
    """
    if actual == requested:
        return None
    if reason:
        return (f"{requested.upper()} unavailable — running {actual.upper()}. {reason}", "error")
    return (f"{requested.upper()} maps to {actual.upper()} for this method", "info")


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
class DropoutStrip:
    """A 60-second history of output glitches for the latency panel's dropout tick strip.

    ``overs``/``unders`` are per-bin event counts across ``window_s`` (oldest bin first, newest
    last — so the strip reads left→right = past→now, like a timeline). A bin with any overflow
    draws a red tick; any underrun, an amber tick. ``total_over``/``total_under`` are the counts
    within the window. Unlike :class:`~binaural_headtracker.diagnostics.Diagnosis`'s 2-second
    ``recent_*`` counts (which flash and vanish), this keeps the whole window so a slow
    clock-drift trend is visible instead of a blink.
    """

    overs: tuple[int, ...]
    unders: tuple[int, ...]
    total_over: int
    total_under: int
    window_s: float


def dropout_strip(
    over_ts: Iterable[float],
    under_ts: Iterable[float],
    *,
    now: float,
    window_s: float = 60.0,
    bins: int = 30,
) -> DropoutStrip:
    """Bin overflow/underrun timestamps into a :class:`DropoutStrip` over ``[now-window, now]``.

    ``over_ts``/``under_ts`` are monotonic-clock timestamps (seconds) of recent glitches — the raw
    ``over_ts``/``under_ts`` deques the live app records. Events older than ``window_s`` or in the
    future are ignored. Bin 0 is the oldest slice, bin ``bins-1`` the most recent, so drawing the
    bins left→right yields a past→now timeline.
    """
    if bins <= 0:
        raise ValueError("bins must be positive")
    if not window_s > 0:
        raise ValueError("window_s must be positive")
    overs = [0] * bins
    unders = [0] * bins
    step = window_s / bins

    def _accumulate(timestamps: Iterable[float], counts: list[int]) -> int:
        total = 0
        for raw in timestamps:
            age = now - float(raw)
            if age < 0.0 or age >= window_s:
                continue
            # newest (age≈0) → last bin; oldest (age≈window) → first bin
            idx = bins - 1 - int(age / step)
            idx = 0 if idx < 0 else bins - 1 if idx >= bins else idx
            counts[idx] += 1
            total += 1
        return total

    total_over = _accumulate(over_ts, overs)
    total_under = _accumulate(under_ts, unders)
    return DropoutStrip(tuple(overs), tuple(unders), total_over, total_under, float(window_s))


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
    # 60 s overflow/underrun history for the latency panel's tick strip (None → no strip drawn)
    dropouts: DropoutStrip | None = None


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
    dropout_over_ts: Iterable[float] = (),
    dropout_under_ts: Iterable[float] = (),
    now: float | None = None,
    dropout_window_s: float = 60.0,
    dropout_bins: int = 30,
) -> CockpitView:
    """Compose a :class:`CockpitView` from a live :class:`Diagnosis` and its latency samples.

    ``latency_samples`` are the raw pose-age-at-ear measurements (ms) from the app's
    :class:`~binaural_headtracker.latency.LatencyProbe` (``probe.samples()``); they drive the
    histogram. ``backend`` is the active render engine (``cpu``/``rust``/``gpu``) and ``method``
    the interpolation method — both shown so the realtime switches reflect the running config.
    """
    samples = tuple(float(s) for s in latency_samples)
    # 60 s dropout history for the panel tick strip — only when the caller passes a clock (``now``);
    # the headless unit tests and any caller that doesn't track timestamps simply get ``None``.
    dropouts = (
        dropout_strip(dropout_over_ts, dropout_under_ts, now=now,
                      window_s=dropout_window_s, bins=dropout_bins)
        if now is not None else None
    )
    hi = _nice_upper_ms(diag, samples)
    histogram = tuple(latency_histogram(samples, lo_ms=0.0, hi_ms=hi, bins=hist_bins))

    measured = diag.end_to_end_measured and diag.pose_age_n > 0
    if measured:
        detail = (f"p95 {diag.pose_age_p95_ms:.0f} · p99 {diag.pose_age_p99_ms:.0f} ms"
                  f" · n={diag.pose_age_n}")
        components = (
            # camera front (estimated, hence MUTED) + the three measured parts — together these
            # now sum to the head-move-to-ear headline, so the waterfall adds up.
            LatencyComponent("capture", diag.pose_interval_ms, MUTED),
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
        dropouts=dropouts,
    )

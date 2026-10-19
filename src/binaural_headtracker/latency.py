"""Latency measurement: a pose age → sound distribution, not a mean (B8.5).

The world-lock quality that the ear notices is **how old the head pose is at the moment the
sound carrying it reaches the ear** — the "pose age at the ear". Until now the GUI only
*estimated* it (``1.5 × frame interval`` for the camera stage + the audio buffers). This module
is the measurement: the live apps timestamp every pose (``HeadPose.t``) and, when a rendered block
is pushed toward the output, record how long ago that pose was captured plus the audio still
queued ahead of it. :class:`LatencyProbe` accumulates those samples and reports the **distribution**
— p50/p95/p99 and the extremes — because a rare 100 ms spike hurts a world-locked scene far more
than a good mean hides (requirements § 7.3, journal 36). It also carries the pose timestamp seam
that the B8.6 orientation predictor needs (predict forward by the measured delay).

Pure and headless: the apps feed it millisecond samples; the maths here is unit-tested with no
audio, no camera, no clock.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LatencyStats:
    """A summary of recorded latency samples (all milliseconds). ``n == 0`` ⇒ no data yet."""

    n: int
    p50: float
    p95: float
    p99: float
    min: float
    max: float
    mean: float

    @property
    def empty(self) -> bool:
        return self.n == 0


class LatencyProbe:
    """Rolling accumulator of latency samples → a :class:`LatencyStats` distribution.

    Keeps the last ``window`` samples (a few seconds at audio-block rate) so the reported
    distribution tracks the *current* behaviour rather than the whole run. ``add`` is cheap
    (one deque append) so it is safe to call from the render worker every block; ``stats``
    does the percentile reduction only when the GUI asks. Not internally locked — the apps
    call ``add`` from one thread (the worker) and ``stats`` from another (the GUI); a deque's
    append/iterate are each atomic under CPython, and a slightly stale snapshot is harmless
    for a read-out, so no lock is taken on the hot path.
    """

    def __init__(self, window: int = 600) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self._samples: deque[float] = deque(maxlen=int(window))

    def add(self, ms: float) -> None:
        """Record one latency sample (ms). Non-finite or negative values are ignored."""
        v = float(ms)
        if np.isfinite(v) and v >= 0.0:
            self._samples.append(v)

    def reset(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    def samples(self) -> tuple[float, ...]:
        """A snapshot of the current window's raw samples (ms), oldest first.

        For a histogram/sparkline that wants the distribution's *shape*, not just the
        percentiles. Like :meth:`stats`, this is a cheap atomic snapshot of the deque, so the
        GUI thread may call it while the worker appends — a slightly stale copy is harmless.
        """
        return tuple(self._samples)

    def stats(self) -> LatencyStats:
        """Reduce the current window to a distribution. Empty ⇒ all-zero stats, ``n == 0``."""
        snap = np.fromiter(tuple(self._samples), dtype=np.float64)
        if snap.size == 0:
            return LatencyStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        p50, p95, p99 = (float(x) for x in np.percentile(snap, (50, 95, 99)))
        return LatencyStats(
            n=int(snap.size), p50=p50, p95=p95, p99=p99,
            min=float(snap.min()), max=float(snap.max()), mean=float(snap.mean()),
        )

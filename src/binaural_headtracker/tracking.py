"""Head-pose tracker: a smoothed (yaw, pitch, roll) stream on its own thread (B2.1).

The render engine (M3) needs a *steady, low-latency* head pose, updated continuously
off the audio thread. This module is the portable, device-free core of that:

* :class:`OneEuroFilter` — the Casiez et al. 1€ filter, the standard
  latency-vs-jitter smoother. Its two knobs are the trade-off: ``min_cutoff`` sets
  the floor smoothing (lower = steadier but laggier at rest), ``beta`` sets how fast
  the cutoff opens up with motion (higher = less lag on fast turns, a bit more
  jitter). Exposed so M3/B2.2 can tune latency vs jitter.
* :class:`HeadPose` — one ``(yaw, pitch, roll)`` sample with a timestamp (seconds).
* :class:`PoseFilter` — a 1€ filter per angle, smoothing a pose stream.
* :class:`HeadTracker` — pulls raw poses from an arbitrary **source** iterator on a
  background thread, smooths them, and publishes the latest pose + update-rate/jitter
  stats. The source is abstracted so a **recorded clip** drives it headless in tests
  while the live **MediaPipe webcam** source lives in ``spikes/`` (host/vision only).

Angles are in degrees; within the ±45° operating range (requirements FR-4) there is
no wrap, so each angle is filtered directly. Pure stdlib + numpy; unit-tested with no
camera, no MediaPipe, no real sleeps (timing is driven by each sample's timestamp).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace

import numpy as np

DEFAULT_RATE_HZ = 30.0  # fallback dt for the first sample / non-increasing timestamps
STATS_WINDOW = 600  # keep only the last N sample timestamps (~ seconds at 30–120 Hz)
                    # so a long-lived/live source doesn't grow memory and stats stay
                    # a *recent* rate/jitter, not a drifting lifetime average.


@dataclass(frozen=True)
class HeadPose:
    """A head orientation sample: Euler angles (deg) at time ``t`` (seconds)."""

    yaw: float
    pitch: float
    roll: float
    t: float


def _alpha(cutoff: float, dt: float) -> float:
    """1€ smoothing factor for a given cutoff (Hz) and time step (s)."""
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """Scalar 1€ filter (Casiez, Roussel & Vogel 2012).

    ``min_cutoff`` (Hz) is the baseline low-pass; ``beta`` raises the cutoff with the
    signal's speed so fast motion is not over-smoothed; ``d_cutoff`` (Hz) low-passes
    the derivative. Lower ``min_cutoff`` / lower ``beta`` ⇒ steadier but laggier.
    """

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0) -> None:
        if min_cutoff <= 0 or d_cutoff <= 0:
            raise ValueError("min_cutoff and d_cutoff must be > 0")
        if beta < 0:
            raise ValueError("beta must be >= 0 (a negative beta can drive the cutoff < 0)")
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.reset()

    def reset(self) -> None:
        self._x_prev: float | None = None
        self._dx_prev = 0.0
        self._t_prev = 0.0

    def filter(self, x: float, t: float) -> float:
        x = float(x)
        if self._x_prev is None:  # first sample: pass through, seed state
            self._x_prev, self._t_prev, self._dx_prev = x, float(t), 0.0
            return x
        dt = float(t) - self._t_prev
        if dt <= 0.0:
            dt = 1.0 / DEFAULT_RATE_HZ
        dx = (x - self._x_prev) / dt
        edx = self._dx_prev + _alpha(self.d_cutoff, dt) * (dx - self._dx_prev)
        cutoff = self.min_cutoff + self.beta * abs(edx)
        x_hat = self._x_prev + _alpha(cutoff, dt) * (x - self._x_prev)
        self._x_prev, self._dx_prev, self._t_prev = x_hat, edx, float(t)
        return x_hat


class PoseFilter:
    """A 1€ filter per Euler angle, smoothing a :class:`HeadPose` stream."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.0, d_cutoff: float = 1.0) -> None:
        self._f = {k: OneEuroFilter(min_cutoff, beta, d_cutoff) for k in ("yaw", "pitch", "roll")}

    def reset(self) -> None:
        for f in self._f.values():
            f.reset()

    def update(self, pose: HeadPose) -> HeadPose:
        return replace(
            pose,
            yaw=self._f["yaw"].filter(pose.yaw, pose.t),
            pitch=self._f["pitch"].filter(pose.pitch, pose.t),
            roll=self._f["roll"].filter(pose.roll, pose.t),
        )


class HeadTracker:
    """Run a raw-pose ``source`` on a background thread → smoothed latest pose + stats.

    ``source`` is any iterable of raw :class:`HeadPose` (a recorded clip for tests, a
    webcam→MediaPipe generator on the host). The worker smooths each sample with a
    :class:`PoseFilter` and publishes it; :meth:`latest` reads it (thread-safe).
    Update-rate / jitter :meth:`stats` are derived from the samples' own timestamps,
    so they are deterministic and independent of processing speed.
    """

    def __init__(
        self,
        source: Iterable[HeadPose],
        *,
        min_cutoff: float = 1.0,
        beta: float = 0.0,
        d_cutoff: float = 1.0,
    ) -> None:
        self._source = source
        self._filter = PoseFilter(min_cutoff, beta, d_cutoff)
        self._lock = threading.Lock()
        self._latest: HeadPose | None = None
        self._times: deque[float] = deque(maxlen=STATS_WINDOW)  # recent sample times
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> HeadTracker:
        if self._thread is not None:
            raise RuntimeError("tracker already started")
        self._running = True
        self._thread = threading.Thread(target=self._run, name="head-tracker", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        it: Iterator[HeadPose] = iter(self._source)
        while self._running:
            try:
                raw = next(it)
            except StopIteration:
                break
            smoothed = self._filter.update(raw)
            with self._lock:
                self._latest = smoothed
                self._times.append(raw.t)
        self._running = False

    def latest(self) -> HeadPose | None:
        """The most recent smoothed pose (or ``None`` before the first sample)."""
        with self._lock:
            return self._latest

    def stats(self) -> dict:
        """Update-rate and jitter over the last :data:`STATS_WINDOW` samples."""
        with self._lock:
            times = list(self._times)
        if len(times) < 2:
            return {"n": len(times), "rate_hz": 0.0, "mean_interval_ms": 0.0, "jitter_ms": 0.0}
        dt = np.diff(times)
        mean = float(np.mean(dt))
        return {
            "n": len(times),
            "rate_hz": 1.0 / mean if mean > 0 else 0.0,
            "mean_interval_ms": mean * 1000.0,
            "jitter_ms": float(np.std(dt)) * 1000.0,
        }

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the worker finishes (source exhausted); ``True`` if it did."""
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def stop(self, timeout: float = 1.0) -> None:
        """Signal the worker to stop and join it (for infinite/live sources).

        The worker re-checks the stop flag each loop, but it cannot interrupt a
        *blocked* ``next(source)`` (e.g. a stalled camera read): in that case the
        thread keeps waiting until the source returns, and ``stop`` returns after
        ``timeout`` with the thread still alive. It is ``daemon=True``, so the
        process can still exit. Once stopped, a tracker is not restartable.
        """
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout)

    def __enter__(self) -> HeadTracker:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

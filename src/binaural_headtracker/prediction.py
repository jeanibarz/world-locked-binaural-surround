"""Orientation prediction to the audio-render instant (B8.6).

The measured latency split (journal 46) is ~40 ms of irreducible output buffer plus **~40 ms of
camera pose staleness** — the pose is that old by the time the sound carrying it reaches the ear.
No buffer tweak touches that term; the only lever without a faster camera is to **predict** where
the head *will be* when the audio is heard and render with that pose, so a head turn is reflected
on time instead of a frame-and-a-buffer late.

This module is the predictor: a per-Euler-angle **α-β tracker** (position + angular velocity) that
smooths the noisy pose stream and extrapolates each angle forward by a horizon (the measured
pose-age-at-ear). Head motion is yaw-dominated and the horizon is short (~tens of ms), so
per-angle linear extrapolation is enough — no quaternion machinery — while a bounded delta keeps an
abrupt stop from overshooting the fine static resolution (the ~1° MAA; journal 36). Pure and
headless: fed `(angle, t)` samples, unit-tested with no camera.

Why α-β and not the 1€ filter the tracker already uses: 1€ *smooths* (it has no forward model), α-β
carries an explicit velocity state, which is exactly what extrapolation needs. The tracker keeps
its 1€ smoothing; this runs on top for the look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

from binaural_headtracker.tracking import DEFAULT_RATE_HZ, HeadPose


class AlphaBetaPredictor:
    """Scalar α-β tracker: smooth a `(value, t)` stream and extrapolate it forward.

    ``alpha`` corrects position from each residual, ``beta`` corrects velocity; higher = more
    responsive but noisier (more jitter fed into the velocity, so into the prediction). Defaults
    are a moderate tracking pair. ``predict`` extrapolates ``value + velocity · horizon``, with the
    step clamped to ``max_delta`` so a sudden stop (velocity not yet decayed) cannot fling the
    estimate far past the real angle.
    """

    def __init__(self, alpha: float = 0.35, beta: float = 0.08) -> None:
        if not (0.0 < alpha <= 1.0):
            raise ValueError("alpha must be in (0, 1]")
        if beta < 0.0:
            raise ValueError("beta must be >= 0")
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.reset()

    def reset(self) -> None:
        self._x: float | None = None   # smoothed position (deg)
        self._v = 0.0                  # velocity (deg/s)
        self._t = 0.0

    @property
    def velocity(self) -> float:
        return self._v

    def update(self, value: float, t: float) -> None:
        value = float(value)
        if self._x is None:  # first sample: seed, no velocity yet
            self._x, self._v, self._t = value, 0.0, float(t)
            return
        dt = float(t) - self._t
        if dt <= 0.0:
            dt = 1.0 / DEFAULT_RATE_HZ
        x_pred = self._x + self._v * dt
        residual = value - x_pred
        self._x = x_pred + self.alpha * residual
        self._v += (self.beta / dt) * residual
        self._t = float(t)

    def predict(self, horizon_s: float, max_delta: float | None = None) -> float:
        """Value extrapolated ``horizon_s`` into the future (clamped to ±``max_delta``)."""
        if self._x is None:
            return 0.0
        step = self._v * max(0.0, horizon_s)
        if max_delta is not None:
            step = max(-max_delta, min(max_delta, step))
        return self._x + step


@dataclass
class PredictorConfig:
    alpha: float = 0.35
    # beta was 0.08: the velocity state then decayed too slowly on a dead stop, so the look-ahead
    # lingered ~35 deg past the real angle after a fast turn (overshoot). 0.20 corrects velocity
    # faster so the overshoot settles quickly, at a small cost in jitter sensitivity.
    beta: float = 0.20
    # max_horizon_ms was 120: it tracked the full head-move-to-ear latency, which the FIFO cushion
    # inflates, pinning the horizon at the cap. Peak overshoot ~= turn-speed x horizon, so 80 ms
    # roughly halves it while still compensating the irreducible camera+device staleness.
    max_horizon_ms: float = 80.0    # never extrapolate beyond this, however large the estimate
    max_rate_dps: float = 300.0     # bound the step at this angular speed × horizon (overshoot)


class PosePredictor:
    """Predict a whole :class:`HeadPose` forward by a horizon — one α-β tracker per Euler angle.

    Feed it the (smoothed) pose stream with :meth:`update`; :meth:`predict` returns a pose whose
    yaw/pitch/roll are extrapolated by ``horizon_ms`` (clamped to ``max_horizon_ms``), with each
    step bounded by ``max_rate_dps × horizon`` so a stop doesn't overshoot. The returned pose keeps
    the source timestamp; only the angles look ahead. Horizon ≤ 0 returns the smoothed current pose
    (prediction disabled), so the same path serves the predictor-off case.
    """

    def __init__(self, config: PredictorConfig | None = None) -> None:
        self.cfg = config or PredictorConfig()
        self._yaw = AlphaBetaPredictor(self.cfg.alpha, self.cfg.beta)
        self._pitch = AlphaBetaPredictor(self.cfg.alpha, self.cfg.beta)
        self._roll = AlphaBetaPredictor(self.cfg.alpha, self.cfg.beta)

    def reset(self) -> None:
        for p in (self._yaw, self._pitch, self._roll):
            p.reset()

    def update(self, pose: HeadPose) -> None:
        self._yaw.update(pose.yaw, pose.t)
        self._pitch.update(pose.pitch, pose.t)
        self._roll.update(pose.roll, pose.t)

    def predict(self, pose: HeadPose, horizon_ms: float) -> HeadPose:
        """Return ``pose`` with its angles extrapolated forward by ``horizon_ms``."""
        h = min(max(0.0, horizon_ms), self.cfg.max_horizon_ms) / 1000.0
        md = self.cfg.max_rate_dps * h  # the furthest a plausible head turn moves in the horizon
        return HeadPose(
            yaw=self._yaw.predict(h, md),
            pitch=self._pitch.predict(h, md),
            roll=self._roll.predict(h, md),
            t=pose.t,
        )

"""Discover and load dense HRIR-grid sets for live switching (RFC 0003).

The sibling ``binaural-localization`` project emits **dense horizontal HRIR grids**
as SOFA files — a matched database subject's real measured ring, recoloured to the
owner's own timbre — so the head-tracked renderer can move smoothly with plain
``nearest`` lookup instead of snapping across the owner's 8 sparse points or
flipping with synthesis.

This module is the consumer seam:

- :func:`load_dense_grid_sofa` reads one such SOFA into the ``(BakedBRIRSet,
  azimuths)`` pair that :class:`live_render.InterpWorldLocked7p1` takes as its
  ``dense_override`` (and :class:`live_render.WorldLockedSource` takes as
  ``azimuths=``).
- :func:`discover_sets` lists the switchable sets found in a directory, with the
  owner's own measured set first (``path=None`` → render from the sparse 8 via the
  usual interpolation path).

Pure h5py/numpy, unit-tested headless — no Dear PyGui, no audio.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from binaural_headtracker.surround import BakedBRIRSet

OWNER_LABEL = "mine"


def _wrap180(a: float) -> float:
    return (float(a) + 180.0) % 360.0 - 180.0


def load_dense_grid_sofa(
    path, target_rate: int | None = None
) -> tuple[BakedBRIRSet, dict[int, float]]:
    """Load a horizontal dense-grid SOFA (``SimpleFreeFieldHRIR``, elevation 0) into
    a ``(BakedBRIRSet, azimuths)`` pair.

    Rows are sorted by azimuth; ``azimuths`` maps each channel index → its azimuth
    wrapped to ``(-180, 180]`` (the convention :class:`WorldLockedSource` /
    :class:`InterpWorldLocked7p1` steer in). ``target_rate`` resamples if given.
    """
    import h5py

    p = Path(path)
    with h5py.File(p, "r") as h:
        ir = np.asarray(h["Data.IR"], dtype=np.float64)          # (M, 2, N)
        pos = np.asarray(h["SourcePosition"], dtype=np.float64)  # (M, 3)
        kind = h["SourcePosition"].attrs.get("Type", b"spherical")
        try:
            fs = int(round(float(np.asarray(h["Data.SamplingRate"]).ravel()[0])))
        except Exception:  # noqa: BLE001 — missing/odd rate dataset
            fs = 48000
    if ir.ndim != 3 or ir.shape[1] != 2:
        raise ValueError(f"expected Data.IR (M,2,N); got {ir.shape}")

    kind = kind if isinstance(kind, bytes) else str(kind).encode()
    if kind == b"cartesian":
        x, y = pos[:, 0], pos[:, 1]
        az = np.degrees(np.arctan2(y, x)) % 360.0
    else:
        az = pos[:, 0] % 360.0

    order = np.argsort(az)
    az = az[order]
    ir = ir[order]
    left = np.ascontiguousarray(ir[:, 0, :])
    right = np.ascontiguousarray(ir[:, 1, :])

    if target_rate is not None and target_rate != fs:
        from math import gcd

        from scipy.signal import resample_poly
        g = gcd(int(fs), int(target_rate))
        up, down = int(target_rate) // g, int(fs) // g
        left = resample_poly(left, up, down, axis=1)
        right = resample_poly(right, up, down, axis=1)
        fs = int(target_rate)

    channels = tuple(f"A{int(round(a)) % 360:03d}" for a in az)
    brir = BakedBRIRSet(channels=channels, left=left, right=right, samplerate=fs)
    azimuths = {i: _wrap180(a) for i, a in enumerate(az)}
    return brir, azimuths


@dataclass(frozen=True)
class HrirSet:
    """A switchable HRIR set: ``path is None`` means the owner's own measured set
    (rendered from the sparse 8 via the normal interpolation path)."""

    name: str
    path: Path | None

    @property
    def is_owner(self) -> bool:
        return self.path is None


@dataclass
class BlindAB:
    """Pure state machine for a 2-alternative blind HRIR comparison (RFC 0003 decider).

    Two sets sit behind sealed slots **A** and **B**; the listener hears a slot
    without knowing which set it is, states which they prefer, and the mapping is
    revealed only at the end. ``shuffle`` randomises (and re-hides) the slot→set
    assignment. The GUI is a thin driver: it calls :meth:`play` (then switches the
    renderer to the returned set name) and :meth:`prefer_current`; the preference
    tally is kept per *set*, so it survives shuffles.
    """

    set_a: str
    set_b: str
    _map: dict[str, str] = field(default_factory=dict)
    current: str | None = None          # the slot currently playing ('A'/'B')
    revealed: bool = False
    tally: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._map = {"A": self.set_a, "B": self.set_b}
        self.tally = {self.set_a: 0, self.set_b: 0}

    def shuffle(self, rng: random.Random | None = None) -> None:
        """Randomly (re)assign the two sets to slots A/B and re-hide the mapping."""
        r = rng or random
        if r.random() < 0.5:
            self._map = {"A": self.set_a, "B": self.set_b}
        else:
            self._map = {"A": self.set_b, "B": self.set_a}
        self.current = None
        self.revealed = False

    def set_name_for(self, slot: str) -> str:
        return self._map[slot]

    def play(self, slot: str) -> str:
        """Mark ``slot`` ('A'/'B') as playing and return the (hidden) set to render."""
        if slot not in ("A", "B"):
            raise ValueError("slot must be 'A' or 'B'")
        self.current = slot
        return self._map[slot]

    def prefer_current(self) -> str | None:
        """Record a preference for whatever is playing; returns its set name (or None)."""
        if self.current is None:
            return None
        name = self._map[self.current]
        self.tally[name] = self.tally.get(name, 0) + 1
        return name

    def reveal(self) -> dict[str, str]:
        self.revealed = True
        return dict(self._map)

    @property
    def trials(self) -> int:
        return sum(self.tally.values())

    def label(self, slot: str) -> str:
        """What to show on a slot's button: the slot letter while sealed, the set once revealed."""
        return self._map[slot] if self.revealed else slot


def _ang_err_deg(a: float, b: float) -> float:
    """Absolute wrapped angular distance between two azimuths, in [0, 180]."""
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


def _fb_mirror_deg(az: float) -> float:
    """Front/back mirror of an azimuth — reflect across the interaural (L–R) axis."""
    return (180.0 - float(az)) % 360.0


def _is_fb_confusion(truth: float, guess: float) -> bool:
    """True when the guess is nearer the front/back *mirror* of the truth than the
    truth itself — heard on the right side but flipped front↔back (the back-gap error)."""
    return _ang_err_deg(guess, _fb_mirror_deg(truth)) < _ang_err_deg(guess, truth)


@dataclass
class LocTrial:
    set_name: str
    truth_az: float
    guess_az: float | None = None
    rt_s: float | None = None

    @property
    def answered(self) -> bool:
        return self.guess_az is not None

    @property
    def abs_err_deg(self) -> float | None:
        return None if self.guess_az is None else _ang_err_deg(self.truth_az, self.guess_az)

    @property
    def fb_confusion(self) -> bool:
        return self.guess_az is not None and _is_fb_confusion(self.truth_az, self.guess_az)


@dataclass
class LocalizationTest:
    """Blind absolute-localization (azimuth-pointing) test over several HRIR sets.

    Each trial renders a source at a random azimuth through a (hidden) set; the
    listener points where they hear it and the error is scored against truth. Faster
    + more accurate ⇒ better set. Objective, so it can't be gamed by expectation —
    the per-set scoreboard is the placebo-proof decider the blind A/B only hints at.

    Pure state + scoring (no audio/UI): the app renders the current trial's set at
    its ``truth_az``, feeds back the pointed azimuth + response time via
    :meth:`answer`, and shows :meth:`scoreboard` when :attr:`done`.
    """

    sets: list[str]
    n_per_set: int = 25
    trials: list[LocTrial] = field(default_factory=list)
    i: int = 0

    @classmethod
    def build(cls, sets, n_per_set: int = 25, *, rng: random.Random | None = None,
              azimuths: list[float] | None = None) -> LocalizationTest:
        """Build a shuffled, set-interleaved trial list. ``azimuths`` is an optional pool
        of candidate truth angles (else uniform random over the full circle)."""
        r = rng or random
        trials: list[LocTrial] = []
        for s in sets:
            for _ in range(int(n_per_set)):
                az = float(r.choice(azimuths)) if azimuths else r.uniform(0.0, 360.0)
                trials.append(LocTrial(s, az))
        r.shuffle(trials)
        return cls(list(sets), int(n_per_set), trials, 0)

    @property
    def done(self) -> bool:
        return self.i >= len(self.trials)

    @property
    def total(self) -> int:
        return len(self.trials)

    @property
    def current(self) -> LocTrial | None:
        return None if self.done else self.trials[self.i]

    def answer(self, guess_az: float, rt_s: float | None = None) -> None:
        """Record the pointed azimuth (+ optional response time) for the current trial
        and advance. No-op once done."""
        t = self.current
        if t is None:
            return
        t.guess_az = float(guess_az)
        t.rt_s = None if rt_s is None else float(rt_s)
        self.i += 1

    def scoreboard(self) -> dict[str, dict]:
        """Per-set metrics over answered trials: ``n``, ``mae`` (mean abs azimuth error,
        deg), ``fbc`` (front/back confusion rate), ``rt`` (median response time, s)."""
        out: dict[str, dict] = {}
        for s in self.sets:
            ts = [t for t in self.trials if t.set_name == s and t.answered]
            if not ts:
                continue
            rts = [t.rt_s for t in ts if t.rt_s is not None]
            out[s] = {
                "n": len(ts),
                "mae": float(np.mean([t.abs_err_deg for t in ts])),
                "fbc": float(np.mean([1.0 if t.fb_confusion else 0.0 for t in ts])),
                "rt": float(np.median(rts)) if rts else None,
            }
        return out

    def ranking(self) -> list[str]:
        """Sets ordered best→worst by mean error, then median response time."""
        sb = self.scoreboard()
        return sorted(sb, key=lambda s: (sb[s]["mae"], sb[s]["rt"] if sb[s]["rt"] else 0.0))


def discover_sets(directory, *, pattern: str = "dense_*.sofa",
                  owner_label: str = OWNER_LABEL) -> list[HrirSet]:
    """List switchable HRIR sets: the owner's own set first, then every dense-grid
    SOFA in ``directory`` (named by its file stem, ``dense_`` prefix stripped),
    sorted by name. A missing directory yields just the owner's set."""
    sets: list[HrirSet] = [HrirSet(owner_label, None)]
    if directory is not None:
        d = Path(directory)
        if d.is_dir():
            for p in sorted(d.glob(pattern)):
                name = p.stem
                if name.startswith("dense_"):
                    name = name[len("dense_"):]
                sets.append(HrirSet(name, p))
    return sets

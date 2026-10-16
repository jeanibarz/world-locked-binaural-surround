"""Resolve the sibling project's trained localization **judge** models (B7 glue).

The sibling ``binaural-localization`` trains direction judges (keras models) and,
once validated, publishes them to a shared **model registry** so this project can
pick up the *best validated* one for calibration/evaluation (M7, FR-10/11) without
guessing which ``saved_model/*.keras`` is current. This module is the consumer
side of the registry contract — deliberately **pure-python** (json + pathlib, no
TensorFlow), so importing it costs nothing: it only *locates* a model and returns
its path + metadata; whatever actually loads the keras model (an eval script) runs
in the sibling's environment with that path.

Registry contract (v1) — a shared, persistent, non-versioned directory (default
``/mnt/d/binaural-models/registry``, override with ``$JUDGE_MODEL_REGISTRY``; the
same env var binaural-localization honours when it publishes) laid out as::

    registry/
      index.json                 {"best": "<name>", "models": [...], "updated_utc": ...}
      <name>/
        model.keras              the trained judge
        metadata.json            name, databases, mixed_rig, metrics{...}, accepted, ...
        README.md                human summary
        plots/                   accuracy figures

``best_judge()`` returns the registry's declared best; with no ``index.json`` best
it falls back to the accepted model with the lowest ``cross_rig_qe_pct``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

#: Metric used to rank models when ``index.json`` declares no explicit "best".
RANK_METRIC = "cross_rig_qe_pct"


#: Shared, persistent, non-versioned registry both repos publish to / read from
#: (a 4 TB drive outside any git worktree, so it survives branch/worktree switches).
DEFAULT_REGISTRY = Path("/mnt/d/binaural-models/registry")

#: Env var that overrides the location (honoured by this repo AND binaural-localization).
REGISTRY_ENV = "JUDGE_MODEL_REGISTRY"


def registry_dir() -> Path:
    """The model-registry directory: ``$JUDGE_MODEL_REGISTRY`` or :data:`DEFAULT_REGISTRY`."""
    env = os.environ.get(REGISTRY_ENV)
    return Path(env).expanduser() if env else DEFAULT_REGISTRY


@dataclass(frozen=True)
class JudgeModel:
    """A resolved registry entry: where the model is + what is known about it."""

    name: str
    model_path: Path  # the .keras file (may be large / git-ignored but on disk)
    metadata: dict
    directory: Path

    @property
    def exists(self) -> bool:
        return self.model_path.is_file()

    @property
    def metric(self) -> float | None:
        v = self.metadata.get("metrics", {}).get(RANK_METRIC)
        return float(v) if isinstance(v, (int, float)) else None


def _require_registry(registry: Path | None) -> Path:
    reg = registry or registry_dir()
    if not reg.is_dir():
        raise FileNotFoundError(
            f"judge registry not found at {reg}. The sibling binaural-localization "
            f"must publish validated judges there (see the registry contract), or set "
            f"$JUDGE_MODEL_REGISTRY."
        )
    return reg


def list_judges(registry: Path | None = None) -> list[str]:
    """Names of registered judges (sub-directories with a ``metadata.json``)."""
    reg = _require_registry(registry)
    return sorted(
        p.name for p in reg.iterdir() if p.is_dir() and (p / "metadata.json").is_file()
    )


def get_judge(name: str, registry: Path | None = None) -> JudgeModel:
    """Resolve one registered judge by name (reads its ``metadata.json``)."""
    reg = _require_registry(registry)
    d = reg / name
    meta_path = d / "metadata.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"no registered judge {name!r} in {reg}")
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    model_file = metadata.get("model_file", "model.keras")
    return JudgeModel(name=name, model_path=d / model_file, metadata=metadata, directory=d)


def best_judge(registry: Path | None = None) -> JudgeModel:
    """The registry's best validated judge.

    Prefers ``index.json``'s ``"best"``; otherwise the *accepted* model with the
    lowest :data:`RANK_METRIC`. Raises if the registry has no usable model.
    """
    reg = _require_registry(registry)
    index = reg / "index.json"
    if index.is_file():
        best = json.loads(index.read_text(encoding="utf-8")).get("best")
        if best:
            return get_judge(best, reg)
    candidates = [get_judge(n, reg) for n in list_judges(reg)]
    accepted = [j for j in candidates if j.metadata.get("accepted") and j.metric is not None]
    if not accepted:
        raise LookupError(
            f"registry {reg} declares no 'best' in index.json and has no accepted model "
            f"with a '{RANK_METRIC}' metric to rank."
        )
    return min(accepted, key=lambda j: j.metric)

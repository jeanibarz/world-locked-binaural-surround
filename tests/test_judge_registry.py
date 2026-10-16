"""Unit tests for the judge-model-registry consumer (pure-python, no TF/registry).

A mock registry is built in a tmp dir so the resolver is tested headless, with no
dependency on the sibling project actually having published a model yet.
"""

from __future__ import annotations

import json

import pytest

from binaural_headtracker import judge_registry as jr


def _write_model(reg, name, *, cross_rig_qe, accepted=True, make_keras=True):
    d = reg / name
    (d / "plots").mkdir(parents=True)
    meta = {
        "name": name,
        "databases": ["HUTUBS", "CIPIC", "ARI", "RIEC", "LISTEN"],
        "mixed_rig": True,
        "metrics": {"cross_rig_qe_pct": cross_rig_qe, "angular_err_deg": 40.0},
        "accepted": accepted,
    }
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    if make_keras:
        (d / "model.keras").write_bytes(b"\x00")  # placeholder; resolver never loads it
    return d


def _registry(tmp_path, best=None, models=None):
    reg = tmp_path / "registry"
    reg.mkdir()
    if best is not None or models is not None:
        idx = {"best": best, "models": models or [], "updated_utc": "2026-10-16T00:00:00Z"}
        (reg / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    return reg


def test_list_and_get(tmp_path):
    reg = _registry(tmp_path)
    _write_model(reg, "pooled_v1", cross_rig_qe=30.0)
    _write_model(reg, "hutubs96", cross_rig_qe=45.0)
    assert jr.list_judges(reg) == ["hutubs96", "pooled_v1"]
    j = jr.get_judge("pooled_v1", reg)
    assert j.name == "pooled_v1"
    assert j.model_path == reg / "pooled_v1" / "model.keras"
    assert j.exists and j.metric == 30.0
    assert j.metadata["mixed_rig"] is True


def test_best_prefers_index_json(tmp_path):
    reg = _registry(tmp_path, best="hutubs96", models=["pooled_v1", "hutubs96"])
    _write_model(reg, "pooled_v1", cross_rig_qe=30.0)
    _write_model(reg, "hutubs96", cross_rig_qe=45.0)  # worse metric, but declared best
    assert jr.best_judge(reg).name == "hutubs96"  # explicit declaration wins


def test_best_falls_back_to_metric_when_no_index_best(tmp_path):
    reg = _registry(tmp_path)  # no index.json
    _write_model(reg, "pooled_v1", cross_rig_qe=30.0)
    _write_model(reg, "hutubs96", cross_rig_qe=45.0)
    assert jr.best_judge(reg).name == "pooled_v1"  # lowest cross_rig_qe among accepted


def test_best_ignores_unaccepted_in_fallback(tmp_path):
    reg = _registry(tmp_path)
    # best metric but not accepted → must be ignored by the fallback ranking
    _write_model(reg, "experimental", cross_rig_qe=10.0, accepted=False)
    _write_model(reg, "pooled_v1", cross_rig_qe=30.0, accepted=True)
    assert jr.best_judge(reg).name == "pooled_v1"


def test_missing_registry_raises_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="registry not found"):
        jr.list_judges(tmp_path / "nope")


def test_env_override(tmp_path, monkeypatch):
    reg = _registry(tmp_path)
    _write_model(reg, "pooled_v1", cross_rig_qe=30.0)
    monkeypatch.setenv("JUDGE_MODEL_REGISTRY", str(reg))
    assert jr.registry_dir() == reg
    assert jr.best_judge().name == "pooled_v1"  # uses the env registry


def test_empty_registry_best_raises(tmp_path):
    reg = _registry(tmp_path)  # exists but no models
    with pytest.raises(LookupError):
        jr.best_judge(reg)

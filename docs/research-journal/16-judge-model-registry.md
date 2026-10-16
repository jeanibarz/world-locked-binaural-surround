# 16 — Judge-model registry: a contract for picking the best validated judge (B7.0)

**Date:** 2026-10-16  ·  **Milestone:** M7  ·  **Status:** done (consumer side)

## Question

M7 scores candidate HRIR/EQ/interpolation choices with the sibling's trained
localization **judge**. But `binaural-localization/saved_model/` holds a dozen
ad-hoc `*.keras` files with no provenance or metrics — when we needed "the best
mixed-rig judge" to score interpolation methods, we couldn't tell which one it was
(`production_hutubs96` is HUTUBS single-rig; the pooled/DANN work that entry 24
found "finally helps" saved only figures, not a model). Guessing a model gives
confidently-wrong scores. How do we always get the *best validated* judge?

## Decision

A shared **model registry** with provenance, so selection is never a guess
(owner request). Contract v1 (also in `judge_registry`'s docstring):

- Location: `../binaural-localization/models/registry/` (both repos on one disk;
  override with `$BHT_JUDGE_REGISTRY`).
- `registry/<name>/` holds `model.keras` + `metadata.json` (databases/rigs,
  `mixed_rig`, `metrics{cross_rig_qe_pct, angular_err_deg, front_back_flip_pct…}`,
  `accepted`, training provenance) + `README.md` + `plots/`.
- `registry/index.json` names the canonical `"best"`.
- A model is published only once **trained AND validated**.

Split of work: the **sibling owns publishing** (it has the data, the training env,
and the validation protocol) — delegated to its task, which also (re)trains and
registers the pooled mixed-rig judge per entry 24. **This project owns consuming.**

## Method

`binaural_headtracker.judge_registry` — deliberately **pure-python** (json +
pathlib, no TensorFlow), so importing it is free and it never pulls the sibling's
heavy ML deps. It only *locates* a model: `registry_dir()`, `list_judges()`,
`get_judge(name)`, and `best_judge()` → a `JudgeModel(name, model_path, metadata,
directory)`. `best_judge()` prefers `index.json`'s `"best"`, else falls back to the
accepted model with the lowest `cross_rig_qe_pct`. Whatever loads the keras model
(the offline scoring harness) runs in the sibling env with the returned path.

## Result

B7.0 **done** (consumer side). 7 tests on a mock registry (list/get, index-best vs
metric-fallback, unaccepted-excluded, env override, helpful errors when the
registry is absent/empty). Full suite green, ruff clean. It degrades gracefully:
until the sibling publishes, `best_judge()` raises a clear, actionable error.

## Decision / next

- The judge-selection guesswork is gone: `judge_registry.best_judge()` is the one
  way to pick the judge, for B7.1 and for the interpolation evaluation.
- **Pending the sibling** (delegated task): implement the registry publish side and
  register the pooled mixed-rig judge as `"best"`. Once it lands, re-run the
  interpolation comparison (timealign/minphase/notchwarp/nearest) through *that*
  judge — the lens the owner actually trained and trusts.
- Then **B7.1**: wire `best_judge()` into an in-repo scoring entrypoint.
(→ L37)

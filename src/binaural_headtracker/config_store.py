"""Safe settings persistence for the cockpit — RFC ``rfc-crash-safe-settings`` (v2).

Design is **prevent at apply/load time, not recover after a crash** (the review falsified the
crash-recovery premise). This module owns:

* **SCHEMA** — each *user-tweakable* key → ``Key(default, validate, failure_class)``. Host
  (model / SOFA / BRIR / source paths, device *names*) is NOT here; it comes from the profile.
* ``load()`` — start from DEFAULTS, overlay ``settings.json``, **dropping any key whose value fails
  validation** (per-key fallback) and returning the dropped keys so the UI can toast them. A
  corrupt / unreadable file is treated as absent ⇒ all defaults.
* ``save()`` / ``reset()`` — **atomic + durable**: ``*.tmp`` → ``flush`` → ``os.fsync`` →
  ``os.replace``.
* ``mark_starting()`` / ``mark_started()`` / ``consume_backstop()`` — the one-step DEFAULTS backstop
  marker, **fail-safe** (an unreadable marker counts as "still set").

``failure_class`` tells the engine how to apply a change safely:
  ``catchable`` — try/except, keep the old value on failure;
  ``live-reversible`` — apply live, then auto-revert if health degrades in a short window;
  ``native-probe`` — open in a disposable subprocess first; adopt only if the child succeeds.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

METHODS = ("nearest", "timealign", "minphase", "notchwarp")
BACKENDS = ("cpu", "rust", "gpu")
BLOCKS = (256, 512, 1024, 2048)
COMPS = ("ctf", "df")


@dataclass(frozen=True)
class Key:
    default: Any
    validate: Callable[[Any], bool]
    failure_class: str  # "catchable" | "live-reversible" | "native-probe"


def _in(opts):
    return lambda v: v in opts


def _int_min(lo):
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= lo


def _is_bool(v):
    return isinstance(v, bool)


def _is_str(v):
    return isinstance(v, str)


# The user-tweakable settings and how each one fails. Defaults are the app's baseline; the launch
# profile (run.cmd flags) may override them, and a saved settings.json overrides that.
SCHEMA: dict[str, Key] = {
    "method":         Key("timealign", _in(METHODS), "catchable"),
    "backend":        Key("rust", _in(BACKENDS), "catchable"),
    "block":          Key(512, _in(BLOCKS), "live-reversible"),
    "cushion_blocks": Key(6, _int_min(2), "live-reversible"),
    "exclusive":      Key(False, _is_bool, "catchable"),  # open already try/excepts -> shared
    "hrir":           Key("mine", _is_str, "catchable"),
    "comp":           Key("ctf", _in(COMPS), "catchable"),
    "predict":        Key(True, _is_bool, "catchable"),
    "hp_eq":          Key(True, _is_bool, "catchable"),
    "camera":         Key(0, _int_min(0), "catchable"),
    "cam_w":          Key(0, _int_min(0), "catchable"),
    "cam_h":          Key(0, _int_min(0), "catchable"),
    "cam_fps":        Key(0, _int_min(0), "catchable"),
}



def defaults() -> dict:
    return {k: key.default for k, key in SCHEMA.items()}


def validate(key: str, value: Any) -> bool:
    k = SCHEMA.get(key)
    return bool(k and k.validate(value))


def failure_class(key: str) -> str:
    k = SCHEMA.get(key)
    return k.failure_class if k else "catchable"


class ConfigStore:
    """Atomic, validating persistence for the user-tweakable settings + the backstop marker."""

    def __init__(self, directory: str | os.PathLike):
        self.dir = Path(directory)
        self.settings_path = self.dir / "settings.json"
        self.bad_path = self.dir / "settings.bad.json"
        self.marker_path = self.dir / ".starting"

    # ---- persistence (atomic + durable) ----
    def load(self) -> tuple[dict, list[str]]:
        """Return ``(effective_config, dropped_keys)``: DEFAULTS overlaid with the valid saved keys;
        any saved key that is unknown or fails validation is dropped (per-key fallback)."""
        cfg = defaults()
        dropped: list[str] = []
        raw = self._read_json(self.settings_path)
        if not isinstance(raw, dict):
            if self.settings_path.exists():  # present but corrupt -> quarantine for inspection
                self._quarantine()
            return cfg, dropped
        for k, v in raw.items():
            if k in SCHEMA and SCHEMA[k].validate(v):
                cfg[k] = v
            else:
                dropped.append(k)
        return cfg, dropped

    def save(self, cfg: dict) -> None:
        """Persist only the known, valid user-tweakable keys."""
        clean = {k: cfg[k] for k in SCHEMA if k in cfg and SCHEMA[k].validate(cfg[k])}
        self._write_json(self.settings_path, clean)

    def reset(self) -> dict:
        """Reset to built-in DEFAULTS (persisted). Does NOT touch head-tracking calibration."""
        d = defaults()
        self._write_json(self.settings_path, d)
        return d

    # ---- one-step DEFAULTS backstop marker (fail-safe) ----
    def mark_starting(self) -> None:
        """Written (durably) right before the risky part of init; its presence at the next startup
        means the previous start never reached a usable UI."""
        self._write_text(self.marker_path, "1")

    def mark_started(self) -> None:
        """Cleared only on reaching the serving/usable state (never from finally/atexit/signals)."""
        try:
            self.marker_path.unlink(missing_ok=True)
        except OSError:
            pass

    def consume_backstop(self) -> bool:
        """At startup: True iff the previous start did not complete (marker present or unreadable
        fail-safe suspect). When True the caller should start on DEFAULTS this once and quarantine
        the saved settings. Consuming it does not itself write the marker (caller does on apply)."""
        try:
            present = self.marker_path.exists()
        except OSError:
            present = True  # fail-safe: can't tell -> assume the last start failed
        if present:
            self._quarantine()
        return present

    # ---- helpers ----
    def _quarantine(self) -> None:
        try:
            if self.settings_path.exists():
                os.replace(self.settings_path, self.bad_path)
        except OSError:
            pass

    @staticmethod
    def _read_json(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_json(self, p: Path, obj) -> None:
        self._write_text(p, json.dumps(obj, indent=2, sort_keys=True))

    def _write_text(self, p: Path, text: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)  # atomic

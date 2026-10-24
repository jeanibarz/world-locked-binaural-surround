"""Tests for the crash-safe settings store (RFC rfc-crash-safe-settings v2)."""
import json

from binaural_headtracker.config_store import (
    ConfigStore,
    defaults,
    failure_class,
    validate,
)


def test_defaults_are_all_valid():
    d = defaults()
    assert all(validate(k, v) for k, v in d.items())
    assert d["block"] == 512 and d["cushion_blocks"] == 6 and d["exclusive"] is False


def test_failure_classes():
    assert failure_class("block") == "live-reversible"
    assert failure_class("cushion_blocks") == "live-reversible"
    assert failure_class("exclusive") == "catchable"  # open already falls back to shared
    assert failure_class("backend") == "catchable"
    assert failure_class("unknown_key") == "catchable"  # safe default


def test_validation_rules():
    assert validate("backend", "rust") and not validate("backend", "quantum")
    assert validate("block", 512) and not validate("block", 500)
    assert validate("cushion_blocks", 2) and not validate("cushion_blocks", 1)
    assert not validate("exclusive", "yes") and validate("exclusive", True)
    assert not validate("block", True)  # bool is not a valid int block


def test_load_absent_returns_defaults(tmp_path):
    cfg, dropped = ConfigStore(tmp_path).load()
    assert cfg == defaults() and dropped == []


def test_save_load_roundtrip(tmp_path):
    s = ConfigStore(tmp_path)
    cfg = defaults() | {"backend": "gpu", "block": 1024, "cushion_blocks": 4, "exclusive": True}
    s.save(cfg)
    loaded, dropped = s.load()
    assert dropped == []
    assert loaded["backend"] == "gpu" and loaded["block"] == 1024
    assert loaded["cushion_blocks"] == 4 and loaded["exclusive"] is True


def test_invalid_and_unknown_keys_are_dropped_per_key(tmp_path):
    s = ConfigStore(tmp_path)
    # write a file by hand with one bad value, one unknown key, the rest good
    s.settings_path.parent.mkdir(parents=True, exist_ok=True)
    s.settings_path.write_text(json.dumps({"backend": "gpu", "block": 999, "bogus": 1}))
    cfg, dropped = s.load()
    assert cfg["backend"] == "gpu"          # good key kept
    assert cfg["block"] == 512              # bad value -> default (per-key fallback)
    assert "bogus" not in cfg               # unknown dropped
    assert set(dropped) == {"block", "bogus"}


def test_corrupt_file_falls_back_and_quarantines(tmp_path):
    s = ConfigStore(tmp_path)
    s.settings_path.parent.mkdir(parents=True, exist_ok=True)
    s.settings_path.write_text("{ this is not json")
    cfg, dropped = s.load()
    assert cfg == defaults()
    assert s.bad_path.exists() and not s.settings_path.exists()  # quarantined, not read


def test_reset_writes_defaults(tmp_path):
    s = ConfigStore(tmp_path)
    s.save(defaults() | {"backend": "gpu"})
    assert s.reset() == defaults()
    loaded, _ = s.load()
    assert loaded["backend"] == "rust"


def test_backstop_marker_lifecycle(tmp_path):
    s = ConfigStore(tmp_path)
    assert s.consume_backstop() is False          # no marker -> clean start
    s.save(defaults() | {"exclusive": True})
    s.mark_starting()                             # risky start begins
    assert s.marker_path.exists()
    # simulate a crash before mark_started: next startup sees the marker
    s2 = ConfigStore(tmp_path)
    assert s2.consume_backstop() is True          # previous start didn't complete
    assert s2.bad_path.exists()                   # saved settings quarantined, start on defaults


def test_mark_started_clears_marker(tmp_path):
    s = ConfigStore(tmp_path)
    s.mark_starting()
    s.mark_started()
    assert not s.marker_path.exists()
    assert ConfigStore(tmp_path).consume_backstop() is False

"""Tests for the single host entrypoint dispatcher (apps/launch.py).

``launch.py`` only parses the mode, injects per-mode default flags, and forwards the rest to
the target app's ``main()``. The apps themselves are host-only (camera/audio/GUI), so here we
stub them into ``sys.modules`` and assert on the argv the dispatcher hands them — no host stack
needed, which is the whole point of keeping the launcher pure.
"""

from __future__ import annotations

import sys
import types

import launch
import pytest


def _stub_app(record: dict) -> types.ModuleType:
    """A fake app module whose ``main`` records the argv it was dispatched and returns 0."""
    mod = types.ModuleType("stub_app")

    def main(argv=None):
        record["argv"] = list(argv) if argv is not None else None
        return 0

    mod.main = main  # type: ignore[attr-defined]
    return mod


@pytest.fixture
def stub(monkeypatch):
    """Route every app the dispatcher might import to one shared recorder."""
    rec: dict = {}
    app = _stub_app(rec)
    for name in ("voicemeeter_passthrough", "calibration_gui", "latency_sweep"):
        monkeypatch.setitem(sys.modules, name, app)
    return rec


def test_no_args_is_usage_error():
    assert launch.main([]) == 2


def test_help_is_clean_exit(capsys):
    assert launch.main(["--help"]) == 0
    assert "Modes:" in capsys.readouterr().out


def test_unknown_mode_rejected(capsys):
    assert launch.main(["wat"]) == 2
    assert "unknown mode" in capsys.readouterr().err


def test_listen_defaults_to_web_ui(stub):
    assert launch.main(["listen", "--brir-dir", "x"]) == 0
    argv = stub["argv"]
    assert argv[:2] == ["--brir-dir", "x"]  # forwarded verbatim, in order
    assert argv[-2:] == ["--ui", "web"]  # default appended


def test_listen_respects_explicit_ui(stub):
    assert launch.main(["listen", "--ui", "web"]) == 0
    assert stub["argv"].count("--ui") == 1
    assert "cockpit" not in stub["argv"]


def test_listen_respects_ui_equals_form(stub):
    assert launch.main(["listen", "--ui=web"]) == 0
    assert "--ui" not in stub["argv"]  # the =form already satisfies it
    assert "cockpit" not in stub["argv"]


def test_listen_does_not_require_hrir_sets(stub):
    assert launch.main(["listen"]) == 0
    assert "--hrir-sets-dir" not in stub["argv"]


def test_test_mode_requires_hrir_sets(stub, capsys):
    assert launch.main(["test"]) == 2
    assert "hrir-sets-dir" in capsys.readouterr().err
    assert "argv" not in stub  # the app was never dispatched


def test_test_mode_forwards_hrir_sets_and_web(stub):
    assert launch.main(["test", "--hrir-sets-dir", "sets"]) == 0
    argv = stub["argv"]
    assert "--hrir-sets-dir" in argv and "sets" in argv
    assert argv[-2:] == ["--ui", "web"]


def test_calibrate_forwards_untouched(stub):
    assert launch.main(["calibrate", "--camera", "0"]) == 0
    assert stub["argv"] == ["--camera", "0"]  # no --ui injection for calibrate


def test_latency_forwards_untouched(stub):
    assert launch.main(["latency", "--output", "sim"]) == 0
    assert stub["argv"] == ["--output", "sim"]

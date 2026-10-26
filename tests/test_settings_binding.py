"""Tests for the settings<->engine binding (RFC rfc-crash-safe-settings v2)."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
import settings_binding as sb  # noqa: E402

from binaural_headtracker import config_store as cs  # noqa: E402


def _args(**kw):
    base = dict(block=512, method="timealign", rust=True, gpu=False, predict=True,
                exclusive=False, camera=0, cam_width=0, cam_height=0, cam_fps=0,
                latency_ms=60.0, output_eq="/some/eq.txt")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _cockpit_backend(a):
    """Mirror voicemeeter_passthrough's live backend readout so the test guards the invariant."""
    return "rust" if a.rust else "gpu" if a.gpu else "cpu"


def test_backend_round_trips_through_apply_so_autosave_is_quiet():
    # For every backend, applying it to args and reading it back the way the cockpit does must
    # return the same name -> otherwise auto-save would silently rewrite the saved backend.
    for backend in cs.BACKENDS:
        a = _args(rust=False, gpu=False)
        sb.apply_to_args(cs.defaults() | {"backend": backend}, a)
        assert _cockpit_backend(a) == backend


def test_apply_to_args_overlays_settings_over_flags():
    a = _args(block=512, rust=True, gpu=False)
    cfg = cs.defaults() | {"backend": "gpu", "block": 1024, "cushion_blocks": 4,
                           "exclusive": True, "predict": False, "hp_eq": False, "camera": 2}
    sb.apply_to_args(cfg, a)
    # each flag names exactly its backend; the engine ORs them (use_rust = rust or gpu), and the
    # cockpit only reads "gpu" when rust is False -> gpu must NOT set args.rust
    assert a.block == 1024 and a.gpu is True and a.rust is False
    assert a.exclusive is True and a.predict is False and a.camera == 2
    assert a.output_eq is None                       # hp_eq False -> EQ disabled
    assert round(a.latency_ms) == round(4 * 1024 / 48000 * 1000)  # cushion blocks -> ms


def test_knobs_from_args_seed():
    a = _args(block=1024, latency_ms=85.3, gpu=True, rust=False)
    k = sb.knobs_from_args(a)
    assert k["backend"] == "gpu" and k["block"] == 1024
    assert k["cushion_blocks"] == max(2, round(85.3e-3 * 48000 / 1024))
    assert cs.validate("cushion_blocks", k["cushion_blocks"])


def test_current_knobs_roundtrips_through_store(tmp_path):
    store = cs.ConfigStore(tmp_path)
    knobs = sb.current_knobs(method="minphase", backend="cpu", block=256, cushion_blocks=8,
                             exclusive=True, hrir="subj_03", comp="df", predict=False,
                             camera=1, cam_w=640, cam_h=480, cam_fps=60, hp_eq=True)
    store.save(knobs)
    loaded, dropped = store.load()
    assert dropped == [] and loaded["method"] == "minphase" and loaded["cushion_blocks"] == 8
    assert loaded["hrir"] == "subj_03" and loaded["comp"] == "df"


def test_load_effective_first_run_seeds_and_overlays(tmp_path):
    store = cs.ConfigStore(tmp_path)
    a = _args(block=1024, latency_ms=60.0)
    cfg, dropped, backstop = sb.load_effective(store, a)
    assert backstop is False and dropped == []
    assert store.settings_path.exists()        # seeded
    assert a.block == 1024                      # overlaid (same here)


def test_load_effective_backstop_forces_defaults(tmp_path):
    store = cs.ConfigStore(tmp_path)
    store.save(cs.defaults() | {"block": 2048, "exclusive": True})
    store.mark_starting()                       # simulate a start that didn't complete
    a = _args(block=512)
    cfg, dropped, backstop = sb.load_effective(store, a)
    assert backstop is True
    assert cfg["block"] == 512 and cfg["exclusive"] is False   # forced defaults
    assert store.bad_path.exists()              # the suspect file was quarantined

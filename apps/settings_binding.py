"""Bind the crash-safe ConfigStore (RFC rfc-crash-safe-settings v2) to the engine args + state.

Keeps the settings <-> engine mapping in one testable place so `voicemeeter_passthrough.py`
only needs a few call sites. The user-tweakable keys (config_store.SCHEMA) don't map 1:1 to
argparse, so:
  * ``apply_to_args(cfg, args)`` overlays a loaded config onto the launch args (settings override
    the run.cmd flags; host identity — model/BRIR/source paths, device names — is untouched).
  * ``current_knobs(...)`` reads the live engine state back into a SCHEMA dict for saving.
  * ``load_effective(store, args)`` runs the startup protocol: backstop → load/seed → overlay.
All apply paths the engine uses are already catch-and-fall-back (backend→CPU, exclusive→shared,
bad HRIR→default), so loading a bad saved value can't take the app down — this just routes values.
"""
from __future__ import annotations

from binaural_headtracker import config_store as cs

_SR = 48000  # this app is 48 kHz throughout; cushion is defined in blocks, latency_ms derived


def apply_to_args(cfg: dict, args) -> None:
    """Overlay the arg-mapped user-knobs from ``cfg`` onto ``args`` (settings win over flags)."""
    args.block = int(cfg["block"])
    args.method = cfg["method"]
    b = cfg["backend"]
    # Match the launch convention: each flag names exactly its backend. The engine already treats
    # gpu as implying rust (use_rust = args.rust or args.gpu), and the cockpit's backend readout
    # (``"rust" if args.rust else "gpu" ...``) only reports "gpu" when args.rust is False.
    args.rust = b == "rust"
    args.gpu = b == "gpu"
    args.predict = bool(cfg["predict"])
    args.exclusive = bool(cfg["exclusive"])
    args.camera = int(cfg["camera"])
    args.cam_width = int(cfg["cam_w"])
    args.cam_height = int(cfg["cam_h"])
    args.cam_fps = int(cfg["cam_fps"])
    # cushion is stored in BLOCKS; the engine wants latency_ms (it re-derives blocks from it)
    args.latency_ms = cfg["cushion_blocks"] * cfg["block"] / _SR * 1000.0
    if not cfg["hp_eq"]:
        args.output_eq = None   # disable headphone EQ by dropping the output-EQ file
    # hrir + comp are applied at the cockpit (via cur_set / cur["comp"]); caller reads them from cfg


def knobs_from_args(args) -> dict:
    """Seed a SCHEMA config from the launch args (first run, before any settings.json exists)."""
    _rust = getattr(args, "rust", False)
    backend = "gpu" if getattr(args, "gpu", False) else ("rust" if _rust else "cpu")
    block = int(getattr(args, "block", 512))
    cushion_blocks = max(2, round(getattr(args, "latency_ms", 60.0) * 1e-3 * _SR / block))
    return {
        "method": getattr(args, "method", "timealign"),
        "backend": backend,
        "block": block,
        "cushion_blocks": cushion_blocks,
        "exclusive": bool(getattr(args, "exclusive", False)),
        "hrir": "mine",
        "comp": "ctf",
        "predict": bool(getattr(args, "predict", True)),
        "hp_eq": getattr(args, "output_eq", None) is not None,
        "camera": int(getattr(args, "camera", 0)),
        "cam_w": int(getattr(args, "cam_width", 0)),
        "cam_h": int(getattr(args, "cam_height", 0)),
        "cam_fps": int(getattr(args, "cam_fps", 0)),
    }


def current_knobs(*, method, backend, block, cushion_blocks, exclusive, hrir, comp,
                  predict, camera, cam_w, cam_h, cam_fps, hp_eq) -> dict:
    """Assemble a SCHEMA config dict from the live engine state (for saving on change)."""
    return {
        "method": method, "backend": backend, "block": int(block),
        "cushion_blocks": int(cushion_blocks), "exclusive": bool(exclusive),
        "hrir": hrir, "comp": comp, "predict": bool(predict), "hp_eq": bool(hp_eq),
        "camera": int(camera), "cam_w": int(cam_w), "cam_h": int(cam_h), "cam_fps": int(cam_fps),
    }


def load_effective(store: cs.ConfigStore, args) -> tuple[dict, list[str], bool]:
    """Startup protocol. Returns ``(cfg, dropped_keys, backstop)``:
    * backstop=True (previous start didn't complete) → force DEFAULTS this once (store already
      quarantined the saved file);
    * else if settings.json exists → load it (per-key validation drops bad keys);
    * else first run → seed from the launch flags and save.
    Then overlay cfg onto args. hrir/comp are returned in cfg for the cockpit to apply.
    """
    backstop = store.consume_backstop()
    if backstop:
        cfg, dropped = cs.defaults(), []
    elif store.settings_path.exists():
        cfg, dropped = store.load()
    else:
        cfg, dropped = knobs_from_args(args), []
        store.save(cfg)
    apply_to_args(cfg, args)
    return cfg, dropped, backstop

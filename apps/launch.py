"""Single host entrypoint for binaural-headtracker — one start script, several modes.

This replaces the per-app ``launch_*.py`` / ``run_*.cmd`` zoo on the host
(``C:\\Users\\ibarz\\BinauralHeadtracker``): every launcher there was the *same* few
apps with different flag presets. There is now one start script — the host's
``run.cmd`` (see ``host/run.cmd``) forwards here with a **mode** as its first argument:

    py -3.12 apps/launch.py listen    [passthrough flags...]   # daily world-lock, cockpit UI
    py -3.12 apps/launch.py test      [passthrough flags...]   # + HRIR-set blind A/B & loc test
    py -3.12 apps/launch.py calibrate [calibration flags...]   # recenter / axis-sign check
    py -3.12 apps/launch.py latency   [sweep flags...]         # latency sweep (dev, headless)

``listen`` and ``test`` are the same engine (``voicemeeter_passthrough``): ``test`` is
``listen`` plus an ``--hrir-sets-dir`` so the cockpit exposes the RFC-0003 blind A/B and
localization test over the owner's set + the dense-grid candidates. Both default to the
**cockpit** UI (``--ui cockpit``); pass ``--ui console`` to override. Everything after the
mode is forwarded verbatim to the target app's ``main()``, so every app flag still works.

The module computes its own repo root from ``__file__`` and prepends ``src`` + ``apps`` to
``sys.path``, so it runs the WSL checkout's code straight off a ``\\\\wsl.localhost\\...``
UNC path with nothing installed (the supported host pattern; see AGENTS.md). It only wires
flags and dispatches — no camera, audio or GUI here — so it imports and unit-tests on
headless WSL/CI without the host-only ``cv2``/``sounddevice``/Dear PyGui stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "apps", _ROOT / "src"):
    _s = str(_p)
    if _p.is_dir() and _s not in sys.path:
        sys.path.insert(0, _s)

# mode -> (target app module, one-line help). The import is deferred to dispatch time so a
# missing host-only dependency only bites the mode that needs it, not ``--help`` or a test.
MODES: dict[str, str] = {
    "listen": "daily world-lock passthrough (cockpit UI)",
    "test": "passthrough + HRIR-set blind A/B & localization test (needs --hrir-sets-dir)",
    "calibrate": "calibration GUI — recenter / axis-sign check",
    "latency": "latency sweep (headless dev benchmark)",
}


def _has_flag(flags: list[str], name: str) -> bool:
    """True if ``name`` (e.g. ``--ui``) is already present, as ``--ui x`` or ``--ui=x``."""
    return any(f == name or f.startswith(name + "=") for f in flags)


def _usage() -> str:
    lines = ["Usage: launch.py <mode> [flags forwarded to the app]", "", "Modes:"]
    width = max(len(m) for m in MODES)
    lines += [f"  {m.ljust(width)}  {help_}" for m, help_ in MODES.items()]
    lines += [
        "",
        "listen/test default to --ui cockpit (override with --ui console).",
        "test requires --hrir-sets-dir <dir of dense_*.sofa> (the host run.cmd supplies it).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        print(_usage(), file=sys.stderr)
        return 2
    if argv[0] in ("-h", "--help"):
        print(_usage())
        return 0

    mode, rest = argv[0], argv[1:]
    if mode not in MODES:
        print(f"[launch] unknown mode {mode!r}. Pick one of: {', '.join(MODES)}", file=sys.stderr)
        print("", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2

    if mode in ("listen", "test"):
        flags = list(rest)
        if not _has_flag(flags, "--ui"):
            flags += ["--ui", "cockpit"]
        if mode == "test" and not _has_flag(flags, "--hrir-sets-dir"):
            print(
                "[launch] 'test' mode needs --hrir-sets-dir <dir containing dense_*.sofa>\n"
                "         (the owner's host run.cmd passes it automatically).",
                file=sys.stderr,
            )
            return 2
        import voicemeeter_passthrough as app

        return app.main(flags)

    if mode == "calibrate":
        import calibration_gui as app

        return app.main(rest)

    if mode == "latency":
        import latency_sweep as app

        return app.main(rest)

    return 2  # unreachable (mode validated above); keeps type checkers happy


if __name__ == "__main__":
    raise SystemExit(main())

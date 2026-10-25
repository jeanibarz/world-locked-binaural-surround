"""World-lock *any* system audio: capture the virtual 7.1 device → render → headphones (B4.1).

The end-to-end Windows path (M4). Point your apps at a virtual 7.1 output (VoiceMeeter
Input / VAIO, set as the Windows default playback); this app **captures** that 7.1 stream
from the virtual device's output bus, world-locks every channel with your head pose
(`WorldLocked7p1`: each of the 8 speakers steered through its nearest BRIR, click-free,
LFE non-steered), and plays the stereo result to your **headphone soundcard**. So anything
on the computer — a game, a film, music — becomes head-tracked and world-locked.

    any app (7.1) → VoiceMeeter Input (VAIO) → [this engine] → FiiO K11 (headphones)

Set VoiceMeeter so it does **not** also route to the headphones (disable the A1 that
points at them); this engine is the only thing feeding them, so there's no double audio.

Three decoupled stages so the render never blocks a real-time audio callback: the capture
callback only *enqueues* the raw 8ch frames; a **render worker thread** world-locks them to
stereo (the heavy convolution lives here, with the freshest head pose) and fills a FIFO;
the output callback drains the FIFO to the headphones. Rendering in the callback itself
starves the audio threads (the GIL, plus GPU sync on the `--gpu` path) and drops audio — the
worker keeps the callbacks microsecond-light and the cushion absorbs the render's jitter.
Composed from tested parts
(`live_render.WorldLocked7p1`,
`calibrate`, `world`, `tracking`, `devices`); only the camera, MediaPipe and the two
`sounddevice` streams are host-side, so this runs on the Windows host, not CI/WSL.

Run on the host (see AGENTS.md "Running the host GUI apps"), e.g.::

    py -3.12 apps/voicemeeter_passthrough.py --model face_landmarker.task --brir-dir <baked> --rust
    # a pre-launch menu picks camera + 7.1 input + headphone output; --no-dialog uses flags
    # --input-device "VoiceMeeter Output"  --audio-device "FiiO"  (name substrings or indices)
    # --rust: native CPU kernel, real-time without a GPU (B8.2) — the preferred path.
    #         --gpu (CuPy) is an optional alternative; pure-Python CPU alone won't keep up.

Keys: **C** recenter, **F** flip L/R (if the feed is mirrored), **Q** quit.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import _console as C
import numpy as np

from binaural_headtracker.calibrate import HeadCalibration, calibrate_neutral, directions_for_pose
from binaural_headtracker.devices import (
    camera_choices,
    probe_cameras,
    query_output_devices,
    resolve_output_device,
    settings_dialog,
)
from binaural_headtracker.diagnostics import classify
from binaural_headtracker.head_pose import head_pose_from_matrix
from binaural_headtracker.hrir_sets import LocalizationTest, discover_sets, load_dense_grid_sofa
from binaural_headtracker.latency import LatencyProbe
from binaural_headtracker.live_render import (
    InterpWorldLocked7p1,
    WorldLocked7p1,
    WorldLockedSource,
    interpolated_ring,
)
from binaural_headtracker.output_eq import load_output_eq
from binaural_headtracker.prediction import PosePredictor
from binaural_headtracker.radar import nearest_front_channel
from binaural_headtracker.surround import load_baked_brir
from binaural_headtracker.tracking import HeadPose, PoseFilter
from binaural_headtracker.world import layout_7p1

_MMCSS_HANDLES: list = []  # keep MMCSS task handles alive for the process (dropping one reverts it)


def _elevate_to_pro_audio() -> str:
    """Register the CURRENT thread (the render worker) for pro-audio scheduling so the OS doesn't
    preempt it for background work — the main cause of the render-deadline spikes that drop blocks.

    Windows: MMCSS ``"Pro Audio"`` via ``avrt.dll`` (what DAWs use), then bump its MMCSS priority;
    falls back to a time-critical thread priority if MMCSS is unavailable. No-op (and harmless) on
    other platforms. Returns a short status for the startup log. The handle is kept alive for the
    process — releasing it drops the thread back to normal scheduling."""
    import sys
    if not sys.platform.startswith("win"):
        return "normal (non-Windows)"
    import ctypes
    from ctypes import wintypes
    try:
        avrt = ctypes.WinDLL("avrt")
        avrt.AvSetMmThreadCharacteristicsW.restype = wintypes.HANDLE
        avrt.AvSetMmThreadCharacteristicsW.argtypes = [wintypes.LPCWSTR,
                                                       ctypes.POINTER(wintypes.DWORD)]
        idx = wintypes.DWORD(0)
        h = avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(idx))
        if h:
            try:  # AVRT_PRIORITY_HIGH = 1 — schedule above the MMCSS default
                avrt.AvSetMmThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
                avrt.AvSetMmThreadPriority(h, 1)
            except Exception:  # noqa: BLE001 — the characteristic alone is the main win
                pass
            _MMCSS_HANDLES.append(h)
            return "MMCSS 'Pro Audio'"
    except Exception:  # noqa: BLE001 — avrt missing/blocked → fall back to a plain priority bump
        pass
    try:
        k32 = ctypes.WinDLL("kernel32")
        k32.SetThreadPriority(k32.GetCurrentThread(), 15)  # THREAD_PRIORITY_TIME_CRITICAL
        return "time-critical"
    except Exception:  # noqa: BLE001
        return "normal (elevation unavailable)"


def _raise_process_priority() -> str:
    """Raise the WHOLE process to HIGH priority class so *every* engine thread beats normal-priority
    background work (a compiler, a file indexer, a Windows-update task). MMCSS boosts only the one
    render-worker thread (`_elevate_to_pro_audio`), but that worker still needs the GIL for its
    per-block Python glue, and the GIL is held by the camera/detect/main-loop threads — which run at
    the process base priority. Under a CPU-saturating normal-priority competitor those GIL holders
    starve, so the worker misses its deadline and audio drops out *despite* MMCSS (observed live: a
    background ``cargo build`` at normal priority made the DSP inaudible with constant dropouts).
    Raising the process class lifts all of them together. HIGH is what pro-audio hosts use;
    REALTIME is deliberately avoided — it outranks kernel/driver threads and can stall audio I/O and
    the whole machine. Best-effort, Windows-only; the class reverts when the process exits. Returns
    a short status for the startup log."""
    import sys
    if not sys.platform.startswith("win"):
        return "normal (non-Windows)"
    import ctypes
    from ctypes import wintypes
    HIGH_PRIORITY_CLASS = 0x0000_0080
    try:
        k32 = ctypes.WinDLL("kernel32")
        # Set the signatures explicitly: GetCurrentProcess returns a 64-bit HANDLE, and ctypes
        # truncates it to a 32-bit int without restype=c_void_p — the call then fails silently and
        # the process stays at normal priority. (HIGH needs no privilege; only REALTIME does.)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        if k32.SetPriorityClass(k32.GetCurrentProcess(), HIGH_PRIORITY_CLASS):
            return "HIGH"
    except Exception:  # noqa: BLE001 — scheduling is a best-effort bonus, never fatal
        pass
    return "normal (elevation unavailable)"


FG = (245, 237, 232)
MUTED = (171, 151, 139)
ACCENT = (214, 214, 56)
EXPERIMENT_METHODS = ("nearest", "timealign", "minphase", "notchwarp")
# main() returns this so the host launcher can relaunch (pick up code changes) without the
# user re-running run.cmd. run_passthrough.cmd loops while it sees this exit code; any other
# code (0 = quit) ends. Kept distinct from 0/1/signal codes. See AGENTS.md / journal 39.
RESTART_EXIT_CODE = 97


def _pid_is_live_python(pid: int) -> bool:
    """Best-effort check that ``pid`` is a live Python process (guards single-instance against a
    recycled PID before we terminate it)."""
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5)
            return "python" in out.stdout.lower()
        os.kill(pid, 0)  # POSIX liveness probe
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _terminate_pid(pid: int) -> None:
    """Forcibly stop ``pid`` (and its children) so it releases the audio device, camera and port."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError):
        pass


def ensure_single_instance(lock_path: Path) -> None:
    """Guarantee one live engine: if a previous instance is recorded in ``lock_path`` and still
    running, terminate it (so it frees the camera / FiiO / web port) before we claim the resources,
    then record our own PID and arrange to clear it on exit. Replace-the-previous, by design."""
    try:
        prev_pid = int(json.loads(lock_path.read_text(encoding="utf-8")).get("pid", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        prev_pid = 0
    if _pid_is_live_python(prev_pid):
        print(f"single-instance: replacing the previous cockpit (pid {prev_pid})")
        _terminate_pid(prev_pid)
        for _ in range(50):  # wait up to ~5 s for it to exit and release the devices
            if not _pid_is_live_python(prev_pid):
                break
            time.sleep(0.1)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    except OSError:
        return

    def _release() -> None:
        try:  # only clear the lock if it is still ours (not a replacement's)
            if int(json.loads(lock_path.read_text(encoding="utf-8")).get("pid", 0)) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    atexit.register(_release)


def _load_mono_loop(path: Path, target_sr: int) -> np.ndarray:
    """Read a WAV as mono float64 in [-1, 1], resampled to ``target_sr``."""
    import warnings

    from scipy.io import wavfile
    from scipy.signal import resample_poly

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sr, data = wavfile.read(path)
    data = np.asarray(data)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = (data.astype(np.float64) / float(np.iinfo(data.dtype).max)
            if np.issubdtype(data.dtype, np.integer) else data.astype(np.float64))
    if sr != target_sr:
        from math import gcd
        g = gcd(int(sr), int(target_sr))
        data = resample_poly(data, target_sr // g, sr // g)
    return data


def _make_test_loop(source: Path | None, sr: int) -> np.ndarray:
    """The test signal for the experiment layer: the owner's source WAV if given, else a
    built-in 1 s loop with a short noise tick — a repeating transient that is easy to localise."""
    if source is not None:
        return _load_mono_loop(source, sr)
    n = sr
    buf = np.zeros(n)
    k = int(0.03 * sr)
    rng = np.random.default_rng(0)
    buf[:k] = rng.standard_normal(k) * np.hanning(2 * k)[:k]
    buf *= 0.5 / max(float(np.abs(buf).max()), 1e-9)  # normalize the tick to a safe level
    return buf


def main(argv=None) -> int:
    import cv2
    import mediapipe as mp
    import sounddevice as sd
    from mediapipe.tasks.python import vision

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True, help="FaceLandmarker .task path")
    p.add_argument("--brir-dir", type=Path, required=True, help="baked 7.1 BRIR WAV directory")
    p.add_argument("--hrir-sets-dir", type=Path, default=None,
                   help="directory of dense-grid SOFA candidates (dense_*.sofa, from "
                        "binaural-localization RFC 0003) to switch between live in the cockpit; "
                        "'mine' (--brir-dir) is always the first option")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--input-device", default=None,
                   help="7.1 capture device index or name substring (e.g. 'VoiceMeeter Output')")
    p.add_argument("--pull-sysvad", action="store_true",
                   help="Approach A (B2): capture our own pull-driven SYSVAD virtual device instead of "
                        "VoiceMeeter, and drive it from the K11 output callback (one KookrPullTick per "
                        "period) so the headphone clock is the single master clock — no two-clock drift, "
                        "no resampler. Pins the output to one 10 ms period and rebuffers --block behind "
                        "the FIFO. Defaults --input-device to the Tablet (SYSVAD) loopback.")
    p.add_argument("--audio-device", default=None,
                   help="headphone output device index or name substring (e.g. 'FiiO')")
    p.add_argument("--exclusive", action="store_true",
                   help="open the output in WASAPI exclusive mode (smallest device buffer, ~13 vs "
                        "~22 ms shared); falls back to shared if the device refuses it")
    p.add_argument("--out-latency-ms", type=float, default=None,
                   help="requested output DEVICE buffer (ms). Default picks the host-native 'low' "
                        "latency (smallest). In exclusive mode that minimum has no slack and can "
                        "underrun — raise this (e.g. 8–12) to add buffer periods back while still "
                        "beating shared mode's ~22 ms.")
    p.add_argument("--no-dialog", action="store_true", help="skip the picker, use the flags")
    p.add_argument("--block", type=int, default=2048,
                   help="render block size (bigger = steadier; 2048 gives dense-grid 7.1 render "
                        "ample headroom)")
    p.add_argument("--fade-ms", type=float, default=20.0)
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--latency-ms", type=float, default=60.0,
                   help="output FIFO cushion (ms). This is the dominant head-move-to-ear delay: "
                        "a head turn is heard ~this late, since the cushion of already-rendered "
                        "audio drains first. Lower = snappier; raise if you get dropouts. Note the "
                        "cushion floors at 2 render blocks, so below ~2x the --block duration this "
                        "has no effect — lower --block to actually shrink the floor (journal 76). "
                        "The cushion is whole render blocks (round(ms·sr/block)), so it grows in "
                        "~--block-duration steps: at block 2048 the jump from 2→3 blocks needs "
                        "~107 ms, 3→4 ~150 ms — a smaller bump reads back as the same depth. The "
                        "cushion is held as a setpoint, not just a prefill: after an underrun "
                        "the output re-primes to full depth so the margin persists (journal 81).")
    p.add_argument("--min-cutoff", type=float, default=1.0)
    p.add_argument("--beta", type=float, default=0.3)
    p.add_argument("--yaw-sign", type=float, default=1.0)
    p.add_argument("--pitch-sign", type=float, default=-1.0)
    p.add_argument("--roll-sign", type=float, default=1.0)
    p.add_argument("--flip-az", action="store_true", help="mirror azimuth L/R (also F live)")
    p.add_argument("--gpu", action="store_true",
                   help="render on the GPU (CuPy) — the full-BRIR 8ch path in real time")
    p.add_argument("--rust", action="store_true",
                   help="render with the native Rust kernel (no GPU needed; ~5x real time on "
                        "CPU at block 256). Build it first: maturin develop --release -m "
                        "rust/Cargo.toml. Takes precedence over --gpu.")
    p.add_argument("--monitor", action="store_true",
                   help="print per-channel captured input RMS ~1/s (diagnose channel layout)")
    p.add_argument("--lfe-mode", choices=("flat", "brir"), default="flat",
                   help="'flat' sums LFE non-directionally to both ears (keeps the bass the "
                        "bass-light BRIRs strip); 'brir' convolves it through its BRIR (B4.3)")
    p.add_argument("--output-eq", type=Path, default=None,
                   help="EqualizerAPO config whose global per-ear comp+EQ chain (the part after "
                        "the downmix to L/R) is applied to the stereo output, so the render "
                        "matches your tuned EqualizerAPO sound (B5.3). Pair with --lfe-mode brir.")
    p.add_argument("--cam-width", type=int, default=0, help="request camera width (0 = default)")
    p.add_argument("--cam-height", type=int, default=0, help="request camera height (0 = default)")
    p.add_argument("--cam-fps", type=int, default=0, help="request camera fps (0 = default)")
    # Experiment layer (live, over the running passthrough): a movable test source for A/B.
    p.add_argument("--source", type=Path, default=None,
                   help="test-source WAV for the experiment layer (T); a noise tick if omitted")
    p.add_argument("--test-gain", type=float, default=0.5, help="test-source level")
    p.add_argument("--method", default="nearest", choices=EXPERIMENT_METHODS,
                   help="shared interpolation method for the 7.1 AND the test source "
                        "(cycle live with M); a synthesis method wants --rust + a bigger --block")
    p.add_argument("--grid-step", type=float, default=10.0,
                   help="degrees between synthesized grid directions (shared 7.1 + test source)")
    p.add_argument("--predict", action="store_true",
                   help="orientation prediction (B8.6): extrapolate the head pose forward by the "
                        "measured pose-age so a head turn is heard on time (cancels camera lag)")
    p.add_argument("--predict-ms", type=float, default=0.0,
                   help="prediction horizon in ms; 0 = auto (use the measured pose-age-at-ear p50)")
    p.add_argument("--max-tail-ms", type=float, default=150.0,
                   help="cap the ring-out of a left-behind test-source grid voice (ms); bounds "
                        "concurrent voices so a moving source on a fine grid stays real-time")
    p.add_argument("--rotate-speed", type=float, default=45.0,
                   help="auto-rotation speed of the test source in deg/s (toggle with R)")
    p.add_argument("--ui", choices=("web",), default="web",
                   help="front-end: the web cockpit (HTTP server + browser/native window). The "
                        "only UI; kept as a flag for forward compatibility.")
    p.add_argument("--port", type=int, default=0,
                   help="fixed HTTP port for the cockpit (0 = pick a free one). A native shell "
                        "(Tauri) uses a fixed port so it knows the window URL without parsing stdout.")
    p.add_argument("--no-browser", action="store_true",
                   help="serve only; do not auto-open a browser. Use when a native shell provides "
                        "the window, or to open the URL in Chrome yourself for inspection.")
    p.add_argument("--log-fifo-fill", type=Path, default=None,
                   help="A0 diagnostic (RFC Track A): log output-FIFO fill vs time to this CSV, "
                        "then run `python -m binaural_headtracker.fifo_drift <csv>` to classify "
                        "the queue term as clock skew (A1 resampler) vs jitter/burst (Track B). "
                        "Off by default; measure at a fixed --block/--latency-ms.")
    args = p.parse_args(argv)

    # --- crash-safe settings (RFC rfc-crash-safe-settings v2): overlay persisted user-knobs onto the
    # launch flags (settings win; host identity is untouched), with a one-step DEFAULTS backstop. ---
    import settings_binding as _SB
    from pathlib import Path as _CfgPath
    from binaural_headtracker.config_store import ConfigStore as _ConfigStore
    # resolve to absolute so the config dir never depends on the launch cwd
    _cfg_dir = (_CfgPath(args.model).resolve().parent if args.model else _CfgPath.home()) / "cockpit_config"
    ensure_single_instance(_cfg_dir / ".instance.lock")  # replace any previous live engine first
    settings_store = _ConfigStore(_cfg_dir)
    saved_cfg, _dropped_keys, _settings_backstop = _SB.load_effective(settings_store, args)
    settings_store.mark_starting()  # cleared once audio is up (if the start never completes -> defaults)
    if _dropped_keys:
        print(f"settings: dropped invalid keys {_dropped_keys} (using defaults for them)")
    if _settings_backstop:
        print("settings: previous start did not complete — started with DEFAULTS; "
              f"your settings were moved to {settings_store.bad_path}")
    pull_mode = args.pull_sysvad  # Approach A: drive our SYSVAD device from the output clock
    # Note: the pull path needed a 200 ms cushion in journal 75, but journal 76 found that was the
    # cockpit starving the render worker on the GIL, not an intrinsic cost — fixed by the tighter
    # GIL switch interval below, so both paths share the normal 60 ms default again (issue #109).

    brir = load_baked_brir(args.brir_dir)

    def _resolve_backend(backend: str | None) -> tuple[bool, bool]:
        """(use_rust, use_gpu) for an explicit backend ('cpu'/'rust'/'gpu'), else the launch flags.
        Lets the cockpit switch backend live (B6.12 slice 2) without restarting."""
        if backend is None:
            return args.rust, args.gpu
        return backend == "rust", backend == "gpu"

    # The render block size is live (cockpit stepper): the builders below read ``blk["n"]`` so a
    # change rebuilds every renderer at the new size, and the worker snapshots it per batch under
    # ``lock`` so the slice width always matches the renderer it hands the slice to. The output
    # stream is blocksize=0 (device-native) and re-sliced by the callback, so it needs no reopen;
    # only the cushion math (blocks ↔ ms) and the rendered partition size follow the block.
    blk = {"n": int(args.block)}

    def _nearest_renderer(backend: str | None = None):
        """Build the fast 8-bus nearest renderer for ``backend``. Returns ``(renderer, reason)``
        where ``reason`` is a short failure string when a requested accelerator (rust/gpu) was
        unavailable and we fell back to pure-Python CPU, else ``None`` — so the caller can snap the
        UI to the real engine and surface a toast instead of silently running the wrong backend."""
        use_rust, use_gpu = _resolve_backend(backend)
        if use_rust:
            try:
                from binaural_headtracker.rust_render import RustWorldLocked7p1
                print("render: nearest — native Rust kernel (CPU, no GPU)")
                return RustWorldLocked7p1(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                                          lfe_mode=args.lfe_mode), None
            except Exception as exc:  # noqa: BLE001  — fall back to pure-Python CPU
                reason = f"Rust kernel unavailable ({type(exc).__name__})"
                print(f"{reason}: {exc!r}; falling back to CPU.")
                return (WorldLocked7p1(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                                       lfe_mode=args.lfe_mode), reason)
        if use_gpu:
            try:
                from binaural_headtracker.gpu_render import GpuWorldLocked7p1
                print("render: nearest — GPU (CuPy)")
                return GpuWorldLocked7p1(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                                         lfe_mode=args.lfe_mode), None
            except Exception as exc:  # noqa: BLE001  — fall back to CPU
                reason = f"GPU unavailable ({type(exc).__name__})"
                print(f"{reason}: {exc!r}; falling back to CPU.")
                return (WorldLocked7p1(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                                       lfe_mode=args.lfe_mode), reason)
        print("render: nearest — pure-Python CPU (pass --rust or --gpu for real time)")
        return WorldLocked7p1(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                              lfe_mode=args.lfe_mode), None

    def build_main_renderer(method: str, backend: str | None = None):
        """The full 7.1 renderer for ``method`` — the method applies to the WHOLE surround
        (shared with the test source). 'nearest' uses the fast 8-bus path; a synthesis method
        uses the dense interpolated grid (InterpWorldLocked7p1, B4.4), which needs a bigger
        block for headroom (the diagnostics panel shows if it keeps up). ``backend`` (cpu/rust/gpu)
        overrides the launch flags so the cockpit can switch it live."""
        if method == "nearest":
            return _nearest_renderer(backend)
        # Synthesis methods run the dense interpolated grid, which now has a GPU path too
        # (GpuBusConvolver over the grid). A 'gpu' request builds the GPU convolver and only falls
        # back to Rust (then pure-Python CPU) if CuPy is missing; 'rust' uses the kernel; 'cpu'
        # stays pure Python.
        use_gpu = backend == "gpu" if backend else args.gpu
        use_rust = backend in ("rust", "gpu") if backend else (args.rust or args.gpu)
        r = InterpWorldLocked7p1(brir, method, block_size=blk["n"], fade_ms=args.fade_ms,
                                 grid_step=args.grid_step, lfe_mode=args.lfe_mode,
                                 use_rust=use_rust, use_gpu=use_gpu)
        actual = getattr(r, "engine", "cpu")
        print(f"render: {method} — dense-grid 7.1 ({args.grid_step:.0f}°) [{actual}]")
        # Report a fallback so the UI can snap the selector and toast (GPU first, then the kernel).
        if use_gpu and actual != "gpu":
            reason = "GPU unavailable"
        elif use_rust and actual == "cpu":
            reason = "Rust kernel unavailable"
        else:
            reason = None
        return r, reason

    def render_label(renderer, method: str) -> str:
        """A one-line description of what the 7.1 is actually running: engine · method [· grid]."""
        if isinstance(renderer, InterpWorldLocked7p1):
            return (f"{renderer.engine} · {method} · "
                    f"{renderer.grid_step:.0f}° grid ({renderer.n_bus} bus)")
        # nearest: engine is the renderer's own class (rust/gpu fall back to cpu silently)
        return f"{_backend_of(renderer)} · nearest (8-bus)"

    def _backend_of(renderer) -> str:
        """The backend ('cpu'/'rust'/'gpu') a built renderer ACTUALLY runs on — used to snap the
        cockpit's backend segment to engine truth after a switch (a failed accelerator falls back).
        The dense-grid renderer carries gpu/rust/cpu on ``.engine``; the nearest classes map 1:1."""
        if isinstance(renderer, InterpWorldLocked7p1):
            return getattr(renderer, "engine", "cpu")
        return {"RustWorldLocked7p1": "rust", "GpuWorldLocked7p1": "gpu",
                "WorldLocked7p1": "cpu"}.get(type(renderer).__name__, "cpu")

    _main0, _ = build_main_renderer(args.method)
    main_rend = {"r": _main0}  # the 7.1 renderer, swappable live (M)
    main_rend["label"] = render_label(main_rend["r"], args.method)

    # HRIR-set switcher (RFC 0003): the owner's own set ('mine', from --brir-dir) plus any
    # dense-grid SOFA candidates in --hrir-sets-dir, switchable live in the cockpit. The owner
    # set renders from the sparse 8 as usual; a candidate renders its dense real ring via
    # InterpWorldLocked7p1(dense_override=...) steered by nearest.
    hrir_sets = discover_sets(args.hrir_sets_dir)
    sets_by_name = {s.name: s for s in hrir_sets}
    cur_set = {"name": hrir_sets[0].name}
    if saved_cfg.get("hrir") in {s.name for s in hrir_sets}:  # apply a persisted HRIR set if it exists
        cur_set["name"] = saved_cfg["hrir"]
    if len(hrir_sets) > 1:
        print(f"HRIR sets ({len(hrir_sets)}): " + ", ".join(s.name for s in hrir_sets)
              + " — switch live in the cockpit")

    # Global output-bus comp + EQ (B5.3): the bare BRIRs carry room/direction but not the
    # headphone comp or subjective EQ, which EqualizerAPO applies once to the stereo mix. Apply
    # the same per-ear chain here so the render sounds like the tuned config, not the thin bake.
    def build_output_eq(block: int):
        """The per-ear comp+EQ for the stereo bus, sized to ``block`` — rebuilt on a live block
        change (its FFT partitions are block-sized). ``None`` when no --output-eq was given."""
        if args.output_eq is None:
            return None
        return load_output_eq(args.output_eq, samplerate=brir.samplerate, block_size=block)

    eqh = {"eq": build_output_eq(blk["n"])}  # holder so _set_block can swap it under the lock
    if args.output_eq is not None:
        print(f"output EQ: {args.output_eq} (global per-ear comp+EQ on the stereo bus)")
        if args.lfe_mode != "brir":
            print("  note: the EQ restores the sub-bass the LFE BRIR strips — pair it with "
                  "--lfe-mode brir to match EqualizerAPO (flat LFE would double the bass).")
    layout = layout_7p1(channels=brir.channels)  # aligned to BRIR channel order
    world_az = layout.head_relative_directions()[:, 0]  # fixed world azimuths (neutral pose)
    c_idx = brir.channels.index("C") if "C" in brir.channels else 0

    # Experiment layer: a single movable test source rendered through a selectable interpolation
    # method (dense grid, B3.3), mixed into the running passthrough so A/B needs no restart.
    test_loop = _make_test_loop(args.source, brir.samplerate)
    test_renderers: dict[str, WorldLockedSource] = {}

    def build_test_renderer(method: str) -> WorldLockedSource:
        if method == "nearest":
            return WorldLockedSource(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                                     max_tail_ms=args.max_tail_ms)
        # Dense grid: a moving source sweeps through many grid points, each spinning up a
        # full-length convolution voice that otherwise rings its whole ~0.5 s tail. Cap the
        # retired-voice tail (--max-tail-ms) so concurrent voices stay bounded → no dropouts.
        dense, azmap = interpolated_ring(brir, method, step_deg=args.grid_step)
        return WorldLockedSource(dense, block_size=blk["n"], fade_ms=args.fade_ms,
                                 max_tail_ms=args.max_tail_ms, azimuths=azmap)

    test_renderers[args.method] = build_test_renderer(args.method)
    test = {"on": False, "world_az": 0.0, "az": 0.0, "method": args.method,
            "gain": float(args.test_gain), "solo": False, "rotate": False,
            "renderer": test_renderers[args.method]}
    test_pos = {"i": 0}  # worker-local read cursor into test_loop

    out_devs, default_out = query_output_devices()
    cam_index = args.camera
    out_index = resolve_output_device(args.audio_device, out_devs)
    if not args.no_dialog:
        picked = settings_dialog(
            camera_choices(probe_cameras()), out_devs,
            title="World-lock passthrough — devices", camera_default=args.camera,
            audio_default=out_index if out_index is not None else default_out)
        if picked is None:
            return 0
        cam_index = picked["camera"] if picked["camera"] is not None else args.camera
        out_index = picked["audio_device"]

    # Capture the RAW 7.1 as apps send it INTO the virtual device, via WASAPI **loopback**
    # (PyAudioWPatch). Reading VoiceMeeter's *output* bus is wrong — VoiceMeeter downmixes
    # it to stereo, losing centre/LFE/surrounds (confirmed by measurement). The loopback
    # taps the pre-mix stream on the render endpoint apps target.
    import pyaudiowpatch as pawp

    pya = pawp.PyAudio()
    in_spec = (args.input_device or ("tablet" if pull_mode else "VoiceMeeter Input")).lower()
    _cands = [d for d in pya.get_loopback_device_info_generator()
              if in_spec in d["name"].lower()]
    # Prefer the endpoint with the right channel count: "tablet"/"voicemeeter" match several endpoints
    # (headset 2ch, SPDIF, the 7.1 speakers), and only the 7.1 one has brir.n_channels.
    lb = next((d for d in _cands if int(d["maxInputChannels"]) == brir.n_channels),
              _cands[0] if _cands else None)
    if lb is None:
        raise SystemExit(f"no WASAPI loopback device matching {in_spec!r} — "
                         + ("is the SYSVAD 'Tablet Sample' speakers endpoint present and set to 7.1?"
                            if pull_mode else
                            "is VoiceMeeter running and set as the default 7.1 playback device?"))
    in_ch = int(lb["maxInputChannels"])
    if in_ch != brir.n_channels:
        raise SystemExit(f"loopback '{lb['name']}' is {in_ch}ch, need {brir.n_channels} — set "
                         f"that endpoint to 7.1 in Windows Sound.")
    if out_index is not None:
        od = sd.query_devices(out_index)
        apis = [a["name"] for a in sd.query_hostapis()]
        out_api = apis[od["hostapi"]] if 0 <= od.get("hostapi", -1) < len(apis) else "?"
        buf_ms = od["default_low_output_latency"] * 1000
        out_name = f"{od['name']} [{out_api}]  (buffer ~{buf_ms:.0f} ms)"
    else:
        out_name = "default"
    print(f"capture (loopback): {lb['name']}  {in_ch}ch  ->  output: {out_name}  "
          f"@ {brir.samplerate} Hz")
    print(f"block {args.block}  latency cushion ~{args.latency_ms:.0f} ms")

    # Shared state: per-channel head-relative azimuth (written by the camera thread, read by
    # the render worker). Start at the neutral (fixed-head) directions.
    state = {
        "az": layout.head_relative_directions()[:, 0].copy(),
        "cal": HeadCalibration(0.0, 0.0, 0.0, args.yaw_sign, args.pitch_sign, args.roll_sign),
        "pose_t": None,  # capture time (monotonic s) of the pose behind "az"; None until first face
    }
    lock = threading.Lock()
    # The loopback capture clock ≠ the output clock, and both deliver audio in bursts, so
    # the output FIFO is primed with a silence cushion (`--latency-ms`) that absorbs the
    # jitter; the backlog is capped just above the cushion so clock drift can't let the queue
    # (and so the head-move-to-ear latency) grow unbounded — it stays near the cushion.
    cushion = max(2, round(args.latency_ms * 1e-3 * brir.samplerate / blk["n"]))
    # Each FIFO entry is (stereo_block, pose_t, render_t) so the output callback can measure the
    # pose-age-at-ear precisely at *playout* and split it into stale / queue / device (B8.5e).
    out_fifo: deque = deque((np.zeros((blk["n"], 2)), None, None) for _ in range(cushion))
    fifo_lock = threading.Lock()
    in_tail = {"buf": np.zeros((0, brir.n_channels))}  # leftover capture < one block
    cb_error: list[BaseException] = []
    max_backlog = cushion * 2  # cap latency growth from drift at ~2x the target cushion

    # Approach A (B2 / issue #104): drive our own pull-driven SYSVAD device from the K11 output callback,
    # making the headphone output the single master clock. Each output callback advances SYSVAD exactly one
    # notification period by signalling KookrPullTick. The output is PINNED to one period (blocksize below),
    # so one SetEvent == one period -- an auto-reset event coalesces, so we must never need >1 per callback.
    # This removes the two-clock drift the VoiceMeeter path carries (no async resampler needed); the renderer
    # keeps its larger --block behind the out_fifo cushion. SAFETY: to stop it, close this handle (finally)
    # and let the stream close -- NEVER unload/disable/devcon-restart the driver (that bugchecks).
    PULL_PERIOD = max(1, round(brir.samplerate * 0.010))   # SYSVAD 10 ms notification period (480 @ 48k)
    hev = None
    kset = None
    if pull_mode:
        import ctypes
        from ctypes import wintypes, Structure, POINTER, byref, c_void_p, sizeof
        _k = ctypes.WinDLL("kernel32", use_last_error=True)
        _adv = ctypes.WinDLL("advapi32", use_last_error=True)

        class _SA(Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSD", c_void_p), ("bInherit", wintypes.BOOL)]

        _sdb = (ctypes.c_byte * 64)()
        _adv.InitializeSecurityDescriptor(byref(_sdb), 1)
        _adv.SetSecurityDescriptorDacl(byref(_sdb), True, None, False)  # NULL DACL -> the SYSTEM driver opens it
        _sa = _SA(sizeof(_SA), ctypes.cast(byref(_sdb), c_void_p), False)
        _k.CreateEventW.restype = wintypes.HANDLE
        _k.CreateEventW.argtypes = [POINTER(_SA), wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        _k.SetEvent.argtypes = [wintypes.HANDLE]
        hev = _k.CreateEventW(byref(_sa), False, False, "Global\\KookrPullTick")
        if not hev:
            raise SystemExit(f"CreateEvent(KookrPullTick) failed (err {ctypes.get_last_error()})")
        kset = _k.SetEvent
        print(f"SYSVAD pull: KookrPullTick armed; output pinned to one period "
              f"({PULL_PERIOD} fr = {PULL_PERIOD / brir.samplerate * 1e3:.1f} ms) as the single master clock")

    # A0 diagnostic (RFC Track A, `--log-fifo-fill`): record output-FIFO fill vs time so the
    # standing `queue` term can be classified as clock skew (a sustained slope → the A1 async
    # resampler fixes it in user mode) vs jitter/burst pacing (bounce around a level → the lever
    # is the capture path, i.e. Track B). Off by default (``fifo_log`` is None), so the proven
    # path is byte-for-byte unchanged. When on, the output callback appends one cheap (t, frames)
    # row per call under the FIFO lock; the buffer is flushed to CSV at process exit. Host-only
    # measurement: the skew only exists between the real VoiceMeeter-capture and K11 clocks, so
    # this is the owner's to run (analyse with ``binaural_headtracker.fifo_drift``). Measure at a
    # fixed block size — ``n`` is captured once here, not re-read if the block is changed live.
    fifo_log: dict | None = None
    if args.log_fifo_fill is not None:
        fifo_log = {"rows": [], "t0": time.monotonic(), "n": blk["n"],
                    "path": Path(args.log_fifo_fill)}

        def _dump_fifo_log() -> None:
            rows = fifo_log["rows"]
            if not rows:
                return
            try:
                with open(fifo_log["path"], "w", encoding="utf-8") as fh:
                    fh.write("time_s,fill_frames\n")
                    for t_s, frames in rows:
                        fh.write(f"{t_s:.6f},{frames}\n")
                print(f"A0: wrote {len(rows)} FIFO-fill rows to {fifo_log['path']}")
            except OSError as exc:  # noqa: BLE001  — diagnostics only, never crash the app
                print(f"A0: failed to write {fifo_log['path']}: {exc!r}")

        atexit.register(_dump_fifo_log)

    # Live diagnostics the GUI panel reads (B6.3): render time per block (EWMA mean + a decaying
    # peak), and the timestamps of recent output underruns (dropouts). Updated off the GUI thread
    # — the worker writes render times, the output callback records underruns (one cheap append).
    meter = {"ms": 0.0, "peak": 0.0, "test_ms": 0.0,
             "under_ts": deque(maxlen=256),   # output FIFO underruns (starved)
             "over_ts": deque(maxlen=256)}    # FIFO overflow drops (clock drift / capture bursts)
    # Measured pose-age-at-ear (B8.5): the worker timestamps each rendered block against the pose
    # it carries and the audio still queued ahead of it, so the GUI shows a real distribution
    # (p50/p95/p99) instead of the old estimate. ``out_lat`` is the output device buffer (ms),
    # refreshed by the GUI loop from the live stream (the worker can't read it before it exists).
    ear_probe = LatencyProbe()        # total pose-age-at-ear (stale + queue + device)
    stale_probe = LatencyProbe()      # pose age when the block was rendered (camera + worker)
    queue_probe = LatencyProbe()      # time the block then waited in the output FIFO
    out_lat = {"ms": 0.0}

    # The render must NOT run in the audio callback: the full 8-ch BRIR convolution (tens of
    # ms, GPU sync) would hold the GIL and starve the capture/output callbacks, collapsing
    # throughput and dropping audio (measured: ~31 kHz effective, constant drops). Instead the
    # capture callback only *enqueues* the raw frames and returns in microseconds; a dedicated
    # worker thread drains the queue, renders, and fills the output FIFO. The callbacks stay
    # light, and the render's jitter is absorbed by the cushion — click-free, no dropouts.
    in_q: deque[np.ndarray] = deque()
    in_q_lock = threading.Lock()
    # Bound the captured-input backlog: if the render worker falls sub-real-time (e.g. the user
    # switches to the pure-Python CPU renderer), captured chunks would otherwise pile up unbounded
    # and the worker would stay stuck rendering an ever-growing buffer with the *old* renderer —
    # so switching back to a fast backend never seemed to take effect. Dropping the oldest stale
    # input keeps the backlog small, so a backend switch is picked up within one short cycle (and
    # the drop shows as an overflow, not silent). Generous (~0.25 s) so it never bites a keeping-up
    # renderer. See journal 51.
    in_q_max = max(8, 4 * cushion)
    stop_worker = threading.Event()
    mon = {"t": 0.0}

    def pa_in_cb(in_data, frame_count, _ti, _status):  # noqa: ANN001  (PyAudio loopback)
        try:
            # astype(copy) is essential: PyAudio reuses in_data after we return.
            cap = np.frombuffer(in_data, dtype=np.float32).reshape(-1, in_ch).astype(np.float64)
            with in_q_lock:
                in_q.append(cap)
                while len(in_q) > in_q_max:  # worker can't keep up → drop oldest stale input
                    in_q.popleft()
                    meter["over_ts"].append(time.monotonic())
        except BaseException as exc:  # noqa: BLE001
            cb_error.append(exc)
        return (None, pawp.paContinue)

    def render_worker() -> None:
        """Drain captured 7.1 → world-lock render → output FIFO, off the audio threads."""
        prio = _elevate_to_pro_audio()  # pro-audio scheduling so the OS doesn't preempt the render
        print(f"render thread: {prio} scheduling")
        while not stop_worker.is_set():
            with in_q_lock:
                chunks = list(in_q)
                in_q.clear()
            if not chunks:
                stop_worker.wait(0.001)  # idle: brief, interruptible pause
                continue
            try:
                with lock:
                    az = state["az"]
                    pose_t = state["pose_t"]
                    r_main = main_rend["r"]
                    cur_blk = blk["n"]      # snapshot with r_main so the slice width matches it
                    cur_eq = eqh["eq"]
                    t_on, t_rnd, t_az = test["on"], test["renderer"], test["az"]
                    t_gain, t_solo = test["gain"], test["solo"]
                cap = np.concatenate(chunks, axis=0)
                if args.monitor:
                    tnow = time.monotonic()
                    if tnow - mon["t"] > 1.0:
                        mon["t"] = tnow
                        rms = np.sqrt((cap ** 2).mean(axis=0) + 1e-20)
                        labels = " ".join(f"{c}={v:.3f}" for c, v in zip(brir.channels, rms,
                                                                          strict=True))
                        print("in RMS  " + labels, flush=True)
                buf = np.concatenate([in_tail["buf"], cap], axis=0)
                i = 0
                rendered = []
                t0 = time.perf_counter()
                test_dt = 0.0  # time spent on the test source alone (to split the load readout)
                while buf.shape[0] - i >= cur_blk:
                    stereo = r_main.process_block(buf[i : i + cur_blk], az)
                    if t_on:  # mix the movable test source (solo replaces the 7.1)
                        t_test0 = time.perf_counter()
                        idx = (test_pos["i"] + np.arange(cur_blk)) % test_loop.size
                        tstereo = t_rnd.process_block(test_loop[idx], t_az) * t_gain
                        test_pos["i"] = int((test_pos["i"] + cur_blk) % test_loop.size)
                        stereo = tstereo if t_solo else stereo + tstereo
                        test_dt += time.perf_counter() - t_test0
                    if cur_eq is not None:
                        stereo = cur_eq.process_block(stereo)
                    rendered.append(stereo * args.gain)
                    i += cur_blk
                in_tail["buf"] = buf[i:]
                if rendered:  # per-block render time: EWMA mean + a slowly-decaying peak
                    per_ms = (time.perf_counter() - t0) * 1000.0 / len(rendered)
                    meter["ms"] = 0.9 * meter["ms"] + 0.1 * per_ms if meter["ms"] else per_ms
                    meter["peak"] = max(per_ms, meter["peak"] * 0.97)
                    test_per_ms = test_dt * 1000.0 / len(rendered)
                    meter["test_ms"] = (0.9 * meter["test_ms"] + 0.1 * test_per_ms
                                        if meter["test_ms"] else test_per_ms)
                if rendered:
                    render_t = time.monotonic()  # tag every block of this batch with its pose + now
                    with fifo_lock:
                        out_fifo.extend((b, pose_t, render_t) for b in rendered)
                        while len(out_fifo) > max_backlog:  # bound latency: drop oldest
                            out_fifo.popleft()
                            # An overflow drop is an audible glitch too (discarded audio), from the
                            # capture/output clocks drifting faster than the cushion absorbs — count
                            # it, else "0 dropouts" lies while the ear hears glitches (B6.8).
                            meter["over_ts"].append(time.monotonic())
            except BaseException as exc:  # noqa: BLE001
                cb_error.append(exc)

    out_carry = {"buf": np.zeros((0, 2))}  # leftover rendered samples < one output request
    # Re-prime the cushion after a starvation (journal 81). The cushion is a one-shot *prefill*:
    # in the single-master-clock pull path the worker is rate-locked to the output (one
    # KookrPullTick per callback → one capture period in), so it produces exactly one block per
    # block it consumes and can NEVER get ahead to rebuild margin a transient has eaten. Left to
    # itself the FIFO therefore rides the 0–1-block floor after the first hiccup — the configured
    # cushion stops being margin and the next jitter underruns (the rare crack the owner hears).
    # On underrun we instead hold the output silent — the clock keeps ticking (kset above), so the
    # worker keeps filling — until the FIFO is back to the full ``cushion`` depth, then resume with
    # the whole cushion restored. Bounded: the rebuild silence lasts at most the cushion's own
    # latency, which the owner already accepts as head-move-to-ear delay. Flag is touched only by
    # this callback thread; the ``out_fifo`` read stays under ``fifo_lock``.
    reprime = {"active": False}

    def out_cb(outdata, frames, _t, status):  # noqa: ANN001  (FIFO → headphones)
        try:
            now = time.monotonic()
            if kset is not None:
                kset(hev)  # advance SYSVAD one period; output is pinned to one period/callback (no coalescing)
            dev = out_lat["ms"]
            buf = out_carry["buf"]
            with fifo_lock:
                if fifo_log is not None:  # A0: standing FIFO depth (frames) at this callback
                    fifo_log["rows"].append((now - fifo_log["t0"], len(out_fifo) * fifo_log["n"]))
                if reprime["active"]:
                    # Rebuilding after a starvation: stay silent until the cushion is whole again,
                    # so we resume with real margin instead of limping along at the empty floor.
                    if len(out_fifo) >= cushion:
                        reprime["active"] = False
                    else:
                        outdata.fill(0)
                        out_carry["buf"] = np.zeros((0, 2))
                        return
                while buf.shape[0] < frames and out_fifo:
                    block, pose_t, render_t = out_fifo.popleft()
                    # Measure pose-age-at-ear at the moment the block is handed to the device:
                    # stale (pose→render) + queue (render→now) + the device buffer.
                    if pose_t is not None:
                        stale_probe.add((render_t - pose_t) * 1000.0)
                        queue_probe.add((now - render_t) * 1000.0)
                        ear_probe.add((now - pose_t) * 1000.0 + dev)
                    buf = np.concatenate([buf, block], axis=0)
            n = min(frames, buf.shape[0])
            outdata[:n] = buf[:n].astype(np.float32)
            if n < frames:
                outdata[n:] = 0  # underrun → silence
                meter["under_ts"].append(time.monotonic())  # record the dropout (cheap)
                reprime["active"] = True   # rebuild the cushion to full depth before draining again
                buf = buf[:n]              # drop the sub-block remainder; re-prime restarts clean
            out_carry["buf"] = buf[n:]
        except BaseException as exc:  # noqa: BLE001
            cb_error.append(exc)
            outdata.fill(0)

    detector = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        output_facial_transformation_matrixes=True, num_faces=1))
    cap = cv2.VideoCapture(cam_index)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # freshest frame, don't trail buffered ones
    cam_w, cam_h, cam_fps = C.apply_camera_settings(
        cap, width=args.cam_width, height=args.cam_height, fps=args.cam_fps)
    recent: deque[HeadPose] = deque(maxlen=20)
    pose_filter = PoseFilter(args.min_cutoff, args.beta)
    flip_az = args.flip_az

    # Capture = PyAudio WASAPI loopback (raw 7.1); output = sounddevice to the headphones
    # (blocksize=0 lets it pick the host-native buffer; the callbacks handle any frame count).
    in_stream = pya.open(format=pawp.paFloat32, channels=in_ch, rate=brir.samplerate,
                         input=True, input_device_index=lb["index"],
                         frames_per_buffer=args.block, stream_callback=pa_in_cb)
    # Move the big, long-lived graph (BRIRs, dense grids, renderers, FFT plans) out of the GC's
    # generational scanning: a routine collection landing mid-block is a render-deadline spike, and
    # after setup that graph is static. freeze() keeps it out of every future sweep (so sweeps stay
    # cheap) without disabling GC — transient per-block allocations and live-rebuilt renderers are
    # still collected normally. See AGENTS.md perf notes.
    import gc
    gc.collect()
    gc.freeze()
    # Tighten the GIL hand-off so the real-time render worker is never starved by a GIL-greedy
    # thread. The convolution itself runs GIL-free (rust/src/lib.rs `py.allow_threads`), but the
    # worker still needs the GIL for its per-block Python glue — and in `--ui cockpit` the main
    # thread repaints the whole Dear PyGui canvas every frame (hundreds of `dpg.draw_*` calls),
    # holding the GIL. At CPython's default 5 ms switch interval the worker waited up to 5 ms for
    # the GIL each time it needed it, several times per block, compounding into ~150 ms stalls that
    # only a huge FIFO cushion could mask (console was clean at 20 ms; cockpit needed ~200 ms —
    # journal 76). A 0.5 ms interval hands the GIL back ~10x sooner, so the cockpit no longer forces
    # a deep cushion. Process-global and harmless to the console/bench paths (no contention there).
    import sys
    sys.setswitchinterval(0.0005)
    # Whole-process HIGH priority (journal 82): MMCSS above only covers the render worker, but it
    # needs the GIL from the camera/detect/main-loop threads, which run at the process base class. A
    # normal-priority CPU hog starves those and drops audio despite MMCSS, so lift the whole proc.
    _proc_prio = _raise_process_priority()
    print(f"process priority: {_proc_prio}")
    worker = threading.Thread(target=render_worker, name="render", daemon=True)
    worker.start()
    in_stream.start_stream()

    # Requested output buffer: ``--out-latency-ms`` in seconds, else 'low' (host-native minimum).
    # Raising it in exclusive mode trades a little latency for the slack the minimum buffer lacks.
    out_lat_req = (args.out_latency_ms / 1000.0) if args.out_latency_ms else "low"

    def _open_output(exclusive: bool):
        extra = sd.WasapiSettings(exclusive=True) if exclusive else None
        ctx = sd.OutputStream(samplerate=brir.samplerate, channels=2, device=out_index,
                              blocksize=(PULL_PERIOD if pull_mode else 0), dtype="float32",
                              latency=out_lat_req, callback=out_cb, extra_settings=extra)
        ctx.start()
        return ctx

    # WASAPI exclusive mode (B0.5) takes the endpoint for a much smaller device buffer (measured
    # ~13 ms vs ~22 ms shared, vs ~90 ms on MME). It can be refused (format/rate, or the device is
    # in use), so fall back to shared and say so rather than failing to start.
    try:
        out_ctx = _open_output(args.exclusive)
        mode = "exclusive" if args.exclusive else "shared"
        print(f"output: WASAPI {mode} ({out_ctx.latency * 1000:.0f} ms device buffer)")
    except Exception as exc:  # noqa: BLE001
        if not args.exclusive:
            raise
        print(f"WASAPI exclusive unavailable ({exc!r}); using shared mode.")
        out_ctx = _open_output(False)
        print(f"output: WASAPI shared ({out_ctx.latency * 1000:.0f} ms device buffer)")
    settings_store.mark_started()  # audio opened -> the risky start completed; clear the backstop
    _reset_req = {"v": False}  # set by the cockpit's reset hook; consumed after the loop exits
    def _run_cockpit() -> bool:
        """Drive the live engine through the Dear PyGui cockpit (B6.12 slice 2).

        Same audio path as the console — this only replaces the display + input. The camera/pose/
        diagnostics are computed exactly as the console loop does; the result feeds the cockpit's
        view-model, and the cockpit's control hooks mutate the live engine state (method, backend,
        predict, the test source placed/dragged on the radar, recenter). Returns True to reload."""
        nonlocal cap
        from binaural_headtracker import cockpit as CM

        import cockpit_web as CW
        # reopen at the last window size the owner left (persisted on resize); fall back to the
        # design default on first run / a bad file.
        _win_path = _cfg_dir / "window.json"
        _w0, _h0 = 1320, 864
        try:
            _wj = json.loads(_win_path.read_text(encoding="utf-8"))
            if 600 <= int(_wj["w"]) <= 10000 and 400 <= int(_wj["h"]) <= 10000:
                _w0, _h0 = int(_wj["w"]), int(_wj["h"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        win_ck = CW.WebCockpit(
            title=f"World-Locked Binaural Surround — webcam {cam_index}", width=_w0, height=_h0,
            port=args.port, open_browser=not args.no_browser)

        def _save_window_size(w, h) -> None:  # noqa: ANN001
            try:
                if 600 <= int(w) <= 10000 and 400 <= int(h) <= 10000:
                    _win_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = _win_path.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps({"w": int(w), "h": int(h)}), encoding="utf-8")
                    os.replace(tmp, _win_path)
            except (OSError, ValueError):
                pass
        win_ck.on_window_size = _save_window_size
        q = {"stop": False, "restart": False}
        cur = {"backend": ("rust" if args.rust else "gpu" if args.gpu else "cpu"),
               "predict": args.predict,
               "comp": (saved_cfg.get("comp") if saved_cfg.get("comp") in ("ctf", "df") else "ctf")}
        predictor_ck = PosePredictor()

        def _recenter() -> None:
            if recent:
                with lock:
                    state["cal"] = calibrate_neutral(
                        list(recent), yaw_sign=args.yaw_sign, pitch_sign=args.pitch_sign,
                        roll_sign=args.roll_sign)

        def _set_method(m: str) -> None:
            if m not in EXPERIMENT_METHODS:
                return
            if m not in test_renderers:
                test_renderers[m] = build_test_renderer(m)
            requested = cur["backend"]  # capture BEFORE the snap below, else the note sees no diff
            nm, reason = build_main_renderer(m, requested)
            actual = _backend_of(nm)
            with lock:
                main_rend["r"], main_rend["label"] = nm, render_label(nm, m)
                test["renderer"], test["method"] = test_renderers[m], m
            win_ck.set_method_display(m)  # keep the settings dialog's method seed in sync
            # The method's engine may differ from the selected backend (a requested accelerator can
            # be unavailable and fall back); keep the segment and toast honest about what's actually
            # running.
            if actual != requested:
                cur["backend"] = actual
                win_ck.set_backend_display(actual)
            note = CM.backend_switch_note(requested, actual, reason)
            if note:  # error on a real unavailability fallback, info on a method-forced remap
                win_ck.toast(note[0], note[1])

        def _set_backend(b: str) -> None:
            nm, reason = build_main_renderer(test["method"], b)
            actual = _backend_of(nm)  # what the renderer ACTUALLY runs on (not the request)
            with lock:
                main_rend["r"], main_rend["label"] = nm, render_label(nm, test["method"])
            cur["backend"] = actual                 # engine truth, never the failed request
            win_ck.set_backend_display(actual)      # snap the radio back if the switch didn't take
            note = CM.backend_switch_note(b, actual, reason)
            if note:                                # error on a real fallback, info on a remap
                win_ck.toast(note[0], note[1])

        def _tog(key: str):
            def f(on) -> None:  # noqa: ANN001
                with lock:
                    test[key] = bool(on)
            return f

        def _place(az) -> None:  # noqa: ANN001  — radar click/drag → world azimuth
            with lock:
                test["world_az"], test["on"], test["rotate"] = float(az), True, False

        def _set_cushion(ms) -> None:  # noqa: ANN001  — output FIFO cushion (ms), live
            nonlocal cushion, max_backlog, in_q_max
            nc = max(2, round(float(ms) * 1e-3 * brir.samplerate / blk["n"]))
            with fifo_lock:
                cushion, max_backlog, in_q_max = nc, nc * 2, max(8, 4 * nc)
                # grow immediately with a little silence so raising it takes audible effect now;
                # lowering just lets the worker trim to the smaller max_backlog next cycle.
                while len(out_fifo) < cushion:
                    out_fifo.appendleft((np.zeros((blk["n"], 2)), None, None))
            # keep the settings dialog's seed in sync: it re-reads this on open, so a stale value
            # would make a changed cushion appear to "snap back" when the dialog is reopened.
            win_ck.set_cushion_display(nc * blk["n"] / brir.samplerate * 1000.0)

        def _set_block(n) -> None:  # noqa: ANN001  — render block size (frames), live
            """Swap the render block size without a restart. Rebuilds every renderer + the output EQ
            at the new size and keeps the cushion's *duration* (not its block count) — all under
            ``lock`` so the worker never slices at one width while holding a renderer built for
            another. One brief glitch at the swap (the renderers lose their overlap tails); larger
            for GPU (cuFFT replan). The output stream is blocksize=0 so it needs no reopen."""
            nonlocal cushion, max_backlog, in_q_max
            n = int(n)
            old_blk = blk["n"]
            if n == old_blk or n < 32:
                return
            with lock:
                blk["n"] = n  # builders below now size to n
                nm, _ = build_main_renderer(test["method"], cur["backend"])
                main_rend["r"], main_rend["label"] = nm, render_label(nm, test["method"])
                test_renderers.clear()  # drop old-block test voices; rebuild the active one at n
                test_renderers[test["method"]] = build_test_renderer(test["method"])
                test["renderer"] = test_renderers[test["method"]]
                eqh["eq"] = build_output_eq(n)
                with fifo_lock:
                    cushion = max(2, round(cushion * old_blk / n))  # preserve the cushion's ms
                    max_backlog, in_q_max = cushion * 2, max(8, 4 * cushion)
                    while len(out_fifo) > max_backlog:          # block grew → trim surplus
                        out_fifo.popleft()
                    while len(out_fifo) < cushion:              # block shrank → top up to target
                        out_fifo.appendleft((np.zeros((n, 2)), None, None))
            win_ck.set_block_display(n)
            # block size changes the cushion's ms-per-block, so refresh its displayed value too
            win_ck.set_cushion_display(cushion * n / brir.samplerate * 1000.0)

        def _build_for_set(hs):
            """``(main_renderer, test_renderer, reason)`` for an HRIR set, at the current block
            size + backend. The owner's own set renders from the sparse 8 via the normal path; a
            dense-grid candidate renders its real ring via ``InterpWorldLocked7p1(dense_override)``
            steered by nearest (``brir`` is used only for 7.1 input routing + LFE)."""
            if hs.is_owner:
                nm, reason = build_main_renderer(test["method"], cur["backend"])
                return nm, build_test_renderer(test["method"]), reason
            dense, azmap = load_dense_grid_sofa(hs.path_for(cur["comp"]),
                                                target_rate=brir.samplerate)
            use_gpu = cur["backend"] == "gpu"
            use_rust = cur["backend"] in ("rust", "gpu")
            nm = InterpWorldLocked7p1(brir, block_size=blk["n"], fade_ms=args.fade_ms,
                                      lfe_mode=args.lfe_mode, dense_override=(dense, azmap),
                                      use_rust=use_rust, use_gpu=use_gpu)
            tr = WorldLockedSource(dense, block_size=blk["n"], fade_ms=args.fade_ms,
                                   max_tail_ms=args.max_tail_ms, azimuths=azmap)
            return nm, tr, None

        def _set_hrir(name, announce: bool = True) -> None:  # noqa: ANN001 — live HRIR-set swap
            """Swap the live HRIR set under ``lock``, modelled on :func:`_set_block`: rebuild the
            7.1 renderer + the test voice from the new set, leaving block size / cushion / output EQ
            untouched. A bad or missing SOFA toasts and leaves the current set playing."""
            hs = sets_by_name.get(name)
            if hs is None:
                win_ck.toast(f"no HRIR set '{name}'", "error")
                return
            try:
                nm, tr, _reason = _build_for_set(hs)
            except Exception as exc:  # noqa: BLE001 — a bad SOFA must not kill the audio engine
                win_ck.toast(f"HRIR '{name}' load failed: {exc}", "error")
                win_ck.set_hrir_display(cur_set["name"])  # snap the pills back to what's playing
                return
            label = (render_label(nm, test["method"]) if hs.is_owner
                     else f"{getattr(nm, 'engine', 'cpu')} · {name} [{cur['comp']}] · "
                          f"dense ({nm.n_bus} pts)")
            if not announce:   # blind A/B: mask the status label too (it names the set)
                label = "— blind A/B (hidden) —"
            with lock:
                main_rend["r"], main_rend["label"] = nm, label
                test_renderers.clear()
                test_renderers[test["method"]] = tr
                test["renderer"] = tr
                cur_set["name"] = name
            if announce:   # blind A/B swaps silently so the toast/pill don't reveal the set
                win_ck.set_hrir_display(name)
                if not hs.is_owner:
                    tag = cur["comp"] + ("" if hs.has_df or cur["comp"] == "ctf" else "→ctf")
                    win_ck.toast(f"HRIR: {name} [{tag}]", "info", seconds=3)

        def _set_comp(m) -> None:  # noqa: ANN001  — compensation variant (ctf | df)
            if m not in ("ctf", "df"):
                return
            cur["comp"] = m
            win_ck.set_comp_display(m)
            _set_hrir(cur_set["name"])   # reload the current subject under the new comp

        # --- blind localization test (RFC 0003 decider) -------------------------------
        from pathlib import Path as _Path
        results_dir = (_Path(args.hrir_sets_dir).parent / "test_results"
                       if args.hrir_sets_dir else _Path("test_results"))
        try:
            results_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass

        # Session telemetry (B*): sample the live health + current settings to a JSONL every few
        # seconds, so issues (underruns/overruns, latency creep, render overload) and the settings
        # tried over time are recoverable after the fact for diagnosis. Line-buffered so a crash
        # keeps the data; failures here never touch the audio path.
        import datetime as _dt_tel
        import json as _json_tel
        _sess_ts = _dt_tel.datetime.now().strftime("%Y%m%d_%H%M%S")
        telem = {"last": 0.0, "every": 3.0, "t0": time.monotonic(), "fh": None}
        try:
            telem["fh"] = open(results_dir / f"session_{_sess_ts}.jsonl", "a", buffering=1)
        except Exception:  # noqa: BLE001
            telem["fh"] = None

        loc = {"test": None, "t0": None, "cache": {}, "comp": "ctf"}

        def _write_loc_results(t):
            """Persist a localization test: a per-trial CSV + scoreboard JSON; returns JSON path."""
            import csv as _csv
            import datetime as _datetime
            import json as _json
            ts = _datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base = results_dir / f"loc_{ts}"
            with open(base.with_suffix(".csv"), "w", newline="") as f:
                w = _csv.writer(f)
                w.writerow(["trial", "set", "comp", "truth_az", "guess_az",
                            "abs_err_deg", "fb_confusion", "rt_s"])
                for i, tr in enumerate(t.trials):
                    w.writerow([i, tr.set_name, loc["comp"], round(tr.truth_az, 1),
                                "" if tr.guess_az is None else round(tr.guess_az, 1),
                                "" if tr.abs_err_deg is None else round(tr.abs_err_deg, 1),
                                int(tr.fb_confusion),
                                "" if tr.rt_s is None else round(tr.rt_s, 2)])
            base.with_suffix(".json").write_text(_json.dumps(
                {"timestamp": ts, "comp": loc["comp"], "method": test["method"],
                 "n_per_set": t.n_per_set, "total": t.total,
                 "scoreboard": t.scoreboard(), "ranking": t.ranking()}, indent=2))
            return base.with_suffix(".json")

        def _loc_renderer(hs):
            """Cached test-source renderer for a set + current comp (solo'd in the loc test)."""
            key = (hs.name, cur["comp"])
            if key not in loc["cache"]:
                loc["cache"][key] = _build_for_set(hs)[1]
            return loc["cache"][key]

        def _loc_apply() -> None:
            """Render the current trial: solo the source at its truth azimuth through the
            (hidden) set, start the response timer, prompt for a radar click."""
            t = loc["test"]
            tr = t.current
            if tr is None:
                return
            renderer = _loc_renderer(sets_by_name[tr.set_name])
            with lock:
                test["renderer"] = renderer
                test["world_az"], test["on"], test["solo"], test["rotate"] = \
                    float(tr.truth_az), True, True, False
            loc["t0"] = time.monotonic()
            win_ck.set_loc_status(f"trial {t.i + 1}/{t.total} — click where you hear it")

        def _loc_finish() -> None:
            t = loc["test"]
            path = _write_loc_results(t)
            rank = t.ranking()
            win_ck.set_loc_status(f"done · best: {rank[0] if rank else '-'} · saved {path.name}")
            win_ck.toast(f"loc results saved → {path}", "info", seconds=8)
            loc["test"] = None
            win_ck.set_loc_active(False)
            with lock:
                test["on"], test["solo"] = False, False
            _set_hrir(cur_set["name"])

        def _loc_start(n) -> None:  # noqa: ANN001
            win_ck.toast("preparing localization test…", "info", seconds=3)
            try:
                for hs in hrir_sets:          # preload each set's renderer once (one-off)
                    _loc_renderer(hs)
            except Exception as exc:          # noqa: BLE001
                win_ck.toast(f"loc test prep failed: {exc}", "error")
                return
            import random as _random
            loc["comp"] = cur["comp"]     # pin the compensation for the whole session (for the log)
            loc["test"] = LocalizationTest.build([s.name for s in hrir_sets],
                                                 n_per_set=int(n), rng=_random.Random())
            win_ck.set_loc_active(True)
            _loc_apply()

        def _loc_answer(guess_az) -> None:  # noqa: ANN001
            t = loc["test"]
            if t is None:
                return
            rt = None if loc["t0"] is None else (time.monotonic() - loc["t0"])
            t.answer(float(guess_az), rt_s=rt)
            _loc_finish() if t.done else _loc_apply()

        def _loc_stop() -> None:
            if loc["test"] is None:
                return
            t = loc["test"]
            if any(tr.answered for tr in t.trials):
                path = _write_loc_results(t)
                win_ck.set_loc_status(f"stopped · partial saved {path.name}")
                win_ck.toast(f"partial loc results saved → {path}", "info", seconds=8)
            else:
                win_ck.set_loc_status("stopped")
            loc["test"] = None
            win_ck.set_loc_active(False)
            with lock:
                test["on"], test["solo"] = False, False
            _set_hrir(cur_set["name"])

        def _blind_result(mapping, tally) -> None:  # noqa: ANN001 — A/B reveal → log the result
            import datetime as _datetime
            import json as _json
            ts = _datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = results_dir / f"ab_{ts}.json"
            try:
                path.write_text(_json.dumps(
                    {"timestamp": ts, "comp": cur["comp"],
                     "mapping": dict(mapping), "tally": dict(tally)}, indent=2))
                win_ck.toast(f"A/B saved → {path}", "info", seconds=6)
            except Exception as exc:  # noqa: BLE001
                win_ck.toast(f"A/B save failed: {exc}", "error")

        def _reset_defaults() -> None:
            """Reset-to-defaults (settings dialog). Flag it, stop the loop, and relaunch; the actual
            store.reset() runs after the loop exits (race-free: no auto-save can overwrite it) so the
            relaunch loads the built-in defaults. Does NOT touch head-tracking calibration."""
            win_ck.toast("Settings reset to defaults — reloading…", "info", seconds=4)
            _reset_req["v"] = True
            q["restart"] = True
            q["stop"] = True

        # Starvation watch (advisory): block/cushion apply live; if the setting then keeps starving
        # the output we TOAST a warning — we do NOT force-revert. The user can hear it, change it
        # back, or hit Cancel (which already restores the pre-dialog snapshot); block/cushion only
        # cause dropouts, never an unreachable UI, so a forced revert just fought the user on
        # genuinely-marginal-but-usable depths. The watch IGNORES the first _STARVE_SETTLE_S: a
        # block-size change rebuilds every renderer under the lock and a cushion change re-tops the
        # FIFO, both a one-time swap burst that must NOT read as "the new setting is bad".
        _STARVE_SETTLE_S = 1.2    # swallow the swap/top-up transient
        _STARVE_WINDOW_S = 4.0    # stop watching after this if it settled
        _STARVE_UNDERRUNS = 8     # underruns AFTER settle that mean the depth genuinely can't hold
        _watch: dict = {"t0": 0.0, "knob": ""}

        # settings edit session (web dialog): changes apply live but are NOT persisted until the
        # user Confirms; Cancel restores the snapshot from when the dialog opened. ``snap`` non-None
        # means a session is open, which suspends the loop's auto-save. The DPG cockpit never opens
        # a session (no dialog), so there it stays None and auto-save behaves as before.
        _edit: dict = {"snap": None}

        def _knobs_now() -> dict:
            return _SB.current_knobs(
                method=test["method"], backend=cur["backend"], block=blk["n"],
                cushion_blocks=cushion, exclusive=bool(args.exclusive),
                hrir=cur_set["name"], comp=cur["comp"], predict=cur["predict"],
                camera=cam_index, cam_w=cam_w, cam_h=cam_h, cam_fps=cam_fps,
                hp_eq=(args.output_eq is not None))

        def _apply_snapshot(s: dict) -> None:
            """Restore the live engine to a snapshot (dialog Cancel). exclusive/hp_eq don't apply
            live (they need a restart), so for those only the displayed value is put back."""
            if int(s["block"]) != blk["n"]:
                _set_block(int(s["block"]))
            if s["method"] != test["method"]:
                _set_method(s["method"])
            if s["backend"] != cur["backend"]:
                _set_backend(s["backend"])
            if s["comp"] != cur["comp"]:
                _set_comp(s["comp"])
            if s["hrir"] != cur_set["name"]:
                _set_hrir(s["hrir"])
            _set_cushion(int(s["cushion_blocks"]) * blk["n"] / brir.samplerate * 1000.0)
            cur["predict"] = bool(s["predict"])
            win_ck.set_exclusive_display(bool(s["exclusive"]))
            win_ck.set_hpeq_display(bool(s["hp_eq"]))

        def _settings_begin() -> None:
            _edit["snap"] = _knobs_now()  # restore point; suspends auto-save until commit/cancel

        def _settings_commit() -> None:
            nonlocal _last_knobs
            if _edit["snap"] is None:
                return
            kn = _knobs_now()
            try:
                settings_store.save(kn)  # persist the previewed state (atomic + durable)
            except Exception:  # noqa: BLE001 — persistence must never crash the engine
                pass
            _last_knobs = kn
            _edit["snap"] = None

        def _settings_cancel() -> None:
            nonlocal _last_knobs
            snap = _edit["snap"]
            if snap is None:
                return
            _apply_snapshot(snap)        # revert live engine while the session still blocks saving
            _last_knobs = snap           # saved file already = last-confirmed, so persist nothing
            _edit["snap"] = None

        def _arm_watch(knob: str) -> None:
            _watch["knob"], _watch["t0"] = knob, time.monotonic()

        def _cushion_user(ms) -> None:  # noqa: ANN001 — settings-dialog cushion change (watched)
            _set_cushion(ms)
            _arm_watch("Cushion")

        def _block_user(n) -> None:  # noqa: ANN001 — settings-dialog block change (watched)
            old_n = blk["n"]
            _set_block(n)
            if blk["n"] != old_n:  # _set_block no-ops on unchanged/invalid sizes -> don't arm
                _arm_watch("Block size")

        win_ck.on_quit = lambda: q.__setitem__("stop", True)
        win_ck.on_reset_defaults = _reset_defaults
        win_ck.on_settings_begin = _settings_begin
        win_ck.on_settings_commit = _settings_commit
        win_ck.on_settings_cancel = _settings_cancel
        win_ck.on_recenter = _recenter
        win_ck.on_method = _set_method
        win_ck.on_backend = _set_backend
        win_ck.on_cushion = _cushion_user
        win_ck.on_block = _block_user
        win_ck.on_test = _tog("on")
        win_ck.on_solo = _tog("solo")
        win_ck.on_rotate = _tog("rotate")
        win_ck.on_place_test = _place
        win_ck.on_predict = lambda on: cur.__setitem__("predict", bool(on))
        win_ck.on_hrir = _set_hrir
        win_ck.on_hrir_blind = lambda n: _set_hrir(n, announce=False)
        win_ck.on_comp = _set_comp
        win_ck.on_loc_start = _loc_start
        win_ck.on_loc_answer = _loc_answer
        win_ck.on_loc_stop = _loc_stop
        win_ck.on_blind_result = _blind_result
        win_ck.set_hrir_sets([s.name for s in hrir_sets])   # seed the pills BEFORE setup()
        win_ck.setup()
        win_ck.set_cushion_display(args.latency_ms)  # slider → the real launch cushion, not 60
        win_ck.set_block_display(blk["n"])           # stepper → the real launch block size
        win_ck.set_hrir_display(cur_set["name"])     # pills → the set actually loaded at launch
        win_ck.set_comp_display(cur["comp"])         # comp segment → the launch compensation
        win_ck.set_method_display(test["method"])    # method dropdown → the launch method
        win_ck.set_exclusive_display(bool(args.exclusive))  # exclusive toggle → launch value
        win_ck.set_hpeq_display(args.output_eq is not None)  # HP-EQ toggle → launch value
        cur["backend"] = _backend_of(main_rend["r"])  # the engine that actually built at launch
        win_ck.set_backend_display(cur["backend"])    # radio → real launch backend (not 'rust'),
        if args.gpu and cur["backend"] != "gpu":      # and warn if --gpu silently fell back
            win_ck.toast(f"GPU unavailable at launch — running {cur['backend'].upper()}", "error")

        detect_ms = 0.0
        times_ck: deque[float] = deque(maxlen=60)
        miss = 0
        predict_ms_used = 0.0  # persist across frames so a no-face frame doesn't blink it to 0
        _last_knobs = dict(saved_cfg)  # auto-save: persist only when a knob actually changes
        try:
            while win_ck.running() and not q["stop"]:
                if cb_error:
                    print(f"audio callback error: {cb_error[0]!r}")
                    break
                ok, frame = cap.read()
                if not ok:  # camera lost — hold the last pose (audio keeps world-locking)
                    miss += 1
                    if miss % 90 == 0:
                        cap.release()
                        cap = cv2.VideoCapture(cam_index)
                    win_ck.render_frame()
                    continue
                miss = 0
                now = time.monotonic()
                times_ck.append(now)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # un-mirrored → real yaw sign
                td = time.perf_counter()
                result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                dms = (time.perf_counter() - td) * 1000.0
                detect_ms = 0.9 * detect_ms + 0.1 * dms if detect_ms else dms
                disp = cv2.flip(rgb, 1)  # mirror for display only (cockpit camera panel is RGB)
                landmarks = result.face_landmarks[0] if result.face_landmarks else None
                pose = None
                with lock:
                    cal = state["cal"]
                if result.facial_transformation_matrixes:
                    yaw, pitch, roll = head_pose_from_matrix(
                        np.asarray(result.facial_transformation_matrixes[0]))
                    pose = pose_filter.update(HeadPose(yaw, pitch, roll, now))
                    recent.append(pose)
                    pose_for_az = pose
                    if cur["predict"]:
                        predictor_ck.update(pose)
                        es = ear_probe.stats()
                        predict_ms_used = (args.predict_ms if args.predict_ms > 0
                                           else (es.p50 if es.n > 30 else 60.0))
                        pose_for_az = predictor_ck.predict(pose, predict_ms_used)
                    dirs = directions_for_pose(cal, pose_for_az, layout)
                else:
                    dirs = layout.head_relative_directions()
                if flip_az:
                    dirs = dirs.copy()
                    dirs[:, 0] = -dirs[:, 0]
                if pose is not None:
                    with lock:
                        state["az"] = dirs[:, 0].copy()
                        state["pose_t"] = pose.t
                nearest_i = nearest_front_channel(dirs)
                head_deg = -float(dirs[c_idx][0])
                dt = (times_ck[-1] - times_ck[-2]) if len(times_ck) >= 2 else 0.0
                with lock:
                    if test["rotate"]:
                        test["world_az"] = (test["world_az"] + args.rotate_speed * dt + 180.0) \
                            % 360.0 - 180.0
                    test["az"] = float(test["world_az"]) + float(dirs[c_idx][0])
                    t_on, t_world_az = test["on"], test["world_az"]
                span = times_ck[-1] - times_ck[0]
                rate = (len(times_ck) - 1) / span if len(times_ck) > 1 and span > 0 else 0.0
                with fifo_lock:
                    fifo_fill = len(out_fifo)
                recent_under = sum(1 for t in tuple(meter["under_ts"]) if now - t < 2.0)
                recent_over = sum(1 for t in tuple(meter["over_ts"]) if now - t < 2.0)
                if _watch["knob"]:  # a block/cushion change is being watched for starvation
                    settle_t = _watch["t0"] + _STARVE_SETTLE_S
                    if now >= settle_t:  # past the swap transient -> judge sustained starvation
                        since = sum(1 for t in tuple(meter["under_ts"]) if t > settle_t)
                        if since >= _STARVE_UNDERRUNS:    # still starving -> warn (no revert)
                            kn = _watch["knob"]
                            _watch["knob"] = ""
                            win_ck.toast(f"{kn} is starving the output — raise it or lower the "
                                         f"block size", "error", seconds=6)
                        elif now - _watch["t0"] >= _STARVE_WINDOW_S:  # settled -> stop watching
                            _watch["knob"] = ""
                out_lat_ms = float(getattr(out_ctx, "latency", 0.0) or 0.0) * 1000.0
                out_lat["ms"] = out_lat_ms
                rlabel = main_rend["label"] + ("  · test ON" if t_on else "")
                ear = ear_probe.stats()
                diag = classify(
                    block_size=blk["n"], samplerate=brir.samplerate,
                    render_ms=meter["ms"], render_ms_peak=meter["peak"],
                    render_ms_test=(meter["test_ms"] if t_on else 0.0),
                    render_label=rlabel, fifo_fill_blocks=fifo_fill, cushion_blocks=cushion,
                    output_latency_ms=out_lat_ms,
                    input_latency_ms=1000.0 * blk["n"] / brir.samplerate,
                    pose_rate_hz=rate, recent_underruns=recent_under, recent_overflows=recent_over,
                    measured_pose_age_ms=(ear.p50 if ear.n else None),
                    measured_pose_age_p95_ms=ear.p95, measured_pose_age_p99_ms=ear.p99,
                    measured_pose_age_n=ear.n, measured_stale_ms=stale_probe.stats().p50,
                    measured_queue_ms=queue_probe.stats().p50, measured_device_ms=out_lat_ms,
                    detect_ms=detect_ms, camera_fps=cam_fps, cam_w=cam_w, cam_h=cam_h)
                if telem["fh"] is not None and now - telem["last"] >= telem["every"]:
                    telem["last"] = now
                    try:
                        telem["fh"].write(_json_tel.dumps({
                            "t": _dt_tel.datetime.now().isoformat(timespec="seconds"),
                            "uptime_s": round(now - telem["t0"], 1),
                            "settings": {
                                "block": blk["n"], "cushion_blocks": cushion,
                                "method": test["method"], "backend": cur["backend"],
                                "comp": cur["comp"], "hrir_set": cur_set["name"],
                                "predict": cur["predict"], "exclusive": bool(args.exclusive),
                                "grid_step": args.grid_step, "render": rlabel},
                            "stats": {
                                "lat_p50_ms": round(ear.p50, 1) if ear.n else None,
                                "lat_p95_ms": round(ear.p95, 1) if ear.n else None,
                                "lat_p99_ms": round(ear.p99, 1) if ear.n else None, "lat_n": ear.n,
                                "stale_ms": round(stale_probe.stats().p50, 1),
                                "queue_ms": round(queue_probe.stats().p50, 1),
                                "device_ms": round(out_lat_ms, 1),
                                "render_ms": round(meter["ms"], 2),
                                "render_peak_ms": round(meter["peak"], 2),
                                "render_budget_ms": round(1000.0 * blk["n"] / brir.samplerate, 2),
                                "fifo_fill_blocks": fifo_fill,
                                "underruns_2s": recent_under, "overruns_2s": recent_over,
                                "underruns_buf": len(meter["under_ts"]),
                                "overruns_buf": len(meter["over_ts"]),
                                "health": str(getattr(diag, "health", "") or ""),
                                "tracker_hz": round(rate, 1)}}) + "\n")
                    except Exception:  # noqa: BLE001 — telemetry must never crash the app
                        pass
                view = CM.build_view(
                    diag, latency_samples=ear_probe.samples(), rate_hz=rate,
                    method=test["method"], backend=cur["backend"],
                    predict_ms=(predict_ms_used if cur["predict"] else None),
                    facing_label=brir.channels[nearest_i], flipped=flip_az,
                    dropout_over_ts=tuple(meter["over_ts"]),
                    dropout_under_ts=tuple(meter["under_ts"]), now=now)
                win_ck.set_test_state(t_on, t_world_az)  # reflect engine state on the radar
                # Auto-save direct changes (e.g. main-page Predict, DPG cockpit) — but NOT while a
                # settings-dialog edit session is open: those changes are live-previewed and only
                # persisted on Confirm (or discarded on Cancel). See _settings_begin/commit/cancel.
                _cur_knobs = _knobs_now()
                if (_cur_knobs != _last_knobs and not q["stop"]
                        and _edit["snap"] is None):  # persist a change (atomic + durable)
                    _last_knobs = _cur_knobs
                    try:
                        settings_store.save(_cur_knobs)
                    except Exception:  # noqa: BLE001 — persistence must never crash the engine
                        pass
                win_ck.update(view, camera_rgb=disp, landmarks=landmarks, pose=pose,
                              world_az=world_az, channels=brir.channels,
                              facing_i=nearest_i, head_deg=head_deg,
                              diag=diag, rate_hz=rate,
                              cushion_ms=cushion * blk["n"] / brir.samplerate * 1000.0,
                              predict=cur["predict"], exclusive=bool(args.exclusive),
                              cam_fps=cam_fps, cam_w=cam_w, cam_h=cam_h,
                              hrir_name=cur_set["name"], comp=cur["comp"], cal=cal)
                win_ck.render_frame()
        finally:
            win_ck.teardown()
            if telem["fh"] is not None:
                try:
                    telem["fh"].close()
                except Exception:  # noqa: BLE001
                    pass
        return q["restart"]

    restart = False  # Reload key (Enter): tear down cleanly, then the launcher relaunches us
    try:
        restart = _run_cockpit()
        if _reset_req["v"]:  # loop exited -> no auto-save can race the reset write
            settings_store.reset()
            print("settings: reset to defaults (reloading)")
    finally:
        stop_worker.set()
        worker.join(timeout=1.0)
        out_ctx.stop()
        out_ctx.close()
        if hev:
            try:
                import ctypes
                ctypes.WinDLL("kernel32").CloseHandle(hev)  # close KookrPullTick; NEVER unload the driver
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
        in_stream.stop_stream()
        in_stream.close()
        pya.terminate()
        cap.release()
        cv2.destroyAllWindows()
        es = ear_probe.stats()  # measured pose-age-at-ear distribution for the session (B8.5)
        if es.n:
            ss, qs = stale_probe.stats(), queue_probe.stats()
            print(f"pose-age-at-ear (measured, n={es.n}): p50 {es.p50:.0f}  p95 {es.p95:.0f}  "
                  f"p99 {es.p99:.0f}  min {es.min:.0f}  max {es.max:.0f} ms  "
                  f"(+ ~1 camera frame of exposure not counted)", flush=True)
            print(f"  split p50: stale {ss.p50:.0f} (camera+worker) + queue {qs.p50:.0f} (FIFO) + "
                  f"device {out_lat['ms']:.0f} ms", flush=True)
    return RESTART_EXIT_CODE if restart else 0


if __name__ == "__main__":
    raise SystemExit(main())

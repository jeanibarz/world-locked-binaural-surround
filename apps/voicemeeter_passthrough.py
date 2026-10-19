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

FG = (245, 237, 232)
MUTED = (171, 151, 139)
ACCENT = (214, 214, 56)
EXPERIMENT_METHODS = ("nearest", "timealign", "minphase", "notchwarp")
# main() returns this so the host launcher can relaunch (pick up code changes) without the
# user re-running run.cmd. run_passthrough.cmd loops while it sees this exit code; any other
# code (0 = quit) ends. Kept distinct from 0/1/signal codes. See AGENTS.md / journal 39.
RESTART_EXIT_CODE = 97


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
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--input-device", default=None,
                   help="7.1 capture device index or name substring (e.g. 'VoiceMeeter Output')")
    p.add_argument("--audio-device", default=None,
                   help="headphone output device index or name substring (e.g. 'FiiO')")
    p.add_argument("--exclusive", action="store_true",
                   help="open the output in WASAPI exclusive mode (smallest device buffer, ~13 vs "
                        "~22 ms shared); falls back to shared if the device refuses it")
    p.add_argument("--no-dialog", action="store_true", help="skip the picker, use the flags")
    p.add_argument("--block", type=int, default=512, help="render block size (bigger = steadier)")
    p.add_argument("--fade-ms", type=float, default=20.0)
    p.add_argument("--gain", type=float, default=1.0)
    p.add_argument("--latency-ms", type=float, default=60.0,
                   help="output FIFO cushion (ms). This is the dominant head-move-to-ear delay: "
                        "a head turn is heard ~this late, since the cushion of already-rendered "
                        "audio drains first. Lower = snappier; raise if you get dropouts.")
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
    p.add_argument("--ui", choices=("console", "cockpit"), default="console",
                   help="front-end: 'console' = the OpenCV window (default); 'cockpit' = the "
                        "native Dear PyGui cockpit (B6.12) — same engine, resizable, with live "
                        "method/backend/predict/test controls. Needs the 'gui' extra (dearpygui).")
    args = p.parse_args(argv)

    brir = load_baked_brir(args.brir_dir)

    def _resolve_backend(backend: str | None) -> tuple[bool, bool]:
        """(use_rust, use_gpu) for an explicit backend ('cpu'/'rust'/'gpu'), else the launch flags.
        Lets the cockpit switch backend live (B6.12 slice 2) without restarting."""
        if backend is None:
            return args.rust, args.gpu
        return backend == "rust", backend == "gpu"

    def _nearest_renderer(backend: str | None = None):
        use_rust, use_gpu = _resolve_backend(backend)
        if use_rust:
            try:
                from binaural_headtracker.rust_render import RustWorldLocked7p1
                print("render: nearest — native Rust kernel (CPU, no GPU)")
                return RustWorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms,
                                          lfe_mode=args.lfe_mode)
            except Exception as exc:  # noqa: BLE001  — fall back to pure-Python CPU
                print(f"Rust kernel unavailable ({exc!r}); falling back to CPU.")
        elif use_gpu:
            try:
                from binaural_headtracker.gpu_render import GpuWorldLocked7p1
                print("render: nearest — GPU (CuPy)")
                return GpuWorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms,
                                         lfe_mode=args.lfe_mode)
            except Exception as exc:  # noqa: BLE001  — fall back to CPU
                print(f"GPU render unavailable ({exc!r}); falling back to CPU.")
        print("render: nearest — pure-Python CPU (pass --rust or --gpu for real time)")
        return WorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms,
                              lfe_mode=args.lfe_mode)

    def build_main_renderer(method: str, backend: str | None = None):
        """The full 7.1 renderer for ``method`` — the method applies to the WHOLE surround
        (shared with the test source). 'nearest' uses the fast 8-bus path; a synthesis method
        uses the dense interpolated grid (InterpWorldLocked7p1, B4.4), which needs a bigger
        block for headroom (the diagnostics panel shows if it keeps up). ``backend`` (cpu/rust/gpu)
        overrides the launch flags so the cockpit can switch it live."""
        use_rust, _ = _resolve_backend(backend)
        if method == "nearest":
            return _nearest_renderer(backend)
        print(f"render: {method} — dense-grid 7.1 ({args.grid_step:.0f}°) "
              f"{'[rust]' if use_rust else '[cpu]'}")
        return InterpWorldLocked7p1(brir, method, block_size=args.block, fade_ms=args.fade_ms,
                                    grid_step=args.grid_step, lfe_mode=args.lfe_mode,
                                    use_rust=use_rust)

    def render_label(renderer, method: str) -> str:
        """A one-line description of what the 7.1 is actually running: engine · method [· grid]."""
        if isinstance(renderer, InterpWorldLocked7p1):
            return (f"{renderer.engine} · {method} · "
                    f"{renderer.grid_step:.0f}° grid ({renderer.n_bus} bus)")
        # nearest: engine is the renderer's own class (rust/gpu fall back to cpu silently)
        eng = {"RustWorldLocked7p1": "rust", "GpuWorldLocked7p1": "gpu",
               "WorldLocked7p1": "cpu"}.get(type(renderer).__name__, "cpu")
        return f"{eng} · nearest (8-bus)"

    main_rend = {"r": build_main_renderer(args.method)}  # the 7.1 renderer, swappable live (M)
    main_rend["label"] = render_label(main_rend["r"], args.method)

    # Global output-bus comp + EQ (B5.3): the bare BRIRs carry room/direction but not the
    # headphone comp or subjective EQ, which EqualizerAPO applies once to the stereo mix. Apply
    # the same per-ear chain here so the render sounds like the tuned config, not the thin bake.
    output_eq = None
    if args.output_eq is not None:
        output_eq = load_output_eq(
            args.output_eq, samplerate=brir.samplerate, block_size=args.block)
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
            return WorldLockedSource(brir, block_size=args.block, fade_ms=args.fade_ms,
                                     max_tail_ms=args.max_tail_ms)
        # Dense grid: a moving source sweeps through many grid points, each spinning up a
        # full-length convolution voice that otherwise rings its whole ~0.5 s tail. Cap the
        # retired-voice tail (--max-tail-ms) so concurrent voices stay bounded → no dropouts.
        dense, azmap = interpolated_ring(brir, method, step_deg=args.grid_step)
        return WorldLockedSource(dense, block_size=args.block, fade_ms=args.fade_ms,
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
    in_spec = (args.input_device or "VoiceMeeter Input").lower()
    lb = next((d for d in pya.get_loopback_device_info_generator()
               if in_spec in d["name"].lower()), None)
    if lb is None:
        raise SystemExit(f"no WASAPI loopback device matching {in_spec!r} — is VoiceMeeter "
                         "running and set as the default 7.1 playback device?")
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
    cushion = max(2, round(args.latency_ms * 1e-3 * brir.samplerate / args.block))
    # Each FIFO entry is (stereo_block, pose_t, render_t) so the output callback can measure the
    # pose-age-at-ear precisely at *playout* and split it into stale / queue / device (B8.5e).
    out_fifo: deque = deque((np.zeros((args.block, 2)), None, None) for _ in range(cushion))
    fifo_lock = threading.Lock()
    in_tail = {"buf": np.zeros((0, brir.n_channels))}  # leftover capture < one block
    cb_error: list[BaseException] = []
    max_backlog = cushion * 2  # cap latency growth from drift at ~2x the target cushion

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
    stop_worker = threading.Event()
    mon = {"t": 0.0}

    def pa_in_cb(in_data, frame_count, _ti, _status):  # noqa: ANN001  (PyAudio loopback)
        try:
            # astype(copy) is essential: PyAudio reuses in_data after we return.
            cap = np.frombuffer(in_data, dtype=np.float32).reshape(-1, in_ch).astype(np.float64)
            with in_q_lock:
                in_q.append(cap)
        except BaseException as exc:  # noqa: BLE001
            cb_error.append(exc)
        return (None, pawp.paContinue)

    def render_worker() -> None:
        """Drain captured 7.1 → world-lock render → output FIFO, off the audio threads."""
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
                while buf.shape[0] - i >= args.block:
                    stereo = r_main.process_block(buf[i : i + args.block], az)
                    if t_on:  # mix the movable test source (solo replaces the 7.1)
                        t_test0 = time.perf_counter()
                        idx = (test_pos["i"] + np.arange(args.block)) % test_loop.size
                        tstereo = t_rnd.process_block(test_loop[idx], t_az) * t_gain
                        test_pos["i"] = int((test_pos["i"] + args.block) % test_loop.size)
                        stereo = tstereo if t_solo else stereo + tstereo
                        test_dt += time.perf_counter() - t_test0
                    if output_eq is not None:
                        stereo = output_eq.process_block(stereo)
                    rendered.append(stereo * args.gain)
                    i += args.block
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

    def out_cb(outdata, frames, _t, status):  # noqa: ANN001  (FIFO → headphones)
        try:
            now = time.monotonic()
            dev = out_lat["ms"]
            buf = out_carry["buf"]
            with fifo_lock:
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
    detect_ms = 0.0
    recent: deque[HeadPose] = deque(maxlen=20)
    pose_filter = PoseFilter(args.min_cutoff, args.beta)
    predictor = PosePredictor() if args.predict else None
    pred_hud = {"ms": 0.0}  # the horizon currently used, for the readout chip
    flip_az = args.flip_az
    miss = 0  # consecutive failed camera reads (graceful degradation, B4.2)
    times: deque[float] = deque(maxlen=60)  # frame timestamps → tracker rate
    show_stats = False
    phase = 0.0
    win = "Binaural head-tracker — world-lock passthrough"
    if args.ui == "console":
        cv2.namedWindow(win)
    drag = {"on": False}

    def on_mouse(event, mx, my, flags, _param):  # noqa: ANN001  — click/drag the radar to move test
        # Press starts a drag (and turns the test source on, stopping any auto-rotation);
        # moving with the button held sweeps the source live; release ends the drag.
        if event == cv2.EVENT_LBUTTONDOWN:
            az = C.radar_az_at(mx, my)
            if az is not None:
                with lock:
                    test["world_az"] = float(az)
                    test["on"] = True
                    test["rotate"] = False
                drag["on"] = True
        elif event == cv2.EVENT_MOUSEMOVE and drag["on"] and (flags & cv2.EVENT_FLAG_LBUTTON):
            az = C.radar_az_at(mx, my, allow_outside=True)
            if az is not None:
                with lock:
                    test["world_az"] = float(az)
        elif event == cv2.EVENT_LBUTTONUP:
            drag["on"] = False

    if args.ui == "console":
        cv2.setMouseCallback(win, on_mouse)

    # Capture = PyAudio WASAPI loopback (raw 7.1); output = sounddevice to the headphones
    # (blocksize=0 lets it pick the host-native buffer; the callbacks handle any frame count).
    in_stream = pya.open(format=pawp.paFloat32, channels=in_ch, rate=brir.samplerate,
                         input=True, input_device_index=lb["index"],
                         frames_per_buffer=args.block, stream_callback=pa_in_cb)
    worker = threading.Thread(target=render_worker, name="render", daemon=True)
    worker.start()
    in_stream.start_stream()

    def _open_output(exclusive: bool):
        extra = sd.WasapiSettings(exclusive=True) if exclusive else None
        ctx = sd.OutputStream(samplerate=brir.samplerate, channels=2, device=out_index,
                              blocksize=0, dtype="float32", latency="low", callback=out_cb,
                              extra_settings=extra)
        ctx.start()
        return ctx

    # WASAPI exclusive mode (B0.5) takes the endpoint for a much smaller device buffer (measured
    # ~13 ms vs ~22 ms shared, vs ~90 ms on MME). It can be refused (format/rate, or the device is
    # in use), so fall back to shared and say so rather than failing to start.
    try:
        out_ctx = _open_output(args.exclusive)
        if args.exclusive:
            print(f"output: WASAPI exclusive ({out_ctx.latency * 1000:.0f} ms device buffer)")
    except Exception as exc:  # noqa: BLE001
        if not args.exclusive:
            raise
        print(f"WASAPI exclusive unavailable ({exc!r}); using shared mode.")
        out_ctx = _open_output(False)
    def _run_cockpit() -> bool:
        """Drive the live engine through the Dear PyGui cockpit (B6.12 slice 2).

        Same audio path as the console — this only replaces the display + input. The camera/pose/
        diagnostics are computed exactly as the console loop does; the result feeds the cockpit's
        view-model, and the cockpit's control hooks mutate the live engine state (method, backend,
        predict, the test source placed/dragged on the radar, recenter). Returns True to reload."""
        nonlocal cap
        import cockpit_view as CK

        from binaural_headtracker import cockpit as CM

        win_ck = CK.CockpitWindow(title=f"world-lock passthrough — webcam {cam_index}")
        q = {"stop": False, "restart": False}
        cur = {"backend": ("rust" if args.rust else "gpu" if args.gpu else "cpu"),
               "predict": args.predict}
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
            nm = build_main_renderer(m, cur["backend"])
            with lock:
                main_rend["r"], main_rend["label"] = nm, render_label(nm, m)
                test["renderer"], test["method"] = test_renderers[m], m

        def _set_backend(b: str) -> None:
            cur["backend"] = b
            nm = build_main_renderer(test["method"], b)
            with lock:
                main_rend["r"], main_rend["label"] = nm, render_label(nm, test["method"])

        def _tog(key: str):
            def f(on) -> None:  # noqa: ANN001
                with lock:
                    test[key] = bool(on)
            return f

        def _place(az) -> None:  # noqa: ANN001  — radar click/drag → world azimuth
            with lock:
                test["world_az"], test["on"], test["rotate"] = float(az), True, False

        win_ck.on_quit = lambda: q.__setitem__("stop", True)
        win_ck.on_recenter = _recenter
        win_ck.on_method = _set_method
        win_ck.on_backend = _set_backend
        win_ck.on_test = _tog("on")
        win_ck.on_solo = _tog("solo")
        win_ck.on_rotate = _tog("rotate")
        win_ck.on_place_test = _place
        win_ck.on_predict = lambda on: cur.__setitem__("predict", bool(on))
        win_ck.setup()

        detect_ms = 0.0
        times_ck: deque[float] = deque(maxlen=60)
        miss = 0
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
                pose = None
                with lock:
                    cal = state["cal"]
                predict_ms_used = 0.0
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
                out_lat_ms = float(getattr(out_ctx, "latency", 0.0) or 0.0) * 1000.0
                out_lat["ms"] = out_lat_ms
                rlabel = main_rend["label"] + ("  · test ON" if t_on else "")
                ear = ear_probe.stats()
                diag = classify(
                    block_size=args.block, samplerate=brir.samplerate,
                    render_ms=meter["ms"], render_ms_peak=meter["peak"],
                    render_ms_test=(meter["test_ms"] if t_on else 0.0),
                    render_label=rlabel, fifo_fill_blocks=fifo_fill, cushion_blocks=cushion,
                    output_latency_ms=out_lat_ms,
                    input_latency_ms=1000.0 * args.block / brir.samplerate,
                    pose_rate_hz=rate, recent_underruns=recent_under, recent_overflows=recent_over,
                    measured_pose_age_ms=(ear.p50 if ear.n else None),
                    measured_pose_age_p95_ms=ear.p95, measured_pose_age_p99_ms=ear.p99,
                    measured_pose_age_n=ear.n, measured_stale_ms=stale_probe.stats().p50,
                    measured_queue_ms=queue_probe.stats().p50, measured_device_ms=out_lat_ms,
                    detect_ms=detect_ms, camera_fps=cam_fps, cam_w=cam_w, cam_h=cam_h)
                view = CM.build_view(
                    diag, latency_samples=ear_probe.samples(), rate_hz=rate,
                    method=test["method"], backend=cur["backend"],
                    predict_ms=(predict_ms_used if cur["predict"] else None),
                    facing_label=brir.channels[nearest_i], flipped=flip_az)
                win_ck.set_test_state(t_on, t_world_az)  # reflect engine state on the radar
                win_ck.update(view, camera_rgb=disp, world_az=world_az, channels=brir.channels,
                              facing_i=nearest_i, head_deg=head_deg)
                win_ck.render_frame()
        finally:
            win_ck.teardown()
        return q["restart"]

    restart = False  # Reload key (Enter): tear down cleanly, then the launcher relaunches us
    try:
        if args.ui == "cockpit":
            restart = _run_cockpit()
        while args.ui == "console":
            if cb_error:
                print(f"audio callback error: {cb_error[0]!r}")
                break
            ok, frame = cap.read()
            if not ok:
                # Camera lost: hold the last pose (audio keeps world-locking), keep the
                # window + keys alive, and try to reopen periodically — don't spin blind.
                miss += 1
                if miss % 90 == 0:
                    cap.release()
                    cap = cv2.VideoCapture(cam_index)
                lost = np.full((240, 520, 3), (28, 20, 16), np.uint8)
                cv2.putText(lost, "camera lost - holding last pose (audio live)", (14, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, MUTED, 1, cv2.LINE_AA)
                cv2.putText(lost, "[Q] quit", (14, 150),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, FG, 1, cv2.LINE_AA)
                cv2.imshow(win, lost)
                if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                    break
                continue
            miss = 0
            now = time.monotonic()
            times.append(now)
            phase = (phase + 0.03) % 1.0
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)  # un-mirrored → real yaw sign
            td = time.perf_counter()
            result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            dms = (time.perf_counter() - td) * 1000.0
            detect_ms = 0.9 * detect_ms + 0.1 * dms if detect_ms else dms
            frame = cv2.flip(frame, 1)  # mirror for display only
            pose = None
            landmarks = result.face_landmarks[0] if result.face_landmarks else None
            with lock:
                cal = state["cal"]
            if result.facial_transformation_matrixes:
                yaw, pitch, roll = head_pose_from_matrix(
                    np.asarray(result.facial_transformation_matrixes[0]))
                pose = pose_filter.update(HeadPose(yaw, pitch, roll, now))
                recent.append(pose)
                # Orientation prediction (B8.6): extrapolate the pose forward by the measured
                # pose-age-at-ear so the render matches the head when the sound is *heard*, not when
                # the camera saw it — cancels the ~40 ms camera staleness. The audio steers on the
                # predicted pose; the HUD still shows the real one.
                pose_for_az = pose
                if predictor is not None:
                    predictor.update(pose)
                    es = ear_probe.stats()
                    predict_ms = (args.predict_ms if args.predict_ms > 0
                                  else (es.p50 if es.n > 30 else 60.0))
                    pred_hud["ms"] = predict_ms
                    pose_for_az = predictor.predict(pose, predict_ms)
                dirs = directions_for_pose(cal, pose_for_az, layout)
            else:
                dirs = layout.head_relative_directions()
            if flip_az:
                dirs = dirs.copy()
                dirs[:, 0] = -dirs[:, 0]
            if pose is not None:
                with lock:
                    state["az"] = dirs[:, 0].copy()
                    state["pose_t"] = pose.t  # carry the capture time for the pose-age probe
            nearest_i = nearest_front_channel(dirs)
            head_deg = -float(dirs[c_idx][0])  # where the head faces in the world
            # Experiment test source: advance any auto-rotation and update its head-relative az
            # (a world-fixed source at world_az is heard at world_az + the head's own offset).
            dt = (times[-1] - times[-2]) if len(times) >= 2 else 0.0
            with lock:
                if test["rotate"]:
                    test["world_az"] = (test["world_az"] + args.rotate_speed * dt + 180.0) \
                        % 360.0 - 180.0
                test["az"] = float(test["world_az"]) + float(dirs[c_idx][0])

            span = times[-1] - times[0]
            rate = (len(times) - 1) / span if len(times) > 1 and span > 0 else 0.0
            with fifo_lock:
                fifo_fill = len(out_fifo)
            # snapshot: out_cb may append concurrently (avoid "deque mutated during iteration")
            recent_under = sum(1 for t in tuple(meter["under_ts"]) if now - t < 2.0)
            recent_over = sum(1 for t in tuple(meter["over_ts"]) if now - t < 2.0)
            out_lat_ms = float(getattr(out_ctx, "latency", 0.0) or 0.0) * 1000.0
            out_lat["ms"] = out_lat_ms  # feed the worker's pose-age probe (it can't read out_ctx)
            rlabel = main_rend["label"] + ("  · test ON" if test["on"] else "")
            ear = ear_probe.stats()  # measured pose-age-at-ear distribution (B8.5)
            diag = classify(
                block_size=args.block, samplerate=brir.samplerate,
                render_ms=meter["ms"], render_ms_peak=meter["peak"],
                render_ms_test=(meter["test_ms"] if test["on"] else 0.0),
                render_label=rlabel,
                fifo_fill_blocks=fifo_fill, cushion_blocks=cushion,
                output_latency_ms=out_lat_ms,
                input_latency_ms=1000.0 * args.block / brir.samplerate,
                pose_rate_hz=rate, recent_underruns=recent_under,
                recent_overflows=recent_over,
                measured_pose_age_ms=(ear.p50 if ear.n else None),
                measured_pose_age_p95_ms=ear.p95, measured_pose_age_p99_ms=ear.p99,
                measured_pose_age_n=ear.n,
                measured_stale_ms=stale_probe.stats().p50,
                measured_queue_ms=queue_probe.stats().p50,
                measured_device_ms=out_lat_ms,
                detect_ms=detect_ms, camera_fps=cam_fps, cam_w=cam_w, cam_h=cam_h)

            live = "TRACKING" if pose is not None else "NO FACE"
            live_col = C.GOOD if pose is not None else C.MUTED
            readout = [
                ("rate", f"{rate:3.0f}Hz", C.GOOD if rate >= 20 else C.ACTIVE),
                ("lat", f"~{diag.end_to_end_ms:.0f}ms", C.health_color(diag.health)),
                ("facing", brir.channels[nearest_i] + ("  [flip]" if flip_az else ""), C.ACTIVE),
            ]
            if predictor is not None:
                readout.append(("predict", f"{pred_hud['ms']:.0f}ms", C.GOOD))
            if test["on"]:
                # method now lives in the title-bar chip (shared by 7.1 + test), so the
                # test readout only carries the source's position/mode
                readout.append(("test", f"{test['world_az']:+.0f}\xb0"
                                + ("  SOLO" if test["solo"] else "")
                                + ("  rot" if test["rotate"] else ""), C.GOOD))
            buttons = [("Test", "T", test["on"]), ("Method", "M", False),
                       ("Stats", "S", False), ("Reload", "⏎", False), ("Quit", "Q", False)]
            canvas = C.compose(
                frame, title=f"world-lock passthrough — webcam {cam_index}", live=live,
                live_color=live_col, pose=pose, landmarks=landmarks, lock=None,
                world_az=world_az, channels=brir.channels, facing_i=nearest_i, head_deg=head_deg,
                source_az=(test["world_az"] if test["on"] else None),
                readout=readout, buttons=buttons, diagnosis=diag, method=test["method"],
                show_stats=show_stats, audio_stats=True, rate_hz=rate, phase=phase)
            cv2.imshow(win, canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key in (13, 10):  # Enter: reload — relaunch to pick up latest code (clean restart)
                cv2.putText(canvas, "restarting...", (C.CAM_W + 20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, ACCENT, 1, cv2.LINE_AA)
                cv2.imshow(win, canvas)
                cv2.waitKey(1)
                restart = True
                break
            if key == ord("s"):
                show_stats = not show_stats
            if key == ord("f"):
                flip_az = not flip_az
            if key == ord("t"):  # experiment layer: toggle the movable test source
                with lock:
                    test["on"] = not test["on"]
            if key == ord("o"):  # solo: hear only the test source (mute the 7.1)
                with lock:
                    test["solo"] = not test["solo"]
            if key == ord("r"):  # auto-rotate the test source around you
                with lock:
                    test["rotate"] = not test["rotate"]
            if key in (ord("["), ord("]")):  # test level down / up
                with lock:
                    f = 0.8 if key == ord("[") else 1.25
                    test["gain"] = float(np.clip(test["gain"] * f, 0.02, 4.0))
            if key == ord("m"):  # cycle the SHARED method — rebuilds the 7.1 AND the test source
                nxt = EXPERIMENT_METHODS[(EXPERIMENT_METHODS.index(test["method"]) + 1)
                                         % len(EXPERIMENT_METHODS)]
                cv2.putText(canvas, f"switching to {nxt} (building)...", (C.CAM_W + 20, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, ACCENT, 1, cv2.LINE_AA)
                cv2.imshow(win, canvas)
                cv2.waitKey(1)
                if nxt not in test_renderers:
                    test_renderers[nxt] = build_test_renderer(nxt)
                new_main = build_main_renderer(nxt)  # dense-grid 7.1 for a synthesis method
                new_label = render_label(new_main, nxt)
                with lock:
                    main_rend["r"] = new_main
                    main_rend["label"] = new_label
                    test["renderer"] = test_renderers[nxt]
                    test["method"] = nxt
            if key == ord("c") and recent:
                new_cal = calibrate_neutral(list(recent), yaw_sign=args.yaw_sign,
                                            pitch_sign=args.pitch_sign, roll_sign=args.roll_sign)
                with lock:
                    state["cal"] = new_cal
    finally:
        stop_worker.set()
        worker.join(timeout=1.0)
        out_ctx.stop()
        out_ctx.close()
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

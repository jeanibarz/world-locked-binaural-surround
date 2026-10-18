"""Automated latency / throughput benchmark for the world-lock passthrough (B8.5b).

The manual loop — edit the launcher, restart, move your head, read the on-exit numbers — does
not scale and hides *which stage* the latency is in. This runs the real render pipeline with a
**synthetic head pose** (a deterministic yaw sweep, no camera/MediaPipe) and a **synthetic 7.1
sine** (no VoiceMeeter capture), so one invocation measures a single configuration end to end,
unattended, and ``latency_sweep.py`` drives a whole grid of them.

What it measures, as distributions (p50/p95/p99 + extremes), after discarding a warm-up:

* **stale** — pose age when the block is rendered (camera/pose interval + worker pickup),
* **queue** — how long the rendered block then waits in the output FIFO,
* **device** — the output device buffer (real in ``--output device``; a nominal in ``sim``),
* **total** = stale + queue + device — the pose-age-at-ear, the number the GUI shows.

Splitting them is the point: block size moves *queue*, the camera moves *stale*, WASAPI
exclusive moves *device* — a single summed number can't tell you which to chase.

It also checks the audio is **processed correctly**: the rendered output stays finite and
un-clipped, is not silent, and still has its energy at the input tone (the pipeline passes the
sine), and it counts **dropouts** (the consumer finding the FIFO empty). ``--output sim`` runs
anywhere (a real-time-clocked consumer thread, no device); ``--output device`` uses the real
sounddevice output to capture the true device-buffer term on the host.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import deque

import numpy as np

from binaural_headtracker.latency import LatencyProbe
from binaural_headtracker.live_render import InterpWorldLocked7p1, WorldLocked7p1
from binaural_headtracker.output_eq import load_output_eq
from binaural_headtracker.surround import load_baked_brir
from binaural_headtracker.world import layout_7p1

METHODS = ("nearest", "timealign", "minphase", "notchwarp")


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def build_renderer(brir, args):
    """The 7.1 renderer for ``args.method`` — mirrors the passthrough's selection."""
    if args.method == "nearest":
        if args.rust:
            from binaural_headtracker.rust_render import RustWorldLocked7p1
            return RustWorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms,
                                      lfe_mode=args.lfe_mode), "rust/nearest"
        return WorldLocked7p1(brir, block_size=args.block, fade_ms=args.fade_ms,
                              lfe_mode=args.lfe_mode), "cpu/nearest"
    r = InterpWorldLocked7p1(brir, args.method, block_size=args.block, fade_ms=args.fade_ms,
                             grid_step=args.grid_step, lfe_mode=args.lfe_mode, use_rust=args.rust)
    return r, f"{r.engine}/{args.method}/{args.grid_step:.0f}deg"


def run(args) -> dict:
    return run_bench(load_baked_brir(args.brir_dir), args)


def run_bench(brir, args) -> dict:
    sr = brir.samplerate
    block = args.block
    block_s = block / sr
    renderer, label = build_renderer(brir, args)
    world_az = layout_7p1(channels=brir.channels).head_relative_directions()[:, 0]
    out_eq = (load_output_eq(args.output_eq, samplerate=sr, block_size=block)
              if args.output_eq else None)

    cushion = max(2, round(args.latency_ms * 1e-3 * sr / block))
    max_backlog = cushion * 2
    fifo: deque = deque()          # (block_ndarray, pose_t, render_t)
    fifo_lock = threading.Lock()
    for _ in range(cushion):       # prime with silence (as the app does)
        fifo.append((np.zeros((block, 2)), None, None))

    pose = {"az": world_az.copy(), "t": time.perf_counter()}
    pose_lock = threading.Lock()
    stop = threading.Event()
    meter = {"render_ms": 0.0, "peak_ms": 0.0}
    stale, queue, total = LatencyProbe(), LatencyProbe(), LatencyProbe()
    drops = {"n": 0}
    integ = {"peak": 0.0, "finite": True, "acc": deque(maxlen=int(sr))}  # ~1 s of output
    warm_until = time.perf_counter() + args.warmup

    def pose_thread() -> None:
        # deterministic yaw sweep: amplitude·sin(2π·rate·t), published at --fps
        dt = 1.0 / args.fps
        nxt = time.perf_counter()
        while not stop.is_set():
            t = time.perf_counter()
            yaw = args.sweep_deg * np.sin(2 * np.pi * args.sweep_hz * t)
            az = np.array([_wrap(w - yaw) for w in world_az])
            with pose_lock:
                pose["az"], pose["t"] = az, t
            nxt += dt
            stop.wait(max(0.0, nxt - time.perf_counter()))

    phase = {"n": 0}

    def gen_block() -> np.ndarray:
        n0 = phase["n"]
        t = (n0 + np.arange(block)) / sr
        s = (args.tone_level * np.sin(2 * np.pi * args.tone_hz * t)).astype(np.float64)
        phase["n"] = n0 + block
        return np.repeat(s[:, None], brir.n_channels, axis=1)

    def produce_one() -> None:
        pcm = gen_block()
        with pose_lock:
            az, pose_t = pose["az"], pose["t"]
        t0 = time.perf_counter()
        out = renderer.process_block(pcm, az)
        if out_eq is not None:
            out = out_eq.process_block(out)
        render_t = time.perf_counter()
        ms = (render_t - t0) * 1000.0
        meter["render_ms"] = 0.9 * meter["render_ms"] + 0.1 * ms if meter["render_ms"] else ms
        meter["peak_ms"] = max(ms, meter["peak_ms"] * 0.98)
        if render_t > warm_until:
            if not np.isfinite(out).all():
                integ["finite"] = False
            integ["peak"] = max(integ["peak"], float(np.abs(out).max()))
            integ["acc"].extend(out[:, 0].tolist())
        with fifo_lock:
            fifo.append((out, pose_t, render_t))
            while len(fifo) > max_backlog:
                fifo.popleft()

    def consume_one(now: float) -> None:
        with fifo_lock:
            item = fifo.popleft() if fifo else None
        if item is None:
            if now > warm_until:
                drops["n"] += 1
            return
        _, pose_t, render_t = item
        if pose_t is None or now <= warm_until:
            return
        dev = args.device_ms
        stale.add((render_t - pose_t) * 1000.0)
        queue.add((now - render_t) * 1000.0)
        total.add((now - pose_t) * 1000.0 + dev)

    def producer() -> None:
        # Keep the FIFO topped up to the cushion, paced by the *consumer* draining it (not by a
        # Python sleep clock — Windows' ~15 ms timer granularity can't pace small blocks, which
        # otherwise shows up as phantom dropouts). Refills in a burst after a coarse wake, exactly
        # like the real worker draining a capture backlog, so a realistic cushion depth is held.
        while not stop.is_set():
            with fifo_lock:
                fill = len(fifo)
            if fill < cushion:
                produce_one()
            else:
                stop.wait(0.0005)

    def sim_consumer() -> None:
        nxt = time.perf_counter()
        while not stop.is_set():
            consume_one(time.perf_counter())
            nxt += block_s
            stop.wait(max(0.0, nxt - time.perf_counter()))

    # Raise the Windows timer resolution to ~1 ms for the run, so the Python producer/consumer
    # sleeps are fine-grained (default ~15 ms granularity otherwise causes phantom dropouts that
    # the real hardware-clocked audio stack never sees). No-op off Windows.
    wmm = None
    try:
        import ctypes
        wmm = ctypes.windll.winmm  # type: ignore[attr-defined]
        wmm.timeBeginPeriod(1)
    except Exception:  # noqa: BLE001
        wmm = None

    threads = [threading.Thread(target=pose_thread, daemon=True),
               threading.Thread(target=producer, daemon=True)]
    out_ctx = None
    if args.output == "sim":
        threads.append(threading.Thread(target=sim_consumer, daemon=True))
    for t in threads:
        t.start()

    if args.output == "device":
        import sounddevice as sd

        from binaural_headtracker.devices import output_device_choices, resolve_output_device
        # Resolve the device name ourselves (sounddevice raises on a name that matches several
        # host APIs); our resolver prefers WASAPI, which is also what we want to benchmark.
        raw = sd.query_devices()
        hostapis = [a["name"] for a in sd.query_hostapis()]
        dev_index = resolve_output_device(args.audio_device,
                                          output_device_choices(raw, hostapis))
        extra = None
        if args.exclusive:
            # WASAPI exclusive mode takes the device away from the mixer for a much smaller
            # buffer (the suspected dominant latency term in shared mode). Windows-only.
            extra = sd.WasapiSettings(exclusive=True)
        carry = {"buf": np.zeros((0, 2))}

        def out_cb(outdata, frames, _t, _s):  # noqa: ANN001
            now = time.perf_counter()
            buf = carry["buf"]
            while buf.shape[0] < frames:
                with fifo_lock:
                    item = fifo.popleft() if fifo else None
                if item is None:
                    if now > warm_until:
                        drops["n"] += 1
                    break
                consume_one(now)
                buf = np.concatenate([buf, item[0]], axis=0)
            n = min(frames, buf.shape[0])
            outdata[:n] = buf[:n].astype(np.float32)
            if n < frames:
                outdata[n:] = 0
            carry["buf"] = buf[n:]
        out_ctx = sd.OutputStream(samplerate=sr, channels=2, device=dev_index,
                                  blocksize=0, dtype="float32", latency="low", callback=out_cb,
                                  extra_settings=extra)
        out_ctx.start()
        args.device_ms = float(out_ctx.latency or 0.0) * 1000.0
        args.resolved_device = sd.query_devices(dev_index)["name"] if dev_index is not None else ""

    time.sleep(args.warmup + args.seconds)
    stop.set()
    time.sleep(0.05)
    if out_ctx is not None:
        out_ctx.stop()
        out_ctx.close()
    if wmm is not None:
        wmm.timeEndPeriod(1)

    tone_snr_db = _tone_snr(np.array(integ["acc"], dtype=np.float64), args.tone_hz, sr)
    def dist(p: LatencyProbe) -> dict:
        s = p.stats()
        return {"n": s.n, "p50": round(s.p50, 1), "p95": round(s.p95, 1),
                "p99": round(s.p99, 1), "min": round(s.min, 1), "max": round(s.max, 1)}
    return {
        "label": label, "output": args.output,
        "block": block, "block_ms": round(block_s * 1000, 2), "latency_ms": args.latency_ms,
        "cushion_blocks": cushion, "method": args.method, "grid_step": args.grid_step,
        "rust": bool(args.rust), "exclusive": bool(args.exclusive), "samplerate": sr,
        "render_ms": round(meter["render_ms"], 2), "render_peak_ms": round(meter["peak_ms"], 2),
        "render_load": round(meter["render_ms"] / (block_s * 1000), 3),
        "render_load_peak": round(meter["peak_ms"] / (block_s * 1000), 3),
        "dropouts": drops["n"], "device_ms": round(args.device_ms, 1),
        "stale_ms": dist(stale), "queue_ms": dist(queue), "total_ms": dist(total),
        # "ok" = the pipeline processed the audio correctly and kept up: finite, the tone came
        # through, and no dropouts. Peak is reported (watch for clipping) but not gated — absolute
        # level depends on gain staging the bench doesn't replicate, so it's not a correctness fail.
        "integrity": {"finite": integ["finite"], "peak": round(integ["peak"], 3),
                      "tone_snr_db": round(tone_snr_db, 1),
                      "ok": integ["finite"] and tone_snr_db > 15.0 and drops["n"] == 0},
    }


def _tone_snr(x: np.ndarray, tone_hz: float, sr: int) -> float:
    """dB between the energy at the tone bin and the median bin — confirms the pipeline
    passed the sine (BRIR/EQ colour it but do not move the fundamental)."""
    if x.size < 2048:
        return 0.0
    w = np.hanning(x.size)
    mag = np.abs(np.fft.rfft(x * w)) ** 2
    freqs = np.fft.rfftfreq(x.size, 1.0 / sr)
    k = int(np.argmin(np.abs(freqs - tone_hz)))
    peak = mag[max(0, k - 2): k + 3].max()
    med = np.median(mag[1:]) + 1e-20
    return 10.0 * np.log10(peak / med)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="latency/throughput benchmark (synthetic pose+sine)")
    p.add_argument("--brir-dir", required=True)
    p.add_argument("--method", default="timealign", choices=METHODS)
    p.add_argument("--block", type=int, default=512)
    p.add_argument("--latency-ms", type=float, default=20.0)
    p.add_argument("--grid-step", type=float, default=10.0)
    p.add_argument("--fade-ms", type=float, default=20.0)
    p.add_argument("--lfe-mode", default="brir", choices=("brir", "flat"))
    p.add_argument("--output-eq", default=None)
    p.add_argument("--rust", action="store_true")
    p.add_argument("--output", default="sim", choices=("sim", "device"))
    p.add_argument("--audio-device", default=None, help="output device (device mode)")
    p.add_argument("--exclusive", action="store_true",
                   help="WASAPI exclusive mode on the output (device mode, Windows) — small buffer")
    p.add_argument("--device-ms", type=float, default=0.0,
                   help="nominal device buffer added in sim mode (ms)")
    p.add_argument("--fps", type=float, default=30.0, help="synthetic pose update rate")
    p.add_argument("--sweep-deg", type=float, default=20.0, help="yaw sweep amplitude")
    p.add_argument("--sweep-hz", type=float, default=0.25, help="yaw sweep rate")
    p.add_argument("--tone-hz", type=float, default=220.0)
    p.add_argument("--tone-level", type=float, default=0.2)
    p.add_argument("--warmup", type=float, default=3.0, help="seconds discarded before measuring")
    p.add_argument("--seconds", type=float, default=8.0, help="measurement seconds")
    p.add_argument("--json", action="store_true", help="print one JSON line (for the sweep)")
    args = p.parse_args(argv)

    result = run(args)
    if args.json:
        print(json.dumps(result))
    else:
        t, q, s = result["total_ms"], result["queue_ms"], result["stale_ms"]
        print(f"[{result['label']}] block {result['block']} ({result['block_ms']}ms) "
              f"cushion {result['cushion_blocks']}  load {result['render_load'] * 100:.0f}%"
              f" (pk {result['render_load_peak'] * 100:.0f}%)  dropouts {result['dropouts']}")
        print(f"  total p50 {t['p50']}  p95 {t['p95']}  p99 {t['p99']}  (min {t['min']} "
              f"max {t['max']}) ms   [integrity {'OK' if result['integrity']['ok'] else 'BAD'}]")
        print(f"  stale p50 {s['p50']} / queue p50 {q['p50']} / device {result['device_ms']} ms"
              f"   tone_snr {result['integrity']['tone_snr_db']} dB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

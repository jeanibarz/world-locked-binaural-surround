"""B4.1 — end-to-end audio loopback diagnostics for the 7.1 world-lock passthrough.

A reusable probe toolkit for the kind of bug that only shows up against real hardware —
wrong channel order, lost bass, dropouts, spectral imbalance — by driving a **known** signal
through the real capture→render→output path and measuring what comes back. Born from
debugging the live passthrough (journal 24/25): dropouts (render was in the audio callback)
and "no bass / hollow front" (LFE through a bass-dead BRIR). See the `audio-loopback-
diagnostics` skill for the method.

Runs on the **Windows host** (WASAPI loopback + a virtual 7.1 device), driven from WSL via
interop, except ``spectral`` which is offline (BRIR + numpy only, so it also runs on CI/WSL)::

    WINPATH=$(wslpath -w spikes/b4_1_loopback_diag.py)
    py.exe -3.12 "$WINPATH" devices                       # list outputs + loopbacks + rates
    py.exe -3.12 "$WINPATH" order                          # is the capture channel order right?
    py.exe -3.12 "$WINPATH" duplex --arch worker --gpu     # dropouts / throughput / underruns
    py.exe -3.12 "$WINPATH" duplex --arch inline           # (reproduce the render-in-callback bug)
    python spikes/b4_1_loopback_diag.py spectral --brir-dir <dir>   # per-channel balance + bass

Host deps: ``pyaudiowpatch`` (WASAPI render-loopback; the ``loopback`` extra) and
``sounddevice`` (the ``audio`` extra); ``--gpu`` adds CuPy (the ``gpu`` extra).
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from binaural_headtracker.live_render import WorldLocked7p1  # noqa: E402
from binaural_headtracker.surround import load_baked_brir  # noqa: E402
from binaural_headtracker.world import layout_7p1  # noqa: E402

DEFAULT_BRIR = (r"C:\Program Files\EqualizerAPO\config"
                r"\audeze-lcd-gx-binaural-surround\eqapo_config\baked_7p1_test")
VM_MATCH = "voicemeeter input"  # the virtual 7.1 render endpoint (feeder target + loopback)


# --------------------------------------------------------------------------- device helpers
def _find_output(sd, name_sub, prefer_api="WASAPI", sr=None):
    """First output device whose name contains ``name_sub``, preferring a host API and rate."""
    apis = [a["name"] for a in sd.query_hostapis()]
    cands = [(i, d) for i, d in enumerate(sd.query_devices())
             if d["max_output_channels"] > 0 and name_sub.lower() in d["name"].lower()]
    if not cands:
        return None
    cands.sort(key=lambda it: (
        prefer_api.lower() not in apis[it[1]["hostapi"]].lower(),      # preferred API first
        sr is not None and abs(it[1]["default_samplerate"] - sr) > 1,  # matching rate first
    ))
    return cands[0][0]


def _find_loopback(pya, name_sub):
    return next((d for d in pya.get_loopback_device_info_generator()
                 if name_sub.lower() in d["name"].lower()), None)


def cmd_devices(args) -> int:
    import pyaudiowpatch as pawp
    import sounddevice as sd
    apis = [a["name"] for a in sd.query_hostapis()]
    print("== OUTPUT devices ==")
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            print(f"  idx {i:3d}  {d['max_output_channels']}ch  {d['default_samplerate']:6.0f}Hz "
                  f"{apis[d['hostapi']]:<18} {d['name']}")
    print("== WASAPI LOOPBACK devices ==")
    pya = pawp.PyAudio()
    try:
        for d in pya.get_loopback_device_info_generator():
            print(f"  idx {d['index']:3d}  {d['maxInputChannels']}ch  "
                  f"{d['defaultSampleRate']:6.0f}Hz  {d['name']}")
    finally:
        pya.terminate()
    return 0


# ------------------------------------------------------------------------- channel-order probe
def cmd_order(args) -> int:
    """Feed a distinct tone into each virtual-device channel; read the captured column order."""
    import pyaudiowpatch as pawp
    import sounddevice as sd
    brir = load_baked_brir(args.brir_dir)
    expect = brir.channels
    nch = len(expect)
    sr = brir.samplerate
    freqs = [200 + 100 * k for k in range(nch)]
    phase = {"p": 0}

    def feed_cb(outdata, frames, _t, _s):
        t = (phase["p"] + np.arange(frames)) / sr
        outdata[:] = 0
        for k in range(min(nch, outdata.shape[1])):
            outdata[:, k] = (0.25 * np.sin(2 * np.pi * freqs[k] * t)).astype(np.float32)
        phase["p"] += frames

    pya = pawp.PyAudio()
    lb = _find_loopback(pya, args.loopback)
    if lb is None:
        print(f"no loopback matching {args.loopback!r}", flush=True)
        return 1
    in_ch = int(lb["maxInputChannels"])
    feed_idx = _find_output(sd, args.feed, sr=sr)
    print(f"feed -> idx {feed_idx} ({args.feed});  capture <- '{lb['name']}' {in_ch}ch", flush=True)
    print("feeding " + "  ".join(f"col{k}={freqs[k]}Hz({expect[k]})" for k in range(nch)),
          flush=True)
    cap = deque()

    def in_cb(data, _fc, _ti, _st):
        cap.append(np.frombuffer(data, dtype=np.float32).reshape(-1, in_ch).copy())
        return (None, pawp.paContinue)

    feeder = sd.OutputStream(samplerate=sr, channels=nch, device=feed_idx, dtype="float32",
                             callback=feed_cb)
    instream = pya.open(format=pawp.paFloat32, channels=in_ch, rate=sr, input=True,
                        input_device_index=lb["index"], frames_per_buffer=1024,
                        stream_callback=in_cb)
    feeder.start()
    instream.start_stream()
    time.sleep(args.seconds)
    instream.stop_stream()
    instream.close()
    feeder.stop()
    feeder.close()
    pya.terminate()

    data = np.concatenate(list(cap), axis=0)[sr // 2:]
    fr = np.fft.rfftfreq(data.shape[0], 1 / sr)
    print(f"\n{'cap_col':>7} {'domFreq':>8} {'->chan':>7} {'rms':>8}", flush=True)
    measured = []
    for col in range(data.shape[1]):
        x = data[:, col]
        rms = float(np.sqrt((x ** 2).mean()))
        peak = fr[np.argmax(np.abs(np.fft.rfft(x)))]
        fed = int(round((peak - 200) / 100)) if rms > 1e-4 else -1
        name = expect[fed] if 0 <= fed < nch else "(silent)"
        measured.append(name)
        print(f"{col:>7} {peak:7.0f}Hz {name:>7} {rms:8.4f}", flush=True)
    print(f"\nexpected : {tuple(expect)}", flush=True)
    print(f"measured : {tuple(measured)}", flush=True)
    print("MATCH" if tuple(measured) == tuple(expect) else ">>> MISMATCH (remap needed) <<<",
          flush=True)
    return 0


# ------------------------------------------------------------------- duplex throughput / drops
def cmd_duplex(args) -> int:
    """Measure the live duplex: effective sample rates, underruns, FIFO health; record a WAV.

    ``--arch inline`` renders inside the capture callback (the bug); ``--arch worker`` renders
    on a worker thread (the fix). ``--feed`` self-generates a tone into the virtual device.
    """
    import pyaudiowpatch as pawp
    import sounddevice as sd
    brir = load_baked_brir(args.brir_dir)
    sr, nch, block = brir.samplerate, brir.n_channels, args.block
    layout = layout_7p1(channels=brir.channels)
    az = layout.head_relative_directions()[:, 0].copy()
    if args.gpu:
        from binaural_headtracker.gpu_render import GpuWorldLocked7p1
        renderer = GpuWorldLocked7p1(brir, block_size=block)
    else:
        renderer = WorldLocked7p1(brir, block_size=block)
    for _ in range(10):
        renderer.process_block(np.zeros((block, nch)), az)

    pya = pawp.PyAudio()
    lb = _find_loopback(pya, args.loopback)
    if lb is None:
        print(f"no loopback matching {args.loopback!r}", flush=True)
        return 1
    in_ch = int(lb["maxInputChannels"])
    out_idx = _find_output(sd, args.output, sr=sr)
    st = {"in_cb": 0, "in_frames": 0, "in_last": None, "in_gap_max": 0.0,
          "out_cb": 0, "out_req": 0, "out_served": 0, "underflow": 0, "fifo_empty": 0,
          "silent_frames": 0, "render_max": 0.0, "render_sum": 0.0, "render_n": 0,
          "fifo_min": 10 ** 9, "fifo_max": 0}
    fifo: deque = deque()
    flock = threading.Lock()
    cushion = max(2, round(args.latency_ms * 1e-3 * sr / block))
    for _ in range(cushion):
        fifo.append(np.zeros((block, 2)))
    max_backlog = cushion * 4
    in_tail = {"buf": np.zeros((0, nch))}
    out_carry = {"buf": np.zeros((0, 2))}
    inq: deque = deque()
    iqlock = threading.Lock()
    stop = threading.Event()
    rec, series = [], []

    def render_into_fifo(cap):
        buf = np.concatenate([in_tail["buf"], cap], axis=0)
        i, blocks = 0, []
        while buf.shape[0] - i >= block:
            t0 = time.perf_counter()
            o = renderer.process_block(buf[i:i + block], az)
            dt = (time.perf_counter() - t0) * 1000
            st["render_max"] = max(st["render_max"], dt)
            st["render_sum"] += dt
            st["render_n"] += 1
            blocks.append(o)
            i += block
        in_tail["buf"] = buf[i:]
        if blocks:
            with flock:
                fifo.extend(blocks)
                while len(fifo) > max_backlog:
                    fifo.popleft()

    def worker():
        while not stop.is_set():
            with iqlock:
                chunks = list(inq)
                inq.clear()
            if chunks:
                render_into_fifo(np.concatenate(chunks, axis=0))
            else:
                stop.wait(0.001)

    def in_cb(data, fc, _ti, _st):
        now = time.perf_counter()
        if st["in_last"] is not None:
            st["in_gap_max"] = max(st["in_gap_max"], now - st["in_last"])
        st["in_last"] = now
        st["in_cb"] += 1
        st["in_frames"] += fc
        cap = np.frombuffer(data, dtype=np.float32).reshape(-1, in_ch).astype(np.float64)
        if args.arch == "worker":
            with iqlock:
                inq.append(cap)
        else:
            render_into_fifo(cap)
        return (None, pawp.paContinue)

    def out_cb(outdata, frames, _t, status):
        st["out_cb"] += 1
        st["out_req"] += frames
        if status.output_underflow:
            st["underflow"] += 1
        buf = out_carry["buf"]
        with flock:
            st["fifo_min"] = min(st["fifo_min"], len(fifo))
            st["fifo_max"] = max(st["fifo_max"], len(fifo))
            while buf.shape[0] < frames and fifo:
                buf = np.concatenate([buf, fifo.popleft()], axis=0)
        n = min(frames, buf.shape[0])
        outdata[:n] = buf[:n].astype(np.float32)
        st["out_served"] += n
        if n < frames:
            outdata[n:] = 0
            st["fifo_empty"] += 1
            st["silent_frames"] += frames - n
        rec.append(outdata.copy())
        out_carry["buf"] = buf[n:]

    feeder = None
    if args.feed:
        feed_idx = _find_output(sd, args.feed, sr=sr)
        vm = sd.query_devices(feed_idx)
        ph = {"p": 0}

        def feed_cb(outdata, frames, _t, _s):
            t = (ph["p"] + np.arange(frames)) / sr
            outdata[:] = 0
            outdata[:, 0] = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
            outdata[:, 1] = outdata[:, 0]
            if outdata.shape[1] > 2:
                outdata[:, 2] = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
            ph["p"] += frames
        feeder = sd.OutputStream(samplerate=sr, channels=min(8, vm["max_output_channels"]),
                                 device=feed_idx, dtype="float32", callback=feed_cb)

    print(f"arch={args.arch} gpu={args.gpu} block={block} cushion={cushion}(~{args.latency_ms}ms) "
          f"capture='{lb['name']}' {in_ch}ch -> out idx {out_idx}", flush=True)
    instream = pya.open(format=pawp.paFloat32, channels=in_ch, rate=sr, input=True,
                        input_device_index=lb["index"], frames_per_buffer=block,
                        stream_callback=in_cb)
    out_stream = sd.OutputStream(samplerate=sr, channels=2, device=out_idx, blocksize=0,
                                 dtype="float32", callback=out_cb)
    wthread = threading.Thread(target=worker, daemon=True) if args.arch == "worker" else None
    t0 = time.perf_counter()
    if feeder is not None:
        feeder.start()
    if wthread is not None:
        wthread.start()
    instream.start_stream()
    out_stream.start()
    try:
        while time.perf_counter() - t0 < args.seconds:
            with flock:
                series.append(len(fifo))
            time.sleep(0.25)
    finally:
        elapsed = time.perf_counter() - t0
        stop.set()
        if wthread is not None:
            wthread.join(timeout=1.0)
        instream.stop_stream()
        instream.close()
        out_stream.stop()
        out_stream.close()
        if feeder is not None:
            feeder.stop()
            feeder.close()
        pya.terminate()

    rn = max(1, st["render_n"])
    print("\n===== SUMMARY =====", flush=True)
    print(f"elapsed {elapsed:.2f}s", flush=True)
    print(f"capture: eff_rate {st['in_frames']/elapsed:.0f}Hz (nominal {sr})  "
          f"gap_max {st['in_gap_max']*1000:.1f}ms", flush=True)
    print(f"output : served_rate {st['out_served']/elapsed:.0f}Hz", flush=True)
    print(f"UNDERRUNS: pa_underflow={st['underflow']}  fifo_empty={st['fifo_empty']}  "
          f"silence={st['silent_frames']/sr*1000:.0f}ms", flush=True)
    print(f"render : mean {st['render_sum']/rn:.2f}ms max {st['render_max']:.2f}ms "
          f"(period {block/sr*1000:.1f}ms)", flush=True)
    print(f"FIFO   : min {st['fifo_min']} max {st['fifo_max']} cap {max_backlog}  "
          f"series {series[:30]}", flush=True)
    if rec and args.wav:
        out = np.concatenate(rec, axis=0)
        with wave.open(args.wav, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(out, -1, 1) * 32767).astype("<i2").tobytes())
        print(f"wrote {args.wav} ({out.shape[0]/sr:.2f}s, peak {np.abs(out).max():.3f})",
              flush=True)
    verdict = "OK (real-time, no drops)" if st["silent_frames"] == 0 and \
        st["in_frames"] / elapsed > sr * 0.95 else ">>> DROPS / THROUGHPUT COLLAPSE <<<"
    print(verdict, flush=True)
    return 0


# ----------------------------------------------------------------- spectral / balance (offline)
def cmd_spectral(args) -> int:
    """Per-channel render output level + sub-bass retention + each BRIR's low-freq response.

    Offline (BRIR + numpy only). Flags a bass-dead channel (e.g. LFE through an HRIR that
    rolls off sub-bass) and gross L/R imbalance.
    """
    brir = load_baked_brir(args.brir_dir)
    sr, nch, block = brir.samplerate, brir.n_channels, args.block
    layout = layout_7p1(channels=brir.channels)
    az = layout.head_relative_directions()[:, 0].copy()
    rng = np.random.default_rng(0)
    n = sr

    def mag_db(ir, f):
        fr = np.fft.rfftfreq(8192, 1 / sr)
        X = np.abs(np.fft.rfft(ir, n=8192))
        ref = max(np.abs(np.fft.rfft(ir, n=8192))[np.argmin(np.abs(fr - g))] for g in (500, 1000))
        return 20 * np.log10(X[np.argmin(np.abs(fr - f))] / (ref + 1e-12) + 1e-12)

    def subfrac(x):
        fr = np.fft.rfftfreq(len(x), 1 / sr)
        p = np.abs(np.fft.rfft(x)) ** 2
        return p[fr < 120].sum() / (p.sum() + 1e-20)

    print("BRIR low-freq magnitude (dB re 0.5-1kHz), left ear:")
    for k, c in enumerate(brir.channels):
        row = "  ".join(f"{f}Hz:{mag_db(brir.left[k], f):+5.1f}" for f in (30, 50, 80, 120))
        print(f"  {c:>4}  {row}", flush=True)
    print(f"\nper-channel render @ neutral ({'ch':>4} {'outRMS_L':>9} {'outRMS_R':>9} "
          f"{'sub<120Hz%':>10}):")
    for k, c in enumerate(brir.channels):
        pcm = np.zeros((n, nch))
        pcm[:, k] = rng.normal(0, 0.2, n)
        r = WorldLocked7p1(brir, block_size=block)
        out, i = [], 0
        while i + block <= n:
            out.append(r.process_block(pcm[i:i + block], az))
            i += block
        o = np.concatenate(out, axis=0)
        lr = np.sqrt((o[:, 0] ** 2).mean()), np.sqrt((o[:, 1] ** 2).mean())
        print(f"  {c:>4} {lr[0]:9.5f} {lr[1]:9.5f} {subfrac(o[:, 0])*100:9.2f}%", flush=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("devices", "order", "duplex", "spectral"):
        sp = sub.add_parser(name)
        sp.add_argument("--brir-dir", default=DEFAULT_BRIR)
        sp.add_argument("--loopback", default=VM_MATCH, help="capture loopback name substring")
        sp.add_argument("--feed", default=VM_MATCH if name in ("order", "duplex") else None,
                        help="feed a known signal into this output-device name substring")
        sp.add_argument("--output", default="FiiO", help="headphone output name substring")
        sp.add_argument("--seconds", type=float, default=2.0 if name == "order" else 15.0)
        sp.add_argument("--block", type=int, default=1024)
        sp.add_argument("--latency-ms", type=float, default=120.0)
        sp.add_argument("--arch", choices=("inline", "worker"), default="worker")
        sp.add_argument("--gpu", action="store_true")
        sp.add_argument("--wav", default=None, help="record the output to this WAV path")
    args = p.parse_args(argv)
    return {"devices": cmd_devices, "order": cmd_order, "duplex": cmd_duplex,
            "spectral": cmd_spectral}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

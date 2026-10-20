"""A0 probe (RFC Track A, B8.7a) — classify the output-FIFO `queue` term: clock skew vs jitter.

Runs on the Windows host (real VoiceMeeter + FiiO K11). It reproduces the passthrough's
`queue`-term pipeline exactly where it matters — PyAudio WASAPI **loopback** capture of the
VoiceMeeter 7.1 render endpoint -> a burst-drain worker (MMCSS Pro-Audio, gc.freeze, like the
app) -> a cushioned output FIFO -> sounddevice output to the K11 — with a **synthetic pose** and
no camera/window (AGENTS.md: the camera does not touch FIFO timing). A player stream feeds a
looped WAV into all 8 VoiceMeeter channels so the capture is never silent.

It logs the **cumulative (captured − played) frame divergence** each output callback. The SLOPE
of that series is the true clock skew (ppm) and is *not* hidden by the FIFO drop-cap (dropped
frames still count as captured-but-not-played); the detrended variance is the jitter/burst term.
``binaural_headtracker.fifo_drift`` classifies it (imported from the repo ``src`` if present).

Driven from WSL via interop, e.g.::

    cmd.exe /c "cd /d C:\\Users\\ibarz\\BinauralHeadtracker && py -3.12 \\
        \\\\wsl.localhost\\...\\spikes\\a0_fifo_probe.py --seconds 90"

Result (owner's host, 2026-10-20, 512@20 shared, two runs): skew ≈ 4–27 ppm (≤2.4 ms over 90 s —
negligible), jitter ≈ 3–5 ms RMS, trend fraction ≈ 0 -> JITTER-dominated. A drift-compensating
resampler (A1) corrects skew, of which there is essentially none, so it would not cut the queue
term; the floor is VoiceMeeter's bursty loopback delivery (journal 52).
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
import sounddevice as sd


def _find_sd(name_parts: list[str], min_out: int) -> int:
    apis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if apis[d["hostapi"]]["name"] != "Windows WASAPI":
            continue
        if all(p in d["name"].lower() for p in name_parts) and d["max_output_channels"] >= min_out:
            return i
    raise SystemExit(f"no WASAPI output device matching {name_parts} (>= {min_out}ch)")


def _load_wav_loop(path: str, channels: int, sr: int) -> np.ndarray:
    """Read a PCM WAV as float32 mono and tile it across `channels` (no channel left silent)."""
    try:
        with wave.open(path, "rb") as w:
            n, sw, nch, fr = w.getnframes(), w.getsampwidth(), w.getnchannels(), w.getframerate()
            raw = w.readframes(n)
        dt = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
        data = np.frombuffer(raw, dtype=dt).astype(np.float32) / float(np.iinfo(dt).max)
        data = data.reshape(-1, nch).mean(axis=1)
        if fr != sr:
            idx = (np.arange(int(data.size * sr / fr)) * fr / sr).astype(int)
            data = data[np.clip(idx, 0, data.size - 1)]
    except (FileNotFoundError, wave.Error, KeyError) as exc:
        print(f"WAV load failed ({exc!r}); using continuous noise.")
        data = np.random.default_rng(0).standard_normal(sr * 2).astype(np.float32) * 0.1
    out = np.zeros((data.size, channels), np.float32)
    for c in range(channels):
        out[:, c] = np.roll(data, c * 97)  # small per-channel offset so all channels carry signal
    return out


def _elevate_pro_audio(keep: list) -> str:
    """MMCSS 'Pro Audio' for the current thread (matches the passthrough's render worker)."""
    import ctypes
    from ctypes import wintypes
    try:
        avrt = ctypes.WinDLL("avrt")
        avrt.AvSetMmThreadCharacteristicsW.restype = wintypes.HANDLE
        avrt.AvSetMmThreadCharacteristicsW.argtypes = [wintypes.LPCWSTR,
                                                       ctypes.POINTER(wintypes.DWORD)]
        h = avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(wintypes.DWORD(0)))
        if h:
            avrt.AvSetMmThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
            avrt.AvSetMmThreadPriority(h, 1)
            keep.append(h)
            return "MMCSS 'Pro Audio'"
    except Exception:  # noqa: BLE001
        pass
    return "normal"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A0: classify the FIFO queue term (skew vs jitter).")
    p.add_argument("--seconds", type=float, default=90.0)
    p.add_argument("--block", type=int, default=512)
    p.add_argument("--latency-ms", type=float, default=20.0)
    p.add_argument("--samplerate", type=int, default=48000)
    p.add_argument("--channels", type=int, default=8)
    p.add_argument("--wav", default="dry_snare.wav")
    p.add_argument("--out-csv", default="a0_fifo_fill.csv")
    p.add_argument("--player-gain", type=float, default=0.35)
    p.add_argument("--output-gain", type=float, default=0.30)
    args = p.parse_args(argv)

    sr, block, in_ch = args.samplerate, args.block, args.channels
    k11 = _find_sd(["fiio", "k11"], 2)
    vm_in = _find_sd(["voicemeeter input", "vaio"], in_ch)
    print(f"player -> sd[{vm_in}] {sd.query_devices(vm_in)['name']}")
    print(f"output -> sd[{k11}] {sd.query_devices(k11)['name']}")

    import pyaudiowpatch as pawp
    pya = pawp.PyAudio()
    lb = next((d for d in pya.get_loopback_device_info_generator()
               if "voicemeeter input" in d["name"].lower()), None)
    if lb is None:
        raise SystemExit("no VoiceMeeter Input WASAPI loopback device — is VoiceMeeter running?")
    print(f"capture (loopback) -> pa[{lb['index']}] {lb['name']} {int(lb['maxInputChannels'])}ch")

    cushion = max(2, round(args.latency_ms * 1e-3 * sr / block))
    max_backlog, in_q_max = cushion * 2, max(8, 4 * cushion)
    print(f"block {block}  latency ~{args.latency_ms:.0f} ms  cushion {cushion}  "
          f"max_backlog {max_backlog}  for {args.seconds:.0f}s")

    in_q: deque = deque()
    in_q_lock = threading.Lock()
    out_fifo: deque = deque((np.zeros((block, 2), np.float32), None) for _ in range(cushion))
    fifo_lock = threading.Lock()
    in_tail = {"buf": np.zeros((0, in_ch), np.float32)}
    stop = threading.Event()
    errors: list[BaseException] = []
    counts = {"under": 0, "over": 0}
    cum = {"in": 0, "out": 0}
    standing = {"sum": 0, "n": 0}
    keep_mmcss: list = []
    rows: list[tuple[float, int]] = []
    t0 = time.monotonic()

    dm = np.zeros((in_ch, 2), np.float32)  # cheap 8->2 downmix (content irrelevant to the clock)
    dm[0::2, 0] = 0.5
    dm[1::2, 1] = 0.5

    wav = _load_wav_loop(args.wav, in_ch, sr)
    play_pos = {"i": 0}

    def play_cb(outdata, frames, _t, _s):  # noqa: ANN001
        i = play_pos["i"]
        outdata[:] = wav[(i + np.arange(frames)) % wav.shape[0]] * args.player_gain
        play_pos["i"] = int((i + frames) % wav.shape[0])

    def pa_in_cb(in_data, frame_count, _ti, _st):  # noqa: ANN001
        try:
            cap = np.frombuffer(in_data, dtype=np.float32).reshape(-1, in_ch).copy()
            cum["in"] += cap.shape[0]
            with in_q_lock:
                in_q.append(cap)
                while len(in_q) > in_q_max:
                    counts["over"] += 1
                    in_q.popleft()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        return (None, pawp.paContinue)

    def worker() -> None:
        print(f"render thread: {_elevate_pro_audio(keep_mmcss)} scheduling")
        while not stop.is_set():
            with in_q_lock:
                chunks = list(in_q)
                in_q.clear()
            if not chunks:
                stop.wait(0.001)
                continue
            try:
                buf = np.concatenate([in_tail["buf"], *chunks], axis=0)
                i, rendered = 0, []
                while buf.shape[0] - i >= block:
                    rendered.append((buf[i:i + block] @ dm) * args.output_gain)
                    i += block
                in_tail["buf"] = buf[i:]
                if rendered:
                    pose_t = time.monotonic()  # synthetic: always-fresh pose
                    with fifo_lock:
                        out_fifo.extend((b.astype(np.float32), pose_t) for b in rendered)
                        while len(out_fifo) > max_backlog:
                            counts["over"] += 1
                            out_fifo.popleft()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    carry = {"buf": np.zeros((0, 2), np.float32)}

    def out_cb(outdata, frames, _t, _s):  # noqa: ANN001
        try:
            now = time.monotonic()
            cum["out"] += frames
            buf = carry["buf"]
            with fifo_lock:
                standing["sum"] += len(out_fifo) * block
                standing["n"] += 1
                rows.append((now - t0, cum["in"] - cum["out"]))
                while buf.shape[0] < frames and out_fifo:
                    block_data, _pose = out_fifo.popleft()
                    buf = np.concatenate([buf, block_data], axis=0)
            n = min(frames, buf.shape[0])
            outdata[:n] = buf[:n]
            if n < frames:
                outdata[n:] = 0
                counts["under"] += 1
            carry["buf"] = buf[n:]
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            outdata.fill(0)

    player = sd.OutputStream(samplerate=sr, channels=in_ch, device=vm_in, blocksize=block,
                             dtype="float32", callback=play_cb)
    in_stream = pya.open(format=pawp.paFloat32, channels=in_ch, rate=sr, input=True,
                         input_device_index=lb["index"], frames_per_buffer=block,
                         stream_callback=pa_in_cb)
    out_stream = sd.OutputStream(samplerate=sr, channels=2, device=k11, blocksize=0,
                                 dtype="float32", latency="low", callback=out_cb)

    import gc
    gc.collect()
    gc.freeze()
    th = threading.Thread(target=worker, name="render", daemon=True)
    player.start()
    th.start()
    in_stream.start_stream()
    out_stream.start()
    print(f"output: WASAPI shared ({out_stream.latency * 1000:.0f} ms device buffer) — running...")
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        print("interrupted")
    stop.set()
    time.sleep(0.05)
    out_stream.stop()
    out_stream.close()
    in_stream.stop_stream()
    in_stream.close()
    player.stop()
    player.close()
    pya.terminate()

    if errors:
        print(f"callback error: {errors[0]!r}")
    with open(args.out_csv, "w", encoding="utf-8") as fh:
        fh.write("time_s,fill_frames\n")
        for t_s, fr in rows:
            fh.write(f"{t_s:.6f},{fr}\n")
    mean_standing = (standing["sum"] / standing["n"] / sr * 1000.0) if standing["n"] else 0.0
    net = cum["in"] - cum["out"]
    print(f"wrote {len(rows)} rows to {args.out_csv} | underruns={counts['under']} "
          f"overflow_drops={counts['over']}")
    print(f"mean standing FIFO depth {mean_standing:.1f} ms | net divergence {net} frames "
          f"({net / sr * 1000.0:+.1f} ms)")

    # classify inline if the repo analyzer is importable
    src = Path(__file__).resolve().parent.parent / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))
    try:
        from binaural_headtracker.fifo_drift import _format, _read_csv, analyze_fill
        times, fill = _read_csv(args.out_csv)
        print(_format(analyze_fill(times, fill, sr)))
    except Exception as exc:  # noqa: BLE001
        print(f"(analyze separately: python -m binaural_headtracker.fifo_drift "
              f"{args.out_csv}; {exc!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

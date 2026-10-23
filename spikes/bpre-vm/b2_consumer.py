"""B2 consumer + latency harness (RFC Track B, host-only).

The real pull-driven consumer the whole of Track B was building toward, plus an unattended latency /
drop measurement. Wiring (journal 62/63 design, no cushion FIFO):

  producer  --(8ch 7.1 render, shared)-->  SYSVAD virtual sink (pull-driven)
                                               |  (advances one period per KookrPullTick)
                                               v
  loopback capture (8ch) --> render thread (RustWorldLocked7p1 -> stereo) --> latest_stereo (1-deep)
                                               |
  K11 output callback: SetEvent(KookrPullTick)  +  write latest_stereo  (the single master clock)

The K11 render callback is the only thing that ticks, so the SYSVAD device, its loopback, and the
producer all advance 1:1 with the K11 hardware clock -> one clock, no drift, no jitter buffer. The
producer stands in for games/apps (unattended test signal); with real apps playing, drop --no-producer.

Measures, per (block, mode): realised device latency in/out, per-block render time, and under/overflow
counts on capture and output. Pass/fail gate = ZERO drops over the run.

Usage (host, py -3.12):
  py -3.12 b2_consumer.py --brir-dir "<baked_7p1>" --block 512 --mode exclusive --seconds 60 [--rust]
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import threading
import time
from ctypes import POINTER, Structure, byref, c_void_p, sizeof, wintypes
from pathlib import Path

import numpy as np

# import the WSL repo's binaural_headtracker (run from the host over the \\wsl.localhost UNC path)
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))

import sounddevice as sd  # noqa: E402

SR = 48000
CH_IN = 8  # 7.1


# ----------------------------------------------------------------- KookrPullTick (pull the device)
def make_pull_event():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    class SA(Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSD", c_void_p), ("bInherit", wintypes.BOOL)]

    sdb = (ctypes.c_byte * 64)()
    adv.InitializeSecurityDescriptor(byref(sdb), 1)
    adv.SetSecurityDescriptorDacl(byref(sdb), True, None, False)  # NULL DACL -> SYSTEM driver can open
    sa = SA(sizeof(SA), ctypes.cast(byref(sdb), c_void_p), False)
    k.CreateEventW.restype = wintypes.HANDLE
    k.CreateEventW.argtypes = [POINTER(SA), wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k.SetEvent.argtypes = [wintypes.HANDLE]
    h = k.CreateEventW(byref(sa), False, False, "Global\\KookrPullTick")
    if not h:
        raise OSError(f"CreateEvent failed (err {ctypes.get_last_error()})")
    return k, h


# ----------------------------------------------------------------- device discovery
def find_dev(name_substrs, out=True):
    want_out = out
    for i, d in enumerate(sd.query_devices()):
        ha = sd.query_hostapis(d["hostapi"])["name"]
        if "WASAPI" not in ha:
            continue
        ch = d["max_output_channels"] if want_out else d["max_input_channels"]
        if ch <= 0:
            continue
        if all(s.lower() in d["name"].lower() for s in name_substrs):
            return i, d
    return None, None


# ----------------------------------------------------------------- DSP
class Passthrough:
    """Cheap 8->2 downmix stand-in (isolates the device/pacing latency without the BRIR)."""

    block_size = 0

    def __init__(self, block):
        self.block_size = block
        # L R C LFE RL RR SL SR  ->  stereo downmix (ITU-ish)
        m = np.zeros((8, 2), dtype=np.float64)
        m[0] = (1.0, 0.0)   # L
        m[1] = (0.0, 1.0)   # R
        m[2] = (0.707, 0.707)  # C
        m[3] = (0.5, 0.5)   # LFE
        m[4] = (0.85, 0.0)  # RL
        m[5] = (0.0, 0.85)  # RR
        m[6] = (0.85, 0.0)  # SL
        m[7] = (0.0, 0.85)  # SR
        self._m = m

    def process_block(self, pcm, azimuths=None):
        return (np.asarray(pcm, dtype=np.float64) @ self._m).astype(np.float32)


def build_renderer(use_rust, brir_dir, block):
    if not use_rust:
        return Passthrough(block), "passthrough(8->2 downmix)"
    from binaural_headtracker.live_render import ring_azimuths
    from binaural_headtracker.rust_render import RustWorldLocked7p1
    from binaural_headtracker.surround import load_baked_brir
    brir = load_baked_brir(Path(brir_dir))
    r = RustWorldLocked7p1(brir, block_size=block, lfe_mode="brir")
    r._azimuths = ring_azimuths(brir)  # static forward pose (constant) for an unattended run
    return r, f"RustWorldLocked7p1 (brir sr={brir.samplerate}, {brir.n_channels}ch)"


# ----------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--brir-dir")
    ap.add_argument("--block", type=int, default=512)
    ap.add_argument("--mode", choices=("shared", "exclusive"), default="exclusive")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--rust", action="store_true")
    ap.add_argument("--no-producer", action="store_true")
    ap.add_argument("--k11", default="FiiO")
    args = ap.parse_args(argv)

    blk = args.block
    rend, rlabel = build_renderer(args.rust, args.brir_dir, blk)
    az = getattr(rend, "_azimuths", None)

    sv_idx, sv = find_dev(["Tablet Sample", "parleurs"], out=True) or find_dev(["Tablet Sample", "Speakers"], out=True)
    if sv_idx is None:
        print("SYSVAD 7.1 speakers endpoint not found"); return 2
    k11_idx, k11 = find_dev([args.k11], out=True)
    if k11_idx is None:
        print(f"K11 output ({args.k11}) not found"); return 2
    print(f"renderer: {rlabel}")
    print(f"sink[{sv_idx}] = {sv['name']} (maxout={sv['max_output_channels']})")
    print(f"K11 [{k11_idx}] = {k11['name']}  mode={args.mode} block={blk} ({blk/SR*1e3:.1f} ms)")

    k, hev = make_pull_event()

    latest = {"stereo": np.zeros((blk, 2), dtype=np.float32), "n": 0}
    lock = threading.Lock()
    st = {"cap_cb": 0, "cap_over": 0, "cap_under": 0, "out_cb": 0, "out_under": 0,
          "ticks": 0, "render_us": [], "no_data": 0}

    # ---- loopback capture (pyaudiowpatch) + render in this thread ----
    import pyaudiowpatch as pa
    pya = pa.PyAudio()
    lb = None
    for d in pya.get_loopback_device_info_generator():
        nm = d["name"]
        if ("Tablet Sample" in nm) and (("parleurs" in nm) or ("Speakers" in nm)):
            lb = d; break
    if lb is None:
        print("no WASAPI loopback device for the SYSVAD speakers"); return 2
    lb_ch = int(lb["maxInputChannels"])
    print(f"loopback[{lb['index']}] = {lb['name']}  {lb_ch}ch")

    def cap_cb(in_data, frame_count, time_info, status):
        st["cap_cb"] += 1
        if status:  # paInputOverflow=flag; treat any nonzero as a glitch
            if status & pa.paInputOverflow:
                st["cap_over"] += 1
            if status & pa.paInputUnderflow:
                st["cap_under"] += 1
        pcm = np.frombuffer(in_data, dtype=np.float32)
        n = pcm.size // lb_ch
        pcm = pcm[: n * lb_ch].reshape(n, lb_ch)
        if n >= blk:
            x = pcm[:blk, :8] if lb_ch >= 8 else np.pad(pcm[:blk], ((0, 0), (0, 8 - lb_ch)))
            t0 = time.perf_counter()
            stereo = rend.process_block(x, az) if az is not None else rend.process_block(x)
            st["render_us"].append((time.perf_counter() - t0) * 1e6)
            with lock:
                latest["stereo"] = np.ascontiguousarray(stereo[:blk], dtype=np.float32)
                latest["n"] += 1
        return (None, pa.paContinue)

    cap_stream = pya.open(format=pa.paFloat32, channels=lb_ch, rate=SR, input=True,
                          input_device_index=lb["index"], frames_per_buffer=blk,
                          stream_callback=cap_cb)

    # ---- producer: 8ch test signal into the SYSVAD sink (the stand-in app) ----
    prod_stream = None
    if not args.no_producer:
        ph = {"p": 0}
        freqs = np.array([110, 140, 180, 60, 220, 260, 300, 340], dtype=np.float64)  # per-channel tones

        def prod_cb(outdata, frames, tinfo, status):
            t = (ph["p"] + np.arange(frames))[:, None] / SR
            outdata[:] = (0.05 * np.sin(2 * np.pi * freqs[None, :] * t)).astype(np.float32)
            ph["p"] += frames
        prod_stream = sd.OutputStream(device=sv_idx, samplerate=SR, channels=8, dtype="float32",
                                      blocksize=blk, callback=prod_cb)

    # ---- K11 output: tick + emit latest stereo (the master clock) ----
    extra = None
    if args.mode == "exclusive":
        extra = sd.WasapiSettings(exclusive=True)

    def out_cb(outdata, frames, tinfo, status):
        st["out_cb"] += 1
        if status and status.output_underflow:
            st["out_under"] += 1
        k.SetEvent(hev)  # advance the pull-driven SYSVAD device, locked to this hardware clock
        st["ticks"] += 1
        with lock:
            s = latest["stereo"]
        if s.shape[0] >= frames:
            outdata[:] = s[:frames]
        else:
            outdata.fill(0); st["no_data"] += 1

    out_stream = sd.OutputStream(device=k11_idx, samplerate=SR, channels=2, dtype="float32",
                                 blocksize=blk, callback=out_cb, extra_settings=extra)

    # ---- run ----
    try:
        cap_stream.start_stream()
        if prod_stream is not None:
            prod_stream.start()
        out_stream.start()
    except Exception as e:  # noqa: BLE001
        print(f"STREAM OPEN FAILED ({args.mode}): {e!r}")
        try: cap_stream.close()
        except Exception: pass
        return 3
    dev_in = cap_stream.get_input_latency() * 1e3
    dev_out = out_stream.latency * 1e3
    time.sleep(args.seconds)

    out_stream.stop(); out_stream.close()
    if prod_stream is not None:
        prod_stream.stop(); prod_stream.close()
    cap_stream.stop_stream(); cap_stream.close(); pya.terminate()

    ru = np.array(st["render_us"]) if st["render_us"] else np.array([0.0])
    drops = st["cap_over"] + st["cap_under"] + st["out_under"] + st["no_data"]
    print("---- RESULT ----")
    print(f"block={blk} mode={args.mode} seconds={args.seconds:.0f}")
    print(f"device latency: in(loopback)={dev_in:.1f} ms  out(K11)={dev_out:.1f} ms")
    print(f"render/block: p50={np.percentile(ru,50):.0f}us p95={np.percentile(ru,95):.0f}us "
          f"max={ru.max():.0f}us  (block budget {blk/SR*1e3:.1f} ms)")
    print(f"callbacks: cap={st['cap_cb']} out={st['out_cb']} ticks={st['ticks']} rendered={latest['n']}")
    print(f"DROPS: cap_over={st['cap_over']} cap_under={st['cap_under']} out_under={st['out_under']} "
          f"no_data={st['no_data']}  TOTAL={drops}")
    approx = dev_in + dev_out + 2 * blk / SR * 1e3  # device in+out + ~2-block pull pipeline
    print(f"approx audio latency (device_in + device_out + ~2 block pull) = {approx:.1f} ms")
    print("VERDICT: " + ("ZERO-DROP PASS" if drops == 0 else f"FAIL ({drops} drops)"))
    return 0 if drops == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""B2 consumer + latency harness (RFC Track B, host-only).

The real pull-driven consumer the whole of Track B was building toward, plus an unattended latency /
drop measurement. Wiring (journal 62/63 design):

  producer  --(8ch 7.1 render, shared)-->  SYSVAD virtual sink (pull-driven)
                                               |  (advances one period per KookrPullTick)
                                               v
  loopback capture (8ch) --> render EVERY complete block (-> stereo) --> bounded FIFO (out_q)
                                               |
  K11 output callback: SetEvent(KookrPullTick)  +  pop one block from out_q  (the single master clock)

The K11 callback is the only thing that ticks, so the SYSVAD device, its loopback, and the producer
advance against the K11 hardware clock. The loopback callback delivers frames in bursts (often > blk
per callback) at a different cadence than the output, so a small bounded FIFO decouples the two: the
capture side renders every complete block into it (journal 71 — rendering only the first block per
callback discarded the surplus and beeped), the output side pops one per callback, and a FIFO empty
(starve) or full (overflow) is counted as a real drop. The producer stands in for games/apps
(unattended test signal); with real apps playing, drop --no-producer.

Measures, per (block, mode): realised device latency in/out, per-block render time, capture/output
frame balance, FIFO depth, and real under/overflow/starve counts. Pass/fail gate = ZERO drops.

Usage (host, py -3.12):
  py -3.12 b2_consumer.py --brir-dir "<baked_7p1>" --block 512 --mode exclusive --seconds 60 [--rust]
  # First validate on a fresh boot with:  --mode shared --seconds 6   (exclusive-open is the leg
  # that can wedge; shared first proves the pipe, then raise --seconds / switch to exclusive).

SAFETY (journal 70 — a BSOD cost us a whole session): every exit path tears down the *streams*
only. If a run ever leaves the SYSVAD endpoint stuck (post-run "KookrPullTick clear: False"), the
ONLY safe recovery is a host REBOOT. NEVER uninstall / remove / disable / `devcon restart` the
virtual-audio driver to force a stuck stream shut — unloading it with a stream still open
bugchecks (BSOD) the host.
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import os
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

    lock = threading.Lock()
    # Bounded jitter FIFO of rendered stereo blocks. The loopback callback delivers frames in
    # bursts (often > blk per callback) and at a different cadence than the K11 output callback,
    # so a 1-deep "latest block" either discards surplus capture or replays a stale block — both
    # audible as beeps (journal 71). The FIFO decouples the two: capture renders EVERY complete
    # block into it in order; the K11 consumes one per callback.
    OUT_Q_MAX = 8  # ~85 ms at block 512 / 48 kHz; overflow means capture is outpacing output
    out_q = collections.deque()
    armed = {"v": False}  # set True after a warm-up so FIFO priming isn't counted as drops
    st = {"cap_cb": 0, "cap_over": 0, "cap_under": 0, "out_cb": 0, "out_under": 0,
          "ticks": 0, "render_us": [], "rendered": 0, "out_starve": 0, "q_over": 0,
          "q_depth_max": 0, "cap_frames": 0, "out_frames": 0}

    # ---- loopback capture (pyaudiowpatch) + render in this thread ----
    import pyaudiowpatch as pa
    pya = pa.PyAudio()
    cap_stream = prod_stream = out_stream = None

    # --------------------------------------------------------------------- safe teardown
    # WHY THIS EXISTS (docs/research-journal/70): a half-open stream on the SYSVAD sink
    # pins the kernel pull-event (Global\KookrPullTick) and keeps audiodg attached to the
    # device. The ONLY safe way to clear a stuck one is a host REBOOT. Never unload /
    # remove / disable / `devcon restart` the virtual-audio driver to force the stream
    # shut — tearing the driver down with a stream still open bugchecks (BSOD) the host.
    # So every exit path — normal, exception, KeyboardInterrupt, or watchdog deadline —
    # must route through teardown(), which closes the streams (never the driver).
    def _close(label, *actions):
        try:
            for act in actions:
                act()
        except Exception as e:  # noqa: BLE001 — teardown must never raise
            print(f"  teardown {label}: {e!r}")

    _td_lock = threading.Lock()

    def teardown():
        # Serialised + idempotent: the watchdog thread and the main thread's `finally`
        # may both reach here. Closes streams first, then the pull-event handle, so no
        # audio callback runs after its handle is freed. Never touches the driver.
        nonlocal cap_stream, prod_stream, out_stream, hev, pya
        with _td_lock:
            if out_stream is not None:
                _close("out_stream", out_stream.stop, out_stream.close)
                out_stream = None
            if prod_stream is not None:
                _close("prod_stream", prod_stream.stop, prod_stream.close)
                prod_stream = None
            if cap_stream is not None:
                _close("cap_stream", cap_stream.stop_stream, cap_stream.close)
                cap_stream = None
            if pya is not None:
                _close("pyaudio", pya.terminate)
                pya = None
            if hev:
                _close("pull_event", lambda: k.CloseHandle(hev))
                hev = None

    def pulltick_is_clear():
        """True if Global\\KookrPullTick is NOT pinned by a leaked stream."""
        probe = ctypes.WinDLL("kernel32", use_last_error=True)
        probe.CreateEventW.restype = wintypes.HANDLE
        h = probe.CreateEventW(None, 1, 0, "Global\\KookrPullTick")
        err = ctypes.get_last_error()
        if h:
            probe.CloseHandle(h)
        # 0 = created fresh, 183 = exists but openable (ours) -> clear;
        # 5 = ACCESS_DENIED -> a leaked stream still pins it.
        return err in (0, 183)

    def print_recovery_banner():
        print("\n" + "=" * 72)
        print("!! SAFE RECOVERY — the SYSVAD endpoint may hold a half-open stream.")
        print("   To clear it: REBOOT the host. That is the ONLY safe recovery.")
        print("   Do NOT uninstall / remove / disable / `devcon restart` the")
        print("   'Virtual Audio Device (WDM) - Tablet Sample' driver to force it —")
        print("   unloading it with a stream still open BUGCHECKS (BSOD) the host.")
        print("   (Root cause: docs/research-journal/70-*.md)")
        print("=" * 72)

    rc = 3
    crashed = False

    # Hard deadline that bounds the ENTIRE open+run window — a WASAPI open (the exclusive
    # K11 leg especially) or a start() can wedge indefinitely, and nothing on the main
    # thread could then close it. As a last resort the watchdog hard-exits the PROCESS
    # (client-side only: the OS reclaims this process's audio streams; the driver is never
    # unloaded, so no bugcheck). If that still leaves the endpoint pinned, the banner says
    # the only recovery is a reboot. Armed before the first open; cancelled once teardown
    # completes on a healthy run.
    def _watchdog_fire():
        print(f"\nWATCHDOG: {args.seconds:.0f}s + grace elapsed with the run wedged — "
              f"hard-exiting the process (client streams only; the driver is left installed).")
        print_recovery_banner()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(4)
    watchdog = threading.Timer(args.seconds + 20.0, _watchdog_fire)
    watchdog.daemon = True
    watchdog.start()

    try:
        lb = None
        for d in pya.get_loopback_device_info_generator():
            nm = d["name"]
            if ("Tablet Sample" in nm) and (("parleurs" in nm) or ("Speakers" in nm)):
                lb = d; break
        if lb is None:
            print("no WASAPI loopback device for the SYSVAD speakers"); return 2
        lb_ch = int(lb["maxInputChannels"])
        print(f"loopback[{lb['index']}] = {lb['name']}  {lb_ch}ch")

        cap_accum = {"buf": np.zeros((0, 8), dtype=np.float32)}  # frames not yet forming a full block

        def cap_cb(in_data, frame_count, time_info, status):
            st["cap_cb"] += 1
            if status:  # paInputOverflow=flag; treat any nonzero as a glitch
                if status & pa.paInputOverflow and armed["v"]:
                    st["cap_over"] += 1
                if status & pa.paInputUnderflow and armed["v"]:
                    st["cap_under"] += 1
            pcm = np.frombuffer(in_data, dtype=np.float32)
            n = pcm.size // lb_ch
            if n == 0:
                return (None, pa.paContinue)
            pcm = pcm[: n * lb_ch].reshape(n, lb_ch)
            x8 = pcm[:, :8] if lb_ch >= 8 else np.pad(pcm, ((0, 0), (0, 8 - lb_ch)))
            if armed["v"]:
                st["cap_frames"] += n
            # Keep EVERY captured frame: append to the carry buffer and render each complete block.
            # (Rendering only the first blk per callback was the beeps bug — it discarded the rest.)
            buf = cap_accum["buf"]
            buf = x8 if buf.shape[0] == 0 else np.concatenate((buf, x8), axis=0)
            off = 0
            while buf.shape[0] - off >= blk:
                blockin = buf[off:off + blk]
                t0 = time.perf_counter()
                stereo = rend.process_block(blockin, az) if az is not None else rend.process_block(blockin)
                if armed["v"]:
                    st["render_us"].append((time.perf_counter() - t0) * 1e6)
                stereo = np.ascontiguousarray(stereo[:blk], dtype=np.float32)
                with lock:
                    if len(out_q) >= OUT_Q_MAX:
                        out_q.popleft()
                        if armed["v"]:
                            st["q_over"] += 1  # capture outpaced output -> a real dropped block
                    out_q.append(stereo)
                    if armed["v"]:
                        st["rendered"] += 1
                        if len(out_q) > st["q_depth_max"]:
                            st["q_depth_max"] = len(out_q)
                off += blk
            cap_accum["buf"] = np.ascontiguousarray(buf[off:])
            return (None, pa.paContinue)

        cap_stream = pya.open(format=pa.paFloat32, channels=lb_ch, rate=SR, input=True,
                              input_device_index=lb["index"], frames_per_buffer=blk,
                              stream_callback=cap_cb)

        # ---- producer: 8ch test signal into the SYSVAD sink (the stand-in app) ----
        if not args.no_producer:
            ph = {"p": 0}
            freqs = np.array([110, 140, 180, 60, 220, 260, 300, 340], dtype=np.float64)  # per-channel tones

            def prod_cb(outdata, frames, tinfo, status):
                t = (ph["p"] + np.arange(frames))[:, None] / SR
                outdata[:] = (0.05 * np.sin(2 * np.pi * freqs[None, :] * t)).astype(np.float32)
                ph["p"] += frames
            prod_stream = sd.OutputStream(device=sv_idx, samplerate=SR, channels=8, dtype="float32",
                                          blocksize=blk, callback=prod_cb)

        # ---- K11 output: tick + pop one rendered block from the FIFO (the master clock) ----
        extra = None
        if args.mode == "exclusive":
            extra = sd.WasapiSettings(exclusive=True)

        def out_cb(outdata, frames, tinfo, status):
            st["out_cb"] += 1
            if status and status.output_underflow and armed["v"]:
                st["out_under"] += 1
            h = hev  # may be None once teardown has run; a late callback must not SetEvent a freed handle
            if h:
                k.SetEvent(h)  # advance the pull-driven SYSVAD device, locked to this hardware clock
            st["ticks"] += 1
            if armed["v"]:
                st["out_frames"] += frames
            with lock:
                block = out_q.popleft() if out_q else None
            if block is not None and block.shape[0] >= frames:
                outdata[:] = block[:frames]
            else:
                # FIFO empty (or short block): a real, audible gap — count it honestly instead of
                # replaying a stale block (the old 1-deep path hid this and still reported ZERO drops).
                outdata.fill(0)
                if armed["v"]:
                    st["out_starve"] += 1

        out_stream = sd.OutputStream(device=k11_idx, samplerate=SR, channels=2, dtype="float32",
                                     blocksize=blk, callback=out_cb, extra_settings=extra)

        # ---- run (watchdog, armed before the opens above, bounds any wedge) ----
        try:
            cap_stream.start_stream()
            if prod_stream is not None:
                prod_stream.start()
            out_stream.start()
        except Exception as e:  # noqa: BLE001
            print(f"STREAM OPEN FAILED ({args.mode}): {e!r}")
            crashed = True
            return 3  # finally still tears the streams down
        dev_in = cap_stream.get_input_latency() * 1e3
        dev_out = out_stream.latency * 1e3

        # Warm-up: let the FIFO prime and the streams settle, THEN start counting, so the
        # unavoidable first-block priming starves don't read as drops. Measure for --seconds after.
        time.sleep(0.7)
        armed["v"] = True
        deadline = time.monotonic() + args.seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))
        armed["v"] = False

        # graceful stop BEFORE reading final counters (idempotent with the finally)
        teardown()

        ru = np.array(st["render_us"]) if st["render_us"] else np.array([0.0])
        drops = (st["cap_over"] + st["cap_under"] + st["out_under"]
                 + st["out_starve"] + st["q_over"])
        print("---- RESULT ----")
        print(f"block={blk} mode={args.mode} seconds={args.seconds:.0f}")
        print(f"device latency: in(loopback)={dev_in:.1f} ms  out(K11)={dev_out:.1f} ms")
        print(f"render/block: p50={np.percentile(ru,50):.0f}us p95={np.percentile(ru,95):.0f}us "
              f"max={ru.max():.0f}us  (block budget {blk/SR*1e3:.1f} ms)")
        print(f"callbacks: cap={st['cap_cb']} out={st['out_cb']} ticks={st['ticks']} rendered={st['rendered']}")
        # capture/output frame balance: should be ~1.0 (every captured frame reaches the output).
        bal = (st["cap_frames"] / st["out_frames"]) if st["out_frames"] else 0.0
        print(f"frames: cap={st['cap_frames']} out={st['out_frames']} balance={bal:.3f}  "
              f"q_depth_max={st['q_depth_max']}/{OUT_Q_MAX}")
        print(f"DROPS: cap_over={st['cap_over']} cap_under={st['cap_under']} out_under={st['out_under']} "
              f"out_starve={st['out_starve']} q_over={st['q_over']}  TOTAL={drops}")
        approx = dev_in + dev_out + 2 * blk / SR * 1e3  # device in+out + ~2-block pull pipeline
        print(f"approx audio latency (device_in + device_out + ~2 block pull) = {approx:.1f} ms")
        print("VERDICT: " + ("ZERO-DROP PASS" if drops == 0 else f"FAIL ({drops} drops)"))
        rc = 0 if drops == 0 else 1
    except KeyboardInterrupt:
        print("\ninterrupted — tearing streams down (driver left installed)")
        crashed = True
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e!r}")
        crashed = True
    finally:
        # Tear the streams down FIRST, then disarm the watchdog — so if a close() blocks on
        # a wedged stream, the watchdog (still armed) hard-exits the process at its deadline
        # instead of the finally hanging forever.
        teardown()
        if watchdog is not None:
            watchdog.cancel()
        try:
            clear = pulltick_is_clear()
            print(f"post-run KookrPullTick clear: {clear}")
        except Exception as e:  # noqa: BLE001 — never mask the return code over a probe failure
            print(f"post-run KookrPullTick probe failed: {e!r}")
            clear = False
        if crashed or not clear:
            print_recovery_banner()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

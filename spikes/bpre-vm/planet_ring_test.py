"""planet_ring_test.py — validate the Plane T v2 render->capture ring bridge. Play a known 500 Hz
into the SYSVAD render endpoint, capture each SYSVAD input/loopback endpoint, and FFT: 500 Hz means
the loopback now carries the REAL rendered mix (ring works); 3 kHz means still the stock tone;
silence means the ring isn't being fed/drained."""
import time, threading
import numpy as np
import sounddevice as sd

FREQ = 500.0
SR = 48000
REC = 3.0


def apis():
    return {i: h["name"] for i, h in enumerate(sd.query_hostapis())}


def find_out():
    ha = apis()
    cands = [(i, d) for i, d in enumerate(sd.query_devices())
             if d["max_output_channels"] >= 8 and "Tablet Sample" in d["name"]]
    cands.sort(key=lambda c: 0 if "DirectSound" in ha.get(c[1]["hostapi"], "") else 1)
    return (cands[0][0], cands[0][1]["name"]) if cands else (None, None)


def find_ins():
    ha = apis()
    return [(i, d["name"], ha.get(d["hostapi"], ""), min(8, int(d["max_input_channels"])))
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0 and "Tablet Sample" in d["name"]]


stop = threading.Event()
ph = {"p": 0}
def ocb(o, f, t, s):  # noqa: ANN001
    n = np.arange(f) + ph["p"]
    tone = (0.3 * np.sin(2 * np.pi * FREQ * n / SR)).astype("float32")
    o[:] = 0.0; o[:, 0] = tone
    if o.shape[1] > 1: o[:, 1] = tone
    ph["p"] += f


outdev, outname = find_out()
print("play:", outdev, repr(outname))
ins = find_ins()
print("inputs:", [(i, api, nch, nm) for (i, nm, api, nch) in ins])

def play():
    try:
        with sd.OutputStream(device=outdev, samplerate=SR, channels=8, dtype="float32",
                             blocksize=0, callback=ocb):
            stop.wait(REC * max(1, len(ins)) + 12)
    except Exception as e:  # noqa: BLE001
        print("play err:", repr(e))

if outdev is not None:
    threading.Thread(target=play, daemon=True).start()
time.sleep(1.2)

for (i, nm, api, nch) in ins:
    print(f"--- capture dev {i} [{api}] {nch}ch {nm!r} ---")
    try:
        rec = sd.rec(int(REC * SR), samplerate=SR, channels=nch, dtype="float32", device=i)
        sd.wait()
        x = rec[:, 0] if rec.ndim > 1 else rec
        rms = float(np.sqrt(np.mean(x ** 2)))
        if rms < 1e-4:
            print(f"   SILENT (rms {rms:.6f})"); continue
        seg = x[: SR * 2] if x.size > SR * 2 else x
        sp = np.abs(np.fft.rfft(seg * np.hanning(seg.size)))
        fr = np.fft.rfftfreq(seg.size, 1.0 / SR)
        pk = fr[int(np.argmax(sp[1:]) + 1)]
        tag = ("REAL 500 Hz -> RING BRIDGE WORKS" if abs(pk - FREQ) < 20
               else ("3 kHz stock tone" if abs(pk - 3000) < 60 else f"{pk:.0f} Hz (other)"))
        print(f"   rms {rms:.4f}  dominant {pk:.0f} Hz  -> {tag}")
    except Exception as e:  # noqa: BLE001
        print("   capture err:", repr(e))
stop.set(); time.sleep(0.3)

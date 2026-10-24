"""planet_dma_check.py — is the render DMA empty because the DRIVER doesn't get audio, or because my
TEST playback is silent? Matrix: for each SYSVAD output device x {2ch, 8ch}, play 500 Hz, then read
the stock SaveData WAV and FFT. Finds any playback method that actually fills the DMA."""
import time, threading, struct, glob, os
import numpy as np
import sounddevice as sd

FREQ = 500.0
SR = 48000
DUR = 3.0
DUMPDIR = r"C:\Windows\System32\drivers\DriverData\Audio_Samples\Sysvad"


def apis():
    return {i: h["name"] for i, h in enumerate(sd.query_hostapis())}


def outs():
    ha = apis()
    return [(i, d["name"], ha.get(d["hostapi"], ""), int(d["max_output_channels"]))
            for i, d in enumerate(sd.query_devices())
            if d["max_output_channels"] >= 2 and "Tablet Sample" in d["name"]]


def clear_wavs():
    for f in glob.glob(os.path.join(DUMPDIR, "*.wav")):
        try:
            os.remove(f)
        except OSError:
            pass


def newest():
    ws = glob.glob(os.path.join(DUMPDIR, "*.wav"))
    return max(ws, key=os.path.getmtime) if ws else None


def wav_rms_freq(path):
    b = open(path, "rb").read()
    i, fmt, data = 12, None, None
    while i + 8 <= len(b):
        cid = b[i:i+4]; (sz,) = struct.unpack("<I", b[i+4:i+8]); body = b[i+8:i+8+sz]
        if cid == b"fmt ": fmt = body
        elif cid == b"data": data = body
        i += 8 + sz + (sz & 1)
    if not fmt or not data:
        return None
    tag, ch, sr, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 3 or bits == 32:
        data = data[: len(data) // 4 * 4]
        x = np.frombuffer(data, dtype=np.float32).astype(np.float64)
    else:
        data = data[: len(data) // 2 * 2]
        x = np.frombuffer(data, dtype=np.int16).astype(np.float64) / 32768.0
    ch = max(1, ch); x = x[: (x.size // ch) * ch].reshape(-1, ch)
    rms = np.sqrt((x ** 2).mean(axis=0)); c = int(np.argmax(rms)); sig = x[:, c]
    if sig.size < 2048 or rms[c] < 1e-4:
        return (float(rms[c]), None, ch, sr)
    seg = sig[: sr*2] if sig.size > sr*2 else sig
    sp = np.abs(np.fft.rfft(seg * np.hanning(seg.size))); fr = np.fft.rfftfreq(seg.size, 1.0/sr)
    return (float(rms[c]), float(fr[int(np.argmax(sp[1:]) + 1)]), ch, sr)


def play_for(devidx, nch):
    stop = threading.Event(); ph = {"p": 0}
    def ocb(o, f, t, s):  # noqa: ANN001
        n = np.arange(f) + ph["p"]
        tone = (0.4 * np.sin(2 * np.pi * FREQ * n / SR)).astype("float32")
        o[:] = 0.0
        for cch in range(min(2, o.shape[1])):
            o[:, cch] = tone
        ph["p"] += f
    try:
        with sd.OutputStream(device=devidx, samplerate=SR, channels=nch, dtype="float32",
                             blocksize=0, callback=ocb):
            time.sleep(DUR)
        return None
    except Exception as e:  # noqa: BLE001
        return repr(e)


print("DriverData exists:", os.path.isdir(DUMPDIR))
for (i, nm, api, maxch) in outs():
    for nch in sorted({2, min(8, maxch)}):
        clear_wavs(); time.sleep(0.3)
        err = play_for(i, nch); time.sleep(1.0)
        w = newest()
        try:
            r = wav_rms_freq(w) if w else None
        except Exception as e:  # noqa: BLE001
            r = None; w = w or None
        tag = "no WAV"
        if err:
            tag = f"open-fail {err[:40]}"
        elif r is None:
            tag = "WAV but unparsable" if w else "no WAV (ReadBytes not called)"
        elif r[1] is None:
            tag = f"SILENT rms{r[0]:.5f}"
        else:
            hit = "<<< FILLS DMA (REAL)" if abs(r[1]-FREQ) < 20 else ""
            tag = f"rms{r[0]:.3f} {r[1]:.0f}Hz {hit}"
        print(f"dev {i:2d} [{api:18s}] {nch}ch -> {tag}")

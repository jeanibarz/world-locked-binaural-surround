"""Measure the ring producer vs consumer rates to explain the ~7% pitch drop. Play 500 Hz, capture
the loopback with exact wall-clock timing (consumer fps + dominant freq), and read the producer's
SaveData WAV (producer fps over the play window)."""
import time, threading, struct, glob, os
import numpy as np
import sounddevice as sd
import pyaudiowpatch as pawp

FREQ = 500.0
SR = 48000
PLAY = 7.0
CAP = 5.0
DUMPDIR = r"C:\Windows\System32\drivers\DriverData\Audio_Samples\Sysvad"


def find_out():
    ha = {i: h["name"] for i, h in enumerate(sd.query_hostapis())}
    c = [(i, d) for i, d in enumerate(sd.query_devices())
         if d["max_output_channels"] >= 8 and "Tablet Sample" in d["name"]]
    c.sort(key=lambda x: 0 if "DirectSound" in ha.get(x[1]["hostapi"], "") else 1)
    return c[0][0] if c else None


# clear old producer WAVs (best effort; may need admin)
for f in glob.glob(os.path.join(DUMPDIR, "*.wav")):
    try: os.remove(f)
    except OSError: pass

outdev = find_out()
stop = threading.Event(); ph = {"p": 0}
def ocb(o, f, t, s):  # noqa: ANN001
    n = np.arange(f) + ph["p"]
    tone = (0.3 * np.sin(2 * np.pi * FREQ * n / SR)).astype("float32")
    o[:] = 0.0; o[:, 0] = tone
    if o.shape[1] > 1: o[:, 1] = tone
    ph["p"] += f
def play():
    try:
        with sd.OutputStream(device=outdev, samplerate=SR, channels=8, dtype="float32",
                             blocksize=0, callback=ocb):
            stop.wait(PLAY)
    except Exception as e:  # noqa: BLE001
        print("play err:", repr(e))
threading.Thread(target=play, daemon=True).start()
play_t0 = time.perf_counter()
time.sleep(1.0)

pa = pawp.PyAudio()
lb = next((d for d in pa.get_loopback_device_info_generator()
           if "Tablet Sample" in d["name"] and "Haut" in d["name"] and int(d["maxInputChannels"]) >= 8), None)
ch = int(lb["maxInputChannels"]); rate = int(lb["defaultSampleRate"])
print("loopback reports rate:", rate, "ch:", ch)
frames = []
def icb(ind, fc, ti, s):  # noqa: ANN001
    frames.append(ind); return (None, pawp.paContinue)
st = pa.open(format=pawp.paFloat32, channels=ch, rate=rate, input=True,
             input_device_index=lb["index"], frames_per_buffer=2048, stream_callback=icb)
t0 = time.perf_counter(); st.start_stream(); time.sleep(CAP)
st.stop_stream(); elapsed = time.perf_counter() - t0
st.close(); pa.terminate()
play_dur = time.perf_counter() - play_t0
stop.set()

raw = b"".join(frames); x = np.frombuffer(raw, dtype=np.float32)
x = x[: (x.size // ch) * ch].reshape(-1, ch)
cons_fps = x.shape[0] / elapsed
print(f"CONSUMER (loopback): {x.shape[0]} frames / {elapsed:.2f}s = {cons_fps:.0f} fps")
sig = x[:, int(np.argmax(np.sqrt((x**2).mean(0))))]
seg = sig[: rate*3]
sp = np.abs(np.fft.rfft(seg * np.hanning(seg.size))); fr = np.fft.rfftfreq(seg.size, 1.0/rate)
pk = fr[int(np.argmax(sp[1:]) + 1)]
print(f"  dominant {pk:.1f} Hz (played {FREQ})  => implied data rate {FREQ/pk*rate:.0f} if const")

ws = glob.glob(os.path.join(DUMPDIR, "*.wav"))
if ws:
    w = max(ws, key=os.path.getmtime); b = open(w, "rb").read()
    i, fmt, data = 12, None, None
    while i + 8 <= len(b):
        cid = b[i:i+4]; (sz,) = struct.unpack("<I", b[i+4:i+8]); body = b[i+8:i+8+sz]
        if cid == b"fmt ": fmt = body
        elif cid == b"data": data = body
        i += 8 + sz + (sz & 1)
    tagf, chf, srf, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    bypf = chf * (bits // 8)
    pframes = len(data) // bypf
    print(f"PRODUCER (SaveData WAV): {pframes} frames, fmt {chf}ch {srf}Hz {bits}bit over ~{play_dur:.1f}s play "
          f"=> {pframes/play_dur:.0f} fps")
else:
    print("PRODUCER: no SaveData WAV (couldn't clear/read DriverData without admin)")

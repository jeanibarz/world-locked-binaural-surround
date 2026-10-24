"""Host validation of the ring bridge: play a known 500 Hz into the SYSVAD render endpoint and
capture its WASAPI render-loopback (pyaudiowpatch). On the host, real audio flows into the device,
so: 500 Hz => the ring bridge carries the REAL rendered mix (WORKS); 3 kHz => stock tone (ring not
active / old driver); silence => ring active but unfed."""
import time, threading, sys
import numpy as np
import sounddevice as sd
import pyaudiowpatch as pawp

FREQ = 500.0
SR = 48000
REC = 4.0


def find_out():
    ha = {i: h["name"] for i, h in enumerate(sd.query_hostapis())}
    c = [(i, d) for i, d in enumerate(sd.query_devices())
         if d["max_output_channels"] >= 8 and "Tablet Sample" in d["name"]]
    c.sort(key=lambda x: 0 if "DirectSound" in ha.get(x[1]["hostapi"], "") else 1)
    return (c[0][0], c[0][1]["name"]) if c else (None, None)


outdev, outname = find_out()
print("play:", outdev, repr(outname))
stop = threading.Event()
ph = {"p": 0}
def ocb(o, f, t, s):  # noqa: ANN001
    n = np.arange(f) + ph["p"]
    tone = (0.3 * np.sin(2 * np.pi * FREQ * n / SR)).astype("float32")
    o[:] = 0.0; o[:, 0] = tone
    if o.shape[1] > 1: o[:, 1] = tone
    ph["p"] += f
def play():
    for nch in (8, 2):
        try:
            with sd.OutputStream(device=outdev, samplerate=SR, channels=nch, dtype="float32",
                                 blocksize=0, callback=ocb):
                print(f"playing {nch}ch")
                stop.wait(REC + 3); return
        except Exception as e:  # noqa: BLE001
            print(f"play {nch}ch failed: {e!r}")
if outdev is not None:
    threading.Thread(target=play, daemon=True).start()

pa = pawp.PyAudio()
lb = None
for d in pa.get_loopback_device_info_generator():
    if "Tablet Sample" in d["name"] and "Haut" in d["name"] and int(d["maxInputChannels"]) >= 8:
        lb = d; break
if lb is None:
    for d in pa.get_loopback_device_info_generator():
        if "Tablet Sample" in d["name"] and "Haut" in d["name"]:
            lb = d; break
print("loopback:", (lb["index"], lb["name"], int(lb["maxInputChannels"])) if lb else None)
if lb is None:
    print("no Haut-parleurs loopback"); stop.set(); sys.exit(0)
time.sleep(1.0)
ch = int(lb["maxInputChannels"]); rate = int(lb["defaultSampleRate"])
frames = []
def icb(ind, fc, ti, s):  # noqa: ANN001
    frames.append(ind)
    return (None, pawp.paContinue)
st = pa.open(format=pawp.paFloat32, channels=ch, rate=rate, input=True,
             input_device_index=lb["index"], frames_per_buffer=2048, stream_callback=icb)
st.start_stream(); time.sleep(REC); st.stop_stream(); st.close(); pa.terminate(); stop.set()

raw = b"".join(frames); x = np.frombuffer(raw, dtype=np.float32)
if x.size < 4096:
    print("captured almost nothing"); sys.exit(0)
x = x[: (x.size // ch) * ch].reshape(-1, ch)
rms = np.sqrt((x ** 2).mean(axis=0))
print(f"captured {x.shape[0]} frames {ch}ch {rate}Hz | per-ch RMS {np.round(rms,4).tolist()}")
c = int(np.argmax(rms)); sig = x[:, c]
if rms[c] < 1e-4:
    print("VERDICT: loopback SILENT -> ring active but unfed"); sys.exit(0)
seg = sig[: rate*3] if sig.size > rate*3 else sig
sp = np.abs(np.fft.rfft(seg * np.hanning(seg.size))); fr = np.fft.rfftfreq(seg.size, 1.0/rate)
pk = fr[int(np.argmax(sp[1:]) + 1)]
print(f"loopback dominant {pk:.0f} Hz (played {FREQ:.0f})")
if abs(pk - FREQ) < 20:
    print("VERDICT: *** RING BRIDGE WORKS *** loopback carries the real rendered audio!")
elif abs(pk - 3000) < 60:
    print("VERDICT: still 3 kHz tone -> ring build not active on the device (restart device) or old driver")
else:
    print(f"VERDICT: {pk:.0f} Hz unexpected")

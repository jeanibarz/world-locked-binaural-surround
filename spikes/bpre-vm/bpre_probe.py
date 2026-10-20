"""B-pre probe: play a tone into a SYSVAD render endpoint and measure audiodg tolerance.

Signals the RFC cares about for the pull-driven position:
  - output underflows (audiodg inserting silence / not keeping up),
  - DAC-clock (IAudioClock) monotonicity + rate (non-monotonic => position misbehaving),
  - stream staying open (no disconnect).
Run baseline (smooth timer position) then the modified driver (bursty/pull-driven) and compare.

Usage: python bpre_probe.py <device_index> <seconds>
"""
import sys
import time

import numpy as np
import sounddevice as sd

DEV = int(sys.argv[1]) if len(sys.argv) > 1 else 21
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
SR = 48000
st = {"calls": 0, "underflow": 0, "other": 0, "phase": 0, "dac": []}


def cb(outdata, frames, timeinfo, status):
    st["calls"] += 1
    if status.output_underflow:
        st["underflow"] += 1
    if status and not status.output_underflow:
        st["other"] += 1
    t = (st["phase"] + np.arange(frames)) / SR
    tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    outdata[:] = np.column_stack([tone, tone])
    st["phase"] += frames
    st["dac"].append((time.perf_counter(), float(timeinfo.outputBufferDacTime)))


err = None
try:
    with sd.OutputStream(device=DEV, samplerate=SR, channels=2, dtype="float32",
                         blocksize=0, callback=cb):
        time.sleep(DUR)
except Exception as e:  # noqa: BLE001
    err = repr(e)

dac = st["dac"]
nonmono = sum(1 for i in range(1, len(dac)) if dac[i][1] < dac[i - 1][1] - 1e-9)
rate = 0.0
if len(dac) > 10:
    wall = dac[-1][0] - dac[0][0]
    dd = dac[-1][1] - dac[0][1]
    rate = dd / wall if wall > 0 else 0.0
print(f"DEV={DEV} dur={DUR} callbacks={st['calls']} frames={st['phase']} "
      f"(~{st['phase'] / SR:.1f}s of audio)")
print(f"underflows={st['underflow']} other_status={st['other']} "
      f"dac_nonmonotonic={nonmono}/{len(dac)} dac_rate={rate:.4f} (want ~1.0)")
print(f"error={err}")
ok = err is None and st["underflow"] == 0 and nonmono == 0 and 0.95 < rate < 1.05
print("VERDICT:", "CLEAN" if ok else "ISSUES")

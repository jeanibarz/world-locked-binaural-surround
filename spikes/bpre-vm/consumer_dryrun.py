"""Consumer dry-run (B2 mechanism test): drive the pull tick from a real WASAPI render callback.

pseudo_k11.py ticked from a Python busy-wait loop. The REAL K11 consumer will tick from its WASAPI
render callback (the K11's hardware audio clock is the master). This stands in for that: it opens an
output stream on the VM's default endpoint and signals KookrPullTick once per render callback, so the
pull-driven SYSVAD device advances 1:1 with a real audio clock instead of a software timer.

If the SYSVAD device-output stays clean under this cadence, the real K11 callback (same mechanism,
tighter hardware clock) should drive it at least as cleanly.

Usage: python consumer_dryrun.py <seconds> [blocksize_frames]
"""
import ctypes
import sys
import time
from ctypes import POINTER, Structure, byref, c_void_p, sizeof, wintypes

import numpy as np
import sounddevice as sd

k = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)


class SECURITY_ATTRIBUTES(Structure):
    _fields_ = [("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


# NULL-DACL security descriptor so the SYSTEM driver can open the event we create
sd_buf = (ctypes.c_byte * 64)()
adv.InitializeSecurityDescriptor(byref(sd_buf), 1)
adv.SetSecurityDescriptorDacl(byref(sd_buf), True, None, False)
sa = SECURITY_ATTRIBUTES(sizeof(SECURITY_ATTRIBUTES), ctypes.cast(byref(sd_buf), c_void_p), False)
k.CreateEventW.restype = wintypes.HANDLE
k.CreateEventW.argtypes = [POINTER(SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
k.SetEvent.argtypes = [wintypes.HANDLE]

dur = float(sys.argv[1]) if len(sys.argv) > 1 else 14.0
blk = int(sys.argv[2]) if len(sys.argv) > 2 else 480   # 10 ms @ 48 kHz ~ one notification period
SR = 48000

h = k.CreateEventW(byref(sa), False, False, "Global\\KookrPullTick")
if not h:
    print(f"CreateEvent failed (err {ctypes.get_last_error()})")
    sys.exit(1)

st = {"cb": 0, "ticks": 0, "underflow": 0}


def cb(outdata, frames, timeinfo, status):
    st["cb"] += 1
    if status and status.output_underflow:
        st["underflow"] += 1
    outdata.fill(0)                 # the stand-in K11 output (DSP'd audio goes here in the real B2)
    k.SetEvent(h)                   # advance the pull-driven SYSVAD device, locked to this audio clock
    st["ticks"] += 1


print(f"consumer dry-run: ticking from the render callback (blocksize={blk} ~{blk/SR*1000:.1f} ms) "
      f"for {dur:.0f}s")
try:
    with sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=blk, callback=cb):
        time.sleep(dur)
except Exception as e:  # noqa: BLE001
    print(f"output stream error: {e!r}")

elapsed = dur
print(f"callbacks={st['cb']} ticks={st['ticks']} (~{st['ticks']/elapsed:.0f} Hz) "
      f"output_underflows={st['underflow']}")

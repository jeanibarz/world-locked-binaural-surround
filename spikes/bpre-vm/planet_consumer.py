"""planet_consumer.py — Plane T (rfc-sysvad-real-loopback) validation + minimal consumer.

Creates the shared section `Global\\KookrPullData` and the tick event `Global\\KookrPullTick`
(both with a permissive NULL DACL, so the SYSVAD driver can open them from kernel), plays a known
500 Hz signal into the SYSVAD 7.1 render endpoint, drives the device by setting the tick once per
10 ms period, and reads back the render window the driver exports into the section — then reports
the dominant frequency.

  500 Hz  -> the real rendered mix reaches the render DMA and Plane T delivers it (Q1=YES).
  silence -> the mix never reaches the render DMA (Q1=NO; upstream B-pre gap).

Usage: python planet_consumer.py [seconds]
"""
import sys, time, threading, ctypes as C
from ctypes import wintypes
import numpy as np
import sounddevice as sd

SR = 48000
FREQ = 500.0
PERIOD_FR = 480                 # SYSVAD notification period (10 ms @ 48k)
SEC_SIZE = 1 << 20              # 1 MiB shared section
HDR = 16                        # KOOKR_PULLDATA_HDR: seq,len,periodBytes,cap (4x ULONG)
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

k32 = C.WinDLL("kernel32", use_last_error=True)
PAGE_READWRITE = 0x04
FILE_MAP_ALL = 0xF001F
INVALID = wintypes.HANDLE(-1)

# Proper 64-bit signatures (default ctypes argtypes truncate pointers -> ERROR_NOACCESS 998).
# No explicit DACL: the default security on a user-created object already grants SYSTEM (the kernel
# pull thread) access, so the driver's ZwOpenSection/ZwOpenEvent succeed.
k32.CreateFileMappingW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
k32.CreateFileMappingW.restype = wintypes.HANDLE
k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, C.c_size_t]
k32.MapViewOfFile.restype = wintypes.LPVOID
k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
k32.CreateEventW.restype = wintypes.HANDLE
k32.SetEvent.argtypes = [wintypes.HANDLE]
k32.SetEvent.restype = wintypes.BOOL

# --- create section + tick event (default security; Global\ namespace) ---
hMap = k32.CreateFileMappingW(INVALID, None, PAGE_READWRITE, 0, SEC_SIZE, "Global\\KookrPullData")
if not hMap:
    print("CreateFileMapping failed", C.get_last_error()); sys.exit(1)
view = k32.MapViewOfFile(hMap, FILE_MAP_ALL, 0, 0, SEC_SIZE)
if not view:
    print("MapViewOfFile failed", C.get_last_error()); sys.exit(1)
C.memset(view, 0, HDR)
hTick = k32.CreateEventW(None, False, False, "Global\\KookrPullTick")   # auto-reset
if not hTick:
    print("CreateEvent failed", C.get_last_error()); sys.exit(1)
print("created Global\\KookrPullData (%d KiB) + Global\\KookrPullTick" % (SEC_SIZE >> 10))

def rd_u32(off):
    return C.cast(view + off, C.POINTER(wintypes.DWORD))[0]

# --- find SYSVAD render endpoints (any host API; WASAPI hits a PortAudio usbTerminalGUID bug on
#     this endpoint, so try MME/DirectSound first). The render DMA fills regardless of host API. ---
def find_outs():
    ha = {i: h["name"] for i, h in enumerate(sd.query_hostapis())}
    cands = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] >= 2 and "Tablet Sample" in d["name"]:
            api = ha.get(d["hostapi"], "")
            cands.append((i, d["name"], api, min(8, int(d["max_output_channels"]))))
    order = {"MME": 0, "Windows DirectSound": 1, "Windows WDM-KS": 2, "Windows WASAPI": 3}
    cands.sort(key=lambda c: (order.get(c[2], 5), -c[3]))   # prefer MME/DS, then most channels
    return cands

stop = threading.Event()
ph = {"p": 0}
def ocb(o, f, t, s):  # noqa: ANN001
    n = np.arange(f) + ph["p"]
    tone = (0.3 * np.sin(2 * np.pi * FREQ * n / SR)).astype("float32")
    o[:] = 0.0; o[:, 0] = tone
    if o.shape[1] > 1: o[:, 1] = tone
    ph["p"] += f
def play():
    for (i, nm, api, nch) in find_outs():
        try:
            with sd.OutputStream(device=i, samplerate=SR, channels=nch, dtype="float32",
                                 blocksize=0, callback=ocb):
                print(f"playing to dev {i} [{api}] {nch}ch {nm!r}")
                stop.wait(DUR + 2)
            return
        except Exception as e:  # noqa: BLE001
            print(f"open dev {i} [{api}] failed: {e!r}")
    print("NO playable SYSVAD output found")
threading.Thread(target=play, daemon=True).start()

# --- tick the device at the 10 ms period ---
def tick():
    dt = PERIOD_FR / SR
    nxt = time.perf_counter()
    while not stop.is_set():
        k32.SetEvent(hTick)
        nxt += dt
        d = nxt - time.perf_counter()
        if d > 0: time.sleep(d)
threading.Thread(target=tick, daemon=True).start()

# --- read exported windows via the seqlock, accumulate channel 0 ---
time.sleep(0.8)
acc = []
last = -1
t0 = time.time()
got = 0
while time.time() - t0 < DUR:
    s1 = rd_u32(0)
    if (s1 & 1) == 0 and s1 != last:         # even (stable) and new
        ln = rd_u32(4)
        if 0 < ln <= SEC_SIZE - HDR:
            raw = C.string_at(view + HDR, ln)
            s2 = rd_u32(0)
            if s2 == s1:                      # no tear during the copy
                x = np.frombuffer(raw, dtype=np.float32)
                ch = 8
                x = x[: (x.size // ch) * ch].reshape(-1, ch)
                acc.append(x[:, 0].copy()); got += 1
                last = s1
    time.sleep(0.001)
stop.set(); time.sleep(0.3)

print("windows read:", got, "| last seq:", last)
if not acc:
    print("VERDICT: no windows exported (seq never advanced) -> driver not writing the section")
    sys.exit(0)
sig = np.concatenate(acc)
rms = float(np.sqrt(np.mean(sig ** 2))) if sig.size else 0.0
print(f"accumulated {sig.size} samples ch0 | RMS {rms:.5f}")
if sig.size < 2048 or rms < 1e-4:
    print("VERDICT: exported window is ~SILENT -> Q1=NO (real mix not in the render DMA)")
    sys.exit(0)
seg = sig[: SR * 3] if sig.size > SR * 3 else sig
win = seg * np.hanning(seg.size)
sp = np.abs(np.fft.rfft(win)); fr = np.fft.rfftfreq(seg.size, 1.0 / SR)
pk = fr[int(np.argmax(sp[1:]) + 1)]
print(f"dominant freq: {pk:.0f} Hz (played {FREQ:.0f})")
if abs(pk - FREQ) < 20:
    print("VERDICT: Plane T delivers the REAL rendered audio -> Q1=YES, Plane T works")
else:
    print(f"VERDICT: exported dominant {pk:.0f} Hz != played {FREQ:.0f} -> investigate")

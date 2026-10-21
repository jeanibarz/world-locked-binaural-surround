"""Pseudo-K11 (B1, RFC Track B): drive the pull-driven SYSVAD device from user mode.

CREATES the named sync event (with a NULL DACL so the SYSTEM driver can open it) BEFORE the audio
stream starts, then signals it at the K11 cadence; each signal makes the driver advance one
notification period (consume + notify audiodg). Stands in for the real K11 playback callback.

Usage: python pseudo_k11.py <seconds> <interval_ms>
"""
import ctypes
import sys
import time
from ctypes import POINTER, Structure, byref, c_void_p, sizeof, wintypes

k = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)


class SECURITY_ATTRIBUTES(Structure):
    _fields_ = [("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


# build a security descriptor with a NULL DACL (everyone), so the SYSTEM driver can open the event
sd = (ctypes.c_byte * 64)()
adv.InitializeSecurityDescriptor(byref(sd), 1)                 # SECURITY_DESCRIPTOR_REVISION
adv.SetSecurityDescriptorDacl(byref(sd), True, None, False)    # present=TRUE, DACL=NULL => allow all
sa = SECURITY_ATTRIBUTES(sizeof(SECURITY_ATTRIBUTES), ctypes.cast(byref(sd), c_void_p), False)

k.CreateEventW.restype = wintypes.HANDLE
k.CreateEventW.argtypes = [POINTER(SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
k.SetEvent.argtypes = [wintypes.HANDLE]

dur = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
interval = (float(sys.argv[2]) / 1000.0) if len(sys.argv) > 2 else 0.010

h = k.CreateEventW(byref(sa), False, False, "Global\\KookrPullTick")  # auto-reset, non-signaled
if not h:
    print(f"CreateEvent failed (err {ctypes.get_last_error()})")
    sys.exit(1)
print(f"pseudo-K11 created event, ticking every {interval * 1000:.2f} ms for {dur:.0f}s")

end = time.perf_counter() + dur
nxt = time.perf_counter()
n = 0
while time.perf_counter() < end:
    k.SetEvent(h)
    n += 1
    nxt += interval
    while time.perf_counter() < nxt:  # busy-wait for ~ms precision
        pass
print(f"ticks={n} (~{n / dur:.0f} Hz)")
# keep the handle until exit so the event lives for the whole run

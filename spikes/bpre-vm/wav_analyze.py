"""Analyze a SYSVAD SaveData WAV (the device-output bytes our engine->K11 would consume) for
discontinuities. A continuous tone played through audiodg should come out continuous; silent runs
or sample jumps mean audiodg under-produced / repeated a buffer under the (bursty) cadence.

Manually parses the WAV (SaveData may write IEEE-float, which the stdlib wave module mishandles).
Usage: python wav_analyze.py <path>
"""
import struct
import sys

import numpy as np

path = sys.argv[1]
with open(path, "rb") as f:
    b = f.read()
if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
    print(f"{path}: not a WAV ({len(b)} bytes)")
    sys.exit(1)

# walk chunks
fmt, data = None, None
i = 12
while i + 8 <= len(b):
    cid = b[i:i + 4]
    (sz,) = struct.unpack("<I", b[i + 4:i + 8])
    body = b[i + 8:i + 8 + sz]
    if cid == b"fmt ":
        fmt = body
    elif cid == b"data":
        data = body
    i += 8 + sz + (sz & 1)

if fmt is None or data is None:
    print(f"{path}: missing fmt/data chunk")
    sys.exit(1)

tag, ch, sr, _br, _ba, bits = struct.unpack("<HHIIHH", fmt[:16])
if tag == 3 or bits == 32:        # IEEE float (or treat 32-bit as float, SaveData uses float mix)
    x = np.frombuffer(data, dtype=np.float32).astype(np.float64)
elif bits == 16:
    x = np.frombuffer(data, dtype=np.int16).astype(np.float64) / 32768.0
elif bits == 8:
    x = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128) / 128.0
else:
    print(f"{path}: unsupported bits={bits} tag={tag}")
    sys.exit(1)

ch0 = x[::ch] if ch > 1 else x
# skip the first 0.5 s (stream startup silence) so it isn't counted as a gap
if sr and ch0.size > sr:
    ch0 = ch0[sr // 2:]
n = ch0.size
dur = n / sr if sr else 0

# silent runs >= 1 ms (audiodg produced silence = under-production gap)
sil = np.abs(ch0) < 0.005
gaps, j = 0, 0
while j < sil.size:
    if sil[j]:
        k = j
        while k < sil.size and sil[k]:
            k += 1
        if k - j >= sr // 1000:
            gaps += 1
        j = k
    else:
        j += 1

# large sample-to-sample jumps (a repeated/stale buffer breaks a smooth tone)
d = np.abs(np.diff(ch0))
jumps = int(np.sum(d > 0.05))
rms = float(np.sqrt(np.mean(ch0 ** 2))) if n else 0.0

print(f"file={path.split(chr(92))[-1]}")
print(f"fmt tag={tag} ch={ch} sr={sr} bits={bits} frames={n} dur={dur:.1f}s rms={rms:.3f}")
print(f"silent_gaps(>=1ms)={gaps}  discontinuity_jumps(>0.05)={jumps}")
print("DEVICE OUTPUT:", "CLEAN" if (gaps == 0 and jumps == 0 and rms > 0.01) else "GLITCHED/EMPTY")

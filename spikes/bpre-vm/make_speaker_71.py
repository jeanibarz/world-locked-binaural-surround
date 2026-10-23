# make_speaker_71.py -- convert the SYSVAD "Speaker" render endpoint from stereo to 7.1 LPCM so it
# can be the surround sink the binaural head-tracker renders into. Idempotent. Run with the path to
# the sysvad EndpointsCommon dir as argv[1].
import re, sys, pathlib
ec = pathlib.Path(sys.argv[1])
MASK_OLD = "KSAUDIO_SPEAKER_STEREO"
MASK_NEW = "KSAUDIO_SPEAKER_7POINT1_SURROUND"

def patch_wavtable(p: pathlib.Path):
    t = p.read_text()
    orig = t
    # 1. the MAX_CHANNELS defines (host, device, offload, loopback follows host)
    for name in ("SPEAKER_DEVICE_MAX_CHANNELS", "SPEAKER_HOST_MAX_CHANNELS", "SPEAKER_OFFLOAD_MAX_CHANNELS"):
        t = re.sub(r"(#define\s+%s\s+)2\b" % name, r"\g<1>8", t)
    # 2. every WAVEFORMATEX block: nChannels 2->8, nAvgBytesPerSec SR*4 -> SR*16, nBlockAlign 4->16
    #    block shape: WAVE_FORMAT_EXTENSIBLE, / 2, / <SR>, / <AVG>, / 4, / 16,
    pat = re.compile(
        r"(WAVE_FORMAT_EXTENSIBLE,\s*\n\s*)2(,\s*\n\s*)(\d+)(,\s*\n\s*)(\d+)(,\s*\n\s*)4(,\s*\n\s*)16(,)")
    def repl(m):
        sr = int(m.group(3))
        return (m.group(1) + "8" + m.group(2) + m.group(3) + m.group(4)
                + str(sr * 16) + m.group(6) + "16" + m.group(7) + "16" + m.group(8))
    t = pat.sub(repl, t)
    # 3. channel mask on every speaker format
    t = t.replace(MASK_OLD, MASK_NEW)
    if t != orig:
        p.write_text(t)
    return t != orig

def patch_toptable(p: pathlib.Path):
    t = p.read_text(); orig = t
    t = t.replace(MASK_OLD, MASK_NEW)   # jack description channel config
    if t != orig: p.write_text(t)
    return t != orig

w = ec / "speakerwavtable.h"
tp = ec / "speakertoptable.h"
cw = patch_wavtable(w)
ct = patch_toptable(tp)
# verify
wt = w.read_text()
n8 = wt.count("\n                8,\n") + wt.count("\n            8,\n")
print("speakerwavtable.h changed:", cw, " 7POINT1 masks:", wt.count(MASK_NEW), " STEREO left:", wt.count(MASK_OLD))
print("  SPEAKER_HOST_MAX_CHANNELS 8:", bool(re.search(r"SPEAKER_HOST_MAX_CHANNELS\s+8\b", wt)))
print("  SPEAKER_DEVICE_MAX_CHANNELS 8:", bool(re.search(r"SPEAKER_DEVICE_MAX_CHANNELS\s+8\b", wt)))
print("speakertoptable.h changed:", ct, " 7POINT1:", tp.read_text().count(MASK_NEW))

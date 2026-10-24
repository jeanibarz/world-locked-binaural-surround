#!/usr/bin/env python3
"""make_ring_bridge.py — RFC rfc-sysvad-real-loopback, Plane T v2: real loopback via an in-driver
render->capture ring (the MS-documented fix, the OpenAudioCable CVCable pattern, implemented from
the MIT SYSVAD base).

SYSVAD's loopback capture pin returns a sine tone (GenerateSine). This makes it carry the REAL
rendered mix: the render stream writes its just-played DMA window into a device-wide byte ring
(RingWrite, next to the stock SaveData), and the loopback capture pin drains the ring into its DMA
(RingRead, replacing GenerateSine). Then the app's existing WASAPI loopback carries real audio.

Both the render and capture advances run at DISPATCH (timer DPCs), so the ring is spin-lock guarded.
Apply to a STOCK (non-pull) minwavertstream.cpp (plus the 7.1 + Win10-compat edits). Idempotent.
Usage:  py -3 make_ring_bridge.py <path-to>/EndpointsCommon
"""
import sys
from pathlib import Path

MARK = "Plane T v2 ring bridge (rfc-sysvad-real-loopback)"


def load(p):
    return p.read_text(encoding="utf-8", errors="strict").replace("\r\n", "\n")


def save(p, text):
    p.write_text(text.replace("\n", "\r\n"), encoding="utf-8", newline="")


def must_replace(text, old, new, what):
    if old not in text:
        raise SystemExit(f"ANCHOR NOT FOUND ({what})")
    if text.count(old) != 1:
        raise SystemExit(f"ANCHOR NOT UNIQUE ({what}): {text.count(old)}x")
    return text.replace(old, new, 1)


RING = '''#define MINWAVERTSTREAM_POOLTAG 'SRWM'

//=============================================================================
// ''' + MARK + ''': device-wide render->capture byte ring. The render stream feeds it (producer);
// the loopback capture pin drains it (consumer), replacing the stock sine tone -- so the device's
// loopback carries the real rendered mix (MS: "implement real mixing of the render stream into the
// loopback capture path"). DISPATCH-safe (render + capture advances run on timer DPCs).
#define KOOKR_RING_BYTES (256 * 1024)
static KSPIN_LOCK  g_RingLock;
static LONG        g_RingInit = 0;      // 0=uninit, 1=initializing, 2=ready
static PBYTE       g_RingBuf  = NULL;
static ULONG       g_RingHead = 0;      // write position
static ULONG       g_RingFill = 0;      // bytes available to read

static VOID RingEnsure()
{
    if (InterlockedCompareExchange(&g_RingInit, 1, 0) == 0)
    {
        KeInitializeSpinLock(&g_RingLock);
        g_RingBuf = (PBYTE)ExAllocatePool2(POOL_FLAG_NON_PAGED, KOOKR_RING_BYTES, MINWAVERTSTREAM_POOLTAG);
        g_RingHead = 0; g_RingFill = 0;
        InterlockedExchange(&g_RingInit, g_RingBuf ? 2 : 0);
    }
    else
    {
        while (InterlockedCompareExchange(&g_RingInit, 2, 2) != 2) { if (g_RingInit == 0) { return; } }
    }
}

// Producer (render). Writes n bytes; drops oldest on overflow (fill capped).
static VOID RingWrite(PBYTE src, ULONG n)
{
    RingEnsure();
    if (g_RingBuf == NULL || n == 0) { return; }
    if (n >= KOOKR_RING_BYTES) { src += (n - (KOOKR_RING_BYTES - 1)); n = KOOKR_RING_BYTES - 1; }
    KIRQL irql; KeAcquireSpinLock(&g_RingLock, &irql);
    ULONG first = min(n, KOOKR_RING_BYTES - g_RingHead);
    RtlCopyMemory(g_RingBuf + g_RingHead, src, first);
    if (n > first) { RtlCopyMemory(g_RingBuf, src + first, n - first); }
    g_RingHead = (g_RingHead + n) % KOOKR_RING_BYTES;
    g_RingFill += n;
    if (g_RingFill > KOOKR_RING_BYTES) { g_RingFill = KOOKR_RING_BYTES; }
    KeReleaseSpinLock(&g_RingLock, irql);
}

// Consumer (capture/loopback). Reads n bytes; zero-fills (silence) on underflow.
static VOID RingRead(PBYTE dst, ULONG n)
{
    RingEnsure();
    if (g_RingBuf == NULL) { RtlZeroMemory(dst, n); return; }
    KIRQL irql; KeAcquireSpinLock(&g_RingLock, &irql);
    ULONG avail = min(n, g_RingFill);
    ULONG tail  = (g_RingHead + KOOKR_RING_BYTES - g_RingFill) % KOOKR_RING_BYTES;
    ULONG first = min(avail, KOOKR_RING_BYTES - tail);
    RtlCopyMemory(dst, g_RingBuf + tail, first);
    if (avail > first) { RtlCopyMemory(dst + first, g_RingBuf, avail - first); }
    g_RingFill -= avail;
    KeReleaseSpinLock(&g_RingLock, irql);
    if (avail < n) { RtlZeroMemory(dst + avail, n - avail); }
}
'''


def patch_cpp(p):
    t = load(p)
    if MARK in t:
        print("  .cpp already patched — skip"); return
    # ring globals + funcs, right after the pool-tag define (before WriteBytes/ReadBytes use them)
    t = must_replace(t, "#define MINWAVERTSTREAM_POOLTAG 'SRWM'\n", RING, "cpp:ring-globals")
    # capture/loopback: drain the ring instead of the sine tone
    t = must_replace(
        t,
        "            m_ToneGenerator.GenerateSine(m_pDmaBuffer + bufferOffset, runWrite);\n",
        "            RingRead(m_pDmaBuffer + bufferOffset, runWrite);   // " + MARK + ": real render mix, not the tone\n",
        "cpp:capture-read",
    )
    # render: feed the ring with the just-played window (next to the stock SaveData)
    t = must_replace(
        t,
        "        m_SaveData.WriteData(m_pDmaBuffer + bufferOffset, runWrite);\n",
        "        m_SaveData.WriteData(m_pDmaBuffer + bufferOffset, runWrite);\n"
        "        RingWrite(m_pDmaBuffer + bufferOffset, runWrite);   // " + MARK + ": feed the loopback ring\n",
        "cpp:render-write",
    )
    save(p, t)
    print("  .cpp patched")


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: make_ring_bridge.py <path-to>/EndpointsCommon")
    patch_cpp(Path(argv[1]) / "minwavertstream.cpp")
    print("done.")


if __name__ == "__main__":
    main(sys.argv)

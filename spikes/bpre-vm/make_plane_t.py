#!/usr/bin/env python3
"""make_plane_t.py — RFC rfc-sysvad-real-loopback, Plane T (tick-return) data plane.

Exports the render stream's just-consumed DMA window (the REAL rendered mix audiodg wrote) to a
user-created shared section `\\BaseNamedObjects\\KookrPullData`, so the engine reads the real audio
instead of the capture pin's 3 kHz tone. Same ownership pattern as KookrPullTick (Option B): the
user-mode consumer CREATES the section with a permissive DACL; the driver only ever OPENS + maps it.

Why a section (not a file dump): PullAdvance runs at DISPATCH_LEVEL under g_PullSpinLock +
m_PositionSpinLock, where file I/O is illegal. A memcpy into a mapped+locked section is legal at
DISPATCH. The pull thread maps the section and MmProbeAndLockPages it at PASSIVE (lazily, like it
opens the event), so the per-tick copy in PullAdvance is a safe RtlCopyMemory to a locked system VA.

Idempotent. Run AFTER make_device_pull.py. Edits EndpointsCommon/minwavertstream.{h,cpp}, CRLF-safe.
Usage:  py -3 make_plane_t.py <path-to>/EndpointsCommon
"""
import sys
from pathlib import Path

MARK = "Plane T (rfc-sysvad-real-loopback)"


def load(p):
    return p.read_text(encoding="utf-8", errors="strict").replace("\r\n", "\n")


def save(p, text):
    p.write_text(text.replace("\n", "\r\n"), encoding="utf-8", newline="")


def must_replace(text, old, new, what):
    if old not in text:
        raise SystemExit(f"ANCHOR NOT FOUND ({what}): patcher cannot apply cleanly.")
    if text.count(old) != 1:
        raise SystemExit(f"ANCHOR NOT UNIQUE ({what}): found {text.count(old)}x.")
    return text.replace(old, new, 1)


# --------------------------------------------------------------------------- minwavertstream.h
def patch_h(p):
    t = load(p)
    if MARK in t:
        print("  .h already patched — skip")
        return
    t = must_replace(
        t,
        "    static VOID PullRegister(CMiniportWaveRTStream* stream);\n",
        "    static VOID PullRegister(CMiniportWaveRTStream* stream);\n"
        "    // " + MARK + ": export the just-consumed render window to the KookrPullData section.\n"
        "    VOID PullPushRenderWindow(ULONG bytes);\n",
        "h:decl",
    )
    save(p, t)
    print("  .h patched")


# ------------------------------------------------------------------------- minwavertstream.cpp
GLOBALS = '''//=============================================================================
// ''' + MARK + ''': shared-section data plane. The render stream's PullAdvance copies its
// just-consumed DMA window (what audiodg rendered) into a user-created shared section so the engine
// reads the REAL mix. ZwOpenSection/ZwMapViewOfSection are exported on Win10 19041+ (research-journal
// 74). The section is user-created (consumer, permissive DACL) and the driver only opens it, mirroring
// KookrPullTick. We map it and MmProbeAndLockPages it once at PASSIVE (pull thread), so the per-tick
// RtlCopyMemory at DISPATCH is to a locked system VA (never pages, never faults). Single-writer: only
// the pull thread touches these.
typedef struct _KOOKR_PULLDATA_HDR {
    volatile LONG seq;   // bumped AFTER each window write; odd => write in progress (seqlock)
    ULONG len;           // bytes written in the latest window
    ULONG periodBytes;   // the full period size (>= len if clamped)
    ULONG cap;           // payload capacity (section size - header)
} KOOKR_PULLDATA_HDR;
static HANDLE   g_hPullSection = NULL;
static PVOID    g_pPullSectionView = NULL;  // user section mapped into the system process
static SIZE_T   g_PullSectionSize = 0;
static PMDL     g_pPullMdl = NULL;
static PVOID    g_pPullData = NULL;          // locked system VA (safe to touch at DISPATCH)
static ULONG    g_PullDataCap = 0;           // payload bytes after the header

// ''' + MARK + ''': open + map + lock the consumer's KookrPullData section. PASSIVE only (pull thread).
static VOID PullTryMapData()
{
    if (g_pPullData != NULL) { return; }
    UNICODE_STRING name;
    RtlInitUnicodeString(&name, L"\\\\BaseNamedObjects\\\\KookrPullData");
    OBJECT_ATTRIBUTES oa;
    InitializeObjectAttributes(&oa, &name, OBJ_KERNEL_HANDLE | OBJ_CASE_INSENSITIVE, NULL, NULL);
    HANDLE h = NULL;
    if (!NT_SUCCESS(ZwOpenSection(&h, SECTION_MAP_READ | SECTION_MAP_WRITE, &oa))) { return; }
    PVOID view = NULL; SIZE_T viewSize = 0; LARGE_INTEGER off; off.QuadPart = 0;
    NTSTATUS st = ZwMapViewOfSection(h, NtCurrentProcess(), &view, 0, 0, &off, &viewSize,
                                     ViewUnmap, 0, PAGE_READWRITE);
    if (!NT_SUCCESS(st)) { ZwClose(h); return; }
    PMDL mdl = IoAllocateMdl(view, (ULONG)viewSize, FALSE, FALSE, NULL);
    if (mdl == NULL) { ZwUnmapViewOfSection(NtCurrentProcess(), view); ZwClose(h); return; }
    PVOID sysVA = NULL;
    __try {
        MmProbeAndLockPages(mdl, KernelMode, IoModifyAccess);
        sysVA = MmGetSystemAddressForMdlSafe(mdl, NormalPagePriority);
    } __except (EXCEPTION_EXECUTE_HANDLER) { sysVA = NULL; }
    if (sysVA == NULL) {
        IoFreeMdl(mdl); ZwUnmapViewOfSection(NtCurrentProcess(), view); ZwClose(h); return;
    }
    g_hPullSection = h; g_pPullSectionView = view; g_PullSectionSize = viewSize; g_pPullMdl = mdl;
    g_pPullData = sysVA;
    g_PullDataCap = (viewSize > sizeof(KOOKR_PULLDATA_HDR)) ? (ULONG)(viewSize - sizeof(KOOKR_PULLDATA_HDR)) : 0;
}

// ''' + MARK + ''': release the section (PASSIVE, thread exit). Unlock before unmap before close.
static VOID PullUnmapData()
{
    if (g_pPullMdl != NULL) { MmUnlockPages(g_pPullMdl); IoFreeMdl(g_pPullMdl); g_pPullMdl = NULL; }
    if (g_pPullSectionView != NULL) { ZwUnmapViewOfSection(NtCurrentProcess(), g_pPullSectionView); g_pPullSectionView = NULL; }
    if (g_hPullSection != NULL) { ZwClose(g_hPullSection); g_hPullSection = NULL; }
    g_pPullData = NULL; g_PullDataCap = 0; g_PullSectionSize = 0;
}

// ''' + MARK + ''': copy the just-consumed render window into the section (DISPATCH, under the stream
// position lock). Take-and-clear: zero the consumed source so an audiodg under-run replays silence,
// not a stale ring lap (RFC D3). seqlock: odd seq during the write so the consumer can detect a tear.
VOID CMiniportWaveRTStream::PullPushRenderWindow(ULONG bytes)
{
    if (g_pPullData == NULL || g_PullDataCap == 0 || m_pDmaBuffer == NULL || m_ulDmaBufferSize == 0) { return; }
    KOOKR_PULLDATA_HDR* hdr = (KOOKR_PULLDATA_HDR*)g_pPullData;
    BYTE* dst = (BYTE*)g_pPullData + sizeof(KOOKR_PULLDATA_HDR);
    InterlockedIncrement(&hdr->seq);            // -> odd: write in progress
    ULONG srcOff = (ULONG)(m_ullLinearPosition % m_ulDmaBufferSize);
    ULONG rem = bytes, w = 0;
    while (rem > 0 && w < g_PullDataCap)
    {
        ULONG run = min(rem, m_ulDmaBufferSize - srcOff);
        if (w + run > g_PullDataCap) { run = g_PullDataCap - w; }
        RtlCopyMemory(dst + w, m_pDmaBuffer + srcOff, run);
        RtlZeroMemory(m_pDmaBuffer + srcOff, run);   // take-and-clear (RFC D3)
        srcOff = (srcOff + run) % m_ulDmaBufferSize;
        w += run; rem -= run;
    }
    hdr->len = w; hdr->periodBytes = bytes; hdr->cap = g_PullDataCap;
    InterlockedIncrement(&hdr->seq);            // -> even: write complete
}

'''

PATCH_THREAD_MAP = ('        PullTryOpenEvent();   // lock onto the consumer\'s event once it exists (open-only, never create)',
                    '        PullTryOpenEvent();   // lock onto the consumer\'s event once it exists (open-only, never create)\n'
                    '        PullTryMapData();     // ' + MARK + ': lock onto the consumer\'s KookrPullData section')

PATCH_THREAD_UNMAP = ('    PullReleaseEvent();   // own the event lifetime: always release before the thread exits',
                      '    PullReleaseEvent();   // own the event lifetime: always release before the thread exits\n'
                      '    PullUnmapData();      // ' + MARK + ': release the data section too')

PATCH_PULLADVANCE = ('        if (m_bCapture) { WriteBytes(bytes); }\n'
                     '        else if (!g_DoNotCreateDataFiles) { ReadBytes(bytes); }',
                     '        if (m_bCapture) { WriteBytes(bytes); }\n'
                     '        else if (!g_DoNotCreateDataFiles) { ReadBytes(bytes); }\n'
                     '        if (!m_bCapture) { PullPushRenderWindow(bytes); }   // ' + MARK)


def patch_cpp(p):
    t = load(p)
    if MARK in t:
        print("  .cpp already patched — skip")
        return
    # globals + funcs go right before PullTryOpenEvent (added by the device-pull patch)
    if "static VOID PullTryOpenEvent()" not in t:
        raise SystemExit("ANCHOR NOT FOUND (cpp:PullTryOpenEvent): run make_device_pull.py first.")
    t = must_replace(t, "static VOID PullTryOpenEvent()\n", GLOBALS + "static VOID PullTryOpenEvent()\n", "cpp:globals")
    t = must_replace(t, PATCH_THREAD_MAP[0], PATCH_THREAD_MAP[1], "cpp:thread-map")
    t = must_replace(t, PATCH_THREAD_UNMAP[0], PATCH_THREAD_UNMAP[1], "cpp:thread-unmap")
    t = must_replace(t, PATCH_PULLADVANCE[0], PATCH_PULLADVANCE[1], "cpp:pulladvance")
    save(p, t)
    print("  .cpp patched")


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: make_plane_t.py <path-to>/EndpointsCommon")
    d = Path(argv[1])
    patch_h(d / "minwavertstream.h")
    patch_cpp(d / "minwavertstream.cpp")
    print("done.")


if __name__ == "__main__":
    main(sys.argv)
